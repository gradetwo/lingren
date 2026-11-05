#!/bin/bash
# 时光切片 · 第一阶段 B、C 切片（只跑烟囱通过的库）
# 用法：BANKS="qixuan lang ji" nohup bash /content/run_shiguang_bc.sh > /content/stage1_shiguang_bc.log 2>&1 &
# 输出：/content/stage1_shiguang/sliceB|sliceC/*_stage1_<bank>.wav/.json/_measure.json
exec > /content/stage1_shiguang_bc.log 2>&1
set -u
cd /content || exit 1
if [ -d /content/lindong_venv ]; then
  export PATH="/content/lindong_venv/bin:$PATH"
  export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
fi
echo "python: $(which python3)"
SCORES=/content/scores/shiguang_slices.json
VOCODER=/content/voicebanks/vocoder/unpacked
QIXUAN=/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau
declare -A BANKDIR=( [qixuan]="$QIXUAN" )
for cfg in $(find /content/voicebanks_banks -maxdepth 3 -name "dsconfig.yaml" 2>/dev/null); do
  d=$(dirname "$cfg"); base=$(basename "$d")
  if echo "$base $d" | grep -qi "lang\|琅"; then BANKDIR[lang]="$d"; fi
  if echo "$base $d" | grep -qiE "(^|[^a-z])ji([^a-z]|$)|齑"; then BANKDIR[ji]="$d"; fi
done
echo "banks: ${!BANKDIR[@]}"
for b in ${BANKS:-qixuan}; do echo "  $b -> ${BANKDIR[$b]:-MISSING} map /content/${b}_map.json"; done

run_slice () {
  local slice="$1" start="$2" end="$3" t0="$4"
  mkdir -p "/content/stage1_shiguang/$slice"
  for name in ${BANKS:-qixuan}; do
    echo "=== render $name $slice ($start-$end) ==="
    python3 /content/sing_bank.py --track shiguang --scores "$SCORES" \
      --bank "${BANKDIR[$name]}" --vocoder "$VOCODER" --map "/content/${name}_map.json" \
      --outdir "/content/stage1_shiguang/$slice" --bank-name "$name" \
      --dur score --steps 20 --depth 0.6 --expr 1.0 \
      --start-s "$start" --end-s "$end" \
      || { echo "RENDER-FAIL $name $slice"; continue; }
    echo "=== measure $name $slice ==="
    python3 /content/measure_bank.py --wav "/content/stage1_shiguang/$slice/shiguang_stage1_${name}.wav" \
      --scores "$SCORES" --track shiguang --t0 "$t0" \
      --start-s "$start" --end-s "$end" \
      --out "/content/stage1_shiguang/$slice/shiguang_stage1_${name}_measure.json" \
      || echo "MEASURE-FAIL $name $slice"
  done
}

run_slice sliceB 51.85 63.20 51.80
run_slice sliceC 103.78 113.81 103.73
ls -la /content/stage1_shiguang/sliceB/ /content/stage1_shiguang/sliceC/
echo "STAGE1-SHIGUANG-BC-DONE"
