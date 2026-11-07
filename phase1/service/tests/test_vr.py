"""VocalRender adapter tests (no GPU, no real VR model).

Covers: <NOTE_4> denominator token mapping, out-of-range note
rejection (patch 05 rule: reject, never silently quantize), SP entries
in receipts (ignored=True, measured onset None, consuming no
candidate), and per-part model routing through the job API (one job
carrying a stub part and a vocalrender part at once).
"""
import io
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.adapters.base import BaseAdapter  # noqa: E402
from app.adapters.vocalrender import VocalRenderAdapter  # noqa: E402
from app.adapters.stub import StubAdapter  # noqa: E402
from app.jobs import JobStore  # noqa: E402
from app.schemas import PartIn, ScoreIn, NoteIn  # noqa: E402

SR48 = 48000


def _note(lyric, midi, start, dur, phoneme=None):
    return NoteIn(lyric=lyric, midi=midi, start_s=start, dur_s=dur,
                  phoneme=phoneme)


def _part(model, notes, takes=1, seeds=None, bpm=120.0):
    return PartIn(label="p", score=ScoreIn(bpm=bpm, notes=notes),
                  voice_id="v", model=model, takes=takes, seeds=seeds)


def test_vr_note_tokens_denominator_system():
    """1 beat -> <NOTE_4>, not a note name; dotted values map too."""
    from app.score_converter import vr_label_entry
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 64, 0.0, 0.5),   # 1 beat
        _note("啊", 67, 0.5, 0.25),  # 0.5 beat
        _note("啊", 64, 0.75, 1.0),  # 2 beats
        _note("啊", 67, 1.75, 0.75),  # 1.5 beats
        _note("啊", 64, 2.5, 0.125),  # 0.25 beat
        _note("<SP>", 0, 2.625, 0.5),
    ])
    entry = vr_label_entry(score, "lingren_p")
    assert entry["note"] == ["<NOTE_4>", "<NOTE_8>", "<NOTE_2>",
                             "<NOTE_DOT_4>", "<NOTE_16>", "<NOTE_4>"]
    assert entry["word"][-1] == "SP" and entry["pitch"][-1] == 0
    assert entry["pitch2word"] == [0, 1, 2, 3, 4, 5]
    assert entry["bpm"] == 120.0


def test_vr_adapter_rejects_out_of_range_note(tmp_path, monkeypatch):
    """A 7-beat note must fail loudly, not quantize to <NOTE_DOT_1>."""
    monkeypatch.delenv("VR_REPO", raising=False)
    monkeypatch.delenv("VR_CKPT_DIR", raising=False)
    part = _part("vocalrender", [_note("啊", 64, 0.0, 3.5)])  # 7 beats
    adapter = VocalRenderAdapter()
    try:
        adapter.synthesize(part, {"audio_path": str(tmp_path / "x.wav")},
                           0, tmp_path / "out.wav", tmp_path)
    except Exception as e:
        assert "outside" in str(e) and "beats" in str(e)
    else:
        raise AssertionError("out-of-range note was not rejected")


def test_vr_adapter_missing_env(tmp_path, monkeypatch):
    """Valid score but no VR_REPO -> loud AdapterError, not a crash."""
    from app.adapters.base import AdapterError
    monkeypatch.delenv("VR_REPO", raising=False)
    monkeypatch.delenv("VR_CKPT_DIR", raising=False)
    prompt = tmp_path / "prompt.wav"
    sf.write(str(prompt), np.zeros(SR48, dtype=np.float32), SR48)
    part = _part("vocalrender", [_note("啊", 64, 0.0, 0.5)])
    try:
        VocalRenderAdapter().synthesize(
            part, {"audio_path": str(prompt)},
            0, tmp_path / "out.wav", tmp_path)
    except AdapterError as e:
        assert "VR_REPO" in str(e)
    else:
        raise AssertionError("missing VR_REPO was not reported")


def _burst_audio():
    """Three sung bursts separated by silence, with an SP gap.

    Score: sung 0.5-1.4, SP 1.5-2.0, sung 2.5-3.4, sung 3.5-4.4.
    """
    x = np.zeros(int(4.6 * SR48), dtype=np.float32)
    for start, freq in [(0.5, 220.0), (2.5, 261.6), (3.5, 293.7)]:
        t = np.arange(int(0.9 * SR48)) / SR48
        s0 = int(start * SR48)
        x[s0:s0 + len(t)] += (0.7 * np.sin(2 * np.pi * freq * t)
                              ).astype(np.float32)
    return x


def test_alignment_sp_ignored_vocalrender():
    part = _part("vocalrender", [
        _note("啊", 64, 0.5, 0.9), _note("<SP>", 0, 1.5, 0.5),
        _note("啊", 67, 2.5, 0.9), _note("啊", 64, 3.5, 0.9)])
    aligned = JobStore._alignment(part, _burst_audio(), SR48)
    assert len(aligned) == 4
    sp = aligned[1]
    assert sp["lyric"] == "<SP>" and sp["ignored"] is True
    assert sp["measured_onset_s"] is None and sp["deviation_ms"] is None
    sung = [a for a in aligned if not a["ignored"]]
    assert len(sung) == 3
    assert all(a["lyric"] == "啊" for a in sung)
    assert sum(1 for a in sung if a["measured_onset_s"] is not None) >= 1


