"""The Claude Desktop extension: structure, manifest and a real start of the bundled server."""

import hashlib
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from tests.names import NAMES

pytest.importorskip("tomllib")

SPEC = importlib.util.spec_from_file_location(
    "build_mcpb", Path(__file__).resolve().parent.parent / "packaging" / "build_mcpb.py"
)
build_mcpb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_mcpb)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return build_mcpb.build(tmp_path_factory.mktemp("dist"))


def read(bundle, name):
    with zipfile.ZipFile(bundle) as z:
        return z.read(name)


def test_the_bundle_has_what_the_uv_runtime_needs(bundle):
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
    assert {"manifest.json", "pyproject.toml", "LICENSE", "src/server.py"} <= names
    assert "src/fl_mcp/server.py" in names and "src/fl_mcp/commerce/issuer.json" in names
    assert "src/fl_mcp/fl/device_FLMCP.py" in names and "src/fl_mcp/api.json" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if n.startswith(("server/lib", "server/venv", ".venv"))]
    assert not [n for n in names if n.startswith(("tests", "service"))]


def test_the_manifest_matches_the_spec_and_the_code(bundle):
    from fl_mcp import __version__

    m = json.loads(read(bundle, "manifest.json"))
    for field in ("manifest_version", "name", "version", "description", "author", "server"):
        assert m[field]
    assert m["manifest_version"] == "0.4" and m["version"] == __version__
    assert m["server"]["type"] == "uv" and m["server"]["entry_point"] == "src/server.py"
    assert m["compatibility"]["platforms"] == ["darwin", "win32"]
    key = m["user_config"]["license_key"]
    assert key["sensitive"] is True and key["required"] is False and key["type"] == "string"
    assert m["server"]["mcp_config"]["env"]["FL_MCP_LICENSE"] == "${user_config.license_key}"
    assert len(m["tools"]) == len(NAMES) + 1  # fl_desk is listed too (opt-in)
    assert all(t["name"] and t["description"] for t in m["tools"])
    assert {t["name"] for t in m["tools"]} >= {"fl_produce", "fl_mix", "fl_status", "fl_desk"}
    assert m["user_config"]["desktop"]["default"] is False
    assert m["server"]["mcp_config"]["env"]["FL_MCP_DESKTOP"] == "${user_config.desktop}"


def test_the_bundle_installs_dependencies_but_not_the_project(bundle):
    import tomllib

    pyproject = tomllib.loads(read(bundle, "pyproject.toml").decode())
    assert "build-system" not in pyproject
    deps = " ".join(pyproject["project"]["dependencies"])
    assert "mcp" in deps and "mido" in deps and "python-rtmidi" in deps
    assert "fastapi" not in deps and "httpx" not in deps


def test_the_same_source_gives_the_same_bytes(bundle, tmp_path):
    again = build_mcpb.build(tmp_path)
    assert (
        hashlib.sha256(again.read_bytes()).hexdigest()
        == hashlib.sha256(bundle.read_bytes()).hexdigest()
    )


def test_a_service_address_is_baked_in_only_when_asked(bundle, tmp_path):
    assert json.loads(read(bundle, "src/fl_mcp/commerce/issuer.json")) == {"service": ""}
    metered = build_mcpb.build(tmp_path, "https://licence.example.test")
    assert json.loads(read(metered, "src/fl_mcp/commerce/issuer.json")) == {
        "service": "https://licence.example.test"
    }


@pytest.mark.parametrize(
    "url", ["http://licence.example.test", "licence.example.test", "https://x.test/?a=1", "ftp://x"]
)
def test_only_plain_https_addresses_are_accepted(url):
    with pytest.raises(SystemExit):
        build_mcpb.check_service(url)
    assert (
        build_mcpb.check_service("https://licence.example.test/") == "https://licence.example.test"
    )


def test_the_bundled_server_starts_and_lists_its_tools(bundle, tmp_path):
    root = tmp_path / "unpacked"
    with zipfile.ZipFile(bundle) as z:
        z.extractall(root)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-I", str(root / "src" / "server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=root,
        env={**os.environ, "FL_MCP_DESKTOP": "0"},
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()
    replies: dict[int, dict] = {}
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
        while 2 not in replies:  # a real client keeps the pipe open until it has its answers
            reply = json.loads(lines.get(timeout=60))
            if "id" in reply:
                replies[reply["id"]] = reply
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert replies[1]["result"]["serverInfo"]["name"] == "fl_mcp"
    assert len(replies[2]["result"]["tools"]) == len(NAMES)
