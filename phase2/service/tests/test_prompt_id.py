"""Prompt-id registry tests with the fake SVC driver (no GPU).

Covers: every completed svc-prompt job registers its pair under a
prompt_id served at GET /v1/prompts (+ info + audio); the registry
reloads from disk on restart; a later svc-prompt call may pass that
prompt_id instead of timbre audio; both/neither/unknown prompt_id
fail loudly (400/400/404); the voices genre path and the plain
voice file upload also register prompts, with the voice entry
carrying prompt_id. Conversion quality is out of scope here (fake
driver); the real-model check is the pilot report.
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
        tmp_path, on_voice_ready=main_mod.store.register_voice_files,
        on_prompt_ready=main_mod.store.prompt_store.register_prompt_files)
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


def test_svc_prompt_registers_prompt_and_serves_it(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        assert r.json()["prompt_id"]
        job_id = r.json()["job_id"]
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["prompt_id"] == r.json()["prompt_id"]
        assert st["prompt_status"] == "registered"
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["prompt_id"] == r.json()["prompt_id"]
        assert rec["prompt_status"] == "registered"
        pid = r.json()["prompt_id"]
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["prompt_id"] == pid]
        assert len(rows) == 1
        assert rows[0]["source"] == "svc-prompt"
        assert rows[0]["voice_id"] is None
        info = client.get(f"/v1/prompts/{pid}").json()
        assert info["prompt_id"] == pid
        au = client.get(f"/v1/prompts/{pid}/audio")
        assert au.status_code == 200
        assert au.content[:4] == b"RIFF"
        assert au.content == client.get(
            f"/v1/jobs/{job_id}/prompt-audio").content
        assert client.get("/v1/prompts/nope").status_code == 404
        assert client.get("/v1/prompts/nope/audio").status_code == 404


def test_prompt_registry_reloads_after_restart(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        pid = r.json()["prompt_id"]
    from app.jobs import JobStore
    reloaded = JobStore(tmp_path)
    info = reloaded.prompt_store.get_prompt(pid)
    assert info is not None
    assert info["source"] == "svc-prompt"
    assert Path(info["audio_path"]).is_file()
    assert len(reloaded.prompt_store.list_prompts()) == 1


def test_svc_prompt_by_prompt_id(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = _submit_svc(client)
        assert r.status_code == 200, r.text
        st = _poll(client, r.json()["job_id"])
        assert st["status"] == "completed", st
        pid = r.json()["prompt_id"]
        r2 = client.post("/v1/svc-prompt",
                         files={"source": ("src.wav", _wav_bytes(440.0))},
                         data={"prompt_id": pid})
        assert r2.status_code == 200, r2.text
        new_pid = r2.json()["prompt_id"]
        assert new_pid and new_pid != pid
        st2 = _poll(client, r2.json()["job_id"])
        assert st2["status"] == "completed", st2
        assert st2["timbre_prompt_id"] == pid
        rec = client.get(f"/v1/jobs/{r2.json()['job_id']}/receipt").json()
        assert rec["prompt_id"] == new_pid
        assert rec["timbre_prompt_id"] == pid
        assert rec["params"]["auto_shift"] is False
        assert rec["prompt_status"] == "registered"


def test_svc_prompt_prompt_id_validation(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        # prompt_id together with timbre audio
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes()),
                               "timbre": ("tim.wav", _wav_bytes())},
                        data={"prompt_id": "abc123"})
        assert r.status_code == 400, r.text
        # neither prompt_id nor timbre audio
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes())})
        assert r.status_code == 400, r.text
        # unknown prompt_id
        r = client.post("/v1/svc-prompt",
                        files={"source": ("src.wav", _wav_bytes())},
                        data={"prompt_id": "nosuchid"})
        assert r.status_code == 404, r.text


def test_voices_genre_carries_prompt_id(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"timbre": ("t.wav", _wav_bytes(660.0)),
                               "genre": ("g.wav", _wav_bytes(440.0))},
                        data={"name": "auto"})
        assert r.status_code == 200, r.text
        body = r.json()
        voice_id, job_id, pid = (body["voice_id"], body["job_id"],
                                 body["prompt_id"])
        st = _poll(client, job_id)
        assert st["status"] == "completed", st
        assert st["voice_status"] == "registered"
        assert st["prompt_id"] == pid
        assert st["prompt_status"] == "registered"
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["prompt_id"] == pid
        voices = client.get("/v1/voices").json()["voices"]
        info = [v for v in voices if v["voice_id"] == voice_id]
        assert len(info) == 1
        assert info[0]["prompt_id"] == pid
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["prompt_id"] == pid]
        assert len(rows) == 1
        assert rows[0]["source"] == "voices-genre"
        assert rows[0]["voice_id"] == voice_id


def test_voice_upload_registers_prompt(monkeypatch, tmp_path):
    main_mod = _setup(monkeypatch, tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices",
                        files={"file": ("p.wav", _wav_bytes())})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prompt_id"]
        prompts = client.get("/v1/prompts").json()["prompts"]
        rows = [p for p in prompts if p["prompt_id"] == body["prompt_id"]]
        assert len(rows) == 1
        assert rows[0]["source"] == "voice-upload"
        assert rows[0]["voice_id"] == body["voice_id"]
        au = client.get(f"/v1/prompts/{body['prompt_id']}/audio")
        assert au.status_code == 200 and au.content[:4] == b"RIFF"
