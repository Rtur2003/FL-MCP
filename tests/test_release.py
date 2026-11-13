"""Tests for packaging/build_release.py and standalone release bundles."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_release_under_test", ROOT / "packaging" / "build_release.py"
)
build_release_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release_mod)


def test_build_release_creates_valid_bundles_and_checksums(tmp_path):
    from fl_mcp import __version__

    out_dir = tmp_path / "release"
    built = build_release_mod.build_release(out_dir, include_python=False)
    names = {p.name for p in built}

    mcpb = out_dir / f"fl-mcp-{__version__}.mcpb"
    plugin_zip = out_dir / f"fl-studio-claude-plugin-{__version__}.zip"
    scripts_zip = out_dir / f"fl-mcp-fl-scripts-{__version__}.zip"
    notes = out_dir / "RELEASE_NOTES.md"
    checksums = out_dir / "SHA256SUMS.txt"

    assert {mcpb.name, plugin_zip.name, scripts_zip.name, notes.name, checksums.name}.issubset(
        names
    )

    with zipfile.ZipFile(mcpb) as zf:
        znames = set(zf.namelist())
        assert {"manifest.json", "src/server.py"}.issubset(znames)

    with zipfile.ZipFile(plugin_zip) as zf:
        znames = set(zf.namelist())
        assert {
            "plugin/.claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
        }.issubset(znames)

    with zipfile.ZipFile(scripts_zip) as zf:
        znames = set(zf.namelist())
        assert {
            "Hardware/FLMCP/device_FLMCP.py",
            "Piano roll scripts/FL MCP.pyscript",
            "install-fl-scripts.py",
            "install-fl-scripts.ps1",
            "INSTALL.txt",
        }.issubset(znames)

    assert f"# FL-MCP v{__version__}" in notes.read_text(encoding="utf-8")

    lines = [
        line.strip() for line in checksums.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    recorded = {}
    for line in lines:
        digest, fname = line.split("  ", 1)
        recorded[fname] = digest
        assert hashlib.sha256((out_dir / fname).read_bytes()).hexdigest() == digest
    assert {mcpb.name, plugin_zip.name, scripts_zip.name} == set(recorded)

    # Verify the offline FL Studio installer extracted from the standalone zip works end-to-end.
    unpacked = tmp_path / "scripts_unpacked"
    with zipfile.ZipFile(scripts_zip) as zf:
        zf.extractall(unpacked)
    fake_fl_user = tmp_path / "Documents" / "Image-Line" / "FL Studio"
    (fake_fl_user / "Settings").mkdir(parents=True)
    res = subprocess.run(
        [sys.executable, str(unpacked / "install-fl-scripts.py"), str(fake_fl_user)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr + res.stdout
    assert (fake_fl_user / "Settings" / "Hardware" / "FLMCP" / "device_FLMCP.py").is_file()
    pyscript = (fake_fl_user / "Settings" / "Piano roll scripts" / "FL MCP.pyscript").read_text(
        encoding="utf-8"
    )
    assert '"@DIR@"' not in pyscript
    expected_dir = json.dumps(str((fake_fl_user / "Settings" / "Piano roll scripts").resolve()))
    assert expected_dir in pyscript
