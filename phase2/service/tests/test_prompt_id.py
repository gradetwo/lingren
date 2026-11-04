"""Voice-id unification tests with the fake SVC driver (no GPU).

One id only: voice_id is the id. Covers: every completed svc-prompt
job registers its pair as a voice served at GET /v1/voices and at
the GET /v1/prompts alias (+ info + audio); the voices registry
reloads from disk on restart; a later svc-prompt call may pass that
voice_id instead of timbre audio, and the produced voice_id sings
directly via /v1/synthesize; both/neither/unknown voice_id fail
loudly (400/400/404); the voices genre response carries no second
id; the plain voice file upload appears in both lists.
Conversion quality is out of scope here (fake driver); the
real-model check is the pilot report.
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


def _submit_svc(client, **data):
    return client.post("/v1/svc-prompt",
                       files={"source": ("src.wav", _wav_bytes(440.0)),
                              "timbre": ("tim.wav", _wav_bytes(660.0))},
                       data=data)


def test_svc_prompt_registers_voice_and_serves_it(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        assert r.json()["voice_id"]
        job_id = r.json()["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["voice_id"] == r.json()["voice_id"]
        assert st["voice_status"] == "registered"
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["voice_id"] == r.json()["voice_id"]
        assert rec["voice_status"] == "registered"
        vid = r.json()["voice_id"]
        voices = client.get("/v1/voices").json()["voices"]
        rows = [v for v in voices if v["voice_id"] == vid]
        assert len(rows) == 1
        assert rows[0]["source"] == "svc-prompt"
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["voice_id"] == vid]
        assert len(rows) == 1
        assert rows[0]["source"] == "svc-prompt"
        info = client.get(f"/v1/prompts/{vid}").json()
        assert info["voice_id"] == vid
        au = client.get(f"/v1/prompts/{vid}/audio")
        assert au.status_code == 200
        assert au.content[:4] == b"RIFF"
        assert au.content == client.get(
            f"/v1/jobs/{job_id}/prompt-audio").content
        assert client.get("/v1/prompts/nope").status_code == 404
        assert client.get("/v1/prompts/nope/audio").status_code == 404


def test_voice_registry_reloads_after_restart(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        vid = r.json()["voice_id"]
    from app.jobs import JobStore
    reloaded = JobStore(tmp_path)
    info = reloaded.voices.get(vid)
    assert info is not None
    assert info["source"] == "svc-prompt"
    assert Path(info["audio_path"]).is_file()
    assert len(reloaded.voices) == 1


def test_svc_prompt_by_voice_id_and_direct_synthesis(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        vid = r.json()["voice_id"]
        r2 = client.post("/v1/svc-prompt",
                         files={"source": ("src.wav", _wav_bytes(440.0))},
                         data={"voice_id": vid})
        assert r2.status_code == 200, r2.text
        new_vid = r2.json()["voice_id"]
        assert new_vid and new_vid != vid
        st2 = _poll(client, r2.json()["job_id"])
        assert st2["status"] == "completed", st2
        assert st2["timbre_voice_id"] == vid
        rec = client.get(f"/v1/jobs/{r2.json()['job_id']}/receipt").json()
        assert rec["voice_id"] == new_vid
        assert rec["timbre_voice_id"] == vid
        assert rec["params"]["auto_shift"] is False
        assert rec["voice_status"] == "registered"
        part = {"label": "lead",
                "score": {"bpm": 120.0, "language": "Mandarin",
                          "notes": [{"lyric": "啊", "midi": 64,
                                     "start_s": 0.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"},
                                    {"lyric": "啊", "midi": 67,
                                     "start_s": 1.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"}]},
                "voice_id": new_vid, "model": "stub", "takes": 1,
                "seeds": [0]}
        r3 = client.post("/v1/synthesize", json={"parts": [part]})
        assert r3.status_code == 200, r3.text
        st3 = _poll(client, r3.json()["job_id"])
        assert st3["status"] == "completed", st3
        assert st3["takes_completed"] == 1


def test_svc_prompt_voice_id_validation(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        # voice_id together with timbre audio
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes()),
                               "timbre": ("tim.wav", _wav_bytes())},
                        data={"voice_id": "abc123"})
        assert r.status_code == 400, r.text
        # neither voice_id nor timbre audio
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes())})
        assert r.status_code == 400, r.text
        # unknown voice_id
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes())},
                        data={"voice_id": "nosuchid"})
        assert r.status_code == 404, r.text


def test_voices_genre_response_has_no_second_id(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"timbre": ("t.wav", _wav_bytes(660.0)),
                               "genre": ("g.wav", _wav_bytes(440.0))},
                        data={"name": "auto"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"voice_id", "job_id", "status"}, body
        voice_id, job_id = body["voice_id"], body["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["voice_status"] == "registered"
        assert "prompt_id" not in st
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["voice_id"] == voice_id
        assert rec["voice_status"] == "registered"
        assert "prompt_id" not in rec
        voices = client.get("/v1/voices").json()["voices"]
        info = [v for v in voices if v["voice_id"] == voice_id]
        assert len(info) == 1
        assert info[0]["source"] == "voices-genre"
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["voice_id"] == voice_id]
        assert len(rows) == 1
        assert rows[0]["source"] == "voices-genre"


def test_voice_upload_appears_in_both_lists(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        wav = _wav_bytes()
        r = client.post("/v1/voices", files={"file": ("p.wav", wav)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["voice_id"]
        assert body["source"] == "voice-upload"
        voices = client.get("/v1/voices").json()["voices"]
        rows = [v for v in voices if v["voice_id"] == body["voice_id"]]
        assert len(rows) == 1
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["voice_id"] == body["voice_id"]]
        assert len(rows) == 1
        au = client.get(f"/v1/prompts/{body['voice_id']}/audio")
        assert au.status_code == 200 and au.content == wav
