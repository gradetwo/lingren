# SVS API 四管线设计（PartIn.model 扩展）

日期：2026-11-07。基底：远端 main `405dbf24`。分支：`lingren-svs-four-pipelines`。
定夺来源：Wang 2026-11-07 15:16（四条管线进 `PartIn.model`）、15:17（默认走 YuE2；文档注明 DiffSinger 所在分支）、15:20（YuE2/MuLaCover 之后 SVC 作可选参数，默认不接）。
证据：民谣实验目录 `~/workspace/song-folk-two/work/` 下的 REPORT 与 NOTE（见各节出处）；时光 stage 1b 报告；`phase2/vr-adapter/REPORT.md`。

## 0. 结论与分歧

### 0.1 结论先行

- 管线一共四条，全进已有参数 `PartIn.model`，不新设端点：`soulx`、`vocalrender`、`yue2`、`mulacover`（另有测试用的 `stub`，不变）。
- 默认值由 `'soulx'` 改为 `'yue2'`。不传 `model` 的请求走 YuE2。这是行为变化，见 §2。
- SoulX 适配器行为不变。VocalRender 不再是占位：从 `phase2/service/` 把已闭合的实现原样移植过来（§3）。
- YuE2 与 MuLaCover 新写适配器，沿用子进程模式（模型代码不出 API 进程），默认直出干声（§4、§5）。
- YuE2 与 MuLaCover 之后接 SVC 是可选后处理，`part.options.svc` 开关，默认关闭（§6）。
- 权重与模型文件不进仓库。缺失的必需输入一律大声失败，不静默用替代值，不静默回落到别的管线。

### 0.2 分歧：落点是 phase1 还是 phase2（须父 agent 定）

本设计按 brief 字面落在 `phase1/service/app/`。但亲验发现以下事实，实施前须父 agent 定夺：

- `phase1/service/` 自 Phase 2 起已冻结（`phase2/REPORT.md` 原文：`phase0/` 与 `phase1/` 未动）。
- VocalRender 接线已在 `phase2/service/` 闭合（2026-11-02）：`app/adapters/vocalrender.py`（137 行）+ `app/vr_driver.py`（85 行），12 项本地测试全过，Colab L4 端到端作业 `e1b68cd64b394d9b`，约 140 秒，4/4 遍首试通过。
- `phase2/service/` 还多了转录、vocaledit、SVC prompt（`svc_prompt.py`）等演进，`phase1/service/` 没有这些。
- 但 Docker 镜像打包的是 `phase1/service`（`phase2/infra/Dockerfile`：`COPY phase1/service/ /app/`；`docker-compose.yml` 同款说明）。
- Colab 部署脚本 `phase2/infra/tunnel/run-service-colab.sh` 跑 `$HOME/lingren/service`，未写明拷哪个副本；VR 端到端跑的是 `phase2/service` 的副本。

选项：

- A（按 brief）：在 `phase1/service/` 实现四条管线，VR 从 `phase2/service/` 移植。优点：与 brief 与 Dockerfile 一致；缺点：`phase2/service/` 的演进（转录、vocaledit、SVC prompt）不在本支里，两份服务副本继续分叉，须另派前向移植。
- B（按演进现状）：在 `phase2/service/` 实现，VR 已是现成的，只加 YuE2/MuLaCover 与默认改动。优点：工作量最小，服务只有一份演进副本；缺点：与 brief 字面不符；Dockerfile 仍打包 `phase1/service`，新管线进不了 Docker 部署，直到有人改 Dockerfile。
- C：先在 phase1 做，再前向移植到 phase2。工作量翻倍，不推荐。

建议：先按 A 开工（brief 与 Dockerfile 都指向 phase1；YuE2/MuLaCover 适配器代码与落点无关，写一次，两边都能用），父 agent 另定 phase2 的前向移植（或 Dockerfile 改指 phase2）。**本设计不等定夺先写实现细节，但实施协调员须在回执里写明实际落点；若父 agent 改选 B，适配器文件原样搬过去，只改注册与文档路径。**

### 0.3 行为变化清单（写进提交说明与文档醒目处）

- `PartIn.model` 默认值 `'soulx'` → `'yue2'`。老调用方不传 `model` 时，以前走 SoulX，现在走 YuE2。
- `vocalrender` 由占位（调用即报 Phase 2 待接线）变为可用管线。
- 新增 `yue2`、`mulacover` 两个可用值；未知 `model` 仍大声失败。
- 其余不变：端点、请求形状、回执形状、takes 门、SoulX 适配器行为、`stub` 测试适配器。

## 1. 请求形状

`PartIn.model: str`，默认值 `'yue2'`，描述同步改写，列出全部可选值与默认值：

```
'soulx' | 'vocalrender' | 'yue2' | 'mulacover' | 'stub'（测试用）
默认值 'yue2'。未知值大声失败，不静默回落。
```

