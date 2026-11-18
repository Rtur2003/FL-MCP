import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PYSCRIPT = Path(__file__).parents[1] / "src" / "fl_mcp" / "fl" / "FL MCP.pyscript"


class Note:
    def __init__(self):
        self.number = self.time = self.length = 0
        self.velocity = 0.0


class Score:
    PPQ = 96

    def __init__(self):
        self.notes = []
        self.cleared = 0

    @property
    def noteCount(self):
        return len(self.notes)

    def addNote(self, note):
        copy = Note()
        copy.__dict__.update(note.__dict__)
        self.notes.append(copy)

    def getNote(self, index):
        return self.notes[index]

    def deleteNote(self, index):
        self.notes.pop(index)

    def clearNotes(self, all=False):
        assert all is True
        self.notes.clear()
        self.cleared += 1


@pytest.fixture
def roll(monkeypatch, tmp_path):
    messages = []
    flp = types.ModuleType("flpianoroll")
    flp.score, flp.Note = Score(), Note
    flp.ScriptDialog = lambda title, text: ("dialog", title, text)
    flp.Utils = SimpleNamespace(ShowMessage=messages.append)
    monkeypatch.setitem(sys.modules, "flpianoroll", flp)

    def load(folder=tmp_path):
        source = PYSCRIPT.read_text(encoding="utf-8").replace('"@DIR@"', json.dumps(str(folder)))
        module = types.ModuleType("roll")
        exec(compile(source, "FL MCP.pyscript", "exec"), module.__dict__)
        return module

    def queue(notes, replace=False, serial=7):
        (tmp_path / "inbox.json").write_text(
            json.dumps({"serial": serial, "replace": replace, "notes": notes}), encoding="utf-8"
        )

    return SimpleNamespace(
        load=load,
        queue=queue,
        score=flp.score,
        messages=messages,
        dir=tmp_path,
        state=lambda: json.loads((tmp_path / "state.json").read_text()),
    )


def test_script_runs_without_a_modal_dialog(roll):
    module = roll.load()
    assert not hasattr(module, "createDialog")
    roll.queue([[60, 0, 1, 100]])
    module.apply()
    assert roll.score.noteCount == 1


def test_notes_are_converted_to_ticks(roll):
    roll.queue([[60, 0, 1, 100], [64, 1.5, 0.5, 127], [67, 2, 0.001, 1]])
    roll.load().apply(None)
    got = [(n.number, n.time, n.length, round(n.velocity, 3)) for n in roll.score.notes]
    assert got == [(60, 0, 96, 0.787), (64, 144, 48, 1.0), (67, 192, 1, 0.008)]


def test_apply_is_idempotent_and_replace_clears(roll):
    roll.queue([[60, 0, 1, 100], [62, 1, 1, 100]])
    module = roll.load()
    module.apply(None)
    module.apply(None)
    assert roll.score.noteCount == 2
    roll.queue([[72, 0, 1, 100]], replace=True)
    module.apply(None)
    assert [n.number for n in roll.score.notes] == [72] and roll.score.cleared == 1


def test_state_export_reports_serial_and_beats(roll):
    roll.queue([[60, 0.25, 0.5, 64]], serial=1234)
    roll.load().apply(None)
    state = roll.state()
    assert state["serial"] == 1234 and state["count"] == 1 and state["ppq"] == 96
    assert state["notes"] == [[60, 0.25, 0.5, 64]] and state["ts"] > 0
    assert not (roll.dir / "state.json.tmp").exists()


def test_export_without_queue(roll):
    note = Note()
    note.number, note.time, note.length, note.velocity = 48, 96, 96, 0.5
    roll.score.addNote(note)
    roll.load().apply(None)
    state = roll.state()
    assert state["serial"] is None and state["notes"] == [[48, 1.0, 1.0, 64]]
    assert roll.score.noteCount == 1


def test_errors_are_shown_not_raised(roll):
    (roll.dir / "inbox.json").write_text("{broken", encoding="utf-8")
    roll.load().apply(None)
    assert roll.messages and roll.messages[0].startswith("FL MCP: JSONDecodeError")


def test_default_folder_when_not_templated(roll, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    module = roll.load("@DIR@")
    assert module.DIR == str(tmp_path / ".fl-mcp")


def test_selective_delete_and_empty_replace_clear(roll):
    roll.queue([[60, 0, 1, 100], [60, 2, 1, 100], [64, 0, 1, 100]])
    module = roll.load()
    module.apply(None)
    assert roll.score.noteCount == 3

    (roll.dir / "inbox.json").write_text(
        json.dumps({"serial": 9, "replace": False, "delete": [[60, 2]], "notes": []}),
        encoding="utf-8",
    )
    module.apply(None)
    assert [(n.number, n.time) for n in roll.score.notes] == [(60, 0), (64, 0)]

    roll.queue([], replace=True, serial=10)
    module.apply(None)
    assert roll.score.noteCount == 0
    assert roll.score.cleared == 1


def test_digest_idempotency_preserves_manual_edits(roll):
    roll.queue([[60, 0, 1, 100]], replace=True, serial=11)
    scr = roll.load()
    scr.apply()
    assert [n.number for n in roll.score.notes] == [60]
    st = roll.state()
    assert "sig" in st and st["serial"] == 11

    # Simulate user editing Piano Roll manually without changing inbox.json
    roll.score.clearNotes(all=True)
    n = roll.score.notes
    from tests.test_pyscript import Note

    manual = Note()
    manual.number, manual.time, manual.length, manual.velocity = 67, 0, 96, 0.9
    n.append(manual)
    scr.apply()
    assert [x.number for x in roll.score.notes] == [67]

    # New serial overwrites cleanly
    roll.queue([[72, 0, 1, 100]], replace=True, serial=12)
    scr.apply()
    assert [x.number for x in roll.score.notes] == [72]


def test_baked_fallback_applies_when_file_io_is_blocked(roll, tmp_path, monkeypatch):
    import builtins
    import runpy

    from fl_mcp import install

    fl_home = tmp_path / "fl"
    (fl_home / "Settings").mkdir(parents=True)
    notes_folder = tmp_path / "notes"
    install.install(fl_home, notes_folder)
    baked_path = install.bake_piano_roll_script(
        {"serial": 77, "replace": True, "notes": [[64, 0, 1, 100]]},
        fl_dir=fl_home,
        notes_dir=notes_folder,
    )
    assert baked_path is not None and baked_path.is_file()

    orig_open = builtins.open

    def blocked_open(file, *a, **kw):
        if str(file).endswith(("inbox.json", "state.json")):
            raise OSError("sandboxed FL file I/O")
        return orig_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", blocked_open)
    ns = runpy.run_path(str(baked_path))
    if not roll.score.notes:
        ns["apply"]()
    assert not roll.messages, roll.messages
    assert [n.number for n in roll.score.notes] == [64]
