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
    model: str = Field(default="yue2",
                       description="'soulx' | 'vocalrender' | 'yue2' "
                       "(default) | 'mulacover' | 'stub' (test only). "
                       "Unknown values fail loudly, never silently "
                       "fall back. NOTE (2026-11-07, four-pipeline "
                       "work): the default changed from 'soulx' to "
                       "'yue2'; callers that omit model now get YuE2.")
    variant: str | None = None
    takes: int = Field(default=1, ge=1, le=8)
    seeds: list[int] | None = Field(
        default=None,
        description="Per-take seeds; when absent, seeds are 0..takes-1")
    options: dict = Field(
        default_factory=dict,
        description="soulx: auto_shift (bool, default False), "
                    "pitch_shift (int semitones, default 0, NOT a harmony "
                    "route -- see patches/02-soulx-pitch-shift-note.md); "
                    "vocalrender: temperature, fsq_temperature, max_len "
                    "(all optional; omitted = upstream CLI defaults); "
                    "yue2: yue2_style (required), yue2_cot, "
                    "yue2_cfg_scale, yue2_temperature, yue2_top_p, "
                    "yue2_top_k, yue2_repetition_penalty, ins_abc, "
                    "yue2_lyrics, yue2_key; mulacover: tags (required), "
                    "chords, mulacover_cfg_scale, mulacover_temperature, "
                    "mulacover_topk, transpose12, mulacover_lyrics; "
                    "yue2/mulacover: svc (bool, default False -- "
                    "post-process the dry vocal through SoulX SVC). "
                    "Unknown option keys are ignored, never an error.")


class SynthesizeIn(BaseModel):
    parts: list[PartIn] = Field(min_length=1)
