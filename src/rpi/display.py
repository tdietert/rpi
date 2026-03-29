"""Rich terminal display layer for the RPI pipeline."""

from __future__ import annotations

import shutil

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# Pipeline stages for the stage tracker bar
_STAGES = [
    ("research", "Research"),
    ("plan_draft", "Plan Draft"),
    ("preflight", "Pre-flight"),
    ("plan_review", "Plan Review"),
    ("implement", "Implement"),
    ("review_fix", "Review-Fix"),
    ("commit", "Commit"),
    ("push_pr", "Push/PR"),
]


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


class Display:
    """Centralized terminal output using rich."""

    def __init__(self) -> None:
        self.console = Console(stderr=True)  # progress/spinners to stderr
        self.stdout = Console()  # results/summary to stdout
        self.width = shutil.get_terminal_size().columns

    def truncate(self, text: str, max_width: int | None = None) -> str:
        """Truncate text with ellipsis at terminal width."""
        w = max_width or self.width
        if len(text) <= w:
            return text
        return text[: w - 1] + "\u2026"

    @staticmethod
    def _status_style(value: str) -> str:
        """Return rich style string for a status value."""
        v = value.lower().replace(" ", "").replace("_", "")
        if v in ("success", "ready", "clean", "passed", "none", "nothingtoccommit"):
            return "green"
        if v in ("failed", "needsmajorrevision"):
            return "red"
        if v in ("needsrevision", "issuesremaining"):
            return "yellow"
        return ""

    @staticmethod
    def _model_summary_rich(model: BaseModel, index: int = 0) -> str:
        """Return a one-line summary for a nested model."""
        if hasattr(model, "number") and hasattr(model, "name") and hasattr(model, "tasks"):
            n_tasks = len(model.tasks) if hasattr(model, "tasks") else 0
            return f"[bold]Phase {model.number}: {model.name}[/bold] ({n_tasks} tasks)"
        if hasattr(model, "id") and hasattr(model, "group"):
            return f"Task {model.id}: {model.name} \\[{model.group}]"
        if hasattr(model, "severity") and hasattr(model, "description"):
            tag = model.severity.upper()
            desc = model.description[:80]
            return f"\\[{tag}] {desc}"
        return ""

    def _format_model_fields(self, model: BaseModel, indent: int = 2) -> Text:
        """Recursively format Pydantic model fields as rich Text."""
        text = Text()
        pad = " " * indent
        for name, value in model:
            display_name = name.replace("_", " ").title()
            if isinstance(value, BaseModel):
                text.append(f"{pad}  {display_name}:\n", style="cyan")
                text.append(self._format_model_fields(value, indent + 4))
            elif isinstance(value, list):
                if not value:
                    text.append(f"{pad}  ", style="")
                    text.append(f"{display_name}:", style="cyan")
                    text.append(" (none)\n")
                elif all(isinstance(v, BaseModel) for v in value):
                    text.append(f"{pad}  ", style="")
                    text.append(f"{display_name}:", style="cyan")
                    text.append(f" ({len(value)} items)\n")
                    for i, item in enumerate(value):
                        header = self._model_summary_rich(item, i)
                        text.append(f"{pad}    ")
                        text.append(f"[{i + 1}]", style="dim")
                        text.append(" ")
                        text.append_text(Text.from_markup(header))
                        text.append("\n")
                        text.append(self._format_model_fields(item, indent + 8))
                else:
                    text.append(f"{pad}  ", style="")
                    text.append(f"{display_name}:", style="cyan")
                    text.append(f" ({len(value)} items)\n")
                    for item in value:
                        text.append(f"{pad}    - {item}\n")
            elif isinstance(value, str):
                style = self._status_style(value)
                if len(value) > 80:
                    text.append(f"{pad}  ", style="")
                    text.append(f"{display_name}:", style="cyan")
                    text.append("\n")
                    for line in _wrap_text(value, width=76 - indent):
                        text.append(f"{pad}    {line}\n")
                else:
                    text.append(f"{pad}  ", style="")
                    text.append(f"{display_name}:", style="cyan")
                    text.append(f" ")
                    text.append(value, style=style)
                    text.append("\n")
            else:
                text.append(f"{pad}  ", style="")
                text.append(f"{display_name}:", style="cyan")
                text.append(f" {value}\n")
        return text

    def result_panel(self, label: str, model: BaseModel) -> None:
        """Render a Pydantic model as a rich Panel to stdout."""
        body = self._format_model_fields(model)
        panel = Panel(body, title=f"[bold]{label}[/bold]", border_style="dim", width=min(80, self.width))
        self.stdout.print(panel)

    def summary_table(
        self,
        title: str,
        rows: list[tuple[str, str, str]],
        footer: dict[str, str] | None = None,
    ) -> None:
        """Render final summary as a rich Table in a Panel.

        rows: list of (label, status_icon, detail)
        footer: optional key-value pairs below the table
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Stage", style="cyan", min_width=14)
        table.add_column("Status")
        for label, icon, detail in rows:
            table.add_row(label, f"{icon}  {detail}")
        if footer:
            table.add_row("", "")
            for k, v in footer.items():
                table.add_row(k, v)
        panel = Panel(table, title=f"[bold]{title}[/bold]", border_style="dim", width=min(80, self.width))
        self.stdout.print(panel)

    def stage_bar(self, current_key: str) -> None:
        """Print a stage progress tracker bar."""
        parts: list[str] = []
        found = False
        for key, label in _STAGES:
            if key == current_key:
                parts.append(f"[bold white on blue] {label} [/bold white on blue]")
                found = True
            elif not found:
                parts.append(f"[green]{label}[/green]")
            else:
                parts.append(f"[dim]{label}[/dim]")
        bar = " > ".join(parts)
        self.console.print(bar)

    def banner(self, title: str, subtitle: str, fields: list[tuple[str, str]]) -> None:
        """Print startup banner as a rich Panel."""
        lines: list[str] = []
        if subtitle:
            lines.append(f"[dim]{subtitle}[/dim]")
            lines.append("")
        for label, value in fields:
            lines.append(f"[cyan]{label + ':':<12}[/cyan] {value}")
        body = "\n".join(lines)
        panel = Panel(body, title=f"[bold]{title}[/bold]", border_style="blue", width=min(80, self.width))
        self.console.print(panel)

    def stage_header(self, text: str) -> None:
        """Print a stage transition header."""
        self.console.rule(f"[bold]{text}[/bold]")

    def info(self, msg: str) -> None:
        """Print an informational message to stderr."""
        self.console.print(f"  {msg}")

    def warn(self, msg: str) -> None:
        """Print a warning to stderr."""
        self.console.print(f"  [yellow]{msg}[/yellow]")

    def error(self, msg: str) -> None:
        """Print an error to stderr."""
        self.console.print(f"  [red]{msg}[/red]")

    def spinner_context(self, message: str) -> "Console.status":
        """Return a console.status() context manager for spinners."""
        return self.console.status(f"  {message}", spinner="dots")


display = Display()
