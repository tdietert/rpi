"""Research stage: explore codebase and produce structured research output."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..display import display
from ..feedback import collect_feedback
from ..process import run_claude_structured
from ..research import Research, research_file_path, serialize_to_markdown


def _dry_run_research(prompt: str) -> Research:
    return Research(
        title="(dry run)",
        question=prompt,
        summary="(dry run)",
        findings=[],
        architecture="(dry run)",
        patterns="(dry run)",
        code_references=[],
        open_questions=[],
    )


_RESEARCH_SYSTEM = (
    "You are researching a codebase to document what exists. "
    "Your job is to explore, understand, and report — not to suggest changes. "
    "Use your tools (Read, Glob, Grep, Bash) to thoroughly investigate."
)

_RESEARCH_INSTRUCTIONS = """\
Decompose the research question into 3-6 areas and investigate each thoroughly:
- Read relevant source files, configs, and tests
- Document file paths with line references (e.g. src/foo.py:42)
- Describe architecture and data flow between components
- Note patterns and conventions used in the codebase
- List open questions or ambiguities that may need human input

Fill in ALL fields of the Research schema with detailed, accurate information \
from your codebase exploration."""


class ResearchStage:
    name = "research"
    label = "Research"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_research or ctx.config.plan_path is not None

    def run(self, ctx) -> None:
        config = ctx.config
        today = date.today().isoformat()

        # Build research prompt
        prompt = (
            f"{_RESEARCH_SYSTEM}\n\n"
            f"## Research Query\n\n{config.prompt}\n\n"
            f"## Instructions\n\n{_RESEARCH_INSTRUCTIONS}"
        )

        research = run_claude_structured(
            prompt=prompt,
            schema=Research,
            effort="high",
            streaming=True,
            worktree=config.worktree,
            work_dir=ctx.work_dir,
            dry_run=config.dry_run,
            dry_run_default=_dry_run_research(config.prompt),
        )

        # Serialize and write
        md = serialize_to_markdown(research, today)
        rel_path = research_file_path(research.title, today)
        if config.worktree:
            path = Path(config.worktree) / rel_path
        else:
            path = rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md)

        display.result_panel("Research", research)
        display.info(f"Research written to: {path}")

        # Feedback loop
        while True:
            feedback = collect_feedback("Research")
            if feedback is None:
                break
            # Build update prompt with current state + feedback
            update_prompt = (
                f"{_RESEARCH_SYSTEM}\n\n"
                f"## Current Research\n\n{research.model_dump_json(indent=2)}\n\n"
                f"## Feedback\n\n{feedback}\n\n"
                f"## Instructions\n\nUpdate the research based on the feedback above. "
                f"Re-investigate as needed using your tools. "
                f"Fill in ALL fields of the Research schema."
            )
            research = run_claude_structured(
                prompt=update_prompt,
                schema=Research,
                effort="high",
                streaming=True,
                worktree=config.worktree,
                work_dir=ctx.work_dir,
                dry_run=config.dry_run,
                dry_run_default=_dry_run_research(config.prompt),
            )
            md = serialize_to_markdown(research, today)
            path.write_text(md)
            display.result_panel("Research", research)
            display.info(f"Research written to: {path}")

        ctx.research_path = path
        ctx.config.research_path = path
        ctx.research = research
        ctx.progress.research_done = True

    def execute(self, ctx) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            return
        display.stage_header(self.label)
        self.run(ctx)
        self._snapshot(ctx)

    def _snapshot(self, ctx) -> None:
        if ctx.snap_dir is not None:
            from ..snapshot import save_snapshot
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.plan, ctx.work_dir)
