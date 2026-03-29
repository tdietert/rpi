"""Implementation stage: execute plan phases with verification."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..diagnosis import (
    print_implementation_diagnosis,
    run_implementation_diagnosis,
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


@dataclass
class _PhaseAttemptRecord:
    """Records a single implementation attempt for diagnosis."""
    attempt: int
    result: PhaseResult
    verification_error: str


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


def _format_phase_prompt(
    phase: PlanPhase, plan_path: Path, error_context: str = ""
) -> str:
    """Build the implementation prompt for a single phase."""
    phase_json = phase.model_dump_json(indent=2)
    parts = [
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
    ]
    if error_context:
        parts.append("")
        parts.append(f"Previous attempt failed with: {error_context}")
    return "\n".join(parts)


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

            has_verification = bool(phase.verification_commands)
            max_attempts = 3 if has_verification else 2
            error_context = ""
            phase_attempts: list[_PhaseAttemptRecord] = []

            for attempt in range(1, max_attempts + 1):
                result = run_claude_structured(
                    prompt=_format_phase_prompt(phase, path, error_context=error_context),
                    schema=PhaseResult,
                    effort="medium",
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                    dry_run_default=_dry_run_phase_result(),
                )
                display.result_panel(f"Phase {phase_num} Attempt {attempt}/{max_attempts}", result)

                if has_verification:
                    # Verification-gated path: run commands regardless of self-report
                    n_cmds = len(phase.verification_commands)
                    display.info(f"Verification: running {n_cmds} command{'s' if n_cmds != 1 else ''}...")
                    v_ok, v_msg = _run_verification_commands(
                        phase.verification_commands, worktree=config.worktree
                    )
                    if v_ok:
                        display.info("[green]Verification: passed[/green]")
                        break
                    # Verification failed — record the attempt
                    failed_cmd = v_msg.split("\n")[0]  # "Command failed: ..."
                    display.error(f"Verification: FAILED \u2014 {failed_cmd}")

                    # Triage: is the command itself broken?
                    if attempt == 1:
                        raw_failed = v_msg.split("\n")[0].removeprefix("Command failed: ").strip()
                        display.info("Triaging verification failure...")
                        triage = triage_verification_failure(
                            failed_command=raw_failed,
                            error_output=v_msg,
                            phase=phase,
                            work_dir=work_dir,
                            dry_run=config.dry_run,
                            worktree=config.worktree,
                        )
                        if triage and triage.command_is_wrong and triage.corrected_command:
                            display.warn(f"Triage: command is wrong \u2014 {triage.reasoning}")
                            display.info(f"Correcting: `{raw_failed}` \u2192 `{triage.corrected_command}`")
                            # Update the phase's verification commands in memory
                            phase.verification_commands = [
                                triage.corrected_command if c == raw_failed else c
                                for c in phase.verification_commands
                            ]
                            # Re-run verification with corrected command
                            display.info("Re-running verification with corrected command...")
                            v_ok, v_msg = _run_verification_commands(
                                phase.verification_commands, worktree=config.worktree
                            )
                            if v_ok:
                                display.info("[green]Verification: passed (after command correction)[/green]")
                                break
                            display.warn("Verification: still failing after correction")
                        elif triage:
                            display.info(f"Triage: command is fine \u2014 {triage.reasoning}")

                    error_context = v_msg
                    if result.status == "failed":
                        error_context = f"Implementer error: {result.errors}\n\nVerification error: {v_msg}"
                    phase_attempts.append(_PhaseAttemptRecord(
                        attempt=attempt, result=result, verification_error=v_msg,
                    ))
                    if attempt < max_attempts:
                        display.info(f"Retrying phase ({attempt}/{max_attempts} attempts used)...")
                        continue
                    # Exhausted retries — run diagnosis before prompting
                    display.info("Running verification failure diagnosis...")
                    diagnosis = run_implementation_diagnosis(
                        phase=phase,
                        attempts=phase_attempts,
                        plan_path=path,
                        work_dir=work_dir,
                        dry_run=config.dry_run,
                        worktree=config.worktree,
                    )
                    print_implementation_diagnosis(diagnosis, phase_num)
                    if confirm("  Verification failed after all retries. Continue to next phase? (y/n): "):
                        break
                    display.error(f"Stopped at phase {phase_num}.")
                    sys.exit(1)
                else:
                    # No verification commands: fall back to existing behavior
                    if result.status == "failed":
                        if attempt < max_attempts and confirm("  Retry this phase? (y/n): "):
                            error_context = result.errors
                            continue
                        display.error(f"Stopped at phase {phase_num}.")
                        sys.exit(1)
                    break

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
