import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from fl_mcp import paths

DEVICE = "device_FLMCP.py"
PYSCRIPT = "FL MCP.pyscript"


def _configure_loopmidi() -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Tobias Erichsen\loopMIDI",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "DetectFeedback", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "CommandTreshhold", 0, winreg.REG_DWORD, 50000)
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Tobias Erichsen\loopMIDI\Ports",
            0,
            winreg.KEY_SET_VALUE,
        ) as ports:
            winreg.SetValueEx(ports, "FL MCP", 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def targets(fl_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(fl_dir).expanduser() if fl_dir else paths.fl_user_dir()
    settings = root / "Settings"
    return settings / "Hardware" / "FLMCP" / DEVICE, settings / "Piano roll scripts" / PYSCRIPT


def install(fl_dir: str | Path | None = None, notes_dir: str | Path | None = None) -> list[Path]:
    """Copy the controller script and the piano roll script into FL Studio's user data folder."""
    root = Path(fl_dir).expanduser() if fl_dir else paths.fl_user_dir()
    if not (root / "Settings").is_dir():
        raise SystemExit(
            f"FL Studio user data folder not found: {root}\n"
            "Start FL Studio once, or pass --fl-dir <folder that contains Settings>."
        )
    folder = Path(notes_dir).expanduser().resolve() if notes_dir else paths.data_dir()
    source = resources.files("fl_mcp").joinpath("fl")
    device_dst, script_dst = targets(root)
    device_dst.parent.mkdir(parents=True, exist_ok=True)
    script_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source.joinpath(DEVICE)), device_dst)
    text = source.joinpath(PYSCRIPT).read_text(encoding="utf-8")
    script_dst.write_text(text.replace('"@DIR@"', json.dumps(str(folder))), encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    _configure_loopmidi()
    return [device_dst, script_dst]
