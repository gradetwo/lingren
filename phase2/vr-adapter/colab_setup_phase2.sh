#!/bin/bash
# Phase 2 VR-line Colab setup: clone vr + soulx, apply patches, install
# deps, download weights, verify the VR CLI surface.
# Runs on the VM; log: /content/setup_phase2.log
exec > /content/setup_phase2.log 2>&1
set -x
cd /content
nvidia-smi | head -12
git clone --depth 1 https://github.com/pymaster17/VocalRender vr
git clone --depth 1 https://github.com/Soul-AILab/SoulX-Singer soulx
cd /content/vr && git apply /content/bundle/patches/03-vocalrender-cli-seed.patch
cd /content/soulx
git apply /content/bundle/patches/00-soulx-llama-config-kwargs.patch
git apply /content/bundle/patches/01-soulx-record-f0-shift.patch
git apply /content/bundle/patches/02-soulx-cli-seed.patch
cd /content
pip install -q transformers==4.46.3 tokenizers==0.20.3 argbind librosa soundfile scipy huggingface_hub fastapi uvicorn pydantic python-multipart httpx
echo "--- VR CLI args ---"
grep -n "add_argument" vr/scripts/infer_vocalrender_svs_single.py | head -30
echo "--- VR json_file handling ---"
grep -n "json_file\|item_name" vr/scripts/infer_vocalrender_svs_single.py | head -20
echo "--- VR prompt audio ---"
ls -la vr/examples/prompt_audio/ 2>/dev/null | head
python3 - <<'PY'
import soundfile as sf, glob
for p in sorted(glob.glob("/content/vr/examples/prompt_audio/*.wav")):
    info = sf.info(p)
    print("PROMPT", p, round(info.duration, 3), "s", info.samplerate, "Hz")
PY
python3 - <<'PY'
from huggingface_hub import snapshot_download
import os
p1 = snapshot_download("Soul-AILab/SoulX-Singer", allow_patterns=["*.pt"])
print("SOULX_HF_DIR", p1)
for root, _, files in os.walk(p1):
    for f in files:
        print("SOULX_FILE", os.path.join(root, f))
p2 = snapshot_download("pymaster/VocalRender", allow_patterns=["VocalRender/*"])
print("VR_HF_DIR", p2)
for root, _, files in os.walk(p2):
    print("VR_CKPT_CANDIDATE", root)
    for f in files:
        print("VR_FILE", os.path.join(root, f))
PY
echo "SETUP_DONE"
