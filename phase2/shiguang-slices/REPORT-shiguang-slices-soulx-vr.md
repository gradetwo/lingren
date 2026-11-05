# 《时光》谱子 · SoulX 与 VocalRender-Pro 切片对比报告（阶段 1b）

日期：2026-11-06。分支：`lingren-diffsinger-svc-twostage`。Wang 2026-11-06 06:43 令：同一谱子再用 SoulX 与 VocalRender 各生成切片比较。本轮只做阶段 1b（SoulX SVC 第二段＋VocalRender-Pro 直出），不碰第一阶段已交付的 9 份 DiffSinger 切片。

## 结论

- 同 3 段切片 × 2 个引擎，共 6 份音频，全部落盘。
- SoulX SVC：3 片全部通过烟囱门。检出率 93.3–93.8%（每片漏 1 音），偏差中位 −0.04／−0.03／+0.02 半音，|偏差| p90 0.08–0.14 半音。相对 DiffSinger 源音频（绮萱）几乎不引入音高误差。
- VocalRender-Pro（warp 后量测）：A、B 通过烟囱门，C 未通过（检出率 73.3% < 80%）。偏差中位 A +0.38、B −0.01、C −0.01 半音；|偏差| p90 0.73–1.78 半音，尾部明显宽于 SoulX。
- VR 时长：原始渲染比谱面短 1.9%／7.0%／2.9%（A／B／C），均匀拉伸到谱面跨度后量测。短句压缩远小于凛冬整段的 −53.7%／−19.8%，与此前短句 +6.7%／−3.0% 的结论一致。
- 试听判断只由耳验定。本报告只写数字，不写听感判断。

## 切片定义

谱子：`~/workspace/song-shiguang-20261102-v3/.work/score_v3.json`，208 音，MIDI 51–68，96 BPM，普通话。与阶段 1 同一切片窗口、同烟囱门、同交付格式（44.1 kHz，16-bit，单声道，峰值归一 0.89）。

| 切片 | 窗口 | 音数 | 歌词 | 音区 |
|---|---|---|---|---|
| A（开头） | 0.50–10.94 s | 16 | 假如时光能够倒流我要回到那个路口 | MIDI 56–63 |
| B（高音版同句） | 51.85–63.20 s | 16 | 假如时光能够倒流我要回到那个路口 | MIDI 61–68 |
| C（桥段） | 103.77–113.82 s | 15 | 那些未说的话是否还是没勇气开口 | MIDI 59–66 |

## 量测表（pyworld dio 独立测 f0，与谱面比；与阶段 1 同法）

烟囱门：检出率 ≥80%，有声帧占比 ≥50%，|偏差中位| ≤0.5 半音。量测用 `measure_bank.py`（dio f0_floor 27 Hz，f0_ceil 800 Hz，帧 5 ms，stonemask；每音取中部 50% 帧，中位 f0 → MIDI；检出音 = 有声帧占比 ≥50% 且 |偏差| < 2 半音）。

| 切片 | 引擎 | 检出 | 偏差中位 | \|偏差\| p90 | ±0.5 内占比 | 有声帧占比 | 烟囱门 |
|---|---|---|---|---|---|---|---|
| A | SoulX SVC（zh_prompt） | 15/16 | −0.040 | 0.085 | 100% | 91.6% | 通过 |
| B | SoulX SVC（zh_prompt） | 15/16 | −0.026 | 0.084 | 100% | 86.0% | 通过 |
| C | SoulX SVC（zh_prompt） | 14/15 | +0.017 | 0.135 | 93.3% | 87.0% | 通过 |
| A | VocalRender-Pro（warp 后） | 15/16 | +0.376 | 0.817 | 50.0% | 88.4% | 通过 |
| B | VocalRender-Pro（warp 后） | 15/16 | −0.011 | 0.734 | 87.5% | 96.0% | 通过 |
| C | VocalRender-Pro（warp 后） | 11/15 | −0.012 | 1.775 | 66.7% | 79.7% | 未通过 |

对照：阶段 1 DiffSinger 绮萱源音频——A 16/16，偏差中位 +0.002，p90 0.077，±0.5 内 100%，有声 96.0%；B 16/16，−0.000，0.082，100%，93.2%；C 15/15，−0.004，0.086，100%，87.5%。

## 单音异常（已逐一核对原始文件，非猜测）

SoulX 未检出 3 音：
- A “那”（start 8.221 s）：窗口内有声帧占比 0.091，近乎无声。其余单音偏差最大 −0.117 半音（“路”）。
- B “能”（start 54.539 s）：有声帧占比 0.452，刚低于 50% 阈值。其余单音偏差最大 −0.258 半音（“回”，MIDI 68，最高音）。
- C “那”（start 103.775 s，片首音）：SVC 输出文件开头约 0.3 s 含 30–48 Hz 低频瞬态，中位 f0 55.73 Hz，偏差 −27.77 半音。阶段 1 同音在源音频中检出正常，瞬态由 SVC 段引入，只影响片首音的量测窗口。

