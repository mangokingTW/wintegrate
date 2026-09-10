"""CLI entry point for `python -m wintegrate.skills`."""

from __future__ import annotations

import argparse
import sys

from wintegrate.skills import get_skill_content, get_skill_path, install_skill


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wintegrate.skills",
        description="Manage wintegrate AI agent skills for coding assistants.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # install subcommand
    install_parser = subparsers.add_parser(
        "install",
        help="Install wintegrate SKILL.md into your project or global agent directory.",
    )
    install_parser.add_argument(
        "-t",
        "--target",
        help="Custom destination directory or file path (default: .agents/skills/wintegrate/SKILL.md).",
    )
    install_parser.add_argument(
        "-g",
        "--global",
        dest="global_install",
        action="store_true",
        help="Install globally to ~/.agents/skills/wintegrate/SKILL.md.",
    )
    install_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite destination file if it already exists.",
    )

    # show subcommand
    subparsers.add_parser(
        "show",
        help="Print the bundled SKILL.md content to standard output.",
    )

    # path subcommand
    subparsers.add_parser(
        "path",
        help="Print the path to the bundled SKILL.md inside the package.",
    )

    args = parser.parse_args(argv)

    if args.command == "install":
        try:
            dest = install_skill(
                target=args.target,
                global_install=args.global_install,
                force=args.force,
            )
            print(f"Successfully installed wintegrate skill to:\n  {dest}")
            return 0
        except FileExistsError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        except Exception as err:
            print(f"Failed to install skill: {err}", file=sys.stderr)
            return 1
    elif args.command == "show":
        try:
            print(get_skill_content(), end="")
            return 0
        except Exception as err:
            print(f"Failed to read skill content: {err}", file=sys.stderr)
            return 1
    elif args.command == "path":
        try:
            print(get_skill_path())
            return 0
        except Exception as err:
            print(f"Failed to locate skill path: {err}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
