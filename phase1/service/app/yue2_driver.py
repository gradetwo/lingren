#!/usr/bin/env python3
"""YuE2 single-take driver for the 伶人 service (four-pipeline work).

Usage: see adapters/yue2.py. Loads YuE2Pipeline (in-process, one take
per process, one seed per take), generates the full part from a native
ABC file + lyrics + style, saves artifacts, then separates the dry
vocal with Demucs 4.1.0 htdemucs (fixed step, cannot be disabled --
YuE2 generates a full song, the service output is the dry vocal).
Prints one JSON summary line on stdout as the last line:
  {"out": ..., "sr": ..., "duration_s": ..., "gen_duration_s": ...}

The YuE2 package must be importable from this interpreter (the
YUE2_REPO checkout with `uv pip install .` done, per the setup
script yue2-min-exp/setup_yue2_r4.sh); --repo is added to sys.path
as a fallback only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _demucs_separate(audio_path: Path, workdir: Path) -> Path:
    """Demucs htdemucs two-stems=vocals; returns the vocals wav."""
    out_dir = workdir / "demucs"
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals",
           "-n", "htdemucs", "-o", str(out_dir), str(audio_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=3600)
    if proc.returncode != 0:
        sys.stderr.write((proc.stderr or proc.stdout)[-3000:])
        raise SystemExit(proc.returncode or 1)
    vocals = out_dir / "htdemucs" / audio_path.stem / "vocals.wav"
    if not vocals.is_file():
        print(f"yue2 driver: demucs produced no vocals wav: {vocals}",
              file=sys.stderr)
        raise SystemExit(3)
    return vocals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="YuE checkout (yue2-v0.1.6, uv pip install . done)")
    ap.add_argument("--model", default="m-a-p/YuE2-3B")
    ap.add_argument("--model-revision",
                    default="8f24312f187763b9854adeec87e437e2b03bbff1")
    ap.add_argument("--vae-revision",
                    default="152733a19ad43aa67e367f9b5503ef8075bb5126")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--abc", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--style", required=True)
    ap.add_argument("--cot", default="melody")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cfg-scale", type=float, default=1.0)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--repetition-penalty", type=float, default=1.2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo)
    if (repo / "yue2").is_dir():
        sys.path.insert(0, str(repo))
    abc = Path(args.abc).read_text(encoding="utf-8")
    lyrics = Path(args.lyrics).read_text(encoding="utf-8")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    from yue2 import YuE2Pipeline

    t0 = time.time()
    pipe_ctx = YuE2Pipeline.from_pretrained(
        args.model, revision=args.model_revision,
        vae_revision=args.vae_revision, device=args.device)
    with pipe_ctx as pipe:
        print(f"model loaded in {time.time() - t0:.1f}s", flush=True)
        ts = time.time()
        song = pipe(style=args.style, lyrics=lyrics, abc=abc,
                    cot=args.cot, seed=args.seed,
                    cfg_scale=args.cfg_scale,
                    semantic_sampling={
                        "temperature": args.temperature,
                        "top_p": args.top_p, "top_k": args.top_k,
                        "repetition_penalty": args.repetition_penalty})
        gen_dir = workdir / "gen"
        song.save_artifacts(str(gen_dir))
        gen_s = round(time.time() - ts, 1)
    audio = gen_dir / "audio.flac"
    if not audio.is_file():
        # Fall back to whatever audio artifact the pipeline wrote.
        cands = sorted(gen_dir.glob("*.flac")) + sorted(
            gen_dir.glob("*.wav"))
        if not cands:
            print(f"yue2 driver: no audio artifact in {gen_dir}",
                  file=sys.stderr)
            return 3
        audio = cands[0]
    vocals = _demucs_separate(audio, workdir)
    out_path = Path(args.out)
    shutil.copyfile(vocals, out_path)
    try:
        import soundfile as sf
        info = sf.info(str(out_path))
        sr, duration = int(info.samplerate), round(info.duration, 4)
    except Exception:
        import wave
        with wave.open(str(out_path), "rb") as w:
            sr = w.getframerate()
            duration = round(w.getnframes() / sr, 4)
    print(json.dumps({"out": args.out, "sr": sr, "duration_s": duration,
                      "gen_duration_s": gen_s, "model": args.model,
                      "model_revision": args.model_revision}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
