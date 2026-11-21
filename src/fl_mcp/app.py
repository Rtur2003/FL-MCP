"""Cross-platform standalone Desktop Application host for FL-MCP Studio.

Launches FL-MCP Studio as a native desktop window (never as a normal browser tab unless
requested explicitly with ``--browser``) across Windows (x86_64/arm64), macOS (arm64/x86_64),
and Linux (x86_64/arm64):

1. Optional native WebView (WebView2 / WKWebView / WebKitGTK) if ``pywebview`` is installed.
2. Zero-dependency chromeless OS App-Mode window (Edge/Chrome/Brave/Chromium ``--app=...``)
   backed by ``/manifest.webmanifest`` and OS app icons (no URL bar, no browser tabs).
3. Zero-dependency built-in dark-DAW ``tkinter`` native desktop control hub fallback
   (or ``--native`` directly on any platform).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fl_mcp import __version__, paths
from fl_mcp.assets import load_asset

BACKENDS = ("auto", "webview", "app-window", "native", "browser")


def normalize_os(plat: str | None = None) -> str:
    """Return canonical OS family: ``windows``, ``macos``, or ``linux``."""
    raw = (plat or sys.platform).lower()
    if raw.startswith("win"):
        return "windows"
    if raw == "darwin" or "mac" in raw:
        return "macos"
    return "linux"


def normalize_arch(machine: str | None = None) -> str:
    """Return canonical CPU architecture: ``x86_64``, ``arm64``, etc."""
    raw = (machine or platform.machine() or "").strip().lower()
    if raw in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if raw in ("arm64", "aarch64", "arm64v8"):
        return "arm64"
    if raw in ("x86", "i386", "i686"):
        return "x86"
    return raw or "unknown"


def app_manifest() -> dict[str, Any]:
    """Web App Manifest served at ``/manifest.webmanifest`` for standalone app shells."""
    return {
        "name": "FL-MCP Studio",
        "short_name": "FL-MCP",
        "description": "FL Studio MCP bridge, live telemetry, and background service console.",
        "start_url": "/?app=1",
        "scope": "/",
        "display": "standalone",
        "background_color": "#141518",
        "theme_color": "#141518",
        "icons": [
            {"src": "/assets/icon-64.png", "sizes": "64x64", "type": "image/png"},
            {"src": "/assets/icon-128.png", "sizes": "128x128", "type": "image/png"},
            {"src": "/assets/icon-256.png", "sizes": "256x256", "type": "image/png"},
        ],
    }


def _win_app_path(exe_name: str) -> Path | None:
    try:
        import winreg

        sub = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                    if isinstance(val, str) and Path(val).is_file():
                        return Path(val)
            except OSError:
                continue
    except OSError:
        pass
    return None


def find_chromium_app_runtime() -> Path | None:
    """Locate an installed Chromium-family browser supporting chromeless ``--app=`` mode."""
    os_name = normalize_os()
    if os_name == "windows":
        for name in ("msedge.exe", "chrome.exe", "brave.exe", "chromium.exe"):
            found = shutil.which(name) or _win_app_path(name)
            if found and Path(found).is_file():
                return Path(found)
        roots = [
            Path(os.environ[k])
            for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")
            if os.environ.get(k)
        ]
        rels = (
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
            Path("BraveSoftware") / "Brave-Browser" / "Application" / "brave.exe",
            Path("Chromium") / "Application" / "chrome.exe",
        )
        for root in roots:
            for rel in rels:
                cand = root / rel
                if cand.is_file():
                    return cand
        return None

    if os_name == "macos":
        home = Path.home()
        mac_apps = (
            "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "Google Chrome.app/Contents/MacOS/Google Chrome",
            "Brave Browser.app/Contents/MacOS/Brave Browser",
            "Chromium.app/Contents/MacOS/Chromium",
            "Vivaldi.app/Contents/MacOS/Vivaldi",
        )
        for base in (Path("/Applications"), home / "Applications"):
            for rel in mac_apps:
                cand = base / rel
                if cand.is_file():
                    return cand
        for cmd in ("microsoft-edge", "google-chrome", "chromium"):
            found = shutil.which(cmd)
            if found:
                return Path(found)
        return None

    for cmd in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "microsoft-edge",
        "brave-browser",
        "vivaldi",
    ):
        found = shutil.which(cmd)
        if found:
            return Path(found)
    return None


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


_RUNTIME_CACHE: dict[str, Any] | None = None


def detect_runtime(*, refresh: bool = False) -> dict[str, Any]:
    """Detect available desktop app runtimes cleanly across OS and CPU architecture."""
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is not None and not refresh:
        return dict(_RUNTIME_CACHE)
    os_name = normalize_os()
    arch = normalize_arch()
    has_webview = _has_module("webview")
    has_tk = _has_module("tkinter")
    app_bin = find_chromium_app_runtime()
    backends: list[str] = []
    if has_webview:
        backends.append("webview")
    if app_bin is not None:
        backends.append("app-window")
    if has_tk:
        backends.append("native")
    backends.append("browser")
    _RUNTIME_CACHE = {
        "os": os_name,
        "arch": arch,
        "webview": has_webview,
        "app_window": str(app_bin) if app_bin else None,
        "tkinter": has_tk,
        "backends": backends,
        "default_backend": backends[0],
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    return dict(_RUNTIME_CACHE)


def _app_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return url if "app=1" in url else f"{url}{sep}app=1"


def _detached_spawn(cmd: list[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x08000000 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def open_chromeless_window(
    url: str, *, width: int = 1360, height: int = 860, wait: bool = False
) -> Path | None:
    """Launch Studio inside a dedicated chromeless OS app window (no tabs/address bar)."""
    binary = find_chromium_app_runtime()
    if binary is None:
        return None
    profile_dir = paths.data_dir() / "app-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        (profile_dir / "First Run").touch(exist_ok=True)
    cmd = [
        str(binary),
        f"--app={_app_url(url)}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--class=FL-MCP-Studio",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-features=ExtensionsToolbarMenu,Translate",
    ]
    proc = _detached_spawn(cmd)
    if wait:
        proc.wait()
    return binary


def _open_webview(url: str, *, title: str, width: int, height: int) -> str:
    import webview  # type: ignore[import-not-found]

    webview.create_window(title, _app_url(url), width=width, height=height, min_size=(960, 640))
    webview.start()
    return "webview"


def _http_json(url: str, method: str = "GET", timeout: float = 1.5) -> dict[str, Any] | None:
    req = urllib.request.Request(
        url,
        data=(b"{}" if method == "POST" else None),
        method=method,
    )
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _run_native_tk(url: str, *, host: str, port: int, runtime: dict[str, Any]) -> str:
    """Zero-dependency dark-DAW native desktop control panel via stdlib tkinter."""
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title(f"FL-MCP Studio v{__version__}")
    root.geometry("760x560")
    root.minsize(640, 480)
    root.configure(bg="#141518")

    # Apply bundled OS icon cleanly when available.
    if sys.platform == "win32":
        loaded_ico = load_asset("icon.ico")
        if loaded_ico:
            ico_file = paths.data_dir() / "icons" / "fl-mcp-studio.ico"
            ico_file.parent.mkdir(parents=True, exist_ok=True)
            ico_file.write_bytes(loaded_ico[0])
            try:
                root.iconbitmap(default=str(ico_file))
            except tk.TclError:
                pass
    loaded_png = load_asset("icon-64.png")
    emblem = None
    if loaded_png:
        try:
            import base64

            emblem = tk.PhotoImage(data=base64.b64encode(loaded_png[0]).decode("ascii"))
            if sys.platform != "win32":
                root.iconphoto(True, emblem)
        except tk.TclError:
            emblem = None

    bg = "#141518"
    panel = "#1c1e22"
    elev = "#181a1e"
    fg = "#e8ecf2"
    muted = "#8e949f"
    accent = "#ff7a2f"
    green = "#65c466"
    ui_font = (
        "Segoe UI"
        if runtime["os"] == "windows"
        else ("Helvetica Neue" if runtime["os"] == "macos" else "DejaVu Sans")
    )
    mono_font = (
        "Consolas"
        if runtime["os"] == "windows"
        else ("Menlo" if runtime["os"] == "macos" else "DejaVu Sans Mono")
    )

    header = tk.Frame(
        root, bg=elev, padx=18, pady=14, highlightthickness=1, highlightbackground="#2c3036"
    )
    header.pack(fill="x")
    if emblem is not None:
        img_lbl = tk.Label(header, image=emblem, bg=elev)
        img_lbl.image = emblem  # type: ignore[attr-defined]
        img_lbl.pack(side="left", padx=(0, 12))

    title_box = tk.Frame(header, bg=elev)
    title_box.pack(side="left", fill="both", expand=True)
    tk.Label(
        title_box, text="FL-MCP Studio", font=(ui_font, 14, "bold"), fg=fg, bg=elev, anchor="w"
    ).pack(fill="x")
    tk.Label(
        title_box,
        text=f"v{__version__} · {runtime['os']} ({runtime['arch']}) · Desktop Console",
        font=(ui_font, 9),
        fg=muted,
        bg=elev,
        anchor="w",
    ).pack(fill="x")

    status_var = tk.StringVar(value="Checking FL Studio & service...")
    service_var = tk.StringVar(value=f"MCP Endpoint: http://{host}:{port}/mcp")
    metrics_var = tk.StringVar(value="MIDI Calls: 0 · MCP Calls: 0")

    cards = tk.Frame(root, bg=bg, padx=18, pady=12)
    cards.pack(fill="x")
    for var, fg_color in ((status_var, green), (service_var, fg), (metrics_var, muted)):
        box = tk.Frame(
            cards, bg=panel, padx=12, pady=8, highlightthickness=1, highlightbackground="#2c3036"
        )
        box.pack(fill="x", pady=4)
        tk.Label(
            box, textvariable=var, fg=fg_color, bg=panel, font=(mono_font, 10), anchor="w"
        ).pack(fill="x")

    btn_row = tk.Frame(root, bg=bg, padx=18, pady=4)
    btn_row.pack(fill="x")

    def copy_endpoint() -> None:
        ep = f"http://{host}:{port}/mcp"
        root.clipboard_clear()
        root.clipboard_append(ep)
        status_var.set(f"Copied MCP endpoint: {ep}")

    def poll_now() -> None:
        def worker() -> None:
            res = _http_json(f"http://{host}:{port}/api/poll", method="POST", timeout=3.0)
            msg = (
                "FL Studio polled successfully."
                if res and res.get("ok")
                else f"Poll error: {(res or {}).get('error') or 'FL Studio unreachable'}"
            )
            root.after(0, lambda: status_var.set(msg))

        threading.Thread(target=worker, daemon=True).start()

    def open_studio() -> None:
        if open_chromeless_window(url) is None:
            import webbrowser

            webbrowser.open(url)

    def run_install_scripts() -> None:
        try:
            from fl_mcp.install import install

            created = install()
            messagebox.showinfo(
                "FL-MCP Scripts", "Installed:\n" + "\n".join(str(p) for p in created)
            )
        except BaseException as exc:
            messagebox.showerror("FL-MCP Scripts", str(exc))

    def create_shortcut() -> None:
        try:
            res = install_app_shortcut()
            messagebox.showinfo("App Shortcut", f"Shortcut ready: {res['path']}")
        except Exception as exc:
            messagebox.showerror("App Shortcut", str(exc))

    def connect_all_clients() -> None:
        try:
            from fl_mcp import clients

            results = clients.install_all(
                source="local", transport="http", url=f"http://{host}:{port}/mcp"
            )
            active = [r for r in results if r.get("ok") and not r.get("skipped")]
            skipped = [r for r in results if r.get("skipped")]
            errors = [r for r in results if not r.get("ok")]
            lines = [
                f"• {r.get('label', r.get('client'))}: "
                f"{r.get('path') or ' '.join(r.get('command', ()))}"
                for r in active
            ]
            if skipped:
                lines.append("")
                lines.append("Skipped:")
                lines.extend(
                    f"• {r.get('label', r.get('client'))}: "
                    f"{r.get('reason') or r.get('error') or 'optional'}"
                    for r in skipped
                )
            if errors:
                lines.append("")
                lines.append("Errors:")
                lines.extend(
                    f"• {r.get('label', r.get('client'))}: {r.get('error')}" for r in errors
                )
            messagebox.showinfo(
                "AI Clients Connected",
                "Registered HTTP MCP endpoint:\n" + "\n".join(lines),
            )
        except Exception as exc:
            messagebox.showerror("AI Clients", str(exc))

    btn_specs = (
        ("Poll FL Now", poll_now, True),
        ("Copy MCP URL", copy_endpoint, False),
        ("Connect AI Clients", connect_all_clients, False),
        ("Install FL Scripts", run_install_scripts, False),
        ("Add App Shortcut", create_shortcut, False),
        ("Open Full Console", open_studio, False),
    )
    for label, cmd_fn, primary in btn_specs:
        tk.Button(
            btn_row,
            text=label,
            command=cmd_fn,
            bg=accent if primary else panel,
            fg="#0b0e14" if primary else fg,
            activebackground="#ff8c4a" if primary else "#20283a",
            activeforeground="#0b0e14" if primary else fg,
            relief="flat",
            padx=11,
            pady=6,
            font=(ui_font, 9, "bold"),
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))

    log_box = scrolledtext.ScrolledText(
        root,
        bg="#080b10",
        fg=fg,
        insertbackground=fg,
        font=(mono_font, 9),
        relief="flat",
        padx=10,
        pady=8,
        height=14,
    )
    log_box.pack(fill="both", expand=True, padx=18, pady=(8, 16))
    log_box.configure(state="disabled")

    alive = {"run": True}

    def _apply_state(data: dict[str, Any]) -> None:
        if not alive["run"]:
            return
        studio = data.get("studio") or {}
        st = studio.get("status")
        svc = data.get("service") or {}
        tf = (data.get("traffic") or {}).get("stats") or {}
        events = (data.get("traffic") or {}).get("events") or []
        if st:
            status_var.set(
                f"FL Studio: {st.get('title') or 'Connected'} | {st.get('bpm') or '--'} BPM | "
                f"Pat {st.get('pat') or 1}"
            )
        else:
            doc = data.get("doctor") or {}
            status_var.set(
                "FL Studio: Waiting on MIDI port"
                if doc.get("midi_port_found")
                else f"FL Studio: MIDI port '{doc.get('port_name', 'FL MCP')}' not found"
            )
        pid_txt = f"PID {svc['pid']}" if svc.get("running") else "Embedded Service"
        service_var.set(f"{pid_txt} · MCP: http://{host}:{port}/mcp · SSE: /sse")
        metrics_var.set(
            f"MIDI: {tf.get('midi_calls', 0)} calls ({tf.get('midi_errors', 0)} err, "
            f"avg {tf.get('avg_ms', 0)} ms) · MCP Tools: {tf.get('mcp_calls', 0)} calls"
        )
        lines = []
        for ev in events[-16:]:
            ok = "OK" if ev.get("ok") else "ERR"
            lines.append(
                f"[{ev.get('dir', '?').upper():4s}] {ev.get('op', '?'):<14} {ok:3s} "
                f"({ev.get('ms', 0)} ms) {ev.get('error') or ev.get('req') or ''}"
            )
        text = "\n".join(lines) or "Waiting for MIDI or MCP tool activity..."
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.insert("1.0", text)
        log_box.configure(state="disabled")

    def refresh_loop() -> None:
        if not alive["run"]:
            return
        data = _http_json(f"http://{host}:{port}/api/state", timeout=1.5)
        if not alive["run"]:
            return
        if data:
            try:
                root.after(0, lambda d=data: _apply_state(d))
            except tk.TclError:
                return
        try:
            root.after(2200, lambda: threading.Thread(target=refresh_loop, daemon=True).start())
        except tk.TclError:
            pass

    root.protocol("WM_DELETE_WINDOW", lambda: (alive.__setitem__("run", False), root.destroy()))
    threading.Thread(target=refresh_loop, daemon=True).start()
    root.mainloop()
    return "native"


def open_studio_window(
    url: str,
    *,
    backend: str = "auto",
    host: str = "127.0.0.1",
    port: int = 8765,
    width: int = 1360,
    height: int = 860,
    wait_embedded: bool = False,
) -> str:
    """Open FL-MCP Studio as a native app window (never a regular browser tab unless asked)."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(BACKENDS)}")
    rt = detect_runtime()
    chosen = rt["default_backend"] if backend == "auto" else backend
    if chosen == "webview":
        try:
            return _open_webview(
                url, title=f"FL-MCP Studio v{__version__}", width=width, height=height
            )
        except Exception:
            chosen = (
                "app-window" if rt["app_window"] else ("native" if rt["tkinter"] else "browser")
            )
    if chosen == "app-window":
        if open_chromeless_window(url, width=width, height=height, wait=wait_embedded) is not None:
            return "app-window"
        chosen = "native" if rt["tkinter"] else "browser"
    if chosen == "native":
        if rt["tkinter"]:
            return _run_native_tk(url, host=host, port=port, runtime=rt)
        chosen = "browser"
    import webbrowser

    webbrowser.open(url)
    return "browser"


