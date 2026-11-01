"""The Claude Code plugin, the guides it is generated from, and the MCP prompts and resources."""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp import Client

from fl_mcp import __version__, guides, server
from tests.names import NAMES

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_plugin", ROOT / "scripts" / "build_plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_builder()


def front_matter(text):
    """(fields, body). Values are plain words or JSON strings, which YAML reads the same way."""
    head, _, rest = text.partition("\n---\n")
    assert head.startswith("---\n")
    fields = {}
    for line in head[4:].splitlines():
        key, _, value = line.partition(": ")
        fields[key] = json.loads(value) if value.startswith('"') else value
    return fields, rest


# ---- the generated files are the committed files


def test_the_committed_plugin_is_what_the_guides_generate():
    assert builder.stale(builder.generate()) == []


def test_a_hand_edited_skill_is_noticed(tmp_path):
    files = builder.generate()
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    assert builder.stale(files, tmp_path) == []
    (tmp_path / "plugin/skills/mix/SKILL.md").write_text("changed", encoding="utf-8")
    (tmp_path / "plugin/skills/extra.md").write_text("stray", encoding="utf-8")
    assert builder.stale(files, tmp_path) == [
        Path("plugin/skills/mix/SKILL.md"),
        Path("plugin/skills/extra.md"),
    ]


# ---- the plugin is well formed


def test_the_manifest_and_marketplace_are_consistent():
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text("utf-8"))
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text("utf-8"))
    assert plugin["name"] == "fl-studio" and plugin["version"] == __version__
    assert not re.match(r"(claude|anthropic|cc-plugin)", plugin["name"])
    entry = market["plugins"][0]
    assert market["name"] == "fl-mcp" and entry["name"] == plugin["name"]
    assert entry["source"] == "./plugin" and entry["version"] == plugin["version"]
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()
    for key, option in plugin["userConfig"].items():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        assert {"type", "title", "description"} <= set(option)


def test_the_mcp_config_only_uses_declared_options_and_real_env_names():
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text("utf-8"))
    config = json.loads((ROOT / "plugin/.mcp.json").read_text("utf-8"))
    server_config = config["mcpServers"]["fl-studio"]
    used = set()
    for name, value in server_config["env"].items():
        used |= set(re.findall(r"\$\{user_config\.(\w+)\}", value))
        assert name in {"FL_MCP_LICENSE", "FL_MCP_DESKTOP", "FL_MCP_PORT"}
    assert used <= set(plugin["userConfig"]) and server_config["command"] == "uvx"
    assert server_config["args"][-1] == "fl-mcp"


@pytest.mark.parametrize("key", list(guides.TOPICS))
def test_each_skill_has_valid_front_matter_and_a_sane_size(key):
    text = (ROOT / f"plugin/skills/{key}/SKILL.md").read_text("utf-8")
    meta, body = front_matter(text)
    assert meta["name"] == key and re.fullmatch(r"[a-z][a-z0-9-]*", key)
    assert len(meta["description"]) + len(meta["when_to_use"]) <= 1536
    assert meta["when_to_use"].startswith("Use when")
    assert text.count("\n") < 500 and "{{" not in text and body.lstrip().startswith("# ")


def test_the_agent_has_a_description_and_names_real_tools():
    text = (ROOT / "plugin/agents/mix-engineer.md").read_text("utf-8")
    meta, body = front_matter(text)
    assert meta["name"] == "mix-engineer" and len(meta["description"]) > 40
    assert set(re.findall(r"\bfl_[a-z]+\b", body)) <= {*NAMES, "fl_desk"}


@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI")
def test_claude_accepts_the_plugin_and_the_marketplace(tmp_path):
    base = __import__("os").environ
    env = {
        **{
            k: v
            for k, v in base.items()
            if k.upper() in ("SYSTEMROOT", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE")
        },
        "CLAUDE_CONFIG_DIR": str(tmp_path),
        "PATH": base["PATH"],
        "HOME": str(tmp_path),
    }
    for target in ("plugin", "."):
        done = subprocess.run(
            ["claude", "plugin", "validate", target, "--strict"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr


# ---- the guides tell the truth about the tools


def tool_parameters():
    params = set()
    for tool in server._tools(True):
        params |= set(tool.parameters.get("properties", {}))
    return params


def code_spans(text):
    return re.findall(r"`([^`\n]+)`", text)


@pytest.mark.parametrize("key", list(guides.TOPICS))
def test_every_tool_and_argument_a_guide_names_exists(key):
    text = guides.body(key)
    names = {*NAMES, "fl_desk"}
    known = tool_parameters()
    for span in code_spans(text):
        for tool in re.findall(r"\bfl_[a-z]+\b", span):
            assert tool in names, f"{key}: unknown tool {tool} in `{span}`"
        for arg in re.findall(r"(?<![\w-])([a-z][a-z_]*)=", span):
            assert arg in known, f"{key}: unknown argument {arg}= in `{span}`"
    for tool in re.findall(r"\bfl_[a-z]+\b", text):
        assert tool in names, f"{key}: unknown tool {tool}"


@pytest.mark.parametrize("key", list(guides.TOPICS))
def test_live_tables_are_filled_in(key):
    text = guides.body(key)
    assert "{{" not in text and text.endswith("\n") and text.startswith("# ")


def test_the_tables_come_from_the_real_data():
    produce = guides.body("produce")
    assert "| house (House) | 120-124-128 | minor |" in produce and "hicaz_trap" in produce
    assert "dubstep -6" in guides.body("mix") and "lofi -13" in guides.body("mix")
    plugins = guides.body("plugins")
    assert "**kick**" in plugins and "Fruity Kick" in plugins and "Sytrus" in plugins


def test_unknown_guides_are_refused():
    with pytest.raises(KeyError, match="unknown guide"):
        guides.body("cooking")


# ---- resources and prompts any MCP client can use


async def test_the_guides_are_mcp_resources():
    async with Client(server.mcp) as client:
        listed = (await client.list_resources()).resources
        assert [str(r.uri) for r in listed] == [guides.uri(k) for k in guides.TOPICS]
        assert all(r.mime_type == "text/markdown" for r in listed)
        read = await client.read_resource("fl://guide/mix")
    assert read.contents[0].text == guides.body("mix")


async def test_prompts_carry_the_task_and_the_guides():
    async with Client(server.mcp) as client:
        names = [p.name for p in (await client.list_prompts()).prompts]
        assert names == ["make_beat", "mix_check", "master", "fix_connection", "load_plugin"]
        beat = await client.get_prompt("make_beat", {"genre": "house", "mood": "dark"})
        text = beat.messages[0].content.text
        assert text.startswith("Make a short dark house track")
        assert "# Produce a track fast" in text and "# Mix and master" in text
        anything = await client.get_prompt("make_beat")
        assert "Pick the genre that fits" in anything.messages[0].content.text
        plugin = await client.get_prompt(
            "load_plugin", {"name": "Fruity Limiter", "target": "the master"}
        )
        assert "Load 'Fruity Limiter' onto the master" in plugin.messages[0].content.text
        goal = await client.get_prompt("master", {"genre": "techno", "target_lufs": "-9"})
        assert "Target -9 LUFS" in goal.messages[0].content.text
        mix = await client.get_prompt("mix_check", {})
        assert "fl_mix doctor first" in mix.messages[0].content.text
        fix = await client.get_prompt("fix_connection", {})
        assert "# Set up and fix the FL Studio link" in fix.messages[0].content.text
