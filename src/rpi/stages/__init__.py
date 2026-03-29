"""Pipeline stage ABC and coordination context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..display import display
from ..plan import Plan, PlanMetadata
from ..research import Research
from ..snapshot import SnapshotPhaseProgress, SnapshotStageProgress, save_snapshot
from ..types import Config


@dataclass
class PipelineContext:
    """Mutable state threaded through all pipeline stages."""
    config: Config
    work_dir: Path
    snap_dir: Path | None
    progress: SnapshotStageProgress
    meta: PlanMetadata | None = None
    plan: Plan | None = None
    review_score: int | None = None
    review_iters: int = 0
    fix_status: str = "skipped"
    fix_score: int | None = None
    fix_iters: int = 0
    commit_result: Any = None  # CommitResult
    push_ok: bool = False
    pr_result: Any = None  # PrResult
    resume_completed_phases: set[int] = field(default_factory=set)
    research_path: Path | None = None
    research: Research | None = None


class Stage(ABC):
    name: str
    label: str

    @abstractmethod
    def should_skip(self, ctx: PipelineContext) -> bool: ...

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None: ...

    def execute(self, ctx: PipelineContext) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            return
        display.stage_header(self.label)
        self.run(ctx)
        self._snapshot(ctx)

    def _snapshot(self, ctx: PipelineContext) -> None:
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.plan, ctx.work_dir)


def print_summary(ctx: PipelineContext) -> None:
    """Print a formatted run summary."""
    config = ctx.config
    meta = ctx.meta

    def icon(ok: bool) -> str:
        return "[green]ok[/green]" if ok else "[red]--[/red]"

    def skip_label() -> str:
        return "[dim]skipped[/dim]"

    rows: list[tuple[str, str, str]] = []

    # Research
    if ctx.config.skip_research or ctx.config.plan_path is not None:
        rows.append(("Research", "", skip_label()))
    elif ctx.progress.research_done:
        rows.append(("Research", icon(True), "done"))
    elif not ctx.config.skip_research:
        rows.append(("Research", "", "in progress"))

    # Plan Draft
    if ctx.config.plan_path is not None and not ctx.progress.plan_draft_done:
        rows.append(("Plan Draft", "", skip_label()))
    elif ctx.progress.plan_draft_done:
        rows.append(("Plan Draft", icon(True), "done"))
    elif ctx.config.plan_path is None:
        rows.append(("Plan Draft", "", "in progress"))

    if meta is not None:
        # Plan review
        if config.skip_implement or config.skip_plan_review:
            rows.append(("Plan Review", "", skip_label()))
        else:
            ok = ctx.review_score is not None and ctx.review_score >= config.min_score
            detail = f"score {ctx.review_score}/10, {ctx.review_iters} iter"
            rows.append(("Plan Review", icon(ok), detail))

        # Implementation
        if config.skip_implement:
            rows.append(("Implement", "", skip_label()))
        else:
            n_phases = len(ctx.plan.phases) if ctx.plan else 0
            rows.append(("Implement", icon(True), f"{n_phases} phases"))

    # Review-fix
    if config.skip_fix:
        rows.append(("Review-Fix", "", skip_label()))
    else:
        ok = ctx.fix_status == "clean"
        detail = f"score {ctx.fix_score}/10, {ctx.fix_iters} iter"
        if ctx.fix_status != "clean":
            detail += f" ({ctx.fix_status})"
        rows.append(("Review-Fix", icon(ok), detail))

    # Commit
    if config.skip_commit:
        rows.append(("Commit", "", skip_label()))
    elif ctx.commit_result:
        ok = ctx.commit_result.status == "success"
        detail = (
            f"{ctx.commit_result.num_commits} commits"
            if ok
            else ctx.commit_result.status
        )
        rows.append(("Commit", icon(ok), detail))

    # Push / PR
    if config.push:
        rows.append(("Push", icon(ctx.push_ok), "done" if ctx.push_ok else "failed"))
    elif config.skip_pr:
        rows.append(("PR", "", skip_label()))
    elif ctx.pr_result:
        ok = ctx.pr_result.status == "success"
        detail = ctx.pr_result.pr_url if ok else ctx.pr_result.status
        rows.append(("PR", icon(ok), detail))

    footer = {}
    if config.worktree:
        footer["Worktree"] = config.worktree

    display.summary_table(f"Summary  {meta.title if meta else 'RPI Run'}", rows, footer or None)


# Re-export stage classes for convenience
from .plan_draft import PlanDraftStage
from .preflight import PreflightStage
from .research import ResearchStage
from .plan_review import PlanReviewStage
from .implement import ImplementStage
from .review_fix import ReviewFixStage
from .commit import CommitStage
from .push_pr import PushPrStage

__all__ = [
    "PipelineContext",
    "Stage",
    "print_summary",
    "ResearchStage",
    "PlanDraftStage",
    "PreflightStage",
    "PlanReviewStage",
    "ImplementStage",
    "ReviewFixStage",
    "CommitStage",
    "PushPrStage",
]
