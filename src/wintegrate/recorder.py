"""Action recorder, playback engine, text-based action timeline recording, and CLI inspect tool."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wintegrate.diagnostics import WindowCensus
from wintegrate.element import UiaElement, get_uia
from wintegrate.exceptions import ActionVerificationError
from wintegrate.interop import (
    attach_to_input_desktop,
    get_foreground_window,
    get_window_title,
    send_char_input,
)
from wintegrate.text import normalize_line_endings
from wintegrate.window import Window

logger = logging.getLogger(__name__)


@dataclass
class RecordedAction:
    timestamp_offset: float
    action_type: str  # "type", "click", "invoke", "key_press", "window_focus", "assert_text"
    target_automation_id: str | None = None
    target_name: str | None = None
    target_class: str | None = None
    window_title: str | None = None
    text_content: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class TextActionTimelineRecorder:
    """
    Records a high-fidelity, human-readable structured text timeline (.log / .jsonl / .txt)
    of all UI actions, state mutations, and window focus transitions.

    Extremely lightweight for CI runs where video capture might be disabled or where
    instant diffing/grepping in PR comments / CI terminal output is desired.
    """

    def __init__(self, output_path: str | Path | None = None):
        self.output_path = Path(output_path) if output_path else None
        self.t0 = time.monotonic()
        self.events: list[RecordedAction] = []
        self._file = None

        if self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.output_path, "w", encoding="utf-8", buffering=1)

    def record_action(
        self,
        action_type: str,
        target: UiaElement | None = None,
        window: Window | None = None,
        text: str | None = None,
        **details: Any,
    ):
        offset = time.monotonic() - self.t0
        auto_id = target.automation_id if target else None
        name = target.name if target else None
        cls_name = target.class_name if target else None
        w_title = (
            window.title
            if window
            else (get_window_title(get_foreground_window()) if not target else None)
        )

        action = RecordedAction(
            timestamp_offset=round(offset, 4),
            action_type=action_type,
            target_automation_id=auto_id,
            target_name=name,
            target_class=cls_name,
            window_title=w_title,
            text_content=text,
            details=details,
        )
        self.events.append(action)

        line = (
            f"[{action.timestamp_offset:7.3f}s] {action.action_type.upper():<12} | "
            f"Window: '{w_title or ''}' | "
            f"ID: '{auto_id or ''}' | Name: '{name or ''}'"
        )
        if text:
            line += f" | Text: {repr(text)}"
        if details:
            line += f" | Details: {details}"

        if self._file:
            self._file.write(line + "\n")
        logger.debug(line)

    def dump_json(self, path: str | Path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(e) for e in self.events], f, indent=2)

    def close(self):
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None


class ActionPlayer:
    """
    Plays back a sequence of recorded structured actions or a recorded session.
    """

    def __init__(self, actions: list[RecordedAction] | None = None):
        self.actions = actions or []

    @classmethod
    def from_json(cls, path: str | Path) -> ActionPlayer:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = [RecordedAction(**item) for item in data]
        return cls(actions)

    def playback(self, session: Any = None, speed_factor: float = 1.0):
        """Replays all actions sequentially."""
        last_offset = 0.0
        for action in self.actions:
            dt = (action.timestamp_offset - last_offset) / max(0.1, speed_factor)
            if dt > 0:
                time.sleep(min(dt, 3.0))  # Cap wait to 3 seconds for CI playback
            last_offset = action.timestamp_offset

            logger.info(f"Replaying: {action.action_type} on ID '{action.target_automation_id}'")

            # Locate window if specified
            if action.window_title:
                win = Window.find(
                    title_pattern=f".*{re.escape(action.window_title)}.*", timeout=2.0
                )
                win.set_foreground()
                root = win.re_resolve_element()
            else:
                root = UiaElement.from_handle(get_foreground_window())

            # Find target element
            elem = None
            if action.target_automation_id or action.target_name:
                elem = root.find_descendant(
                    automation_id=action.target_automation_id,
                    name_exact=action.target_name,
                    timeout=3.0,
                )

            # Perform action
            if action.action_type == "type" and elem and action.text_content:
                elem.type_verified(action.text_content)
            elif action.action_type == "invoke" and elem:
                elem.invoke()
            elif action.action_type == "click" and elem:
                elem.click()
            elif action.action_type == "key_press" and action.text_content:
                if elem:
                    elem.set_focus()
                for ch in action.text_content:
                    send_char_input(ch)
            elif action.action_type in ("set_focus", "window_focus"):
                if elem:
                    elem.set_focus()
                elif not action.window_title:
                    logger.warning(
                        f"Skipping '{action.action_type}' action: no target element resolved "
                        "and no window_title to focus"
                    )
                # else: the window was already brought to the foreground above
            elif action.action_type == "assert_text" and elem and action.text_content:
                val = elem.get_value()
                if normalize_line_endings(action.text_content) not in normalize_line_endings(val):
                    raise ActionVerificationError(
                        f"assert_text failed: '{action.text_content}' not found in '{val}'"
                    )
            else:
                logger.warning(
                    f"Skipping action '{action.action_type}' (unsupported type, unresolved "
                    f"target element, or missing text_content; "
                    f"id='{action.target_automation_id}', name='{action.target_name}')"
                )


def inspect_desktop_tree(max_depth: int = 2) -> dict[str, Any]:
    """
    CLI/inspect helper to dump current UIA hierarchy on desktop.
    """
    attach_to_input_desktop()
    windows = WindowCensus.capture()
    visible_wins = [w for w in windows if w.is_visible and w.title]

    results = []
    for w in visible_wins:
        win_dict: dict[str, Any] = {
            "hwnd": w.hwnd,
            "title": w.title,
            "class": w.class_name,
            "pid": w.pid,
            "children": [],
        }
        try:
            root = UiaElement.from_handle(w.hwnd)
            uia = get_uia()
            cond = uia.CreateTrueCondition()
            # TreeScope_Children = 2
            children = root._element.FindAll(2, cond)
            if children:
                for i in range(min(children.Length, 15)):
                    c = children.GetElement(i)
                    win_dict["children"].append(
                        {
                            "name": c.CurrentName or "",
                            "automation_id": c.CurrentAutomationId or "",
                            "control_type": c.CurrentLocalizedControlType or "",
                            "class": c.CurrentClassName or "",
                        }
                    )
        except Exception:
            pass
        results.append(win_dict)

    return {"desktop_windows": results}
