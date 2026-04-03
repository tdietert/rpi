"""Install/uninstall subcommands for RPI skills and agents."""

from __future__ import annotations

import sys

from rich.console import Console

from .agents import install_agents, uninstall_agents
from .display import bold, green
from .skills import install_skills, uninstall_skills

_INSTALL_CMDS = {"install", "install-skills", "install-agents"}
_UNINSTALL_CMDS = {"uninstall", "uninstall-skills", "uninstall-agents"}
ALL_CMDS = _INSTALL_CMDS | _UNINSTALL_CMDS


def handle_install_subcommand(cmd: str, argv: list[str]) -> None:
    """Handle an install/uninstall subcommand and sys.exit."""
    console = Console()

    if cmd in _INSTALL_CMDS:
        force = "--force" in argv

        if cmd in ("install", "install-skills"):
            console.print(bold("Installing RPI skills..."))
            for msg in install_skills(force=force):
                console.print(msg)

        if cmd in ("install", "install-agents"):
            console.print(bold("Installing RPI agents..."))
            for msg in install_agents(force=force):
                console.print(msg)

        console.print(green("Done."))
        sys.exit(0)

    if cmd in _UNINSTALL_CMDS:
        if cmd in ("uninstall", "uninstall-skills"):
            console.print(bold("Uninstalling RPI skills..."))
            for msg in uninstall_skills():
                console.print(msg)

        if cmd in ("uninstall", "uninstall-agents"):
            console.print(bold("Uninstalling RPI agents..."))
            for msg in uninstall_agents():
                console.print(msg)

        console.print(green("Done."))
        sys.exit(0)
