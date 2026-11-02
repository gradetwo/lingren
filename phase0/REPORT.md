# SVS Phase 0 实测报告（2026-11-02）

执行员：Phase 0 实测（Colab GPU）。目标：跑通两模型官方 demo，量 RTF、SoulX 输出采样率与时钟偏差、VocalRender 对齐可测性、两模型 takes 门（同乐谱同音色换 seed / 换遍，逐对验差异）。
结论先写在前：两模型官方 demo 全部跑通；SoulX 作默认、VocalRender 作音质选项的选型成立；takes 门两模型都通过字面要求，但差异性质不同（见 §5）。

## 1. 实测总表（全部实测数，不抄 README）

| 模型 | GPU | 精度 | 合成 RTF | 输出采样率 | 时钟 | 对齐 | takes 门 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SoulX-Singer | NVIDIA L4（23 GB） | fp16（mel 保持 fp32，官方 demo 默认） | **0.51–0.53**（合成 25.7–26.8 s / 音频 51.0 s；模型加载另计 12.2 s） | **24,000 Hz** 单声道 PCM_16（读 WAV 头实测） | 见 §3：全局起点准（1 ms）；音符边界中位绝对偏差 70 ms，系统性 +43 ms 可由 pyin 2 帧延迟解释 | 不适用（乐谱时钟直接贴轨） | 通过（字面门，见 §5） |
| VocalRender（base 检查点，非 Pro） | NVIDIA L4（23 GB） | bf16（官方 demo 默认） | 生成 **1.11–1.18**（慢于实时；生成 6.8–7.0 s / 音频 5.9–6.2 s；模型加载另计 19.9 s；首跑端到端 37.7 s） | **48,000 Hz** 单声道 PCM_16（读 WAV 头实测，与 README 标称一致） | 模型自定时值：总时长与乐谱推算时长偏差 ±5% 以内；无逐音节乐谱时值可对照 | 部分可行（见 §4：能量起点法找回 11/15 个音节） | 通过（字面门，见 §5；差异比 SoulX 大） |

注：RTF = 合成墙钟时间 / 输出音频时长。SoulX 的 RTF 不含模型加载；VocalRender 首跑端到端 RTF 6.20（含 19.9 s 加载 + 0.8 s prompt 编码）只作冷启动参考，不作选型依据。

## 2. 环境与复现路径

- 会话：Colab `svs-phase0-20261102`，L4，torch 2.11.0+cu130，Python 3.13。
- SoulX：仓库 Soul-AILab/SoulX-Singer（2026-11-02 克隆），权重 `model.pt` 2,818,092,278 字节（HF 下载 19.4 s），官方 demo `example/infer.sh` 同参数（score 模式、zh_prompt、music.json、fp16、auto_shift）。
- VocalRender：仓库 pymaster17/VocalRender（2026-11-02 克隆），权重 `VocalRender/*` 8.9 GB（6 文件），官方 demo `scripts/infer_vocalrender_svs_single.py --item_name 2003000087 --prompt_audio examples/prompt_audio/2003000081.wav`（官方自带 prompt，6.17 s，无需替代）。
- 环境修正（全部记下来，不藏）：
  - SoulX 仓库 pin transformers==4.41.2，但在 Python 3.13 上 tokenizers 旧版无轮子、源码编译失败；改用 transformers==4.46.3 + tokenizers==0.20.3，可导入，demo 跑通。
  - SoulX `llama.py:132` 的 `LlamaConfig(0, 256, 1024, 1, 1)` 位置参数在新 transformers 上被拒；改成同值关键字参数（vocab_size=0, hidden_size=256, intermediate_size=1024, num_hidden_layers=1, num_attention_heads=1），数值不变，模型行为不变。原文件备份在会话内 /tmp（会话已 stop，备份不保留；改动仅此一行）。
  - VocalRender 推理只需 argbind 一个补装；s3prl / audiobox-aesthetics（评估指标用）未装，推理脚本不 import 它们。
- 费用：见 §7。

## 3. SoulX-Singer 细项

### 3.1 官方 demo 输出

- 输入：1 段，Mandarin 歌词（《只是因为在人群中多看了你一眼》选段），86 音素、86 时值、乐谱区间 240–51,240 ms（51.0 s）。
- 输出：`generated.wav`，51.24 s（含 240 ms 前置偏移，demo 代码把段音频按 `start_sample_idx` 贴进整段），峰值 0.725，RMS 0.098。
- 合成时间：官方 CLI 26.75 s（tqdm 实测）；驱动内三次 26.66 / 25.72 / 25.86 s。RTF 0.51–0.53。
- 模型加载：驱动内 12.2 s（热缓存；首跑冷加载更长，未单独计时）。