`jobs.py` 的 `_adapters()` 返回四条真适配器加 `stub`，占位类 `_VocalRenderPending` 删除：

```python
{"soulx": SoulXAdapter(), "vocalrender": VocalRenderAdapter(),
 "yue2": YuE2Adapter(), "mulacover": MuLaCoverAdapter(),
 "stub": StubAdapter()}
```

`part.options` 是各管线的参数命名空间（见 §4–§6）。未知 option 键不报错、不生效，写进文档（沿用 SoulX 的现行做法，不另立校验）。

客户端 `client/svs_client.py` 不用改：`synthesize(parts)` 原样透传 parts，无默认值。已核对：仓库内无测试依赖旧默认（`tests/test_api.py` 显式传 `"model": "stub"`；`phase1/colab_e2e.py:33` 显式传 `"model": "soulx"`）。

## 2. SoulX（老管线，行为不变）

适配器 `adapters/soulx.py` 一行不改。环境变量 `SOULX_REPO` / `SOULX_MODEL_PATH` / `SOULX_CONFIG` / `SOULX_PHONESET` / `SOULX_DEVICE` / `SOULX_FP16` 不变。

实测证据（出处：`~/workspace/song-folk-two/work/deliver-folk-vocal/`）：

- 利：干声中位偏差约 0 音分。v2 A 版 SVC 干声中位偏差 −0.006 半音，B 版 +0.004 半音，漏音均为 0，过门全过（`rejected-v2/NOTE-folk-a-dry-v2.md`、`NOTE-folk-b-dry-v2.md`）。
- 利：时值准。v2 B 版 F0 轮廓互相关 5 个 30 秒窗口 −5、+5、+10、−10、−10 毫秒，中位 −5.0 毫秒，基本准时，未修正（同上）。
- 弊：只换音色不改唱法。v1 否决原因原文：“SVC 只换音色，不改唱法，源表演唱法错了，后面救不回来”（`rejected-v1/REJECTED-v1.md`）。
- 弊：v1 被 Wang 耳验否决（2026-11-06 19:34，原话“老管线一塌糊涂”；美声唱法）。v2 被 Wang 耳验否决（2026-11-06 20:58，原话“老管线唱法人有问题 不能用”），v2 已用参考歌手本人（宋冬野《安和桥》、木小雅《可能否》）作 SVS prompt 与 SVC 目标，过门全过仍判不可用（`rejected-v2/REJECTED-v2.md`）。
- 问题：需 prompt 音频与 prompt meta。v2 A 版 prompt 为 `prompts/anheqiao_24k.wav`（24 kHz 单声道）与 `prompts/anheqiao_prompt.json`；B 版为 `refsrc/kenengfou_prompt.wav` 与 `refsrc/kenengfou_prompt.json`。链路为 SoulX 先按谱合成参考音（seed 11，pitch_shift 0，auto_shift 关）再 SoulX SVC 换音色（同一音频作目标，pitch_shift 0，auto_shift 关），最终链只含 SVC 音。
- 附：女声 pitch_shift +3 惯例已证伪（+3 得中位偏差 +2.944 半音，改 0 后得 −0.006 半音；全程用 0 完成）。

## 3. VocalRender（移植已闭合的实现，VR 直出）

### 3.1 形态定夺：VR 直出

服务侧 `vocalrender` 适配器 = VR 直接合成（prompt 音频作 `--prompt_audio`，48 kHz 直出），移植 `phase2/service/app/adapters/vocalrender.py` 与 `app/vr_driver.py`，只改 import 路径与注册，不改逻辑。`phase1/service/app/score_converter.py` 已有 `vr_label_entry` 与 `NOTE_TOKENS`，`phase1/patches/` 已有补丁 03（`--seed`）与补丁 05（note token），移植无缺口。

民谣两次试听跑的是另一条链路——VR 合成参考音再 SVC（A：VR prompt 安和桥本人 `anheqiao_24k.wav`，seed 11 → 逐音移调纠正 → signalsmith timemap warp → SoulX SVC 到安和桥音色；B：同链路，VR prompt 为木小雅《可能否》2–8 秒干净演唱），那是歌曲制作层面的组合，不是服务适配器形态。本项不把 VR→SVC 做成服务管线；YuE2/MuLaCover 的 SVC 开关见 §6。

### 3.2 适配器要点（移植自 phase2，行为照抄）

