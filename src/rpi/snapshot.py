"""Snapshot types and I/O for persisting RPI pipeline state."""

from __future__ import annotations

import atexit
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .display import display
from .types import Config


# -- Snapshot types -----------------------------------------------------------


class SnapshotPhaseProgress(BaseModel):
    """Tracks which implementation phases have completed."""
    completed_phases: list[int] = Field(default_factory=list)
    phase_attempts: dict[str, int] = Field(default_factory=dict)


class SnapshotReviewProgress(BaseModel):
    """Tracks progress within a review loop."""
    completed_iterations: int = 0
    last_score: int | None = None


class SnapshotStageProgress(BaseModel):
    """Tracks which stages are done and in-flight progress."""
    plan_review_done: bool = False
    plan_review: SnapshotReviewProgress | None = None
    implementation_done: bool = False
    implementation: SnapshotPhaseProgress | None = None
    review_fix_done: bool = False
    review_fix: SnapshotReviewProgress | None = None
    commit_done: bool = False
    push_or_pr_done: bool = False
    research_done: bool = False
    plan_draft_done: bool = False


class Snapshot(BaseModel):
    """Complete RPI run state, serializable to JSON."""
    version: int = 1
    timestamp: str = ""

    # Config fields (flattened)
    plan_path: str = ""
    min_score: int = 8
    max_review_iters: int = 5
    max_fix_iters: int = 5
    review_quorum: int = 3
    skip_plan_review: bool = False
    skip_implement: bool = False
    skip_fix: bool = False
    skip_commit: bool = False
    skip_pr: bool = False
    push: bool = False
    worktree: str = ""
    dry_run: bool = False
    prompt: str = ""
    skip_research: bool = False
    research_path: str = ""

    # Parsed plan (JSON-serialized)
    plan_json: str | None = None

    # Stage progress
    progress: SnapshotStageProgress = Field(default_factory=SnapshotStageProgress)

    # Paths to copied supporting files within the snapshot dir
    copied_files: dict[str, str] = Field(default_factory=dict)


# -- Snapshot utilities -------------------------------------------------------


def create_snapshot_dir(config: Config) -> Path:
    """Create a timestamped snapshot directory under ~/.claude/snapshots/."""
    snap_base = Path.home() / ".claude" / "snapshots"
    snap_base.mkdir(parents=True, exist_ok=True)
    if config.plan_path is not None:
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", config.plan_path.stem)
    else:
        # Derive slug from prompt in prompt mode
        words = config.prompt.split()[:4]
        slug = "-".join(words) if words else ""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-") or "rpi"
    ts = time.strftime("%Y%m%d-%H%M%S")
    snap_dir = snap_base / f"rpi-{slug}-{ts}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    return snap_dir


def save_snapshot(
    snap_dir: Path,
    config: Config,
    progress: SnapshotStageProgress,
    plan: object | None,
    work_dir: Path,
) -> None:
    """Write snapshot.json and copy supporting files.

    plan is typed as object to avoid importing Plan here;
    it must be a Pydantic BaseModel with model_dump_json().
    """
    from .plan import _extract_plan_frontmatter

    copied_files: dict[str, str] = {}

    # Copy plan file
    if config.plan_path is not None and config.plan_path.is_file():
        shutil.copy2(str(config.plan_path), str(snap_dir / "plan.md"))
        copied_files["plan"] = "plan.md"

    # Copy research/spec from plan front matter
    if config.plan_path is not None and config.plan_path.is_file():
        fm = _extract_plan_frontmatter(config.plan_path)
        plan_dir = config.plan_path.parent
        for label in ("research", "spec"):
            raw = fm.get(label, "")
            if not raw:
                continue
            # research can be comma-separated
            paths = [p.strip() for p in raw.split(",") if p.strip()]
            for i, p in enumerate(paths):
                src = Path(p) if Path(p).is_absolute() else (plan_dir / p).resolve()
                if src.is_file():
                    suffix = f"-{i}" if len(paths) > 1 else ""
                    fname = f"{label}{suffix}{src.suffix}"
                    shutil.copy2(str(src), str(snap_dir / fname))
                    copied_files[f"{label}{suffix}"] = fname

    # Copy work_dir contents
    work_snap = snap_dir / "work"
    if work_snap.exists():
        shutil.rmtree(str(work_snap))
    if work_dir.exists() and any(work_dir.iterdir()):
        shutil.copytree(str(work_dir), str(work_snap))

    snapshot = Snapshot(
        version=1,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        plan_path=str(config.plan_path) if config.plan_path else "",
        min_score=config.min_score,
        max_review_iters=config.max_review_iters,
        max_fix_iters=config.max_fix_iters,
        review_quorum=config.review_quorum,
        skip_plan_review=config.skip_plan_review,
        skip_implement=config.skip_implement,
        skip_fix=config.skip_fix,
        skip_commit=config.skip_commit,
        skip_pr=config.skip_pr,
        push=config.push,
        worktree=config.worktree,
        dry_run=config.dry_run,
        prompt=config.prompt,
        skip_research=config.skip_research,
        research_path=str(config.research_path) if config.research_path else "",
        plan_json=(
            plan.model_dump_json() if plan else None
        ),
        progress=progress,
        copied_files=copied_files,
    )
    (snap_dir / "snapshot.json").write_text(snapshot.model_dump_json(indent=2))


