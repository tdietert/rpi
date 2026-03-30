"""Review types, quorum logic, and feedback application."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .process import (
    _child_procs,
    _drain_queue,
    _interrupt,
    _parse_structured,
    _reader,
    run_claude_structured,
)

if TYPE_CHECKING:
    from .display import Display


class ReviewIssue(BaseModel):
    severity: Literal["critical", "note"]
    description: str = Field(
        description="Issue with justification. Format: 'Issue description. Why: concrete consequence.'"
    )


class ReviewResult(BaseModel):
    score: int = Field(description="Sum of all four dimension scores, out of 20")
    correctness: int = Field(description="Score out of 5")
    completeness: int = Field(description="Score out of 5")
    simplicity: int = Field(description="Score out of 5")
    clarity: int = Field(description="Score out of 5")
    issues: list[ReviewIssue] = Field(
        description="Each issue categorized as 'critical' (blocks implementation) or 'note' (observation, not blocking). Max 3 critical + 2 notes."
    )
    suggested_changes: list[str] = Field(
        description="Each change with justification: 'Change description. Why: what breaks or degrades without it.'"
    )


@dataclass
class QuorumResult:
    aggregated: ReviewResult
    per_reviewer: list[ReviewResult]


@dataclass
class IterationRecord:
    iteration: int
    aggregated: ReviewResult
    per_reviewer: list[ReviewResult]
    apply_summary: str  # empty if no feedback was applied




def _dry_run_review() -> ReviewResult:
    return ReviewResult(
        score=18,
        correctness=5,
        completeness=4,
        simplicity=5,
        clarity=4,
        issues=[],
        suggested_changes=[],
    )




class QuorumProcess:
    """Handle for N parallel Claude CLI subprocesses (review quorum)."""

    def __init__(
        self,
        procs: list[subprocess.Popen],
        q: queue.Queue,
        reader_threads: list[threading.Thread],
        interrupt: threading.Event,
        result_holders: list[list[str]],
    ) -> None:
        self._procs = procs
        self._queue = q
        self._reader_threads = reader_threads
        self._interrupt = interrupt
        self._result_holders = result_holders
        self._exhausted = False

    def tagged_lines(self) -> Iterator[tuple[int, str]]:
        """Iterate over ``(reviewer_index, line)`` tuples from all reviewers."""
        try:
            yield from _drain_queue(
                self._queue, self._interrupt, sentinel_count=len(self._procs)
            )
        finally:
            self._exhausted = True
        if self._interrupt.is_set():
            raise KeyboardInterrupt

    def results(self) -> list[str]:
        """Return per-reviewer structured result strings.

        Raises ``RuntimeError`` if ``tagged_lines()`` has not been fully consumed.
        """
        if not self._exhausted:
            raise RuntimeError(
                "tagged_lines() must be exhausted before calling results()"
            )
        return [h[0] for h in self._result_holders]


def start_quorum(
    prompt: str,
    quorum_size: int,
    work_dir: Path | None = None,
    worktree: str = "",
    interrupt: threading.Event | None = None,
) -> QuorumProcess:
    """Launch *quorum_size* parallel Claude reviewers and return a handle."""
    if interrupt is None:
        interrupt = _interrupt

    full_prompt = prompt
    if work_dir:
        full_prompt += (
            f"\n\nYou have a shared workspace directory at {work_dir} for writing "
            "intermediate resources (notes, context files, analysis) that later "
            "agents in this pipeline can read."
        )

    schema_str = json.dumps(ReviewResult.model_json_schema())
    cmd = [
        "claude",
        "-p",
        full_prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema_str,
        "--effort",
        "medium",
        "--dangerously-skip-permissions",
        "--settings",
        json.dumps({"outputStyle": ""}),
    ]

    procs: list[subprocess.Popen] = []
    result_holders: list[list[str]] = []
    threads: list[threading.Thread] = []
    q: queue.Queue = queue.Queue()

    try:
        for i in range(quorum_size):
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                cwd=worktree or None,
            )
            _child_procs.append(proc)
            procs.append(proc)

            rh: list[str] = [""]
            result_holders.append(rh)
            t = threading.Thread(
                target=_reader, args=(proc, q, rh, i), daemon=True
            )
            t.start()
            threads.append(t)
    except Exception:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
        raise

    return QuorumProcess(procs, q, threads, interrupt, result_holders)


def _derive_verdict(result: ReviewResult) -> str:
    """Derive verdict from issues: any critical issue -> NeedsRevision, else Ready."""
    if any(i.severity == "critical" for i in result.issues):
        return "NeedsRevision"
    return "Ready"




def run_review_quorum(
    prompt: str,
    quorum_size: int,
    work_dir: Path | None,
    dry_run: bool,
    worktree: str = "",
    display: Display | None = None,
) -> QuorumResult:
    """Run parallel reviewers with streaming display and structured output."""
    if dry_run:
        if display is not None:
            display.info(f"[dim]\\[DRY RUN] Would launch {quorum_size} parallel reviewers[/dim]")
        r = _dry_run_review()
        return QuorumResult(aggregated=r, per_reviewer=[])

    qp = start_quorum(
        prompt=prompt,
        quorum_size=quorum_size,
        work_dir=work_dir,
        worktree=worktree,
    )

    if display is not None:
        with display.quorum_activity(
            "Reviewing", "review-quorum", reviewer_count=quorum_size
        ) as act:
            for reviewer, line in qp.tagged_lines():
                act.stream_line(line, reviewer=reviewer)
            act.complete("success", "review complete")
    else:
        for _ in qp.tagged_lines():
            pass

    raw_results = qp.results()
    results: list[ReviewResult] = []
    for i, raw in enumerate(raw_results):
        if not raw:
            if display is not None:
                display.warn(f"Reviewer {i + 1}: no structured output")
            continue
        try:
            result = _parse_structured(ReviewResult, raw)
            results.append(result)
        except Exception as e:
            if display is not None:
                display.error(f"Reviewer {i + 1}: parse failed: {e}")

    if not results:
        if display is not None:
            display.error(f"No reviewers produced valid results (0 of {quorum_size}).")
        sys.exit(1)

    if len(results) == 1:
        r = results[0]
        if display is not None:
            display.info(f"Score: {r.score}/20")
        return QuorumResult(aggregated=r, per_reviewer=[r])

    scores_str = " ".join(f"R{i+1}:{r.score}/20" for i, r in enumerate(results))
    med_score = int(median(r.score for r in results))
    if display is not None:
        display.info(f"{scores_str} -> median {med_score}/20")

    med_correctness = int(median(r.correctness for r in results))
    med_completeness = int(median(r.completeness for r in results))
    med_simplicity = int(median(r.simplicity for r in results))
    med_clarity = int(median(r.clarity for r in results))

    all_issues: list[ReviewIssue] = []
    for r in results:
        all_issues.extend(r.issues)
    all_changes: list[str] = []
    for r in results:
        all_changes.extend(r.suggested_changes)

    aggregated = ReviewResult(
        score=med_score,
        correctness=med_correctness,
        completeness=med_completeness,
        simplicity=med_simplicity,
        clarity=med_clarity,
        issues=all_issues,
        suggested_changes=all_changes,
    )
    return QuorumResult(aggregated=aggregated, per_reviewer=results)




from .plan import ApplyFeedbackResult


def _apply_quorum_feedback(
    per_reviewer: list[ReviewResult],
    quorum_size: int,
    path: Path,
    work_dir: Path | None,
    dry_run: bool,
    worktree: str = "",
) -> ApplyFeedbackResult:
    """Synthesize and apply quorum feedback to the plan file."""
    if quorum_size <= 1 or len(per_reviewer) <= 1:
        # Single reviewer path: flat list
        r = per_reviewer[0]
        issues_text = "\n".join(
            f"- [{i.severity.upper()}] {i.description}" for i in r.issues
        ) or "- None"
        changes_text = (
            "\n".join(f"- {c}" for c in r.suggested_changes) or "- None"
        )
        return run_claude_structured(
            prompt=(
                f"Read the plan file at {path} and apply these improvements:\n\n"
                f"Issues found:\n{issues_text}\n\n"
                f"Suggested changes:\n{changes_text}\n\n"
                "Edit the plan file to address each issue. Be precise -- make the specific "
                "changes suggested. Do not add scope or features beyond what is suggested. "
                "Do not remove phases or restructure unless a suggestion explicitly calls for it.\n\n"
                "After applying changes, scan for transitive references: if you changed a task's "
                "endpoint, tool name, file path, or API design, search the entire plan for all "
                "other mentions of the old name/endpoint and update them.\n\n"
                "Prioritize CRITICAL issues. NOTE items are observations -- apply them only if "
                "trivially fixable without introducing new changes or touching additional sections."
            ),
            schema=ApplyFeedbackResult,
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
            dry_run_default=ApplyFeedbackResult(changes_applied=0, summary="(dry run)"),
        )

    # Multiple reviewers: build per-reviewer attributed feedback for synthesis
    n = len(per_reviewer)
    sections: list[str] = []
    for i, r in enumerate(per_reviewer):
        issues_text = "\n".join(
            f"  - [{x.severity.upper()}] {x.description}" for x in r.issues
        ) or "  - None"
        changes_text = (
            "\n".join(f"  - {x}" for x in r.suggested_changes) or "  - None"
        )
        sections.append(
            f"Reviewer {i + 1} (score {r.score}/20, {_derive_verdict(r)}):\n"
            f"  Issues:\n{issues_text}\n"
            f"  Suggested changes:\n{changes_text}"
        )
    reviewer_block = "\n\n".join(sections)

    return run_claude_structured(
        prompt=(
            f"You are given feedback from {n} independent reviewers of an implementation plan.\n\n"
            "Your job:\n"
            f"1. Read the plan file at {path}\n"
            "2. Synthesize the feedback:\n"
            "   - Apply every suggestion that has strong justification (a concrete consequence\n"
            "     if not addressed). Err on the side of applying rather than dropping.\n"
            "   - Merge semantically identical suggestions from different reviewers into one change.\n"
            "   - If reviewers contradict each other, side with the suggestion that has stronger\n"
            "     justification, not just the majority.\n"
            "   - Only drop suggestions that lack justification or are purely stylistic preferences.\n"
            "3. Apply the synthesized changes to the plan file\n"
            "4. After applying changes, scan for transitive references: if you changed a task's\n"
            "   endpoint, tool name, file path, or API design, search the entire plan for all\n"
            "   other mentions of the old name/endpoint and update them. A fix that updates one\n"
            "   location but leaves stale references elsewhere is worse than no fix.\n"
            "5. Prioritize CRITICAL issues. NOTE items are observations -- apply them only if\n"
            "   trivially fixable without introducing new changes or touching additional sections.\n\n"
            f"Reviewer feedback:\n\n{reviewer_block}\n\n"
            "Be precise -- make the specific changes suggested. Do not add scope or features\n"
            "beyond what reviewers suggested. Do not remove phases or restructure unless\n"
            "reviewers explicitly call for it."
        ),
        schema=ApplyFeedbackResult,
        effort="medium",
        work_dir=work_dir,
        dry_run=dry_run,
        worktree=worktree,
        dry_run_default=ApplyFeedbackResult(changes_applied=0, summary="(dry run)"),
    )


def _apply_quorum_fix(
    per_reviewer: list[ReviewResult],
    quorum_size: int,
    work_dir: Path | None,
    dry_run: bool,
    worktree: str = "",
) -> ApplyFeedbackResult:
    """Synthesize code review feedback and apply fixes to the codebase."""
    # Format reviewer feedback for the /rpi-fix skill
    if quorum_size <= 1 or len(per_reviewer) <= 1:
        r = per_reviewer[0]
        issues_text = "\n".join(
            f"- [{i.severity.upper()}] {i.description}" for i in r.issues
        ) or "- None"
        changes_text = (
            "\n".join(f"- {c}" for c in r.suggested_changes) or "- None"
        )
        feedback_block = (
            f"Issues found:\n{issues_text}\n\n"
            f"Suggested fixes:\n{changes_text}"
        )
    else:
        n = len(per_reviewer)
        sections: list[str] = []
        for i, r in enumerate(per_reviewer):
            issues_text = "\n".join(
                f"  - [{x.severity.upper()}] {x.description}" for x in r.issues
            ) or "  - None"
            changes_text = (
                "\n".join(f"  - {x}" for x in r.suggested_changes) or "  - None"
            )
            sections.append(
                f"Reviewer {i + 1} (score {r.score}/20, {_derive_verdict(r)}):\n"
                f"  Issues:\n{issues_text}\n"
                f"  Suggested fixes:\n{changes_text}"
            )
        feedback_block = (
            f"Feedback from {n} independent reviewers:\n\n"
            + "\n\n".join(sections)
        )

    return run_claude_structured(
        prompt=(
            f"Run /rpi-fix with the following reviewer feedback:\n\n{feedback_block}"
        ),
        schema=ApplyFeedbackResult,
        effort="medium",
        work_dir=work_dir,
        dry_run=dry_run,
        worktree=worktree,
        dry_run_default=ApplyFeedbackResult(changes_applied=0, summary="(dry run)"),
    )


def _has_feedback(per_reviewer: list[ReviewResult]) -> bool:
    """Check if any reviewer produced issues or suggested changes."""
    return any(r.issues or r.suggested_changes for r in per_reviewer)




def _write_iteration_history(
    history: list[IterationRecord],
    loop_type: str,
    work_dir: Path,
) -> Path:
    """Write cumulative iteration history to the workspace for downstream agents."""
    history_path = work_dir / f"{loop_type}-history.md"
    text = _format_iteration_history(history, loop_type)
    header = (
        f"# {loop_type.replace('_', ' ').title()} -- Iteration History\n\n"
        "This file records what previous review iterations flagged and what\n"
        "changes were applied. Use it to avoid re-flagging issues that were\n"
        "already addressed and to avoid reverting intentional changes.\n\n"
    )
    history_path.write_text(header + text + "\n")
    return history_path


def _format_iteration_history(
    history: list[IterationRecord],
    loop_type: str,
) -> str:
    """Format iteration history for the diagnosis agent prompt."""
    sections: list[str] = []
    for rec in history:
        agg = rec.aggregated
        lines = [
            f"### Iteration {rec.iteration}",
            f"Aggregated: score={agg.score}/20 ({agg.score // 2}/10), verdict={_derive_verdict(agg)}",
            f"  correctness={agg.correctness}, completeness={agg.completeness}, "
            f"simplicity={agg.simplicity}, clarity={agg.clarity}",
        ]
        if len(rec.per_reviewer) > 1:
            for i, r in enumerate(rec.per_reviewer):
                lines.append(
                    f"  Reviewer {i + 1}: score={r.score}/20, verdict={_derive_verdict(r)}"
                )
        if agg.issues:
            lines.append("Issues:")
            for issue in agg.issues:
                lines.append(f"  - [{issue.severity.upper()}] {issue.description}")
        if agg.suggested_changes:
            lines.append("Suggested changes:")
            for change in agg.suggested_changes:
                lines.append(f"  - {change}")
        if rec.apply_summary:
            lines.append(f"Fix applied: {rec.apply_summary}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
