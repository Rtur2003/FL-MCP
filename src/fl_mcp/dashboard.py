"""Live studio visualizer and incoming/outgoing traffic monitor (HTTP + stdlib).

Serves a dark DAW-styled local web UI on 127.0.0.1 that displays:
- Real-time incoming/outgoing MCP tool calls and MIDI bridge TX/RX frames, bytes & latencies
- Studio overview: FL connection, transport, Channel Rack, Mixer strips, Peak meters & FX slots
- HTML5 Canvas Piano Roll showing queued (inbox.json) vs applied (state.json) notes
- Latest audio loudness (LUFS / true peak / crest) and 7-band spectrum + actionable mix advice
- Production plan timeline, auto-discovered .flp templates, and layer-by-layer doctor checks
"""

from __future__ import annotations

import importlib.resources
import importlib.util
import json
import math
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fl_mcp import __version__, desk, paths, produce, telemetry
from fl_mcp.bridge import DEFAULT_PORT, Bridge, FLError, match_port
from fl_mcp.install import targets

_TEMPLATE_CACHE: dict[str, Any] = {"ts": 0.0, "items": []}
_CACHE_LOCK = threading.Lock()


def load_html() -> str:
    """Return the bundled dark-DAW dashboard HTML UI."""
    local = Path(__file__).with_name("data") / "dashboard.html"
    if local.is_file():
        return local.read_text(encoding="utf-8")
    return importlib.resources.files("fl_mcp").joinpath("data", "dashboard.html").read_text("utf-8")


DASHBOARD_HTML = load_html()


