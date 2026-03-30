"""Cross-cutting types used throughout the RPI pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Effort = Literal["low", "medium", "high"]


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
class IterationRecord:
    iteration: int
    aggregated: ReviewResult
    per_reviewer: list[ReviewResult]
    apply_summary: str  # empty if no feedback was applied


def derive_verdict(result: ReviewResult) -> str:
    """Derive verdict from issues: any critical issue -> NeedsRevision, else Ready."""
    if any(i.severity == "critical" for i in result.issues):
        return "NeedsRevision"
    return "Ready"


def format_iteration_history(
    history: list[IterationRecord],
    loop_type: str,
) -> str:
    """Format iteration history for the diagnosis agent prompt."""
    sections: list[str] = []
    for rec in history:
        agg = rec.aggregated
        lines = [
            f"### Iteration {rec.iteration}",
            f"Aggregated: score={agg.score}/20 ({agg.score // 2}/10), verdict={derive_verdict(agg)}",
            f"  correctness={agg.correctness}, completeness={agg.completeness}, "
            f"simplicity={agg.simplicity}, clarity={agg.clarity}",
        ]
        if len(rec.per_reviewer) > 1:
            for i, r in enumerate(rec.per_reviewer):
                lines.append(
                    f"  Reviewer {i + 1}: score={r.score}/20, verdict={derive_verdict(r)}"
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


class ApplyFeedbackResult(BaseModel):
    changes_applied: int
    summary: str = Field(
        description="One-line summary of changes made to the plan"
    )


class Config(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    plan_path: Path | None = None
    min_score: int = 8  # threshold on 0-10 scale (ReviewResult.score / 2)
    max_review_iters: int = 3
    max_fix_iters: int = 3
    review_quorum: int = 3  # number of parallel reviewers
    skip_plan_review: bool = False
    skip_implement: bool = False
    skip_fix: bool = False
    skip_commit: bool = False
    skip_pr: bool = False
    push: bool = False  # push to current branch after commit (implies skip_pr)
    worktree: str = ""  # if non-empty, absolute path to a pre-created git worktree
    dry_run: bool = False
    prompt: str = ""
    skip_research: bool = False
    research_path: Path | None = None


class SnapshotPhaseProgress(BaseModel):
    """Tracks which implementation phases have completed."""
    completed_phases: list[int] = Field(default_factory=list)
    phase_attempts: dict[str, int] = Field(default_factory=dict)


class SnapshotReviewProgress(BaseModel):
    """Tracks progress within a review loop."""
    completed_iterations: int = 0
    last_score: int | None = None


class SnapshotStageProgress(BaseModel):
    """Tracks which stages are done and in-flight progress."""
    plan_review_done: bool = False
    plan_review: SnapshotReviewProgress | None = None
    implementation_done: bool = False
    implementation: SnapshotPhaseProgress | None = None
    review_fix_done: bool = False
    review_fix: SnapshotReviewProgress | None = None
    commit_done: bool = False
    push_or_pr_done: bool = False
    research_done: bool = False
    plan_draft_done: bool = False
