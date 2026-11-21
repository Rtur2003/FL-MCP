"""Tests for D:\\FL parity: capture/mute fixes, articulation physics, guitar solver, local-key
harmony, score->FLP articulation wiring, delivery packaging and the `fl_produce score` action."""

from __future__ import annotations

import base64
import io
import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any

import mido
import pytest

from fl_mcp import capture, cli, deliver, flp, render
from fl_mcp.music import articulation as art
from fl_mcp.music import harm, theory
from tests.test_workflows_v2 import _call_tool, _make_sf_template

# ---- 1. CoreAudio per-app mute: the PowerShell param block must be first


def test_mute_command_uses_encoded_scriptblock_with_param_first():
    cmd = capture._mute_command("on", "FL64")
    assert cmd[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(cmd[4]).decode("utf-16-le")
    assert script.startswith("& {")
    assert script.split("{", 1)[1].lstrip().startswith("param(")
    assert script.rstrip().endswith("} 'on' 'FL64'")
    with pytest.raises(ValueError, match="invalid process name"):
        capture._mute_command("on", "FL64; calc")
    with pytest.raises(ValueError, match="mute mode"):
        capture._mute_command("loud", "FL64")


@pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell only"
)
def test_mute_script_parses_and_runs_for_real():
    res = capture.set_fl_app_mute(False)
    assert "ParserError" not in res["detail"], res
    if not res["ok"]:
        pytest.skip(f"no usable audio endpoint here: {res['detail'][:120]}")
    assert "sessions=" in res["detail"]


# ---- 2. capture_fl_video flow (fake ffmpeg / window / audio session)


class _FakeProc:
    def __init__(self, cmd: list[str], dies: bool = False) -> None:
        Path(cmd[-1]).write_bytes(b"x" * 10)
        self.dies = dies
        self.done = False
        self.returncode = 3 if dies else 0
        self.stderr = io.StringIO("gfx boom") if dies else None

    def poll(self) -> int | None:
        return self.returncode if (self.dies or self.done) else None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.done = True
        return "", ""

    def kill(self) -> None:
        self.done = True


def _wire_capture(monkeypatch: pytest.MonkeyPatch, dies: bool = False) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setattr(capture, "current_desktop_name", lambda: "Default")
    monkeypatch.setattr(capture, "find_fl_window", lambda: (77, "Song - FL Studio"))
    monkeypatch.setattr(capture, "push_fl_behind", lambda h=None: calls.append("behind") or True)
    monkeypatch.setattr(capture, "foreground_hwnd", lambda: 5)
    monkeypatch.setattr(
        capture, "restore_foreground", lambda h, skip=None: calls.append(("fg", h, skip)) or True
    )
    monkeypatch.setattr(
        capture, "set_fl_app_mute", lambda m, **_k: calls.append(("mute", m)) or {"ok": True}
    )
    monkeypatch.setattr(
        capture, "toggle_play_space", lambda h=None: calls.append(("space", h)) or True
    )
    monkeypatch.setattr(capture.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        capture.subprocess, "Popen", lambda cmd, **_k: _FakeProc(cmd, dies=dies)
    )
    return calls


def test_capture_remutes_after_playback_starts_and_restores(monkeypatch, tmp_path):
    calls = _wire_capture(monkeypatch)
    res = capture.capture_fl_video(tmp_path / "rec.mp4", 5.0)
    assert Path(res["video"]).is_file() and res["muted_fl_only"] is True
    assert "play_offset_s" in res
    assert calls.count(("mute", True)) == 4  # before, after push, +0.35 s, +1.35 s
    assert calls[-1] == ("mute", False)  # session restored in finally
    assert calls.count(("space", 77)) == 2  # start + stop
    assert ("fg", 5, 77) in calls  # owner's window brought back


def test_capture_aborts_when_ffmpeg_dies_during_startup(monkeypatch, tmp_path):
    calls = _wire_capture(monkeypatch, dies=True)
    with pytest.raises(ValueError, match="exited during start-up"):
        capture.capture_fl_video(tmp_path / "rec.mp4", 5.0)
    assert ("space", 77) not in calls  # playback never started
    assert calls[-1] == ("mute", False)