- 环境变量：`VR_REPO`（打好补丁 03 的检出）、`VR_CKPT_DIR`（快照里的 `VocalRender` 子目录）。缺失大声失败。
- Voice 只需参考音频（作 `--prompt_audio`），不需 SoulX prompt_meta。prompt 应为 2–8 秒干净歌声；实际时长记进回执 `adapter_info.prompt_duration_s`，超出范围只告警不拒跑。
- 音符 token 是 `<NOTE_4>` 分母制（补丁 05），超出 0.125–6.0 拍响亮失败，不静默量化。
- 放置必须用回执实测起点，不能按乐谱硬贴。`jobs.py` 的 `_alignment` 已有 `vocalrender` 的 `rescaled` 模式，本项不改。
- `options` 可传 `temperature`、`fsq_temperature`、`max_len`（照抄 phase2 的透传，不另加）。

### 3.3 实测证据

- 利：B 版时序门全过、0 错音。v3 参考音 smoke gate 检出 1.0000、有声 0.9732、中位偏差 −0.007 半音、0 漏音；SVC 干声检出 1.0、有声 0.9506、中位 −0.011 半音、0 漏音（`folk-b-vr-svc/vr_ref_v3_gate.json`、`svc_measure.json`）。
- 利：A 版纠音后参考音 gate PASS（检出 0.9766、有声 0.9149、中位 +0.002 半音、0 漏音 5 音均在段边界）；SVC 干声 gate PASS（检出 0.986、中位 −0.018 半音、0 漏 3 音）（`deliver-folk-vocal/rejected-vr/vr_reference_gate.json`、`gate_align_vr.json`）。
- 利：服务侧端到端已跑通（Colab L4，会话 `svs-phase2-vr`，已停）：一个作业两个 part，作业号 `e1b68cd64b394d9b`，约 140 秒完成，4/4 遍首试通过（`phase2/vr-adapter/REPORT.md` §2）。
- 弊：A 版 VR 唱错 46 音（|偏差| > 1 半音，集中在 seg04 14 音、seg06 10 音、seg07 11 音，误差 ±3~5 半音即四/五度错音），须逐音移调纠正后才 0 错音（`folk-a-vr-svc/PROGRESS.md` 21:45 节）。
- 弊：时光切片 C 通过率 73.3%（检出 11/15 < 80% 门），未通过；A、B 通过（15/16）。尾部时序漂移约 0.3 秒（时光 stage 1b 报告 `REPORT-shiguang-slices-soulx-vr.md`）。
- 问题：VR 自定时值压缩真实存在。服务侧端到端：乐谱 4.5 秒 → 输出 4.16/4.0 秒（−7.6%/−11.1%），末音节偏差 −789.3/−629.3 毫秒。民谣 A 原始跨度比 0.917–0.990，B 各段 0.950–0.996。放置必须用实测起点。
- 问题：B 版 SVC 曾因 prompt 文件用错整轮失败：首轮 10 段全是 50 Hz 嗡鸣（检出 0.5222、中位偏差 −32.144 半音、129 漏音），根因是 bundle 用了 227.02 秒整首 Demucs 分离人声作 prompt，而非 14.5 秒 clip `kenengfou_prompt_24k.wav`；换正确 prompt 重跑后 10/10 一次成功（`folk-b-vr-svc/PROGRESS.md` 诊断节）。教训见 §6.3。
- 未测：民谣 VR 试点是否经 Wang 耳验——两份 PROGRESS 均写“待 Wang 耳验”，文件里无耳验结论。

## 4. YuE2（新写适配器，默认直出干声）

### 4.1 配方（按实验结论写死为默认值）

- 模型：YuE2 v0.1.6，commit `9c6c4b34`；权重 HF `m-a-p/YuE2-3B` revision `8f24312f`、VAE `m-a-p/YuE2-Vae` revision `152733a1`。
- 输入：ScoreIn 转原生 ABC（Vocal + Ins 双声部、段落注释）。只换原生 ABC 是唯一证实有效的单变量（拆分 a：时长比 0.884→1.005，覆盖 72.0%→100.0%（132/132），中位起音误差 171→107.5 毫秒）。
- 谱子先移高 12 半音再生成（r3/r4 两段都成立：相对移调后输入中位 −1207.5/−1199.1 音分，相对原谱子 −7.5/+0.9 音分）。移调由适配器内部做，输出回到原谱子音区。
- 采样参数默认值：`cot="melody"`，`cfg_scale=1.0`，`temperature=1.0`，`top_p=0.95`，`top_k=100`，`repetition_penalty=1.2`，seed 取 part 的 seed（实验 seed 42；未传 seed 时 jobs.py 给 0 起，见 §4.4）。
- 组合控制反效果已证实（新控制组时长比 2.237，只过 2/5 门，比单变量原生 ABC 的 3/5 更差），适配器不提供“一键全开”组合；各参数只许单独覆盖（§4.5）。

### 4.2 Ins 声部来源（定夺）

`PartIn` 没有伴奏字段。默认 Ins 声部全为休止（与谱子同小节数、同 bpm 的空声部）。调用方可在 `options.ins_abc` 传入整段 Ins 声部的 ABC 原文（Vocal 声部仍由适配器从 ScoreIn 生成），覆盖默认休止。无伴奏字段时不静默假设有伴奏；默认休止写进文档。