### 3.2 时钟偏差（方法 + 结论，含一次方法证伪）

- 全局锚点（可靠）：首个高于 -40 dBFS 的能量点 0.241 s，对乐谱起点 0.240 s 偏差 1 ms；末能量点 51.064 s，对乐谱终点 51.240 s 差 -176 ms（尾音节自然衰减，属正常收尾）。
- 音素级能量起点法（已证伪，不作结论）：对 77 个非静音音素，在乐谱起点 ±（-150 ms / +350 ms）窗内找最大 log 能量跳变，得平均绝对偏差 147.5 ms、中位 110 ms。但同一方法跑在官方 prompt 真人录音（已知时值）上也得到平均 156.3 ms、中位 140 ms——说明该方法对连贯歌声有约 150 ms 的晚偏（元音稳态能量跳变晚于音素边界），不是模型时钟误差。此法作废。
- 音符边界法（采用）：pyin 提 F0 → MIDI → 中值滤波 → 稳定变音点，对乐谱 66 个音高变化边界，64 个在 ±0.5 s 内匹配。平均绝对偏差 103.7 ms，中位绝对偏差 70 ms，平均带符号偏差 +42.8 ms。+43 ms 系统性滞后与 pyin 2 帧延迟（hop 512/24 kHz = 21.3 ms）量级一致，扣除后残差约 30–50 ms 量级。结论：SoulX 音符时钟跟乐谱在几十毫秒量级，DAW 贴轨可用；逐音素级精度本方法测不出，不宣称。
- auto_shift 说明：它是音高移调（prompt 与目标中位音高对齐），不是时间移位；不影响时钟结论。

### 3.3 同 voice 不同音高（和声可行性，顺带验证）

- 方法 A（pitch_shift 旋钮）：同乐谱同 prompt，seed 0，`pitch_shift=4`（auto_shift 关）合成一遍，与 auto_shift 开的 seed 0 遍比 F0 中位。结果：合成 26.52 s；F0 中位 379.7 Hz → 404.6 Hz，只抬了 +110 音分，远不到 +400 音分。结论：pitch_shift 旋钮不能用来做和声部，不要走这条路。
- 方法 B（独立乐谱，干净对照）：同 prompt 同 seed 0，auto_shift 关，乐谱 note_pitch 原样 vs 全 +4 半音（休止 0 不动），各合成一遍。结果：score0 合成 26.52 s、F0 中位 312.0 Hz；score4 合成 25.79 s、F0 中位 404.6 Hz，抬了 +450 音分（≈ +4 半音，中位法本身有约 ±50 音分 slack）。结论：和声部走独立乐谱路线成立——每个 part 写自己的 note_pitch、同 voice_id 合成，音高跟得上。
- 附带发现（auto_shift 的副作用）：同一 seed 0、同一乐谱，auto_shift 开（take_seed0）F0 中位 379.7 Hz，关（harmony_score0）312.0 Hz，差 +340 音分。auto_shift 会把输出音高往 prompt 歌手的音域拉（设计使然：prompt 与目标中位音高对齐）。DAW 里要乐谱绝对音高时，必须关 auto_shift 或把它的移调量记进回执，否则和声部音高会被 prompt 帶偏。

## 4. VocalRender 细项

### 4.1 官方 demo 输出

- 输入：item 2003000087，18 词（15 个实词 + SP），21 音符，bpm 58，官方 prompt 音频 6.17 s。
- 输出：`vr_out1.wav`，48,000 Hz 单声道 PCM_16，6.08 s，峰值 0.9000（官方脚本 normalize_audio 归一化到 0.9，峰值不能用于区分各遍）。
- 生成时间：驱动内三遍 6.98 / 6.84 / 6.95 s；加载 19.9 s；prompt 编码 0.8 s。生成 RTF 1.11–1.18（慢于实时）。3 分钟歌按此推算生成约 3.3 分钟，服务必须走异步任务（设计已是任务 API，无需改设计）。
- F0 范围校验：输出 F0 中位 299.7 Hz，p10 231.1 Hz，p90 467.5 Hz；乐谱音高 58–70 MIDI = 233–466 Hz。范围逐位吻合，说明唱对了音。