def test_capture_refuses_non_interactive_desktop_and_opens_projects(monkeypatch, tmp_path):
    _wire_capture(monkeypatch)
    monkeypatch.setattr(capture, "current_desktop_name", lambda: "exebox-9")
    with pytest.raises(ValueError, match="interactive desktop"):
        capture.capture_fl_video(tmp_path / "a.mp4", 2.0)

    monkeypatch.setattr(capture, "current_desktop_name", lambda: "Default")
    opened: list[Path] = []
    monkeypatch.setattr(render, "launch_fl", lambda p=None: opened.append(p) or {"launched": 1})
    monkeypatch.setattr(capture, "wait_fl_title", lambda s, timeout=45.0: (77, f"{s} - FL Studio"))
    res = capture.capture_fl_video(tmp_path / "b.mp4", 2.0, project=tmp_path / "SC01_Song.flp")
    assert opened and opened[0].name == "SC01_Song.flp" and Path(res["video"]).is_file()

    monkeypatch.setattr(capture, "wait_fl_title", lambda s, timeout=45.0: (77, "Untitled"))
    with pytest.raises(ValueError, match="did not load"):
        capture.capture_fl_video(tmp_path / "c.mp4", 2.0, project=tmp_path / "SC01_Song.flp")


# ---- 3. harm (chord grammar, voicings, bass)


def test_harm_parse_voicing_bass_and_walk():
    assert harm.parse("Ab7#11") == (8, (0, 4, 10, 14, 18), None)
    assert harm.parse("C6/9")[1] == (0, 4, 7, 9, 14)
    assert harm.parse("Am7/G")[2] == 7
    with pytest.raises(ValueError, match="cannot read chord"):
        harm.parse("H7")
    core, ext = harm.chord_pcs("Dm9")
    assert {2, 5, 9, 0, 4} <= core and {4, 11, 7} <= ext
    v = harm.voicing("Dm9", lo=53, hi=72)
    assert v == sorted(v) and all(53 <= n <= 72 for n in v) and {n % 12 for n in v} <= core
    b = harm.bass("Am7/G")
    assert b % 12 == 7 and 33 <= b <= 45
    assert len(harm.walk(["Dm7"], "G7")) == 4
    assert harm.name(60) == "C4"


# ---- 4. articulation physics


def test_apply_instrument_physics_modes():
    with pytest.raises(ValueError, match="unknown instrument mode"):
        art.apply_instrument_physics([[0, 1, 60, 80]], "kazoo")
    assert art.apply_instrument_physics([], "let_ring") == []

    ring = art.apply_instrument_physics([[0, 0.1, 60, 80], [5, 0.1, 64, 80]], "let_ring")
    assert ring[0][1] >= 2.79
    bounded = art.apply_instrument_physics(
        [[0, 0.1, 60, 80], [5, 0.1, 64, 80]], "let_ring", chord_bounds=[0.0, 2.0, 4.0]
    )
    assert bounded[0][1] == pytest.approx(1.975, abs=1e-3)

    pedal = art.apply_instrument_physics(
        [[0, 0.2, 48, 70], [0.5, 0.2, 60, 70]], "pedal_keys", chord_bounds=[0.0, 3.0]
    )
    assert pedal[0][1] == pytest.approx(2.972, abs=1e-3)

    wind = art.apply_instrument_physics([[0, 1.0, 72, 80], [0.9, 1.0, 74, 80]], "mono_wind")
    assert wind[0][0] + wind[0][1] == pytest.approx(wind[1][0] + 0.038, abs=1e-6)

    perc = art.apply_instrument_physics([[0, 0.1, 40, 80], [3, 0.1, 72, 80]], "perc")
    assert perc[0][1] >= 1.65 and perc[1][1] >= 0.55

    clamp = art.apply_instrument_physics(
        [[1.0, 1.0, 10, 80], [3.0, 1.0, 120, 80]], "guitar6", pitch_lo=21, pitch_hi=108
    )
    assert [e[2] for e in clamp] == [22, 108]
    pre = art.apply_instrument_physics([[1.0, 1.0, 72, 80]], "mono_wind", attack_pre_ms=65.0)
    assert pre[0][0] == pytest.approx(0.935, abs=1e-6)
    zero = art.apply_instrument_physics([[0.02, 1.0, 72, 80]], "mono_wind", attack_pre_ms=65.0)
    assert zero[0][0] == 0.0


def test_guitar6_voicing_and_section_are_playable_and_deterministic():
    for sym in ("Am7", "Dmaj9", "G7", "Em/B"):
        v = art.solve_guitar6_voicing(sym)
        strings = [s for s, _ in v]
        pitches = [p for _, p in v]
        assert len(v) == 4 and strings == sorted(set(strings)) and pitches == sorted(pitches)
        assert all(0 <= p - art.GUITAR_OPEN[s] <= 12 for s, p in v)
    assert art.solve_guitar6_voicing("Am7")[0][1] % 12 == 9

    def run() -> list[list[float]]:
        return art.pick_guitar6_section(
            [["Am7"], ["Dm7"]], 1, 8.0, lambda u: u * 0.5, rng=random.Random(1)
        )

    evs = run()
    assert evs and evs == run()
    assert all(0.18 <= e[1] <= 4.8 and 40 <= e[2] <= 71 for e in evs)


