"""Unified score JSON -> model-native inputs (Phase 1).

SoulX target_meta (one segment; repo format, see cli/inference.py +
DataProcessor): keys index/language/time/duration/text/phoneme/
note_pitch/note_type/f0. Score mode ignores f0. note_type: 1 = <SP>,
2 = normal note, 3 = tied continuation ('-' lyric in the repo's
midi_parser convention).
VocalRender label entry: word/pitch/note/pitch2word/bpm (Phase 2 wire-up;
token strings are verified against the VR preprocessor in
patches/05-vocalrender-note-tokens.md before use).
"""

from __future__ import annotations

import re
from functools import lru_cache

from .schemas import ScoreIn

_CJK = re.compile(r"[\u4e00-\u9fff]")

# Beat value -> VocalRender note token (nearest-quantize table).
# Verified against vr/src/vocalrender/model/svs_utils.py
# (get_svs_token_maps): denominator-numbered tokens, NOT note names.
NOTE_TOKENS = [
    (6.0, "<NOTE_DOT_1>"),
    (4.0, "<NOTE_1>"),
    (3.0, "<NOTE_DOT_2>"),
    (2.0, "<NOTE_2>"),
    (1.5, "<NOTE_DOT_4>"),
    (1.0, "<NOTE_4>"),
    (0.75, "<NOTE_DOT_8>"),
    (0.5, "<NOTE_8>"),
    (0.375, "<NOTE_DOT_16>"),
    (0.25, "<NOTE_16>"),
    (0.1875, "<NOTE_DOT_32>"),
    (0.125, "<NOTE_32>"),
]


@lru_cache(maxsize=1)
def _mandarin_g2p():
    """The serial worker reuses one G2P model instead of loading per syllable."""
    try:
        from g2pM import G2pM  # type: ignore
    except ImportError as e:
        raise ValueError(
            "g2pM is not installed and note has no explicit phoneme; "
            "supply note.phoneme (e.g. zh_zhi3) or install g2pM") from e
    return G2pM()


def _g2p_mandarin_char(ch: str) -> str:
    """Single-character Mandarin G2P; explicit phonemes still take priority."""
    res = _mandarin_g2p()(ch, tone=True, char_split=False)
    if not res:
        raise ValueError(f"G2P produced nothing for {ch!r}")
    return "zh_" + res[0]


def _note_phoneme(note, language: str) -> str:
    if note.phoneme:
        return note.phoneme
    lyric = note.lyric
    if lyric in ("", "<SP>"):
        return "<SP>"
    if len(lyric) == 1 and _CJK.fullmatch(lyric):
        if language == "Cantonese":
            try:
                import ToJyutping  # type: ignore
            except ImportError as e:
                raise ValueError(
                    "ToJyutping is not installed and note has no explicit "
                    "phoneme") from e
            jy = ToJyutping.get_jyutping_list(lyric)
            if not jy:
                raise ValueError(f"Jyutping produced nothing for {lyric!r}")
            return "yue_" + jy[0][1]
        return _g2p_mandarin_char(lyric)
    if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*", lyric):
        try:
            from g2p_en import G2p as G2pE  # type: ignore
        except ImportError as e:
            raise ValueError(
                "g2p_en is not installed and note has no explicit phoneme"
            ) from e
        return "en_" + "-".join(G2pE()(lyric.lower()))
    raise ValueError(
        f"Cannot derive a phoneme for lyric {lyric!r}; supply note.phoneme")


def soulx_target_meta(score: ScoreIn, transpose: int = 0) -> dict:
    """One unified-score part -> one SoulX target_meta segment."""
    items = []  # (phoneme, dur_s, pitch, type, text)
    prev_end = 0.0
    prev_phoneme, prev_pitch = "<SP>", 0
    for note in sorted(score.notes, key=lambda n: n.start_s):
        if note.start_s > prev_end + 1e-6:
            gap = note.start_s - prev_end
            items.append(("<SP>", gap, 0, 1, "<SP>"))
        is_rest = note.lyric in ("", "<SP>") or note.midi <= 0
        if is_rest:
            items.append(("<SP>", note.dur_s, 0, 1, "<SP>"))
            prev_phoneme, prev_pitch = "<SP>", 0
        elif note.tie:
            items.append((prev_phoneme, note.dur_s, prev_pitch, 3,
                          items[-1][4] if items else note.lyric))
        else:
            ph = _note_phoneme(note, score.language)
            pitch = note.midi + transpose
            items.append((ph, note.dur_s, pitch, 2, note.lyric))
            prev_phoneme, prev_pitch = ph, pitch
        prev_end = max(prev_end, note.start_s + note.dur_s)
    total_ms = int(round(prev_end * 1000.0))
    return {
        "index": "lingren_part",
        "language": score.language,
        "time": [0, total_ms],
        "duration": " ".join(f"{d:.3f}" for _, d, _, _, _ in items),
        "text": " ".join(t for _, _, _, _, t in items),
        "phoneme": " ".join(p for p, _, _, _, _ in items),
        "note_pitch": " ".join(str(p) for _, _, p, _, _ in items),
        "note_type": " ".join(str(t) for _, _, _, t, _ in items),
        "f0": "",
    }


def score_onsets_seconds(score: ScoreIn) -> list[float]:
    """Score-clock onset per note (rests included, ties included)."""
    return [float(n.start_s) for n in sorted(score.notes,
                                             key=lambda n: n.start_s)]


def vr_label_entry(score: ScoreIn, item_name: str) -> dict:
    """Unified score -> VocalRender label entry (Phase 2 wire-up)."""
    beat = 60.0 / score.bpm
    words, pitches, notes, pitch2word = [], [], [], []
    for i, note in enumerate(sorted(score.notes, key=lambda n: n.start_s)):
        if note.lyric in ("", "<SP>") or note.midi <= 0:
            words.append("SP")
            pitches.append(0)
        else:
            words.append(note.lyric)
            pitches.append(note.midi)
        beats = note.dur_s / beat
        token = min(NOTE_TOKENS, key=lambda kv: abs(kv[0] - beats))[1]
        notes.append(token)
        pitch2word.append(i)
    return {"item_name": item_name, "word": words, "pitch": pitches,
            "note": notes, "pitch2word": pitch2word, "bpm": score.bpm}