### 4.2 时值：模型自定，但总量贴合乐谱

- demo 乐谱没有逐音节时值字段（word_dur / pitch_dur 官方文档明说只是可视化/评估元数据，demo json 里根本没有），所以“与乐谱逐音节偏差”对 VocalRender 无定义，不硬算。
- 可算的是总量：21 个音符时值（16 分音符 0.25 拍、32 分 0.125、附点 32 分 0.1875、附点 8 分 0.75、附点 16 分 0.375、8 分 0.5）合计 6.0 拍，bpm 58 → 乐谱推算 6.207 s。三遍实测 5.92 / 6.08 / 6.24 s，偏差 -4.6% / -2.0% / +0.5%，在 ±5% 内。结论：模型自定时值总量贴合乐谱，逐音节分布未知。

### 4.3 对齐可测性（能否从输出音频量出每音节起点）

- 方法：log-RMS 包络（20 ms 窗、10 ms 跳）+ 能量跳变（30 ms 窗），>8 dB 且局部极大、最小间隔 150 ms。
- 结果：找回 11 个强起点（0.07 / 1.38 / 1.69 / 2.91 / 3.28 / 3.52 / 3.93 / 4.17 / 4.43 / 4.99 / 5.81 s），对 15 个实词音节是 73%。间隔 0.24–0.37 s（≈16 分音符 0.259 s）、0.56 s（≈8 分音符 0.517 s），结构合理；但 4 个音节无强起点（连贯/弱起音）。
- 结论：部分可行。能量法可作对齐回执的初版，但不能单独作生产依据；Phase 1 需要更好的方法（F0 + 能量联合分段，或音素强制对齐），否则回执会丢约 1/4 的音节起点。

## 5. takes 门（批量硬性要求：每遍互不相同）

门的三项按 Wang 定的口径逐对报：是否逐位相同、相关度、起点偏差。注意：原始波形样本级相关度对相位/微时移极度敏感（同内容差 10 ms 就能跌到 0.02），不是可用相似度指标；下表用包络相关度（log-RMS 包络）+ F0 + 起点偏差作判断，样本级相关度只附注。

### 5.1 SoulX：同乐谱同 prompt，seed 0/1/2（三遍，torch 全局 RNG 设 seed；CLI 本身没有 --seed 参数）

| 遍对 | 逐位相同 | 包络 log-RMS 相关度 | F0 中位绝对差 | F0 在 50 音分内占比 | 起点中位绝对差 |
| --- | --- | --- | --- | --- | --- |
| 0-1 | 否（98% 采样不同） | 0.944 | 20 音分 | 86.8% | 25 ms |
| 0-2 | 否 | 0.944 | 10 音分 | 88.6% | 30 ms |
| 1-2 | 否 | 0.950 | 10 音分 | 87.1% | 30 ms |

- 另：三遍合成时间 26.66 / 25.72 / 25.86 s，峰值 0.548 / 0.552 / 0.493（各不相同）。
- 结论：通过字面门。seed 变化给出真实微差：音高 ±10–20 音分中位、起点 ±25–30 ms 中位、包络形状共享（0.94）。但三遍共享同一时值/力度骨架——是同一表演的不同渲染，不是三次独立表演。叠放不会只变响（相位不同，有真实合唱/厚度效应），但也别指望它像真人三遍那样各处不同。

### 5.2 VocalRender：同乐谱同 prompt，连续三遍（无 seed 参数，靠 RNG 自然推进；官方暴露的采样参数是 temperature / fsq_temperature / cfg / inference_timesteps，本次用默认值 temperature=1.0）

| 遍对 | 逐位相同 | 长度 | 包络 log-RMS 相关度 |
| --- | --- | --- | --- |
| take0-take1 | 否 | 5.92 s vs 6.08 s | 0.555 |
| take0-take2 | 否 | 5.92 s vs 6.24 s | 0.442 |
| take1-take2 | 否 | 6.08 s vs 6.24 s | 0.648 |

- 结论：通过字面门，且差异比 SoulX 大：长度本身就差 0.16–0.32 s（AR 模型自己决定何时结束，长度按约 0.16 s 量化），包络相关度只有 0.44–0.65。叠放是更松的 double；comping 跨遍拼句时各遍时值分布不同，Yeban 侧要按各遍实测起点贴，不能按同一乐谱时值拼。

### 5.3 建议的 Phase 1 服务侧验差异门（由本次实测数据定）

