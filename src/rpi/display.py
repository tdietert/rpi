"""Rich terminal display layer for the RPI pipeline."""

from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

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

def green(text: str) -> str:
    """Wrap text in green Rich markup, auto-escaping special characters."""
    return f"[green]{rich_escape(str(text))}[/green]"


def red(text: str) -> str:
    """Wrap text in red Rich markup, auto-escaping special characters."""
    return f"[red]{rich_escape(str(text))}[/red]"


def dim(text: str) -> str:
    """Wrap text in dim Rich markup, auto-escaping special characters."""
    return f"[dim]{rich_escape(str(text))}[/dim]"


def bold(text: str) -> str:
    """Wrap text in bold Rich markup, auto-escaping special characters."""
    return f"[bold]{rich_escape(str(text))}[/bold]"


def yellow(text: str) -> str:
    """Wrap text in yellow Rich markup, auto-escaping special characters."""
    return f"[yellow]{rich_escape(str(text))}[/yellow]"


def cyan(text: str) -> str:
    """Wrap text in cyan Rich markup, auto-escaping special characters."""
    return f"[cyan]{rich_escape(str(text))}[/cyan]"


def italic(text: str) -> str:
    """Wrap text in italic Rich markup, auto-escaping special characters."""
    return f"[italic]{rich_escape(str(text))}[/italic]"


def filelink(path: str | Path) -> str:
    """Wrap a file path in a clickable file:// hyperlink (OSC 8)."""
    s = str(path)
    return f"[link=file://{s}]{rich_escape(s)}[/link]"


_STYLE_FN: dict[str, Callable[[str], str]] = {
    "green": green,
    "red": red,
    "blue": lambda t: f"[blue]{rich_escape(str(t))}[/blue]",
    "dim": dim,
    "yellow": yellow,
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
        self._completion_line: str | None = None
        self._refresh_stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    def _start_refresh_thread(self) -> None:
        """Spawn a daemon thread that rebuilds the panel every 0.5s."""

        def _loop() -> None:
            while not self._refresh_stop.wait(0.5):
                if self._completed:
                    break
                self._update_live(self._build_panel())  # type: ignore[attr-defined]

        self._refresh_thread = threading.Thread(target=_loop, daemon=True)
        self._refresh_thread.start()

    def _stop_refresh_thread(self) -> None:
        """Signal the refresh thread to stop and wait for it."""
        self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=2)
            self._refresh_thread = None

    def complete(self, status: ActivityStatus, summary: str) -> None:
        """Mark the activity as completed with a status and summary line.

        The completion line is deferred — stored in ``_completion_line`` and
        printed by the owning context manager **after** the Live panel is
        stopped.  Printing while Live is active causes Rich to hide/re-show
        the panel, which produces a visible blink of raw markup tags.
        """
        self._completed = True
        elapsed = time.monotonic() - self.start_time
        icon = STATUS_ICONS.get(status, "?")
        style = STATUS_STYLES.get(status, "")
        _style = _STYLE_FN[style]
        self._completion_line = (
            f"{_style(icon)} {self.label} {_style(summary)} {dim(f'({elapsed:.1f}s)')}"
        )
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
        self._ring_max = ring_max
        self._ring_buffer: deque[Text] = deque(maxlen=ring_max)
        self._event_count = 0
        self._last_event_time = time.monotonic()

    def stream_line(self, text: Text | str) -> None:
        if self._completed:
            raise RuntimeError("Activity already completed")
        self._event_count += 1
        self._last_event_time = time.monotonic()
        text_obj = text if isinstance(text, Text) else Text(str(text))
        self._ring_buffer.append(text_obj)
        self._write_log(text_obj.plain + "\n")
        self._update_live(self._build_panel())

    def _pad_body(self, lines: deque[Text], height: int) -> Text:
        visible = list(lines)[:height]
        padding_count = max(0, height - len(visible))
        if padding_count:
            visible.extend([Text("")] * padding_count)
        return Text("\n").join(visible)

    def _build_panel(self) -> Panel:
        elapsed = time.monotonic() - self.start_time
        spinner = SPINNER_FRAMES[int(elapsed * 2) % len(SPINNER_FRAMES)]

        terminal_height = shutil.get_terminal_size().lines
        chrome_lines = 4
        effective = min(self._ring_max, max(3, terminal_height - chrome_lines - 2))

        body = self._pad_body(self._ring_buffer, effective) if self._ring_buffer else Text("waiting...", style="dim")
        idle = time.monotonic() - self._last_event_time
        idle_suffix = f", idle {idle:.0f}s" if idle > 10 else ""
        inner = Panel(
            body,
            title=f"Agent ({self._event_count} events{idle_suffix})",
            height=effective + 2,
            border_style="yellow" if idle > 30 else "dim",
        )
        return Panel(
            inner,
            title=f"{spinner} {bold(self.label)} {dim(f'({elapsed:.1f}s)')}",
            border_style="blue",
        )


