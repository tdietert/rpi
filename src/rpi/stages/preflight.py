"""Pre-flight stage: parse plan into structured representation."""

from __future__ import annotations

from ..plan import run_plan_processing
from ..stage_name import StageName
from . import Stage


class PreflightStage(Stage):
    name = StageName.preflight
    label = "Pre-flight: parse plan structure"

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
