# Prompt 预处理流水线：生音频 → prompt_meta

脚本：`prompt_pipeline.py`（只依赖 numpy；soundfile 可选，ffmpeg 是
mp3/m4a 的解码后备）。Phase 1 服务本身不改；本脚本只产出它接受的
上传文件。

## 跑法

```bash
python3 prompt_pipeline.py <生音频> --out-dir <输出目录> [--write-normalized]
# 带歌词标注时（见下）同时产出可上传的 prompt_meta.json：
python3 prompt_pipeline.py <生音频> --out-dir <输出目录> \
  --lyrics ni hao --phonemes zh_ni3 zh_hao3 --pitches 64 67
```

退出码：0 = accept / accept_with_warnings，2 = reject，1 = 输入错误。

## 输出文件

| 文件 | 说明 |
| --- | --- |
| `prompt_report.json` | 全部测量值、verdict、警告/拒绝理由（本流水线自有格式） |
| `trimmed.wav` | 去头尾静音后的全段（仅参考，verdict 非 reject 时写） |
| `prompt_window.wav` | 选出的 2–8 秒 VR 窗口切片（上传 `file` 就用它） |
| `normalized.wav` | 窗口音频 + 建议增益（仅 `--write-normalized`） |
| `prompt_meta.json` | SoulX prompt-meta（仅有标注时产出，可直接作 `prompt_meta` 上传） |
| `prompt_meta.template.json` | 无标注时的模板：time 已按窗口填好，text/phoneme/note_pitch/note_type 为空，必须人工标注后才能当 prompt_meta 用 |

## 流程（按顺序）

1. 解码 → 单声道 float32（soundfile / stdlib wave / ffmpeg 后备）。
2. 按 20 ms 帧 RMS、−45 dBFS 门限去头尾静音，两侧各留 50 ms。
3. 在去静音段内选 VR 窗口：最长 8 秒，分数 = 平均 log-RMS −
   5 × 窗内静音帧占比（响且干净的段优先；整段 ≤8 秒时窗口就是整段）。
4. 在窗口上量：峰值/RMS dBFS、削波占比、SNR 估计、F0 统计、
   窗内静音洞、响度建议增益。
5. 按下表定 verdict；reject 时不写任何音频文件（不让坏 prompt 流出）。

## 校验规则与阈值（全部可执行，见脚本顶部常量）

| 检查 | reject | warn |
| --- | --- | --- |
| 窗口时长 | 去静音后 < 2 秒（无 2–8 秒窗口） | — |
| 削波（窗口样本 \|x\| ≥ 0.999） | > 1% | > 0.1% |
| 有音高帧占比（自相关 F0，80–600 Hz，置信 ≥ 0.5） | < 0.30（基本无音高） | < 0.60（疑似音乐/噪声，人耳复核） |
| 纯音特征 | F0 半音标准差 < 0.3 且有音高占比 > 0.70（测试音，不是歌声） | — |
| 语音特征 | — | 有音高段中位长 < 0.15 秒（短促突发，疑似语音不是歌声） |
| 窗内静音洞 | 最长洞 > 1.0 秒 | 最长洞 > 0.30 秒 |
| SNR 估计（窗内响帧中位 RMS / 静音帧中位 RMS） | — | < 20 dB；窗内无静音帧时记 null 并警告“无法估计” |
| 峰值 | — | > −1 dBFS（近削波） |
| 建议增益（峰值→−3 dBFS 与 RMS→−18 dBFS 取较小者） | — | 绝对值 > 12 dB（录音电平异常） |

## POST /v1/voices 字段映射（Phase 1 语义，`phase1/service/app/main.py` + `jobs.py`）

| 流水线产物 | /v1/voices 字段 | 服务端行为 |
| --- | --- | --- |
| `prompt_window.wav`（或 `normalized.wav`） | `file`（multipart 音频） | 存为 `voices/<id>/prompt.wav` |
| `prompt_meta.json`（仅有标注时） | `prompt_meta`（multipart 文件） | 原样存为 `voices/<id>/prompt_meta.json`；`soulx_ready=true` |
| 无 prompt_meta | 不传该字段 | 注册成功但 `soulx_ready=false`；SoulX 合成时响亮失败，不静默 |
| `prompt_report.json` | 不上传 | 只给人看的质检报告；`vr_ready` = 有窗口且无 reject |

SoulX prompt_meta 形状（驱动取 JSON 列表第 0 个段，
`phase1/service/app/soulx_driver.py` + `score_converter.soulx_target_meta`）：

```json
[{"index": "lingren_prompt", "language": "Mandarin",
  "time": [0, 5850], "duration": "2.925 2.925",
  "text": "ni hao", "phoneme": "zh_ni3 zh_hao3",
  "note_pitch": "64 67", "note_type": "2 2", "f0": ""}]
```

- `time[1]` = 窗口毫秒数；`duration` 各项之和必须等于窗口时长
  （`--soulx-meta` 透传时脚本校验，误差 > 0.05 秒即报错退出）。
- `--lyrics/--phonemes` 模式下各音符时长暂按窗口均分（窗口/音符数）；
  有实测分音符时值时应替换为实测值——均分只是占位，报告里写明。
- 模板（`prompt_meta.template.json`）绝不能直接上传：它的
  text/phoneme 是空的，传了等于给模型喂假标注。

VocalRender 侧不需要 prompt_meta：VR prompt 只要音频本身满足
2–8 秒干净歌声，即 `vr_ready=true`。

## 诚实边界

- 非歌声标签（纯音/语音/音乐噪声样）是 F0 统计启发式，不是训练
  好的分类器；最终接受一律以人耳为准。
- 官方 demo prompt 音频原文件（SoulX `zh_prompt.mp3`、VR
  `2003000081.wav`）只在 Colab 上，本地没有；实测 corpus 见 `tests/`
  （Phase 0/1 证据歌声 + 合成对照音），报告 `TEST-REPORT.md` 写清了
  每条的来源。
- SNR 是帧 RMS 比值估计，不是标准响度/信噪比测量；窗内全程有声
  时无静音帧可作噪声底，记 null 而不是编一个数。
