"""Diagnosis types and logic for convergence analysis."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .display import Display
from .plan import PlanPhase
from .process import run_claude_with_display
from .types import IterationRecord, format_iteration_history


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


class TriageFixRecord(BaseModel):
    """Records a single triage-fix attempt for diagnosis."""
    attempt: int
    verdict: str
    reasoning: str
    corrected_command: str | None = None
    fix_instructions: str | None = None
    fix_summary: str | None = None
    verification_error: str


class ImplementationDiagnosisResult(BaseModel):
    root_cause: str = Field(
        description="The most likely root cause of repeated verification failure"
    )
    attempt_analysis: list[str] = Field(
        description="One-line analysis of each triage-fix attempt: what was tried and why it didn't work"
    )
    recommendations: list[str] = Field(
        description="2-4 concrete next steps the user can take to fix this"
    )


class VerificationTriageResult(BaseModel):
    verdict: Literal["command_wrong", "code_fix", "fundamental"] = Field(
        description=(
            "command_wrong: the verification command itself is broken "
            "(wrong binary, missing runtime wrapper, bad flag). "
            "code_fix: the code has a small, targetable problem "
            "(missing import, typo, wrong argument) that a fixer agent can resolve. "
            "fundamental: the implementation approach is wrong or the plan is broken; "
            "nothing short of re-planning will help."
        )
    )
    corrected_command: str | None = Field(
        default=None,
        description="The corrected command when verdict is 'command_wrong', otherwise null"
    )
    corrected_commands: list[str] | None = Field(
        default=None,
        description=(
            "When verdict is 'command_wrong': the FULL list of corrected verification "
            "commands (same length as the input list). Apply the same fix pattern to ALL "
            "commands that share the issue, not just the one that failed. Null otherwise."
        ),
    )
    fix_instructions: str | None = Field(
        default=None,
        description=(
            "When verdict is 'code_fix': specific instructions for a fixer agent "
            "describing what file(s) to change and what the fix is. "
            "Be concrete: 'add missing import X in file Y' not 'fix the import'."
        ),
    )
    reasoning: str = Field(
        description="Brief explanation of the diagnosis"
    )


class VerificationFixResult(BaseModel):
    changes_applied: int = Field(description="Number of distinct fixes applied")
    summary: str = Field(description="What was changed and why")


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
        recommendations=[],
    )


def _dry_run_triage() -> VerificationTriageResult:
    return VerificationTriageResult(
        verdict="command_wrong",
        corrected_command="echo ok",
        corrected_commands=["echo ok"],
        reasoning="(dry run)",
    )


def _dry_run_fix() -> VerificationFixResult:
    return VerificationFixResult(
        changes_applied=0,
        summary="(dry run)",
    )


def run_diagnosis(
    history: list[IterationRecord],
    loop_type: str,
    plan_path: Path,
    min_score: int,
    work_dir: Path,
    dry_run: bool,
    worktree: str = "",
    display: Display | None = None,
) -> DiagnosisResult | None:
    """Run the rpi-diagnosis skill to analyze why a loop didn't converge."""
    history_text = format_iteration_history(history, loop_type)
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
        return run_claude_with_display(
            prompt,
            DiagnosisResult,
            display=display,
            label="Convergence Diagnosis",
            log_name="convergence-diagnosis",
            complete_summary=lambda r: r.pattern,
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            dry_run_default=_dry_run_diagnosis(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        if display is not None:
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
    for wrapped in textwrap.wrap(diagnosis.summary, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append("  Score Trajectory:")
    for wrapped in textwrap.wrap(diagnosis.score_trajectory, width=width - 4):
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


def print_diagnosis(diagnosis: DiagnosisResult | None, loop_type: str, display: Display | None = None) -> None:
    """Print a formatted diagnosis to the terminal and write it to a file."""
    if diagnosis is None:
        return
    if display is not None:
        display.info(f"Convergence Diagnosis: {diagnosis.pattern} — {diagnosis.summary}")
    path = _write_diagnosis_file(diagnosis, loop_type)
    if display is not None:
        display.info(f"Diagnosis written to: {path}")


def triage_verification_failure(
    failed_command: str,
    error_output: str,
    phase: PlanPhase,
    all_commands: list[str] | None = None,
    work_dir: Path | None = None,
    dry_run: bool = False,
    worktree: str = "",
    implementer_verification: str = "",
    display: Display | None = None,
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

    implementer_context = ""
    if implementer_verification:
        implementer_context = (
            f"\n\nThe implementer agent ran its own verification and reported:\n"
            f"```\n{implementer_verification}\n```\n"
            "If the implementer found a working command variant (e.g., using a runtime "
            "wrapper like `uv run`, `poetry run`, `npx`, `nix run`), prefer that fix."
        )

    all_commands_context = ""
    if all_commands:
        numbered = "\n".join(f"  {i+1}. `{c}`" for i, c in enumerate(all_commands))
        all_commands_context = (
            f"\n\nAll verification commands for this phase:\n{numbered}\n\n"
            "IMPORTANT: If the issue affects multiple commands (e.g., all use bare "
            "`python` instead of `uv run python`), set `corrected_commands` to the "
            "FULL corrected list with the same fix applied to ALL affected commands. "
            "This avoids burning one triage attempt per command for the same bug."
        )

    prompt = (
        f"A verification command failed after implementing Phase {phase.number} ({phase.name}).\n\n"
        f"Failed command: `{failed_command}`\n\n"
        f"Error output:\n```\n{error_output}\n```\n\n"
        "Classify this failure into exactly one of three verdicts:\n\n"
        "**command_wrong** — The verification command itself is broken. Examples: "
        "'command not found', missing runtime wrapper (e.g., bare `python` instead of "
        "`uv run python`), wrong flag, wrong path, absolute `cd` to wrong directory, "
        "'ModuleNotFoundError' when the module IS installed in a managed environment. "
        "Set `corrected_command` to the fixed failing command. "
        "Also set `corrected_commands` to the full corrected list if other commands "
        "share the same issue.\n\n"
        "**code_fix** — The command is correct but the code has a small, targetable "
        "problem that a fixer agent can resolve. Examples: missing import, typo in "
        "a name, wrong function signature, missing export. "
        "Set `fix_instructions` to concrete instructions: which file(s) to change, "
        "what the fix is, and why.\n\n"
        "**fundamental** — The implementation approach is wrong or the plan itself is "
        "broken. Examples: entirely wrong architecture, missing module that should have "
        "been created, circular dependency that can't be patched. "
        "Nothing short of re-planning will help. Explain in `reasoning`.\n\n"
        "Choose `code_fix` only if the fix is small and localized. "
        "Choose `fundamental` if the problem requires rethinking the approach."
        f"{all_commands_context}"
        f"{worktree_context}"
        f"{implementer_context}"
    )
    try:
        return run_claude_with_display(
            prompt,
            VerificationTriageResult,
            display=display,
            label="Triage",
            log_name="triage",
            complete_summary=lambda r: r.verdict,
            effort="low",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            model="haiku",
            dry_run_default=_dry_run_triage(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        if display is not None:
            display.error(f"Triage failed: {e}")
        return None


def run_verification_fix(
    fix_instructions: str,
    failed_command: str,
    error_output: str,
    phase: PlanPhase,
    work_dir: Path | None = None,
    dry_run: bool = False,
    worktree: str = "",
    display: Display | None = None,
) -> VerificationFixResult | None:
    """Spawn a lightweight fixer agent to resolve a verification failure."""
    prompt = (
        "Run /rpi-fix. A verification command is failing after implementation. "
        "Apply a targeted fix based on the instructions below.\n\n"
        f"## Phase\n\nPhase {phase.number}: {phase.name}\n\n"
        f"## What to fix\n\n{fix_instructions}\n\n"
        f"## Failed command\n\n`{failed_command}`\n\n"
        f"## Error output\n\n```\n{error_output}\n```\n\n"
        "Make the minimal edit that fixes the issue. Do not refactor, "
        "do not add scope, do not re-implement the phase."
    )
    try:
        return run_claude_with_display(
            prompt,
            VerificationFixResult,
            display=display,
            label="Verification Fix",
            log_name="verification-fix",
            complete_summary=lambda r: r.summary,
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            dry_run_default=_dry_run_fix(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        if display is not None:
            display.error(f"Verification fix failed: {e}")
        return None


def run_implementation_diagnosis(
    phase: object,  # PlanPhase
    fix_attempts: list[TriageFixRecord],
    implementer_verification: str,
    work_dir: Path,
    dry_run: bool,
    worktree: str = "",
    display: Display | None = None,
) -> ImplementationDiagnosisResult | None:
    """Run Claude to diagnose why a phase's triage-fix loop didn't resolve."""
    attempt_details = []
    for rec in fix_attempts:
        parts = [
            f"### Fix Attempt {rec.attempt}",
            f"- Verdict: {rec.verdict}",
            f"- Reasoning: {rec.reasoning}",
        ]
        if rec.corrected_command:
            parts.append(f"- Corrected command: `{rec.corrected_command}`")
        if rec.fix_instructions:
            parts.append(f"- Fix instructions: {rec.fix_instructions}")
        if rec.fix_summary:
            parts.append(f"- Fix result: {rec.fix_summary}")
        parts.append(f"- Verification error after this attempt:\n```\n{rec.verification_error}\n```")
        attempt_details.append("\n".join(parts))

    verification_cmds = "\n".join(f"  - `{c}`" for c in phase.verification_commands)

    prompt = (
        f"Diagnose why Phase {phase.number} ({phase.name}) failed verification "
        f"after {len(fix_attempts)} triage-fix attempts.\n\n"
        f"Verification commands (current, possibly corrected):\n{verification_cmds}\n\n"
        f"Implementer's self-reported verification:\n```\n{implementer_verification}\n```\n\n"
        f"## Triage-Fix History\n\n"
        + "\n\n".join(attempt_details)
        + "\n\nThe implementation ran once and was not retried. The triage-fix loop "
        "attempted lightweight corrections but could not resolve the failure. "
        "Analyze why: is the triage misdiagnosing? Is the fix too narrow? "
        "Is this actually a plan-level problem? Provide actionable recommendations."
    )

    try:
        return run_claude_with_display(
            prompt,
            ImplementationDiagnosisResult,
            display=display,
            label="Phase Diagnosis",
            log_name="impl-diagnosis",
            complete_summary=lambda r: r.root_cause[:60],
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            dry_run_default=_dry_run_impl_diagnosis(),
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        if display is not None:
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
    for wrapped in textwrap.wrap(diagnosis.root_cause, width=width - 4):
        lines.append(f"    {wrapped}")
    lines.append("")
    lines.append(f"  Triage-Fix Analysis: ({len(diagnosis.attempt_analysis)} attempts)")
    for i, item in enumerate(diagnosis.attempt_analysis, 1):
        lines.append(f"    {i}. {item}")
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
    display: Display | None = None,
) -> None:
    """Print and write an implementation phase diagnosis."""
    if diagnosis is None:
        return
    if display is not None:
        display.info(f"Phase {phase_num} Verification Diagnosis: {diagnosis.root_cause}")
    path = _write_implementation_diagnosis_file(diagnosis, phase_num)
    if display is not None:
        display.info(f"Diagnosis written to: {path}")
