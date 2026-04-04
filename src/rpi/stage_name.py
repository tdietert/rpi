"""Stage name enum — leaf module with no internal imports."""

from __future__ import annotations

from enum import Enum


class StageName(Enum):
    research = "research"
    spec = "spec"
    plan = "plan"
    preflight = "preflight"
    plan_review = "plan_review"
    implement = "implement"
    review_fix = "review_fix"
    commit = "commit"
    push_pr = "push_pr"

    @property
    def label(self) -> str:
        """Human-readable display label for banners and summaries."""
        return _LABELS[self]

    @property
    def progress_key(self) -> str | None:
        """Field name on SnapshotStageProgress, or None if not tracked."""
        return _PROGRESS_KEYS.get(self)

    @property
    def skip_config_key(self) -> str | None:
        """Field name on Config to skip this stage, or None."""
        return _SKIP_CONFIG_KEYS.get(self)


_LABELS: dict[StageName, str] = {
    StageName.research: "Research",
    StageName.spec: "Spec",
    StageName.plan: "Plan",
    StageName.preflight: "Preflight",
    StageName.plan_review: "Plan Review",
    StageName.implement: "Implement",
    StageName.review_fix: "Review-Fix",
    StageName.commit: "Commit",
    StageName.push_pr: "PR",
}

_SKIP_CONFIG_KEYS: dict[StageName, str] = {
    StageName.plan_review: "skip_plan_review",
    StageName.implement: "skip_implement",
    StageName.review_fix: "skip_fix",
    StageName.commit: "skip_commit",
    StageName.push_pr: "skip_pr",
}

_PROGRESS_KEYS: dict[StageName, str] = {
    StageName.research: "research_done",
    StageName.spec: "spec_done",
    StageName.plan: "plan_done",
    StageName.plan_review: "plan_review_done",
    StageName.implement: "implementation_done",
    StageName.review_fix: "review_fix_done",
    StageName.commit: "commit_done",
    StageName.push_pr: "push_or_pr_done",
}
