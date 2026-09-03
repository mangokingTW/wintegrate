"""Direct Windows UI Automation (UIA) COM bindings without control-type wrapper hierarchy."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from typing import NamedTuple

if sys.platform == "win32":
    import comtypes
    import comtypes.client
else:
    comtypes = None

from wintegrate.exceptions import (
    ActionVerificationError,
    ElementNotFoundError,
    FocusStealDetectedError,
    TextMismatchError,
    ValueUnavailableError,
)
from wintegrate.interop import (
    WM_GETTEXT,
    WM_GETTEXTLENGTH,
    attach_to_input_desktop,
    ole32,
    send_char_input,
    send_keys,
    send_mouse_click,
    send_mouse_double_click,
    send_mouse_drag,
    send_mouse_middle_click,
    send_mouse_move,
    send_mouse_right_click,
    send_mouse_wheel,
    send_physical_keys,
    user32,
)
from wintegrate.text import count_lines, normalize_line_endings

logger = logging.getLogger(__name__)

# UIA control pattern ids. Declared here rather than imported from comtypes.gen so a
# comtypes build missing one name cannot take down the whole UIA initialization.
UIA_InvokePatternId = 10000
UIA_SelectionPatternId = 10001
UIA_ValuePatternId = 10002
UIA_RangeValuePatternId = 10003
UIA_ScrollPatternId = 10004
UIA_ExpandCollapsePatternId = 10005
UIA_GridPatternId = 10006
UIA_GridItemPatternId = 10007
UIA_SelectionItemPatternId = 10010
UIA_TablePatternId = 10012
UIA_TableItemPatternId = 10013
UIA_TogglePatternId = 10015
UIA_ScrollItemPatternId = 10017
UIA_ItemContainerPatternId = 10019
UIA_VirtualizedItemPatternId = 10020

UIA_TabControlTypeId = 50018
UIA_TabItemControlTypeId = 50019
UIA_MenuItemControlTypeId = 50011

TreeScope_Children = 2


class ValueReading(NamedTuple):
    """What an element's text read back as, and where it came from.

    `source` is one of `"TextPattern"`, `"ValuePattern"`, `"WM_GETTEXT"` or
    `"Name"`. The first three are the element's contents. `"Name"` is not — it is
    the element's label, reported only because there was nothing else to read,
    and it is the reason this type exists rather than a bare `str`.
    """

    text: str
    source: str


# UIA control types used to recognise grid and tree structure.
UIA_DataItemControlTypeId = 50029
UIA_TreeItemControlTypeId = 50024
UIA_HeaderItemControlTypeId = 50035

_uia = None

if comtypes is not None:
    # Ensure thread is attached to input desktop and COM is initialized
    attach_to_input_desktop()
    try:
        ole32.CoInitializeEx(None, 0x2)  # COINIT_MULTITHREADED = 0x2
    except Exception:
        pass

    # Load UIAutomationClient type library via comtypes
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            IUIAutomationElement,
            IUIAutomationInvokePattern,
            IUIAutomationTextPattern,
            IUIAutomationValuePattern,
            TreeScope_Descendants,
            UIA_InvokePatternId,
            UIA_TextPatternId,
            UIA_ValuePatternId,
        )

        _uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
    except Exception as exc:
        logger.warning(
            f"Failed to initialize UIAutomationCore COM module ({type(exc).__name__}): {exc}"
        )
        _uia = None


def get_uia() -> IUIAutomation:
    """Returns the process-wide IUIAutomation singleton."""
    global _uia
    if comtypes is None:
        raise RuntimeError(
            "wintegrate requires Windows: UI Automation (comtypes) is unavailable on this platform"
        )
    if _uia is None:
        attach_to_input_desktop()
        try:
            ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

        _uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
    return _uia


class UiaElement:
    """
    Thin, robust wrapper directly over IUIAutomationElement COM interface.
    No fragile UI control class hierarchies.
    """

    def __init__(self, element: IUIAutomationElement):
        if element is None:
            raise ValueError("IUIAutomationElement cannot be None")
        self._element = element

    def __repr__(self) -> str:
        """A one-line identity, so a failed assertion says what it was holding.

        Every field here is a cross-process read, so this stays cheap and
        tolerates failure: a repr that raises turns a useful assertion message
        into a traceback about formatting.
        """
        try:
            return (
                f"<UiaElement {self.control_type_name} name={self.name!r} "
                f"class={self.class_name!r} id={self.automation_id!r}>"
            )
        except Exception:
            return "<UiaElement (unreadable — the element has probably gone stale)>"

    def __eq__(self, other: object) -> bool:
        """Identity as UIA defines it, via CompareElements.

        Two COM pointers to the same UI element are not necessarily the same
        pointer value, so the default `==` answers False for elements that are the
        same thing. Only UIA can settle it.
        """
        if not isinstance(other, UiaElement):
            return NotImplemented
        try:
            return bool(get_uia().CompareElements(self._element, other._element))
        except Exception as exc:
            logger.debug(f"CompareElements failed ({type(exc).__name__}): {exc}")
            return False

    # Deliberately unhashable. An element is a mutable handle to something in
    # another process: it can go stale, and two unequal-looking elements can
    # become equal. Anything relying on a stable hash — a set, a dict key — would
    # be relying on a guarantee that does not exist, so fail loudly instead.
    __hash__ = None

    @property
    def name(self) -> str:
        try:
            return self._element.CurrentName or ""
        except Exception:
            return ""

    @property
    def automation_id(self) -> str:
        try:
            return self._element.CurrentAutomationId or ""
        except Exception:
            return ""

    @property
    def class_name(self) -> str:
        try:
            return self._element.CurrentClassName or ""
        except Exception:
            return ""

    @property
    def control_type_name(self) -> str:
        try:
            return self._element.CurrentLocalizedControlType or ""
        except Exception:
            return ""

    @property
    def control_type_id(self) -> int:
        try:
            return self._element.CurrentControlType or 0
        except Exception:
            return 0

    @property
    def handle(self) -> int:
        try:
            return self._element.CurrentNativeWindowHandle or 0
        except Exception:
            return 0

    @property
    def bounding_rectangle(self) -> tuple[int, int, int, int]:
        """Returns (left, top, right, bottom) bounding rectangle."""
        try:
            rect = self._element.CurrentBoundingRectangle
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return (0, 0, 0, 0)

    def is_enabled(self) -> bool:
        """Returns True if this element is enabled."""
        try:
            return bool(self._element.CurrentIsEnabled)
        except Exception:
            return True

    def is_visible(self) -> bool:
        """Returns True if this element is visible and not offscreen."""
        try:
            if not self._element.CurrentIsOffscreen:
                return True
        except Exception:
            pass
        left, top, right, bottom = self.bounding_rectangle
        return right > left and bottom > top

    @classmethod
    def from_handle(cls, hwnd: int) -> UiaElement:
        """Resolves an element directly from a native window handle."""
        uia = get_uia()
        elem = uia.ElementFromHandle(hwnd)
        if not elem:
            raise ElementNotFoundError(f"Cannot resolve UIA element from HWND: {hwnd}")
        return cls(elem)

    @classmethod
    def get_root(cls) -> UiaElement:
        """Resolves the Desktop RootElement directly."""
        uia = get_uia()
        elem = uia.GetRootElement()
        if not elem:
            raise ElementNotFoundError("Cannot resolve UIA Desktop RootElement")
        return cls(elem)

    @classmethod
    def get_focused(cls) -> UiaElement:
        """Resolves the currently focused UIA element."""
        uia = get_uia()
        elem = uia.GetFocusedElement()
        if not elem:
            raise ElementNotFoundError("Cannot resolve UIA FocusedElement")
        return cls(elem)

    def get_parent(self) -> UiaElement | None:
        """Navigates to the parent element using UIA ControlViewWalker."""
        try:
            uia = get_uia()
            walker = uia.ControlViewWalker
            parent = walker.GetParentElement(self._element)
            if parent:
                return UiaElement(parent)
        except Exception:
            pass
        return None

    def click(self, require_rectangle: bool = True):
        """Clicks the centre of this element with a synthesised mouse click.

        Raises when the element has no bounding rectangle, which is the whole
        point of this signature. A physical click needs a coordinate; an element
        that is scrolled out of view, not yet laid out, or hosted in a way that
        publishes no rectangle has none, and there is nothing sensible to click.

        This used to return quietly in that case, and the cost was consistently
        paid somewhere else: a WinUI flyout button whose rectangle is (0,0,0,0)
        looked like "focus never reached the rename box"; tree nodes scrolled
        out of a dialog's viewport looked like "none of the 27 pages had the
        control"; a list item on a smaller desktop looked like "the selection
        was stolen". One cause, three unrecognisable symptoms.

        Pass `require_rectangle=False` for the old behaviour where a missing
        rectangle is genuinely acceptable — and prefer `invoke()` where the
        element supports it, since an Invoke needs no coordinates at all.
        """
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_click(cx, cy)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point to click. It may be scrolled out of "
                "view, not laid out yet, or hosted somewhere that publishes no "
                "rectangle — try invoke(), or scroll_into_view() first."
            )
        return False

    def hover(self, require_rectangle: bool = True, steps: int = 1, delay: float = 0.0) -> bool:
        """Moves the mouse pointer to the centre of this element."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_move(cx, cy, steps=steps, delay=delay)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point to hover."
            )
        return False

    def middle_click(self, require_rectangle: bool = True) -> bool:
        """Clicks the centre of this element with a synthesised middle mouse click."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_middle_click(cx, cy)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point to middle click."
            )
        return False

    def right_click(self, require_rectangle: bool = True) -> bool:
        """Clicks the centre of this element with a synthesised right mouse click."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_right_click(cx, cy)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point to right click."
            )
        return False

    def double_click(self, require_rectangle: bool = True, interval: float = 0.05) -> bool:
        """Double clicks the centre of this element with synthesised left mouse clicks."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_double_click(cx, cy, interval=interval)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point to double click."
            )
        return False

    def mouse_wheel(self, delta: int, require_rectangle: bool = False) -> bool:
        """Sends a vertical mouse wheel event over the centre of this element."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_wheel(delta, cx, cy)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"{self} has an empty bounding rectangle ({left}, {top}, {right}, "
                f"{bottom}), so there is no point for mouse wheel."
            )
        send_mouse_wheel(delta)
        return True

    def drag_to(
        self,
        target: UiaElement,
        steps: int = 10,
        delay: float = 0.01,
        require_rectangle: bool = True,
    ) -> bool:
        """Smoothly drags from the centre of this element to the centre of target element."""
        s_left, s_top, s_right, s_bottom = self.bounding_rectangle
        t_left, t_top, t_right, t_bottom = target.bounding_rectangle

        if s_right > s_left and s_bottom > s_top and t_right > t_left and t_bottom > t_top:
            sx = (s_left + s_right) // 2
            sy = (s_top + s_bottom) // 2
            tx = (t_left + t_right) // 2
            ty = (t_top + t_bottom) // 2
            send_mouse_drag(sx, sy, tx, ty, steps=steps, delay=delay)
            return True

        if require_rectangle:
            raise ActionVerificationError(
                f"Cannot drag from {self} to {target}: one or both elements have empty bounding rectangles."
            )
        return False

    def locator(self, selector: str | dict):
        """Returns a Playwright-style Locator rooted at this element."""
        from wintegrate.locators import Locator

        if isinstance(selector, str):
            query_dict = {"name": selector}
        else:
            query_dict = selector

        def query(root: UiaElement) -> list[UiaElement]:
            return root.find_all(**query_dict)

        return Locator(lambda: self, query, description=f"{self} >> {selector}")

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False):
        """Finds descendant elements by role from this element."""
        from wintegrate.locators import Locator

        loc = Locator(lambda: self, lambda r: [self], description=str(self))
        return loc.get_by_role(role, name=name, exact=exact)

    def get_by_text(self, text: str, exact: bool = False):
        """Finds descendant elements matching text from this element."""
        from wintegrate.locators import Locator

        loc = Locator(lambda: self, lambda r: [self], description=str(self))
        return loc.get_by_text(text, exact=exact)

    def get_by_automation_id(self, auto_id: str):
        """Finds descendant elements by automation_id from this element."""
        from wintegrate.locators import Locator

        loc = Locator(lambda: self, lambda r: [self], description=str(self))
        return loc.get_by_automation_id(auto_id)

    def get_by_class(self, class_name: str):
        """Finds descendant elements by class name from this element."""
        from wintegrate.locators import Locator

        loc = Locator(lambda: self, lambda r: [self], description=str(self))
        return loc.get_by_class(class_name)

    def set_focus(self, verify: bool = True, timeout: float = 2.0, click: bool = True) -> bool:
        """
        Sets focus to this element via UIA SetFocus, then a physical centre click.

        The click is **not** a fallback, despite how this used to read: it happens
        whenever `click` is true, before anything is verified, because it is what
        makes focus reliable on controls that ignore UIA SetFocus. There is no cheap
        way to know in advance which those are.

        It is a real click, though. On a container it lands on whatever is at the
        centre, which can select or activate something, and a recording draws a
        marker for each one. Pass `click=False` when the focus change has to have no
        side effects, and check the return value: without the click, UIA SetFocus is
        the only thing that ran.
        """
        try:
            self._element.SetFocus()
        except Exception as exc:
            logger.debug(f"SetFocus raised ({type(exc).__name__}): {exc}")

        # Physical click fallback to claim true OS foreground focus. This is the
        # one place a missing rectangle is genuinely fine: SetFocus above may
        # already have worked, and an element with no rectangle is exactly the
        # kind this fallback cannot help anyway.
        if click:
            self.click(require_rectangle=False)

        if not verify:
            return True

        deadline = time.monotonic() + timeout
        uia = get_uia()
        while time.monotonic() < deadline:
            try:
                # Focus often lands on a descendant of the target (XAML/WinUI trees),
                # so walk the focused element's ancestors. CompareElements matches by
                # UIA runtime id, which works for elements with no native handle and
                # no automation id — no polling until timeout for those.
                node = UiaElement.get_focused()
                for _ in range(6):
                    if node is None:
                        break
                    try:
                        if uia.CompareElements(node._element, self._element):
                            return True
                    except Exception:
                        pass
                    if node.handle and self.handle and node.handle == self.handle:
                        return True
                    if (
                        node.automation_id
                        and self.automation_id
                        and node.automation_id == self.automation_id
                    ):
                        return True
                    node = node.get_parent()
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def find_descendant(
        self,
        automation_id: str | None = None,
        name_contains: str | None = None,
        name_exact: str | None = None,
        class_name: str | None = None,
        control_type_id: int | None = None,
        timeout: float = 5.0,
        required: bool = True,
    ) -> UiaElement | None:
        """
        Finds a descendant matching ALL supplied criteria within a bounded timeout,
        with a RawViewWalker fallback for UWP/XAML islands.

        Criteria are combined with AND: `find_descendant(name_contains="Browse",
        control_type_id=50000)` returns a Button whose name contains "Browse", never
        merely the first Button in the tree. `name_contains` is a case-insensitive
        substring match on the element's Name only — UIA property conditions cannot
        express substring matching, so it is applied as a filter over the candidates
        selected by the other criteria.

        With `required=False` the call returns None instead of raising when nothing
        matches — the presence check to use in place of a raise/except pair.
        """
        if not any((automation_id, name_exact, class_name, control_type_id, name_contains)):
            raise ValueError("At least one search criterion must be specified")

        deadline = time.monotonic() + timeout
        uia = get_uia()

        while time.monotonic() < deadline:
            conditions = []
            if automation_id:
                prop_id = 30011  # UIA_AutomationIdPropertyId
                cond = uia.CreatePropertyCondition(prop_id, automation_id)
                conditions.append(cond)
            if name_exact:
                prop_id = 30005  # UIA_NamePropertyId
                cond = uia.CreatePropertyCondition(prop_id, name_exact)
                conditions.append(cond)
            if class_name:
                prop_id = 30012  # UIA_ClassNamePropertyId
                cond = uia.CreatePropertyCondition(prop_id, class_name)
                conditions.append(cond)
            if control_type_id:
                prop_id = 30003  # UIA_ControlTypePropertyId
                cond = uia.CreatePropertyCondition(prop_id, control_type_id)
                conditions.append(cond)

            if not conditions:
                full_cond = uia.CreateTrueCondition()
            elif len(conditions) == 1:
                full_cond = conditions[0]
            else:
                full_cond = uia.CreateAndConditionFromArray(conditions)

            if name_contains is None:
                # Every criterion is expressible as a UIA condition.
                try:
                    found = self._element.FindFirst(TreeScope_Descendants, full_cond)
                    if found:
                        return UiaElement(found)
                except Exception:
                    pass
            else:
                # Enumerate the candidates the conditions already narrowed down,
                # then apply the substring filter on top (AND, not fallback).
                needle = name_contains.lower()
                try:
                    arr = self._element.FindAll(TreeScope_Descendants, full_cond)
                    if arr:
                        for i in range(arr.Length):
                            child = arr.GetElement(i)
                            try:
                                c_name = child.CurrentName or ""
                            except Exception:
                                continue
                            if needle in c_name.lower():
                                return UiaElement(child)
                except Exception:
                    pass

            time.sleep(0.1)

        # Single-pass fallback for UWP/XAML islands and nested Win32 child HWND boundaries via RawViewWalker
        try:
            walker = uia.RawViewWalker

            def matches_node(node) -> bool:
                try:
                    if automation_id and node.CurrentAutomationId != automation_id:
                        return False
                    if class_name and node.CurrentClassName != class_name:
                        return False
                    if control_type_id and node.CurrentControlType != control_type_id:
                        return False
                    if name_exact and (node.CurrentName or "") != name_exact:
                        return False
                    if (
                        name_contains
                        and name_contains.lower() not in (node.CurrentName or "").lower()
                    ):
                        return False
                    return True
                except Exception:
                    return False

            def search_walker(node, depth=0):
                if depth > 15 or not node:
                    return None
                try:
                    if not uia.CompareElements(node, self._element) and matches_node(node):
                        return node
                except Exception:
                    pass
                try:
                    child = walker.GetFirstChildElement(node)
                    while child:
                        res = search_walker(child, depth + 1)
                        if res:
                            return res
                        child = walker.GetNextSiblingElement(child)
                except Exception:
                    pass
                return None

            raw_found = search_walker(self._element)
            if raw_found:
                return UiaElement(raw_found)
        except Exception:
            pass

        if not required:
            return None
        raise ElementNotFoundError(
            f"Descendant not found (automation_id={automation_id}, name_contains={name_contains}, "
            f"name_exact={name_exact}, class_name={class_name}, control_type_id={control_type_id})"
        )

    def capture(self, path=None):
        """
        Captures the screen region this element occupies and returns a PIL Image.
        Saves to `path` when given.

        Cropped from a desktop capture rather than rendered: an element is not a
        window, so there is nothing to ask to draw itself.
        """
        from pathlib import Path as _Path

        from wintegrate.diagnostics import capture_screen_image
        from wintegrate.interop import SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN

        left, top, right, bottom = self.bounding_rectangle
        if right <= left or bottom <= top:
            raise ActionVerificationError(
                f"Element {self} has an empty bounding rectangle ({left},{top},{right},{bottom})"
            )
        x0 = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y0 = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        img = capture_screen_image(all_monitors=True).crop(
            (left - x0, top - y0, right - x0, bottom - y0)
        )
        if path is not None:
            path = _Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
            logger.info(f"Saved element capture to {path}")
        return img

    def exists(self) -> bool:
        """
        Returns whether this element is still alive in the UI tree.

        Reads a property off the live element: a destroyed or replaced element
        raises a COM error instead of answering, which is how staleness surfaces.
        """
        try:
            self._element.CurrentControlType
            return True
        except Exception:
            return False

    def children(self) -> list[UiaElement]:
        """Returns this element's direct children (control view)."""
        return self._collect(TreeScope_Children, get_uia().CreateTrueCondition())

    def find_all(
        self,
        automation_id: str | None = None,
        name_contains: str | None = None,
        name_exact: str | None = None,
        class_name: str | None = None,
        control_type_id: int | None = None,
    ) -> list[UiaElement]:
        """Returns every descendant matching ALL supplied criteria (empty list if none)."""
        uia = get_uia()
        conditions = []
        if automation_id:
            conditions.append(uia.CreatePropertyCondition(30011, automation_id))
        if name_exact:
            conditions.append(uia.CreatePropertyCondition(30005, name_exact))
        if class_name:
            conditions.append(uia.CreatePropertyCondition(30012, class_name))
        if control_type_id:
            conditions.append(uia.CreatePropertyCondition(30003, control_type_id))

        if not conditions:
            cond = uia.CreateTrueCondition()
        elif len(conditions) == 1:
            cond = conditions[0]
        else:
            cond = uia.CreateAndConditionFromArray(conditions)

        found = self._collect(TreeScope_Descendants, cond)
        if name_contains:
            needle = name_contains.lower()
            found = [el for el in found if needle in el.name.lower()]
        return found

    def _collect(self, scope, condition) -> list[UiaElement]:
        try:
            arr = self._element.FindAll(scope, condition)
        except Exception as exc:
            logger.debug(f"FindAll failed ({type(exc).__name__}): {exc}")
            return []
        if not arr:
            return []
        out = []
        for i in range(arr.Length):
            try:
                out.append(UiaElement(arr.GetElement(i)))
            except Exception:
                continue
        return out

    def _pattern(self, pattern_id: int, interface_name: str):
        """Resolves a UIA control pattern, or None when the element doesn't support it."""
        try:
            raw = self._element.GetCurrentPattern(pattern_id)
            if not raw:
                return None
            module = __import__("comtypes.gen.UIAutomationClient", fromlist=[interface_name])
            return raw.QueryInterface(getattr(module, interface_name))
        except Exception as exc:
            logger.debug(f"Pattern {interface_name} unavailable ({type(exc).__name__}): {exc}")
            return None

    # --- TogglePattern (checkboxes) ---

    @property
    def toggle_state(self) -> int | None:
        """0 = off, 1 = on, 2 = indeterminate; None when not a toggleable control."""
        pat = self._pattern(UIA_TogglePatternId, "IUIAutomationTogglePattern")
        if pat is None:
            return None
        try:
            return int(pat.CurrentToggleState)
        except Exception:
            return None

    def toggle(self) -> bool:
        """Advances the toggle state by one step. Returns False if unsupported."""
        pat = self._pattern(UIA_TogglePatternId, "IUIAutomationTogglePattern")
        if pat is None:
            return False
        try:
            pat.Toggle()
            return True
        except Exception:
            return False

    def set_toggle_verified(self, checked: bool, timeout: float = 2.0) -> bool:
        """
        Drives a checkbox to the requested state and confirms it landed there.

        Toggle() only cycles the state, so a control already in the target state
        must be left alone; tri-state controls may need more than one step.
        """
        want = 1 if checked else 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.toggle_state
            if state is None:
                raise ActionVerificationError(f"Element {self} does not support TogglePattern")
            if state == want:
                return True
            if not self.toggle():
                raise ActionVerificationError(f"Toggle() failed on {self}")
            time.sleep(0.1)
        raise ActionVerificationError(
            f"Toggle did not reach state {want} within {timeout}s (last={self.toggle_state})"
        )

    # --- SelectionItemPattern (list items, radio buttons, tabs) ---

    @property
    def is_selected(self) -> bool | None:
        pat = self._pattern(UIA_SelectionItemPatternId, "IUIAutomationSelectionItemPattern")
        if pat is None:
            return None
        try:
            return bool(pat.CurrentIsSelected)
        except Exception:
            return None

    def select_verified(self, timeout: float = 2.0) -> bool:
        """Selects this item and confirms the selection stuck."""
        pat = self._pattern(UIA_SelectionItemPatternId, "IUIAutomationSelectionItemPattern")
        if pat is None:
            raise ActionVerificationError(f"Element {self} does not support SelectionItemPattern")
        try:
            pat.Select()
        except Exception as exc:
            raise ActionVerificationError(f"Select() failed on {self}: {exc}") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_selected:
                return True
            time.sleep(0.05)
        raise ActionVerificationError(f"Element {self} did not become selected within {timeout}s")

    def get_selection(self) -> list[UiaElement]:
        """Returns the currently selected children of a selection container (list, combo)."""
        pat = self._pattern(UIA_SelectionPatternId, "IUIAutomationSelectionPattern")
        if pat is None:
            return []
        try:
            arr = pat.GetCurrentSelection()
        except Exception:
            return []
        if not arr:
            return []
        out = []
        for i in range(arr.Length):
            try:
                out.append(UiaElement(arr.GetElement(i)))
            except Exception:
                continue
        return out

    # --- ExpandCollapsePattern (combo boxes, tree items) ---

    @property
    def expand_collapse_state(self) -> int | None:
        """0 = collapsed, 1 = expanded, 2 = partially expanded, 3 = leaf; None if unsupported."""
        pat = self._pattern(UIA_ExpandCollapsePatternId, "IUIAutomationExpandCollapsePattern")
        if pat is None:
            return None
        try:
            return int(pat.CurrentExpandCollapseState)
        except Exception:
            return None

    def expand_verified(self, expand: bool = True, timeout: float = 2.0) -> bool:
        """Expands or collapses this element and confirms the state changed."""
        pat = self._pattern(UIA_ExpandCollapsePatternId, "IUIAutomationExpandCollapsePattern")
        if pat is None:
            raise ActionVerificationError(f"Element {self} does not support ExpandCollapsePattern")
        want = 1 if expand else 0
        try:
            pat.Expand() if expand else pat.Collapse()
        except Exception as exc:
            raise ActionVerificationError(
                f"{'Expand' if expand else 'Collapse'}() failed on {self}: {exc}"
            ) from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.expand_collapse_state
            # 3 = leaf node: nothing to expand, and that is not a failure.
            if state == want or state == 3:
                return True
            time.sleep(0.05)
        raise ActionVerificationError(
            f"Element {self} did not reach expand state {want} within {timeout}s"
        )

    # --- Virtualization countermeasures ---

    def scroll_into_view(self) -> bool:
        """
        Brings this element into its container's viewport via ScrollItemPattern.

        Returns False when the element does not support the pattern, which is not
        an error: a control that never scrolls has nothing to do here.
        """
        pat = self._pattern(UIA_ScrollItemPatternId, "IUIAutomationScrollItemPattern")
        if pat is None:
            return False
        try:
            pat.ScrollIntoView()
            return True
        except Exception as exc:
            logger.debug(f"ScrollIntoView failed on {self} ({type(exc).__name__}): {exc}")
            return False

    def realize(self) -> bool:
        """
        Materializes a virtualized item so it exists in the UIA tree.

        WPF and WinUI collections only create peers for the rows currently on
        screen; everything else is a placeholder that answers property queries but
        cannot be invoked or selected. Realize() forces creation. Returns False
        when the element is not virtualized — the common case, and not a problem.
        """
        pat = self._pattern(UIA_VirtualizedItemPatternId, "IUIAutomationVirtualizedItemPattern")
        if pat is None:
            return False
        try:
            pat.Realize()
            return True
        except Exception as exc:
            logger.debug(f"Realize failed on {self} ({type(exc).__name__}): {exc}")
            return False

    def ensure_available(self) -> UiaElement:
        """
        Makes this element ready to interact with: realize it if virtualized, then
        scroll it into view. Safe to call on any element; returns self for chaining.
        """
        self.realize()
        self.scroll_into_view()
        return self

    def find_item_by_property(
        self, property_id: int, value, start_after: UiaElement | None = None
    ) -> UiaElement | None:
        """
        Finds a child through ItemContainerPattern, which searches the container's
        own data rather than the visual tree.

        This is the way to reach an item that virtualization has kept out of the
        tree entirely: `find_descendant` cannot see it, because as far as UIA is
        concerned it does not exist yet.
        """
        pat = self._pattern(UIA_ItemContainerPatternId, "IUIAutomationItemContainerPattern")
        if pat is None:
            return None
        try:
            after = start_after._element if start_after else None
            found = pat.FindItemByProperty(after, property_id, value)
            return UiaElement(found) if found else None
        except Exception as exc:
            logger.debug(f"FindItemByProperty failed on {self} ({type(exc).__name__}): {exc}")
            return None

    # Pattern id -> name, for describing what an element actually supports.
    _PATTERN_NAMES = {
        10000: "Invoke",
        10001: "Selection",
        10002: "Value",
        10004: "Scroll",
        10005: "ExpandCollapse",
        10006: "Grid",
        10007: "GridItem",
        10010: "SelectionItem",
        10012: "Table",
        10013: "TableItem",
        10014: "Text",
        10015: "Toggle",
        10017: "ScrollItem",
        10019: "ItemContainer",
        10020: "VirtualizedItem",
    }

    def supported_patterns(self) -> list[str]:
        """
        Names the control patterns this element actually supports.

        For diagnosing "the control does not do what I expected": UIA providers
        differ between control versions and Windows builds, and a list of what is
        there beats guessing at what is missing.
        """
        found = []
        for pattern_id, name in self._PATTERN_NAMES.items():
            try:
                if self._element.GetCurrentPattern(pattern_id):
                    found.append(name)
            except Exception:
                continue
        return found

    def describe(self) -> str:
        """A one-line identity for error messages: what this element is and can do."""
        return (
            f"<{self.control_type_name or self.control_type_id} "
            f"class={self.class_name!r} name={self.name!r} "
            f"id={self.automation_id!r} patterns={self.supported_patterns()}>"
        )

    # --- Typed control views ---

    def as_data_grid(self):
        """Views this element as a DataGrid. Raises if it exposes no Grid pattern."""
        from wintegrate.controls import DataGrid

        return DataGrid(self)

    def as_tree_view(self):
        """Views this element as a TreeView."""
        from wintegrate.controls import TreeView

        return TreeView(self)

    def as_tree_item(self):
        """Views this element as a single TreeView item."""
        from wintegrate.controls import TreeViewItem

        return TreeViewItem(self)

    def send_physical_keys(
        self, text: str, delay_per_key: float = 0.03, click: bool = True
    ) -> bool:
        """
        Focuses this element and types `text` as physical (scan-code) key presses,
        which an active IME sees and composes.

        `type_verified` and `send_char_input` inject Unicode codepoints, which reach
        the control without passing through the IME — fine for getting characters in,
        useless when the IME itself is under test.

        `click=False` focuses without the physical click. Pass it when the caller has
        already focused deliberately, or when a click would be visible: a recording
        draws a marker for every one, and a run that types ten phrases otherwise
        collects ten markers nobody asked for.
        """
        self.set_focus(verify=False, click=click)
        time.sleep(0.05)
        return send_physical_keys(text, delay_per_key=delay_per_key)

    def send_keys(self, spec: str, delay_per_key: float = 0.02, click: bool = True) -> bool:
        """
        Focuses this element and sends a SendKeys-style spec ("{ENTER}", "^a", "{TAB 3}").

        Unlike type_verified this asserts no post-condition — the caller decides what
        the keys were supposed to achieve and verifies that.

        `click=False` focuses without the physical click; see `send_physical_keys`.
        """
        self.set_focus(verify=False, click=click)
        time.sleep(0.05)
        return send_keys(spec, delay_per_key=delay_per_key)

    def read_value(self) -> ValueReading:
        """
        Reads the element's text and reports **which source answered**.

        The sources are tried in order of authority: `TextPattern` (a document),
        `ValuePattern` (a value-bearing control), `WM_GETTEXT` (a native window),
        and finally `Name` — which is not the element's text at all but its
        *label*, and is reported as such rather than passed off as content.

        Never raises: a reading always comes back, and the `source` says how much
        it is worth. `get_value()` is the version that refuses the weak answer.
        """
        try:
            text_pattern = self._element.GetCurrentPattern(UIA_TextPatternId)
            if text_pattern:
                text_pat = text_pattern.QueryInterface(IUIAutomationTextPattern)
                doc_range = text_pat.DocumentRange
                if doc_range:
                    txt = doc_range.GetText(-1)
                    if txt is not None:
                        return ValueReading(txt, "TextPattern")
        except Exception:
            pass

        try:
            val_pattern = self._element.GetCurrentPattern(UIA_ValuePatternId)
            if val_pattern:
                val_pat = val_pattern.QueryInterface(IUIAutomationValuePattern)
                val = val_pat.CurrentValue
                if val is not None:
                    return ValueReading(val, "ValuePattern")
        except Exception:
            pass

        if self.handle:
            # A zero length is the answer "this window's text is empty", not
            # "the query did not work" — DefWindowProc answers WM_GETTEXTLENGTH
            # for every window. Treating 0 as a miss is what used to send an
            # empty native Edit on to the Name fallback and have it report its
            # label as its contents.
            length = user32.SendMessageW(self.handle, WM_GETTEXTLENGTH, 0, 0)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(self.handle, WM_GETTEXT, length + 1, ctypes.byref(buf))
                return ValueReading(buf.value, "WM_GETTEXT")
            return ValueReading("", "WM_GETTEXT")

        return ValueReading(self.name, "Name")

    def get_value(self, allow_name_fallback: bool = False) -> str:
        """
        Reads the element's text, refusing to return its Name in place of it.

        When nothing in the element can be asked what text it holds, the only
        thing left is the Name — the element's *label*. Returning that silently is
        how a caller ends up asserting against a placeholder: the field driving
        this change is a WinUI `TextBox` whose Name is `'n'` (from `{n}`), so an
        empty field read back as `'n'` and every "the field is not empty" check
        would have passed on nothing.

        `allow_name_fallback=True` opts back into it, for the controls where the
        Name genuinely *is* the displayed text — a ComboBox reflecting its
        selection, a grid cell — and `read_value()` gives the reading plus its
        source when the caller would rather decide for itself.

        Raises `ValueUnavailableError` when there is no text source. An empty
        string from a real source is returned as `''`, because that is an answer.
        """
        reading = self.read_value()
        if reading.source == "Name" and not allow_name_fallback:
            raise ValueUnavailableError(
                f"{self.describe()} exposes no text source (no TextPattern, no "
                f"ValuePattern, no window handle), so the only reading available "
                f"is its Name, {reading.text!r} — which is the element's label, "
                f"not its contents. Pass allow_name_fallback=True if the Name is "
                f"what you actually want here, or use read_value() to inspect the "
                f"source yourself."
            )
        return reading.text

    def set_value_verified(self, text: str, timeout: float = 3.0) -> bool:
        """Sets text via ValuePattern and verifies the value immediately."""
        try:
            val_pattern = self._element.GetCurrentPattern(UIA_ValuePatternId)
            if val_pattern:
                val_pat = val_pattern.QueryInterface(IUIAutomationValuePattern)
                val_pat.SetValue(text)
        except Exception as exc:
            raise ActionVerificationError(f"SetValue failed ({type(exc).__name__}): {exc}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cur = self.get_value()
            if normalize_line_endings(cur) == normalize_line_endings(text):
                return True
            time.sleep(0.05)

        raise TextMismatchError(
            f"Value verification failed. Expected '{text}', got '{self.get_value()}'"
        )

    def invoke(self, verify_closed: bool = False, timeout: float = 3.0) -> bool:
        """Triggers InvokePattern on the element."""
        try:
            inv_pattern = self._element.GetCurrentPattern(UIA_InvokePatternId)
            if not inv_pattern:
                raise ActionVerificationError("Element does not support InvokePattern")
            inv_pat = inv_pattern.QueryInterface(IUIAutomationInvokePattern)
            inv_pat.Invoke()
        except Exception as exc:
            raise ActionVerificationError(f"Invoke failed ({type(exc).__name__}): {exc}")

        if verify_closed and self.handle:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not user32.IsWindow(self.handle) or not user32.IsWindowVisible(self.handle):
                    return True
                time.sleep(0.05)
            raise ActionVerificationError(
                f"Window handle {self.handle} remained visible after invoke"
            )

        return True

    def type_verified(
        self,
        text: str,
        expected_line_count_delta: int = 0,
        verify_contains: str | None = None,
        delay_per_char: float = 0.03,
        timeout: float = 8.0,
        click: bool = True,
    ) -> bool:
        """
        Sends hardware keypresses via send_char_input (SendInput KEYEVENTF_UNICODE),
        and asserts verified text mutation.

        `click=False` focuses without the physical click; see `send_physical_keys`.
        Note that it applies to every retry below, so a caller that turns it off is
        relying on UIA focus working — which is the trade the click exists to avoid.
        """
        # Foreground contention (first-run popups, notification toasts) is usually
        # transient, so retry focus a few times before declaring a steal.
        focus_ok = False
        for _ in range(3):
            if self.set_focus(timeout=2.0, click=click):
                focus_ok = True
                break
            time.sleep(0.3)
        if not focus_ok:
            # Focus verification needs a native handle or an automation id to compare
            # against; without either it can never confirm, so falling through to text
            # verification is the only meaningful check available.
            if self.handle or self.automation_id:
                raise FocusStealDetectedError(
                    f"Failed to focus element {self} before sending keystrokes"
                )
            logger.warning(
                f"Focus verification is not possible for {self} (no native window handle "
                "or automation id); proceeding and relying on text verification"
            )
        time.sleep(0.1)

        initial_text = self.get_value()
        initial_lines = count_lines(initial_text) if initial_text else 1

        # Always verify the content, deriving the expectation from the typed text when
        # the caller gave none. A line-count delta on its own only proves that some
        # newlines arrived: mangled input (ARM64 runners repeat or drop characters
        # under load, e.g. "pywinauto" landing as "uuuuuuuto") still has the right
        # line count and would otherwise be reported as success.
        target_verify_contains = verify_contains
        if target_verify_contains is None:
            target_verify_contains = text.strip() if text.strip() else text

        # Containment alone passes vacuously whenever the target text already exists in
        # the buffer (replaying an action twice, or an explicit verify_contains matching
        # pre-existing content while every keystroke was dropped) — require a *new*
        # occurrence instead. A line-count delta is an independent, additional check.
        required_occurrences = None
        if target_verify_contains:
            norm_initial = normalize_line_endings(initial_text)
            required_occurrences = (
                norm_initial.count(normalize_line_endings(target_verify_contains)) + 1
            )

        # Send characters using SendInput
        for char in text:
            send_char_input(char)
            if delay_per_char > 0:
                time.sleep(delay_per_char)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current_text = self.get_value()
            current_lines = count_lines(current_text) if current_text else 1

            verified = True
            if expected_line_count_delta != 0:
                actual_delta = current_lines - initial_lines
                if actual_delta != expected_line_count_delta:
                    verified = False

            if target_verify_contains is not None:
                norm_current = normalize_line_endings(current_text)
                norm_expected = normalize_line_endings(target_verify_contains)
                if required_occurrences is not None:
                    if norm_current.count(norm_expected) < required_occurrences:
                        verified = False
                elif norm_expected not in norm_current:
                    verified = False

            if verified:
                return True

            time.sleep(0.1)

        # Fallback: if hardware keyboard input was dropped in CI virtualization, try ValuePattern
        try:
            val_pattern = self._element.GetCurrentPattern(UIA_ValuePatternId)
            if val_pattern:
                val_pat = val_pattern.QueryInterface(IUIAutomationValuePattern)
                val_pat.SetValue(initial_text + text)
                time.sleep(0.2)
                current_text = self.get_value()
                current_lines = count_lines(current_text) if current_text else 1
                fallback_verified = True
                if target_verify_contains is not None:
                    norm_current = normalize_line_endings(current_text)
                    norm_expected = normalize_line_endings(target_verify_contains)
                    if required_occurrences is not None:
                        if norm_current.count(norm_expected) < required_occurrences:
                            fallback_verified = False
                    elif norm_expected not in norm_current:
                        fallback_verified = False
                if expected_line_count_delta != 0:
                    if current_lines - initial_lines != expected_line_count_delta:
                        fallback_verified = False
                if fallback_verified:
                    return True
        except Exception:
            pass

        final_text = self.get_value()
        raise TextMismatchError(
            f"type_verified failed. Expected delta {expected_line_count_delta} lines (had {initial_lines}, got {count_lines(final_text)} lines), "
            f"contains='{target_verify_contains}'. Final buffer: '{final_text}'"
        )
