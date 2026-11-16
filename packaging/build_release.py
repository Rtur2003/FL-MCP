"""Build all GitHub Release assets for FL-MCP into ``dist/``.

Usage:
    python packaging/build_release.py [--out dist] [--service URL] [--no-python-build]

Artifacts produced:
    - fl_mcp-<ver>-py3-none-any.whl        (Python wheel, unless --no-python-build)
    - fl_mcp-<ver>.tar.gz                  (Python source archive, unless --no-python-build)
    - fl-mcp-<ver>.mcpb                    (Claude Desktop extension bundle)
    - fl-studio-claude-plugin-<ver>.zip    (Claude Code plugin + marketplace bundle)
    - fl-mcp-fl-scripts-<ver>.zip          (Standalone FL Studio scripts + offline installer)
    - RELEASE_NOTES.md                     (Changelog section for this version)
    - SHA256SUMS.txt                       (SHA-256 manifest for every release file)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))

import build_mcpb  # noqa: E402

from fl_mcp import __version__  # noqa: E402

STAMP = build_mcpb.STAMP
PLUGIN_EXCLUDE = {"__pycache__", ".DS_Store"}

STANDALONE_INSTALL_PY = '''"""Offline FL Studio script installer from fl-mcp-fl-scripts."""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _win_user_data_candidates() -> tuple[list[Path], Path | None]:
    candidates: list[Path] = []
    shell_docs: Path | None = None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Image-Line") as root:
            sub_count = winreg.QueryInfoKey(root)[0]
            fl_keys = [
                winreg.EnumKey(root, idx)
                for idx in range(sub_count)
                if winreg.EnumKey(root, idx).startswith("FL Studio")
            ]
            for key_name in sorted(fl_keys, reverse=True):
                try:
                    with winreg.OpenKey(root, rf"{key_name}\\General") as gen:
                        val, _ = winreg.QueryValueEx(gen, "User data folder")
                        if isinstance(val, str) and val.strip():
                            candidates.append(Path(val.strip()).expanduser())
                except OSError:
                    continue
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\\Image-Line\\Shared\\Paths") as key:
                    val, _ = winreg.QueryValueEx(key, "Shared data")
                    if isinstance(val, str) and val.strip():
                        candidates.append(Path(val.strip()).expanduser() / "FL Studio")
            except OSError:
                continue
    except OSError:
        pass
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            shell_docs = Path(buf.value)
            candidates.append(shell_docs / "Image-Line" / "FL Studio")
    except Exception:
        pass
    return candidates, shell_docs


def fl_user_dir() -> Path:
    env_user = os.environ.get("FL_MCP_FL_USER_DIR", "").strip()
    if env_user:
        return Path(env_user).expanduser().resolve()
    home = Path(os.environ["HOME"]).expanduser() if "HOME" in os.environ else Path.home()
    default = home / "Documents" / "Image-Line" / "FL Studio"
    if (default / "Settings").is_dir() or default.is_dir():
        return default
    candidates: list[Path] = []
    shell_docs: Path | None = None
    if sys.platform == "win32":
        reg_cands, shell_docs = _win_user_data_candidates()
        candidates.extend(reg_cands)
    candidates.append(home / "OneDrive" / "Documents" / "Image-Line" / "FL Studio")
    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "Image-Line" / "FL Studio")
    elif sys.platform != "win32":
        for prefix in [
            Path(p).expanduser()
            for p in (os.environ.get("WINEPREFIX", ""), str(home / ".wine"))
            if p
        ]:
            users_dir = prefix / "drive_c" / "users"
            if users_dir.is_dir():
                for u in sorted(users_dir.iterdir()):
                    if u.is_dir():
                        candidates.append(u / "Documents" / "Image-Line" / "FL Studio")
    for cand in candidates:
        if (cand / "Settings").is_dir():
            return cand
    for cand in candidates:
        if cand.is_dir():
            return cand
    if shell_docs is not None and shell_docs.is_dir():
        return shell_docs / "Image-Line" / "FL Studio"
    return default


def _configure_loopmidi() -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg

        base = r"Software\\Tobias Erichsen\\loopMIDI"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "DetectFeedback", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "CommandTreshhold", 0, winreg.REG_DWORD, 50000)
        port = os.environ.get("FL_MCP_PORT") or "FL MCP"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\\Ports") as key:
            winreg.SetValueEx(key, port, 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def main() -> int:
    root = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else fl_user_dir()
    settings = root / "Settings"
    if not settings.is_dir():
        print(f"ERROR: FL Studio Settings folder not found: {settings}")
        print(
            "Pass the FL Studio user data folder (containing Settings/) as argument or set "
            "FL_MCP_FL_USER_DIR."
        )
        return 1
    hw_dir = settings / "Hardware" / "FLMCP"
    pr_dir = settings / "Piano roll scripts"
    hw_dir.mkdir(parents=True, exist_ok=True)
    pr_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "Hardware" / "FLMCP" / "device_FLMCP.py", hw_dir / "device_FLMCP.py")
    notes_env = os.environ.get("FL_MCP_DIR", "").strip()
    notes_dir = Path(notes_env).expanduser().resolve() if notes_env else pr_dir.resolve()
    notes_dir.mkdir(parents=True, exist_ok=True)
    tpl = (HERE / "Piano roll scripts" / "FL MCP.pyscript").read_text(encoding="utf-8")
    (pr_dir / "FL MCP.pyscript").write_text(
        tpl.replace('"@DIR@"', json.dumps(str(notes_dir))),
        encoding="utf-8",
    )
    _configure_loopmidi()
    port = os.environ.get("FL_MCP_PORT") or "FL MCP"
    print(f"Installed controller script: {hw_dir / 'device_FLMCP.py'}")
    print(f"Installed piano roll script: {pr_dir / 'FL MCP.pyscript'}")
    print(f"Next: in FL Studio > Options > MIDI Settings enable '{port}' on Input & Output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

STANDALONE_INSTALL_PS1 = r"""param(
    [string]$FlUserDir = ""
)
$ErrorActionPreference = "Stop"
function Resolve-FlUserDir {
    if ($env:FL_MCP_FL_USER_DIR -and (Test-Path (Join-Path $env:FL_MCP_FL_USER_DIR "Settings"))) {
        return $env:FL_MCP_FL_USER_DIR
    }
    $cands = [System.Collections.Generic.List[string]]::new()
    $docs = [Environment]::GetFolderPath("MyDocuments")
    if ($docs) { $cands.Add((Join-Path $docs "Image-Line\FL Studio")) }
    try {
        $ilKeys = Get-ChildItem "HKCU:\Software\Image-Line" -ErrorAction SilentlyContinue |
            Where-Object { $_.PSChildName -like "FL Studio*" } |
            Sort-Object PSChildName -Descending
        foreach ($k in $ilKeys) {
            $genPath = Join-Path $k.PSPath "General"
            $gen = Get-ItemProperty -Path $genPath -ErrorAction SilentlyContinue
            if ($gen -and $gen."User data folder") { $cands.Add([string]$gen."User data folder") }
        }
        $sharedPaths = @(
            "HKCU:\Software\Image-Line\Shared\Paths",
            "HKLM:\Software\Image-Line\Shared\Paths"
        )
        foreach ($hive in $sharedPaths) {
            $sh = Get-ItemProperty -Path $hive -ErrorAction SilentlyContinue
            if ($sh -and $sh."Shared data") {
                $cands.Add((Join-Path ([string]$sh."Shared data") "FL Studio"))
            }
        }
    } catch {}
    if ($env:USERPROFILE) {
        $cands.Add((Join-Path $env:USERPROFILE "OneDrive\Documents\Image-Line\FL Studio"))
        $cands.Add((Join-Path $env:USERPROFILE "Documents\Image-Line\FL Studio"))
    }
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c "Settings"))) { return $c }
    }
    if ($docs) { return (Join-Path $docs "Image-Line\FL Studio") }
    return (Join-Path $env:USERPROFILE "Documents\Image-Line\FL Studio")
}
if (-not $FlUserDir) {
    $FlUserDir = Resolve-FlUserDir
}
$settings = Join-Path $FlUserDir "Settings"
if (-not (Test-Path $settings)) {
    throw "FL Studio Settings not found at '$settings'. Pass -FlUserDir or set FL_MCP_FL_USER_DIR."
}
$hw = Join-Path $settings "Hardware\FLMCP"
$pr = Join-Path $settings "Piano roll scripts"
New-Item -ItemType Directory -Force -Path $hw, $pr | Out-Null
$srcDev = Join-Path $PSScriptRoot "Hardware\FLMCP\device_FLMCP.py"
Copy-Item -Force $srcDev (Join-Path $hw "device_FLMCP.py")
$notesTarget = if ($env:FL_MCP_DIR) {
    New-Item -ItemType Directory -Force -Path $env:FL_MCP_DIR | Out-Null
    (Resolve-Path $env:FL_MCP_DIR).Path
} else {
    (Resolve-Path $pr).Path
}
$escaped = ConvertTo-Json $notesTarget
$srcPy = Join-Path $PSScriptRoot "Piano roll scripts\FL MCP.pyscript"
$tpl = Get-Content -Raw -Encoding UTF8 $srcPy
$dstPy = Join-Path $pr "FL MCP.pyscript"
Set-Content -Encoding UTF8 -NoNewline -Path $dstPy -Value ($tpl.Replace('"@DIR@"', $escaped))
# Configure loopMIDI port if loopMIDI is installed or planned
$portName = if ($env:FL_MCP_PORT) { $env:FL_MCP_PORT } else { "FL MCP" }
try {
    $lm = "HKCU:\Software\Tobias Erichsen\loopMIDI"
    $lmPorts = Join-Path $lm "Ports"
    New-Item -Path $lmPorts -Force | Out-Null
    Set-ItemProperty -Path $lm -Name "DetectFeedback" -Type DWord -Value 0
    Set-ItemProperty -Path $lm -Name "CommandTreshhold" -Type DWord -Value 50000
    Set-ItemProperty -Path $lmPorts -Name $portName -Type DWord -Value 1
} catch {}
Write-Host "Installed FL-MCP scripts to $settings"
Write-Host "Restart FL Studio and enable '$portName' Input & Output in Options > MIDI Settings."
"""

STANDALONE_README = """# FL-MCP Standalone FL Studio Scripts

