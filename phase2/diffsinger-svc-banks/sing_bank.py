#!/usr/bin/env python3
"""通用 DiffSinger ONNX 直驱（第一阶段合成，任意 OpenUtau 声库）。

由 sing_qixuan.py 通用化而来：声库文件按角色自动发现（不再写死绮萱文件名），
其余链路与原研究完全一致（dsdur -> 时长按谱面重缩放 -> dspitch -> dsvariance
-> acoustic -> NSF-HiFiGAN 声码器），量测口径与绮萱基线可比。

发现规则（bank_dir 内）：
  acoustic: 顶层 *acoustic*.onnx ＋ *acoustic*.phonemes.json ＋ *.languages.json
  dsdur:    dsdur/ 内 *linguistic*.onnx（语言编码器）＋其余 *.onnx（时长模型）＋ *.phonemes.json
  dspitch:  dspitch/ 同上（缺失时退化为谱面音高直通，无表情）
  dsvariance: dsvariance/ 同上（缺失时气声/发声取 0）
多说话人声库：acoustic 若有 speakers/speaker 输入，用 --speaker-id（默认 0）；
说话人表见声库 dsconfig.yaml / character.yaml（先跑 inspect_bank.py 看）。

用法（Colab，system python，GPU）：
  python3 sing_bank.py --track vox --scores /content/scores/vocal_scores.json \
      --bank /content/voicebanks_banks/Leif \
      --vocoder /content/voicebanks/vocoder/unpacked \
      --map /content/leif_mini_map.json --outdir /content/stage1_banks/leif \
      --bank-name leif [--dur score|pred] [--steps 20] [--depth 0.6] [--expr 1.0]
      [--gender 0.0] [--speaker-id 0] [--probe]
  --probe：只跑第一句，不写 wav。
"""
import argparse
import json
import os
import sys
import time

import numpy as np

SR = 44100
HOP = 512
FRAMES_PER_SEC = SR / HOP  # 86.1328


def midi_to_hz(m):
    return 440.0 * (2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0))


def discover_bank(bank_dir):
    """按角色发现声库文件。优先读 dsconfig.yaml 的 phonemes/languages/acoustic
    路径（权威）；否则按名发现。缺失 dspitch/dsvariance 时对应值为 None
    （调用方退化处理）；缺失 acoustic 或 dsdur 时直接报错（链路跑不起来，不猜）。
    声码器：声库自带 dsvocoder/*.onnx 时优先用自带的，否则用 --vocoder 目录
    下的共享 pc_nsf_hifigan。"""
    import glob
    import yaml

    cfg = {}
    cfg_path = os.path.join(bank_dir, "dsconfig.yaml")
    if os.path.exists(cfg_path):
        cfg = yaml.safe_load(open(cfg_path)) or {}

    def resolve(rel, role):
        if rel:
            p = os.path.join(bank_dir, rel)
            if os.path.exists(p):
                return p
            raise FileNotFoundError(f"{role}: dsconfig 所指 {rel} 不存在")
        return None

    def one(pattern, role, required=True, recursive=False):
        hits = sorted(glob.glob(os.path.join(bank_dir, pattern),
                                recursive=recursive))
        if not hits:
            if required:
                raise FileNotFoundError(f"{role}: {pattern} 在 {bank_dir} 内未找到")
            return None
        if len(hits) > 1:
            raise FileNotFoundError(f"{role}: {pattern} 命中多个 {hits}，需人工指定")
        return hits[0]

    acoustic = resolve(cfg.get("acoustic"), "acoustic") or \
        one("*acoustic*.onnx", "acoustic") or \
        one("**/*acoustic*.onnx", "acoustic", recursive=True)
    a_dir = os.path.dirname(acoustic)
    acoustic_ph = resolve(cfg.get("phonemes"), "acoustic phonemes") or \
        one(os.path.join(os.path.relpath(a_dir, bank_dir), "*phonemes.json"),
            "acoustic phonemes") or \
        one("*acoustic*.phonemes.json", "acoustic phonemes")
    languages = resolve(cfg.get("languages"), "languages") or \
        one(os.path.join(os.path.relpath(a_dir, bank_dir), "*languages.json"),
            "languages") or \
        one("*.languages.json", "languages")

    def submod(sub, role):
        """子模型目录（如 dsdur）：linguistic onnx＋主模型 onnx＋音素表。
        模型可能在子目录的 src/ 下（Leif 布局），一并找。"""
        d = os.path.join(bank_dir, sub)
        if not os.path.isdir(d):
            alt = sorted(glob.glob(os.path.join(bank_dir, f"*{sub}*")))
            d = alt[0] if alt and os.path.isdir(alt[0]) else None
        if d is None:
            return None
        cand_dirs = [d, os.path.join(d, "src")]
        ling, mains, ph = [], [], []
        for cd in cand_dirs:
            if not os.path.isdir(cd):
                continue
            ling += sorted(glob.glob(os.path.join(cd, "*linguistic*.onnx")))
            mains += [p for p in sorted(glob.glob(os.path.join(cd, "*.onnx")))
                      if "linguistic" not in os.path.basename(p)]
            ph += sorted(glob.glob(os.path.join(cd, "*phonemes.json")))
        if not ling or not mains:
            raise FileNotFoundError(f"{role}: {d} 内模型不全 (ling={ling}, main={mains})")
        if len(mains) > 1:
            raise FileNotFoundError(f"{role}: {d} 内主模型多个 {mains}，需人工指定")
        if not ph:
            # 子模型无自带音素表时共用 acoustic 表（同一套音素 id 不一定相同，
            # 调用方 token_ids 会按此表取 id；若 id 对不上，推理会报错而非静默错）
            ph = [acoustic_ph]
        return {"ling": ling[0], "main": mains[0], "phonemes": ph[0]}

    dur = submod("dsdur", "dsdur")
    if dur is None:
        raise FileNotFoundError(f"dsdur: {bank_dir} 内无 dsdur 目录，链路无法跑")
    pitch = submod("dspitch", "dspitch")
    var = submod("dsvariance", "dsvariance")
    if var is None:
        var = submod("dsvar", "dsvariance")
    own_voc = sorted(glob.glob(os.path.join(bank_dir, "dsvocoder", "*.onnx")))
    return {
        "acoustic": acoustic,
        "acoustic_ph": acoustic_ph,
        "languages": languages,
        "dur": dur, "pitch": pitch, "var": var,
        "vocoder": own_voc[0] if own_voc else None,
        "speakers_cfg": cfg.get("speakers"),
    }


