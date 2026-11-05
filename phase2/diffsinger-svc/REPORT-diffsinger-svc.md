# DiffSinger＋SVC 两阶段路线研究报告

- 日期：2026-11-05
- 分支：`lingren-diffsinger-svc-twostage`（基底远端 main 405dbf2）
- 谱子：V4 `winter_metal.yeban` 的 5 条人声轨，共 685 音
- 结论只写实测数，不写听感判断

## 结论

- 两阶段管线全程跑通。5 轨各出 3 份音频，共 15 份，全部落盘。
- 第一阶段只在 1 轨上真正成立。vox3（黑嗓高八度，音高 MIDI 45–58）音高正确。
- 第一阶段在其余 4 轨上失败。vox、voxTel、vox2、vox4 的音高为 MIDI 23–46，远低于童声声库的训练音区。声学模型输出的 mel 不含清晰低音谐波。渲染音频无明确基频。
- 第二阶段不能修复第一阶段。两份 SVC 输出继承了第一阶段的缺失。4 条低音轨的两份 SVC 版音高均偏离谱面。
- 瓶颈在声学模型，不在声码器。对照实验证明声码器可正确渲染 73.42 Hz。
- 两个 SVC 的行为差别真实存在。SoulX 不自动移调。YingMusic 自动移调，vox2 与 vox4 被上移 12 个半音（一个八度）。

## 交付物（15 份）

- 目录：`phase2/diffsinger-svc/outputs/`
- 第一阶段：44.1 kHz，16-bit，峰值归一 0.89
- SoulX 第二阶段：48 kHz，16-bit，峰值归一 0.89（原生 24 kHz）
- YingMusic 第二阶段：44.1 kHz，16-bit，峰值归一 0.89（原生 44.1 kHz）
- 每份音频旁有同名 `.json` sidecar，含时长、峰值、RMS、移调量、耗时

| 轨 | 第一阶段 DiffSinger | 时长 | 第二阶段 SoulX SVC | 时长 | 第二阶段 YingMusic-SVC | 时长 |
|---|---|---|---|---|---|---|
| vox（主唱，335 音） | `vox_stage1_diffsinger.wav` | 179.44 s | `vox_stage2_soulx-svc.wav` | 179.44 s | `vox_stage2_yingmusic-svc.wav` | 179.43 s |
| voxTel（电话滤波低语，99 音） | `voxTel_stage1_diffsinger.wav` | 45.68 s | `voxTel_stage2_soulx-svc.wav` | 45.68 s | `voxTel_stage2_yingmusic-svc.wav` | 45.67 s |
| vox2（齐吼，151 音） | `vox2_stage1_diffsinger.wav` | 166.84 s | `vox2_stage2_soulx-svc.wav` | 166.84 s | `vox2_stage2_yingmusic-svc.wav` | 166.84 s |
| vox3（黑嗓高八度，46 音） | `vox3_stage1_diffsinger.wav` | 158.52 s | `vox3_stage2_soulx-svc.wav` | 158.52 s | `vox3_stage2_yingmusic-svc.wav` | 158.52 s |
| vox4（暴民合唱低层，54 音） | `vox4_stage1_diffsinger.wav` | 141.40 s | `vox4_stage2_soulx-svc.wav` | 141.40 s | `vox4_stage2_yingmusic-svc.wav` | 141.40 s |

- SHA256（前 16 位）：vox 第一阶段 206a0d6833358cff，SoulX 18c812d6013ec04f，YingMusic 53dede6f6a3808e3；voxTel 第一阶段 00ea78a8fc7d8bd6，SoulX 629d17e910a61e7b，YingMusic b58f0daae60873be；vox2 第一阶段 53c2e906b6bd3c2d，SoulX 705270be82b6c74c，YingMusic eb8820d1f1543b6d；vox3 第一阶段 99261cb2f14ff9a9，SoulX 4fa9d5bbf21dcaf3，YingMusic e286149a247f3611；vox4 第一阶段 0a6858ade6c18ae0，SoulX f39a1218c3156126，YingMusic 4db530c8849b2ee4

## 第一阶段量测

