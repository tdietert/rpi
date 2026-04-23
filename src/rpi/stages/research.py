"""Research stage: invoke /rpi-research skill and collect feedback."""

from __future__ import annotations

import re
import shutil
from datetime import date
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

        # Compute research file path in the work directory (avoids .claude/ permission prompts)
        today = date.today().isoformat()
        raw = config.prompt or "research"
        kebab = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:60] or "research"
        filename = f"{today}-{kebab}.md"
        abs_path = ctx.work_dir / filename

        prompt = (
            f"Run /rpi-research to research the following:\n\n"
            f"{config.prompt}\n\n"
            f"Write the research document to `{abs_path}`."
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

        path = abs_path if abs_path.is_file() else Path(result.research_path)
        final_path = self._copy_to_research_dir(path, config.worktree)
        ctx.display.info(Text.assemble("Research written to: ", filelink(final_path)))

        # Feedback loop
        while True:
            feedback = ctx.display.collect_feedback("Research")
            if feedback is None:
                break
            update_prompt = (
                f"Run /rpi-research to update the research at `{abs_path}`.\n\n"
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
            path = abs_path if abs_path.is_file() else Path(result.research_path)
            final_path = self._copy_to_research_dir(path, config.worktree)
            ctx.display.info(Text.assemble("Research updated: ", filelink(final_path)))

        ctx.research_path = final_path
        ctx.config.research_path = final_path
        ctx.progress.research_done = True

    def _copy_to_research_dir(self, src: Path, worktree: str) -> Path:
        """Copy research from work_dir to .claude/research/ for persistence."""
        if worktree:
            research_dir = Path(worktree) / ".claude" / "research"
        else:
            research_dir = Path.cwd() / ".claude" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)
        dest = research_dir / src.name
        shutil.copy2(str(src), str(dest))
        return dest
