# Phase 1 推理代码补丁（全部存成文件，拒绝“只改已装包”）

应用方法：进各仓库根目录，`git apply <补丁>`；顺序不限（各改不同文件）。
上游更新后若上下文漂移，用 `git apply --check` 先验，手工移植后重新
`git diff` 生成新版补丁文件，不得只改装机不留档。

## SoulX-Singer（仓库 Soul-AILab/SoulX-Singer）

- `00-soulx-llama-config-kwargs.patch`：llama.py 的 LlamaConfig 位置
  参数改关键字参数（同值）。Phase 0 教训的存档版：transformers
  4.41.2 在 Python 3.13 上无轮子，工作组合是 transformers==4.46.3 +
  tokenizers==0.20.3，新版 transformers 拒位置参数。已验：pristine
  克隆 `git apply --check` 全过。
- `01-soulx-record-f0-shift.patch`：infer() 内算出的 f0_shift（半音）
  写到 `self.last_f0_shift`，供服务回执记录。无签名变化、无行为变化。
  背景：auto_shift 会把输出音高往 prompt 音域拉（Phase 0 实测 +340
  音分），不开它或记进回执是设计要求；未打此补丁时回执字段为 null。
- `02-soulx-cli-seed.patch`：CLI 加 `--seed`，在 main() 内一次种
  torch/random/numpy（含 CUDA）。上游 CLI 本无 seed 参数（Phase 0
  靠驱动脚本设全局 RNG）。一进程一遍；多段乐谱不重种，段间不塌成
  相同音频。

## VocalRender（仓库 pymaster17/VocalRender）

- `03-vocalrender-cli-seed.patch`：推理脚本加 `--seed`，在
  generate_batch 前一次种 torch/random/numpy（含 CUDA）。上游脚本本
  无 seed 参数（Phase 0 靠连续 RNG 取遍）。扩散解码用 torch 全局 RNG
  （unified_cfm 的 torch.randn/rand），AR 部分采样路径待实证确认；
  本补丁的实际效果以 Phase 1 实证为准：同 seed 两遍是否逐位相同、
  换 seed 是否过差异门（见 phase1 报告）。
- 另需环境补装（Phase 0 教训）：仅 argbind；s3prl /
  audiobox-aesthetics 是评估用，推理不 import，不装。

## 未打补丁、靠设计规避的项

- SoulX 时钟贴轨：不需补丁。CLI 本就按段起点 `start_sample_idx`
  把段音频贴进整段（score 时钟是结构性的）；服务侧一 part 一段。
- SoulX pitch_shift 和声：见 `02-soulx-pitch-shift-note.md`（实证后写）。
  Phase 0 结论（旋钮不能做和声）建立在混杂对照上，Phase 1 用干净
  对照重测：同 seed、auto_shift 关，三臂（基线 / pitch_shift=4 /
  乐谱整体 +4 半音）比 F0 中位与逐音准确率。
- VocalRender 对齐回执：不需改模型。服务侧用能量 + F0 联合分段
  （`service/app/alignment.py`），Phase 0 能量法 11/15 是初版，联合法
  在 Phase 0 的 vr_out1.wav 上本地验证，目标找回全部可辨音节起点。
