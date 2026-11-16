"""Loudness, peaks, stereo and spectrum, checked against signals whose answers are known."""

import math
import os
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
sf = pytest.importorskip("soundfile")

from fl_mcp import audio, render  # noqa: E402

FS = 48000


def sine(db, seconds=20.0, hz=997.0, fs=FS, phase=0.0):
    t = np.arange(int(fs * seconds)) / fs
    return (10 ** (db / 20) * np.sin(2 * np.pi * hz * t + phase)).astype("float32")


def stereo_of(x, y=None):
    return np.stack([x, x if y is None else y], axis=1)


def wav(tmp_path, data, name="x.wav", fs=FS, subtype="FLOAT"):
    path = tmp_path / name
    sf.write(str(path), data, fs, subtype=subtype)
    return path


def test_the_k_weighting_filters_match_the_standard_at_48_khz():
    (b1, a1), (b2, a2) = audio.k_filters(48000)
    assert b1 == pytest.approx([1.53512485958697, -2.69169618940638, 1.19839281085285], abs=1e-9)
    assert a1[1:] == pytest.approx([-1.69065929318241, 0.73248077421585], abs=1e-9)
    assert b2 == pytest.approx([1.0, -2.0, 1.0], abs=1e-9)
    assert a2[1:] == pytest.approx([-1.99004745483398, 0.99007225036621], abs=1e-6)


@pytest.mark.parametrize("db", [-23.0, -33.0, -14.0, -6.0])
def test_a_997_hz_stereo_sine_reads_its_own_level_in_lufs(db):
    got = audio.loudness(stereo_of(sine(db)), FS)
    assert got["lufs_i"] == pytest.approx(db, abs=0.1)
    assert got["lufs_m_max"] == pytest.approx(db, abs=0.1)
    assert got["lufs_s_max"] == pytest.approx(db, abs=0.1)
    assert got["lra"] == pytest.approx(0.0, abs=0.1)


def test_one_channel_of_a_stereo_pair_is_three_db_quieter():
    mono = stereo_of(sine(-20), np.zeros(FS * 20, dtype="float32"))
    assert audio.loudness(mono, FS)["lufs_i"] == pytest.approx(-23.0, abs=0.1)


def test_other_sample_rates_give_the_same_loudness():
    for fs in (44100, 96000):
        x = stereo_of(sine(-23, fs=fs))
        assert audio.loudness(x, fs)["lufs_i"] == pytest.approx(-23.0, abs=0.15)


def test_quiet_passages_are_gated_out_of_the_integrated_loudness():
    x = np.concatenate([sine(-23, 10), sine(-60, 10)])
    got = audio.loudness(stereo_of(x), FS)
    assert got["lufs_i"] == pytest.approx(-23.0, abs=0.15)
    assert got["lufs_s_max"] == pytest.approx(-23.0, abs=0.15)


def test_loudness_range_is_the_spread_of_short_term_loudness():
    x = np.concatenate([sine(-20, 30), sine(-30, 30)])
    assert audio.loudness(stereo_of(x), FS)["lra"] == pytest.approx(10.0, abs=0.6)


def test_silence_has_no_loudness():
    got = audio.loudness(np.zeros((FS * 5, 2), dtype="float32"), FS)
    assert got["lufs_i"] is None and got["lra"] is None


def test_true_peak_finds_what_sits_between_the_samples():
    x = sine(0, 2.0, hz=FS / 4, phase=math.pi / 4)  # every sample is +-0.7071, the wave peaks at 1
    data = stereo_of(x)
    assert audio.peaks(data)["peak_db"] == pytest.approx(-3.0, abs=0.1)
    assert audio.true_peak(data) == pytest.approx(0.0, abs=0.25)


def test_the_spectrum_puts_a_tone_in_its_band_and_noise_spread_by_width():
    s = audio.spectrum(stereo_of(sine(-12, 5, hz=100)), FS)
    assert s["bass"] > -0.5 and all(v < -25 for k, v in s.items() if k != "bass")
    noise = np.random.default_rng(1).uniform(-0.5, 0.5, (FS * 8, 2)).astype("float32")
    n = audio.spectrum(noise, FS)
    assert n["air"] > n["sub"] + 15 and n["high_mid"] > n["mid"]
    assert sum(10 ** (v / 10) for v in n.values()) == pytest.approx(1.0, abs=0.12)


