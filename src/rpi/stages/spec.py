"""Spec draft stage: invoke /rpi-spec skill and collect feedback."""

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


class SpecResult(BaseModel):
    status: Literal["success", "failed"]
    spec_path: str = Field(description="Path to the spec file written")
    title: str = Field(description="Feature or system name from the spec")
    summary: str = Field(description="One-line summary of the architectural decisions made")
    errors: str = Field(description="Any errors encountered, or 'None'")


class SpecStage(Stage):
    name = StageName.spec
    label = "Spec"

    def run(self, ctx) -> None:
        config = ctx.config

        # Compute spec file path in the work directory (avoids .claude/ permission issues)
        today = date.today().isoformat()
        raw = config.prompt or ""
        if not raw and ctx.research_path:
            raw = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", ctx.research_path.stem)
        kebab = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:60]
        filename = f"{today}-{kebab}.md"
        abs_path = ctx.work_dir / filename

        prompt = self._build_prompt(config, ctx.research_path, abs_path)

        with ctx.display.activity("Spec", "spec") as act:
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

        # Use the work_dir path (where we told Claude to write)
        path = abs_path if abs_path.is_file() else Path(result.spec_path)

        # Copy to .claude/specs/ for persistence
        final_path = self._copy_to_specs_dir(path, config.worktree)
        ctx.display.info(Text.assemble("Spec written to: ", filelink(final_path)))

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
            with ctx.display.activity("Spec (update)", "spec-update") as act:
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
            path = abs_path if abs_path.is_file() else Path(result.spec_path)
            final_path = self._copy_to_specs_dir(path, config.worktree)
            ctx.display.info(Text.assemble("Spec updated: ", filelink(final_path)))

        ctx.spec_path = final_path
        ctx.config.spec_path = final_path
        ctx.progress.spec_done = True

    def _copy_to_specs_dir(self, src: Path, worktree: str) -> Path:
        """Copy spec from work_dir to .claude/specs/ for persistence."""
        if worktree:
            specs_dir = Path(worktree) / ".claude" / "specs"
        else:
            specs_dir = Path.cwd() / ".claude" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        dest = specs_dir / src.name
        shutil.copy2(str(src), str(dest))
        return dest

    def _build_prompt(self, config, research_path: Path | None, spec_path: Path) -> str:
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
        parts.append(f"\n\nWrite the spec to `{spec_path}`.")
        return "\n".join(parts)
