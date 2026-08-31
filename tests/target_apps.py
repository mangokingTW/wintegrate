"""Locating the third-party applications the `target_app` tests drive.

These tests exist because a test app written for this library only proves the
library agrees with itself. Four real applications, one per generation of Windows
UI technology:

    Notepad++                Scintilla   (tests/test_scintilla.py)
    WinMerge                 Win32/MFC   (tests/test_target_winmerge.py)
    DB Browser for SQLite    Qt          (tests/test_target_sqlitebrowser.py)
    Files                    WinUI 3     (tests/test_target_files.py)

A missing application skips its tests. That is the right default for a laptop and
the wrong default for a release gate, where a silent skip looks exactly like a
pass — so `WINTEGRATE_REQUIRE_TARGET_APPS` names the applications whose absence
must be a *failure* instead:

    WINTEGRATE_REQUIRE_TARGET_APPS=1                     every application
    WINTEGRATE_REQUIRE_TARGET_APPS='WinMerge,Notepad++'  only these

A list rather than a boolean so that a partially-provisioned machine can still be
strict about the applications it does have. CI passes `1` and requires all four.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REQUIRED = os.environ.get("WINTEGRATE_REQUIRE_TARGET_APPS", "")


def _is_required(what: str) -> bool:
    if _REQUIRED.strip() == "1":
        return True
    wanted = {name.strip().casefold() for name in _REQUIRED.split(",") if name.strip()}
    return what.casefold() in wanted


def _missing(what: str, reason: str):
    """Skip, or fail when the caller has declared the app must be present."""
    message = f"{what} not available: {reason}"
    if _is_required(what):
        pytest.fail(
            f"{message}\n"
            f"WINTEGRATE_REQUIRE_TARGET_APPS={_REQUIRED!r} names {what}, so this is a "
            "failure rather than a skip: the application was supposed to have been "
            "installed."
        )
    pytest.skip(message)


def find_executable(name: str, candidates: tuple[Path, ...]) -> Path:
    """The first candidate path that exists, or skip/fail naming every path tried.

    Installers disagree about where they put things — WinMerge lands in
    `%LOCALAPPDATA%\\Programs` when installed per-user and in `Program Files` when
    installed for the machine — so every plausible location is listed and the
    failure message says which ones were checked.
    """
    for path in candidates:
        if path.exists():
            return path
    tried = "\n".join(f"  - {p}" for p in candidates)
    _missing(name, f"not found at any of:\n{tried}")


def find_packaged_app(package_name: str) -> str:
    """The AUMID of an installed MSIX package, or skip/fail.

    Packaged apps have no path to test for; the package has to be asked about, and
    it is addressed by AUMID (`PackageFamilyName!ApplicationId`) rather than by an
    executable.
    """
    script = (
        f"$p = Get-AppxPackage -Name '{package_name}' | Select-Object -First 1; "
        "if (-not $p) { exit 1 }; "
        "$m = Get-AppxPackageManifest $p; "
        "$a = $m.Package.Applications.Application | Select-Object -First 1; "
        "Write-Output ($p.PackageFamilyName + '!' + $a.Id)"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _missing(package_name, f"could not query Get-AppxPackage: {exc}")
    aumid = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    if result.returncode != 0 or "!" not in aumid:
        _missing(
            package_name,
            f"Get-AppxPackage returned no installed package (exit {result.returncode})",
        )
    return aumid


def launch_packaged_app(aumid: str) -> list[str]:
    """The command that starts a packaged app.

    A packaged app cannot be started by path, and `Start-Process shell:appsFolder`
    would put PowerShell between us and the app. Handing the shell moniker to
    explorer keeps the launcher trivial — `launch_and_discover` already copes with
    the launcher's pid differing from the window's.
    """
    return ["explorer.exe", f"shell:appsFolder\\{aumid}"]