- 声库：绮萱 Qixuan v2.7.0（ONNX，童声女声，中英混杂）
- 声码器：`pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.onnx`
- 参数：steps 20，depth 0.6，expr 1.0，gender 0，velocity 取音符 velocity/127，时长策略 `--dur score`（dsdur 预测时长按谱面重缩放）
- 模型内音高偏差（pitch_pred 与谱面比，每句中位数的中位）：vox +0.170 半音，voxTel +0.212 半音，vox2 +0.154 半音，vox3 +0.159 半音，vox4 +0.195 半音。模型自认音高基本正确。
- 独立 f0 检测（pyworld dio＋stonemask，对渲染音频测，与谱面比）：

| 轨 | 检出音数 | 偏差中位 | \|偏差\| p90 | 超 ±0.5 半音占比 | 有声帧占比 |
|---|---|---|---|---|---|
| vox | 57/335 | +1.216 半音 | 14.605 半音 | 91.2% | 3.6% |
| voxTel | 15/99 | +5.678 半音 | 15.576 半音 | 80.0% | 2.8% |
| vox2 | 21/151 | +1.904 半音 | 18.488 半音 | 95.2% | 2.3% |
| vox3 | 45/46 | −0.022 半音 | 1.223 半音 | 22.2% | 7.1% |
| vox4 | 20/54 | +1.393 半音 | 2.795 半音 | 90.0% | 2.1% |

- 读法：模型内偏差小，但渲染音频的独立检测只在 vox3 上通过。4 条低音轨的有声帧占比仅 2–4%，检出音数少，偏差达数个半音至十余半音。谱分析证实低音轨无清晰谐波列（voxTel 第 1 音 D2，期望 73.4 Hz，自相关置信度 0.36，谱峰散布在 204–538 Hz，无 73 Hz 谐波）。vox3 第 1 音（期望 174.6 Hz）自相关 183.8 Hz，置信度 0.66，谱峰 182.4、357.3、541.0 Hz 呈清晰谐波列。
- dsdur 预测时长比谱面长约 1.3 倍（各轨中位 1.26–1.36）。童声声库自然唱得慢。`--dur score` 已把时长压回谱面。
- 原始峰值很低（0.065–0.157），已归一到 0.89。归一前后的峰值记在 sidecar 内。

## 瓶颈定位（对照实验）

- 声码器对照：喂干净的 73.42 Hz 谐波 mel＋f0 73.42 Hz，声码器输出自相关 f0 73.5 Hz，置信度 0.97，RMS 0.8071。声码器在低音区工作正常。
- 音高模型对照：dspitch 预测的 f0 与谱面偏差中位约 +0.2 半音。音高模型工作正常。
- 剩余环节是声学模型。它在训练音区之外（童声约 200–500 Hz，谱面低音 27–117 Hz）输出的 mel 不含低音谐波结构。瓶颈在声学模型。
- 换声库不能直接解决。`voice.md` 所列声库均为女声、童声或中性 Alto，无成年男低音声库。SPORE 为英语 Alto，不能唱中文行。

## 第二阶段量测

- 目标 prompt（与 V4 一致）：vox／voxTel 用 `bard_growl_24k.wav`，vox2／vox4 用 `groupyell_24k.wav`，vox3 用 `bard_scream_24k.wav`。
- 两份 SVC 的原始输出峰值均为 1.0（满幅削波）。已按增益归一到 0.89。增益记在 sidecar 内。

### 音高继承（独立 f0 检测，与谱面比）

| 轨 | SoulX 检出 | SoulX 偏差中位 | SoulX 超 ±0.5 占比 | YingMusic 检出 | YingMusic 偏差中位 | YingMusic 超 ±0.5 占比 |
|---|---|---|---|---|---|---|
| vox | 46/335 | +4.976 半音 | 95.7% | 32/335 | +25.825 半音 | 100.0% |
| voxTel | 40/99 | +10.371 半音 | 100.0% | 14/99 | +24.984 半音 | 100.0% |
| vox2 | 21/151 | +3.629 半音 | 95.2% | 27/151 | +7.628 半音 | 88.9% |
| vox3 | 37/46 | −0.031 半音 | 24.3% | 42/46 | −0.058 半音 | 26.2% |
| vox4 | 10/54 | +6.357 半音 | 90.0% | 10/54 | +9.315 半音 | 100.0% |

