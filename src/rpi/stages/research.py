"""Research stage: invoke /rpi-research skill and collect feedback."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from rich.text import Text

from ..display import filelink
from ..process import run_claude_structured
from ..stage_name import StageName
from . import Stage


class ResearchResult(BaseModel):
    status: Literal["success", "failed"]
    research_path: str = Field(description="Path to the research file written")
    title: str = Field(description="Research topic name")
    summary: str = Field(description="One-line summary of findings")
    errors: str = Field(description="Any errors encountered, or 'None'")


class ResearchStage(Stage):
    name = StageName.research
    label = "Research"

    def run(self, ctx) -> None:
        config = ctx.config
        prompt = (
            f"Run /rpi-research to research the following:\n\n"
            f"{config.prompt}"
        )

        with ctx.display.activity("Research", "research") as act:
            result = run_claude_structured(
                prompt=prompt,
                schema=ResearchResult,
                effort="high",
                worktree=config.worktree,
                work_dir=ctx.work_dir,
                dry_run=config.dry_run,
                activity=act,
            )
            act.complete(
                "success" if result.status == "success" else "failed",
                result.title,
            )

        path = Path(result.research_path)
        ctx.display.info(Text.assemble("Research written to: ", filelink(path)))

        # Feedback loop
        while True:
            feedback = ctx.display.collect_feedback("Research")
            if feedback is None:
                break
            update_prompt = (
                f"Run /rpi-research to update the research at {path}.\n\n"
                f"## Feedback\n\n{feedback}\n\n"
                "Read the existing research file, then update it based on the "
                "feedback above. Re-investigate as needed."
            )
            with ctx.display.activity("Research (update)", "research-update") as act:
                result = run_claude_structured(
                    prompt=update_prompt,
                    schema=ResearchResult,
                    effort="high",
                    worktree=config.worktree,
                    work_dir=ctx.work_dir,
                    dry_run=config.dry_run,
                    activity=act,
                )
                act.complete(
                    "success" if result.status == "success" else "failed",
                    result.title,
                )
            path = Path(result.research_path)
            ctx.display.info(Text.assemble("Research updated: ", filelink(path)))

        ctx.research_path = path
        ctx.config.research_path = path
        ctx.progress.research_done = True
