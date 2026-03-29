"""Implementation stage: execute plan phases with verification."""

from __future__ import annotations

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
from ..display import display
from ..plan import PlanPhase
from ..process import confirm, run_claude_structured
from ..snapshot import SnapshotPhaseProgress, save_snapshot


# -- Types local to implement stage -------------------------------------------


class PhaseResult(BaseModel):
    status: Literal["success", "failed"]
    phase: int
    summary: str = Field(
        description="2-3 sentence description of what was implemented"
    )
    errors: str = Field(description="Any errors encountered, or 'None'")
    verification: str = Field(description="Result of verification steps")


# -- Dry-run default ----------------------------------------------------------


def _dry_run_phase_result() -> PhaseResult:
    return PhaseResult(
        status="success",
        phase=0,
        summary="(dry run)",
        errors="None",
        verification="Skipped",
    )


# -- Helpers ------------------------------------------------------------------


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


# -- Stage class --------------------------------------------------------------


class ImplementStage:
    name = "implement"
    label = "Stage 2: Implementation"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_implement

    def run(self, ctx) -> None:
        config = ctx.config
        work_dir = ctx.work_dir
        plan = ctx.plan
        skip_phases = ctx.resume_completed_phases or None
        num_phases = len(plan.phases)
        path = config.plan_path

        display.stage_header(f"Stage 2: Implementation ({num_phases} phases)")
        if ctx.progress.implementation is None:
            ctx.progress.implementation = SnapshotPhaseProgress()

        for phase in plan.phases:
            phase_num = phase.number

            if skip_phases and phase_num in skip_phases:
                display.info(f"[dim]Phase {phase_num}/{num_phases}: {phase.name} -- SKIPPED (already complete)[/dim]")
                continue

            display.console.rule(
                f"[bold]Implementing phase {phase_num}/{num_phases}: {phase.name}[/bold]"
            )
            display.info(
                f"Tasks: {len(phase.tasks)}, "
                f"Groups: {', '.join(sorted({t.group for t in phase.tasks}))}"
            )

            # -- Implementation (runs exactly once) --
            result = run_claude_structured(
                prompt=_format_phase_prompt(phase, path),
                schema=PhaseResult,
                effort="medium",
                work_dir=work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
                dry_run_default=_dry_run_phase_result(),
            )
            display.result_panel(f"Phase {phase_num} Implementation", result)

            has_verification = bool(phase.verification_commands)

            if not has_verification:
                if result.status == "failed":
                    display.error(f"Phase {phase_num} failed: {result.errors}")
                    if not confirm("  Continue to next phase? (y/n): "):
                        display.error(f"Stopped at phase {phase_num}.")
                        sys.exit(1)
            else:
                # -- Verification --
                n_cmds = len(phase.verification_commands)
                display.info(f"Verification: running {n_cmds} command{'s' if n_cmds != 1 else ''}...")
                v_ok, v_msg = _run_verification_commands(
                    phase.verification_commands, worktree=config.worktree
                )

                if not v_ok:
                    failed_cmd = v_msg.split("\n")[0]
                    display.error(f"Verification: FAILED \u2014 {failed_cmd}")

                    # -- Triage-fix loop --
                    max_fix_attempts = 3
                    fix_records: list[TriageFixRecord] = []
                    for fix_attempt in range(max_fix_attempts):
                        raw_failed = v_msg.split("\n")[0].removeprefix("Command failed: ").strip()
                        display.info(f"Triaging verification failure (attempt {fix_attempt + 1}/{max_fix_attempts})...")
                        triage = triage_verification_failure(
                            failed_command=raw_failed,
                            error_output=v_msg,
                            phase=phase,
                            all_commands=phase.verification_commands,
                            work_dir=work_dir,
                            dry_run=config.dry_run,
                            worktree=config.worktree,
                            implementer_verification=result.verification,
                        )
                        if triage is None:
                            display.error("Triage failed to produce a result.")
                            break

                        display.info(f"Triage verdict: {triage.verdict} \u2014 {triage.reasoning}")

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
                                    display.error("Triage said command is wrong but gave no correction.")
                                    fix_records.append(record)
                                    break
                                if triage.corrected_commands:
                                    display.info(f"Replacing verification commands ({len(triage.corrected_commands)} total)")
                                    phase.verification_commands = triage.corrected_commands
                                else:
                                    display.info(f"Correcting: `{raw_failed}` \u2192 `{triage.corrected_command}`")
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
                                    display.info("[green]Verification: passed (after command correction)[/green]")
                                    break

                            case "code_fix":
                                if not triage.fix_instructions:
                                    display.error("Triage said code_fix but gave no instructions.")
                                    fix_records.append(record)
                                    break
                                display.info("Spawning fixer agent...")
                                fix_result = run_verification_fix(
                                    fix_instructions=triage.fix_instructions,
                                    failed_command=raw_failed,
                                    error_output=v_msg,
                                    phase=phase,
                                    work_dir=work_dir,
                                    dry_run=config.dry_run,
                                    worktree=config.worktree,
                                )
                                if fix_result:
                                    display.info(f"Fixer: {fix_result.summary}")
                                    record.fix_summary = fix_result.summary
                                v_ok, v_msg = _run_verification_commands(
                                    phase.verification_commands, worktree=config.worktree
                                )
                                record.verification_error = v_msg
                                fix_records.append(record)
                                if v_ok:
                                    display.info("[green]Verification: passed (after code fix)[/green]")
                                    break

                            case "fundamental":
                                display.error(f"Fundamental issue: {triage.reasoning}")
                                fix_records.append(record)
                                break

                    if not v_ok:
                        display.info("Running verification failure diagnosis...")
                        diagnosis = run_implementation_diagnosis(
                            phase=phase,
                            fix_attempts=fix_records,
                            implementer_verification=result.verification,
                            work_dir=work_dir,
                            dry_run=config.dry_run,
                            worktree=config.worktree,
                        )
                        print_implementation_diagnosis(diagnosis, phase_num)
                        if not confirm("  Verification failed after triage-fix loop. Continue to next phase? (y/n): "):
                            display.error(f"Stopped at phase {phase_num}.")
                            sys.exit(1)
                else:
                    display.info("[green]Verification: passed[/green]")

            display.info(f"[green]Phase {phase_num}/{num_phases} complete:[/green] {result.summary}")
            # Phase complete callback
            ctx.progress.implementation.completed_phases.append(phase_num)
            self._snapshot(ctx)

        display.info(f"[green]All {num_phases} phases implemented.[/green]")
        display.info(
            f"[green]Stage 2 complete:[/green] all {num_phases} phases implemented"
        )
        ctx.progress.implementation_done = True

    def execute(self, ctx) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            return
        self.run(ctx)
        self._snapshot(ctx)

    def _snapshot(self, ctx) -> None:
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.plan, ctx.work_dir)
