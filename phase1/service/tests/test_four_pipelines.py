"""Four-pipeline tests (no GPU, no real models).

Covers DESIGN-four-pipelines.md section 9.1: registry, default
model, unknown-model rejection, needs_voice hook, ABC conversion
(YuE2), MIDI conversion + chord derivation (MuLaCover), option
defaults, VR reuse (tests/test_vr.py), SoulX regression
(tests/test_api.py, tests/test_gate.py).
"""
import csv
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.adapters.base import AdapterError  # noqa: E402
from app.adapters.mulacover import DEFAULTS as MC_DEFAULTS  # noqa: E402
from app.adapters.mulacover import MuLaCoverAdapter  # noqa: E402
from app.adapters.mulacover import _distribute_lines, _slice_window  # noqa: E402
from app.adapters.yue2 import DEFAULTS as YUE2_DEFAULTS  # noqa: E402
from app.adapters.yue2 import YuE2Adapter  # noqa: E402
from app.jobs import JobStore, _adapters  # noqa: E402
from app.schemas import NoteIn, PartIn, ScoreIn  # noqa: E402
from app.score_converter import (  # noqa: E402
    chords_to_midi_notes, derive_chords, score_lyrics_text,
    score_to_midi_notes, score_to_yue2_abc, stretch_midi_notes,
    write_midi_file)


def _read_midi_notes(path):
    """Minimal MIDI note reader (stdlib only, for tests)."""
    data = Path(path).read_bytes()
    assert data[:4] == b"MThd"
    hlen = struct.unpack(">I", data[4:8])[0]
    _, ntr, _ = struct.unpack(">HHH", data[8:14])
    pos = 8 + hlen
    notes = []
    for _ in range(ntr):
        assert data[pos:pos + 4] == b"MTrk"
        pos += 4
        tlen = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        end = pos + tlen
        tick = 0
        open_notes = {}
        last = None
        while pos < end:
            d = 0
            while True:
                b = data[pos]
                pos += 1
                d = (d << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += d
            b = data[pos]
            if b < 0x80:
                st = last
            else:
                st = b
                pos += 1
                last = st
            if st == 0xFF:
                pos += 1
                ln = 0
                while True:
                    bb = data[pos]
                    pos += 1
                    ln = (ln << 7) | (bb & 0x7F)
                    if not bb & 0x80:
                        break
                pos += ln
            elif st & 0xF0 in (0x90, 0x80):
                n, v = data[pos], data[pos + 1]
                pos += 2
                if (st & 0xF0) == 0x90 and v > 0:
                    open_notes.setdefault((st & 0x0F, n), []).append(
                        (tick, v))
                else:
                    on, v0 = open_notes[(st & 0x0F, n)].pop(0)
                    notes.append((on, tick, n, v0, st & 0x0F))
        pos = end
    return sorted(notes)

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


# --- step 4: MuLaCover MIDI conversion + chords ------------------------

def test_registry_has_all_five():
    assert sorted(_adapters()) == ["mulacover", "soulx", "stub",
                                   "vocalrender", "yue2"]
    assert "vocalrender" in _adapters()
    from app import jobs as jobs_mod
    assert not hasattr(jobs_mod, "_VocalRenderPending")


def test_mulacover_option_defaults_are_experiment_values():
    assert MC_DEFAULTS == {"cfg_scale": 2.5, "temperature": 1.0,
                           "topk": 250}


def test_mulacover_tags_missing_fails_loudly(tmp_path):
    part = _part("mulacover", [_note("啊", 64, 0.0, 0.5)])
    with pytest.raises(AdapterError, match="tags is required"):
        MuLaCoverAdapter().synthesize(part, None, 42,
                                      tmp_path / "o.wav", tmp_path)


def test_mulacover_needs_no_voice_without_svc(tmp_path):
    store = JobStore(tmp_path)  # no voices at all
    job = {"parts_completed": 0, "takes_completed": 0}
    part = _part("mulacover", [_note("啊", 64, 0.0, 0.5)],
                 voice_id="nobody", options={"tags": "x"})
    with pytest.raises(AdapterError, match="MULACOVER_REPO"):
        store._run_part(job, part, _adapters(), tmp_path / "job")


def test_chord_derivation_major_minor(tmp_path):
    # 120 BPM, bar = 2 s. Bar 0: A 1.0 s, C 0.5, E 0.5 -> root A,
    # minor third C present, major third C# absent -> minor.
    # Bar 1: C 1.0, E 0.5, G 0.5 -> root C, major.
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 57, 0.0, 1.0), _note("啊", 60, 1.0, 0.5),
        _note("啊", 64, 1.5, 0.5),
        _note("啊", 60, 2.0, 1.0), _note("啊", 64, 3.0, 0.5),
        _note("啊", 67, 3.5, 0.5)])
    chords = derive_chords(score)
    assert chords == [{"bar": 0, "root": 9, "quality": "minor"},
                      {"bar": 1, "root": 0, "quality": "major"}]


