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

import math
import re

from .schemas import ScoreIn


def n_bars_for_span(span_s: float, bar_s: float) -> int:
    """Whole bars spanned by span_s (4/4 assumed by callers).

    An exact multiple of bar_s is that many bars, not one more.
    Two kinds of noise must not add a phantom bar: float residue
    (e.g. 101.8182 / 3.63636... == 28.000005) and the input data's
    own rounding (score times are routinely rounded to 4 decimals,
    so a span can sit ~20 us past the bar line). Both are far below
    1 ms, while a genuine partial bar is at least a 16th note
    (~0.23 s at 66 BPM), so an overshoot under 1 ms is treated as
    rounding noise. The span itself is unchanged -- only the bar
    count is affected, so no audio is lost.
    """
    return max(1, math.ceil((span_s - 1e-3) / bar_s))

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


def _g2p_mandarin_char(ch: str) -> str:
    """Single-character Mandarin G2P via the SoulX repo's g2pM path."""
    try:
        from g2pM import G2pM  # type: ignore
    except ImportError as e:
        raise ValueError(
            "g2pM is not installed and note has no explicit phoneme; "
            "supply note.phoneme (e.g. zh_zhi3) or install g2pM") from e
    res = G2pM()(ch, tone=True, char_split=False)
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


def score_lyrics_text(score: ScoreIn) -> str:
    """Sung lyrics in score order, rests skipped, no separators.

    YuE2/MuLaCover adapters use this as the default lyrics text;
    callers may pass a verbatim lyrics file via options
    (yue2_lyrics / mulacover_lyrics) when line breaks and section
    tags matter -- the note-level score carries neither.
    """
    return "".join(n.lyric for n in sorted(score.notes,
                                            key=lambda n: n.start_s)
                   if n.lyric not in ("", "<SP>") and n.midi > 0)


# ---------------------------------------------------------------------------
# YuE2 native ABC (four-pipeline design section 4).
# ---------------------------------------------------------------------------

# Pitch class -> (letter, accidental), sharp spelling. Accidentals are
# always written explicitly, so the spelling is valid under any key
# signature (the r3 experiment file itself writes ^f under K:G).
_SHARP_PC = {0: ("C", ""), 1: ("C", "^"), 2: ("D", ""), 3: ("D", "^"),
             4: ("E", ""), 5: ("F", ""), 6: ("F", "^"), 7: ("G", ""),
             8: ("G", "^"), 9: ("A", ""), 10: ("A", "^"), 11: ("B", "")}

_UNITS_PER_BAR = 16  # L:1/16, 4/4


def _abc_note_token(midi: int) -> str:
    """MIDI pitch -> ABC note token without duration (no octave bug:
    uppercase = octave 4, lowercase = octave 5, ',' / \"'\" shift)."""
    letter, acc = _SHARP_PC[midi % 12]
    octave = midi // 12 - 1
    if octave <= 4:
        return acc + letter + "," * (4 - octave)
    return acc + letter.lower() + "'" * (octave - 5)


