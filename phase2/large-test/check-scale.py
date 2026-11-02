"""Check a transcription of scale.wav against the known ground truth.

scale.wav: C major scale, 8 notes, 0.5 s each, 4.0 s total:
  C4 D4 E4 F4 G4 A4 B4 C5 = MIDI 60 62 64 65 67 69 71 72
  starts at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5 s.

Usage: python3 check-scale.py notes.json
A detected note counts as a hit for an expected note when the pitch
matches exactly and the detected start is within 0.30 s of the
expected start. Extras are detected notes that match no expected
note under that rule. Prints a per-note table plus a summary JSON.
"""

import json
import sys

EXPECTED = [(60, 0.0), (62, 0.5), (64, 1.0), (65, 1.5),
            (67, 2.0), (69, 2.5), (71, 3.0), (72, 3.5)]
TOL_S = 0.30


def main() -> int:
    notes = json.load(open(sys.argv[1]))
    used = set()
    rows = []
    hits = 0
    for pitch, start in EXPECTED:
        best = None
        for i, n in enumerate(notes):
            if i in used or n["pitch"] != pitch:
                continue
            dev = n["start_s"] - start
            if abs(dev) <= TOL_S and (best is None
                                      or abs(dev) < abs(best[1])):
                best = (i, dev)
        if best is not None:
            used.add(best[0])
            hits += 1
            rows.append({"expected_midi": pitch, "expected_start_s": start,
                         "hit": True, "start_dev_s": round(best[1], 4),
                         "detected": notes[best[0]]})
        else:
            rows.append({"expected_midi": pitch, "expected_start_s": start,
                         "hit": False, "start_dev_s": None,
                         "detected": None})
    extras = [n for i, n in enumerate(notes) if i not in used]
    for r in rows:
        print(r)
    print("SUMMARY " + json.dumps({
        "expected": len(EXPECTED), "hits": hits,
        "detected_total": len(notes), "extras": len(extras),
        "extra_notes": extras[:20]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
