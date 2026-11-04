#!/usr/bin/env python3
"""One-shot CLI and job-scoped worker; heavy imports remain in a child.

The CLI keeps legacy seeding before model construction. Reusable sessions seed
each take independently; opt-in output need not match legacy same-seed output.
"""
from __future__ import annotations
import argparse
import contextlib
import json
import sys
import time
from pathlib import Path


class SoulXRuntime:
    def __init__(self, options: dict):
        started = time.monotonic()
        sys.path.insert(0, options['repo'])
        import numpy as np
        import soundfile as sf
        import torch
        self.np, self.sf, self.torch = np, sf, torch
        if options.get('legacy_seed') is not None:
            self._seed(options['legacy_seed'])
        from soulxsinger.utils.data_processor import DataProcessor
        from soulxsinger.utils.file_utils import load_config
        from cli.inference import build_model
        self.options = options
        self.config = load_config(options['config'])
        loading = time.monotonic()
        self.model = build_model(model_path=options['model_path'], config=self.config,
                                 device=options['device'], use_fp16=options['fp16'])
        self.dp = DataProcessor(hop_size=self.config.audio.hop_size,
                                sample_rate=self.config.audio.sample_rate,
                                phoneset_path=options['phoneset'], device=options['device'])
        self._sync()
        self.model_load_s = time.monotonic() - loading
        self.runtime_init_s = time.monotonic() - started
        self.requests = 0

    def _sync(self):
        device = self.options['device']
        if device.startswith('cuda'):
            self.torch.cuda.synchronize()
        elif device == 'mps':
            self.torch.mps.synchronize()

    def _seed(self, seed):
        import random
        random.seed(seed)
        self.np.random.seed(seed % (2 ** 32))
        self.torch.manual_seed(seed)
        if self.torch.cuda.is_available():
            self.torch.cuda.manual_seed_all(seed)

    def synthesize(self, request: dict) -> dict:
        started = time.monotonic()
        seed = int(request['seed'])
        if self.options.get('legacy_seed') is None:
            self._seed(seed)
        # DataProcessor mutates metadata: never share dictionaries across takes.
        prompt = json.loads(Path(request['prompt_meta']).read_text())[0]
        target = json.loads(Path(request['target_meta']).read_text())[0]
        infer_prompt = self.dp.process(prompt, request['prompt_wav'])
        infer_target = self.dp.process(target, None)
        self._sync()
        preprocessed = time.monotonic()
        with self.torch.no_grad():
            audio = self.model.infer(
                {'prompt': infer_prompt, 'target': infer_target},
                auto_shift=request['auto_shift'], pitch_shift=request['pitch_shift'],
                n_steps=self.config.infer.n_steps, cfg=self.config.infer.cfg,
                control='score', use_fp16=self.options['fp16'])
        audio = audio.squeeze().cpu().numpy()
        inferred = time.monotonic()
        self.sf.write(request['out'], audio, 24000)
        written = time.monotonic()
        reused = self.requests > 0
        self.requests += 1
        return {'out': request['out'], 'sr': 24000,
                'duration_s': round(len(audio) / 24000, 4),
                'f0_shift_semitones': getattr(self.model, 'last_f0_shift', None),
                'model_reused': reused,
                'timings': {'runtime_init_s': 0.0 if reused else round(self.runtime_init_s, 6),
                            'model_load_s': 0.0 if reused else round(self.model_load_s, 6),
                            'preprocess_s': round(preprocessed - started, 6),
                            'inference_s': round(inferred - preprocessed, 6),
                            'write_s': round(written - inferred, 6)}}


def serve(connection, options: dict, log_path: str):
    """One serial private pipe; EOF/None or any failure ends GPU ownership."""
    try:
        with open(log_path, 'a', buffering=1) as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                runtime = None
                while True:
                    request = connection.recv()
                    if request is None:
                        break
                    try:
                        if runtime is None:
                            runtime = SoulXRuntime(options)
                        connection.send({'result': runtime.synthesize(request)})
                    except Exception as e:
                        connection.send({'error': f'{type(e).__name__}: {e}'})
                        break
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ['repo', 'model-path', 'config', 'phoneset', 'prompt-wav',
                 'prompt-meta', 'target-meta', 'out']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--pitch-shift', type=int, default=0)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--auto-shift', dest='auto_shift', action='store_true')
    parser.add_argument('--no-auto-shift', dest='auto_shift', action='store_false')
    parser.add_argument('--fp16', action='store_true')
    parser.set_defaults(auto_shift=False)
    args = vars(parser.parse_args())
    args['legacy_seed'] = args['seed']
    print(json.dumps(SoulXRuntime(args).synthesize(args)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
