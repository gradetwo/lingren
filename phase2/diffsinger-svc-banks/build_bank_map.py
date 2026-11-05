#!/usr/bin/env python3
"""为任意 OpenUtau DiffSinger 声库建拼音/英文 -> 音素表（sing_bank.py 的 --map）。
依据：声库 acoustic phonemes.json 的实际音素名（先跑 inspect_bank.py 核对命名）。
拼音切分与英文词表与原研究 build_ds.py 完全一致（可比性：同一代码＋同一映射结构）：
  中文：pypinyin Style.NORMAL 无调全拼，按声母表切分（build_ds.py split_pinyin 原样，
        含 ü->v、yu->[y, v...]、y->[y, ...]、w->[w, ...]）。
  英文：winter/is/coming 三词（EN_PHONEMES 原样），片段由 sing_bank.py 的
        startswith/endswith 分支处理，不另加断词条目。
音素名形式：按声库 acoustic 表实测判定——表里有 "zh/" 前缀名则 zh 值加 "zh/" 前缀、
en 值加 "en/" 前缀；表里是裸音素名则不加前缀。绝不猜：每个音素逐项核对在表里，
不在的打印 MISSING 并退出 1。
用法（会话内，system python）：
  python3 build_bank_map.py <声库目录> <vocal_scores.json> <输出 map.json>
"""
import json, os, sys, glob

BANK = sys.argv[1]
SCORES = sys.argv[2]
OUT = sys.argv[3]

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

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
            # （+12 试验 build_qixuan_map.py 同款修正，已在绮萱表上验证 MAP-OK）
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
    import glob
    import yaml
    cfg = {}
    cfg_path = os.path.join(BANK, "dsconfig.yaml")
    if os.path.exists(cfg_path):
        cfg = yaml.safe_load(open(cfg_path)) or {}
    hits = []
    if cfg.get("phonemes"):
        p = os.path.join(BANK, cfg["phonemes"])
        if os.path.exists(p):
            hits = [p]
    if not hits:
        hits = sorted(glob.glob(os.path.join(BANK, "*acoustic*.phonemes.json")))
    if not hits:
        hits = sorted(glob.glob(os.path.join(BANK, "**/*phonemes.json"), recursive=True))
        # 排除子模型目录的表，只留 acoustic 所在目录的表
        hits = [h for h in hits if os.path.basename(os.path.dirname(h)) in
                ("src", os.path.basename(BANK))] or hits
    if not hits:
        print("ACOUSTIC-TABLE-MISSING", BANK, sorted(os.listdir(BANK)))
        sys.exit(1)
    if len(hits) > 1:
        print("ACOUSTIC-TABLE-AMBIGUOUS", hits)
        sys.exit(1)
    acoustic_path = hits[0]
    table = load_json(acoustic_path)
    phonemes = set(table.keys()) if isinstance(table, dict) else set(table)
    print("acoustic table:", acoustic_path, "size", len(phonemes))
    print("sample:", sorted(phonemes)[:30])
    print("has SP:", "SP" in phonemes)
    has_zh_prefix = any(k.startswith("zh/") for k in phonemes)
    has_en_prefix = any(k.startswith("en/") for k in phonemes)
    print("zh/ prefix:", has_zh_prefix, "en/ prefix:", has_en_prefix)
    # 语言表先读：判定 en 是“形式不一致”还是“本库根本无英文”
    lang_hits = []
    if cfg.get("languages"):
        p = os.path.join(BANK, cfg["languages"])
        if os.path.exists(p):
            lang_hits = [p]
    if not lang_hits:
        lang_hits = sorted(glob.glob(os.path.join(BANK, "*.languages.json")))
    if not lang_hits:
        lang_hits = sorted(glob.glob(os.path.join(BANK, "**/*languages.json"), recursive=True))
    lang_keys = []
    if lang_hits:
        langs = load_json(lang_hits[0])
        lang_keys = list(langs.keys()) if isinstance(langs, dict) else list(langs)
        print("languages:", lang_keys)
    else:
        print("注意：未找到 *.languages.json")
    en_absent = (not has_en_prefix) and ("en" not in lang_keys)
    if has_zh_prefix != has_en_prefix and not en_absent:
        print("MAP-BUILD-FAIL: 语言前缀形式不一致（zh 有前缀而 en 没有，或反之），需人工核对")
        sys.exit(1)
    if en_absent:
        print("注意：本库无英文音素（languages 表无 en，音素表无 en/ 前缀）——"
              "英文歌词将不在 map 内，驱动会把它们作 melisma 处理（不发新 token，"
              "音高/时长保留），不伪造歌词")
    if "SP" not in phonemes:
        print("MAP-BUILD-FAIL: acoustic 表无 SP（休止音素），驱动会在休止处查表失败")
        sys.exit(1)

    def fmt(lang, ph):
        use_prefix = has_zh_prefix if lang == "zh" else has_en_prefix
        return f"{lang}/{ph}" if use_prefix else ph

    from pypinyin import lazy_pinyin, Style
    scores = load_json(SCORES)
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
        zh_map[py] = [fmt("zh", p) for p in split_pinyin(py)]
    print("zh entries:", len(zh_map))
    for k in sorted(zh_map)[:8]:
        print("  zh", k, "->", zh_map[k])
    en_map = {} if en_absent else {
        w: [fmt("en", p) for p in phs if p != "|"] for w, phs in EN_PHONEMES.items()}
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
    print(f"MAP-OK {OUT} zh={len(zh_map)} en={len(en_map)} zh_prefixed={has_zh_prefix} en_prefixed={has_en_prefix} en_absent={en_absent}")

if __name__ == "__main__":
    main()
