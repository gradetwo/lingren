"""Transcription endpoint tests with the fake driver (no GPU, no
weights, no torch). These prove plumbing only: submission
validation, status polling, MIDI + receipt shapes, the audio/midi
endpoint guards, and loud driver failure. They say nothing about
transcription accuracy; the real-model check is REPORT-TRANSCRIBE.md.
"""
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app  # noqa: E402

SR = 16000


def _wav_bytes(freq=440.0, seconds=2.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        x = (0.3 * np.sin(2 * np.pi * freq * np.arange(int(SR * seconds))
                          / SR) * 32767)
        w.writeframes(x.astype(np.int16).tobytes())
    buf.seek(0)
    return buf.read()


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MUSCRIPTOR_DRIVER_CMD",
                       f"{sys.executable} "
                       f"{Path(__file__).parent / 'fake_muscriptor_cli.py'}")
    from app import main as main_mod
    from app.jobs import JobStore
    from app.transcribe import TranscriptionWorker
    main_mod.store = JobStore(tmp_path)
    main_mod.tstore = TranscriptionWorker(tmp_path)
    return main_mod


def _poll(client, job_id, timeout_s=30.0):
    deadline = time.time() + timeout_s
    while True:
        st = client.get(f"/v1/jobs/{job_id}").json()
        if st["status"] in ("completed", "failed"):
            return st
        assert time.time() < deadline, st
        time.sleep(0.05)


def test_transcribe_happy_path(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())},
                        data={"model": "small", "instruments": "voice"})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["kind"] == "transcribe"
        assert st["notes_total"] == 2
        rec = client.get(f"/v1/jobs/{job_id}/receipt")
        assert rec.status_code == 200
        body = rec.json()
        assert body["model"] == "small"
        assert body["notes"][0]["instrument"] == "voice"
        assert body["notes"][0]["pitch"] == 69
        assert body["warnings"] == []
        midi = client.get(f"/v1/jobs/{job_id}/midi")
        assert midi.status_code == 200
        assert midi.content[:4] == b"MThd"
        au = client.get(f"/v1/jobs/{job_id}/audio")
        assert au.status_code == 400


def test_transcribe_unknown_instrument_rejected(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())},
                        data={"instruments": "kazoo"})
        assert r.status_code == 400, r.text
        assert "kazoo" in r.text


def test_transcribe_driver_failure_is_loud(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MUSCRIPTOR_FAIL", "1")
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())})
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "failed", st
        assert "ModelDownloadError" in st["error"]


def test_midi_on_synthesis_job_rejected(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes())})
        assert r.status_code == 200, r.text
        voice_id = r.json()["voice_id"]
        part = {"label": "lead",
                "score": {"bpm": 120.0, "language": "Mandarin",
                          "notes": [{"lyric": "啊", "midi": 64,
                                     "start_s": 0.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"}]},
                "voice_id": voice_id, "model": "stub", "takes": 1,
                "seeds": [0]}
        r = client.post("/v1/synthesize", json={"parts": [part]})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        for _ in range(300):
            st = client.get(f"/v1/jobs/{job_id}").json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert st["status"] == "completed", st
        midi = client.get(f"/v1/jobs/{job_id}/midi")
        assert midi.status_code == 400


def test_transcribe_model_resolution_env_and_override(monkeypatch,
                                                      tmp_path):
    monkeypatch.setenv("MUSCRIPTOR_MODEL", "medium")
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        # No model field: env wins.
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())})
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        rec = client.get(f"/v1/jobs/{st['job_id']}/receipt").json()
        assert rec["model"] == "medium"
        # Explicit request model overrides the env.
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())},
                        data={"model": "small"})
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        rec = client.get(f"/v1/jobs/{st['job_id']}/receipt").json()
        assert rec["model"] == "small"
        # Unknown request model is a 400, not a queued job.
        r = client.post("/v1/transcribe",
                        files={"file": ("tone.wav", _wav_bytes())},
                        data={"model": "huge"})
        assert r.status_code == 400