- 读法：只有 vox3 的两份 SVC 版保住了谱面音高。4 条低音轨的两份 SVC 版音高全部偏离。YingMusic 在 vox 与 voxTel 上偏离约 +25 半音（约两个八度）。原因是第一阶段源无清晰基频，SVC 的 f0 提取（RMVPE）锁定了高次谐波。YingMusic 的自动移调又叠加了上移。
- 有声帧占比仍低。SoulX 低音轨 2.2–18.8%，YingMusic 低音轨 2.5–5.4%。vox3 两份分别为 11.1% 与 8.5%。

### 音色贴合（长期平均谱 log-mel 余弦相似度）

- 数值越高，音色越接近目标 prompt。第一阶段源与 prompt 的相似度接近 0（−0.34–0.07），即源音色与目标无关。

| 轨 | 第一阶段~prompt | SoulX~prompt | YingMusic~prompt | SoulX~第一阶段 | YingMusic~第一阶段 |
|---|---|---|---|---|---|
| vox | 0.062 | 0.145 | 0.497 | −0.535 | 0.747 |
| voxTel | 0.069 | 0.626 | 0.516 | −0.101 | 0.727 |
| vox2 | −0.011 | 0.896 | 0.833 | −0.345 | 0.337 |
| vox3 | −0.342 | −0.198 | 0.761 | 0.761 | −0.276 |
| vox4 | −0.167 | 0.903 | 0.905 | −0.446 | −0.043 |

- 读法：两份 SVC 均把音色向目标推进（10 组对比全部高于第一阶段）。YingMusic 在 5 轨上均匀推进（0.497–0.905）。SoulX 在 vox2 与 vox4 上推进最强（0.896、0.903），在 vox（0.145）与 vox3（−0.198）上推进最弱。vox3 的 SoulX 版与第一阶段源相似度 0.761，即 SoulX 对 vox3 的音色改动最小。LTAS 是全轨平均谱的粗指标，只作方向参考，不作精确音色分。

### 两个 SVC 的实测差别

- 移调：SoulX 固定 pitch_shift 0，不自动移调。YingMusic 自动移调（上限 24 半音）。vox2 与 vox4 各上移 12 半音。vox、voxTel、vox3 移调 0 半音。移调量记在每轨 sidecar 内。
- 音高继承：vox3 上两者相当（偏差中位 −0.031 与 −0.058 半音）。低音轨上两者均失败，YingMusic 偏离更大（vox、voxTel 约 +25 半音，SoulX 为 +5 至 +10 半音）。
- 音色贴合：YingMusic 在 5 轨上均匀（0.497–0.905）。SoulX 两极分化（vox2／vox4 约 0.90，vox／vox3 低于 0.15）。
- 耗时（单轨 SVC 推理，不含模型加载）：SoulX：voxTel 43 s，vox3 46 s，vox 248 s，vox2 233 s，vox4 183 s。YingMusic（扩散 100 步）：voxTel 32 s，vox3 89 s，vox 109 s，vox2 145 s，vox4 123 s。YingMusic 的 RTF 为 0.56–0.87（快于实时）。SoulX 的长轨单段推理慢于 YingMusic（vox 248 s 对 109 s）。
- 采样率：SoulX 原生 24 kHz，本交付上采样到 48 kHz。YingMusic 原生 44.1 kHz，直接交付。

## 耗时总账（L4，23 GB）

- 第一阶段：voxTel 约 15 s，vox＋vox2＋vox3＋vox4 四轨共约 62 s。GPU 扩散步每句约 1 s。第一阶段不是瓶颈。
- 第二阶段 SoulX：5 轨推理共 753 s，另加 8 次预处理（3 个 prompt＋5 个目标）与模型加载。整批约 20 分钟。
- 第二阶段 YingMusic：5 轨推理共 498 s，另加模型加载（含 RMVPE、CAMPPlus、BigVGAN、Whisper-small 首次自动下载）。整批约 15 分钟。
- 第一阶段最快。第二阶段占端到端耗时的绝大部分。

## 下一步建议