def test_stereo_correlation_width_and_mono_compatibility():
    rng = np.random.default_rng(2)
    a, b = (
        rng.uniform(-0.5, 0.5, FS * 5).astype("float32"),
        rng.uniform(-0.5, 0.5, FS * 5).astype("float32"),
    )
    same = audio.stereo(stereo_of(a), FS)
    assert (
        same["corr"] == 1.0
        and same["side_db"] < -60
        and same["mono_loss_db"] == pytest.approx(0, abs=0.01)
    )
    flipped = audio.stereo(stereo_of(a, -a), FS)
    assert flipped["corr"] == -1.0 and flipped["mono_loss_db"] < -30
    apart = audio.stereo(stereo_of(a, b), FS)
    assert abs(apart["corr"]) < 0.05 and apart["side_db"] == pytest.approx(0.0, abs=0.5)
    assert audio.stereo(a.reshape(-1, 1), FS) is None


def test_a_wide_low_end_is_visible_in_the_side_signal():
    rng = np.random.default_rng(3)
    low = sine(-10, 5, hz=60)
    wide = audio.stereo(stereo_of(low, -low), FS)
    centred = audio.stereo(stereo_of(low), FS)
    assert wide["low_side_db"] > -3 and centred["low_side_db"] < -40
    assert rng is not None


def test_clipping_dc_and_silence_are_counted():
    x = np.clip(sine(6, 6.0, hz=200), -1, 1)
    lead, tail = np.zeros(FS * 2, dtype="float32"), np.zeros(FS * 4, dtype="float32")
    data = stereo_of(np.concatenate([lead, x + 0.02, tail]))
    p = audio.peaks(data)
    assert p["clipped"] > 100 and p["dc"] == pytest.approx(0.02, abs=0.003)
    assert p["lead"] == pytest.approx(FS * 2, abs=50) and p["trail"] == pytest.approx(
        FS * 4, abs=50
    )


def test_the_report_gives_measurements_and_plain_advice(tmp_path):
    loud = stereo_of(np.clip(sine(8, 10, hz=150), -1, 1))  # hard clipped and loud
    out = audio.report(wav(tmp_path, loud), target_lufs=-14.0)
    assert out["channels"] == 2 and out["rate"] == FS and out["dur_s"] == 10.0
    text = " | ".join(out["advice"])
    assert "louder than the -14.0 target" in text and "true peak" in text and "clipped" in text
    assert "crest factor" in text


def test_a_balanced_file_has_no_complaints(tmp_path):
    rng = np.random.default_rng(4)
    music = (0.07 * rng.standard_normal((FS * 12, 2))).astype("float32")
    out = audio.report(wav(tmp_path, music), target_lufs=out_lufs(music))
    assert [t for t in out["advice"] if "louder" in t or "quieter" in t or "peak" in t] == []


def out_lufs(x):
    return audio.loudness(x, FS)["lufs_i"]


def test_quieter_than_the_target_says_to_raise_the_limiter_input(tmp_path):
    rng = np.random.default_rng(5)
    quiet = (0.02 * rng.standard_normal((FS * 8, 2))).astype("float32")
    advice = audio.report(wav(tmp_path, quiet), target_lufs=-9.0)["advice"]
    assert any("quieter than the -9.0 target" in t and "limiter" in t for t in advice)


def test_comparing_with_a_reference_points_at_the_bands_that_differ(tmp_path):
    rng = np.random.default_rng(6)
    white = rng.standard_normal(FS * 10).astype("float64")
    dark = np.copy(white)
    for i in range(1, len(dark)):  # one-pole low-pass: dull
        dark[i] = dark[i - 1] + 0.15 * (white[i] - dark[i - 1])
    mix = wav(tmp_path, stereo_of((0.1 * dark / dark.std()).astype("float32")), "mix.wav")
    ref = wav(tmp_path, stereo_of((0.1 * white / white.std()).astype("float32")), "ref.wav")
    out = audio.compare(mix, ref)
    bands = {m["band"]: m["delta_db"] for m in out["tonal"]}
    assert bands["bass"] > 1.5 and bands["air"] < -1.5 and bands["high"] < -1.5
    assert any("lift it" in m["advice"] for m in out["tonal"])
    assert any("cut it" in m["advice"] for m in out["tonal"])
    assert out["loudness_diff_lu"] is not None and abs(out["width_diff_db"]) < 1


