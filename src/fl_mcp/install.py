from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any

from fl_mcp import paths

DEVICE = "device_FLMCP.py"
PYSCRIPT = "FL MCP.pyscript"
DEFAULT_PORT = "FL MCP"
SCRIPT_LABEL = "FL MCP"  # matches '# name=FL MCP' in device_FLMCP.py

_WINREG: Any = None


def _winreg():
    if _WINREG is not None:
        return _WINREG
    import winreg

    return winreg


def find_loopmidi() -> Path | None:
    """Locate Tobias Erichsen's loopMIDI.exe on Windows."""
    if sys.platform != "win32":
        return None
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if not root:
            continue
        cand = Path(root) / "Tobias Erichsen" / "loopMIDI" / "loopMIDI.exe"
        if cand.is_file():
            return cand
    found = shutil.which("loopMIDI.exe") or shutil.which("loopMIDI")
    return Path(found) if found else None


def _process_running_win(names: tuple[str, ...]) -> bool:
    if sys.platform != "win32":
        return False
    low = {n.lower() for n in names}
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for line in res.stdout.splitlines():
            head = line.split(",", 1)[0].strip().strip('"').lower()
            if head in low:
                return True
    except OSError:
        pass
    return False


def is_fl_running() -> bool:
    """Return True if an FL Studio process is currently running."""
    if sys.platform == "win32":
        return _process_running_win(("FL64.exe", "FL.exe", "FL64 (scaled).exe"))
    if sys.platform == "darwin":
        try:
            res = subprocess.run(
                ["pgrep", "-x", "OsxFL"], capture_output=True, text=True, timeout=3, check=False
            )
            return res.returncode == 0
        except OSError:
            return False
    return False


def is_loopmidi_running() -> bool:
    """Return True if loopMIDI.exe is currently running on Windows."""
    return _process_running_win(("loopMIDI.exe",)) if sys.platform == "win32" else False


def _configure_loopmidi(port: str = DEFAULT_PORT, *, autostart: bool = True) -> None:
    if sys.platform != "win32":
        return
    try:
        reg = _winreg()
        with reg.CreateKeyEx(
            reg.HKEY_CURRENT_USER,
            r"Software\Tobias Erichsen\loopMIDI",
            0,
            reg.KEY_SET_VALUE,
        ) as key:
            reg.SetValueEx(key, "DetectFeedback", 0, reg.REG_DWORD, 0)
            reg.SetValueEx(key, "CommandTreshhold", 0, reg.REG_DWORD, 50000)
            reg.SetValueEx(key, "StartMinimized", 0, reg.REG_DWORD, 1)
        with reg.CreateKeyEx(
            reg.HKEY_CURRENT_USER,
            r"Software\Tobias Erichsen\loopMIDI\Ports",
            0,
            reg.KEY_SET_VALUE,
        ) as ports:
            reg.SetValueEx(ports, port, 0, reg.REG_DWORD, 1)
        if autostart:
            exe = find_loopmidi()
            if exe is not None:
                with reg.CreateKeyEx(
                    reg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    reg.KEY_SET_VALUE,
                ) as run_key:
                    reg.SetValueEx(run_key, "loopMIDI", 0, reg.REG_SZ, str(exe))
    except OSError:
        pass


def _mido_port_present(port: str = DEFAULT_PORT) -> tuple[bool, list[str], list[str]]:
    from fl_mcp.bridge import match_port

    try:
        import mido

        ins = list(mido.get_input_names())
        outs = list(mido.get_output_names())
        ok = bool(
            (match_port(ins, port) or (sys.platform == "darwin" and match_port(ins, "iac")))
            and (match_port(outs, port) or (sys.platform == "darwin" and match_port(outs, "iac")))
        )
        return ok, ins, outs
    except Exception:
        return False, [], []


