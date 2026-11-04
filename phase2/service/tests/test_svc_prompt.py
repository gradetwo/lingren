"""SVC genre-prompt endpoint tests with the fake driver (no GPU, no
weights, no torch). These prove plumbing only: submission
validation, status polling, prompt-audio + prompt-meta shapes,
receipt fields, the audio/midi/prompt endpoint guards, loud driver
failure with no half-finished prompt served, and the clipped-warning
path. They say nothing about conversion quality; the real-model
check is the 《孤勇者》 pilot report.
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

SR = 24000


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
    monkeypatch.setenv("SVC_PROMPT_DRIVER_CMD",
                       f"{sys.executable} "
                       f"{Path(__file__).parent / 'fake_svc_prompt_cli.py'}")
    from app import main as main_mod
    from app.jobs import JobStore
    from app.svc_prompt import SvcPromptWorker
    from app.transcribe import TranscriptionWorker
    main_mod.store = JobStore(tmp_path)
    main_mod.tstore = TranscriptionWorker(tmp_path)
    main_mod.sstore = SvcPromptWorker(
        tmp_path, on_voice_ready=main_mod.store.register_voice_files)
    return main_mod


def _poll(client, job_id, timeout_s=30.0):
    deadline = time.time() + timeout_s
    while True:
        st = client.get(f"/v1/jobs/{job_id}").json()
        if st["status"] in ("completed", "failed"):
            return st
        assert time.time() < deadline, st
        time.sleep(0.05)


def _submit(client, **data):
    return client.post("/v1/svc-prompt",
                       files={"source": ("src.wav", _wav_bytes(440.0)),
                              "timbre": ("tim.wav", _wav_bytes(660.0))},
                       data=data)


def test_svc_prompt_happy_path(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit(client)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["kind"] == "svc-prompt"
        assert st["prompt_wav"] == "prompt.wav"
        assert st["prompt_meta"] == "prompt_meta.json"
        assert st["segments"] == 1
        rec = client.get(f"/v1/jobs/{job_id}/receipt")
        assert rec.status_code == 200
        body = rec.json()
        assert body["status"] == "completed"
        assert body["params"]["n_steps"] == 32
        assert body["params"]["cfg"] == 3.0
        assert body["params"]["vocal_sep"] is True
        assert body["prompt_wav"] == "prompt.wav"
        assert body["prompt_meta"] == "prompt_meta.json"
        assert body["sample_rate"] == 24000
        assert body["duration_s"] == 2.0
        assert body["segments"] == 1
        assert body["warnings"] == []
        au = client.get(f"/v1/jobs/{job_id}/prompt-audio")
        assert au.status_code == 200
        assert au.content[:4] == b"RIFF"
        meta = client.get(f"/v1/jobs/{job_id}/prompt-meta")
        assert meta.status_code == 200
        segs = meta.json()
        assert len(segs) == 1
        for k in ("text", "phoneme", "note_pitch", "duration", "f0",
                  "time"):
            assert k in segs[0], k
        assert client.get(f"/v1/jobs/{job_id}/audio").status_code == 400
        assert client.get(f"/v1/jobs/{job_id}/midi").status_code == 400


def test_svc_prompt_params_echoed(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit(client, vocal_sep="false", n_steps=16, cfg=2.5,
                    pitch_shift=-3, auto_shift="false",
                    fp16="false", language="Mandarin")
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        body = client.get(
            f"/v1/jobs/{r.json()['job_id']}/receipt").json()
        assert body["params"]["vocal_sep"] is False
        assert body["params"]["n_steps"] == 16
        assert body["params"]["cfg"] == 2.5
        assert body["params"]["pitch_shift"] == -3
        assert body["params"]["auto_shift"] is False
        assert body["params"]["fp16"] is False


def test_svc_prompt_auto_shift_defaults_off(monkeypatch, tmp_path):
    # Endpoint default must match the no-compensation rule: omitting
    # auto_shift means False. (Regression: the Form default once stayed
    # True while only the model default was flipped to False.)
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        body = client.get(
            f"/v1/jobs/{r.json()['job_id']}/receipt").json()
        assert body["params"]["auto_shift"] is False


def test_svc_prompt_validation_rejected(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        for data in ({"n_steps": 100}, {"n_steps": 8}, {"cfg": 0},
                     {"pitch_shift": 13}, {"pitch_shift": -13},
                     {"max_merge_duration": 0}):
            r = _submit(client, **data)
            assert r.status_code == 400, (data, r.text)
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", b""),
                               "timbre": ("tim.wav", _wav_bytes())})
        assert r.status_code == 400
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes()),
                               "timbre": ("tim.wav", b"")})
        assert r.status_code == 400


def test_svc_prompt_validation_unit():
    # Empty language cannot arrive via the form (FastAPI substitutes
    # the default for an empty field), but direct construction must
    # still reject it -- same for the other bounds.
    from app.svc_prompt import (SvcPromptError, SvcPromptIn,
                                validate_svc_prompt)
    for bad in ({"language": ""}, {"n_steps": 100}, {"cfg": 0},
                {"pitch_shift": 13}, {"max_merge_duration": 0}):
        try:
            validate_svc_prompt(SvcPromptIn(**bad))
        except SvcPromptError:
            continue
        raise AssertionError(f"accepted {bad}")
    validate_svc_prompt(SvcPromptIn())


def test_svc_prompt_driver_failure_is_loud(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_SVC_PROMPT_FAIL", "1")
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit(client)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "failed", st
        assert "WeightDownloadError" in st["error"]
        rec = client.get(f"/v1/jobs/{job_id}/receipt")
        assert rec.status_code == 200
        assert rec.json()["status"] == "failed"
        # No half-finished prompt is served.
        assert client.get(f"/v1/jobs/{job_id}/prompt-audio").status_code \
            == 409
        assert client.get(f"/v1/jobs/{job_id}/prompt-meta").status_code \
            == 409


def test_svc_prompt_clipped_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_SVC_PROMPT_CLIP", "1")
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        body = client.get(
            f"/v1/jobs/{r.json()['job_id']}/receipt").json()
        assert body["clipped_samples"] == 3
        assert "svc_clipped" in body["warnings"]


def test_prompt_endpoints_on_other_jobs_rejected(monkeypatch, tmp_path):
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
        assert client.get(
            f"/v1/jobs/{job_id}/prompt-audio").status_code == 400
        assert client.get(
            f"/v1/jobs/{job_id}/prompt-meta").status_code == 400
        assert client.get("/v1/jobs/nope/prompt-audio").status_code \
            == 404


def test_voices_genre_auto_registers(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"timbre": ("t.wav", _wav_bytes(660.0)),
                               "genre": ("g.wav", _wav_bytes(440.0))},
                        data={"name": "auto"})
        assert r.status_code == 200, r.text
        body = r.json()
        voice_id = body["voice_id"]
        job_id = body["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["voice_status"] == "registered"
        voices = client.get("/v1/voices").json()["voices"]
        info = [v for v in voices if v["voice_id"] == voice_id]
        assert len(info) == 1
        assert info[0]["soulx_ready"] is True
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["voice_status"] == "registered"
        assert rec["voice_id"] == voice_id


def test_voices_genre_validation(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        # genre without timbre
        r = client.post("/v1/voices",
                        files={"genre": ("g.wav", _wav_bytes())})
        assert r.status_code == 400, r.text
        # genre together with a prompt file
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes()),
                               "timbre": ("t.wav", _wav_bytes()),
                               "genre": ("g.wav", _wav_bytes())})
        assert r.status_code == 400, r.text
        # timbre without genre
        r = client.post("/v1/voices",
                        files={"timbre": ("t.wav", _wav_bytes())})
        assert r.status_code == 400, r.text
        # nothing at all (valid form body, no files)
        r = client.post("/v1/voices", data={"name": "x"})
        assert r.status_code == 400, r.text
