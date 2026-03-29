"""Plan review stage: iterative review with quorum."""

from __future__ import annotations

import sys

from ..display import display
from ..diagnosis import print_diagnosis, run_diagnosis
from ..plan import run_plan_processing
from ..process import confirm
from ..review import (
    IterationRecord,
    _apply_quorum_feedback,
    _derive_verdict,
    _has_feedback,
    _write_iteration_history,
    run_review_quorum,
)
from ..snapshot import SnapshotReviewProgress, save_snapshot


class PlanReviewStage:
    name = "plan_review"
    label = "Stage 1: Plan Review"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_plan_review or ctx.config.skip_implement

    def run(self, ctx) -> None:
        config = ctx.config
        path = config.plan_path
        work_dir = ctx.work_dir

        display.stage_header(
            f"Stage 1: Plan Review (target >= {config.min_score}/10, "
            f"max {config.max_review_iters} iter)"
        )

        score_10 = 0
        history: list[IterationRecord] = []

        for iteration in range(1, config.max_review_iters + 1):
            display.console.rule(
                f"[bold]Plan review iteration {iteration}/{config.max_review_iters}[/bold]"
            )

            review_prompt = f"Run /rpi-plan-review on the plan file at {path}."
            if history:
                history_path = work_dir / "plan_review-history.md"
                review_prompt += (
                    f"\n\nIMPORTANT: This is iteration {iteration}. Read the "
                    f"iteration history at {history_path} before reviewing. It "
                    "records what previous reviewers flagged and what changes were "
                    "applied. Do not re-flag issues that were already addressed."
                )

            quorum_result = run_review_quorum(
                prompt=review_prompt,
                quorum_size=config.review_quorum,
                work_dir=work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
            )
            result = quorum_result.aggregated

            score_10 = result.score // 2
            score_style = "green" if score_10 >= config.min_score else "yellow"
            display.info(
                f"Score: [{score_style}]{score_10}/10[/{score_style}] ({result.score}/20), "
                f"Verdict: {_derive_verdict(result)}"
            )
            if result.issues:
                n_critical = sum(1 for i in result.issues if i.severity == "critical")
                n_notes = sum(1 for i in result.issues if i.severity == "note")
                display.info(f"Issues: {len(result.issues)} ({n_critical} critical, {n_notes} notes)")
                for issue in result.issues[:5]:
                    display.info(f"  - \\[{issue.severity.upper()}] {issue.description[:100]}")
                if len(result.issues) > 5:
                    display.info(f"  ... and {len(result.issues) - 5} more")

            # Apply feedback on both pass and fail paths
            apply_summary = ""
            if _has_feedback(quorum_result.per_reviewer):
                display.info("Applying review feedback to plan...")
                apply_result = _apply_quorum_feedback(
                    per_reviewer=quorum_result.per_reviewer,
                    quorum_size=config.review_quorum,
                    path=path,
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                )
                display.result_panel("Applied Feedback", apply_result)
                apply_summary = f"{apply_result.changes_applied} changes: {apply_result.summary}"

            history.append(IterationRecord(
                iteration=iteration,
                aggregated=result,
                per_reviewer=quorum_result.per_reviewer,
                apply_summary=apply_summary,
            ))
            _write_iteration_history(history, "plan_review", work_dir)

            if score_10 >= config.min_score and _derive_verdict(result) == "Ready":
                display.info(
                    f"[green]Plan review passed:[/green] {score_10}/10 after {iteration} iteration(s)."
                )
                ctx.review_score = score_10
                ctx.review_iters = iteration
                break

            if iteration >= config.max_review_iters:
                # Loop exhausted -- run diagnosis before prompting user
                display.warn(
                    f"Plan review did not converge after {config.max_review_iters} iterations (score: {score_10}/10)."
                )
                display.info("Running convergence diagnosis...")
                diagnosis = run_diagnosis(
                    history=history,
                    loop_type="plan_review",
                    plan_path=config.plan_path,
                    min_score=config.min_score,
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                )
                print_diagnosis(diagnosis, "plan_review")
                if not confirm("  Proceed to implementation anyway? (y/n): "):
                    display.info("Stopped.")
                    sys.exit(1)

                ctx.review_score = score_10
                ctx.review_iters = config.max_review_iters
                break
        else:
            ctx.review_score = score_10
            ctx.review_iters = config.max_review_iters

        display.info(
            f"[green]Stage 1 complete:[/green] score {ctx.review_score}/10 "
            f"after {ctx.review_iters} iteration(s)"
        )

        ctx.progress.plan_review_done = True
        ctx.progress.plan_review = SnapshotReviewProgress(
            completed_iterations=ctx.review_iters,
            last_score=ctx.review_score,
        )
        self._snapshot(ctx)

        # Re-parse after review -- review may have modified the plan structure
        display.info("Re-parsing plan after review modifications...")
        ctx.plan = run_plan_processing(config, work_dir)
        display.info(
            f"Re-parsed: {len(ctx.plan.phases)} phases, "
            f"{sum(len(p.tasks) for p in ctx.plan.phases)} tasks"
        )
        self._snapshot(ctx)

    def execute(self, ctx) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            return
        self.run(ctx)

    def _snapshot(self, ctx) -> None:
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.plan, ctx.work_dir)
