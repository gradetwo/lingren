#!/usr/bin/env python3
"""Prompt preprocessing pipeline: raw audio -> prompt_meta (lingren SVS).

One job: decide whether a raw recording is usable as a reference
(prompt) voice, and emit everything POST /v1/voices needs.

Inputs
------
A single audio file (wav/flac/ogg via soundfile when installed, plain
wav via stdlib otherwise, anything else via an ffmpeg decode fallback).

Outputs (into --out-dir)
------------------------
- prompt_report.json : full measurements, verdict, warnings/rejects.
  This is the pipeline's own report format (see README for the schema).
- trimmed.wav        : leading/trailing silence removed (+50 ms padding
  each side), mono 16-bit wav at the input sample rate, ONLY when the
  verdict is not "reject". Reference only, NOT the upload.
- prompt_window.wav  : the selected 2-8 s VR window sliced out of the
  trimmed audio, ONLY when the verdict is not "reject". This is the
  audio to upload as `file`.
- normalized.wav     : prompt_window.wav with the suggested gain applied,
  ONLY with --write-normalized and only when the verdict is not reject.
- prompt_meta.json   : the SoulX prompt-meta file uploadable as the
  `prompt_meta` part of POST /v1/voices, ONLY when lyrics annotation is
  supplied (--lyrics + --phonemes, or --soulx-meta passthrough). Without
  annotation the pipeline emits prompt_meta.template.json instead and
  says so plainly: the pipeline measures audio, it does NOT invent
  lyrics, phonemes or pitches. A voice registered without prompt_meta
  has soulx_ready=false in Phase 1 (register ok, SoulX synth fails
  loudly) -- that is the honest state, not a silent pass.
- prompt_meta.template.json : SoulX-shaped segment with time/duration
  filled from the trimmed audio and text/phoneme/note_pitch left empty
  for a human to annotate. Never upload this as prompt_meta.

Verdict tiers (all thresholds are in README and in --help output)
-----------------------------------------------------------------
- accept                 : clean, VR 2-8 s window exists, no warnings.
- accept_with_warnings   : usable, but at least one warning (near clip,
  low SNR, speech-like, short voiced runs, ...). Human ear-check advised.
- reject                 : at least one hard reject (too short after
  trim, no clean 2-8 s window, severe clipping, mostly unpitched,
  pure-tone-like, internal silence hole > 1.0 s).

Exit codes: 0 = accept / accept_with_warnings, 2 = reject,
1 = input/usage error.

VocalRender rule (design): prompt must be 2-8 s of clean singing.
SoulX rule: same audio window plus a prompt_meta segment whose
duration list sums to the window length (checked when annotation is
supplied).

This is a heuristic signal screen, NOT a trained singing classifier.
Its non-singing labels ("pure-tone-like", "speech-like",
"music/noise-like") are warnings derived from pitch statistics; the
final acceptance is always a human ear check.

Only numpy is required. soundfile is optional (better format coverage);
ffmpeg is an optional decode fallback for mp3/m4a.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- thresholds
SILENCE_DBFS = -45.0       # frame RMS below this counts as silence
SILENCE_MIN_S = 0.10       # min silence run treated as a gap (trim edges)
TRIM_PAD_S = 0.05          # padding kept after trimming silence edges
VR_MIN_S = 2.0             # VocalRender prompt window, lower bound
VR_MAX_S = 8.0             # VocalRender prompt window, upper bound
CLIP_LEVEL = 0.999         # |x| >= this counts as a clipped sample
CLIP_REJECT = 0.01         # clipped fraction above this -> reject
CLIP_WARN = 0.001          # clipped fraction above this -> warn
SNR_WARN_DB = 20.0         # estimated SNR below this -> warn
VOICED_REJECT = 0.30       # pitched-frame fraction below this -> reject
VOICED_WARN = 0.60         # pitched-frame fraction below this -> warn
GAP_REJECT_S = 1.0         # internal silence hole above this -> reject
GAP_WARN_S = 0.30          # internal silence hole above this -> warn
PURE_TONE_SEMI_STD = 0.3   # F0 std below this + mostly voiced -> tone
SPEECH_RUN_S = 0.15        # median voiced run below this -> speech-like
PEAK_TARGET_DBFS = -3.0    # loudness suggestion: peak target
RMS_TARGET_DBFS = -18.0    # loudness suggestion: RMS target
GAIN_CAP_DB = 12.0         # suggestion larger than this -> warn "too quiet"
FRAME_S = 0.05             # analysis frame
HOP_S = 0.025              # analysis hop
F0_MIN_HZ = 80.0
F0_MAX_HZ = 600.0
VOICED_CONF = 0.50         # normalised autocorr peak for "pitched"


def dbfs(rms: float) -> float:
    return 20.0 * math.log10(rms) if rms > 1e-12 else -120.0


# ------------------------------------------------------------------- decode
def _read_wav_stdlib(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), \
            w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= (1 << 23), v - (1 << 24), v)
        data = v.astype(np.float32) / 8388608.0
    elif sw == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported wav sample width {sw}")
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    return np.asarray(data, dtype=np.float32), sr


def _read_ffmpeg(path: Path) -> tuple[np.ndarray, int]:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
         "-f", "f32le", "-"], capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise ValueError(f"ffmpeg decode failed: "
                         f"{proc.stderr.decode()[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy(), 48000


def read_audio(path: Path) -> tuple[np.ndarray, int, str]:
    """Return (mono float32, sample_rate, decoder_used)."""
    try:
        import soundfile as sf
        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if data.ndim == 2:
            data = data.mean(axis=1)
        return np.asarray(data, dtype=np.float32), int(sr), "soundfile"
    except ImportError:
        pass
    except Exception:
        pass  # fall through to stdlib / ffmpeg; soundfile errors on
        # some wav variants that the stdlib handles fine
    if path.suffix.lower() == ".wav":
        data, sr = _read_wav_stdlib(path)
        return data, sr, "stdlib-wave"
    data, sr = _read_ffmpeg(path)
    return data, sr, "ffmpeg"


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    pcm = np.clip(data, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


# ------------------------------------------------------------------ analysis
def frame_rms(x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    win = max(1, int(sr * FRAME_S))
    hop = max(1, int(sr * HOP_S))
    n = 1 + max(0, (len(x) - win) // hop)
    if n <= 0:
        return np.array([float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0]), HOP_S
    idx = np.arange(n)[:, None] * hop + np.arange(win)[None, :]
    frames = x[idx]
    return np.sqrt(np.mean(frames ** 2, axis=1)), HOP_S


def _autocorr_pitch(frame: np.ndarray, sr: int) -> tuple[float | None, float]:
    """FFT autocorrelation pitch for one frame. Returns (f0|None, conf)."""
    x = frame - frame.mean()
    if np.sqrt(np.mean(x ** 2)) < 1e-4:
        return None, 0.0
    win = np.hanning(len(x))
    xw = x * win
    nfft = 1 << int(math.ceil(math.log2(2 * len(xw))))
    spec = np.fft.rfft(xw, nfft)
    ac = np.fft.irfft(spec * np.conj(spec))[:len(xw)]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    lo = max(1, int(sr / F0_MAX_HZ))
    hi = min(len(ac) - 1, int(sr / F0_MIN_HZ))
    if hi <= lo:
        return None, 0.0
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    conf = float(ac[lag])
    if conf < VOICED_CONF:
        return None, conf
    # parabolic interpolation around the peak; the shift is clamped to
    # half a lag, otherwise a near-zero denominator can push lag negative
    # and produce a negative (log-breaking) F0.
    if 0 < lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        if denom != 0:
            shift = 0.5 * (a - c) / denom
            lag = lag + max(-0.5, min(0.5, shift))
    if lag <= 0:
        return None, conf
    return float(sr / lag), conf


def pitch_track(x: np.ndarray, sr: int) -> dict:
    win = max(1, int(sr * FRAME_S))
    hop = max(1, int(sr * HOP_S))
    n = 1 + max(0, (len(x) - win) // hop)
    f0s, confs, voiced = [], [], []
    for i in range(n):
        f0, conf = _autocorr_pitch(x[i * hop:i * hop + win], sr)
        f0s.append(f0)
        confs.append(conf)
        voiced.append(f0 is not None)
    voiced_arr = np.array(voiced, dtype=bool)
    f0_vals = np.array([f for f in f0s
                        if f is not None and np.isfinite(f) and f > 0],
                       dtype=float)
    # voiced runs (in frames) -> median sustained run length
    runs, cur = [], 0
    for v in voiced:
        if v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    med_run_s = float(np.median(runs) * HOP_S) if runs else 0.0
    out = {
        "voiced_fraction": float(voiced_arr.mean()) if len(voiced_arr) else 0.0,
        "mean_confidence": float(np.mean(confs)) if confs else 0.0,
        "median_f0_hz": float(np.median(f0_vals)) if len(f0_vals) else None,
        "f0_semitone_std": None,
        "median_voiced_run_s": round(med_run_s, 3),
        "n_frames": int(n),
    }
    if len(f0_vals) >= 3:
        semis = 12.0 * np.log2(f0_vals / np.median(f0_vals))
        out["f0_semitone_std"] = round(float(np.std(semis)), 3)
    return out


def trim_silence(x: np.ndarray, sr: int, rms: np.ndarray) -> tuple[int, int]:
    """Sample indices [start, end) after trimming edge silence."""
    thresh = 10.0 ** (SILENCE_DBFS / 20.0)
    loud = rms >= thresh
    if not loud.any():
        return 0, len(x)
    first = int(np.argmax(loud))
    last = int(len(loud) - 1 - np.argmax(loud[::-1]))
    hop = max(1, int(sr * HOP_S))
    pad = int(sr * TRIM_PAD_S)
    start = max(0, first * hop - pad)
    end = min(len(x), (last + 1) * hop + int(sr * FRAME_S) + pad)
    return start, end


def internal_gaps(rms: np.ndarray, start_f: int, end_f: int) -> list[dict]:
    thresh = 10.0 ** (SILENCE_DBFS / 20.0)
    gaps, cur, cur_start = [], 0, None
    for i in range(start_f, end_f):
        if rms[i] < thresh:
            if cur == 0:
                cur_start = i
            cur += 1
        else:
            if cur and cur * HOP_S >= SILENCE_MIN_S:
                gaps.append({"start_s": round(cur_start * HOP_S, 3),
                             "duration_s": round(cur * HOP_S, 3)})
            cur = 0
    if cur and cur * HOP_S >= SILENCE_MIN_S:
        gaps.append({"start_s": round(cur_start * HOP_S, 3),
                     "duration_s": round(cur * HOP_S, 3)})
    return gaps


def best_vr_window(x: np.ndarray, sr: int, rms: np.ndarray,
                   t_start: int, t_end: int) -> dict | None:
    """Best 2-8 s window inside the trimmed region.

    Scores candidate windows by mean log-RMS minus a heavy penalty for
    silence frames, so a long clean stretch beats a louder stretch with
    a hole in it. Returns None when the trimmed region is < 2 s.
    """
    dur = (t_end - t_start) / sr
    if dur < VR_MIN_S:
        return None
    win_s = min(VR_MAX_S, dur)
    f0 = max(0, min(len(rms), int(t_start / sr / HOP_S)))
    f1 = max(0, min(len(rms), int(t_end / sr / HOP_S)))
    seg = rms[f0:f1]
    if len(seg) == 0:
        return None
    wframes = max(1, int(win_s / HOP_S))
    if len(seg) <= wframes:
        return {"start_s": round(t_start / sr, 3),
                "end_s": round(t_end / sr, 3),
                "duration_s": round(dur, 3)}
    thresh = 10.0 ** (SILENCE_DBFS / 20.0)
    silent = (seg < thresh).astype(float)
    logrms = np.log(seg + 1e-9)
    csum = np.concatenate([[0.0], np.cumsum(logrms)])
    ssum = np.concatenate([[0.0], np.cumsum(silent)])
    means = (csum[wframes:] - csum[:-wframes]) / wframes
    sil_frac = (ssum[wframes:] - ssum[:-wframes]) / wframes
    score = means - 5.0 * sil_frac
    k = int(np.argmax(score))
    s = t_start / sr + k * HOP_S
    return {"start_s": round(s, 3), "end_s": round(s + win_s, 3),
            "duration_s": round(win_s, 3)}


# ------------------------------------------------------------------ pipeline
def analyze(path: Path) -> dict:
    x, sr, decoder = read_audio(path)
    if len(x) == 0:
        raise ValueError("empty audio")
    peak = float(np.max(np.abs(x)))
    rms_all = float(np.sqrt(np.mean(x ** 2)))
    clip_frac = float(np.mean(np.abs(x) >= CLIP_LEVEL))
    rms_frames, _ = frame_rms(x, sr)
    t_start, t_end = trim_silence(x, sr, rms_frames)
    trimmed = x[t_start:t_end]
    window = best_vr_window(x, sr, rms_frames, t_start, t_end)
    # Verdict metrics are measured on the VR WINDOW, not the whole file:
    # a 51 s song is not a prompt; its best 8 s window is. When there is
    # no window (trimmed < 2 s) the whole trimmed region is measured so
    # the reject reason still has numbers behind it.
    if window is not None:
        w_start = max(0, min(len(x), int(round(window["start_s"] * sr))))
        w_end = max(w_start, min(len(x), int(round(window["end_s"] * sr))))
        win_audio = x[w_start:w_end]
        wf0 = max(0, min(len(rms_frames), int(window["start_s"] / HOP_S)))
        wf1 = max(0, min(len(rms_frames), int(window["end_s"] / HOP_S)))
    else:
        win_audio = trimmed
        wf0 = max(0, min(len(rms_frames), int(t_start / sr / HOP_S)))
        wf1 = max(0, min(len(rms_frames), int(t_end / sr / HOP_S)))
    w_peak = float(np.max(np.abs(win_audio))) if len(win_audio) else 0.0
    w_rms = float(np.sqrt(np.mean(win_audio ** 2))) if len(win_audio) else 0.0
    w_clip = float(np.mean(np.abs(win_audio) >= CLIP_LEVEL)) if len(win_audio) else 0.0
    gaps = internal_gaps(rms_frames, wf0, wf1)
    # SNR estimate: quiet frames inside the window = noise floor proxy,
    # loud half of window frames = signal.
    thresh = 10.0 ** (SILENCE_DBFS / 20.0)
    seg_rms = rms_frames[wf0:wf1] if wf1 > wf0 else rms_frames
    quiet = seg_rms[seg_rms < thresh]
    loud_half = np.sort(seg_rms)[len(seg_rms) // 2:] if len(seg_rms) else np.array([])
    if len(quiet) and len(loud_half) and np.median(quiet) > 0:
        snr = 20.0 * math.log10(float(np.median(loud_half)) / float(np.median(quiet)))
    else:
        snr = None
    pitch = pitch_track(win_audio, sr)

    warnings, rejects = [], []
    dur_trim = len(trimmed) / sr
    if window is None:
        rejects.append(f"too_short_after_trim: trimmed {dur_trim:.2f}s < "
                       f"{VR_MIN_S:.0f}s (VR needs a 2-8s window)")
    if w_clip > CLIP_REJECT:
        rejects.append(f"severe_clipping: {w_clip * 100:.2f}% window samples "
                       f"at/above {CLIP_LEVEL} (reject over "
                       f"{CLIP_REJECT * 100:.0f}%)")
    elif w_clip > CLIP_WARN:
        warnings.append(f"clipping: {w_clip * 100:.2f}% window samples clipped")
    if pitch["voiced_fraction"] < VOICED_REJECT:
        rejects.append(f"mostly_unpitched: voiced fraction "
                       f"{pitch['voiced_fraction']:.2f} < {VOICED_REJECT}")
    elif pitch["voiced_fraction"] < VOICED_WARN:
        warnings.append(f"low_pitched_content: voiced fraction "
                        f"{pitch['voiced_fraction']:.2f} "
                        f"(music/noise-like, ear-check)")
    f0std = pitch["f0_semitone_std"]
    if (f0std is not None and f0std < PURE_TONE_SEMI_STD
            and pitch["voiced_fraction"] > 0.70):
        rejects.append(f"pure_tone_like: F0 semitone std {f0std} with "
                       f"voiced fraction {pitch['voiced_fraction']:.2f} "
                       f"(a test tone, not singing)")
    if pitch["median_voiced_run_s"] < SPEECH_RUN_S \
            and pitch["voiced_fraction"] >= VOICED_REJECT:
        warnings.append(f"speech_like: median voiced run "
                        f"{pitch['median_voiced_run_s']}s < {SPEECH_RUN_S}s "
                        f"(short bursts, verify it is singing not speech)")
    longest_gap = max((g["duration_s"] for g in gaps), default=0.0)
    if longest_gap > GAP_REJECT_S:
        rejects.append(f"internal_silence_hole: longest gap "
                       f"{longest_gap}s > {GAP_REJECT_S}s")
    elif longest_gap > GAP_WARN_S:
        warnings.append(f"internal_silence: longest gap {longest_gap}s")
    if snr is not None and snr < SNR_WARN_DB:
        warnings.append(f"low_snr_estimate: {snr:.1f}dB < {SNR_WARN_DB:.0f}dB")
    if w_peak > 10.0 ** (-1.0 / 20.0):
        warnings.append(f"near_clip: window peak {dbfs(w_peak):.1f}dBFS")
    gain_peak = PEAK_TARGET_DBFS - dbfs(w_peak) if w_peak > 0 else 0.0
    gain_rms = RMS_TARGET_DBFS - dbfs(w_rms) if w_rms > 0 else 0.0
    gain = min(gain_peak, gain_rms)
    if abs(gain) > GAIN_CAP_DB:
        warnings.append(f"very_quiet_or_loud: suggested gain {gain:+.1f}dB "
                        f"exceeds {GAIN_CAP_DB:.0f}dB, check recording level")
    if snr is None:
        warnings.append("snr_not_estimable: no sub--45dBFS frames inside "
                        "trimmed region to use as a noise floor proxy")

    verdict = "reject" if rejects else (
        "accept_with_warnings" if warnings else "accept")
    return {
        "input": {"path": str(path), "decoder": decoder,
                  "sample_rate_hz": sr,
                  "channels_note": "mixed to mono for analysis",
                  "duration_s": round(len(x) / sr, 3),
                  "peak_dbfs": round(dbfs(peak), 2),
                  "rms_dbfs": round(dbfs(rms_all), 2),
                  "clipped_fraction": round(clip_frac, 5)},
        "trim": {"start_s": round(t_start / sr, 3),
                 "end_s": round(t_end / sr, 3),
                 "duration_s": round(dur_trim, 3),
                 "head_trimmed_s": round(t_start / sr, 3),
                 "tail_trimmed_s": round((len(x) - t_end) / sr, 3)},
        "window_metrics": {"peak_dbfs": round(dbfs(w_peak), 2),
                           "rms_dbfs": round(dbfs(w_rms), 2),
                           "clipped_fraction": round(w_clip, 5),
                           "snr_estimate_db": (round(snr, 1)
                                               if snr is not None else None),
                           "snr_note": "median loud-half window frame RMS vs "
                                       "median sub--45dBFS window frame "
                                       "RMS; null when no quiet frames exist",
                           "scope_note": "pitch, gaps, SNR, peak, RMS and "
                                         "clipping are all measured on the "
                                         "VR window, not the whole file"},
        "pitch": pitch,
        "internal_silence_gaps": gaps,
        "vr_window": window,
        "vr_ready": window is not None and not rejects,
        "loudness_suggestion": {
            "target_peak_dbfs": PEAK_TARGET_DBFS,
            "target_rms_dbfs": RMS_TARGET_DBFS,
            "suggested_gain_db": round(gain, 2),
            "apply": abs(gain) >= 1.0},
        "warnings": warnings,
        "rejects": rejects,
        "verdict": verdict,
        "_trim_samples": (t_start, t_end),  # stripped before writing report
        "_sr": sr,
        "_window": window,
    }


def build_soulx_segment(trimmed_dur_s: float, lyrics: list[str],
                        phonemes: list[str], pitches: list[int],
                        language: str, index: str) -> dict:
    if not (len(lyrics) == len(phonemes) == len(pitches)):
        raise ValueError("lyrics/phonemes/pitches must have equal length")
    n = len(lyrics)
    per = trimmed_dur_s / n
    return {
        "index": index,
        "language": language,
        "time": [0, int(round(trimmed_dur_s * 1000.0))],
        "duration": " ".join(f"{per:.3f}" for _ in range(n)),
        "text": " ".join(lyrics),
        "phoneme": " ".join(phonemes),
        "note_pitch": " ".join(str(p) for p in pitches),
        "note_type": " ".join("2" for _ in range(n)),
        "f0": "",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Raw audio -> prompt_meta for lingren SVS voices.",
        epilog="Exit 0 = accept / accept_with_warnings, 2 = reject, "
               "1 = input error. See README.md for thresholds and the "
               "POST /v1/voices field mapping.")
    ap.add_argument("input", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--lyrics", nargs="+",
                    help="annotated lyric tokens, one per note, e.g. "
                         "--lyrics ni hao (needs --phonemes)")
    ap.add_argument("--phonemes", nargs="+",
                    help="SoulX phone ids, e.g. zh_ni3 zh_hao3")
    ap.add_argument("--pitches", nargs="+", type=int,
                    help="MIDI pitch per note; default: all 60")
    ap.add_argument("--language", default="Mandarin")
    ap.add_argument("--voice-index", default="lingren_prompt")
    ap.add_argument("--soulx-meta", type=Path,
                    help="passthrough SoulX segment JSON (list with one "
                         "dict, or a bare dict); its duration sum is "
                         "checked against the trimmed duration")
    ap.add_argument("--write-normalized", action="store_true")
    args = ap.parse_args(argv)

    if not args.input.is_file():
        print(f"error: no such file: {args.input}", file=sys.stderr)
        return 1
    try:
        rep = analyze(args.input)
    except Exception as e:
        print(f"error: analysis failed: {e}", file=sys.stderr)
        return 1
    sr = rep.pop("_sr")
    t_start, t_end = rep.pop("_trim_samples")
    window = rep.pop("_window")
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    x, _, _ = read_audio(args.input)
    trimmed = x[t_start:t_end]
    # The upload candidate is the VR WINDOW slice, not the whole trimmed
    # file: a 51 s song must not be uploaded as a prompt.
    if window is not None:
        ws = max(0, min(len(x), int(round(window["start_s"] * sr))))
        we = max(ws, min(len(x), int(round(window["end_s"] * sr))))
        upload_audio = x[ws:we]
    else:
        upload_audio = trimmed
    soulx_ready = False
    soulx_note = None
    if rep["verdict"] != "reject":
        write_wav(out / "trimmed.wav", trimmed, sr)
        write_wav(out / "prompt_window.wav", upload_audio, sr)
        if args.write_normalized:
            g = 10.0 ** (rep["loudness_suggestion"]["suggested_gain_db"] / 20.0)
            write_wav(out / "normalized.wav",
                      np.clip(upload_audio * g, -1.0, 1.0), sr)
    if args.soulx_meta is not None:
        raw = json.loads(args.soulx_meta.read_text())
        seg = raw[0] if isinstance(raw, list) else raw
        try:
            durs = [float(v) for v in seg["duration"].split()]
        except Exception as e:
            print(f"error: bad --soulx-meta duration: {e}", file=sys.stderr)
            return 1
        meta_dur = (window["duration_s"] if window is not None
                    else rep["trim"]["duration_s"])
        if abs(sum(durs) - meta_dur) > 0.05:
            print(f"error: --soulx-meta durations sum {sum(durs):.3f}s "
                  f"!= window {meta_dur:.3f}s",
                  file=sys.stderr)
            return 1
        (out / "prompt_meta.json").write_text(json.dumps([seg], indent=2))
        soulx_ready = True
        soulx_note = "passthrough segment validated against trimmed duration"
    elif args.lyrics is not None:
        if args.phonemes is None:
            print("error: --lyrics needs --phonemes", file=sys.stderr)
            return 1
        pitches = args.pitches or [60] * len(args.lyrics)
        meta_dur = (window["duration_s"] if window is not None
                    else rep["trim"]["duration_s"])
        try:
            seg = build_soulx_segment(meta_dur,
                                      args.lyrics, args.phonemes, pitches,
                                      args.language, args.voice_index)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        (out / "prompt_meta.json").write_text(json.dumps([seg], indent=2))
        soulx_ready = True
        soulx_note = ("segment built from supplied annotation; per-note "
                      "durations are uniform (trimmed/n) -- replace with "
                      "measured per-note durations when available")
    else:
        tmpl_dur = (window["duration_s"] if window is not None
                    else rep["trim"]["duration_s"])
        tmpl = {"index": args.voice_index, "language": args.language,
                "time": [0, int(round(tmpl_dur * 1000.0))],
                "duration": "",
                "text": "", "phoneme": "", "note_pitch": "",
                "note_type": "", "f0": "",
                "needs_annotation": True,
                "note": "time filled from the VR window; text/phoneme/"
                        "note_pitch/note_type MUST be annotated by a human "
                        "before this becomes prompt_meta.json"}
        (out / "prompt_meta.template.json").write_text(
            json.dumps([tmpl], indent=2))
        soulx_note = ("no annotation supplied: template only, "
                      "soulx_ready=false (voice still registers, SoulX "
                      "synth fails loudly without prompt_meta)")
    rep["soulx_ready"] = soulx_ready and rep["verdict"] != "reject"
    rep["soulx_note"] = soulx_note
    rep["upload"] = {
        "file": "prompt_window.wav (the VR window slice; "
                "normalized.wav with --write-normalized)",
        "prompt_meta": ("prompt_meta.json" if soulx_ready
                        and rep["verdict"] != "reject" else None),
        "post_v1_voices": "multipart: file=<audio>, prompt_meta=<file> "
                          "when present; without prompt_meta the voice "
                          "registers with soulx_ready=false"}
    (out / "prompt_report.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps({"verdict": rep["verdict"],
                      "vr_ready": rep["vr_ready"],
                      "soulx_ready": rep["soulx_ready"],
                      "trimmed_s": rep["trim"]["duration_s"],
                      "vr_window": rep["vr_window"],
                      "warnings": rep["warnings"],
                      "rejects": rep["rejects"],
                      "out_dir": str(out)}, indent=2))
    return 0 if rep["verdict"] != "reject" else 2


if __name__ == "__main__":
    raise SystemExit(main())
