"""Agent definition management: bundled paths, install, uninstall."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
AGENTS_DIR = _PACKAGE_DIR / ".claude" / "agents"

AGENT_NAMES = [
    "code-fixer",
    "code-implementer",
    "code-reviewer",
]

TARGET_DIR = Path.home() / ".claude" / "agents"


def install_agents(*, force: bool = False) -> list[str]:
    """Create symlinks from ~/.claude/agents/<name>.md -> package agents.

    Returns list of status messages (one per agent).
    """
    messages: list[str] = []
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for name in AGENT_NAMES:
        src = AGENTS_DIR / f"{name}.md"
        dest = TARGET_DIR / f"{name}.md"

        if not src.is_file():
            messages.append(f"  {name}: MISSING from package (skipped)")
            continue

        if dest.is_symlink():
            current_target = dest.resolve()
            if current_target == src.resolve():
                messages.append(f"  {name}: already linked (skipped)")
                continue
            dest.unlink()
            dest.symlink_to(src)
            messages.append(f"  {name}: relinked (was pointing to {current_target})")
        elif dest.exists():
            if not force:
                messages.append(
                    f"  {name}: EXISTS as regular file (use --force to overwrite)"
                )
                continue
            dest.unlink()
            dest.symlink_to(src)
            messages.append(f"  {name}: overwritten (was regular file)")
        else:
            dest.symlink_to(src)
            messages.append(f"  {name}: installed")

    return messages


def uninstall_agents() -> list[str]:
    """Remove symlinks from ~/.claude/agents/<name>.md that point to our package.

    Only removes symlinks that resolve into our package directory. Leaves
    regular files and foreign symlinks untouched.
    """
    messages: list[str] = []
    for name in AGENT_NAMES:
        dest = TARGET_DIR / f"{name}.md"
        if dest.is_symlink():
            target = dest.resolve()
            if str(target).startswith(str(AGENTS_DIR)):
                dest.unlink()
                messages.append(f"  {name}: removed")
            else:
                messages.append(f"  {name}: symlink to {target} (not ours, skipped)")
        elif dest.exists():
            messages.append(f"  {name}: regular file (not ours, skipped)")
        else:
            messages.append(f"  {name}: not present (skipped)")
    return messages
