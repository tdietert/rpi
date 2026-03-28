"""Push/PR stage: push to remote or create a GitHub PR."""

from __future__ import annotations

import subprocess
from typing import Literal

from pydantic import BaseModel, Field

from ..display import display
from ..process import run_claude_structured
from ..snapshot import save_snapshot


class PrResult(BaseModel):
    status: Literal["success", "failed"]
    pr_url: str = Field(description="URL of the created PR, or empty if failed")
    pr_title: str = Field(description="Title of the created PR")
    summary: str = Field(description="One-line description of the PR")
    errors: str = Field(description="Any errors encountered, or 'None'")


def _dry_run_pr() -> PrResult:
    return PrResult(
        status="success",
        pr_url="https://github.com/example/repo/pull/0",
        pr_title="(dry run PR)",
        summary="(dry run)",
        errors="None",
    )


def _run_push(dry_run: bool, worktree: str = "") -> bool:
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


class PushPrStage:
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
                display.stage_bar(self.name)
                display.info("[dim]Stage 5: Push -- SKIPPED (commit failed)[/dim]")
            else:
                display.stage_bar(self.name)
                display.stage_header("Stage 5: Push")
                ctx.push_ok = _run_push(config.dry_run, config.worktree)
                display.info(
                    f"[green]Stage 5 complete:[/green] {'success' if ctx.push_ok else 'failed'}"
                )
                ctx.progress.push_or_pr_done = True
                self._snapshot(ctx)
        elif config.skip_pr:
            display.stage_bar(self.name)
            display.info("[dim]Stage 5: Create PR -- SKIPPED[/dim]")
        elif commit_result and commit_result.status == "failed":
            display.stage_bar(self.name)
            display.info("[dim]Stage 5: Create PR -- SKIPPED (commit failed)[/dim]")
        else:
            display.stage_bar(self.name)
            display.stage_header("Stage 5: Create PR")
            pr_result = run_claude_structured(
                prompt="Run /rpi-create-pr to create a GitHub PR for this branch.",
                schema=PrResult,
                effort="medium",
                work_dir=ctx.work_dir,
                dry_run=config.dry_run,
                worktree=config.worktree,
                dry_run_default=_dry_run_pr(),
            )
            display.result_panel("Pull Request", pr_result)
            display.info(f"[green]Stage 5 complete:[/green] {pr_result.status}")
            ctx.pr_result = pr_result
            ctx.progress.push_or_pr_done = True
            self._snapshot(ctx)

    def _snapshot(self, ctx) -> None:
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.parsed_plan, ctx.work_dir)
