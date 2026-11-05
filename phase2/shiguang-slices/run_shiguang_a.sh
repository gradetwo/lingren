#!/bin/bash
# 时光切片 · 第一阶段 A 切片 × 3 库＋量测＋烟囱门
# 输入：/content/scores/shiguang_slices.json，声库 /content/voicebanks*，脚本 /content/*.py
# 输出：/content/stage1_shiguang/sliceA/*_stage1_<bank>.wav/.json/_measure.json
# 跑法：nohup detached，日志 /content/stage1_shiguang_a.log
exec > /content/stage1_shiguang_a.log 2>&1
set -u
cd /content || exit 1
if [ -d /content/lindong_venv ]; then
  export PATH="/content/lindong_venv/bin:$PATH"
  export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
fi
echo "python: $(which python3)"; python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" 2>&1 | tail -1
python3 -c "import onnxruntime, pypinyin, soundfile, numpy, yaml, pyworld; print('stage1 imports OK')" \
  || pip install -q onnxruntime-gpu pypinyin soundfile numpy pyyaml pyworld
python3 -c "import onnxruntime as ort; print('ort providers:', ort.get_available_providers())"
python3 -c "import pyworld; print('pyworld OK')"
mkdir -p /content/stage1_shiguang/sliceA /content/scores

# 声库目录发现：按目录名／dsconfig 内容判琅／齑
QIXUAN=/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau
LANG_DIR=""; JI_DIR=""
for cfg in $(find /content/voicebanks_banks -maxdepth 3 -name "dsconfig.yaml" 2>/dev/null); do
  d=$(dirname "$cfg")
  base=$(basename "$d")
  if echo "$base $d" | grep -qi "lang\|琅"; then LANG_DIR="$d"; fi
  if echo "$base $d" | grep -qiE "(^|[^a-z])ji([^a-z]|$)|齑"; then JI_DIR="$d"; fi
  echo "candidate bank dir: $d"
done
for d in "$QIXUAN" "$LANG_DIR" "$JI_DIR"; do
  echo "bank dir: $d"; ls "$d" 2>/dev/null | head -5
done
test -f "$QIXUAN/dsconfig.yaml" || { echo "QIXUAN-MISSING"; exit 1; }
test -n "$LANG_DIR" && test -f "$LANG_DIR/dsconfig.yaml" || echo "LANG-DIR-WARN $LANG_DIR"
test -n "$JI_DIR" && test -f "$JI_DIR/dsconfig.yaml" || echo "JI-DIR-WARN $JI_DIR"

SCORES=/content/scores/shiguang_slices.json
VOCODER=/content/voicebanks/vocoder/unpacked
A_START=0.5; A_END=10.93; A_T0=0.45

echo "=== 建 map（时光谱子汉字集）==="
python3 /content/build_bank_map.py "$QIXUAN" "$SCORES" /content/qixuan_map.json || echo "MAP-FAIL qixuan"
python3 /content/build_bank_map.py "$LANG_DIR" "$SCORES" /content/lang_map.json || echo "MAP-FAIL lang"
python3 /content/build_bank_map.py "$JI_DIR" "$SCORES" /content/ji_map.json || echo "MAP-FAIL ji"

run_bank () {
  local name="$1" bank="$2" map="$3"
  echo "=== probe $name（A 切片前 4 音）==="
  python3 /content/sing_bank.py --track shiguang --scores "$SCORES" \
    --bank "$bank" --vocoder "$VOCODER" --map "$map" \
    --outdir /content/stage1_shiguang/sliceA --bank-name "$name" \
    --dur score --steps 20 --depth 0.6 --expr 1.0 \
    --start-s "$A_START" --end-s "$A_END" --probe \
    || { echo "PROBE-FAIL $name"; return 1; }
  echo "=== render $name A 切片 ==="
  python3 /content/sing_bank.py --track shiguang --scores "$SCORES" \
    --bank "$bank" --vocoder "$VOCODER" --map "$map" \
    --outdir /content/stage1_shiguang/sliceA --bank-name "$name" \
    --dur score --steps 20 --depth 0.6 --expr 1.0 \
    --start-s "$A_START" --end-s "$A_END" \
    || { echo "RENDER-FAIL $name"; return 1; }
  echo "=== measure $name A 切片 ==="
  python3 /content/measure_bank.py --wav "/content/stage1_shiguang/sliceA/shiguang_stage1_${name}.wav" \
    --scores "$SCORES" --track shiguang --t0 "$A_T0" \
    --start-s "$A_START" --end-s "$A_END" \
    --out "/content/stage1_shiguang/sliceA/shiguang_stage1_${name}_measure.json" \
    || echo "MEASURE-FAIL $name"
}

run_bank qixuan "$QIXUAN" /content/qixuan_map.json
run_bank lang "$LANG_DIR" /content/lang_map.json
run_bank ji "$JI_DIR" /content/ji_map.json
ls -la /content/stage1_shiguang/sliceA/
echo "STAGE1-SHIGUANG-A-DONE"
