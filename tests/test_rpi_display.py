"""Tests for the RPI Display layer."""

from __future__ import annotations

import os
import threading
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.panel import Panel

from rpi.display import (
    Display,
    QuorumActivity,
    StreamActivity,
)


@pytest.fixture
def display(tmp_path):
    """Create a Display instance for testing."""
    return Display(verbose=False, log_dir=tmp_path / "logs")


@pytest.fixture
def verbose_display(tmp_path):
    """Create a verbose Display instance for testing."""
    return Display(verbose=True, log_dir=tmp_path / "logs")


@pytest.fixture
def captured_display(tmp_path):
    """Create a Display with captured stderr and stdout consoles."""
    d = Display(verbose=False, log_dir=tmp_path / "logs")
    stderr_buf = StringIO()
    stdout_buf = StringIO()
    d._console = Console(file=stderr_buf, width=80)
    d._stdout = Console(file=stdout_buf, width=80)
    return d, stderr_buf, stdout_buf


class TestDisplayConstructor:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        Display(verbose=False, log_dir=log_dir)
        assert log_dir.is_dir()

    def test_default_not_verbose(self, display):
        assert display._verbose is False

    def test_verbose_flag(self, verbose_display):
        assert verbose_display._verbose is True

    def test_lock_initialized(self, display):
        assert isinstance(display._lock, type(threading.Lock()))

    def test_no_active_activity(self, display):
        assert display._active is None


class TestStreamActivity:
    def test_stream_line_writes_to_log(self, display, tmp_path):
        with display.activity("Test", "test-log") as act:
            act.stream_line("line one")
            act.stream_line("line two")
            act.complete("success", "done")
        log_file = tmp_path / "logs" / "test-log.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "line one" in content
        assert "line two" in content

    def test_ring_buffer_caps_at_ring_max(self, display):
        with display.activity("Test", "test-ring", ring_max=3) as act:
            for i in range(10):
                act.stream_line(f"line {i}")
            assert len(act._ring_buffer) == 3
            assert [t.plain for t in act._ring_buffer] == ["line 7", "line 8", "line 9"]
            act.complete("success", "done")

    def test_stream_line_raises_after_complete(self, display):
        with display.activity("Test", "test-raise") as act:
            act.complete("success", "done")
            with pytest.raises(RuntimeError, match="already completed"):
                act.stream_line("too late")

    def test_is_stream_activity(self, display):
        with display.activity("Test", "test-type") as act:
            assert isinstance(act, StreamActivity)
            act.complete("success", "done")


class TestQuorumActivity:
    def test_stream_line_increments_counts(self, display):
        with display.quorum_activity("Review", "quorum-log", reviewer_count=3) as act:
            act.stream_line("event a", reviewer=0)
            act.stream_line("event b", reviewer=0)
            act.stream_line("event c", reviewer=1)
            assert act._event_counts == [2, 1, 0]
            assert [t.plain for t in act._ring_buffers[0]] == ["event a", "event b"]
            assert [t.plain for t in act._ring_buffers[1]] == ["event c"]
            assert [t.plain for t in act._ring_buffers[2]] == []
            act.complete("success", "done")

    def test_log_has_tagged_lines(self, display, tmp_path):
        with display.quorum_activity("Review", "quorum-tagged", reviewer_count=2) as act:
            act.stream_line("hello", reviewer=0)
            act.stream_line("world", reviewer=1)
            act.complete("success", "done")
        content = (tmp_path / "logs" / "quorum-tagged.log").read_text()
        assert "[reviewer 0] hello" in content
        assert "[reviewer 1] world" in content

    def test_is_quorum_activity(self, display):
        with display.quorum_activity("Review", "quorum-type", reviewer_count=2) as act:
            assert isinstance(act, QuorumActivity)
            act.complete("success", "done")

    def test_stream_line_raises_after_complete(self, display):
        with display.quorum_activity("Review", "quorum-raise", reviewer_count=2) as act:
            act.complete("success", "done")
            with pytest.raises(RuntimeError, match="already completed"):
                act.stream_line("too late", reviewer=0)

    def test_ring_buffer_truncation(self, display):
        with display.quorum_activity("Review", "quorum-trunc", reviewer_count=2, ring_max=3) as act:
            for i in range(5):
                act.stream_line(f"line {i}", reviewer=0)
            assert [t.plain for t in act._ring_buffers[0]] == ["line 2", "line 3", "line 4"]
            assert list(act._ring_buffers[1]) == []
            assert act._event_counts[0] == 5
            act.complete("success", "done")

    def test_build_panel_returns_panel(self, tmp_path):
        act = QuorumActivity(
            label="Test",
            log_path=tmp_path / "panel.log",
            reviewer_count=2,
            ring_max=4,
            verbose=False,
            _print=lambda s: None,
            _update_live=lambda r: None,
        )
        act.stream_line("hello", reviewer=0)
        result = act._build_panel()
        assert isinstance(result, Panel)
        act._close_log()

    def test_quorum_activity_ring_max_passthrough(self, display):
        with display.quorum_activity("Test", "ring-max-test", reviewer_count=2, ring_max=5) as act:
            assert act._ring_max == 5
            act.complete("success", "done")

    def test_adaptive_sizing_short_terminal(self, tmp_path):
        """Ring buffer is capped by terminal height so panels don't overflow."""
        act = QuorumActivity(
            label="Test",
            log_path=tmp_path / "adaptive.log",
            reviewer_count=3,
            ring_max=15,
            verbose=False,
            _print=lambda s: None,
            _update_live=lambda r: None,
        )
        # Feed more lines than the short terminal allows per reviewer.
        for i in range(10):
            act.stream_line(f"line {i}", reviewer=0)
        with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 20))):
            result = act._build_panel()
        assert isinstance(result, Panel)
        # effective = min(15, max(3, (20 - 2) // 3 - 2)) = 4
        inner_panels = list(result.renderable.renderables)
        # Reviewer 0 should have at most 4 visible lines.
        body_text = inner_panels[0].renderable.plain
        visible_lines = [line for line in body_text.split("\n") if line]
        assert len(visible_lines) <= 4
        act._close_log()


