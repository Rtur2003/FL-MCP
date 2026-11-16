"""How to register this server with each MCP client: inspect or write config files safely."""

import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

NAME = "fl-studio"
REPO = "https://github.com/Rtur2003/FL-MCP"
SOURCES = ("pypi", "git", "local")
TRANSPORTS = ("stdio", "http")
DEFAULT_HTTP_URL = "http://127.0.0.1:8765/mcp"


def resolve_uvx() -> str:
    """Locate uvx on PATH or common user install locations (for GUI apps with minimal PATH)."""
    found = shutil.which("uvx")
    if found:
        return found
    home = Path.home()
    exe_name = "uvx.exe" if os.name == "nt" else "uvx"
    for cand in (
        home / ".local" / "bin" / exe_name,
        home / ".cargo" / "bin" / exe_name,
        Path(sys.executable).parent / exe_name,
    ):
        if cand.is_file():
            return str(cand)
    return "uvx"


def command(
    source: str = "pypi", audio: bool = True, *, resolve: bool = False
) -> tuple[str, list[str]]:
    """The command and arguments that start the server."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    if source == "local":  # works from venv, uv tool, or standalone frozen app binary
        if getattr(sys, "frozen", False):
            return sys.executable, ["--mcp"]
        return sys.executable, ["-m", "fl_mcp"]
    spec = "fl-mcp[audio]" if audio else "fl-mcp"
    uvx = resolve_uvx() if resolve else (shutil.which("uvx") or "uvx")
    if source == "git":
        return uvx, ["--from", f"{spec} @ git+{REPO}", "fl-mcp"]
    return uvx, ["--from", spec, "fl-mcp"] if audio else ["fl-mcp"]


CLIENTS = {
    "claude-desktop": (
        "Claude Desktop",
        "Settings > Developer > Edit Config (claude_desktop_config.json). "
        "Or install the .mcpb bundle by double-clicking it.",
    ),
    "claude-code": ("Claude Code", "run this once in a terminal"),
    "cursor": ("Cursor", "~/.cursor/mcp.json (or .cursor/mcp.json in a project)"),
    "vscode": ("VS Code (Copilot)", ".vscode/mcp.json in the project"),
    "windsurf": ("Windsurf", "~/.codeium/windsurf/mcp_config.json"),
    "gemini": ("Gemini CLI", "~/.gemini/settings.json"),
    "antigravity": ("Antigravity", "~/.gemini/antigravity/mcp_config.json"),
    "codex": ("Codex CLI", "~/.codex/config.toml"),
}


def _server_entry(
    client: str,
    cmd: str = "",
    args: list[str] | None = None,
    *,
    transport: str = "stdio",
    url: str | None = None,
) -> dict[str, Any]:
    if transport == "http":
        endpoint = url or DEFAULT_HTTP_URL
        if client == "vscode":
            return {"type": "http", "url": endpoint}
        if client == "gemini":
            return {"httpUrl": endpoint}
        if client == "windsurf":
            return {"serverUrl": endpoint}
        if client == "claude-desktop":
            return {"command": "npx", "args": ["-y", "mcp-remote", endpoint, "--allow-http"]}
        return {"url": endpoint}
    arg_list = args or []
    return (
        {"type": "stdio", "command": cmd, "args": arg_list}
        if client == "vscode"
        else {"command": cmd, "args": arg_list}
    )


def config_path(client: str, project: Path | None = None) -> Path | None:
    """Default configuration file path for client (None for CLI-managed clients)."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    if client == "claude-code":
        return None
    home = Path.home()
    if client == "claude-desktop":
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
        return base / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return (Path(project) if project else home) / ".cursor" / "mcp.json"
    if client == "vscode":
        return (Path(project) if project else Path.cwd()) / ".vscode" / "mcp.json"
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    if client == "gemini":
        return home / ".gemini" / "settings.json"
    if client == "antigravity":
        ide_dir = home / ".gemini" / "antigravity"
        cli_cfg = home / ".gemini" / "config" / "mcp_config.json"
        if not ide_dir.exists() and cli_cfg.parent.exists():
            return cli_cfg
        return ide_dir / "mcp_config.json"
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    return codex_home / "config.toml"


