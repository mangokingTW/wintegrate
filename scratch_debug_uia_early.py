import ctypes
from ctypes import wintypes
import comtypes
import comtypes.client

user32 = ctypes.windll.user32

# In non-interactive or subprocess sessions, check thread desktop vs input desktop
desk_id = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
print(f"Current Thread Desktop: {desk_id}")

input_desk = user32.OpenInputDesktop(0, False, 0x01FF)
print(f"OpenInputDesktop: {input_desk}")

# Switch thread desktop before COM initialization
if input_desk:
    user32.SetThreadDesktop(input_desk)

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation, TreeScope_Children

uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
root = uia.GetRootElement()
cond = uia.CreateTrueCondition()
children = root.FindAll(TreeScope_Children, cond)
print(f"UIA Children on Input Desktop: {children.Length}")
for i in range(min(5, children.Length)):
    el = children.GetElement(i)
    try:
        print(f"  [{i}] HWND={el.CurrentNativeWindowHandle}, Name={repr(el.CurrentName)}, Class={repr(el.CurrentClassName)}")
    except Exception as e:
        print(f"  [{i}] Error: {e}")
