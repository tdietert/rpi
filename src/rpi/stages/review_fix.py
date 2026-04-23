"""Review-fix stage: iterative code review with quorum."""

from __future__ import annotations

from rich.text import Text

from ..display import green
from ..progress import SnapshotReviewProgress
from ..review import ReviewLoopConfig, run_review_loop
from ..stage_name import StageName
from . import Stage


class ReviewFixStage(Stage):
    name = StageName.review_fix
    label = "Stage 3: Review-Fix"

    def run(self, ctx) -> None:
        config = ctx.config

        ctx.display.stage_header(
            f"Stage 3: Review-Fix (target >= {config.min_score}/10, "
            f"max {config.max_fix_iters} iter)"
        )

        loop_config = ReviewLoopConfig(
            loop_type="review_fix",
            review_prompt=(
                "Run /rpi-review to review all uncommitted code changes for "
                "correctness, code quality, and potential issues."
            ),
            history_noun="fixes",
            apply_label="Applying review fixes...",
            apply_noun="fixes",
            pass_message="Review-fix passed:",
            failure_prompt="Proceed to commit+PR anyway?",
            max_iters=config.max_fix_iters,
            min_score=config.min_score,
            review_quorum=config.review_quorum,
            plan_path=ctx.work_plan or config.plan_path,
            work_dir=ctx.work_dir,
            dry_run=config.dry_run,
            worktree=config.worktree,
            apply_path=None,  # code fix path
        )
        result = run_review_loop(loop_config, ctx.display)

        ctx.fix_status = "clean" if result.converged else "issues_remaining"
        ctx.fix_score = result.score
        ctx.fix_iters = result.iterations

        ctx.display.info(
            Text.assemble(
                green("Stage 3 complete:"),
                f" score {ctx.fix_score}/10 after {ctx.fix_iters} iteration(s)",
            )
        )

        ctx.progress.review_fix_done = True
        ctx.progress.review_fix = SnapshotReviewProgress(
            completed_iterations=ctx.fix_iters,
            last_score=ctx.fix_score,
        )

    def execute(self, ctx) -> None:
        self.run(ctx)
        self._snapshot(ctx)
