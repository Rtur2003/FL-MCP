import os
import re
import sys
from pathlib import Path


def context_dir() -> Path:
    """Folder for global, per-project, and sparse producer memory files."""
    return data_dir() / "context"


def memory_dir() -> Path:
    """Folder for sparse producer memories (<name>.md)."""
    return data_dir() / "memory"


def data_dir() -> Path:
    """Folder for plans, exports, the library index and desktop state."""
    return Path(os.environ.get("FL_MCP_DIR") or Path.home() / ".fl-mcp").expanduser().resolve()


def logs_dir() -> Path:
    """Folder for persistent daemon logs."""
    return data_dir() / "logs"


def cache_dir() -> Path:
    """Folder for cached indexes and scratch state."""
    return data_dir() / "cache"


def models_dir() -> Path:
    """Folder for offline ONNX / AI models (Basic Pitch, Silero VAD, etc.)."""
    env = os.environ.get("FL_MCP_MODELS_DIR")
    return Path(env).expanduser().resolve() if env else data_dir() / "models"


def assets_dir() -> Path:
    """Folder for downloadable audio assets (soundfonts, samples, irs, midi)."""
    env = os.environ.get("FL_MCP_ASSETS_DIR")
    return Path(env).expanduser().resolve() if env else data_dir() / "assets"


def notes_dir(fl_dir: str | Path | None = None) -> Path:
    """Folder where the server and the piano roll script exchange inbox.json and state.json.

    FL Studio 2025 sandboxes piano roll scripts to 'Settings/Piano roll scripts', so when
    FL_MCP_DIR is not explicitly set we store the exchange files there if that folder exists."""
    if os.environ.get("FL_MCP_DIR"):
        return data_dir()
    root = Path(fl_dir).expanduser() if fl_dir else fl_user_dir()
    scripts = root / "Settings" / "Piano roll scripts"
    return scripts.resolve() if scripts.is_dir() else data_dir()


def _is_isolated_home(home: Path) -> bool:
    """Return True when HOME/USERPROFILE was overridden for sandboxed isolation."""
    if "HOME" in os.environ and sys.platform == "win32":
        try:
            if Path(os.environ["HOME"]).expanduser().resolve() != Path.home().resolve():
                return True
        except OSError:
            return True
    if sys.platform == "win32" and "LOCALAPPDATA" in os.environ:
        try:
            local_app = Path(os.environ["LOCALAPPDATA"]).expanduser().resolve()
            if not local_app.is_relative_to(home.resolve()):
                return True
        except OSError:
            return True
    return False


def fl_version_sort_key(name: str) -> tuple[int, str]:
    """Sort 'FL Studio 26' > 'FL Studio 25' > 'FL Studio 9' numerically."""
    match = re.search(r"(\d+)", name)
    return (int(match.group(1)) if match else 0, name)


def fl_registry_version_keys() -> list[str]:
    """Installed HKCU\\Software\\Image-Line\\FL Studio <N> registry key names, newest first."""
    if sys.platform != "win32":
        return []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line") as root:
            sub_count = winreg.QueryInfoKey(root)[0]
            fl_keys = [
                winreg.EnumKey(root, idx)
                for idx in range(sub_count)
                if winreg.EnumKey(root, idx).startswith("FL Studio")
            ]
            return sorted(fl_keys, key=fl_version_sort_key, reverse=True)
    except OSError:
        return []


def _win_user_data_candidates() -> tuple[list[Path], Path | None]:
    """Query Image-Line Registry settings and Windows Shell Documents folder."""
    candidates: list[Path] = []
    shell_docs: Path | None = None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line") as root:
            for key_name in fl_registry_version_keys():
                try:
                    with winreg.OpenKey(root, rf"{key_name}\General") as gen:
                        val, _ = winreg.QueryValueEx(gen, "User data folder")
                        if isinstance(val, str) and val.strip():
                            p = Path(val.strip()).expanduser()
                            candidates.append(p)
                except OSError:
                    continue
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Image-Line\Shared\Paths") as key:
                    val, _ = winreg.QueryValueEx(key, "Shared data")
                    if isinstance(val, str) and val.strip():
                        p = Path(val.strip()).expanduser()
                        candidates.append(p / "FL Studio")
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
    """FL Studio user data folder (contains Settings/).

    Supports FL_MCP_FL_USER_DIR, Windows Image-Line Registry overrides, redirected
    Documents (OneDrive / custom drive), macOS Application Support, and Linux Wine."""
    env_user = os.environ.get("FL_MCP_FL_USER_DIR", "").strip()
    if env_user:
        return Path(env_user).expanduser().resolve()
    home = Path(os.environ["HOME"]).expanduser() if "HOME" in os.environ else Path.home()
    default = home / "Documents" / "Image-Line" / "FL Studio"
    if (default / "Settings").is_dir() or default.is_dir() or _is_isolated_home(home):
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
        prefixes = [
            Path(p).expanduser()
            for p in (os.environ.get("WINEPREFIX", ""), str(home / ".wine"))
            if p
        ]
        for prefix in prefixes:
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


def diagnostics() -> dict[str, object]:
    """Return resolved universal paths and active non-secret FL_MCP_* environment overrides."""
    env_keys = (
        "FL_MCP_DIR",
        "FL_MCP_FL_DIR",
        "FL_MCP_FL_USER_DIR",
        "FL_MCP_PORT",
        "FL_MCP_SAMPLES",
        "FL_MCP_TEMPLATES",
        "FL_MCP_PROJECTS",
        "FL_MCP_DESKTOP",
    )
    user_dir = fl_user_dir()
    installs = fl_install_dirs()
    return {
        "platform": sys.platform,
        "data_dir": str(data_dir()),
        "models_dir": str(models_dir()),
        "assets_dir": str(assets_dir()),
        "logs_dir": str(logs_dir()),
        "notes_dir": str(notes_dir(user_dir)),
        "fl_user_dir": str(user_dir),
        "fl_user_dir_exists": user_dir.is_dir(),
        "fl_settings_exists": (user_dir / "Settings").is_dir(),
        "fl_install_dirs": [str(p) for p in installs[:8]],
        "env": {k: v for k in env_keys if (v := os.environ.get(k))},
    }


def _win_registry_install_dirs() -> list[Path]:
    """Read FL Studio install folders from Image-Line's Windows Registry keys."""
    out: list[Path] = []
    if sys.platform != "win32":
        return out
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Image-Line\Shared\Paths") as key:
                    for name in ("FL Studio", "Install path"):
                        try:
                            val, _ = winreg.QueryValueEx(key, name)
                            if val:
                                p = Path(str(val))
                                folder = p.parent if p.suffix.lower() == ".exe" else p
                                if folder.is_dir() and folder not in out:
                                    out.append(folder)
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass
    return out


def fl_install_dirs() -> list[Path]:
    """FL Studio installations on this computer (FL_MCP_FL_DIR overrides the search)."""
    env = os.environ.get("FL_MCP_FL_DIR")
    if env is not None:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    found: list[Path] = []
    if sys.platform == "win32":
        found += _win_registry_install_dirs()
        for var, default in (
            ("ProgramFiles", r"C:\Program Files"),
            ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            base = Path(os.environ.get(var, default)) / "Image-Line"
            if base.is_dir():
                for p in sorted(base.glob("FL Studio*"), reverse=True):
                    if p not in found:
                        found.append(p)
        found.sort(key=lambda d: not ((d / "FL64.exe").is_file() or (d / "FL.exe").is_file()))
    elif sys.platform == "darwin":
        found += sorted(Path("/Applications").glob("FL Studio*.app"), reverse=True)
    return found
