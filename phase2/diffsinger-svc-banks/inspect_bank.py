#!/usr/bin/env python3
"""任意 OpenUtau DiffSinger 声库探查（sing_bank.py 的前置步骤）。
用法（会话内，system python）：python3 inspect_bank.py <声库目录>
输出：声库目录清单＋dsconfig.yaml＋各音素表键样例＋各语言表＋
      每个 ONNX 模型的输入／输出名、形状、类型（供发现说话人输入与 I/O 差异）。
"""
import json, os, sys, glob

BANK = sys.argv[1] if len(sys.argv) > 1 else "/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau"

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

print("== bank dir ==")
print(sorted(os.listdir(BANK)))
cfg = os.path.join(BANK, "dsconfig.yaml")
if os.path.exists(cfg):
    print("== dsconfig.yaml ==")
    print(open(cfg, encoding="utf-8").read()[:3000])
else:
    print("== dsconfig.yaml 缺失 ==")
for fn in ("character.txt", "character.yaml"):
    p = os.path.join(BANK, fn)
    if os.path.exists(p):
        print(f"== {fn} ==")
        print(open(p, encoding="utf-8", errors="replace").read()[:1500])

for root, dirs, files in os.walk(BANK):
    for fn in sorted(files):
        if fn == "phonemes.json" or fn.endswith(".phonemes.json"):
            p = os.path.join(root, fn)
            d = load_json(p)
            keys = list(d.keys()) if isinstance(d, dict) else list(d)
            print(f"== {p} ({len(keys)} phonemes) ==")
            print(keys)
            if isinstance(d, dict):
                for k in keys[:5]:
                    print("   ", k, "->", d[k])
        if fn == "languages.json" or fn.endswith(".languages.json"):
            p = os.path.join(root, fn)
            d = load_json(p)
            print(p, "->", d if not isinstance(d, dict) else list(d.keys()))

try:
    import onnxruntime as ort
    print("== ONNX I/O ==")
    for p in sorted(glob.glob(os.path.join(BANK, "**/*.onnx"), recursive=True)):
        rel = os.path.relpath(p, BANK)
        try:
            s = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
            ins = [(i.name, list(i.shape), i.type) for i in s.get_inputs()]
            outs = [(o.name, list(o.shape), o.type) for o in s.get_outputs()]
            print(f"-- {rel}\n   in: {ins}\n   out: {outs}")
        except Exception as e:
            print(f"-- {rel} 读失败: {e}")
except ImportError:
    print("onnxruntime 未装，跳过 ONNX I/O 探查")
print("INSPECT-BANK-DONE")
