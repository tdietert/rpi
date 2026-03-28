"""Claude CLI subprocess wrappers and signal handling."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .display import display
from .types import Config
from .snapshot import SnapshotStageProgress

T = TypeVar("T", bound=BaseModel)

# -- Global state for child process tracking and interrupt handling -----------

_child_procs: list[subprocess.Popen] = []
_interrupt = threading.Event()


@dataclass
class _RunState:
    """Mutable container for interrupt-safe snapshot."""
    snap_dir: Path | None = None
    config: Config | None = None
    progress: SnapshotStageProgress | None = None
    parsed_plan: object = None  # ParsedPlan, kept as object to avoid circular import
    work_dir: Path | None = None


_run_state = _RunState()


def _sigint_handler(signum: int, frame: object) -> None:
    """SIGINT handler: set interrupt event, clean up children, raise KeyboardInterrupt."""
    _interrupt.set()
    cleanup_children()
    raise KeyboardInterrupt


def cleanup_children() -> None:
    """Kill all tracked child processes."""
    for proc in _child_procs:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


# -- Event parsing -----------------------------------------------------------


def _extract_event_text(event: dict) -> str | None:
    """Extract displayable text from a stream-json event."""
    if event.get("type") == "assistant":
        msg = event.get("message", {})
        parts = []
        for block in msg.get("content", []):
            if block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts) if parts else None
    if event.get("type") == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text")
    return None


def _progress_line(
    event_counts: list[int],
    start_time: float,
) -> str:
    """Build a single-line progress string (no newline)."""
    elapsed = int(time.time() - start_time)
    parts = [f"R{i + 1}:{c}ev" for i, c in enumerate(event_counts)]
    return f"  Reviewing... [{' '.join(parts)}] {elapsed}s"


# -- Claude CLI subprocess wrapper -------------------------------------------


def _run_claude_json(
    prompt: str,
    effort: str = "medium",
    work_dir: Path | None = None,
    dry_run: bool = False,
    json_schema_str: str | None = None,
    worktree: str = "",
    model: str | None = None,
) -> str:
    """Run claude -p with --output-format json and return the result text.

    Uses --output-format json (not stream-json) so that --json-schema works
    correctly without --verbose.  Reads stdout in a background thread so
    the main thread stays responsive to signals (Ctrl+C).
    """
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
        "json",
        "--effort",
        effort,
        "--dangerously-skip-permissions",
        # Clear any user/project output style (e.g. Explanatory) that would
        # inject non-JSON formatting into structured responses.
        "--settings",
        json.dumps({"outputStyle": ""}),
    ]
    if json_schema_str is not None:
        cmd.extend(["--json-schema", json_schema_str])
    if model is not None:
        cmd.extend(["--model", model])

    if dry_run:
        preview = prompt[:80].replace("\n", " ")
        wt_note = f" [wt:{worktree}]" if worktree else ""
        display.info(f"[dim]\\[DRY RUN]{wt_note} claude -p '{preview}...'[/dim]")
        if json_schema_str is not None:
            return "{}"
        return "(dry run -- no output)"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=worktree or None,
    )
    _child_procs.append(proc)

    stdout_chunks: list[str] = []

    def reader() -> None:
        for line in proc.stdout:
            stdout_chunks.append(line)
        proc.wait()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    start = time.time()
    while t.is_alive() and not _interrupt.is_set():
        elapsed = int(time.time() - start)
        sys.stderr.write(f"\r  Running... {elapsed}s")
        sys.stderr.flush()
        _interrupt.wait(timeout=1.0)
        if _interrupt.is_set():
            break
    # Clear the progress line
    sys.stderr.write("\r\033[2K")
    sys.stderr.flush()

    if proc in _child_procs:
        _child_procs.remove(proc)

    if proc.returncode != 0:
        stderr_text = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(
            f"Claude CLI failed (exit {proc.returncode}):\n{stderr_text}"
        )

    raw = "".join(stdout_chunks).strip()
    # --output-format json emits a JSON envelope. When --json-schema is used,
    # the schema-constrained output is in "structured_output", not "result".
    # "result" contains the regular text response (e.g. markdown).
    try:
        envelope = json.loads(raw)
        if isinstance(envelope, dict):
            if json_schema_str and "structured_output" in envelope:
                so = envelope["structured_output"]
                return so if isinstance(so, str) else json.dumps(so)
            if "result" in envelope:
                return envelope["result"]
    except json.JSONDecodeError:
        pass
    return raw


def _run_claude_streaming(
    prompt: str,
    effort: str = "medium",
    work_dir: Path | None = None,
    json_schema_str: str | None = None,
    worktree: str = "",
    model: str | None = None,
) -> str:
    """Run claude with stream-json, displaying text to stderr as it arrives.

    Returns the same string as _run_claude_json: structured_output if a
    json_schema was provided and the result contains it, otherwise the
    result text.
    """
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

    buffer: list[str] = []
    result_output: list[str] = [""]  # mutable container for thread assignment

    def reader() -> None:
        for raw_line in proc.stdout:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                # Extract structured_output or result, matching _run_claude_json logic
                if json_schema_str:
                    so = event.get("structured_output", "")
                    if isinstance(so, dict):
                        result_output[0] = json.dumps(so)
                    elif isinstance(so, str) and so:
                        result_output[0] = so
                if not result_output[0]:
                    rt = event.get("result", "")
                    result_output[0] = rt if isinstance(rt, str) else json.dumps(rt)
                break
            text = _extract_event_text(event)
            if text:
                for line in text.split("\n"):
                    stripped = line.rstrip()
                    if stripped:
                        buffer.append(stripped)
        proc.wait()

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    start = time.time()
    printed = 0
    while t.is_alive() and not _interrupt.is_set():
        current_len = len(buffer)
        if current_len > printed:
            sys.stderr.write("\r\033[2K")
            for line in buffer[printed:current_len]:
                sys.stderr.write(f"  {display.truncate(line, display.width - 4)}\n")
            printed = current_len
            sys.stderr.flush()
        elapsed = int(time.time() - start)
        sys.stderr.write(f"\r  Running... {elapsed}s")
        sys.stderr.flush()
        _interrupt.wait(timeout=1.0)
        if _interrupt.is_set():
            break

    # Print remaining buffered lines
    if len(buffer) > printed:
        sys.stderr.write("\r\033[2K")
        for line in buffer[printed:]:
            sys.stderr.write(f"  {display.truncate(line, display.width - 4)}\n")
    sys.stderr.write("\r\033[2K")
    sys.stderr.flush()

    if proc in _child_procs:
        _child_procs.remove(proc)

    if proc.returncode != 0:
        stderr_text = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(
            f"Claude CLI failed (exit {proc.returncode}):\n{stderr_text}"
        )

    return result_output[0]


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
    effort: str = "medium",
    work_dir: Path | None = None,
    dry_run: bool = False,
    streaming: bool = True,
    worktree: str = "",
    model: str | None = None,
    dry_run_default: T | None = None,
) -> T:
    """Run claude -p with --json-schema and return a validated Pydantic model.

    Each call site can provide a dry_run_default for plausible dry-run output.
    If none is provided, falls back to schema.model_validate({}).
    """
    schema_str = json.dumps(schema.model_json_schema())
    if streaming and not dry_run:
        raw = _run_claude_streaming(prompt, effort, work_dir, schema_str, worktree, model=model)
    else:
        raw = _run_claude_json(prompt, effort, work_dir, dry_run, schema_str, worktree, model=model)
    if dry_run:
        if dry_run_default is not None:
            return dry_run_default
        return schema.model_validate({})
    return _parse_structured(schema, raw)


def confirm(prompt: str) -> bool:
    """Prompt the user for y/n confirmation, re-asking on invalid input."""
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please enter y or n.")
