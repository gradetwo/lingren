#!/bin/bash
# +12 试验重建 · 第二阶段下载（setup 脚本跑完、SVC-ENV-CHECK-OK 之后跑）
# 跑法：上传到 /content/rebuild_downloads.sh，nohup detached 跑，日志 /content/rebuild_downloads.log
# 内容：绮萱声库＋声码器＋SoulX 权重＋YingMusic 代码／检查点／venv
exec > /content/rebuild_downloads.log 2>&1
set -u
cd /content || exit 1

export PATH="/content/lindong_venv/bin:$PATH"
export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
echo "python: $(which python3)"; python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

echo "=== 1. 绮萱声库 Qixuan v2.7.0 ==="
mkdir -p /content/voicebanks
if [ -f /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau/dsconfig.yaml ]; then
  echo "BANK-EXISTS skip download"
else
  cd /content/voicebanks || exit 1
  curl -sL -o Qixuan_v2.7.0_DiffSinger_OpenUtau.zip \
    https://github.com/yqzhishen/qixuan-diffsinger/releases/download/v2.7.0/Qixuan_v2.7.0_DiffSinger_OpenUtau.zip
  ls -la Qixuan_v2.7.0_DiffSinger_OpenUtau.zip
  unzip -q -o Qixuan_v2.7.0_DiffSinger_OpenUtau.zip -d Qixuan_v2.7.0_DiffSinger_OpenUtau_tmp
  # zip 内层可能自带一层目录；抹平到目标目录
  inner=$(find Qixuan_v2.7.0_DiffSinger_OpenUtau_tmp -maxdepth 2 -name "dsconfig.yaml" | head -1 | xargs dirname)
  echo "bank inner dir: $inner"
  mkdir -p Qixuan_v2.7.0_DiffSinger_OpenUtau
  cp -r "$inner"/. Qixuan_v2.7.0_DiffSinger_OpenUtau/
  ls /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau/ | head
  test -f /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau/dsconfig.yaml && echo "BANK-OK" || { echo "BANK-FAIL"; exit 1; }
fi

echo "=== 2. 声码器 pc_nsf_hifigan 44.1k ==="
if [ -f /content/voicebanks/vocoder/unpacked/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx ]; then
  echo "VOCODER-EXISTS skip download"
else
  mkdir -p /content/voicebanks/vocoder/unpacked
  cd /content/voicebanks/vocoder || exit 1
  # 注意：.zip 里只有 model.ckpt（无 .onnx）。 .onnx 在 .oudep 包内（.oudep 本身就是 zip）。
  curl -sL -o vocoder.oudep \
    https://github.com/openvpi/vocoders/releases/download/pc-nsf-hifigan-44.1k-hop512-128bin-2025.02/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.oudep
  ls -la vocoder.oudep
  unzip -q -o vocoder.oudep -d unpacked/
  find unpacked -name "*.onnx" | head
  test -f /content/voicebanks/vocoder/unpacked/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx && echo "VOCODER-OK" || { echo "VOCODER-FAIL"; exit 1; }
fi

echo "=== 3. SoulX 权重（model-svc.pt + model.pt，约 5.6 GB）==="
mkdir -p /content/soulx/pretrained_models/SoulX-Singer
python3 - <<'PYEOF'
from huggingface_hub import hf_hub_download
import os
out = "/content/soulx/pretrained_models/SoulX-Singer"
for fn in ["model-svc.pt", "model.pt"]:
    p = os.path.join(out, fn)
    if os.path.exists(p) and os.path.getsize(p) > 1_000_000_000:
        print("WEIGHT-EXISTS", fn, os.path.getsize(p)); continue
    print("downloading", fn, flush=True)
    got = hf_hub_download(repo_id="Soul-AILab/SoulX-Singer", filename=fn, local_dir=out)
    print("WEIGHT-OK", fn, os.path.getsize(got), flush=True)
print("WEIGHTS-DONE")
PYEOF

echo "=== 4. YingMusic-SVC 代码＋检查点＋venv ==="
if [ ! -d /content/YingMusic-SVC ]; then
  cd /content || exit 1
  git clone --depth 1 https://github.com/GiantAILab/YingMusic-SVC YingMusic-SVC 2>&1 | tail -2
fi
ls /content/YingMusic-SVC | head
mkdir -p /content/YingMusic-SVC/checkpoints
python3 - <<'PYEOF'
from huggingface_hub import hf_hub_download
import os
out = "/content/YingMusic-SVC/checkpoints"
p = os.path.join(out, "YingMusic-SVC-full.pt")
if os.path.exists(p) and os.path.getsize(p) > 100_000_000:
    print("YING-CKPT-EXISTS", os.path.getsize(p))
else:
    got = hf_hub_download(repo_id="GiantAILab/YingMusic-SVC", filename="YingMusic-SVC-full.pt", local_dir=out)
    print("YING-CKPT-OK", os.path.getsize(got), flush=True)
PYEOF
if [ ! -d /content/ying_venv ]; then
  uv venv /content/ying_venv --python 3.12
  uv pip install --python /content/ying_venv/bin/python pip
  uv pip install --python /content/ying_venv/bin/python torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu121 2>&1 | tail -2
  if [ -f /content/YingMusic-SVC/requirements.txt ]; then
    uv pip install --python /content/ying_venv/bin/python -r /content/YingMusic-SVC/requirements.txt 2>&1 | tail -3
  fi
  /content/ying_venv/bin/python -c "import torch, numpy; print('ying venv torch', torch.__version__, 'numpy', numpy.__version__, 'cuda', torch.cuda.is_available())"
fi
echo "REBUILD-DOWNLOADS-DONE"
