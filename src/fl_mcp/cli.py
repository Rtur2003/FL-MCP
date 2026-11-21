import argparse
import getpass
import json
import logging
import os
import platform
import sys
from pathlib import Path

from fl_mcp import __version__


def _disk_script_version(path: Path) -> int | None:
    import re

    try:
        m = re.search(r"^VERSION\s*=\s*(\d+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        return int(m.group(1)) if m else None
    except OSError:
        return None


def _doctor() -> int:
    from fl_mcp.bridge import SCRIPT_VERSION, Bridge, FLError, match_port
    from fl_mcp.install import diagnose_fl_midi, find_loopmidi, targets

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
    print(f"port '{bridge.port}': " + ("found" if found else "NOT FOUND (run `fl-mcp onboard`)"))
    if sys.platform == "win32":
        lm = find_loopmidi()
        print(f"loopMIDI    : {lm if lm else 'not found'}")
    device, script = targets()
    disk_ver = _disk_script_version(device) if device.exists() else None
    ver_note = f", disk v{disk_ver}" if disk_ver is not None else ""
    dev_state = f"ok{ver_note}" if device.exists() else "missing, run `fl-mcp install`"
    print(f"FL script   : {dev_state} ({device})")
    print(f"piano roll  : {'ok' if script.exists() else 'missing, run `fl-mcp install`'}")
    midi_diag = diagnose_fl_midi(bridge.port)
    if midi_diag.get("configured"):
        print("FL MIDI F10 : ok")
    elif midi_diag.get("issues"):
        print(f"FL MIDI F10 : {', '.join(midi_diag['issues'])}")
    try:
        info = bridge.call({"o": "ping"})
    except FLError as exc:
        print(f"FL Studio   : {exc}")
        return 1
    live_ver = int(info.get("ver") or 0)
    if live_ver < SCRIPT_VERSION:
        if disk_ver is not None and disk_ver >= SCRIPT_VERSION:
            fix = (
                f"disk already has v{disk_ver} — restart FL Studio (or close any stale hidden "
                "FL64.exe holding the MIDI port)"
            )
        else:
            fix = "run `fl-mcp install`, then restart FL Studio"
        print(
            f"FL Studio   : OUTDATED script v{live_ver} (need v{SCRIPT_VERSION}); {fix}"
        )
        _extras()
        return 1
    print(f"FL Studio   : ok, {info.get('fl')} (API {info.get('api')}, script v{live_ver})")
    _extras()
    return 0


def _extras() -> None:
    from fl_mcp import audio, desk, paths, render, service_daemon, telemetry

    render_status = render.status()
    mode = desk.mode()
    exe_msg = (
        f"ok, {render_status['fl_exe']}" if render_status["fl_exe"] else "no FL64.exe (Windows)"
    )
    svc = service_daemon.service_status()
    svc_msg = (
        f"running pid={svc['pid']} ({svc['mcp_url']})"
        if svc["running"]
        else "stopped, `fl-mcp service start` for background HTTP+UI"
    )
    print(f"user data   : {paths.fl_user_dir()}")
    print(f"audio       : {'ok' if audio.available() else 'missing, install fl-mcp[audio]'}")
    print(f"render      : {exe_msg}")
    print(f"desktop     : {mode if mode != 'off' else 'off, `fl-mcp desktop on` to allow'}")
    print(f"service     : {svc_msg}")
    try:
        from fl_mcp import vault

        vs = vault.status()["summary"]
        print(
            f"vault       : models={vs['models_installed']}/{vs['models_total']}, "
            f"sf2={vs['soundfonts_count']}, samples={vs['samples_count']}, "
            f"irs={vs['irs_count']}, midi={vs['midi_count']}, rules={vs['rules_count']}"
        )
    except Exception:
        pass
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
    if action == "status":
        print(
            json.dumps(
                {"enabled": desk.enabled(), "mode": desk.mode(), "flag": str(desk.flag_file())}
            )
        )
        return 0
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

    transport = getattr(args, "transport", "stdio") or "stdio"
    url = getattr(args, "url", None)
    if getattr(args, "status", False):
        proj = Path(args.project) if getattr(args, "project", None) else None
        for row in clients.client_statuses(project=proj):
            if row["error"]:
                state = f"error ({row['error']})"
            elif row["configured"]:
                state = f"ok ({row['transport']})"
            elif row["exists"]:
                state = "present, not registered"
            else:
                state = "not configured"
            where = row["path"] or "CLI managed"
            print(f"{row['id']:<15} {state:<24} {where}")
        return 0
    if getattr(args, "write", False):
        if not args.client:
            print(
                "config --write requires a client name (e.g. fl-mcp config claude-desktop --write, "
                "or 'all')",
                file=sys.stderr,
            )
            return 2
        proj = Path(args.project) if getattr(args, "project", None) else None
        if args.client == "all":
            rows = clients.install_all(
                args.source,
                not args.no_audio,
                project=proj,
                transport=transport,
                url=url,
                only_installed=True,
            )
            for row in rows:
                status = "ok" if row.get("ok") else f"ERR ({row.get('error')})"
                where_txt = row.get("path") or " ".join(row.get("command", ())) or row.get("id")
                print(f"[{status}] {row.get('label', row.get('id'))} -> {where_txt}")
            return 0 if all(r.get("ok", True) for r in rows) else 1
        try:
            res = clients.install_client(
                args.client,
                args.source,
                not args.no_audio,
                project=proj,
                transport=transport,
                url=url,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(exc, file=sys.stderr)
            return 1
        title = clients.CLIENTS[args.client][0]
        if res["method"] == "cli":
            print(f"Registered {title} via: {' '.join(res['command'])}")
        elif res["written"]:
            bak = f" (backup: {res['backup']})" if res["backup"] else ""
            print(f"Registered {title} in {res['path']}{bak}")
        else:
            print(f"{title} already up to date in {res['path']}")
        return 0
    names = list(clients.CLIENTS) if args.client in (None, "all") else [args.client]
    for name in names:
        where, text = clients.snippet(
            name, args.source, not args.no_audio, transport=transport, url=url
        )
        print(f"# {clients.CLIENTS[name][0]}: {where}\n{text}\n")
    return 0


def _onboard(args: argparse.Namespace) -> int:
    from fl_mcp import install

    res = install.onboard(
        fl_dir=getattr(args, "fl_dir", None),
        notes_dir=getattr(args, "notes_dir", None),
        auto_install_midi=bool(getattr(args, "install_midi", False)),
        configure_midi=not bool(getattr(args, "no_fl_midi", False)),
        client=getattr(args, "client", "all") or "all",
        transport=getattr(args, "transport", "http") or "http",
        source=getattr(args, "source", "auto") or "auto",
        start_service=not bool(getattr(args, "no_service", False)),
        autostart_service=bool(getattr(args, "autostart", False)),
        desktop_mode=getattr(args, "desktop", None),
        prewarm=bool(getattr(args, "prewarm", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0 if res.get("ok") else 1
    for name, step in (res.get("steps") or {}).items():
        if isinstance(step, dict):
            ok = bool(step.get("ok", True))
            detail = step.get("error") or (
                f"{len(step.get('files', []))} files" if "files" in step else ""
            )
            if not detail and "mcp_url" in step:
                detail = str(step["mcp_url"])
            elif not detail and "port_found" in step:
                detail = "port ok" if step["port_found"] else "port missing"
            elif not detail and "updated" in step:
                detail = f"configured {len(step['updated'])} FL version(s)"
            print(f"[{'ok' if ok else '!!'}] {name:<13} {detail}".rstrip())
    diag = res.get("fl_diagnostics") or {}
    if diag.get("issues"):
        print("\nRemaining FL Studio MIDI steps:")
        for issue in diag["issues"]:
            print(f"  - {issue}")
    else:
        print("\nFL Studio MIDI bindings look ready. Run `fl-mcp doctor` or open FL Studio.")
    return 0 if res.get("ok") else 1


def _setup(args: argparse.Namespace) -> int:
    from fl_mcp import clients, desk, install, paths, service_daemon
    from fl_mcp.bridge import Bridge, FLError, match_port

    if getattr(args, "auto", False):
        return _onboard(args)

    transport = getattr(args, "transport", "stdio") or "stdio"
    url = getattr(args, "url", None)
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
        desk.set_enabled(args.desktop)
        steps.append(("desktop", True, f"{desk.mode()}: non-intrusive desktop control enabled"))
    if getattr(args, "service", False):
        try:
            svc = service_daemon.start_daemon()
            url = url or svc["mcp_url"]
            steps.append(
                ("service", True, f"background daemon ready ({svc['url']}, {svc['mcp_url']})")
            )
        except Exception as exc:
            steps.append(("service", False, str(exc)))
    wrote_client = False
    if getattr(args, "write", False):
        try:
            res = clients.install_client(
                args.client,
                args.source,
                not getattr(args, "no_audio", False),
                transport=transport,
                url=url,
            )
            where_txt = res.get("path") or " ".join(res.get("command", ()))
            steps.append(
                ("client", True, f"registered {clients.CLIENTS[args.client][0]} ({where_txt})")
            )
            wrote_client = True
        except (OSError, ValueError, RuntimeError) as exc:
            steps.append(("client", False, str(exc)))
    for name, ok, text in steps:
        print(f"[{'ok' if ok else '!!'}] {name:<10} {text}")
    print(
        "\nIn FL Studio: Options > MIDI Settings. Under Input, select the FL MCP port, set its "
        "controller type to 'FL MCP' and enable it; under Output select the same port and give it "
        "the same port number. Then:\n"
    )
    where, text = clients.snippet(
        args.client,
        args.source,
        not getattr(args, "no_audio", False),
        transport=transport,
        url=url,
    )
    if not wrote_client:
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


def _service(args: argparse.Namespace) -> int:
    from fl_mcp import app as studio_app
    from fl_mcp import service_daemon

    action = args.action
    if action == "run":
        return service_daemon.run_foreground(
            host=args.host,
            port=args.port,
            poll=not args.no_poll,
            poll_interval=args.poll_interval,
        )
    if action == "start":
        try:
            st = service_daemon.start_daemon(
                host=args.host,
                port=args.port,
                poll=not args.no_poll,
                poll_interval=args.poll_interval,
            )
        except Exception as exc:
            print(exc, file=sys.stderr)
            return 1
        tag = "already running" if st.get("already_running") else "started"
        print(f"service {tag} (pid={st['pid']}) | UI: {st['url']} | MCP: {st['mcp_url']}")
        if getattr(args, "open", False):
            studio_app.open_studio_window(
                str(st["url"]), host=args.host, port=int(st.get("port") or args.port)
            )
        return 0
    if action == "restart":
        try:
            st = service_daemon.restart_daemon(
                host=args.host,
                port=args.port,
                poll=not args.no_poll,
                poll_interval=args.poll_interval,
            )
        except Exception as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"service restarted (pid={st['pid']}) | UI: {st['url']} | MCP: {st['mcp_url']}")
        return 0
    if action == "stop":
        res = service_daemon.stop_daemon()
        if not res["was_running"]:
            print("service is not running")
        elif res["stopped"]:
            print(f"service stopped (pid={res['pid']})")
        else:
            print(f"failed to stop service pid={res['pid']}", file=sys.stderr)
            return 1
        return 0
    if action == "status":
        st = service_daemon.service_status()
        if getattr(args, "json", False):
            print(json.dumps(st, indent=2, ensure_ascii=False))
        elif st["running"]:
            auto = "on" if st["autostart"]["enabled"] else "off"
            print(
                f"service: running (pid={st['pid']}) | UI: {st['url']} | "
                f"MCP: {st['mcp_url']} | autostart: {auto} | log: {st['log_path']}"
            )
        else:
            auto = "on" if st["autostart"]["enabled"] else "off"
            print(f"service: stopped | autostart: {auto} | log: {st['log_path']}")
        return 0
    if action == "logs":
        txt = service_daemon.read_logs(lines=getattr(args, "lines", 40))
        print(txt or "(no service log yet)")
        return 0
    if action == "autostart":
        mode = getattr(args, "mode", None) or "status"
        if mode == "enable":
            res = service_daemon.enable_autostart(port=args.port, poll=not args.no_poll)
            print(f"autostart enabled ({res['backend']})")
        elif mode == "disable":
            res = service_daemon.disable_autostart()
            print(f"autostart disabled ({res['backend']})")
        else:
            res = service_daemon.autostart_status()
            print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    return 2


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


def clients_sources() -> list[str]:
    from fl_mcp import clients

    return list(clients.SOURCES)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fl-mcp", description="MCP server for FL Studio.")
    sub = parser.add_subparsers(dest="cmd")
    serve_p = sub.add_parser("serve", help="run the MCP server (stdio default, or http/sse)")
    serve_p.add_argument(
        "--transport", choices=["stdio", "streamable-http", "sse"], default="stdio"
    )
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument(
        "--profile",
        choices=["full", "compact", "produce", "mix", "read-only"],
        help="tool profile preset (sets FL_MCP_PROFILE)",
    )
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
        "action", choices=["on", "off", "stealth", "polite", "foreground", "status", "test"]
    )
    conf = sub.add_parser(
        "config", help="print or write the config that registers this server with a client"
    )
    conf.add_argument("client", nargs="?", choices=[*sorted(clients_names()), "all"])
    conf.add_argument("--source", choices=clients_sources(), default="pypi")
    conf.add_argument("--no-audio", action="store_true", help="leave out the audio extra")
    conf.add_argument(
        "--write", action="store_true", help="write the registration directly to the client config"
    )
    conf.add_argument(
        "--project", help="project folder for workspace-scoped clients (cursor, vscode)"
    )
    conf.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (default) or background http service",
    )
    conf.add_argument("--url", help="HTTP MCP endpoint URL (default: http://127.0.0.1:8765/mcp)")
    conf.add_argument(
        "--status", action="store_true", help="check existing AI client registrations"
    )
    setup = sub.add_parser("setup", help="install the scripts, check MIDI and show what is left")
    setup.add_argument("--fl-dir", help="FL Studio user data folder (contains Settings)")
    setup.add_argument("--notes-dir", help="folder shared with the piano roll script")
    setup.add_argument(
        "--client", choices=[*sorted(clients_names()), "all"], default="claude-desktop"
    )
    setup.add_argument("--source", choices=clients_sources(), default="pypi")
    setup.add_argument("--no-audio", action="store_true", help="leave out the audio extra")
    setup.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio", help="client transport mode"
    )
    setup.add_argument("--url", help="HTTP MCP endpoint URL when using --transport http")
    setup.add_argument(
        "--service",
        action="store_true",
        help="start the detached background Studio UI + HTTP MCP daemon",
    )
    setup.add_argument(
        "--write",
        action="store_true",
        help="write the MCP registration directly to --client config",
    )
    setup.add_argument(
        "--auto",
        action="store_true",
        help="run full autonomous onboarding (same as `fl-mcp onboard`)",
    )
    setup.add_argument("--json", action="store_true", help="output JSON report when using --auto")
    setup.add_argument(
        "--desktop",
        nargs="?",
        const="polite",
        choices=["stealth", "polite", "foreground"],
        help="also enable desktop control (default mode: polite)",
    )
    onb = sub.add_parser(
        "onboard", help="autonomous one-shot setup: scripts, virtual MIDI, FL F10, daemon, clients"
    )
    onb.add_argument("--fl-dir", help="FL Studio user data folder (contains Settings)")
    onb.add_argument("--notes-dir", help="folder shared with the piano roll script")
    onb.add_argument("--client", choices=[*sorted(clients_names()), "all"], default="all")
    onb.add_argument("--source", choices=clients_sources(), default="auto")
    onb.add_argument("--transport", choices=["stdio", "http"], default="http")
    onb.add_argument(
        "--install-midi",
        action="store_true",
        help="auto-install loopMIDI via winget on Windows if missing",
    )
    onb.add_argument("--no-fl-midi", action="store_true", help="skip FL F10 registry pairing")
    onb.add_argument("--no-service", action="store_true", help="do not start background daemon")
    onb.add_argument("--autostart", action="store_true", help="enable OS login autostart")
    onb.add_argument(
        "--desktop",
        nargs="?",
        const="polite",
        choices=["stealth", "polite", "foreground"],
        help="also enable desktop mode",
    )
    onb.add_argument("--prewarm", action="store_true", help="prewarm uvx/tool package cache")
    onb.add_argument("--json", action="store_true", help="output structured JSON report")
    svc = sub.add_parser(
        "service", help="manage persistent background daemon (Studio UI + HTTP/SSE MCP + poller)"
    )
    svc.add_argument(
        "action", choices=["start", "stop", "restart", "status", "logs", "run", "autostart"]
    )
    svc.add_argument(
        "mode", nargs="?", choices=["enable", "disable", "status"], help="autostart mode"
    )
    svc.add_argument("--host", default="127.0.0.1")
    svc.add_argument("--port", type=int, default=8765)
    svc.add_argument("--poll-interval", type=float, default=5.0)
    svc.add_argument(
        "--no-poll", action="store_true", help="disable automatic background FL polling"
    )
    svc.add_argument("--open", action="store_true", help="open Studio desktop app after starting")
    svc.add_argument("--json", action="store_true", help="output JSON for status")
    svc.add_argument("--lines", type=int, default=40, help="log tail lines")
    app_p = sub.add_parser("app", help="open FL-MCP Studio as a native desktop application window")
    app_p.add_argument("--host", default="127.0.0.1")
    app_p.add_argument("--port", type=int, default=8765)
    app_p.add_argument("--poll-interval", type=float, default=5.0)
    app_p.add_argument("--no-poll", action="store_true", help="disable background FL polling")
    app_p.add_argument(
        "--backend",
        choices=["auto", "webview", "app-window", "native", "browser"],
        default="auto",
        help="desktop window runtime",
    )
    app_p.add_argument("--native", action="store_true", help="use built-in tkinter panel")
    app_p.add_argument(
        "--browser", action="store_true", help="open in browser instead of app shell"
    )
    app_p.add_argument("--foreground", action="store_true", help="keep server inside app process")
    app_p.add_argument("--status", action="store_true", help="inspect OS, arch & desktop runtime")
    app_p.add_argument("--json", action="store_true", help="output JSON")
    app_p.add_argument(
        "--install-shortcut",
        action="store_true",
        help="register OS Start Menu / Applications / .desktop launcher",
    )
    dash = sub.add_parser("dashboard", help="open Studio desktop app (or headless server)")
    dash.add_argument("--port", type=int, default=8765, help="HTTP port on 127.0.0.1 (0 = random)")
    dash.add_argument("--no-open", action="store_true", help="do not open window automatically")
    dash.add_argument(
        "--browser", action="store_true", help="force browser tab instead of app window"
    )
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
    vlt = sub.add_parser(
        "vault", help="manage offline AI models, SoundFonts, drum/IR packs, frameworks & rules"
    )
    vlt.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "download", "rules"],
        help="status (default), download assets/models, or install agent rulebooks",
    )
    vlt.add_argument(
        "--target",
        default="all",
        choices=["all", "models", "soundfonts", "samples", "irs", "midi"],
        help="download category target (default: all)",
    )
    vlt.add_argument("--workspace", help="target project directory for `vault rules`")
    vlt.add_argument("--force", action="store_true", help="re-download even if present")
    vlt.add_argument("--json", action="store_true", help="output structured JSON")
    lch = sub.add_parser("launch", help="launch FL Studio safely on the interactive desktop")
    lch.add_argument("project", nargs="?", help="optional .flp project to open")
    lch.add_argument("--json", action="store_true", help="output JSON")
    cap = sub.add_parser(
        "capture", help="record FL Studio window in the background via ffmpeg gfxcapture (Windows)"
    )
    cap.add_argument("out", help="output .mp4 file path")
    cap.add_argument("--seconds", type=float, required=True, help="capture duration in seconds")
    cap.add_argument("--fps", type=int, default=30, help="frame rate (default: 30)")
    cap.add_argument("--crf", type=int, default=16, help="x264 CRF quality (default: 16)")
    cap.add_argument("--title", default="FL Studio", help="window title match (default: FL Studio)")
    cap.add_argument(
        "--keep-audio", action="store_true", help="keep FL64.exe audio unmuted during capture"
    )
    cap.add_argument(
        "--no-play", action="store_true", help="do not auto-toggle Spacebar playback during capture"
    )
    cap.add_argument("--json", action="store_true", help="output JSON")
    cap.add_argument("--project", help="open this .flp in FL first and wait for it to load")
    dlv = sub.add_parser(
        "deliver", help="ship a score JSON: per-channel MIDI, YouTube chapters, tagged MP3"
    )
    dlv.add_argument("score_json", help="score JSON path (same shape as `fl-mcp score`)")
    dlv.add_argument("out", help="output folder")
    dlv.add_argument("--wav", help="master wav to encode as a 320k MP3")
    dlv.add_argument("--cover", help="cover image embedded in the MP3")
    dlv.add_argument("--title", default="", help="MP3 title tag")
    dlv.add_argument("--artist", default="", help="MP3 artist tag")
    dlv.add_argument("--album", default="", help="MP3 album tag")
    dlv.add_argument("--json", action="store_true", help="output JSON")
    rnd = sub.add_parser("renders", help="inspect or prune ~/.fl-mcp/renders cache")
    rnd.add_argument("--clean", action="store_true", help="delete old render folders")
    rnd.add_argument("--max-keep", type=int, default=10, help="keep N newest renders (default: 10)")
    rnd.add_argument(
        "--max-age-hours", type=float, default=24.0, help="delete renders older than H hours"
    )
    rnd.add_argument("--json", action="store_true", help="output JSON")
    sf2_p = sub.add_parser(
        "sf2", help="inspect or patch SoundFont 2.01 (.sf2) gen 38 (-100 dB release) curves"
    )
    sf2_p.add_argument("path", help="path to .sf2 file")
    sf2_p.add_argument(
        "--release",
        help="seconds (e.g. 2.6) or preset (wind, bowed, piano, guitar, plucked, mallet, perc)",
    )
    sf2_p.add_argument("--out", help="output .sf2 path (default: patch in-place with .bak)")
    sf2_p.add_argument("--no-backup", action="store_true", help="skip .bak when patching in-place")
    sf2_p.add_argument("--json", action="store_true", help="output JSON")
    scr = sub.add_parser(
        "score", help="compile a structured score JSON file into a hybrid multi-channel .flp"
    )
    scr.add_argument("score_json", help="input score JSON path")
    scr.add_argument("template", help="template .flp path")
    scr.add_argument("out", help="output .flp path")
    scr.add_argument("--donor", help="optional donor .flp providing a Sampler channel seed")
    scr.add_argument(
        "--no-harmony", action="store_true", help="skip automatic 10-pass ensemble harmony solver"
    )
    scr.add_argument("--json", action="store_true", help="output JSON")
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
    elif args.cmd == "onboard":
        raise SystemExit(_onboard(args))
    elif args.cmd == "dashboard":
        from fl_mcp.dashboard import serve_dashboard

        raise SystemExit(
            serve_dashboard(
                port=args.port,
                open_browser=not args.no_open,
                poll_first=args.poll,
                backend="browser" if getattr(args, "browser", False) else "auto",
            )
        )
    elif args.cmd == "app":
        from fl_mcp import app as studio_app

        if args.status:
            info = studio_app.detect_runtime()
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                print(
                    f"{info['os']} ({info['arch']}) | backend={info['default_backend']} | "
                    f"runtime={info['app_window'] or ('tkinter' if info['tkinter'] else 'browser')}"
                )
            raise SystemExit(0)
        if args.install_shortcut:
            res = studio_app.install_app_shortcut()
            print(json.dumps(res, indent=2) if args.json else f"Installed: {res['path']}")
            raise SystemExit(0)
        backend = "native" if args.native else ("browser" if args.browser else args.backend)
        raise SystemExit(
            studio_app.launch_app(
                host=args.host,
                port=args.port,
                poll=not args.no_poll,
                poll_interval=args.poll_interval,
                backend=backend,
                embedded=args.foreground,
            )
        )
    elif args.cmd == "traffic":
        raise SystemExit(_traffic(args))
    elif args.cmd == "templates":
        raise SystemExit(_templates(args))
    elif args.cmd == "vault":
        from fl_mcp import vault

        if args.action == "download":
            res = vault.download(target=args.target, force=args.force)
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Vault download ({args.target}): downloaded={len(res['downloaded'])}, "
                    f"skipped={len(res['skipped'])}, errors={len(res['errors'])}"
                )
            raise SystemExit(0 if res["ok"] else 1)
        if args.action == "rules":
            res = vault.install_rules(workspace=args.workspace)
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                for w in res["written"]:
                    print(f"[{w['action']}] {w['target']}: {w['path']}")
            raise SystemExit(0)
        st = vault.status()
        if args.json:
            print(json.dumps(st, indent=2, ensure_ascii=False))
        else:
            s = st["summary"]
            mb = round(s["total_vault_bytes"] / (1024 * 1024), 1)
            print(
                f"Vault ({mb} MB) | models={s['models_installed']}/{s['models_total']} | "
                f"sf2={s['soundfonts_count']} | samples={s['samples_count']} | "
                f"irs={s['irs_count']} | waveforms={s.get('waveforms_count', 0)} | "
                f"midi={s['midi_count']} | pyscripts={s.get('pyscripts_count', 0)} | "
                f"presets={s.get('presets_count', 0)} | scores={s.get('scores_count', 0)} | "
                f"templates={s.get('templates_count', 0)} | scales={s.get('scales_count', 0)} | "
                f"fonts={s['ui_fonts_count']} | "
                f"frameworks={s['frameworks_installed']}/{s['frameworks_total']} | "
                f"rules={s['rules_count']}"
            )
            for m in st["models"]:
                tag = "OK" if m["installed"] else "MISSING"
                print(f"  [model:{tag}] {m['filename']} ({m['bytes']} B) - {m['title']}")
        raise SystemExit(0)
    elif args.cmd == "launch":
        from fl_mcp import render

        try:
            res = render.launch_fl(args.project)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Launched FL Studio (pid={res.get('pid')}) {res.get('project') or ''}".strip())
        raise SystemExit(0)
    elif args.cmd == "capture":
        from fl_mcp import capture

        try:
            res = capture.capture_fl_video(
                args.out,
                args.seconds,
                fps=args.fps,
                crf=args.crf,
                window_title=args.title,
                mute_fl_only=not args.keep_audio,
                drive_playback=not args.no_play,
                project=args.project,
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Captured {res['video']} ({res['bytes']} B, {res['duration_s']} s)")
        raise SystemExit(0)
    elif args.cmd == "renders":
        from fl_mcp import paths, render

        root = paths.data_dir() / "renders"
        if args.clean:
            res = render.cleanup_renders(
                max_keep=args.max_keep, max_age_hours=args.max_age_hours, workdir=root
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                mb = round(res["freed_bytes"] / (1024 * 1024), 2)
                print(f"Removed {res['removed']} render dir(s), freed {mb} MB")
            raise SystemExit(0)
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.is_dir() else []
        total_b = sum(
            f.stat().st_size for d in dirs for f in d.rglob("*") if f.is_file()
        )
        info = {"dir": str(root), "count": len(dirs), "bytes": total_b}
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"{root}: {len(dirs)} render(s), {round(total_b / (1024 * 1024), 2)} MB")
        raise SystemExit(0)
    elif args.cmd == "sf2":
        from fl_mcp import sf2

        try:
            if args.release is not None:
                rel: float | str
                try:
                    rel = float(args.release)
                except ValueError:
                    rel = args.release
                res = sf2.patch_sf2_release(
                    args.path, rel, out_path=args.out, backup=not args.no_backup
                )
            else:
                res = sf2.inspect_sf2(args.path)
        except (OSError, ValueError) as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            if args.release is not None:
                print(
                    f"Patched {res['file']}: release={res['release_s']}s ({res['timecents']} tc) "
                    f"updated={res['updated_zones']} inserted={res['inserted_zones']}"
                )
            else:
                r = res["release_s"]
                print(
                    f"{res['file']}: zones={res['zones']} with_gen38={res['zones_with_release']} "
                    f"release={r['min']}..{r['max']}s slam_risk={res['slam_cut_risk']}"
                )
        raise SystemExit(0)
    elif args.cmd == "score":
        from fl_mcp import flp

        try:
            data = json.loads(Path(args.score_json).read_text(encoding="utf-8"))
            res = flp.build_from_score(
                args.template,
                data,
                args.out,
                donor_flp=args.donor,
                resolve_harmony=not args.no_harmony,
            )
        except (OSError, ValueError) as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(
                f"Built {res['file']} ({res['tempo']} BPM, {len(res['channels'])} ch, "
                f"{res['sections']} sections, {res['clips']} clips)"
            )
        raise SystemExit(0)
    elif args.cmd == "deliver":
        from fl_mcp import deliver

        try:
            data = json.loads(Path(args.score_json).read_text(encoding="utf-8"))
            res = deliver.package(
                data,
                args.out,
                wav=args.wav,
                cover=args.cover,
                title=args.title,
                artist=args.artist,
                album=args.album,
            )
        except (OSError, ValueError) as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(
                f"Delivered to {res['dir']}: {len(res['midi'])} MIDI file(s), chapters "
                f"{'ok' if res['chapters_valid'] else 'INVALID'}"
                + (f", mp3 {res['mp3']['mp3']}" if "mp3" in res else "")
            )
            for note in res["chapter_issues"]:
                print(f"  note: {note}")
        raise SystemExit(0)
    elif args.cmd == "service":
        raise SystemExit(_service(args))
    elif args.cmd in ("login", "logout", "account"):
        raise SystemExit(_licence(args))
    elif args.cmd == "serve":
        if getattr(args, "profile", None):
            os.environ["FL_MCP_PROFILE"] = args.profile
        from fl_mcp.server import mcp, serve

        if args.transport == "stdio":
            serve()
        else:
            mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        from fl_mcp.server import serve

        serve()
