"""Render a project to audio with FL Studio's own command line (Windows: FL64.exe /R /E<format>).

FL is started with the project, renders it to a file next to it, and the file is waited for. If
FL is already running it may hand the project to that instance; the answer then comes later or
not at all, which is reported rather than hidden."""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fl_mcp import paths

FORMATS = ("wav", "mp3", "ogg", "flac")


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


def render(
    flp: str | Path,
    fmt: str = "wav",
    timeout: float = 900.0,
    exe: Path | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> dict[str, Any]:
    project = Path(flp).expanduser()
    if project.suffix.lower() != ".flp" or not project.is_file():
        raise ValueError(f"not an existing .flp project: {project}")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    exe = exe or find_fl()
    if exe is None:
        why = (
            "command line rendering is only supported on Windows"
            if sys.platform != "win32"
            else ("FL Studio's FL64.exe was not found: set FL_MCP_FL_DIR to its folder")
        )
        raise ValueError(f"cannot render from the command line: {why}")
    target = project.with_suffix(f".{fmt}")
    started = wall()
    proc = popen([str(exe), "/R", f"/E{fmt}", str(project)])
    deadline, last_size, stable = clock() + timeout, -1, 0
    while clock() < deadline:
        sleep(1.0)
        found = target if target.is_file() and target.stat().st_mtime >= started - 1 else None
        if found is not None:
            size = found.stat().st_size
            stable = stable + 1 if size == last_size and size > 0 else 0
            last_size = size
            if stable >= 2:
                return {
                    "audio": str(found),
                    "fmt": fmt,
                    "seconds": round(clock() - (deadline - timeout), 1),
                }
        code = proc.poll()
        if code not in (None, 0) and found is None:
            raise ValueError(f"FL Studio exited with code {code} and wrote no audio")
    raise ValueError(
        f"no audio appeared within {timeout:.0f} s. If FL Studio was already open it may have "
        "taken the project instead of rendering it: close FL, or export from its File menu"
    )
