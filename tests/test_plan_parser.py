"""Tests for parse_plan_from_markdown()."""

import pytest

from rpi.plan import (
    Plan,
    PlanPhase,
    PlanTask,
    parse_plan_from_markdown,
    serialize_plan_to_markdown,
)


def _make_plan(**overrides) -> Plan:
    defaults = dict(
        title="Widget Refactor",
        overview="Refactor the widget subsystem.",
        current_state="Widgets are monolithic.",
        desired_end_state="Widgets are modular.",
        phases=[
            PlanPhase(
                number=1,
                name="Extract interfaces",
                goal="Define widget interfaces",
                tasks=[
                    PlanTask(
                        id="1.1",
                        name="Create interface file",
                        files=["src/widget.py"],
                        group="A",
                        steps=["Define WidgetProtocol", "Add type exports"],
                    ),
                    PlanTask(
                        id="1.2",
                        name="Update imports",
                        files=["src/main.py"],
                        group="B",
                        steps=["Switch to new imports"],
                    ),
                ],
                verification=["All type checks pass", "Tests green"],
                verification_commands=["uv run ruff check src/", "uv run pytest"],
            ),
        ],
        testing_strategy="Run the full test suite.",
        risks=["Breaking changes to public API"],
        open_questions=["Should we deprecate the old interface?"],
    )
    defaults.update(overrides)
    return Plan(**defaults)


def _serialize(plan: Plan) -> str:
    return serialize_plan_to_markdown(
        plan, task_description="Refactor widgets", date="2026-04-02"
    )


class TestRoundTrip:
    def test_basic_round_trip(self):
        original = _make_plan()
        md = _serialize(original)
        parsed = parse_plan_from_markdown(md)

        assert parsed.title == original.title
        assert parsed.overview == original.overview
        assert parsed.current_state == original.current_state
        assert parsed.desired_end_state == original.desired_end_state
        assert parsed.testing_strategy == original.testing_strategy
        assert parsed.risks == original.risks
        assert parsed.open_questions == original.open_questions
        assert len(parsed.phases) == len(original.phases)

        for op, pp in zip(original.phases, parsed.phases, strict=True):
            assert pp.number == op.number
            assert pp.name == op.name
            assert pp.goal == op.goal
            assert pp.verification == op.verification
            assert pp.verification_commands == op.verification_commands
            assert len(pp.tasks) == len(op.tasks)
            for ot, pt in zip(op.tasks, pp.tasks, strict=True):
                assert pt.id == ot.id
                assert pt.name == ot.name
                assert pt.files == ot.files
                assert pt.group == ot.group
                assert pt.steps == ot.steps

    def test_multiple_phases(self):
        plan = _make_plan(
            phases=[
                PlanPhase(
                    number=1,
                    name="Phase one",
                    goal="Do first thing",
                    tasks=[
                        PlanTask(id="1.1", name="Task A", files=["a.py"], group="A", steps=["Step 1"]),
                    ],
                    verification=["Check A"],
                    verification_commands=["pytest -k a"],
                ),
                PlanPhase(
                    number=2,
                    name="Phase two",
                    goal="Do second thing",
                    tasks=[
                        PlanTask(id="2.1", name="Task B", files=["b.py"], group="A", steps=["Step 2"]),
                    ],
                    verification=["Check B"],
                    verification_commands=[],
                ),
            ],
        )
        md = _serialize(plan)
        parsed = parse_plan_from_markdown(md)
        assert len(parsed.phases) == 2
        assert parsed.phases[0].number == 1
        assert parsed.phases[1].number == 2
        assert parsed.phases[1].verification_commands == []


class TestHeadingDepthVariations:
    def test_deeper_phase_headings(self):
        plan = _make_plan()
        md = _serialize(plan)
        # Replace ### Phase with #### Phase
        md = md.replace("### Phase", "#### Phase")
        parsed = parse_plan_from_markdown(md)
        assert len(parsed.phases) == 1
        assert parsed.phases[0].name == "Extract interfaces"

    def test_deeper_task_headings(self):
        plan = _make_plan()
        md = _serialize(plan)
        # Replace ##### Task with ###### Task
        md = md.replace("##### Task", "###### Task")
        parsed = parse_plan_from_markdown(md)
        assert len(parsed.phases[0].tasks) == 2


class TestSubBullets:
    def test_sub_bullets_captured_in_step(self):
        """Indented sub-bullets under a checkbox are included in the step text."""
        plan = _make_plan()
        md = _serialize(plan)
        # Replace a flat step with one that has sub-bullets
        md = md.replace(
            "- [ ] Define WidgetProtocol",
            "- [ ] Define WidgetProtocol:\n  - field: name (str)\n  - field: value (int)",
        )
        parsed = parse_plan_from_markdown(md)
        step = parsed.phases[0].tasks[0].steps[0]
        assert step.startswith("Define WidgetProtocol:")
        assert "  - field: name (str)" in step
        assert "  - field: value (int)" in step

    def test_sub_bullets_round_trip(self):
        """Steps with sub-bullets survive serialize -> parse."""
        plan = _make_plan()
        plan.phases[0].tasks[0].steps[0] = (
            "Define WidgetProtocol:\n  - field: name (str)\n  - field: value (int)"
        )
        md = _serialize(plan)
        parsed = parse_plan_from_markdown(md)
        assert parsed.phases[0].tasks[0].steps[0] == plan.phases[0].tasks[0].steps[0]