class Bank:
    """任意 OpenUtau DiffSinger 声库：ONNX 会话＋音素/语言表。"""

    def __init__(self, bank_dir, vocoder_dir, provider="CUDAExecutionProvider",
                 speaker_id=0, gender=0.0):
        import onnxruntime as ort
        import yaml

        self.dir = bank_dir
        self.speaker_id = speaker_id
        self.gender = gender
        disc = discover_bank(bank_dir)
        print("bank discovered:", json.dumps(
            {k: (v if isinstance(v, str) else v) for k, v in disc.items()},
            ensure_ascii=False, indent=1), flush=True)
        # 各模型音素 id 表不同（同名音素 id 不同），必须分表取 id
        self.phoneme_tables = {"acoustic": json.load(open(disc["acoustic_ph"]))}
        for role, key in (("dur", "dur"), ("pitch", "pitch"), ("var", "var")):
            if disc[key] is not None:
                self.phoneme_tables[role] = json.load(open(disc[key]["phonemes"]))
        self.phonemes = self.phoneme_tables["acoustic"]
        self.languages = json.load(open(disc["languages"]))
        cfg_path = os.path.join(bank_dir, "dsconfig.yaml")
        self.acoustic_cfg = yaml.safe_load(open(cfg_path)) if os.path.exists(cfg_path) else {}
        self.sessions = {}
        self.has_pitch = disc["pitch"] is not None
        self.has_var = disc["var"] is not None
        specs = [("acoustic", disc["acoustic"]),
                 ("dur_ling", disc["dur"]["ling"]), ("dur", disc["dur"]["main"])]
        if self.has_pitch:
            specs += [("pitch_ling", disc["pitch"]["ling"]), ("pitch", disc["pitch"]["main"])]
        if self.has_var:
            specs += [("var_ling", disc["var"]["ling"]), ("var", disc["var"]["main"])]
        for key, path in specs:
            self.sessions[key] = ort.InferenceSession(path, providers=[provider])
        if disc["vocoder"]:
            voc = disc["vocoder"]
            print(f"声码器用声库自带：{voc}", flush=True)
        else:
            voc = os.path.join(vocoder_dir, "pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx")
            print(f"声码器用共享：{voc}", flush=True)
        self.vocoder_path = voc
        self.sessions["vocoder"] = ort.InferenceSession(voc, providers=[provider])
        # 说话人嵌入：dsconfig speakers 列表第 speaker_id 项＋".emb" 后缀。
        # 有 .emb 文件时加载（256 维 float32）；无 .emb 文件的声库（如齑，
        # pitch/var 模型声明了 spk_embed 但无嵌入文件）喂全零向量——OpenUtau
        # 对无嵌入声库的同款处理。acoustic 本身无 speakers 输入时亦然。
        self.spk_vec = None
        spk_list = disc.get("speakers_cfg") or []
        if spk_list and speaker_id < len(spk_list):
            emb_path = os.path.join(bank_dir, str(spk_list[speaker_id]) + ".emb")
            if os.path.exists(emb_path):
                raw = open(emb_path, "rb").read()
                self.spk_vec = np.frombuffer(raw, dtype=np.float32).astype(np.float32)
                print(f"说话人嵌入：{emb_path}（{len(raw)} 字节，{len(self.spk_vec)} 维）",
                      flush=True)
        if self.spk_vec is None:
            print("说话人嵌入：无 .emb 文件，spk_embed 输入喂全零向量", flush=True)
        ac_inputs = {i.name: i for i in self.sessions["acoustic"].get_inputs()}
        self.speaker_input = next(
            (n for n in ("speakers", "speaker", "spk_id", "speaker_id", "spk_embed")
             if n in ac_inputs), None)
        if self.speaker_input:
            meta = ac_inputs[self.speaker_input]
            print(f"acoustic 有说话人输入 {self.speaker_input} "
                  f"shape={meta.shape} type={meta.type}，speaker_id={speaker_id}",
                  flush=True)
        print(f"bank loaded ({len(self.sessions)} sessions, "
              f"pitch={self.has_pitch}, var={self.has_var})", flush=True)

    def token_ids(self, ph_names, table):
        m = self.phoneme_tables[table]
        return np.array([[m[p] for p in ph_names]], dtype=np.int64)

    def lang_ids(self, lang_names):
        return np.array([[self.languages[lg] for lg in lang_names]], dtype=np.int64)

    def spk_embed(self, n):
        """说话人嵌入铺到 (1, n, 256)：有 .emb 用实向量，无则全零。"""
        vec = self.spk_vec if self.spk_vec is not None else np.zeros(256, dtype=np.float32)
        return np.tile(vec.reshape(1, 1, -1), (1, n, 1)).astype(np.float32)

    def speaker_id_feed(self):
        """acoustic 说话人输入为整数 id 形态时返回 (1,) id，否则 None。"""
        if not self.speaker_input:
            return None
        meta = {i.name: i for i in self.sessions["acoustic"].get_inputs()}[self.speaker_input]
        if "int" in meta.type:
            return np.full((1,), self.speaker_id, dtype=np.int64)
        return None


