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
from .svc_prompt import (SvcPromptError, SvcPromptIn, SvcPromptWorker,
                         validate_svc_prompt)
from .transcribe import (TranscribeError, TranscribeIn,
                         TranscriptionWorker, validate_transcribe)

DATA_DIR = Path(os.environ.get("SVS_DATA_DIR", "data"))
API_KEY = os.environ.get("SVS_API_KEY", "")

store = JobStore(DATA_DIR)
tstore = TranscriptionWorker(DATA_DIR)
sstore = SvcPromptWorker(
    DATA_DIR,
    on_voice_ready=store.register_voice_files)
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
    sstore.start()


@app.get("/health")
def health():
    return {"status": "ok", "voices": len(store.voices)}


@app.post("/v1/voices", dependencies=[Depends(_auth)])
async def upload_voice(file: UploadFile | None = File(default=None),
                       prompt_meta: UploadFile | None = File(default=None),
                       timbre: UploadFile | None = File(default=None),
                       genre: UploadFile | None = File(default=None),
                       name: str | None = None):
    """Register a voice, two ways.

    Prompt path: upload `file` (a prompt wav) plus optional
    `prompt_meta`, exactly as before.

    SVC path: upload `timbre` (our voice, clean singing) plus
    `genre` (a target-genre performance, may be a mix). When genre
    audio is present, the service runs SVC automatically (genre
    performance into the timbre voice, SVC defaults: vocal_sep on,
    auto_shift off per the no-compensation rule) and registers the
    finished prompt pair as the voice. The response carries the
    pending voice_id plus the SVC job_id; the voice appears in
    GET /v1/voices once the job completes, and the job receipt
    carries voice_id and voice_status. Ambiguous combinations
    fail loudly:
    genre without timbre, genre together with a prompt file,
    timbre without genre, or nothing at all.
    """
    if genre is not None:
        if timbre is None:
            raise HTTPException(
                status_code=400,
                detail="genre audio requires timbre audio for SVC")
        if file is not None:
            raise HTTPException(
                status_code=400,
                detail="provide either a prompt file or genre+timbre "
                       "audio, not both")
        genre_bytes = await genre.read()
        timbre_bytes = await timbre.read()
        if not genre_bytes:
            raise HTTPException(status_code=400,
                                detail="empty genre audio")
        if not timbre_bytes:
            raise HTTPException(status_code=400,
                                detail="empty timbre audio")
        req = SvcPromptIn()
        try:
            validate_svc_prompt(req)
        except SvcPromptError as e:
            raise HTTPException(status_code=400, detail=str(e))
        voice_id = uuid.uuid4().hex[:12]
        job = sstore.create_job(genre_bytes,
                                genre.filename or "genre.wav",
                                timbre_bytes,
                                timbre.filename or "timbre.wav",
                                req,
                                auto_voice={"voice_id": voice_id,
                                            "name": name},
                                voice_id=voice_id)
        return {"voice_id": voice_id, "job_id": job["job_id"],
                "status": job["status"]}
    if file is None:
        raise HTTPException(
            status_code=400,
            detail="provide a prompt file or genre+timbre audio")
    if timbre is not None:
        raise HTTPException(
            status_code=400,
            detail="timbre audio needs genre audio for SVC")
    voice_id = uuid.uuid4().hex[:12]
    vdir = store.voices_dir / voice_id
    vdir.mkdir(parents=True, exist_ok=True)
    audio_path = vdir / "prompt.wav"
    audio_path.write_bytes(await file.read())
    meta_path = None
    if prompt_meta is not None:
        meta_path = vdir / "prompt_meta.json"
        meta_path.write_bytes(await prompt_meta.read())
    info = store.register_voice(voice_id, audio_path, meta_path, name,
                                "voice-upload")
    return info


@app.get("/v1/voices", dependencies=[Depends(_auth)])
def list_voices():
    return {"voices": list(store.voices.values())}


@app.get("/v1/prompts", dependencies=[Depends(_auth)])
def list_prompts():
    """Voice list under the prompts alias, same rows as /v1/voices.

    One id only: every row is a voice, keyed by voice_id. A voice_id
    from any row may be passed as `voice_id` to POST /v1/svc-prompt
    instead of re-uploading timbre audio, or as `voice_id` in
    POST /v1/synthesize parts.
    """
    return {"prompts": list(store.voices.values())}


@app.get("/v1/prompts/{voice_id}", dependencies=[Depends(_auth)])
def get_prompt(voice_id: str):
    info = store.voices.get(voice_id)
    if info is None:
        raise HTTPException(status_code=404, detail="unknown voice_id")
    return info


@app.get("/v1/prompts/{voice_id}/audio", dependencies=[Depends(_auth)])
def get_prompt_audio(voice_id: str):
    info = store.voices.get(voice_id)
    if info is None:
        raise HTTPException(status_code=404, detail="unknown voice_id")
    path = Path(info["audio_path"])
    if not path.is_file():
        raise HTTPException(status_code=500,
                            detail="stored voice audio missing")
    return FileResponse(str(path), media_type="audio/wav")


@app.post("/v1/synthesize", dependencies=[Depends(_auth)])
def synthesize(req: SynthesizeIn):
    job = store.create_job(req)
    return {"job_id": job["job_id"], "status": job["status"],
            "parts_total": job["parts_total"],
            "takes_total": job["takes_total"]}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(_auth)])
