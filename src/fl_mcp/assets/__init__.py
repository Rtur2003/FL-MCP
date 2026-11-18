"""Packaged static visual assets for the Studio Dashboard and desktop bundle."""

from importlib import resources

MIME_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
    ".icns": "image/x-icns",
    ".webmanifest": "application/manifest+json",
}


def load_asset(name: str) -> tuple[bytes, str] | None:
    """Safely load a packaged asset by filename, returning (bytes, content_type) or None."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = MIME_TYPES.get(suffix)
    if mime is None:
        return None
    try:
        target = resources.files("fl_mcp.assets").joinpath(name)
        return target.read_bytes(), mime
    except (FileNotFoundError, IsADirectoryError, ModuleNotFoundError):
        return None
