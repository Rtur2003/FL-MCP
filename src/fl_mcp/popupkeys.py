"""Confirm or dismiss the context menu FL opens for a browser item (Windows).

FL's scripting API opens that menu (`ui.selectBrowserMenuItem`) but its keys never reach it, so
the menu window is addressed directly: a Return goes to its first entry ("Send to selected
channel or focused plugin" for an effect, "Open in new channel" for an instrument). Only Return
and Escape are ever sent. The entries below the first ones (favourite, delete file) are never
moved to: no arrow key is sent."""

import sys
import time

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
CLASS = "TQuickPopupMenuWindow"


def _user32():
    windll = getattr(__import__("ctypes"), "windll", None) if sys.platform == "win32" else None
    return windll.user32 if windll is not None else None


def window() -> int:
    """The open context menu's window handle, or 0 (also 0 away from Windows)."""
    u32 = _user32()
    return int(u32.FindWindowW(CLASS, None)) if u32 is not None else 0


def _send(vk: int) -> bool:
    u32, hwnd = _user32(), window()
    if u32 is None or not hwnd:
        return False
    u32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    u32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
    return True


def _closed(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not window():
            return True
        time.sleep(0.05)
    return not window()


def confirm() -> bool:
    """Press Return in the open menu (its first entry); True once the menu has closed."""
    return _send(VK_RETURN) and _closed(1.5)


def dismiss() -> bool:
    """Close the open menu without choosing anything; True when no menu is left."""
    if window():
        _send(VK_ESCAPE)
        return _closed(1.0)
    return True

