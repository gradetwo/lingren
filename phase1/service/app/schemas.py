"""Request/response schemas for the Phase 1 job API (design section 5)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NoteIn(BaseModel):
    lyric: str = Field(description="Lyric syllable; '' or '<SP>' = rest")
    midi: int = Field(description="MIDI pitch; <=0 = rest")
    start_s: float = Field(ge=0.0)
    dur_s: float = Field(gt=0.0)
    tie: bool = Field(default=False,
                      description="True = tied continuation of previous note")
    phoneme: str | None = Field(
        default=None,
        description="SoulX phone id, e.g. zh_zhi3. When absent, the service "
                    "runs G2P (Mandarin/Cantonese/English) if available.")


class ScoreIn(BaseModel):
    bpm: float = Field(gt=0.0)
    language: str = "Mandarin"
    notes: list[NoteIn]


class PartIn(BaseModel):
    label: str
    score: ScoreIn
    voice_id: str
    model: str = Field(default="soulx",
                       description="'soulx' (default) or 'vocalrender'")
    variant: str | None = None
    takes: int = Field(default=1, ge=1, le=8)
    seeds: list[int] | None = Field(
        default=None,
        description="Per-take seeds; when absent, seeds are 0..takes-1")
    options: dict = Field(
        default_factory=dict,
        description="soulx: auto_shift (bool, default False), "
                    "pitch_shift (int semitones, default 0, NOT a harmony "
                    "route -- see patches/02-soulx-pitch-shift-note.md)")


class SynthesizeIn(BaseModel):
    parts: list[PartIn] = Field(min_length=1)
