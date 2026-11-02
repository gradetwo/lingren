#!/usr/bin/env python3
"""SoulX-Singer single-segment driver for the 伶人 service (Phase 1).

Usage: see adapters/soulx.py. Prints the model log, then one JSON
summary line on stdout as the last line:
  {"out": ..., "sr": 24000, "duration_s": ...,
   "f0_shift_semitones": <int|None>}

Seeding: torch/random/numpy global RNGs are seeded BEFORE model build
(patch 02: the upstream CLI has no --seed). f0_shift reporting needs
patch 01 (model.last_f0_shift); without it the field is null, which is
honest, not an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--phoneset", required=True)
    ap.add_argument("--prompt-wav", required=True)
    ap.add_argument("--prompt-meta", required=True)
    ap.add_argument("--target-meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pitch-shift", type=int, default=0)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--auto-shift", dest="auto_shift", action="store_true")
    ap.add_argument("--no-auto-shift", dest="auto_shift",
                    action="store_false")
    ap.add_argument("--fp16", action="store_true")
    ap.set_defaults(auto_shift=False)
    args = ap.parse_args()

    sys.path.insert(0, args.repo)
    import random

    import numpy as np
    import soundfile as sf
    import torch

    random.seed(args.seed)
    np.random.seed(args.seed % (2 ** 32))
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    from soulxsinger.utils.data_processor import DataProcessor
    from soulxsinger.utils.file_utils import load_config
    from cli.inference import build_model

    config = load_config(args.config)
    model = build_model(model_path=args.model_path, config=config,
                        device=args.device, use_fp16=args.fp16)
    dp = DataProcessor(hop_size=config.audio.hop_size,
                       sample_rate=config.audio.sample_rate,
                       phoneset_path=args.phoneset, device=args.device)
    prompt_meta = json.loads(Path(args.prompt_meta).read_text())[0]
    target_meta = json.loads(Path(args.target_meta).read_text())[0]
    infer_prompt = dp.process(prompt_meta, args.prompt_wav)
    infer_target = dp.process(target_meta, None)
    with torch.no_grad():
        audio = model.infer({"prompt": infer_prompt, "target": infer_target},
                            auto_shift=args.auto_shift,
                            pitch_shift=args.pitch_shift,
                            n_steps=config.infer.n_steps,
                            cfg=config.infer.cfg,
                            control="score", use_fp16=args.fp16)
    audio = audio.squeeze().cpu().numpy()
    sf.write(args.out, audio, 24000)
    print(json.dumps({"out": args.out, "sr": 24000,
                      "duration_s": round(len(audio) / 24000, 4),
                      "f0_shift_semitones": getattr(model, "last_f0_shift",
                                                    None)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
