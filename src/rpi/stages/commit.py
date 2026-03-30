"""Commit stage: commit changes using rpi-commit skill."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..process import run_claude_structured
from . import Stage


class CommitResult(BaseModel):
    status: Literal["success", "failed", "nothing_to_commit"]
    num_commits: int = Field(description="Number of commits created")
    commits: list[str] = Field(description="Commit subject lines, in order")
    summary: str = Field(description="One-line description of what was committed")
    errors: str = Field(description="Any errors encountered, or 'None'")


def _dry_run_commit() -> CommitResult:
    return CommitResult(
        status="success",
        num_commits=1,
        commits=["(dry run commit)"],
        summary="(dry run)",
        errors="None",
    )


class CommitStage(Stage):
    name = "commit"
    label = "Stage 4: Commit"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_commit

    def run(self, ctx) -> None:
        config = ctx.config
        with ctx.display.activity("Commit", "commit") as act:
            result = run_claude_structured(
                prompt=(
                    f"Run /rpi-commit to commit changes related to the plan at {config.plan_path}. "
                    "Read the plan file first to understand what this plan covers, then only commit "
                    "changes that are relevant to the plan. Leave unrelated changes unstaged."
                ),
                schema=CommitResult,
                effort="medium",
                work_dir=ctx.work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
                dry_run_default=_dry_run_commit(),
                activity=act,
            )
            act.complete(
                "success" if result.status == "success" else "failed",
                f"{result.num_commits} commits — {result.summary}",
            )

        ctx.display.info(f"[green]Stage 4 complete:[/green] {result.status}")
        ctx.commit_result = result
        ctx.progress.commit_done = True
