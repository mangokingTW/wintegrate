"""Drive a classic Win32 dialog: controls by id, checkboxes, combo boxes, lists.

    python examples/02_win32_dialog_controls.py "Some Dialog Title"

Classic dialogs are a different UIA provider from modern XAML apps: controls are
addressed by their numeric control id (the same ids in the dialog resource), and
state changes go through Toggle / Selection / ExpandCollapse rather than typing.

Point it at any open `#32770` dialog to see what wintegrate makes of it.
"""

from __future__ import annotations

import sys

from wintegrate import Window


def main(title: str) -> None:
    dialog = Window.find(class_name="#32770", title_pattern=title, timeout=10.0)
    print(f"dialog: {dialog.title!r} (hwnd {dialog.hwnd}, pid {dialog.pid})")

    root = dialog.re_resolve_element()

    for control in root.children():
        # A classic control's AutomationId is its control id from the resource.
        ident = control.automation_id or "-"
        line = f"  [{ident:>6}] {control.class_name:<12} {control.name!r}"

        # Only ask for a pattern's state when the control supports it; None means
        # "not that kind of control", not "off".
        if (state := control.toggle_state) is not None:
            line += f"  checked={bool(state)}"
        if (expand := control.expand_collapse_state) is not None:
            line += f"  expanded={expand == 1}"
        if control.class_name in ("Edit", "ComboBox"):
            line += f"  value={control.get_value()!r}"

        print(line)

    # Presence check without an exception: what .exists() was for in pywinauto.
    ok_button = root.find_descendant(automation_id="1", timeout=1.0, required=False)
    print("has an IDOK button:", ok_button is not None)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".*")
