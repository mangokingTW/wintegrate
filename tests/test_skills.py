"""Tests for wintegrate.skills and the skill CLI."""

from __future__ import annotations

from pathlib import Path
import pytest

from wintegrate.skills import get_skill_content, get_skill_path, install_skill
from wintegrate.skills.__main__ import main


def test_get_skill_content():
    content = get_skill_content()
    assert content.startswith("---")
    assert "name: wintegrate" in content
    assert "description:" in content
    assert "## Core Philosophy and Golden Rules" in content


def test_get_skill_path():
    path = get_skill_path()
    assert path.name == "SKILL.md"
    assert path.exists()



def test_install_skill_custom_path(tmp_path: Path):
    dest = install_skill(target=tmp_path / "custom" / "SKILL.md")
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == get_skill_content()


def test_install_skill_into_dir(tmp_path: Path):
    dest = install_skill(target=tmp_path)
    assert dest == tmp_path / "SKILL.md"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == get_skill_content()


def test_install_skill_idempotent(tmp_path: Path):
    dest = install_skill(target=tmp_path / "SKILL.md")
    # Calling install again with same content should succeed without error
    dest2 = install_skill(target=tmp_path / "SKILL.md", force=False)
    assert dest == dest2


def test_install_skill_conflict_requires_force(tmp_path: Path):
    dest = tmp_path / "SKILL.md"
    dest.write_text("old customized content", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        install_skill(target=dest, force=False)

    assert dest.read_text(encoding="utf-8") == "old customized content"

    # With force=True, it should overwrite
    install_skill(target=dest, force=True)
    assert dest.read_text(encoding="utf-8") == get_skill_content()


def test_cli_show(capsys):
    ret = main(["show"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "name: wintegrate" in captured.out


def test_cli_path(capsys):
    ret = main(["path"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "SKILL.md" in captured.out


def test_cli_install(tmp_path: Path, capsys):
    target = tmp_path / "installed_skill.md"
    ret = main(["install", "--target", str(target)])
    assert ret == 0
    assert target.exists()
    captured = capsys.readouterr()
    assert "Successfully installed" in captured.out
