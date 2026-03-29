"""Tests for the RPI Display layer and signal handling."""

from __future__ import annotations

import threading
from io import StringIO

import pytest
from rich.console import Console

from rpi.display import _STAGES, Display
from rpi.plan import PlanPhase, PlanTask
from rpi.process import _child_procs, _interrupt, _sigint_handler, cleanup_children
from rpi.review import ReviewIssue, ReviewResult
from rpi.stages.commit import CommitResult
from rpi.stages.implement import PhaseResult


@pytest.fixture
def display():
    """Create a Display instance for testing."""
    return Display()


@pytest.fixture
def sample_review_result():
    return ReviewResult(
        score=16,
        correctness=4,
        completeness=4,
        simplicity=4,
        clarity=4,
        issues=[
            ReviewIssue(severity="critical", description="Missing error handling in auth flow"),
            ReviewIssue(severity="note", description="Consider adding type hints"),
        ],
        suggested_changes=["Add try/catch around auth calls", "Add type annotations"],
    )


@pytest.fixture
def sample_phase_result():
    return PhaseResult(
        status="success",
        phase=1,
        summary="Implemented authentication module with OAuth2 support",
        errors="None",
        verification="All tests passing",
    )


@pytest.fixture
def sample_commit_result():
    return CommitResult(
        status="success",
        num_commits=3,
        commits=[
            "feat: add OAuth2 authentication",
            "test: add auth integration tests",
            "docs: update API documentation",
        ],
        summary="Added authentication with tests and docs",
        errors="None",
    )


@pytest.fixture
def sample_plan_phase():
    return PlanPhase(
        number=1,
        name="Add Authentication",
        goal="Implement OAuth2 auth flow",
        tasks=[
            PlanTask(
                id="1.1",
                name="Create auth module",
                files=["src/auth.ts"],
                group="A",
                steps=["Create OAuth2 client", "Add token refresh"],
            ),
            PlanTask(
                id="1.2",
                name="Add auth middleware",
                files=["src/middleware.ts"],
                group="A",
                steps=["Create middleware function"],
            ),
        ],
        verification=["npm run test", "npm run typecheck"],
        verification_commands=["npm run test", "npm run typecheck"],
    )




class TestTruncate:
    def test_no_truncation_when_fits(self, display):
        assert display.truncate("short", max_width=80) == "short"

    def test_truncation_with_ellipsis(self, display):
        result = display.truncate("a" * 100, max_width=50)
        assert len(result) == 50
        assert result.endswith("\u2026")

    def test_exact_boundary(self, display):
        text = "a" * 50
        assert display.truncate(text, max_width=50) == text

    def test_one_over(self, display):
        result = display.truncate("a" * 51, max_width=50)
        assert len(result) == 50
        assert result.endswith("\u2026")

    def test_empty_string(self, display):
        assert display.truncate("", max_width=50) == ""




class TestStatusStyle:
    def test_green_statuses(self):
        for status in ("success", "ready", "clean", "passed", "none"):
            assert Display._status_style(status) == "green", f"Expected green for {status}"

    def test_red_statuses(self):
        for status in ("failed", "needsmajorrevision"):
            assert Display._status_style(status) == "red", f"Expected red for {status}"

    def test_yellow_statuses(self):
        for status in ("needsrevision", "issuesremaining"):
            assert Display._status_style(status) == "yellow", f"Expected yellow for {status}"

    def test_case_insensitivity(self):
        assert Display._status_style("SUCCESS") == "green"
        assert Display._status_style("Failed") == "red"

    def test_unknown(self):
        assert Display._status_style("unknown_status") == ""




class TestModelSummaryRich:
    def test_plan_phase(self, sample_plan_phase):
        result = Display._model_summary_rich(sample_plan_phase)
        assert "Phase 1" in result
        assert "Add Authentication" in result
        assert "2 tasks" in result

    def test_review_issue(self):
        issue = ReviewIssue(severity="critical", description="Missing error handling")
        result = Display._model_summary_rich(issue)
        assert "CRITICAL" in result
        assert "Missing error handling" in result

    def test_plan_task(self):
        task = PlanTask(
            id="1.1",
            name="Create module",
            files=["src/mod.ts"],
            group="A",
            steps=["Create it"],
        )
        result = Display._model_summary_rich(task)
        assert "Task 1.1" in result
        assert "Create module" in result
        assert "A" in result

    def test_unknown_model(self):
        from pydantic import BaseModel

        class Unknown(BaseModel):
            foo: str = "bar"

        result = Display._model_summary_rich(Unknown())
        assert result == ""




