"""Gate unit tests (no GPU, no service)."""
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.gate import check_pair, envelope_correlation  # noqa: E402

SR = 24000


def _tone(seed, wander=True):
    """Three notes with gaps: realistic take structure (onsets + vibrato)."""
    rng = np.random.default_rng(seed)
    sig = np.zeros(SR * 3)
    for start, freq in [(0.0, 220.0), (1.0, 261.6), (2.0, 293.7)]:
        jitter = float(rng.uniform(-0.020, 0.020)) if wander else 0.0
        gain = float(rng.uniform(0.88, 1.12)) if wander else 1.0
        t = np.arange(int(0.9 * SR)) / SR
        vib = 1.0 + 0.006 * np.sin(2 * np.pi * 5.5 * t
                                   + rng.uniform(0, 2 * np.pi))
        s0 = int(max(0.0, start + jitter) * SR)
        sig[s0:s0 + len(t)] += gain * np.sin(2 * np.pi * freq * vib * t)
    return sig.astype(np.float32)


def test_identical_fails():
    x = _tone(0)
    r = check_pair(x, SR, x.copy(), SR)
    assert not r["passed"] and r["bit_identical"]


def test_micro_variation_passes():
    r = check_pair(_tone(0), SR, _tone(1), SR)
    assert r["passed"], r
    assert r["envelope_corr"] < 0.99


def test_flat_identical_envelope_fails_micro():
    x = np.sin(2 * np.pi * 220 * np.arange(SR * 3) / SR).astype(np.float32)
    r = check_pair(x, SR, x.copy(), SR)
    assert not r["passed"]
    assert envelope_correlation(x, SR, x, SR) == 1.0


def test_dp_alignment_ignores_spurious_candidates():
    """Positional matching fabricates ~640 ms diffs from count mismatch;
    DP alignment must match the shared boundary pattern instead."""
    import numpy as np
    from app.gate import align_candidate_sequences
    a = np.array([0.597, 1.003, 1.6, 1.685])
    c = np.array([0.085, 0.427, 0.555, 0.981, 1.6])
    diffs = align_candidate_sequences(a, c)
    assert len(diffs) >= 2
    assert float(np.median(diffs)) * 1000 < 100  # ~22 ms, not ~640 ms


def test_dp_alignment_identical_is_zero():
    import numpy as np
    from app.gate import align_candidate_sequences
    a = np.array([0.5, 1.0, 1.5])
    assert align_candidate_sequences(a, a.copy()) == [0.0, 0.0, 0.0]
