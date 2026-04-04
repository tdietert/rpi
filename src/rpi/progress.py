"""Snapshot progress tracking types."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .stage_name import StageName


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

    def completed_stages(self) -> list[StageName]:
        """Return stages marked done, in pipeline order."""
        return [
            s for s in StageName
            if s.progress_key and getattr(self, s.progress_key, False)
        ]