逐对验三项，任一硬失败即换 seed 有界重试，仍失败则任务响亮失败，绝不把相同音频当多遍返回：

1. 逐位相同 = 否（硬门；本次两模型全否）。
2. 包络 log-RMS 相关度 < 0.99（SoulX 0.94、VocalRender 0.44–0.65 都过；退化成相同输出时为 1.0，正好拦住）。
3. 微差通道至少一处激活：F0 中位绝对差 > 5 音分，或起点中位绝对差 > 10 ms（SoulX：F0 10–20 音分、起点 25–30 ms，全激活；VocalRender：长度与包络差异激活）。
4. 不用样本级波形相关度作门（相位敏感，会误杀真微差遍）。

## 6. Mac MPS 测试计划（本 VM 是 Linux，未实测，不编造）

在 Wang 的 Mac 上跑，目标：SoulX 的 MPS RTF；触发条件：MPS RTF > 2 则启动 MLX 二期（设计 §7 既定）。

```bash
# 前置：Mac 上装好 Python 3.11+、torch（MPS 版）、SoulX 仓库与权重（同 §2 路径）
cd SoulX-Singer
# 1. 确认 MPS 可用
python3 -c "import torch; print(torch.backends.mps.is_available(), torch.backends.mps.is_built())"
# 2. 同官方 demo 参数、device 改 mps，计时（合成段 + 加载分开记）
time python3 -m cli.inference --device mps \
  --model_path pretrained_models/SoulX-Singer/model.pt \
  --config soulxsinger/config/soulxsinger.yaml \
  --prompt_wav_path example/audio/zh_prompt.mp3 \
  --prompt_metadata_path example/audio/zh_prompt.json \
  --target_metadata_path example/audio/music.json \
  --phoneset_path soulxsinger/utils/phoneme/phone_set.json \
  --save_dir /tmp/soulx_mps --auto_shift --pitch_shift 0 --fp16
# 注意：若 --device mps 被 demo 代码拒绝（只处理 cuda/cpu 分支），改走驱动脚本：
# 把 soulx_seed_driver.py 里的 "cuda" 换成 "mps"、use_fp16 保持 True，单 seed 跑一遍，记 synth_s。
```

要量的数（与本次口径一致）：

| 项 | 怎么量 | 达标/触发 |
| --- | --- | --- |
| MPS 可用性 | torch.backends.mps.is_available() | False 则 MPS 路线不成立，记为阻塞 |
| 模型加载时间 | 驱动内 build_model 计时 | 只作参考 |
| 合成 RTF | synth_s / 51.0 s（同段 music.json） | RTF > 2 → 启动 MLX 移植（先 SoulX）；RTF ≤ 2 → MPS 继续 |
| 输出采样率 | WAV 头（应为 24,000 Hz） | 与本次 L4 结果对照 |
| 回退项 | 若 MPS 有算子不支持（如某些 biquad/vocoder 算子），记下算子名与改 CPU 回退的损失 | 如实记，不绕过 |

VocalRender 的 MPS 不在本轮必测（设计里 VocalRender 在 Mac 上是“慢但可用”的次要路径；若顺带测，用同 demo 命令 --device mps，记生成 RTF 即可）。

## 7. 花费与证据

- Colab 余额：开始 530.19 CU，结束 529.41 CU，花掉 0.78 CU。会话已 stop（亲验无活动会话）。会话内下载：SoulX 权重 2.6 GB、VocalRender 权重 8.9 GB（会话盘随会话死，下次重下；设计 §7.4 的 Drive 缓存是 Phase 1 要做的事）。
- 证据文件：`audio/`（SoulX generated.wav、take_seed0/1/2.wav、harmony_shift4.wav、harmony_score0.wav、harmony_score4.wav；VocalRender vr_out1.wav、vr_take0/1/2.wav）+ `logs/`（soulx_cli.log、soulx_seed.log、soulx_harmony.log、soulx_harmony2.log、vr_run1.log、vr_driver.log）。
- 关键日志原句（SoulX）："Model parameters: 704.344578 M"、"Inferring segments: 100% | 1/1 [00:26, 26.75s/it]"、"Generated audio saved to /content/soulx_out/generated.wav"；（VocalRender）："Running on device: cuda, dtype: bfloat16"、"[SVS Single] Saved: /content/vr_out1.wav (6.08s)"、"WALL_S 37.709011078"。
- 未完成项：无。Mac MPS 是待办（§6），不是缺口。
