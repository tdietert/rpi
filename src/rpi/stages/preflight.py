"""Pre-flight stage: parse plan into structured representation."""

from __future__ import annotations

from ..display import display
from ..plan import run_plan_processing
from . import Stage


class PreflightStage(Stage):
    name = "preflight"
    label = "Pre-flight: parse plan structure"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_implement

    def run(self, ctx) -> None:
        if ctx.plan is not None:
            display.info(
                f"Pre-flight: using plan from draft stage "
                f"({len(ctx.plan.phases)} phases)"
            )
        else:
            ctx.plan = run_plan_processing(ctx.config, ctx.work_dir)
            display.info(
                f"Pre-flight complete: "
                f"{len(ctx.plan.phases)} phases, "
                f"{sum(len(p.tasks) for p in ctx.plan.phases)} tasks"
            )

    def execute(self, ctx) -> None:
        display.stage_bar(self.name)
        if self.should_skip(ctx):
            display.info(f"[dim]{self.label} -- SKIPPED[/dim]")
            display.stage_bar("implement")
            display.info("[dim]Stage 2: Implementation -- SKIPPED[/dim]")
            return
        display.stage_header(self.label)
        self.run(ctx)
        self._snapshot(ctx)
