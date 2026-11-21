"""Background-safe FL Studio window capture & per-app audio session mute (Windows).

Ported from battle-tested studio capture workflows so users can record live FL Studio
playback (playlist/mixer/meters) in the background without stealing focus or muting
the user's own system audio:
  - Uses Windows Graphics Capture (`ffmpeg -f gfxcapture`) targeting `TFruityLoopsMainForm`.
  - Mutes ONLY `FL64.exe` via CoreAudio `ISimpleAudioVolume` (all other PC audio stays ON).
  - Keeps FL un-minimized at `HWND_BOTTOM` (`SWP_NOACTIVATE`) so DWM still renders frames.
  - Sends Play/Stop via `PostMessageW(hwnd, WM_KEYDOWN/WM_KEYUP, VK_SPACE)` with zero focus steal.
"""

from __future__ import annotations

import base64
import ctypes
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_HWND_BOTTOM = 1
_SW_SHOWNOACTIVATE = 4
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
_WM_KEYDOWN, _WM_KEYUP = 0x0100, 0x0101
_VK_SPACE = 0x20

_APP_MUTE_PS = r"""
param([string]$mode = "list", [string]$procName = "FL64")
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

public class FlMcpAppAudio {
  [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice {
    int Activate(
      ref Guid id, int clsCtx, IntPtr p,
      [MarshalAs(UnmanagedType.IUnknown)] out object o);
  }

  [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator {
    int r0();
    int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d);
  }

  [Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionManager2 {
    int r0(); int r1();
    int GetSessionEnumerator(out IAudioSessionEnumerator e);
  }

  [Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionEnumerator {
    int GetCount(out int count);
    int GetSession(int index, out IAudioSessionControl ctl);
  }

  [Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionControl { }

  [Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionControl2 {
    int r0(); int r1(); int r2(); int r3(); int r4(); int r5(); int r6(); int r7(); int r8();
    int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
    int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
    int GetProcessId(out uint pid);
  }

  [Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8")]
  [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface ISimpleAudioVolume {
    int SetMasterVolume(float level, ref Guid g);
    int GetMasterVolume(out float level);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool b, ref Guid g);
    int GetMute([MarshalAs(UnmanagedType.Bool)] out bool b);
  }

  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class Enumerator { }

  public static string Run(string procName, string mode) {
    var en = (IMMDeviceEnumerator)(new Enumerator());
    IMMDevice dev; en.GetDefaultAudioEndpoint(0, 1, out dev);
    Guid iid = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
    object o; dev.Activate(ref iid, 23, IntPtr.Zero, out o);
    var mgr = (IAudioSessionManager2)o;
    IAudioSessionEnumerator se; mgr.GetSessionEnumerator(out se);
    int n; se.GetCount(out n);
    int hit = 0;
    Guid g = Guid.Empty;
    for (int i = 0; i < n; i++) {
      IAudioSessionControl ctl; se.GetSession(i, out ctl);
      var c2 = ctl as IAudioSessionControl2;
      if (c2 == null) continue;
      uint pid; c2.GetProcessId(out pid);
      if (pid == 0) continue;
      try {
        var p = Process.GetProcessById((int)pid);
        if (string.Equals(p.ProcessName, procName, StringComparison.OrdinalIgnoreCase)) {
          var vol = ctl as ISimpleAudioVolume;
          if (vol != null && (mode == "on" || mode == "off")) {
            vol.SetMute(mode == "on", ref g);
            hit++;
          }
        }
      } catch { }
    }
    return "sessions=" + n + " hits=" + hit;
  }
}
'@
Write-Output ([FlMcpAppAudio]::Run($procName, $mode))
"""


def find_fl_window() -> tuple[int | None, str]:
    """Return `(hwnd, title)` for the visible `TFruityLoopsMainForm` window on Windows."""
    if sys.platform != "win32":
        return None, ""
    u32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lp):
        if u32.IsWindowVisible(hwnd):
            cls_buf = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(hwnd, cls_buf, 128)
            if cls_buf.value == "TFruityLoopsMainForm":
                n = u32.GetWindowTextLengthW(hwnd)
                t_buf = ctypes.create_unicode_buffer(max(1, n + 1))
                if n > 0:
                    u32.GetWindowTextW(hwnd, t_buf, n + 1)
                found.append((int(hwnd), t_buf.value))
        return True

    u32.EnumWindows(_cb, 0)
    return found[0] if found else (None, "")


