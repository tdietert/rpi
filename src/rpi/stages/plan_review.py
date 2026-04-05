"""Plan review stage: iterative review with quorum."""

from __future__ import annotations

import shutil

from ..display import filelink, green
from ..plan import run_plan_processing
from ..progress import SnapshotReviewProgress
from ..review import ReviewLoopConfig, run_review_loop
from ..stage_name import StageName
from . import Stage


class PlanReviewStage(Stage):
    name = StageName.plan_review
    label = "Stage 1: Plan Review"

    def run(self, ctx) -> None:
        config = ctx.config

        # Agents cannot edit files inside .claude/, so ensure the plan
        # lives in work_dir before handing it to reviewers.
        work_plan = ctx.work_dir / config.plan_path.name
        if config.plan_path != work_plan:
            shutil.copy2(config.plan_path, work_plan)
            ctx.display.info(f"Plan copied to: {filelink(work_plan)}")

        ctx.display.stage_header(
            f"Stage 1: Plan Review (target >= {config.min_score}/10, "
            f"max {config.max_review_iters} iter)"
        )

        loop_config = ReviewLoopConfig(
            loop_type="plan_review",
            review_prompt=f"Run /rpi-plan-review on the plan file at {work_plan}.",
            history_noun="changes",
            apply_label="Applying review feedback to plan...",
            apply_noun="changes",
            pass_message="Plan review passed:",
            failure_prompt="Proceed to implementation anyway?",
            max_iters=config.max_review_iters,
            min_score=config.min_score,
            review_quorum=config.review_quorum,
            plan_path=work_plan,
            work_dir=ctx.work_dir,
            dry_run=config.dry_run,
            worktree=config.worktree,
            apply_path=work_plan,
        )
        result = run_review_loop(loop_config, ctx.display)

        ctx.review_score = result.score
        ctx.review_iters = result.iterations

        ctx.display.info(
            f"{green('Stage 1 complete:')} score {ctx.review_score}/10 "
            f"after {ctx.review_iters} iteration(s)"
        )

        ctx.progress.plan_review_done = True
        ctx.progress.plan_review = SnapshotReviewProgress(
            completed_iterations=ctx.review_iters,
            last_score=ctx.review_score,
        )
        self._snapshot(ctx)

        # Copy reviewed plan back to canonical location
        if config.plan_path != work_plan:
            shutil.copy2(work_plan, config.plan_path)
            ctx.display.info(f"Plan updated at: {filelink(config.plan_path)}")

        # Re-parse after review -- review may have modified the plan structure
        ctx.display.info("Re-parsing plan after review modifications...")
        ctx.plan = run_plan_processing(config, ctx.work_dir, ctx.display)
        ctx.display.info(
            f"Re-parsed: {len(ctx.plan.phases)} phases, "
            f"{sum(len(p.tasks) for p in ctx.plan.phases)} tasks"
        )
        self._snapshot(ctx)

    def execute(self, ctx) -> None:
        self.run(ctx)
