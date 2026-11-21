"""Render a project to audio with FL Studio's own command line (Windows: FL64.exe /R /E<format>).

FL is started with the project, renders it to a file next to it, and the file is waited for. If
FL is already running it may hand the project to that instance; the answer then comes later or
not at all, which is reported rather than hidden."""

import contextlib
import ctypes
import itertools
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Any

from fl_mcp import paths

FORMATS = ("wav", "mp3", "ogg", "flac")
EXPORT_FORMATS = (*FORMATS, "mid", "zip")
_RENDER_DIR_LOCK = threading.Lock()
_RENDER_PREFS_LOCK = threading.Lock()
_RENDER_SEQ = itertools.count(1)
_DESK_SEQ = itertools.count(1)
_TAIL_MODES = {"leave": 0, "remainder": 0, "wrap": 1, "cut": 2}
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_SW_SHOWNORMAL = 1
_CREATE_NO_WINDOW = 0x08000000
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_DESKTOP_ALL = 0x000F01FF
_WAIT_OBJECT_0 = 0x00000000


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _DesktopProcess:
    """Handle to a Windows process running inside a private hidden desktop."""

    def __init__(self, hproc: int, hthread: int, hdesk: int, desktop_name: str) -> None:
        self.hproc = hproc
        self.hthread = hthread
        self.hdesk = hdesk
        self.desktop_name = desktop_name
        self.returncode: int | None = None
        self._closed = False

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        k32 = ctypes.windll.kernel32
        if k32.WaitForSingleObject(self.hproc, 0) == _WAIT_OBJECT_0:
            code = wintypes.DWORD(0)
            if k32.GetExitCodeProcess(self.hproc, ctypes.byref(code)):
                self.returncode = int(code.value)
            else:
                self.returncode = 0
            self.close()
        return self.returncode

    def terminate(self) -> None:
        if not self._closed and self.returncode is None:
            try:
                ctypes.windll.kernel32.TerminateProcess(self.hproc, 1)
            except Exception:
                pass
        self.close()

    def close(self, *, wait_ms: int = 2000) -> None:
        if self._closed:
            return
        self._closed = True
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        if self.hproc and self.returncode is None and wait_ms > 0:
            try:
                k32.WaitForSingleObject(self.hproc, int(wait_ms))
            except Exception:
                pass
        for h in (self.hthread, self.hproc):
            if h:
                try:
                    k32.CloseHandle(h)
                except Exception:
                    pass
        if self.hdesk:
            try:
                u32.CloseDesktop(self.hdesk)
            except Exception:
                pass


def _hidden_startupinfo(desktop: str | None = None) -> _STARTUPINFOW:
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    si.dwFlags = _STARTF_USESHOWWINDOW
    si.wShowWindow = _SW_HIDE
    if desktop:
        si.lpDesktop = desktop
    return si