def push_fl_behind(hwnd: int | None = None) -> bool:
    """Keep FL Studio un-minimized at HWND_BOTTOM without stealing foreground focus."""
    if sys.platform != "win32":
        return False
    if hwnd is None:
        hwnd, _ = find_fl_window()
    if not hwnd:
        return False
    u32 = ctypes.windll.user32
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    return bool(
        u32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(_HWND_BOTTOM),
            0,
            0,
            0,
            0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
        )
    )


def toggle_play_space(hwnd: int | None = None) -> bool:
    """Send Space key directly to FL Studio via PostMessageW without stealing focus."""
    if sys.platform != "win32":
        return False
    if hwnd is None:
        hwnd, _ = find_fl_window()
    if not hwnd:
        return False
    u32 = ctypes.windll.user32
    lp_down = 1 | (0x39 << 16)
    lp_up = ctypes.c_ssize_t(lp_down | (1 << 30) | (1 << 31)).value
    ok1 = bool(u32.PostMessageW(ctypes.c_void_p(hwnd), _WM_KEYDOWN, _VK_SPACE, lp_down))
    time.sleep(0.03)
    ok2 = bool(u32.PostMessageW(ctypes.c_void_p(hwnd), _WM_KEYUP, _VK_SPACE, lp_up))
    return ok1 and ok2


def _mute_command(mode: str, proc_name: str) -> list[str]:
    """PowerShell argv running the CoreAudio mute script (param block first, no temp file)."""
    if mode not in ("on", "off", "list"):
        raise ValueError("mute mode must be on, off or list")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", proc_name):
        raise ValueError(f"invalid process name {proc_name!r}")
    script = f"& {{ {_APP_MUTE_PS} }} '{mode}' '{proc_name}'"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def set_fl_app_mute(muted: bool, *, proc_name: str = "FL64") -> dict[str, Any]:
    """Mute/unmute ONLY FL64.exe's Windows audio session (leaves all other PC audio ON).

    FL creates its audio session lazily on first playback, so call this again right after
    starting playback (see `capture_fl_video`).
    """
    if sys.platform != "win32":
        return {"ok": False, "reason": "Windows CoreAudio only"}
    mode = "on" if muted else "off"
    res = subprocess.run(
        _mute_command(mode, proc_name),
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    out = (res.stdout or res.stderr or "").strip()
    return {"ok": res.returncode == 0, "muted": muted, "detail": out}


def current_desktop_name() -> str:
    """Return active Win32 desktop name for this thread (e.g. 'Default' or 'exebox-*')."""
    if sys.platform != "win32":
        return ""
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        h = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(128)
        u32.GetUserObjectInformationW(h, 2, buf, 256, None)
        return str(buf.value)
    except Exception:
        return ""


def foreground_hwnd() -> int | None:
    """The window currently in front (so it can be restored after FL is pushed behind)."""
    if sys.platform != "win32":
        return None
    h = ctypes.windll.user32.GetForegroundWindow()
    return int(h) if h else None


def restore_foreground(hwnd: int | None, *, skip: int | None = None) -> bool:
    """Bring `hwnd` back to the front unless it is FL itself (`skip`)."""
    if sys.platform != "win32" or not hwnd or hwnd == skip:
        return False
    return bool(ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd)))


