"""Tests for the skills module: bundled paths, install, uninstall."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from rpi.skills import ADD_DIR_PATH, SKILL_NAMES, SKILLS_DIR, install_skills, uninstall_skills


def test_skills_dir_exists():
    assert SKILLS_DIR.is_dir()
    for name in SKILL_NAMES:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        assert skill_file.is_file(), f"Missing skill: {skill_file}"


def test_add_dir_path_is_absolute():
    assert ADD_DIR_PATH.is_absolute()


def test_skills_dir_is_under_add_dir():
    assert str(SKILLS_DIR).startswith(str(ADD_DIR_PATH))


def test_install_creates_symlinks(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    with patch("rpi.skills.TARGET_DIR", target):
        messages = install_skills()
    for name in SKILL_NAMES:
        link = target / name
        assert link.is_symlink()
        assert link.resolve() == (SKILLS_DIR / name).resolve()
    assert all("installed" in m for m in messages)


def test_install_skips_existing_correct_symlink(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    with patch("rpi.skills.TARGET_DIR", target):
        install_skills()
        messages = install_skills()
    assert all("already linked" in m for m in messages)


def test_install_refuses_existing_dir_without_force(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    (target / "rpi-commit").mkdir()
    (target / "rpi-commit" / "SKILL.md").write_text("custom")
    with patch("rpi.skills.TARGET_DIR", target):
        messages = install_skills(force=False)
    commit_msg = next(m for m in messages if "rpi-commit" in m)
    assert "EXISTS" in commit_msg
    assert (target / "rpi-commit" / "SKILL.md").read_text() == "custom"


def test_install_overwrites_existing_dir_with_force(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    (target / "rpi-commit").mkdir()
    (target / "rpi-commit" / "SKILL.md").write_text("custom")
    with patch("rpi.skills.TARGET_DIR", target):
        messages = install_skills(force=True)
    commit_msg = next(m for m in messages if "rpi-commit" in m)
    assert "overwritten" in commit_msg
    assert (target / "rpi-commit").is_symlink()


def test_uninstall_removes_our_symlinks(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    with patch("rpi.skills.TARGET_DIR", target):
        install_skills()
        messages = uninstall_skills()
    for name in SKILL_NAMES:
        assert not (target / name).exists()
    assert all("removed" in m for m in messages)


def test_uninstall_preserves_foreign_symlinks(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    foreign = tmp_path / "foreign-skill"
    foreign.mkdir()
    (target / "rpi-commit").symlink_to(foreign)
    with patch("rpi.skills.TARGET_DIR", target):
        messages = uninstall_skills()
    commit_msg = next(m for m in messages if "rpi-commit" in m)
    assert "not ours" in commit_msg
    assert (target / "rpi-commit").is_symlink()


def test_uninstall_preserves_regular_directories(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    (target / "rpi-commit").mkdir()
    with patch("rpi.skills.TARGET_DIR", target):
        messages = uninstall_skills()
    commit_msg = next(m for m in messages if "rpi-commit" in m)
    assert "not ours" in commit_msg
    assert (target / "rpi-commit").is_dir()
