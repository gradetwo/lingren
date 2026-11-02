"""API test with the stub adapter (no GPU)."""
import io
import sys
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app  # noqa: E402

SR = 24000


def _wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        x = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR) / SR) * 32767)
        w.writeframes(x.astype(np.int16).tobytes())
    buf.seek(0)
    return buf.read()


def test_submit_poll_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    # Re-point the store's dirs (store was built at import with env default).
    from app import main as main_mod
    from app.jobs import JobStore
    main_mod.store = JobStore(tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices", files={"file": ("p.wav", _wav_bytes())})
        assert r.status_code == 200, r.text
        voice_id = r.json()["voice_id"]
        part = {"label": "lead",
                "score": {"bpm": 120.0, "language": "Mandarin",
                          "notes": [{"lyric": "啊", "midi": 64,
                                     "start_s": 0.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"},
                                    {"lyric": "啊", "midi": 67,
                                     "start_s": 1.0, "dur_s": 1.0,
                                     "phoneme": "zh_a1"}]},
                "voice_id": voice_id, "model": "stub", "takes": 2,
                "seeds": [0, 1]}
        r = client.post("/v1/synthesize", json={"parts": [part]})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        import time
        for _ in range(300):
            st = client.get(f"/v1/jobs/{job_id}").json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert st["status"] == "completed", st
        assert st["takes_completed"] == 2
        rec = client.get(f"/v1/jobs/{job_id}/receipt")
        assert rec.status_code == 200
        takes = rec.json()["parts"]["lead"]["takes"]
        assert len(takes) == 2
        assert takes[0]["alignment"][0]["lyric"] == "啊"
        au = client.get(f"/v1/jobs/{job_id}/audio?part=lead&take=1")
        assert au.status_code == 200 and len(au.content) > 1000