def wait_fl_title(substr: str, timeout: float = 45.0) -> tuple[int | None, str]:
    """Poll until FL's main window title contains `substr` (the project finished loading)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        hwnd, title = find_fl_window()
        if hwnd and substr.lower() in title.lower():
            return hwnd, title
        time.sleep(0.8)
    return find_fl_window()



def build_gfxcapture_cmd(
    ffmpeg_exe: str,
    out_mp4: str | Path,
    duration_s: float,
    *,
    window_title: str = "FL Studio",
    fps: int = 30,
    crf: int = 16,
) -> list[str]:
    """Build the verified ffmpeg DWM gfxcapture filtergraph command (Windows)."""
    safe_title = window_title.replace(":", r"\:").replace("'", r"\'")
    vf = (
        f"gfxcapture=window_title={safe_title}:capture_cursor=0:"
        f"max_framerate={int(fps)},hwdownload,format=bgra,fps={int(fps)},format=yuv420p"
    )
    return [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-filter_complex",
        vf,
        "-t",
        f"{float(duration_s):.2f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(int(crf)),
        str(out_mp4),
    ]


def capture_fl_video(
    out_mp4: str | Path,
    duration_s: float,
    *,
    fps: int = 30,
    crf: int = 16,
    window_title: str = "FL Studio",
    mute_fl_only: bool = True,
    drive_playback: bool = True,
    ffmpeg_exe: str | None = None,
    project: str | Path | None = None,
    startup_s: float = 2.8,
) -> dict[str, Any]:
    """Capture FL Studio's live window in the background via ffmpeg gfxcapture (Windows).

    Optionally opens `project` first and waits for its title. FL64.exe alone is muted, and the
    mute is re-applied right after playback starts because FL creates its audio session lazily.
    Playback is toggled with a background Space message (never steals focus); the previous
    foreground window is restored. Raises if ffmpeg dies during start-up (playback never starts).
    """
    if sys.platform != "win32":
        raise ValueError("background FL window capture (gfxcapture) is supported on Windows only")
    ff = ffmpeg_exe or shutil.which("ffmpeg")
    if not ff:
        raise ValueError("ffmpeg not found on PATH (required for gfxcapture)")
    desktop = current_desktop_name()
    if desktop and desktop.lower() != "default":
        raise ValueError(
            f"FL Studio's window lives on the interactive desktop; this process runs on "
            f"'{desktop}'. Run the capture from your normal desktop session."
        )
    out = Path(out_mp4).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    prev_fg = foreground_hwnd()
    muted_applied = False

    def _mute(on: bool) -> None:
        nonlocal muted_applied
        if mute_fl_only and set_fl_app_mute(on).get("ok") and on:
            muted_applied = True

    _mute(True)
    try:
        if project is not None:
            from fl_mcp import render

            proj = Path(project).expanduser().resolve()
            render.launch_fl(proj)
            time.sleep(2.5)
            hwnd, title = wait_fl_title(proj.stem)
            if not hwnd or proj.stem.lower() not in title.lower():
                raise ValueError(f"FL Studio did not load {proj.name} (window title {title!r})")
            time.sleep(1.5)
        else:
            hwnd, title = find_fl_window()
        if not hwnd:
            raise ValueError("no visible FL Studio main window (TFruityLoopsMainForm) found")
        push_fl_behind(hwnd)
        restore_foreground(prev_fg, skip=hwnd)
        _mute(True)

        cmd = build_gfxcapture_cmd(
            ff, out, duration_s, window_title=window_title, fps=fps, crf=crf
        )
        t_ff = time.monotonic()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        playing = False
        try:
            time.sleep(startup_s)
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else "") or ""
                raise ValueError(
                    f"ffmpeg gfxcapture exited during start-up (code {proc.returncode}): "
                    f"{err[-300:]}"
                )
            play_offset = round(time.monotonic() - t_ff, 3)
            if drive_playback:
                playing = bool(toggle_play_space(hwnd))
                time.sleep(0.35)
                _mute(True)
                time.sleep(1.0)
                _mute(True)
            try:
                _, stderr = proc.communicate(timeout=float(duration_s) + 30.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise ValueError("ffmpeg gfxcapture did not finish in time") from None
        finally:
            if playing:
                toggle_play_space(hwnd)
            if proc.poll() is None:
                proc.kill()
        if proc.returncode != 0 or not out.is_file():
            raise ValueError(f"ffmpeg gfxcapture failed (code {proc.returncode}): {stderr[-300:]}")
        return {
            "video": str(out),
            "hwnd": hwnd,
            "title": title,
            "duration_s": round(float(duration_s), 2),
            "fps": int(fps),
            "play_offset_s": play_offset,
            "muted_fl_only": muted_applied,
            "bytes": out.stat().st_size,
        }
    finally:
        if muted_applied:
            set_fl_app_mute(False)
