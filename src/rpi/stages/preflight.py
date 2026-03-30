"""Pre-flight stage: parse plan into structured representation."""

from __future__ import annotations

from ..plan import run_plan_processing
from . import Stage


class PreflightStage(Stage):
    name = "preflight"
    label = "Pre-flight: parse plan structure"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_implement

    def run(self, ctx) -> None:
        if ctx.plan is not None:
            ctx.display.info(
                f"Pre-flight: using plan from draft stage "
                f"({len(ctx.plan.phases)} phases)"
            )
        else:
            ctx.plan = run_plan_processing(ctx.config, ctx.work_dir, ctx.display)
            ctx.display.info(
                f"Pre-flight complete: "
                f"{len(ctx.plan.phases)} phases, "
                f"{sum(len(p.tasks) for p in ctx.plan.phases)} tasks"
            )

    def execute(self, ctx) -> None:
        if self.should_skip(ctx):
            ctx.display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            ctx.display.info("[dim]Stage 2: Implementation -- SKIPPED[/dim]")
            return
        ctx.display.stage_header(self.label)
        self.run(ctx)
        self._snapshot(ctx)
