#!/usr/bin/env python3
"""Phase 2 VR-line end-to-end: service (mixed soulx + vocalrender job).

Registers the SoulX official zh_prompt voice (audio + prompt meta) and
a VocalRender voice from the official VR demo prompt audio
(vr/examples/prompt_audio/2003000081.wav -- the Phase 1 evidence prompt,
no unlicensed audio), then submits ONE job with two parts:
  lead: soulx, 4 notes (Phase 1 proven score), takes=2, seeds 0/1
  vr:   vocalrender, 8 sung notes + 1 SP rest, takes=2, seeds 0/1
Waits, fetches receipt + all take wavs, saves receipt + wavs under
/content, and prints E2E_RESULT JSON as the last line.
"""
import json
import sys

import soundfile as sf

sys.path.insert(0, "/content/lingren_service")
from client.svs_client import LingrenClient  # noqa: E402

BASE = "http://127.0.0.1:8000"


def main():
    c = LingrenClient(BASE)
    v_soulx = c.upload_voice("/content/soulx/example/audio/zh_prompt.mp3",
                             "/content/soulx/example/audio/zh_prompt.json",
                             name="demo-zh")
    print("soulx voice:", v_soulx["voice_id"],
          "soulx_ready:", v_soulx["soulx_ready"])
    vr_prompt = "/content/vr/examples/prompt_audio/2003000081.wav"
    info = sf.info(vr_prompt)
    print("vr prompt duration:", round(info.duration, 3), "s",
          info.samplerate, "Hz")
    v_vr = c.upload_voice(vr_prompt, None, name="demo-vr")
    print("vr voice:", v_vr["voice_id"],
          "soulx_ready:", v_vr["soulx_ready"])

    lead_notes = []
    for i, (lyric, midi, ph) in enumerate([("你", 64, "zh_ni3"),
                                           ("好", 67, "zh_hao3"),
                                           ("你", 64, "zh_ni3"),
                                           ("好", 67, "zh_hao3")]):
        lead_notes.append({"lyric": lyric, "midi": midi, "start_s": i * 0.5,
                           "dur_s": 0.5, "phoneme": ph})
    vr_notes = []
    seq = [("你", 64), ("好", 67), ("你", 64), ("好", 67)]
    for i, (lyric, midi) in enumerate(seq):
        vr_notes.append({"lyric": lyric, "midi": midi,
                         "start_s": i * 0.5, "dur_s": 0.5})
    vr_notes.append({"lyric": "<SP>", "midi": 0, "start_s": 2.0,
                     "dur_s": 0.5})
    for j, (lyric, midi) in enumerate(seq):
        vr_notes.append({"lyric": lyric, "midi": midi,
                         "start_s": 2.5 + j * 0.5, "dur_s": 0.5})
    parts = [
        {"label": "lead",
         "score": {"bpm": 120.0, "language": "Mandarin",
                   "notes": lead_notes},
         "voice_id": v_soulx["voice_id"], "model": "soulx",
         "takes": 2, "seeds": [0, 1], "options": {}},
        {"label": "vr",
         "score": {"bpm": 120.0, "language": "Mandarin",
                   "notes": vr_notes},
         "voice_id": v_vr["voice_id"], "model": "vocalrender",
         "takes": 2, "seeds": [0, 1], "options": {}},
    ]
    job = c.synthesize(parts)
    print("job:", job["job_id"])
    st = c.wait(job["job_id"], timeout_s=3000, poll_s=5)
    print("final status:", st["status"], "error:", st.get("error"))
    res = {"job_id": job["job_id"], "status": st["status"],
           "error": st.get("error"),
           "takes_completed": st["takes_completed"],
           "vr_prompt_duration_s": round(info.duration, 3)}
    if st["status"] == "completed":
        rec = c.fetch_receipt(job["job_id"])
        with open("/content/e2e_receipt_vr.json", "w") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
        res["parts"] = {}
        for label, pdata in rec["parts"].items():
            res["parts"][label] = {
                "model": pdata["model"],
                "takes": [{"take": t["take"], "seed": t["seed"],
                           "attempts": t["attempts"],
                           "duration_s": t["duration_s"],
                           "sample_rate": t["sample_rate"],
                           "adapter_info": t["adapter_info"]}
                          for t in pdata["takes"]],
                "gate_pairwise": pdata["gate_pairwise"]}
            for t in pdata["takes"]:
                au = c.fetch_audio(job["job_id"], label, t["take"])
                fn = f"/content/e2e_{label}_take{t['take']}.wav"
                with open(fn, "wb") as f:
                    f.write(au)
                res["parts"][label]["takes"][t["take"]][
                    "audio_bytes"] = len(au)
            if label == "vr":
                al = pdata["takes"][0]["alignment"]
                res["vr_alignment_take0"] = al
                res["vr_sp_entry"] = [a for a in al if a.get("ignored")]
                res["vr_sung_measured"] = sum(
                    1 for a in al if not a.get("ignored")
                    and a.get("measured_onset_s") is not None)
    print("E2E_RESULT " + json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
