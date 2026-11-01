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
