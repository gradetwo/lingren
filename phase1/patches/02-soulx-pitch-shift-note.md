# SoulX pitch_shift 旋钮复测：Phase 0 结论不成立（干净对照实证）

Phase 0 结论（§3.3 方法 A）：“pitch_shift 旋钮不能用来做和声部”。
该结论的对照是混杂的：它把 `pitch_shift=4, auto_shift 关` 的输出
（F0 中位 404.6 Hz）与 `auto_shift 开` 的输出（379.7 Hz）比，得
+110 音分。但 auto_shift 开本身已把音高拉了 +340 音分（同乐谱同
seed：关 312.0 Hz → 开 379.7 Hz），所以 +110 音分是“在已移调基线
上的增量”，不是旋钮的移调量。

## Phase 1 干净对照（Colab L4，同 prompt、同 seed 0、auto_shift 关）

短乐谱 4 音符（你 64 / 好 67 交替，各 0.5 s，共 2.0 s），pyin 测 F0
中位（`colab_soulx_test.py`，输出见 `colab_logs/soulx_test.log`）：

| 臂 | 处理 | F0 中位 | 相对基线 |
|---|---|---|---|
| A（基线） | 原乐谱，pitch_shift=0 | 335.4 Hz | — |
| F | 原乐谱，pitch_shift=4 | 433.7 Hz | **+445 音分** |
| G | 乐谱整体 +4 半音，pitch_shift=0 | 431.2 Hz | **+435 音分** |

F 与 G 包络 log-RMS 相关度 0.9955（几乎同一音频），F0 中位差仅
10 音分。结论：**pitch_shift 旋钮的移调效果与乐谱整体移调基本等价**
（本测试：+445 vs +435 音分，目标 +400 音分，余量来自 pyin 与
模型本身的音高 slack，Phase 0 独立乐谱路线同样测得 +450 音分）。

机制注：`infer()` 里 `note_pitch[note_pitch > 0] += f0_shift` 作用于
prompt + target 拼接后的全部音高（prompt 也被移），但输出仍与只移
target 的 G 基本一致——prompt 音高条件对输出音高的影响弱于 target
音高条件与 prompt 音频本身。

## 对和声路线的修正建议

- Phase 0 的“旋钮不能做和声”结论**收回**，改为：旋钮与独立乐谱
  路线在本测试中等价；独立乐谱路线仍是主推（语义最干净：每 part
  写自己的 note_pitch，prompt 不动），旋钮是可用备选（例如已有
  乐谱不想重写时整体移调）。
- 保留谨慎：本测试只有 2 秒、4 音符、1 prompt、1 seed。长乐句、
  多音高跨度上的等价性需 Phase 2 用真实乐句再验一次后才能写进
  skill/文档当定论；本文件结论的适用范围就是本次测试条件。
- auto_shift 的处理不变：要乐谱绝对音高时必须关 auto_shift（本
  测试：开 auto_shift 录得 f0_shift=+3 半音，实测 F0 +295 音分，
  与记录值一致），或把记录的移调量写进回执（补丁 01）。
