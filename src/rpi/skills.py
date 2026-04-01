"""Skill file management: bundled paths, install, uninstall."""

from __future__ import annotations

import shutil
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = _PACKAGE_DIR / ".claude" / "skills"
ADD_DIR_PATH = _PACKAGE_DIR

SKILL_NAMES = [
    "rpi-commit",
    "rpi-create-pr",
    "rpi-diagnosis",
    "rpi-fix",
    "rpi-implement",
    "rpi-plan",
    "rpi-plan-review",
    "rpi-research",
    "rpi-review",
    "rpi-spec",
]

TARGET_DIR = Path.home() / ".claude" / "skills"


def install_skills(*, force: bool = False) -> list[str]:
    """Create symlinks from ~/.claude/skills/<name> -> package skills.

    Returns list of status messages (one per skill).
    """
    messages: list[str] = []
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for name in SKILL_NAMES:
        src = SKILLS_DIR / name
        dest = TARGET_DIR / name

        if not src.is_dir():
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
                    f"  {name}: EXISTS as regular directory (use --force to overwrite)"
                )
                continue
            shutil.rmtree(dest)
            dest.symlink_to(src)
            messages.append(f"  {name}: overwritten (was regular directory)")
        else:
            dest.symlink_to(src)
            messages.append(f"  {name}: installed")

    return messages


def uninstall_skills() -> list[str]:
    """Remove symlinks from ~/.claude/skills/<name> that point to our package.

    Only removes symlinks that resolve into our package directory. Leaves
    regular directories and foreign symlinks untouched.
    """
    messages: list[str] = []
    for name in SKILL_NAMES:
        dest = TARGET_DIR / name
        if dest.is_symlink():
            target = dest.resolve()
            if str(target).startswith(str(SKILLS_DIR)):
                dest.unlink()
                messages.append(f"  {name}: removed")
            else:
                messages.append(f"  {name}: symlink to {target} (not ours, skipped)")
        elif dest.exists():
            messages.append(f"  {name}: regular directory (not ours, skipped)")
        else:
            messages.append(f"  {name}: not present (skipped)")
    return messages