### 4.3 生成与分离

- YuE2 生成的是完整歌曲（人声 + 模型自生伴奏），不保留输入伴奏（YuE2 不接受伴奏音频输入）。生成后用 Demucs 4.1.0 htdemucs 分离出干声，输出即干声 wav。分离是适配器内固定步骤，不可关闭。
- 单次调用生成整个 part，不按段切分（v1）。已测单次跨度：r3 输出 107.52 秒、r4 输出 61.96 秒；更长 part 能跑，但时长保真度未测（早期整曲干声时间压缩约 0.80：A 196.88 秒对目标 245.2 秒），写进文档作已知局限，不静默处理。
- Voice：生成本身不需要 voice（YuE2 无参考音频输入）。`needs_voice` 见 §7；SVC 开关打开时才需要 voice（§6）。

### 4.4 Seed

seed 走 `part.seeds`（jobs.py 已有逻辑：未传时取 0..takes-1）。实验默认 seed 42；调用方不传 seeds 时 jobs.py 给 0，适配器不另改 seed（seed 0 未在民谣实验里跑过，文档注明）。

### 4.5 Options

| 键 | 默认值 | 说明 |
|---|---|---|
| `ins_abc` | （无） | Ins 声部 ABC 原文；缺省全休止 |
| `yue2_style` | （必填，缺失大声失败） | 风格文本；实验用的风格串见试听脚本（`yue2-min-exp/gen_minexp_r3.py`），冒烟测试用同一串 |
| `yue2_cot` | `"melody"` | 实验值；`full` 未证实更好 |
| `yue2_cfg_scale` | `1.0` | 实验值；2.0 单变量证实无效 |
| `yue2_temperature` | `1.0` | 实验值；0.7–0.8 有害（时长炸到 360 秒上限） |
| `yue2_top_p` | `0.95` | 实验值 |
| `yue2_top_k` | `100` | 实验值 |
| `yue2_repetition_penalty` | `1.2` | 实验值；1.0–1.05 有害 |
| `svc` | `false` | 见 §6 |

### 4.6 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `YUE2_REPO` | （必填） | YuE 检出（`yue2-v0.1.6`，`uv pip install .` 已装） |
| `YUE2_MODEL` | `m-a-p/YuE2-3B` | HF 仓库 |
| `YUE2_MODEL_REVISION` | `8f24312f` | 权重 revision |
| `YUE2_VAE_REVISION` | `152733a1` | VAE revision |
| `YUE2_DEVICE` | `cuda` | 设备 |

缺失必填项大声失败。权重不进仓库，走 §8 的 Drive 缓存模式。

### 4.7 实测证据（利/弊/问题）

- 利：原生 ABC 是唯一有效的单变量（见 §4.1 数字）。
- 利：八度杠杆两段都成立（r3 −7.5 音分，r4 +0.9 音分；相对移调后输入 −1207.5/−1199.1 音分）。
- 利：r4 第二段 identity 读法五项全过（5/5），移调段首次；r3 第一段 identity 读法 4/5（分块拟合不过）。
- 弊：整曲五版全败（A 五 seed 轮廓相关最好一致 r 约 0.40，门要求 r > 0.5 且配对一致；覆盖 54.0%–77.9%）。
- 弊：早期干声时间压缩约 0.80（A 196.88 秒对 245.2 秒；B 191.88 秒对 233.7 秒）。
- 弊：尾部与段间静音区噪声（Wang 2026-11-06 20:56 耳验）。一类是 Demucs 误分（模型自生伴奏漏进干声）；另一类无峰匹配，RMS ≤ 0.002，来源未定（VAE 噪声或分离飞溅）；噪声与 seed 有关。
- 问题：第一段高音压扁在第二段未复现（r3 自 MIDI 60 起一路变扁，−100 音分零点交叉在 MIDI 60.5；r4 同音区全程平坦，无任何 MIDI 组中位低于 −100 音分）。固定上限与按比例压缩两个假设都被否定，音区上限给不出。是否追第一段压扁（同段重 roll 换 seed），待 Wang 定。
- 问题：r4 第二段残差是零星八度错（约 20% 的音唱高一个八度还多，偏差 +1000 至 +1510 音分），与第一段系统性压扁结构不同。
- 问题：控制面只有六项（风格文本、歌词文本、ABC 谱、cot 模式、seed、采样参数）。无时长参数、无伴奏音频输入、无参考音频输入、无音素对齐通道、无 BPM 字段、无负 prompt 字段。和弦只在 `chords.json` 里，没有进模型。
- 问题：seed 方差大。五版 seed 只换随机结果，不修控制缺口（A seed 42 干声 196.88 秒、seed 2025 干声 139.96 秒）。