def _find_free_port(host: str, preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, preferred))
            return int(s.getsockname()[1])
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
        try:
            s2.bind((host, 0))
            return int(s2.getsockname()[1])
        except OSError:
            return preferred


def _start_embedded_server(
    host: str, port: int, *, poll: bool = True, poll_interval: float = 5.0
) -> tuple[Any, threading.Thread, int]:
    import uvicorn

    from fl_mcp import service_daemon

    chosen_port = _find_free_port(host, port)
    asgi = service_daemon.create_asgi_app(
        host=host, port=chosen_port, poll=poll, poll_interval=poll_interval
    )
    cfg = uvicorn.Config(asgi, host=host, port=chosen_port, log_level="warning")
    uv = uvicorn.Server(cfg)
    thread = threading.Thread(target=uv.run, name="fl-mcp-embedded", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _http_json(f"http://{host}:{chosen_port}/api/service", timeout=0.4) is not None:
            return uv, thread, chosen_port
        time.sleep(0.1)
    return uv, thread, chosen_port


def _shortcut_exec_parts() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return str(sys.executable), []
    gui_bin = shutil.which("fl-mcp-studio")
    if gui_bin:
        return gui_bin, []
    from fl_mcp.service_daemon import _pythonw_executable

    return _pythonw_executable(), ["-m", "fl_mcp.app"]


def install_app_shortcut(*, base_dir: Path | None = None) -> dict[str, Any]:
    """Install OS-native application launcher (Windows .lnk / macOS .app / Linux .desktop)."""
    os_name = normalize_os()
    exe, args = _shortcut_exec_parts()

    if os_name == "windows":
        ico_data = load_asset("icon.ico")
        icon_dir = (base_dir or paths.data_dir()) / "icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_file = icon_dir / "fl-mcp-studio.ico"
        if ico_data:
            icon_file.write_bytes(ico_data[0])
        programs = (
            base_dir
            if base_dir is not None
            else Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
        )
        programs.mkdir(parents=True, exist_ok=True)
        lnk = programs / "FL-MCP Studio.lnk"
        if base_dir is None:
            arg_str = subprocess.list2cmdline(args) if args else ""
            ps = (
                "$s=(New-Object -COM WScript.Shell).CreateShortcut("
                + json.dumps(str(lnk))
                + ");"
                + f"$s.TargetPath={json.dumps(exe)};"
                + f"$s.Arguments={json.dumps(arg_str)};"
                + f"$s.IconLocation={json.dumps(str(icon_file) + ',0')};"
                + "$s.Description='FL-MCP Studio';$s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            # Deterministic offline write when base_dir is provided in isolated unit tests.
            lnk.write_text(
                json.dumps({"target": exe, "args": args, "icon": str(icon_file)}), "utf-8"
            )
        return {"os": os_name, "arch": normalize_arch(), "path": str(lnk), "icon": str(icon_file)}

    if os_name == "macos":
        apps_dir = base_dir if base_dir is not None else (Path.home() / "Applications")
        bundle = apps_dir / "FL-MCP Studio.app"
        contents = bundle / "Contents"
        macos_dir = contents / "MacOS"
        res_dir = contents / "Resources"
        macos_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(parents=True, exist_ok=True)
        icns_data = load_asset("icon.icns")
        if icns_data:
            (res_dir / "AppIcon.icns").write_bytes(icns_data[0])
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            "  <key>CFBundleName</key><string>FL-MCP Studio</string>\n"
            "  <key>CFBundleDisplayName</key><string>FL-MCP Studio</string>\n"
            "  <key>CFBundleIdentifier</key><string>io.flmcp.studio</string>\n"
            "  <key>CFBundleExecutable</key><string>FL-MCP Studio</string>\n"
            "  <key>CFBundleIconFile</key><string>AppIcon.icns</string>\n"
            f"  <key>CFBundleShortVersionString</key><string>{__version__}</string>\n"
            "  <key>CFBundlePackageType</key><string>APPL</string>\n"
            "</dict>\n</plist>\n"
        )
        (contents / "Info.plist").write_text(plist, encoding="utf-8")
        import shlex

        cmdline = " ".join(shlex.quote(x) for x in [exe, *args])
        launcher = macos_dir / "FL-MCP Studio"
        launcher.write_text(f'#!/bin/sh\nexec {cmdline} "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
        return {"os": os_name, "arch": normalize_arch(), "path": str(bundle)}

    data_home = (
        base_dir
        if base_dir is not None
        else Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    )
    apps_dir = data_home / "applications"
    icons_dir = data_home / "icons" / "hicolor" / "256x256" / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)
    png_data = load_asset("icon-256.png")
    if png_data:
        (icons_dir / "fl-mcp-studio.png").write_bytes(png_data[0])
    import shlex

    cmdline = " ".join(shlex.quote(x) for x in [exe, *args])
    desktop = apps_dir / "fl-mcp-studio.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=FL-MCP Studio\n"
        "Comment=FL Studio MCP bridge, live telemetry, and background service console\n"
        f"Exec={cmdline}\n"
        "Icon=fl-mcp-studio\n"
        "Terminal=false\n"
        "StartupWMClass=FL-MCP-Studio\n"
        "Categories=AudioVideo;Audio;Music;\n",
        encoding="utf-8",
    )
    return {"os": os_name, "arch": normalize_arch(), "path": str(desktop)}


