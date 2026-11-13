"""Incoming/outgoing traffic telemetry and live studio snapshot cache.

Records every MIDI bridge request/response (TX/RX bytes, frame counts, retries,
latency, status) and MCP tool invocation to a bounded JSONL log under
``data_dir()``, plus an atomic ``snapshot.json`` state cache. Storing both on
disk lets a separate ``fl-mcp dashboard`` or ``fl-mcp traffic`` process watch
live AI <-> FL Studio activity started by Claude, Cursor, Codex, or any client.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fl_mcp import paths

MAX_FILE_BYTES = 384 * 1024
KEEP_LINES = 500
SUMMARY_CHARS = 180

_LOCK = threading.Lock()
_SEQ = 0
_PID = os.getpid()


def enabled() -> bool:
    """Traffic recording is on unless FL_MCP_TRAFFIC is explicitly false."""
    raw = os.environ.get("FL_MCP_TRAFFIC", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def traffic_path() -> Path:
    return paths.data_dir() / "traffic.jsonl"


def snapshot_path() -> Path:
    return paths.data_dir() / "snapshot.json"


def _clip(text: str, limit: int = SUMMARY_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def summarize(value: Any, limit: int = SUMMARY_CHARS) -> str:
    """Compact single-line representation of a request, reply, or tool args."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _clip(value, limit)
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return f"<{len(value)} bytes>"
        return _clip(text, limit)
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        encoded = str(value)
    return _clip(encoded, limit)


def _next_id() -> tuple[int, str]:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        seq = _SEQ
    return seq, f"{_PID}-{seq}"


def _append(event: dict[str, Any]) -> None:
    if not enabled():
        return
    path = traffic_path()
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if path.stat().st_size > MAX_FILE_BYTES:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                trimmed = "\n".join(lines[-KEEP_LINES:]) + "\n"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(trimmed, encoding="utf-8")
                os.replace(tmp, path)
        except OSError:
            pass


def record_midi(
    *,
    op: str,
    fid: int,
    req: Any,
    tx_bytes: int,
    tx_frames: int,
    rx_bytes: int,
    rx_frames: int,
    attempts: int,
    ms: float,
    ok: bool,
    error: str = "",
    res: Any = None,
) -> None:
    """Record one MIDI bridge transaction (TX request + RX response or failure)."""
    seq, eid = _next_id()
    _append(
        {
            "id": eid,
            "seq": seq,
            "ts": round(time.time(), 3),
            "dir": "midi",
            "op": op or "?",
            "fid": fid,
            "tx_bytes": tx_bytes,
            "tx_frames": tx_frames,
            "rx_bytes": rx_bytes,
            "rx_frames": rx_frames,
            "attempts": attempts,
            "ms": round(ms, 2),
            "ok": ok,
            "req": summarize(req),
            "res": summarize(res) if ok else "",
            "error": _clip(error, 200) if error else "",
        }
    )


def record_mcp(
    *,
    tool: str,
    args: dict[str, Any] | None,
    out_bytes: int,
    ms: float,
    ok: bool,
    error: str = "",
) -> None:
    """Record one MCP tool invocation and outcome."""
    seq, eid = _next_id()
    clean_args = {k: v for k, v in (args or {}).items() if v is not None}
    _append(
        {
            "id": eid,
            "seq": seq,
            "ts": round(time.time(), 3),
            "dir": "mcp",
            "op": tool,
            "tx_bytes": len(json.dumps(clean_args, default=str)) if clean_args else 2,
            "rx_bytes": out_bytes,
            "ms": round(ms, 2),
            "ok": ok,
            "req": summarize(clean_args) if clean_args else "{}",
            "res": f"{out_bytes}B" if ok else "",
            "error": _clip(error, 200) if error else "",
        }
    )


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return default


