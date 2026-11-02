#!/bin/bash
# Phase 2 long-phrase Colab setup: clone repos, install deps (Phase 1
# pins), apply Phase 2 patch copies (01/02 SoulX, 03 VR), download
# weights. Log: /content/setup_long.log
exec > /content/setup_long.log 2>&1
set -x
cd /content
git clone --depth 1 https://github.com/Soul-AILab/SoulX-Singer soulx
git clone --depth 1 https://github.com/pymaster17/VocalRender vr
pip install -q transformers==4.46.3 tokenizers==0.20.3 argbind librosa soundfile scipy huggingface_hub
cp /content/patches/01-soulx-record-f0-shift.patch /tmp/p01.patch
cp /content/patches/02-soulx-cli-seed.patch /tmp/p02.patch
cp /content/patches/03-vocalrender-cli-seed.patch /tmp/p03.patch
cd /content/soulx && git apply --check /tmp/p01.patch && git apply /tmp/p01.patch && echo SOULX_P01_OK
cd /content/soulx && git apply --check /tmp/p02.patch && git apply /tmp/p02.patch && echo SOULX_P02_OK
cd /content/vr && git apply --check /tmp/p03.patch && git apply /tmp/p03.patch && echo VR_P03_OK
cd /content
python3 - <<'PY'
from huggingface_hub import snapshot_download
import os
p1 = snapshot_download("Soul-AILab/SoulX-Singer", allow_patterns=["*.pt"])
print("SOULX_HF_DIR", p1)
p2 = snapshot_download("pymaster/VocalRender", allow_patterns=["VocalRender/*"])
print("VR_HF_DIR", p2)
PY
nvidia-smi --query-gpu=name --format=csv,noheader
python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
echo "SETUP_DONE"
