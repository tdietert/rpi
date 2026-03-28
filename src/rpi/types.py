"""Cross-cutting types used throughout the RPI pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    plan_path: Path
    min_score: int = 8  # threshold on 0-10 scale (ReviewResult.score / 2)
    max_review_iters: int = 5
    max_fix_iters: int = 5
    review_quorum: int = 3  # number of parallel reviewers
    skip_plan_review: bool = False
    skip_implement: bool = False
    skip_fix: bool = False
    skip_commit: bool = False
    skip_pr: bool = False
    push: bool = False  # push to current branch after commit (implies skip_pr)
    worktree: str = ""  # if non-empty, absolute path to a pre-created git worktree
    dry_run: bool = False
