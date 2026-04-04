"""Pipeline stage ABC and coordination context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..display import Display, dim, filelink, green, red
from ..plan import Plan, PlanMetadata
from ..progress import SnapshotStageProgress
from ..snapshot import save_snapshot
from ..stage_name import StageName


@dataclass
class PipelineContext:
    """Mutable state threaded through all pipeline stages."""
    config: Config
    work_dir: Path
    snap_dir: Path | None
    progress: SnapshotStageProgress
    display: Display
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
    spec_path: Path | None = None


class Stage(ABC):
    name: StageName
    label: str

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None: ...

    def execute(self, ctx: PipelineContext) -> None:
        ctx.display.stage_header(self.label)
        self.run(ctx)
        self._snapshot(ctx)

    def _snapshot(self, ctx: PipelineContext) -> None:
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.plan, ctx.work_dir)


# Re-export stage classes for convenience
from .commit import CommitStage
from .implement import ImplementStage
from .plan import PlanStage
from .plan_review import PlanReviewStage
from .preflight import PreflightStage
from .push_pr import PushPrStage
from .research import ResearchStage
from .review_fix import ReviewFixStage
from .spec import SpecStage

PIPELINE: tuple[type[Stage], ...] = (
    ResearchStage,
    SpecStage,
    PlanStage,
    PreflightStage,
    PlanReviewStage,
    ImplementStage,
    ReviewFixStage,
    CommitStage,
    PushPrStage,
)

_STAGE_ORDER: dict[StageName, int] = {S.name: i for i, S in enumerate(PIPELINE)}

_SKIP_FLAGS: dict[StageName, Callable[[Config], bool]] = {
    StageName.plan_review: lambda c: c.skip_plan_review or c.skip_implement,
    StageName.preflight: lambda c: c.skip_implement,
    StageName.implement: lambda c: c.skip_implement,
    StageName.review_fix: lambda c: c.skip_fix,
    StageName.commit: lambda c: c.skip_commit,
    StageName.push_pr: lambda c: c.skip_pr and not c.push,
}


def skips_stage(config: Config, stage: StageName) -> bool:
    """Determine whether a stage should be skipped based on config."""
    if _STAGE_ORDER[config.start_from] > _STAGE_ORDER[stage]:
        return True
    flag_fn = _SKIP_FLAGS.get(stage)
    return flag_fn(config) if flag_fn else False


def print_summary(ctx: PipelineContext, *, total_elapsed: float | None = None) -> None:
    """Print a formatted run summary."""
    config = ctx.config
    meta = ctx.meta

    def icon(ok: bool) -> str:
        return green("ok") if ok else red("--")

    def skip_label() -> str:
        return dim("skipped")

    rows: list[tuple[str, str, str]] = []

    # Research
    if skips_stage(config, StageName.research):
        rows.append(("Research", "", skip_label()))
    elif ctx.progress.research_done:
        rows.append(("Research", icon(True), "done"))
    else:
        rows.append(("Research", "", "in progress"))

    # Spec
    if skips_stage(config, StageName.spec):
        rows.append(("Spec", "", skip_label()))
    elif ctx.progress.spec_done:
        rows.append(("Spec", icon(True), "done"))
    else:
        rows.append(("Spec", "", "in progress"))

    # Plan
    if skips_stage(config, StageName.plan):
        rows.append(("Plan", "", skip_label()))
    elif ctx.progress.plan_done:
        rows.append(("Plan", icon(True), "done"))
    else:
        rows.append(("Plan", "", "in progress"))

    if meta is not None:
        # Plan review
        if skips_stage(config, StageName.plan_review):
            rows.append(("Plan Review", "", skip_label()))
        else:
            ok = ctx.review_score is not None and ctx.review_score >= config.min_score
            detail = f"score {ctx.review_score}/10, {ctx.review_iters} iter"
            rows.append(("Plan Review", icon(ok), detail))

        # Implementation
        if skips_stage(config, StageName.implement):
            rows.append(("Implement", "", skip_label()))
        else:
            n_phases = len(ctx.plan.phases) if ctx.plan else 0
            rows.append(("Implement", icon(True), f"{n_phases} phases"))

    # Review-fix
    if skips_stage(config, StageName.review_fix):
        rows.append(("Review-Fix", "", skip_label()))
    else:
        ok = ctx.fix_status == "clean"
        detail = f"score {ctx.fix_score}/10, {ctx.fix_iters} iter"
        if ctx.fix_status != "clean":
            detail += f" ({ctx.fix_status})"
        rows.append(("Review-Fix", icon(ok), detail))

    # Commit
    if skips_stage(config, StageName.commit):
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
    elif skips_stage(config, StageName.push_pr):
        rows.append(("PR", "", skip_label()))
    elif ctx.pr_result:
        ok = ctx.pr_result.status == "success"
        detail = ctx.pr_result.pr_url if ok else ctx.pr_result.status
        rows.append(("PR", icon(ok), detail))

    footer = {}
    if config.worktree:
        footer["Worktree"] = filelink(config.worktree)

    ctx.display.summary_table(f"Summary  {meta.title if meta else 'RPI Run'}", rows, footer or None, total_elapsed=total_elapsed)


__all__ = [
    "PIPELINE",
    "CommitStage",
    "ImplementStage",
    "PipelineContext",
    "PlanReviewStage",
    "PlanStage",
    "PreflightStage",
    "PushPrStage",
    "ResearchStage",
    "ReviewFixStage",
    "SpecStage",
    "Stage",
    "print_summary",
    "skips_stage",
]