class TestResultPanel:
    def test_phase_result_renders(self, display, sample_phase_result):
        """PhaseResult renders without exception."""
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        display.result_panel("Phase 1", sample_phase_result)
        output = buf.getvalue()
        assert "Phase 1" in output
        assert "success" in output

    def test_review_result_with_issues(self, display, sample_review_result):
        """ReviewResult with issues renders without exception."""
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        display.result_panel("Review", sample_review_result)
        output = buf.getvalue()
        assert "Review" in output
        assert "Missing error handling" in output

    def test_commit_result(self, display, sample_commit_result):
        """CommitResult renders without exception."""
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        display.result_panel("Commit", sample_commit_result)
        output = buf.getvalue()
        assert "Commit" in output
        assert "OAuth2" in output

    def test_nested_models(self, display, sample_review_result):
        """ReviewResult with nested ReviewIssue list renders without crash."""
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        display.result_panel("Nested", sample_review_result)
        # No exception means success




class TestSummaryTable:
    def test_all_completed(self, display):
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        rows = [
            ("Plan Review", "[green]ok[/green]", "score 9/10, 1 iter"),
            ("Implement", "[green]ok[/green]", "3 phases"),
            ("Review-Fix", "[green]ok[/green]", "score 8/10, 2 iter"),
            ("Commit", "[green]ok[/green]", "2 commits"),
            ("PR", "[green]ok[/green]", "https://github.com/example/pull/1"),
        ]
        display.summary_table("Summary  My Plan", rows)
        output = buf.getvalue()
        assert "Summary" in output
        assert "Plan Review" in output

    def test_mixed_states(self, display):
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        rows = [
            ("Plan Review", "[green]ok[/green]", "score 9/10"),
            ("Implement", "[green]ok[/green]", "2 phases"),
            ("Review-Fix", "[red]--[/red]", "score 5/10 (issues_remaining)"),
            ("Commit", "", "[dim]skipped[/dim]"),
        ]
        display.summary_table("Summary", rows)
        output = buf.getvalue()
        assert "Summary" in output

    def test_with_footer(self, display):
        buf = StringIO()
        display.stdout = Console(file=buf, width=80)
        rows = [("Commit", "[green]ok[/green]", "1 commits")]
        display.summary_table("Summary", rows, footer={"Worktree": "/tmp/wt"})
        output = buf.getvalue()
        assert "/tmp/wt" in output




class TestStageBar:
    def test_each_stage_renders(self, display):
        """Each stage key renders without exception."""
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        for key, _ in _STAGES:
            display.stage_bar(key)

    def test_current_stage_in_output(self, display):
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        display.stage_bar("implement")
        output = buf.getvalue()
        assert "Implement" in output

    def test_all_stages_appear(self, display):
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        display.stage_bar("preflight")
        output = buf.getvalue()
        for _, label in _STAGES:
            assert label in output




class TestInterrupt:
    def test_interrupt_starts_unset(self):
        # Reset for test isolation
        _interrupt.clear()
        assert not _interrupt.is_set()

    def test_sigint_handler_sets_interrupt(self):
        _interrupt.clear()
        with pytest.raises(KeyboardInterrupt):
            _sigint_handler(0, None)
        assert _interrupt.is_set()
        _interrupt.clear()  # cleanup

    def test_cleanup_children_empty(self):
        """With empty _child_procs list, no crash."""
        original = _child_procs[:]
        _child_procs.clear()
        cleanup_children()  # should not raise
        _child_procs.extend(original)

    def test_interrupt_wait_returns_quickly(self):
        """Setting _interrupt causes wait to return promptly."""
        _interrupt.clear()

        def setter():
            import time
            time.sleep(0.1)
            _interrupt.set()

        t = threading.Thread(target=setter, daemon=True)
        t.start()

        # Should return in ~0.1s, not 5.0s
        result = _interrupt.wait(timeout=5.0)
        assert result is True
        _interrupt.clear()  # cleanup




class TestBanner:
    def test_renders_with_fields(self, display):
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        display.banner(
            "Review-Implement-Fix",
            "",
            [("Plan", "My Feature"), ("Phases", "3")],
        )
        output = buf.getvalue()
        assert "Review-Implement-Fix" in output
        assert "My Feature" in output

    def test_renders_with_empty_fields(self, display):
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        display.banner("Test", "", [])
        # No crash

    def test_renders_with_subtitle(self, display):
        buf = StringIO()
        display.console = Console(file=buf, width=80)
        display.banner("Test", "RESUMED", [("Plan", "Test")])
        output = buf.getvalue()
        assert "RESUMED" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