## 5. MuLaCover（新写适配器，默认直出干声）

### 5.1 配方（按实验结论写死为默认值）

- 模型：MuLaCover main HEAD `e27fa7fa`（2026-10-20，无 tag）；权重 HF `HeartMuLa/MuLaCover` 8.2 GB + `Qwen/Qwen3-Embedding-0.6B` 1.2 GB + `HeartMuLa/HeartCodec-oss-20260123` 6.2 GB，合计约 15.6 GB。
- 输入：ScoreIn 转旋律 MIDI + 和弦 MIDI + 歌词 + 风格 tags，用 new-control 配方：拉伸 ×1.5 的旋律 MIDI + 拉伸 ×1.5 的和弦 MIDI + 原歌词 + 新 tags + `cfg_scale 2.5`，`temperature 1.0`、`topk 250` 不变，seed 42。
- 配方保留说明：拉伸 ×1.5 的时长效应已被证伪（新控制组输出由基线 68.16 秒缩到 52.32 秒，时长比 0.669→0.514），配方保留是因为组合实测最佳（轮廓 r 0.332，全系列最高），不是因为每个成分都证实有效。写进文档，不写反。
- 鼓通道不传（证伪：鼓臂 0/5，副歌开头静音 20.55 秒）。降温不设默认值（0.7 单变量有效但换 seed 后三项最佳全消失，是采样运气）。

### 5.2 和弦来源（定夺）

`PartIn` 没有和弦字段。默认从谱子推导，规则写死如下（确定性，无静默假设）：

- 按小节切分（4/4 假设，见 §5.3）；每小节收集起始点落在本小节内的音符的音级；
- 根音 = 总时值最大的音级；三度：有大三度（根音 +4 半音）判大调，有小三度（根音 +3 半音）判小调，两者都有取时值大者，两者都无判大调；
- 空小节（本小节无音符）沿用上一小节的和弦；首小节为空则大声失败；
- 七音忽略，只出三和弦。

调用方可在 `options.chords` 传入显式和弦表覆盖推导，格式为 `[{"bar": 0, "root": 0, "quality": "major"}]`（`bar` 从 0 起的小节号，`root` 为 0–11 的音级，C=0）。推导与传入的和弦表都写进回执 `adapter_info`，供核对。

### 5.3 按段生成与拼接

长段按段生成再拼接。段定义：按小节边界切分，每段至多 12 小节（4/4 假设：ScoreIn 无拍号字段，v1 假定 4/4，写进文档作已知局限；非 4/4 谱子的切分点会落在小节内，文档注明）；不足 12 小节的 part 为一段。

- 每段用同一 base seed 生成（实验做法：各段同 seed 跑两轮再挑优；v1 不挑优，单 seed 直出，挑优是后续项）。
- 拼接方法照抄 `seggen_splice.py`：每段取 DIO 首/末有声帧裁剪，librosa 相位声码器拉伸到段长（音高不变），放到谱子时钟的段位；无音符的段（桥段类）留数字静音。
- 歌词按音符顺序取，段标签取 `part.label` 作 section 标记（MuLaCover 的 `[verse]`/`[chorus]` 标记只是纯文本，不解析）。

### 5.4 Options

| 键 | 默认值 | 说明 |
|---|---|---|
| `tags` | （必填，缺失大声失败） | 风格 tags 全文；实验串见 §5.5，不设通用默认值（见下） |
| `chords` | （无） | 显式和弦表，覆盖 §5.2 的推导 |
| `mulacover_cfg_scale` | `2.5` | new-control 配方值 |
| `mulacover_temperature` | `1.0` | 实验值 |
| `mulacover_topk` | `250` | 实验值 |
| `transpose12` | `false` | 未定项，默认关（见 §5.6） |
| `svc` | `false` | 见 §6 |

tags 必填的理由：实验的 tags 全文是民谣 A 专用的（topic、genre、instrument、mood、vocal 音区、tempo 全是该歌的值），不存在“通用默认值”；调用方不给 tags 时，适配器不编一个通用串冒充，必须大声失败。文档给民谣 A 的 tags 全文作示例。

### 5.5 实验 tags 全文（示例，冒烟测试用同一串）

```
topic:[Longing for a lost love at a crossroads]; genre:[Chinese folk]; instrument:[acoustic guitar, piano, bass, drums]; mood:[nostalgic, gentle, wistful]; vocal:[male tenor, sings in the written octave, range G3 to G4]; tempo:[66 BPM, slow ballad]
```

### 5.6 移调 +12（未定项，默认关）

`options.transpose12` 默认 `false`。开启时旋律 MIDI 与和弦 MIDI 整体 +12 半音再生成。未试的杠杆：八度误差在本实验里不是系统性的（各臂中位 −776 至 −0.7 音分），全局移调会把已准的臂推高一个八度；是否试待 Wang 定，适配器只留开关，不默认开。

