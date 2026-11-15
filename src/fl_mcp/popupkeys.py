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


def _post_client_click(u32: Any, hwnd: int, rel_x: int, rel_y: int) -> None:
    """Deliver a click in client coordinates via PostMessage without touching the cursor."""
    lp = _lparam_xy(rel_x, rel_y)
    try:
        u32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)
        u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.02)
        u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    except Exception:
        pass


def _click(x: int, y: int) -> None:
    u32 = _user32()
    if u32 is None or not hasattr(u32, "SetCursorPos") or not hasattr(u32, "mouse_event"):
        return
    saved = _POINT()
    had_pos = bool(hasattr(u32, "GetCursorPos") and u32.GetCursorPos(ctypes.byref(saved)))
    try:
        u32.SetCursorPos(x, y)
        time.sleep(0.02)
        u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception:
        pass
    finally:
        if had_pos:
            try:
                u32.SetCursorPos(saved.x, saved.y)
            except Exception:
                pass


def _closed(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not window():
            return True
        time.sleep(0.05)
    return not window()


def confirm(allow_cursor_fallback: bool = False) -> bool:
    """Accept Item 1 of the open menu via Return / cursor-less click before touching the cursor."""
    u32 = _user32()
    hwnd = wait_window()
    if u32 is None or not hwnd:
        return False
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)
    if _closed(0.35):
        return True
    r = _rect(hwnd)
    if r:
        rel_x = min(50, max(8, (r[2] - r[0]) // 2))
        rel_y = min(16, max(4, (r[3] - r[1]) // 2))
        _post_client_click(u32, hwnd, rel_x, rel_y)
        if _closed(0.35):
            return True
        if allow_cursor_fallback:
            cx = min(r[0] + 50, (r[0] + r[2]) // 2)
            cy = r[1] + 16
            _click(cx, cy)
            return _closed(0.9)
    return not window()


def dismiss(allow_cursor_fallback: bool = False) -> bool:
    """Close the open menu without choosing anything; avoids stealing the cursor by default."""
    u32 = _user32()
    hwnd = window()
    if u32 is None or not hwnd:
        return True
    u32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
    u32.PostMessageW(hwnd, WM_KEYUP, VK_ESCAPE, 0)
    if _closed(0.30):
        return True
    try:
        u32.PostMessageW(hwnd, WM_CANCELMODE, 0, 0)
    except Exception:
        pass
    if _closed(0.30):
        return True
    r = _rect(hwnd)
    if r and allow_cursor_fallback:
        _click(max(0, r[0] - 20), max(0, r[1] - 20))
        return _closed(0.6)
    return not window()
