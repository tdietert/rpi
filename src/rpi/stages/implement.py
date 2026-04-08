"""Implementation stage: execute plan phases with verification."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..diagnosis import (
    TriageFixRecord,
    print_implementation_diagnosis,
    run_implementation_diagnosis,
    run_verification_fix,
    triage_verification_failure,
)
from ..display import dim, green
from ..plan import PlanPhase
from ..process import run_claude_structured
from ..progress import SnapshotPhaseProgress
from ..stage_name import StageName
from . import Stage


class PhaseResult(BaseModel):
    status: Literal["success", "failed"]
    phase: int
    summary: str = Field(
        description="2-3 sentence description of what was implemented"
    )
    errors: str = Field(description="Any errors encountered, or 'None'")
    verification: str = Field(description="Result of verification steps")


def _run_verification_commands(
    commands: list[str], worktree: str = ""
) -> tuple[bool, str]:
    """Run verification commands via subprocess, returning (passed, error_msg)."""
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=worktree or None,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return (False, f"Command timed out after 300s: {cmd}")
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"
            return (
                False,
                f"Command failed: {cmd}\nExit code: {result.returncode}\n{output}",
            )
    return (True, "")


def _format_phase_prompt(phase: PlanPhase, plan_path: Path) -> str:
    """Build the implementation prompt for a single phase."""
    phase_json = phase.model_dump_json(indent=2)
    return "\n".join([
        f"Run /rpi-implement. Implement ONLY Phase {phase.number}: {phase.name}.",
        "",
        "## Structured Phase Data",
        "",
        "The following JSON is the authoritative specification for this phase. "
        "Use it for the list of tasks, files, groups, steps, and verification. "
        f"The full plan file at {plan_path} has additional prose context "
        "(overview, current state, risks, edge cases).",
        "",
        f"```json\n{phase_json}\n```",
    ])


class ImplementStage(Stage):
    name = StageName.implement
    label = "Stage 2: Implementation"

    def run(self, ctx) -> None:
        config = ctx.config
        work_dir = ctx.work_dir
        plan = ctx.plan
        skip_phases = ctx.resume_completed_phases or None
        num_phases = len(plan.phases)

        # Use the work_dir copy of the plan so agents can edit it
        # (the canonical .claude/plans/ path is not writable by agents).
        work_plan = work_dir / config.plan_path.name
        if config.plan_path != work_plan and config.plan_path.is_file():
            shutil.copy2(config.plan_path, work_plan)
        path = work_plan if work_plan.is_file() else config.plan_path

        ctx.display.stage_header(f"Stage 2: Implementation ({num_phases} phases)")
        if ctx.progress.implementation is None:
            ctx.progress.implementation = SnapshotPhaseProgress()

        for phase in plan.phases:
            phase_num = phase.number

            if skip_phases and phase_num in skip_phases:
                ctx.display.info(dim(f"Phase {phase_num}/{num_phases}: {phase.name} -- SKIPPED (already complete)"))
                continue

            ctx.display.stage_header(
                f"Implementing phase {phase_num}/{num_phases}: {phase.name}"
            )
            ctx.display.info(
                f"Tasks: {len(phase.tasks)}, "
                f"Groups: {', '.join(sorted({t.group for t in phase.tasks}))}"
            )

            with ctx.display.activity(
                f"Phase {phase_num}/{num_phases}: {phase.name}",
                f"implement-phase-{phase_num}",
            ) as act:
                result = run_claude_structured(
                    prompt=_format_phase_prompt(phase, path),
                    schema=PhaseResult,
                    effort="medium",
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                    activity=act,
                )
                act.complete(
                    "success" if result.status == "success" else "failed",
                    result.summary,
                )

            has_verification = bool(phase.verification_commands)

            if not has_verification:
                if result.status == "failed":
                    ctx.display.error(f"Phase {phase_num} failed: {result.errors}")
                    if not ctx.display.confirm("Continue to next phase?"):
                        ctx.display.error(f"Stopped at phase {phase_num}.")
                        sys.exit(1)
            else:
                n_cmds = len(phase.verification_commands)
                ctx.display.info(f"Verification: running {n_cmds} command{'s' if n_cmds != 1 else ''}...")
                v_ok, v_msg = _run_verification_commands(
                    phase.verification_commands, worktree=config.worktree
                )

                if not v_ok:
                    failed_cmd = v_msg.split("\n")[0]
                    ctx.display.error(f"Verification: FAILED \u2014 {failed_cmd}")

                    max_fix_attempts = 3
                    fix_records: list[TriageFixRecord] = []
                    for fix_attempt in range(max_fix_attempts):
                        raw_failed = v_msg.split("\n")[0].removeprefix("Command failed: ").strip()
                        ctx.display.info(f"Triaging verification failure (attempt {fix_attempt + 1}/{max_fix_attempts})...")
                        triage = triage_verification_failure(
                            failed_command=raw_failed,
                            error_output=v_msg,
                            phase=phase,
                            all_commands=phase.verification_commands,
                            work_dir=work_dir,
                            dry_run=config.dry_run,
                            worktree=config.worktree,
                            implementer_verification=result.verification,
                            display=ctx.display,
                        )
                        if triage is None:
                            ctx.display.error("Triage failed to produce a result.")
                            break

                        ctx.display.info(f"Triage verdict: {triage.verdict} \u2014 {triage.reasoning}")

                        # Build record (verification_error updated after action)
                        record = TriageFixRecord(
                            attempt=fix_attempt + 1,
                            verdict=triage.verdict,
                            reasoning=triage.reasoning,
                            corrected_command=triage.corrected_command,
                            fix_instructions=triage.fix_instructions,
                            verification_error=v_msg,
                        )

                        match triage.verdict:
                            case "command_wrong":
                                if not triage.corrected_commands and not triage.corrected_command:
                                    ctx.display.error("Triage said command is wrong but gave no correction.")
                                    fix_records.append(record)
                                    break
                                if triage.corrected_commands:
                                    ctx.display.info(f"Replacing verification commands ({len(triage.corrected_commands)} total)")
                                    phase.verification_commands = triage.corrected_commands
                                else:
                                    ctx.display.info(f"Correcting: `{raw_failed}` \u2192 `{triage.corrected_command}`")
                                    phase.verification_commands = [
                                        triage.corrected_command if c == raw_failed else c
                                        for c in phase.verification_commands
                                    ]
                                v_ok, v_msg = _run_verification_commands(
                                    phase.verification_commands, worktree=config.worktree
                                )
                                record.verification_error = v_msg
                                fix_records.append(record)
                                if v_ok:
                                    ctx.display.info(green("Verification: passed (after command correction)"))
                                    break

                            case "code_fix":
                                if not triage.fix_instructions:
                                    ctx.display.error("Triage said code_fix but gave no instructions.")
                                    fix_records.append(record)
                                    break
                                ctx.display.info("Spawning fixer agent...")
                                fix_result = run_verification_fix(
                                    fix_instructions=triage.fix_instructions,
                                    failed_command=raw_failed,
                                    error_output=v_msg,
                                    phase=phase,
                                    work_dir=work_dir,
                                    dry_run=config.dry_run,
                                    worktree=config.worktree,
                                    display=ctx.display,
                                )
                                if fix_result:
                                    ctx.display.info(f"Fixer: {fix_result.summary}")
                                    record.fix_summary = fix_result.summary
                                v_ok, v_msg = _run_verification_commands(
                                    phase.verification_commands, worktree=config.worktree
                                )
                                record.verification_error = v_msg
                                fix_records.append(record)
                                if v_ok:
                                    ctx.display.info(green("Verification: passed (after code fix)"))
                                    break

                            case "fundamental":
                                ctx.display.error(f"Fundamental issue: {triage.reasoning}")
                                fix_records.append(record)
                                break

                    if not v_ok:
                        ctx.display.info("Running verification failure diagnosis...")
                        diagnosis = run_implementation_diagnosis(
                            phase=phase,
                            fix_attempts=fix_records,
                            implementer_verification=result.verification,
                            work_dir=work_dir,
                            dry_run=config.dry_run,
                            worktree=config.worktree,
                            display=ctx.display,
                        )
                        print_implementation_diagnosis(diagnosis, phase_num, display=ctx.display)
                        if not ctx.display.confirm("Verification failed after triage-fix loop. Continue to next phase?"):
                            ctx.display.error(f"Stopped at phase {phase_num}.")
                            sys.exit(1)
                else:
                    ctx.display.info(green("Verification: passed"))

            ctx.display.info(f"{green(f'Phase {phase_num}/{num_phases} complete:')} {result.summary}")
            ctx.progress.implementation.completed_phases.append(phase_num)
            self._snapshot(ctx)

        ctx.display.info(green(f"All {num_phases} phases implemented."))
        ctx.display.info(
            f"{green('Stage 2 complete:')} all {num_phases} phases implemented"
        )
        ctx.progress.implementation_done = True

    def execute(self, ctx) -> None:
        self.run(ctx)
        self._snapshot(ctx)
