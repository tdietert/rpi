"""Spec draft stage: invoke /rpi-spec skill and collect feedback."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..process import run_claude_structured
from ..stage_name import StageName
from . import Stage


class SpecResult(BaseModel):
    status: Literal["success", "failed"]
    spec_path: str = Field(description="Path to the spec file written")
    title: str = Field(description="Feature or system name from the spec")
    summary: str = Field(description="One-line summary of the architectural decisions made")
    errors: str = Field(description="Any errors encountered, or 'None'")


class SpecStage(Stage):
    name = StageName.spec_draft
    label = "Spec Draft"

    def run(self, ctx) -> None:
        config = ctx.config
        prompt = self._build_prompt(config, ctx.research_path)

        with ctx.display.activity("Spec Draft", "spec-draft") as act:
            result = run_claude_structured(
                prompt=prompt,
                schema=SpecResult,
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

        path = Path(result.spec_path)
        ctx.display.info(f"Spec written to: {path}")

        # Feedback loop
        while True:
            feedback = ctx.display.collect_feedback("Spec")
            if feedback is None:
                break
            update_prompt = (
                f"Run /rpi-spec to update the spec at {path}.\n\n"
                f"## Feedback\n\n{feedback}\n\n"
                "Read the existing spec file, then update it based on the "
                "feedback above. Re-investigate as needed."
            )
            with ctx.display.activity("Spec Draft (update)", "spec-draft-update") as act:
                result = run_claude_structured(
                    prompt=update_prompt,
                    schema=SpecResult,
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
            path = Path(result.spec_path)
            ctx.display.info(f"Spec updated: {path}")

        ctx.spec_path = path
        ctx.config.spec_path = path
        ctx.progress.spec_draft_done = True

    def _build_prompt(self, config, research_path: Path | None) -> str:
        parts = ["Run /rpi-spec to create an architectural spec"]
        if config.prompt:
            parts[0] += " for the following task:\n"
            parts.append(config.prompt)
        elif research_path:
            parts[0] += " based on the research file below."
        else:
            parts[0] += "."
        if research_path:
            parts.append(f"\n\nResearch file: {research_path}")
        return "\n".join(parts)
