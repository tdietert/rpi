"""Tests for process handle primitives (_drain_queue, ClaudeProcess, QuorumProcess)."""

from __future__ import annotations

import json
import queue
import threading
from unittest.mock import MagicMock

import pytest

from rpi.process import (
    _SENTINEL,
    ClaudeProcess,
    QuorumProcess,
    _child_procs,
    _drain_queue,
    _interrupt,
    _parse_stream_event,
    _reader,
    _tool_input_summary,
    cleanup_children,
    sigint_handler,
)


def test_drain_queue_yields_items_then_stops():
    q: queue.Queue = queue.Queue()
    items = ["a", "b", "c"]
    for item in items:
        q.put(item)
    q.put(_SENTINEL)

    result = list(_drain_queue(q, threading.Event(), sentinel_count=1))
    assert result == items


def test_drain_queue_interrupt_exits():
    q: queue.Queue = queue.Queue()
    interrupt = threading.Event()
    interrupt.set()

    result = list(_drain_queue(q, interrupt, sentinel_count=1))
    assert result == []


def test_drain_queue_multiple_sentinels():
    q: queue.Queue = queue.Queue()
    q.put("a")
    q.put(_SENTINEL)
    q.put("b")
    q.put(_SENTINEL)
    q.put("c")
    q.put(_SENTINEL)

    result = list(_drain_queue(q, threading.Event(), sentinel_count=3))
    assert result == ["a", "b", "c"]


def test_claude_process_result_raises_before_exhausted():
    q: queue.Queue = queue.Queue()
    q.put(_SENTINEL)
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()

    cp = ClaudeProcess(
        proc=MagicMock(),
        q=q,
        reader_thread=t,
        interrupt=threading.Event(),
        result_holder=["some result"],
    )

    with pytest.raises(RuntimeError, match=r"lines.*must be exhausted"):
        cp.result()


def test_quorum_process_results_raises_before_exhausted():
    q: queue.Queue = queue.Queue()
    q.put(_SENTINEL)
    q.put(_SENTINEL)

    qp = QuorumProcess(
        procs=[MagicMock(), MagicMock()],
        q=q,
        reader_threads=[],
        interrupt=threading.Event(),
        result_holders=[["r1"], ["r2"]],
    )

    with pytest.raises(RuntimeError, match=r"tagged_lines.*must be exhausted"):
        qp.results()


def test_reader_pushes_sentinel_on_exception():
    """Even if the process stdout iteration raises, the sentinel is still pushed."""
    proc = MagicMock()
    proc.stdout = iter([])  # empty — simulates immediate EOF
    proc.wait.return_value = 0

    q: queue.Queue = queue.Queue()
    result_holder = [""]

    _reader(proc, q, result_holder)

    assert q.get_nowait() is _SENTINEL


def test_reader_pushes_sentinel_on_error():
    """If proc.stdout raises an exception, sentinel is still pushed."""
    proc = MagicMock()

    def exploding_iter():
        raise RuntimeError("boom")
        yield  # makes this a generator

    proc.stdout = exploding_iter()

    q: queue.Queue = queue.Queue()
    result_holder = [""]

    with pytest.raises(RuntimeError, match="boom"):
        _reader(proc, q, result_holder)

    assert q.get_nowait() is _SENTINEL


class TestInterrupt:
    def test_interrupt_starts_unset(self):
        _interrupt.clear()
        assert not _interrupt.is_set()

    def testsigint_handler_sets_interrupt(self):
        _interrupt.clear()
        sigint_handler(0, None)
        assert _interrupt.is_set()
        _interrupt.clear()

    def test_cleanup_children_empty(self):
        original = _child_procs[:]
        _child_procs.clear()
        cleanup_children()
        _child_procs.extend(original)

    def test_interrupt_wait_returns_quickly(self):
        _interrupt.clear()

        def setter():
            import time
            time.sleep(0.1)
            _interrupt.set()

        t = threading.Thread(target=setter, daemon=True)
        t.start()
        result = _interrupt.wait(timeout=5.0)
        assert result is True
        _interrupt.clear()


class TestParseStreamEvent:
    def test_text_delta(self):
        event = json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}})
        holder = [""]
        assert _parse_stream_event(event, holder) == "hello"

    def test_assistant_text_blocks(self):
        event = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "analysis"}]},
        })
        holder = [""]
        assert _parse_stream_event(event, holder) == "analysis"

    def test_content_block_start_tool_use(self):
        event = json.dumps({
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
        })
        holder = [""]
        assert _parse_stream_event(event, holder) == "-> Read"

    def test_content_block_start_tool_use_with_input(self):
        event = json.dumps({
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "id": "t1",
                "name": "Read",
                "input": {"file_path": "src/main.py"},
            },
        })
        holder = [""]
        assert _parse_stream_event(event, holder) == "-> Read src/main.py"

    def test_content_block_start_text_returns_none(self):
        event = json.dumps({
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""},
        })
        holder = [""]
        assert _parse_stream_event(event, holder) is None

    def test_assistant_tool_use_blocks(self):
        event = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"}},
            ]},
        })
        holder = [""]
        assert _parse_stream_event(event, holder) == "-> Grep TODO"

    def test_input_json_delta_returns_none(self):
        event = json.dumps({
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
        })
        holder = [""]
        assert _parse_stream_event(event, holder) is None

    def test_result_stores_structured_output(self):
        event = json.dumps({"type": "result", "structured_output": '{"score": 18}'})
        holder = [""]
        assert _parse_stream_event(event, holder) is None
        assert holder[0] == '{"score": 18}'

    def test_unknown_event_returns_none(self):
        event = json.dumps({"type": "system", "data": "something"})
        holder = [""]
        assert _parse_stream_event(event, holder) is None


class TestToolInputSummary:
    def test_file_path(self):
        assert _tool_input_summary({"file_path": "src/main.py"}) == "src/main.py"

    def test_pattern(self):
        assert _tool_input_summary({"pattern": "TODO.*fix"}) == "TODO.*fix"

    def test_command(self):
        assert _tool_input_summary({"command": "ls -la"}) == "ls -la"

    def test_empty_input(self):
        assert _tool_input_summary({}) == ""

    def test_fallback_to_first_string(self):
        assert _tool_input_summary({"custom_key": "value"}) == "value"

    def test_truncation(self):
        long_val = "x" * 100
        result = _tool_input_summary({"file_path": long_val})
        assert len(result) == 80