def job_status(job_id: str):
    job = (store.get_job(job_id) or tstore.get_job(job_id)
           or sstore.get_job(job_id))
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
    if sstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="svc-prompt jobs have no audio takes; use "
                   "/prompt-audio and /receipt")
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
        job = (store.get_job(job_id) or tstore.get_job(job_id)
               or sstore.get_job(job_id))
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
    if sstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="svc-prompt jobs have no MIDI; they serve a "
                   "prompt pair at /prompt-audio and /prompt-meta")
    path = tstore.jobs_dir / job_id / "transcription.mid"
    if not path.is_file():
        job = tstore.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        raise HTTPException(status_code=409,
                            detail=f"job still {job['status']}")
    return FileResponse(str(path), media_type="audio/midi")


@app.post("/v1/svc-prompt", dependencies=[Depends(_auth)])
async def svc_prompt(source: UploadFile = File(...),
                     timbre: UploadFile | None = File(default=None),
                     voice_id: str | None = Form(default=None),
                     vocal_sep: bool = Form(default=True),
                     n_steps: int = Form(default=32),
                     cfg: float = Form(default=3.0),
                     pitch_shift: int = Form(default=0),
                     auto_shift: bool = Form(default=False),
                     fp16: bool = Form(default=True),
                     language: str = Form(default="Mandarin"),
                     max_merge_duration: int = Form(default=30000)):
    """SVC genre-prompt job (reverse pipeline).

    Upload a source performance (pop singing; may be a mix) and a
    timbre reference (our voice, clean singing). The job separates
    the source mix when vocal_sep is on, converts the source
    performance into the timbre voice with SoulX-Singer-SVC, and
    transcribes the SVC output into a SoulX prompt pair (wav + meta).

    The timbre reference may be given two ways: upload `timbre`
    audio, or pass `voice_id` for a voice already stored on the
    server (from an earlier svc-prompt job, a voices genre job, or
    a voice file upload -- see GET /v1/prompts). Passing both is a
    400, passing neither is a 400, an unknown voice_id is a 404.
    Reusing an id means the night side holds one id per voice and
    never re-uploads audio for it.

    Direction is fixed: the source supplies the performance (genre,
    melody, phrasing), the timbre reference supplies only the voice.
    SVC has no style parameter and preserves the source performance,
    so an operatic source stays operatic whatever the timbre -- the
    genre must come from a real performance of that genre. The
    prompt pair is published only on full success; any step failing
    (weights download blocked, separation failure, SVC failure,
    transcription failure) fails the job loudly and serves no
    half-finished prompt. Fetch the pair at /prompt-audio and
    /prompt-meta; on completion the pair is also registered as a
    voice under the response's voice_id, and the job receipt carries
    that voice_id and voice_status. So a voice made by svc-prompt
    can be sung with directly, and a later svc-prompt call can pass
    it as `voice_id` timbre.
    """
    req = SvcPromptIn(
        vocal_sep=vocal_sep, n_steps=n_steps, cfg=cfg,
        pitch_shift=pitch_shift, auto_shift=auto_shift, fp16=fp16,
        language=language, max_merge_duration=max_merge_duration)
    try:
        validate_svc_prompt(req)
    except SvcPromptError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if voice_id is not None and timbre is not None:
        raise HTTPException(
            status_code=400,
            detail="provide either a voice_id or timbre audio, "
                   "not both")
    if voice_id is None and timbre is None:
        raise HTTPException(
            status_code=400,
            detail="provide timbre audio or a voice_id")
    source_bytes = await source.read()
    if not source_bytes:
        raise HTTPException(status_code=400, detail="empty source audio")
    timbre_filename = "timbre.wav"
    if voice_id is not None:
        info = store.voices.get(voice_id)
        if info is None:
            raise HTTPException(status_code=404,
                                detail="unknown voice_id")
        stored = Path(info["audio_path"])
        if not stored.is_file():
            raise HTTPException(status_code=500,
                                detail="stored voice audio missing")
        timbre_bytes = stored.read_bytes()
        timbre_filename = stored.name
    else:
        assert timbre is not None
        timbre_bytes = await timbre.read()
        if not timbre_bytes:
            raise HTTPException(status_code=400,
                                detail="empty timbre audio")
        timbre_filename = timbre.filename or "timbre.wav"
    new_voice_id = uuid.uuid4().hex[:12]
    job = sstore.create_job(source_bytes, source.filename or "source.wav",
                            timbre_bytes, timbre_filename,
                            req, voice_id=new_voice_id,
                            timbre_voice_id=voice_id)
    return {"job_id": job["job_id"], "status": job["status"],
            "voice_id": new_voice_id}


@app.get("/v1/jobs/{job_id}/prompt-audio", dependencies=[Depends(_auth)])
def job_prompt_audio(job_id: str):
    if store.get_job(job_id) is not None \
            or tstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="only svc-prompt jobs serve a prompt pair; "
                   "synthesis jobs use /audio, transcription jobs "
                   "use /midi")
    path = sstore.jobs_dir / job_id / "prompt.wav"
    if not path.is_file():
        job = sstore.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        raise HTTPException(status_code=409,
                            detail=f"job still {job['status']}")
    return FileResponse(str(path), media_type="audio/wav")


@app.get("/v1/jobs/{job_id}/prompt-meta", dependencies=[Depends(_auth)])
def job_prompt_meta(job_id: str):
    if store.get_job(job_id) is not None \
            or tstore.get_job(job_id) is not None:
        raise HTTPException(
            status_code=400,
            detail="only svc-prompt jobs serve a prompt pair")
    path = sstore.jobs_dir / job_id / "prompt_meta.json"
    if not path.is_file():
        job = sstore.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        raise HTTPException(status_code=409,
                            detail=f"job still {job['status']}")
    return FileResponse(str(path), media_type="application/json")
