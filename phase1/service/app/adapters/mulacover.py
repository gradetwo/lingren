"""MuLaCover adapter (four-pipeline work, design section 5).

Runs the MuLaCover driver as a subprocess so model code (torch,
CUDA) stays out of the API process. One take = one process = one
seed (the same base seed for every segment).
Recipe (new-control, from the folk experiments): stretched x1.5
melody+chords MIDIs, tags, cfg_scale 2.5, temperature 1.0, topk 250.
The stretch's duration effect was falsified (outputs got shorter,
not longer); the recipe is kept because the combination measured
best (contour r 0.332, the series max), not because each part
works -- see the design, do not rewrite it as if it did.

Required environment:
  MULACOVER_REPO        MuLaCover checkout (e27fa7fa,
                        uv pip install -e '.[audio]' done)
  MULACOVER_MODEL_PATH  weights dir (MuLaCover + Qwen3-Embedding +
                        HeartCodec-oss, ~15.6 GB)
  MULACOVER_DEVICE      cuda:0 | ... (default cuda:0)

Voice: generation needs no voice (no reference-audio input).
needs_voice is False unless options.svc is on (section 6).

Chords: derived from the score by the design section 5.2 rule
(deterministic, no silent assumptions) unless options.chords
supplies an explicit table [{"bar": 0, "root": 0-11,
"quality": "major"|"minor"}]. Both the derived and the supplied
table are reported in adapter_info.

Segments: bars are cut at most 12 per segment (4/4 assumed -- the
score has no time-signature field, documented as a known limit);
segments are spliced by the driver (DIO crop + phase-vocoder
stretch, copied from seggen_splice.py). No per-segment seed
picking in v1 (single base seed straight through).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..schemas import PartIn
from ..score_converter import (
    PPQ, chords_to_midi_notes, derive_chords, score_lyrics_text,
    score_to_midi_notes, stretch_midi_notes, write_midi_file)
from .base import AdapterError, BaseAdapter

_DRIVER = Path(__file__).resolve().parent.parent / "mulacover_driver.py"

# Experiment defaults (design section 5.1/5.4).
DEFAULTS = {"cfg_scale": 2.5, "temperature": 1.0, "topk": 250}

MAX_BARS_PER_SEGMENT = 12
_BAR_TICKS = 4 * PPQ


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise AdapterError(f"missing environment variable {name}")
    return v


def _validate_chords_table(table, n_bars: int) -> list[dict]:
    """Validate an explicit options.chords table; covers every bar
    (missing bars fail loudly -- no silent carry-over here, the
    derivation rule already covers that case)."""
    if not isinstance(table, list) or not table:
        raise AdapterError("options.chords must be a non-empty list "
                           "of {bar, root, quality}")
    out = {}
    for entry in table:
        try:
            bar = int(entry["bar"])
            root = int(entry["root"])
            quality = str(entry["quality"])
        except (KeyError, TypeError, ValueError):
            raise AdapterError(
                "options.chords entries need {bar, root, quality}: "
                f"{entry!r}")
        if not 0 <= root <= 11:
            raise AdapterError(f"options.chords root out of range "
                               f"0-11: {entry!r}")
        if quality not in ("major", "minor"):
            raise AdapterError(f"options.chords quality must be "
                               f"'major'/'minor': {entry!r}")
        if bar in out:
            raise AdapterError(f"options.chords repeats bar {bar}")
        out[bar] = {"bar": bar, "root": root, "quality": quality}
    missing = [b for b in range(n_bars) if b not in out]
    if missing:
        raise AdapterError(
            f"options.chords misses bars {missing}; pass a table "
            "covering every bar or omit it to use derivation")
    return [out[b] for b in range(n_bars)]


def _slice_window(notes: list[tuple], w0: int, w1: int) -> list[tuple]:
    """Keep notes overlapping [w0, w1), truncate at the boundaries
    (same semantics as make_slice.slice_notes)."""
    out = []
    for (on, off, midi, vel, ch) in notes:
        if off <= w0 or on >= w1:
            continue
        out.append((max(on, w0) - w0, min(off, w1) - w0, midi, vel, ch))
    out.sort()
    return out


def _distribute_lines(lines: list[str], weights: list[int]) -> list[str]:
    """Split lyric lines across segments in proportion to each
    segment's note count (largest remainder, deterministic). Used
    only when options.mulacover_lyrics supplies a verbatim lyrics
    text for a multi-segment part."""
    total_w = sum(weights)
    if total_w == 0 or not lines:
        return ["\n".join(lines)] * len(weights)
    raw = [len(lines) * w / total_w for w in weights]
    counts = [int(x) for x in raw]
    leftover = len(lines) - sum(counts)
    order = sorted(range(len(weights)),
                   key=lambda i: (raw[i] - counts[i]), reverse=True)
    for i in order[:leftover]:
        counts[i] += 1
    out, pos = [], 0
    for c in counts:
        out.append("\n".join(lines[pos:pos + c]))
        pos += c
    return out


class MuLaCoverAdapter(BaseAdapter):
    name = "mulacover"

    def needs_voice(self, part: PartIn) -> bool:
        return bool((part.options or {}).get("svc", False))

    def synthesize(self, part: PartIn, voice: dict | None, seed: int,
                   out_path: Path, workdir: Path) -> tuple[Path, int, dict]:
        opts = part.options or {}
        tags = opts.get("tags")
        if not tags:
            raise AdapterError(
                "tags is required: MuLaCover needs a style-tags text "
                "and there is no generic default (the experiment "
                "tags are folk-A specific). Pass options.tags.")
        if not any(n.lyric not in ("", "<SP>") and n.midi > 0
                   for n in part.score.notes):
            raise AdapterError("score has no sung notes")
        if opts.get("svc", False):
            raise AdapterError(
                "options.svc is not wired yet (lands in step 5 of "
                "DESIGN-four-pipelines.md section 12); run with "
                "svc off for now")
        transpose = 12 if opts.get("transpose12", False) else 0
        beat = 60.0 / part.score.bpm
        bar_s = 4 * beat
        span_s = max(n.start_s + n.dur_s for n in part.score.notes)
        n_bars = max(1, int(span_s / bar_s)
                     + (1 if span_s % bar_s else 0))
        melody = score_to_midi_notes(part.score, transpose=transpose)
        if opts.get("chords") is not None:
            chords = _validate_chords_table(opts["chords"], n_bars)
            chords_source = "options"
        else:
            try:
                chords = derive_chords(part.score)
            except ValueError as e:
                raise AdapterError(str(e)) from e
            chords_source = "derived"
        chord_notes = chords_to_midi_notes(chords, part.score,
                                           transpose=transpose)

        # Segment lyrics: note-order lyrics per segment; a verbatim
        # override text is distributed across segments by note count.
        seg_notes = []
        for b0 in range(0, n_bars, MAX_BARS_PER_SEGMENT):
            b1 = min(b0 + MAX_BARS_PER_SEGMENT, n_bars)
            seg_notes.append([n for n in sorted(
                part.score.notes, key=lambda n: n.start_s)
                if b0 * bar_s - 1e-9 <= n.start_s < b1 * bar_s - 1e-9])
        override = opts.get("mulacover_lyrics")
        if override is not None:
            seg_lyrics = _distribute_lines(
                [ln for ln in str(override).splitlines()],
                [len([n for n in ns
                      if n.lyric not in ("", "<SP>") and n.midi > 0])
                 for ns in seg_notes])
        else:
            from ..schemas import ScoreIn as _ScoreIn
            seg_lyrics = []
            for ns in seg_notes:
                seg_lyrics.append(score_lyrics_text(_ScoreIn(
                    bpm=part.score.bpm, language=part.score.language,
                    notes=list(ns))))

        seg_dir = workdir / f"mc_{part.label}"
        seg_dir.mkdir(parents=True, exist_ok=True)
        tags_path = seg_dir / "tags.txt"
        tags_path.write_text(str(tags), encoding="utf-8")
        manifest_segments = []
        for i, b0 in enumerate(range(0, n_bars, MAX_BARS_PER_SEGMENT)):
            b1 = min(b0 + MAX_BARS_PER_SEGMENT, n_bars)
            w0, w1 = b0 * _BAR_TICKS, b1 * _BAR_TICKS
            mel_slice = _slice_window(melody, w0, w1)
            ch_slice = _slice_window(chord_notes, w0, w1)
            mel_path = seg_dir / f"seg{i}_melody.mid"
            ch_path = seg_dir / f"seg{i}_chords.mid"
            write_midi_file(mel_path,
                            stretch_midi_notes(mel_slice),
                            part.score.bpm, track_name="Lead vocal")
            write_midi_file(ch_path,
                            stretch_midi_notes(ch_slice),
                            part.score.bpm, track_name="Chords")
            lyrics_text = seg_lyrics[i]
            tag = f"[{part.label}]"
            if not lyrics_text.startswith("["):
                lyrics_text = tag + "\n" + lyrics_text + "\n"
            else:
                lyrics_text += "\n"
            lyrics_path = seg_dir / f"seg{i}_lyrics.txt"
            lyrics_path.write_text(lyrics_text, encoding="utf-8")
            n_sung = len([n for n in seg_notes[i]
                          if n.lyric not in ("", "<SP>") and n.midi > 0])
            manifest_segments.append({
                "melody_midi": str(mel_path), "chord_midi": str(ch_path),
                "lyrics": str(lyrics_path), "tags": str(tags_path),
                "slot": [round(b0 * bar_s, 4), round(b1 * bar_s, 4)],
                "bars": [b0, b1], "notes": n_sung})
        manifest = {"bpm": part.score.bpm, "bar_s": bar_s,
                    "span_s": span_s, "segments": manifest_segments}
        manifest_path = seg_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        cmd = [sys.executable, str(_DRIVER),
               "--repo", _env("MULACOVER_REPO"),
               "--model-path", _env("MULACOVER_MODEL_PATH"),
               "--device", os.environ.get("MULACOVER_DEVICE", "cuda:0"),
               "--seed", str(seed),
               "--temperature", str(opts.get(
                   "mulacover_temperature", DEFAULTS["temperature"])),
               "--topk", str(opts.get("mulacover_topk",
                                      DEFAULTS["topk"])),
               "--cfg-scale", str(opts.get(
                   "mulacover_cfg_scale", DEFAULTS["cfg_scale"])),
               "--manifest", str(manifest_path),
               "--out", str(out_path),
               "--workdir", str(workdir / f"mc_run_{part.label}")]
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
                "MuLaCover driver failed (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
        info = {"adapter": "mulacover", "seed": seed,
                "duration_s": summary.get("duration_s"),
                "peak": summary.get("peak"),
                "bars": n_bars,
                "segments": summary.get("segments"),
                "chords": chords, "chords_source": chords_source,
                "transpose12": bool(opts.get("transpose12", False)),
                "cfg_scale": opts.get("mulacover_cfg_scale",
                                      DEFAULTS["cfg_scale"]),
                "temperature": opts.get("mulacover_temperature",
                                        DEFAULTS["temperature"]),
                "topk": opts.get("mulacover_topk", DEFAULTS["topk"]),
                "svc": False}
        return out_path, int(summary.get("sr", 44100)), info