def run_filtered(session, feeds, outputs=None):
    """只喂模型声明的输入；缺失的声明输入直接报错（列出缺了谁）。
    各声库的 ONNX 输入集不同（有无 languages/tension/spk_embed 各异），
    调用方把全部候选键都备齐，由此按声明过滤。"""
    declared = {i.name for i in session.get_inputs()}
    missing = declared - set(feeds)
    if missing:
        raise ValueError(f"模型缺输入 {sorted(missing)}（已给 {sorted(feeds)}）")
    return session.run(outputs, {k: feeds[k] for k in declared})


def phonemize_note(lyric, mini_map):
    """一音的 lyric -> [(音素, 语言)]。中文单字 / 英文音节片段。"""
    from pypinyin import lazy_pinyin, Style

    lyric = (lyric or "").strip()
    if not lyric:
        return []
    frag = lyric.replace("-", "")
    if any("\u4e00" <= c <= "\u9fff" for c in frag):
        out = []
        for ch in frag:
            if "\u4e00" <= ch <= "\u9fff":
                py = lazy_pinyin(ch, style=Style.NORMAL)[0]
                out.extend((p, "zh") for p in mini_map["zh"][py])
        return out
    low = frag.lower()
    vowels = ("ih", "er", "ah", "eh", "ae", "aa", "ao", "ow", "uw", "uh", "ax", "ey", "ay", "oy", "aw")
    for word, phs in mini_map["en"].items():
        if low == word:
            return [(p, "en") for p in phs]
        if word.startswith(low):
            syl = []
            for p in phs:
                syl.append(p)
                if p.split("/")[-1] in vowels:
                    break
            return [(p, "en") for p in syl]
        if word.endswith(low):
            idx = max(i for i, p in enumerate(phs) if p.split("/")[-1] in vowels)
            return [(p, "en") for p in phs[idx:]]
    # 本库无英文音素（en_map 为空）或片段不在表内：作 melisma——不发新 token，
    # 音高/时长保留在原音符上，不伪造歌词。调用方 build_phrase_inputs 据此
    # 把该音并入前一音组。
    print(f"  注意：英文片段 {lyric!r} 不在本库 map 内，作 melisma 处理", flush=True)
    return []


