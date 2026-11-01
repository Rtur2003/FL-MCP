import json
import random

import pytest

from fl_mcp import bridge as b
from tests.fakefl import load_script


def decode(pairs):
    dec, out = b.Decoder(), None
    for cc, value in pairs:
        out = dec.feed(cc, value) or out
    return out


@pytest.mark.parametrize("n", [0, 1, 2, 127, 128, 500, 4096])
def test_roundtrip(n):
    rnd = random.Random(n)
    payload = bytes(rnd.randrange(128) for _ in range(n))
    assert decode(b.encode(7, payload)) == (7, payload)


def test_every_value_is_a_valid_midi_data_byte():
    pairs = b.encode(127, bytes(range(128)))
    assert all(0 <= cc < 128 and 0 <= value < 128 for cc, value in pairs)
    assert {cc for cc, _ in pairs} == {b.BEGIN, b.DATA, b.SUM_A, b.SUM_B}


def test_corruption_is_detected():
    pairs = b.encode(5, b'{"o":"status"}')
    flipped = list(pairs)
    flipped[3] = (flipped[3][0], (flipped[3][1] + 1) % 128)
    dropped = pairs[:3] + pairs[4:]
    duplicated = pairs[:3] + pairs[2:3] + pairs[3:]
    swapped = pairs[:3] + [pairs[4], pairs[3]] + pairs[5:]
    for broken in (flipped, dropped, duplicated, swapped):
        assert decode(broken) == (5, None)


def test_random_single_errors_never_pass():
    rnd = random.Random(1)
    payload = bytes(rnd.randrange(32, 127) for _ in range(300))
    pairs = b.encode(9, payload)
    for _ in range(300):
        i = rnd.randrange(1, len(pairs) - 2)
        broken = list(pairs)
        broken[i] = (broken[i][0], (broken[i][1] + rnd.randrange(1, 127)) % 128)
        assert decode(broken) != (9, payload)


def test_garbage_and_restarts_are_ignored():
    dec = b.Decoder()
    assert dec.feed(b.DATA, 65) is None and dec.feed(b.SUM_B, 3) is None
    for cc, value in b.encode(3, b"lost")[:3]:
        dec.feed(cc, value)
    result = None
    for cc, value in b.encode(4, b"ok"):
        result = dec.feed(cc, value) or result
    assert result == (4, b"ok")


def test_checksum_matches_the_fl_script():
    script = load_script()
    rnd = random.Random(2)
    for n in (0, 1, 50, 2000):
        data = bytes(rnd.randrange(128) for _ in range(n))
        assert script._sums(data) == b.sums(data)


def test_dumps_is_seven_bit_and_lossless():
    obj = {"n": "Çalgı \x7f ünï 🎹", "k": [1, 2.5, None, True]}
    raw = b.dumps(obj)
    assert max(raw) < 128
    assert json.loads(raw) == obj
    assert load_script()._dump(obj) == raw


@pytest.mark.parametrize(
    ("names", "wanted", "expected"),
    [
        (["Microsoft GS Wavetable Synth", "FL MCP 1"], "FL MCP", "FL MCP 1"),
        (["FL MCP 2", "FL MCP"], "FL MCP", "FL MCP"),
        (["loopMIDI Port FL MCP"], "fl mcp", "loopMIDI Port FL MCP"),
        (["IAC Driver Bus 1"], "iac", "IAC Driver Bus 1"),
        (["Other"], "FL MCP", None),
        ([], "FL MCP", None),
    ],
)
def test_match_port(names, wanted, expected):
    assert b.match_port(names, wanted) == expected
