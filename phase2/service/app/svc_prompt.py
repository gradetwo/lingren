"""SVC genre-prompt jobs: build a SoulX prompt pair from a pop
performance in our timbre, BEFORE singing with SoulX.

A job is NOT a synthesis job and does not reuse the synthesis adapter
interface: its inputs are two audio files (a source performance and a
timbre reference) and its output is a SoulX prompt pair (wav + meta),
not a wav take. It has no parts, no takes and no takes gate.

Direction (fixed, see app/svc_prompt_driver.py): the source supplies
the performance -- genre, melody, phrasing -- and the timbre
reference supplies only the voice. SVC preserves the source
performance and swaps only the timbre; its inputs carry no style
parameter, so the forward direction (an operatic source swapped to
another timbre) stays operatic and is useless. The reverse direction
(a real pop performance swapped into our timbre) is what produces a
pop-style prompt in our voice. The genre must come from a real
performance; no parameter invents one.

Plumbing mirrors the transcription side on purpose: same jobs
directory layout, same GET /v1/jobs/{id} status endpoint, same
receipt.json convention, same loud-failure rule (a failed job keeps
its error in the record, and prompt files are published into the job
directory ONLY on full driver success, so a failure can never serve
a half-finished prompt).

Worker separation: SVC prompt jobs run on their own queue + thread so
a long conversion never blocks synthesis or transcription job status,
but every model execution holds the shared MODEL_LOCK from jobs.py,
so two models never sit on the GPU / MPS at the same time.
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

# cli.inference_svc documented ranges (upstream README): n_steps
# 16-64, pitch_shift -12..+12 semitones. cfg has no documented range;
# only positivity is enforced.
_N_STEPS_MIN, _N_STEPS_MAX = 16, 64
_PITCH_SHIFT_MIN, _PITCH_SHIFT_MAX = -12, 12
# Near-full-scale threshold, pilot convention (sf float32 read of an
# int16 wav tops at 32767/32768 = 0.99997; the pilot counted >= 0.9999).
_CLIP_THRESHOLD = 0.9999

_DRIVER = Path(__file__).resolve().parent / "svc_prompt_driver.py"

# Meta keys a usable SoulX prompt segment must carry (same set the
# driver enforces; the worker re-checks the published files).
_META_REQUIRED_KEYS = ("text", "phoneme", "note_pitch", "duration",
                       "f0", "time")


class SvcPromptError(Exception):
    pass


class SvcPromptIn(BaseModel):
    vocal_sep: bool = Field(
        default=True,
        description="True = separate the source mix into vocal + "
                    "dereverb before SVC (pilot: True for a mixed "
                    "preview, False for an already clean vocal).")
    n_steps: int = Field(default=32,
                         description="SVC diffusion steps (16-64).")
    cfg: float = Field(default=3.0,
                       description="SVC guidance scale, must be > 0.")
    pitch_shift: int = Field(default=0,
                             description="SVC output transposition in "
                                         "semitones (-12..+12).")
    auto_shift: bool = Field(default=False,
                             description="Pass --auto_shift to SVC "
                                         "(default off: no-compensation rule).")
    fp16: bool = Field(default=True,
                       description="Pass --fp16 to SVC (pilot: on).")
    language: str = Field(default="Mandarin",
                          description="Language for both preprocess "
                                      "passes.")
    max_merge_duration: int = Field(
        default=30000,
        description="Preprocess max merged segment duration in ms.")


def validate_svc_prompt(req: SvcPromptIn) -> None:
    if not (_N_STEPS_MIN <= req.n_steps <= _N_STEPS_MAX):
        raise SvcPromptError(
            f"n_steps {req.n_steps} outside "
            f"{_N_STEPS_MIN}..{_N_STEPS_MAX}")
    if not (req.cfg > 0):
        raise SvcPromptError(f"cfg {req.cfg} must be > 0")
    if not (_PITCH_SHIFT_MIN <= req.pitch_shift <= _PITCH_SHIFT_MAX):
        raise SvcPromptError(
            f"pitch_shift {req.pitch_shift} outside "
            f"{_PITCH_SHIFT_MIN}..{_PITCH_SHIFT_MAX}")
    if not req.language:
        raise SvcPromptError("language must not be empty")
    if req.max_merge_duration <= 0:
        raise SvcPromptError(
            f"max_merge_duration {req.max_merge_duration} must be > 0")


def _driver_cmd() -> list[str]:
    override = os.environ.get("SVC_PROMPT_DRIVER_CMD")
    if override:
        return shlex.split(override)
    return [sys.executable, str(_DRIVER)]


def _measure_wav(path: Path) -> dict:
    import soundfile as sf
    info = sf.info(str(path))
    data, _ = sf.read(str(path), dtype="float32")
    peak = float(max(abs(data))) if data.size else 0.0
    clipped = int(sum(1 for x in data.flat if abs(x) >= _CLIP_THRESHOLD))
    return {"sample_rate": int(info.samplerate),
            "duration_s": round(float(info.duration), 4),
            "peak": round(peak, 6), "clipped_samples": clipped}


class SvcPromptWorker:
    def __init__(self, data_dir: Path, on_voice_ready=None):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        # Called as on_voice_ready(voice_id, wav_path, meta_path, name)
        # when a job carrying auto_voice completes. None = no hook.
        self._on_voice_ready = on_voice_ready

    def start(self):
        if not self._worker.is_alive():
            self._worker.start()

    def create_job(self, source_bytes: bytes, source_filename: str,
                   timbre_bytes: bytes, timbre_filename: str,
                   req: SvcPromptIn,
                   auto_voice: dict | None = None) -> dict:
        job_id = uuid.uuid4().hex[:16]
        job = {"job_id": job_id, "kind": "svc-prompt",
               "status": "queued", "error": None,
               "created_at": time.time(), "updated_at": time.time(),
               "prompt_wav": None, "prompt_meta": None,
               "duration_s": None, "segments": None,
               "auto_voice": auto_voice,
               "request": {"source_filename": source_filename,
                           "timbre_filename": timbre_filename,
                           **req.model_dump()}}
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / ("source" + (Path(source_filename).suffix or ".wav"))
         ).write_bytes(source_bytes)
        (job_dir / ("timbre" + (Path(timbre_filename).suffix or ".wav"))
         ).write_bytes(timbre_bytes)
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

    def _fail(self, job: dict, job_dir: Path, err: str) -> None:
        self._set(job, status="failed", error=err)
        (job_dir / "receipt.json").write_text(json.dumps(
            {"job_id": job["job_id"], "status": "failed",
             "error": err}, indent=2))

    def _run(self, job_id: str):
        job = self.get_job(job_id)
        req = SvcPromptIn(**{k: v for k, v in job["request"].items()
                             if k not in ("source_filename",
                                          "timbre_filename")})
        job_dir = self.jobs_dir / job_id
        sources = sorted(job_dir.glob("source.*"))
        timbres = sorted(job_dir.glob("timbre.*"))
        if not sources or not timbres:
            self._fail(job, job_dir,
                       "svc-prompt input audio missing")
            return
        out_wav = job_dir / "prompt.wav"
        out_meta = job_dir / "prompt_meta.json"
        cmd = _driver_cmd()
        repo = os.environ.get("SOULX_REPO", "")
        if repo:
            cmd += ["--repo", repo]
        cmd += [
            "--model-path",
            os.environ.get(
                "SVC_MODEL_PATH",
                "pretrained_models/SoulX-Singer/model-svc.pt"),
            "--config",
            os.environ.get(
                "SVC_CONFIG", "soulxsinger/config/soulxsinger.yaml"),
            "--device", os.environ.get("SVC_DEVICE", "cuda"),
            "--source", str(sources[0]),
            "--timbre", str(timbres[0]),
            "--vocal-sep", "True" if req.vocal_sep else "False",
            "--language", req.language,
            "--max-merge-duration", str(req.max_merge_duration),
            "--n-steps", str(req.n_steps),
            "--cfg", str(req.cfg),
            "--pitch-shift", str(req.pitch_shift),
            "--out-wav", str(out_wav),
            "--out-meta", str(out_meta),
            "--workdir", str(job_dir / "work"),
        ]
        if req.auto_shift:
            cmd.append("--auto-shift")
        if req.fp16:
            cmd.append("--fp16")
        self._set(job, status="running")
        started = time.time()
        stderr_tail = ""
        # The model lock serializes GPU/MPS execution with the
        # synthesis and transcription workers.
        with MODEL_LOCK:
            try:
                proc = subprocess.run(cmd, capture_output=True,
                                      text=True, timeout=3600)
            except subprocess.TimeoutExpired:
                self._fail(job, job_dir,
                           "svc-prompt driver timed out after 3600 s")
                return
            stderr_tail = (proc.stderr or proc.stdout or "")[-2000:]
        if proc.returncode != 0:
            self._fail(job, job_dir,
                       f"svc-prompt driver failed (exit "
                       f"{proc.returncode}): {stderr_tail}")
            return
        # Driver exit 0 is not trusted on its own: verify the
        # published files before the job may serve them.
        if not out_wav.is_file() or not out_meta.is_file():
            self._fail(job, job_dir,
                       "svc-prompt driver exited 0 but published no "
                       "prompt pair")
            return
        try:
            segments = json.loads(out_meta.read_text())
        except Exception as e:
            self._fail(job, job_dir,
                       f"prompt meta unreadable: {e}")
            return
        if not isinstance(segments, list) or not segments:
            self._fail(job, job_dir,
                       "prompt meta is empty (no segments)")
            return
        for seg in segments:
            missing = [k for k in _META_REQUIRED_KEYS if k not in seg]
            if missing:
                self._fail(job, job_dir,
                           f"prompt meta segment missing keys {missing}")
                return
        try:
            meas = _measure_wav(out_wav)
            src = _measure_wav(sources[0])
            tim = _measure_wav(timbres[0])
        except Exception as e:
            self._fail(job, job_dir,
                       f"prompt wav unreadable: {e}")
            return
        warnings = []
        if meas["clipped_samples"] > 0:
            warnings.append("svc_clipped")
        if not (2.0 <= tim["duration_s"] <= 8.0):
            warnings.append("timbre_duration_outside_2_8s")
        # Auto-register hook: a job carrying auto_voice registers its
        # finished pair as a voice. Registration failure never hides
        # the pair itself: the job stays completed, the pair stays
        # servable at /prompt-audio and /prompt-meta, and the failure
        # is recorded on the job and the receipt.
        voice_extra: dict = {}
        auto = job.get("auto_voice")
        if auto and self._on_voice_ready:
            voice_extra["voice_id"] = auto.get("voice_id")
            try:
                self._on_voice_ready(auto["voice_id"], out_wav, out_meta,
                                     auto.get("name"))
                voice_extra["voice_status"] = "registered"
            except Exception as e:
                voice_extra["voice_status"] = "registration_failed"
                voice_extra["voice_error"] = str(e)
        receipt = {"job_id": job_id, "status": "completed",
                   "params": req.model_dump(),
                   "source_duration_s": src["duration_s"],
                   "timbre_duration_s": tim["duration_s"],
                   "prompt_wav": "prompt.wav",
                   "prompt_meta": "prompt_meta.json",
                   "sample_rate": meas["sample_rate"],
                   "duration_s": meas["duration_s"],
                   "peak": meas["peak"],
                   "clipped_samples": meas["clipped_samples"],
                   "segments": len(segments),
                   "elapsed_s": round(time.time() - started, 3),
                   "warnings": warnings,
                   **voice_extra}
        (job_dir / "receipt.json").write_text(json.dumps(receipt,
                                                          indent=2))
        self._set(job, status="completed", prompt_wav="prompt.wav",
                  prompt_meta="prompt_meta.json",
                  duration_s=meas["duration_s"],
                  segments=len(segments), **voice_extra)
