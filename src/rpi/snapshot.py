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

from .display import Display
from .plan import Plan, extract_plan_frontmatter
from .types import Config, SnapshotStageProgress

_V1_CONFIG_FIELDS = {
    "plan_path", "min_score", "max_review_iters", "max_fix_iters",
    "review_quorum", "skip_plan_review", "skip_implement", "skip_fix",
    "skip_commit", "skip_pr", "push", "worktree", "dry_run", "prompt",
    "skip_research", "research_path",
}


class Snapshot(BaseModel):
    """Complete RPI run state, serializable to JSON."""
    version: int = 2
    timestamp: str = ""
    config: Config = Field(default_factory=Config)

    # Parsed plan (JSON-serialized)
    plan_json: str | None = None

    # Stage progress
    progress: SnapshotStageProgress = Field(default_factory=SnapshotStageProgress)

    # Paths to copied supporting files within the snapshot dir
    copied_files: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_v1(cls, data: dict) -> Snapshot:
        """Migrate a v1 (flat config fields) snapshot dict to v2 (nested config)."""
        config_data: dict = {}
        for key in _V1_CONFIG_FIELDS:
            if key in data:
                val = data.pop(key)
                # v1 stored paths as strings; convert empty strings to None
                if key in ("plan_path", "research_path"):
                    val = val if val else None
                config_data[key] = val
        data["config"] = config_data
        data["version"] = 2
        return cls.model_validate(data)


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
    plan: Plan | None,
    work_dir: Path,
) -> None:
    """Write snapshot.json and copy supporting files."""

    copied_files: dict[str, str] = {}

    # Copy plan file
    if config.plan_path is not None and config.plan_path.is_file():
        shutil.copy2(str(config.plan_path), str(snap_dir / "plan.md"))
        copied_files["plan"] = "plan.md"

    # Copy research/spec from plan front matter
    if config.plan_path is not None and config.plan_path.is_file():
        fm = extract_plan_frontmatter(config.plan_path)
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
        config=config,
        plan_json=plan.model_dump_json() if plan else None,
        progress=progress,
        copied_files=copied_files,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    (snap_dir / "snapshot.json").write_text(snapshot.model_dump_json(indent=2))


def load_snapshot(snap_dir: Path, display: Display | None = None) -> Snapshot:
    """Load a snapshot from disk, migrating v1 format if needed."""
    import json as _json

    snap_path = snap_dir / "snapshot.json"
    if not snap_path.is_file():
        if display is not None:
            display.error(f"No snapshot.json found in {snap_dir}")
        sys.exit(1)
    data = _json.loads(snap_path.read_text())
    if data.get("version", 1) < 2:
        return Snapshot.from_v1(data)
    return Snapshot.model_validate(data)


def restore_from_snapshot(
    snap_dir: Path, display: Display | None = None,
) -> tuple[Config, Plan | None, Path, SnapshotStageProgress]:
    """Reconstruct state from a snapshot directory.

    Returns (config, plan_or_None, work_dir, progress).
    """

    snapshot = load_snapshot(snap_dir, display=display)
    config = snapshot.config.model_copy()

    # Resolve plan_path: in prompt mode (no plan file), skip resolution
    if config.plan_path is not None:
        resolved = config.plan_path
        if not resolved.is_file():
            snap_plan = snap_dir / snapshot.copied_files.get("plan", "plan.md")
            if snap_plan.is_file():
                resolved = snap_plan
                if display is not None:
                    display.warn(f"Original plan file not found, using snapshot copy: {snap_plan}")
            else:
                if display is not None:
                    display.error(f"Plan file not found: {config.plan_path}")
                sys.exit(1)
        config.plan_path = resolved
    elif config.prompt and snapshot.progress.plan_draft_done:
        snap_plan = snap_dir / snapshot.copied_files.get("plan", "plan.md")
        if snap_plan.is_file():
            config.plan_path = snap_plan

    # Set skip flags for completed new stages
    if snapshot.progress.research_done:
        config.skip_research = True

    # Verify worktree still exists on disk
    if config.worktree and not Path(config.worktree).is_dir():
        if display is not None:
            display.error(
                f"Worktree from snapshot no longer exists: {config.worktree}\n"
                "  It may have been cleaned up since the snapshot was saved.\n"
                "  Re-run without --resume, or use --worktree to create a new one."
            )
        sys.exit(1)

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