def hidden_popen(cmd: str, *, isolated_desktop: bool = True) -> Any:
    """Spawn `cmd` without showing a window or flashing on the user's active desktop."""
    if sys.platform != "win32":
        return subprocess.Popen(shlex.split(cmd))
    if isolated_desktop:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        desk_name = f"FLMCP_Render_{os.getpid()}_{threading.get_ident()}_{next(_DESK_SEQ)}"
        hdesk = u32.CreateDesktopW(desk_name, None, None, 0, _DESKTOP_ALL, None)
        if hdesk:
            si = _hidden_startupinfo(desk_name)
            pi = _PROCESS_INFORMATION()
            buf = ctypes.create_unicode_buffer(cmd)
            ok = k32.CreateProcessW(
                None,
                buf,
                None,
                None,
                False,
                _CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY_CLASS,
                None,
                None,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if ok:
                return _DesktopProcess(int(pi.hProcess), int(pi.hThread), int(hdesk), desk_name)
            try:
                u32.CloseDesktop(hdesk)
            except Exception:
                pass
    si_sub = subprocess.STARTUPINFO()
    si_sub.dwFlags = _STARTF_USESHOWWINDOW
    si_sub.wShowWindow = _SW_HIDE
    return subprocess.Popen(
        cmd,
        startupinfo=si_sub,
        creationflags=_CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY_CLASS,
    )


def launch_fl(
    project: str | Path | None = None,
    *,
    exe: Path | None = None,
    desktop: str = r"Winsta0\Default",
) -> dict[str, Any]:
    """Launch FL Studio on the interactive desktop (safe from sandboxed exebox-* desktops)."""
    target_flp: Path | None = None
    if project is not None:
        target_flp = Path(project).expanduser().resolve()
        if target_flp.suffix.lower() != ".flp" or not target_flp.is_file():
            raise ValueError(f"not an existing .flp project: {target_flp}")
    if sys.platform == "darwin":
        cmd = ["open", "-a", "FL Studio", *([str(target_flp)] if target_flp else [])]
        subprocess.Popen(cmd)
        return {
            "launched": True,
            "platform": "darwin",
            "project": str(target_flp) if target_flp else None,
        }
    fl_exe = exe or find_fl()
    if fl_exe is None:
        raise ValueError("FL Studio executable not found: set FL_MCP_FL_DIR to its folder")
    if sys.platform == "win32":
        cmdline = f'"{fl_exe}"' + (f' "{target_flp}"' if target_flp else "")
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        si.dwFlags = _STARTF_USESHOWWINDOW
        si.wShowWindow = _SW_SHOWNORMAL
        si.lpDesktop = desktop
        pi = _PROCESS_INFORMATION()
        k32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(cmdline)
        ok = k32.CreateProcessW(
            None, buf, None, None, False, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
        )
        if not ok:
            err = k32.GetLastError()
            raise ValueError(f"failed to launch FL Studio on {desktop} (Win32 error {err})")
        pid = int(pi.dwProcessId)
        for h in (pi.hThread, pi.hProcess):
            if h:
                try:
                    k32.CloseHandle(h)
                except Exception:
                    pass
        return {
            "launched": True,
            "pid": pid,
            "exe": str(fl_exe),
            "desktop": desktop,
            "project": str(target_flp) if target_flp else None,
        }
    proc = subprocess.Popen([str(fl_exe), *([str(target_flp)] if target_flp else [])])
    return {
        "launched": True,
        "pid": proc.pid,
        "exe": str(fl_exe),
        "project": str(target_flp) if target_flp else None,
    }


def find_fl() -> Path | None:
    for base in paths.fl_install_dirs():
        for name in ("FL64.exe", "FL.exe"):
            if (base / name).is_file():
                return base / name
    return None


def project_dirs() -> list[Path]:
    extra = [
        Path(p).expanduser() for p in os.environ.get("FL_MCP_PROJECTS", "").split(os.pathsep) if p
    ]
    return [*extra, paths.fl_user_dir() / "Projects", paths.fl_user_dir() / "Backup"]


def newest_project(within_s: float = 120.0, now: float | None = None) -> Path | None:
    """The .flp saved most recently (FL writes it on save), to find the current project."""
    now = time.time() if now is None else now
    best: tuple[float, Path] | None = None
    for base in project_dirs():
        if not base.is_dir():
            continue
        for f in base.rglob("*.flp"):
            try:
                age = now - f.stat().st_mtime
            except OSError:
                continue
            if age <= within_s and (best is None or f.stat().st_mtime > best[0]):
                best = (f.stat().st_mtime, f)
    return best[1] if best else None


AUDIO = (".wav", ".flac", ".mp3", ".ogg")


def newest_audio(within_s: float = 1800.0, now: float | None = None) -> Path | None:
    """The audio file written most recently in FL's project folders (an export just made)."""
    now = time.time() if now is None else now
    best: tuple[float, Path] | None = None
    seen = 0
    for base in project_dirs():
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            seen += 1
            if seen > 50000:
                break
            if f.suffix.lower() not in AUDIO:
                continue
            try:
                stamp = f.stat().st_mtime
            except OSError:
                continue
            if now - stamp <= within_s and (best is None or stamp > best[0]):
                best = (stamp, f)
    return best[1] if best else None


def status() -> dict[str, Any]:
    exe = find_fl()
    project = newest_project()
    return {
        "platform": sys.platform,
        "fl_exe": str(exe) if exe else None,
        "command_line_render": exe is not None,
        "recent_project": str(project) if project else None,
    }


def _siblings(project: Path) -> list[Path]:
    """Other projects FL's folder render would also pick up (its Backup folder is skipped)."""
    out = []
    for f in project.parent.rglob("*.flp"):
        inside = {part.lower() for part in f.relative_to(project.parent).parts[:-1]}
        if f != project and "backup" not in inside:
            out.append(f)
    return out


def command_line(exe: Path, fmt: str, source: Path, out: Path) -> str:
    """FL's documented batch render: /R renders, /E<fmt> or /M (MIDI) or /Z (zipped package)."""
    if fmt == "mid":
        mode = "/M"
    elif fmt == "zip":
        mode = "/Z"
    else:
        mode = f"/E{fmt}"
    return f'"{exe}" /R {mode} /F"{source}" /O"{out}"'


def _alloc_render_dir(root: Path, ts: int) -> tuple[Path, Path]:
    with _RENDER_DIR_LOCK:
        base = root / str(ts)
        while base.exists():
            base = root / f"{ts}-{next(_RENDER_SEQ)}"
        out = base / "audio"
        out.mkdir(parents=True, exist_ok=True)
        return base, out


def cleanup_renders(
    *, max_keep: int = 10, max_age_hours: float = 24.0, workdir: Path | None = None
) -> dict[str, Any]:
    """Prune old temporary render directories under ~/.fl-mcp/renders to avoid disk bloat."""
    root = workdir or paths.data_dir() / "renders"
    if not root.is_dir():
        return {"removed": 0, "freed_bytes": 0}
    now = time.time()
    dirs = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = freed = 0
    for idx, d in enumerate(dirs):
        try:
            age_h = (now - d.stat().st_mtime) / 3600.0
            if idx < max_keep and age_h < max_age_hours:
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
            freed += size
        except OSError:
            continue
    return {"removed": removed, "freed_bytes": freed}


def _measure_start_offset_s(audio_path: Path, threshold_db: float = -60.0) -> float | None:
    """Measure leading silence / PDC lag (seconds) cleanly when audio libs are available."""
    try:
        from fl_mcp import audio

        if not audio.available() or audio_path.suffix.lower() not in AUDIO:
            return None
        np, _, _ = audio._libs()
        x, fs = audio.load(audio_path)
        if len(x) == 0 or fs <= 0:
            return None
        thresh = 10.0 ** (threshold_db / 20.0)
        peak_per_frame = np.max(np.abs(x), axis=1)
        hits = np.flatnonzero(peak_per_frame >= thresh)
        if len(hits) == 0:
            return round(len(x) / float(fs), 3)
        return round(int(hits[0]) / float(fs), 3)
    except Exception:
        return None


def _find_stems(folder: Path, stem: str, fmt: str, started: float) -> list[Path]:
    if not folder.is_dir():
        return []
    hits = [
        f
        for f in sorted(folder.glob(f"{stem}_*.{fmt}"))
        if f.is_file() and f.stat().st_mtime >= started - 1
    ]
    return hits


@contextlib.contextmanager
def _fl_render_prefs(
    *,
    split_mixer: bool | None = None,
    tail: str | None = None,
    trim_pdc: bool | None = None,
    save_markers: bool | None = None,
    save_tempo: bool | None = None,
) -> Iterator[bool]:
    """Temporarily override FL Studio's Windows `General\\Render (master)` registry prefs.

    Enables true headless `split_mixer_tracks=True` (and tail/PDC/marker options) for `FL64.exe /R`
    without GUI clicks, then restores the user's exact prior registry values in `finally:`.
    """
    updates: dict[str, int] = {}
    if split_mixer is not None:
        updates["Split mixer tracks"] = 1 if split_mixer else 0
    if tail is not None:
        key = tail.strip().lower()
        if key not in _TAIL_MODES:
            raise ValueError(f"tail must be one of {', '.join(_TAIL_MODES)}")
        updates["Tail"] = _TAIL_MODES[key]
    if trim_pdc is not None:
        updates["Trim PDC silence"] = 1 if trim_pdc else 0
    if save_markers is not None:
        updates["Save playlist markers"] = 1 if save_markers else 0
    if save_tempo is not None:
        updates["Save tempo"] = 1 if save_tempo else 0

    if not updates or sys.platform != "win32":
        yield False
        return

    try:
        import winreg
    except ImportError:
        yield False
        return

    ver_keys = paths.fl_registry_version_keys()
    if not ver_keys:
        yield False
        return

    subkey = f"{ver_keys[0]}\\General\\Render (master)"
    saved: dict[str, tuple[Any, int] | None] = {}
    with _RENDER_PREFS_LOCK:
        applied = False
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE
            ) as k:
                for name, val in updates.items():
                    try:
                        old_v, old_t = winreg.QueryValueEx(k, name)
                        saved[name] = (old_v, old_t)
                        # Preserve FL Studio's existing registry type (REG_SZ vs REG_DWORD)
                        out_v: Any = str(val) if old_t == winreg.REG_SZ else int(val)
                        winreg.SetValueEx(k, name, 0, old_t, out_v)
                    except FileNotFoundError:
                        saved[name] = None
                        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(val))
                applied = True
        except OSError:
            applied = False
        try:
            yield applied
        finally:
            if applied:
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_WRITE
                    ) as k:
                        for name, prev in saved.items():
                            if prev is None:
                                with contextlib.suppress(OSError):
                                    winreg.DeleteValue(k, name)
                            else:
                                winreg.SetValueEx(k, name, 0, prev[1], prev[0])
                except OSError:
                    pass


