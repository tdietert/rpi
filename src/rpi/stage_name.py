"""Stage name enum — leaf module with no internal imports."""

from __future__ import annotations

from enum import Enum


class StageName(Enum):
    research = "research"
    spec = "spec"
    plan = "plan"
    preflight = "preflight"
    plan_review = "plan_review"
    implement = "implement"
    review_fix = "review_fix"
    commit = "commit"
    push_pr = "push_pr"
