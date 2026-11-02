"""MuScriptor transcription driver (design section 12).

Standalone script: the API process never imports torch. It mirrors
upstream's TranscriptionModel.transcribe_and_postprocess body (public
methods only) with one addition: ProgressEvent lines are printed as
JSON so the worker can report chunk progress while the run is going.

Stdout contract (one JSON object per line):
  {"type": "progress", "completed": c, "total": t}
  {"type": "summary", "duration_s": ..., "notes_total": ...,
   "input_rms_db": ..., "beat_grid": {...}|null}
Anything else on stdout is a bug; errors go to stderr and exit != 0.
The worker treats a non-zero exit or a missing summary line as a loud
failure and keeps the stderr tail in the job record (including
upstream's ModelDownloadError text when HF auth is missing).

Notes JSON: [{"instrument", "pitch", "start_s", "end_s"}] in model
time, unquantized. Quantization (when requested) applies to the MIDI
bytes only, never to the receipt notes -- upstream's own rule that
quantizing is for sheet music, not for listening.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def events_to_notes(events) -> list[dict]:
    """Pair NoteStart/NoteEnd events (by start index) into notes.

    Pure function over duck-typed events, so it is unit-testable
    without torch: each start event needs .pitch, .start_time, .index
    and .instrument; each end event needs .end_time and
    .start_event_index. A start never closed by the stream is dropped
    (upstream guarantees exactly one end per start, so a drop means
    a truncated stream, and a truncated stream must not invent an
    end time).
    """
    open_notes: dict[int, dict] = {}
    notes: list[dict] = []
    for ev in events:
        kind = type(ev).__name__
        if kind == "NoteStartEvent":
            open_notes[ev.index] = {
                "instrument": ev.instrument,
                "pitch": int(ev.pitch),
                "start_s": round(float(ev.start_time), 4),
            }
        elif kind == "NoteEndEvent":
            start = open_notes.pop(ev.start_event_index, None)
            if start is not None:
                start["end_s"] = round(float(ev.end_time), 4)
                notes.append(start)
    notes.sort(key=lambda n: (n["start_s"], n["instrument"], n["pitch"]))
    return notes


def _input_rms_db(audio_path: Path) -> float | None:
    try:
        import soundfile as sf

        data, _sr = sf.read(str(audio_path), dtype="float32")
        import numpy as np

        mono = data[:, 0] if data.ndim == 2 else data
        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
        return round(20 * math.log10(rms), 2) if rms > 0 else -120.0
    except Exception:
        return None


def _duration_s(audio_path: Path) -> float | None:
    try:
        import soundfile as sf

        return round(float(sf.info(str(audio_path)).duration), 4)
    except Exception:
        return None


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

    try:
        from muscriptor.events import NoteEndEvent, NoteStartEvent
        from muscriptor.transcription_model import TranscriptionModel
        from muscriptor.utils.midi import ExportOptions, resolve_grid
    except ImportError as e:
        print(f"muscriptor package not importable: {e}\n"
              f"Install it (pip install muscriptor, or uvx muscriptor) "
              f"into the driver environment, or point "
              f"MUSCRIPTOR_DRIVER_CMD at an environment that has it.",
              file=sys.stderr)
        return 2

    instruments = [n for n in args.instruments.split(",") if n] or None
    detect_tempo = {"true": True, "false": False,
                    "best-effort": "best-effort"}[args.detect_tempo]
    export = ExportOptions(
        bpm=args.bpm, beats_per_bar=args.beats_per_bar,
        first_downbeat=args.first_downbeat, quantize=args.quantize,
        subdivision=args.grid)

    try:
        # Upstream CLI maps "auto" to None before load_model
        # (main.py:434); torch.device("auto") is rejected, so the
        # driver must do the same mapping, not pass "auto" through.
        device = None if args.device == "auto" else args.device
        model = TranscriptionModel.load_model(
            weights_path=args.model, device=device,
            dtype=args.dtype)
    except Exception as e:
        # Includes upstream ModelDownloadError (missing HF auth /
        # unaccepted CC BY-NC license): surfaced verbatim, not
        # translated, not swallowed.
        print(f"model load failed: {e}", file=sys.stderr)
        return 3

    audio = Path(args.audio)
    try:
        beat_grid = model.detect_beat_grid_for(audio, detect_tempo)
        events = []
        for ev in model.transcribe(
                audio, instruments=instruments):
            if type(ev).__name__ == "ProgressEvent":
                print(json.dumps({"type": "progress",
                                  "completed": ev.completed,
                                  "total": ev.total}))
                sys.stdout.flush()
            else:
                events.append(ev)
        onsets = [ev.start_time for ev in events
                  if isinstance(ev, NoteStartEvent)]
        if beat_grid is not None:
            beat_grid = beat_grid.with_onset_delay(onsets)
        midi_bytes = model.events_to_midi_bytes(
            iter(events), beat_grid=beat_grid, export=export)
        if beat_grid is None and export.bpm is None:
            resolved = None
        else:
            resolved = resolve_grid(beat_grid, export, onsets)
    except Exception as e:
        print(f"transcription failed: {e}", file=sys.stderr)
        return 4

    notes = events_to_notes(events)
    Path(args.out_midi).write_bytes(midi_bytes)
    Path(args.out_notes).write_text(json.dumps(notes, indent=2))
    grid_out = None
    if resolved is not None:
        grid_out = {"bpm": getattr(resolved, "bpm", None),
                    "beats_per_bar": getattr(resolved, "beats_per_bar",
                                             None),
                    "first_downbeat": getattr(resolved, "first_downbeat",
                                              None)}
    print(json.dumps({"type": "summary",
                      "duration_s": _duration_s(audio),
                      "notes_total": len(notes),
                      "input_rms_db": _input_rms_db(audio),
                      "beat_grid": grid_out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