class TestCompletedSteps:
    def test_checked_boxes_parsed(self):
        plan = _make_plan()
        md = _serialize(plan)
        # Replace unchecked with checked
        md = md.replace("- [ ]", "- [x]")
        parsed = parse_plan_from_markdown(md)
        assert parsed.phases[0].tasks[0].steps == ["Define WidgetProtocol", "Add type exports"]
        assert parsed.phases[0].verification == ["All type checks pass", "Tests green"]


class TestMissingOptionalFields:
    def test_no_open_questions(self):
        plan = _make_plan(open_questions=[])
        md = _serialize(plan)
        parsed = parse_plan_from_markdown(md)
        assert parsed.open_questions == []

    def test_no_verification_commands(self):
        phase = _make_plan().phases[0].model_copy(update={"verification_commands": []})
        plan = _make_plan(phases=[phase])
        md = _serialize(plan)
        parsed = parse_plan_from_markdown(md)
        assert parsed.phases[0].verification_commands == []


    def test_inline_verification_commands(self):
        """Verification commands written inline (not as a bulleted list) are parsed."""
        plan = _make_plan()
        md = _serialize(plan)
        # Replace the bulleted list format with inline format
        md = md.replace(
            "**Verification Commands:**\n- `uv run ruff check src/`\n- `uv run pytest`",
            "**Verification Commands:** `uv run ruff check src/ && uv run pytest`",
        )
        parsed = parse_plan_from_markdown(md)
        assert parsed.phases[0].verification_commands == [
            "uv run ruff check src/ && uv run pytest"
        ]


class TestErrorCases:
    def test_missing_title(self):
        md = "## Overview\n\nSome overview\n"
        with pytest.raises(ValueError, match="Missing plan title"):
            parse_plan_from_markdown(md)

    def test_missing_phases_section(self):
        md = (
            "# My Plan\n\n"
            "## Overview\n\nOverview text\n\n"
            "## Current State\n\nCurrent\n\n"
            "## Desired End State\n\nDesired\n\n"
            "## Testing Strategy\n\nTest\n\n"
            "## Risks and Edge Cases\n\n- Risk 1\n"
        )
        with pytest.raises(ValueError, match=r"Missing required section.*Implementation Phases"):
            parse_plan_from_markdown(md)

    def test_no_tasks_in_phase(self):
        md = (
            "# My Plan\n\n"
            "## Overview\n\nOverview text\n\n"
            "## Current State\n\nCurrent\n\n"
            "## Desired End State\n\nDesired\n\n"
            "## Implementation Phases\n\n"
            "### Phase 1: Do stuff\n\n"
            "**Goal:** Do the thing\n\n"
            "**Verification:**\n- [ ] Check it\n\n"
            "## Testing Strategy\n\nTest\n\n"
            "## Risks and Edge Cases\n\n- Risk 1\n"
        )
        with pytest.raises(ValueError, match="Phase 1: no tasks found"):
            parse_plan_from_markdown(md)

    def test_missing_task_files(self):
        md = (
            "# My Plan\n\n"
            "## Overview\n\nOverview text\n\n"
            "## Current State\n\nCurrent\n\n"
            "## Desired End State\n\nDesired\n\n"
            "## Implementation Phases\n\n"
            "### Phase 1: Do stuff\n\n"
            "**Goal:** Do the thing\n\n"
            "##### Task 1.1: A task\n"
            "**Group:** A\n"
            "- [ ] A step\n\n"
            "**Verification:**\n- [ ] Check it\n\n"
            "## Testing Strategy\n\nTest\n\n"
            "## Risks and Edge Cases\n\n- Risk 1\n"
        )
        with pytest.raises(ValueError, match=r"Task 1.1: missing \*\*Files:\*\*"):
            parse_plan_from_markdown(md)


class TestFrontmatterStripping:
    def test_with_frontmatter(self):
        plan = _make_plan()
        md = _serialize(plan)
        assert md.startswith("---")
        parsed = parse_plan_from_markdown(md)
        assert parsed.title == "Widget Refactor"

    def test_without_frontmatter(self):
        plan = _make_plan()
        md = _serialize(plan)
        # Strip frontmatter
        end = md.find("---", 3)
        md = md[end + 3:].lstrip("\n")
        parsed = parse_plan_from_markdown(md)
        assert parsed.title == "Widget Refactor"
