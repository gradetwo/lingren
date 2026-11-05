#!/bin/bash
# 时光切片 · 第一阶段全量：A（修正 end=10.94，含末音“口”）＋B＋C，3 库
# 日志 /content/stage1_shiguang_all.log
exec > /content/stage1_shiguang_all.log 2>&1
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
echo "banks:"; for b in qixuan lang ji; do echo "  $b -> ${BANKDIR[$b]:-MISSING}"; done

run_slice () {
  local slice="$1" start="$2" end="$3" t0="$4"
  mkdir -p "/content/stage1_shiguang/$slice"
  for name in qixuan lang ji; do
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

run_slice sliceA 0.5 10.94 0.45
run_slice sliceB 51.85 63.20 51.80
run_slice sliceC 103.78 113.82 103.73
for s in sliceA sliceB sliceC; do ls -la "/content/stage1_shiguang/$s/"; done
echo "STAGE1-SHIGUANG-ALL-DONE"
