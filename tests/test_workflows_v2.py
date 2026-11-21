"""Tests for v2 high-leverage workflows: SF2 patcher, registry split-mixer, calibrator,
one-call score->FLP, 10-pass harmony audit, section arc/stem balance, plugin enums, dismiss & CLI.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import types
import wave
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from fl_mcp import (
    audio,
    calibrate,
    capture,
    cli,
    dashboard,
    desk,
    flp,
    plugins_kb,
    render,
    server,
    sf2,
)
from fl_mcp.bridge import FLError
from fl_mcp.flp import (
    AUTOMATION_DATA,
    CHAN_LEVELS,
    CHAN_NAME,
    INIT_EVENTS,
    INSERT_NAME,
    NEW_CHAN,
    PLUGIN_NAME,
    PLUGIN_PARAMS,
    Event,
    Flp,
    _encode_varint,
    _utf16,
)
from fl_mcp.music import theory
from tests.flpgen import template


def _make_minimal_sf2(path: Path) -> Path:
    """Build a valid minimal SoundFont 2.01 file with 2 zones (one short gen38, one missing)."""
    def _chunk(cid: bytes, payload: bytes) -> bytes:
        pad = b"\x00" if (len(payload) & 1) else b""
        return cid + struct.pack("<I", len(payload)) + payload + pad

    def _list(tag: bytes, body: bytes) -> bytes:
        return _chunk(b"LIST", tag + body)

    # phdr: 1 preset + EOP sentinel (38 bytes each)
    phdr = (
        b"Grand Piano".ljust(20, b"\x00")
        + struct.pack("<HHHIII", 0, 0, 0, 0, 0, 0)
        + b"EOP".ljust(20, b"\x00")
        + struct.pack("<HHHIII", 0, 0, 1, 0, 0, 0)
    )
    # inst: 1 instrument + EOI sentinel (22 bytes each)
    inst = (
        b"PianoInst".ljust(20, b"\x00")
        + struct.pack("<H", 0)
        + b"EOI".ljust(20, b"\x00")
        + struct.pack("<H", 2)
    )
    # Zone 0: gen 38 = -4000 (~0.099s, slam cut!), gen 53 (sampleID) = 0
    # Zone 1: NO gen 38, only gen 53 (sampleID) = 1
    # Terminal sentinel gen: (0, 0)
    gens = [(38, -4000), (53, 0), (53, 1), (0, 0)]
    igen = b"".join(struct.pack("<Hh", o, v) for o, v in gens)
    bags = [(0, 0), (2, 0), (3, 0)]
    ibag = b"".join(struct.pack("<HH", g, m) for g, m in bags)

    info_list = _list(b"INFO", _chunk(b"INAM", b"TestSF2\x00"))
    sdta_list = _list(b"sdta", _chunk(b"smpl", b"\x00" * 32))
    pdta_body = (
        _chunk(b"phdr", phdr)
        + _chunk(b"inst", inst)
        + _chunk(b"ibag", ibag)
        + _chunk(b"igen", igen)
    )
    pdta_list = _list(b"pdta", pdta_body)
    body = b"sfbk" + info_list + sdta_list + pdta_list
    raw = b"RIFF" + struct.pack("<I", len(body)) + body
    path.write_bytes(raw)
    return path


def _make_sf_template(path: Path) -> Path:
    """Create a valid template `.flp` containing a SoundFont Player channel + Reeverb 2."""
    p = Flp.parse(template(sampler=False, channels=()))
    sf_path = b"C:\\sf\\seed.sf2"
    blob = bytes(56) + _encode_varint(len(sf_path)) + sf_path + b"\x00\x00\x00\x00"
    anchor = p._first_arrangement()
    ch_block = [
        Event(NEW_CHAN, struct.pack("<H", 0)),
        Event(CHAN_NAME, _utf16("SeedSF")),
        Event(CHAN_LEVELS, struct.pack("<II", 6400, 10000)),
        Event(PLUGIN_NAME, _utf16("Fruity Soundfont Player")),
        Event(PLUGIN_PARAMS, blob),
    ]
    p.events[anchor:anchor] = ch_block
    p.header = p.header[:10] + struct.pack("<H", 1) + p.header[12:]
    p.events.append(Event(INSERT_NAME, _utf16("Master")))
    for i in range(1, 10):
        p.events.append(Event(INSERT_NAME, _utf16(f"Insert {i}")))
    p.events.append(Event(PLUGIN_NAME, _utf16("Fruity Reeverb 2")))
    p.events.append(Event(PLUGIN_PARAMS, struct.pack("<16i", *([0] * 16)) + b"\x00\x00"))
    path.write_bytes(p.dumps())
    return path


def _write_stereo_wav(path: Path, samples: list[float], fs: int = 44100) -> Path:
    pcm = bytearray()
    for s in samples:
        v = max(-32767, min(32767, int(round(s * 32767))))
        pcm.extend(struct.pack("<hh", v, v))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(bytes(pcm))
    return path


async def _call_tool(tool: str, args: dict[str, Any] | None = None, *, expect_error: bool = False):
    async with Client(server.mcp) as client:
        res = await client.call_tool(tool, args or {})
    assert res.is_error is expect_error, res.content[0].text
    text = res.content[0].text
    return text if expect_error else json.loads(text)


# ---- 1. SoundFont 2.01 Inspector & Release Patcher (sf2.py)


def test_sf2_inspect_and_patch_release(tmp_path: Path):
    src = _make_minimal_sf2(tmp_path / "piano.sf2")
    info = sf2.inspect_sf2(src)
    assert info["presets"] == ["Grand Piano"]
    assert info["instruments"] == ["PianoInst"]
    assert info["zones"] == 2
    assert info["zones_with_release"] == 1
    assert info["slam_cut_risk"] is True
    assert info["release_s"]["min"] == pytest.approx(0.0992, abs=0.01)

    out = tmp_path / "piano_patched.sf2"
    patched = sf2.patch_sf2_release(src, "piano", out_path=out)
    assert patched["updated_zones"] == 1
    assert patched["inserted_zones"] == 1
    assert patched["release_s"] == 2.6

    # Verify RIFF size and sampleID ordering
    raw = out.read_bytes()
    assert struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8
    after = sf2.inspect_sf2(out)
    assert after["zones"] == 2
    assert after["zones_with_release"] == 2
    assert after["slam_cut_risk"] is False
    assert after["release_s"]["min"] == pytest.approx(2.6, abs=0.01)

    # Confirm gen 53 (sampleID) stayed last in Zone 1
    _, _, subs = sf2._locate_pdta(raw)
    igen_pos, igen_sz = subs[b"igen"]
    ibag_pos, ibag_sz = subs[b"ibag"]
    gens = [struct.unpack_from("<Hh", raw, igen_pos + k * 4) for k in range(igen_sz // 4)]
    bags = [struct.unpack_from("<HH", raw, ibag_pos + k * 4) for k in range(ibag_sz // 4)]
    z1_gens = gens[bags[1][0] : bags[2][0]]
    assert [g[0] for g in z1_gens] == [38, 53]

    # In-place patching creates .bak
    in_place = sf2.patch_sf2_release(src, 3.0, backup=True)
    assert in_place["backup"] == str(src) + ".bak"
    assert Path(in_place["backup"]).is_file()

    with pytest.raises(sf2.Sf2Error):
        sf2.patch_sf2_release(src, "nonexistent_preset")
    with pytest.raises(sf2.Sf2Error):
        sf2.patch_sf2_release(src, 0.001)


# ---- 2. Windows Registry Headless Render Prefs (render._fl_render_prefs)


def test_render_fl_prefs_saves_and_restores_registry(monkeypatch):
    store: dict[str, tuple[Any, int]] = {
        "Split mixer tracks": ("0", 1),  # REG_SZ
        "Tail": (0, 4),  # REG_DWORD
    }

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=0x80000001,
        KEY_READ=0x20019,
        KEY_WRITE=0x20006,
        REG_SZ=1,
        REG_DWORD=4,
        CreateKeyEx=lambda *_a: FakeKey(),
        OpenKey=lambda *_a: FakeKey(),
        QueryValueEx=lambda _k, name: (
            store[name] if name in store else (_ for _ in ()).throw(FileNotFoundError(name))
        ),
        SetValueEx=lambda _k, name, _res, typ, val: store.__setitem__(name, (val, typ)),
        DeleteValue=lambda _k, name: store.pop(name, None),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(
        render.paths,
        "fl_registry_version_keys",
        lambda: [r"Software\Image-Line\FL Studio 25"],
    )

    with render._fl_render_prefs(split_mixer=True, tail="cut", trim_pdc=True) as applied:
        assert applied is True
        assert store["Split mixer tracks"] == ("1", 1)  # preserved REG_SZ
        assert store["Tail"] == (2, 4)  # preserved REG_DWORD
        assert store["Trim PDC silence"] == ("1", 1)  # inserted missing key

    # Exact prior state restored, newly inserted key deleted
    assert store == {"Split mixer tracks": ("0", 1), "Tail": (0, 4)}

    # Also restores on exception
    with pytest.raises(RuntimeError, match="boom"):
        with render._fl_render_prefs(split_mixer=True):
            assert store["Split mixer tracks"] == ("1", 1)
            raise RuntimeError("boom")
    assert store["Split mixer tracks"] == ("0", 1)

    with pytest.raises(ValueError, match="tail must be"):
        with render._fl_render_prefs(tail="invalid"):
            pass


# ---- 3. Single-Channel FL Calibrator (calibrate.py)


def test_calibrate_build_flp_and_analyze_wav(tmp_path: Path):
    tpl = _make_sf_template(tmp_path / "tpl.flp")
    out_flp = tmp_path / "cal.flp"
    built = calibrate.build_calibration_flp(
        tpl,
        [
            {"name": "Flute", "sf2": r"C:\sf\flute.sf2", "insert": 1, "pitch": 72},
            {"name": "Cello", "sf2": r"C:\sf\cello.sf2", "insert": 2, "pitch": 48},
        ],
        out_flp,
        note_s=1.0,
        tail_s=2.0,
    )
    assert Path(built["flp"]).is_file()
    assert len(built["channels"]) == 2
    parsed = Flp.open(out_flp)
    assert [c.name for c in parsed.channels()] == ["Flute", "Cello"]

    # Synthesize WAV matching built timeline: slot 0 smooth tail (OK), slot 1 abrupt cut (SLAM_CUT)
    fs = 16000
    total_n = int(round((built["total_s"] + 1.0) * fs))
    buf = [0.0] * total_n
    for idx, slot in enumerate(built["channels"]):
        s0 = int(slot["start_s"] * fs)
        dur_n = int(slot["note_s"] * fs)
        for k in range(dur_n):
            amp = 0.25 * min(1.0, k / (0.02 * fs))
            buf[s0 + k] += amp * math.sin(2.0 * math.pi * 440.0 * k / fs)
        if idx == 0:
            # Smooth exponential tail (-12 dB/s) -> no SLAM_CUT, no DRY_TAIL
            for k in range(int(1.8 * fs)):
                amp = 0.25 * (10.0 ** (-0.6 * (k / fs)))
                if s0 + dur_n + k < total_n:
                    buf[s0 + dur_n + k] += amp * math.sin(2.0 * math.pi * 440.0 * k / fs)
        # idx == 1 drops instantly to 0.0 -> SLAM_CUT + DRY_TAIL

    wav_path = _write_stereo_wav(tmp_path / "cal.wav", buf, fs=fs)
    rep = calibrate.analyze_calibration_wav(wav_path, built["channels"], target_eff_db=-38.0)
    assert rep["all_ok"] is False
    flute_r, cello_r = rep["channels"]
    assert flute_r["status"] == "OK"
    assert flute_r["atk_ms"] is not None and flute_r["atk_ms"] < 30.0
    assert flute_r["recommended_level_db"] == pytest.approx(
        -38.0 - flute_r["body_rms_db"], abs=0.2
    )
    assert "SLAM_CUT" in cello_r["status"]
    assert "DRY_TAIL" in cello_r["status"]


# ---- 4. 10-Pass Ensemble Harmony Solver & Audit (theory.py)


def test_ensemble_harmony_audit_and_10_pass_solver():
    ch_events = {
        "piano": [
            [0.0, 0.15, 48, 80],  # short + low mud with 50
            [0.0, 1.5, 50, 80],
            [4.5, 1.2, 60, 80],  # dead-air hole between 1.5 and 4.5
        ],
        "flute": [
            [0.0, 1.5, 64, 85],
            [0.4, 1.0, 65, 85],  # mono overlap + b2 clash with 64
        ],
    }
    before = theory.audit_ensemble_harmony(ch_events)
    assert before["ok"] is False
    assert before["shorts"] >= 1
    assert before["mono_overlaps"] >= 1
    assert before["low_mud"] >= 1
    assert before["b2_m9_clashes"] >= 1
    assert before["dead_air_holes"] >= 1

    resolved = theory.resolve_ensemble_harmony(ch_events)
    after = theory.audit_ensemble_harmony(resolved)
    assert after["shorts"] == 0
    assert after["mono_overlaps"] == 0
    assert after["low_mud"] == 0
    assert after["b2_m9_clashes"] == 0


# ---- 5. One-Call Score JSON -> Hybrid FLP + Flp.set_insert (flp.py)


def test_build_from_score_and_set_insert(tmp_path: Path):
    tpl = _make_sf_template(tmp_path / "tpl.flp")
    donor = tmp_path / "donor.flp"
    donor.write_bytes(template(sampler=True))
    out_flp = tmp_path / "suite.flp"

    score = {
        "tempo": 100.0,
        "time_unit": "seconds",
        "sections": [[0.0, "I. Sight"], [2.4, "II. Hearing"]],
        "time_signatures": [[0.0, 3, 4], [2.4, 4, 4]],
        "tempo_map": [[0.0, 100.0], [2.4, 112.0]],
        "channels": [
            {
                "id": "piano",
                "name": "Piano",
                "kind": "sf2",
                "sf2": r"C:\sf\piano.sf2",
                "insert": 1,
                "level_db": -6.0,
                "pan": -0.2,
                "sf2_release": 1400,
                "events": [[0.0, 1.8, 60, 75], [2.4, 2.0, 67, 82]],
            },
            {
                "id": "perc_wav",
                "name": "Bell",
                "kind": "wav",
                "wav": r"C:\wav\bell.wav",
                "insert": 2,
                "events": [[0.0, 1.0, 60, 90]],
            },
        ],
        "inserts": {
            "1": {
                "name": "Grand Piano",
                "gain_db": -2.0,
                "pan": -0.15,
                "stereo": 0.2,
                "sends": {9: 0.4},
            }
        },
        "hall": {"decay_s": 2.8, "lowcut_hz": 220, "highcut_hz": 7200, "wet": 40, "send_bus": True},
    }

    res = flp.build_from_score(tpl, score, out_flp, donor_flp=donor)
    assert Path(res["file"]).is_file()
    assert res["sections"] == 2
    assert res["clips"] == 2
    assert res["harmony_audit"] is not None

    reloaded = Flp.open(out_flp)
    assert reloaded.tempo == 100.0
    names = {c.name for c in reloaded.channels()}
    assert "Piano" in names and "Bell" in names
    assert reloaded.first(INIT_EVENTS) is not None
    assert reloaded.first(AUTOMATION_DATA) is not None
    # Verify send_bus=True clamped Reeverb 2 wet up to >= 100
    rv_idx = next(
        i
        for i, ev in enumerate(reloaded.events)
        if ev.id == PLUGIN_NAME and "reeverb 2" in ev.data.decode("utf-16le", "ignore").lower()
    )
    rv_params = next(
        ev.data for ev in reloaded.events[rv_idx : rv_idx + 5] if ev.id == PLUGIN_PARAMS
    )
    rv_vals = struct.unpack("<16i", rv_params[:64])
    assert rv_vals[8] >= 100


# ---- 6. Per-Section LUFS Arc & Stem Balance Table (audio.py + MCP fl_audio)


@pytest.mark.anyio
async def test_audio_section_arc_and_stem_balance(fl, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    fs = 44100
    n_half = 3 * fs
    quiet = [0.05 * math.sin(2.0 * math.pi * 440.0 * k / fs) for k in range(n_half)]
    loud = [0.35 * math.sin(2.0 * math.pi * 440.0 * k / fs) for k in range(n_half)]
    arc_wav = _write_stereo_wav(tmp_path / "arc.wav", quiet + loud, fs=fs)

    arc_direct = audio.section_arc(arc_wav, [(0.0, "Intro"), (3.0, "Drop")])
    assert len(arc_direct["sections"]) == 2
    assert arc_direct["dynamic_span_lu"] > 10.0

    arc_mcp = await _call_tool(
        "fl_audio", {"action": "arc", "path": str(arc_wav), "reference": "0:Intro,3:Drop"}
    )
    assert [s["name"] for s in arc_mcp["sections"]] == ["Intro", "Drop"]
    assert arc_mcp["dynamic_span_lu"] > 10.0

    # Split mixer stems folder
    stem_dir = tmp_path / "stems"
    stem_dir.mkdir()
    n_s = 2 * fs
    bass = [0.25 * math.sin(2.0 * math.pi * 80.0 * k / fs) for k in range(n_s)]
    air = [0.15 * math.sin(2.0 * math.pi * 8000.0 * k / fs) for k in range(n_s)]
    master = [b + a for b, a in zip(bass, air, strict=False)]
    _write_stereo_wav(stem_dir / "track_Master.wav", master, fs=fs)
    _write_stereo_wav(stem_dir / "track_Bass.wav", bass, fs=fs)
    _write_stereo_wav(stem_dir / "track_Air.wav", air, fs=fs)

    stems_mcp = await _call_tool("fl_audio", {"action": "stems", "path": str(stem_dir)})
    assert stems_mcp["count"] == 3
    assert stems_mcp["stems"][0]["is_master"] is True
    by_name = {s["name"]: s for s in stems_mcp["stems"]}
    assert by_name["track_Bass"]["low_pct"] > 70.0
    assert by_name["track_Air"]["high_pct"] > 70.0
    assert "vs_master_lu" in by_name["track_Bass"]


# ---- 7. Stock Plugin Enums, Acoustic Recipes, fl_param info, fl_desk dismiss, Space fallback


@pytest.mark.anyio
async def test_plugin_enums_acoustic_recipes_dismiss_and_space_fallback(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    eq_enums = plugins_kb.enums("Fruity Parametric EQ 2")
    assert eq_enums is not None and eq_enums["band_type"]["0.429"] == "High pass"
    lim_enums = plugins_kb.enums("Fruity Limiter")
    assert lim_enums is not None and "comp_only" in lim_enums
    assert plugins_kb.recipe("piano") is not None
    assert plugins_kb.recipe("strings") is not None
    assert plugins_kb.recipe("hall_send") is not None

    fl.fake.tracks[12]["slots"] = {0: "Fruity Limiter"}
    fl.fake.params[("m", 12, 0)] = [["Gain", 0.5, "db"]]
    info = await _call_tool(
        "fl_param", {"target": "mixer", "index": 12, "slot": 0, "view": "info"}
    )
    assert info["plugin"] == "Fruity Limiter"
    assert "enums" in info and "comp_only" in info["enums"]

    # fl_transport Win32 Space fallback on MIDI "No answer"
    monkeypatch.setattr(
        server.bridge, "call", lambda _req: (_ for _ in ()).throw(FLError("No answer from FL"))
    )
    monkeypatch.setattr(capture, "find_fl_window", lambda: (12345, "Song - FL Studio 2025"))
    monkeypatch.setattr(capture, "toggle_play_space", lambda hwnd: hwnd == 12345)
    monkeypatch.setattr(server.os, "name", "nt")
    tr_out = await _call_tool("fl_transport", {"action": "play"})
    assert tr_out == {
        "fallback": "win32_space",
        "action": "play",
        "hwnd": 12345,
        "title": "Song - FL Studio 2025",
    }

    # Desk.dismiss()
    posted: list[tuple[str, str]] = []

    class FakeBackend:
        name = "fake"

        def windows(self):
            return [
                desk.Win(1, "Song - FL Studio 2025", (0, 0, 1280, 720), "FL64.exe"),
                desk.Win(2, "Welcome to FL Studio", (100, 100, 600, 400), "FL64.exe"),
            ]

        def post_key(self, win: desk.Win, combo: desk.Combo):
            posted.append((win.title, combo.key))
            return True

        def foreground(self):
            return None

        def idle_seconds(self):
            return 10.0

    d = desk.Desk(FakeBackend(), sleep=lambda _s: None)
    assert d.dismiss() == {
        "dismissed": True,
        "window": "Welcome to FL Studio",
        "method": "post_escape",
    }
    assert posted == [("Welcome to FL Studio", "escape")]


# ---- 8. Dashboard script-outdated + cleanup_renders / launch_fl + CLI commands


def test_dashboard_and_cli_new_commands(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "fl_user"))

    # Create an outdated controller script on disk
    from fl_mcp import install

    dev_path, _ = install.targets()
    dev_path.parent.mkdir(parents=True, exist_ok=True)
    dev_path.write_text("# test\nVERSION = 1\n", encoding="utf-8")

    doc = dashboard.doctor_info()
    assert doc["fl_script_version"] == 1
    assert doc["script_outdated"] is True

    # Dashboard cleanup_renders + launch_fl actions
    old_render = tmp_path / "renders" / "1000"
    old_render.mkdir(parents=True)
    (old_render / "out.wav").write_bytes(b"0" * 512)
    clean_res = dashboard.dispatch_action(
        {"action": "cleanup_renders", "max_keep": 0, "max_age_hours": 0}
    )
    assert clean_res["ok"] is True and clean_res["removed"] == 1

    monkeypatch.setattr(
        render, "launch_fl", lambda p=None: {"launched": True, "pid": 999, "project": p}
    )
    launch_res = dashboard.dispatch_action({"action": "launch_fl"})
    assert launch_res["ok"] is True and launch_res["pid"] == 999

    # CLI: launch, renders, sf2, score, capture
    with pytest.raises(SystemExit) as e1:
        cli.main(["launch", "--json"])
    assert e1.value.code == 0
    assert json.loads(capsys.readouterr().out)["pid"] == 999

    with pytest.raises(SystemExit) as e2:
        cli.main(["renders", "--json"])
    assert e2.value.code == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0

    src_sf2 = _make_minimal_sf2(tmp_path / "cli_test.sf2")
    out_sf2 = tmp_path / "cli_patched.sf2"
    with pytest.raises(SystemExit) as e3:
        cli.main(["sf2", str(src_sf2), "--release", "bowed", "--out", str(out_sf2), "--json"])
    assert e3.value.code == 0
    assert json.loads(capsys.readouterr().out)["release_s"] == 2.2

    tpl = _make_sf_template(tmp_path / "cli_tpl.flp")
    score_file = tmp_path / "cli_score.json"
    score_file.write_text(
        json.dumps(
            {
                "tempo": 120.0,
                "channels": [
                    {
                        "name": "Flute",
                        "sf2": str(out_sf2),
                        "insert": 1,
                        "events": [[0.0, 1.0, 72, 80]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_score_flp = tmp_path / "cli_out.flp"
    with pytest.raises(SystemExit) as e4:
        cli.main(["score", str(score_file), str(tpl), str(out_score_flp), "--json"])
    assert e4.value.code == 0
    assert Path(json.loads(capsys.readouterr().out)["file"]).is_file()

    monkeypatch.setattr(
        capture,
        "capture_fl_video",
        lambda out, dur, **_kw: {"video": str(out), "duration_s": dur, "bytes": 1234},
    )
    with pytest.raises(SystemExit) as e5:
        cli.main(["capture", str(tmp_path / "rec.mp4"), "--seconds", "5", "--json"])
    assert e5.value.code == 0
    assert json.loads(capsys.readouterr().out)["bytes"] == 1234
