"""Rich terminal display layer for the RPI pipeline."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from rich.console import RenderableType

DisplayStatus = Literal["success", "failed", "running", "skipped", "warning"]
ActivityStatus = Literal["success", "failed", "skipped"]

STATUS_ICONS: dict[DisplayStatus, str] = {
    "success": "✓",
    "failed": "✗",
    "running": "⟳",
    "skipped": "·",
    "warning": "!",
}

STATUS_STYLES: dict[DisplayStatus, str] = {
    "success": "green",
    "failed": "red",
    "running": "blue",
    "skipped": "dim",
    "warning": "yellow",
}


def _wrap_text(text: str, width: int) -> list[str]:
    """Simple word-wrap for long strings."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        elif current:
            current += " " + word
        else:
            current = word
    if current:
        lines.append(current)
    return lines


class Activity:
    """Base class for long-running operations rendered with a Live panel."""

    def __init__(
        self,
        label: str,
        log_path: Path,
        verbose: bool,
        _print: Callable[[str], None],
        _update_live: Callable[[RenderableType], None],
    ) -> None:
        self.label = label
        self._log_path = log_path
        self._verbose = verbose
        self._print = _print
        self._update_live = _update_live
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_path.open("w")
        self.start_time = time.monotonic()
        self._completed = False

    def complete(self, status: ActivityStatus, summary: str) -> None:
        """Mark the activity as completed with a status and summary line."""
        self._completed = True
        elapsed = time.monotonic() - self.start_time
        icon = STATUS_ICONS.get(status, "?")
        style = STATUS_STYLES.get(status, "")
        line = f"[{style}]{icon}[/{style}] {self.label} [{style}]{summary}[/{style}] [dim]({elapsed:.1f}s)[/dim]"
        self._print(line)
        self._write_log(f"\n--- {status}: {summary} ({elapsed:.1f}s) ---\n")
        self._log_file.flush()

    def _write_log(self, text: str) -> None:
        self._log_file.write(text)

    def _close_log(self) -> None:
        if self._log_file and not self._log_file.closed:
            self._log_file.close()


class StreamActivity(Activity):
    """Handle for a single-source long-running operation."""

    def __init__(
        self,
        label: str,
        log_path: Path,
        ring_max: int,
        verbose: bool,
        _print: Callable[[str], None],
        _update_live: Callable[[RenderableType], None],
    ) -> None:
        super().__init__(label, log_path, verbose, _print, _update_live)
        self._ring_buffer: list[str] = []
        self._ring_max = ring_max

    def stream_line(self, text: str) -> None:
        if self._completed:
            raise RuntimeError("Activity already completed")
        self._ring_buffer.append(text)
        if len(self._ring_buffer) > self._ring_max:
            self._ring_buffer = self._ring_buffer[-self._ring_max :]
        self._write_log(text + "\n")
        self._update_live(self._build_panel())
        if self._verbose:
            self._print(text)

    def _build_panel(self) -> Panel:
        elapsed = time.monotonic() - self.start_time
        body = "\n".join(self._ring_buffer) if self._ring_buffer else "[dim]waiting...[/dim]"
        return Panel(
            body,
            title=f"[bold]{self.label}[/bold] [dim]({elapsed:.1f}s)[/dim]",
            border_style="blue",
        )


class QuorumActivity(Activity):
    """Handle for a multi-reviewer operation."""

    def __init__(
        self,
        label: str,
        log_path: Path,
        reviewer_count: int,
        verbose: bool,
        _print: Callable[[str], None],
        _update_live: Callable[[RenderableType], None],
    ) -> None:
        super().__init__(label, log_path, verbose, _print, _update_live)
        self.reviewer_count = reviewer_count
        self._event_counts: list[int] = [0] * reviewer_count

    def stream_line(self, text: str, reviewer: int) -> None:
        if self._completed:
            raise RuntimeError("Activity already completed")
        self._event_counts[reviewer] += 1
        self._write_log(f"[reviewer {reviewer}] {text}\n")
        self._update_live(self._build_panel())
        if self._verbose:
            self._print(f"[dim]\\[reviewer {reviewer}][/dim] {text}")

    def _build_panel(self) -> Panel:
        elapsed = time.monotonic() - self.start_time
        lines = []
        for i, count in enumerate(self._event_counts):
            lines.append(f"  Reviewer {i + 1}: {count} events")
        body = "\n".join(lines)
        return Panel(
            body,
            title=f"[bold]{self.label}[/bold] [dim]({elapsed:.1f}s)[/dim]",
            border_style="blue",
        )


