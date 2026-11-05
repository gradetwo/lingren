#!/usr/bin/env python3
"""V4 谱子 -> DiffSinger .ds 构建器（两阶段路线 · 第一阶段输入）.

输入: ~/workspace/song-lindong-metal-revise/work/dry-vocal-package/lyrics/vocal_scores.json
      （5 轨，685 音，669 音带歌词；PPQ 960，150 BPM，1 秒 = 2400 tick）
输出: 每轨一个 .ds（JSON list of segments），字段与 openvpi/DiffSinger samples/*.ds 一致：
      offset / text / ph_seq / ph_dur / ph_num / note_seq / note_dur / note_slur
      （f0_seq / f0_timestep 由 variance 模型预测或由本脚本显式填）

音素化：
- 中文单字 -> pypinyin（无调拼音，拆声母+韵母，如 凛 -> "l in"，将 -> "j iang"）
- 英文词 -> 绮萱英语音素表（见 EN_PHONEMES，待声库 phonemes.txt 核对后填）
- 无歌词音（melisma/延音）-> 不发新音素，note_slur=1，沿用前一音的元音
- 句间休止（>0.6s）-> 断句（新 segment）；句内休止 -> note_seq 填 "rest"

用法:
    python3 build_ds.py --track vox --scores <vocal_scores.json> --out vox.ds
    python3 build_ds.py --all --scores <vocal_scores.json> --outdir .
"""
import argparse
import json
import re

# MIDI 号 -> DiffSinger note 名（C4 = 60）
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note(midi):
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


# --- 中文：拼音（无调）拆声母+韵母 ---------------------------------------
# pypinyin 的 lazy_pinyin(..., style=Style.NORMAL) 给无调全拼；再按声母表切分。
INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
            "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w"]


def split_pinyin(py):
    """'tian' -> ['t', 'ian']; 'zhi' -> ['zh', 'i']; 'yu' -> ['y', 'v']."""
    for init in INITIALS:
        if py.startswith(init) and len(py) > len(init):
            final = py[len(init):]
            # ü 写作 v（DiffSinger 汉语习惯，见 sample .ds 的 "y v"）
            if final.startswith("ü"):
                final = "v" + final[1:]
            return [init, final]
    # 零声母：a/o/e/ai/an/ang/ao/ou/en/eng/er/yi/wu/yu...
    if py.startswith("yu"):
        return ["y", "v" + py[2:]]
    if py.startswith("y"):
        return ["y", py[1:] or "i"]
    if py.startswith("w"):
        return ["w", py[1:] or "u"]
    return [py]


# --- 英文：绮萱英语音素（待声库 phonemes.txt 核对；先按常见 DiffSinger 英语集占位）
# 歌词里只出现 4 个英文词：Winter / Is / Coming（另有 Win- / Com-ing 分音节写法）
EN_PHONEMES = {
    # 词 -> 音素串（按音节切分点用 | 标，后续按 note 的 lyric 分段取）
    "winter": ["w", "ih", "n", "|", "t", "er"],
    "is": ["ih", "z"],
    "coming": ["k", "ah", "m", "|", "ih", "ng"],
}


def phonemize_lyric(lyric):
    """一音的 lyric -> 音素 list。中文单字 / 英文音节片段 / 标注跳过。"""
    lyric = lyric.strip()
    if not lyric or lyric in ("［群吼］", "（耳语）"):
        return []
    # 去连字符：lyric 形如 "Win-" / "Com-ing" / "ter"（音节片段）
    frag = lyric.replace("-", "")
    if re.search(r"[\u4e00-\u9fff]", frag):
        # 中文：逐字
        from pypinyin import lazy_pinyin, Style
        out = []
        for ch in frag:
            if "\u4e00" <= ch <= "\u9fff":
                py = lazy_pinyin(ch, style=Style.NORMAL)[0]
                out.extend(split_pinyin(py))
        return out
    # 英文片段：按整词查表；"|" 为音节切分点
    low = frag.lower()
    for word, phs in EN_PHONEMES.items():
        clean = [p for p in phs if p != "|"]
        if low == word:
            return clean
        if word.startswith(low):
            # 前缀 = 首音节（"|" 之前）
            return phs[: phs.index("|")] if "|" in phs else clean
        if word.endswith(low):
            # 后缀 = 尾音节（"|" 之后）
            return phs[phs.index("|") + 1 :] if "|" in phs else clean
    return [low]  # 未知词：原样留待人工核对（会在校验步报错）


