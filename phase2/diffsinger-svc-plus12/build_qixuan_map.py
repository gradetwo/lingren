#!/usr/bin/env python3
"""重建 /content/qixuan_mini_map.json（+12 试验用）。
依据：声库 acoustic phonemes.json 的实际音素名（先跑 inspect_bank.py 核对命名）。
拼音切分与英文词表与原研究 build_ds.py 完全一致（可比性：同一代码＋同一映射结构）：
  中文：pypinyin Style.NORMAL 无调全拼，按声母表切分（build_ds.py split_pinyin 原样，
        含 ü->v、yu->[y, v...]、y->[y, ...]、w->[w, ...]）。
  英文：winter/is/coming 三词（EN_PHONEMES 原样），片段由 sing_qixuan.py 的
        startswith/endswith 分支处理，不另加断词条目。
音素名形式：声库表是 "语言/音素" 形式（sing_qixuan.py 里 p.split("/")[1] 作证），
故 zh 值加 "zh/" 前缀、en 值加 "en/" 前缀（含分隔符 "|" -> "en/|"）。
跑法：在会话内跑 /content/lindong_venv/bin/python /content/build_qixuan_map.py
输出：/content/qixuan_mini_map.json，并逐项核对每个音素都在声库 acoustic 表里
      （不在的打印 MISSING 并退出 1，绝不猜）。
"""
import json, os, sys

BANK = "/content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau"
OUT = "/content/qixuan_mini_map.json"

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

# --- 与 build_ds.py 完全一致的拼音切分 ---
INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
            "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"]

def split_pinyin(py):
    """'tian' -> ['t', 'ian']; 'zhi' -> ['zh', 'i']; 'yu' -> ['y', 'v']."""
    for init in INITIALS:
        if py.startswith(init) and len(py) > len(init):
            final = py[len(init):]
            if final.startswith("ü"):
                final = "v" + final[1:]
            # j/q/x 后的 ue 读作 üe（que/xue -> qve/xve）；声库表只有 zh/ve，无 zh/ue
            if final == "ue":
                final = "ve"
            return [init, final]
    if py.startswith("yu"):
        return ["y", "v" + py[2:]]
    if py.startswith("y"):
        return ["y", py[1:] or "i"]
    if py.startswith("w"):
        return ["w", py[1:] or "u"]
    return [py]

EN_PHONEMES = {
    "winter": ["w", "ih", "n", "|", "t", "er"],
    "is": ["ih", "z"],
    "coming": ["k", "ah", "m", "|", "ih", "ng"],
}

def main():
    # 声学表：0101_qixuan_muon1_acoustic.phonemes.json（sing_qixuan.py 的 Bank 定义）
    acoustic_path = os.path.join(BANK, "0101_qixuan_muon1_acoustic.phonemes.json")
    if not os.path.exists(acoustic_path):
        print("ACOUSTIC-TABLE-MISSING", acoustic_path)
        print("bank dir:", sorted(os.listdir(BANK)))
        sys.exit(1)
    table = load_json(acoustic_path)
    phonemes = set(table.keys()) if isinstance(table, dict) else set(table)
    print("acoustic table:", acoustic_path, "size", len(phonemes))
    print("sample:", sorted(phonemes)[:30])
    print("has SP:", "SP" in phonemes)
    # 前缀形式判定：表里是否含 "zh/" 前缀名
    has_zh_prefix = any(k.startswith("zh/") for k in phonemes)
    has_en_prefix = any(k.startswith("en/") for k in phonemes)
    print("zh/ prefix:", has_zh_prefix, "en/ prefix:", has_en_prefix)
    if not (has_zh_prefix and has_en_prefix):
        print("MAP-BUILD-FAIL: acoustic table does not use lang/ prefix names; inspect before guessing")
        sys.exit(1)

    from pypinyin import lazy_pinyin, Style
    scores = load_json("/content/scores/vocal_scores.json")
    chars = set()
    for t, tr in scores["tracks"].items():
        for n in tr["notes"]:
            for ch in (n.get("lyric") or ""):
                if "\u4e00" <= ch <= "\u9fff":
                    chars.add(ch)
    print("unique zh chars in scores:", len(chars))
    zh_map = {}
    for ch in sorted(chars):
        py = lazy_pinyin(ch, style=Style.NORMAL)[0]
        zh_map[py] = ["zh/" + p for p in split_pinyin(py)]
    print("zh entries:", len(zh_map))
    for k in sorted(zh_map)[:8]:
        print("  zh", k, "->", zh_map[k])
    en_map = {w: ["en/" + p for p in phs if p != "|"] for w, phs in EN_PHONEMES.items()}
    # "|" 是 build_ds.py 的音节切分标记，不是音素（声库表里无 "en/|"，原研究建 map 时同样去掉；
    # sing_qixuan.py 的片段分支只用到首个／末个元音位置，去 "|" 不改变其行为）。
    print("en entries (without '|' separator):", en_map)

    missing = set()
    for src, m in (("zh", zh_map), ("en", en_map)):
        for k, v in m.items():
            for p in v:
                if p not in phonemes:
                    missing.add((src, k, p))
    if missing:
        print("MISSING PHONEMES (not in acoustic table):")
        for m in sorted(missing):
            print("  ", m)
        print("MAP-BUILD-FAIL missing phonemes")
        sys.exit(1)
    json.dump({"zh": zh_map, "en": en_map},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"MAP-OK {OUT} zh={len(zh_map)} en={len(en_map)}")

if __name__ == "__main__":
    main()