def score_to_yue2_abc(score: ScoreIn, key: str = "C", transpose: int = 12,
                      ins_abc: str | None = None,
                      section_label: str | None = None) -> str:
    """Unified score -> YuE2 native two-voice ABC (design section 4).

    Vocal: notes transposed by `transpose` (default +12, the octave
    lever), quantized to the 1/16 grid (L:1/16), split at bar lines
    with ties, rests as z (whole-bar rest Z). Ins: all rests by
    default (one Z<n> per 4-bar group, as in the r3 experiment file);
    `ins_abc` overrides it with the caller's Ins voice music lines,
    one line per 4-bar Vocal group, in order (shortfall = rests,
    surplus lines appended at the end under their own V: Ins).
    One section comment ("% <label>") heads the first group when
    `section_label` is given; the score carries no section data.
    """
    beat = 60.0 / score.bpm
    unit = beat / 4.0
    notes = sorted(score.notes, key=lambda n: n.start_s)
    # Timeline in 16th units: (start_u, dur_u, token or None for rest).
    timeline = []
    cursor = 0
    for note in notes:
        start_u = int(round(note.start_s / unit))
        dur_u = max(1, int(round(note.dur_s / unit)))
        if start_u > cursor:
            timeline.append((cursor, start_u - cursor, None))
            cursor = start_u
        if start_u < cursor:
            # Overlap after quantization: clamp to the cursor.
            dur_u -= cursor - start_u
            start_u = cursor
            if dur_u <= 0:
                continue
        is_rest = note.lyric in ("", "<SP>") or note.midi <= 0
        token = None if is_rest else _abc_note_token(note.midi + transpose)
        timeline.append((start_u, dur_u, token))
        cursor = start_u + dur_u
    total_u = cursor
    n_bars = max(1, (total_u + _UNITS_PER_BAR - 1) // _UNITS_PER_BAR)
    # Split the timeline into per-bar token lists.
    bars: list[list[str]] = [[] for _ in range(n_bars)]
    for start_u, dur_u, token in timeline:
        pos, left = start_u, dur_u
        while left > 0:
            bar = pos // _UNITS_PER_BAR
            if bar >= n_bars:
                break
            room = _UNITS_PER_BAR - pos % _UNITS_PER_BAR
            take = min(left, room)
            if token is None:
                bars[bar].append(f"z{take}")
            else:
                bars[bar].append(f"{token}{take}"
                                 + ("-" if take < left else ""))
            pos += take
            left -= take
    bar_texts = []
    for bar_tokens in bars:
        if len(bar_tokens) == 1 and bar_tokens[0] == "z16":
            bar_texts.append("Z")
        else:
            bar_texts.append(" ".join(bar_tokens))
    bpm_q = int(score.bpm) if float(score.bpm).is_integer() else score.bpm
    lines = ["X:1", "T:", "M:4/4", "L:1/16", f"Q:1/4={bpm_q}",
             "V: Vocal clef=treble name=\"Vocal Melody\" snm=\"Vocal\"",
             "V: Ins clef=treble name=\"Ins Melody\" snm=\"Inst.\"",
             f"K:{key}"]
    ins_lines = ([ln.strip() for ln in ins_abc.splitlines()
                  if ln.strip()] if ins_abc else [])
    ins_idx = 0
    for g0 in range(0, n_bars, 4):
        grp = bar_texts[g0:g0 + 4]
        if g0 == 0 and section_label:
            lines.append(f"% {section_label}")
        lines.append("V: Vocal")
        lines.append("|".join(grp) + "|")
        lines.append("V: Ins")
        if ins_idx < len(ins_lines):
            lines.append(ins_lines[ins_idx])
            ins_idx += 1
        else:
            n = len(grp)
            lines.append(f"Z{n if n > 1 else ''}|")
    while ins_idx < len(ins_lines):
        lines.append("V: Ins")
        lines.append(ins_lines[ins_idx])
        ins_idx += 1
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# MuLaCover MIDI (four-pipeline design section 5). Stdlib-only writer:
# the service venv has no mido; the byte layout copies make_slice.py
# (type 1, one track, track_name + set_tempo, note on/off events).
# ---------------------------------------------------------------------------

PPQ = 960


def score_to_midi_notes(score: ScoreIn, ppq: int = PPQ,
                        transpose: int = 0) -> list[tuple]:
    """Sung notes -> (onset_ticks, end_ticks, midi, velocity, channel).

    Rests are skipped (gaps stay gaps). Velocity 100, channel 0, as
    in the folk-A experiment melody MIDI.
    """
    beat = 60.0 / score.bpm
    out = []
    for note in sorted(score.notes, key=lambda n: n.start_s):
        if note.lyric in ("", "<SP>") or note.midi <= 0:
            continue
        on = int(round(note.start_s / beat * ppq))
        off = max(on + 1, int(round((note.start_s + note.dur_s)
                                    / beat * ppq)))
        out.append((on, off, note.midi + transpose, 100, 0))
    out.sort()
    return out


def derive_chords(score: ScoreIn) -> list[dict]:
    """Per-bar triads from the score (design section 5.2, deterministic).

    4/4 bars; per bar, the root is the pitch class with the greatest
    total duration among notes starting in the bar; major if a major
    third (root+4) is present, minor if a minor third (root+3) is
    present, both present -> the third with the greater total
    duration, neither -> major. Empty bars carry the previous bar's
    chord; an empty first bar fails loudly. Sevenths are ignored.
    Returns [{"bar": i, "root": 0-11, "quality": "major"|"minor"}].
    """
    beat = 60.0 / score.bpm
    bar_s = 4 * beat
    notes = sorted(score.notes, key=lambda n: n.start_s)
    sung = [n for n in notes
            if n.lyric not in ("", "<SP>") and n.midi > 0]
    if not sung:
        raise ValueError("score has no sung notes; cannot derive chords")
    last_end = max(n.start_s + n.dur_s for n in sung)
    n_bars = n_bars_for_span(last_end, bar_s)
    chords = []
    prev = None
    for bar in range(n_bars):
        b0, b1 = bar * bar_s, (bar + 1) * bar_s
        in_bar = [n for n in sung if b0 - 1e-9 <= n.start_s < b1 - 1e-9]
        if not in_bar:
            if prev is None:
                raise ValueError(
                    f"first bar ({bar}) has no notes; cannot derive "
                    "a chord -- pass options.chords explicitly")
            chords.append({"bar": bar, "root": prev["root"],
                           "quality": prev["quality"]})
            continue
        dur_by_pc: dict[int, float] = {}
        for n in in_bar:
            dur_by_pc[n.midi % 12] = (dur_by_pc.get(n.midi % 12, 0.0)
                                      + n.dur_s)
        root = max(dur_by_pc, key=lambda pc: (dur_by_pc[pc], -pc))
        maj = dur_by_pc.get((root + 4) % 12, 0.0)
        mino = dur_by_pc.get((root + 3) % 12, 0.0)
        if maj > 0 and mino > 0:
            quality = "major" if maj >= mino else "minor"
        elif maj > 0:
            quality = "major"
        elif mino > 0:
            quality = "minor"
        else:
            quality = "major"
        prev = {"bar": bar, "root": root, "quality": quality}
        chords.append(prev)
    return chords


def chords_to_midi_notes(chords: list[dict], score: ScoreIn,
                         ppq: int = PPQ, transpose: int = 0) -> list[tuple]:
    """Chord table -> MIDI notes (root, third, fifth per bar).

    Root at MIDI 48 + root pc (C3 area, as in the experiment chords
    MIDI); each note spans its whole bar. Velocities copy the
    experiment file: root 80, third/fifth 70, channel 0.
    """
    beat = 60.0 / score.bpm
    bar_ticks = 4 * ppq
    out = []
    for ch in chords:
        bar = int(ch["bar"])
        root_pc = int(ch["root"]) % 12
        third = 4 if ch["quality"] == "major" else 3
        on, off = bar * bar_ticks, (bar + 1) * bar_ticks
        base = 48 + root_pc + transpose
        out.append((on, off, base, 80, 0))
        out.append((on, off, base + third, 70, 0))
        out.append((on, off, base + 7, 70, 0))
    out.sort()
    return out


def stretch_midi_notes(notes: list[tuple], factor: float = 1.5) -> list[tuple]:
    """Scale onsets and durations by `factor` (copy of make_slice.py
    stretch: onset*factor, duration*factor, rounded to ticks)."""
    out = []
    for (on, off, midi, vel, ch) in notes:
        dur = off - on
        on2 = round(on * factor)
        out.append((on2, on2 + round(dur * factor), midi, vel, ch))
    out.sort()
    return out


def _vlq(n: int) -> bytes:
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def write_midi_file(path, notes: list[tuple], bpm: float,
                    ppq: int = PPQ, track_name: str = "lingren") -> None:
    """Write a type-1 MIDI file with one track (see module header)."""
    from pathlib import Path
    events = []
    for (on, off, midi, vel, ch) in notes:
        events.append((on, 0, 0x90 | (ch & 0x0F), midi, vel))
        events.append((off, 1, 0x80 | (ch & 0x0F), midi, 0))
    events.sort(key=lambda e: (e[0], e[1]))
    track = bytearray()
    tempo_us = int(round(60_000_000 / bpm))
    name_b = track_name.encode("utf-8")
    track += b"\x00\xff\x03" + _vlq(len(name_b)) + name_b
    track += b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big")
    last = 0
    for tick, _, status, d1, d2 in events:
        track += _vlq(tick - last)
        last = tick
        track += bytes([status, d1 & 0x7F, d2 & 0x7F])
    track += b"\x00\xff\x2f\x00"
    header = (b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
              + (1).to_bytes(2, "big") + ppq.to_bytes(2, "big"))
    Path(path).write_bytes(header + b"MTrk" + len(track).to_bytes(4, "big")
                           + bytes(track))


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
