"""Builds the Claude Desktop extension: python packaging/build_mcpb.py [--service URL] [--out DIR].

The bundle follows MCPB manifest 0.4 with the `uv` server type: the host installs the
dependencies listed in the generated pyproject.toml, so the compiled MIDI library matches the
customer's platform. --service bakes the licence service address into the bundle; without it the
bundle meters nothing.
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fl_mcp import __version__  # noqa: E402

STAMP = (2024, 1, 1, 0, 0, 0)  # fixed, so the same source gives the same bytes
LONG = (
    "Controls the FL Studio running on this computer: every plugin by name and in real units, "
    "mixer, channels, patterns, step sequencer and piano roll; fast genre-aware production "
    "(drums into the Channel Rack, a MIDI file or a finished .flp with samples); mixing and "
    "mastering by measurement (loudness, true peak, stereo, spectrum); and, opt-in, the screen "
    "and keyboard for what FL's scripting API cannot do. Setup: install loopMIDI (Windows) or "
    "enable the IAC Driver (macOS), create a port named 'FL MCP', then run `uvx fl-mcp setup` "
    "once to copy the controller script into FL Studio and `uvx fl-mcp selftest` to check it."
)


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return (match.group(1) if match else text)[:200]


def tool_list() -> list[dict]:
    from fl_mcp import server

    tools = server._tools(True)  # fl_desk too: it is off until the user switches it on
    return [{"name": t.name, "description": first_sentence(t.description or "")} for t in tools]


def manifest() -> dict:
    return {
        "manifest_version": "0.4",
        "name": "fl-mcp",
        "display_name": "FL Studio",
        "version": __version__,
        "description": "Control FL Studio and produce music fast, from any AI assistant.",
        "long_description": LONG,
        "author": {"name": "Hasan Arthur Altuntaş"},
        "repository": {"type": "git", "url": "https://github.com/Rtur2003/FL-MCP"},
        "homepage": "https://github.com/Rtur2003/FL-MCP",
        "license": "MIT",
        "keywords": ["fl studio", "music", "daw", "midi", "production"],
        "server": {
            "type": "uv",
            "entry_point": "src/server.py",
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", "src/server.py"],
                "env": {
                    "FL_MCP_LICENSE": "${user_config.license_key}",
                    "FL_MCP_DESKTOP": "${user_config.desktop}",
                },
            },
        },
        "tools": tool_list(),
        "tools_generated": False,
        "user_config": {
            "license_key": {
                "type": "string",
                "title": "Licence key",
                "description": "Only needed for the credit-based tools; leave empty otherwise.",
                "sensitive": True,
                "required": False,
            },
            "desktop": {
                "type": "boolean",
                "title": "Desktop control",
                "description": "Let the model see FL Studio's screen and press its keys (only "
                "while an FL window is in front). Off by default.",
                "default": False,
            },
        },
        "compatibility": {"platforms": ["darwin", "win32"]},
    }


def bundle_pyproject() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    wanted = [*project["dependencies"], *project["optional-dependencies"]["audio"]]
    deps = "\n".join(f"    {json.dumps(d)}," for d in wanted)
    return (
        f'[project]\nname = "fl-mcp-bundle"\nversion = "{__version__}"\n'
        f'requires-python = "{project["requires-python"]}"\ndependencies = [\n{deps}\n]\n'
    )


def check_service(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise SystemExit(
            "--service must be a plain https address, e.g. https://licence.example.com"
        )
    return url.rstrip("/")


def build(out: Path, service: str = "") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"fl-mcp-{__version__}.mcpb"
    files: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest(), indent=2, ensure_ascii=False).encode() + b"\n",
        "pyproject.toml": bundle_pyproject().encode(),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
        "src/server.py": (ROOT / "packaging" / "server.py").read_bytes(),
    }
    for path in sorted((ROOT / "src" / "fl_mcp").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            files["src/" + path.relative_to(ROOT / "src").as_posix()] = path.read_bytes()
    if service:
        files["src/fl_mcp/commerce/issuer.json"] = json.dumps({"service": service}).encode()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--service", default="", help="licence service address (https)")
    args = parser.parse_args()
    service = check_service(args.service) if args.service else ""
    print(build(Path(args.out), service))


if __name__ == "__main__":
    main()