def build_phrase_inputs(notes, mini_map):
    """一段 notes -> 模型输入。

    返回：音素名/语言名序列（各模型按自己的表转 id）、词切分、音符数组、
    token->音符映射、token->音素组映射（melisma 音并入前一音的组）。
    """
    ph_names, lang_names = [], []
    word_div, word_dur = [], []
    note_midi, note_rest, note_dur, note_vel = [], [], [], []
    ph_midi = []
    token_note_idx = []
    token_group = []
    group_score_frames = []
    prev_end = None
    cur_group = -1
    for n in notes:
        start_s, end_s = n["start_s"], n["end_s"]
        if prev_end is not None and start_s - prev_end > 0.05:
            gap_f = max(1, int(round((start_s - prev_end) * FRAMES_PER_SEC)))
            ph_names.append("SP")
            lang_names.append("zh")
            word_div.append(1)
            word_dur.append(gap_f)
            ph_midi.append(0)
            cur_group += 1
            group_score_frames.append(gap_f)
            token_note_idx.append(len(note_midi))
            token_group.append(cur_group)
            note_midi.append(0.0)
            note_rest.append(True)
            note_dur.append(gap_f)
            note_vel.append(0)
        phs = phonemize_note(n.get("lyric"), mini_map)
        dur_f = max(1, int(round(n["duration_s"] * FRAMES_PER_SEC)))
        if not phs:
            # melisma：不发新 token，音符仍占位（音高/时长），组并入前一音
            note_midi.append(float(n["pitch"]))
            note_rest.append(False)
            note_dur.append(dur_f)
            note_vel.append(n.get("velocity", 100))
            if cur_group >= 0:
                group_score_frames[cur_group] += dur_f
        else:
            cur_group += 1
            group_score_frames.append(dur_f)
            for ph, lg in phs:
                ph_names.append(ph)
                lang_names.append(lg)
                ph_midi.append(n["pitch"])
                token_note_idx.append(len(note_midi))
                token_group.append(cur_group)
            word_div.append(len(phs))
            word_dur.append(dur_f)
            note_midi.append(float(n["pitch"]))
            note_rest.append(False)
            note_dur.append(dur_f)
            note_vel.append(n.get("velocity", 100))
        prev_end = end_s
    return {
        "ph_names": ph_names,
        "lang_names": lang_names,
        "word_div": np.array([word_div], dtype=np.int64),
        "word_dur": np.array([word_dur], dtype=np.int64),
        "ph_midi": np.array([ph_midi], dtype=np.int64),
        "note_midi": np.array([note_midi], dtype=np.float32),
        "note_rest": np.array([note_rest], dtype=bool),
        "note_dur": np.array([note_dur], dtype=np.int64),
        "note_vel": np.array([note_vel], dtype=np.int64),
        "token_note_idx": np.array(token_note_idx, dtype=np.int64),
        "token_group": np.array(token_group, dtype=np.int64),
        "group_score_frames": np.array(group_score_frames, dtype=np.int64),
    }


