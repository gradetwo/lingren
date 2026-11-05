#!/bin/bash
# +12 试验 · 第一阶段（DiffSinger 合成，谱面 +12 半音）
# 输入：/content/scores/vocal_scores_plus12.json（4 轨音高 +12；vox3 不动，不跑）
# 输出：/content/stage1_plus12/<轨>_stage1_plus12.wav（44.1kHz，峰值归一 0.89）+ sidecar
# 参数与原研究一致：--dur score --steps 20 --depth 0.6 --expr 1.0
# 跑法：nohup detached，日志 /content/stage1_plus12.log
exec > /content/stage1_plus12.log 2>&1
set -u
cd /content || exit 1
export PATH="/content/lindong_venv/bin:$PATH"
export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
python3 -c "import onnxruntime, pypinyin, soundfile, numpy, yaml; print('stage1 imports OK')" \
  || pip install -q onnxruntime-gpu pypinyin soundfile numpy pyyaml
python3 -c "import onnxruntime as ort; print('ort providers:', ort.get_available_providers())"
mkdir -p /content/stage1_plus12
echo "=== probe voxTel 前 4 音（烟囱：声库＋map＋声码器链路）==="
python3 /content/sing_qixuan.py --track voxTel \
  --scores /content/scores/vocal_scores_plus12.json \
  --bank /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau \
  --vocoder /content/voicebanks/vocoder/unpacked \
  --map /content/qixuan_mini_map.json --outdir /content/stage1_plus12 \
  --dur score --steps 20 --depth 0.6 --expr 1.0 --probe \
  || { echo "STAGE1-PLUS12-FAIL probe"; exit 1; }
for t in vox voxTel vox2 vox4; do
  echo "=== stage1+12 $t ==="
  python3 /content/sing_qixuan.py --track "$t" \
    --scores /content/scores/vocal_scores_plus12.json \
    --bank /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau \
    --vocoder /content/voicebanks/vocoder/unpacked \
    --map /content/qixuan_mini_map.json --outdir /content/stage1_plus12 \
    --dur score --steps 20 --depth 0.6 --expr 1.0 \
    || echo "STAGE1-PLUS12-FAIL $t"
  # sing_qixuan.py 写死输出名 <轨>_stage1_diffsinger.wav/.json；改名成 +12 口径
  mv -f "/content/stage1_plus12/${t}_stage1_diffsinger.wav" "/content/stage1_plus12/${t}_stage1_plus12.wav" 2>/dev/null
  mv -f "/content/stage1_plus12/${t}_stage1_diffsinger.json" "/content/stage1_plus12/${t}_stage1_plus12.json" 2>/dev/null
  ls -la "/content/stage1_plus12/${t}_stage1_plus12.wav" "/content/stage1_plus12/${t}_stage1_plus12.json" 2>/dev/null \
    || echo "STAGE1-PLUS12-MISSING $t"
done
ls -la /content/stage1_plus12/
echo "STAGE1-PLUS12-DONE"
