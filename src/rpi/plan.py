"""Plan types, validation, and parsing."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .display import display
from .process import run_claude_structured
from .types import Config


# -- Plan types ---------------------------------------------------------------


class PlanTask(BaseModel):
    id: str = Field(min_length=1, description="Task identifier, e.g. '1.1', '2.3'")
    name: str = Field(min_length=1, description="Short descriptive name of the task")
    files: list[str] = Field(min_length=1, description="File paths this task touches")
    group: str = Field(min_length=1, description="Group label for parallelism, e.g. 'A', 'B'")
    steps: list[str] = Field(
        min_length=1, description="Checkbox items: the specific changes to make"
    )


class PlanPhase(BaseModel):
    number: int = Field(ge=1, description="Phase number, 1-indexed")
    name: str = Field(min_length=1, description="Phase name from the heading")
    goal: str = Field(min_length=1, description="What this phase accomplishes")
    tasks: list[PlanTask] = Field(
        min_length=1, description="Ordered list of tasks in this phase"
    )
    verification: list[str] = Field(
        min_length=1, description="Verification commands or checks for this phase"
    )
    verification_commands: list[str] = Field(
        default_factory=list,
        description="Raw shell commands to run after this phase completes. Each command is run via subprocess; non-zero exit fails verification.",
    )


class ParsedPlan(BaseModel):
    title: str = Field(min_length=1, description="Plan title from the top-level heading")
    overview: str = Field(min_length=1, description="1-3 sentence overview of the plan")
    current_state: str = Field(min_length=1, description="Summary of the current state section")
    desired_end_state: str = Field(min_length=1, description="Summary of the desired end state")
    phases: list[PlanPhase] = Field(
        min_length=1, description="All implementation phases in order"
    )
    testing_strategy: str = Field(min_length=1, description="End-to-end testing approach")
    risks: list[str] = Field(description="Risks and edge cases")
    open_questions: str = Field(
        description="Any unresolved questions, or 'None'"
    )


@dataclass
class PlanMetadata:
    title: str
    num_phases: int
    completed_phases: int


class ApplyFeedbackResult(BaseModel):
    changes_applied: int
    summary: str = Field(
        description="One-line summary of changes made to the plan"
    )


# -- Plan frontmatter parsing ------------------------------------------------


def _extract_plan_frontmatter(plan_path: Path) -> dict[str, str]:
    """Parse YAML front matter (between --- fences) from a plan file.

    Returns a dict of key-value pairs.  No pyyaml dependency — simple
    line-by-line parsing for flat key: value entries.
    """
    text = plan_path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    result: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            result[key.strip()] = value
    return result


# -- Plan metadata -----------------------------------------------------------


def parse_plan_metadata(path: Path) -> PlanMetadata:
    """Extract title, phase count, and completed phase count from a plan file."""
    text = path.read_text()
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    title = title_match.group(1) if title_match else "(untitled)"
    num_phases = len(re.findall(r"^##+ Phase \d", text, re.MULTILINE))
    completed_phases = text.count("- [x]")
    return PlanMetadata(
        title=title, num_phases=num_phases, completed_phases=completed_phases
    )


# -- Plan file validation ----------------------------------------------------


def validate_plan_file(path: Path) -> list[str]:
    """Cheap, deterministic pre-flight check that a file looks like a plan.

    Returns a list of error strings. Empty list means the file passes.
    Catches common mistakes like passing a spec, research doc, or random
    markdown file before any Claude API calls are made.
    """
    errors: list[str] = []
    text = path.read_text()
    fm = _extract_plan_frontmatter(path)

    # Check 1: Wrong directory (spec or research file)
    parts = path.parts
    if ".claude" in parts:
        claude_idx = len(parts) - 1 - parts[::-1].index(".claude")
        if claude_idx + 1 < len(parts):
            subdir = parts[claude_idx + 1]
            if subdir == "specs":
                errors.append(
                    f"This file is in .claude/specs/ — it's a spec, not a plan. "
                    f"Run '/plan' on it first to produce an implementation plan."
                )
            elif subdir == "research":
                errors.append(
                    f"This file is in .claude/research/ — it's a research doc, not a plan. "
                    f"Run '/spec' then '/plan' to produce an implementation plan."
                )

    # Check 2: Frontmatter has 'feature:' (spec) instead of 'task:' (plan)
    if "feature" in fm and "task" not in fm:
        errors.append(
            "Frontmatter has 'feature:' key (spec pattern) but no 'task:' key (plan pattern). "
            "This looks like a spec file."
        )

    # Check 3: Missing plan structural markers
    has_phases = bool(re.search(r"^##+ Phase \d", text, re.MULTILINE))
    has_tasks = bool(re.search(r"^###+ Task \d+\.\d+", text, re.MULTILINE))
    has_groups = "**Group:**" in text
    has_verification_cmds = "**Verification Commands:**" in text

    if not has_phases:
        errors.append(
            "No '## Phase N' headings found. Plan files must have implementation phases."
        )
    if not has_tasks and not has_groups:
        errors.append(
            "No task definitions (Task N.N) or group labels (**Group:**) found. "
            "Plan files must have structured tasks with group assignments."
        )

    # Check 4: Spec-only markers that plans shouldn't have
    has_signatures = bool(re.search(
        r"^##+ (Function Signatures|Core Types|Hard Constraints)", text, re.MULTILINE
    ))
    if has_signatures and not has_phases:
        errors.append(
            "File has spec-only sections (Function Signatures / Core Types / Hard Constraints) "
            "but no implementation phases. This is a spec, not a plan."
        )

    return errors


# -- Parsed plan validation --------------------------------------------------


def validate_parsed_plan(plan: ParsedPlan) -> list[str]:
    """Run cross-item semantic checks that Pydantic cannot express.

    Field presence, non-empty strings, and min-length lists are enforced
    by Pydantic constraints on the model fields. This function only checks
    cross-item relationships: sequential numbering, unique task IDs, and
    cross-group file overlap.
    """
    errors: list[str] = []

    for i, phase in enumerate(plan.phases):
        prefix = f"Phase {phase.number}"

        if phase.number != i + 1:
            errors.append(f"{prefix}: expected phase number {i + 1}, got {phase.number}")

        seen_ids: set[str] = set()
        groups: dict[str, list[str]] = {}
        for task in phase.tasks:
            if task.id in seen_ids:
                errors.append(f"{prefix}, Task {task.id}: duplicate task ID")
            seen_ids.add(task.id)
            groups.setdefault(task.group, []).extend(task.files)

        group_names = list(groups.keys())
        for gi in range(len(group_names)):
            for gj in range(gi + 1, len(group_names)):
                overlap = set(groups[group_names[gi]]) & set(groups[group_names[gj]])
                if overlap:
                    errors.append(
                        f"{prefix}: Group {group_names[gi]} and Group {group_names[gj]} "
                        f"share files: {', '.join(sorted(overlap))}"
                    )

        if not phase.verification_commands:
            display.warn(
                f"Phase {phase.number} ({phase.name}) has no "
                "verification_commands \u2014 verification will be skipped for this phase"
            )

    return errors


# -- Plan processing ----------------------------------------------------------


def _dry_run_parsed_plan() -> ParsedPlan:
    """Create a plausible dry-run ParsedPlan."""
    return ParsedPlan(
        title="(dry run plan)",
        overview="(dry run)",
        current_state="(dry run)",
        desired_end_state="(dry run)",
        phases=[
            PlanPhase(
                number=1,
                name="(dry run phase)",
                goal="(dry run)",
                tasks=[
                    PlanTask(
                        id="1.1",
                        name="(dry run task)",
                        files=["example.ts"],
                        group="A",
                        steps=["(dry run step)"],
                    )
                ],
                verification=["(dry run verification)"],
            )
        ],
        testing_strategy="(dry run)",
        risks=[],
        open_questions="None",
    )


def run_plan_processing(
    config: Config, work_dir: Path
) -> ParsedPlan:
    """Parse the plan file into a structured representation.

    Calls Claude with --json-schema to extract the plan structure, then
    runs deterministic validation. Exits if the plan cannot be parsed or
    fails validation.
    """
    path = config.plan_path
    display.info("Parsing plan into structured representation...")

    plan_text = path.read_text()
    parsed = run_claude_structured(
        prompt=(
            "Extract the implementation plan into the provided JSON schema. "
            "Read the plan text below and populate every field precisely from "
            "the plan content. Do not invent information -- extract only what "
            "is written.\n\n"
            "For each task, the 'id' is the X.Y number (e.g. '1.1', '2.3'), "
            "'name' is the short descriptive name after the Task heading, "
            "'files' are from the **Files:** line, 'group' is from the **Group:** "
            "line, and 'steps' are the checkbox items (without the '- [ ] ' or "
            "'- [x] ' prefix).\n\n"
            "For verification, extract each verification item as a string "
            "(without checkbox prefix).\n\n"
            "For verification_commands, extract each command from the "
            "**Verification Commands:** backtick-quoted items as raw strings "
            "(e.g., `make typecheck` becomes `make typecheck`).\n\n"
            f"Plan text:\n\n{plan_text}"
        ),
        schema=ParsedPlan,
        effort="low",
        work_dir=work_dir,
        dry_run=config.dry_run,
        streaming=False,
        worktree=config.worktree,
        dry_run_default=_dry_run_parsed_plan(),
    )

    display.result_panel("Parsed Plan", parsed)

    # Deterministic validation + auto-fix loop
    max_fix_attempts = 3
    for attempt in range(max_fix_attempts):
        validation_errors = validate_parsed_plan(parsed)
        if not validation_errors:
            display.info("[green]Plan structure validated.[/green]")
            return parsed

        display.warn("Plan structure validation found issues:")
        for err in validation_errors:
            display.info(f"  - {err}")

        if attempt >= max_fix_attempts - 1:
            display.error(
                "The plan file cannot be reliably executed after "
                f"{max_fix_attempts} fix attempts. Fix the plan manually and re-run."
            )
            sys.exit(1)

        # Ask an agent to fix the structural issues in the plan file
        display.info(f"Fixing plan structure (attempt {attempt + 1}/{max_fix_attempts})...")
        fix_result = run_claude_structured(
            prompt=(
                f"The plan file at {path} has structural issues that prevent "
                "automated execution. Fix ONLY these specific issues by editing "
                "the plan file:\n\n"
                + "\n".join(f"- {e}" for e in validation_errors)
                + "\n\n"
                "Rules for fixing:\n"
                "- Cross-group file overlap: merge the tasks that share files "
                "into the same Group (pick the group that already has more tasks "
                "touching that file, or merge the smaller group into the larger). "
                "Do NOT split tasks or change their content.\n"
                "- Non-sequential phase numbering: renumber phases sequentially "
                "starting from 1. Update task IDs to match (e.g., if Phase 3 "
                "becomes Phase 2, task 3.1 becomes 2.1).\n"
                "- Duplicate task IDs: renumber tasks sequentially within their "
                "phase.\n\n"
                "Make minimal edits. Do not change task content, goals, steps, "
                "files, or verification. Only fix the structural issues listed above."
            ),
            schema=ApplyFeedbackResult,
            effort="low",
            work_dir=work_dir,
            dry_run=config.dry_run,
            streaming=False,
            worktree=config.worktree,
            dry_run_default=ApplyFeedbackResult(changes_applied=0, summary="(dry run)"),
        )
        display.result_panel("Plan Structure Fix", fix_result)

        # Re-parse the modified plan
        display.info("Re-parsing fixed plan...")
        plan_text = path.read_text()
        parsed = run_claude_structured(
            prompt=(
                "Extract the implementation plan into the provided JSON schema. "
                "Read the plan text below and populate every field precisely from "
                "the plan content. Do not invent information -- extract only what "
                "is written.\n\n"
                "For each task, the 'id' is the X.Y number (e.g. '1.1', '2.3'), "
                "'name' is the short descriptive name after the Task heading, "
                "'files' are from the **Files:** line, 'group' is from the **Group:** "
                "line, and 'steps' are the checkbox items (without the '- [ ] ' or "
                "'- [x] ' prefix).\n\n"
                "For verification, extract each verification item as a string "
                "(without checkbox prefix).\n\n"
                "For verification_commands, extract each command from the "
                "**Verification Commands:** backtick-quoted items as raw strings "
                "(e.g., `make typecheck` becomes `make typecheck`).\n\n"
                f"Plan text:\n\n{plan_text}"
            ),
            schema=ParsedPlan,
            effort="low",
            work_dir=work_dir,
            dry_run=config.dry_run,
            streaming=False,
            worktree=config.worktree,
            dry_run_default=_dry_run_parsed_plan(),
        )
        display.result_panel("Re-parsed Plan", parsed)

    # Unreachable, but satisfies the type checker
    sys.exit(1)
