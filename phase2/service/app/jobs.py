"""Job store + serial worker (design section 3.2).

One worker thread, strictly serial: SVS generation is seconds-to-minutes
and two models must never share the GPU. A job fails loudly on any
adapter error or takes-gate exhaustion; the receipt is still written so
the failure reason (including gate metrics) is inspectable.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path

import soundfile as sf
import numpy as np

from .alignment import align_to_score, detect_candidates
from .gate import check_pair
from .schemas import PartIn, SynthesizeIn
from .adapters.base import AdapterError
from .adapters.soulx import SoulXAdapter
from .adapters.stub import StubAdapter
from .adapters.vocalrender import VocalRenderAdapter

MAX_TAKE_ATTEMPTS = 3

# Shared with the transcription worker (app/transcribe.py): every
# model execution -- synthesis adapter calls here AND transcription
# runs there -- holds this lock, so two models never sit on the
# GPU / MPS at the same time. It changes no endpoint response shape;
# the synthesis worker was previously the only worker, so the lock
# was trivially held.
MODEL_LOCK = threading.Lock()


def _adapters() -> dict:
    return {"soulx": SoulXAdapter(), "vocalrender": VocalRenderAdapter(),
            "stub": StubAdapter()}


class RetryLinesError(Exception):
    """Retry-lines failure carrying an HTTP status (main maps it)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail

class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.voices_dir = data_dir / "voices"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.voices: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self._load_voices()
        self._worker = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        if not self._worker.is_alive():
            self._worker.start()

    def _load_voices(self):
        for info_path in self.voices_dir.glob("*/info.json"):
            try:
                info = json.loads(info_path.read_text())
                self.voices[info["voice_id"]] = info
            except Exception:
                continue

    def register_voice(self, voice_id: str, audio_path: Path,
                       prompt_meta_path: Path | None,
                       name: str | None) -> dict:
        info = {"voice_id": voice_id, "name": name,
                "audio_path": str(audio_path),
                "prompt_meta_path": (str(prompt_meta_path)
                                     if prompt_meta_path else None),
                "soulx_ready": bool(prompt_meta_path)}
        (audio_path.parent / "info.json").write_text(json.dumps(info))
        with self.lock:
            self.voices[voice_id] = info
        return info

    def create_job(self, req: SynthesizeIn,
                   retry_generation: int = 0) -> dict:
        job_id = uuid.uuid4().hex[:16]
        job = {"job_id": job_id, "status": "queued",
               "parts_total": len(req.parts),
               "takes_total": sum(p.takes for p in req.parts),
               "parts_completed": 0, "takes_completed": 0,
               "error": None, "created_at": time.time(),
               "updated_at": time.time(),
               "retry_generation": retry_generation,
               "request": req.model_dump()}
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
                    self._set(job, status="failed", error=f"worker: {e}")
            finally:
                self.queue.task_done()

    def _run(self, job_id: str):
        job = self.get_job(job_id)
        req = SynthesizeIn(**job["request"])
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        adapters = _adapters()
        self._set(job, status="running")
        receipt_parts: dict[str, dict] = {}
        try:
            for part in req.parts:
                receipt_parts[part.label] = self._run_part(
                    job, part, adapters, job_dir)
                self._set(job,
                          parts_completed=job["parts_completed"] + 1)
        except AdapterError as e:
            self._set(job, status="failed", error=str(e))
            (job_dir / "receipt.json").write_text(json.dumps(
                {"job_id": job_id, "status": "failed", "error": str(e),
                 "parts": receipt_parts}, indent=2))
            return
        self._set(job, status="completed")
        (job_dir / "receipt.json").write_text(json.dumps(
            {"job_id": job_id, "status": "completed",
             "parts": receipt_parts}, indent=2))

    def _run_part(self, job: dict, part: PartIn, adapters: dict,
                  job_dir: Path) -> dict:
        voice = self.voices.get(part.voice_id)
        if voice is None:
            raise AdapterError(f"unknown voice_id {part.voice_id!r}")
        adapter = adapters.get(part.model)
        if adapter is None:
            raise AdapterError(f"unknown model {part.model!r}")
        workdir = job_dir / f"work_{part.label}"
        workdir.mkdir(parents=True, exist_ok=True)
        seeds = part.seeds or list(range(part.takes))
        takes_out = []
        accepted: list[tuple] = []  # (wav float array, sr)
        gate_pairwise = []
        for take_idx in range(part.takes):
            base_seed = seeds[take_idx] if take_idx < len(seeds) \
                else take_idx
            last_error = None
            for attempt in range(MAX_TAKE_ATTEMPTS):
                seed = base_seed if attempt == 0 else base_seed + 1000 * attempt
                out_path = job_dir / f"{part.label}_take{take_idx}.wav"
                with MODEL_LOCK:
                    wav_path, sr, info = adapter.synthesize(
                        part, voice, seed, out_path, workdir)
                data, sr_read = sf.read(str(wav_path), dtype="float32")
                mono = data[:, 0] if data.ndim == 2 else data
                if accepted:
                    pair_checks = [check_pair(mono, sr_read, a, s)
                                   for a, s in accepted]
                    gate_pairwise.append(
                        {"take": take_idx, "attempt": attempt,
                         "vs_accepted": pair_checks})
                    if all(c["passed"] for c in pair_checks):
                        accepted.append((mono, sr_read))
                        break
                    last_error = ("takes gate failed vs accepted takes: "
                                  + json.dumps(pair_checks))
                    continue
                accepted.append((mono, sr_read))
                break
            else:
                raise AdapterError(
                    f"part {part.label!r} take {take_idx}: takes gate "
                    f"exhausted after {MAX_TAKE_ATTEMPTS} attempts; "
                    f"last: {last_error}")
            self._set(job, takes_completed=job["takes_completed"] + 1)
            takes_out.append({
                "take": take_idx, "seed": seed,
                "attempts": attempt + 1,
                "audio_file": out_path.name,
                "duration_s": round(len(mono) / sr_read, 4),
                "sample_rate": sr_read,
                "adapter_info": info,
                "alignment": self._alignment(part, mono, sr_read),
            })
        return {"model": part.model, "takes": takes_out,
                "gate_pairwise": gate_pairwise}

    @staticmethod
    def _is_rest(note) -> bool:
        return note.lyric in ("", "<SP>") or note.midi <= 0

    def retry_lines(self, job_id: str, part_label: str,
                        line_ranges: list[tuple[int, int]]) -> dict:
        """Gate-check line ranges of a completed job's take 0 and queue a
        retry job for the failing lines only (Melodyne Phase 1 gate trigger).

        line_ranges are half-open note-index ranges into the part's full
        score (rests included in indexing, excluded from gating). A failing
        line becomes one subset part labelled ``f"{part_label}_retry{i}"``
        (i = request order), same voice/model/options, one take, seed =
        original take-0 seed + 1000. Subset scores keep ABSOLUTE start_s
        (no rebasing): the retry take stays on the same score clock, and
        Yeban maps by note index. Retry chains are bounded: a job whose
        retry_generation is already MAX_RETRY_GENERATIONS (3) cannot be
        retried again (409). Passing lines get no retry part. Returns
        {"retry_job_id" (or None), "part_label", "retry_generation",
        "lines": [{note_start_index, note_end_index, label, gate}]}.
        """
        from .schemas import ScoreIn
        from .vocal_gate import (MAX_RETRY_GENERATIONS, RETRY_SEED_BUMP,
                                 interpolate_coarse, line_gate)
        job = self.get_job(job_id)
        if job is None:
            raise RetryLinesError(404, "unknown job")
        if job["status"] != "completed":
            raise RetryLinesError(409, f"job still {job['status']}")
        req = SynthesizeIn(**job["request"])
        part = next((p for p in req.parts if p.label == part_label), None)
        if part is None:
            raise RetryLinesError(404, f"unknown part {part_label!r}")
        job_dir = self.jobs_dir / job_id
        receipt_path = job_dir / "receipt.json"
        if not receipt_path.is_file():
            raise RetryLinesError(409, "receipt missing")
        receipt = json.loads(receipt_path.read_text())
        try:
            take0 = receipt["parts"][part_label]["takes"][0]
            audio_name = take0["audio_file"]
            alignment = take0["alignment"]
        except (KeyError, IndexError) as e:
            raise RetryLinesError(409, f"receipt has no take 0: {e}")
        audio_path = job_dir / audio_name
        if not audio_path.is_file():
            raise RetryLinesError(409, "take audio missing")
        data, sr_read = sf.read(str(audio_path), dtype="float32")
        mono = data[:, 0] if data.ndim == 2 else data
        notes_all = sorted(part.score.notes, key=lambda n: n.start_s)
        sung_positions = [i for i, n in enumerate(notes_all)
                          if not JobStore._is_rest(n)]
        if len(alignment) != len(notes_all):
            raise RetryLinesError(409, "alignment length != score length")
        measured = [alignment[i].get("measured_onset_s")
                    for i in sung_positions]
        score_starts = [float(notes_all[i].start_s) for i in sung_positions]
        coarse_all = interpolate_coarse(score_starts, measured)
        generation = int(job.get("retry_generation", 0))
        if generation >= MAX_RETRY_GENERATIONS:
            raise RetryLinesError(
                409, f"retry chain exhausted (generation {generation})")
        orig_seed = (part.seeds or [0])[0]
        take_dur = len(mono) / sr_read
        failing_parts = []
        line_results = []
        for order, (s, e) in enumerate(line_ranges):
            if not (0 <= s < e <= len(notes_all)):
                raise RetryLinesError(
                    400, f"line range [{s}, {e}) out of score "
                         f"(0..{len(notes_all)})")
            within = [k for k, i in enumerate(sung_positions) if s <= i < e]
            if not within:
                line_results.append({
                    "note_start_index": s, "note_end_index": e,
                    "label": None,
                    "gate": {"pass": True,
                             "failures": ["no sung notes in range"]}})
                continue
            line_notes = [{"start": float(notes_all[sung_positions[k]].start_s),
                           "dur": float(notes_all[sung_positions[k]].dur_s)}
                          for k in within]
            line_coarse = [coarse_all[k] for k in within]
            prev_c = coarse_all[within[0] - 1] if within[0] > 0 else None
            next_c = coarse_all[within[-1] + 1] \
                if within[-1] + 1 < len(coarse_all) else None
            verdict = line_gate(mono.astype(np.float64), sr_read, line_notes,
                                line_coarse, prev_c, next_c, take_dur)
            label = None
            if not verdict["pass"]:
                label = f"{part_label}_retry{order}"
                subset = [notes_all[i] for i in range(s, e)]
                failing_parts.append(PartIn(
                    label=label,
                    score=ScoreIn(bpm=part.score.bpm,
                                  language=part.score.language,
                                  notes=subset),
                    voice_id=part.voice_id, model=part.model,
                    variant=part.variant, takes=1,
                    seeds=[orig_seed + RETRY_SEED_BUMP],
                    options=part.options))
            line_results.append({"note_start_index": s, "note_end_index": e,
                                 "label": label, "gate": verdict})
        retry_job_id = None
        if failing_parts:
            retry_job = self.create_job(
                SynthesizeIn(parts=failing_parts),
                retry_generation=generation + 1)
            retry_job_id = retry_job["job_id"]
        return {"retry_job_id": retry_job_id, "part_label": part_label,
                "retry_generation": generation + 1 if failing_parts
                else generation,
                "lines": line_results}

    @staticmethod
    def _alignment(part: PartIn, mono, sr: int) -> list[dict]:
        """Measured onsets vs score onsets, one entry per score note.

        Entry keys: score_onset_s, measured_onset_s, deviation_ms,
        lyric, ignored. Sung notes get ignored=False. Rest (SP) notes
        have no sung onset: they are reported as ignored=True with
        measured_onset_s=None and deviation_ms=None, and they consume
        no detection candidate -- assigning a neighbour syllable's
        onset to a rest (the Phase 1 behaviour) would misplace both.
        Alignment therefore runs over sung notes only, then re-expands
        to the full note list.

        SoulX: placement stays on the score clock; deviations are
        validation data (mode "score"). VocalRender: these measured
        onsets ARE the placement data (mode "rescaled"); Yeban places
        by measured onset, and skips ignored (SP) entries.
        """
        try:
            cands = detect_candidates(mono, sr)
            notes = sorted(part.score.notes, key=lambda n: n.start_s)
            sung = [n for n in notes if not JobStore._is_rest(n)]
            total = max((n.start_s + n.dur_s for n in sung), default=0.0)
            mode = "rescaled" if part.model == "vocalrender" else "score"
            sung_onsets = [float(n.start_s) for n in sung]
            aligned = align_to_score(cands, sung_onsets, total,
                                     mode=mode) if sung else []
            out: list[dict] = []
            sung_iter = iter(aligned)
            for note in notes:
                if JobStore._is_rest(note):
                    out.append({"score_onset_s": round(float(note.start_s),
                                                      4),
                                "measured_onset_s": None,
                                "deviation_ms": None,
                                "lyric": note.lyric,
                                "ignored": True})
                else:
                    entry = next(sung_iter)
                    entry["lyric"] = note.lyric
                    entry["ignored"] = False
                    out.append(entry)
            return out
        except Exception as e:
            return [{"error": f"alignment failed: {e}"}]