def test_chord_blocks_boundaries_and_melody_audit():
    blocks = [([["Am7"], ["Dm7", "G7"]], 1, 8.0, lambda u: u * 0.5)]
    assert art.extract_chord_boundaries(blocks) == [0.0, 4.0, 6.0, 8.0]
    slots = art.chord_slots(blocks)
    assert slots == [(0.0, 4.0, "Am7"), (4.0, 6.0, "Dm7"), (6.0, 8.0, "G7")]
    assert art.slot_boundaries(slots) == [0.0, 4.0, 6.0, 8.0]
    assert art.slot_boundaries([]) == []

    mel = [[0.5, 1.0, 69, 80], [1.0, 1.0, 70, 80], [2.0, 0.1, 71, 80], [4.5, 1.0, 62, 80]]
    assert art.audit_melody_clashes(mel, slots) == [(1.04, "Bb4", "Am7")]
    # a 65 ms attack pre-roll must judge the note at its musical beat (the next chord)
    early = [[3.90, 1.0, 65, 80]]
    assert art.audit_melody_clashes(early, slots) == [(3.94, "F4", "Am7")]
    assert art.audit_melody_clashes(early, slots, pre_ms=65.0) == []


# ---- 5. harmony solver judges clashes in the LOCAL key


def test_resolve_harmony_uses_local_key_in_modulating_scores():
    c_major = [60, 62, 64, 65, 67, 69, 71]
    eb_major = [63, 65, 67, 68, 70, 72, 74]
    grand = [[2.0 * i, 2.0, p, 80] for i, p in enumerate(c_major)]
    grand += [[100.0 + 2.0 * i, 2.0, p - 12, 80] for i, p in enumerate(eb_major)]
    events = {
        "grand": grand,
        "flute": [[100.0, 3.0, 70, 80]],
        "harp": [[100.0, 3.0, 71, 70]],  # B natural: b2 against the flute's Bb, out of Eb major
    }
    out = theory.resolve_ensemble_harmony(events)
    harp = next(e for e in out["harp"] if abs(e[0] - 100.0) < 1e-6)
    assert harp[2] % 12 in {n % 12 for n in eb_major}
    assert abs(harp[2] - 70) % 12 != 1
    assert theory.audit_ensemble_harmony(out)["b2_m9_clashes"] == 0


# ---- 6. build_from_score wires articulation physics + melody audit


def test_build_from_score_applies_modes_and_audits_melody(tmp_path):
    tpl = _make_sf_template(tmp_path / "tpl.flp")
    score = {
        "tempo": 100.0,
        "chords": [[0.0, 4.0, "Am7"]],
        "sections": [[0.0, "Main"]],
        "channels": [
            {
                "id": "piano",
                "name": "Piano",
                "sf2": r"C:\sf\p.sf2",
                "insert": 1,
                "mode": "pedal_keys",
                "audit_mel": True,
                "events": [[0.0, 1.0, 70, 80]],
            },
            {
                "id": "flute",
                "name": "Flute",
                "sf2": r"C:\sf\f.sf2",
                "insert": 2,
                "mode": "mono_wind",
                "pre_ms": 50,
                "events": [[2.0, 1.0, 69, 80]],
            },
        ],
    }
    res = flp.build_from_score(tpl, score, tmp_path / "out.flp")
    assert res["articulation"] == {"piano": "pedal_keys", "flute": "mono_wind"}
    assert list(res["melody_audit"]) == ["piano"]
    assert res["melody_audit"]["piano"][0][1:] == ["Bb4", "Am7"]
    assert Path(res["file"]).is_file()

    quiet = flp.build_from_score(tpl, {**score, "chords": None}, tmp_path / "q.flp")
    assert quiet["melody_audit"] == {} and quiet["articulation"]["piano"] == "pedal_keys"


# ---- 7. delivery packaging


def test_midi_export_and_chapters(tmp_path):
    mid = deliver.write_midi([[0.0, 1.0, 60, 90], [1.0, 0.5, 64, 70]], tmp_path / "a" / "x.mid")
    msgs = [m for m in mido.MidiFile(mid).tracks[0] if m.type in ("note_on", "note_off")]
    assert [m.type for m in msgs] == ["note_on", "note_off", "note_on", "note_off"]
    assert sum(m.time for m in msgs) == round(1.5 * 960)  # events are seconds @ 120 BPM

    files = deliver.export_channel_midis(
        {"Pno I": [[0, 1, 60, 80]], "halo_wav": [[0, 5, 60, 80]], "empty": []},
        tmp_path / "midi",
        skip=["halo_wav"],
    )
    assert set(files) == {"Pno I", "FULL_SUITE"}
    assert Path(files["Pno I"]).name == "Pno_I.mid"

    ch = deliver.youtube_chapters([[0, "A"], [30, "B"], [35, "Tiny"], [90, "C"]], 200)
    assert ch["text"] == "00:00 A\n00:35 Tiny\n01:30 C" and ch["valid"] is True
    assert any("merged into 'A'" in note for note in ch["issues"])
    early_first = deliver.youtube_chapters([[5, "A"], [60, "B"], [100, "C"]], 150)
    assert early_first["chapters"][0] == [0.0, "A"] and early_first["valid"] is True
    short = deliver.youtube_chapters([[20, "A"]], 120)
    assert short["valid"] is False and short["chapters"][0] == [0.0, "Intro"]
    assert deliver.youtube_chapters([[0, "A"], [4, "B"], [60, "C"], [90, "D"]], 150)["text"] == (
        "00:00 A\n01:00 C\n01:30 D"
    )
    assert deliver.youtube_chapters([[0, "A"], [3700, "B"], [4000, "C"]], 5000)["text"].startswith(
        "00:00 A\n1:01:40 B"
    )
    assert deliver.youtube_chapters([])["valid"] is False


