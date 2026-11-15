"""Confirm or dismiss the context menu FL opens for a browser item (Windows).

FL's scripting API opens that menu (`ui.selectBrowserMenuItem`) but its keys never reach it, so
the menu window is addressed directly: selecting its first entry ("Send to selected channel or
focused plugin" for an effect, "Open in new channel" for an instrument) via click and Return.
The entries below the first ones (favourite, delete file) are never reached.
"""

import ctypes
import sys
import time

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_CANCELMODE = 0x001F
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
MK_LBUTTON = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
CLASS = "TQuickPopupMenuWindow"


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _lparam_xy(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _user32():
    windll = getattr(__import__("ctypes"), "windll", None) if sys.platform == "win32" else None
    if windll is None:
        return None
    u32 = windll.user32
    try:
        ws = u32.OpenWindowStationW("winsta0", False, 0x037F)
        if ws:
            u32.SetProcessWindowStation(ws)
        dt = u32.OpenDesktopW("default", 0, False, 0x01FF)
        if dt:
            u32.SetThreadDesktop(dt)
            if hasattr(u32, "CloseDesktop"):
                u32.CloseDesktop(dt)
        if ws and hasattr(u32, "CloseWindowStation"):
            u32.CloseWindowStation(ws)
    except Exception:
        pass
    return u32


def window() -> int:
    """The open context menu's window handle, or 0 (also 0 away from Windows)."""
    u32 = _user32()
    return int(u32.FindWindowW(CLASS, None)) if u32 is not None else 0


def wait_window(timeout: float = 0.6) -> int:
    """Wait for the context menu window to appear; 0 if it does not appear within timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        w = window()
        if w:
            return w
        time.sleep(0.04)
    return 0


def _rect(hwnd: int) -> tuple[int, int, int, int] | None:
    u32 = _user32()
    if u32 is None or not hwnd or not hasattr(u32, "GetWindowRect"):
        return None
    r = _RECT()
    try:
        if u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


def _click(x: int, y: int) -> None:
    u32 = _user32()
    if u32 is None or not hasattr(u32, "SetCursorPos") or not hasattr(u32, "mouse_event"):
        return
    try:
        u32.SetCursorPos(x, y)
        time.sleep(0.02)
        u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception:
        pass


def _closed(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not window():
            return True
        time.sleep(0.05)
    return not window()


def confirm() -> bool:
    """Press Return or click Item 1 in the open menu; True once the menu has closed."""
    u32 = _user32()
    hwnd = wait_window()
    if u32 is None or not hwnd:
        return False
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)
    r = _rect(hwnd)
    if r:
        cx = min(r[0] + 50, (r[0] + r[2]) // 2)
        cy = r[1] + 16
        _click(cx, cy)
    return _closed(1.5)


def dismiss() -> bool:
    """Close the open menu without choosing anything; True when no menu is left."""
    u32 = _user32()
    hwnd = window()
    if u32 is None or not hwnd:
        return True
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_ESCAPE, 0)
    r = _rect(hwnd)
    if r:
        _click(max(0, r[0] - 20), max(0, r[1] - 20))
    return _closed(1.0)
