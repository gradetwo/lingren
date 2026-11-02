"""VocalRender adapter (Phase 2).

Runs the VR driver as a subprocess so model code (torch, CUDA) stays
out of the API process. One take = one process = one seed, using the
patched CLI surface (patches/03: --seed). The label entry is built by
score_converter.vr_label_entry with <NOTE_4> denominator tokens
(patches/05); durations outside the 0.125-6.0 beat token range are
rejected here rather than silently quantized to the nearest token.

Required environment:
  VR_REPO       VocalRender checkout (patch 03 applied)
  VR_CKPT_DIR   checkpoint dir passed as --ckpt_dir (the "VocalRender"
                subdir of the pymaster/VocalRender snapshot)

Voice requirements: only the reference audio is needed (it becomes
--prompt_audio). VR wants a 2-8 s clean singing prompt; the adapter
records the prompt duration in the receipt info and flags it when it
falls outside 2-8 s, but does not refuse to run -- the caller chose
the prompt, and a hard refusal would turn a warning into an outage.
SoulX prompt_meta, when present on the same voice, is ignored by this
adapter.

Timing: VocalRender decides its own timing (Phase 0: total -2%, per
syllable deviation up to -560 ms). The receipt alignment's measured
onsets ARE the placement data; Yeban must place by measured onset,
never by the score clock. SP (rest) entries carry no sung onset and
are marked ignored=True with measured_onset_s=None in jobs.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import soundfile as sf

from ..schemas import PartIn
from ..score_converter import NOTE_TOKENS, vr_label_entry
from .base import AdapterError, BaseAdapter

_DRIVER = Path(__file__).resolve().parent.parent / "vr_driver.py"

# Beat range covered by the VR note-token table (patches/05).
_MIN_BEATS = min(beats for beats, _ in NOTE_TOKENS)
_MAX_BEATS = max(beats for beats, _ in NOTE_TOKENS)


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise AdapterError(f"missing environment variable {name}")
    return v


def _validate_note_range(part: PartIn) -> None:
    """Reject notes the token table cannot represent exactly-ish.

    Quantizing a 7-beat note to <NOTE_DOT_1> (6 beats) would silently
    shorten the lyric by a beat; failing loudly is the patch-05 rule
    ("caller should reject overlong notes").
    """
    beat = 60.0 / part.score.bpm
    for note in part.score.notes:
        beats = note.dur_s / beat
        if beats < _MIN_BEATS - 1e-9 or beats > _MAX_BEATS + 1e-9:
            raise AdapterError(
                f"note {note.lyric!r} lasts {beats:.3f} beats, outside "
                f"the VR token range {_MIN_BEATS}-{_MAX_BEATS} beats; "
                "split or shorten the note")


class VocalRenderAdapter(BaseAdapter):
    name = "vocalrender"

    def synthesize(self, part: PartIn, voice: dict, seed: int,
                   out_path: Path, workdir: Path) -> tuple[Path, int, dict]:
        _validate_note_range(part)
        prompt_wav = voice.get("audio_path")
        if not prompt_wav or not Path(prompt_wav).is_file():
            raise AdapterError(
                "voice has no reference audio: POST /v1/voices must "
                "include the prompt audio file (VocalRender uses it as "
                "--prompt_audio)")
        item_name = f"lingren_{part.label}"
        entry = vr_label_entry(part.score, item_name)
        label_path = workdir / f"vr_label_{part.label}.json"
        label_path.write_text(json.dumps([entry]))
        try:
            prompt_info = sf.info(str(prompt_wav))
            prompt_dur = round(float(prompt_info.duration), 3)
        except Exception:
            prompt_dur = None
        prompt_warn = prompt_dur is not None and not (2.0 <= prompt_dur
                                                      <= 8.0)
        opts = part.options or {}
        cmd = [sys.executable, str(_DRIVER),
               "--repo", _env("VR_REPO"),
               "--ckpt-dir", _env("VR_CKPT_DIR"),
               "--label-json", str(label_path),
               "--item-name", item_name,
               "--prompt-audio", str(prompt_wav),
               "--out", str(out_path),
               "--seed", str(seed)]
        if opts.get("temperature") is not None:
            cmd += ["--temperature", str(opts["temperature"])]
        if opts.get("fsq_temperature") is not None:
            cmd += ["--fsq-temperature", str(opts["fsq_temperature"])]
        if opts.get("max_len") is not None:
            cmd += ["--max-len", str(opts["max_len"])]
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
                "VocalRender driver failed (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
        info = {"adapter": "vocalrender", "seed": seed,
                "duration_s": summary.get("duration_s"),
                "label_item": item_name,
                "note_tokens": entry["note"],
                "prompt_duration_s": prompt_dur,
                "prompt_duration_warning": prompt_warn,
                "timing_note": "VR self-timing: place by measured "
                               "onset in the receipt alignment, not "
                               "by the score clock"}
        return out_path, int(summary.get("sr", 48000)), info
