"""Push/PR stage: push to remote or create a GitHub PR."""

from __future__ import annotations

import subprocess
from typing import Literal

from pydantic import BaseModel, Field

from ..display import Display
from ..process import run_claude_structured
from . import Stage


class PrResult(BaseModel):
    status: Literal["success", "failed"]
    pr_url: str = Field(description="URL of the created PR, or empty if failed")
    pr_title: str = Field(description="Title of the created PR")
    summary: str = Field(description="One-line description of the PR")
    errors: str = Field(description="Any errors encountered, or 'None'")


def _run_push(display: Display, dry_run: bool, worktree: str = "") -> bool:
    """Push the current branch to origin. Returns True on success."""
    if dry_run:
        display.info("[dim]\\[DRY RUN] git push -u origin HEAD[/dim]")
        return True

    proc = subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        capture_output=True,
        text=True,
        cwd=worktree or None,
    )
    if proc.returncode == 0:
        output = (proc.stderr or proc.stdout or "").strip()
        if output:
            for line in output.splitlines()[-3:]:
                display.info(line.strip())
        else:
            display.info("[green]Pushed.[/green]")
        return True
    else:
        stderr = (proc.stderr or "").strip()
        display.error(f"Push FAILED (exit {proc.returncode}): {stderr[:200]}")
        return False


class PushPrStage(Stage):
    name = "push_pr"
    label = "Stage 5: Push/PR"

    def should_skip(self, ctx) -> bool:
        # Never fully skip — the execute method handles the branching logic
        return False

    def run(self, ctx) -> None:
        # This is handled entirely in execute due to complex branching
        pass

    def execute(self, ctx) -> None:
        config = ctx.config
        commit_result = ctx.commit_result

        if config.push:
            if commit_result and commit_result.status == "failed":
                ctx.display.info("[dim]Stage 5: Push -- SKIPPED (commit failed)[/dim]")
            else:
                ctx.display.stage_header("Stage 5: Push")
                ctx.push_ok = _run_push(ctx.display, config.dry_run, config.worktree)
                ctx.display.info(
                    f"[green]Stage 5 complete:[/green] {'success' if ctx.push_ok else 'failed'}"
                )
                ctx.progress.push_or_pr_done = True
                self._snapshot(ctx)
        elif config.skip_pr:
            ctx.display.info("[dim]Stage 5: Create PR -- SKIPPED[/dim]")
        elif commit_result and commit_result.status == "failed":
            ctx.display.info("[dim]Stage 5: Create PR -- SKIPPED (commit failed)[/dim]")
        else:
            ctx.display.stage_header("Stage 5: Create PR")
            with ctx.display.activity("Create PR", "create-pr") as act:
                pr_result = run_claude_structured(
                    prompt="Run /rpi-create-pr to create a GitHub PR for this branch.",
                    schema=PrResult,
                    effort="medium",
                    work_dir=ctx.work_dir,
                    dry_run=config.dry_run,
                    worktree=config.worktree,
                    activity=act,
                )
                act.complete(
                    "success" if pr_result.status == "success" else "failed",
                    pr_result.pr_url or pr_result.status,
                )
            ctx.display.info(f"[green]Stage 5 complete:[/green] {pr_result.status}")
            ctx.pr_result = pr_result
            ctx.progress.push_or_pr_done = True
            self._snapshot(ctx)
