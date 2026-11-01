import argparse
import logging
import os
import platform
import sys

from fl_mcp import __version__


def _doctor() -> int:
    from fl_mcp.bridge import Bridge, FLError, match_port
    from fl_mcp.install import targets

    print(f"fl-mcp {__version__} | python {platform.python_version()} | {platform.platform()}")
    bridge = Bridge()
    try:
        import mido

        outs, ins = mido.get_output_names(), mido.get_input_names()
    except Exception as exc:
        print(f"MIDI backend: FAILED ({exc})")
        return 1
    print(f"MIDI inputs : {ins or 'none'}\nMIDI outputs: {outs or 'none'}")
    found = match_port(ins, bridge.port) and match_port(outs, bridge.port)
    print(f"port '{bridge.port}': " + ("found" if found else "NOT FOUND (see README, step 2)"))
    device, script = targets()
    print(
        f"FL script   : {'ok' if device.exists() else 'missing, run `fl-mcp install`'} ({device})"
    )
    print(f"piano roll  : {'ok' if script.exists() else 'missing, run `fl-mcp install`'}")
    try:
        info = bridge.call({"o": "ping"})
    except FLError as exc:
        print(f"FL Studio   : {exc}")
        return 1
    print(f"FL Studio   : ok, {info.get('fl')} (API {info.get('api')}, script v{info.get('ver')})")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fl-mcp", description="MCP server for FL Studio.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    inst = sub.add_parser("install", help="copy the scripts into FL Studio's user data folder")
    inst.add_argument("--fl-dir", help="FL Studio user data folder (contains Settings)")
    inst.add_argument("--notes-dir", help="folder shared with the piano roll script")
    sub.add_parser("doctor", help="check MIDI ports, installed scripts and the FL Studio link")
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if os.environ.get("FL_MCP_DEBUG") else logging.WARNING,
    )
    if args.cmd == "install":
        from fl_mcp.install import install

        for path in install(args.fl_dir, args.notes_dir):
            print(f"installed {path}")
        print("Restart FL Studio (or reload the script), then see README step 3.")
    elif args.cmd == "doctor":
        raise SystemExit(_doctor())
    else:
        from fl_mcp.server import serve

        serve()
