"""Snapshot progress tracking types."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    spec_done: bool = False
    plan_done: bool = False
