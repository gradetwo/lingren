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

from .alignment import align_to_score, detect_candidates
from .gate import check_pair
from .schemas import PartIn, SynthesizeIn
from .score_converter import score_onsets_seconds
from .adapters.base import AdapterError
from .adapters.soulx import SoulXAdapter
from .adapters.stub import StubAdapter

MAX_TAKE_ATTEMPTS = 3


def _adapters() -> dict:
    # Registration lands progressively as each adapter lands (steps
    # 2-4 of DESIGN-four-pipelines.md section 12); the finished
    # registry is soulx, vocalrender, yue2, mulacover + stub.
    # The _VocalRenderPending placeholder is gone (step 2).
    return {"soulx": SoulXAdapter(), "stub": StubAdapter()}


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

    def create_job(self, req: SynthesizeIn) -> dict:
        job_id = uuid.uuid4().hex[:16]
        job = {"job_id": job_id, "status": "queued",
               "parts_total": len(req.parts),
               "takes_total": sum(p.takes for p in req.parts),
               "parts_completed": 0, "takes_completed": 0,
               "error": None, "created_at": time.time(),
               "updated_at": time.time(),
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
        # Adapter first, then voice: YuE2/MuLaCover generation needs
        # no voice (their needs_voice hook returns False unless the
        # SVC switch is on), so an unknown voice_id must not fail
        # them. SoulX/VocalRender keep the old behaviour (voice
        # required, same error text).
        adapter = adapters.get(part.model)
        if adapter is None:
            raise AdapterError(f"unknown model {part.model!r}")
        nv = getattr(adapter, "needs_voice", True)
        needs_voice = nv(part) if callable(nv) else bool(nv)
        voice = self.voices.get(part.voice_id)
        if needs_voice and voice is None:
            raise AdapterError(f"unknown voice_id {part.voice_id!r}")
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
    def _alignment(part: PartIn, mono, sr: int) -> list[dict]:
        """Measured onsets vs score onsets.

        SoulX: placement stays on the score clock; deviations are
        validation data. VocalRender (Phase 2): these measured onsets
        ARE the placement data (first syllable anchors the score start).
        """
        try:
            cands = detect_candidates(mono, sr)
            onsets = score_onsets_seconds(part.score)
            notes = sorted(part.score.notes, key=lambda n: n.start_s)
            total = max((n.start_s + n.dur_s for n in notes), default=0.0)
            mode = "rescaled" if part.model == "vocalrender" else "score"
            aligned = align_to_score(cands, onsets, total, mode=mode)
            # Carry the lyric so consumers can ignore rests (SP) when
            # placing: rests have no sung onset, their assigned candidate
            # is a neighbouring syllable's at best.
            for entry, note in zip(aligned, notes):
                entry["lyric"] = note.lyric
            return aligned
        except Exception as e:
            return [{"error": f"alignment failed: {e}"}]
