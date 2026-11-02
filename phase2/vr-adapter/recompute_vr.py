#!/usr/bin/env python3
"""Local recomputation check for the Phase 2 VR E2E receipt.

Re-runs the phase2/service gate + alignment code on the downloaded VR
take wavs and compares against e2e_receipt_vr.json. This verifies the
server's arithmetic, not phonetic truth: alignment candidates come
from the same detector, so agreement proves the receipt was computed
from these exact wavs by this code.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "service"))
import soundfile as sf  # noqa: E402
from app.gate import check_pair  # noqa: E402
from app.jobs import JobStore  # noqa: E402
from app.schemas import NoteIn, PartIn, ScoreIn  # noqa: E402

EV = HERE / "evidence"


def main():
    rec = json.loads((EV / "e2e_receipt_vr.json").read_text())
    vr = rec["parts"]["vr"]
    wavs = []
    for t in vr["takes"]:
        data, sr = sf.read(str(EV / f"e2e_vr_take{t['take']}.wav"),
                           dtype="float32")
        wavs.append((data[:, 0] if data.ndim == 2 else data, sr))
    gate = check_pair(wavs[1][0], wavs[1][1], wavs[0][0], wavs[0][1])
    srv_gate = vr["gate_pairwise"][0]["vs_accepted"][0]
    print("local gate :", json.dumps(gate, ensure_ascii=False))
    print("server gate:", json.dumps(srv_gate, ensure_ascii=False))
    for k in ("bit_identical", "envelope_corr", "f0_diff_cents",
              "onset_diff_ms", "passed"):
        assert gate[k] == srv_gate[k], (k, gate[k], srv_gate[k])
    print("GATE MATCH")

    seq = [("你", 64), ("好", 67), ("你", 64), ("好", 67)]
    notes = [NoteIn(lyric=l, midi=m, start_s=i * 0.5, dur_s=0.5)
             for i, (l, m) in enumerate(seq)]
    notes.append(NoteIn(lyric="<SP>", midi=0, start_s=2.0, dur_s=0.5))
    notes += [NoteIn(lyric=l, midi=m, start_s=2.5 + j * 0.5, dur_s=0.5)
              for j, (l, m) in enumerate(seq)]
    part = PartIn(label="vr", score=ScoreIn(bpm=120.0, notes=notes),
                  voice_id="v", model="vocalrender", takes=2)
    for i, (mono, sr) in enumerate(wavs):
        local = JobStore._alignment(part, mono, sr)
        srv = vr["takes"][i]["alignment"]
        assert len(local) == len(srv) == 9
        for a, b in zip(local, srv):
            assert a["lyric"] == b["lyric"], (a, b)
            assert a["ignored"] == b["ignored"], (a, b)
            assert a["measured_onset_s"] == b["measured_onset_s"], (a, b)
            assert a["deviation_ms"] == b["deviation_ms"], (a, b)
        print(f"ALIGNMENT TAKE{i} MATCH "
              f"(sung measured: "
              f"{sum(1 for a in local if not a['ignored'] and a['measured_onset_s'] is not None)}/8, "
              f"SP ignored: {[a for a in local if a['ignored']]})")
    print("RECOMPUTE OK")


if __name__ == "__main__":
    main()
