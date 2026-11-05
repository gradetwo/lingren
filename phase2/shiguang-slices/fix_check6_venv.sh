#!/bin/bash
# §6 自检重跑（用 lindong_venv python；上一版误跑系统 python 2.11 且缺 rotary）。
# 日志 /content/check6v.log。
exec > /content/check6v.log 2>&1
set -u
export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
PY=/content/lindong_venv/bin/python
$PY -m pip install -q rotary-embedding-torch
$PY - <<'PYEOF'
import torch, torchvision, torchaudio, soundfile
print("torch", torch.__version__, "torchvision", torchvision.__version__, "torchaudio", torchaudio.__version__)
import torchvision.ops
_nms_keep = torchvision.ops.nms(
    torch.tensor([[0., 0., 10., 10.], [1., 1., 11., 11.]]),
    torch.tensor([0.9, 0.8]), 0.5)
assert _nms_keep.tolist() == [0], _nms_keep
print("torchvision nms call OK, keep", _nms_keep.tolist())
import pretty_midi, pyworld, rotary_embedding_torch
print("pretty_midi/pyworld/rotary_embedding_torch import OK")
import transformers
print("transformers import OK", transformers.__version__)
import numpy as np
sf_sr = 24000
tone = (0.1 * np.sin(2 * np.pi * 440 * np.arange(sf_sr) / sf_sr)).astype(np.float32)
soundfile.write("/tmp/svc_env_check.wav", tone, sf_sr)
wav, sr = torchaudio.load("/tmp/svc_env_check.wav")
assert sr == sf_sr and wav.shape[1] == sf_sr, (sr, wav.shape)
print("torchaudio.load roundtrip OK sr", sr, "frames", wav.shape[1])
soundfile.write("/tmp/svc_env_check_sf.wav", tone, sf_sr)
print("soundfile roundtrip OK")
print("SVC-ENV-CHECK-OK")
PYEOF
echo "CHECK6V-DONE"
