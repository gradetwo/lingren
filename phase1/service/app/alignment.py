"""Measured-onset alignment for VocalRender receipts (Phase 1).

Implementation lives in gate.py (detect_candidates + align_to_score);
this module re-exports them so receipt code has one import site.

Phase 0 result: energy-jump onsets alone recover 11/15 syllables (73%);
4 syllables have no strong energy onset (smooth/weak onsets). The joint
detector adds F0 stable-change points and assigns candidates to score
syllables monotonically; unassigned syllables get measured_onset_s =
null (honest miss, not an invented value).

For SoulX the same function validates score-clock placement: deviations
are reported, placement stays on the score clock (design section 6).
For VocalRender (Phase 2) the measured onsets ARE the placement data.
"""

from __future__ import annotations

from .gate import align_to_score, detect_candidates  # noqa: F401

__all__ = ["align_to_score", "detect_candidates"]
