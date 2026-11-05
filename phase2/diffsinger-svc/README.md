# DiffSinger＋SVC 两阶段路线研究

Wang 2026-11-05 18:32 新令：伶人开分支研究 DiffSinger＋SVC 两阶段法。
第一阶段 DiffSinger 合成对应乐谱／音高和曲风的音频；第二阶段 SVC 到目标音色。
两个 SVC 都要做（Wang 18:35）：SoulX SVC 与 YingMusic-SVC，Wang 要比对。
每轨最终出三份：DiffSinger 版（SVC 之前）＋ SoulX SVC 版 ＋ YingMusic-SVC 版。5 轨共 15 份。

## 交付物命名

`outputs/<轨名>_stage1_diffsinger.wav`（第一阶段，SVC 之前）
`outputs/<轨名>_stage2_soulx-svc.wav`（第二阶段，SoulX SVC）
`outputs/<轨名>_stage2_yingmusic-svc.wav`（第二阶段，YingMusic-SVC）

轨名：vox（主唱）、voxTel（电话滤波低语）、vox2（齐吼）、vox3（黑嗓高八度）、vox4（暴民合唱低层）。

## 谱子

V4 谱子：winter_metal.yeban 的 5 条人声轨，共 685 音（vox 335、voxTel 99、vox2 151、vox3 46、vox4 54）。
来源：`~/workspace/song-lindong-metal-revise/work/dry-vocal-package/`（vocal_scores.json：669 音带歌词，逐音对齐）。
歌词中英混杂：中文行（凛冬将至、昨日你信誓旦旦……）＋英文 "Winter Is Coming"（只 4 个词：Winter / Is / Coming）。

## 声库

- 第一阶段：绮萱 Qixuan v2.7.0（中文为主，支持多语种；气声／发声参数）。
  中英混杂的谱子只能用多语种声库；SPORE 是英语单语，不能唱中文行。
- SPORE（英语）：只作管线对照，不用于 V4 交付。
- 目标音色（第二阶段 prompt，与 V4 一致）：
  vox / voxTel = bard_growl_24k.wav（bard 低吼）；vox2 / vox4 = groupyell_24k.wav；
  vox3 = bard_scream_24k.wav。prompt 在 dry-vocal-package/voice/prompts_24k/。

## 关键发现（实测为准，见 REPORT-diffsinger-svc.md）

- 15 份音频全部落盘（`outputs/`）。第一阶段只在 vox3 上成立（音高正确）。其余 4 轨（音高 MIDI 23–46）超出童声声库音区，第一阶段渲染无清晰基频。第二阶段不能修复第一阶段。瓶颈在声学模型，声码器对照实验证明其在 73.42 Hz 工作正常。
- SoulX 不自动移调。YingMusic 自动移调，vox2 与 vox4 被上移 12 半音。vox3 上两份 SVC 均保住谱面音高（偏差中位 −0.031 与 −0.058 半音）。
- `.ds` 格式（openvpi/DiffSinger samples/*.ds 实测）：JSON list of segments；
  每段含 offset / text / ph_seq / ph_dur / ph_num / note_seq / note_dur / note_slur /
  f0_seq / f0_timestep。ph_seq 是无调拼音音素（如 "t ian sh ang"），声调由 f0_seq 承载。
- 绮萱包是 OpenUTAU 包，只有 ONNX，没有 .ckpt。官方 PyTorch `scripts/infer.py` 无法直接用。本研究用 ONNX Runtime 直驱 8 个模型会话（acoustic／dsdur／dspitch／dsvariance 各 linguistic＋主模型）＋ NSF-HiFiGAN 声码器。
- YingMusic-SVC：GiantAILab/YingMusic-SVC，MIT，基于 Seed-VC；推理 `my_inference.py`（扩散 100 步，fp16）；
  检查点 YingMusic-SVC-full 在 Hugging Face；本研究用 Python 3.12 venv＋torch 2.4.0（numpy 1.26.4 要求 Python ≤3.12）。

## 脚本

- `scripts/sing_qixuan.py`：第一阶段驱动（Bank 加载→dsdur 量测→时长重缩放→dspitch→dsvariance→acoustic→声码器→拼轨）。参数：steps 20，depth 0.6，expr 1.0，`--dur score`。
- `scripts/build_ds.py`：vocal_scores.json -> 每轨 .ds（音素化：中文 pypinyin 无调拆声母+韵母；
  英文按绮萱音素表查；无歌词音作 melisma，note_slur=1；句间休止 >0.6s 断句）。
- `scripts/run_stage2_soulx.sh`：第二阶段 SoulX SVC（`cli.inference_svc`，pitch_shift 0，fp16；输出 24k，上采样到 48k 交付）。
- `scripts/run_stage2_yingmusic.py`：第二阶段 YingMusic-SVC 批量驱动（模型加载一次，连跑 5 轨；扩散 100 步，fp16；输出 44.1k 直接交付）。

## 环境

Colab 会话 `ds-svc-l4`（L4，torch 2.11.0+cu130 系统栈；SoulX 侧走 svc-colab-setup.sh 的
uv+venv torch 2.10 路径，与 lindong 线一致）。重活一律在 Colab 跑，不在 VM 跑 GPU 活。

## 来源与许可（只做实验，不发布，不分发）

- Qixuan v2.7.0：yqzhishen/qixuan-diffsinger，GitHub Releases（420,707,627 字节）。
- YingMusic-SVC：GiantAILab/YingMusic-SVC，MIT 许可；检查点 YingMusic-SVC-full（HF）。
- DiffSinger：openvpi/DiffSinger（推理框架）。
- SoulX-Singer：内部服务链路（svc_prompt_driver.py）。
- 声库只本地实验用，不分发；发布前重核许可。
