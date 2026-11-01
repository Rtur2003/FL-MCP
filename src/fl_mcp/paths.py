import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Folder where the server and the piano roll script exchange notes."""
    return Path(os.environ.get("FL_MCP_DIR") or Path.home() / ".fl-mcp").expanduser().resolve()


def fl_user_dir() -> Path:
    """FL Studio user data folder (contains Settings/)."""
    docs = Path.home() / "Documents"
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                docs = Path(buf.value)
        except Exception:
            pass
    return docs / "Image-Line" / "FL Studio"


def fl_install_dirs() -> list[Path]:
    """FL Studio installations on this computer (FL_MCP_FL_DIR overrides the search)."""
    env = os.environ.get("FL_MCP_FL_DIR")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    found: list[Path] = []
    if sys.platform == "win32":
        for var, default in (
            ("ProgramFiles", r"C:\Program Files"),
            ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            base = Path(os.environ.get(var, default)) / "Image-Line"
            found += sorted(base.glob("FL Studio*"), reverse=True) if base.is_dir() else []
    elif sys.platform == "darwin":
        found += sorted(Path("/Applications").glob("FL Studio*.app"), reverse=True)
    return found
