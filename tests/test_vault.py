"""Tests for the AI Models, Audio Packs, UI Typography/Icons, Frameworks & Rulebooks Vault."""

from __future__ import annotations

import hashlib
import json

import pytest
from mcp import Client

from fl_mcp import assets, audio, cli, dashboard, library, paths, server, vault

pytestmark = pytest.mark.anyio


def test_catalog_rules_and_ui_assets_are_bundled():
    cat = vault.load_catalog()
    assert {m["id"] for m in cat["models"]} == {"basic_pitch", "silero_vad"}
    assert {s["id"] for s in cat["soundfonts"]} == {"timgm6mb", "fluidr3_gm"}
    topics = vault.rule_topics()
    assert [t["id"] for t in topics] == [
        "production",
        "mixing",
        "mastering",
        "ui_design",
        "fl_safety",
        "fl_formulas",
    ]
    for t in topics:
        body = vault.read_rule(t["id"])
        assert len(body) > 400
    with pytest.raises(ValueError, match="unknown rulebook"):
        vault.read_rule("nonexistent")

    combined = vault.all_rules_markdown()
    assert vault.RULE_BEGIN in combined and vault.RULE_END in combined

    # Bundled self-hosted WOFF2 fonts & Lucide SVG icon sprite
    for fname in cat["ui_assets"]["fonts"]:
        loaded = assets.load_asset(fname)
        assert loaded is not None
        data, mime = loaded
        assert mime == "font/woff2"
        assert data[:4] == b"wOF2"
    sprite = assets.load_asset("lucide-sprite.svg")
    assert sprite is not None and sprite[1] == "image/svg+xml"
    assert b'id="lucide-database"' in sprite[0]
    knob = assets.load_asset("knob-ring.svg")
    assert knob is not None and knob[1] == "image/svg+xml"


