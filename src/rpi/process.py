"""Claude CLI subprocess wrappers and signal handling."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .display import Display, StreamActivity
from .types import Effort

T = TypeVar("T", bound=BaseModel)


_child_procs: list[subprocess.Popen] = []
_interrupt = threading.Event()


def sigint_handler(signum: int, frame: object) -> None:
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


def _tool_input_summary(inp: dict) -> str:
    """Extract a brief human-readable summary from a tool input dict."""
    for key in ("file_path", "pattern", "command", "path", "query"):
        val = inp.get(key)
        if isinstance(val, str) and val:
            return val[:80]
    # Fallback: first string value
    for val in inp.values():
        if isinstance(val, str) and val:
            return val[:80]
    return ""


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

    if etype == "content_block_start":
        cb = event.get("content_block", {})
        if cb.get("type") == "tool_use":
            name = cb.get("name", "tool")
            inp = cb.get("input", {})
            summary = _tool_input_summary(inp)
            return f"-> {name} {summary}" if summary else f"-> {name}"
        return None

    if etype == "assistant":
        msg = event.get("message", {})
        parts = []
        for block in msg.get("content", []):
            if block.get("type") == "text":
                parts.append(block["text"])
            elif block.get("type") == "tool_use":
                name = block.get("name", "tool")
                inp = block.get("input", {})
                summary = _tool_input_summary(inp)
                parts.append(f"-> {name} {summary}" if summary else f"-> {name}")
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
    activity: StreamActivity | None = None,
) -> T:
    """Run claude -p with --json-schema and return a validated Pydantic model.

    Each call site can provide a dry_run_default for plausible dry-run output.
    If none is provided, falls back to schema.model_validate({}).

    When *activity* is provided, each streaming line is forwarded to it so the
    caller can render live progress.
    """
    if dry_run:
        if dry_run_default is not None:
            return dry_run_default
        return schema.model_validate({})
    schema_str = json.dumps(schema.model_json_schema())
    handle = start_claude(prompt, effort=effort, work_dir=work_dir, json_schema_str=schema_str, worktree=worktree, model=model)
    for line in handle.lines():
        if activity is not None:
            activity.stream_line(line)
    raw = handle.result()
    return _parse_structured(schema, raw)


def run_claude_with_display(
    prompt: str,
    schema: type[T],
    *,
    display: Display | None = None,
    label: str = "",
    log_name: str = "",
    complete_summary: Callable[[T], str] = lambda r: "",
    **kwargs,
) -> T:
    """Wrap run_claude_structured with optional display activity.

    When *display* and *label* are provided, wraps the call in a display
    activity with streaming. Otherwise calls run_claude_structured directly.
    Extra **kwargs are forwarded (effort, work_dir, dry_run, worktree, model, dry_run_default).
    """
    if display is not None and label:
        with display.activity(label, log_name or label.lower().replace(" ", "-")) as act:
            result = run_claude_structured(prompt=prompt, schema=schema, activity=act, **kwargs)
            act.complete("success", complete_summary(result))
        return result
    return run_claude_structured(prompt=prompt, schema=schema, **kwargs)


class QuorumProcess:
    """Handle for N parallel Claude CLI subprocesses (review quorum)."""

    def __init__(
        self,
        procs: list[subprocess.Popen],
        q: queue.Queue,
        reader_threads: list[threading.Thread],
        interrupt: threading.Event,
        result_holders: list[list[str]],
    ) -> None:
        self._procs = procs
        self._queue = q
        self._reader_threads = reader_threads
        self._interrupt = interrupt
        self._result_holders = result_holders
        self._exhausted = False

    def tagged_lines(self) -> Iterator[tuple[int, str]]:
        """Iterate over ``(reviewer_index, line)`` tuples from all reviewers."""
        try:
            yield from _drain_queue(
                self._queue, self._interrupt, sentinel_count=len(self._procs)
            )
        finally:
            self._exhausted = True
        if self._interrupt.is_set():
            raise KeyboardInterrupt

    def results(self) -> list[str]:
        """Return per-reviewer structured result strings.

        Raises ``RuntimeError`` if ``tagged_lines()`` has not been fully consumed.
        """
        if not self._exhausted:
            raise RuntimeError(
                "tagged_lines() must be exhausted before calling results()"
            )
        return [h[0] for h in self._result_holders]


def start_quorum(
    prompt: str,
    quorum_size: int,
    *,
    effort: Effort = "medium",
    work_dir: Path | None = None,
    json_schema_str: str | None = None,
    worktree: str = "",
    model: str | None = None,
    interrupt: threading.Event | None = None,
) -> QuorumProcess:
    """Launch *quorum_size* parallel Claude CLI subprocesses and return a handle."""
    if interrupt is None:
        interrupt = _interrupt

    full_prompt = prompt
    if work_dir:
        full_prompt += (
            f"\n\nYou have a shared workspace directory at {work_dir} for writing "
            "intermediate resources (notes, context files, analysis) that later "
            "agents in this pipeline can read."
        )

    cmd = [
        "claude",
        "-p",
        full_prompt,
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

    procs: list[subprocess.Popen] = []
    result_holders: list[list[str]] = []
    threads: list[threading.Thread] = []
    q: queue.Queue = queue.Queue()

    try:
        for i in range(quorum_size):
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                cwd=worktree or None,
            )
            _child_procs.append(proc)
            procs.append(proc)

            rh: list[str] = [""]
            result_holders.append(rh)
            t = threading.Thread(
                target=_reader, args=(proc, q, rh, i), daemon=True
            )
            t.start()
            threads.append(t)
    except Exception:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
        raise

    return QuorumProcess(procs, q, threads, interrupt, result_holders)
