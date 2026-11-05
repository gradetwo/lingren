#!/usr/bin/env python3
"""绮萱 Qixuan ONNX 直驱（第一阶段 DiffSinger 合成）。

链路（实测 ONNX I/O，见 REPORT-diffsinger-svc.md）：
  tokens/languages --dsdur-ling--> encoder_out --dsdur--> ph_dur_pred（每音素时长，单位=帧）
  tokens/languages/ph_dur --dspitch-ling--> encoder_out --dspitch--> pitch_pred（MIDI/帧）
  tokens/languages/ph_dur --dsvariance-ling--> encoder_out --dsvariance--> breathiness/voicing
  tokens/languages/durations/f0/breathiness/voicing/gender/velocity/depth/steps --acoustic--> mel
  mel/f0 --pc-nsf-hifigan--> wav（44.1kHz）

关键语义（实测）：
- 各模型音素 id 表不同（同名音素 id 不同），必须分表取 id。
- dsdur 输出单位是帧；自然时长比谱面长约 1.5 倍（童声声库唱得慢），
  故默认 --dur score：按音素组把预测时长重缩放到谱面时长（保留声母/韵母比例）。
- melisma（无歌词音）：不发新 token，沿用前一音的元音（note_slur=1 等价处理）。
- 休止帧的 f0 用邻近音高插值填平，避免阶跃。

输入：vocal_scores.json（V4 谱子）＋ qixuan_mini_map.json（拼音/英文 -> 音素表）
输出：<轨名>_stage1_diffsinger.wav（44.1kHz 16-bit）＋ sidecar JSON（量测）

用法（Colab，system python，GPU）：
  python3 sing_qixuan.py --track voxTel --scores /content/scores/.../vocal_scores.json \
      --bank /content/voicebanks/Qixuan_v2.7.0_DiffSinger_OpenUtau \
      --vocoder /content/voicebanks/vocoder/unpacked \
      --map /content/qixuan_mini_map.json --outdir /content/stage1 \
      [--dur score|pred] [--steps 20] [--depth 0.6] [--expr 1.0] [--probe]
  --probe：只跑第一句（voxTel 的 凛冬将至），打印 dsdur/dspitch 量值，不写 wav。
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


class Bank:
    """绮萱声库：ONNX 会话＋音素/语言表。"""

    def __init__(self, bank_dir, vocoder_dir, provider="CUDAExecutionProvider"):
        import onnxruntime as ort
        import yaml

        self.dir = bank_dir
        # 各模型有自己的音素 id 表（同名音素，id 不同！）——必须分表取 id
        self.phoneme_tables = {
            "acoustic": json.load(open(os.path.join(bank_dir, "0101_qixuan_muon1_acoustic.phonemes.json"))),
            "dur": json.load(open(os.path.join(bank_dir, "dsdur/0102_qixuan_newdict_dur.phonemes.json"))),
            "pitch": json.load(open(os.path.join(bank_dir, "dspitch/0102_qixuan_muon1_pitch.phonemes.json"))),
            "var": json.load(open(os.path.join(bank_dir, "dsvariance/0102_qixuan_muon1_multivar.phonemes.json"))),
        }
        self.phonemes = self.phoneme_tables["acoustic"]
        self.languages = json.load(open(os.path.join(bank_dir, "0101_qixuan_muon1_acoustic.languages.json")))
        self.acoustic_cfg = yaml.safe_load(open(os.path.join(bank_dir, "dsconfig.yaml")))
        self.sessions = {}
        for key, rel in [
            ("acoustic", "0101_qixuan_muon1_acoustic.qixuan.onnx"),
            ("dur_ling", "dsdur/0102_qixuan_newdict_dur.qixuan.linguistic.onnx"),
            ("dur", "dsdur/0102_qixuan_newdict_dur.qixuan.dur.onnx"),
            ("pitch_ling", "dspitch/0102_qixuan_muon1_pitch.qixuan.linguistic.onnx"),
            ("pitch", "dspitch/0102_qixuan_muon1_pitch.qixuan.pitch.onnx"),
            ("var_ling", "dsvariance/0102_qixuan_muon1_multivar.qixuan.linguistic.onnx"),
            ("var", "dsvariance/0102_qixuan_muon1_multivar.qixuan.variance.onnx"),
        ]:
            path = os.path.join(bank_dir, rel)
            self.sessions[key] = ort.InferenceSession(path, providers=[provider])
        voc = os.path.join(vocoder_dir, "pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx")
        self.sessions["vocoder"] = ort.InferenceSession(voc, providers=[provider])
        print("bank loaded (8 sessions)", flush=True)

    def token_ids(self, ph_names, table):
        m = self.phoneme_tables[table]
        return np.array([[m[p] for p in ph_names]], dtype=np.int64)

    def lang_ids(self, lang_names):
        return np.array([[self.languages[lg] for lg in lang_names]], dtype=np.int64)


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
                if p.split("/")[1] in vowels:
                    break
            return [(p, "en") for p in syl]
        if word.endswith(low):
            idx = max(i for i, p in enumerate(phs) if p.split("/")[1] in vowels)
            return [(p, "en") for p in phs[idx:]]
    raise ValueError(f"未知英文片段：{lyric!r}")


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
    enc = bank.sessions["dur_ling"].run(
        ["encoder_out", "x_masks"],
        {"tokens": bank.token_ids(inp["ph_names"], "dur"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "word_div": inp["word_div"], "word_dur": inp["word_dur"]},
    )
    pred = bank.sessions["dur"].run(
        ["ph_dur_pred"],
        {"encoder_out": enc[0], "x_masks": enc[1], "ph_midi": inp["ph_midi"]},
    )[0]
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
    enc = bank.sessions["pitch_ling"].run(
        ["encoder_out", "x_masks"],
        {"tokens": bank.token_ids(inp["ph_names"], "pitch"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :]},
    )
    n_frames = int(ph_dur_frames.sum())
    note_dur_pred = resolve_note_dur(inp, ph_dur_frames, mode)
    pitch_init = np.zeros(n_frames, dtype=np.float32)
    pos = 0
    for m, d in zip(inp["note_midi"][0], note_dur_pred):
        pitch_init[pos:pos + int(d)] = m
        pos += int(d)
    pitch_init = pitch_init[:n_frames]
    out = bank.sessions["pitch"].run(
        ["pitch_pred"],
        {"encoder_out": enc[0],
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "note_midi": inp["note_midi"],
         "note_rest": inp["note_rest"],
         "note_dur": note_dur_pred[None, :],
         "pitch": pitch_init[None, :].astype(np.float32),
         "expr": np.full((1, n_frames), expr, dtype=np.float32),
         "retake": np.ones((1, n_frames), dtype=bool),
         "steps": np.array(steps, dtype=np.int64)},
    )[0]
    return out[0], pitch_init, note_dur_pred


def run_dsvariance(bank, inp, ph_dur_frames, pitch_midi, steps=20):
    enc = bank.sessions["var_ling"].run(
        ["encoder_out", "x_masks"],
        {"tokens": bank.token_ids(inp["ph_names"], "var"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :]},
    )
    n_frames = int(ph_dur_frames.sum())
    b, v = bank.sessions["var"].run(
        ["breathiness_pred", "voicing_pred"],
        {"encoder_out": enc[0],
         "ph_dur": ph_dur_frames.astype(np.int64)[None, :],
         "pitch": pitch_midi[None, :].astype(np.float32),
         "breathiness": np.zeros((1, n_frames), dtype=np.float32),
         "voicing": np.zeros((1, n_frames), dtype=np.float32),
         "retake": np.ones((1, n_frames, 2), dtype=bool),
         "steps": np.array(steps, dtype=np.int64)},
    )
    return b[0], v[0]


def run_acoustic(bank, inp, ph_dur_frames, f0_hz, breathiness, voicing, velocity, depth=0.6, steps=20):
    mel = bank.sessions["acoustic"].run(
        ["mel"],
        {"tokens": bank.token_ids(inp["ph_names"], "acoustic"),
         "languages": bank.lang_ids(inp["lang_names"]),
         "durations": ph_dur_frames.astype(np.int64)[None, :],
         "f0": f0_hz[None, :].astype(np.float32),
         "breathiness": breathiness[None, :].astype(np.float32),
         "voicing": voicing[None, :].astype(np.float32),
         "gender": np.zeros((1, len(f0_hz)), dtype=np.float32),
         "velocity": velocity[None, :].astype(np.float32),
         "depth": np.array(depth, dtype=np.float32),
         "steps": np.array(steps, dtype=np.int64)},
    )[0]
    return mel[0]


def run_vocoder(bank, mel, f0_hz):
    wav = bank.sessions["vocoder"].run(
        ["waveform"],
        {"mel": mel[None, :, :].astype(np.float32),
         "f0": f0_hz[None, :].astype(np.float32)},
    )[0]
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
    pitch_pred, pitch_init, _ = run_dspitch(bank, inp, ph_dur_frames, expr=args.expr, steps=args.steps, mode=args.dur)
    # 休止帧 f0 插值填平
    fids, _ = frame_note_id(inp, ph_dur_frames, mode=args.dur)
    rest_mask = inp["note_rest"][0][fids]
    pitch_filled = pitch_pred.copy()
    if rest_mask.any():
        good = ~rest_mask
        idx = np.arange(len(pitch_pred))
        pitch_filled[rest_mask] = np.interp(idx[rest_mask], idx[good], pitch_pred[good])
    f0_hz = midi_to_hz(np.clip(pitch_filled, 20, 110))
    breath, voice = run_dsvariance(bank, inp, ph_dur_frames, pitch_filled, steps=args.steps)
    velocity = inp["note_vel"][0][fids] / 127.0
    mel = run_acoustic(bank, inp, ph_dur_frames, f0_hz, breath, voice, velocity,
                       depth=args.depth, steps=args.steps)
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
    ap.add_argument("--provider", default="CUDAExecutionProvider")
    args = ap.parse_args()

    data = json.load(open(args.scores))
    mini_map = json.load(open(args.map))
    bank = Bank(args.bank, args.vocoder, provider=args.provider)
    track = data["tracks"][args.track]
    notes = sorted(track["notes"], key=lambda n: n["start_tick"])
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
    out = np.zeros(int(np.ceil(total_s * SR)) + SR, dtype=np.float32)
    sides = []
    for i, ph in enumerate(phrases):
        t0 = time.time()
        wav, side = render_phrase(bank, ph, mini_map, args)
        start_s = max(0.0, ph[0]["start_s"] - 0.05)
        start = int(round(start_s * SR))
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
    wav_path = os.path.join(args.outdir, f"{args.track}_stage1_diffsinger.wav")
    sf.write(wav_path, out, SR, subtype="PCM_16")
    side_path = os.path.join(args.outdir, f"{args.track}_stage1_diffsinger.json")
    json.dump({"track": args.track, "sr": SR, "dur_policy": args.dur,
               "steps": args.steps, "depth": args.depth, "expr": args.expr,
               "duration_s": round(len(out) / SR, 3),
               "peak_raw": round(raw_peak, 4),
               "peak_normalized": 0.89,
               "phrases": sides}, open(side_path, "w"), ensure_ascii=False, indent=1)
    print(f"写 {wav_path}（{len(out)/SR:.2f}s，原始峰值 {raw_peak:.4f} -> 归一 0.89）", flush=True)
    print(f"写 {side_path}", flush=True)


if __name__ == "__main__":
    main()