def cached_templates(force: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and _TEMPLATE_CACHE["items"] and now - _TEMPLATE_CACHE["ts"] < 30.0:
            return list(_TEMPLATE_CACHE["items"])
    try:
        items = produce.discover_templates(limit=30)
    except Exception:
        items = []
    with _CACHE_LOCK:
        _TEMPLATE_CACHE["items"] = items
        _TEMPLATE_CACHE["ts"] = now
    return items


def _active_port() -> str:
    return os.environ.get("FL_MCP_PORT") or DEFAULT_PORT


def doctor_info() -> dict[str, Any]:
    """Quick non-destructive host layer check (no MIDI ping side-effects)."""
    from fl_mcp.install import diagnose_fl_midi, find_loopmidi, is_loopmidi_running

    port = _active_port()
    info: dict[str, Any] = {
        "version": __version__,
        "port_name": port,
        "midi_inputs": [],
        "midi_outputs": [],
        "midi_port_found": False,
        "midi_error": "",
        "desktop_enabled": desk.enabled(),
        "desktop_mode": desk.mode(),
        "data_dir": str(paths.data_dir()),
        "notes_dir": str(paths.notes_dir()),
    }
    try:
        import mido

        ins = list(mido.get_input_names())
        outs = list(mido.get_output_names())
        info["midi_inputs"] = ins
        info["midi_outputs"] = outs
        has_in = bool(
            match_port(ins, port) or (sys.platform == "darwin" and match_port(ins, "iac"))
        )
        has_out = bool(
            match_port(outs, port) or (sys.platform == "darwin" and match_port(outs, "iac"))
        )
        info["midi_port_found"] = has_in and has_out
    except Exception as exc:
        info["midi_error"] = str(exc)

    fl_user = paths.fl_user_dir()
    dev, scr = targets(fl_user)
    info["fl_script_installed"] = dev.is_file()
    info["piano_roll_script_installed"] = scr.is_file()
    info["fl_user_dir"] = str(fl_user)
    info["paths"] = paths.diagnostics()
    info["loopmidi"] = {
        "installed": find_loopmidi() is not None if sys.platform == "win32" else None,
        "running": is_loopmidi_running() if sys.platform == "win32" else None,
    }
    info["fl_midi"] = diagnose_fl_midi(port)
    try:
        info["audio_analysis"] = all(
            sys.modules.get(mod, False) is not None and importlib.util.find_spec(mod) is not None
            for mod in ("numpy", "scipy", "soundfile")
        )
    except Exception:
        info["audio_analysis"] = False
    return info


def poll_fl_once(bridge_factory: Any = Bridge, *, bridge: Any | None = None) -> dict[str, Any]:
    """Connect or reuse a shared bridge, snapshot live FL Studio state, and update snapshot.json."""
    owns = bridge is None
    b = bridge_factory() if owns else bridge
    try:
        status = b.call({"o": "status"})
        channels = b.call({"o": "list", "k": "channels", "s": 0, "n": 100, "a": 0})
        mixer = b.call({"o": "list", "k": "mixer", "s": 0, "n": 100, "a": 0})
        patterns = b.call({"o": "list", "k": "patterns", "s": 0, "n": 100, "a": 0})
        plugins = b.call({"o": "list", "k": "plugins", "s": 0, "n": 120, "a": 0})
        tracks = [int(r[0]) for r in mixer.get("rows", [])[:32]] or [0]
        raw_peaks = b.call({"o": "peaks", "t": tracks})
        vals = raw_peaks.get("p", []) if isinstance(raw_peaks, dict) else list(raw_peaks)
        peaks_rows = []
        names = {int(r[0]): r[1] for r in mixer.get("rows", [])}
        for idx, t in enumerate(tracks):
            val = float(vals[idx]) if idx < len(vals) else 0.0
            db = round(20 * math.log10(val), 1) if val > 1e-6 else -99.0
            peaks_rows.append([t, names.get(t, f"Insert {t}"), db, int(val > 1e-4)])
        peaks = {
            "cols": ["track", "name", "peak_db", "active"],
            "rows": peaks_rows,
            "clipping": [r[0] for r in peaks_rows if r[2] >= 0.0],
        }
        steps_rows = []
        for ch_row in (channels.get("rows") or [])[:12]:
            ch_idx = int(ch_row[0])
            try:
                st_res = b.call(
                    {"o": "steps", "c": ch_idx, "p": 0, "n": 16, "clear": 0, "on": [], "off": []}
                )
                on_set = set(st_res.get("on") or [])
                bits = "".join("1" if s in on_set else "0" for s in range(16))
                steps_rows.append([ch_idx, bits])
            except Exception:
                pass
        telemetry.update_snapshot(
            status=status,
            channels=channels,
            mixer=mixer,
            patterns=patterns,
            plugins=plugins,
            peaks=peaks,
            steps={"cols": ["channel", "bits"], "rows": steps_rows},
        )
        return {"ok": True, "status": status}
    except (FLError, OSError, Exception) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if owns:
            try:
                b.close()
            except Exception:
                pass


def build_state(
    *,
    limit: int = 120,
    kind: str | None = None,
    only_errors: bool = False,
    refresh_templates: bool = False,
) -> dict[str, Any]:
    """Combined payload for `/api/state` and CLI JSON diagnostics."""
    from fl_mcp import app, clients, server, service_daemon, vault

    rt = app.detect_runtime()
    snap = telemetry.read_snapshot()
    proj_title = (snap.get("status") or {}).get("title")
    return {
        "meta": {
            "version": __version__,
            "ts": round(time.time(), 3),
            "port": _active_port(),
            "platform": rt["os"],
            "arch": rt["arch"],
            "app_runtime": rt["default_backend"],
            "desktop_enabled": desk.enabled(),
            "desktop_mode": desk.mode(),
            "profile": os.environ.get("FL_MCP_PROFILE", "full"),
            "readonly": server._is_readonly(),
            "tools_count": len(server._tools(desk.enabled())),
            "data_dir": str(paths.data_dir()),
            "notes_dir": str(paths.notes_dir()),
        },
        "traffic": telemetry.read_traffic(limit=limit, kind=kind, only_errors=only_errors),
        "studio": snap,
        "context": telemetry.load_context_layers(proj_title),
        "doctor": doctor_info(),
        "paths": paths.diagnostics(),
        "clients": clients.client_statuses(),
        "service": service_daemon.service_status(),
        "templates": cached_templates(force=refresh_templates),
        "vault": vault.status(),
    }


def _act_install_scripts(_body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp import install

    (paths.fl_user_dir() / "Settings").mkdir(parents=True, exist_ok=True)
    installed = install.install()
    return {
        "ok": True,
        "action": "install_scripts",
        "installed": [str(p) for p in installed],
        "doctor": doctor_info(),
    }


def _act_install_client(body: dict[str, Any], http_port: int) -> dict[str, Any]:
    from fl_mcp import clients, service_daemon

    target = str(body.get("client") or "all").strip()
    transport = str(body.get("transport") or "http").strip()
    source = str(body.get("source") or "auto").strip()
    proj = Path(str(body["project"])) if body.get("project") else None
    endpoint = str(body.get("url") or body.get("http_url") or "")
    if transport == "http" and not endpoint and bool(body.get("start_service")):
        try:
            svc = service_daemon.start_daemon(port=http_port)
            endpoint = str(svc.get("mcp_url") or "")
        except Exception:
            pass
    endpoint = endpoint or f"http://127.0.0.1:{http_port}/mcp"
    if target == "all":
        res: Any = clients.install_all(
            source=source,
            project=proj,
            transport=transport,
            url=endpoint,
            only_installed=bool(body.get("only_installed", False)),
        )
        ok = all(r.get("ok", True) for r in res)
    else:
        res = clients.install_client(
            target, source=source, project=proj, transport=transport, url=endpoint
        )
        ok = True
    return {
        "ok": ok,
        "action": "install_client",
        "result": res,
        "clients": clients.client_statuses(project=proj),
    }


def _act_onboard(body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp import clients, install

    res = install.onboard(
        fl_dir=body.get("fl_dir") or None,
        notes_dir=body.get("notes_dir") or None,
        auto_install_midi=bool(body.get("install_midi", True)),
        configure_midi=bool(body.get("configure_midi", True)),
        client=str(body.get("client") or "all"),
        transport=str(body.get("transport") or "http"),
        source=str(body.get("source") or "auto"),
        start_service=bool(body.get("start_service", True)),
        autostart_service=bool(body.get("autostart", False)),
        desktop_mode=body.get("desktop_mode") or "polite",
        prewarm=bool(body.get("prewarm", False)),
    )
    return {
        "action": "onboard",
        **res,
        "doctor": doctor_info(),
        "clients": clients.client_statuses(),
    }


def _act_desktop_mode(body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    mode = body.get("mode", "stealth")
    flag_path = desk.set_enabled(mode)
    return {
        "ok": True,
        "action": "desktop_mode",
        "desktop_enabled": desk.enabled(),
        "desktop_mode": desk.mode(),
        "path": str(flag_path),
    }


def _act_autostart(body: dict[str, Any], http_port: int) -> dict[str, Any]:
    from fl_mcp import service_daemon

    enable = bool(body.get("enable", True))
    res = (
        service_daemon.enable_autostart(port=http_port)
        if enable
        else service_daemon.disable_autostart()
    )
    return {"ok": True, "action": "autostart", "autostart": res}


def _act_app_shortcut(_body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp import app as studio_app

    return {"ok": True, "action": "app_shortcut", "shortcut": studio_app.install_app_shortcut()}


def _act_vault_download(body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp import vault

    target = str(body.get("target") or "all").strip()
    res = vault.download(target=target, force=bool(body.get("force", False)))
    return {"ok": res["ok"], "action": "vault_download", "result": res, "vault": vault.status()}


def _act_vault_rules(body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp import vault

    ws = body.get("workspace") or None
    res = vault.install_rules(workspace=ws)
    return {"ok": res["ok"], "action": "vault_rules", "result": res}


def _act_produce_plan(body: dict[str, Any], _http_port: int) -> dict[str, Any]:
    from fl_mcp.music import compose

    genre = str(body.get("genre") or "house").strip()
    key = str(body.get("key") or "Am").strip()
    raw_bpm = body.get("bpm")
    bpm = int(float(raw_bpm)) if raw_bpm not in (None, "") else None
    raw_len = body.get("length") or "short"
    length: str | int = (
        int(raw_len)
        if str(raw_len).isdigit()
        else (str(raw_len) if str(raw_len) in ("sketch", "short", "full") else "short")
    )
    built = compose(genre, key=key, bpm=bpm, length=length)
    saved = produce.save_plan(built)
    summary = built.summary()
    telemetry.update_snapshot(latest_plan=summary)
    return {"ok": True, "action": "produce_plan", "plan": summary, "path": str(saved)}


def _act_notes(body: dict[str, Any], get_bridge: Any) -> dict[str, Any]:
    from fl_mcp import server
    from fl_mcp.music import theory

    inbox_file = paths.notes_dir() / "inbox.json"
    clear_mode = body.get("clear")
    if clear_mode == "queue":
        inbox_file.unlink(missing_ok=True)
        telemetry.update_snapshot(piano_inbox={"notes": [], "clear": False})
        return {"ok": True, "action": "notes", "cleared_queue": True}

    raw_notes = list(body.get("notes") or [])
    chords = str(body.get("chords") or "").strip()
    if chords:
        raw_notes.extend(theory.chords_to_notes(chords))
    clean = [server._note(n) for n in raw_notes]

    if bool(body.get("preview")):
        if not clean:
            return {"ok": False, "action": "notes", "error": "enter chords or notes first"}
        b = get_bridge()
        ch_arg = body.get("channel")
        ch = (
            int(ch_arg)
            if ch_arg not in (None, "")
            else int((b.call({"o": "status"}) or {}).get("ch") or 0)
        )
        seen: dict[int, int] = {}
        for r in clean:
            seen.setdefault(int(r[0]), int(r[3]))
        pitches = list(seen.items())[:16]
        for p, v in pitches:
            b.call({"o": "note", "c": ch, "n": p, "v": v})
        time.sleep(0.22)
        for p, _ in pitches:
            b.call({"o": "note", "c": ch, "n": p, "v": 0})
        return {"ok": True, "action": "notes", "previewed": len(pitches), "channel": ch}

    clear_roll = clear_mode in (True, "roll", "all")
    if not clean and not clear_roll:
        return {"ok": False, "action": "notes", "error": "enter chords or notes to queue"}
    payload = {"notes": clean, "clear": bool(clear_roll), "ts": round(time.time(), 3)}
    server._write_json(inbox_file, payload)
    telemetry.update_snapshot(piano_inbox=payload)
    return {"ok": True, "action": "notes", "queued": len(clean), "cleared": bool(clear_roll)}


def _act_bridge(
    action: str, body: dict[str, Any], get_bridge: Any, bridge_factory: Any
) -> dict[str, Any]:
    b = get_bridge()
    if action == "transport":
        act = str(body.get("transport_action") or body.get("a") or "").strip()
        res = b.call({"o": "tr", "a": act, "v": body.get("value")})
    elif action == "set":
        kind = str(body.get("kind") or "mixer").strip()
        idx = int(body.get("index", 0))
        res = b.call({"o": "set", "k": kind, "i": idx, "p": dict(body.get("props") or {})})
    else:
        ch = int(body.get("channel", 0))
        step_idx = max(0, min(63, int(body.get("step", 0))))
        turn_on = bool(body.get("on", True))
        res = b.call(
            {
                "o": "steps",
                "c": ch,
                "p": 0,
                "n": 16,
                "clear": 0,
                "on": [step_idx] if turn_on else [],
                "off": [] if turn_on else [step_idx],
            }
        )
    poll_fl_once(bridge_factory, bridge=b)
    return {"ok": True, "action": action, "result": res}


_HOST_ACTIONS = {
    "install_scripts": _act_install_scripts,
    "install_client": _act_install_client,
    "desktop_mode": _act_desktop_mode,
    "autostart": _act_autostart,
    "app_shortcut": _act_app_shortcut,
    "produce_plan": _act_produce_plan,
    "onboard": _act_onboard,
    "vault_download": _act_vault_download,
    "vault_rules": _act_vault_rules,
}


def dispatch_action(
    body: dict[str, Any],
    *,
    bridge_factory: Any = Bridge,
    bridge: Any | None = None,
    http_port: int = 8765,
) -> dict[str, Any]:
    """Execute an interactive Studio Desktop App action and return structured status."""
    action = str(body.get("action") or "").strip()
    if not action:
        return {"ok": False, "action": "", "error": "missing action"}

    owns = bridge is None
    b: Any | None = None

    def get_bridge() -> Any:
        nonlocal b
        if b is None:
            b = bridge_factory() if owns else bridge
        return b

    try:
        host_fn = _HOST_ACTIONS.get(action)
        if host_fn is not None:
            return host_fn(body, http_port)
        if action == "notes":
            return _act_notes(body, get_bridge)
        if action in ("transport", "set", "step"):
            return _act_bridge(action, body, get_bridge, bridge_factory)
        return {"ok": False, "action": action, "error": f"unknown action: {action}"}
    except (Exception, SystemExit) as exc:
        return {"ok": False, "action": action, "error": str(exc)}
    finally:
        if owns and b is not None:
            try:
                b.close()
            except Exception:
                pass


def make_handler(bridge_factory: Any = Bridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, obj: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            from fl_mcp import clients, service_daemon
            from fl_mcp.assets import load_asset

            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_bytes(
                    load_html().encode("utf-8"), "text/html; charset=utf-8", "no-cache"
                )
                return
            if parsed.path in ("/favicon.ico", "/favicon.png"):
                loaded = load_asset("favicon.png")
                if loaded:
                    self._send_bytes(loaded[0], loaded[1], "public, max-age=3600")
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/manifest.webmanifest":
                from fl_mcp import app as studio_app

                body = json.dumps(studio_app.app_manifest(), ensure_ascii=False).encode("utf-8")
                self._send_bytes(body, "application/manifest+json", "public, max-age=3600")
                return
            if parsed.path.startswith("/assets/"):
                loaded = load_asset(parsed.path[len("/assets/") :])
                if loaded:
                    self._send_bytes(loaded[0], loaded[1], "public, max-age=3600")
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/api/state":
                refresh = "refresh_templates=1" in parsed.query
                self._send_json(build_state(refresh_templates=refresh))
                return
            if parsed.path == "/api/doctor":
                self._send_json(doctor_info())
                return
            if parsed.path == "/api/paths":
                self._send_json(paths.diagnostics())
                return
            if parsed.path == "/api/clients":
                self._send_json(
                    {
                        "clients": clients.client_statuses(),
                        "service": service_daemon.service_status(),
                    }
                )
                return
            if parsed.path == "/api/service":
                self._send_json(service_daemon.service_status())
                return
            if parsed.path == "/api/vault":
                from fl_mcp import vault

                self._send_json(vault.status())
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _read_json_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
                return raw if isinstance(raw, dict) else {}
            except Exception:
                return {}

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/poll":
                self._send_json(poll_fl_once(bridge_factory))
                return
            if parsed.path == "/api/action":
                port_num = int(getattr(self.server, "server_port", 8765))
                self._send_json(
                    dispatch_action(
                        self._read_json_body(),
                        bridge_factory=bridge_factory,
                        http_port=port_num,
                    )
                )
                return
            if parsed.path == "/api/traffic/clear":
                cleared = telemetry.clear_traffic()
                self._send_json({"cleared": cleared})
                return
            if parsed.path == "/api/templates/refresh":
                items = cached_templates(force=True)
                self._send_json({"count": len(items), "templates": items})
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def start_server(
    host: str = "127.0.0.1", port: int = 8765, bridge_factory: Any = Bridge
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(bridge_factory))


def serve_dashboard(
    port: int = 8765,
    open_browser: bool = True,
    poll_first: bool = False,
    *,
    backend: str = "auto",
) -> int:
    """Start the local Studio server and open it inside a native desktop app window."""
    from fl_mcp import app, service_daemon

    st = service_daemon.service_status()
    if st["running"] and st.get("port") == port:
        if poll_first:
            poll_fl_once()
        url = str(st["url"])
        print(f"FL-MCP Background Service already hosts Studio at {url}", flush=True)
        if open_browser:
            app.open_studio_window(url, backend=backend, host="127.0.0.1", port=port)
        return 0
    if poll_first:
        poll_fl_once()
    srv = start_server("127.0.0.1", port)
    actual_port = srv.server_port
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"FL-MCP Studio running at {url} (Ctrl+C to stop)", flush=True)
    rt = app.detect_runtime()
    chosen = rt["default_backend"] if backend == "auto" else backend
    if open_browser and chosen in ("webview", "native"):
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            app.open_studio_window(url, backend=chosen, host="127.0.0.1", port=actual_port)
        finally:
            srv.shutdown()
            srv.server_close()
        return 0
    if open_browser:
        threading.Timer(
            0.3,
            lambda: app.open_studio_window(url, backend=chosen, host="127.0.0.1", port=actual_port),
        ).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped FL-MCP Studio.")
    finally:
        srv.server_close()
    return 0
