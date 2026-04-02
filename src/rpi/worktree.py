"""Git worktree creation and resolution for RPI runs."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .display import Display


def create_worktree(args: argparse.Namespace, prompt: str, disp: Display) -> str:
    """Resolve or create a git worktree. Returns worktree path (empty if none)."""
    if args.worktree_path:
        existing = Path(args.worktree_path).resolve()
        if not existing.is_dir():
            disp.error(f"Worktree path does not exist: {existing}")
            sys.exit(1)
        return str(existing)

    worktree_name = args.worktree_name
    use_worktree = (
        args.worktree or worktree_name is not None
        if args.worktree is not None or worktree_name is not None
        else os.environ.get("WORKTREE", "0") == "1"
    )
    if not use_worktree:
        return ""

    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )

    slug = _derive_slug(args, prompt)

    branch_name = f"rpi/{slug}"
    base_branch = args.worktree_base or "main"
    ts = int(time.time())
    worktree_dir = repo_root / ".claude" / "worktrees" / f"{slug}-{ts}"
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), base_branch],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    if result.returncode != 0:
        disp.error(f"Failed to create worktree: {result.stderr.strip()}")
        sys.exit(1)
    return str(worktree_dir)


def _derive_slug(args: argparse.Namespace, prompt: str) -> str:
    """Build a URL-safe slug from CLI args for branch/worktree naming."""
    ts = int(time.time())
    fallback = f"rpi-{ts}"

    if args.worktree_name:
        raw = args.worktree_name
    elif args.plan_path is not None:
        raw = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", args.plan_path.stem)
    elif prompt:
        raw = "-".join(prompt.split()[:4])
    elif args.spec is not None:
        raw = args.spec.stem
    elif args.research is not None:
        raw = args.research.stem
    else:
        return fallback

    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", raw).strip("-")
    return slug or fallback