class Display:
    """Centralized terminal output using rich."""

    def __init__(self, verbose: bool = False, *, log_dir: Path, width: int | None = None) -> None:
        self._console = Console(stderr=True)
        self._stdout = Console()
        self._verbose = verbose
        self._log_dir = log_dir
        self._width = width or min(72, shutil.get_terminal_size().columns)
        self._lock = threading.Lock()
        self._active: Activity | None = None
        self._live: Live | None = None
        log_dir.mkdir(parents=True, exist_ok=True)


    def _print(self, msg: str) -> None:
        with self._lock:
            if self._live is not None:
                self._live.console.print(msg)
            else:
                self._console.print(msg)

    def _update_live(self, renderable: RenderableType) -> None:
        with self._lock:
            if self._live is not None:
                self._live.update(renderable)
                self._live.refresh()

    def _start_live(self) -> None:
        self._live = Live(console=self._console, auto_refresh=False)
        self._live.start()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def info(self, msg: str) -> None:
        self._print(f"  {msg}")

    def success(self, msg: str) -> None:
        self._print(f"  [green]{msg}[/green]")

    def warn(self, msg: str) -> None:
        self._print(f"  [yellow]{msg}[/yellow]")

    def error(self, msg: str) -> None:
        self._print(f"  [red]{msg}[/red]")

    def detail(self, msg: str) -> None:
        if self._verbose:
            self._print(f"  [dim]{msg}[/dim]")

    def truncate(self, text: str, max_width: int | None = None) -> str:
        """Truncate text with ellipsis at terminal width."""
        w = max_width or self._width
        if len(text) <= w:
            return text
        return text[: w - 1] + "\u2026"

    def banner(self, title: str, fields: list[tuple[str, str]]) -> None:
        """Print startup banner as a rich Panel."""
        lines: list[str] = []
        for label, value in fields:
            lines.append(f"[cyan]{label + ':':<12}[/cyan] {value}")
        body = "\n".join(lines)
        panel = Panel(body, title=f"[bold]{title}[/bold]", border_style="blue", width=min(80, self._width))
        self._console.print(panel)

    def stage_header(self, text: str) -> None:
        """Print a stage transition header."""
        self._console.print()
        self._console.rule(f"[bold]{text}[/bold]")

    def summary_table(
        self,
        title: str,
        rows: list[tuple[str, str, str]],
        footer: dict[str, str] | None = None,
        total_elapsed: float | None = None,
    ) -> None:
        """Render final summary as a rich Table in a Panel."""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Stage", style="cyan", min_width=14)
        table.add_column("Status")
        for label, icon, detail in rows:
            table.add_row(label, f"{icon}  {detail}")
        effective_footer = dict(footer) if footer else {}
        if total_elapsed is not None:
            minutes, seconds = divmod(total_elapsed, 60)
            effective_footer["Elapsed"] = f"{int(minutes)}m {seconds:.1f}s"
        if effective_footer:
            table.add_row("", "")
            for k, v in effective_footer.items():
                table.add_row(k, v)
        panel = Panel(table, title=f"[bold]{title}[/bold]", border_style="dim", width=min(80, self._width))
        self._stdout.print(panel)

    @contextmanager
    def activity(self, label: str, log_name: str, ring_max: int = 8) -> Iterator[StreamActivity]:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("Another activity is already active")
            act = StreamActivity(
                label=label,
                log_path=self._log_dir / f"{log_name}.log",
                ring_max=ring_max,
                verbose=self._verbose,
                _print=self._print,
                _update_live=self._update_live,
            )
            self._active = act
        self._start_live()
        try:
            yield act
        finally:
            try:
                if not act._completed:
                    act.complete("failed", f"{label} — interrupted")
            finally:
                act._close_log()
                self._stop_live()
                with self._lock:
                    self._active = None

    @contextmanager
    def quorum_activity(self, label: str, log_name: str, reviewer_count: int) -> Iterator[QuorumActivity]:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("Another activity is already active")
            act = QuorumActivity(
                label=label,
                log_path=self._log_dir / f"{log_name}.log",
                reviewer_count=reviewer_count,
                verbose=self._verbose,
                _print=self._print,
                _update_live=self._update_live,
            )
            self._active = act
        self._start_live()
        try:
            yield act
        finally:
            try:
                if not act._completed:
                    act.complete("failed", f"{label} — interrupted")
            finally:
                act._close_log()
                self._stop_live()
                with self._lock:
                    self._active = None

    def confirm(self, prompt: str) -> bool:
        """Ask a yes/no question. Raises if an activity is active."""
        if self._active is not None:
            raise RuntimeError("Cannot confirm while an activity is active")
        self._console.print(f"  {prompt} [dim](y/n)[/dim] ", end="")
        answer = input().strip().lower()
        return answer in ("y", "yes")

    def collect_feedback(self, stage_name: str, *, is_initial_input: bool = False) -> str | None:
        """Collect multiline feedback from the user.

        Returns stripped text or None if empty. EOF (Ctrl-D) is treated as empty.
        """
        if self._active is not None:
            raise RuntimeError("Cannot collect feedback while an activity is active")
        from prompt_toolkit import prompt as pt_prompt

        action = "provide input" if is_initial_input else "provide feedback"
        self._console.print(f"\n  [bold]{stage_name}:[/bold] {action} (press [dim]Esc+Enter[/dim] to submit, [dim]Ctrl-D[/dim] to skip)")
        try:
            text = pt_prompt("  > ", multiline=True)
        except EOFError:
            return None
        stripped = text.strip()
        return stripped if stripped else None

