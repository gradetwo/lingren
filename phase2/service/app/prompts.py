"""Prompt registry: stored prompt audio addressable by prompt_id.

Night-side callers (Yeban) should hold one id, not shuttle audio
back and forth: a prompt pair produced by an svc-prompt job, by the
voices genre SVC path, or by a plain voice file upload is registered
here under a prompt_id, and a later POST /v1/svc-prompt may pass
that prompt_id instead of re-uploading timbre audio.

Layout mirrors the voices registry on purpose (same restart-reload
pattern): data_dir/prompts/<prompt_id>/prompt.wav (+ prompt_meta.json
when the pair has one) + info.json. info.json is the record; a
directory without a readable info.json is skipped on reload, same
as voices. No new dependency, no model code in this module.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path


class PromptStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.prompts_dir = data_dir / "prompts"
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.prompts: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._load_prompts()

    def _load_prompts(self):
        for info_path in self.prompts_dir.glob("*/info.json"):
            try:
                info = json.loads(info_path.read_text())
                self.prompts[info["prompt_id"]] = info
            except Exception:
                continue

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def get_prompt(self, prompt_id: str) -> dict | None:
        with self.lock:
            info = self.prompts.get(prompt_id)
        return dict(info) if info is not None else None

    def list_prompts(self) -> list[dict]:
        with self.lock:
            return [dict(info) for info in self.prompts.values()]

    def register_prompt(self, prompt_id: str, audio_path: Path,
                        prompt_meta_path: Path | None, source: str,
                        voice_id: str | None) -> dict:
        info = {"prompt_id": prompt_id,
                "audio_path": str(audio_path),
                "prompt_meta_path": (str(prompt_meta_path)
                                     if prompt_meta_path else None),
                "source": source,
                "voice_id": voice_id,
                "created_at": time.time()}
        (audio_path.parent / "info.json").write_text(json.dumps(info))
        with self.lock:
            self.prompts[prompt_id] = info
        return info

    def register_prompt_files(self, prompt_id: str, wav_path: Path,
                              meta_path: Path | None, source: str,
                              voice_id: str | None) -> dict:
        """Copy a finished prompt pair into the prompts dir and
        register it. Used by the SVC completion hooks: the pair
        lives in a job dir, the registry must own its own copy so
        the id stays servable after the job dir is gone."""
        pdir = self.prompts_dir / prompt_id
        pdir.mkdir(parents=True, exist_ok=True)
        audio_path = pdir / "prompt.wav"
        audio_path.write_bytes(wav_path.read_bytes())
        meta_copy = None
        if meta_path is not None:
            meta_copy = pdir / "prompt_meta.json"
            meta_copy.write_bytes(meta_path.read_bytes())
        return self.register_prompt(prompt_id, audio_path, meta_copy,
                                    source, voice_id)
