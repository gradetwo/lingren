"""YuE2 adapter (four-pipeline work, design section 4).

Runs the YuE2 driver as a subprocess so model code (torch, CUDA)
stays out of the API process. One take = one process = one seed.
Recipe (write-once defaults from the folk experiments):
native ABC (Vocal +12 semitones, Ins rests by default), cot=melody,
cfg 1.0, temp 1.0, top_p 0.95, top_k 100, repetition_penalty 1.2.
The driver separates the dry vocal with Demucs 4.1.0 htdemucs; the
adapter output is the dry vocal, never the full mix.

Required environment:
  YUE2_REPO             YuE checkout (yue2-v0.1.6, uv pip install . done)
  YUE2_MODEL            HF repo (default m-a-p/YuE2-3B)
  YUE2_MODEL_REVISION   weights revision (default 8f24312f...)
  YUE2_VAE_REVISION     VAE revision (default 152733a1...)
  YUE2_DEVICE           cuda | cpu (default cuda)

Voice: generation needs no voice (YuE2 has no reference-audio
input). needs_voice is False unless options.svc is on (section 6:
the SVC post-process needs the target voice's reference audio).

Known limits (design section 4.3/4.7, written here not hidden):
single-call generation of the whole part; parts longer than ~108 s
of output have unmeasured duration fidelity; tail/segment-silence
noise was heard in the folk experiments (Demucs mis-separation or
VAE noise, seed-dependent).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..schemas import PartIn
from ..score_converter import score_lyrics_text, score_to_yue2_abc
from .base import AdapterError, BaseAdapter

_DRIVER = Path(__file__).resolve().parent.parent / "yue2_driver.py"

# Experiment defaults (design section 4.1/4.5). No "all-on" combo:
# the combined-control group measured worse than native ABC alone.
DEFAULTS = {"cot": "melody", "cfg_scale": 1.0, "temperature": 1.0,
            "top_p": 0.95, "top_k": 100, "repetition_penalty": 1.2}


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise AdapterError(f"missing environment variable {name}")
    return v


def _has_sung_notes(part: PartIn) -> bool:
    return any(n.lyric not in ("", "<SP>") and n.midi > 0
               for n in part.score.notes)


class YuE2Adapter(BaseAdapter):
    name = "yue2"

    def needs_voice(self, part: PartIn) -> bool:
        return bool((part.options or {}).get("svc", False))

    def synthesize(self, part: PartIn, voice: dict | None, seed: int,
                   out_path: Path, workdir: Path) -> tuple[Path, int, dict]:
        opts = part.options or {}
        style = opts.get("yue2_style")
        if not style:
            raise AdapterError(
                "yue2_style is required: YuE2 needs a style text "
                "and there is no generic default (the experiment "
                "style string is folk-A specific). Pass "
                "options.yue2_style.")
        if not _has_sung_notes(part):
            raise AdapterError("score has no sung notes")
        if opts.get("svc", False):
            raise AdapterError(
                "options.svc is not wired yet (lands in step 5 of "
                "DESIGN-four-pipelines.md section 12); run with "
                "svc off for now")
        abc = score_to_yue2_abc(
            part.score, key=str(opts.get("yue2_key", "C")),
            transpose=12, ins_abc=opts.get("ins_abc"),
            section_label=part.label)
        lyrics = opts.get("yue2_lyrics") or score_lyrics_text(part.score)
        abc_path = workdir / f"yue2_{part.label}.abc"
        lyrics_path = workdir / f"yue2_{part.label}_lyrics.txt"
        abc_path.write_text(abc, encoding="utf-8")
        lyrics_path.write_text(lyrics, encoding="utf-8")
        n_bars = max(1, int((max(n.start_s + n.dur_s
                                for n in part.score.notes)
                             / (4 * 60.0 / part.score.bpm)) + 0.999))
        cmd = [sys.executable, str(_DRIVER),
               "--repo", _env("YUE2_REPO"),
               "--model", os.environ.get("YUE2_MODEL", "m-a-p/YuE2-3B"),
               "--model-revision",
               os.environ.get(
                   "YUE2_MODEL_REVISION",
                   "8f24312f187763b9854adeec87e437e2b03bbff1"),
               "--vae-revision",
               os.environ.get(
                   "YUE2_VAE_REVISION",
                   "152733a19ad43aa67e367f9b5503ef8075bb5126"),
               "--device", os.environ.get("YUE2_DEVICE", "cuda"),
               "--abc", str(abc_path), "--lyrics", str(lyrics_path),
               "--style", str(style),
               "--cot", str(opts.get("yue2_cot", DEFAULTS["cot"])),
               "--seed", str(seed),
               "--cfg-scale", str(opts.get(
                   "yue2_cfg_scale", DEFAULTS["cfg_scale"])),
               "--temperature", str(opts.get(
                   "yue2_temperature", DEFAULTS["temperature"])),
               "--top-p", str(opts.get("yue2_top_p", DEFAULTS["top_p"])),
               "--top-k", str(opts.get("yue2_top_k", DEFAULTS["top_k"])),
               "--repetition-penalty", str(opts.get(
                   "yue2_repetition_penalty",
                   DEFAULTS["repetition_penalty"])),
               "--out", str(out_path),
               "--workdir", str(workdir / f"yue2_run_{part.label}")]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
        summary = None
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if proc.returncode != 0 or summary is None:
            raise AdapterError(
                "YuE2 driver failed (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
        info = {"adapter": "yue2", "seed": seed,
                "duration_s": summary.get("duration_s"),
                "gen_duration_s": summary.get("gen_duration_s"),
                "bars": n_bars,
                "notes": len(part.score.notes),
                "transpose_semitones": 12,
                "cot": str(opts.get("yue2_cot", DEFAULTS["cot"])),
                "cfg_scale": opts.get("yue2_cfg_scale",
                                      DEFAULTS["cfg_scale"]),
                "temperature": opts.get("yue2_temperature",
                                        DEFAULTS["temperature"]),
                "top_p": opts.get("yue2_top_p", DEFAULTS["top_p"]),
                "top_k": opts.get("yue2_top_k", DEFAULTS["top_k"]),
                "repetition_penalty": opts.get(
                    "yue2_repetition_penalty",
                    DEFAULTS["repetition_penalty"]),
                "svc": False}
        return out_path, int(summary.get("sr", 44100)), info
