import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Folder where the server and the piano roll script exchange notes."""
    return Path(os.environ.get("FL_MCP_DIR") or Path.home() / ".fl-mcp").expanduser().resolve()


def fl_user_dir() -> Path:
    """FL Studio user data folder (contains Settings/)."""
    if "FL_USER_DIR" in os.environ:
        return Path(os.environ["FL_USER_DIR"]).expanduser().resolve()
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or Path.home())
    docs = home / "Documents"
    if sys.platform == "win32" and not docs.is_dir() and "HOME" not in os.environ:
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                candidate = Path(buf.value)
                if candidate.is_relative_to(home):
                    docs = candidate
        except Exception:
            pass
    return docs / "Image-Line" / "FL Studio"


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
