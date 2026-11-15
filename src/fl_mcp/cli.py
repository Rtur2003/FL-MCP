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
    from fl_mcp import audio, desk, render, telemetry

    render_status = render.status()
    mode = desk.mode()
    exe_msg = (
        f"ok, {render_status['fl_exe']}" if render_status["fl_exe"] else "no FL64.exe (Windows)"
    )
    print(f"audio       : {'ok' if audio.available() else 'missing, install fl-mcp[audio]'}")
    print(f"render      : {exe_msg}")
    print(f"desktop     : {mode if mode != 'off' else 'off, `fl-mcp desktop on` to allow'}")
    st = telemetry.read_traffic(limit=0)["stats"]
    if st["total"]:
        print(
            f"traffic     : {st['midi_calls']} midi ({st['tx_bytes']}B tx / {st['rx_bytes']}B rx, "
            f"avg {st['avg_ms']} ms, {st['midi_errors']} err), {st['mcp_calls']} mcp"
        )


def _format_event(e: dict[str, object]) -> str:
    import datetime

    ts = datetime.datetime.fromtimestamp(float(e.get("ts") or 0)).strftime("%H:%M:%S")
    kind = str(e.get("dir") or "?").upper().ljust(4)
    op = str(e.get("op") or "?").ljust(14)
    ok = "OK " if e.get("ok") else "ERR"
    att = int(e.get("attempts") or 1)
    retry = f" x{att}" if att > 1 else ""
    bytes_info = f"{e.get('tx_bytes', 0)}->{e.get('rx_bytes', 0)}B".rjust(11)
    ms = f"{e.get('ms', 0)}ms".rjust(8)
    detail = str(e.get("error") or e.get("req") or "")
    return f"{ts} {kind} {op} {ok}{retry:<3} {bytes_info} {ms}  {detail}"


def _traffic(args: argparse.Namespace) -> int:
    import time

    from fl_mcp import telemetry

    if args.clear:
        n = telemetry.clear_traffic()
        print(f"cleared {n} traffic events ({telemetry.traffic_path()})")
        return 0
    kind = "midi" if args.midi else "mcp" if args.mcp else None
    data = telemetry.read_traffic(limit=args.limit, kind=kind, only_errors=args.errors)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    st = data["stats"]
    print(
        f"traffic: midi={st['midi_calls']} ({st['midi_errors']} err, {st['retries']} retries, "
        f"{st['tx_bytes']}B tx / {st['rx_bytes']}B rx, avg {st['avg_ms']}ms, p95 {st['p95_ms']}ms) "
        f"| mcp={st['mcp_calls']} ({st['mcp_errors']} err)"
    )
    seen: set[str] = set()
    for e in data["events"]:
        seen.add(str(e.get("id") or ""))
        print(_format_event(e))
    if not args.follow:
        return 0
    try:
        while True:
            time.sleep(0.5)
            latest = telemetry.read_traffic(limit=50, kind=kind, only_errors=args.errors)["events"]
            for e in latest:
                eid = str(e.get("id") or "")
                if eid and eid not in seen:
                    seen.add(eid)
                    print(_format_event(e), flush=True)
    except KeyboardInterrupt:
        return 0


def _templates(args: argparse.Namespace) -> int:
    from fl_mcp import produce

    items = produce.discover_templates(limit=60)
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    if not items:
        print("No .flp templates found. Put .flp templates in ~/.fl-mcp/templates or FL Templates.")
        return 1
    for t in items:
        flag = "sampler-ready" if t["sampler_ready"] else "ready" if t["ready"] else "no-seed"
        roles = ", ".join(t["roles"]) or "-"
        print(
            f"[{flag:<13}] {t['name']:<26} {t['tempo']:>5} BPM  "
            f"ch={t['channels']:>2}  roles={roles}"
        )
        print(f"               {t['path']}")
    return 0


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

    action = args.action
    if action in ("on", "off", "stealth", "polite", "foreground"):
        desk.set_enabled(action)
        if action == "off":
            print("Desktop control is off.")
        else:
            mode = desk.mode()
            print(
                f"Desktop control is on (mode={mode}): restart your AI client. fl_desk uses "
                "background PrintWindow/PostMessage first so FL stays out of the way; in polite "
                "mode any foreground fallback waits until you are idle and restores your window. "
                "Fling the mouse to the top-left corner to stop it. `fl-mcp desktop off` undoes it."
            )
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
        bg = "background" if meta.get("background") else "foreground"
        print(f"screenshot ({bg}) {meta['size'][0]}x{meta['size'][1]} saved to {target}")
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
    desktop = sub.add_parser(
        "desktop", help="let the model see and drive FL silently (stealth/polite/foreground)"
    )
    desktop.add_argument(
        "action", choices=["on", "off", "stealth", "polite", "foreground", "test"]
    )
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
    dash = sub.add_parser("dashboard", help="open live web studio visualizer & traffic monitor")
    dash.add_argument("--port", type=int, default=8765, help="HTTP port on 127.0.0.1 (0 = random)")
    dash.add_argument("--no-open", action="store_true", help="do not launch browser automatically")
    dash.add_argument("--poll", action="store_true", help="poll FL Studio once on startup")
    traf = sub.add_parser("traffic", help="show or tail incoming/outgoing MIDI & MCP traffic")
    traf.add_argument("--limit", type=int, default=30, help="recent events to show (0 = all)")
    traf.add_argument("--follow", "-f", action="store_true", help="watch live traffic until Ctrl+C")
    traf.add_argument("--midi", action="store_true", help="only MIDI bridge transactions")
    traf.add_argument("--mcp", action="store_true", help="only MCP tool calls")
    traf.add_argument("--errors", action="store_true", help="only failed calls")
    traf.add_argument("--json", action="store_true", help="output raw JSON")
    traf.add_argument("--clear", action="store_true", help="clear recorded traffic log")
    tpl = sub.add_parser("templates", help="list auto-discovered FL Studio .flp templates")
    tpl.add_argument("--json", action="store_true", help="output JSON")
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
    elif args.cmd == "dashboard":
        from fl_mcp.dashboard import serve_dashboard

        raise SystemExit(
            serve_dashboard(port=args.port, open_browser=not args.no_open, poll_first=args.poll)
        )
    elif args.cmd == "traffic":
        raise SystemExit(_traffic(args))
    elif args.cmd == "templates":
        raise SystemExit(_templates(args))
    elif args.cmd in ("login", "logout", "account"):
        raise SystemExit(_licence(args))
    else:
        from fl_mcp.server import serve

        serve()
