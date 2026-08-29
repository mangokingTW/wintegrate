"""Pytest fixtures and configuration for platform-aware Windows UI automation testing."""

from __future__ import annotations

import sys
import pytest


def is_windows_server() -> bool:
    """Returns True if the current operating system is a Windows Server edition."""
    if sys.platform != "win32":
        return False
    # sys.getwindowsversion().product_type: 1 = Workstation (Client/Desktop), 2/3 = Server/DC
    return getattr(sys.getwindowsversion(), "product_type", 1) != 1


def is_windows_desktop() -> bool:
    """Returns True if the current operating system is a Windows Desktop / Client edition."""
    if sys.platform != "win32":
        return False
    return getattr(sys.getwindowsversion(), "product_type", 1) == 1


# Reusable test decorators
desktop_only = pytest.mark.skipif(
    is_windows_server(),
    reason="Test requires Windows Desktop edition (e.g. UWP apps / client Virtual Desktops)",
)

server_only = pytest.mark.skipif(
    is_windows_desktop(),
    reason="Test is specifically designed for Windows Server environments",
)