def build_segments(notes, rest_split_s=0.6):
    """notes（已按 start_tick 排）-> segments（每段一段歌词句）。"""
    # 按 lyric_line_index 分组；无 lyric_line 的音并入当前段
    segments = []
    cur = []
    cur_line = None
    for n in notes:
        li = n.get("lyric_line_index")
        if cur and li is not None and li != cur_line:
            segments.append(cur)
            cur = []
        cur.append(n)
        if li is not None:
            cur_line = li
    if cur:
        segments.append(cur)
    # 句内大休止再断句
    split = []
    for seg in segments:
        part = [seg[0]]
        for prev, n in zip(seg, seg[1:]):
            gap = n["start_s"] - prev["end_s"]
            if gap > rest_split_s:
                split.append(part)
                part = [n]
            else:
                part.append(n)
        split.append(part)
    return split


def segment_to_ds(seg, offset_pad=0.05):
    """一段 notes -> 一个 .ds segment dict。"""
    offset = max(0.0, seg[0]["start_s"] - offset_pad)
    text_parts, ph_seq, ph_num = [], [], []
    note_seq, note_dur, note_slur = [], [], []
    prev_end = offset
    for n in seg:
        gap = n["start_s"] - prev_end
        if gap > 0.05:
            # 句内休止：插 rest 音
            note_seq.append("rest")
            note_dur.append(round(gap, 4))
            note_slur.append(0)
            ph_seq.append("SP")
            ph_num.append(1)
            text_parts.append("SP")
        lyric = n.get("lyric") or ""
        phs = phonemize_lyric(lyric)
        if not phs:
            # 无歌词音：melisma，沿用前一音元音
            note_seq.append(midi_to_note(n["pitch"]))
            note_dur.append(round(n["duration_s"], 4))
            note_slur.append(1)
            ph_num.append(0)
            text_parts.append("—")
        else:
            note_seq.append(midi_to_note(n["pitch"]))
            note_dur.append(round(n["duration_s"], 4))
            note_slur.append(0)
            ph_seq.extend(phs)
            ph_num.append(len(phs))
            text_parts.append(lyric)
        prev_end = n["end_s"]
    # ph_dur：先按音长在音素间均分（variance 模型会重预测；显式版亦可用）
    ph_dur = []
    for i, cnt in enumerate(ph_num):
        if cnt == 0:
            continue
        dur = note_dur[i] if i < len(note_dur) else 0.2
        ph_dur.extend([round(dur / cnt, 4)] * cnt)
    return {
        "offset": round(offset, 4),
        "text": " ".join(text_parts),
        "ph_seq": " ".join(ph_seq),
        "ph_dur": " ".join(f"{d:.4f}" for d in ph_dur),
        "ph_num": " ".join(str(c) for c in ph_num),
        "note_seq": " ".join(note_seq),
        "note_dur": " ".join(f"{d:.4f}" for d in note_dur),
        "note_slur": " ".join(str(s) for s in note_slur),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True)
    ap.add_argument("--track", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()
    data = json.load(open(args.scores))
    tracks = data["tracks"]
    names = list(tracks) if args.all else [args.track]
    for name in names:
        t = tracks[name]
        notes = sorted(t["notes"], key=lambda n: n["start_tick"])
        segs = build_segments(notes)
        ds = [segment_to_ds(s) for s in segs]
        out = f"{args.outdir}/{name}.ds" if args.all else (args.out or f"{name}.ds")
        json.dump(ds, open(out, "w"), ensure_ascii=False, indent=1)
        n_ph = sum(len(s["ph_seq"].split()) for s in ds)
        print(f"{name}: {len(notes)} 音 -> {len(ds)} 段，{n_ph} 音素，写 {out}")


if __name__ == "__main__":
    main()