VocalRender-Pro 未检出 5 音（warp 后量测）：
- A “口”（start 9.983 s，片尾音）：warp 后中位 f0 74.0 Hz。原始文件同窗口中位 221.1 Hz（谱面 MIDI 57 = 220 Hz，偏差 +0.09 半音），唱准了；74 Hz 是均匀拉伸（phase-vocoder）在尾音引入的伪影，不是 VR 的错。
- B “如”（start 52.33 s）：warp 后中位 f0 60.1 Hz。原始文件同窗口中位 312.1 Hz（谱面 MIDI 63 = 311 Hz，偏差 +0.05 半音），唱准了；60 Hz 同样是拉伸伪影。
- C “是”（start 107.966 s）、“开”（start 112.044 s）：原始文件同窗口有声帧占比 0.000，两音在原始文件里也晚到约 0.3 s，不是拉伸造成的。这是 VR 本身的时值漂移（见下节）。
- C “没”（start 109.628 s）：偏差 −2.089 半音；C “气”（start 110.987 s）：有声帧占比 0.457。均在 C 片尾部时值漂移区内。

修正后的 VR 检出（去掉两处已证实的拉伸伪影 A-口、B-如，用原始文件实测偏差代入）：A 16/16，偏差中位 +0.376，p90 0.77，±0.5 内 56.2%；B 16/16，偏差中位 +0.073，p90 0.442，±0.5 内 93.8%；C 仍为 11/15，未通过。修正只改检出数与 ±0.5 占比，不改中位与 p90 的量级。

VR 其余大偏差（已核原始文件，真实存在）：
- A “流” +0.823、“倒” +0.810（均为 MIDI 59）：原始文件同窗口中位 259.2 Hz，与 warp 后 259.0 Hz 一致，VR-A 在这两个音上确实唱高约 0.8 半音；这是 A 片偏差中位 +0.376 的主要来源。
- B “时” +1.015（MIDI 64）。
- C “是”（start 109.184 s）+1.779、“勇” +1.746：窗口落在时值漂移区，测到的是邻音音高，不是 VR 唱错一个音高；根因仍是时值漂移。

## VR 时值（原始渲染，未 warp）

| 切片 | 原始时长 | 谱面跨度 | 压缩 | warp 比（目标／原始） |
|---|---|---|---|---|
| A | 10.24 s | 10.433 s | −1.92% | 1.0195 |
| B | 10.56 s | 11.347 s | −6.96% | 1.0748 |
| C | 9.76 s | 10.035 s | −2.89% | 1.0297 |

warp 方法：整文件均匀 librosa phase-vocoder 拉伸到谱面跨度（保音高），再重采样 44.1 kHz、峰值归一 0.89。均匀拉伸只对齐总跨度，不对齐逐音位置：C 片尾部音符在原始文件里已晚约 0.3 s（“是”“开”实测），拉伸后仍落在窗口外。A-口与 B-如的量测失败是拉伸伪影（上节已证），不是 VR 的错；但拉伸本身会污染边界音窗口，今后 VR 对比应优先用逐音对齐或直接量测原始文件。

SoulX 时值：SVC 保源音频时值，输出时长与源音频逐位相同（A 10.99 s，B 11.90 s，C 10.60 s），无需 warp。

## 引擎与参数

SoulX SVC：
- 仓库 `Soul-AILab/SoulX-Singer`（depth-1 克隆），3 个 patch 全部应用（00 llama-config-kwargs、01 record-f0-shift、02 cli-seed）。
- 权重 `Soul-AILab/SoulX-Singer`：model.pt 2,818,092,278 字节、model-svc.pt 2,793,965,154 字节，均已核对；预处理权重 `Soul-AILab/SoulX-Singer-Preprocess`。
- 目标音色：`zh_prompt.wav`——时光工程自带的 SoulX prompt，女声，唱“除了想你你 除了了爱你你 我什么什么都愿意”。未用任何金属 prompt（bard_growl、bard_scream、brandon、von、groupyell 均未用）。
- 源音频：阶段 1 绮萱切片 wav（`stage1_shiguang/sliceA|B|C/`）。prep：Mandarin，vocal_sep False，max_merge_duration 30000，midi_transcribe False。SVC：pitch_shift 0，fp16。3 片首轮全部成功。
- 输出 24 kHz，峰值原始 0.1535／0.1917／0.1895（A／B／C），重采样 44.1 kHz 后峰值归一 0.89。

