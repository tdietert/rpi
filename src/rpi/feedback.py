"""Multi-line feedback input for interactive stage loops."""

from __future__ import annotations

from prompt_toolkit import prompt as pt_prompt


def collect_feedback(stage_name: str) -> str | None:
    """Collect multi-line feedback from the user.

    Returns the stripped feedback text, or None if the user submits
    empty/whitespace-only input (meaning they accept the current result).
    """
    print(f"\n{stage_name} feedback (Enter for newline, Esc+Enter to submit, empty to accept):")
    text = pt_prompt("", multiline=True)
    stripped = text.strip()
    return stripped if stripped else None