- 首选：换一条能唱低音的第一阶段声源。现有声库表内无成年男低音 DiffSinger 声库。需另找男低音声库，或改用其它合成路线出第一阶段干声。
- 备选（不换声库）：第一阶段谱面整体上移 12 半音合成，再把音频下移 12 半音。合成落在声库音区内，移调后音高回谱面。代价是共振峰随之下移，音色变暗。SVC 阶段再施加目标音色。本研究未验证此路线，只列为建议。
- 不建议：继续用绮萱直唱低音轨。4 轨已证实失败。重复跑只会重复同一结论。
- YingMusic 的自动移调须显式控制。vox2 与 vox4 的 +12 半音上移改变了演唱音高。比对时须先读 sidecar 的移调量。需要保音高时，应固定移调为 0 再跑。
- SoulX 在 vox3 上的音色推进弱（LTAS −0.198）。若 vox3 这条线继续做，需查 bard_scream prompt 的预处理与 SoulX 的 f0 提取是否匹配尖叫音色。

## 复现

- 第一阶段驱动：`phase2/diffsinger-svc/scripts/sing_qixuan.py`（Colab L4，onnxruntime-gpu，CUDA provider）。声库与声码器路径、映射文件见驱动参数。谱子用 `dry-vocal-package/lyrics/vocal_scores.json`。
- SoulX 第二阶段：`phase2/diffsinger-svc/scripts/run_stage2_soulx.sh`（lindong venv，torch 2.10.0，`cli.inference_svc`，pitch_shift 0，fp16）。
- YingMusic 第二阶段：`phase2/diffsinger-svc/scripts/run_stage2_yingmusic.py`（ying_venv，Python 3.12，torch 2.4.0，扩散 100 步，fp16）。检查点 `YingMusic-SVC-full.pt` 在 HF `GiantAILab/YingMusic-SVC` 仓库内。RMVPE、CAMPPlus、BigVGAN、Whisper-small 首次运行自动下载。
- 量测脚本：f0 检测 `ds_f0check.py`／`ds_f0s2.py`（pyworld dio＋stonemask），谱分析 `ds_spec.py`，声码器对照 `ds_voctest2.py`，LTAS `ds_ltas.py`。量测脚本在 Colab `/content/` 与本机 `/tmp/`，未全部收入分支。需要时从会话取。

## 环境记录（供复现者避坑）

- Colab 会话 `ds-svc-l4`（L4，23 GB）。绝不用 `colab run -s <已有会话名>`。它开全新 VM 并覆盖本地会话映射，旧 GPU 机变孤儿。跑脚本一律用 `colab exec -f` 或 `colab upload`。
- `svc-colab-setup.sh` 需补一钉：torch 安装带入 setuptools 84.2.1，已删 pkg_resources，pyworld import 失败。会话内已补 `pip install -q "setuptools==80.9.0"`，自检通过。规范脚本仍待落地。
- exec 通道偶发卡住（大输出、GPU 满载时）。卡住超 2 分钟可 `colab restart-kernel`。detached 的 bash 任务不受影响。长活一律 detached 跑、写日志、轮询日志。
- YingMusic 的 requirements 要求 Python ≤3.12（numpy 1.26.4）。本研究另建 Python 3.12 venv（uv），与 lindong venv（Python 3.13）隔离。`uv venv` 默认不带 pip 模块，安装须用 `uv pip install --python <venv python>`。

## 来源与许可

- 绮萱 Qixuan v2.7.0：`yqzhishen/qixuan-diffsinger`，Releases 资产 `Qixuan_v2.7.0_DiffSinger_OpenUtau.zip`。仓库记载明确允许商业使用（需署名并标注 AI 合成）。
- 声码器 `pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.oudep`：openvpi/vocoders Releases。
- SoulX-Singer：`Soul-AILab/SoulX-Singer`，权重经 HF `Soul-AILab/SoulX-Singer` 与 `SoulX-Singer-Preprocess` 下载。
- YingMusic-SVC：`GiantAILab/YingMusic-SVC`，MIT，基于 Seed-VC。检查点 `YingMusic-SVC-full.pt` 在 HF 同名仓库内。
- 谱子与 prompt：V4 `winter_metal.yeban` 的 5 条人声轨与 `dry-vocal-package` 内的 prompt 音频。只作本地实验，不分发。
- Wang 2026-11-05 18:33 定：不考虑协议和授权作排除条件。只做实验，不发布，不涉及商业。来源与许可信息如实记录，发布前重核。
