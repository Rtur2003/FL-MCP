"""Run under Python <= 3.10 with PyFLP installed: print what an independent FLP parser sees."""

import json
import sys

import pyflp

proj = pyflp.parse(sys.argv[1])
out = {
    "tempo": proj.tempo,
    "ppq": proj.ppq,
    "channels": [[c.iid, c.name] for c in proj.channels],
    "detail": {
        c.iid: {
            "name": c.name,
            "insert": getattr(c, "insert", None),
            "pan": getattr(c, "pan", None),
            "volume": getattr(c, "volume", None),
            "sample": getattr(c, "sample_path", None) and str(c.sample_path),
        }
        for c in proj.channels
    },
    "patterns": {
        p.iid: {
            "name": p.name,
            "color": list(p.color) if p.color is not None else None,
            "notes": sorted(
                [n.position, n.length, n.key, n.velocity, n.rack_channel] for n in p.notes
            ),
        }
        for p in proj.patterns
    },
}
arr = list(proj.arrangements)[0]
items = []
for track in arr.tracks:
    for it in track:
        pat = getattr(it, "pattern", None)
        items.append([it.position, it.length, pat.iid if pat is not None else None, track.iid])
out["playlist"] = sorted(items, key=lambda x: (x[0], x[3], x[2] if x[2] is not None else -1))
print(json.dumps(out))
