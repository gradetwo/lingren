#!/usr/bin/env python3
"""Phase 1 end-to-end: service (SoulX adapter) via HTTP on Colab.

Uploads the official zh_prompt voice (audio + prompt meta), submits one
part (4-note score, soulx, takes=2, seeds 0/1), waits, fetches receipt +
both take wavs. Prints E2E_RESULT JSON as the last line.
"""
import json
import sys

sys.path.insert(0, "/content/lingren_service")
from client.svs_client import LingrenClient  # noqa: E402

BASE = "http://127.0.0.1:8000"


def main():
    c = LingrenClient(BASE)
    v = c.upload_voice("/content/soulx/example/audio/zh_prompt.mp3",
                       "/content/soulx/example/audio/zh_prompt.json",
                       name="demo-zh")
    print("voice:", v["voice_id"], "soulx_ready:", v["soulx_ready"])
    notes = []
    for i, (lyric, midi, ph) in enumerate([("你", 64, "zh_ni3"),
                                           ("好", 67, "zh_hao3"),
                                           ("你", 64, "zh_ni3"),
                                           ("好", 67, "zh_hao3")]):
        notes.append({"lyric": lyric, "midi": midi, "start_s": i * 0.5,
                      "dur_s": 0.5, "phoneme": ph})
    part = {"label": "lead",
            "score": {"bpm": 120.0, "language": "Mandarin",
                      "notes": notes},
            "voice_id": v["voice_id"], "model": "soulx",
            "takes": 2, "seeds": [0, 1], "options": {}}
    job = c.synthesize([part])
    print("job:", job["job_id"])
    st = c.wait(job["job_id"], timeout_s=1500, poll_s=5)
    print("final status:", st["status"], "error:", st.get("error"))
    res = {"job_id": job["job_id"], "status": st["status"],
           "error": st.get("error"), "takes_completed": st["takes_completed"]}
    if st["status"] == "completed":
        rec = c.fetch_receipt(job["job_id"])
        takes = rec["parts"]["lead"]["takes"]
        res["receipt_takes"] = [
            {"take": t["take"], "seed": t["seed"],
             "attempts": t["attempts"], "duration_s": t["duration_s"],
             "f0_shift": t["adapter_info"].get("f0_shift_semitones")}
            for t in takes]
        res["gate_pairwise"] = rec["parts"]["lead"]["gate_pairwise"]
        for t in takes:
            au = c.fetch_audio(job["job_id"], "lead", t["take"])
            res[f"audio_take{t['take']}_bytes"] = len(au)
    print("E2E_RESULT " + json.dumps(res))


if __name__ == "__main__":
    main()