def load_snapshot(snap_dir: Path) -> Snapshot:
    """Load a snapshot from disk."""
    snap_path = snap_dir / "snapshot.json"
    if not snap_path.is_file():
        display.error(f"No snapshot.json found in {snap_dir}")
        sys.exit(1)
    return Snapshot.model_validate_json(snap_path.read_text())


def restore_from_snapshot(
    snap_dir: Path,
) -> tuple[Config, object | None, Path, SnapshotStageProgress]:
    """Reconstruct state from a snapshot directory.

    Returns (config, plan_or_None, work_dir, progress).
    plan is returned as a Plan but typed loosely to keep
    the import lazy.
    """
    from .plan import Plan

    snapshot = load_snapshot(snap_dir)

    # Resolve plan_path: in prompt mode (no plan file), skip resolution
    plan_path: Path | None = None
    if snapshot.plan_path:
        # Plan-file mode: resolve the plan path
        resolved = Path(snapshot.plan_path)
        if not resolved.is_file():
            snap_plan = snap_dir / snapshot.copied_files.get("plan", "plan.md")
            if snap_plan.is_file():
                resolved = snap_plan
                display.warn(f"Original plan file not found, using snapshot copy: {snap_plan}")
            else:
                display.error(f"Plan file not found: {snapshot.plan_path}")
                sys.exit(1)
        plan_path = resolved
    elif snapshot.prompt and snapshot.progress.plan_draft_done:
        # Prompt mode with plan draft completed: restore plan_path from snapshot
        snap_plan = snap_dir / snapshot.copied_files.get("plan", "plan.md")
        if snap_plan.is_file():
            plan_path = snap_plan

    # Restore research_path
    research_path: Path | None = None
    if snapshot.research_path:
        research_path = Path(snapshot.research_path)

    # Set skip flags for completed new stages
    skip_research = snapshot.skip_research
    if snapshot.progress.research_done:
        skip_research = True

    config = Config(
        plan_path=plan_path,
        min_score=snapshot.min_score,
        max_review_iters=snapshot.max_review_iters,
        max_fix_iters=snapshot.max_fix_iters,
        review_quorum=snapshot.review_quorum,
        skip_plan_review=snapshot.skip_plan_review,
        skip_implement=snapshot.skip_implement,
        skip_fix=snapshot.skip_fix,
        skip_commit=snapshot.skip_commit,
        skip_pr=snapshot.skip_pr,
        push=snapshot.push,
        worktree=snapshot.worktree,
        dry_run=snapshot.dry_run,
        prompt=snapshot.prompt,
        skip_research=skip_research,
        research_path=research_path,
    )

    plan = None
    if snapshot.plan_json:
        plan = Plan.model_validate_json(snapshot.plan_json)

    # Restore work_dir: seed a new tempdir from snapshot's work/ contents
    work_dir = Path(tempfile.mkdtemp(prefix="rpi-"))
    atexit.register(shutil.rmtree, str(work_dir))
    work_snap = snap_dir / "work"
    if work_snap.is_dir():
        for item in work_snap.iterdir():
            dest = work_dir / item.name
            if item.is_dir():
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))

    return config, plan, work_dir, snapshot.progress
