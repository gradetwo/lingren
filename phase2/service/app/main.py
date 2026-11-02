"""伶人 SVS API service, Phase 1 (design section 5).

Run from service/:  SVS_DATA_DIR=./data uvicorn app.main:app --port 8000
Auth: set SVS_API_KEY to require Bearer tokens (required for remote
tunnel deployments; optional for local dev).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .jobs import JobStore, RetryLinesError
from .schemas import RetryLinesIn, SynthesizeIn
from .transcribe import (TranscribeError, TranscribeIn,
                         TranscriptionWorker, validate_transcribe)

DATA_DIR = Path(os.environ.get("SVS_DATA_DIR", "data"))
API_KEY = os.environ.get("SVS_API_KEY", "")

store = JobStore(DATA_DIR)
tstore = TranscriptionWorker(DATA_DIR)
app = FastAPI(title="lingren-svs", version="0.2.0-phase2")


def _auth(authorization: str | None = Header(default=None)):
    if API_KEY:
        if authorization != f"Bearer {API_KEY}":
            raise HTTPException(status_code=401, detail="bad API key")
    return True


@app.on_event("startup")
def _startup():
    store.start()
    tstore.start()


@app.get("/health")
def health():
    return {"status": "ok", "voices": len(store.voices)}


@app.post("/v1/voices", dependencies=[Depends(_auth)])
async def upload_voice(file: UploadFile = File(...),
                       prompt_meta: UploadFile | None = File(default=None),
                       name: str | None = None):
    voice_id = uuid.uuid4().hex[:12]
    vdir = store.voices_dir / voice_id
    vdir.mkdir(parents=True, exist_ok=True)
    audio_path = vdir / "prompt.wav"
    audio_path.write_bytes(await file.read())
    meta_path = None
    if prompt_meta is not None:
        meta_path = vdir / "prompt_meta.json"
        meta_path.write_bytes(await prompt_meta.read())
    info = store.register_voice(voice_id, audio_path, meta_path, name)
    return info


@app.get("/v1/voices", dependencies=[Depends(_auth)])
def list_voices():
    return {"voices": list(store.voices.values())}


@app.post("/v1/synthesize", dependencies=[Depends(_auth)])
def synthesize(req: SynthesizeIn):
    job = store.create_job(req)
    return {"job_id": job["job_id"], "status": job["status"],
            "parts_total": job["parts_total"],
            "takes_total": job["takes_total"]}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(_auth)])
def job_status(job_id: str):
    job = store.get_job(job_id) or tstore.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {k: v for k, v in job.items() if k != "request"}


@app.post("/v1/jobs/{job_id}/retry-lines", dependencies=[Depends(_auth)])
def retry_lines(job_id: str, req: RetryLinesIn):
    """Melodyne Phase 1 gate trigger: re-synthesize failing lines only.

    The gate verdict for each requested line is computed from the source
    job's take-0 audio (Lingren-side mirror of the Yeban import gate).
    Failing lines become subset parts of one new retry job (label
    ``<part>_retry<i>``, seed = original + 1000, bounded to 3 retry
    generations); passing lines get no retry part. Subset scores keep
    absolute start_s (same score clock; Yeban maps by note index).
    """
    if store.get_job(job_id) is None and tstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="transcription jobs have no takes to retry")
    try:
        return store.retry_lines(
            job_id, req.part_label,
            [(r.note_start_index, r.note_end_index) for r in req.lines])
    except RetryLinesError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@app.get("/v1/jobs/{job_id}/audio", dependencies=[Depends(_auth)])
def job_audio(job_id: str, part: str | None = None, take: int | None = None):
    if tstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="transcription jobs have no audio takes; use "
                   "/midi and /receipt")
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    job_dir = store.jobs_dir / job_id
    if part is not None and take is not None:
        path = job_dir / f"{part}_take{take}.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="audio not found")
        return FileResponse(str(path), media_type="audio/wav")
    wavs = sorted(job_dir.glob("*_take*.wav"))
    if len(wavs) == 1:
        return FileResponse(str(wavs[0]), media_type="audio/wav")
    raise HTTPException(
        status_code=400,
        detail="ambiguous: pass ?part=<label>&take=<n>; "
               f"available: {[w.name for w in wavs]}")


@app.get("/v1/jobs/{job_id}/receipt", dependencies=[Depends(_auth)])
def job_receipt(job_id: str):
    path = store.jobs_dir / job_id / "receipt.json"
    if not path.is_file():
        job = store.get_job(job_id) or tstore.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        raise HTTPException(status_code=409,
                            detail=f"job still {job['status']}")
    return FileResponse(str(path), media_type="application/json")


@app.post("/v1/transcribe", dependencies=[Depends(_auth)])
async def transcribe(file: UploadFile = File(...),
                     model: str | None = Form(default=None),
                     instruments: str = Form(default=""),
                     bpm: float | None = Form(default=None),
                     beats_per_bar: int | None = Form(default=None),
                     first_downbeat: float | None = Form(default=None),
                     quantize: bool = Form(default=False),
                     grid: int | None = Form(default=None),
                     detect_tempo: str = Form(default="best-effort")):
    """MuScriptor transcription (design section 12).

    Upload an audio file; the job returns MIDI + a note receipt.
    Instruments is a comma-separated list of exact MT3_FULL_PLUS group
    names, or empty for Automatic (the model chooses). A given list
    is a hard mask, not a post-filter. Weights are CC BY-NC 4.0,
    gated HuggingFace downloads, never shipped with the service;
    missing authorization fails the job loudly. Do not transcribe
    audio you do not hold rights to.
    """
    req = TranscribeIn(
        model=model,
        instruments=[n for n in instruments.split(",") if n] or None,
        bpm=bpm, beats_per_bar=beats_per_bar,
        first_downbeat=first_downbeat, quantize=quantize, grid=grid,
        detect_tempo=detect_tempo)
    try:
        validate_transcribe(req)
    except TranscribeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Resolution: request model overrides env, env overrides "small".
    # A request model must be a keyword (validated above); the env
    # value passes through operator-controlled, so it may also be a
    # local safetensors path -- a bad env value fails the job loudly
    # in the driver, it is never silently replaced.
    effective = req.model or os.environ.get("MUSCRIPTOR_MODEL") or "small"
    req = req.model_copy(update={"model": effective})
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio upload")
    job = tstore.create_job(audio_bytes, file.filename or "input.wav",
                            req)
    return {"job_id": job["job_id"], "status": job["status"]}


@app.get("/v1/jobs/{job_id}/midi", dependencies=[Depends(_auth)])
def job_midi(job_id: str):
    if store.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="synthesis jobs have no MIDI; transcription jobs "
                   "serve MIDI here")
    path = tstore.jobs_dir / job_id / "transcription.mid"
    if not path.is_file():
        job = tstore.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        raise HTTPException(status_code=409,
                            detail=f"job still {job['status']}")
    return FileResponse(str(path), media_type="audio/midi")
