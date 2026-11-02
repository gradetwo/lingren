"""Transcription jobs (MuScriptor, design section 12).

A transcription job is NOT a synthesis job and does not reuse the
synthesis adapter interface: its input is an audio file (not a score +
voice + seed) and its output is notes + MIDI (not a wav take). It has
no parts, no takes and no takes gate.

Plumbing mirrors the synthesis side on purpose: same jobs directory
layout, same GET /v1/jobs/{id} status endpoint, same receipt.json
convention, same loud-failure rule (a failed job keeps its error in
the record; an empty transcription of non-silent audio is a completed
job with an "empty_transcription" warning, not a silent success and
not a fake failure).

Worker separation: transcription runs on its own queue + thread so a
long transcription never blocks synthesis job status, but every model
execution (synthesis adapter calls AND transcription runs) holds the
shared MODEL_LOCK from jobs.py, so two models never sit on the GPU /
MPS at the same time. The lock is an internal serialization guarantee;
it changes no synthesize endpoint response shape.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .jobs import MODEL_LOCK

# Exact MT3_FULL_PLUS group names (muscriptor/tokenizer/mt3.py,
# MT3_FULL_PLUS_GROUP_NAMES). The service accepts exact names only:
# upstream also accepts unambiguous abbreviations, but the service
# narrows deliberately so a typo can never silently resolve to the
# wrong instrument. Unknown names fail at submission with this list.
INSTRUMENT_GROUPS = (
    "acoustic_piano", "electric_piano", "chromatic_percussion", "organ",
    "acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar",
    "acoustic_bass", "electric_bass", "violin", "viola", "cello",
    "contrabass", "orchestral_harp", "timpani", "string_ensemble",
    "synth_strings", "voice", "orchestra_hit", "trumpet", "trombone",
    "tuba", "french_horn", "brass_section", "soprano_and_alto_sax",
    "tenor_sax", "baritone_sax", "oboe", "english_horn", "bassoon",
    "clarinet", "flutes", "synth_lead", "synth_pad", "drums",
)

MODEL_SIZES = ("small", "medium", "large")
DETECT_TEMPO_VALUES = ("true", "false", "best-effort")

# RMS below this is treated as silence for the empty-transcription
# warning (a 0-note receipt for truly silent input is unremarkable).
_SILENCE_RMS_DB = -60.0

_DRIVER = Path(__file__).resolve().parent / "muscriptor_driver.py"


class TranscribeError(Exception):
    pass


class TranscribeIn(BaseModel):
    model: str | None = Field(default=None,
                              description="None = defer to "
                              "MUSCRIPTOR_MODEL env, else 'small'. "
                              "The request field accepts only the three "
                              "keywords; local safetensors paths go via "
                              "MUSCRIPTOR_MODEL, not here.")
    instruments: list[str] | None = Field(
        default=None,
        description="Exact MT3_FULL_PLUS group names, or None = Automatic "
                    "(the model chooses). A given list is a hard mask: "
                    "instruments outside it are never decoded, not "
                    "filtered out afterwards.")
    bpm: float | None = None
    beats_per_bar: int | None = None
    first_downbeat: float | None = None
    quantize: bool = False
    grid: int | None = None
    detect_tempo: str = "best-effort"


def validate_transcribe(req: TranscribeIn) -> None:
    if req.model is not None and req.model not in MODEL_SIZES:
        raise TranscribeError(
            f"unknown model {req.model!r}; expected one of "
            f"{list(MODEL_SIZES)} (local safetensors paths go via "
            f"MUSCRIPTOR_MODEL)")
    if req.detect_tempo not in DETECT_TEMPO_VALUES:
        raise TranscribeError(
            f"unknown detect_tempo {req.detect_tempo!r}; expected one of "
            f"{list(DETECT_TEMPO_VALUES)}")
    if req.instruments:
        unknown = [n for n in req.instruments
                   if n not in INSTRUMENT_GROUPS]
        if unknown:
            raise TranscribeError(
                f"unknown instrument(s) {unknown}; expected exact "
                f"MT3_FULL_PLUS group names: {list(INSTRUMENT_GROUPS)}")


def parse_driver_line(line: str) -> dict | None:
    """One stdout line from the driver: progress or summary JSON.

    Returns the parsed dict, or None for non-JSON lines (the driver
    prints nothing else on stdout, but a stray line must never kill
    the worker).
    """
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _driver_cmd() -> list[str]:
    override = os.environ.get("MUSCRIPTOR_DRIVER_CMD")
    if override:
        return shlex.split(override)
    return [sys.executable, str(_DRIVER)]


class TranscriptionWorker:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        if not self._worker.is_alive():
            self._worker.start()

    def create_job(self, audio_bytes: bytes, filename: str,
                   req: TranscribeIn) -> dict:
        job_id = uuid.uuid4().hex[:16]
        suffix = Path(filename).suffix or ".wav"
        job = {"job_id": job_id, "kind": "transcribe",
               "status": "queued", "error": None,
               "created_at": time.time(), "updated_at": time.time(),
               "progress": {"completed_chunks": 0, "total_chunks": None},
               "audio_duration_s": None, "notes_total": None,
               "request": {"filename": filename,
                           **req.model_dump()}}
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / f"input{suffix}").write_bytes(audio_bytes)
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job

    def get_job(self, job_id: str) -> dict | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _set(self, job: dict, **kw):
        with self.lock:
            job.update(kw)
            job["updated_at"] = time.time()

    def _loop(self):
        while True:
            job_id = self.queue.get()
            try:
                self._run(job_id)
            except Exception as e:  # never kill the worker thread
                job = self.get_job(job_id)
                if job is not None:
                    self._set(job, status="failed",
                              error=f"worker: {e}")
            finally:
                self.queue.task_done()

    def _run(self, job_id: str):
        job = self.get_job(job_id)
        req = TranscribeIn(**{k: v for k, v in job["request"].items()
                              if k != "filename"})
        job_dir = self.jobs_dir / job_id
        inputs = sorted(job_dir.glob("input.*"))
        if not inputs:
            self._set(job, status="failed",
                      error="transcription input audio missing")
            return
        audio_path = inputs[0]
        midi_path = job_dir / "transcription.mid"
        notes_path = job_dir / "notes.json"
        cmd = _driver_cmd() + [
            "--audio", str(audio_path),
            "--model", req.model,
            "--device", os.environ.get("MUSCRIPTOR_DEVICE", "auto"),
            "--instruments",
            ",".join(req.instruments) if req.instruments else "",
            "--detect-tempo", req.detect_tempo,
            "--out-midi", str(midi_path),
            "--out-notes", str(notes_path),
        ]
        dtype = os.environ.get("MUSCRIPTOR_DTYPE")
        if dtype:
            cmd += ["--dtype", dtype]
        if req.bpm is not None:
            cmd += ["--bpm", str(req.bpm)]
        if req.beats_per_bar is not None:
            cmd += ["--beats-per-bar", str(req.beats_per_bar)]
        if req.first_downbeat is not None:
            cmd += ["--first-downbeat", str(req.first_downbeat)]
        if req.quantize:
            cmd += ["--quantize"]
        if req.grid is not None:
            cmd += ["--grid", str(req.grid)]
        self._set(job, status="running")
        started = time.time()
        summary = None
        stderr_tail = ""
        # The model lock serializes GPU/MPS execution with the
        # synthesis worker; the queue separation only keeps status
        # and admission independent.
        with MODEL_LOCK:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                obj = parse_driver_line(line)
                if obj is None:
                    continue
                if obj.get("type") == "progress":
                    self._set(job, progress={
                        "completed_chunks": obj.get("completed", 0),
                        "total_chunks": obj.get("total")})
                elif obj.get("type") == "summary":
                    summary = obj
            _, stderr = proc.communicate()
            stderr_tail = (stderr or "")[-2000:]
        elapsed = time.time() - started
        if proc.returncode != 0 or summary is None:
            err = (f"muscriptor driver failed (exit "
                   f"{proc.returncode}): {stderr_tail}")
            self._set(job, status="failed", error=err)
            (job_dir / "receipt.json").write_text(json.dumps(
                {"job_id": job_id, "status": "failed", "error": err},
                indent=2))
            return
        try:
            notes = json.loads(notes_path.read_text())
        except Exception as e:
            err = f"transcription notes unreadable: {e}"
            self._set(job, status="failed", error=err)
            return
        duration = summary.get("duration_s")
        rtf = (duration / elapsed) if duration and elapsed > 0 else None
        warnings = []
        rms = summary.get("input_rms_db")
        if len(notes) == 0 and rms is not None \
                and rms > _SILENCE_RMS_DB:
            warnings.append("empty_transcription")
        receipt = {"job_id": job_id, "status": "completed",
                   "model": req.model,
                   "device": os.environ.get("MUSCRIPTOR_DEVICE", "auto"),
                   "instruments_requested": req.instruments,
                   "duration_s": duration,
                   "elapsed_s": round(elapsed, 3),
                   "rtf": round(rtf, 4) if rtf is not None else None,
                   "notes_total": len(notes),
                   "beat_grid": summary.get("beat_grid"),
                   "input_rms_db": rms,
                   "warnings": warnings,
                   "notes": notes}
        (job_dir / "receipt.json").write_text(json.dumps(receipt,
                                                          indent=2))
        self._set(job, status="completed", audio_duration_s=duration,
                  notes_total=len(notes))
