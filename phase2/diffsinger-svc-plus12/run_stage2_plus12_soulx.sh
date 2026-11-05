#!/bin/bash
# +12 试验 · 第二阶段 SoulX SVC（变体：源为下移后音频，输出到 stage2_plus12）
# 源 = /content/stage1_plus12/<轨>_stage1_plus12_down12.wav（44.1k，已回谱面音高）
# 目标 prompt 与原研究一致：vox/voxTel=bard_growl，vox2/vox4=groupyell，vox3 不跑
# 输出 = /content/stage2_plus12/<轨>_stage2_plus12_soulx-svc.wav（48k，峰值归一 0.89）+ sidecar
# 跑法：nohup detached，日志 /content/stage2_plus12_soulx.log
exec > /content/stage2_plus12_soulx.log 2>&1
set -u
cd /content || exit 1

export PATH="/content/lindong_venv/bin:$PATH"
export LD_LIBRARY_PATH="/content/lindong_venv/lib/python3.13/site-packages/nvidia/npp/lib:${LD_LIBRARY_PATH:-}"
echo "python: $(which python3)"
python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
export PYTHONPATH="/content/soulx:${PYTHONPATH:-}"
cd /content/soulx || exit 1
python3 -c "import cli.inference_svc, preprocess.pipeline; print('PRECHECK-OK')" || { echo "STAGE2-PLUS12-SOULX-FAIL precheck"; exit 1; }

prep_one() {  # $1=name $2=wav
  if [ -f "/content/prep_$1/vocal.wav" ] && [ -f "/content/prep_$1/vocal_f0.npy" ]; then
    echo "PREP-SKIP $1 (already prepped)"; return 0
  fi
  python3 -m preprocess.pipeline --audio_path "$2" --save_dir "/content/prep_$1" \
    --language Mandarin --device cuda --vocal_sep False --max_merge_duration 30000 --midi_transcribe False \
    || { echo "PREP-FAIL $1"; return 1; }
  test -f "/content/prep_$1/vocal.wav" && test -f "/content/prep_$1/vocal_f0.npy" \
    || { echo "PREP-MISSING $1"; return 1; }
  echo "PREP-OK $1"
}

echo "=== prep prompts ==="
for p in bard_growl bard_scream groupyell; do
  prep_one "$p" "/content/scores/dry-vocal-package/voice/prompts_24k/${p}_24k.wav" || { echo "STAGE2-PLUS12-SOULX-FAIL prep prompt $p"; exit 1; }
done

echo "=== prep targets (+12 down-shifted wavs) ==="
for t in vox voxTel vox2 vox4; do
  prep_one "tgt_plus12_$t" "/content/stage1_plus12/${t}_stage1_plus12_down12.wav" || { echo "STAGE2-PLUS12-SOULX-FAIL prep tgt $t"; exit 1; }
done

echo "=== SVC runs ==="
mkdir -p /content/stage2_plus12
svc_run() {  # $1=track $2=prompt
  local start=$SECONDS
  rm -rf "/content/svc_out_plus12_${1}__${2}"
  python3 -m cli.inference_svc --device cuda \
    --model_path pretrained_models/SoulX-Singer/model-svc.pt \
    --config soulxsinger/config/soulxsinger.yaml \
    --prompt_wav_path "/content/prep_${2}/vocal.wav" --prompt_f0_path "/content/prep_${2}/vocal_f0.npy" \
    --target_wav_path "/content/prep_tgt_plus12_${1}/vocal.wav" --target_f0_path "/content/prep_tgt_plus12_${1}/vocal_f0.npy" \
    --save_dir "/content/svc_out_plus12_${1}__${2}" --pitch_shift 0 --fp16
  local rc=$?
  local dt=$((SECONDS - start))
  if [ $rc -ne 0 ] || [ ! -f "/content/svc_out_plus12_${1}__${2}/generated.wav" ]; then
    echo "SVC-FAIL $1 $2 rc=$rc time=${dt}s"; return 1
  fi
  echo "SVC-OK $1 $2 time=${dt}s"
  return 0
}

declare -A PROMPTS=( [vox]=bard_growl [voxTel]=bard_growl [vox2]=groupyell [vox4]=groupyell )
for t in voxTel vox vox2 vox4; do
  svc_run "$t" "${PROMPTS[$t]}" || echo "SVC-FAILED-TRACK $t"
done

echo "=== collect 24k -> 48k + normalize + sidecar ==="
python3 - <<'PYEOF' || { echo "STAGE2-PLUS12-SOULX-FAIL collect"; exit 1; }
import json, os
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
prompts = {"vox": "bard_growl", "voxTel": "bard_growl", "vox2": "groupyell", "vox4": "groupyell"}
os.makedirs("/content/stage2_plus12", exist_ok=True)
for t, p in prompts.items():
    src = f"/content/svc_out_plus12_{t}__{p}/generated.wav"
    rec = {"track": t, "svc": "soulx", "prompt": p, "prompt_wav": f"{p}_24k.wav",
           "pitch_shift": 0, "fp16": True, "stage": "plus12",
           "source": f"{t}_stage1_plus12_down12.wav"}
    if not os.path.exists(src):
        rec["status"] = "failed_no_output"
        print("COLLECT", t, "FAILED_NO_OUTPUT"); continue
    w, sr = sf.read(src)
    assert sr == 24000, (src, sr)
    if w.ndim > 1: w = w[:, 0]
    raw_peak = float(np.max(np.abs(w)))
    raw_rms = float(np.sqrt(np.mean(w ** 2)))
    up = resample_poly(w.astype(np.float32), 2, 1).astype(np.float32)
    gain = 0.89 / float(np.max(np.abs(up))) if np.max(np.abs(up)) > 0 else 1.0
    out = up * gain
    sf.write(f"/content/stage2_plus12/{t}_stage2_plus12_soulx-svc.wav", out, 48000, subtype="PCM_16")
    sf.write(f"/content/stage2_plus12/{t}_stage2_plus12_soulx-svc_24k.wav", w.astype(np.float32), 24000, subtype="PCM_16")
    rec.update({"status": "ok", "duration_s_48k": round(len(out)/48000, 3),
                "duration_s_24k": round(len(w)/24000, 3),
                "peak_raw_24k": round(raw_peak, 4), "rms_raw_24k": round(raw_rms, 4),
                "gain_to_0.89": round(gain, 4), "sr_delivered": 48000})
    print("COLLECT", t, "OK dur", rec["duration_s_48k"], "raw_peak", rec["peak_raw_24k"], "gain", rec["gain_to_0.89"])
    json.dump(rec, open(f"/content/stage2_plus12/{t}_stage2_plus12_soulx-svc.json", "w"), ensure_ascii=False, indent=1)
print("STAGE2-PLUS12-SOULX-COLLECT-DONE")
PYEOF
echo "STAGE2-PLUS12-SOULX-DONE"
