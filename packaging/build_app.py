"""Build native standalone Desktop App archives for the current OS and CPU architecture.

Usage:
    python packaging/build_app.py [--out dist] [--os windows|macos|linux] [--arch x86_64|arm64]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fl_mcp import __version__, app  # noqa: E402


def artifact_filename(
    version: str = __version__, os_name: str | None = None, arch: str | None = None
) -> str:
    os_n = os_name or app.normalize_os()
    ar_n = arch or app.normalize_arch()
    ext = "tar.gz" if os_n == "linux" else "zip"
    return f"fl-mcp-studio-{version}-{os_n}-{ar_n}.{ext}"


def pyinstaller_args(
    *,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
    os_name: str | None = None,
) -> list[str]:
    """Return cross-platform PyInstaller arguments bundling FL-MCP Studio as a native app."""
    os_n = os_name or app.normalize_os()
    pkg = ROOT / "src" / "fl_mcp"
    sep = ";" if os_n == "windows" else ":"
    datas = [
        (pkg / "assets", "fl_mcp/assets"),
        (pkg / "data", "fl_mcp/data"),
        (pkg / "fl", "fl_mcp/fl"),
        (pkg / "commerce" / "issuer.json", "fl_mcp/commerce"),
        (pkg / "api.json", "fl_mcp"),
    ]
    icon_name = (
        "icon.ico" if os_n == "windows" else ("icon.icns" if os_n == "macos" else "icon-256.png")
    )
    app_name = (
        "FL-MCP Studio"
        if os_n == "macos"
        else ("FL-MCP-Studio" if os_n == "windows" else "fl-mcp-studio")
    )

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        app_name,
        "--icon",
        str(pkg / "assets" / icon_name),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(ROOT / "src"),
        "--hidden-import",
        "mido.backends.rtmidi",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        "--hidden-import",
        "starlette.routing",
        "--hidden-import",
        "sse_starlette.sse",
        "--collect-submodules",
        "mcp",
        "--collect-submodules",
        "fl_mcp",
    ]
    if os_n == "macos":
        cmd.extend(["--osx-bundle-identifier", "io.flmcp.studio"])
    for src, dst in datas:
        cmd.extend(["--add-data", f"{src}{sep}{dst}"])
    cmd.append(str(pkg / "app.py"))
    return cmd


def _stage_common_extras(stage: Path, os_name: str) -> None:
    fl_dir = ROOT / "src" / "fl_mcp" / "fl"
    scripts = stage / "FL-Studio-Scripts"
    (scripts / "Hardware" / "FLMCP").mkdir(parents=True, exist_ok=True)
    (scripts / "Piano roll scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(fl_dir / "device_FLMCP.py", scripts / "Hardware" / "FLMCP" / "device_FLMCP.py")
    shutil.copy2(fl_dir / "FL MCP.pyscript", scripts / "Piano roll scripts" / "FL MCP.pyscript")
    from packaging import build_release

    (scripts / "install-fl-scripts.py").write_text(
        build_release.STANDALONE_INSTALL_PY, encoding="utf-8"
    )
    (scripts / "install-fl-scripts.ps1").write_text(
        build_release.STANDALONE_INSTALL_PS1, encoding="utf-8"
    )
    (scripts / "INSTALL.txt").write_text(build_release.STANDALONE_README, encoding="utf-8")

    if os_name == "linux":
        png = ROOT / "src" / "fl_mcp" / "assets" / "icon-256.png"
        shutil.copy2(png, stage / "fl-mcp-studio.png")
        (stage / "fl-mcp-studio.desktop").write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=FL-MCP Studio\n"
            "Comment=FL Studio MCP bridge, live telemetry, and background service console\n"
            "Exec=fl-mcp-studio\n"
            "Icon=fl-mcp-studio\n"
            "Terminal=false\n"
            "StartupWMClass=FL-MCP-Studio\n"
            "Categories=AudioVideo;Audio;Music;\n",
            encoding="utf-8",
        )


def build_app_archive(
    out_dir: Path,
    *,
    os_name: str | None = None,
    arch: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Build native PyInstaller desktop app bundle into ``out_dir`` and return archive path."""
    os_n = os_name or app.normalize_os()
    ar_n = arch or app.normalize_arch()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / artifact_filename(__version__, os_n, ar_n)
    build_root = out_dir / f".app-build-{os_n}-{ar_n}"
    if build_root.exists():
        shutil.rmtree(build_root)
    dist_dir = build_root / "dist"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    build_root.mkdir(parents=True, exist_ok=True)

    cmd = pyinstaller_args(dist_dir=dist_dir, work_dir=work_dir, spec_dir=spec_dir, os_name=os_n)
    if dry_run:
        return archive_path

    subprocess.run(cmd, cwd=ROOT, check=True)
    if os_n == "macos":
        app_bundle = dist_dir / "FL-MCP Studio.app"
        stage = build_root / "stage"
        stage.mkdir(parents=True, exist_ok=True)
        shutil.move(str(app_bundle), str(stage / "FL-MCP Studio.app"))
    else:
        bundle_name = "FL-MCP-Studio" if os_n == "windows" else "fl-mcp-studio"
        stage = dist_dir / bundle_name
    _stage_common_extras(stage, os_n)

    if os_n == "linux":
        with tarfile.open(archive_path, "w:gz") as tf:
            for item in sorted(stage.rglob("*")):
                arc = Path("fl-mcp-studio") / item.relative_to(stage)
                tf.add(item, arcname=arc.as_posix())
    else:
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zf:
            for item in sorted(stage.rglob("*")):
                if item.is_dir():
                    continue
                arc = item.relative_to(stage).as_posix()
                zf.write(item, arc)

    shutil.rmtree(build_root, ignore_errors=True)
    return archive_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    parser.add_argument("--os", dest="os_name", help="override OS label (windows|macos|linux)")
    parser.add_argument("--arch", help="override arch label (x86_64|arm64)")
    parser.add_argument("--dry-run", action="store_true", help="print planned archive and exit")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    target = build_app_archive(out, os_name=args.os_name, arch=args.arch, dry_run=args.dry_run)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
