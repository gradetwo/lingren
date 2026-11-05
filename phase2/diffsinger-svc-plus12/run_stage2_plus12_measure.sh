#!/bin/bash
# +12 试验 · 第二段量测（SoulX 48k＋YingMusic 44.1k，全部对原始谱面）
# 输入：/content/stage2_plus12/<轨>_stage2_plus12_soulx-svc.wav（48k）
#       /content/stage2_ying_plus12/<轨>_stage2_plus12_yingmusic-svc.wav（44.1k）
# 输出：/content/stage2_plus12/measure/<轨>_stage2_plus12_<svc>.json
# 跑法：nohup detached，日志 /content/stage2_plus12_measure.log
exec > /content/stage2_plus12_measure.log 2>&1
set -u
cd /content || exit 1
export PATH="/content/lindong_venv/bin:$PATH"
mkdir -p /content/stage2_plus12/measure
for t in vox voxTel vox2 vox4; do
  python3 /content/f0_measure.py --wav "/content/stage2_plus12/${t}_stage2_plus12_soulx-svc.wav" \
    --scores /content/scores/vocal_scores.json --track "$t" \
    --out "/content/stage2_plus12/measure/${t}_stage2_plus12_soulx.json" \
    || echo "MEASURE-FAIL ${t} soulx"
  python3 /content/f0_measure.py --wav "/content/stage2_ying_plus12/${t}_stage2_plus12_yingmusic-svc.wav" \
    --scores /content/scores/vocal_scores.json --track "$t" \
    --out "/content/stage2_plus12/measure/${t}_stage2_plus12_yingmusic.json" \
    || echo "MEASURE-FAIL ${t} yingmusic"
done
ls -la /content/stage2_plus12/measure/
echo "STAGE2-PLUS12-MEASURE-DONE"