def update_snapshot(**sections: Any) -> None:
    """Merge named sections into ``snapshot.json`` atomically."""
    if not enabled() or not sections:
        return
    path = snapshot_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            cur = _load_json(path, {})
            if not isinstance(cur, dict):
                cur = {}
            cur.update(sections)
            cur["updated"] = round(time.time(), 3)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(cur, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError:
            pass


def read_snapshot() -> dict[str, Any]:
    """Return latest studio snapshot plus queued/exported piano roll and latest plan meta."""
    snap = _load_json(snapshot_path(), {})
    if not isinstance(snap, dict):
        snap = {}
    nd = paths.notes_dir()
    inbox = _load_json(nd / "inbox.json", None)
    state = _load_json(nd / "state.json", None)
    plans_dir = paths.data_dir() / "plans"
    latest_plan = None
    try:
        if plans_dir.is_dir():
            files = sorted(
                plans_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                p = json.loads(files[0].read_text(encoding="utf-8"))
                latest_plan = {
                    "id": p.get("id"),
                    "genre": p.get("genre"),
                    "key": p.get("key"),
                    "bpm": p.get("bpm"),
                    "bars": p.get("bars"),
                    "roles": p.get("roles", []),
                    "patterns": [
                        {
                            "name": pat.get("name"),
                            "bars": pat.get("bars"),
                            "parts": list((pat.get("parts") or {}).keys()),
                        }
                        for pat in (p.get("patterns") or [])[:12]
                    ],
                    "sections": p.get("sections", []),
                    "mix": p.get("mix", {}),
                }
    except (OSError, ValueError):
        pass
    out = dict(snap)
    if inbox is not None:
        out["piano_inbox"] = {
            "serial": inbox.get("serial"),
            "replace": inbox.get("replace"),
            "count": len(inbox.get("notes") or []),
            "notes": (inbox.get("notes") or [])[:500],
        }
    if state is not None:
        out["piano_state"] = {
            "serial": state.get("serial"),
            "ts": state.get("ts"),
            "count": state.get("count", len(state.get("notes") or [])),
            "applied": bool(
                inbox
                and inbox.get("serial") is not None
                and inbox.get("serial") == state.get("serial")
            ),
            "notes": (state.get("notes") or [])[:500],
        }
    if latest_plan is not None:
        out["latest_plan"] = latest_plan
    return out


def read_all_events() -> list[dict[str, Any]]:
    path = traffic_path()
    try:
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except ValueError:
            continue
    return events


def compute_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    midi = [e for e in events if e.get("dir") == "midi"]
    mcp = [e for e in events if e.get("dir") == "mcp"]
    midi_lat = sorted(float(e.get("ms") or 0) for e in midi)
    p95 = midi_lat[min(len(midi_lat) - 1, int(0.95 * len(midi_lat)))] if midi_lat else 0.0
    by_op: dict[str, int] = {}
    for e in midi:
        op = str(e.get("op") or "?")
        by_op[op] = by_op.get(op, 0) + 1
    by_tool: dict[str, int] = {}
    for e in mcp:
        tool = str(e.get("op") or "?")
        by_tool[tool] = by_tool.get(tool, 0) + 1
    errors = [e for e in events if not e.get("ok")]
    return {
        "total": len(events),
        "midi_calls": len(midi),
        "midi_errors": sum(1 for e in midi if not e.get("ok")),
        "retries": sum(max(0, int(e.get("attempts") or 1) - 1) for e in midi),
        "tx_bytes": sum(int(e.get("tx_bytes") or 0) for e in midi),
        "rx_bytes": sum(int(e.get("rx_bytes") or 0) for e in midi),
        "tx_frames": sum(int(e.get("tx_frames") or 0) for e in midi),
        "rx_frames": sum(int(e.get("rx_frames") or 0) for e in midi),
        "avg_ms": round(sum(midi_lat) / len(midi_lat), 2) if midi_lat else 0.0,
        "p95_ms": round(p95, 2),
        "mcp_calls": len(mcp),
        "mcp_errors": sum(1 for e in mcp if not e.get("ok")),
        "last_op": midi[-1].get("op") if midi else None,
        "last_error": errors[-1].get("error") if errors else None,
        "by_op": dict(sorted(by_op.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def read_traffic(
    *,
    limit: int = 100,
    kind: str | None = None,
    only_errors: bool = False,
) -> dict[str, Any]:
    """Read logged traffic events and aggregate TX/RX metrics."""
    events = read_all_events()
    stats = compute_stats(events)
    filtered = events
    if kind in ("midi", "mcp"):
        filtered = [e for e in filtered if e.get("dir") == kind]
    if only_errors:
        filtered = [e for e in filtered if not e.get("ok")]
    if limit > 0:
        filtered = filtered[-limit:]
    return {"stats": stats, "events": filtered}


def brief(limit: int = 8) -> dict[str, Any]:
    """Compact incoming/outgoing summary returned inside ``fl_status(log=True)``."""
    data = read_traffic(limit=limit)
    st = data["stats"]
    return {
        "midi_calls": st["midi_calls"],
        "midi_errors": st["midi_errors"],
        "retries": st["retries"],
        "tx_bytes": st["tx_bytes"],
        "rx_bytes": st["rx_bytes"],
        "avg_ms": st["avg_ms"],
        "p95_ms": st["p95_ms"],
        "recent": [
            {
                "dir": e.get("dir"),
                "op": e.get("op"),
                "tx_B": e.get("tx_bytes"),
                "rx_B": e.get("rx_bytes"),
                "ms": e.get("ms"),
                "ok": e.get("ok"),
                **({"err": e["error"]} if e.get("error") else {}),
            }
            for e in data["events"]
        ],
    }


def clear_traffic() -> int:
    """Clear logged traffic events and return how many entries existed."""
    path = traffic_path()
    with _LOCK:
        n = len(read_all_events())
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    return n
