"""Fake SVC prompt driver CLI for tests (NOT a model).

Implements the same CLI contract as app/svc_prompt_driver.py (all of
its flags, values ignored except --out-wav/--out-meta) but writes
canned outputs: a 24 kHz sine prompt wav and a one-segment prompt
meta with all required keys, plus a JSON summary line. It never
imports torch and never touches weights; it proves the job plumbing,
endpoint shapes and receipt format, NOT conversion quality. The
real-model check is the 《孤勇者》 pilot report.

Set FAKE_SVC_PROMPT_FAIL=1 to exit non-zero with a loud stderr
message (driver-failure path test). Set FAKE_SVC_PROMPT_CLIP=1 to
write 3 full-scale samples into the prompt wav (clipped-warning
path test); the worker measures clipping from the file itself.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np


def _wav_bytes(seconds=2.0, clip=False):
    sr = 24000
    x = (0.3 * np.sin(2 * np.pi * 440.0
                      * np.arange(int(sr * seconds)) / sr) * 32767)
    x = x.astype(np.int16)
    if clip:
        x[100:103] = 32767
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    buf.seek(0)
    return buf.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="")
    ap.add_argument("--model-path", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--source", default="")
    ap.add_argument("--timbre", default="")
    ap.add_argument("--vocal-sep", default="True")
    ap.add_argument("--language", default="Mandarin")
    ap.add_argument("--max-merge-duration", type=int, default=30000)
    ap.add_argument("--n-steps", type=int, default=32)
    ap.add_argument("--cfg", type=float, default=3.0)
    ap.add_argument("--pitch-shift", type=int, default=0)
    ap.add_argument("--auto-shift", action="store_true")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--out-wav", required=True)
    ap.add_argument("--out-meta", required=True)
    ap.add_argument("--workdir", default="")
    args = ap.parse_args()
    if os.environ.get("FAKE_SVC_PROMPT_FAIL") == "1":
        print("fake driver: simulated WeightDownloadError: "
              "model-svc.pt not found under SOULX_REPO",
              file=sys.stderr)
        return 3
    clip = os.environ.get("FAKE_SVC_PROMPT_CLIP") == "1"
    Path(args.out_wav).write_bytes(_wav_bytes(clip=clip))
    segments = [{
        "index": "fake_0_2000",
        "language": "Mandarin",
        "time": [0, 2000],
        "text": "<SP> 假 段",
        "phoneme": "<SP> zh_jia3 zh_duan4",
        "note_pitch": "60 62",
        "note_type": "0 0",
        "duration": "1.0 1.0",
        "f0": "0 " * 99 + "0",
    }]
    Path(args.out_meta).write_text(json.dumps(segments, indent=2))
    print(json.dumps({"out_wav": args.out_wav,
                      "out_meta": args.out_meta,
                      "segments": len(segments),
                      "sr": 24000, "duration_s": 2.0,
                      "peak": 0.3 if not clip else 1.0,
                      "clipped_samples": 3 if clip else 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
