import argparse
import getpass
import json
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
    _extras()
    return 0


def _extras() -> None:
    from fl_mcp import audio, desk, render

    print(f"audio       : {'ok' if audio.available() else 'missing, install fl-mcp[audio]'}")
    info = render.status()
    print(f"render      : {'ok, ' + info['fl_exe'] if info['fl_exe'] else 'no FL64.exe (Windows)'}")
    print(f"desktop     : {'on' if desk.enabled() else 'off, `fl-mcp desktop on` to allow'}")


def _selftest(args: argparse.Namespace) -> int:
    from fl_mcp import selftest
    from fl_mcp.bridge import Bridge

    report = selftest.run(Bridge().call, args.deep)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(selftest.render_text(report))
        print(f"\nreport saved to {selftest.save(report)}")
    return 1 if report["summary"]["fail"] else 0


def _desktop(args: argparse.Namespace) -> int:
    from fl_mcp import desk

    if args.action in ("on", "off"):
        desk.set_enabled(args.action == "on")
        if args.action == "on":
            print(
                "Desktop control is on: restart your AI client. The model can then see FL Studio's "
                "screen (fl_desk) and press keys and buttons, only while an FL window is in front. "
                "Fling the mouse to the top-left corner to stop it. `fl-mcp desktop off` undoes it."
            )
        else:
            print("Desktop control is off.")
        return 0
    try:
        d = desk.Desk(desk.make_backend())
        status = d.status()
        print(json.dumps(status, indent=2))
        if status["fl"] is None:
            print("The backend works but FL Studio's window was not found: start FL first.")
            return 1
        png, meta = d.shot(full=False)
        target = desk.flag_file().with_name("desk-test.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        print(f"screenshot {meta['size'][0]}x{meta['size'][1]} saved to {target}")
    except desk.DeskError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _config(args: argparse.Namespace) -> int:
    from fl_mcp import clients

    names = [args.client] if args.client else list(clients.CLIENTS)
    for name in names:
        where, text = clients.snippet(name, args.source, not args.no_audio)
        print(f"# {clients.CLIENTS[name][0]}: {where}\n{text}\n")
    return 0


def _setup(args: argparse.Namespace) -> int:
    from fl_mcp import clients, desk, install, paths
    from fl_mcp.bridge import Bridge, FLError, match_port

    steps = []
    root = paths.fl_user_dir() if not args.fl_dir else args.fl_dir
    try:
        written = install.install(args.fl_dir, args.notes_dir)
        steps.append(("scripts", True, f"installed {len(written)} files under {root}"))
    except SystemExit as exc:
        steps.append(("scripts", False, str(exc)))
    bridge = Bridge()
    try:
        import mido

        outs, ins = mido.get_output_names(), mido.get_input_names()
        found = match_port(ins, bridge.port) and match_port(outs, bridge.port)
        text = (
            f"port '{bridge.port}' found"
            if found
            else (
                f"no MIDI port named '{bridge.port}': "
                + (
                    "install loopMIDI, add a port with that name"
                    if sys.platform == "win32"
                    else "Audio MIDI Setup > MIDI Studio > IAC Driver > enable, add a port"
                )
            )
        )
        steps.append(("midi port", bool(found), text))
    except Exception as exc:
        steps.append(("midi port", False, f"MIDI backend: {exc}"))
    if args.desktop:
        desk.set_enabled(True)
        steps.append(("desktop", True, "on: the model may see and press FL's screen"))
    for name, ok, text in steps:
        print(f"[{'ok' if ok else '!!'}] {name:<10} {text}")
    print(
        "\nIn FL Studio: Options > MIDI Settings. Under Input, select the FL MCP port, set its "
        "controller type to 'FL MCP' and enable it; under Output select the same port and give it "
        "the same port number. Then:\n"
    )
    where, text = clients.snippet(args.client, args.source, True)
    print(f"Register with {clients.CLIENTS[args.client][0]} ({where}):\n{text}\n")
    try:
        Bridge().call({"o": "ping"})
        print("FL Studio answers. Run `fl-mcp selftest` to check every feature.")
        return 0
    except FLError as exc:
        print(
            f"FL Studio does not answer yet: {exc}\nFinish the steps above, then `fl-mcp doctor`."
        )
        return 1


def _licence(args: argparse.Namespace) -> int:
    from fl_mcp.commerce import Gate, LicenseError

    gate = Gate()
    if not gate.metered:
        print("This build has no licence service: nothing is metered and no licence is needed.")
        return 0
    try:
        if args.cmd == "login":
            key = args.key or getpass.getpass("Licence key: ")
            out = gate.login(key, args.name, args.replace)
            print(f"Signed in: {out['plan']} plan, {out['credits']} credits.")
        elif args.cmd == "logout":
            gate.logout()
            print("Signed out.")
        else:
            print(json.dumps(gate.status(), indent=2))
    except LicenseError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def clients_names() -> list[str]:
    from fl_mcp import clients

    return list(clients.CLIENTS)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fl-mcp", description="MCP server for FL Studio.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    inst = sub.add_parser("install", help="copy the scripts into FL Studio's user data folder")
    inst.add_argument("--fl-dir", help="FL Studio user data folder (contains Settings)")
    inst.add_argument("--notes-dir", help="folder shared with the piano roll script")
    sub.add_parser("doctor", help="check MIDI ports, installed scripts and the FL Studio link")
    login = sub.add_parser("login", help="sign this computer in with a licence key")
    login.add_argument("key", nargs="?", help="asked for (hidden) when left out")
    login.add_argument("--name", help="how this computer is listed; default: its host name")
    login.add_argument("--replace", help="sign out another computer (name or id) to free a seat")
    sub.add_parser("logout", help="sign this computer out and free its seat")
    sub.add_parser("account", help="show plan, credits and prices")
    test = sub.add_parser("selftest", help="check every feature against the running FL Studio")
    test.add_argument("--json", action="store_true", help="print the report as JSON")
    test.add_argument(
        "--deep",
        nargs="?",
        const="Fruity Balance",
        metavar="PLUGIN",
        help="also load PLUGIN (default Fruity Balance) through FL's browser and undo it; "
        "save your project first",
    )
    desktop = sub.add_parser("desktop", help="let the model see and drive FL's screen (opt-in)")
    desktop.add_argument("action", choices=["on", "off", "test"])
    conf = sub.add_parser("config", help="print the text that registers this server with a client")
    conf.add_argument("client", nargs="?", choices=sorted(clients_names()))
    conf.add_argument("--source", choices=["pypi", "git", "local"], default="pypi")
    conf.add_argument("--no-audio", action="store_true", help="leave out the audio extra")
    setup = sub.add_parser("setup", help="install the scripts, check MIDI and show what is left")
    setup.add_argument("--fl-dir", help="FL Studio user data folder (contains Settings)")
    setup.add_argument("--notes-dir", help="folder shared with the piano roll script")
    setup.add_argument("--client", choices=sorted(clients_names()), default="claude-desktop")
    setup.add_argument("--source", choices=["pypi", "git", "local"], default="pypi")
    setup.add_argument("--desktop", action="store_true", help="also turn desktop control on")
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
    elif args.cmd == "selftest":
        raise SystemExit(_selftest(args))
    elif args.cmd == "desktop":
        raise SystemExit(_desktop(args))
    elif args.cmd == "config":
        raise SystemExit(_config(args))
    elif args.cmd == "setup":
        raise SystemExit(_setup(args))
    elif args.cmd in ("login", "logout", "account"):
        raise SystemExit(_licence(args))
    else:
        from fl_mcp.server import serve

        serve()
