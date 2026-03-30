"""Tests for process handle primitives (_drain_queue, ClaudeProcess, QuorumProcess)."""

from __future__ import annotations

import queue
import threading
from unittest.mock import MagicMock

import pytest

from rpi.process import (
    _SENTINEL,
    ClaudeProcess,
    _child_procs,
    _drain_queue,
    _interrupt,
    _reader,
    _sigint_handler,
    cleanup_children,
)
from rpi.review import QuorumProcess


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

    def test_sigint_handler_sets_interrupt(self):
        _interrupt.clear()
        _sigint_handler(0, None)
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