def render(
    flp: str | Path,
    fmt: str = "wav",
    timeout: float = 900.0,
    exe: Path | None = None,
    popen: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
    workdir: Path | None = None,
    *,
    isolate: bool = True,
    always_copy: bool = False,
    out_dir: str | Path | None = None,
    keep: bool = True,
    split_mixer: bool = False,
    tail: str | None = None,
    trim_pdc: bool | None = None,
) -> dict[str, Any]:
    """Render one saved project with FL's command line (isolated hidden desktop on Windows)."""
    project = Path(flp).expanduser().resolve()
    if project.suffix.lower() != ".flp" or not project.is_file():
        raise ValueError(f"not an existing .flp project: {project}")
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"format must be one of {', '.join(EXPORT_FORMATS)}")
    exe = exe or find_fl()
    if exe is None:
        why = (
            "command line rendering is only supported on Windows"
            if sys.platform != "win32"
            else ("FL Studio's FL64.exe was not found: set FL_MCP_FL_DIR to its folder")
        )
        raise ValueError(f"cannot render from the command line: {why}")
    base, temp_out = _alloc_render_dir(workdir or paths.data_dir() / "renders", int(wall()))
    out = Path(out_dir).expanduser().resolve() if out_dir is not None else temp_out
    out.mkdir(parents=True, exist_ok=True)
    source, copied = project.parent, False
    if always_copy or _siblings(project):
        source, copied = base / "project", True
        source.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project, source / project.name)
    started = wall()
    cmd = command_line(exe, fmt, source, out)
    # Apply temporary Windows registry overrides only for real FL launches (never mock popen)
    want_prefs = popen is None and (split_mixer or tail is not None or trim_pdc is not None)
    prefs_ctx = (
        _fl_render_prefs(
            split_mixer=True if split_mixer else None, tail=tail, trim_pdc=trim_pdc
        )
        if want_prefs
        else contextlib.nullcontext(False)
    )
    with prefs_ctx as prefs_applied:
        if popen is not None:
            proc = popen(cmd)
            isolated = False
        elif sys.platform == "win32" and isolate:
            proc = hidden_popen(cmd, isolated_desktop=True)
            isolated = isinstance(proc, _DesktopProcess)
        elif sys.platform == "win32":
            proc = hidden_popen(cmd, isolated_desktop=False)
            isolated = False
        else:
            proc = subprocess.Popen(shlex.split(cmd))
            isolated = False
        wanted = [
            out / f"{project.stem}.{fmt}",
            project.with_suffix(f".{fmt}"),
        ]  # /O, or beside it
        deadline, last_size, stable = clock() + timeout, -1, 0
        finished = False
        exited_grace = 0
        try:
            while clock() < deadline:
                sleep(1.0)
                found = next(
                    (f for f in wanted if f.is_file() and f.stat().st_mtime >= started - 1),
                    None,
                )
                stems: list[Path] = []
                if found is None or split_mixer:
                    stems = _find_stems(out, project.stem, fmt, started) or _find_stems(
                        project.parent, project.stem, fmt, started
                    )
                    if found is None and stems:
                        found = next(
                            (s for s in stems if s.stem.lower().endswith("_master")),
                            stems[0],
                        )
                if found is not None:
                    size = found.stat().st_size
                    stable = stable + 1 if size == last_size and size > 0 else 0
                    last_size = size
                    if stable >= 2:
                        finished = True
                        res: dict[str, Any] = {
                            "audio": str(found),
                            "fmt": fmt,
                            "seconds": round(clock() - (deadline - timeout), 1),
                            "copied": copied,
                            "isolated_desktop": isolated,
                        }
                        if prefs_applied:
                            res["render_prefs_applied"] = True
                        if stems:
                            res["stems"] = [str(s) for s in stems]
                        offset_s = _measure_start_offset_s(found)
                        if offset_s is not None:
                            res["start_offset_s"] = offset_s
                        if not keep and out_dir is not None and base != out:
                            shutil.rmtree(base, ignore_errors=True)
                        return res
                code = proc.poll()
                if code is not None and found is None:
                    if code != 0:
                        raise ValueError(f"FL Studio exited with code {code} and wrote no audio")
                    exited_grace += 1
                    if exited_grace >= 2:
                        raise ValueError(
                            "FL Studio exited (code 0) without writing audio — another FL "
                            "instance may have absorbed the launch or export failed"
                        )
            raise ValueError(
                f"no audio appeared within {timeout:.0f} s. FL's batch renderer starts its own "
                "copy of FL Studio: close FL first. Otherwise export from its File menu, or turn "
                "on fl_desk"
            )
        finally:
            if isinstance(proc, _DesktopProcess):
                proc.terminate() if not finished else proc.close()
            elif not finished and hasattr(proc, "terminate"):
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
            if not finished and not keep:
                shutil.rmtree(base, ignore_errors=True)
