"""Direct Windows UI Automation (UIA) COM bindings without control-type wrapper hierarchy."""

from __future__ import annotations

import ctypes
import logging
import sys
import time

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
)
from wintegrate.interop import (
    WM_GETTEXT,
    WM_GETTEXTLENGTH,
    attach_to_input_desktop,
    send_char_input,
    send_mouse_click,
    user32,
)
from wintegrate.text import count_lines, normalize_line_endings

logger = logging.getLogger(__name__)

_uia = None

if comtypes is not None:
    # Ensure thread is attached to input desktop and COM is initialized
    attach_to_input_desktop()
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_MULTITHREADED = 0x2
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
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
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

    def click(self):
        """Clicks the center of this element using SendInput mouse simulation."""
        left, top, right, bottom = self.bounding_rectangle
        if right > left and bottom > top:
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            send_mouse_click(cx, cy)

    def set_focus(self, verify: bool = True, timeout: float = 2.0) -> bool:
        """
        Sets focus to this element via UIA SetFocus and physical center click fallback.
        """
        try:
            self._element.SetFocus()
        except Exception as exc:
            logger.debug(f"SetFocus raised ({type(exc).__name__}): {exc}")

        # Physical click fallback to claim true OS foreground focus
        self.click()

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
    ) -> UiaElement:
        """
        Finds a descendant matching ALL supplied criteria within a bounded timeout,
        with a RawViewWalker fallback for UWP/XAML islands.

        Criteria are combined with AND: `find_descendant(name_contains="Browse",
        control_type_id=50000)` returns a Button whose name contains "Browse", never
        merely the first Button in the tree. `name_contains` is a case-insensitive
        substring match on the element's Name only — UIA property conditions cannot
        express substring matching, so it is applied as a filter over the candidates
        selected by the other criteria.
        """
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

            if conditions or name_contains:
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

            # Fallback for UWP/XAML isolated controls via RawViewWalker
            if automation_id and name_contains is None:
                try:
                    walker = uia.RawViewWalker

                    def search_walker(node, depth=0):
                        if depth > 10 or not node:
                            return None
                        try:
                            if node.CurrentAutomationId == automation_id:
                                return node
                        except Exception:
                            pass
                        child = walker.GetFirstChildElement(node)
                        while child:
                            res = search_walker(child, depth + 1)
                            if res:
                                return res
                            child = walker.GetNextSiblingElement(child)
                        return None

                    raw_found = search_walker(self._element)
                    if raw_found:
                        return UiaElement(raw_found)
                except Exception:
                    pass

            time.sleep(0.1)

        raise ElementNotFoundError(
            f"Descendant not found (automation_id={automation_id}, name_contains={name_contains}, name_exact={name_exact})"
        )

    def get_value(self) -> str:
        """Reads element text via TextPattern, ValuePattern, or window text fallback."""
        try:
            text_pattern = self._element.GetCurrentPattern(UIA_TextPatternId)
            if text_pattern:
                text_pat = text_pattern.QueryInterface(IUIAutomationTextPattern)
                doc_range = text_pat.DocumentRange
                if doc_range:
                    txt = doc_range.GetText(-1)
                    if txt is not None:
                        return txt
        except Exception:
            pass

        try:
            val_pattern = self._element.GetCurrentPattern(UIA_ValuePatternId)
            if val_pattern:
                val_pat = val_pattern.QueryInterface(IUIAutomationValuePattern)
                val = val_pat.CurrentValue
                if val is not None:
                    return val
        except Exception:
            pass

        # Fallback: check window text if handle exists
        if self.handle:
            length = user32.SendMessageW(self.handle, WM_GETTEXTLENGTH, 0, 0)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(self.handle, WM_GETTEXT, length + 1, ctypes.byref(buf))
                return buf.value

        return self.name

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
    ) -> bool:
        """
        Sends hardware keypresses via send_char_input (SendInput KEYEVENTF_UNICODE),
        and asserts verified text mutation.
        """
        # Foreground contention (first-run popups, notification toasts) is usually
        # transient, so retry focus a few times before declaring a steal.
        focus_ok = False
        for _ in range(3):
            if self.set_focus(timeout=2.0):
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
