"""Regression & capability tests covering all D:\\FL findings and independent code-audit fixes."""

import json
import socket
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from starlette.testclient import TestClient

from fl_mcp import (
    app,
    audio,
    bridge,
    capture,
    chain,
    cli,
    clients,
    flp,
    mastering,
    mixing,
    plugins_kb,
    render,
    server,
    service_daemon,
    units,
)
from fl_mcp.flp import (
    CHAN_LEVELS,
    CHAN_ROUTE,
    CHAN_ROUTE_16,
    INIT_EVENTS,
    INSERT_NAME,
    NEW_ARR,
    NEW_PAT,
    PLUGIN_NAME,
    PLUGIN_PARAMS,
    REC_TEMPO,
    TIME_MARKER_NAME,
    TIME_MARKER_POS,
    Event,
    Flp,
    _encode_varint,
    _utf16,
    note_record,
    patch_soundfont_blob,
    read_soundfont_blob_path,
)
from tests.flpgen import template


async def _call_tool(tool: str, args: dict | None = None, *, expect_error: bool = False):
    async with Client(server.mcp) as client:
        res = await client.call_tool(tool, args or {})
    assert res.is_error is expect_error, res.content[0].text
    text = res.content[0].text
    return text if expect_error else json.loads(text)


# ---- 1. Units, params, 25-slot mixer, enum probing


def test_units_parse_settings_routes_out_of_unit_numbers_to_real_solver():
    parsed = units.parse_settings("Gain=0.6, Room size=80, Pitch=-12, Decay=~0.5, Mix=50%")
    assert parsed == [
        {"p": "Gain", "v": 0.6},
        {"p": "Room size", "r": "80"},
        {"p": "Pitch", "r": "-12"},
        {"p": "Decay", "r": "0.5"},
        {"p": "Mix", "r": "50%"},
    ]
    assert units.parse_value("-∞ dB") == (-120.0, "db")