def test_unreadable_audio_is_explained(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        audio.load(tmp_path / "missing.wav")
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(ValueError, match="cannot read bad.wav"):
        audio.load(bad)
    with pytest.raises(ValueError, match="shorter than"):
        audio.load(wav(tmp_path, np.zeros((100, 2), dtype="float32"), "tiny.wav"))


def test_other_formats_load_too(tmp_path):
    x = stereo_of(sine(-12, 2.0))
    flac = tmp_path / "x.flac"
    sf.write(str(flac), x, FS)
    data, fs = audio.load(flac)
    assert fs == FS and data.shape == x.shape
    pcm = wav(tmp_path, x, "pcm24.wav", subtype="PCM_24")
    assert audio.loudness(audio.load(pcm)[0], FS)["lufs_i"] == pytest.approx(-12.0, abs=0.15)


# ---- rendering through FL's command line


class FakeProc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_render(tmp_path, flp, fmt="wav", writes=None, timeout=60, proc=None, **kw):
    """Render with a fake FL: `writes(cmd, clock)` plays the part of FL writing files."""
    clock, calls = Clock(), []
    work = tmp_path / "work"

    def popen(cmd):
        calls.append(cmd)
        return proc or FakeProc()

    def sleep(s):
        clock.sleep(s)
        if writes:
            writes(calls[-1], clock)

    out = render.render(
        flp, fmt, timeout, Path("C:/FL/FL64.exe"), popen, sleep, clock.now, lambda: 1000.0,
        workdir=work, **kw,
    )  # fmt: skip
    return out, calls


def test_a_lone_project_is_rendered_where_it_is_with_flps_own_switches(tmp_path):
    folder = tmp_path / "My Song"
    folder.mkdir()
    flp = folder / "song.flp"
    flp.write_bytes(b"FLhd")

    def fl_writes(cmd, clock):
        out = Path(cmd.split('/O"')[1].rstrip('"'))
        (out / "song.wav").write_bytes(b"x" * min(int(clock.t) * 1000, 3000))
        os.utime(out / "song.wav", (1001, 1001))

    out, calls = run_render(tmp_path, flp, writes=fl_writes)
    assert calls == [
        f'"{Path("C:/FL/FL64.exe")}" /R /Ewav /F"{folder.resolve()}" '
        f'/O"{tmp_path / "work" / "1000" / "audio"}"'
    ]
    assert out["audio"].endswith("song.wav") and out["copied"] is False and out["seconds"] >= 3


def test_a_project_among_others_is_copied_to_a_folder_of_its_own(tmp_path):
    for name in ("a", "b", "song"):
        (tmp_path / f"{name}.flp").write_bytes(b"FLhd" + name.encode())
    (tmp_path / "Backup").mkdir()
    (tmp_path / "Backup" / "song.flp").write_bytes(b"old")  # FL skips backups, so do we

    def fl_writes(cmd, clock):
        source = Path(cmd.split('/F"')[1].split('"')[0])
        assert [f.name for f in source.glob("*.flp")] == ["song.flp"]
        out = Path(cmd.split('/O"')[1].rstrip('"'))
        (out / "song.wav").write_bytes(b"y" * 100)
        os.utime(out / "song.wav", (1001, 1001))

    out, calls = run_render(tmp_path, tmp_path / "song.flp", writes=fl_writes)
    assert out["copied"] is True and "/F" in calls[0]
    assert (tmp_path / "song.flp").read_bytes() == b"FLhdsong"  # the original is untouched


def test_a_lone_backup_does_not_count_as_another_project(tmp_path):
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    (tmp_path / "Backup").mkdir()
    (tmp_path / "Backup" / "song.flp").write_bytes(b"old")

    def fl_writes(cmd, clock):
        out = Path(cmd.split('/O"')[1].rstrip('"'))
        (out / "song.mp3").write_bytes(b"z" * 10)
        os.utime(out / "song.mp3", (1001, 1001))

    out, calls = run_render(tmp_path, flp, "mp3", writes=fl_writes)
    assert out["copied"] is False and "/Emp3" in calls[0]


def test_an_older_fl_without_the_output_folder_switch_writes_beside_the_project(tmp_path):
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")

    def fl_writes(cmd, clock):
        (tmp_path / "song.wav").write_bytes(b"w" * 50)
        os.utime(tmp_path / "song.wav", (1001, 1001))

    out, _ = run_render(tmp_path, flp, writes=fl_writes)
    assert out["audio"] == str(tmp_path / "song.wav")


def test_render_failures_say_what_happened(tmp_path):
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    with pytest.raises(ValueError, match="exited with code 3 and wrote no audio"):
        run_render(tmp_path, flp, proc=FakeProc(3))
    with pytest.raises(ValueError, match="no audio appeared within 5 s.*close FL first"):
        run_render(tmp_path, flp, timeout=5)
    with pytest.raises(ValueError, match="not an existing .flp"):
        render.render(tmp_path / "nope.flp", "wav", 5, Path("FL64.exe"))
    with pytest.raises(ValueError, match="format must be one of"):
        render.render(flp, "aac", 5, Path("FL64.exe"))


def test_without_fl_the_reason_is_given(tmp_path, monkeypatch):
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="cannot render from the command line"):
        render.render(flp)


