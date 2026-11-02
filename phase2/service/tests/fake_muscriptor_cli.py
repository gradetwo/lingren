"""Fake muscriptor driver CLI for tests (NOT a model).

Implements the same CLI contract as app/muscriptor_driver.py
(--audio, --model, --device, --instruments, --out-midi, --out-notes,
plus tempo flags it ignores) but writes canned outputs: a minimal
MIDI header, a fixed note list, progress lines and a summary line.
It never imports torch and never touches weights; it proves the
job plumbing, endpoint shapes and receipt format, NOT transcription
accuracy. Set FAKE_MUSCRIPTOR_FAIL=1 to exit non-zero with a loud
stderr message (driver-failure path test).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--dtype", default=None)
    ap.add_argument("--instruments", default="")
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--beats-per-bar", type=int, default=None)
    ap.add_argument("--first-downbeat", type=float, default=None)
    ap.add_argument("--quantize", action="store_true")
    ap.add_argument("--grid", type=int, default=None)
    ap.add_argument("--detect-tempo", default="best-effort")
    ap.add_argument("--out-midi", required=True)
    ap.add_argument("--out-notes", required=True)
    args = ap.parse_args()
    if os.environ.get("FAKE_MUSCRIPTOR_FAIL") == "1":
        print("fake driver: simulated ModelDownloadError: gated "
              "weights need HuggingFace auth", file=sys.stderr)
        return 3
    print(json.dumps({"type": "progress", "completed": 0, "total": 2}))
    print(json.dumps({"type": "progress", "completed": 1, "total": 2}))
    print(json.dumps({"type": "progress", "completed": 2, "total": 2}))
    sys.stdout.flush()
    Path(args.out_midi).write_bytes(
        b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0"
        b"MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")
    notes = [
        {"instrument": "voice", "pitch": 69, "start_s": 0.0,
         "end_s": 1.0},
        {"instrument": "acoustic_piano", "pitch": 60, "start_s": 0.5,
         "end_s": 1.5},
    ]
    Path(args.out_notes).write_text(json.dumps(notes, indent=2))
    print(json.dumps({"type": "summary", "duration_s": 2.0,
                      "notes_total": len(notes),
                      "input_rms_db": -20.0, "beat_grid": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
