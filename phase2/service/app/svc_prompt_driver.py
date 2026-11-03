#!/usr/bin/env python3
"""SVC genre-prompt driver for the 伶人 service.

Runs the SoulX-Singer-SVC reverse pipeline as subprocesses so model
code (torch, CUDA) stays out of the API process:

  1. preprocess the source performance (vocal_sep on/off) ->
     vocal.wav + vocal_f0.npy
  2. preprocess the timbre reference (vocal_sep off) ->
     vocal.wav + vocal_f0.npy
  3. cli.inference_svc (source -> timbre) -> generated.wav (24 kHz)
  4. preprocess generated.wav (midi_transcribe on) -> metadata.json

Step 4's metadata.json plus step 3's generated.wav ARE the SoulX
prompt pair. The direction matters and is fixed by this driver: the
source supplies the performance (genre, melody, phrasing) and the
timbre reference supplies only the voice. SVC preserves the source
performance and swaps only the timbre -- there is no style parameter
in its inputs -- so the forward direction (an operatic source swapped
to another timbre) stays operatic and is useless here. The reverse
direction (a real pop performance swapped into our timbre) is what
produces a pop-style prompt in our voice.

Flag names verified against upstream SoulX-Singer source
(cli/inference_svc.py, preprocess/pipeline.py), not from memory:
inference_svc takes --device --model_path --config --prompt_wav_path
--prompt_f0_path --target_wav_path --target_f0_path --save_dir
--auto_shift --pitch_shift --n_steps --cfg --fp16; pipeline takes
--audio_path --save_dir --language --device --vocal_sep (string
"True"/"False") --max_merge_duration (ms) --midi_transcribe (string
"True"/"False").

On success the driver copies generated.wav -> --out-wav and
metadata.json -> --out-meta ONLY after all steps and the meta
validation pass, then prints one JSON summary line as the last stdout
line. Any step failing (non-zero exit, missing output, meta failing
validation) exits non-zero with the step's stderr tail on stderr, and
no prompt files are copied: the worker turns that into a failed job
and serves no half-finished prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Keys a SoulX prompt meta segment must carry to be usable as a
# prompt pair (the pilot's expected key set also has index, language
# and note_type; those are recorded, not required).
_META_REQUIRED_KEYS = ("text", "phoneme", "note_pitch", "duration",
                       "f0", "time")


def _resolve(path: str, repo: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else repo / p


def _run(cmd: list[str], step: str, repo: Path, env: dict) -> None:
    proc = subprocess.run(cmd, cwd=str(repo), env=env,
                          capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        print(f"svc-prompt driver: step {step} failed "
              f"(exit {proc.returncode}): {tail}", file=sys.stderr)
        raise SystemExit(proc.returncode or 1)


def _require(path: Path, step: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        print(f"svc-prompt driver: step {step} produced no output: "
              f"{path}", file=sys.stderr)
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
                    help="source performance audio (pop singing)")
    ap.add_argument("--timbre", required=True,
                    help="timbre reference audio (our voice)")
    ap.add_argument("--vocal-sep", default="True",
                    help='string "True"/"False": separate the source '
                         "mix into vocal + dereverb before SVC")
    ap.add_argument("--language", default="Mandarin")
    ap.add_argument("--max-merge-duration", type=int, default=30000)
    ap.add_argument("--n-steps", type=int, default=32)
    ap.add_argument("--cfg", type=float, default=3.0)
    ap.add_argument("--pitch-shift", type=int, default=0)
    ap.add_argument("--auto-shift", action="store_true")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--out-wav", required=True)
    ap.add_argument("--out-meta", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"svc-prompt driver: repo not a directory: {repo}",
              file=sys.stderr)
        return 2
    model_path = _resolve(args.model_path, repo)
    config = _resolve(args.config, repo)
    if not model_path.is_file():
        print(f"svc-prompt driver: SVC weights not found: "
              f"{model_path} (public HF download "
              f"Soul-AILab/SoulX-Singer model-svc.pt; a gated or "
              f"missing download fails here, loudly)",
              file=sys.stderr)
        return 2
    if not config.is_file():
        print(f"svc-prompt driver: config not found: {config}",
              file=sys.stderr)
        return 2
    for label, path in (("source", args.source), ("timbre", args.timbre)):
        if not Path(path).is_file():
            print(f"svc-prompt driver: {label} audio not found: "
                  f"{path}", file=sys.stderr)
            return 2

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    prep_source = workdir / "prep_source"
    prep_timbre = workdir / "prep_timbre"
    svc_out = workdir / "svc_out"
    prompt_build = workdir / "prompt_build"

    env = dict(os.environ)
    env["PYTHONPATH"] = (str(repo) + os.pathsep
                         + env.get("PYTHONPATH", ""))

    def pipeline(audio: str, save_dir: Path, vocal_sep: str,
                 midi: str, step: str) -> None:
        _run([sys.executable, "-m", "preprocess.pipeline",
              "--audio_path", audio, "--save_dir", str(save_dir),
              "--language", args.language, "--device", args.device,
              "--vocal_sep", vocal_sep,
              "--max_merge_duration", str(args.max_merge_duration),
              "--midi_transcribe", midi], step, repo, env)

    # 1. Source prep: separated vocal + f0 when the source is a mix.
    pipeline(args.source, prep_source, args.vocal_sep, "False",
             "prep_source")
    _require(prep_source / "vocal.wav", "prep_source")
    _require(prep_source / "vocal_f0.npy", "prep_source")
    # 2. Timbre prep: the reference is already clean vocal.
    pipeline(args.timbre, prep_timbre, "False", "False",
             "prep_timbre")
    _require(prep_timbre / "vocal.wav", "prep_timbre")
    _require(prep_timbre / "vocal_f0.npy", "prep_timbre")
    # 3. SVC: source performance -> timbre voice.
    svc_cmd = [sys.executable, "-m", "cli.inference_svc",
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
    # 4. Prompt build: transcribe the SVC output into a SoulX meta.
    pipeline(str(svc_out / "generated.wav"), prompt_build, "False",
             "True", "prompt_build")
    meta_path = prompt_build / "metadata.json"
    _require(meta_path, "prompt_build")
    try:
        segments = json.loads(meta_path.read_text())
    except Exception as e:
        print(f"svc-prompt driver: prompt meta unreadable: {e}",
              file=sys.stderr)
        return 3
    if not isinstance(segments, list) or not segments:
        print("svc-prompt driver: prompt meta is empty (no segments)",
              file=sys.stderr)
        return 3
    for seg in segments:
        missing = [k for k in _META_REQUIRED_KEYS if k not in seg]
        if missing:
            print(f"svc-prompt driver: prompt meta segment missing "
                  f"keys {missing}", file=sys.stderr)
            return 3

    # Only now, on full success, publish the prompt pair.
    shutil.copyfile(svc_out / "generated.wav", args.out_wav)
    shutil.copyfile(meta_path, args.out_meta)
    m = _measure(Path(args.out_wav))
    print(json.dumps({"out_wav": args.out_wav,
                      "out_meta": args.out_meta,
                      "segments": len(segments), **m}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
