"""Plan types, validation, and parsing."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Config
from .display import Display, green
from .iteration import ApplyFeedbackResult
from .process import run_claude_structured


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


class Plan(BaseModel):
    title: str = Field(min_length=1, description="Plan title from the top-level heading")
    overview: str = Field(min_length=1, description="1-3 sentence overview of the plan")
    current_state: str = Field(min_length=1, description="Summary of the current state section")
    desired_end_state: str = Field(min_length=1, description="Summary of the desired end state")
    phases: list[PlanPhase] = Field(
        min_length=1, description="All implementation phases in order"
    )
    testing_strategy: str = Field(min_length=1, description="End-to-end testing approach")
    risks: list[str] = Field(description="Risks and edge cases")
    open_questions: list[str] = Field(
        default_factory=list, description="Any unresolved questions"
    )


@dataclass
class PlanMetadata:
    title: str
    num_phases: int
    completed_phases: int


def extract_plan_frontmatter(plan_path: Path) -> dict[str, str]:
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


def validate_plan_file(path: Path) -> list[str]:
    """Cheap, deterministic pre-flight check that a file looks like a plan.

    Returns a list of error strings. Empty list means the file passes.
    Catches common mistakes like passing a spec, research doc, or random
    markdown file before any Claude API calls are made.
    """
    errors: list[str] = []
    text = path.read_text()
    fm = extract_plan_frontmatter(path)

    # Check 1: Wrong directory (spec or research file)
    parts = path.parts
    if ".claude" in parts:
        claude_idx = len(parts) - 1 - parts[::-1].index(".claude")
        if claude_idx + 1 < len(parts):
            subdir = parts[claude_idx + 1]
            if subdir == "specs":
                errors.append(
                    "This file is in .claude/specs/ — it's a spec, not a plan. "
                    "Run '/plan' on it first to produce an implementation plan."
                )
            elif subdir == "research":
                errors.append(
                    "This file is in .claude/research/ — it's a research doc, not a plan. "
                    "Run '/spec' then '/plan' to produce an implementation plan."
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


def serialize_plan_to_markdown(
    plan: Plan,
    task_description: str,
    date: str,
    research_path: str | None = None,
    spec_path: str | None = None,
) -> str:
    """Serialize a Plan object to markdown matching the format validate_plan_file() expects."""
    lines: list[str] = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f"date: {date}")
    lines.append(f'task: "{task_description}"')
    if spec_path is not None:
        lines.append(f"spec: {spec_path}")
    if research_path is not None:
        lines.append(f"research: {research_path}")
    lines.append("status: draft")
    lines.append("---")
    lines.append("")
    lines.append(f"# {plan.title} Implementation Plan")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(plan.overview)
    lines.append("")
    lines.append("## Current State")
    lines.append("")
    lines.append(plan.current_state)
    lines.append("")
    lines.append("## Desired End State")
    lines.append("")
    lines.append(plan.desired_end_state)
    lines.append("")
    lines.append("## Implementation Phases")
    lines.append("")

    for phase in plan.phases:
        lines.append(f"### Phase {phase.number}: {phase.name}")
        lines.append("")
        lines.append(f"**Goal:** {phase.goal}")
        lines.append("")
        lines.append("#### Tasks")
        lines.append("")

        for task in phase.tasks:
            lines.append(f"##### Task {task.id}: {task.name}")
            lines.append(f"**Files:** {', '.join(task.files)}")
            lines.append(f"**Group:** {task.group}")
            for step in task.steps:
                lines.append(f"- [ ] {step}")
            lines.append("")

        lines.append("**Verification:**")
        for item in phase.verification:
            lines.append(f"- [ ] {item}")
        lines.append("")

        if phase.verification_commands:
            lines.append("**Verification Commands:**")
            for cmd in phase.verification_commands:
                lines.append(f"- `{cmd}`")
            lines.append("")

    lines.append("## Testing Strategy")
    lines.append("")
    lines.append(plan.testing_strategy)
    lines.append("")
    lines.append("## Risks and Edge Cases")
    lines.append("")
    for risk in plan.risks:
        lines.append(f"- {risk}")
    lines.append("")
    lines.append("## Open Questions")
    lines.append("")
    if plan.open_questions:
        for q in plan.open_questions:
            lines.append(f"- {q}")
    else:
        lines.append("None")
    lines.append("")

    return "\n".join(lines)


def parse_plan_from_markdown(text: str) -> Plan:
    """Parse plan markdown into a Plan model deterministically."""
    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].lstrip("\n")

    lines = text.split("\n")

    # Extract title from first # heading
    title = ""
    for line in lines:
        m = re.match(r"^#\s+(.+)", line)
        if m:
            title = m.group(1).strip()
            # Strip trailing "Implementation Plan"
            title = re.sub(r"\s+Implementation Plan$", "", title)
            break
    if not title:
        raise ValueError("Missing plan title: no `# Title` heading found")

    # Split by ## headings into sections
    sections: dict[str, str] = {}
    current_section = ""
    current_lines: list[str] = []
    for line in lines:
        m = re.match(r"^##\s+(.+)", line)
        if m:
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    required = ["Implementation Phases"]
    for req in required:
        if req not in sections:
            raise ValueError(f"Missing required section: ## {req}")

    overview = sections.get("Overview", "")
    current_state = sections.get("Current State", "")
    desired_end_state = sections.get("Desired End State", "")
    testing_strategy = sections.get("Testing Strategy", "")

    risks_text = sections.get("Risks and Edge Cases", "")
    risks = [
        re.sub(r"^-\s*", "", line).strip()
        for line in risks_text.split("\n")
        if line.strip().startswith("-")
    ]

    oq_text = sections.get("Open Questions", "")
    open_questions = [
        re.sub(r"^-\s*", "", line).strip()
        for line in oq_text.split("\n")
        if line.strip().startswith("-")
    ]

    # Parse phases
    phases_text = sections["Implementation Phases"]
    phase_chunks = re.split(r"(?m)^#{2,6}\s+Phase\s+(\d+):\s*(.+)", phases_text)
    # phase_chunks: [preamble, num1, name1, body1, num2, name2, body2, ...]
    phases: list[PlanPhase] = []
    i = 1
    while i < len(phase_chunks) - 2:
        phase_num = int(phase_chunks[i])
        phase_name = phase_chunks[i + 1].strip()
        phase_body = phase_chunks[i + 2]
        i += 3

        # Extract goal
        goal_m = re.search(r"\*\*Goal:\*\*\s*(.+)", phase_body)
        goal = goal_m.group(1).strip() if goal_m else ""

        # Extract verification items
        verif_m = re.search(
            r"\*\*Verification:\*\*\s*\n((?:\s*-\s*\[.\]\s*.+\n?)+)", phase_body
        )
        verification: list[str] = []
        if verif_m:
            for vm in re.finditer(r"-\s*\[.\]\s*(.+)", verif_m.group(1)):
                verification.append(vm.group(1).strip())

        # Extract verification commands
        vcmd_m = re.search(
            r"\*\*Verification Commands:\*\*\s*\n((?:\s*-\s*`.+`\s*\n?)+)", phase_body
        )
        verification_commands: list[str] = []
        if vcmd_m:
            for cm in re.finditer(r"-\s*`(.+?)`", vcmd_m.group(1)):
                verification_commands.append(cm.group(1).strip())

        # Extract tasks
        task_chunks = re.split(r"(?m)^#{2,6}\s+Task\s+([\d.]+):\s*(.+)", phase_body)
        tasks: list[PlanTask] = []
        j = 1
        while j < len(task_chunks) - 2:
            task_id = task_chunks[j].strip()
            task_name = task_chunks[j + 1].strip()
            task_body = task_chunks[j + 2]
            j += 3

            # Truncate task body at **Verification:** to avoid capturing
            # phase-level verification items as task steps
            verif_boundary = re.search(r"\*\*Verification", task_body)
            task_content = task_body[:verif_boundary.start()] if verif_boundary else task_body

            files_m = re.search(r"\*\*Files:\*\*\s*(.+)", task_content)
            if not files_m:
                raise ValueError(f"Task {task_id}: missing **Files:**")
            files = [f.strip() for f in files_m.group(1).split(",") if f.strip()]

            group_m = re.search(r"\*\*Group:\*\*\s*(.+)", task_content)
            group = group_m.group(1).strip() if group_m else "A"

            steps = [
                m.group(1).strip()
                for m in re.finditer(r"-\s*\[.\]\s*(.+)", task_content)
            ]

            tasks.append(PlanTask(
                id=task_id, name=task_name, files=files, group=group, steps=steps,
            ))

        if not tasks:
            raise ValueError(f"Phase {phase_num}: no tasks found")

        phases.append(PlanPhase(
            number=phase_num, name=phase_name, goal=goal, tasks=tasks,
            verification=verification, verification_commands=verification_commands,
        ))

    return Plan(
        title=title, overview=overview, current_state=current_state,
        desired_end_state=desired_end_state, phases=phases,
        testing_strategy=testing_strategy, risks=risks, open_questions=open_questions,
    )


def plan_file_path(title: str, date: str) -> Path:
    """Return the plan file path: .claude/plans/YYYY-MM-DD-<kebab-title>.md."""
    kebab = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return Path(f".claude/plans/{date}-{kebab}.md")


def validate_plan(plan: Plan, display: Display | None = None) -> list[str]:
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

        if not phase.verification_commands and display is not None:
            display.warn(
                f"Phase {phase.number} ({phase.name}) has no "
                "verification_commands \u2014 verification will be skipped for this phase"
            )

    return errors


def run_plan_processing(
    config: Config, work_dir: Path, display: Display
) -> Plan:
    """Parse the plan file into a structured representation.

    Calls Claude with --json-schema to extract the plan structure, then
    runs deterministic validation. Exits if the plan cannot be parsed or
    fails validation.
    """
    path = config.plan_path
    display.info("Parsing plan into structured representation...")

    plan_text = path.read_text()
    with display.activity("Parse Plan", "parse-plan") as act:
        parsed = parse_plan_from_markdown(plan_text)
        act.complete("success", f"{parsed.title} ({len(parsed.phases)} phases)")

    # Deterministic validation + auto-fix loop
    max_fix_attempts = 3
    for attempt in range(max_fix_attempts):
        validation_errors = validate_plan(parsed, display)
        if not validation_errors:
            display.info(green("Plan structure validated."))
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
        with display.activity("Fix Plan Structure", f"fix-plan-{attempt + 1}") as act:
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
                worktree=config.worktree,
                activity=act,
            )
            act.complete("success", f"{fix_result.changes_applied} changes — {fix_result.summary}")

        # Re-parse the modified plan
        plan_text = path.read_text()
        with display.activity("Re-parse Plan", f"reparse-plan-{attempt + 1}") as act:
            parsed = parse_plan_from_markdown(plan_text)
            act.complete("success", f"{parsed.title} ({len(parsed.phases)} phases)")

    # Unreachable, but satisfies the type checker
    sys.exit(1)