Use this bundle when installing via `.mcpb` or without running `fl-mcp setup`.

## Quick Install

- **Windows (PowerShell)**:
  Right-click `install-fl-scripts.ps1` -> *Run with PowerShell*, or run:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\\install-fl-scripts.ps1
  ```
- **Windows / macOS (Python 3)**:
  ```bash
  python3 install-fl-scripts.py
  ```
- **Manual copy**:
  1. Copy `Hardware/FLMCP/device_FLMCP.py` into `<FL User Data>/Settings/Hardware/FLMCP/`
  2. Copy `Piano roll scripts/FL MCP.pyscript` into `<FL User Data>/Settings/Piano roll scripts/`
     and replace `"@DIR@"` with the quoted path of that `Piano roll scripts` folder.
  3. On Windows with loopMIDI, add a port named `FL MCP`.
  4. In FL Studio: `Options > MIDI Settings`, enable `FL MCP` on Input (`FL MCP`)
     and Output using the **same Port number**. Restart FL Studio.
"""


def _write_deterministic_zip(out_path: Path, entries: list[tuple[str, bytes]]) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel, data in sorted(entries, key=lambda kv: kv[0]):
            info = zipfile.ZipInfo(rel, date_time=STAMP)
            info.external_attr = 0o100644 << 16
            zf.writestr(info, data)
    return out_path


def build_plugin_zip(out_dir: Path) -> Path:
    """Package plugin/ + .claude-plugin/marketplace.json as a standalone Claude Code plugin zip."""
    entries: list[tuple[str, bytes]] = []
    for base_rel in ("plugin", ".claude-plugin"):
        base = ROOT / base_rel
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(part in PLUGIN_EXCLUDE for part in path.parts):
                continue
            entries.append((path.relative_to(ROOT).as_posix(), path.read_bytes()))
    return _write_deterministic_zip(out_dir / f"fl-studio-claude-plugin-{__version__}.zip", entries)


def build_fl_scripts_zip(out_dir: Path) -> Path:
    """Package FL controller + Piano roll script + offline installers for non-CLI setup."""
    fl_dir = ROOT / "src" / "fl_mcp" / "fl"
    entries = [
        ("Hardware/FLMCP/device_FLMCP.py", (fl_dir / "device_FLMCP.py").read_bytes()),
        ("Piano roll scripts/FL MCP.pyscript", (fl_dir / "FL MCP.pyscript").read_bytes()),
        ("install-fl-scripts.py", STANDALONE_INSTALL_PY.encode("utf-8")),
        ("install-fl-scripts.ps1", STANDALONE_INSTALL_PS1.encode("utf-8")),
        ("INSTALL.txt", STANDALONE_README.encode("utf-8")),
    ]
    return _write_deterministic_zip(out_dir / f"fl-mcp-fl-scripts-{__version__}.zip", entries)


def build_python_dists(out_dir: Path) -> list[Path]:
    """Build wheel and source tarball using uv (preferred) or python -m build."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("uv"):
        subprocess.run(
            ["uv", "build", "--wheel", "--sdist", "--out-dir", str(out_dir)],
            cwd=ROOT,
            check=True,
        )
    else:
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(out_dir)],
            cwd=ROOT,
            check=True,
        )
    return sorted(
        p
        for p in out_dir.iterdir()
        if p.name.endswith((".whl", ".tar.gz")) and __version__ in p.name
    )


def extract_release_notes(version: str = __version__) -> str:
    """Extract the CHANGELOG section for `version` with install instructions."""
    changelog = ROOT / "CHANGELOG.md"
    section = ""
    if changelog.is_file():
        text = changelog.read_text(encoding="utf-8")
        pattern = rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)"
        m = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
        if m:
            section = m.group(1).strip()
    header = f"# FL-MCP v{version}\n\n"
    if section:
        header += section + "\n\n"
    header += (
        "## Release Assets\n\n"
        f"- `fl-mcp-studio-{version}-<os>-<arch>.zip|.tar.gz`: Standalone Desktop App "
        "(Windows x64/ARM64, macOS Apple Silicon/Intel, Linux x64/ARM64 — zero Python required)\n"
        f"- `fl-mcp-{version}.mcpb`: Claude Desktop extension bundle\n"
        f"- `fl_mcp-{version}-py3-none-any.whl` / `fl_mcp-{version}.tar.gz`: Python package\n"
        f"- `fl-studio-claude-plugin-{version}.zip`: Claude Code plugin & marketplace\n"
        f"- `fl-mcp-fl-scripts-{version}.zip`: Standalone FL scripts & installer\n"
        "- `SHA256SUMS.txt`: SHA-256 verification checksums\n"
    )
    return header


def write_checksums(out_dir: Path, files: list[Path]) -> Path:
    lines = []
    for path in sorted(files, key=lambda p: p.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    target = out_dir / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def build_release(
    out_dir: Path,
    *,
    service: str | None = None,
    include_python: bool = True,
) -> list[Path]:
    """Build complete release artifacts into `out_dir` and return all artifact paths."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    if include_python:
        artifacts.extend(build_python_dists(out_dir))
    artifacts.append(build_mcpb.build(out_dir, service=service))
    artifacts.append(build_plugin_zip(out_dir))
    artifacts.append(build_fl_scripts_zip(out_dir))
    notes = out_dir / "RELEASE_NOTES.md"
    notes.write_text(extract_release_notes(__version__), encoding="utf-8")
    checksums = write_checksums(out_dir, artifacts)
    return [*artifacts, notes, checksums]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    parser.add_argument("--service", help="optional commercial licence service URL (https)")
    parser.add_argument(
        "--no-python-build",
        action="store_true",
        help="skip building .whl and .tar.gz (only zips and .mcpb)",
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    built = build_release(
        out_dir,
        service=args.service or os.environ.get("FL_MCP_RELEASE_SERVICE"),
        include_python=not args.no_python_build,
    )
    for path in built:
        size = path.stat().st_size
        print(f"{path.name:<40} {size:>10,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
