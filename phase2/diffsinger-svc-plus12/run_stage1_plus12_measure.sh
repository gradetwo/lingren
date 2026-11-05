#!/bin/bash
# +12 试验 · 第一阶段后：下移 12 半音＋f0 量测（移调前对 +12 谱、移调后对原谱）
# 输入：/content/stage1_plus12/<轨>_stage1_plus12.wav
# 输出：同目录 <轨>_stage1_plus12_down12.wav + sidecar；
#       量测 /content/stage1_plus12/measure/<轨>_preshift_plus12.json（对 +12 谱）
#       量测 /content/stage1_plus12/measure/<轨>_down12_orig.json（对原谱）
# 跑法：nohup detached，日志 /content/stage1_plus12_measure.log
exec > /content/stage1_plus12_measure.log 2>&1
set -u
cd /content || exit 1
export PATH="/content/lindong_venv/bin:$PATH"
export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
mkdir -p /content/stage1_plus12/measure
echo "=== shift down 12 (librosa, no formant preservation) ==="
python3 /content/shift_down12.py --tracks vox voxTel vox2 vox4 --indir /content/stage1_plus12 \
  || { echo "SHIFT-FAIL"; exit 1; }
echo "=== f0 measure: pre-shift vs +12 scores; down-shifted vs original scores ==="
for t in vox voxTel vox2 vox4; do
  python3 /content/f0_measure.py --wav "/content/stage1_plus12/${t}_stage1_plus12.wav" \
    --scores /content/scores/vocal_scores_plus12.json --track "$t" \
    --out "/content/stage1_plus12/measure/${t}_preshift_plus12.json" \
    || echo "MEASURE-FAIL ${t} preshift"
  python3 /content/f0_measure.py --wav "/content/stage1_plus12/${t}_stage1_plus12_down12.wav" \
    --scores /content/scores/vocal_scores.json --track "$t" \
    --out "/content/stage1_plus12/measure/${t}_down12_orig.json" \
    || echo "MEASURE-FAIL ${t} down12"
done
ls -la /content/stage1_plus12/measure/
echo "STAGE1-PLUS12-MEASURE-DONE"
