#!/usr/bin/env python3
"""VocalRender single-take driver for the 伶人 service (Phase 2).

Usage: see adapters/vocalrender.py. Runs the upstream VR inference
script (patches/03: --seed) as a subprocess, one take per process, one
seed per take, then prints one JSON summary line on stdout as the last
line:
  {"out": ..., "sr": 48000, "duration_s": ...}

The label JSON (one item, list-wrapped, as the upstream CLI expects)
is built by the adapter from the unified score via
score_converter.vr_label_entry -- note tokens are the <NOTE_4>
denominator system (patches/05); an unknown token would silently fall
back to the default one beat, so the adapter validates durations
before reaching this driver.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="VocalRender checkout (patch 03 applied)")
    ap.add_argument("--ckpt-dir", required=True)
    ap.add_argument("--label-json", required=True,
                    help="List-wrapped label file with one item")
    ap.add_argument("--item-name", required=True)
    ap.add_argument("--prompt-audio", required=True,
                    help="2-8 s clean singing reference audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--fsq-temperature", type=float, default=None)
    ap.add_argument("--max-len", type=int, default=None)
    args = ap.parse_args()

    script = Path(args.repo) / "scripts" / "infer_vocalrender_svs_single.py"
    if not script.is_file():
        print(f"VR inference script not found: {script}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script),
           "--ckpt_dir", args.ckpt_dir,
           "--json_file", args.label_json,
           "--item_name", args.item_name,
           "--prompt_audio", args.prompt_audio,
           "--output", args.out,
           "--seed", str(args.seed)]
    if args.temperature is not None:
        cmd += ["--temperature", str(args.temperature)]
    if args.fsq_temperature is not None:
        cmd += ["--fsq_temperature", str(args.fsq_temperature)]
    if args.max_len is not None:
        cmd += ["--max_len", str(args.max_len)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=3600)
    if proc.returncode != 0:
        sys.stderr.write((proc.stderr or proc.stdout)[-3000:])
        return proc.returncode
    out_path = Path(args.out)
    if not out_path.is_file():
        print(f"VR script exited 0 but produced no output: {out_path}",
              file=sys.stderr)
        return 3
    try:
        import soundfile as sf
        info = sf.info(str(out_path))
        sr, duration = int(info.samplerate), round(info.duration, 4)
    except Exception:
        import wave
        with wave.open(str(out_path), "rb") as w:
            sr = w.getframerate()
            duration = round(w.getnframes() / sr, 4)
    print(json.dumps({"out": args.out, "sr": sr, "duration_s": duration}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
