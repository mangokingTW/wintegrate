"""Platform capabilities detection and environment profiling."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass

try:
    import pytest

    _has_pytest = True
except ImportError:
    _has_pytest = False


@dataclass(frozen=True)
class PlatformCapabilities:
    """Represents detected OS edition, architecture, and subsystem support."""

    is_windows: bool
    is_server: bool
    is_desktop: bool
    is_arm64: bool
    os_build: int
    supports_virtual_desktops: bool
    supports_uwp: bool


def detect_capabilities() -> PlatformCapabilities:
    """Detects and returns active platform capabilities."""
    if sys.platform != "win32":
        return PlatformCapabilities(
            is_windows=False,
            is_server=False,
            is_desktop=False,
            is_arm64=False,
            os_build=0,
            supports_virtual_desktops=False,
            supports_uwp=False,
        )

    # 1. Detect Edition: product_type 1=Desktop/Client, 2/3=Server/DC
    win_ver = sys.getwindowsversion()
    is_server = getattr(win_ver, "product_type", 1) != 1
    is_desktop = not is_server

    # 2. Detect Architecture
    mach = platform.machine().lower()
    is_arm64 = mach in ("arm64", "aarch64")

    # 3. Virtual Desktops capability (Desktop edition build >= 10240)
    supports_vd = is_desktop and win_ver.build >= 10240

    # 4. UWP / AppX capability
    supports_uwp = is_desktop

    return PlatformCapabilities(
        is_windows=True,
        is_server=is_server,
        is_desktop=is_desktop,
        is_arm64=is_arm64,
        os_build=win_ver.build,
        supports_virtual_desktops=supports_vd,
        supports_uwp=supports_uwp,
    )


# Process-wide capabilities singleton
env = detect_capabilities()


def _env_flag(name: str) -> bool:
    val = os.getenv(name)
    return val is not None and val.strip().lower() not in ("", "0", "false", "no")


def is_ci() -> bool:
    """Returns True when running under a CI service (CI / GITHUB_ACTIONS truthy)."""
    return _env_flag("CI") or _env_flag("GITHUB_ACTIONS")


def is_windows_server() -> bool:
    """Returns True if the current operating system is a Windows Server edition."""
    return env.is_server


def is_windows_desktop() -> bool:
    """Returns True if the current operating system is a Windows Desktop / Client edition."""
    return env.is_desktop


if _has_pytest:
    desktop_only = pytest.mark.skipif(
        env.is_server,
        reason="Requires Windows Desktop edition (e.g. UWP apps / client Virtual Desktops)",
    )
    server_only = pytest.mark.skipif(
        env.is_desktop,
        reason="Specifically designed for Windows Server environments",
    )
else:
    desktop_only = lambda fn: fn  # noqa: E731
    server_only = lambda fn: fn  # noqa: E731
