"""Claude CLI subprocess wrappers and signal handling."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .snapshot import SnapshotStageProgress
from .types import Config, Effort

T = TypeVar("T", bound=BaseModel)


_child_procs: list[subprocess.Popen] = []
_interrupt = threading.Event()


@dataclass
class _RunState:
    """Mutable container for interrupt-safe snapshot."""
    snap_dir: Path | None = None
    config: Config | None = None
    progress: SnapshotStageProgress | None = None
    plan: object = None  # Plan, kept as object to avoid circular import
    work_dir: Path | None = None
    display: object = None  # Display, kept as object to avoid circular import


_run_state = _RunState()


def _sigint_handler(signum: int, frame: object) -> None:
    """SIGINT handler: set interrupt event and clean up children.

    Does NOT raise KeyboardInterrupt — the drain-based unwinding model
    relies on _drain_queue checking interrupt.is_set() and breaking out,
    allowing activity finally blocks to run in an orderly fashion.
    """
    _interrupt.set()
    cleanup_children()


def cleanup_children() -> None:
    """Kill all tracked child processes."""
    for proc in _child_procs:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass







_SENTINEL = object()


def _drain_queue(
    q: queue.Queue,
    interrupt: threading.Event,
    sentinel_count: int = 1,
) -> Iterator:
    """Yield items from *q* until *sentinel_count* sentinels have been received.

    Checks *interrupt* between blocking gets so the caller can break out
    when SIGINT is received.
    """
    seen_sentinels = 0
    while seen_sentinels < sentinel_count:
        if interrupt.is_set():
            break
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            continue
        if item is _SENTINEL:
            seen_sentinels += 1
            continue
        yield item


def _parse_stream_event(raw_line: str, result_holder: list[str]) -> str | None:
    """Parse a stream-json line and return displayable text, or None.

    For ``result`` events the structured_output (or result) text is stored
    in *result_holder[0]* so the reader thread can pass it back.
    """
    raw_line = raw_line.strip()
    if not raw_line:
        return None
    try:
        event = json.loads(raw_line)
    except json.JSONDecodeError:
        return None

    etype = event.get("type")

    if etype == "result":
        so = event.get("structured_output", "")
        if isinstance(so, dict):
            result_holder[0] = json.dumps(so)
        elif isinstance(so, str) and so:
            result_holder[0] = so
        if not result_holder[0]:
            rt = event.get("result", "")
            result_holder[0] = rt if isinstance(rt, str) else json.dumps(rt)
        return None

    if etype == "assistant":
        msg = event.get("message", {})
        parts = []
        for block in msg.get("content", []):
            if block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts) if parts else None
    if etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text")
    return None


def _reader(
    proc: subprocess.Popen,
    q: queue.Queue,
    result_holder: list[str],
    reviewer: int | None = None,
) -> None:
    """Background thread: read *proc.stdout*, push lines to *q*, push sentinel on exit."""
    try:
        for raw_line in proc.stdout:
            text = _parse_stream_event(raw_line, result_holder)
            if text:
                for line in text.split("\n"):
                    stripped = line.rstrip()
                    if stripped:
                        if reviewer is not None:
                            q.put((reviewer, stripped))
                        else:
                            q.put(stripped)
        proc.wait()
    finally:
        q.put(_SENTINEL)


class ClaudeProcess:
    """Handle for a running Claude CLI subprocess."""

    def __init__(
        self,
        proc: subprocess.Popen,
        q: queue.Queue,
        reader_thread: threading.Thread,
        interrupt: threading.Event,
        result_holder: list[str],
    ) -> None:
        self._proc = proc
        self._queue = q
        self._reader_thread = reader_thread
        self._interrupt = interrupt
        self._result_holder = result_holder
        self._exhausted = False

    def lines(self) -> Iterator[str]:
        """Iterate over text lines produced by the subprocess."""
        try:
            yield from _drain_queue(self._queue, self._interrupt, sentinel_count=1)
        finally:
            self._exhausted = True
        if self._interrupt.is_set():
            raise KeyboardInterrupt

    def result(self) -> str:
        """Return the structured result text.

        Raises ``RuntimeError`` if ``lines()`` has not been fully consumed.
        """
        if not self._exhausted:
            raise RuntimeError("lines() must be exhausted before calling result()")
        return self._result_holder[0]


def start_claude(
    prompt: str,
    effort: Effort = "medium",
    work_dir: Path | None = None,
    json_schema_str: str | None = None,
    worktree: str = "",
    model: str | None = None,
    interrupt: threading.Event | None = None,
) -> ClaudeProcess:
    """Spawn a Claude CLI subprocess and return a handle immediately."""
    if interrupt is None:
        interrupt = _interrupt

    if work_dir:
        prompt += (
            f"\n\nYou have a shared workspace directory at {work_dir} for writing "
            "intermediate resources (notes, context files, analysis) that later "
            "agents in this pipeline can read. Write files there when you produce "
            "artifacts useful for downstream steps. Read existing files there for "
            "context from prior steps."
        )

    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--effort",
        effort,
        "--dangerously-skip-permissions",
        "--settings",
        json.dumps({"outputStyle": ""}),
    ]
    if json_schema_str is not None:
        cmd.extend(["--json-schema", json_schema_str])
    if model is not None:
        cmd.extend(["--model", model])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=worktree or None,
    )
    _child_procs.append(proc)

    result_holder: list[str] = [""]
    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=_reader, args=(proc, q, result_holder), daemon=True)
    t.start()

    return ClaudeProcess(proc, q, t, interrupt, result_holder)


def _parse_structured(schema: type[T], raw: str) -> T:
    """Parse a string into a Pydantic model, handling both JSON strings and dicts."""
    # Try JSON string first (the normal case from structured_output)
    try:
        return schema.model_validate_json(raw)
    except Exception:
        pass
    # Maybe it's a Python dict repr or was double-encoded; try parsing then validating
    try:
        data = json.loads(raw)
        return schema.model_validate(data)
    except Exception:
        pass
    raise ValueError(f"Cannot parse into {schema.__name__}: {raw[:200]}")


def run_claude_structured(
    prompt: str,
    schema: type[T],
    effort: Effort = "medium",
    work_dir: Path | None = None,
    dry_run: bool = False,
    worktree: str = "",
    model: str | None = None,
    dry_run_default: T | None = None,
) -> T:
    """Run claude -p with --json-schema and return a validated Pydantic model.

    Each call site can provide a dry_run_default for plausible dry-run output.
    If none is provided, falls back to schema.model_validate({}).
    """
    if dry_run:
        if dry_run_default is not None:
            return dry_run_default
        return schema.model_validate({})
    schema_str = json.dumps(schema.model_json_schema())
    handle = start_claude(prompt, effort=effort, work_dir=work_dir, json_schema_str=schema_str, worktree=worktree, model=model)
    for _line in handle.lines():
        pass
    raw = handle.result()
    return _parse_structured(schema, raw)


