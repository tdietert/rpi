"""Startup banner rendering for RPI pipeline runs."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .display import Display, filelink
from .plan import PlanMetadata
from .progress import SnapshotStageProgress
from .stages import PIPELINE, skips_stage


def render_banner(
    config: Config,
    meta: PlanMetadata | None,
    snap_dir: Path,
    work_dir: Path,
    disp: Display,
    *,
    progress: SnapshotStageProgress | None = None,
    resume_completed_phases: set[int] | None = None,
) -> None:
    """Print startup banner for both fresh and resume paths."""
    if progress is not None and resume_completed_phases is not None:
        fields = _resume_fields(config, meta, snap_dir, work_dir, progress, resume_completed_phases)
    elif meta:
        fields = [
            ("Plan", meta.title),
            ("File", filelink(config.plan_path)),
            ("Phases", str(meta.num_phases)),
            ("Config", _config_summary(config)),
        ]
    else:
        fields = []
        if config.prompt:
            fields.append(("Prompt", config.prompt))
        fields.append(("Config", _config_summary(config)))
        if config.research_path:
            fields.append(("Research", filelink(config.research_path)))
        if config.spec_path:
            fields.append(("Spec", filelink(config.spec_path)))

    if progress is None:
        if config.worktree:
            fields.append(("Worktree", config.worktree))
        skips = [
            S.name.value.replace("_", "-") for S in PIPELINE
            if skips_stage(config, S.name)
        ]
        if skips:
            fields.append(("Skip", ", ".join(skips)))
        if config.push:
            fields.append(("Push", "yes (commit + push, no PR)"))
        fields.append(("Snapshot", filelink(snap_dir)))
        fields.append(("Work", filelink(work_dir)))

    disp.banner("Review-Implement-Fix", fields)


def _config_summary(config: Config) -> str:
    return (
        f"min_score={config.min_score}, max_review={config.max_review_iters}, "
        f"max_fix={config.max_fix_iters}, quorum={config.review_quorum}"
    )


def _resume_fields(
    config: Config,
    meta: PlanMetadata | None,
    snap_dir: Path,
    work_dir: Path,
    progress: SnapshotStageProgress,
    resume_completed_phases: set[int],
) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [("Plan", meta.title if meta else config.prompt[:60])]
    if config.plan_path:
        fields.append(("File", filelink(config.plan_path)))
    if meta and meta.num_phases:
        fields.append(("Phases", str(meta.num_phases)))
    fields.append(("Snapshot", filelink(snap_dir)))
    if resume_completed_phases:
        fields.append(("Resuming", f"skipping phases {sorted(resume_completed_phases)}"))

    completed_stages = []
    stage_flags = [
        (progress.research_done, "research"),
        (progress.spec_draft_done, "spec-draft"),
        (progress.plan_draft_done, "plan-draft"),
        (progress.plan_review_done, "plan-review"),
        (progress.implementation_done, "implement"),
        (progress.review_fix_done, "review-fix"),
        (progress.commit_done, "commit"),
        (progress.push_or_pr_done, "push/pr"),
    ]
    for done, label in stage_flags:
        if done:
            completed_stages.append(label)
    if completed_stages:
        fields.append(("Done", ", ".join(completed_stages)))
    fields.append(("Work", filelink(work_dir)))
    return fields
