"""Persistent background daemon for Studio UI, HTTP/SSE MCP, and telemetry polling."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fl_mcp import paths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_POLL_INTERVAL = 5.0
AUTOSTART_NAME = "FLStudioMCPService"


def pid_path() -> Path:
    return paths.data_dir() / "service.pid"


def state_path() -> Path:
    return paths.data_dir() / "service.json"


def log_path() -> Path:
    return paths.logs_dir() / "service.log"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_pid_alive(pid: int) -> bool:
    """Return True if process `pid` is currently alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_uint32()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_pid() -> int:
    pf = pid_path()
    if pf.is_file():
        try:
            return int(pf.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    info = _read_json(state_path())
    try:
        return int(info.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def _clear_runtime_files() -> None:
    for p in (pid_path(), state_path()):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def terminate_pid(pid: int, timeout: float = 4.0) -> bool:
    """Terminate `pid` cleanly, waiting up to `timeout` seconds."""
    if not is_pid_alive(pid):
        return True
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, int(pid))
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.WaitForSingleObject(handle, int(timeout * 1000))
                finally:
                    kernel32.CloseHandle(handle)
        except Exception:
            pass
        return not is_pid_alive(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not is_pid_alive(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.1)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    return not is_pid_alive(pid)


def _rotate_log(path: Path, max_bytes: int = 2_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > max_bytes:
        backup = path.with_name(f"{path.name}.1")
        with contextlib.suppress(OSError):
            if backup.exists():
                backup.unlink()
            path.replace(backup)


def _http_post_json(url: str, timeout: float = 1.5) -> dict[str, Any] | None:
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _http_get_json(url: str, timeout: float = 1.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _pythonw_executable() -> str:
    """Prefer console-less pythonw.exe on Windows when spawning detached background processes."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            return str(pw)
    return str(exe)


def _detached_popen_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {
            "creationflags": 0x00000008 | 0x08000000 | 0x00000200,
            "close_fds": True,
        }
    return {"start_new_session": True, "close_fds": True}


class _BackgroundPoller:
    """Non-intrusive FL telemetry poller with automatic exponential backoff."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        interval_s: float = DEFAULT_POLL_INTERVAL,
        bridge: Any | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._bridge = bridge
        self.enabled = bool(enabled)
        self.interval_s = max(2.0, min(60.0, float(interval_s)))
        self.failures = 0
        self.last_poll_ts = 0.0
        self.last_ok = False
        self.last_error = ""
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def config(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "interval_s": self.interval_s,
                "failures": self.failures,
                "last_poll_ts": self.last_poll_ts,
                "last_ok": self.last_ok,
                "last_error": self.last_error,
            }

    def update(
        self, *, enabled: bool | None = None, interval_s: float | None = None
    ) -> dict[str, Any]:
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
                if self.enabled:
                    self.failures = 0
            if interval_s is not None:
                self.interval_s = max(2.0, min(60.0, float(interval_s)))
        self._wake.set()
        return self.config()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="fl-mcp-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _port_present(self) -> bool:
        try:
            import mido

            from fl_mcp.bridge import DEFAULT_PORT, match_port

            port = os.environ.get("FL_MCP_PORT") or DEFAULT_PORT
            outs = mido.get_output_names()
            return bool(
                match_port(outs, port) or (sys.platform == "darwin" and match_port(outs, "iac"))
            )
        except Exception:
            return False

    def _loop(self) -> None:
        from fl_mcp import dashboard

        while not self._stop.is_set():
            cfg = self.config()
            if cfg["enabled"] and self._port_present():
                bridge_lock = getattr(self._bridge, "_lock", None)
                if bridge_lock is not None and bridge_lock.locked():
                    pass
                else:
                    res = dashboard.poll_fl_once(bridge=self._bridge)
                    with self._lock:
                        self.last_poll_ts = time.time()
                        if res.get("ok"):
                            self.failures = 0
                            self.last_ok = True
                            self.last_error = ""
                        else:
                            self.failures += 1
                            self.last_ok = False
                            self.last_error = str(res.get("error") or "FL Studio unreachable")
            wait_s = cfg["interval_s"]
            with self._lock:
                if not self.enabled:
                    wait_s = 15.0
                elif self.failures:
                    wait_s = min(60.0, self.interval_s * (2 ** min(self.failures, 4)))
            self._wake.wait(timeout=wait_s)
            self._wake.clear()


_ACTIVE_POLLER: _BackgroundPoller | None = None


def create_asgi_app(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    poll: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stop_callback: Any | None = None,
) -> Any:
    """Build Starlette ASGI app serving Studio UI, assets, REST API, and HTTP/SSE MCP."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    from fl_mcp import clients, dashboard, server
    from fl_mcp.assets import load_asset

    poller = _BackgroundPoller(enabled=poll, interval_s=poll_interval, bridge=server.bridge)
    global _ACTIVE_POLLER
    _ACTIVE_POLLER = poller

    mcp_app = server.mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
    sse_app = server.mcp.sse_app(sse_path="/sse", message_path="/messages/", host=host)

    @contextlib.asynccontextmanager
    async def lifespan(_root: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            if poll:
                poller.start()
            try:
                yield
            finally:
                poller.stop()

    async def index(_request: Request) -> HTMLResponse:
        return HTMLResponse(dashboard.load_html(), headers={"Cache-Control": "no-cache"})

    async def asset(request: Request) -> Response:
        name = request.path_params.get("name", "")
        loaded = load_asset(name)
        if loaded is None:
            return JSONResponse({"error": "asset not found"}, status_code=404)
        payload, mime = loaded
        return Response(
            payload,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def favicon(_request: Request) -> Response:
        loaded = load_asset("favicon.png")
        if loaded is None:
            return Response(status_code=404)
        return Response(
            loaded[0], media_type=loaded[1], headers={"Cache-Control": "public, max-age=3600"}
        )

    async def api_state(request: Request) -> JSONResponse:
        refresh = request.query_params.get("refresh_templates") == "1"
        state = dashboard.build_state(refresh_templates=refresh)
        state["service"] = {**service_status(), "poller": poller.config()}
        return JSONResponse(state)

    async def api_doctor(_request: Request) -> JSONResponse:
        return JSONResponse(dashboard.doctor_info())

    async def api_paths(_request: Request) -> JSONResponse:
        return JSONResponse(paths.diagnostics())

    async def api_clients(_request: Request) -> JSONResponse:
        return JSONResponse({"clients": clients.client_statuses(), "service": service_status()})

    async def api_service(_request: Request) -> JSONResponse:
        return JSONResponse({**service_status(), "poller": poller.config()})

    async def api_poll(_request: Request) -> JSONResponse:
        return JSONResponse(dashboard.poll_fl_once(bridge=server.bridge))

    async def api_traffic_clear(_request: Request) -> JSONResponse:
        from fl_mcp import telemetry

        return JSONResponse({"cleared": telemetry.clear_traffic()})

    async def api_templates_refresh(_request: Request) -> JSONResponse:
        items = dashboard.cached_templates(force=True)
        return JSONResponse({"count": len(items), "templates": items})

    async def api_poll_config(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = body.get("enabled") if isinstance(body, dict) else None
        interval_s = body.get("interval_s") if isinstance(body, dict) else None
        if enabled and poller._thread is None:
            poller.start()
        return JSONResponse(poller.update(enabled=enabled, interval_s=interval_s))

    async def api_service_stop(_request: Request) -> JSONResponse:
        if stop_callback is not None:
            threading.Timer(0.1, stop_callback).start()
        return JSONResponse({"stopping": True})

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/favicon.ico", favicon, methods=["GET"]),
        Route("/favicon.png", favicon, methods=["GET"]),
        Route("/assets/{name:str}", asset, methods=["GET"]),
        Route("/api/state", api_state, methods=["GET"]),
        Route("/api/doctor", api_doctor, methods=["GET"]),
        Route("/api/paths", api_paths, methods=["GET"]),
        Route("/api/clients", api_clients, methods=["GET"]),
        Route("/api/service", api_service, methods=["GET"]),
        Route("/api/poll", api_poll, methods=["POST"]),
        Route("/api/traffic/clear", api_traffic_clear, methods=["POST"]),
        Route("/api/templates/refresh", api_templates_refresh, methods=["POST"]),
        Route("/api/service/poll-config", api_poll_config, methods=["POST"]),
        Route("/api/service/stop", api_service_stop, methods=["POST"]),
        *mcp_app.routes,
        *sse_app.routes,
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def run_foreground(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    poll: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> int:
    """Run the unified Studio Web UI + MCP HTTP/SSE daemon in the current process."""
    import asyncio
    import socket

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        print(f"fl-mcp service failed to bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    actual_port = int(sock.getsockname()[1])
    sock.close()

    uv_server: uvicorn.Server | None = None

    def request_stop() -> None:
        if uv_server is not None:
            uv_server.should_exit = True

    app = create_asgi_app(
        host=host,
        port=actual_port,
        poll=poll,
        poll_interval=poll_interval,
        stop_callback=request_stop,
    )
    config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning")
    uv_server = uvicorn.Server(config)

    url = f"http://{host}:{actual_port}/"
    mcp_url = f"http://{host}:{actual_port}/mcp"
    sse_url = f"http://{host}:{actual_port}/sse"
    pid = os.getpid()
    pid_path().parent.mkdir(parents=True, exist_ok=True)
    pid_path().write_text(f"{pid}\n", encoding="utf-8")
    _write_json(
        state_path(),
        {
            "pid": pid,
            "host": host,
            "port": actual_port,
            "url": url,
            "mcp_url": mcp_url,
            "sse_url": sse_url,
            "started_at": time.time(),
            "poll_enabled": poll,
            "poll_interval_s": poll_interval,
        },
    )
    try:
        asyncio.run(uv_server.serve())
    finally:
        _clear_runtime_files()
    return 0


def service_status() -> dict[str, Any]:
    """Return background daemon status (pid, running, endpoints, log path, autostart)."""
    pid = _read_pid()
    info = _read_json(state_path())
    alive = is_pid_alive(pid) if pid else False
    if pid and not alive:
        _clear_runtime_files()
        info = {}
        pid = 0
    return {
        "running": bool(alive),
        "pid": pid if alive else None,
        "host": info.get("host") or DEFAULT_HOST,
        "port": info.get("port") or DEFAULT_PORT,
        "url": info.get("url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/",
        "mcp_url": info.get("mcp_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/mcp",
        "sse_url": info.get("sse_url") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/sse",
        "started_at": info.get("started_at"),
        "poll_enabled": bool(info.get("poll_enabled", True)) if alive else False,
        "poll_interval_s": info.get("poll_interval_s") or DEFAULT_POLL_INTERVAL,
        "log_path": str(log_path()),
        "autostart": autostart_status(),
    }


def start_daemon(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    poll: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    wait_timeout: float = 5.0,
) -> dict[str, Any]:
    """Launch the background daemon detached and wait until its endpoint responds."""
    current = service_status()
    if current["running"]:
        return {**current, "already_running": True}

    lf = log_path()
    _rotate_log(lf)
    argv = [
        _pythonw_executable(),
        "-m",
        "fl_mcp",
        "service",
        "run",
        "--host",
        host,
        "--port",
        str(port),
        "--poll-interval",
        str(poll_interval),
    ]
    if not poll:
        argv.append("--no-poll")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(lf, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            **_detached_popen_kwargs(),
        )

    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        st = service_status()
        if st["running"] and st.get("port"):
            if _http_get_json(f"http://{st['host']}:{st['port']}/api/service", timeout=0.5):
                return {**st, "already_running": False}
        if proc.poll() is not None:
            tail = read_logs(lines=20)
            raise RuntimeError(
                f"Background service exited immediately (code {proc.returncode}). Log:\n{tail}"
            )
        time.sleep(0.15)
    raise RuntimeError(f"Timed out waiting for background service on {host}:{port}. Check {lf}")


def stop_daemon(timeout: float = 4.0) -> dict[str, Any]:
    """Stop the running background daemon cleanly."""
    st = service_status()
    if not st["running"] or not st["pid"]:
        _clear_runtime_files()
        return {"stopped": True, "was_running": False, "pid": None}
    pid = int(st["pid"])
    url = f"http://{st['host']}:{st['port']}/api/service/stop"
    _http_post_json(url, timeout=1.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            _clear_runtime_files()
            return {"stopped": True, "was_running": True, "pid": pid}
        time.sleep(0.1)
    ok = terminate_pid(pid, timeout=2.0)
    if ok:
        _clear_runtime_files()
    return {"stopped": ok, "was_running": True, "pid": pid}


def restart_daemon(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    poll: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Restart the background daemon cleanly."""
    stop_daemon()
    return start_daemon(host=host, port=port, poll=poll, poll_interval=poll_interval)


def read_logs(lines: int = 40) -> str:
    """Return the last `lines` of `service.log`."""
    lf = log_path()
    if not lf.is_file():
        return ""
    all_lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(all_lines[-max(1, lines) :])


# ---- Cross-platform OS login autostart


def _autostart_command(port: int = DEFAULT_PORT, poll: bool = True) -> list[str]:
    argv = [_pythonw_executable(), "-m", "fl_mcp", "service", "run", "--port", str(port)]
    if not poll:
        argv.append("--no-poll")
    return argv


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "io.flmcp.service.plist"


def _systemd_unit_path() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return cfg / "systemd" / "user" / "fl-mcp.service"


def _render_launchd_plist(argv: list[str], log_file: Path) -> str:
    args_xml = "\n".join(f"    <string>{a}</string>" for a in argv)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        "  <key>Label</key><string>io.flmcp.service</string>\n"
        f"  <key>ProgramArguments</key>\n  <array>\n{args_xml}\n  </array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "  <key>KeepAlive</key><false/>\n"
        f"  <key>StandardOutPath</key><string>{log_file}</string>\n"
        f"  <key>StandardErrorPath</key><string>{log_file}</string>\n"
        "</dict>\n</plist>\n"
    )


def _render_systemd_unit(argv: list[str]) -> str:
    exec_start = " ".join(shlex.quote(a) for a in argv)
    return (
        "[Unit]\n"
        "Description=FL Studio MCP Background Service & Studio UI\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def autostart_status() -> dict[str, Any]:
    """Check whether OS login autostart is registered for this user."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            ) as key:
                val, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
                return {"enabled": True, "backend": "registry", "command": val}
        except OSError:
            return {"enabled": False, "backend": "registry", "command": None}
    if sys.platform == "darwin":
        p = _launchd_plist_path()
        return {"enabled": p.is_file(), "backend": "launchd", "path": str(p)}
    unit = _systemd_unit_path()
    return {"enabled": unit.is_file(), "backend": "systemd", "path": str(unit)}


def enable_autostart(port: int = DEFAULT_PORT, *, poll: bool = True) -> dict[str, Any]:
    """Register FL-MCP background service to start on user login."""
    argv = _autostart_command(port=port, poll=poll)
    if sys.platform == "win32":
        import winreg

        cmdline = subprocess.list2cmdline(argv)
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
        ) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmdline)
        return {"enabled": True, "backend": "registry", "command": cmdline}
    if sys.platform == "darwin":
        plist = _launchd_plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(_render_launchd_plist(argv, log_path()), encoding="utf-8")
        return {"enabled": True, "backend": "launchd", "path": str(plist)}
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(_render_systemd_unit(argv), encoding="utf-8")
    return {"enabled": True, "backend": "systemd", "path": str(unit)}


def disable_autostart() -> dict[str, Any]:
    """Remove FL-MCP background service user login registration."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, AUTOSTART_NAME)
        except OSError:
            pass
        return {"enabled": False, "backend": "registry"}
    target = _launchd_plist_path() if sys.platform == "darwin" else _systemd_unit_path()
    if target.exists():
        target.unlink()
    return {"enabled": False, "backend": "launchd" if sys.platform == "darwin" else "systemd"}