def test_chord_derivation_both_thirds_and_neither():
    # A 1.0, C 0.9 (minor third), C# 0.8 (major third), E 0.5:
    # root A, both thirds present, minor third longer -> minor.
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 57, 0.0, 1.0), _note("啊", 60, 1.0, 0.9),
        _note("啊", 61, 1.9, 0.1)])
    assert derive_chords(score) == [{"bar": 0, "root": 9,
                                     "quality": "minor"}]
    # Single note: no third at all -> major by rule.
    score = ScoreIn(bpm=120.0, notes=[_note("啊", 57, 0.0, 1.0)])
    assert derive_chords(score) == [{"bar": 0, "root": 9,
                                     "quality": "major"}]


def test_chord_derivation_empty_bar_carries_and_first_empty_fails():
    # Bar 0 has notes, bar 1 empty -> carries bar 0's chord.
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 60, 0.0, 1.0), _note("啊", 64, 4.0, 1.0)])
    chords = derive_chords(score)
    assert len(chords) == 3
    assert (chords[1]["root"], chords[1]["quality"]) == (
        chords[0]["root"], chords[0]["quality"])
    assert chords[1]["bar"] == 1
    # First bar empty -> loud failure.
    score = ScoreIn(bpm=120.0, notes=[_note("啊", 60, 2.0, 1.0)])
    with pytest.raises(ValueError, match="first bar"):
        derive_chords(score)


def test_chords_table_override_and_validation(tmp_path):
    part = _part("mulacover", [_note("啊", 64, 0.0, 0.5)],
                 options={"tags": "x",
                          "chords": [{"bar": 0, "root": 7,
                                      "quality": "major"}]})
    # The table is validated before any model env is read.
    with pytest.raises(AdapterError, match="MULACOVER_REPO"):
        MuLaCoverAdapter().synthesize(part, None, 42,
                                      tmp_path / "o.wav", tmp_path)
    bad = _part("mulacover", [_note("啊", 64, 0.0, 0.5)],
                options={"tags": "x",
                         "chords": [{"bar": 0, "root": 12,
                                     "quality": "major"}]})
    with pytest.raises(AdapterError, match="root out of range"):
        MuLaCoverAdapter().synthesize(bad, None, 42,
                                      tmp_path / "o.wav", tmp_path)


def test_midi_write_round_trip_and_stretch(tmp_path):
    score = ScoreIn(bpm=120.0, notes=[
        _note("啊", 64, 0.0, 0.5), _note("啊", 67, 0.5, 0.5)])
    notes = score_to_midi_notes(score)
    # 120 BPM: 0.5 s = 1 beat = 960 ticks (PPQ 960).
    assert notes == [(0, 960, 64, 100, 0), (960, 1920, 67, 100, 0)]
    path = tmp_path / "m.mid"
    write_midi_file(path, notes, 120.0, track_name="Lead vocal")
    assert _read_midi_notes(path) == notes
    st = stretch_midi_notes(notes)
    assert st == [(0, 1440, 64, 100, 0), (1440, 2880, 67, 100, 0)]
    chords = chords_to_midi_notes(
        [{"bar": 0, "root": 0, "quality": "major"}], score)
    assert chords == [(0, 3840, 48, 80, 0), (0, 3840, 52, 70, 0),
                      (0, 3840, 55, 70, 0)]