def test_mp3_command_encode_and_package(monkeypatch, tmp_path):
    cmd = deliver.build_mp3_cmd("ff", "a.wav", "a.mp3", cover="c.png", title="T", artist="Me")
    assert ["-map", "0:a:0", "-map", "1:v:0"] == cmd[9:13]
    assert "attached_pic" in cmd and "title=T" in cmd and "artist=Me" in cmd
    assert "-map" not in deliver.build_mp3_cmd("ff", "a.wav", "a.mp3")

    ran: list[list[str]] = []

    def fake_run(argv, **_k):
        ran.append(argv)
        Path(argv[-1]).write_bytes(b"ID3" + b"0" * 20)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(deliver.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    wav = tmp_path / "master.wav"
    wav.write_bytes(b"RIFF")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    got = deliver.encode_mp3(wav, tmp_path / "o.mp3", cover=cover, title="X")
    assert got["cover"] is True and Path(got["mp3"]).is_file()
    with pytest.raises(ValueError, match="wav not found"):
        deliver.encode_mp3(tmp_path / "nope.wav", tmp_path / "n.mp3")
    with pytest.raises(ValueError, match="cover image not found"):
        deliver.encode_mp3(wav, tmp_path / "n.mp3", cover=tmp_path / "nope.png")

    score = {
        "sections": [[0, "A"], [30, "B"], [70, "C"]],
        "channels": [
            {"id": "grand", "events": [[0, 2, 60, 80], [100, 2, 64, 80]]},
            {"id": "halo_wav", "kind": "wav", "events": [[0, 5, 60, 80]]},
        ],
    }
    res = deliver.package(score, tmp_path / "ship", wav=wav, cover=cover, title="Suite")
    assert set(res["midi"]) == {"grand", "FULL_SUITE"} and res["chapters_valid"] is True
    assert (tmp_path / "ship" / "chapters.txt").read_text(encoding="utf-8").startswith("00:00 A")
    assert Path(res["mp3"]["mp3"]).name == "master.mp3" and len(ran) == 2


def test_cli_deliver(tmp_path, capsys):
    score = tmp_path / "s.json"
    score.write_text(
        json.dumps(
            {
                "sections": [[0, "A"], [20, "B"], [40, "C"]],
                "channels": [{"id": "g", "events": [[0, 1, 60, 80], [50, 1, 62, 80]]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["deliver", str(score), str(tmp_path / "out"), "--json"])
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["chapters_valid"] is True
    with pytest.raises(SystemExit) as bad:
        cli.main(["deliver", str(tmp_path / "missing.json"), str(tmp_path / "o2")])
    assert bad.value.code == 1


# ---- 8. MCP: fl_produce action=score


@pytest.mark.anyio
async def test_fl_produce_score_action(fl, monkeypatch, tmp_path):
    from fl_mcp import server

    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    tpl = _make_sf_template(tmp_path / "tpl.flp")
    score_path = tmp_path / "score.json"
    score_path.write_text(
        json.dumps(
            {
                "tempo": 110.0,
                "sections": [[0.0, "Main"]],
                "channels": [
                    {
                        "name": "Flute",
                        "sf2": r"C:\sf\f.sf2",
                        "insert": 1,
                        "mode": "mono_wind",
                        "events": [[0.0, 1.5, 72, 80]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = await _call_tool(
        "fl_produce", {"action": "score", "template": str(tpl), "plan": str(score_path)}
    )
    assert Path(out["file"]).is_file() and out["clips"] == 1
    assert out["articulation"] == {"Flute": "mono_wind"}

    err = await _call_tool("fl_produce", {"action": "score"}, expect_error=True)
    assert "score needs template" in err
    err = await _call_tool(
        "fl_produce",
        {"action": "score", "template": str(tpl), "plan": str(tmp_path / "none.json")},
        expect_error=True,
    )
    assert "cannot read score json" in err
