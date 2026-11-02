#!/bin/bash
# Phase 1 Colab setup: clone repos, install deps (Phase 0 lessons applied), download weights.
# Log: /content/setup_phase1.log
exec > /content/setup_phase1.log 2>&1
set -x
cd /content
git clone --depth 1 https://github.com/Soul-AILab/SoulX-Singer soulx
git clone --depth 1 https://github.com/pymaster17/VocalRender vr
pip install -q transformers==4.46.3 tokenizers==0.20.3 argbind librosa soundfile scipy huggingface_hub
echo "--- soulx tree ---"
ls soulx soulx/soulxsinger 2>/dev/null | head -40
echo "--- vr tree ---"
ls vr | head -40
python3 - <<'PY'
from huggingface_hub import snapshot_download
import os
p1 = snapshot_download("Soul-AILab/SoulX-Singer", allow_patterns=["*.pt"])
print("SOULX_HF_DIR", p1)
for root, _, files in os.walk(p1):
    for f in files:
        fp = os.path.join(root, f)
        print("SOULX_FILE", fp, os.path.getsize(fp))
p2 = snapshot_download("pymaster/VocalRender", allow_patterns=["VocalRender/*"])
print("VR_HF_DIR", p2)
for root, _, files in os.walk(p2):
    for f in files:
        fp = os.path.join(root, f)
        print("VR_FILE", fp, os.path.getsize(fp))
PY
echo "SETUP_DONE"
