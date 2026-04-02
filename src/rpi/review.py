"""Review quorum logic, feedback application, and generic review loop."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .display import Display
from .iteration import (
    ApplyFeedbackResult,
    IterationRecord,
    ReviewIssue,
    ReviewResult,
    derive_verdict,
    format_iteration_history,
)
from .process import (
    _parse_structured,
    make_dry_run_default,
    run_claude_with_display,
    start_quorum,
)


@dataclass
class QuorumResult:
    aggregated: ReviewResult
    per_reviewer: list[ReviewResult]


@dataclass
class ReviewLoopConfig:
    loop_type: str              # "plan_review" or "review_fix"
    review_prompt: str          # base prompt text
    history_noun: str           # "changes" or "fixes"
    apply_label: str            # "Applying review feedback to plan..." etc.
    apply_noun: str             # "changes" or "fixes"
    pass_message: str           # "Plan review passed:" etc.
    failure_prompt: str         # "Proceed to implementation anyway?" etc.
    max_iters: int
    min_score: int
    review_quorum: int
    plan_path: Path | None
    work_dir: Path
    dry_run: bool
    worktree: str
    apply_path: Path | None     # non-None = plan feedback; None = code fix


@dataclass
class ReviewLoopResult:
    score: int          # final score on 0-10 scale
    iterations: int     # iterations actually run
    converged: bool     # True if passed threshold


def run_review_quorum(
    prompt: str,
    quorum_size: int,
    work_dir: Path | None,
    dry_run: bool,
    worktree: str = "",
    display: Display | None = None,
) -> QuorumResult:
    """Run parallel reviewers with streaming display and structured output."""
    if dry_run:
        if display is not None:
            display.info(f"[dim]\\[DRY RUN] Would launch {quorum_size} parallel reviewers[/dim]")
        r = make_dry_run_default(ReviewResult)
        # Override scores to max so the dry-run review is accepted on the first
        # iteration.  Without this, auto-generated defaults may produce scores
        # below the passing threshold, causing the review loop to re-iterate.
        r.correctness = 5
        r.completeness = 5
        r.simplicity = 5
        r.clarity = 5
        r.score = 20
        return QuorumResult(aggregated=r, per_reviewer=[])

    qp = start_quorum(
        prompt,
        quorum_size,
        json_schema_str=json.dumps(ReviewResult.model_json_schema()),
        work_dir=work_dir,
        worktree=worktree,
    )

    if display is not None:
        with display.quorum_activity(
            "Reviewing", "review-quorum", reviewer_count=quorum_size
        ) as act:
            for reviewer, line in qp.tagged_lines():
                act.stream_line(line, reviewer=reviewer)
            act.complete("success", "review complete")
    else:
        for _ in qp.tagged_lines():
            pass

    raw_results = qp.results()
    results: list[ReviewResult] = []
    for i, raw in enumerate(raw_results):
        if not raw:
            if display is not None:
                display.warn(f"Reviewer {i + 1}: no structured output")
            continue
        try:
            result = _parse_structured(ReviewResult, raw)
            results.append(result)
        except Exception as e:
            if display is not None:
                display.error(f"Reviewer {i + 1}: parse failed: {e}")

    if not results:
        if display is not None:
            display.error(f"No reviewers produced valid results (0 of {quorum_size}).")
        sys.exit(1)

    if len(results) == 1:
        r = results[0]
        if display is not None:
            display.info(f"Score: {r.score}/20")
        return QuorumResult(aggregated=r, per_reviewer=[r])

    scores_str = " ".join(f"R{i+1}:{r.score}/20" for i, r in enumerate(results))
    med_score = int(median(r.score for r in results))
    if display is not None:
        display.info(f"{scores_str} -> median {med_score}/20")

    med_correctness = int(median(r.correctness for r in results))
    med_completeness = int(median(r.completeness for r in results))
    med_simplicity = int(median(r.simplicity for r in results))
    med_clarity = int(median(r.clarity for r in results))

    all_issues: list[ReviewIssue] = []
    for r in results:
        all_issues.extend(r.issues)
    all_changes: list[str] = []
    for r in results:
        all_changes.extend(r.suggested_changes)

    aggregated = ReviewResult(
        score=med_score,
        correctness=med_correctness,
        completeness=med_completeness,
        simplicity=med_simplicity,
        clarity=med_clarity,
        issues=all_issues,
        suggested_changes=all_changes,
    )
    return QuorumResult(aggregated=aggregated, per_reviewer=results)


def _apply_feedback(
    per_reviewer: list[ReviewResult],
    quorum_size: int,
    work_dir: Path | None,
    dry_run: bool,
    worktree: str = "",
    display: Display | None = None,
    path: Path | None = None,
) -> ApplyFeedbackResult:
    """Synthesize quorum feedback and apply it.

    When *path* is set, generates plan-file-oriented prompt.
    When *path* is None, generates code-fix-oriented prompt (``/rpi-fix``).
    """
    is_plan = path is not None
    label = "Apply Feedback" if is_plan else "Apply Fix"
    log_name = "apply-feedback" if is_plan else "apply-fix"
    noun = "changes" if is_plan else "fixes"

    def _run(prompt: str) -> ApplyFeedbackResult:
        return run_claude_with_display(
            prompt,
            ApplyFeedbackResult,
            display=display,
            label=label,
            log_name=log_name,
            complete_summary=lambda r: f"{r.changes_applied} {noun} — {r.summary}",
            effort="medium",
            work_dir=work_dir,
            dry_run=dry_run,
            worktree=worktree,
        )

    # Build feedback text
    if quorum_size <= 1 or len(per_reviewer) <= 1:
        r = per_reviewer[0]
        issues_text = "\n".join(
            f"- [{i.severity.upper()}] {i.description}" for i in r.issues
        ) or "- None"
        changes_text = (
            "\n".join(f"- {c}" for c in r.suggested_changes) or "- None"
        )
        if is_plan:
            return _run(
                f"Read the plan file at {path} and apply these improvements:\n\n"
                f"Issues found:\n{issues_text}\n\n"
                f"Suggested changes:\n{changes_text}\n\n"
                "Edit the plan file to address each issue. Be precise -- make the specific "
                "changes suggested. Do not add scope or features beyond what is suggested. "
                "Do not remove phases or restructure unless a suggestion explicitly calls for it.\n\n"
                "After applying changes, scan for transitive references: if you changed a task's "
                "endpoint, tool name, file path, or API design, search the entire plan for all "
                "other mentions of the old name/endpoint and update them.\n\n"
                "Prioritize CRITICAL issues. NOTE items are observations -- apply them only if "
                "trivially fixable without introducing new changes or touching additional sections."
            )
        feedback_block = (
            f"Issues found:\n{issues_text}\n\n"
            f"Suggested fixes:\n{changes_text}"
        )
        return _run(f"Run /rpi-fix with the following reviewer feedback:\n\n{feedback_block}")

    # Multiple reviewers
    n = len(per_reviewer)
    sections: list[str] = []
    change_label = "Suggested changes" if is_plan else "Suggested fixes"
    for i, r in enumerate(per_reviewer):
        issues_text = "\n".join(
            f"  - [{x.severity.upper()}] {x.description}" for x in r.issues
        ) or "  - None"
        changes_text = (
            "\n".join(f"  - {x}" for x in r.suggested_changes) or "  - None"
        )
        sections.append(
            f"Reviewer {i + 1} (score {r.score}/20, {derive_verdict(r)}):\n"
            f"  Issues:\n{issues_text}\n"
            f"  {change_label}:\n{changes_text}"
        )
    reviewer_block = "\n\n".join(sections)

    if is_plan:
        return _run(
            f"You are given feedback from {n} independent reviewers of an implementation plan.\n\n"
            "Your job:\n"
            f"1. Read the plan file at {path}\n"
            "2. Synthesize the feedback:\n"
            "   - Apply every suggestion that has strong justification (a concrete consequence\n"
            "     if not addressed). Err on the side of applying rather than dropping.\n"
            "   - Merge semantically identical suggestions from different reviewers into one change.\n"
            "   - If reviewers contradict each other, side with the suggestion that has stronger\n"
            "     justification, not just the majority.\n"
            "   - Only drop suggestions that lack justification or are purely stylistic preferences.\n"
            "3. Apply the synthesized changes to the plan file\n"
            "4. After applying changes, scan for transitive references: if you changed a task's\n"
            "   endpoint, tool name, file path, or API design, search the entire plan for all\n"
            "   other mentions of the old name/endpoint and update them. A fix that updates one\n"
            "   location but leaves stale references elsewhere is worse than no fix.\n"
            "5. Prioritize CRITICAL issues. NOTE items are observations -- apply them only if\n"
            "   trivially fixable without introducing new changes or touching additional sections.\n\n"
            f"Reviewer feedback:\n\n{reviewer_block}\n\n"
            "Be precise -- make the specific changes suggested. Do not add scope or features\n"
            "beyond what reviewers suggested. Do not remove phases or restructure unless\n"
            "reviewers explicitly call for it."
        )

    feedback_block = (
        f"Feedback from {n} independent reviewers:\n\n"
        + "\n\n".join(sections)
    )
    return _run(f"Run /rpi-fix with the following reviewer feedback:\n\n{feedback_block}")


def _has_feedback(per_reviewer: list[ReviewResult]) -> bool:
    """Check if any reviewer produced issues or suggested changes."""
    return any(r.issues or r.suggested_changes for r in per_reviewer)


def _write_iteration_history(
    history: list[IterationRecord],
    loop_type: str,
    work_dir: Path,
) -> Path:
    """Write cumulative iteration history to the workspace for downstream agents."""
    history_path = work_dir / f"{loop_type}-history.md"
    text = format_iteration_history(history, loop_type)
    header = (
        f"# {loop_type.replace('_', ' ').title()} -- Iteration History\n\n"
        "This file records what previous review iterations flagged and what\n"
        "changes were applied. Use it to avoid re-flagging issues that were\n"
        "already addressed and to avoid reverting intentional changes.\n\n"
    )
    history_path.write_text(header + text + "\n")
    return history_path


def run_review_loop(config: ReviewLoopConfig, display: Display) -> ReviewLoopResult:
    """Run the shared review-iterate-apply loop.

    Used by both PlanReviewStage and ReviewFixStage.
    """
    from .diagnosis import print_diagnosis, run_diagnosis

    score_10 = 0
    history: list[IterationRecord] = []

    for iteration in range(1, config.max_iters + 1):
        display.stage_header(
            f"{config.loop_type.replace('_', ' ').title()} iteration "
            f"{iteration}/{config.max_iters}"
        )

        review_prompt = config.review_prompt
        if history:
            history_path = config.work_dir / f"{config.loop_type}-history.md"
            review_prompt += (
                f"\n\nIMPORTANT: This is iteration {iteration}. Read the "
                f"iteration history at {history_path} before reviewing. It "
                f"records what previous reviewers flagged and what {config.history_noun} were "
                "applied. Do not re-flag issues that were already addressed."
            )

        quorum_result = run_review_quorum(
            prompt=review_prompt,
            quorum_size=config.review_quorum,
            work_dir=config.work_dir,
            dry_run=config.dry_run,
            worktree=config.worktree,
            display=display,
        )
        result = quorum_result.aggregated

        score_10 = result.score // 2
        score_style = "green" if score_10 >= config.min_score else "yellow"
        display.info(
            f"Score: [{score_style}]{score_10}/10[/{score_style}] ({result.score}/20), "
            f"Verdict: {derive_verdict(result)}"
        )
        if result.issues:
            n_critical = sum(1 for i in result.issues if i.severity == "critical")
            n_notes = sum(1 for i in result.issues if i.severity == "note")
            display.info(f"Issues: {len(result.issues)} ({n_critical} critical, {n_notes} notes)")
            for issue in result.issues[:5]:
                display.info(f"  - \\[{issue.severity.upper()}] {issue.description[:100]}")
            if len(result.issues) > 5:
                display.info(f"  ... and {len(result.issues) - 5} more")

        apply_summary = ""
        if _has_feedback(quorum_result.per_reviewer):
            display.info(config.apply_label)
            apply_result = _apply_feedback(
                per_reviewer=quorum_result.per_reviewer,
                quorum_size=config.review_quorum,
                work_dir=config.work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
                display=display,
                path=config.apply_path,
            )
            display.info(
                f"Applied {config.apply_noun}: {apply_result.changes_applied} "
                f"{config.apply_noun} — {apply_result.summary}"
            )
            apply_summary = (
                f"{apply_result.changes_applied} {config.apply_noun}: {apply_result.summary}"
            )

        history.append(IterationRecord(
            iteration=iteration,
            aggregated=result,
            per_reviewer=quorum_result.per_reviewer,
            apply_summary=apply_summary,
        ))
        _write_iteration_history(history, config.loop_type, config.work_dir)

        if score_10 >= config.min_score and derive_verdict(result) == "Ready":
            display.info(
                f"[green]{config.pass_message}[/green] {score_10}/10 after {iteration} iteration(s)."
            )
            return ReviewLoopResult(score=score_10, iterations=iteration, converged=True)

        if iteration >= config.max_iters:
            display.warn(
                f"{config.loop_type.replace('_', ' ').title()} did not converge after "
                f"{config.max_iters} iterations (score: {score_10}/10)."
            )
            display.info("Running convergence diagnosis...")
            diagnosis = run_diagnosis(
                history=history,
                loop_type=config.loop_type,
                plan_path=config.plan_path,
                min_score=config.min_score,
                work_dir=config.work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
                display=display,
            )
            print_diagnosis(diagnosis, config.loop_type, display=display)
            if not display.confirm(config.failure_prompt):
                display.info("Stopped.")
                sys.exit(1)
            return ReviewLoopResult(score=score_10, iterations=config.max_iters, converged=False)

    return ReviewLoopResult(score=score_10, iterations=config.max_iters, converged=False)
