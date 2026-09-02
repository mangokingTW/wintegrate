"""Playwright-style lazy Locators with auto-wait and chained filtering for Windows UI."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from wintegrate.element import UiaElement
    from wintegrate.window import Window

from wintegrate.exceptions import (
    ElementNotFoundError,
    TimeoutError,
)

# Standard UIA Control Type IDs mapped to user-friendly role aliases
ROLE_TO_CONTROL_TYPE_IDS: dict[str, list[int]] = {
    "button": [50000, 50031],
    "calendar": [50001],
    "checkbox": [50002],
    "check_box": [50002],
    "combobox": [50003],
    "combo_box": [50003],
    "edit": [50004, 50030],
    "textbox": [50004, 50030],
    "text_box": [50004, 50030],
    "input": [50004, 50030],
    "document": [50030],
    "hyperlink": [50005],
    "link": [50005],
    "image": [50006],
    "listitem": [50007],
    "list_item": [50007],
    "list": [50008],
    "listbox": [50008],
    "menu": [50009, 50010],
    "menubar": [50010],
    "menu_bar": [50010],
    "menuitem": [50011],
    "menu_item": [50011],
    "progressbar": [50012],
    "progress_bar": [50012],
    "radio": [50013],
    "radiobutton": [50013],
    "radio_button": [50013],
    "scrollbar": [50014],
    "scroll_bar": [50014],
    "slider": [50015],
    "spinner": [50016],
    "statusbar": [50017],
    "status_bar": [50017],
    "tab": [50018, 50019],
    "tabcontrol": [50018],
    "tab_control": [50018],
    "tabitem": [50019],
    "tab_item": [50019],
    "text": [50020],
    "label": [50020],
    "toolbar": [50021],
    "tool_bar": [50021],
    "tooltip": [50022],
    "tool_tip": [50022],
    "tree": [50023],
    "treeview": [50023],
    "treeitem": [50024],
    "tree_item": [50024],
    "group": [50026],
    "pane": [50033],
    "header": [50034],
    "headeritem": [50035],
    "header_item": [50035],
    "table": [50036],
    "datagrid": [50028],
    "data_grid": [50028],
    "splitbutton": [50031],
    "split_button": [50031],
    "window": [50032],
}

ROLE_TO_CONTROL_TYPE_ID: dict[str, int] = {k: v[0] for k, v in ROLE_TO_CONTROL_TYPE_IDS.items()}


class Locator:
    """A lazy locator representing an element or collection of elements with auto-wait."""

    def __init__(
        self,
        root_supplier: Callable[[], Window | UiaElement | None],
        query_fn: Callable[[Window | UiaElement], list[UiaElement]],
        description: str = "Locator",
    ):
        self._root_supplier = root_supplier
        self._query_fn = query_fn
        self._description = description

    def __repr__(self) -> str:
        return f"<Locator: {self._description}>"

    # --- Element Resolution & Auto-Wait ---

    def _resolve_elements(self) -> list[UiaElement]:
        root = self._root_supplier()
        if root is None:
            return []
        try:
            return self._query_fn(root)
        except Exception:
            return []

    def _wait_for_elements(self, timeout: float = 10.0, min_count: int = 1) -> list[UiaElement]:
        deadline = time.monotonic() + timeout
        last_found: list[UiaElement] = []
        while time.monotonic() < deadline:
            elems = self._resolve_elements()
            if len(elems) >= min_count:
                return elems
            last_found = elems
            time.sleep(0.05)
        raise ElementNotFoundError(
            f"Timed out after {timeout:.1f}s waiting for {self._description} "
            f"(expected at least {min_count} element(s), found {len(last_found)})"
        )

    @property
    def element(self) -> UiaElement:
        """Resolves and returns the single primary UiaElement, waiting up to 10s if not yet ready."""
        elems = self._wait_for_elements(timeout=10.0, min_count=1)
        return elems[0]

    def all(self, timeout: float = 5.0) -> list[Locator]:
        """Resolves all matching elements and returns a list of individual Locators."""
        try:
            elems = self._wait_for_elements(timeout=timeout, min_count=0)
        except Exception:
            elems = []
        result = []
        for i in range(len(elems)):
            result.append(self.nth(i))
        return result

    def count(self) -> int:
        """Returns the current number of matched elements without waiting."""
        return len(self._resolve_elements())

    # --- Slicing & Sub-Locators ---

    @property
    def first(self) -> Locator:
        """Locator targeting the first matching element."""
        return self.nth(0)

    @property
    def last(self) -> Locator:
        """Locator targeting the last matching element."""

        def query(root: Window | UiaElement) -> list[UiaElement]:
            elems = self._query_fn(root)
            return [elems[-1]] if elems else []

        return Locator(self._root_supplier, query, description=f"{self._description}.last")

    def nth(self, index: int) -> Locator:
        """Locator targeting the element at zero-based index `index`."""

        def query(root: Window | UiaElement) -> list[UiaElement]:
            elems = self._query_fn(root)
            if 0 <= index < len(elems):
                return [elems[index]]
            return []

        return Locator(self._root_supplier, query, description=f"{self._description}.nth({index})")

    def filter(
        self,
        *,
        has_text: str | None = None,
        has: Locator | None = None,
        class_name: str | None = None,
        automation_id: str | None = None,
    ) -> Locator:
        """Filters the matching elements by text content, child locator, class name, or automation ID."""
        criteria = []
        if has_text is not None:
            criteria.append(f"has_text={has_text!r}")
        if has is not None:
            criteria.append(f"has={has}")
        if class_name is not None:
            criteria.append(f"class_name={class_name!r}")
        if automation_id is not None:
            criteria.append(f"automation_id={automation_id!r}")
        desc = (
            f"{self._description}.filter({', '.join(criteria)})" if criteria else self._description
        )

        def query(root: Window | UiaElement) -> list[UiaElement]:
            candidates = self._query_fn(root)
            filtered = []
            for cand in candidates:
                if has_text is not None:
                    # check element text or name
                    val = cand.read_value().text or cand.name
                    if has_text not in val:
                        continue
                if class_name is not None and cand.class_name != class_name:
                    continue
                if automation_id is not None and cand.automation_id != automation_id:
                    continue
                if has is not None:
                    # check if child locator resolves within cand
                    child_matches = has._query_fn(cand)
                    if not child_matches:
                        continue
                filtered.append(cand)
            return filtered

        return Locator(self._root_supplier, query, description=desc)

    # --- Nested Locators ---

    def locator(self, selector: str | dict) -> Locator:
        """Finds descendant elements matching selector within this locator."""
        if isinstance(selector, str):
            query_dict = {"name": selector}
        else:
            query_dict = selector

        def query(root: Window | UiaElement) -> list[UiaElement]:
            parents = self._query_fn(root)
            results = []
            for p in parents:
                results.extend(p.find_all(**query_dict))
            return results

        return Locator(
            self._root_supplier,
            query,
            description=f"{self._description} >> {selector}",
        )

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> Locator:
        """Finds descendant elements by role (e.g. 'button', 'checkbox', 'tab', 'menuitem')."""
        role_lower = role.lower().strip()
        ctrl_type_ids = ROLE_TO_CONTROL_TYPE_IDS.get(role_lower)
        if ctrl_type_ids is None:
            raise ValueError(
                f"Unknown role {role!r}. Supported roles include: {sorted(set(ROLE_TO_CONTROL_TYPE_IDS.keys()))}"
            )

        def query(root: Window | UiaElement) -> list[UiaElement]:
            parents = self._query_fn(root)
            results = []
            seen_handles = set()
            for p in parents:
                for c_id in ctrl_type_ids:
                    matches = p.find_all(control_type_id=c_id)
                    for m in matches:
                        try:
                            h = m.handle
                            if h and h in seen_handles:
                                continue
                            if h:
                                seen_handles.add(h)
                        except Exception:
                            pass

                        if name is not None:
                            elem_name = m.name or ""
                            if exact:
                                if elem_name != name:
                                    continue
                            else:
                                if name not in elem_name:
                                    continue
                        results.append(m)
            return results

        desc = f"{self._description}.get_by_role({role!r}"
        if name is not None:
            desc += f", name={name!r}, exact={exact}"
        desc += ")"
        return Locator(self._root_supplier, query, description=desc)

    def get_by_text(self, text: str, exact: bool = False) -> Locator:
        """Finds descendant elements matching text."""

        def query(root: Window | UiaElement) -> list[UiaElement]:
            parents = self._query_fn(root)
            results = []
            for p in parents:
                matches = p.find_all()
                for m in matches:
                    val = m.read_value().text or m.name or ""
                    if exact:
                        if val == text:
                            results.append(m)
                    else:
                        if text in val:
                            results.append(m)
            return results

        return Locator(
            self._root_supplier,
            query,
            description=f"{self._description}.get_by_text({text!r}, exact={exact})",
        )

    def get_by_automation_id(self, auto_id: str) -> Locator:
        """Finds descendant elements by UIA automation_id."""

        def query(root: Window | UiaElement) -> list[UiaElement]:
            parents = self._query_fn(root)
            results = []
            for p in parents:
                results.extend(p.find_all(automation_id=auto_id))
            return results

        return Locator(
            self._root_supplier,
            query,
            description=f"{self._description}.get_by_automation_id({auto_id!r})",
        )

    def get_by_class(self, class_name: str) -> Locator:
        """Finds descendant elements by window class name."""

        def query(root: Window | UiaElement) -> list[UiaElement]:
            parents = self._query_fn(root)
            results = []
            for p in parents:
                results.extend(p.find_all(class_name=class_name))
            return results

        return Locator(
            self._root_supplier,
            query,
            description=f"{self._description}.get_by_class({class_name!r})",
        )

    # --- Actions with Auto-Wait ---

    def wait_for(self, state: str = "visible", timeout: float = 10.0) -> Locator:
        """Waits for the element to satisfy state ('visible', 'attached', 'hidden')."""
        deadline = time.monotonic() + timeout
        if state in ("visible", "attached"):
            self._wait_for_elements(timeout=timeout, min_count=1)
        elif state == "hidden":
            while time.monotonic() < deadline:
                if len(self._resolve_elements()) == 0:
                    return self
                time.sleep(0.05)
            raise TimeoutError(
                f"Timed out after {timeout:.1f}s waiting for {self._description} to be hidden"
            )
        else:
            raise ValueError(
                f"Unsupported wait state {state!r}. Choose from 'visible', 'attached', 'hidden'."
            )
        return self

    def click(
        self,
        *,
        timeout: float = 10.0,
        right: bool = False,
        double: bool = False,
        force: bool = False,
    ) -> bool:
        """Auto-waits for element, scrolls it into view if needed, and clicks it."""
        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        target = elems[0]
        if not force:
            target.ensure_available()
        if right:
            return target.right_click()
        elif double:
            return target.double_click()
        else:
            return target.click()

    def right_click(self, *, timeout: float = 10.0, force: bool = False) -> bool:
        """Performs a right mouse click on the locator target."""
        return self.click(timeout=timeout, right=True, force=force)

    def double_click(self, *, timeout: float = 10.0, force: bool = False) -> bool:
        """Performs a double mouse click on the locator target."""
        return self.click(timeout=timeout, double=True, force=force)

    def type_verified(
        self,
        text: str,
        *,
        timeout: float = 10.0,
        expected_line_count_delta: int | None = None,
        verify_contains: str | None = None,
    ) -> bool:
        """Auto-waits and types text, strictly verifying text appears in the buffer."""
        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        target = elems[0]
        return target.type_verified(
            text,
            expected_line_count_delta=expected_line_count_delta,
            verify_contains=verify_contains,
        )

    def fill(self, text: str, *, timeout: float = 10.0) -> bool:
        """Sets the entire value/text of the target element."""
        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        target = elems[0]
        return target.set_value(text)

    def check(self, *, timeout: float = 10.0) -> bool:
        """Ensures checkbox/radio is checked."""
        from wintegrate.controls import CheckBox

        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        cb = CheckBox(elems[0])
        return cb.set_checked_verified(True, timeout=timeout)

    def uncheck(self, *, timeout: float = 10.0) -> bool:
        """Ensures checkbox is unchecked."""
        from wintegrate.controls import CheckBox

        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        cb = CheckBox(elems[0])
        return cb.set_checked_verified(False, timeout=timeout)

    def is_checked(self, *, timeout: float = 2.0) -> bool:
        """Returns True if checkbox/radio is checked."""
        from wintegrate.controls import CheckBox

        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        cb = CheckBox(elems[0])
        return cb.is_checked()

    def is_visible(self) -> bool:
        """Returns True if matching element currently exists on screen."""
        elems = self._resolve_elements()
        return len(elems) > 0

    def is_enabled(self) -> bool:
        """Returns True if element is currently enabled."""
        elems = self._resolve_elements()
        if not elems:
            return False
        return elems[0].is_enabled()

    def text_content(self, *, timeout: float = 5.0) -> str:
        """Reads and returns the element's text content."""
        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        return elems[0].read_value().text or elems[0].name or ""

    def select_item(self, item_name: str, *, timeout: float = 10.0) -> bool:
        """Selects an item in a combobox, listbox, or tab."""
        from wintegrate.controls import ComboBox

        elems = self._wait_for_elements(timeout=timeout, min_count=1)
        cb = ComboBox(elems[0])
        return cb.select_item(item_name)
