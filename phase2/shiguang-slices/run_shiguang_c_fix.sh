#!/bin/bash
# 时光切片 · C 切片重跑（start 改 103.77，含首音“那”；上一版 103.78 漏了它）
# 日志 /content/stage1_shiguang_cfix.log
exec > /content/stage1_shiguang_cfix.log 2>&1
set -u
cd /content || exit 1
if [ -d /content/lindong_venv ]; then
  export PATH="/content/lindong_venv/bin:$PATH"
  export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
fi
SCORES=/content/scores/shiguang_slices.json
VOCODER=/content/voicebanks/vocoder/unpacked
QIXUAN=/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau
declare -A BANKDIR=( [qixuan]="$QIXUAN" )
for cfg in $(find /content/voicebanks_banks -maxdepth 3 -name "dsconfig.yaml" 2>/dev/null); do
  d=$(dirname "$cfg"); base=$(basename "$d")
  if echo "$base $d" | grep -qi "lang\|琅"; then BANKDIR[lang]="$d"; fi
  if echo "$base $d" | grep -qiE "(^|[^a-z])ji([^a-z]|$)|齑"; then BANKDIR[ji]="$d"; fi
done
for name in qixuan lang ji; do
  echo "=== render $name sliceC (103.77-113.82) ==="
  python3 /content/sing_bank.py --track shiguang --scores "$SCORES" \
    --bank "${BANKDIR[$name]}" --vocoder "$VOCODER" --map "/content/${name}_map.json" \
    --outdir /content/stage1_shiguang/sliceC --bank-name "$name" \
    --dur score --steps 20 --depth 0.6 --expr 1.0 \
    --start-s 103.77 --end-s 113.82 \
    || { echo "RENDER-FAIL $name sliceC"; continue; }
  echo "=== measure $name sliceC ==="
  python3 /content/measure_bank.py --wav "/content/stage1_shiguang/sliceC/shiguang_stage1_${name}.wav" \
    --scores "$SCORES" --track shiguang --t0 103.72 \
    --start-s 103.77 --end-s 113.82 \
    --out "/content/stage1_shiguang/sliceC/shiguang_stage1_${name}_measure.json" \
    || echo "MEASURE-FAIL $name sliceC"
done
ls -la /content/stage1_shiguang/sliceC/
echo "STAGE1-SHIGUANG-CFIX-DONE"
