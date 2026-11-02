"""Retry-lines endpoint tests (stub adapter + constructed takes only).

The FAIL take is constructed audio (compressed notes), NOT model output;
the PASS take is the stub pseudo-singer (labelled as such by the
adapter itself). No GPU, no real synthesis, no Colab spend.
"""
import io
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app  # noqa: E402

SR = 48000


def _wav_bytes_voice():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        x = (0.3 * np.sin(2 * np.pi * 220
                          * np.arange(24000) / 24000) * 32767)
        w.writeframes(x.astype(np.int16).tobytes())
    buf.seek(0)
    return buf.read()


def _notes(starts):
    return [{"lyric": "啊", "midi": m, "start_s": s, "dur_s": 1.0,
             "phoneme": "zh_a1"}
            for m, s in zip([64, 65, 67], starts)]


def _compressed_take():
    """Constructed FAIL take: score span 3.0 s sung in ~1.4 s."""
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1e-4, int(2.0 * SR))
    for start, freq in [(0.3, 330.0), (0.8, 370.0), (1.3, 392.0)]:
        t = np.arange(int(0.4 * SR)) / SR
        env = np.minimum(1.0, t / 0.03)
        s0 = int(start * SR)
        x[s0:s0 + len(t)] += 0.7 * env * np.sin(2 * np.pi * freq * t)
    return x


def _fabricate_completed(store, job_id: str, voice_id: str, notes,
                         take_audio: np.ndarray, measured,
                         generation: int = 0):
    job_dir = store.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(job_dir / "lead_take0.wav"), take_audio.astype(np.float32),
             SR)
    alignment = [{"score_onset_s": round(float(n["start_s"]), 4),
                  "measured_onset_s": m, "deviation_ms": None,
                  "lyric": n["lyric"], "ignored": False}
                 for n, m in zip(notes, measured)]
    receipt = {"job_id": job_id, "status": "completed",
               "parts": {"lead": {"model": "stub",
                                  "takes": [{"take": 0, "seed": 5,
                                             "audio_file": "lead_take0.wav",
                                             "alignment": alignment}]}}}
    (job_dir / "receipt.json").write_text(json.dumps(receipt))
    req = {"parts": [{"label": "lead",
                      "score": {"bpm": 120.0, "language": "Mandarin",
                                "notes": notes},
                      "voice_id": voice_id, "model": "stub", "takes": 1,
                      "seeds": [5], "options": {}}]}
    store.jobs[job_id] = {"job_id": job_id, "status": "completed",
                          "parts_total": 1, "takes_total": 1,
                          "parts_completed": 1, "takes_completed": 1,
                          "error": None, "created_at": 0.0,
                          "updated_at": 0.0,
                          "retry_generation": generation, "request": req}


def _poll_completed(client, job_id: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/jobs/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] == "completed":
            return
        if r.json()["status"] == "failed":
            raise AssertionError(f"job failed: {r.json()}")
        time.sleep(0.2)
    raise AssertionError("job did not complete in time")


def test_retry_lines_fail_creates_retry_job(tmp_path, monkeypatch):
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    from app import main as main_mod
    from app.jobs import JobStore
    main_mod.store = JobStore(tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes_voice())})
        assert r.status_code == 200, r.text
        voice_id = r.json()["voice_id"]
        notes = _notes([0.0, 1.0, 2.0])
        _fabricate_completed(main_mod.store, "srcfail", voice_id, notes,
                             _compressed_take(), [0.3, 0.8, 1.3])
        r = client.post("/v1/jobs/srcfail/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 3}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["retry_job_id"] is not None
        line = body["lines"][0]
        assert line["label"] == "lead_retry0"
        assert not line["gate"]["pass"]
        assert any("line ratio" in f for f in line["gate"]["failures"])
        _poll_completed(client, body["retry_job_id"])
        retry_req = main_mod.store.get_job(
            body["retry_job_id"])["request"]
        assert retry_req["parts"][0]["label"] == "lead_retry0"
        assert retry_req["parts"][0]["seeds"] == [1005]
        assert len(retry_req["parts"][0]["score"]["notes"]) == 3


def test_retry_lines_pass_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    from app import main as main_mod
    from app.jobs import JobStore
    main_mod.store = JobStore(tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes_voice())})
        voice_id = r.json()["voice_id"]
        part = {"label": "lead",
                "score": {"bpm": 120.0, "language": "Mandarin",
                          "notes": [{"lyric": "啊", "midi": 64,
                                     "start_s": 0.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"},
                                    {"lyric": "啊", "midi": 67,
                                     "start_s": 2.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"}]},
                "voice_id": voice_id, "model": "stub", "takes": 1,
                "seeds": [0]}
        r = client.post("/v1/synthesize", json={"parts": [part]})
        job_id = r.json()["job_id"]
        _poll_completed(client, job_id)
        r = client.post(f"/v1/jobs/{job_id}/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 2}]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["retry_job_id"] is None
        assert body["lines"][0]["gate"]["pass"]


def test_retry_lines_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    from app import main as main_mod
    from app.jobs import JobStore
    main_mod.store = JobStore(tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes_voice())})
        voice_id = r.json()["voice_id"]
        notes = _notes([0.0, 1.0, 2.0])
        _fabricate_completed(main_mod.store, "srcgen", voice_id, notes,
                             _compressed_take(), [0.3, 0.8, 1.3],
                             generation=3)
        r = client.post("/v1/jobs/srcgen/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 3}]})
        assert r.status_code == 409, r.text
        _fabricate_completed(main_mod.store, "srcok", voice_id, notes,
                             _compressed_take(), [0.3, 0.8, 1.3])
        r = client.post("/v1/jobs/srcok/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 99}]})
        assert r.status_code == 400, r.text
        r = client.post("/v1/jobs/nope/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 1}]})
        assert r.status_code == 404, r.text
        main_mod.store.jobs["queued1"] = {
            "job_id": "queued1", "status": "queued", "request": {
                "parts": [{"label": "lead",
                           "score": {"bpm": 120.0, "language": "Mandarin",
                                     "notes": notes},
                           "voice_id": voice_id, "model": "stub",
                           "takes": 1, "seeds": [5], "options": {}}]},
            "retry_generation": 0}
        r = client.post("/v1/jobs/queued1/retry-lines",
                        json={"part_label": "lead",
                              "lines": [{"note_start_index": 0,
                                         "note_end_index": 1}]})
        assert r.status_code == 409, r.text
