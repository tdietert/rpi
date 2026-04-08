"""Pipeline stage ABC and coordination context."""

from __future__ import annotations

import shutil
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
    work_plan: Path | None = None
    work_spec: Path | None = None
    work_research: Path | None = None

    def ensure_work_copies(self) -> None:
        """Copy canonical plan/spec/research to work_dir for agent access.

        Agents running in worktrees cannot access files under .claude/ in
        the main repo.  This copies them to work_dir and sets work_* fields
        so every downstream stage has a guaranteed-valid path.
        """
        if self.config.plan_path and self.config.plan_path.is_file():
            wp = self.work_dir / self.config.plan_path.name
            if self.config.plan_path != wp:
                shutil.copy2(self.config.plan_path, wp)
            self.work_plan = wp

        if self.spec_path and self.spec_path.is_file():
            ws = self.work_dir / self.spec_path.name
            if self.spec_path != ws:
                shutil.copy2(self.spec_path, ws)
            self.work_spec = ws

        if self.research_path and self.research_path.is_file():
            wr = self.work_dir / self.research_path.name
            if self.research_path != wr:
                shutil.copy2(self.research_path, wr)
            self.work_research = wr


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

    for sn in (StageName.research, StageName.spec, StageName.plan):
        if skips_stage(config, sn):
            rows.append((sn.label, "", skip_label()))
        elif sn.progress_key and getattr(ctx.progress, sn.progress_key, False):
            rows.append((sn.label, icon(True), "done"))
        else:
            rows.append((sn.label, "", "in progress"))

    if meta is not None:
        if skips_stage(config, StageName.plan_review):
            rows.append((StageName.plan_review.label, "", skip_label()))
        else:
            ok = ctx.review_score is not None and ctx.review_score >= config.min_score
            detail = f"score {ctx.review_score}/10, {ctx.review_iters} iter"
            rows.append((StageName.plan_review.label, icon(ok), detail))

        if skips_stage(config, StageName.implement):
            rows.append((StageName.implement.label, "", skip_label()))
        else:
            n_phases = len(ctx.plan.phases) if ctx.plan else 0
            rows.append((StageName.implement.label, icon(True), f"{n_phases} phases"))

    if skips_stage(config, StageName.review_fix):
        rows.append((StageName.review_fix.label, "", skip_label()))
    else:
        ok = ctx.fix_status == "clean"
        detail = f"score {ctx.fix_score}/10, {ctx.fix_iters} iter"
        if ctx.fix_status != "clean":
            detail += f" ({ctx.fix_status})"
        rows.append((StageName.review_fix.label, icon(ok), detail))

    if skips_stage(config, StageName.commit):
        rows.append((StageName.commit.label, "", skip_label()))
    elif ctx.commit_result:
        ok = ctx.commit_result.status == "success"
        detail = (
            f"{ctx.commit_result.num_commits} commits"
            if ok
            else ctx.commit_result.status
        )
        rows.append((StageName.commit.label, icon(ok), detail))

    if config.push:
        rows.append(("Push", icon(ctx.push_ok), "done" if ctx.push_ok else "failed"))
    elif skips_stage(config, StageName.push_pr):
        rows.append((StageName.push_pr.label, "", skip_label()))
    elif ctx.pr_result:
        ok = ctx.pr_result.status == "success"
        detail = ctx.pr_result.pr_url if ok else ctx.pr_result.status
        rows.append((StageName.push_pr.label, icon(ok), detail))

    footer = {}
    if config.plan_path:
        footer["Plan"] = filelink(config.plan_path)
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