def test_slice_window_truncates_at_boundaries():
    notes = [(0, 100, 60, 100, 0), (90, 200, 62, 100, 0),
             (200, 300, 64, 100, 0)]
    assert _slice_window(notes, 100, 200) == [(0, 100, 62, 100, 0)]


def test_distribute_lines_proportional():
    lines = ["[verse]", "a", "b", "c", "d", "e"]
    # 6 lines at weights 3:1 -> raw 4.5/1.5; the one leftover line
    # goes to the earlier segment on the fractional tie.
    assert _distribute_lines(lines, [3, 1]) == ["[verse]\na\nb\nc\nd",
                                                "e"]
    assert _distribute_lines(lines, [0, 0]) == ["\n".join(lines)] * 2


_FAKE_MC_DRIVER = '''\
import argparse, json
import numpy as np, soundfile as sf
ap = argparse.ArgumentParser()
ap.add_argument("--manifest", required=True)
ap.add_argument("--out", required=True)
args, _ = ap.parse_known_args()
man = json.loads(open(args.manifest).read())
sr = 44100
n = int(round(man["span_s"] * sr))
sf.write(args.out, np.zeros(n, dtype=np.float32), sr)
print(json.dumps({"out": args.out, "sr": sr,
                  "duration_s": man["span_s"], "peak": 0.0,
                  "segments": []}))
'''


def test_mulacover_segmentation_and_manifest(tmp_path, monkeypatch):
    """28 bars at 66 BPM -> 3 driver segments (12/12/4 bars) with
    stretched MIDIs, per-segment lyrics and the derived chord table
    in adapter_info. The real driver is replaced by a fake that
    writes silence and a summary line."""
    import app.adapters.mulacover as mc_mod
    fake = tmp_path / "fake_mc_driver.py"
    fake.write_text(_FAKE_MC_DRIVER)
    monkeypatch.setattr(mc_mod, "_DRIVER", fake)
    monkeypatch.setenv("MULACOVER_REPO", "/fake/repo")
    monkeypatch.setenv("MULACOVER_MODEL_PATH", "/fake/ckpt")
    notes = [_note("啊", 60 + (i % 7), i * (4 * 60 / 66), 1.0)
             for i in range(28)]
    part = _part("mulacover", notes, bpm=66.0,
                 options={"tags": "topic:[x]; genre:[y]"})
    out = tmp_path / "o.wav"
    _, sr, info = MuLaCoverAdapter().synthesize(
        part, None, 42, out, tmp_path)
    assert sr == 44100 and out.is_file()
    assert info["bars"] == 28
    assert info["chords_source"] == "derived"
    assert len(info["chords"]) == 28
    man = json.loads(
        (tmp_path / "mc_p" / "manifest.json").read_text())
    assert [s["bars"] for s in man["segments"]] == [[0, 12], [12, 24],
                                                   [24, 28]]
    assert [s["notes"] for s in man["segments"]] == [12, 12, 4]
    assert [round(s["slot"][1] - s["slot"][0], 3)
            for s in man["segments"]] == [43.636, 43.636, 14.546]
    seg0_mel = _read_midi_notes(man["segments"][0]["melody_midi"])
    assert len(seg0_mel) == 12  # stretched, count unchanged
    assert seg0_mel[0][0] == 0
    seg0_ch = _read_midi_notes(man["segments"][0]["chord_midi"])
    assert len(seg0_ch) == 12 * 3  # 12 bars x triad


def test_mulacover_svc_not_wired_yet(tmp_path):
    part = _part("mulacover", [_note("啊", 64, 0.0, 0.5)],
                 options={"tags": "x", "svc": True})
    with pytest.raises(AdapterError, match="not wired yet"):
        MuLaCoverAdapter().synthesize(part, {"audio_path": "/x.wav"},
                                      42, tmp_path / "o.wav", tmp_path)
