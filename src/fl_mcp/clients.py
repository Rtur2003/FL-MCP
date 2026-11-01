"""How to register this server with each MCP client: the exact file and text, ready to paste."""

import json
import shlex
import shutil
import sys

NAME = "fl-studio"
REPO = "https://github.com/Rtur2003/FL-MCP"
SOURCES = ("pypi", "git", "local")


def command(source: str = "pypi", audio: bool = True) -> tuple[str, list[str]]:
    """The command and arguments that start the server."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    if source == "local":  # the interpreter that runs this: works from any virtual environment
        return sys.executable, ["-m", "fl_mcp"]
    spec = "fl-mcp[audio]" if audio else "fl-mcp"
    uvx = shutil.which("uvx") or "uvx"  # desktop apps often do not have uv on their PATH
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
    "codex": ("Codex CLI", "~/.codex/config.toml"),
}


def snippet(client: str, source: str = "pypi", audio: bool = True) -> tuple[str, str]:
    """(where it goes, the text) for one client."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    cmd, args = command(source, audio)
    where = CLIENTS[client][1]
    if client == "claude-code":
        return where, "claude mcp add " + NAME + " -- " + shlex.join([cmd, *args])
    if client == "codex":
        text = f"[mcp_servers.{NAME}]\ncommand = {json.dumps(cmd)}\nargs = {json.dumps(args)}\n"
        return where, text
    if client == "vscode":
        body = {"servers": {NAME: {"type": "stdio", "command": cmd, "args": args}}}
    else:
        body = {"mcpServers": {NAME: {"command": cmd, "args": args}}}
    return where, json.dumps(body, indent=2)