### 5.7 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MULACOVER_REPO` | （必填） | MuLaCover 检出（`e27fa7fa`，`uv pip install -e '.[audio]'` 已装） |
| `MULACOVER_MODEL_PATH` | （必填） | 权重目录（含 MuLaCover、Qwen3-Embedding、HeartCodec-oss） |
| `MULACOVER_DEVICE` | `cuda:0` | 设备 |

缺失必填项大声失败。权重不进仓库，走 §8 的 Drive 缓存模式。

### 5.8 实测证据（利/弊/问题）

- 利：谱内覆盖率高（A 93.2%，B 95.2%，只算输出时长内的音符；唱不完是主因，不是唱不准）。
- 利：B 版音高基本是对的（线性 warp 后中位偏差 −59.9 音分；CH1 50/51 音测到，原始中位 −95 音分，全实验最贴谱的段）。
- 利：按段生成后拼接整轨覆盖 95.8%（204/213），时长比 1.000（构造使然），无段间静音，过 3/5 门。
- 弊：A 版中位偏差 −1200.9 音分（低一个八度唱），八度不变量中位 −0.9 音分；有声帧 F0 中位 129.8 Hz（MIDI 47.9），86.1% 的有声帧低于谱面最低音 MIDI 55。
- 弊：四轮调参 13 臂无一过 2/5 门；轮廓 r 全系列最高仍是新控制组的 0.332，离 0.5 的门差一倍。
- 弊：时长自决、输出短（A 140.16 秒对 245.2 秒 = 0.57；B 123.84 秒对 233.7 秒 = 0.53），是模型自己发 EOS 结束，不是截断。
- 弊：按段拼接未解决旋律跟随（拼接整轨轮廓 r 0.199、分块拟合 0.273，两项音高门全不过；瓶颈在段内音高跟随，不在段长或拼接）。
- 问题：条件软性。交叉注意力门 `gate_proj` 初始化为 0，符号条件从“关”开始学；只在主干后 8 层（共 28 层）注入符号条件与风格；训练时故意用部分失配的音频-符号对，模型被训练成容忍符号条件与音频不完全一致。
- 问题：seed 方差压倒变量效应（同参数 seed 42 与 seed 7 的时长差 40.08 秒，比任何单变量效应都大）。
- 问题：无 BPM 输入（MIDI 路径下传 `--bpm` 直接报错；BPM 只存在于 MIDI 的 tempo 元数据里，模型看不到）；无音区/八度参数；无音素对齐通道。
- 问题：无参考音频音色输入（`--ref_audio` 只走转录，音频本身不进生成器；MIDI 路径下 MUQ 风格向量是全零）。

## 6. SVC 可选后处理（YuE2 / MuLaCover，默认不接）

### 6.1 开关

`part.options.svc`（bool，默认 `false`）。只对 `yue2` 与 `mulacover` 生效；`soulx` 与 `vocalrender` 传此开关时忽略（写进文档，不报错——开关语义是“生成之后是否接 SVC”，SoulX 本来就是先合成参考音再 SVC 的链路，VR 直出的 prompt 本身就是音色，见 §6.4）。

- 关：适配器直出干声（Demucs 分离后的 YuE2 干声；MuLaCover 拼接后的干声）。
- 开：干声生成后接 SoulX SVC 换音色，输出为换音色后的 wav；回执 `adapter_info` 记 `svc: true` 与 SVC 的 seed、耗时、目标 voice_id。

### 6.2 目标音色与校验

- 目标音色走现有 voice 注册机制：SVC 目标就是 `part.voice_id` 对应的已注册 voice 的参考音频（`voice.audio_path`）。
- 校验（大声失败，不静默）：voice 不存在；voice 无参考音频；参考音频时长 > 30 秒。30 秒上限的依据：试听用的 prompt 均为 8–15 秒 clip（安和桥 8.12 秒，可能否 14.5 秒）；B 版事故证明 227 秒整首音频作 prompt 会整轮出 50 Hz 嗡鸣（§3.3）。时长记进回执。
- SVC 参数写死：`pitch_shift 0`、`auto_shift` 关、fp16 按设备（与民谣试听一致；女声 pitch_shift +3 惯例已证伪，见 §2）。
- SVC 调用复用 `phase2/service/app/svc_prompt_driver.py` 的调用形状（子进程驱动，`SVC_MODEL_PATH` 环境变量，`MODEL_LOCK` 由 jobs.py 的串行 worker 保证——本项的 jobs.py 本来就是单 worker 串行，不另加锁）。

### 6.3 环境变量（开关打开时才必填）

