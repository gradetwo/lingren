#!/usr/bin/env python3
"""SoulX SVC single-conversion driver for the 伶人 service.

Runs the SoulX-Singer-SVC pipeline as subprocesses so model code
stays out of the API process: preprocess the source performance
(vocal_sep off) -> vocal.wav + vocal_f0.npy, preprocess the timbre
reference (vocal_sep off) -> vocal.wav + vocal_f0.npy, then
cli.inference_svc (source performance -> timbre voice) ->
generated.wav (24 kHz), copied to --out.

This is steps 1-3 of phase2/service/app/svc_prompt_driver.py --
the same subprocess call shape (same flags, same CWD=repo rule:
preprocess loads RMVPE from the relative path
pretrained_models/SoulX-Singer-Preprocess/rmvpe/rmvpe.pt, so the
pipeline MUST run with cwd=repo), WITHOUT that driver's step 4
(prompt-meta transcription): the SVC post-process outputs a wav,
not a SoulX prompt pair. On success prints one JSON summary line
as the last stdout line.

Fixed by design (section 6.2): pitch_shift 0, auto_shift off,
fp16 on cuda. The caller (adapters/svc_post.py) resamples both
inputs to 24 kHz mono first, matching the folk SVC recipe.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Interpreter for the SoulX subprocesses (preprocess + inference).
# Default is this process's interpreter. Set SVC_PYTHON to a
# SoulX-pinned interpreter when the service runs in an env whose
# torch/transformers are too new for SoulX's vendored llama.py
# (newer transformers' LlamaAttention.forward requires a
# position_embeddings argument the vendored call site does not
# pass -- TypeError at inference). The service process itself is
# unaffected; only the two SoulX steps run under SVC_PYTHON.
_PYTHON = os.environ.get("SVC_PYTHON", sys.executable)


def _resolve(path: str, repo: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else repo / p


def _run(cmd: list[str], step: str, repo: Path, env: dict) -> None:
    proc = subprocess.run(cmd, cwd=str(repo), env=env,
                          capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        print(f"svc driver: step {step} failed "
              f"(exit {proc.returncode}): {tail}", file=sys.stderr)
        raise SystemExit(proc.returncode or 1)


def _require(path: Path, step: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        print(f"svc driver: step {step} produced no output: {path}",
              file=sys.stderr)
        raise SystemExit(3)


def _measure(wav_path: Path) -> dict:
    try:
        import soundfile as sf
        info = sf.info(str(wav_path))
        data, _ = sf.read(str(wav_path), dtype="float32")
        peak = float(max(abs(data))) if data.size else 0.0
        clipped = int(sum(1 for x in data.flat if abs(x) >= 0.9999))
        return {"sr": int(info.samplerate),
                "duration_s": round(float(info.duration), 4),
                "peak": round(peak, 6), "clipped_samples": clipped}
    except Exception:
        import wave
        with wave.open(str(wav_path), "rb") as w:
            sr = w.getframerate()
            duration = round(w.getnframes() / sr, 4)
        return {"sr": sr, "duration_s": duration, "peak": None,
                "clipped_samples": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="SoulX-Singer checkout (cwd for all steps)")
    ap.add_argument("--model-path",
                    default="pretrained_models/SoulX-Singer/model-svc.pt",
                    help="relative to --repo unless absolute")
    ap.add_argument("--config",
                    default="soulxsinger/config/soulxsinger.yaml",
                    help="relative to --repo unless absolute")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--source", required=True,
                    help="dry vocal to convert (24 kHz mono, resampled "
                         "by the caller)")
    ap.add_argument("--timbre", required=True,
                    help="timbre reference audio (24 kHz mono, "
                         "resampled by the caller)")
    ap.add_argument("--n-steps", type=int, default=32)
    ap.add_argument("--cfg", type=float, default=3.0)
    ap.add_argument("--pitch-shift", type=int, default=0)
    ap.add_argument("--auto-shift", action="store_true")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"svc driver: repo not a directory: {repo}",
              file=sys.stderr)
        return 2
    model_path = _resolve(args.model_path, repo)
    config = _resolve(args.config, repo)
    if not model_path.is_file():
        print(f"svc driver: SVC weights not found: {model_path}",
              file=sys.stderr)
        return 2
    if not config.is_file():
        print(f"svc driver: config not found: {config}", file=sys.stderr)
        return 2
    for label, path in (("source", args.source), ("timbre", args.timbre)):
        if not Path(path).is_file():
            print(f"svc driver: {label} audio not found: {path}",
                  file=sys.stderr)
            return 2

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    prep_source = workdir / "prep_source"
    prep_timbre = workdir / "prep_timbre"
    svc_out = workdir / "svc_out"

    env = dict(os.environ)
    env["PYTHONPATH"] = (str(repo) + os.pathsep
                         + env.get("PYTHONPATH", ""))
    # Folk SVC OOM fix (2026-11-06): expandable segments.
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF",
                   "expandable_segments:True")

    def pipeline(audio: str, save_dir: Path, step: str) -> None:
        _run([_PYTHON, "-m", "preprocess.pipeline",
              "--audio_path", audio, "--save_dir", str(save_dir),
              "--language", "Mandarin", "--device", args.device,
              "--vocal_sep", "False",
              "--max_merge_duration", "30000",
              "--midi_transcribe", "False"], step, repo, env)

    # 1. Source prep: the dry vocal is already vocal-only.
    pipeline(args.source, prep_source, "prep_source")
    _require(prep_source / "vocal.wav", "prep_source")
    _require(prep_source / "vocal_f0.npy", "prep_source")
    # 2. Timbre prep: the reference is already clean vocal.
    pipeline(args.timbre, prep_timbre, "prep_timbre")
    _require(prep_timbre / "vocal.wav", "prep_timbre")
    _require(prep_timbre / "vocal_f0.npy", "prep_timbre")
    # 3. SVC: source performance -> timbre voice.
    svc_cmd = [_PYTHON, "-m", "cli.inference_svc",
               "--device", args.device,
               "--model_path", str(model_path),
               "--config", str(config),
               "--prompt_wav_path", str(prep_timbre / "vocal.wav"),
               "--prompt_f0_path", str(prep_timbre / "vocal_f0.npy"),
               "--target_wav_path", str(prep_source / "vocal.wav"),
               "--target_f0_path", str(prep_source / "vocal_f0.npy"),
               "--save_dir", str(svc_out),
               "--pitch_shift", str(args.pitch_shift),
               "--n_steps", str(args.n_steps),
               "--cfg", str(args.cfg)]
    if args.auto_shift:
        svc_cmd.append("--auto_shift")
    if args.fp16:
        svc_cmd.append("--fp16")
    _run(svc_cmd, "inference_svc", repo, env)
    _require(svc_out / "generated.wav", "inference_svc")

    shutil.copyfile(svc_out / "generated.wav", args.out)
    m = _measure(Path(args.out))
    print(json.dumps({"out": args.out, **m}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
