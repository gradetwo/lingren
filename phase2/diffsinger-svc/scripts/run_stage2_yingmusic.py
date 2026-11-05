"""YingMusic-SVC 第二阶段批量驱动（5 轨，模型只加载一次）。
源 = /content/stage1/<轨>_stage1_diffsinger.wav（44.1k）
目标 prompt：vox/voxTel=bard_growl，vox2/vox4=groupyell，vox3=bard_scream（24k prompt，librosa 重采样到 44.1k）
输出 = /content/stage2_ying/<轨>_stage2_yingmusic-svc.wav（44.1k 原生，峰值归一 0.89）+ sidecar JSON
"""
import io, json, os, re, sys, time
from contextlib import redirect_stdout
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "/content/YingMusic-SVC")
os.chdir("/content/YingMusic-SVC")
import my_inference as mi

PROMPTS = {"vox": "bard_growl", "voxTel": "bard_growl",
           "vox2": "groupyell", "vox3": "bard_scream", "vox4": "groupyell"}
TRACKS = ["voxTel", "vox3", "vox", "vox2", "vox4"]  # 短轨先行，冒烟在前

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
args.semi_tone_shift = None
args.output = "./outputs"
args.diffusion_steps = 100
os.makedirs(args.output, exist_ok=True)

print("=== 加载 YingMusic 模型（RMVPE/CAMPPlus/BigVGAN/Whisper 首次自动下载） ===", flush=True)
t0 = time.time()
models = mi.load_models_api(args, device=args.cuda)
print(f"模型加载耗时 {time.time()-t0:.0f}s", flush=True)

os.makedirs("/content/stage2_ying", exist_ok=True)
for t in TRACKS:
    p = PROMPTS[t]
    a = Args()
    a.__dict__.update(args.__dict__)
    a.source = f"/content/stage1/{t}_stage1_diffsinger.wav"
    a.target = f"/content/scores/dry-vocal-package/voice/prompts_24k/{p}_24k.wav"
    a.expname = "ds_twostage"
    a.diffusion_steps = 100
    buf = io.StringIO()
    t0 = time.time()
    try:
        with redirect_stdout(buf):
            out_path = mi.run_inference(a, models, device=args.cuda)
        log = buf.getvalue()
    except Exception as e:
        print(f"YING-FAIL {t} {type(e).__name__}: {e}", flush=True)
        print(buf.getvalue()[-1500:], flush=True)
        continue
    dt = time.time() - t0
    m = re.search(r"automatic pitch shift (-?\d+) semi tones", log)
    shift = int(m.group(1)) if m else None
    rtf_m = re.search(r"RTF: ([\d.]+)", log)
    rtf = float(rtf_m.group(1)) if rtf_m else None
    # 收集：44.1k 原生，峰值归一 0.89
    w, sr = sf.read(out_path)
    assert sr == 44100, (out_path, sr)
    if w.ndim > 1: w = w[:, 0]
    raw_peak = float(np.max(np.abs(w)))
    raw_rms = float(np.sqrt(np.mean(w ** 2)))
    gain = 0.89 / raw_peak if raw_peak > 0 else 1.0
    out = (w * gain).astype(np.float32)
    sf.write(f"/content/stage2_ying/{t}_stage2_yingmusic-svc.wav", out, 44100, subtype="PCM_16")
    rec = {"track": t, "svc": "yingmusic", "prompt": p, "prompt_wav": f"{p}_24k.wav",
           "diffusion_steps": 100, "fp16": True, "auto_pitch_shift_semitones": shift,
           "rtf": rtf, "runtime_s": round(dt, 1), "status": "ok",
           "duration_s": round(len(out) / 44100, 3),
           "peak_raw": round(raw_peak, 4), "rms_raw": round(raw_rms, 4),
           "gain_to_0.89": round(gain, 4), "sr_delivered": 44100,
           "raw_output": out_path}
    json.dump(rec, open(f"/content/stage2_ying/{t}_stage2_yingmusic-svc.json", "w"), ensure_ascii=False, indent=1)
    print(f"YING-OK {t} prompt={p} 自动移调={shift}半音 RTF={rtf} 耗时={dt:.0f}s 时长={rec['duration_s']}s 原始峰值={raw_peak:.4f}", flush=True)
print("YING-BATCH-DONE", flush=True)
