#!/usr/bin/env bash
# Reproduce the prompt-pipeline test run (12 cases).
# Usage: tests/run_tests.sh [python-with-numpy]
#   Default python: the phase1 venv (has numpy + soundfile).
# Exits 0 only if every case's verdict matches EXPECTED below.
set -u
PY="${1:-$HOME/workspace/svs-api-service/phase1/.venv/bin/python}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT_DIR:-/tmp/prompt_pipeline_tests}"
rm -rf "$OUT"; mkdir -p "$OUT"
P0="$HOME/workspace/svs-api-service/phase0/audio"
P1="$HOME/workspace/svs-api-service/phase1/colab_logs"
declare -A EXPECTED=(
  [vr_take0]=accept_with_warnings
  [vr_out1]=accept_with_warnings
  [soulx_A]=accept
  [split_base]=reject
  [generated]=accept_with_warnings
  [gap_singing]=reject
  [synth_short_1s]=reject
  [synth_tone_4s]=reject
  [synth_noise_4s]=reject
  [synth_clipped_4s]=reject
  [synth_bursts_4s]=accept_with_warnings
  [synth_quiet_vibrato_4s]=accept_with_warnings
)
declare -A FILES=(
  [vr_take0]="$P0/vr_take0.wav"
  [vr_out1]="$P0/vr_out1.wav"
  [soulx_A]="$P1/soulx_A.wav"
  [split_base]="$P1/split_base.wav"
  [generated]="$P0/generated.wav"
  [gap_singing]="$DIR/tests/corpus/gap_singing.wav"
  [synth_short_1s]="$DIR/tests/corpus/synth_short_1s.wav"
  [synth_tone_4s]="$DIR/tests/corpus/synth_tone_4s.wav"
  [synth_noise_4s]="$DIR/tests/corpus/synth_noise_4s.wav"
  [synth_clipped_4s]="$DIR/tests/corpus/synth_clipped_4s.wav"
  [synth_bursts_4s]="$DIR/tests/corpus/synth_bursts_4s.wav"
  [synth_quiet_vibrato_4s]="$DIR/tests/corpus/synth_quiet_vibrato_4s.wav"
)
fail=0
for name in "${!EXPECTED[@]}"; do
  "$PY" "$DIR/prompt_pipeline.py" "${FILES[$name]}" \
    --out-dir "$OUT/$name" > "$OUT/$name.stdout" 2> "$OUT/$name.stderr"
  rc=$?
  got=$("$PY" -c "import json;print(json.load(open('$OUT/$name/prompt_report.json'))['verdict'])")
  want="${EXPECTED[$name]}"
  if [ "$got" = "$want" ]; then
    echo "PASS $name verdict=$got rc=$rc"
  else
    echo "FAIL $name verdict=$got want=$want rc=$rc"; fail=1
  fi
done
[ "$fail" = 0 ] && echo "ALL 12 PASS" || echo "FAILURES PRESENT"
exit "$fail"