def _validate_transport(transport: str) -> None:
    if transport not in TRANSPORTS:
        raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}")


def snippet(
    client: str,
    source: str = "pypi",
    audio: bool = True,
    *,
    transport: str = "stdio",
    url: str | None = None,
) -> tuple[str, str]:
    """(where it goes, the text) for one client."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    _validate_transport(transport)
    where = CLIENTS[client][1]
    if transport == "http":
        endpoint = url or DEFAULT_HTTP_URL
        if client == "claude-code":
            return where, f"claude mcp add --transport http {NAME} {endpoint}"
        if client == "codex":
            return where, f"[mcp_servers.{NAME}]\nurl = {json.dumps(endpoint)}\n"
        key = "servers" if client == "vscode" else "mcpServers"
        entry = _server_entry(client, transport="http", url=endpoint)
        return where, json.dumps({key: {NAME: entry}}, indent=2)
    cmd, args = command(source, audio)
    if client == "claude-code":
        cmdline = (
            subprocess.list2cmdline([cmd, *args]) if os.name == "nt" else shlex.join([cmd, *args])
        )
        return where, "claude mcp add " + NAME + " -- " + cmdline
    if client == "codex":
        text = f"[mcp_servers.{NAME}]\ncommand = {json.dumps(cmd)}\nargs = {json.dumps(args)}\n"
        return where, text
    key = "servers" if client == "vscode" else "mcpServers"
    return where, json.dumps({key: {NAME: _server_entry(client, cmd, args)}}, indent=2)


def _merge_json(existing: str, key: str, name: str, entry: dict[str, Any]) -> str:
    data: Any = json.loads(existing) if existing.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in config, got {type(data).__name__}")
    section = data.get(key)
    if section is None:
        section = {}
        data[key] = section
    elif not isinstance(section, dict):
        raise ValueError(f"expected '{key}' object in config")
    section[name] = entry
    return json.dumps(data, indent=2) + "\n"


_CODEX_SECTION = re.compile(
    rf'^\s*\[mcp_servers\.(?:{re.escape(NAME)}|"{re.escape(NAME)}")(?:\.[^\]]+)?\]\s*(?:#.*)?$'
)
_ANY_TABLE = re.compile(r"^\s*\[")


def _tomllib():
    try:
        return importlib.import_module("tomllib")
    except ModuleNotFoundError:
        return None


def _merge_codex_toml(
    existing: str,
    name: str,
    cmd: str = "",
    args: list[str] | None = None,
    *,
    transport: str = "stdio",
    url: str | None = None,
) -> str:
    tl = _tomllib()
    if tl and existing.strip():
        tl.loads(existing)  # fail early rather than patching corrupt TOML
    kept: list[str] = []
    skip = False
    for line in existing.splitlines(keepends=True):
        if _CODEX_SECTION.match(line):
            skip = True
            continue
        if skip and _ANY_TABLE.match(line):
            skip = False
        if not skip:
            kept.append(line)
    body = "".join(kept).rstrip()
    if transport == "http":
        endpoint = url or DEFAULT_HTTP_URL
        block = f"[mcp_servers.{name}]\nurl = {json.dumps(endpoint)}\n"
    else:
        arg_list = args or []
        block = (
            f"[mcp_servers.{name}]\ncommand = {json.dumps(cmd)}\nargs = {json.dumps(arg_list)}\n"
        )
    merged = (body + "\n\n" + block) if body else block
    if tl:
        parsed = tl.loads(merged)
        entry = (parsed.get("mcp_servers") or {}).get(name) or {}
        if transport == "http":
            if entry.get("url") != (url or DEFAULT_HTTP_URL):
                raise ValueError("unable to update [mcp_servers.fl-studio] cleanly in config.toml")
        elif entry.get("command") != cmd or entry.get("args") != (args or []):
            raise ValueError("unable to update [mcp_servers.fl-studio] cleanly in config.toml")
    return merged


def install_client(
    client: str,
    source: str = "pypi",
    audio: bool = True,
    *,
    project: Path | None = None,
    path: Path | None = None,
    dry_run: bool = False,
    transport: str = "stdio",
    url: str | None = None,
) -> dict[str, Any]:
    """Register FL-MCP with one MCP client, writing a .bak backup before editing existing files."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    _validate_transport(transport)
    endpoint = url or DEFAULT_HTTP_URL
    cmd, args = ("", []) if transport == "http" else command(source, audio, resolve=True)
    if client == "claude-code":
        argv = (
            ["claude", "mcp", "add", "--transport", "http", NAME, endpoint]
            if transport == "http"
            else ["claude", "mcp", "add", NAME, "--", cmd, *args]
        )
        if dry_run:
            return {"client": client, "method": "cli", "command": argv, "written": False}
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError(
                f"claude CLI not found on PATH; run: "
                f"{snippet(client, source, audio, transport=transport, url=endpoint)[1]}"
            )
        res = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "").strip() or f"exit {res.returncode}"
            raise RuntimeError(f"claude mcp add failed: {msg}")
        return {"client": client, "method": "cli", "command": [exe, *argv[1:]], "written": True}
    target = Path(path) if path is not None else config_path(client, project)
    assert target is not None
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if client == "codex":
        updated = _merge_codex_toml(existing, NAME, cmd, args, transport=transport, url=endpoint)
    else:
        key = "servers" if client == "vscode" else "mcpServers"
        entry = _server_entry(client, cmd, args, transport=transport, url=endpoint)
        updated = _merge_json(existing, key, NAME, entry)
    changed = updated != existing
    backup: str | None = None
    if not dry_run and changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            bak = Path(str(target) + ".bak")
            shutil.copy2(target, bak)
            backup = str(bak)
        target.write_text(updated, encoding="utf-8")
    return {
        "client": client,
        "method": "file",
        "transport": transport,
        "path": str(target),
        "backup": backup,
        "changed": changed,
        "written": not dry_run and changed,
        "config": updated,
    }