class QuorumActivity(Activity):
    """Handle for a multi-reviewer operation."""

    def __init__(
        self,
        label: str,
        log_path: Path,
        reviewer_count: int,
        ring_max: int,
        verbose: bool,
        _print: Callable[[str], None],
        _update_live: Callable[[RenderableType], None],
    ) -> None:
        super().__init__(label, log_path, verbose, _print, _update_live)
        self.reviewer_count = reviewer_count
        self._ring_max = ring_max
        self._ring_buffers: list[deque[Text]] = [deque(maxlen=ring_max) for _ in range(reviewer_count)]
        self._event_counts: list[int] = [0] * reviewer_count

    def stream_line(self, text: Text | str, reviewer: int) -> None:
        if self._completed:
            raise RuntimeError("Activity already completed")
        self._event_counts[reviewer] += 1
        text_obj = text if isinstance(text, Text) else Text(str(text))
        self._ring_buffers[reviewer].append(text_obj)
        self._write_log(f"[reviewer {reviewer}] {text_obj.plain}\n")
        self._update_live(self._build_panel())

    def _pad_body(self, lines: list[Text] | deque[Text], height: int) -> Text:
        visible = list(lines)[:height]
        padding_count = max(0, height - len(visible))
        if padding_count:
            visible.extend([Text("")] * padding_count)
        return Text("\n").join(visible)

    def _build_panel(self) -> Panel:
        elapsed = time.monotonic() - self.start_time
        spinner = SPINNER_FRAMES[int(elapsed * 2) % len(SPINNER_FRAMES)]

        terminal_height = shutil.get_terminal_size().lines
        chrome_lines = 2
        panel_chrome = 2
        effective = min(
            self._ring_max,
            max(3, (terminal_height - chrome_lines) // self.reviewer_count - panel_chrome),
        )

        panels = []
        for i in range(self.reviewer_count):
            body = self._pad_body(self._ring_buffers[i], effective)
            panels.append(
                Panel(
                    body,
                    title=f"Reviewer {i + 1} ({self._event_counts[i]} events)",
                    height=effective + 2,
                    border_style="dim",
                )
            )

        return Panel(
            Group(*panels),
            title=f"{spinner} {bold(self.label)} {dim(f'({elapsed:.1f}s)')}",
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
        with self._lock:
            live = self._live
            self._live = None
        if live is not None:
            live.stop()

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
            padded = f"{label + ':':<12}"
            lines.append(f"{cyan(padded)} {value}")
        body = "\n".join(lines)
        panel = Panel(body, title=bold(title), border_style="blue", width=min(80, self._width))
        self._console.print(panel)

    def stage_header(self, text: str) -> None:
        """Print a stage transition header."""
        self._console.print()
        self._console.rule(bold(text))

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
        panel = Panel(table, title=bold(title), border_style="dim", width=min(80, self._width))
        self._stdout.print(panel)

    @contextmanager
    def activity(self, label: str, log_name: str, ring_max: int = 30) -> Iterator[StreamActivity]:
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
        act._start_refresh_thread()
        try:
            yield act
        finally:
            try:
                if not act._completed:
                    act.complete("failed", f"{label} — interrupted")
            finally:
                act._stop_refresh_thread()
                act._close_log()
                self._stop_live()
                if act._completion_line:
                    self._print(act._completion_line)
                with self._lock:
                    self._active = None

    @contextmanager
    def quorum_activity(self, label: str, log_name: str, reviewer_count: int, ring_max: int = 15) -> Iterator[QuorumActivity]:
        with self._lock:
            if self._active is not None:
                raise RuntimeError("Another activity is already active")
            act = QuorumActivity(
                label=label,
                log_path=self._log_dir / f"{log_name}.log",
                reviewer_count=reviewer_count,
                ring_max=ring_max,
                verbose=self._verbose,
                _print=self._print,
                _update_live=self._update_live,
            )
            self._active = act
        self._start_live()
        act._start_refresh_thread()
        try:
            yield act
        finally:
            try:
                if not act._completed:
                    act.complete("failed", f"{label} — interrupted")
            finally:
                act._stop_refresh_thread()
                act._close_log()
                self._stop_live()
                if act._completion_line:
                    self._print(act._completion_line)
                with self._lock:
                    self._active = None

    def confirm(self, prompt: str) -> bool:
        """Ask a yes/no question. Raises if an activity is active."""
        if self._active is not None:
            raise RuntimeError("Cannot confirm while an activity is active")
        import sys
        import termios
        # Restore terminal to sane cooked mode in case Rich Live left it dirty.
        try:
            fd = sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, termios.tcgetattr(fd))
            # Force canonical mode + echo so input() works normally.
            attrs = termios.tcgetattr(fd)
            attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except (termios.error, OSError, ValueError):
            pass
        self._console.print(f"  {prompt} {dim('(y/n)')} ", end="")
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
        self._console.print(f"\n  {bold(stage_name + ':')} {action} (press {dim('Esc+Enter')} to submit, {dim('Ctrl-D')} to skip)")
        try:
            text = pt_prompt("  > ", multiline=True)
        except EOFError:
            return None
        stripped = text.strip()
        return stripped if stripped else None