@pytest.mark.anyio
async def test_fl_param_25_slots_and_enum_view(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    fl.fake.tracks[2]["slots"] = {15: "Fruity Reeverb 2"}
    fl.fake.params[("m", 2, 15)] = [
        ["Room size", 0.5, "%"],
        ["Mode", 0.0, "enum"],
    ]
    # Simulate FL Studio 26.2+ so _slot_max() returns 25
    monkeypatch.setattr(fl.script.ui, "getVersion", lambda: "26.2.0.5000")
    listed = await _call_tool("fl_param", {"target": "mixer", "index": 2, "slot": 15})
    assert listed["plugin"] == "Fruity Reeverb 2"
    enum_out = await _call_tool(
        "fl_param",
        {"target": "mixer", "index": 2, "slot": 15, "view": "enum", "param": "0", "limit": 5},
    )
    assert len(enum_out["options"]) >= 2
    assert enum_out["param"][2] == pytest.approx(0.5, abs=0.01)


def test_plugins_kb_free_slots_scales_to_25():
    slots = [(i, f"Fruity Parametric EQ {i}") for i in range(12)]
    rep = plugins_kb.analyze_chain("master", slots)
    assert rep["free"] == 13


# ---- 2. Server: savenew, _note velocity=1.0 & extra dict, READONLY guards


@pytest.mark.anyio
async def test_transport_savenew_and_note_extras_and_velocity_1(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    out = await _call_tool("fl_transport", {"action": "savenew"})
    assert "bpm" in out

    assert server._note([60, 0, 1, 1.0]) == [60, 0.0, 1.0, 127]
    assert server._note([60, 0, 1, 1]) == [60, 0.0, 1.0, 1]
    assert server._note([60, 0, 1, 90, {"slide": True, "color": 3}]) == [
        60,
        0.0,
        1.0,
        90,
        {"slide": True, "color": 3},
    ]
    with pytest.raises(Exception, match="unknown note extra"):
        server._note([60, 0, 1, 90, {"bogus": 1}])

    res = await _call_tool(
        "fl_notes",
        {"notes": [[60, 0, 1, 1.0, {"slide": True, "color": 2}]], "replace": True},
    )
    assert res["queued"] == 1
    inbox = json.loads((tmp_path / "inbox.json").read_text(encoding="utf-8"))
    assert inbox["notes"] == [[60, 0.0, 1.0, 127, {"slide": True, "color": 2}]]


@pytest.mark.anyio
async def test_readonly_blocks_all_mutating_paths(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setenv("FL_MCP_READONLY", "1")
    for tool, args in (
        ("fl_ui", {"action": "load", "query": "Fruity Limiter", "index": 0}),
        ("fl_mix", {"action": "balance", "targets": "1=-8"}),
        ("fl_audio", {"action": "render", "fmt": "wav"}),
    ):
        err = await _call_tool(tool, args, expect_error=True)
        assert "read-only mode active" in err


# ---- 3. Offline .flp binary upgrades & bug fixes


def test_flp_soundfont_dynamic_scan_and_release_override():
    sf_path = r"C:\Sounds\Salamander.sf2"
    enc = sf_path.encode("utf-8")
    # Shifted header (68 bytes instead of 56)
    blob = bytes(68) + _encode_varint(len(enc)) + enc + b"\x01\x02\x03\x04"
    assert read_soundfont_blob_path(blob) == sf_path
    new_path = r"D:\FL\libs\Grand.sf2"
    patched = patch_soundfont_blob(blob, new_path, release=1234)
    assert read_soundfont_blob_path(patched) == new_path
    assert struct.unpack_from("<i", patched, 36)[0] == 1234


def test_flp_init_events_reeverb2_markers_time_sig_donor_and_tempo_map():
    base = Flp.parse(template())
    # Insert non-zero-flag 'end' marker inside arrangement 0 (before the 2nd NEW_ARR)
    arr_idxs = [i for i, e in enumerate(base.events) if e.id == NEW_ARR]
    ins_mark = arr_idxs[1] if len(arr_idxs) > 1 else len(base.events)
    base.events[ins_mark:ins_mark] = [
        Event(TIME_MARKER_POS, struct.pack("<I", (4 << 24) | 384)),
        Event(TIME_MARKER_NAME, _utf16("end")),
    ]
    base.events.append(Event(INSERT_NAME, _utf16("Master")))
    base.events.append(Event(INSERT_NAME, _utf16("Insert 1")))
    base.events.append(Event(PLUGIN_NAME, _utf16("Fruity Reeverb 2")))
    base.events.append(Event(PLUGIN_PARAMS, struct.pack("<16i", *([0] * 16)) + b"\x00\x00"))

    assert base.rename_insert(1, "Piano") is True
    assert base.set_mixer_fader(1, 0.0) == 1
    assert base.set_mixer_pan(1, -0.25) == 1
    assert base.set_mixer_stereo(1, 0.5) == 1
    assert base.set_mixer_send(1, 9, 0.75) == 1
    vals = base.patch_fruity_reeverb2(
        decay_s=2.4, wet=115, lowcut_hz=180, highcut_hz=8000, size=0.8
    )
    assert vals is not None and vals[6] == 24 and vals[8] == 115 and vals[3] == 18 and vals[2] == 80

    # End marker update preserves upper flag byte 0x04
    upd = base.set_end_marker(768)
    assert upd == {"action": "updated", "position": 768}
    end_ev = next(
        base.events[m["idx_pos"]]
        for m in base.markers()
        if m["name"].strip().lower() == "end"
    )
    assert struct.unpack("<I", end_ev.data)[0] == ((4 << 24) | 768)

    ts_res = base.set_time_signature(6, 8, ticks=384)
    assert ts_res["mode"] == 8 and ts_res["ts"] == [6, 8]

    # Donor sampler cloning into sampler-less project
    donor = Flp.parse(template(sampler=True))
    iid = base.add_sampler_from_donor(donor, "Perc", r"C:\wav\perc.wav", insert=3)
    assert any(c.iid == iid and c.name == "Perc" and c.kind == "sampler" for c in base.channels())

    # Tempo map
    tm = base.set_tempo_map([(0, 108.0), (384, 124.5)])
    assert tm["base_bpm"] == 108.0 and tm["points"] == 2
    assert base.tempo == 108.0
    auto_ev = next(e for e in base.events if e.id == flp.AUTOMATION_DATA)
    t0, pid0, bpm0 = struct.unpack_from("<IIi", auto_ev.data, 0)
    t1, pid1, bpm1 = struct.unpack_from("<IIi", auto_ev.data, 12)
    assert (t0, pid0, bpm0) == (0, REC_TEMPO, 108000)
    assert (t1, pid1, bpm1) == (384, REC_TEMPO, 124500)

    # Round-trip structural integrity
    reloaded = Flp.parse(base.dumps())
    init_ev = reloaded.first(INIT_EVENTS)
    assert init_ev is not None and len(init_ev.data) == 4 * 12


def test_flp_clone_route_cleanup_and_empty_pattern_put():
    p = Flp.parse(template(version="25.1.0.1000", sampler=True))
    smp_iid = next(c.iid for c in p.channels() if c.kind == "sampler")
    # Shorten CHAN_LEVELS to 4 bytes to verify guard does not crash
    s, e = p._channel_spans()[smp_iid]
    for ev in p.events[s:e]:
        if ev.id == CHAN_LEVELS:
            ev.data, ev.raw = struct.pack("<I", 6400), None
    new_iid = p._clone_channel_block(smp_iid, "HighInsert", insert=300, volume=0.7, pan=0.0)
    ns, ne = p._channel_spans()[new_iid]
    ids = {ev.id for ev in p.events[ns:ne]}
    assert CHAN_ROUTE_16 in ids and CHAN_ROUTE not in ids

    # Existing empty pattern (meta only, no NOTES/PAT_NAME)
    p.events.insert(p._first_arrangement(), Event(NEW_PAT, struct.pack("<H", 7)))
    p.put_pattern(7, [note_record(0, 96, 60, 0, 100)], "Filled")
    pats = p.patterns()
    assert pats[7]["name"] == "Filled" and pats[7]["notes"] == 1


# ---- 4. Render early exit, split stems, out_dir/keep, PDC offset, cleanup


def test_render_early_exit_code0_and_stems_and_cleanup(tmp_path):
    flp_file = tmp_path / "track.flp"
    flp_file.write_bytes(b"FLhd")
    fake_exe = tmp_path / "FL64.exe"
    fake_exe.write_bytes(b"MZ")

    class FastExit:
        def poll(self):
            return 0

    now = [1000.0]

    def tick(_s):
        now[0] += 1.0

    with pytest.raises(ValueError, match="exited \\(code 0\\) without writing audio"):
        render.render(
            flp_file,
            "wav",
            timeout=900.0,
            exe=fake_exe,
            popen=lambda _cmd: FastExit(),
            sleep=tick,
            clock=lambda: now[0],
            wall=lambda: 1000.0,
            workdir=tmp_path / "work",
            keep=False,
        )
    # Must exit in ~2s, NOT hang for 900s!
    assert now[0] - 1000.0 < 10.0

    # Split mixer stems + out_dir
    out_dir = tmp_path / "stems_out"

    class StemWriter:
        def __init__(self, _cmd):
            (out_dir / "track_Master.wav").write_bytes(b"x" * 128)
            (out_dir / "track_Insert 1.wav").write_bytes(b"y" * 128)

        def poll(self):
            return 0

    res = render.render(
        flp_file,
        "wav",
        timeout=30.0,
        exe=fake_exe,
        popen=StemWriter,
        sleep=tick,
        clock=lambda: now[0],
        wall=lambda: now[0],
        workdir=tmp_path / "work2",
        out_dir=out_dir,
        keep=False,
        split_mixer=True,
    )
    assert res["audio"].endswith("track_Master.wav")
    assert len(res["stems"]) == 2


# ---- 5. Service daemon /api/vault, app free port, clients --scope user, doctor version, bridge


def test_service_daemon_exposes_api_vault():
    asgi = service_daemon.create_asgi_app(poll=False)
    with TestClient(asgi) as tc:
        r = tc.get("/api/vault")
        assert r.status_code == 200
        assert "summary" in r.json()


def test_app_find_free_port_falls_back_when_preferred_occupied():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        busy_port = occupied.getsockname()[1]
        picked = app._find_free_port("127.0.0.1", busy_port)
        assert picked != busy_port and picked > 0


def test_clients_claude_code_uses_user_scope_and_detects_project_scope(monkeypatch, tmp_path):
    dry = clients.install_client("claude-code", source="local", dry_run=True)
    assert "--scope" in dry["command"] and "user" in dry["command"]
    _, text = clients.snippet("claude-code", source="local")
    assert "--scope user" in text
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude.json").write_text(
        json.dumps({"projects": {"D:/proj": {"mcpServers": {"fl-studio": {"command": "fl-mcp"}}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    statuses = {s["id"]: s for s in clients.client_statuses()}
    assert statuses["claude-code"]["configured"] is True


def test_cli_doctor_fails_on_outdated_script(monkeypatch, capsys):
    fake_mido = SimpleNamespace(
        get_output_names=lambda: ["FL MCP"], get_input_names=lambda: ["FL MCP"]
    )
    monkeypatch.setitem(__import__("sys").modules, "mido", fake_mido)
    monkeypatch.setattr(
        bridge.Bridge,
        "call",
        lambda self, req, timeout=None: {"ver": 1, "fl": "25", "api": 35},
    )
    rc = cli._doctor()
    out = capsys.readouterr().out
    assert rc == 1 and "OUTDATED script v1" in out


def test_bridge_close_clears_info():
    b = bridge.Bridge()
    b.info = {"proto": 1, "ver": 7}
    b.close()
    assert b.info is None


# ---- 6. Mastering, Chain acoustic mode, Mixing bus/fix, Audio QA integrity


def test_mastering_cumulative_ceiling_and_no_false_saturation():
    set_calls = []

    def fake_call(req):
        if req["o"] == "list":
            return {
                "rows": [[0, "m", 0, 0, "Fruity Limiter", "Fruity Limiter", 2, 0]],
                "next": None,
            }
        if req["o"] == "param" and req["a"] == "list":
            return {
                "count": 2,
                "rows": [
                    [0, "Gain", 0.5, "0.0 dB"],
                    [1, "Ceiling", 0.8, "-1.0 dB"],
                ],
            }
        if req["o"] == "param" and req["a"] == "set":
            set_calls.append((req["p"], req["r"]))
            return {"param": [req["p"], "Gain" if req["p"] == 0 else "Ceiling", 0.5, req["r"], 1]}
        raise AssertionError(req)

    # Pass 0: -16 LUFS, TP -0.4 (over -1.0 by 0.6 -> ceiling moves to -1.6)
    # Pass 1: -15.8 LUFS (ceiling drop offset gain), TP -0.8 (still +0.2 over -> -1.8, not -1.2)
    # Pass 2: -14.0 LUFS, TP -1.0, crest 5.0 (triggers low crest note)
    measures = iter(
        [
            {"lufs_i": -16.0, "true_peak_db": -0.4, "crest_db": 9.0},
            {"lufs_i": -15.8, "true_peak_db": -0.8, "crest_db": 8.0},
            {"lufs_i": -14.0, "true_peak_db": -1.0, "crest_db": 5.0},
        ]
    )
    res = mastering.run(fake_call, lambda: next(measures), target=-14.0, ceiling=-1.0, passes=3)
    ceil_cmds = [r for p, r in set_calls if p == 1]
    assert ceil_cmds == ["-1.0 dB", "-1.6 dB", "-1.8 dB"]
    assert res["reached"] is True
    assert any("crest factor is low" in n for n in res["notes"])
    assert not any("saturating" in n for n in res["notes"])


def test_chain_doctor_acoustic_mode_and_mixing_bus_after_routing():
    calls = []

    def fake_call(req):
        calls.append(req)
        if req["o"] == "list" and req["k"] == "mixer":
            return {"rows": [[0, "Master", 0.8, 0.0], [1, "Grand Piano", 0.8, 0.0]], "next": None}
        if req["o"] == "list" and req["k"] == "channels":
            return {
                "rows": [[0, "Salamander Grand Piano", "plugin", 0, 0.8, 0.0, 0, 1]],
                "next": None,
            }
        if req["o"] == "list" and req["k"] == "plugins":
            return {
                "rows": [[0, "m", 0, 0, "Fruity Limiter", "Fruity Limiter", 2, 0]],
                "next": None,
            }
        if req["o"] == "status":
            return {"play": True}
        if req["o"] == "peaks":
            return {"p": [0.3, 0.25]}
        if req["o"] == "batch":
            return {"bad": 0, "ok": len(req["s"])}
        if req["o"] == "call":
            return {"r": [None] * len(req["c"])}
        return {}

    doc = chain.doctor(fake_call, seconds=0.05)
    assert doc["acoustic"] is True
    assert not any("delay" in f["what"] or "panned centre" in f["what"] for f in doc["findings"])

    calls.clear()
    mixing.setup_bus(fake_call, [1, 2], 10, name="Room", send_level=0.4)
    batch_ops = [op for req in calls if req["o"] == "batch" for op in req["s"]]
    raw_calls = [c for op in batch_ops if op.get("o") == "call" for c in op["c"]]
    assert ["mixer.afterRoutingChanged"] in raw_calls


def test_audio_qa_integrity_detects_abrupt_cut_dropout_and_lead_silence(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    fs = 48000
    # 0.5s leading silence + 0.5s loud tone + instant hard cut + 0.5s zero dropout + 0.4s tone
    t1 = np.arange(int(0.5 * fs)) / fs
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * t1)
    sig = np.concatenate(
        [np.zeros(int(0.5 * fs)), tone, np.zeros(int(0.5 * fs)), tone[: int(0.4 * fs)]]
    )
    wav = tmp_path / "qa.wav"
    sf.write(wav, np.stack([sig, sig], axis=1), fs, subtype="FLOAT")

    qa = audio.qa_integrity(wav)
    assert qa["ok"] is False
    assert qa["start_offset_s"] == pytest.approx(0.5, abs=0.03)
    assert len(qa["abrupt_cuts"]) >= 1
    assert len(qa["dropouts"]) >= 1

    rep = audio.report(wav)
    assert rep["integrity"]["abrupt_cuts"] >= 1
    assert rep["integrity"]["dropouts"] >= 1
    assert any("abrupt note-off cut" in a for a in rep["advice"])


def test_capture_module_surface_and_gfxcapture_filtergraph():
    assert callable(capture.capture_fl_video)
    assert callable(capture.set_fl_app_mute)
    assert callable(capture.push_fl_behind)
    assert callable(capture.toggle_play_space)
    assert callable(capture.current_desktop_name)
    cmd = capture.build_gfxcapture_cmd("ffmpeg", "out.mp4", 12.5, fps=30, crf=14)
    assert "-filter_complex" in cmd
    vf = cmd[cmd.index("-filter_complex") + 1]
    assert "gfxcapture=window_title=FL Studio" in vf and "hwdownload,format=bgra" in vf


def test_flp_reeverb2_send_bus_wet_guard():
    base = Flp.parse(template())
    base.events.append(Event(PLUGIN_NAME, _utf16("Fruity Reeverb 2")))
    base.events.append(Event(PLUGIN_PARAMS, struct.pack("<16i", *([0] * 16)) + b"\x00\x00"))
    vals = base.patch_fruity_reeverb2(decay_s=3.5, wet=40, send_bus=True)
    assert vals is not None and vals[8] == 100


def test_resolve_ensemble_harmony_cleans_clashes_mud_shorts_and_dead_air():
    from fl_mcp.music.theory import resolve_ensemble_harmony

    # D major: F#4(66)->G4(67) b2 bleed; E2(40)->F#2(42) mud; 0.15s double-tap; dead-air 3.5..5.0s
    raw = {
        "flute": [
            [0.0, 0.15, 74, 80],  # D5 choppy
            [0.20, 0.25, 74, 82],  # D5 rapid re-strike -> coalesces
            [1.0, 0.6, 69, 84],  # A4 before breath -> blooms
            [5.0, 0.8, 74, 85],  # D5 final -> blooms >=2.0s
        ],
        "guitar": [
            [0.0, 3.0, 40, 75],  # E2 low mud vs F#2 at 0.8s
            [0.8, 2.0, 42, 75],  # F#2
            [0.5, 2.5, 66, 75],  # F#4 b2 bleed vs G4 at 1.2s
            [1.2, 1.5, 67, 75],  # G4
            [2.0, 1.2, 62, 75],  # D4
            [5.0, 1.0, 62, 75],  # D4
        ],
    }
    solved = resolve_ensemble_harmony(raw, total_sec=7.0)
    # 1. Zero short choppy notes (<0.35s)
    for ch, evs in solved.items():
        for e in evs:
            assert e[1] >= 0.35, f"{ch} short note {e}"
    # 2. Zero b2/m9 overlaps >= 0.22s and zero <54 mud overlaps >= 0.05s
    all_n = [(ch, e) for ch, evs in solved.items() for e in evs]
    for i in range(len(all_n)):
        ch1, e1 = all_n[i]
        for j in range(i + 1, len(all_n)):
            ch2, e2 = all_n[j]
            ov = min(e1[0] + e1[1], e2[0] + e2[1]) - max(e1[0], e2[0])
            diff = abs(e1[2] - e2[2])
            if diff % 12 == 1 and diff <= 13:
                assert ov < 0.22, f"b2/m9 clash {ch1}:{e1} vs {ch2}:{e2} ov={ov}"
            if ch1 == ch2 and e1[2] < 54 and e2[2] < 54 and diff in (1, 2, 3, 4, 6):
                assert ov < 0.05, f"low mud {ch1}:{e1} vs {e2} ov={ov}"
    # 3. Final notes bloom >= 2.0s
    assert solved["flute"][-1][1] >= 2.0
    assert solved["guitar"][-1][1] >= 2.0
