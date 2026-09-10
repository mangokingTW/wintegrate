"""Agent skill distribution and installation helpers for wintegrate."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def get_skill_content() -> str:
    """Returns the raw markdown content of the bundled wintegrate SKILL.md."""
    return (
        importlib.resources.files("wintegrate.skills")
        .joinpath("SKILL.md")
        .read_text(encoding="utf-8")
    )


def get_skill_path() -> Path:
    """Returns the path to the bundled SKILL.md resource."""
    traversable = importlib.resources.files("wintegrate.skills").joinpath("SKILL.md")
    return Path(str(traversable))


def install_skill(
    target: Path | str | None = None,
    *,
    global_install: bool = False,
    force: bool = False,
) -> Path:
    """Installs the bundled wintegrate SKILL.md into a target directory or file.

    Args:
        target: Optional custom path to write the SKILL.md file to. If a directory
            is given, `SKILL.md` is appended. Defaults to
            `.agents/skills/wintegrate/SKILL.md` in the current working directory
            (or `~/.agents/skills/wintegrate/SKILL.md` if `global_install=True`).
        global_install: If True and target is not specified, installs to the user's
            home directory (~/.agents/skills/wintegrate/SKILL.md).
        force: If True, overwrites existing file without checking.

    Returns:
        The destination Path where SKILL.md was written.

    Raises:
        FileExistsError: If target file already exists and force is False.
    """
    if target is not None:
        dest = Path(target).resolve()
        if dest.is_dir() or str(target).endswith(("/", "\\")):
            dest = dest / "SKILL.md"
    elif global_install:
        dest = Path.home() / ".agents" / "skills" / "wintegrate" / "SKILL.md"
    else:
        dest = Path.cwd() / ".agents" / "skills" / "wintegrate" / "SKILL.md"

    if dest.exists() and not force:
        existing_content = dest.read_text(encoding="utf-8", errors="replace")
        new_content = get_skill_content()
        if existing_content == new_content:
            return dest
        raise FileExistsError(f"Skill file already exists at {dest}. Use --force to overwrite.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(get_skill_content(), encoding="utf-8")
    return dest


__all__ = [
    "get_skill_content",
    "get_skill_path",
    "install_skill",
]
