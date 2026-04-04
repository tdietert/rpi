"""Plan stage: generate an implementation plan via Claude."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..display import filelink
from ..plan import (
    Plan,
    PlanMetadata,
    parse_plan_from_markdown,
    serialize_plan_to_markdown,
    validate_plan,
)
from ..process import make_dry_run_default, run_claude_streaming
from ..stage_name import StageName
from . import Stage


class PlanStage(Stage):
    name = StageName.plan
    label = "Plan"

    def run(self, ctx) -> None:
        config = ctx.config
        today = date.today().isoformat()

        # Gather context: prefer spec over raw research
        spec_context = ""
        if ctx.spec_path and ctx.spec_path.is_file():
            spec_context = f" Use spec at {ctx.spec_path} for context."

        research_context = ""
        if not spec_context and ctx.research_path and ctx.research_path.is_file():
            research_context = f" Use research at {ctx.research_path} for context."

        # Compute plan file path from task description
        kebab = re.sub(r"[^a-z0-9]+", "-", config.prompt.lower()).strip("-")[:60]
        rel_path = Path(f".claude/plans/{today}-{kebab}.md")
        if config.worktree:
            abs_path = Path(config.worktree) / rel_path
        else:
            abs_path = rel_path.resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        spec_path_str = str(ctx.spec_path) if ctx.spec_path else ""
        research_path_str = str(ctx.research_path) if ctx.research_path else ""

        if config.dry_run:
            plan = make_dry_run_default(Plan)
            md = serialize_plan_to_markdown(
                plan, config.prompt, today,
                research_path_str or None,
                spec_path_str or None,
            )
            abs_path.write_text(md)
        else:
            prompt = (
                f"Run /rpi-plan to create an implementation plan."
                f"{spec_context}{research_context}"
                f" Write the plan to `{rel_path}`."
                f" Task: {config.prompt}"
            )

            with ctx.display.activity("Plan", "plan") as act:
                run_claude_streaming(
                    prompt=prompt,
                    effort="high",
                    worktree=config.worktree,
                    work_dir=ctx.work_dir,
                    activity=act,
                )
                act.complete("success", "plan written")

            if not abs_path.is_file():
                raise RuntimeError(
                    f"Agent did not write plan file to {rel_path}. "
                    "Check the plan activity log for errors."
                )
            plan = parse_plan_from_markdown(abs_path.read_text())

        ctx.display.info(f"{plan.title} ({len(plan.phases)} phases)")

        # Validate with auto-fix
        plan = self._validate_with_fix(plan, abs_path, rel_path, ctx)

        ctx.display.info(f"Plan written to: {filelink(abs_path)}")

        # Feedback loop
        while True:
            feedback = ctx.display.collect_feedback("Plan")
            if feedback is None:
                break

            update_prompt = (
                f"Update the plan at `{rel_path}` based on this feedback: {feedback}\n\n"
                f"Read the current plan, re-investigate as needed, then edit the file. "
                f"Maintain the exact markdown format — the file is parsed deterministically."
            )
            with ctx.display.activity("Plan (update)", "plan-update") as act:
                run_claude_streaming(
                    prompt=update_prompt,
                    effort="high",
                    worktree=config.worktree,
                    work_dir=ctx.work_dir,
                    activity=act,
                )
                act.complete("success", "plan updated")

            plan = parse_plan_from_markdown(abs_path.read_text())
            plan = self._validate_with_fix(plan, abs_path, rel_path, ctx)
            ctx.display.info(f"Plan updated: {filelink(abs_path)}")

        # Set context for downstream stages
        ctx.plan = plan
        ctx.config.plan_path = abs_path
        ctx.meta = PlanMetadata(
            title=plan.title,
            num_phases=len(plan.phases),
            completed_phases=0,
        )
        ctx.progress.plan_done = True

    def _validate_with_fix(
        self, plan: Plan, abs_path: Path, rel_path: Path, ctx,
    ) -> Plan:
        """Validate plan structure, auto-fix up to 3 times by editing the file."""
        max_attempts = 3
        for attempt in range(max_attempts):
            errors = validate_plan(plan)
            if not errors:
                ctx.display.info("[green]Plan structure validated.[/green]")
                return plan

            ctx.display.warn("Plan structure validation found issues:")
            for err in errors:
                ctx.display.info(f"  - {err}")

            if attempt >= max_attempts - 1:
                ctx.display.warn("Could not auto-fix all plan issues after 3 attempts.")
                return plan

            ctx.display.info(
                f"Fixing plan structure (attempt {attempt + 1}/{max_attempts})..."
            )
            fix_prompt = (
                f"The plan file at `{rel_path}` has structural issues that prevent "
                "automated execution. Fix ONLY these specific issues by editing "
                "the plan file:\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\n"
                "Rules:\n"
                "- Cross-group file overlap: merge tasks sharing files into the "
                "same group\n"
                "- Non-sequential phase numbering: renumber phases sequentially "
                "from 1, update task IDs to match\n"
                "- Duplicate task IDs: renumber tasks sequentially within their "
                "phase\n\n"
                "Make minimal edits. Do not change task content, goals, steps, "
                "or verification."
            )
            with ctx.display.activity("Plan Fix", f"plan-fix-{attempt + 1}") as act:
                run_claude_streaming(
                    prompt=fix_prompt,
                    effort="low",
                    work_dir=ctx.work_dir,
                    worktree=ctx.config.worktree,
                    activity=act,
                )
                act.complete("success", "structure fixed")

            plan = parse_plan_from_markdown(abs_path.read_text())

        return plan