def test_vault_status_download_and_library_autodiscovery(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "fl_home"))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "no_fl"))
    monkeypatch.setenv("FL_MCP_SAMPLES", "")

    st0 = vault.status()
    assert st0["summary"]["models_installed"] == 0
    assert st0["summary"]["soundfonts_count"] == 0
    assert st0["summary"]["ui_fonts_count"] == 7
    assert st0["summary"]["rules_count"] == 6
    assert st0["summary"]["frameworks_total"] >= 10

    # Prepare deterministic mock payloads for vault.download
    cat = vault.load_catalog()
    fake_blobs: dict[str, bytes] = {}
    for m in cat["models"]:
        blob = b"ONNX" + b"\x00" * 60_000
        m["sha256"] = hashlib.sha256(blob).hexdigest()
        for u in m["urls"]:
            fake_blobs[u] = blob
    for sf in cat["soundfonts"]:
        blob = b"RIFF" + b"\x00" * 1_050_000
        for u in sf["urls"]:
            fake_blobs[u] = blob

    # Minimal valid 16-bit 44.1kHz mono WAV header + 4410 frames
    import struct
    import wave

    wav_buf_path = tmp_path / "seed.wav"
    with wave.open(str(wav_buf_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(struct.pack("<" + "h" * 4410, *([1200] * 4410)))
    wav_bytes = wav_buf_path.read_bytes()

    monkeypatch.setattr(vault, "load_catalog", lambda: cat)

    def fake_fetcher(url: str) -> bytes:
        if url in fake_blobs:
            return fake_blobs[url]
        if "/contents/" in url or url.endswith("/contents"):
            return json.dumps(
                [{"name": "Kick_01.wav", "download_url": "https://example.test/Kick_01.wav"}]
            ).encode("utf-8")
        return wav_bytes

    res = vault.download("all", fetcher=fake_fetcher)
    assert res["ok"] is True
    st1 = vault.status()
    assert st1["summary"]["models_installed"] == 2
    assert st1["summary"]["soundfonts_count"] == 2
    assert st1["summary"]["samples_count"] >= 12
    assert st1["summary"]["irs_count"] >= 1
    assert st1["summary"]["midi_count"] == 12
    assert st1["summary"]["scores_count"] >= 6
    assert st1["summary"]["templates_count"] >= 1
    assert st1["summary"]["pyscripts_count"] >= 1
    assert st1["summary"]["ready"] is True

    # Verify library.sample_roots() and _scan_soundfonts_on_disk() automatically find vault assets
    roots = [r.resolve() for r in library.sample_roots()]
    assert (paths.assets_dir() / "samples").resolve() in roots
    assert (paths.assets_dir() / "irs").resolve() in roots
    sfs = library._scan_soundfonts_on_disk()
    assert {s["name"] for s in sfs} >= {"TimGM6mb", "FluidR3_GM"}


def test_install_rules_is_idempotent(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "AGENTS.md").write_text(
        "# Existing Project Notes\n\nKeep this header.\n", encoding="utf-8"
    )

    r1 = vault.install_rules(ws)
    assert r1["ok"] is True
    actions = {w["target"]: w["action"] for w in r1["written"]}
    assert actions == {"agents": "appended", "claude": "created", "cursor": "created"}
    assert "Keep this header." in (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert (ws / ".cursor" / "rules" / "fl-mcp.mdc").is_file()

    # Running again updates in place without duplicating blocks
    r2 = vault.install_rules(ws)
    actions2 = {w["target"]: w["action"] for w in r2["written"]}
    assert actions2 == {"agents": "updated", "claude": "updated", "cursor": "updated"}
    text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(vault.RULE_BEGIN) == 1


async def test_mcp_vault_and_rulebook_resources():
    async with Client(server.mcp) as c:
        res_list = (await c.list_resources()).resources
        uris = {str(r.uri) for r in res_list}
        assert "fl://info/vault" in uris
        for topic in ("production", "mixing", "mastering", "ui_design", "fl_safety", "fl_formulas"):
            assert f"fl://rules/{topic}" in uris
        v_raw = (await c.read_resource("fl://info/vault")).contents
        v_data = json.loads(v_raw[0].text)
        assert "models" in v_data and "fl_studio" in v_data and "summary" in v_data
        r_raw = (await c.read_resource("fl://rules/fl_formulas")).contents
        assert "Fruity Formula Controller" in r_raw[0].text


def test_cli_vault_and_dashboard_actions(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["vault", "status", "--json"])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert "summary" in out and "frameworks" in out

    with pytest.raises(SystemExit) as exc2:
        cli.main(["vault", "rules", "--workspace", str(tmp_path), "--json"])
    assert exc2.value.code == 0
    rout = json.loads(capsys.readouterr().out)
    assert rout["ok"] is True

    act_res = dashboard.dispatch_action({"action": "vault_rules", "workspace": str(tmp_path)})
    assert act_res["ok"] is True
    caps = audio.capabilities()
    assert caps["dsp"] is True and isinstance(caps["onnxruntime"], bool)


def test_onnx_basic_pitch_and_silero_vad_inference():
    import numpy as np
    from scipy import signal

    caps = audio.capabilities()
    assert caps["onnxruntime"] is True
    assert caps["soxr"] is True
    assert caps["pedalboard"] is True
    assert caps["pyloudnorm"] is True
    assert caps["pretty_midi"] is True

    sr = 44100
    t = np.arange(sr * 2, dtype="float64") / sr
    # Rich harmonic signal (A4 = 440 Hz + 3 harmonics)
    harmonic = (
        0.5 * np.sin(2 * np.pi * 440.0 * t)
        + 0.25 * np.sin(2 * np.pi * 880.0 * t)
        + 0.15 * np.sin(2 * np.pi * 1320.0 * t)
    )
    resampled = audio._resample_1d(np, signal, harmonic, sr, 16000)
    assert len(resampled) == pytest.approx(32000, abs=10)

    if caps["basic_pitch_onnx"]:
        bp_notes = audio._basic_pitch_notes(np, signal, harmonic, sr, bpm=120.0, max_notes=32)
        assert bp_notes is not None
        assert isinstance(bp_notes, list)

    if caps["silero_vad_onnx"]:
        voiced, ratio = audio._vad_active_mask(np, signal, harmonic, sr)
        assert ratio is not None and 0.0 <= ratio <= 1.0