class FakeVRAdapter(BaseAdapter):
    """48 kHz pseudo-singing stand-in for the real VR adapter."""

    name = "vocalrender"

    def synthesize(self, part, voice, seed, out_path, workdir):
        notes = sorted(part.score.notes, key=lambda n: n.start_s)
        total = max((n.start_s + n.dur_s for n in notes), default=0.0)
        rng = np.random.default_rng(seed)
        x = np.zeros(int(np.ceil(total * SR48)) + SR48, dtype=np.float64)
        for note in notes:
            if note.lyric in ("", "<SP>") or note.midi <= 0:
                continue
            jitter = float(rng.uniform(-0.020, 0.020))
            gain = float(rng.uniform(0.88, 1.12))
            f0 = 440.0 * 2.0 ** ((note.midi - 69) / 12.0)
            t = np.arange(int(note.dur_s * SR48)) / SR48
            vib = 1.0 + 0.006 * np.sin(2 * np.pi * 5.5 * t
                                       + rng.uniform(0, 2 * np.pi))
            env = np.minimum(1.0, t / 0.03) * np.exp(-t * 0.15)
            s0 = int(max(0.0, note.start_s + jitter) * SR48)
            sig = gain * env * np.sin(2 * np.pi * f0 * vib * t)
            s1 = min(len(x), s0 + len(sig))
            x[s0:s1] += sig[:s1 - s0]
        peak = np.max(np.abs(x)) if len(x) else 0.0
        if peak > 0:
            x = x / peak * 0.7
        sf.write(str(out_path), x.astype(np.float32), SR48)
        return out_path, SR48, {"adapter": "vocalrender-fake",
                                "seed": seed}


def _wav_bytes(sr=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        x = (0.3 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr) * 32767)
        w.writeframes(x.astype(np.int16).tobytes())
    buf.seek(0)
    return buf.read()


def _notes3():
    return [{"lyric": "啊", "midi": 64, "start_s": 0.0, "dur_s": 0.9,
             "phoneme": "zh_a1"},
            {"lyric": "啊", "midi": 67, "start_s": 1.0, "dur_s": 0.9,
             "phoneme": "zh_a1"},
            {"lyric": "啊", "midi": 64, "start_s": 2.0, "dur_s": 0.9,
             "phoneme": "zh_a1"}]


def test_api_mixed_model_job(tmp_path, monkeypatch):
    """One job, two parts, two models: routing is per part."""
    from app import main as main_mod
    from app import jobs as jobs_mod
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    main_mod.store = JobStore(tmp_path)
    monkeypatch.setattr(
        jobs_mod, "_adapters",
        lambda: {"soulx": StubAdapter(), "vocalrender": FakeVRAdapter(),
                 "stub": StubAdapter()})
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices", files={"file": ("p.wav",
                                                     _wav_bytes())})
        assert r.status_code == 200, r.text
        voice_id = r.json()["voice_id"]
        parts = [
            {"label": "lead",
             "score": {"bpm": 120.0, "language": "Mandarin",
                       "notes": _notes3()},
             "voice_id": voice_id, "model": "stub", "takes": 2,
             "seeds": [0, 1]},
            {"label": "harm",
             "score": {"bpm": 120.0, "language": "Mandarin",
                       "notes": _notes3()},
             "voice_id": voice_id, "model": "vocalrender", "takes": 2,
             "seeds": [0, 1]},
        ]
        r = client.post("/v1/synthesize", json={"parts": parts})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        import time
        for _ in range(300):
            st = client.get(f"/v1/jobs/{job_id}").json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert st["status"] == "completed", st
        assert st["takes_completed"] == 4
        rec = client.get(f"/v1/jobs/{job_id}/receipt").json()
        assert rec["parts"]["lead"]["model"] == "stub"
        harm = rec["parts"]["harm"]
        assert harm["model"] == "vocalrender"
        assert len(harm["takes"]) == 2
        assert harm["takes"][0]["sample_rate"] == SR48
        assert harm["gate_pairwise"], "VR part must carry gate records"
        for entry in harm["takes"][0]["alignment"]:
            assert "ignored" in entry and "lyric" in entry
        au = client.get(f"/v1/jobs/{job_id}/audio?part=harm&take=1")
        assert au.status_code == 200 and len(au.content) > 1000


def test_api_vr_out_of_range_fails_loudly(tmp_path, monkeypatch):
    """Real VR adapter + 7-beat note through the API -> failed job
    with the rejection reason in the receipt (no fake audio)."""
    from app import main as main_mod
    monkeypatch.setenv("SVS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VR_REPO", raising=False)
    monkeypatch.delenv("VR_CKPT_DIR", raising=False)
    main_mod.store = JobStore(tmp_path)
    with TestClient(main_mod.app) as client:
        r = client.post("/v1/voices", files={"file": ("p.wav",
                                                     _wav_bytes())})
        voice_id = r.json()["voice_id"]
        part = {"label": "vr",
                "score": {"bpm": 120.0, "language": "Mandarin",
                          "notes": [{"lyric": "啊", "midi": 64,
                                     "start_s": 0.0, "dur_s": 3.5}]},
                "voice_id": voice_id, "model": "vocalrender", "takes": 1}
        r = client.post("/v1/synthesize", json={"parts": [part]})
        job_id = r.json()["job_id"]
        import time
        for _ in range(300):
            st = client.get(f"/v1/jobs/{job_id}").json()
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert st["status"] == "failed", st
        assert "outside" in (st.get("error") or "")
        rec = client.get(f"/v1/jobs/{job_id}/receipt")
        assert rec.status_code == 200
        assert "outside" in rec.json()["error"]
