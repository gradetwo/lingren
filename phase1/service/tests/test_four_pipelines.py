"""Four-pipeline tests (no GPU, no real models).

Covers DESIGN-four-pipelines.md section 9.1: registry, default
model, unknown-model rejection, needs_voice hook, ABC conversion
(YuE2), MIDI conversion + chord derivation (MuLaCover), option
defaults, VR reuse (tests/test_vr.py), SoulX regression
(tests/test_api.py, tests/test_gate.py).
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.adapters.base import AdapterError  # noqa: E402
from app.adapters.yue2 import DEFAULTS as YUE2_DEFAULTS  # noqa: E402
from app.adapters.yue2 import YuE2Adapter  # noqa: E402
from app.jobs import JobStore, _adapters  # noqa: E402
from app.schemas import NoteIn, PartIn, ScoreIn  # noqa: E402
from app.score_converter import score_lyrics_text, score_to_yue2_abc  # noqa: E402

EXP_YUE2 = Path("/home/hatch/workspace/song-folk-two/work/yue2-min-exp")


def _note(lyric, midi, start, dur, phoneme=None):
    return NoteIn(lyric=lyric, midi=midi, start_s=start, dur_s=dur,
                  phoneme=phoneme)


def _part(model, notes, bpm=120.0, voice_id="v", options=None, takes=1):
    return PartIn(label="p", score=ScoreIn(bpm=bpm, notes=notes),
                  voice_id=voice_id, model=model, takes=takes,
                  options=options or {})


def _store_with_voice(tmp_path):
    store = JobStore(tmp_path)
    store.voices["v"] = {"voice_id": "v", "audio_path": "/nonexistent.wav",
                         "prompt_meta_path": None}
    return store


# --- step 1: schemas / registry / needs_voice ---------------------------

def test_default_model_is_yue2():
    part = _part("yue2", [])
    assert part.model == "yue2"
    # Omitting model entirely also defaults to yue2.
    bare = PartIn(label="p", score=ScoreIn(bpm=120.0, notes=[]),
                  voice_id="v")
    assert bare.model == "yue2"


def test_explicit_soulx_still_walks_soulx():
    assert _part("soulx", []).model == "soulx"


def test_unknown_model_fails_loudly(tmp_path):
    store = _store_with_voice(tmp_path)
    job = {"parts_completed": 0, "takes_completed": 0}
    part = _part("diffsinger", [_note("啊", 64, 0.0, 0.5)])
    with pytest.raises(AdapterError, match="unknown model 'diffsinger'"):
        store._run_part(job, part, _adapters(), tmp_path / "job")


def test_yue2_needs_no_voice_without_svc(tmp_path):
    """YuE2 generation needs no voice: the voice check is skipped
    and the failure surfaces at the adapter (missing yue2_style),
    not at unknown voice_id."""
    store = JobStore(tmp_path)  # no voices registered at all
    job = {"parts_completed": 0, "takes_completed": 0}
    part = _part("yue2", [_note("啊", 64, 0.0, 0.5)],
                 voice_id="nobody")
    with pytest.raises(AdapterError, match="yue2_style is required"):
        store._run_part(job, part, _adapters(), tmp_path / "job")


def test_yue2_needs_voice_with_svc(tmp_path):
    store = JobStore(tmp_path)  # no voices registered at all
    job = {"parts_completed": 0, "takes_completed": 0}
    part = _part("yue2", [_note("啊", 64, 0.0, 0.5)],
                 voice_id="nobody", options={"svc": True})
    with pytest.raises(AdapterError, match="unknown voice_id 'nobody'"):
        store._run_part(job, part, _adapters(), tmp_path / "job")


# --- step 3: YuE2 ABC conversion ---------------------------------------

def test_abc_header_and_transpose():
    # 120 BPM: beat 0.5 s, 16th 0.125 s. C4 1 beat -> +12 -> c4.
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 60, 0.0, 0.5), _note("啊", 62, 0.5, 0.5)])
    abc = score_to_yue2_abc(score, key="G", section_label="verse")
    assert abc.startswith("X:1\nT:\nM:4/4\nL:1/16\nQ:1/4=120\n")
    assert "K:G" in abc
    assert "% verse" in abc
    assert "c4 d4" in abc  # C4/D4 up 12 -> c/d (octave 5)
    assert "V: Ins\nZ|" in abc  # default Ins: whole-group rests (1 bar)


def test_abc_rests_and_bar_split_with_tie():
    # Gap -> z rest; a 2-beat note starting on beat 3.5 of a 4/4 bar
    # (unit 14 of 16) splits at the bar line with a tie: 2 units in
    # bar 0, 6 in bar 1. One 4-bar group holds both bars.
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 60, 0.0, 0.5),
        _note("啊", 62, 1.75, 1.0)])
    abc = score_to_yue2_abc(score)
    vocal = [ln for ln in abc.splitlines()
             if ln and not ln.startswith(("X:", "T:", "M:", "L:", "Q:",
                                          "V:", "K:", "%", "Z"))]
    assert vocal == ["c4 z10 d2-|d6|"]


def test_abc_sharp_spelling_and_octave_marks():
    # F#4 (66) +12 = F#5 -> ^f; C3 (48) +12 = C4 -> C (uppercase).
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 66, 0.0, 0.5), _note("啊", 48, 0.5, 0.5)])
    abc = score_to_yue2_abc(score)
    assert "^f4 C4" in abc


def test_abc_ins_override_consumed_per_group():
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 60, 0.0, 0.5)] * 1)
    abc = score_to_yue2_abc(score, ins_abc="C4 E4 G4 C4|")
    assert "V: Ins\nC4 E4 G4 C4|" in abc
    assert "Z4|" not in abc


def test_score_lyrics_text_skips_rests():
    score = ScoreIn(bpm=120.0, notes=[
        _note("你", 64, 0.0, 0.5), _note("<SP>", 0, 0.5, 0.5),
        _note("好", 67, 1.0, 0.5)])
    assert score_lyrics_text(score) == "你好"


def test_yue2_option_defaults_are_experiment_values():
    assert YUE2_DEFAULTS == {"cot": "melody", "cfg_scale": 1.0,
                             "temperature": 1.0, "top_p": 0.95,
                             "top_k": 100, "repetition_penalty": 1.2}


def test_yue2_style_missing_fails_loudly(tmp_path):
    part = _part("yue2", [_note("啊", 64, 0.0, 0.5)])
    with pytest.raises(AdapterError, match="yue2_style is required"):
        YuE2Adapter().synthesize(part, None, 42, tmp_path / "o.wav",
                                 tmp_path)


def test_yue2_svc_not_wired_yet(tmp_path):
    # Step 5 wires the SVC post-process; until then svc=True must
    # fail loudly, never silently generate without SVC.
    store = _store_with_voice(tmp_path)
    part = _part("yue2", [_note("啊", 64, 0.0, 0.5)],
                 options={"svc": True, "yue2_style": "x"})
    with pytest.raises(AdapterError, match="not wired yet"):
        YuE2Adapter().synthesize(part, store.voices["v"], 42,
                                 tmp_path / "o.wav", tmp_path)


@pytest.mark.skipif(
    not (EXP_YUE2 / "seg1_native_melody_octave_up.abc").exists(),
    reason="folk experiment fixture not present on this machine")
def test_abc_reproduces_r3_experiment_file():
    """The adapter's ABC conversion reproduces the r3 experiment's
    Vocal voice from the same vox.csv (bars 9-36, 132 notes). Only
    the section comments differ: the score carries no section data,
    so the converter emits one '% <label>' comment, not the
    experiment's verse/chorus split."""
    rows = list(csv.DictReader(
        open(EXP_YUE2 / "seg1_vox.csv")))
    assert len(rows) == 132
    notes = [NoteIn(lyric="a", midi=int(r["midi"]),
                    start_s=float(r["start_s"]),
                    dur_s=float(r["end_s"]) - float(r["start_s"]))
             for r in rows]
    abc = score_to_yue2_abc(ScoreIn(bpm=66.0, notes=notes), key="G",
                            transpose=12, section_label="verse")
    ref = (EXP_YUE2 / "seg1_native_melody_octave_up.abc").read_text()

    def vocal_lines(t):
        out, cur = [], None
        for ln in t.splitlines():
            s = ln.strip()
            if s.startswith("V:"):
                cur = ("Vocal" if "Vocal" in s else
                       "Ins" if "Ins" in s else None)
            elif cur == "Vocal" and s and not s.startswith("%"):
                out.append(s)
        return out

    assert vocal_lines(abc) == vocal_lines(ref)
    mine = [ln for ln in abc.splitlines() if not ln.startswith("%")]
    theirs = [ln for ln in ref.splitlines() if not ln.startswith("%")]
    assert mine == theirs