def client_statuses(project: Path | None = None) -> list[dict[str, Any]]:
    """Inspect whether FL-MCP is registered in each supported AI client config."""
    out: list[dict[str, Any]] = []
    for key, (label, where) in CLIENTS.items():
        cfg = config_path(key, project)
        configured = False
        transport = ""
        error = ""
        if cfg is not None and cfg.is_file():
            try:
                raw = cfg.read_text(encoding="utf-8")
                if key == "codex":
                    tl = _tomllib()
                    if tl:
                        data = tl.loads(raw)
                        entry = (data.get("mcp_servers") or {}).get(NAME)
                    else:
                        in_sec = False
                        found_sec = False
                        has_url = False
                        for line in raw.splitlines():
                            if _CODEX_SECTION.match(line):
                                in_sec = True
                                found_sec = True
                                continue
                            if in_sec and _ANY_TABLE.match(line):
                                break
                            if in_sec and re.match(r"^\s*url\s*=", line):
                                has_url = True
                                break
                        entry = (
                            ({"url": "http"} if has_url else {"match": True}) if found_sec else None
                        )
                else:
                    data = json.loads(raw)
                    sec_key = "servers" if key == "vscode" else "mcpServers"
                    entry = (data.get(sec_key) or {}).get(NAME) if isinstance(data, dict) else None
                if isinstance(entry, dict):
                    configured = True
                    args_val = entry.get("args")
                    has_remote = isinstance(args_val, list) and any(
                        "mcp-remote" in str(a) for a in args_val
                    )
                    transport = (
                        "http"
                        if (
                            "url" in entry
                            or "httpUrl" in entry
                            or "serverUrl" in entry
                            or has_remote
                        )
                        else "stdio"
                    )
            except Exception as exc:
                error = str(exc)
        out.append(
            {
                "id": key,
                "name": label,
                "hint": where,
                "path": str(cfg) if cfg is not None else None,
                "exists": bool(cfg and cfg.is_file()),
                "configured": configured,
                "transport": transport,
                "error": error,
            }
        )
    return out
