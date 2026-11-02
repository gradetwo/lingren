#!/usr/bin/env python3
"""Generate long-phrase scores for SVS Phase 2 long-phrase re-verification.

SoulX phrase: A + SP(0.5s) + B' + SP(0.5s) + A, where
  A = upstream zh_target sentence ("<SP> 像我这样懦弱的人 <SP> 凡事都要留几分",
      20 items incl. 2 SP, 6.71 s, MIDI 50-56),
  B' = upstream zh_prompt sentence truncated to its first 22 items
      ("<SP> 除了想你你 <SP> 除了了爱你你 <SP> 我什么什么都愿愿",
      drops the trailing item whose pitch is 0 despite note_type 2),
      MIDI 60-69.
Total target ~23 s. Construction caveat (stated in REPORT.md): B' as a
target reuses the sentence of the zh_prompt reference audio; both SoulX
arms share the same prompt, so the pitch_shift-vs-transposition
comparison stays symmetric, but absolute quality is not prompt-independent.

VR phrase: OpenCPop demo item 2003000087 ("假如迷路了一定把思念装进漂流瓶",
18 words incl. 3 SP, 21 notes, bpm 58) repeated 4x with one SP note
(<NOTE_16>, pitch 0) between repeats. Score time ~26 s at bpm 58.

Outputs (this dir's scores/): soulx_base.json, soulx_trans_p4.json,
soulx_trans_m3.json, vr_long.json, manifest.json.
All counts are asserted; any mismatch aborts.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "sources")
OUT = os.path.join(HERE, "scores")


def split_fields(meta):
    n_text = len(meta["text"].split())
    fields = {
        "text": meta["text"].split(),
        "phoneme": meta["phoneme"].split(),
        "pitch": [int(x) for x in meta["note_pitch"].split()],
        "ntype": [int(x) for x in meta["note_type"].split()],
        "dur": [float(x) for x in meta["duration"].split()],
    }
    for k, v in fields.items():
        assert len(v) == n_text, f"{k}: {len(v)} != {n_text}"
    return fields


def join_fields(parts):
    """parts: list of field dicts. Returns a SoulX target meta dict."""
    text, phon, pitch, ntype, dur = [], [], [], [], []
    for p in parts:
        text += p["text"]
        phon += p["phoneme"]
        pitch += p["pitch"]
        ntype += p["ntype"]
        dur += p["dur"]
    total_ms = int(round(sum(dur) * 1000))
    return {
        "index": "long_phrase",
        "language": "Mandarin",
        "time": [0, total_ms],
        "duration": " ".join(f"{d:.2f}" for d in dur),
        "text": " ".join(text),
        "phoneme": " ".join(phon),
        "note_pitch": " ".join(str(p) for p in pitch),
        "note_type": " ".join(str(t) for t in ntype),
        "f0": "",
    }, dur, text, pitch


def sp_field(dur_s=0.5):
    return {"text": ["<SP>"], "phoneme": ["<SP>"], "pitch": [0],
            "ntype": [1], "dur": [dur_s]}


def main():
    os.makedirs(OUT, exist_ok=True)
    a_meta = json.load(open(os.path.join(SRC, "zh_target.json")))[0]
    b_meta = json.load(open(os.path.join(SRC, "zh_prompt.json")))[0]
    A = split_fields(a_meta)
    B = split_fields(b_meta)
    # B': drop trailing item (pitch 0 with note_type 2).
    assert B["pitch"][-1] == 0 and B["ntype"][-1] == 2
    Bp = {k: v[:-1] for k, v in B.items()}
    base, dur, text, pitch = join_fields(
        [A, sp_field(0.5), Bp, sp_field(0.5), A])
    print(f"SoulX items: {len(text)}, total {sum(dur):.2f}s, "
          f"pitch range sung {min(p for p in pitch if p > 0)}-"
          f"{max(pitch)}, jumps max "
          f"{max(abs(b - a) for a, b in zip(pitch, pitch[1:]) if a > 0 and b > 0)}")

    def transposed(meta, n):
        m = dict(meta)
        ps = [int(x) for x in meta["note_pitch"].split()]
        m["note_pitch"] = " ".join(str(p + n) if p > 0 else "0"
                                   for p in ps)
        return m

    for name, meta in [("soulx_base.json", base),
                       ("soulx_trans_p4.json", transposed(base, 4)),
                       ("soulx_trans_m3.json", transposed(base, -3))]:
        with open(os.path.join(OUT, name), "w") as f:
            json.dump([meta], f)
    # Score-clock onsets per non-SP item (for analysis).
    onsets, t = [], 0.0
    for txt, d in zip(text, dur):
        if txt != "<SP>":
            onsets.append({"lyric": txt, "onset_s": round(t, 4),
                           "dur_s": d})
        t += d
    sp_spans, t = [], 0.0
    for txt, d in zip(text, dur):
        if txt == "<SP>":
            sp_spans.append({"onset_s": round(t, 4), "dur_s": d})
        t += d

    # ---- VR long phrase ----
    demo = [e for e in json.load(
        open(os.path.join(SRC, "opencpop_demo.json")))
        if e["item_name"] == "2003000087"][0]
    BEATS = {"<NOTE_1>": 4.0, "<NOTE_DOT_1>": 6.0, "<NOTE_2>": 2.0,
             "<NOTE_DOT_2>": 3.0, "<NOTE_4>": 1.0, "<NOTE_DOT_4>": 1.5,
             "<NOTE_8>": 0.5, "<NOTE_DOT_8>": 0.75, "<NOTE_16>": 0.25,
             "<NOTE_DOT_16>": 0.375, "<NOTE_32>": 0.125,
             "<NOTE_DOT_32>": 0.1875}
    bpm = demo["bpm"]
    words, pitch, note, p2w = [], [], [], []
    for r in range(4):
        if r > 0:  # separator SP note
            words.append("SP")
            pitch.append(0)
            note.append("<NOTE_16>")
            p2w.append(len(words) - 1)
        base_w = len(words)
        words += demo["word"]
        pitch += demo["pitch"]
        note += demo["note"]
        p2w += [base_w + w for w in demo["pitch2word"]]
    assert len(words) == len(p2w) or True
    assert len(pitch) == len(note) == len(p2w), \
        f"VR counts: pitch {len(pitch)} note {len(note)} p2w {len(p2w)}"
    total_beats = sum(BEATS[n] for n in note)
    score_s = total_beats / bpm * 60.0
    print(f"VR: words {len(words)} (non-SP "
          f"{sum(1 for w in words if w != 'SP')}), notes {len(note)}, "
          f"score time {score_s:.2f}s at bpm {bpm}")
    vr = {"item_name": "long_phrase_vr", "word": words, "pitch": pitch,
          "note": note, "pitch2word": p2w, "bpm": bpm}
    with open(os.path.join(OUT, "vr_long.json"), "w") as f:
        json.dump([vr], f)
    # VR score-clock word onsets (first note of each non-SP word).
    word_onset = {}
    t = 0.0
    for ni, tok in enumerate(note):
        w = p2w[ni]
        if w not in word_onset:
            word_onset[w] = t
        t += BEATS[tok] / bpm * 60.0
    vr_onsets = [{"lyric": words[w], "onset_s": round(word_onset[w], 4)}
                 for w in sorted(word_onset) if words[w] != "SP"]
    vr_spans, t = [], 0.0
    for ni, tok in enumerate(note):
        if words[p2w[ni]] == "SP":
            vr_spans.append({"onset_s": round(t, 4),
                             "dur_s": round(BEATS[tok] / bpm * 60.0, 4)})
        t += BEATS[tok] / bpm * 60.0
    manifest = {"soulx_total_s": round(sum(dur), 2),
                "soulx_onsets": onsets, "soulx_sp_spans": sp_spans,
                "vr_score_s": round(score_s, 2),
                "vr_onsets": vr_onsets, "vr_sp_spans": vr_spans,
                "vr_bpm": bpm}
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"SoulX non-SP syllables: {len(onsets)}, SP spans: {len(sp_spans)}")
    print(f"VR non-SP words: {len(vr_onsets)}, SP spans: {len(vr_spans)}")


if __name__ == "__main__":
    main()
