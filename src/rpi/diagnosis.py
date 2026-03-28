"""Diagnosis types and logic for convergence analysis."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .display import _wrap_text, display
from .process import run_claude_structured
from .review import IterationRecord, _format_iteration_history


# -- Diagnosis types ----------------------------------------------------------


class DiagnosisResult(BaseModel):
    pattern: Literal[
        "circular",
        "oscillating",
        "structural",
        "disagreement",
        "diminishing_returns",
        "fixer_blind_spot",
    ]
    summary: str = Field(description="2-3 sentence diagnosis of why the loop did not converge")
    score_trajectory: str = Field(
        description="Brief description of score movement across iterations"
    )
    recurring_issues: list[str] = Field(
        description="Issues that appeared in 2+ iterations (verbatim or paraphrased)"
    )
    recommendations: list[str] = Field(
        description="2-4 concrete next steps the user can take"
    )


class ImplementationDiagnosisResult(BaseModel):
    root_cause: str = Field(
        description="The most likely root cause of repeated verification failure"
    )
    attempt_analysis: list[str] = Field(
        description="One-line analysis of each attempt: what it tried and why verification failed"
    )
    verification_mismatch: str = Field(
        description="Why the implementer reports success but verification fails, or 'N/A' if implementer also reported failure"
    )
    recommendations: list[str] = Field(
        description="2-4 concrete next steps the user can take to fix this"
    )


class VerificationTriageResult(BaseModel):
    command_is_wrong: bool = Field(
        description="True if the verification command itself is broken (wrong flag, wrong subcommand, wrong path), false if the code is the problem"
    )
    corrected_command: str | None = Field(
        default=None,
        description="The corrected command if command_is_wrong is true, otherwise null"
    )
    reasoning: str = Field(
        description="Brief explanation of why the command is or isn't the problem"
    )


# -- Dry-run defaults ---------------------------------------------------------


def _dry_run_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        pattern="diminishing_returns",
        summary="(dry run)",
        score_trajectory="(dry run)",
        recurring_issues=[],
        recommendations=[],
    )


def _dry_run_impl_diagnosis() -> ImplementationDiagnosisResult:
    return ImplementationDiagnosisResult(
        root_cause="(dry run)",
        attempt_analysis=[],
        verification_mismatch="(dry run)",
        recommendations=[],
    )


def _dry_run_triage() -> VerificationTriageResult:
    return VerificationTriageResult(
        command_is_wrong=False,
        corrected_command=None,
        reasoning="(dry run)",
    )


# -- Convergence diagnosis ----------------------------------------------------


def run_diagnosis(
    history: list[IterationRecord],
    loop_type: str,
    plan_path: Path,
    min_score: int,
    work_dir: Path,
    dry_run: bool,
    worktree: str = "",
) -> DiagnosisResult | None:
    """Run the rpi-diagnosis skill to analyze why a loop didn't converge."""
    history_text = _format_iteration_history(history, loop_type)
    scores = [rec.aggregated.score // 2 for rec in history]
    score_summary = ", ".join(str(s) for s in scores)

    prompt = (
        f"Run /rpi-diagnosis to analyze why the {loop_type.replace('_', ' ')} loop "
        f"did not converge.\n\n"
        f"Loop type: {loop_type}\n"
        f"Plan file: {plan_path}\n"
        f"Target score: {min_score}/10\n"
        f"Score trajectory: {score_summary}\n"
        f"Iterations: {len(history)}\n\n"
        f"## Iteration History\n\n{history_text}"
    )

    try:
        return run_claude_structured(
            prompt=prompt,
            schema=DiagnosisResult,
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            dry_run_default=_dry_run_diagnosis(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        display.error(f"Diagnosis failed: {e}")
        return None


def _write_diagnosis_file(diagnosis: DiagnosisResult, loop_type: str) -> Path:
    """Write a plain-text diagnosis file and return its path."""
    diag_dir = Path(".claude/diagnosis")
    diag_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    path = diag_dir / f"{loop_type}-diagnosis-{timestamp}.txt"

    width = 72
    bar = "-" * width

    lines: list[str] = []
    lines.append(bar)
    lines.append("Convergence Diagnosis")
    lines.append(bar)
    lines.append("")
    lines.append(f"  Pattern: {diagnosis.pattern}")
    lines.append("")
    lines.append("  Summary:")
    for wrapped in _wrap_text(diagnosis.summary, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append("  Score Trajectory:")
    for wrapped in _wrap_text(diagnosis.score_trajectory, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append(f"  Recurring Issues: ({len(diagnosis.recurring_issues)} items)")
    for item in diagnosis.recurring_issues:
        lines.append(f"    - {item}")
    lines.append("")
    lines.append(f"  Recommendations: ({len(diagnosis.recommendations)} items)")
    for item in diagnosis.recommendations:
        lines.append(f"    - {item}")
    lines.append("")
    lines.append(bar)

    path.write_text("\n".join(lines) + "\n")
    return path


def print_diagnosis(diagnosis: DiagnosisResult | None, loop_type: str) -> None:
    """Print a formatted diagnosis to the terminal and write it to a file."""
    if diagnosis is None:
        return
    display.result_panel("Convergence Diagnosis", diagnosis)
    path = _write_diagnosis_file(diagnosis, loop_type)
    display.info(f"Diagnosis written to: {path}")


# -- Implementation phase diagnosis -------------------------------------------


def triage_verification_failure(
    failed_command: str,
    error_output: str,
    phase: object,  # PlanPhase — avoid circular import
    work_dir: Path | None = None,
    dry_run: bool = False,
    worktree: str = "",
) -> VerificationTriageResult | None:
    """Quick triage: is the verification command itself broken, or is the code wrong?"""
    worktree_context = ""
    if worktree:
        worktree_context = (
            f"\n\nIMPORTANT: This command is running in a git worktree at `{worktree}`. "
            "The working directory is automatically set to the worktree. "
            "If the command uses `cd` with an absolute path to a DIFFERENT directory "
            "(e.g., the main repo checkout), that is WRONG — the command is running "
            "against the wrong copy of the code. The fix is to remove the absolute "
            "`cd` and use relative paths, since the working directory is already correct."
        )

    prompt = (
        f"A verification command failed after implementing Phase {phase.number} ({phase.name}).\n\n"
        f"Failed command: `{failed_command}`\n\n"
        f"Error output:\n```\n{error_output}\n```\n\n"
        "Determine whether the COMMAND ITSELF is wrong (e.g., invalid flag, "
        "nonexistent subcommand, wrong path, typo in command name, wrong working "
        "directory) or whether the command is correct but the CODE it's verifying "
        "has a problem.\n\n"
        "Signs the command is wrong: 'unknown flag', 'command not found', "
        "'no such file or directory' for a path IN the command, unrecognized option, "
        "absolute `cd` to a directory that isn't the worktree.\n"
        "Signs the code is wrong: type errors, test failures, compilation errors, "
        "missing exports, runtime exceptions.\n\n"
        "If the command is wrong, provide the corrected version."
        f"{worktree_context}"
    )
    try:
        return run_claude_structured(
            prompt=prompt,
            schema=VerificationTriageResult,
            effort="low",
            work_dir=work_dir,
            dry_run=dry_run,
            streaming=False,
            worktree=worktree,
            model="haiku",
            dry_run_default=_dry_run_triage(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        display.error(f"Triage failed: {e}")
        return None


def run_implementation_diagnosis(
    phase: object,  # PlanPhase
    attempts: list,  # list[_PhaseAttemptRecord]
    plan_path: Path,
    work_dir: Path,
    dry_run: bool,
    worktree: str = "",
) -> ImplementationDiagnosisResult | None:
    """Run Claude to diagnose why a phase's verification keeps failing."""
    attempt_details = []
    for rec in attempts:
        attempt_details.append(
            f"### Attempt {rec.attempt}\n"
            f"- Status (self-reported): {rec.result.status}\n"
            f"- Summary: {rec.result.summary}\n"
            f"- Errors: {rec.result.errors}\n"
            f"- Verification (self-reported): {rec.result.verification}\n"
            f"- Verification command output:\n```\n{rec.verification_error}\n```"
        )

    verification_cmds = "\n".join(f"  - `{c}`" for c in phase.verification_commands)

    prompt = (
        f"Diagnose why Phase {phase.number} ({phase.name}) failed verification "
        f"after {len(attempts)} attempts.\n\n"
        f"Plan file: {plan_path}\n"
        f"Verification commands:\n{verification_cmds}\n\n"
        f"## Attempt History\n\n"
        + "\n\n".join(attempt_details)
        + "\n\nAnalyze the pattern across attempts. Focus on: "
        "why does verification keep failing? Is the implementer solving the "
        "wrong problem? Is the verification command itself broken (e.g. wrong "
        "path, wrong cwd)? Is there an environment issue? Provide actionable "
        "recommendations."
    )

    try:
        return run_claude_structured(
            prompt=prompt,
            schema=ImplementationDiagnosisResult,
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            streaming=False,
            worktree=worktree,
            dry_run_default=_dry_run_impl_diagnosis(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        display.error(f"Diagnosis failed: {e}")
        return None


def _write_implementation_diagnosis_file(
    diagnosis: ImplementationDiagnosisResult,
    phase_num: int,
) -> Path:
    """Write an implementation diagnosis file and return its path."""
    diag_dir = Path(".claude/diagnosis")
    diag_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    path = diag_dir / f"implementation-phase{phase_num}-diagnosis-{timestamp}.txt"

    width = 72
    bar = "-" * width

    lines: list[str] = []
    lines.append(bar)
    lines.append(f"Implementation Diagnosis — Phase {phase_num}")
    lines.append(bar)
    lines.append("")
    lines.append("  Root Cause:")
    for wrapped in _wrap_text(diagnosis.root_cause, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append(f"  Attempt Analysis: ({len(diagnosis.attempt_analysis)} attempts)")
    for i, item in enumerate(diagnosis.attempt_analysis, 1):
        lines.append(f"    {i}. {item}")
    lines.append("")
    lines.append("  Verification Mismatch:")
    for wrapped in _wrap_text(diagnosis.verification_mismatch, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append(f"  Recommendations: ({len(diagnosis.recommendations)} items)")
    for item in diagnosis.recommendations:
        lines.append(f"    - {item}")
    lines.append("")
    lines.append(bar)

    path.write_text("\n".join(lines) + "\n")
    return path


def print_implementation_diagnosis(
    diagnosis: ImplementationDiagnosisResult | None,
    phase_num: int,
) -> None:
    """Print and write an implementation phase diagnosis."""
    if diagnosis is None:
        return
    display.result_panel(f"Phase {phase_num} Verification Diagnosis", diagnosis)
    path = _write_implementation_diagnosis_file(diagnosis, phase_num)
    display.info(f"Diagnosis written to: {path}")
