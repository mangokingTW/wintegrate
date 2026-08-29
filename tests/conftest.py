"""Pytest fixtures and configuration for platform-aware Windows UI automation testing."""

from __future__ import annotations

from wintegrate import (
    desktop_only,
    env,
    is_windows_desktop,
    is_windows_server,
    server_only,
)

__all__ = [
    "env",
    "is_windows_server",
    "is_windows_desktop",
    "desktop_only",
    "server_only",
]