def ensure_loopmidi(
    port: str = DEFAULT_PORT,
    *,
    auto_install: bool = False,
    restart_if_missing_port: bool = True,
    autostart: bool = True,
) -> dict[str, Any]:
    """Ensure loopMIDI is configured, installed (optional winget), and exposing `port` live."""
    if sys.platform != "win32":
        ok, ins, outs = _mido_port_present(port)
        return {
            "ok": ok,
            "platform": sys.platform,
            "port_found": ok,
            "inputs": ins,
            "outputs": outs,
        }

    _configure_loopmidi(port, autostart=autostart)
    exe = find_loopmidi()
    installed_now = False
    install_error = ""
    if exe is None and auto_install:
        winget = shutil.which("winget")
        if winget:
            res = subprocess.run(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "TobiasErichsen.loopMIDI",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            exe = find_loopmidi()
            installed_now = exe is not None
            if not installed_now:
                install_error = (
                    res.stderr or res.stdout or f"winget exit {res.returncode}"
                ).strip()
        else:
            install_error = "winget not found on PATH"

    ok, ins, outs = _mido_port_present(port)
    restarted = False
    started = False
    if exe is not None and not ok:
        was_running = is_loopmidi_running()
        if was_running and restart_if_missing_port:
            subprocess.run(
                ["taskkill", "/F", "/IM", "loopMIDI.exe"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            time.sleep(0.5)
            restarted = True
        if not was_running or restarted:
            kwargs: dict[str, Any] = {"close_fds": True}
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
            subprocess.Popen(
                [str(exe)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
            started = not was_running
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                ok, ins, outs = _mido_port_present(port)
                if ok:
                    break
                time.sleep(0.25)

    return {
        "ok": ok,
        "installed": exe is not None,
        "installed_now": installed_now,
        "exe": str(exe) if exe else None,
        "started": started,
        "restarted": restarted,
        "port_found": ok,
        "inputs": ins,
        "outputs": outs,
        "error": install_error
        or (
            ""
            if ok
            else (
                "loopMIDI not installed; install TobiasErichsen.loopMIDI or click Auto-Setup"
                if exe is None
                else f"loopMIDI running/installed at {exe}, but '{port}' did not appear"
            )
        ),
    }


def check_macos_iac() -> dict[str, Any]:
    """Probe macOS CoreMIDI IAC Driver availability & online status without side effects."""
    if sys.platform != "darwin":
        return {"supported": False, "online": False, "ports": []}
    ok, ins, outs = _mido_port_present(DEFAULT_PORT)
    iac_online = False
    devices: list[str] = []
    try:
        import ctypes

        cm = ctypes.CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cm.MIDIGetNumberOfDevices.restype = ctypes.c_size_t
        cm.MIDIGetDevice.argtypes = [ctypes.c_size_t]
        cm.MIDIGetDevice.restype = ctypes.c_uint32
        cm.MIDIObjectGetStringProperty.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        cm.MIDIObjectGetIntegerProperty.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int32),
        ]
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        k_name = cf.CFStringCreateWithCString(None, b"name", 0x08000100)
        k_off = cf.CFStringCreateWithCString(None, b"offline", 0x08000100)
        buf = ctypes.create_string_buffer(256)
        n_dev = cm.MIDIGetNumberOfDevices()
        for idx in range(n_dev):
            dev = cm.MIDIGetDevice(idx)
            s_ref = ctypes.c_void_p()
            if cm.MIDIObjectGetStringProperty(dev, k_name, ctypes.byref(s_ref)) == 0 and s_ref:
                if cf.CFStringGetCString(s_ref, buf, 256, 0x08000100):
                    name = buf.value.decode("utf-8", "replace")
                    devices.append(name)
                    if "iac" in name.lower():
                        off_val = ctypes.c_int32(1)
                        cm.MIDIObjectGetIntegerProperty(dev, k_off, ctypes.byref(off_val))
                        iac_online = iac_online or (off_val.value == 0)
                cf.CFRelease(s_ref)
        for k in (k_name, k_off):
            if k:
                cf.CFRelease(k)
    except Exception:
        pass
    return {
        "supported": True,
        "iac_online": iac_online,
        "port_found": ok,
        "devices": devices,
        "inputs": ins,
        "outputs": outs,
    }


def _reg_values(reg: Any, subkey: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, subkey) as key:
            _, n_val, _ = reg.QueryInfoKey(key)
            for idx in range(n_val):
                name, val, _ = reg.EnumValue(key, idx)
                out[str(name)] = val
    except OSError:
        pass
    return out


def _reg_subkeys(reg: Any, subkey: str) -> list[str]:
    out: list[str] = []
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, subkey) as key:
            n_sub, _, _ = reg.QueryInfoKey(key)
            for idx in range(n_sub):
                out.append(str(reg.EnumKey(key, idx)))
    except OSError:
        pass
    return out


def diagnose_fl_midi(port: str = DEFAULT_PORT) -> dict[str, Any]:
    """Read-only diagnostic of FL Studio's persisted MIDI Input/Output/Controller bindings."""
    if sys.platform != "win32":
        if sys.platform == "darwin":
            iac = check_macos_iac()
            return {
                "platform": "darwin",
                "configured": iac["port_found"],
                "iac": iac,
                "issues": (
                    []
                    if iac["port_found"]
                    else [
                        "Enable IAC Driver (Audio MIDI Setup > MIDI Studio > IAC Driver > Online) "
                        "or run persistent `fl-mcp service start` for CoreMIDI virtual port."
                    ]
                ),
            }
        ok, ins, outs = _mido_port_present(port)
        return {
            "platform": sys.platform,
            "configured": ok,
            "inputs": ins,
            "outputs": outs,
            "issues": [] if ok else [f"No virtual MIDI port '{port}' (check ALSA snd-virmidi)."],
        }

    reg = _winreg()
    keys = paths.fl_registry_version_keys()
    if not keys:
        return {
            "platform": "win32",
            "fl_keys": [],
            "configured": False,
            "issues": ["No HKCU\\Software\\Image-Line\\FL Studio <N> key found (launch FL once)."],
        }
    active = keys[0]
    base = rf"Software\Image-Line\{active}"
    gen = _reg_values(reg, rf"{base}\General")
    in_vals = _reg_values(reg, rf"{base}\Devices\MIDI input\{port}")
    out_vals = _reg_values(reg, rf"{base}\Devices\MIDI output\{port}")
    issues: list[str] = []
    if str(gen.get("Enable MIDI input", "")) != "1":
        issues.append("FL Studio global 'Enable MIDI input' is off")
    if str(gen.get("Enable MIDI output", "")) != "1":
        issues.append("FL Studio global 'Enable MIDI output' is off")
    if not in_vals:
        issues.append(f"MIDI input device '{port}' has not been saved by FL Studio yet")
    else:
        if str(in_vals.get("Enabled", "")) != "1":
            issues.append(f"MIDI input '{port}' is disabled in F10")
        if (
            str(in_vals.get("Type", "")) != "-3"
            or str(in_vals.get("ScriptFolder", "")) != SCRIPT_LABEL
        ):
            issues.append(f"MIDI input '{port}' controller type is not set to '{SCRIPT_LABEL}'")
    in_port = str(in_vals.get("Port", "-1"))
    out_port = str(out_vals.get("Port", "-1"))
    if in_port in ("", "-1") or out_port in ("", "-1") or in_port != out_port:
        issues.append(
            f"MIDI port numbers mismatch or unassigned (input={in_port!r}, output={out_port!r})"
        )
    return {
        "platform": "win32",
        "fl_key": active,
        "fl_keys": keys,
        "fl_running": is_fl_running(),
        "enable_midi_input": str(gen.get("Enable MIDI input", "")) == "1",
        "enable_midi_output": str(gen.get("Enable MIDI output", "")) == "1",
        "input": in_vals,
        "output": out_vals,
        "configured": not issues,
        "issues": issues,
    }


def configure_fl_midi(port: str = DEFAULT_PORT) -> dict[str, Any]:
    """Auto-wire FL Studio's F10 MIDI Input/Output & Controller Type in HKCU (FL must be closed)."""
    if sys.platform != "win32":
        return {
            "ok": False,
            "error": f"Windows registry auto-pairing unsupported on {sys.platform}",
        }
    if is_fl_running():
        return {
            "ok": False,
            "fl_running": True,
            "error": (
                "FL Studio is currently running and overwrites MIDI settings when closed; "
                "close FL Studio and run `fl-mcp onboard` (or retry Auto-Setup) to bind F10."
            ),
        }
    reg = _winreg()
    keys = paths.fl_registry_version_keys()
    if not keys:
        return {
            "ok": False,
            "fl_running": False,
            "error": "FL Studio registry key not found; launch and close FL Studio once first.",
        }
    updated: list[dict[str, Any]] = []
    for key_name in keys:
        base = rf"Software\Image-Line\{key_name}"
        dev_root = rf"{base}\Devices"
        in_vals = _reg_values(reg, rf"{dev_root}\MIDI input\{port}")
        out_vals = _reg_values(reg, rf"{dev_root}\MIDI output\{port}")
        used: set[str] = set()
        for side in ("MIDI input", "MIDI output"):
            for dev_name in _reg_subkeys(reg, rf"{dev_root}\{side}"):
                if dev_name == port:
                    continue
                p_num = str(_reg_values(reg, rf"{dev_root}\{side}\{dev_name}").get("Port", "-1"))
                if p_num not in ("", "-1"):
                    used.add(p_num)
        existing_port = str(in_vals.get("Port") or out_vals.get("Port") or "-1")
        if (
            existing_port not in ("", "-1")
            and existing_port not in used
            and existing_port.isdigit()
        ):
            chosen = existing_port
        else:
            candidates = [str(n) for n in range(220, 256)] + [str(n) for n in range(10, 220)]
            chosen = next((c for c in candidates if c not in used), "220")

        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, rf"{base}\General", 0, reg.KEY_SET_VALUE) as g:
            reg.SetValueEx(g, "Enable MIDI input", 0, reg.REG_SZ, "1")
            reg.SetValueEx(g, "Enable MIDI output", 0, reg.REG_SZ, "1")
        with reg.CreateKeyEx(
            reg.HKEY_CURRENT_USER, rf"{dev_root}\MIDI input\{port}", 0, reg.KEY_SET_VALUE
        ) as k_in:
            for k, v in (
                ("Type", "-3"),
                ("ScriptFolder", SCRIPT_LABEL),
                ("IDString", "-"),
                ("Port", chosen),
                ("Enabled", "1"),
            ):
                reg.SetValueEx(k_in, k, 0, reg.REG_SZ, v)
        with reg.CreateKeyEx(
            reg.HKEY_CURRENT_USER, rf"{dev_root}\MIDI output\{port}", 0, reg.KEY_SET_VALUE
        ) as k_out:
            for k, v in (
                ("Port", chosen),
                ("Sync", "0"),
                ("SendAllNotesOff", "1"),
                ("IDString", "-"),
            ):
                reg.SetValueEx(k_out, k, 0, reg.REG_SZ, v)
        updated.append({"fl_key": key_name, "port_number": int(chosen)})
    return {"ok": True, "port": port, "updated": updated, "fl_running": False}


def bake_piano_roll_script(
    queue: dict[str, Any],
    fl_dir: str | Path | None = None,
    notes_dir: str | Path | None = None,
) -> Path | None:
    """Embed queued notes into FL MCP.pyscript fallback for sandboxed FL 25.2.5+ builds."""
    try:
        _, script_dst = targets(fl_dir)
    except Exception:
        return None
    if not script_dst.is_file():
        return None
    try:
        text = script_dst.read_text(encoding="utf-8")
        marker_re = re.compile(r"^_BAKED\s*=.*# @BAKED@$", re.MULTILINE)
        replacement = f"_BAKED = {repr(queue)}  # @BAKED@"
        if marker_re.search(text):
            updated = marker_re.sub(lambda _m: replacement, text, count=1)
        elif "# @BAKED@" in text:
            updated = text.replace("# @BAKED@", replacement, 1)
        else:
            return None
        if notes_dir is not None:
            resolved = json.dumps(str(Path(notes_dir).expanduser().resolve()))
            updated = re.sub(
                r"^DIR\s*=.*$",
                lambda _m: f"DIR = {resolved}",
                updated,
                count=1,
                flags=re.MULTILINE,
            )
        script_dst.write_text(updated, encoding="utf-8")
        return script_dst
    except OSError:
        return None


def targets(fl_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(fl_dir).expanduser() if fl_dir else paths.fl_user_dir()
    settings = root / "Settings"
    return settings / "Hardware" / "FLMCP" / DEVICE, settings / "Piano roll scripts" / PYSCRIPT


def install(
    fl_dir: str | Path | None = None,
    notes_dir: str | Path | None = None,
    port: str = DEFAULT_PORT,
) -> list[Path]:
    """Copy the controller script and the piano roll script into FL Studio's user data folder."""
    root = Path(fl_dir).expanduser() if fl_dir else paths.fl_user_dir()
    if not (root / "Settings").is_dir():
        raise SystemExit(
            f"FL Studio user data folder not found: {root}\n"
            "Start FL Studio once, or pass --fl-dir <folder that contains Settings>."
        )
    source = resources.files("fl_mcp").joinpath("fl")
    device_dst, script_dst = targets(root)
    device_dst.parent.mkdir(parents=True, exist_ok=True)
    script_dst.parent.mkdir(parents=True, exist_ok=True)
    folder = Path(notes_dir).expanduser().resolve() if notes_dir else paths.notes_dir(root)
    shutil.copyfile(str(source.joinpath(DEVICE)), device_dst)
    text = source.joinpath(PYSCRIPT).read_text(encoding="utf-8")
    script_dst.write_text(text.replace('"@DIR@"', json.dumps(str(folder))), encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    _configure_loopmidi(port)
    return [device_dst, script_dst]


def onboard(
    *,
    fl_dir: str | Path | None = None,
    notes_dir: str | Path | None = None,
    port: str = DEFAULT_PORT,
    auto_install_midi: bool = False,
    configure_midi: bool = True,
    client: str = "all",
    transport: str = "http",
    source: str = "auto",
    start_service: bool = True,
    autostart_service: bool = False,
    desktop_mode: str | None = None,
    prewarm: bool = False,
) -> dict[str, Any]:
    """One-shot setup: scripts, virtual MIDI, FL registry pairing, daemon & AI clients."""
    from fl_mcp import clients, desk, service_daemon

    report: dict[str, Any] = {"ok": True, "steps": {}}

    # 1. Prewarm paths + optional uv tool / cache warmup
    for p in (
        paths.data_dir(),
        paths.logs_dir(),
        paths.cache_dir(),
        paths.context_dir(),
        paths.memory_dir(),
    ):
        p.mkdir(parents=True, exist_ok=True)
    if prewarm:
        report["steps"]["prewarm"] = clients.prewarm_uvx()

    # 2. FL Studio scripts
    try:
        installed = install(fl_dir, notes_dir, port=port)
        report["steps"]["scripts"] = {"ok": True, "files": [str(p) for p in installed]}
    except SystemExit as exc:
        report["ok"] = False
        report["steps"]["scripts"] = {"ok": False, "error": str(exc)}

    # 3. Virtual MIDI port (Windows loopMIDI / macOS IAC / Linux ALSA)
    midi_res = ensure_loopmidi(port, auto_install=auto_install_midi)
    report["steps"]["virtual_midi"] = midi_res
    if not midi_res.get("ok"):
        report["ok"] = False

    # 4. FL Studio MIDI settings auto-pairing (safe when FL is closed)
    if configure_midi and sys.platform == "win32":
        fl_res = configure_fl_midi(port)
    else:
        fl_res = diagnose_fl_midi(port)
        if "configured" in fl_res and "ok" not in fl_res:
            fl_res["ok"] = bool(fl_res["configured"])
    report["steps"]["fl_midi"] = fl_res

    # 5. Optional desktop stealth/polite arming
    if desktop_mode:
        flag = desk.set_enabled(desktop_mode)
        report["steps"]["desktop"] = {"ok": True, "mode": desk.mode(), "path": str(flag)}

    # 6. Background service + autostart
    http_url: str | None = None
    if start_service:
        try:
            svc = service_daemon.start_daemon()
            http_url = str(svc.get("mcp_url") or clients.DEFAULT_HTTP_URL)
            if autostart_service:
                try:
                    svc["autostart"] = service_daemon.enable_autostart()
                except Exception as exc:
                    svc["autostart_error"] = str(exc)
            report["steps"]["service"] = {"ok": True, **svc}
        except Exception as exc:
            report["steps"]["service"] = {"ok": False, "error": str(exc)}

    # 7. AI Client registration (installed clients when client=='all')
    try:
        if client == "all":
            cl_res: Any = clients.install_all(
                source=source,
                transport=transport,
                url=http_url,
                only_installed=True,
            )
            ok_cl = all(r.get("ok", True) for r in cl_res)
        elif client:
            cl_res = clients.install_client(
                client,
                source=source,
                transport=transport,
                url=http_url,
            )
            ok_cl = True
        else:
            cl_res = []
            ok_cl = True
        report["steps"]["clients"] = {
            "ok": ok_cl,
            "result": cl_res,
            "statuses": clients.client_statuses(),
        }
    except Exception as exc:
        report["steps"]["clients"] = {"ok": False, "error": str(exc)}

    report["fl_diagnostics"] = diagnose_fl_midi(port)
    return report
