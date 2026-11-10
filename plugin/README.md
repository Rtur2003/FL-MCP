# FL Studio for Claude Code

Control FL Studio from Claude: produce tracks fast, mix and master with measurements, drive any plugin in real units, and do what the scripting API cannot.

```
/plugin marketplace add Rtur2003/FL-MCP
/plugin install fl-studio@fl-mcp
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
`/fl-studio:setup` or `fl-mcp setup`, then `fl-mcp selftest`.

Do not edit `skills/` by hand: change the guide and run `python scripts/build_plugin.py`.