def launch_app(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    poll: bool = True,
    poll_interval: float = 5.0,
    backend: str = "auto",
    embedded: bool = False,
) -> int:
    """Start (or attach to) the FL-MCP service and open the native Desktop App window."""
    from fl_mcp import service_daemon

    uv_server: Any = None
    actual_port = port
    if not embedded:
        try:
            st = service_daemon.start_daemon(
                host=host, port=port, poll=poll, poll_interval=poll_interval
            )
            url = str(st["url"])
            actual_port = int(st.get("port") or port)
        except Exception:
            embedded = True
    if embedded:
        uv_server, _, actual_port = _start_embedded_server(
            host, port, poll=poll, poll_interval=poll_interval
        )
        url = f"http://{host}:{actual_port}/"
        # Embedded servers exit when the process exits, so prefer a blocking native window.
        if backend == "auto":
            rt = detect_runtime()
            if rt["webview"]:
                backend = "webview"
            elif rt["tkinter"]:
                backend = "native"
    try:
        open_studio_window(
            url, backend=backend, host=host, port=actual_port, wait_embedded=embedded
        )
    finally:
        if uv_server is not None:
            uv_server.should_exit = True
    return 0


def _ensure_stdio() -> None:
    """Bind None stdout/stderr in windowed GUI builds so print/uvicorn never crash."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        lf = paths.logs_dir() / "studio-app.log"
        lf.parent.mkdir(parents=True, exist_ok=True)
        stream = open(lf, "a", encoding="utf-8")
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main(argv: list[str] | None = None) -> None:
    """Desktop App entrypoint supporting frozen ``--mcp`` / ``--cli`` dispatch."""
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in ("--mcp", "--stdio"):
        from fl_mcp.server import serve as server_main

        sys.argv = [sys.argv[0], *raw[1:]]
        server_main()
        return
    _ensure_stdio()
    if raw and raw[0] == "--cli":
        from fl_mcp.cli import main as cli_main

        cli_main(raw[1:])
        return

    parser = argparse.ArgumentParser(
        prog="fl-mcp-studio", description="Launch FL-MCP Studio Desktop App."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--no-poll", action="store_true", help="disable background FL polling")
    parser.add_argument(
        "--backend", choices=BACKENDS, default="auto", help="desktop window runtime"
    )
    parser.add_argument("--native", action="store_true", help="open built-in tkinter desktop panel")
    parser.add_argument(
        "--browser", action="store_true", help="open in browser instead of app shell"
    )
    parser.add_argument("--foreground", action="store_true", help="run server inside app process")
    parser.add_argument("--status", action="store_true", help="print OS, arch & app runtime info")
    parser.add_argument("--json", action="store_true", help="JSON output for --status/--install")
    parser.add_argument(
        "--install-shortcut",
        action="store_true",
        help="register OS Start Menu / Applications / .desktop app shortcut",
    )
    args = parser.parse_args(raw)

    if args.status:
        info = detect_runtime()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(
                f"FL-MCP Studio v{__version__} | {info['os']} ({info['arch']}) | "
                f"backend: {info['default_backend']} | runtime: {info['app_window'] or 'tkinter'}"
            )
        raise SystemExit(0)

    if args.install_shortcut:
        res = install_app_shortcut()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Installed FL-MCP Studio app shortcut: {res['path']}")
        raise SystemExit(0)

    backend = "native" if args.native else ("browser" if args.browser else args.backend)
    raise SystemExit(
        launch_app(
            host=args.host,
            port=args.port,
            poll=not args.no_poll,
            poll_interval=args.poll_interval,
            backend=backend,
            embedded=args.foreground,
        )
    )


if __name__ == "__main__":
    main()