| 变量 | 说明 |
|---|---|
| `SVC_REPO` | SoulX-Singer 检出（打好补丁 00–02） |
| `SVC_MODEL_PATH` | SVC 权重 `model-svc.pt` |
| `SVC_DEVICE` | 设备（默认 `cuda`） |

开关关闭时不读这些变量；开关打开缺失则大声失败。

### 6.4 SoulX 与 VR 不受影响

SoulX 管线不受影响：它本来就是合成参考音在先、SVC 换音色在后（服务侧 SoulX 适配器直接按 prompt 合成；歌曲层的 SoulX→SVC 链路不在本项范围）。VR 直出的 prompt 音频本身就是音色决定项，不接 SVC。

## 7. Voice 需求与 _run_part 改动

现状：`jobs.py` 的 `_run_part` 在查适配器之前先查 voice，不存在即大声失败。YuE2/MuLaCover 生成本身不需要 voice（无参考音频输入），只在 SVC 开关打开时需要。

改动（小，保持 SoulX/VR 行为不变）：

- `BaseAdapter` 加类属性 `needs_voice = True`（默认）；YuE2/MuLaCover 适配器覆写为方法 `needs_voice(part)`：`part.options.get("svc", False)`。
- `_run_part` 先取适配器，再按 `adapter.needs_voice(part)` 决定是否要求 voice 存在；需要而无则大声失败（原有报错文案不变）。
- 适配器内部仍做自己的 voice 校验（SoulX 要 prompt_meta、VR 要参考音频、SVC 开关要目标音频），不依赖 `_run_part` 的检查。

## 8. 执行环境与部署

- 服务跑在 Colab 会话内（`phase2/infra/tunnel/run-service-colab.sh`，`SOULX_DEVICE=cuda`，权重走 Drive 缓存 `$HOME/drive/lingren-models`，无缓存时从 HF 下载后写回缓存）。不要假设 Colab 是唯一落点，也不要假设盒子有 GPU（instabox 盒子无 GPU）。
- YuE2：Colab A100；setup 脚本 `yue2-min-exp/setup_yue2_r4.sh`（uv Python 3.12 venv，torch 2.10.0+cu128，克隆 `multimodal-art-projection/YuE` 并 checkout `yue2-v0.1.6`，`uv pip install .`，demucs 4.1.0）。一次生成约 40–120 秒（模型加载另计约 43 秒），Demucs 每组约 1–2 分钟。
- MuLaCover：Colab A100；setup 脚本 `mulacover-min-exp/mc_minexp_setup.sh`（uv Python 3.10.22，torch 2.10.0 + torchvision 0.25.0 + torchaudio 2.10.0 cu130，克隆 `HeartMuLa/MuLaCover` 并 checkout `e27fa7fa`，`uv pip install -e '.[audio]'`）。一次生成约 109–264 秒，VRAM 峰值约 14664–14684 MiB。
- VR：Colab L4 起步（自回归扩散 + 9.5 GB 权重，T4 能跑但慢）；服务侧 setup 脚本 `phase2/vr-adapter/colab_setup_phase2.sh`。
- SoulX：T4 足够（bf16 模型小）。
- 复用实验的 setup 脚本，不重写。会话跑完即停（Wang 已授权直接建会话；建环境用 uv）。
- 串行 worker 不变：单 worker 串行跑模型，两个模型不同时驻留 GPU（`jobs.py` 现状）。

## 9. 测试与验收

### 9.1 单元测试（VM 本地，无 GPU）

- 注册：`_adapters()` 含 `soulx`、`vocalrender`、`yue2`、`mulacover`、`stub` 五项；无 `_VocalRenderPending`。
- 未知 model 拒绝：`model="diffsinger"` 等未知值经 `_run_part` 大声失败（不静默回落）。
- 默认值：`PartIn(...).model == "yue2"`；显式 `model="soulx"` 仍走 SoulX。
- ABC 转换：ScoreIn → 原生 ABC（Vocal +12 移调、Ins 默认休止、`ins_abc` 覆盖、段落注释）；`yue2_style` 缺失大声失败。
- MIDI 转换：旋律 MIDI 与和弦 MIDI 生成；和弦推导规则逐条覆盖（大/小三度判定、空小节沿用、首小节为空大声失败、`options.chords` 覆盖）。
- Options：`svc` 默认关、`transpose12` 默认关、各采样参数默认值为实验值。
- VR 移植：复用 `phase2/service/tests/` 里的 6 项 VR 测试（词表、超长音符拒绝、缺环境变量、SP 忽略、双模型混装作业、超长音符经 API 响亮失败），改到 phase1 路径。
- SoulX 回归：现有测试全过（`tests/test_api.py`、`tests/test_gate.py`）；SoulX 适配器 0 行改动。

### 9.2 冒烟（Colab GPU，每条管线在民谣测试段上实测）