VocalRender-Pro：
- 仓库 `pymaster17/VocalRender`（depth-1 克隆），seed patch `03-vocalrender-cli-seed.patch` 已应用；推理用 Pro 检查点，不用 base。
- 检查点 `pymaster/VocalRender` snapshot `c2ba9d06f39fa0731b96f0258d3df33cc1bda35a`，`VocalRender-Pro/`：model.safetensors 9,163,382,376 字节，audiovae.pth 376,951,122 字节。
- prompt：`prompt_zh_24k.wav`（zh_prompt.wav 48 kHz → 24 kHz 单声道），与 SoulX 用同一女声。label：`vr_labels/vr_sliceA|B|C.json`（bpm 89／98／99，17 个 token 含 SP 休止；BPM 已按量化跨度与谱面跨度偏差 <0.4% 校准）。seed 11。3 片首轮全部成功（依赖补装后）。
- 输出 48 kHz，峰值原始 0.546／0.8366／0.5106（A／B／C）；warp＋重采样 44.1 kHz 后峰值归一 0.89。

## 试听文件

- `soulx_slices.zip`（2,499,503 字节，SHA256 99563eb571bf8c26ae5ec75b1ca7e8c27bf285c513461ee38fe55c574e2f57bf）：SoulX 3 切片 wav＋sidecar＋量测 JSON。
- `vr_slices.zip`（2,542,589 字节，SHA256 da196f3f64ddd43c6118e5f4b89d8c41c10cc9ffc6101abcd906ce2b7fa1f839）：VocalRender-Pro 3 切片 wav＋sidecar＋量测 JSON。

试听链接：未上传。需父 agent 跑 remote-storage 上传取链接：
`/opt/hatch/bin/remote-storage upload-file --path ~/workspace/tmp-wt-lingren-diffsinger-svc/phase2/shiguang-slices/<soulx|vr>_slices.zip`
链接过期时间以上传返回的 expires_at 为准，届时补进本节。

## 方法与复现

- 会话：Colab `ds-svc-l4`（L4，GPU 空闲 0 MiB 占用时建）。环境脚本 `svc-colab-setup.sh`（共享脚本，venv torch 2.10.0+cu128，§6 自检通过）；会话内全部 python 调用均用 venv python（`/content/lindong_venv/bin/python`）。
- 驱动脚本（会话内）：`/content/run_sg1b.sh`（环境＋SoulX＋VR 主驱动）、`/content/run_sg1b_fix1.sh`（pydantic 补装）、`/content/run_sg1b_fix2.sh`（datasets 补装＋VR 重跑）、`/content/run_sg1b_post.sh`（重采样／归一／warp／量测）、`/content/run_sg1b_diag*.sh`（异常核对）。会话内产物：`/content/sg1b_out/`（6 份原始 wav）、`/content/sg1b_final/`（6 份交付 wav＋量测 JSON＋vr_warp.json）。
- 过程修正（两处，均已验证）：
  - VR 推理缺 `pydantic`：`vocalrender/model/voxcpm.py` import `pydantic.BaseModel`，venv 无此包，3 片两轮全部 `ModuleNotFoundError`。补装 pydantic 2.14.3 后 import 链通过。
  - VR 推理缺 `datasets`：`vocalrender/training/svs_data.py` import `datasets.Dataset`，3 片再次全部失败。补装 `datasets>=3,<4`（实装 3.6.0）＋tensorboard＋matplotlib 后，3 片首轮全部成功。两包均为 VR `pyproject.toml` 声明的核心依赖，但共享环境脚本的 VR 依赖清单里没有——待父 agent 折入 `svc-colab-setup.sh`。
- 量测 t0：SoulX 与阶段 1 相同（A 0.45，B 51.80，C 103.72，wav 第 0 采样对应谱面时间）；VR 交付文件 t0 = 窗口起点（A 0.50，B 51.85，C 103.77），因 VR 输出从首音起音开始、无 0.05 s 前导。
- 报告方法：主表为 warp 后量测（与任务 brief 要求一致）；A-口、B-如两处拉伸伪影与 C 片尾部时值漂移均用原始文件＋stonemask dio 复核（正确映射：warp 文件 file_time = score − t0；原始文件 file_time = (score − t0)／warp 比），结论见“单音异常”一节。

## 来源与许可

- SoulX-Singer：`Soul-AILab/SoulX-Singer`（GitHub）。权重 `Soul-AILab/SoulX-Singer` 与 `Soul-AILab/SoulX-Singer-Preprocess`（Hugging Face）。
- VocalRender：`pymaster17/VocalRender`（GitHub）；检查点 `pymaster/VocalRender`（Hugging Face）。
- 谱子：《时光》score_v3。只作本地实验，不分发。Wang 2026-11-05 18:33 定：不考虑协议和授权作排除条件。只做实验，不发布，不涉及商业。来源与许可信息如实记录，发布前重核。

## 文件清单

本目录：REPORT-shiguang-slices-soulx-vr.md（本报告），REPORT-shiguang-slices.md（阶段 1 报告），shiguang_slices.json（转换后谱子），vr_labels/（VR label 3 份＋sidecar），soulx_slices.zip／vr_slices.zip（交付包），slice*_shiguang_stage1b_soulx|vr.{json,wav,_measure.json}（18 件原件）。
