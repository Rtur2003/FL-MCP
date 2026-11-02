"""Windows desktop backend: windows, screenshots, keys and mouse through user32/gdi32 (ctypes).

Fixed-width field types are used on purpose so the structure sizes can be checked on any
64-bit platform (LONG is 4 bytes on Windows but 8 on Linux, so wintypes would differ)."""

import ctypes
import sys
import time
from typing import Any

from fl_mcp import imaging
from fl_mcp.desk import Backend, Combo, DeskError, Win

LONG, DWORD, WORD = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
MOUSEEVENTF = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
MOUSEEVENTF_WHEEL = 0x0800

VK = {
    "enter": 0x0D, "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}  # fmt: skip
EXTENDED = {"delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down"}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD))


class _Union(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", DWORD), ("u", _Union))


class RECT(ctypes.Structure):
    _fields_ = (("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG))


class POINT(ctypes.Structure):
    _fields_ = (("x", LONG), ("y", LONG))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", WORD),
        ("biBitCount", WORD),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    _fields_ = (("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 3))


def _bind(dll: Any, name: str, restype: Any, *argtypes: Any) -> Any:
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DeskError("the Windows backend only runs on Windows")
        user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32")
        kernel32, dwm = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("dwmapi")
        self._dpi_aware(user32)
        H, B, I = ctypes.c_void_p, ctypes.c_int, ctypes.c_int  # noqa: E741
        P = ctypes.POINTER
        enum_proc = ctypes.WINFUNCTYPE(B, H, ctypes.c_ssize_t)
        self._enum_proc = enum_proc
        self.EnumWindows = _bind(user32, "EnumWindows", B, enum_proc, ctypes.c_ssize_t)
        self.IsWindowVisible = _bind(user32, "IsWindowVisible", B, H)
        self.IsIconic = _bind(user32, "IsIconic", B, H)
        self.GetWindowTextW = _bind(user32, "GetWindowTextW", B, H, ctypes.c_wchar_p, B)
        self.GetWindowRect = _bind(user32, "GetWindowRect", B, H, P(RECT))
        self.GetWindowThreadProcessId = _bind(
            user32, "GetWindowThreadProcessId", DWORD, H, P(DWORD)
        )
        self.GetForegroundWindow = _bind(user32, "GetForegroundWindow", H)
        self.SetForegroundWindow = _bind(user32, "SetForegroundWindow", B, H)
        self.BringWindowToTop = _bind(user32, "BringWindowToTop", B, H)
        self.ShowWindow = _bind(user32, "ShowWindow", B, H, I)
        self.SendInput = _bind(user32, "SendInput", DWORD, DWORD, P(INPUT), I)
        self.SetCursorPos = _bind(user32, "SetCursorPos", B, I, I)
        self.GetCursorPos = _bind(user32, "GetCursorPos", B, P(POINT))
        self.GetSystemMetrics = _bind(user32, "GetSystemMetrics", I, I)
        self.MapVirtualKeyW = _bind(user32, "MapVirtualKeyW", DWORD, DWORD, DWORD)
        self.VkKeyScanW = _bind(user32, "VkKeyScanW", ctypes.c_short, ctypes.c_wchar)
        self.keybd_event = _bind(user32, "keybd_event", None, ctypes.c_ubyte, ctypes.c_ubyte,
                                 DWORD, ULONG_PTR)  # fmt: skip
        self.GetDC = _bind(user32, "GetDC", H, H)
        self.ReleaseDC = _bind(user32, "ReleaseDC", I, H, H)
        self.CreateCompatibleDC = _bind(gdi32, "CreateCompatibleDC", H, H)
        self.CreateCompatibleBitmap = _bind(gdi32, "CreateCompatibleBitmap", H, H, I, I)
        self.SelectObject = _bind(gdi32, "SelectObject", H, H, H)
        self.BitBlt = _bind(gdi32, "BitBlt", B, H, I, I, I, I, H, I, I, DWORD)
        self.GetDIBits = _bind(gdi32, "GetDIBits", I, H, H, ctypes.c_uint, ctypes.c_uint,
                               ctypes.c_void_p, P(BITMAPINFO), ctypes.c_uint)  # fmt: skip
        self.DeleteObject = _bind(gdi32, "DeleteObject", B, H)
        self.DeleteDC = _bind(gdi32, "DeleteDC", B, H)
        self.OpenProcess = _bind(kernel32, "OpenProcess", H, DWORD, B, DWORD)
        self.CloseHandle = _bind(kernel32, "CloseHandle", B, H)
        self.QueryFullProcessImageNameW = _bind(
            kernel32, "QueryFullProcessImageNameW", B, H, DWORD, ctypes.c_wchar_p, P(DWORD)
        )
        self.DwmGetWindowAttribute = _bind(dwm, "DwmGetWindowAttribute", I, H, DWORD,
                                           ctypes.c_void_p, DWORD)  # fmt: skip

    @staticmethod
    def _dpi_aware(user32: Any) -> None:
        try:
            ws = user32.OpenWindowStationW("winsta0", False, 0x037F)
            if ws:
                user32.SetProcessWindowStation(ws)
            dt = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if dt:
                user32.SetThreadDesktop(dt)
        except Exception:
            pass
        # physical pixels everywhere, so screenshots and click coordinates are the same space
        for attempt in (
            lambda: user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
            lambda: user32.SetProcessDPIAware(),
        ):
            try:
                if attempt():
                    return
            except (AttributeError, OSError):
                continue

    def _process(self, hwnd: int) -> str:
        pid = DWORD()
        self.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.OpenProcess(0x1000, 0, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = DWORD(520)
            if self.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
            return ""
        finally:
            self.CloseHandle(handle)

    def _rect(self, hwnd: int) -> tuple[int, int, int, int]:
        box = RECT()
        # the frame you see, without the invisible resize border Windows 10/11 adds
        if self.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(box), ctypes.sizeof(box)) != 0:
            self.GetWindowRect(hwnd, ctypes.byref(box))
        return box.left, box.top, box.right - box.left, box.bottom - box.top

    def _win(self, hwnd: int) -> Win:
        buf = ctypes.create_unicode_buffer(512)
        self.GetWindowTextW(hwnd, buf, 512)
        return Win(int(hwnd), buf.value, self._rect(hwnd), self._process(hwnd))

    def windows(self) -> list[Win]:
        found: list[Win] = []

        def visit(hwnd: int, _: int) -> int:
            if self.IsWindowVisible(hwnd):
                win = self._win(hwnd)
                if win.title and win.rect[2] > 1 and win.rect[3] > 1:
                    found.append(win)
            return 1

        self.EnumWindows(self._enum_proc(visit), 0)
        return found

    def foreground(self) -> Win | None:
        hwnd = self.GetForegroundWindow()
        return self._win(hwnd) if hwnd else None

    def focus(self, win: Win) -> bool:
        if self.IsIconic(win.handle):
            self.ShowWindow(win.handle, 9)  # SW_RESTORE
        # a tap on Alt lifts Windows' rule that only the foreground process may take focus
        self.keybd_event(0x12, 0, 0, 0)
        self.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        self.BringWindowToTop(win.handle)
        self.SetForegroundWindow(win.handle)
        time.sleep(0.15)
        now = self.GetForegroundWindow()
        return bool(now) and int(now) == win.handle

    def cursor(self) -> tuple[int, int]:
        point = POINT()
        self.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def screen(self) -> tuple[int, int, int, int]:
        """The virtual desktop: left, top, width, height."""
        return (
            self.GetSystemMetrics(76),
            self.GetSystemMetrics(77),
            self.GetSystemMetrics(78),
            self.GetSystemMetrics(79),
        )

    def capture(self, rect: tuple[int, int, int, int], max_width: int) -> tuple[bytes, int, int]:
        x, y, w, h = rect
        if w < 1 or h < 1:
            raise DeskError("nothing to capture: the area is empty")
        screen_dc = self.GetDC(None)
        mem_dc = self.CreateCompatibleDC(screen_dc)
        bitmap = self.CreateCompatibleBitmap(screen_dc, w, h)
        try:
            self.SelectObject(mem_dc, bitmap)
            # SRCCOPY | CAPTUREBLT: includes layered windows (plugin windows draw that way)
            if not self.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, 0x00CC0020 | 0x40000000):
                raise DeskError("Windows refused the screen capture (is the screen locked?)")
            info = BITMAPINFO()
            head = info.bmiHeader
            head.biSize, head.biWidth, head.biHeight = ctypes.sizeof(head), w, -h
            head.biPlanes, head.biBitCount, head.biCompression = 1, 32, 0
            buf = ctypes.create_string_buffer(w * h * 4)
            if self.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(info), 0) != h:
                raise DeskError("could not read the captured bitmap")
        finally:
            self.DeleteObject(bitmap)
            self.DeleteDC(mem_dc)
            self.ReleaseDC(None, screen_dc)
        rgb, w2, h2 = imaging.resize(imaging.bgra_to_rgb(buf.raw, w, h), w, h, max_width)
        return imaging.encode_png(rgb, w2, h2), w2, h2

    def _send(self, *events: INPUT) -> None:
        arr = (INPUT * len(events))(*events)
        if self.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
            raise DeskError(
                "Windows blocked the input (FL Studio may be running as administrator: "
                "start Claude as administrator too, or run FL normally)"
            )

    def _key_event(self, vk: int, up: bool, extended: bool) -> INPUT:
        flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        scan = self.MapVirtualKeyW(vk, 0)
        return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0))

    def _vk(self, key: str) -> tuple[int, bool, bool]:
        """(virtual key, needs shift, extended) for a key name or a single character."""
        if key in VK:
            return VK[key], False, key in EXTENDED
        if len(key) == 1:
            scan = self.VkKeyScanW(key)
            if scan == -1:
                raise DeskError(f"this keyboard layout has no key for '{key}'")
            return scan & 0xFF, bool(scan & 0x100), False
        raise DeskError(f"unknown key '{key}'")

    def key(self, combo: Combo) -> None:
        if any(m not in VK for m in combo.mods):
            raise DeskError(f"modifier not available here: {combo.mods}")
        keys = [(m, VK[m], False) for m in combo.mods]
        vk, shift, ext = self._vk(combo.key)
        if shift and "shift" not in combo.mods:
            keys.append(("shift", VK["shift"], False))
        keys.append((combo.key, vk, ext))
        for _, code, e in keys:
            self._send(self._key_event(code, False, e))
            time.sleep(0.012)
        for _, code, e in reversed(keys):
            self._send(self._key_event(code, True, e))
            time.sleep(0.012)

    def type(self, text: str) -> None:
        for ch in text:
            if ch in "\n\t":
                self.key(Combo((), "enter" if ch == "\n" else "tab"))
                continue
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                code = int.from_bytes(units[i : i + 2], "little")
                down = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
                up = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
                self._send(INPUT(type=INPUT_KEYBOARD, ki=down), INPUT(type=INPUT_KEYBOARD, ki=up))
            time.sleep(0.006)

    def _button(self, button: str, up: bool) -> INPUT:
        flag = MOUSEEVENTF[button][1 if up else 0]
        return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flag, 0, 0))

    def mouse(
        self,
        kind: str,
        x: int,
        y: int,
        x2: int | None = None,
        y2: int | None = None,
        amount: int = 0,
    ) -> None:
        self.SetCursorPos(x, y)
        time.sleep(0.04)
        if kind in ("click", "double", "right"):
            button = "right" if kind == "right" else "left"
            for _ in range(2 if kind == "double" else 1):
                self._send(self._button(button, False))
                time.sleep(0.03)
                self._send(self._button(button, True))
                time.sleep(0.06)
        elif kind == "drag" and x2 is not None and y2 is not None:
            self._send(self._button("left", False))
            for i in range(1, 11):
                self.SetCursorPos(x + (x2 - x) * i // 10, y + (y2 - y) * i // 10)
                time.sleep(0.015)
            self._send(self._button("left", True))
        elif kind == "scroll":
            wheel = MOUSEINPUT(0, 0, ctypes.c_uint32(amount * 120).value, MOUSEEVENTF_WHEEL, 0, 0)
            self._send(INPUT(type=INPUT_MOUSE, mi=wheel))
        elif kind != "move":
            raise DeskError(f"unknown mouse action '{kind}'")