def test_the_installed_fl_is_found_by_its_executable(tmp_path, monkeypatch):
    (tmp_path / "FL64.exe").write_bytes(b"")
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path))
    assert render.find_fl() == tmp_path / "FL64.exe"


def test_the_current_project_is_the_flp_saved_a_moment_ago(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_PROJECTS", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    old, new = tmp_path / "old.flp", tmp_path / "sub" / "new.flp"
    new.parent.mkdir()
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))
    os.utime(new, (now - 5, now - 5))
    assert render.newest_project(now=now) == new
    os.utime(new, (now - 600, now - 600))
    assert render.newest_project(now=now) is None


def test_always_copy_isolates_a_lone_project_for_parallel_render(tmp_path):
    folder = tmp_path / "Solo"
    folder.mkdir()
    flp = folder / "song.flp"
    flp.write_bytes(b"FLhd-orig")

    def fl_writes(cmd, clock):
        source = Path(cmd.split('/F"')[1].split('"')[0])
        assert source != folder.resolve()
        assert (source / "song.flp").read_bytes() == b"FLhd-orig"
        out = Path(cmd.split('/O"')[1].rstrip('"'))
        (out / "song.wav").write_bytes(b"z" * 100)
        os.utime(out / "song.wav", (1001, 1001))

    out, calls = run_render(tmp_path, flp, writes=fl_writes, always_copy=True)
    assert out["copied"] is True and "/F" in calls[0]
    assert flp.read_bytes() == b"FLhd-orig"


def test_hidden_popen_creates_isolated_desktop_and_cleans_handles(monkeypatch):
    import ctypes
    import subprocess
    import sys

    created = {}
    closed_handles = []
    closed_desktops = []

    class User32:
        def CreateDesktopW(self, name, dev, devmode, flags, access, sa):  # noqa: N802
            created["desktop"] = name
            return 9001

        def CloseDesktop(self, hdesk):  # noqa: N802
            closed_desktops.append(hdesk)
            return 1

    class Kernel32:
        def CreateProcessW(  # noqa: N802
            self, app, cmd, pa, ta, inherit, flags, env, cwd, si_ptr, pi_ptr
        ):
            si = ctypes.cast(si_ptr, ctypes.POINTER(render._STARTUPINFOW)).contents
            pi = ctypes.cast(pi_ptr, ctypes.POINTER(render._PROCESS_INFORMATION)).contents
            created["flags"] = flags
            created["dwFlags"] = si.dwFlags
            created["show"] = si.wShowWindow
            created["lpDesktop"] = si.lpDesktop
            pi.hProcess = 7001
            pi.hThread = 7002
            return 1

        def WaitForSingleObject(self, handle, ms):  # noqa: N802
            return 0

        def GetExitCodeProcess(self, handle, code_ptr):  # noqa: N802
            code_ptr._obj.value = 0
            return 1

        def CloseHandle(self, handle):  # noqa: N802
            closed_handles.append(handle)
            return 1

    class FakeWindll:
        user32 = User32()
        kernel32 = Kernel32()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    proc = render.hidden_popen('"C:/FL/FL64.exe" /R', isolated_desktop=True)
    assert isinstance(proc, render._DesktopProcess)
    assert created["desktop"].startswith("FLMCP_Render_")
    assert created["lpDesktop"] == created["desktop"]
    assert created["show"] == render._SW_HIDE
    assert created["dwFlags"] == render._STARTF_USESHOWWINDOW
    assert proc.poll() == 0
    assert 7002 in closed_handles and 7001 in closed_handles
    assert closed_desktops == [9001]

    # Fallback when CreateDesktopW fails returns hidden subprocess.Popen args cleanly
    monkeypatch.setattr(FakeWindll.user32, "CreateDesktopW", lambda *a: 0)
    popen_args = {}

    def fake_popen(cmd, **kw):
        popen_args["cmd"] = cmd
        popen_args.update(kw)
        return "subproc"

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: type("SI", (), {})(), raising=False)
    assert render.hidden_popen("cmd", isolated_desktop=True) == "subproc"
    assert popen_args["creationflags"] == (
        render._CREATE_NO_WINDOW | render._BELOW_NORMAL_PRIORITY_CLASS
    )
    assert popen_args["startupinfo"].wShowWindow == render._SW_HIDE
