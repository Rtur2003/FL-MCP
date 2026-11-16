"""Generate the Claude Code plugin (plugin/) and its marketplace from the packaged guides.

Usage: python scripts/build_plugin.py [--check]

The guides in src/fl_mcp/data/guides are the one source of what a model should know. The skills
of the plugin are those guides with a front matter; the manifest follows the package version.
--check fails when a committed file differs from what would be generated (the tests run it)."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fl_mcp import __version__, clients, guides  # noqa: E402

PLUGIN = "fl-studio"
MARKETPLACE = "fl-mcp"
AUTHOR = {"name": "Hasan Arthur Altuntaş", "url": "https://github.com/Rtur2003"}
DESCRIPTION = (
    "Control FL Studio from Claude: produce tracks fast, mix and master with measurements, "
    "drive any plugin in real units, and do what the scripting API cannot."
)

AGENT_DESCRIPTION = (
    "Runs the FL Studio mix and master loop (doctor, balance, plugin chains, render, analyze) in "
    "one go and reports the numbers before and after. Use for a long mixing pass so the main "
    "conversation stays short."
)
AGENT_BODY = """\

You are a mix engineer working in the user's open FL Studio project through the FL Studio MCP \
tools (fl_mix, fl_param, fl_audio, fl_list, fl_set). Follow the mix and plugins skills of this \
plugin.

1. Read the state: fl_status, fl_list channels and mixer, fl_mix action=doctor. Note the worst \
problems and the starting loudness (fl_audio render).
2. Fix in order: routing and clipping, balance (fl_mix balance), tone (high-passes, small EQ \
moves), dynamics (fl_mix chain apply=true, then fl_param), space (sends), master.
3. Change one thing at a time. After each group of changes measure again with fl_audio and keep \
the change only if the numbers or the advice improved.
4. Stop when the advice list is empty or only taste remains. Never leave a fader or plugin \
value changed that you cannot name.

Report: what was wrong, what you changed (track, plugin, value), loudness, true peak, crest \
factor and the remaining advice, before and after.
"""


def q(text: str) -> str:
    """A YAML double-quoted scalar: descriptions contain colons, which a plain scalar cannot."""
    return json.dumps(text, ensure_ascii=False)


def skill(key: str) -> str:
    topic = guides.TOPICS[key]
    return (
        f"---\nname: {key}\ndescription: {q(topic.description)}\n"
        f"when_to_use: {q('Use when ' + topic.when)}\n---\n\n{guides.body(key)}"
    )


def agent() -> str:
    return f"---\nname: mix-engineer\ndescription: {q(AGENT_DESCRIPTION)}\n---\n{AGENT_BODY}"


def manifest() -> dict:
    return {
        "name": PLUGIN,
        "displayName": "FL Studio",
        "version": __version__,
        "description": DESCRIPTION,
        "author": AUTHOR,
        "homepage": "https://github.com/Rtur2003/FL-MCP",
        "repository": "https://github.com/Rtur2003/FL-MCP",
        "license": "MIT",
        "keywords": ["fl studio", "music", "daw", "mixing", "mastering", "midi"],
        "userConfig": {
            "license_key": {
                "type": "string",
                "title": "Licence key",
                "description": "Only for the credit-based tools; leave empty otherwise.",
                "sensitive": True,
                "required": False,
                "default": "",
            },
            "desktop": {
                "type": "boolean",
                "title": "Desktop control",
                "description": "Enable non-intrusive background/polite FL Studio screen and "
                "shortcut control (`fl_desk`). Off by default.",
                "default": False,
            },
            "port": {
                "type": "string",
                "title": "MIDI port name",
                "description": "The virtual MIDI port FL Studio listens on.",
                "default": "FL MCP",
            },
        },
    }


def mcp_config() -> dict:
    command, args = clients.command("git", True)
    return {
        "mcpServers": {
            PLUGIN: {
                "command": "uvx",
                "args": args,
                "env": {
                    "FL_MCP_LICENSE": "${user_config.license_key}",
                    "FL_MCP_DESKTOP": "${user_config.desktop}",
                    "FL_MCP_PORT": "${user_config.port}",
                },
            }
        }
    }


def marketplace() -> dict:
    return {
        "name": MARKETPLACE,
        "owner": AUTHOR,
        "description": "FL Studio plugin for Claude Code",
        "plugins": [
            {
                "name": PLUGIN,
                "source": "./plugin",
                "description": DESCRIPTION,
                "version": __version__,
                "category": "music",
                "tags": ["fl-studio", "music", "mixing", "midi"],
            }
        ],
    }


def dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


README = f"""# FL Studio for Claude Code

{DESCRIPTION}

```
/plugin marketplace add Rtur2003/FL-MCP
/plugin install {PLUGIN}@{MARKETPLACE}
```

The plugin starts the `fl-mcp` server through `uvx` (install [uv](https://docs.astral.sh/uv/)
first) and adds five skills: **setup**, **produce**, **mix**, **plugins** and **automation**,
plus a `mix-engineer` subagent. They are generated from the guides packaged with the server
(`src/fl_mcp/data/guides`), which every other MCP client reads as the resources
`fl://guide/<topic>` and the prompts `make_beat`, `mix_check`, `master`, `fix_connection` and
`load_plugin`.

The first start downloads the Python packages (numpy and scipy for the audio tools take a
minute). Warm the cache once so the client does not time out waiting:
`uvx --from "fl-mcp[audio] @ git+https://github.com/Rtur2003/FL-MCP" fl-mcp doctor`, or set
`MCP_TIMEOUT=120000` for Claude Code.

FL Studio itself needs a one-time setup (virtual MIDI port, controller script): run
`/{PLUGIN}:setup` or `fl-mcp setup`, then `fl-mcp selftest`.

Do not edit `skills/` by hand: change the guide and run `python scripts/build_plugin.py`.
"""


def generate() -> dict[Path, str]:
    files: dict[Path, str] = {
        Path(".claude-plugin/marketplace.json"): dump(marketplace()),
        Path("plugin/.claude-plugin/plugin.json"): dump(manifest()),
        Path("plugin/.mcp.json"): dump(mcp_config()),
        Path("plugin/agents/mix-engineer.md"): agent(),
        Path("plugin/README.md"): README,
    }
    for key in guides.TOPICS:
        files[Path(f"plugin/skills/{key}/SKILL.md")] = skill(key)
    return files


def stale(files: dict[Path, str], root: Path = ROOT) -> list[Path]:
    out = [
        p
        for p, text in files.items()
        if not (root / p).is_file() or (root / p).read_text("utf-8") != text
    ]
    known = {root / p for p in files}
    folder = root / "plugin"
    if folder.is_dir():
        out += [
            p.relative_to(root)
            for p in sorted(folder.rglob("*"))
            if p.is_file() and p not in known and "__pycache__" not in p.parts
        ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if committed files are stale")
    args = parser.parse_args()
    files = generate()
    if args.check:
        bad = stale(files)
        if bad:
            raise SystemExit("stale or unexpected: " + ", ".join(map(str, bad)))
        print("plugin is up to date")
        return
    for path, text in files.items():
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        print(path)


if __name__ == "__main__":
    main()
