"""Review-fix stage: iterative code review with quorum."""

from __future__ import annotations

import sys

from ..diagnosis import print_diagnosis, run_diagnosis
from ..display import display
from ..process import confirm
from ..review import (
    IterationRecord,
    _apply_quorum_fix,
    _derive_verdict,
    _has_feedback,
    _write_iteration_history,
    run_review_quorum,
)
from ..snapshot import SnapshotReviewProgress
from . import Stage


class ReviewFixStage(Stage):
    name = "review_fix"
    label = "Stage 3: Review-Fix"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_fix

    def run(self, ctx) -> None:
        config = ctx.config
        work_dir = ctx.work_dir

        display.stage_header(
            f"Stage 3: Review-Fix (target >= {config.min_score}/10, "
            f"max {config.max_fix_iters} iter)"
        )

        score_10 = 0
        history: list[IterationRecord] = []

        for iteration in range(1, config.max_fix_iters + 1):
            display.console.rule(
                f"[bold]Review-fix iteration {iteration}/{config.max_fix_iters}[/bold]"
            )

            review_prompt = (
                "Run /rpi-review to review all uncommitted code changes for "
                "correctness, code quality, and potential issues."
            )
            if history:
                history_path = work_dir / "review_fix-history.md"
                review_prompt += (
                    f"\n\nIMPORTANT: This is iteration {iteration}. Read the "
                    f"iteration history at {history_path} before reviewing. It "
                    "records what previous reviewers flagged and what fixes were "
                    "applied. Do not re-flag issues that were already fixed."
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

            # Apply fixes on both pass and fail paths
            apply_summary = ""
            if _has_feedback(quorum_result.per_reviewer):
                display.info("Applying review fixes...")
                apply_result = _apply_quorum_fix(
                    per_reviewer=quorum_result.per_reviewer,
                    quorum_size=config.review_quorum,
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                )
                display.result_panel("Applied Fix", apply_result)
                apply_summary = f"{apply_result.changes_applied} fixes: {apply_result.summary}"

            history.append(IterationRecord(
                iteration=iteration,
                aggregated=result,
                per_reviewer=quorum_result.per_reviewer,
                apply_summary=apply_summary,
            ))
            _write_iteration_history(history, "review_fix", work_dir)

            if score_10 >= config.min_score and _derive_verdict(result) == "Ready":
                display.info(
                    f"[green]Review-fix passed:[/green] {score_10}/10 after {iteration} iteration(s)."
                )
                ctx.fix_status = "clean"
                ctx.fix_score = score_10
                ctx.fix_iters = iteration
                break

            if iteration >= config.max_fix_iters:
                # Loop exhausted -- run diagnosis before prompting user
                display.warn(
                    f"Review-fix did not converge after {config.max_fix_iters} iterations (score: {score_10}/10)."
                )
                display.info("Running convergence diagnosis...")
                diagnosis = run_diagnosis(
                    history=history,
                    loop_type="review_fix",
                    plan_path=config.plan_path,
                    min_score=config.min_score,
                    work_dir=work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                )
                print_diagnosis(diagnosis, "review_fix")
                if not confirm("  Proceed to commit+PR anyway? (y/n): "):
                    display.info("Stopped.")
                    sys.exit(1)

                ctx.fix_status = "issues_remaining"
                ctx.fix_score = score_10
                ctx.fix_iters = config.max_fix_iters
                break
        else:
            ctx.fix_status = "issues_remaining"
            ctx.fix_score = score_10
            ctx.fix_iters = config.max_fix_iters

        display.info(
            f"[green]Stage 3 complete:[/green] score {ctx.fix_score}/10 "
            f"after {ctx.fix_iters} iteration(s)"
        )

        ctx.progress.review_fix_done = True
        ctx.progress.review_fix = SnapshotReviewProgress(
            completed_iterations=ctx.fix_iters,
            last_score=ctx.fix_score,
        )

    def execute(self, ctx) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            return
        self.run(ctx)
        self._snapshot(ctx)