测试段：民谣 A 的 V1+V2+CH1，小节 9–36，101.8 秒，132 音（与 YuE2 最小实验、MuLaCover 最小实验同段、同口径）。每条管线报实验同口径的门数据（覆盖率、中位偏差、时长比），不只报“能跑”：

- YuE2：对照值 r3/r4 段级（覆盖 97.5%–100%、中位偏差 −7.5/+0.9 音分、时长比 1.002–1.056）。冒烟用 seed 42、`yue2_style` 取 r3/r4 脚本的同一串。
- MuLaCover：对照值新控制组（覆盖 97.7%、轮廓 r 0.332、时长比 0.514）与 seggen 拼接（覆盖 95.8%、轮廓 0.199）。冒烟用 tags_new 全文、seed 42。
- VocalRender：对照值服务侧端到端（作业 e1b68cd64b394d9b，4/4 遍首试通过；VR 输出 48 kHz，实测起点贴轨）。
- SoulX：回归冒烟（现有行为不变，门数与 Phase 1 一致）。

### 9.3 SVC 验证小样（新增验收项）

- 内容：YuE2 干声经 SVC 到安和桥音色，民谣 A 测试段（V1+V2+CH1），SVC 目标用已备好的安和桥参考 prompt（`~/workspace/song-folk-two/work/folk-a-soulx-v2/prompts/anheqiao_24k.wav`，8.12 秒 clip）。
- 交付：落盘音频 + 门数据（覆盖率、中位偏差、时长比），供 Wang 耳验。耳验结论由 Wang 定，协调员不代判。
- 失败语义：SVC prompt 用错整轮出嗡鸣是已知事故（§3.3）；小样须核对 prompt 文件的时长与哈希，不用错文件。

## 10. 文档更新

- `phase1/service/README.md`：model 枚举与默认值、四条管线的环境变量表、SVC 开关、默认改动的醒目提示、DiffSinger 封存说明。
- `RESEARCH-AND-DESIGN.md`：§10 的“默认模型是否定 SoulX”记为已定（Wang 2026-11-07：默认 YuE2）；新增 §13《四条管线（2026-11-07）》，每条管线的利/弊/问题与证据出处（照抄 §2–§5 的证据节，不写无来源的定性）；§1.3 的“不在 v1 范围的模型”更新（YuE2/MuLaCover 已接入，DiffSinger 仍不在内）。
- 提交说明：默认改动与四条管线写在首段；行为变化清单照抄 §0.3。

## 11. 未接入 / 已封存

DiffSinger 不是第五条可用管线。DiffSinger + SVC 两段管线位于 lingren 仓库分支 `lingren-diffsinger-svc-twostage`，尖端 `8ad4180`（时光 stage 1b VR 标签与 sidecar），代码提交后即停下来，不合入 main（Wang 2026-11-07 15:10 定）。本项不碰该分支。

## 12. 实施顺序

1. schemas（model 枚举 + 默认 `yue2` + 描述）与 `_adapters()` 注册（四条 + stub），删占位类；`needs_voice` 钩子与 `_run_part` 改动。
2. VR 移植（adapter + driver + 注册 + 6 项测试复用）。
3. YuE2 适配器（ABC 转换 + 驱动 + Demucs 分离 + 注册）。
4. MuLaCover 适配器（MIDI 转换 + 和弦推导 + 按段生成与拼接 + 驱动 + 注册）。
5. SVC 开关（SVC 驱动调用 + 校验 + `options.svc`）。
6. 文档（README、RESEARCH-AND-DESIGN.md §13、默认改动提示、DiffSinger 一节）。
7. 单元测试全过（VM 本地）→ Colab 冒烟四条管线 + SVC 验证小样 → 回执。

每项做完即在分支内提交（不推远端，不合入 main）。仓库没有 CI，验收只靠本地测试与实测冒烟，报告里须明说这一点。

## 13. 未决 / 未测

- 落点分歧（§0.2）：phase1 还是 phase2，须父 agent 定。本设计按 brief 落 phase1；若改选 B，适配器文件原样搬过去。
- YuE2 第一段高音压扁是否追（同段重 roll 换 seed 看复现性），待 Wang 定；适配器不做逐音补偿。
- MuLaCover 移调 +12 是否试，待 Wang 定；适配器只留开关，默认关。
- YuE2 单次调用超过约 108 秒输出的时长保真度未测；MuLaCover 按段挑优（多 seed 选最优）不在 v1。
- VR 试点耳验结论未出（两份 PROGRESS 均写“待 Wang 耳验”）；SVC 验证小样的耳验结论由 Wang 定。
- Yeban 侧 MCP 工具（`synthesize_singing` / `svs_job`）透传新 model 值是另一步，不在本项；Yeban 侧若有调用方省略 `model`，默认改动会使它改走 YuE2，须在该步里处理。
