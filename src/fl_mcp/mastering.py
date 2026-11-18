"""Bring a mix to its target loudness with the master limiter, measuring after every change.

The loop is the one a mastering engineer runs by ear and by meter: measure the loudness, move the
limiter's input gain by the difference, measure again. `measure` renders the project and returns
`audio.report`; the limiter is found and set through the plugin engine, in real units."""

from collections.abc import Callable
from typing import Any

from fl_mcp import plugins_kb, units

Call = Callable[[dict[str, Any]], Any]
Measure = Callable[[], dict[str, Any]]
PASSES = 3  # adjustments at most (so up to four measurements)
TOLERANCE = 0.5  # LU: close enough
STEP_LIMIT = 12.0  # dB: the most the gain moves in one pass
NO_LIMITER = (
    'the master track has no limiter: load one with fl_ui action=load query="Fruity Limiter" '
    "index=0, then master again"
)


def find_limiter(call: Call) -> dict[str, Any]:
    start: int | None = 0
    while start is not None:
        out = call({"o": "list", "k": "plugins", "s": start, "n": 60, "a": 1})
        for _, at, index, slot, plugin, *_ in out["rows"]:
            if at == "m" and index == 0 and plugins_kb.classify(plugin) == "limiter":
                return {"slot": slot, "plugin": plugin}
        start = out.get("next")
    raise ValueError(NO_LIMITER)


def parameters(call: Call, slot: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    start = 0
    for _ in range(6):
        out = call({"o": "param", "t": "mixer", "i": 0, "s": slot, "a": "list", "f": "",
                    "st": start, "n": 120})  # fmt: skip
        rows += out["rows"]
        if not out["rows"] or out["rows"][-1][0] + 1 >= out["count"]:
            break
        start = out["rows"][-1][0] + 1
    return rows


def _pick(rows: list[list[Any]], intent: str) -> list[Any] | None:
    items, _ = plugins_kb.match_intents(rows, {intent: "0 dB"})
    return next((r for r in rows if items and r[0] == items[0]["p"]), None)


def _db(text: str) -> float | None:
    got = units.parse_value(text)
    return got[0] if got and got[1] == "db" else None


def run(
    call: Call, measure: Measure, target: float, ceiling: float = -1.0, passes: int = PASSES
) -> dict[str, Any]:
    lim = find_limiter(call)
    rows = parameters(call, lim["slot"])
    gain, sign = _pick(rows, "limitgain"), 1.0
    if gain is None:  # a limiter driven by its threshold: lower threshold means louder
        gain, sign = _pick(rows, "limitthreshold"), -1.0
    if gain is None or _db(gain[3]) is None:
        names = ", ".join(str(r[1]) for r in rows[:12])
        raise ValueError(
            f"cannot find a dB gain or threshold parameter on {lim['plugin']} (it has: {names}): "
            "set it by hand with fl_param and measure with fl_audio"
        )
    ref = {"o": "param", "t": "mixer", "i": 0, "s": lim["slot"], "a": "set"}
    ceil_row = _pick(rows, "ceiling")
    if ceil_row is not None:
        call({**ref, "p": ceil_row[0], "r": f"{ceiling:.1f} dB"})
    current = start = _db(gain[3]) or 0.0
    history: list[dict[str, float | None]] = []
    notes: list[str] = []
    report: dict[str, Any] = {}
    for step in range(passes + 1):
        report = measure()
        lufs = report.get("lufs_i")
        if lufs is None:
            raise ValueError("the render is silent: is the project empty, or everything muted?")
        history.append({"lufs": lufs, "true_peak": report["true_peak_db"], "gain_db": current})
        if step:
            applied = abs(current - history[-2]["gain_db"])
            if applied >= 1.0 and abs(lufs - history[-2]["lufs"]) < 0.25 * applied:
                notes.append(
                    "the limiter is saturating (more gain adds distortion, not loudness): stopped"
                )
                break
        off = target - lufs
        if abs(off) <= TOLERANCE or step == passes:
            break
        delta = max(-STEP_LIMIT, min(STEP_LIMIT, off))
        row = call({**ref, "p": gain[0], "r": f"{current + sign * delta:.1f} dB"})["param"]
        if not row[4]:
            notes.append(f"{gain[1]} cannot reach {current + sign * delta:.1f} dB: stopped")
            break
        current = _db(row[3]) if _db(row[3]) is not None else current + sign * delta
        if ceil_row is not None and report["true_peak_db"] > ceiling + 0.1:
            over = report["true_peak_db"] - ceiling  # a limiter's ceiling is not always true-peak
            call({**ref, "p": ceil_row[0], "r": f"{ceiling - over:.1f} dB"})
    final = history[-1]
    reached = abs(target - final["lufs"]) <= TOLERANCE and final["true_peak"] <= ceiling + 0.1
    return {
        "target_lufs": target,
        "ceiling_db": ceiling,
        "limiter": {
            **lim,
            "gain_param": gain[1],
            "original_gain_db": start,
            "final_gain_db": current,
        },  # fmt: skip
        "passes": history,
        "reached": reached,
        "notes": notes,
        "final": {k: report.get(k) for k in ("lufs_i", "true_peak_db", "crest_db", "advice")},
    }
