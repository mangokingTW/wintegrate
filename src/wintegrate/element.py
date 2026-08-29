"""Direct Windows UI Automation (UIA) COM bindings without control-type wrapper hierarchy."""

from __future__ import annotations

import ctypes
import logging
import time

import comtypes
import comtypes.client

from wintegrate.exceptions import (
    ActionVerificationError,
    ElementNotFoundError,
    TextMismatchError,
)
from wintegrate.interop import (
    WM_GETTEXT,
    WM_GETTEXTLENGTH,
    attach_to_input_desktop,
    send_char_input,
    user32,
)
from wintegrate.text import count_lines, normalize_line_endings

logger = logging.getLogger(__name__)

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
    if _uia is None:
        attach_to_input_desktop()
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

        _uia = comtypes.client.CreateObject(
            CUIAutomation, interface=IUIAutomation
        )
    return _uia


class UiaElement:
    """
    Direct wrapper around raw IUIAutomationElement.
    Exposes direct UIA patterns and properties without arbitrary control-type wrappers.
    """

    def __init__(self, raw_element: IUIAutomationElement):
        self._element = raw_element

    @property
    def raw(self) -> IUIAutomationElement:
        return self._element

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
    def handle(self) -> int:
        try:
            return self._element.CurrentNativeWindowHandle or 0
        except Exception:
            return 0

    @classmethod
    def from_handle(cls, hwnd: int) -> UiaElement:
        """Resolves an element directly from a native window handle."""
        uia = get_uia()
        elem = uia.ElementFromHandle(hwnd)
        if not elem:
            raise ElementNotFoundError(f"Failed to resolve UIA element from HWND {hwnd}")
        return cls(elem)

    @classmethod
    def get_focused(cls) -> UiaElement:
        """
        Retrieves the currently focused UIA element directly.
        Essential fallback for OOBE / CoreWindow webview containers where top-down
        Descendants enumeration fails.
        """
        uia = get_uia()
        elem = uia.GetFocusedElement()
        if not elem:
            raise ElementNotFoundError("No element currently has UIA focus")
        return cls(elem)

    def set_focus(self, verify: bool = True, timeout: float = 2.0) -> bool:
        """Sets focus to this element and optionally verifies it has focus."""
        try:
            self._element.SetFocus()
        except Exception as exc:
            logger.debug(f"SetFocus raised ({type(exc).__name__}): {exc}")

        if not verify:
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                focused = UiaElement.get_focused()
                if focused.handle and self.handle and focused.handle == self.handle:
                    return True
                if (
                    focused.automation_id
                    and focused.automation_id == self.automation_id
                ):
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def find_descendant(
        self,
        automation_id: str | None = None,
        name_contains: str | None = None,
        name_exact: str | None = None,
        timeout: float = 5.0,
    ) -> UiaElement:
        """Finds a descendant matching criteria within a bounded timeout."""
        deadline = time.monotonic() + timeout
        uia = get_uia()

        while time.monotonic() < deadline:
            conditions = []
            if automation_id:
                prop_id = 30011  # UIA_AutomationIdPropertyId
                cond = uia.CreatePropertyCondition(
                    prop_id, automation_id
                )
                conditions.append(cond)
            if name_exact:
                prop_id = 30005  # UIA_NamePropertyId
                cond = uia.CreatePropertyCondition(prop_id, name_exact)
                conditions.append(cond)

            if conditions:
                if len(conditions) == 1:
                    full_cond = conditions[0]
                else:
                    full_cond = uia.CreateAndConditionFromArray(conditions)
                try:
                    found = self._element.FindFirst(
                        TreeScope_Descendants, full_cond
                    )
                    if found:
                        return UiaElement(found)
                except Exception:
                    pass

            if name_contains:
                true_cond = uia.CreateTrueCondition()
                try:
                    arr = self._element.FindAll(
                        TreeScope_Descendants, true_cond
                    )
                    if arr:
                        for i in range(arr.Length):
                            child = arr.GetElement(i)
                            c_name = child.CurrentName or ""
                            if name_contains.lower() in c_name.lower():
                                return UiaElement(child)
                except Exception:
                    pass

            time.sleep(0.1)

        raise ElementNotFoundError(
            f"Descendant not found (automation_id={automation_id}, name_contains={name_contains}, name_exact={name_exact})"
        )

    def get_value(self) -> str:
        """Reads element text via ValuePattern, TextPattern, or window text fallback."""
        try:
            val_pattern = self._element.GetCurrentPattern(UIA_ValuePatternId)
            if val_pattern:
                val_pat = val_pattern.QueryInterface(IUIAutomationValuePattern)
                return val_pat.CurrentValue or ""
        except Exception:
            pass

        try:
            text_pattern = self._element.GetCurrentPattern(UIA_TextPatternId)
            if text_pattern:
                text_pat = text_pattern.QueryInterface(IUIAutomationTextPattern)
                doc_range = text_pat.DocumentRange
                if doc_range:
                    return doc_range.GetText(-1) or ""
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
            if cur == text:
                return True
            time.sleep(0.05)

        raise TextMismatchError(f"Value verification failed. Expected '{text}', got '{self.get_value()}'")

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
            raise ActionVerificationError(f"Window handle {self.handle} remained visible after invoke")

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
        Sends hardware keypresses via send_char_input (SendInput + WM_CHAR fallback),
        and asserts verified text mutation.
        """
        self.set_focus()
        time.sleep(0.2)

        initial_text = self.get_value()
        initial_lines = count_lines(initial_text)

        # Send characters with handle target fallback
        for char in text:
            send_char_input(char, hwnd_target=self.handle)
            if delay_per_char > 0:
                time.sleep(delay_per_char)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current_text = self.get_value()
            current_lines = count_lines(current_text)

            verified = True
            if expected_line_count_delta != 0:
                actual_delta = current_lines - initial_lines
                if actual_delta != expected_line_count_delta:
                    verified = False

            if verify_contains is not None:
                norm_current = normalize_line_endings(current_text)
                norm_expected = normalize_line_endings(verify_contains)
                if norm_expected not in norm_current:
                    verified = False

            if verified:
                return True

            time.sleep(0.1)

        final_text = self.get_value()
        raise TextMismatchError(
            f"type_verified failed. Expected delta {expected_line_count_delta} lines (had {initial_lines}, got {count_lines(final_text)} lines), "
            f"contains='{verify_contains}'. Final buffer: '{final_text}'"
        )
