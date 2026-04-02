"""Pipeline configuration types."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .stage_name import StageName

Effort = Literal["low", "medium", "high"]


class Config(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    plan_path: Path | None = None
    min_score: int = 8  # threshold on 0-10 scale (ReviewResult.score / 2)
    max_review_iters: int = 3
    max_fix_iters: int = 3
    review_quorum: int = 3  # number of parallel reviewers
    start_from: StageName = StageName.research
    skip_plan_review: bool = False
    skip_implement: bool = False
    skip_fix: bool = False
    skip_commit: bool = False
    skip_pr: bool = False
    push: bool = False  # push to current branch after commit (implies skip_pr)
    worktree: str = ""  # if non-empty, absolute path to a pre-created git worktree
    dry_run: bool = False
    prompt: str = ""
    research_path: Path | None = None
    spec_path: Path | None = None
