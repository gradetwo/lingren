"""Lingren-side vocal gate tests (no GPU, no model).

Synthetic takes are constructed audio, NOT model output: they validate
the gate math and the refine->verdict pipeline. Mock/constructed data is
labelled as such wherever it appears.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.vocal_gate import (build_core_rates, interpolate_coarse,
                            line_gate, refine_note, rms_curve)  # noqa: E402

SR = 48000


def _synth_take(notes, total_s: float) -> np.ndarray:
    """notes: [(start, dur, freq)]; vowel with 30 ms attack, -60 dB
    noise floor everywhere (real takes never sit at digital zero)."""
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1e-4, int(total_s * SR))
    for start, dur, freq in notes:
        t = np.arange(int(dur * SR)) / SR
        env = np.minimum(1.0, t / 0.03)
        s0 = int(start * SR)
        x[s0:s0 + len(t)] += 0.7 * env * np.sin(2 * np.pi * freq * t)
    return x.astype(np.float64)


def test_refine_synthetic_syllable():
    # Silence 0.5 s + noise consonant 0.1 s @0.3 + harmonic vowel 150 Hz
    # 0.8 s with 20 ms attack + silence (Rust unit-test mirror).
    rng = np.random.default_rng(3)
    x = np.zeros(int(2.0 * SR))
    x[int(0.5 * SR):int(0.6 * SR)] = 0.3 * rng.normal(0, 1, int(0.1 * SR))
    t = np.arange(int(0.8 * SR)) / SR
    env = np.minimum(1.0, t / 0.02)
    # Harmonic vowel (150 Hz series): a pure 150 Hz sine has NO energy in
    # the 300-3000 Hz band, so the refiner correctly ignores it.
    vowel = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 8))
    x[int(0.6 * SR):int(1.4 * SR)] += 0.8 * env * vowel
    r = refine_note(x, SR, 0.60, 0.0, 2.0)
    assert r["anchored"]
    assert abs(r["vop"] - 0.60) <= 0.030
    assert 0.40 <= r["pre"] <= 0.63
    assert abs(r["off"] - 1.40) <= 0.050
    assert abs(r["f0"] - 150.0) <= 5.0
    assert r["confidence"] >= 0.65


def test_refine_silence_unanchored():
    x = np.zeros(int(1.0 * SR))
    r = refine_note(x, SR, 0.5, 0.0, 1.0)
    assert not r["anchored"] and r["confidence"] == 0.0


def test_line_gate_pass():
    # Score 0/1/2/3 with a 1 s gap; take on-grid.
    take = _synth_take([(0.0, 1.0, 330.0), (2.0, 1.0, 392.0)], 4.0)
    notes = [{"start": 0.0, "dur": 1.0}, {"start": 2.0, "dur": 1.0}]
    verdict = line_gate(take, SR, notes, [0.0, 2.0], None, None, 4.0)
    assert verdict["pass"], verdict
    assert 0.80 <= verdict["line_ratio"] <= 1.25
    assert verdict["ratio_source"] == "anchored"


def test_line_gate_compressed_ratio_fail():
    # Score span 3.0 s sung in ~1.4 s -> R ~2.1, ratio failure.
    take = _synth_take([(0.3, 0.4, 330.0), (0.8, 0.4, 370.0),
                        (1.3, 0.4, 392.0)], 2.0)
    notes = [{"start": 0.0, "dur": 1.0}, {"start": 1.0, "dur": 1.0},
             {"start": 2.0, "dur": 1.0}]
    verdict = line_gate(take, SR, notes, [0.3, 0.8, 1.3], None, None, 2.0)
    assert not verdict["pass"]
    assert any("line ratio" in f for f in verdict["failures"])
    assert verdict["line_ratio"] > 1.25


def test_line_gate_collision_drops_one():
    # One onset claimed by two notes -> exactly one keeps it.
    # Geometry matters: coarse spacing 80 ms leaves note 0's off room
    # for a 65 ms core (so both anchor) while note 1's +/-80 ms window
    # still opens on the same onset frame. Tighter spacing truncates
    # note 0's off below 65 ms (it leaves the anchored set before
    # collision is considered); wider spacing moves note 1's window
    # past the onset frame, so the pair lands one hop apart -- the
    # 10 ms boundary the rule deliberately does not merge.
    rng = np.random.default_rng(7)
    take = rng.normal(0, 1e-4, int(2.0 * SR))
    t = np.arange(int(0.8 * SR)) / SR
    env = np.minimum(1.0, t / 0.03)
    s0 = int(1.0 * SR)
    take[s0:s0 + len(t)] += 0.7 * env * np.sin(2 * np.pi * 330.0 * t)
    notes = [{"start": 0.0, "dur": 1.0}, {"start": 1.0, "dur": 1.0}]
    verdict = line_gate(take, SR, notes, [1.00, 1.08], None, None, 2.0)
    assert verdict["collision_dropped"] == [1]


def test_build_core_rates_silence_absorbs_slack():
    # Loose gap with silence -> core stays 1.0 (line-13 mechanism).
    audio = np.zeros(int(2.0 * SR))
    rms = rms_curve(audio, SR)
    rates, clamps, _ = build_core_rates(
        [(0.0, 0.1, 0.5)], [0.0], 2.0, 2.0, rms, SR)
    assert rates == [1.0] and clamps == []


def test_build_core_rates_tight_clamps_and_hard_fail():
    # Last syllable, need 0.335 s < core 0.965 s x 0.60 -> clamp + hard.
    rng = np.random.default_rng(5)
    audio = rng.normal(0, 0.1, int(2.0 * SR))  # no silence anywhere
    rms = rms_curve(audio, SR)
    rates, clamps, _ = build_core_rates(
        [(0.0, 0.1, 1.1)], [0.0], 0.5, 2.0, rms, SR)
    assert len(clamps) == 1
    # The interval rate is floored to exactly 0.60 (Rust parity); the
    # clamp event's REQUESTED rate (need/core) is the out-of-hard
    # evidence, and clamp presence alone fails the gate.
    assert rates[0] == 0.60
    assert clamps[0]["requested_rate"] < 0.55


def test_interpolate_coarse():
    out = interpolate_coarse([0.0, 1.0, 2.0, 3.0], [0.1, None, None, 3.2])
    assert out[0] == 0.1 and out[3] == 3.2
    assert abs(out[1] - 1.133333) < 1e-4
    assert abs(out[2] - 2.166667) < 1e-4
