"""Plan draft stage: generate a structured implementation plan via Claude."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..display import filelink
from ..plan import (
    Plan,
    PlanMetadata,
    plan_file_path,
    serialize_plan_to_markdown,
    validate_plan,
)
from ..process import run_claude_structured
from ..stage_name import StageName
from . import Stage

_PLAN_SYSTEM = (
    "You are creating a detailed implementation plan. "
    "Use your tools (Read, Glob, Grep, Bash) to explore the codebase "
    "and understand what needs to change."
)

_PLAN_INSTRUCTIONS = """\
Create a detailed implementation plan following these principles:
- Prefer vertical slices over horizontal layers — each phase delivers end-to-end functionality
- Tasks have explicit file lists and group assignments for parallelism
- Tasks in different groups within the same phase MUST NOT share files
- Every phase must leave the codebase type-checkable
- Include verification commands using relative paths only
- Be specific enough for a code-implementer agent to execute without exploring

Fill in ALL fields of the Plan schema:
- Each phase needs tasks with IDs (X.Y format), file lists, group labels, specific steps, \
and verification commands
- Phase numbers must be sequential starting from 1
- Task IDs must be unique within each phase
- Include a testing strategy, risks, and open questions"""


class PlanStage(Stage):
    name = StageName.plan_draft
    label = "Plan Draft"

    def run(self, ctx) -> None:
        config = ctx.config
        today = date.today().isoformat()

        # Gather context: prefer spec over raw research
        spec_content = ""
        if ctx.spec_path and ctx.spec_path.is_file():
            spec_content = ctx.spec_path.read_text()

        research_content = ""
        if not spec_content and ctx.research_path and ctx.research_path.is_file():
            research_content = ctx.research_path.read_text()

        # Build plan prompt
        parts = [_PLAN_SYSTEM, ""]
        if spec_content:
            parts.append("## Spec Context\n")
            parts.append(spec_content)
            parts.append("")
        if research_content:
            parts.append("## Research Context\n")
            parts.append(research_content)
            parts.append("")
        if config.prompt:
            parts.append(f"## Task\n\n{config.prompt}\n")
        parts.append(f"## Instructions\n\n{_PLAN_INSTRUCTIONS}")
        prompt = "\n".join(parts)

        with ctx.display.activity("Plan Draft", "plan-draft") as act:
            plan = run_claude_structured(
                prompt=prompt,
                schema=Plan,
                effort="high",
                worktree=config.worktree,
                work_dir=ctx.work_dir,
                dry_run=config.dry_run,
                activity=act,
            )
            act.complete("success", f"{plan.title} ({len(plan.phases)} phases)")

        # Validate with auto-fix loop
        plan = self._validate_with_fix(plan, ctx)

        # Serialize and write
        research_path_str = str(ctx.research_path) if ctx.research_path else None
        spec_path_str = str(ctx.spec_path) if ctx.spec_path else None
        md = serialize_plan_to_markdown(plan, config.prompt, today, research_path_str, spec_path_str)
        rel_path = plan_file_path(plan.title, today)
        if config.worktree:
            path = Path(config.worktree) / rel_path
        else:
            path = rel_path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md)

        ctx.display.info(f"Plan written to: {filelink(path)}")

        # Feedback loop
        while True:
            feedback = ctx.display.collect_feedback("Plan")
            if feedback is None:
                break
            update_prompt = (
                f"{_PLAN_SYSTEM}\n\n"
                f"## Current Plan\n\n{plan.model_dump_json(indent=2)}\n\n"
                f"## Feedback\n\n{feedback}\n\n"
                f"## Instructions\n\nUpdate the plan based on the feedback above. "
                f"Re-investigate as needed using your tools. "
                f"Fill in ALL fields of the Plan schema."
            )
            with ctx.display.activity("Plan Draft (update)", "plan-draft-update") as act:
                plan = run_claude_structured(
                    prompt=update_prompt,
                    schema=Plan,
                    effort="high",
                    worktree=config.worktree,
                    work_dir=ctx.work_dir,
                    dry_run=config.dry_run,
                    activity=act,
                )
                act.complete("success", f"{plan.title} ({len(plan.phases)} phases)")
            plan = self._validate_with_fix(plan, ctx)
            md = serialize_plan_to_markdown(plan, config.prompt, today, research_path_str, spec_path_str)
            path.write_text(md)
            ctx.display.info(f"Plan written to: {filelink(path)}")

        # Set context for downstream stages
        ctx.plan = plan
        ctx.config.plan_path = path
        ctx.meta = PlanMetadata(
            title=plan.title,
            num_phases=len(plan.phases),
            completed_phases=0,
        )
        ctx.progress.plan_draft_done = True

    def _validate_with_fix(self, plan: Plan, ctx) -> Plan:
        """Validate plan structure, auto-fix up to 3 times."""
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

            ctx.display.info(f"Fixing plan structure (attempt {attempt + 1}/{max_attempts})...")
            fix_prompt = (
                f"{_PLAN_SYSTEM}\n\n"
                f"## Current Plan\n\n{plan.model_dump_json(indent=2)}\n\n"
                f"## Structural Issues\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\n"
                "Fix these structural issues in the plan. Keep all content intact — "
                "only fix numbering, duplicate IDs, or cross-group file overlap.\n\n"
                f"## Instructions\n\n{_PLAN_INSTRUCTIONS}"
            )
            with ctx.display.activity("Plan Fix", f"plan-fix-{attempt + 1}") as act:
                plan = run_claude_structured(
                    prompt=fix_prompt,
                    schema=Plan,
                    effort="low",
                    worktree=ctx.config.worktree,
                    work_dir=ctx.work_dir,
                    dry_run=ctx.config.dry_run,
                    activity=act,
                )
                act.complete("success", "structure fixed")
        return plan
