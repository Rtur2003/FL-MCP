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
import json
import math
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fl_mcp import __version__, desk, paths, produce, telemetry
from fl_mcp.bridge import DEFAULT_PORT, Bridge, FLError
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


def doctor_info() -> dict[str, Any]:
    """Quick non-destructive host layer check (no MIDI ping side-effects)."""
    info: dict[str, Any] = {
        "version": __version__,
        "port_name": DEFAULT_PORT,
        "midi_inputs": [],
        "midi_outputs": [],
        "midi_port_found": False,
        "midi_error": "",
        "desktop_enabled": desk.enabled(),
        "data_dir": str(paths.data_dir()),
        "notes_dir": str(paths.notes_dir()),
    }
    try:
        import mido

        ins = list(mido.get_input_names())
        outs = list(mido.get_output_names())
        info["midi_inputs"] = ins
        info["midi_outputs"] = outs
        info["midi_port_found"] = any(DEFAULT_PORT in n for n in ins) and any(
            DEFAULT_PORT in n for n in outs
        )
    except Exception as exc:
        info["midi_error"] = str(exc)

    fl_user = paths.fl_user_dir()
    dev, scr = targets(fl_user)
    info["fl_script_installed"] = dev.is_file()
    info["piano_roll_script_installed"] = scr.is_file()
    info["fl_user_dir"] = str(fl_user)
    try:
        from fl_mcp import audio

        info["audio_analysis"] = audio.available()
    except Exception:
        info["audio_analysis"] = False
    return info


def poll_fl_once(bridge_factory: Any = Bridge) -> dict[str, Any]:
    """Connect briefly, snapshot live FL Studio state, update snapshot.json, and close cleanly."""
    b = bridge_factory()
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
        telemetry.update_snapshot(
            status=status,
            channels=channels,
            mixer=mixer,
            patterns=patterns,
            plugins=plugins,
            peaks=peaks,
        )
        return {"ok": True, "status": status}
    except (FLError, OSError, Exception) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
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
    return {
        "meta": {
            "version": __version__,
            "ts": round(time.time(), 3),
            "port": DEFAULT_PORT,
            "desktop_enabled": desk.enabled(),
            "data_dir": str(paths.data_dir()),
            "notes_dir": str(paths.notes_dir()),
        },
        "traffic": telemetry.read_traffic(limit=limit, kind=kind, only_errors=only_errors),
        "studio": telemetry.read_snapshot(),
        "doctor": doctor_info(),
        "templates": cached_templates(force=refresh_templates),
    }


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

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = load_html().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/state":
                refresh = "refresh_templates=1" in parsed.query
                self._send_json(build_state(refresh_templates=refresh))
                return
            if parsed.path == "/api/doctor":
                self._send_json(doctor_info())
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/poll":
                self._send_json(poll_fl_once(bridge_factory))
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


def serve_dashboard(port: int = 8765, open_browser: bool = True, poll_first: bool = False) -> int:
    """Start the local HTTP monitor server and block until Ctrl+C."""
    if poll_first:
        poll_fl_once()
    srv = start_server("127.0.0.1", port)
    actual_port = srv.server_port
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"FL-MCP Studio & Traffic Dashboard running at {url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped dashboard.")
    finally:
        srv.server_close()
    return 0
