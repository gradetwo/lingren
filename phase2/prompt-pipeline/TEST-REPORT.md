# Prompt 流水线实测报告（2026-11-02）

运行环境：本 VM，phase1 venv Python（numpy 2.5.4 + soundfile）。
复现：`tests/run_tests.sh`（12/12 PASS，退出 0）。
逐案完整报告：`tests/results/<用例>/prompt_report.json`。

## 结论

- 12 案：accept 1、accept_with_warnings 5、reject 6，全部符合设计预期。
- SoulX 标注路径与 Phase 1 服务实测对接通过（见 §3）。
- 实测中修掉 4 个真 bug（见 §4），修后全量重跑 12/12。

## 1. 实测表（窗口口径：峰值/RMS/SNR/音高/静音洞均在 VR 窗口上量）

| 用例 | 来源 | 输入→窗口 | verdict | 关键实测值与理由 |
| --- | --- | --- | --- | --- |
| vr_take0 | phase0 证据，VR 合成歌声 5.92 s / 48 kHz | 5.85 s | accept_with_warnings | 有音高 0.83，F0 中位 331 Hz，SNR 37.2 dB；唯一警告峰值 −0.9 dBFS（近削波，模型输出本身偏热） |
| vr_out1 | phase0 证据，VR 合成歌声 6.08 s | 6.08 s | accept_with_warnings | 有音高 0.80，SNR 42.2 dB；警告同上（峰值 −0.9 dBFS） |
| soulx_A | phase1 证据，SoulX 合成 2.00 s（下边界） | 2.00 s | accept | 有音高 0.85，SNR 20.0 dB，建议增益 +9.7 dB；零警告——2 秒整恰好过门 |
| split_base | phase1 证据，VR 合成 1.92 s（下边界外） | 无窗口 | reject | 仅差 0.08 秒：去静音后 1.92 s < 2 s |
| generated | phase0 证据，SoulX 合成 51.24 s 长歌 | 18.8–26.8 s（8.0 s 窗） | accept_with_warnings | 有音高 0.85，SNR 41.8 dB；窗口选中段含 0.875 s 静音洞 → 警告（未到 1.0 s 拒绝线）；证明超长输入走窗口切片、不整段上传 |
| gap_singing | vr_take0 中部插入 1.5 s 静音（合成对照） | 7.35 s（整段即窗口） | reject | 最长静音洞 1.55 s > 1.0 s |
| synth_short_1s | 合成 440 Hz 1.0 s | 无窗口 | reject | 过短 + 纯音特征双拒绝 |
| synth_tone_4s | 合成 440 Hz 4.0 s | 4.0 s | reject | 纯音特征：F0 半音标准差 0.0、有音高 1.00 |
| synth_noise_4s | 合成白噪声 4.0 s | 4.0 s | reject | 有音高 0.00 < 0.30 |
| synth_clipped_4s | 合成 440 Hz 驱动至 1.5 后硬削波 | 4.0 s | reject | 削波 53.5% > 1%，另叠加纯音特征 |
| synth_bursts_4s | 合成 100 ms 音高跳变短突发 ×24 | 3.6 s | accept_with_warnings | 语音特征警告：有音高段中位长 0.125 s < 0.15 s |
| synth_quiet_vibrato_4s | 合成 440 Hz + 5 Hz 颤音，峰值 −30 dBFS | 4.0 s | accept_with_warnings | 颤音使 F0 标准差 0.498 > 0.3，不判纯音；警告建议增益 +15.5 dB 超 12 dB 线 |

## 2. 未覆盖项（诚实缺口）

- 本地没有真人自有录音，也没有官方 demo prompt 音频原文件（SoulX
  `zh_prompt.mp3`、VR `2003000081.wav` 只在 Colab 上；注：另一线
  目录 `phase2/long-phrase/sources/` 下有 `zh_prompt.json` meta 文本，
  但 prompt 音频本身仍不在本地，且该文件属另一线材料，本线未用它，
  音频侧缺口仍然成立）。“真人 prompt”
  一档未实测；歌声档用的是 Phase 0/1 模型合成输出（信号层面与真人
  歌声同类检查有效，但音色/咬字分布不等于真人录音）。拿到真人
  录音后应补一案再跑。
- 语音（真人说话）一档只有合成短突发近似，未用真人语音实测；
  speech-like 目前是警告不是拒绝，误判成本低（人耳复核兜底）。
- 纯音乐（多声部伴奏）一档未实测；按规则应落入“有音高占比低 →
  警告”或“无音高 → 拒绝”，待补案验证。

## 3. Phase 1 服务对接实测（本地起 phase1 服务，phase1 文件未动）

- `prompt_window.wav` + `--lyrics ni hao --phonemes zh_ni3 zh_hao3
  --pitches 64 67` 产出的 `prompt_meta.json`（time [0,5850]、
  duration "2.925 2.925"，各项和 = 窗口 5.85 s）上传
  `POST /v1/voices` → `soulx_ready=true`。
- 同音频不带 prompt_meta 上传 → `soulx_ready=false`，与 Phase 1
  语义一致。
- 附带观察（Phase 1 现象，不属本线，不改）：multipart 的 `name`
  字段返回 null——main.py 把 `name` 当 query 参数收，表单字段没
  接住；voice_id 是主键，不影响使用。

## 4. 实测中修掉的 4 个 bug（修后全量重跑）

1. voiced run 统计错：`cur` 清零后才判断追加，导致 run 恒为空，
   真歌声误报 speech-like。修后 vr_take0 中位 run 0.2 s，不再误报。
2. 帧索引越界：去静音尾部 padding 使帧下标超出 RMS 数组，短文件
   崩溃（`index out of bounds`）。帧下标全部钳制到数组范围。
3. verdict 口径错：初版按整段 51 秒量音高/静音洞，长歌必被段内
   乐句间停顿拖成 reject。改为全部 verdict 指标只在 VR 窗口上量；
   上传文件也从整段 trimmed 改为窗口切片 `prompt_window.wav`。
4. 附带：抛物线 F0 插值分母近零时把 lag 推成负值，F0 为负 →
   log2 出 nan。插值位移钳制 ±0.5 帧，并过滤非正 F0；保存的报告
   已验无 NaN。
