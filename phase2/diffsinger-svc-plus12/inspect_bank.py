#!/usr/bin/env python3
"""绮萱声库音素表探查（重建 qixuan_mini_map.json 的前置步骤）。
在会话内跑：/content/lindong_venv/bin/python /content/inspect_bank.py
输出：声库音素名清单（acoustic phonemes.json）＋ dsconfig 关键字段＋
      各语言 phonemes.json 的键样例，供写 map 时核对命名。
"""
import json, os, sys
BANK = "/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau"

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

print("== bank dir ==")
print(sorted(os.listdir(BANK)))
print("== dsconfig.yaml ==")
print(open(os.path.join(BANK, "dsconfig.yaml"), encoding="utf-8").read()[:2000])

# 声学模型音素表：找 *.phonemes.json（acoustic 侧）
for root, dirs, files in os.walk(BANK):
    for fn in files:
        if fn.endswith(".phonemes.json"):
            p = os.path.join(root, fn)
            d = load_json(p)
            keys = list(d.keys()) if isinstance(d, dict) else d[:20]
            print(f"== {p} ({len(keys)} phonemes) ==")
            print(keys)
            # 若是 dict，打印几个值样例
            if isinstance(d, dict):
                for k in keys[:5]:
                    print("   ", k, "->", d[k])
print("== languages ==")
for root, dirs, files in os.walk(BANK):
    for fn in files:
        if fn.endswith(".languages.json"):
            p = os.path.join(root, fn)
            d = load_json(p)
            print(p, "->", d if not isinstance(d, dict) else list(d.keys())[:20])
print("INSPECT-BANK-DONE")
