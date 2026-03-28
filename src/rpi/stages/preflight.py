"""Pre-flight stage: parse plan into structured representation."""

from __future__ import annotations

from ..display import display
from ..plan import run_plan_processing


class PreflightStage:
    name = "preflight"
    label = "Pre-flight: parse plan structure"

    def should_skip(self, ctx) -> bool:
        return ctx.config.skip_implement

    def run(self, ctx) -> None:
        if ctx.parsed_plan is None:
            ctx.parsed_plan = run_plan_processing(ctx.config, ctx.work_dir)
            display.info(
                f"Pre-flight complete: "
                f"{len(ctx.parsed_plan.phases)} phases, "
                f"{sum(len(p.tasks) for p in ctx.parsed_plan.phases)} tasks"
            )
        else:
            display.info(f"Pre-flight: using restored plan ({len(ctx.parsed_plan.phases)} phases)")

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

    def _snapshot(self, ctx) -> None:
        from ..snapshot import save_snapshot
        if ctx.snap_dir is not None:
            save_snapshot(ctx.snap_dir, ctx.config, ctx.progress, ctx.parsed_plan, ctx.work_dir)