def run_dsdur(bank, inp):
    n_tokens = len(inp["ph_names"])
    enc = run_filtered(
        bank.sessions["dur_ling"],
        {"tokens": bank.token_ids(inp["ph_names"], "dur"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "word_div": inp["word_div"], "word_dur": inp["word_dur"]},
        ["encoder_out", "x_masks"])
    pred = run_filtered(
        bank.sessions["dur"],
        {"encoder_out": enc[0], "x_masks": enc[1], "ph_midi": inp["ph_midi"],
         "spk_embed": bank.spk_embed(n_tokens)},
        ["ph_dur_pred"])[0]
    return pred[0]


def rescale_durations(ph_dur_pred, inp, mode):
    """预测时长（帧）-> 实际每 token 帧数。score：按组缩放到谱面时长；pred：原样。"""
    pred = np.maximum(1.0, np.asarray(ph_dur_pred, dtype=np.float64))
    if mode == "pred":
        return np.maximum(1, np.round(pred)).astype(np.int64)
    out = np.zeros_like(pred, dtype=np.int64)
    for g in np.unique(inp["token_group"]):
        idx = np.where(inp["token_group"] == g)[0]
        target = int(inp["group_score_frames"][g])
        w = pred[idx]
        scaled = np.maximum(1, np.round(w / w.sum() * target)).astype(np.int64)
        # 修正舍入误差：把差额补到组内最长的 token 上
        diff = target - int(scaled.sum())
        if diff != 0:
            scaled[np.argmax(scaled)] += diff
            scaled = np.maximum(1, scaled)
        out[idx] = scaled
    return out


def resolve_note_dur(inp, ph_dur_frames, mode):
    """供 pitch 模型与帧->音符映射用的每音帧数。要求和 == sum(ph_dur_frames)。
    score 模式：直接用谱面 note_dur（组重缩放保证总和一致）。
    pred 模式：bincount 求和；melisma 音按谱面占比从前一音分帧（保住音高阶跃）。"""
    n_notes = len(inp["note_midi"][0])
    if mode == "score":
        return np.maximum(1, inp["note_dur"][0].astype(np.int64))
    nd = np.bincount(inp["token_note_idx"], weights=ph_dur_frames,
                     minlength=n_notes).astype(np.int64)
    # melisma 音（bincount 为 0）：按谱面占比从前一音分帧
    for i in range(n_notes):
        if nd[i] == 0 and i > 0:
            grp = inp["token_group"][inp["token_note_idx"] == i - 1]
            # 前一音的组总分帧与本音谱面占比
            share = inp["note_dur"][0][i] / max(1, inp["note_dur"][0][i - 1] + inp["note_dur"][0][i])
            take = max(1, int(round(nd[i - 1] * share)))
            take = min(take, nd[i - 1] - 1) if nd[i - 1] > 1 else 0
            nd[i] = take
            nd[i - 1] -= take
    return np.maximum(0, nd)


def run_dspitch(bank, inp, ph_dur_frames, expr=1.0, steps=20, mode="score"):
    enc = run_filtered(
        bank.sessions["pitch_ling"],
        {"tokens": bank.token_ids(inp["ph_names"], "pitch"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "word_div": inp["word_div"], "word_dur": inp["word_dur"]},
        ["encoder_out", "x_masks"])
    n_frames = int(ph_dur_frames.sum())
    note_dur_pred = resolve_note_dur(inp, ph_dur_frames, mode)
    pitch_init = np.zeros(n_frames, dtype=np.float32)
    pos = 0
    for m, d in zip(inp["note_midi"][0], note_dur_pred):
        pitch_init[pos:pos + int(d)] = m
        pos += int(d)
    pitch_init = pitch_init[:n_frames]
    out = run_filtered(
        bank.sessions["pitch"],
        {"encoder_out": enc[0],
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "note_midi": inp["note_midi"],
         "note_rest": inp["note_rest"],
         "note_dur": note_dur_pred[None, :],
         "pitch": pitch_init[None, :].astype(np.float32),
         "expr": np.full((1, n_frames), expr, dtype=np.float32),
         "retake": np.ones((1, n_frames), dtype=bool),
         "spk_embed": bank.spk_embed(n_frames),
         "steps": np.array(steps, dtype=np.int64)},
        ["pitch_pred"])[0]
    return out[0], pitch_init, note_dur_pred


def run_dsvariance(bank, inp, ph_dur_frames, pitch_midi, steps=20):
    """返回 (breathiness, voicing, tension)。无 tension 输出的旧模型只返回前两项，
    第三项为全零（调用方照常喂 acoustic，由过滤决定是否采用）。"""
    enc = run_filtered(
        bank.sessions["var_ling"],
        {"tokens": bank.token_ids(inp["ph_names"], "var"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "word_div": inp["word_div"], "word_dur": inp["word_dur"]},
        ["encoder_out", "x_masks"])
    n_frames = int(ph_dur_frames.sum())
    # variance 模型的 retake 末维因库而异：绮萱 (1, n_frames, 2)，琅/齑
    # (1, n_frames, 3)。按模型声明的形状造，不硬编码。
    rk = None
    for inp_meta in bank.sessions["var"].get_inputs():
        if inp_meta.name == "retake" and len(inp_meta.shape) == 3:
            rk = int(inp_meta.shape[2])
    retake = (np.ones((1, n_frames, rk), dtype=bool) if rk
              else np.ones((1, n_frames), dtype=bool))
    outs = run_filtered(
        bank.sessions["var"],
        {"encoder_out": enc[0],
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "pitch": pitch_midi[None, :].astype(np.float32),
         "breathiness": np.zeros((1, n_frames), dtype=np.float32),
         "voicing": np.zeros((1, n_frames), dtype=np.float32),
         "tension": np.zeros((1, n_frames), dtype=np.float32),
         "retake": retake,
         "spk_embed": bank.spk_embed(n_frames),
         "steps": np.array(steps, dtype=np.int64)})
    b, v = outs[0][0], outs[1][0]
    t = outs[2][0] if len(outs) > 2 else np.zeros(n_frames, dtype=np.float32)
    return b, v, t


def run_acoustic(bank, inp, ph_dur_frames, f0_hz, breathiness, voicing, tension, velocity, depth=0.6, steps=20, gender=0.0):
    n_frames = len(f0_hz)
    feeds = {"tokens": bank.token_ids(inp["ph_names"], "acoustic"),
             "languages": bank.lang_ids(inp["lang_names"]),
             "durations": ph_dur_frames.astype(np.int64)[None, :],
             "f0": f0_hz[None, :].astype(np.float32),
             "breathiness": breathiness[None, :].astype(np.float32),
             "voicing": voicing[None, :].astype(np.float32),
             "tension": tension[None, :].astype(np.float32),
             "gender": np.full((1, n_frames), gender, dtype=np.float32),
             "velocity": velocity[None, :].astype(np.float32),
             "spk_embed": bank.spk_embed(n_frames),
             "depth": np.array(depth, dtype=np.float32),
             "steps": np.array(steps, dtype=np.int64)}
    if bank.speaker_input:
        # 整数 id 形态的说话人输入才需单独喂 id；float 嵌入形态已由上方的
        # spk_embed 通用键覆盖——speaker_id_feed() 为 None 时绝不能覆盖它
        # （曾因此把 spk_embed 写成 None，ORT 报 Missing Input）。
        sid = bank.speaker_id_feed()
        if sid is not None:
            feeds[bank.speaker_input] = sid
    mel = run_filtered(bank.sessions["acoustic"], feeds, ["mel"])[0]
    return mel[0]


def run_vocoder(bank, mel, f0_hz):
    wav = run_filtered(
        bank.sessions["vocoder"],
        {"mel": mel[None, :, :].astype(np.float32),
         "f0": f0_hz[None, :].astype(np.float32)},
        ["waveform"])[0]
    return wav[0]


def frame_note_id(inp, ph_dur_frames, mode="score"):
    """每帧属于哪个 note 位（用于 velocity/休止处理）。"""
    note_dur_pred = resolve_note_dur(inp, ph_dur_frames, mode)
    ids = np.zeros(int(ph_dur_frames.sum()), dtype=np.int64)
    pos = 0
    for i, d in enumerate(note_dur_pred):
        ids[pos:pos + int(d)] = i
        pos += int(d)
    return ids[:int(ph_dur_frames.sum())], note_dur_pred


def split_phrases(notes, rest_split_s=0.6):
    """按 lyric_line_index 分句；句内大休止再断句。"""
    segs, cur, cur_line = [], [], None
    for n in notes:
        li = n.get("lyric_line_index")
        if cur and li is not None and li != cur_line:
            segs.append(cur)
            cur = []
        cur.append(n)
        if li is not None:
            cur_line = li
    if cur:
        segs.append(cur)
    out = []
    for seg in segs:
        part = [seg[0]]
        for prev, n in zip(seg, seg[1:]):
            if n["start_s"] - prev["end_s"] > rest_split_s:
                out.append(part)
                part = [n]
            else:
                part.append(n)
        out.append(part)
    return out


def render_phrase(bank, notes, mini_map, args):
    """渲染一段，返回 (wav_44k, sidecar_dict)。"""
    t0 = time.time()
    inp = build_phrase_inputs(notes, mini_map)
    ph_dur_pred = run_dsdur(bank, inp)
    ph_dur_frames = rescale_durations(ph_dur_pred, inp, args.dur)
    dur_ratio = float(ph_dur_pred.sum() / max(1, inp["group_score_frames"].sum()))
    if bank.has_pitch:
        pitch_pred, pitch_init, _ = run_dspitch(bank, inp, ph_dur_frames, expr=args.expr, steps=args.steps, mode=args.dur)
    else:
        # 无 dspitch：音高直通谱面（无表情）；pitch_init 由 run_dspitch 的
        # 同款逻辑在 resolve_note_dur 上重建，见下。
        note_dur_pred = resolve_note_dur(inp, ph_dur_frames, args.dur)
        n_frames = int(ph_dur_frames.sum())
        pitch_init = np.zeros(n_frames, dtype=np.float32)
        pos = 0
        for m, d in zip(inp["note_midi"][0], note_dur_pred):
            pitch_init[pos:pos + int(d)] = m
            pos += int(d)
        pitch_init = pitch_init[:n_frames]
        pitch_pred = pitch_init.copy()
        print("  注意：本库无 dspitch，音高直通谱面", flush=True)
    # 休止帧 f0 插值填平
    fids, _ = frame_note_id(inp, ph_dur_frames, mode=args.dur)
    rest_mask = inp["note_rest"][0][fids]
    pitch_filled = pitch_pred.copy()
    if rest_mask.any():
        good = ~rest_mask
        idx = np.arange(len(pitch_pred))
        pitch_filled[rest_mask] = np.interp(idx[rest_mask], idx[good], pitch_pred[good])
    f0_hz = midi_to_hz(np.clip(pitch_filled, 20, 110))
    if bank.has_var:
        breath, voice, tension = run_dsvariance(bank, inp, ph_dur_frames, pitch_filled, steps=args.steps)
    else:
        n_frames = int(ph_dur_frames.sum())
        breath = np.zeros(n_frames, dtype=np.float32)
        voice = np.zeros(n_frames, dtype=np.float32)
        tension = np.zeros(n_frames, dtype=np.float32)
        print("  注意：本库无 dsvariance，气声/发声/张力取 0", flush=True)
    velocity = inp["note_vel"][0][fids] / 127.0
    mel = run_acoustic(bank, inp, ph_dur_frames, f0_hz, breath, voice, tension, velocity,
                       depth=args.depth, steps=args.steps, gender=args.gender)
    wav = run_vocoder(bank, mel, f0_hz)
    dt = time.time() - t0
    # 音高偏差：每音取其中部帧的 pitch_pred 中位，与谱面音高比（半音）
    devs = []
    pos = 0
    note_dur_pred = resolve_note_dur(inp, ph_dur_frames, args.dur)
    for i, (m, d) in enumerate(zip(inp["note_midi"][0], note_dur_pred)):
        seg = pitch_pred[pos:pos + int(d)]
        if len(seg) and not inp["note_rest"][0][i] and m > 0:
            mid = seg[len(seg) // 4: 3 * len(seg) // 4]
            if len(mid) == 0:
                mid = seg
            devs.append(float(np.median(mid) - m))
        pos += int(d)
    side = {
        "n_notes": len(notes),
        "n_tokens": len(inp["ph_names"]),
        "n_frames": int(ph_dur_frames.sum()),
        "dur_ratio_pred_over_score": round(dur_ratio, 3),
        "pitch_dev_median_semitones": round(float(np.median(devs)), 3) if devs else None,
        "pitch_dev_p90_abs_semitones": round(float(np.percentile(np.abs(devs), 90)), 3) if devs else None,
        "pitch_pred_range_midi": [round(float(pitch_pred.min()), 2), round(float(pitch_pred.max()), 2)],
        "breathiness_range": [round(float(breath.min()), 3), round(float(breath.max()), 3)],
        "voicing_range": [round(float(voice.min()), 3), round(float(voice.max()), 3)],
        "tension_range": [round(float(tension.min()), 3), round(float(tension.max()), 3)],
        "render_s": round(dt, 1),
    }
    return wav, side


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--vocoder", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dur", default="score", choices=["score", "pred"])
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--depth", type=float, default=0.6)
    ap.add_argument("--expr", type=float, default=1.0)
    ap.add_argument("--bank-name", default="bank",
                    help="输出文件名后缀，如 leif / lang（默认 bank）")
    ap.add_argument("--gender", type=float, default=0.0,
                    help="声学模型 gender 输入（默认 0.0，声库自然音色）")
    ap.add_argument("--speaker-id", type=int, default=0,
                    help="多说话人声库的说话人 id（默认 0）")
    ap.add_argument("--provider", default="CUDAExecutionProvider")
    ap.add_argument("--start-s", type=float, default=None,
                    help="只渲染 start_s >= 该值的音（烟囱切片用）")
    ap.add_argument("--end-s", type=float, default=None,
                    help="只渲染 end_s <= 该值的音（烟囱切片用）")
    args = ap.parse_args()

    data = json.load(open(args.scores))
    mini_map = json.load(open(args.map))
    bank = Bank(args.bank, args.vocoder, provider=args.provider,
                speaker_id=args.speaker_id, gender=args.gender)
    track = data["tracks"][args.track]
    notes = sorted(track["notes"], key=lambda n: n["start_tick"])
    if args.start_s is not None or args.end_s is not None:
        before = len(notes)
        notes = [n for n in notes
                 if (args.start_s is None or n["start_s"] >= args.start_s)
                 and (args.end_s is None or n["end_s"] <= args.end_s)]
        print(f"SLICE {args.start_s}–{args.end_s}s：{before} 音 -> {len(notes)} 音",
              flush=True)
    if args.probe:
        notes = notes[:4]
        print(f"PROBE: {args.track} 前 4 音：",
              [(n.get("lyric"), n["pitch_name"], round(n["duration_s"], 3)) for n in notes], flush=True)
        wav, side = render_phrase(bank, notes, mini_map, args)
        print("PROBE sidecar:", json.dumps(side, ensure_ascii=False), flush=True)
        print(f"PROBE wav: {len(wav)} samples = {len(wav)/SR:.2f}s, peak={np.abs(wav).max():.4f}", flush=True)
        return

    phrases = split_phrases(notes)
    print(f"{args.track}: {len(notes)} 音 -> {len(phrases)} 句", flush=True)
    total_s = float(track["clip_duration_s"])
    # 切片模式：输出缓冲只覆盖切片窗口（不按整轨长度），t=0 对应切片起点 -0.05s；
    # sidecar 的 start_s 仍记绝对谱面时间，量测时 --t0 传切片起点 -0.05。
    slice_mode = args.start_s is not None or args.end_s is not None
    if slice_mode:
        t_base = (args.start_s if args.start_s is not None else notes[0]["start_s"]) - 0.05
        end_s = args.end_s if args.end_s is not None else notes[-1]["end_s"]
        total_s = end_s - t_base
    else:
        t_base = 0.0
    out = np.zeros(int(np.ceil(total_s * SR)) + SR, dtype=np.float32)
    sides = []
    for i, ph in enumerate(phrases):
        t0 = time.time()
        wav, side = render_phrase(bank, ph, mini_map, args)
        start_s = max(0.0, ph[0]["start_s"] - 0.05)
        start = int(round((start_s - t_base) * SR))
        end = min(len(out), start + len(wav))
        out[start:end] += wav[:end - start]
        side["phrase"] = i
        side["start_s"] = round(start_s, 3)
        side["lyric"] = "".join((n.get("lyric") or "") for n in ph[:12])
        sides.append(side)
        print(f"  句 {i+1}/{len(phrases)}：{len(ph)} 音，{len(wav)/SR:.1f}s，"
              f"耗时 {time.time()-t0:.0f}s，音高偏差中位 {side['pitch_dev_median_semitones']} 半音",
              flush=True)
    # 裁到 clip 长度＋尾音 0.5s；归一到峰值 0.89（与 V4 干声同一口径，便于 SVC 输入）
    cut = int(np.ceil((total_s + 0.5) * SR))
    out = out[:cut]
    raw_peak = float(np.abs(out).max())
    if raw_peak > 0:
        out = out / raw_peak * 0.89
    os.makedirs(args.outdir, exist_ok=True)
    import soundfile as sf
    wav_path = os.path.join(args.outdir, f"{args.track}_stage1_{args.bank_name}.wav")
    sf.write(wav_path, out, SR, subtype="PCM_16")
    side_path = os.path.join(args.outdir, f"{args.track}_stage1_{args.bank_name}.json")
    json.dump({"track": args.track, "bank": args.bank, "bank_name": args.bank_name,
               "sr": SR, "dur_policy": args.dur,
               "steps": args.steps, "depth": args.depth, "expr": args.expr,
               "gender": args.gender, "speaker_id": args.speaker_id,
               "has_pitch_model": bank.has_pitch, "has_variance_model": bank.has_var,
               "slice_s": [args.start_s, args.end_s],
               "t0_s": round(t_base, 3),
               "duration_s": round(len(out) / SR, 3),
               "peak_raw": round(raw_peak, 4),
               "peak_normalized": 0.89,
               "phrases": sides}, open(side_path, "w"), ensure_ascii=False, indent=1)
    print(f"写 {wav_path}（{len(out)/SR:.2f}s，原始峰值 {raw_peak:.4f} -> 归一 0.89）", flush=True)
    print(f"写 {side_path}", flush=True)


if __name__ == "__main__":
    main()