class TestActivityAutoComplete:
    def test_auto_complete_on_exception(self, display, tmp_path):
        with pytest.raises(ValueError, match="boom"), display.activity("Failing", "fail-log") as act:
                act.stream_line("before error")
                raise ValueError("boom")
        # Activity should have been auto-completed with "failed"
        assert act._completed is True
        content = (tmp_path / "logs" / "fail-log.log").read_text()
        assert "failed" in content


class TestNestedActivityRejection:
    def test_nested_activity_raises(self, display):
        with display.activity("Outer", "outer-log") as outer:
            with pytest.raises(RuntimeError, match="already active"), display.activity("Inner", "inner-log"):
                    pass  # pragma: no cover
            outer.complete("success", "done")


class TestConfirmAndFeedbackWithActivity:
    def test_confirm_raises_when_active(self, display):
        with display.activity("Active", "active-log") as act:
            with pytest.raises(RuntimeError, match="activity is active"):
                display.confirm("Continue?")
            act.complete("success", "done")

    def test_collect_feedback_raises_when_active(self, display):
        with display.activity("Active", "active-log") as act:
            with pytest.raises(RuntimeError, match="activity is active"):
                display.collect_feedback("Test")
            act.complete("success", "done")


class TestLogLevelMethods:
    def test_info(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.info("hello info")
        output = stderr_buf.getvalue()
        assert "hello info" in output

    def test_success(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.success("all good")
        output = stderr_buf.getvalue()
        assert "all good" in output

    def test_warn(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.warn("watch out")
        output = stderr_buf.getvalue()
        assert "watch out" in output

    def test_error(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.error("bad thing")
        output = stderr_buf.getvalue()
        assert "bad thing" in output

    def test_detail_hidden_when_not_verbose(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.detail("verbose only")
        output = stderr_buf.getvalue()
        assert "verbose only" not in output

    def test_detail_shown_when_verbose(self, tmp_path):
        d = Display(verbose=True, log_dir=tmp_path / "logs")
        buf = StringIO()
        d._console = Console(file=buf, width=80)
        d.detail("verbose only")
        output = buf.getvalue()
        assert "verbose only" in output


class TestBanner:
    def test_renders_with_fields(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.banner("Review-Implement-Fix", [("Plan", "My Feature"), ("Phases", "3")])
        output = stderr_buf.getvalue()
        assert "Review-Implement-Fix" in output
        assert "My Feature" in output

    def test_renders_with_empty_fields(self, captured_display):
        d, _stderr_buf, _ = captured_display
        d.banner("Test", [])
        # No crash


class TestStageHeader:
    def test_renders(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.stage_header("Implement Phase 1")
        output = stderr_buf.getvalue()
        assert "Implement Phase 1" in output


class TestSummaryTable:
    def test_all_completed(self, captured_display):
        d, _, stdout_buf = captured_display
        rows = [
            ("Plan Review", "[green]ok[/green]", "score 9/10, 1 iter"),
            ("Implement", "[green]ok[/green]", "3 phases"),
        ]
        d.summary_table("Summary", rows)
        output = stdout_buf.getvalue()
        assert "Summary" in output
        assert "Plan Review" in output

    def test_with_footer(self, captured_display):
        d, _, stdout_buf = captured_display
        rows = [("Commit", "[green]ok[/green]", "1 commits")]
        d.summary_table("Summary", rows, footer={"Worktree": "/tmp/wt"})
        output = stdout_buf.getvalue()
        assert "/tmp/wt" in output

    def test_with_total_elapsed(self, captured_display):
        d, _, stdout_buf = captured_display
        rows = [("Commit", "[green]ok[/green]", "1 commits")]
        d.summary_table("Summary", rows, total_elapsed=125.3)
        output = stdout_buf.getvalue()
        assert "Elapsed" in output
        assert "2m" in output

    def test_prints_to_stdout_not_stderr(self, captured_display):
        d, stderr_buf, stdout_buf = captured_display
        rows = [("Test", "ok", "detail")]
        d.summary_table("Summary", rows)
        assert "Summary" in stdout_buf.getvalue()
        assert "Summary" not in stderr_buf.getvalue()


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


class TestUntrustedContentSafety:
    """Untrusted strings passed to Display sinks must never be parsed as Rich markup.

    Regression coverage for MarkupError crashes — e.g. agent-generated text containing
    code snippets like ``noExternal: [/.*/]`` previously crashed the review result panel
    because Rich parsed the brackets as malformed closing tags. The fix moved all sinks
    onto :class:`rich.text.Text`, which treats strings as literals.
    """

    HOSTILE = "noExternal: [/.*/] and unclosed [green tag and [link=http://evil"

    def test_info_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.info(self.HOSTILE)  # must not raise
        assert self.HOSTILE in stderr_buf.getvalue()

    def test_success_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.success(self.HOSTILE)
        assert self.HOSTILE in stderr_buf.getvalue()

    def test_warn_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.warn(self.HOSTILE)
        assert self.HOSTILE in stderr_buf.getvalue()

    def test_error_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.error(self.HOSTILE)
        assert self.HOSTILE in stderr_buf.getvalue()

    def test_detail_does_not_parse_markup(self, tmp_path):
        d = Display(verbose=True, log_dir=tmp_path / "logs")
        stderr_buf = StringIO()
        d._console = Console(file=stderr_buf, width=80)
        d.detail(self.HOSTILE)
        assert self.HOSTILE in stderr_buf.getvalue()

    def test_result_panel_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.result_panel("Title with [brackets]", [self.HOSTILE, "another [/.*/] line"])
        out = stderr_buf.getvalue()
        assert "noExternal: [/.*/]" in out
        assert "another [/.*/] line" in out

    def test_banner_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.banner("Run [config]", [("field", self.HOSTILE)])
        assert "[/.*/]" in stderr_buf.getvalue()

    def test_stage_header_does_not_parse_markup(self, captured_display):
        d, stderr_buf, _ = captured_display
        d.stage_header(self.HOSTILE)
        assert "[/.*/]" in stderr_buf.getvalue()

    def test_summary_table_does_not_parse_markup(self, captured_display):
        d, _, stdout_buf = captured_display
        d.summary_table(
            "Summary",
            [("Stage", "ok", self.HOSTILE)],
        )
        assert "[/.*/]" in stdout_buf.getvalue()

    def test_summary_table_accepts_text_in_footer(self, captured_display):
        """Callers pass filelink() (Text) into footer values; must not crash."""
        from rpi.display import filelink

        d, _, stdout_buf = captured_display
        d.summary_table(
            "Summary",
            [("Stage", "ok", "done")],
            footer={"Plan": filelink("/tmp/plan.md"), "Note": "plain string"},
            total_elapsed=12.3,
        )
        out = stdout_buf.getvalue()
        assert "plan.md" in out
        assert "plain string" in out
        assert "Elapsed" in out


class TestActivityIntegration:
    def test_full_activity_lifecycle_with_log(self, tmp_path):
        d = Display(verbose=False, log_dir=tmp_path / "logs")
        with d.activity("Integration Test", "integration", ring_max=4) as act:
            act.stream_line("step 1: started")
            act.stream_line("step 2: processing")
            act.stream_line("step 3: finishing")
            act.complete("success", "all steps done")

        log_file = tmp_path / "logs" / "integration.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "step 1: started" in content
        assert "step 2: processing" in content
        assert "step 3: finishing" in content
        assert "success" in content
        assert "all steps done" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
