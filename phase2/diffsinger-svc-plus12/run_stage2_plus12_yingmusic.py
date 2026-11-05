"""YingMusic-SVC 第二阶段批量驱动（+12 试验变体）。
源 = /content/stage1_plus12/<轨>_stage1_plus12_down12.wav（44.1k，已回谱面音高）
目标 prompt：vox/voxTel=bard_growl，vox2/vox4=groupyell（24k prompt，librosa 重采样到 44.1k）
关键改动：semi_tone_shift = 0（固定不移调）。原研究为 None（自动），vox2/vox4 被上移 12 半音。
输出 = /content/stage2_ying_plus12/<轨>_stage2_plus12_yingmusic-svc.wav（44.1k 原生，峰值归一 0.89）+ sidecar JSON
"""
import io, json, os, re, sys, time
from contextlib import redirect_stdout
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "/content/YingMusic-SVC")
os.chdir("/content/YingMusic-SVC")
import my_inference as mi

# 语义核查：semi_tone_shift=0 必须是"不移调"，不是"自动"。
# 原驱动用 None 表自动；此处显式传 0，并从运行日志的 "automatic pitch shift N semi tones"
# 一行反查实际移调量，写入 sidecar；若日志未报出该行或 N != 0，sidecar 标 shift_verified=False。
src_txt = open("/content/YingMusic-SVC/my_inference.py").read()
i = src_txt.find("semi_tone_shift")
print("MY_INFERENCE semi_tone_shift 上下文：", src_txt[max(0, i-200):i+400].replace("\n", " | ")[:600], flush=True)

PROMPTS = {"vox": "bard_growl", "voxTel": "bard_growl",
           "vox2": "groupyell", "vox4": "groupyell"}
TRACKS = ["voxTel", "vox", "vox2", "vox4"]  # 短轨先行，冒烟在前

class Args:
    pass

args = Args()
args.checkpoint = "/content/YingMusic-SVC/checkpoints/YingMusic-SVC-full.pt"
args.config = "./configs/YingMusic-SVC.yml"
args.cuda = torch.device("cuda:0")
args.fp16 = True
args.length_adjust = 1.0
args.inference_cfg_rate = 0.7
args.f0_condition = True
args.semi_tone_shift = 0  # ← 固定 0，不许自动移调
args.output = "./outputs"
args.diffusion_steps = 100
os.makedirs(args.output, exist_ok=True)

print("=== 加载 YingMusic 模型 ===", flush=True)
t0 = time.time()
models = mi.load_models_api(args, device=args.cuda)
print(f"模型加载耗时 {time.time()-t0:.0f}s", flush=True)

os.makedirs("/content/stage2_ying_plus12", exist_ok=True)
for t in TRACKS:
    p = PROMPTS[t]
    a = Args()
    a.__dict__.update(args.__dict__)
    a.source = f"/content/stage1_plus12/{t}_stage1_plus12_down12.wav"
    a.target = f"/content/scores/dry-vocal-package/voice/prompts_24k/{p}_24k.wav"
    a.expname = "ds_twostage_plus12"
    a.diffusion_steps = 100
    buf = io.StringIO()
    t0 = time.time()
    try:
        with redirect_stdout(buf):
            out_path = mi.run_inference(a, models, device=args.cuda)
        log = buf.getvalue()
    except Exception as e:
        print(f"YING-PLUS12-FAIL {t} {type(e).__name__}: {e}", flush=True)
        print(buf.getvalue()[-1500:], flush=True)
        continue
    dt = time.time() - t0
    m = re.search(r"automatic pitch shift (-?\d+) semi tones", log)
    shift = int(m.group(1)) if m else None
    rtf_m = re.search(r"RTF: ([\d.]+)", log)
    rtf = float(rtf_m.group(1)) if rtf_m else None
    w, sr = sf.read(out_path)
    assert sr == 44100, (out_path, sr)
    if w.ndim > 1: w = w[:, 0]
    raw_peak = float(np.max(np.abs(w)))
    raw_rms = float(np.sqrt(np.mean(w ** 2)))
    gain = 0.89 / raw_peak if raw_peak > 0 else 1.0
    out = (w * gain).astype(np.float32)
    sf.write(f"/content/stage2_ying_plus12/{t}_stage2_plus12_yingmusic-svc.wav", out, 44100, subtype="PCM_16")
    rec = {"track": t, "svc": "yingmusic", "prompt": p, "prompt_wav": f"{p}_24k.wav",
           "diffusion_steps": 100, "fp16": True, "stage": "plus12",
           "semi_tone_shift_requested": 0,
           "auto_pitch_shift_semitones": shift,
           "shift_verified_zero": (shift == 0),
           "rtf": rtf, "runtime_s": round(dt, 1), "status": "ok",
           "duration_s": round(len(out) / 44100, 3),
           "peak_raw": round(raw_peak, 4), "rms_raw": round(raw_rms, 4),
           "gain_to_0.89": round(gain, 4), "sr_delivered": 44100,
           "raw_output": out_path,
           "source": f"{t}_stage1_plus12_down12.wav"}
    json.dump(rec, open(f"/content/stage2_ying_plus12/{t}_stage2_plus12_yingmusic-svc.json", "w"), ensure_ascii=False, indent=1)
    print(f"YING-PLUS12-OK {t} prompt={p} 请求移调=0 实际移调={shift}半音 verified={shift==0} RTF={rtf} 耗时={dt:.0f}s 时长={rec['duration_s']}s 原始峰值={raw_peak:.4f}", flush=True)
print("YING-PLUS12-BATCH-DONE", flush=True)
