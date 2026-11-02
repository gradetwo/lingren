# SVS API 服务 — 调研与设计

日期：2026-11-02
状态：调研与设计完成；Phase 0 已完成（见 phase0/REPORT.md）；Phase 1 已完成（见 phase1/REPORT.md：补丁验证 + 服务骨架 + SoulX 适配器 + 作业 API + Yeban 客户端层；Yeban 仓库内 MCP 工具本体与 VocalRender 服务接线是 Phase 2 项）；2026-11-02 新增两场景设计输入（多遍厚度、和声），见 5.1 节。
背景：本项目就是 G3 定夺（2026-11-01）里说的「以后加接口调用第三方」的那一步。Yeban 本体不做内置歌声合成，本服务是独立项目，Yeban 通过 HTTP 调用它。

## 0. 结论先行

- 服务形态：一个独立 FastAPI 服务，同一套代码，三种部署（本地 macOS、Colab 远端、Docker Linux GPU）。
- 模型：双适配器。SoulX-Singer 作默认模型，VocalRender 作音质选项。
- 选 SoulX 作默认的理由只有一条，而且是决定性的：它遵守乐谱时钟。VocalRender 不接受逐音符时长，输出时值由模型自己定。
- macOS 本地：v1 只做 PyTorch MPS，不做 MLX。MLX 是二期移植项，不是开关。
- 远端：Colab 会话内跑服务，cloudflared 隧道暴露。开发期用 quick tunnel，固定使用期切 named tunnel。
- Docker 是三期，只面向 Linux GPU 服务器。macOS 上 Docker 没有 GPU 直通，Docker 模式永远不是 macOS 本地路径。

## 1. 模型调研

### 1.1 对照表

| 项目 | SoulX-Singer | VocalRender |
|---|---|---|
| 来源 | Soul AI Lab，2026-02，论文 arXiv:2602.07803 | 陈等，2026-07-30，论文 arXiv:2607.27768 |
| 仓库 | github.com/Soul-AILab/SoulX-Singer | github.com/pymaster17/VocalRender |
| 权重 | huggingface.co/Soul-AILab/SoulX-Singer | huggingface.co/pymaster/VocalRender |
| 许可 | Apache-2.0，代码与权重同许可（仓库 README 原文） | Apache-2.0（仓库 LICENSE，与上游 VoxCPM 同许可） |
| 架构 | 非自回归流匹配 DiT，输出梅尔谱再经声码器成波形 | 交错歌词-音符表示 ＋ AudioVAE ＋ 自回归扩散 |
| 输入 | 乐谱（MIDI ＋ 歌词）或旋律（F0 曲线） | 乐谱原生：歌词 ＋ MIDI 音高 ＋ 音符时值 ＋ 速度，交错成 token 串 |
| 语言 | 普通话、英语、粤语 | 以普通话为主（公开评估为普通话） |
| 训练数据 | 42,000 小时以上对齐歌声 | 2,300 小时；基础版在开源 CrawlSinger-OS 上微调 |
| 音色 | 零样本：一段参考音频克隆任意歌手音色 | 必须给 prompt 音频：一段 2–8 秒干净歌声，决定目标音色 |
| 输出采样率 | 待 Phase 0 实测确认（官方 demo 未在 README 标明） | 48 kHz（官方 README 标明） |
| 权重体积 | bf16 模型约 2 GB，含预处理模型约 6 GB | 每个检查点约 9.5 GB；基础版与 Pro 版同架构同参数量，只差训练配方 |
| 硬件 | 本地 8 GB 以下显存可跑（bf16） | 官方 demo 要求 CUDA GPU |
| 时钟语义 | 严格跟乐谱（见 1.2） | 时值由模型联合决定，不接受逐音符时长 |

### 1.2 核心差异：时钟语义

这是整个设计的分水岭。

- SoulX-Singer 接受乐谱的音高、起点、时长，按乐谱唱。在 VocalRender 自己论文的基准里，SoulX 是五个被测系统里最严格的乐谱跟随者：音符时值交并比 0.63，原始音高准确率 0.77。
- VocalRender  deliberately 不接受逐音符时长。它的输入 JSON 里 `word_dur` 与 `pitch_dur` 只是可视化与评估元数据，官方 README 原文：这两个字段不用于构造乐谱 prompt，也不用于训练。模型在生成中联合决定局部时值与总时长。
- 对 Yeban 的含义：Yeban 是 DAW，时间轴是契约。SoulX 的输出可以直接按乐谱位置贴到时间轴上。VocalRender 的输出不能，必须先实测它实际唱出的每个音节起点，再按实测位置贴。
- 所以服务契约必须包含对齐回执（见 5 节），而不是只回一个 wav 文件。没有对齐回执，VocalRender 在 Yeban 里无法正确落位。

### 1.3 不在 v1 范围的模型

- SoulX-Singer-SVC（2026-03 发布，歌声转换，音频到音频，无需歌词与 MIDI）：与本项目目标不同（本项目是乐谱到歌声），列为后续扩展候选，不进 v1。
- 其他 SVS（DiffSinger、TCSinger 2 等）：不列入 v1。双模型已经覆盖「时钟准」与「音质」两个极点，再加模型只加适配器成本，不加架构成本。

## 2. macOS 本地：MLX 是否更好

结论：v1 不做 MLX。理由如下。

- 两个模型都没有 MLX 移植。官方路径都是 PyTorch，VocalRender 官方 demo 明确要求 CUDA GPU。
- MLX 移植是手工作业，不是开关。先例证明这条路走得通，但每个模型都要单独移植：
  - IndexTTS-2.5-mlx：把 PyTorch 版 TTS 重写到 MLX，GPT 解码器 int8 量化，实测比 PyTorch MPS 快约 2.4 倍，实时系数约 0.45。
  - drama3-mac：Fish Audio Drama 3 移植到 Apple Silicon，MLX 跑主干与音频塔，编解码器仍留 PyTorch/MPS。
  - mlx-audio：Kokoro-82M 的社区 MLX 路径，是 Apple Silicon 上 TTS 的现实加速路径。
- 这些先例同时说明两点：MLX 确实比 MPS 快（统一内存零拷贝、Metal 融合算子、int8 解码融合进 matmul）；但移植工作量以周计，且声码器与编解码环节常常仍需 PyTorch。
- v1 路线：macOS 本地 = PyTorch MPS ＋ 不支持算子回退 CPU。统一内存使 9.5 GB 权重的 VocalRender 在 32 GB 以上内存的 Mac 上装得下，但自回归扩散的解码是延迟敏感的，Mac 上只会慢，不会快。VocalRender 在 Mac 本地只作「慢速可用」，不作主推。
- MLX 立项条件（写死，避免拍脑袋）：Phase 0/1 实测 MPS 实时系数后，若 SoulX 在目标 Mac 上 RTF 高于 2（即唱 1 分钟要算 2 分钟以上），启动 MLX 移植，先做 SoulX（非自回归、模型小、时钟确定，移植收益最明确）。VocalRender 的 MLX 移植排在 SoulX 之后。

## 3. 服务架构

### 3.1 一体三部署

同一套 FastAPI 代码，只换运行后端与权重位置：

- 本地 macOS：原生 Python，PyTorch MPS，权重在本地磁盘。
- 远端 Colab：同一服务跑在 Colab 会话里，GPU 为 CUDA，见 7 节。
- Docker（三期）：同一服务装进 CUDA 基础镜像，跑在 Linux GPU 服务器上，见 8 节。

### 3.2 模块划分

- HTTP API 层：接请求、鉴权、返回任务与产物。只做薄层，不含模型逻辑。
- 任务队列：单 worker 串行跑模型。SVS 生成是秒到分钟级，同步 HTTP 会超时，所以一律异步任务。串行是为了防显存争抢，两个模型不同时驻留。
- 模型适配器：每个模型一个适配器。适配器只做两件事：把统一乐谱 JSON 翻成该模型的输入，跑该模型的官方推理入口。新增模型只加适配器，不动 API 与 Yeban 侧。
- 声音库：voice_id 对应一段参考音频。上传、列表、删除。参考音频只作音色条件，不作训练。
- 产物存储：wav 文件 ＋ receipt JSON（模型、检查点、种子、耗时、对齐数据）。产物按任务 id 寻址。
- 配置：模型路径、设备（cuda/mps/cpu）、并发数、API key。全部走环境变量与配置文件，不写死。

### 3.3 不做的事

- 不做实时演唱。离线渲染 only。实时是另一套架构（流式解码、低延迟管线），不在本项目范围。
- 不把模型、权重、推理依赖编进 Yeban。这是 G3 定夺的直接要求，也是本项目独立成库的原因。
- 不做训练与微调。服务只推理。用户自有音色靠零样本参考音频，不靠训练。

## 4. 统一乐谱 JSON

Yeban 侧只产一种 JSON，两个适配器各取所需：

- 顶层：bpm、语言、调性（可选）。
- 音符数组：每项含歌词音节、MIDI 音高、乐谱起点（秒与 tick 并存）、乐谱时长（秒与音符时值并存）。
- 秒与音符时值并存的原因：SoulX 取秒级起点与时长；VocalRender 取音符时值与速度。两边都不用对方字段，但 Yeban 只需产一次。
- 音色：voice_id，或随请求上传的参考音频。
- 模型选择：model（soulx 或 vocalrender）、variant（vocalrender 的 base 或 pro；soulx 暂无 variant）。
- 输出：采样率（缺省跟模型原生，Yeban 导入时重采样到工程采样率）、格式（wav 缺省）。
- 多声部与多遍不改乐谱 JSON 本身：parts 与 takes 是 5.1 节的请求层概念，每个 part 各带一份本节乐谱 JSON。

## 5. API 契约草案（v1）

- POST /v1/voices：上传参考音频，返回 voice_id。
- POST /v1/synthesize：提交统一乐谱 JSON，返回 job_id 与初始状态。
- GET /v1/jobs/{id}：查状态与进度（排队、加载模型、生成中、完成、失败，失败给可读原因，不静默）。
- GET /v1/jobs/{id}/audio：取 wav。
- GET /v1/jobs/{id}/receipt：取回执，含模型、检查点、种子、耗时，以及对齐数组：每音节的乐谱起点与实测起点。
- 鉴权：Bearer API key。远端必需，本地可选。
- 失败语义：任何一步失败，任务状态写失败原因，不返回静音 wav，不返回空音频冒充成功（这是夜半 v8 检查里立过的规矩：静默降级不可接受）。

### 5.1 分轨与多遍：parts × takes（Wang 2026-11-02 补充的两场景）

两场景都落到同一个请求形状上，服务只出分轨素材，混音永远留在 Yeban。

- 请求形状：POST /v1/synthesize 接受 `parts` 数组，每项 = {label, score（4 节统一乐谱 JSON）, voice_id, model/variant, output, takes, seeds}。单声部就是 parts 长度 1 的特例，不另设端点。
- 场景 1 —— 同一句歌词、同一个人、多遍录音求厚度：同一 part 设 takes = N（例如 3），每遍用不同 seed 独立合成，服务返回 N 条分开的音频，Yeban 把它们叠在平行轨上混出厚度。硬性要求：批量返回的每条音频不能完全一样。遍与遍之间必须有天然微差（时值、音高、咬字的微小起伏），叠完全相同的波形只会变响，不会变厚。服务侧保证：生成后逐对验差异（非逐位相同、相关度低于阈值、至少部分音节起点有偏差）；验出相同或近似相同则换 seed 有界重试，重试仍相同则任务响亮失败，绝不把相同音频当多遍返回。阈值由 Phase 0 实测数据定。Yeban 侧的微小延时/失谐仍是正常混音技巧，但它是用在真实多遍上的，不用来掩盖相同的遍。
- 场景 2 —— 和声：和声部就是另一个 part：音高不同（例如三度、五度），歌词可同可不同，voice_id 可与主唱相同（同一人自己和自己和声，常见情形）也可不同（不同歌手）。各 part 共享 bpm 与时间轴；SoulX 各 part 按乐谱位置直接贴，VocalRender 各 part 按各自回执的实测起点贴。服务返回的分轨按 label 区分，Yeban 每轨一条，声像与电平由 Yeban 混音台定。
- 回执与进度：回执按 part label 分组，每遍含自己的 seed、耗时、对齐数组（VocalRender 每遍各有一份实测起点）。任务状态除总进度外，给出 parts_completed/total 与 takes_completed，串行 worker 跑批量任务时 Yeban 才能显示真实进度。
- 不做的事：服务不混音，不把多遍或和声 baked 成一条成品；不替用户定声像与电平；不把失谐/延时藏进 takes 里冒充真多遍。
- 根本口径（Wang 2026-11-02）：SVS 只是替代真人录音，音乐创作的技巧照原样用。合成出的是普通录音片段，走现有音频片段机制：多遍既可叠放求厚度，也可 comping（各遍挑最好的乐句拼成一轨）；和声、伴唱、加花都只是 parts/takes 的排布。服务不为 SVS 另设一套工作流，夜半也不为 SVS 另设一种片段类型。

## 6. Yeban 接入设计

- 新 MCP 工具两个：synthesize_singing（提交，返回 job 收据）与 svs_job（查状态、取音频与回执）。工具层只做 HTTP 客户端，不含推理。synthesize_singing 的参数含 parts（每项的 label、score、voice_id、model、takes、seeds）；svs_job 返回按 part/take 分组的音频与回执；导入时每 part、每遍各成一个独立音频片段，平行轨叠放由 Yeban 侧定，服务不替用户混音。
- 配置：服务地址 ＋ API key，存在 Yeban 配置里。本地缺省 http://127.0.0.1:8000，远端填 tunnel URL。服务地址不进工程文件，工程只存产物引用。
- 导入：取回的 wav 作为音频轨片段落入工程（走现有音频片段机制），导入时重采样到工程采样率。
- 放置语义（关键，分模型）：
  - SoulX：按乐谱位置直接贴。回执里的实测起点用于校验，偏差超过阈值时在回执里标出，不自动移位。
  - VocalRender：按回执的实测起点贴。首音节实测起点对齐乐谱起点，其余音节按实测相对位置落位。回执同时标出每个音节相对乐谱的偏差，供用户检查。
- 二期再加的选项（v1 不做）：「量化到乐谱」时间拉伸，把 VocalRender 的输出按音节拉伸回乐谱时值。v1 只做到诚实贴片，不做拉伸。
- 与 G3 的关系：本设计就是 G3 的第三方接口路线。Yeban 侧新增的只是 HTTP 客户端与两个 MCP 工具，不碰引擎 crate，不新增引擎依赖。

## 7. 远端：Colab CLI 跑服务

### 7.1 形态

Colab CLI 会话内跑 FastAPI 服务，cloudflared 隧道把端口暴露成公网 HTTPS 地址，Yeban 配这个地址 ＋ API key 调用。

### 7.2 我们已有的 Colab 基础

- 工具：~/workspace/tools/colab-cli-venv/bin/colab，oauth2 登录，账号 gradetwo@gmail.com，验证时余额 354.07 计算单元。
- 会话：CPU/GPU 会话均可开，GPU 档位有 T4/L4/A100 等；长任务用 ssh/console detached 跑、轮询日志文件。
- 限制：会话硬上限 12 小时（免费）/ 24 小时（Pro+）；会话盘随会话死亡；计费按档位乘 wall-clock。

### 7.3 隧道两种

- quick tunnel：`cloudflared tunnel --url http://localhost:8000`，免账号即开即用，得到随机 trycloudflare.com 地址。缺点：每次启动地址都变，Yeban 侧每次都要更新服务地址。
- named tunnel：固定域名，需 Cloudflare 账号与域名。我们已有现成经验：本机 SSH 就是 cloudflared named tunnel（bigbangbang.wangda.today），同一机制可复用。优点：地址固定，Yeban 配置一次。
- 建议：开发期 quick tunnel，固定使用期切 named tunnel。

### 7.4 权重持久化

- 会话盘随会话死，权重不能只放会话盘。启动脚本流程：先查 Google Drive 挂载缓存，有则直接用，无则从 Hugging Face 下载（SoulX 约 6 GB，VocalRender 9.5 GB），下载完写回 Drive 缓存。
- 每次会话启动都要付一次模型加载与（无缓存时）下载成本，写进启动脚本的预算里，不藏在「服务已就绪」后面。

### 7.5 GPU 档位建议（Phase 0 实测后修正）

- SoulX：T4 足够（bf16 模型小，8 GB 以下显存可跑）。
- VocalRender：建议 L4 起步。自回归扩散 ＋ 9.5 GB 权重，T4 16 GB 能跑但慢，实测定档位。

### 7.6 安全

- API key 必开。tunnel URL 不公开张贴。参考音频只存会话内与声音库，不作训练，不二次分发。

## 8. Docker 支持（三期）

- 镜像内容：CUDA 基础镜像 ＋ 服务代码 ＋ 推理依赖。权重不进镜像，用 volume 挂载。理由两条：9.5 GB 的镜像层分发成本高；权重许可与代码许可要分开处理。
- compose 文件一键起：服务 ＋ 权重 volume ＋ 端口 ＋ API key 环境变量。
- 同一镜像可跑本地 Linux GPU 服务器与云 GPU 主机。部署目标变了，服务代码不变。
- 诚实边界再写一次：macOS 上 Docker 没有 GPU 直通。Docker 模式服务的是 Linux GPU 服务器，不是 Mac 本地用户。Mac 本地用户永远走原生 MPS/MLX 路线。

## 9. 分期与验收

- Phase 0 实测 spike（已授权，2026-11-02 执行中）：两模型在 Colab GPU 上跑通官方 demo；测实时系数、SoulX 输出采样率、SoulX 时钟偏差、VocalRender 实测对齐方法的可行性；新增必测量（硬性要求之门）：同乐谱同音色、换 2–3 个 seed，逐对报是否逐位相同、相关度、起点偏差，结论 seed 变化是否满足批量每遍互不相同；若 seed 不起作用，试该模型官方暴露的其他采样参数（只试真实存在的参数），仍不行则记该模型在 takes 上不达标，供 Phase 1 选型；Mac 上测 MPS 可用性与实时系数（本 VM 做不了，记为 Wang 的 Mac 上待跑项）。交付一份实测报告，并据此定 MLX 是否立项。
- Phase 1：服务骨架 ＋ SoulX 适配器 ＋ 任务 API ＋ Yeban 客户端工具（本地 MPS 与 Colab 远端双通）。验收：Yeban 内提交乐谱，取回 wav，贴轨，播放位置与乐谱一致（耳检 ＋ 实测起点偏差报告）。
- Phase 2：VocalRender 适配器 ＋ 对齐回执 ＋ Yeban 实测贴片。验收：同 Phase 1，另加偏差报告可见、VocalRender 片段按实测位置落位。
- Phase 3：MLX 评估或移植（仅当 Phase 0/1 的 MPS 数据触发条件）；Docker 打包 ＋ named tunnel 固定端点。验收：镜像在 Linux GPU 机上一键起通；固定域名可调通。

## 10. 待 Wang 定夺

- 问题：默认模型是否定 SoulX。选项：SoulX 默认，VocalRender 作音质选项；或反过来。建议：SoulX 默认。理由：时钟准是 DAW 的硬需求，音质是可选项。
- 问题：macOS 本地 v1 是否只做 MPS，MLX 留二期。选项：只做 MPS；或 v1 就投 MLX 移植。建议：只做 MPS。理由：移植以周计，先让服务跑通，再决定值不值得移植。
- 问题：远端地址开发期是否接受 quick tunnel 轮换。选项：开发期 quick，固定使用期切 named；或直接上 named。建议：开发期 quick。理由：named 需要 Cloudflare 账号与域名配置，开发期不值得先付这个成本。
- 问题：声音库口径是否只收用户自有或授权参考音。选项：只收自有或授权；或不设限。建议：只收自有或授权。理由：SoulX 官方 disclaimer 同款口径， impersonation 风险不由服务背。
- 问题：仓库是否独立新库。选项：独立新库（建议名 svs-api）；或放进 yeban 仓库。建议：独立新库。理由：G3 定夺要求 Yeban 本体不含 SVS 实现，放进同一仓库会把 Python 推理依赖与 Rust 引擎的边界重新搅浑。（2026-11-02 Wang 定：独立新库，名 伶人。）
- 问题：是否启动 Phase 0 实测。建议：启动。理由：输出采样率、SoulX 时钟偏差、VocalRender 对齐方法、MPS 实时系数这四项，全是设计里写「待实测」的项，不实测就定不下来 Phase 1 的工期。

## 11. 来源

- SoulX-Singer 仓库：https://github.com/Soul-AILab/SoulX-Singer
- SoulX-Singer 论文：https://arxiv.org/abs/2602.07803
- SoulX-Singer 权重：https://huggingface.co/Soul-AILab/SoulX-Singer
- VocalRender 仓库：https://github.com/pymaster17/VocalRender
- VocalRender 论文：https://arxiv.org/abs/2607.27768
- VocalRender 权重：https://huggingface.co/pymaster/VocalRender
- MLX 移植先例 IndexTTS-2.5-mlx：https://pypi.org/project/index-tts-2.5-mlx/
- MLX 移植先例 drama3-mac：https://github.com/JordiPosthumus/drama3-mac
- 隧道方案参考：cloudflared quick tunnel 与 named tunnel（Cloudflare 官方文档与社区实践）
- MuScriptor 仓库：https://github.com/muscriptor/muscriptor
- MuScriptor 论文：https://arxiv.org/abs/2607.08168
- MuScriptor 权重：https://huggingface.co/MuScriptor（small／medium／large，CC BY-NC 4.0 门控）
- NeuralNote 仓库：https://github.com/DamRsn/NeuralNote
- NeuralNote GGUF 权重：https://huggingface.co/DamRsn/muscriptor-gguf

## 12. MuScriptor 转录（2026-11-02 新增，分支 lingren-muscriptor-transcribe）

### 12.0 结论先行

- 转录不是第三个合成适配器。合成适配器的接口是（乐谱＋voice＋seed）→ 一遍 wav；转录的输入是一段音频，输出是音符＋MIDI，没有 voice、没有 seed、没有 takes 门。硬塞进 BaseAdapter 会把接口语义搅浑，所以转录是独立作业类型，与 synthesize 共享任务状态端点，不共享执行路径。
- 接口形状：POST /v1/transcribe（multipart 音频上传）→ job_id；GET /v1/jobs/{id}（与合成作业同一端点）；GET /v1/jobs/{id}/midi（取 MIDI）；GET /v1/jobs/{id}/receipt（取音符回执）。synthesize 与 svs_job 端点行为不动。
- 选型：走 muscriptor 的 Python 包（safetensors）路线，不走 NeuralNote 的 GGUF 路线。理由见 12.4（证据在先，结论在后）。
- 档位：缺省 small；Colab GPU 上 large；medium 只作显式选项。理由见 12.3。
- 许可：代码 MIT，权重 CC BY-NC 4.0（门控下载）。权重永不进仓库、永不进镜像；缺授权响亮失败；NC 约束写进 README 与设计文档，不藏。Wang 立场（学习用途下 NC 不构成障碍）已记，但 shipped 功能必须明示约束。

### 12.1 模型调研（全部以仓库源码为准）

- 来源：Kyutai × Mirelo，论文 arXiv:2607.08168。decoder-only transformer ＋ mel 频谱前缀条件；输入 16 kHz 单声道（内部重采样），音频按 5 秒分块，块内事件按时间顺序产出。
- 三档：small 约 100M（14 层／768 维／12 头）、medium 约 300M（24 层／1024 维／16 头）、large 约 1.3B（48 层／1536 维／24 头）。三档共用输入管线、tokenizer 与训练配方，只差深度与宽度。
- Python API（muscriptor/transcription_model.py，实测源码）：
  - `TranscriptionModel.load_model(weights_path=None|'small'|'medium'|'large'|本地 safetensors 路径|hf://|https://, device=None|'auto'|'cpu'|'cuda'|'mps', dtype=None|'float32'|'float16'|'bfloat16')`。缺省 medium；device 缺省当前加速器否则 CPU；dtype 缺省 MPS 上 float16、其余 float32（MPS 上 float16 是因为解码受内存带宽限制）。
  - `transcribe(audio, use_sampling=False, temperature=1.0, cfg_coef=1.0, instruments: list[str]|None=None, batch_size=None, no_eos_is_ok=True, beam_size=1, prelude_forcing=True)` → 迭代器，产出 NoteStartEvent(pitch, start_time, index, instrument)／NoteEndEvent(end_time, start_event)／ProgressEvent(completed, total)。每个 NoteStart 必有唯一对应的 NoteEnd。
  - `transcribe_and_postprocess(..., detect_tempo='best-effort', export=ExportOptions)` → (midi_bytes, BeatGrid|None)。`events_to_midi_bytes(events, beat_grid, export)` 由 CLI 与 HTTP server 共用，同一输入的 MIDI 字节相同。
  - ExportOptions：bpm、beats_per_bar、first_downbeat、tempo_map、beat_scale（须为 2 的幂）、quantize、subdivision、strength、fill_gaps、trim_leading_bars。注意上游原话：量化是乐谱雕刻要的，不是听感要的；回执音符默认不量化，只在请求时量化进 MIDI。
- 乐器（muscriptor/tokenizer/mt3.py，MT3_FULL_PLUS_GROUP_NAMES，共 34 组）：acoustic_piano、electric_piano、chromatic_percussion、organ、acoustic_guitar、clean_electric_guitar、distorted_electric_guitar、acoustic_bass、electric_bass、violin、viola、cello、contrabass、orchestral_harp、timpani、string_ensemble、synth_strings、voice、orchestra_hit、trumpet、trombone、tuba、french_horn、brass_section、soprano_and_alto_sax、tenor_sax、baritone_sax、oboe、english_horn、bassoon、clarinet、flutes、synth_lead、synth_pad、drums。`instruments=None` = 模型自选；给定列表 = 硬掩码（不在列表的乐器根本不被解码，不是事后过滤）。
- 权重获取（muscriptor/utils/download.py）：门控 HF 仓库 MuScriptor/muscriptor-{small,medium,large}，缓存于 ~/.cache/muscriptor/（HF 仓库走 hf_hub_download）。未登录或未接受许可时抛 ModelDownloadError，服务必须把它翻成响亮的任务失败，不静默。
- CLI（muscriptor/main.py）：`uvx muscriptor transcribe audio.wav`，--format midi|json|jsonl|sheets（sheets 需另装 MuseScore 4+），--instruments（逗号分隔，大小写不敏感、无歧义缩写可接受，`muscriptor list-instruments` 列出全表），--detect-tempo true|false|best-effort（缺省 best-effort），--bpm／--beats-per-bar／--first-downbeat／--quantize／--grid 等 tempo 修正。

### 12.2 NeuralNote v2 的用法（参考点，不是照搬点）

- NeuralNote v2（2026-10-04）把 Spotify Basic Pitch 换成 MuScriptor：整曲按乐器转录、乐器自选或 Automatic、模型首次启动时下载、本地 GPU／CPU 跑、每乐器一轨 MIDI 导出（鼓 ch10）、删了 v1 的灵敏度／量化设置。
- 源码级用法（Lib/Model/MuscriptorEngine.h、ModelManifest.h，实测源码）：
  - 引擎包 `msl::Transcriber`（C++ 移植），模型不在两次转录之间驻留：进调用时加载、出调用前卸载（medium/F16 驻留超 1 GB，插件闲置时不值得持有；重载约 0.3 秒，相对慢于实时的转录可忽略）。
  - 乐器：空列表 = Automatic（模型自选，与 Python 的 instruments=None 同义）；非空 = 条件前缀 ＋ 解码器硬掩码，不是下游过滤（与 Python 语义一致，这一点两边互相印证）。
  - 音频：单声道 16 kHz（TRANSCRIPTION_SAMPLE_RATE = 16000，与 Python 的 _SAMPLE_RATE = 16000 一致）。
  - 音符流式产出（drainNewNotes 增量）＋ 最终权威全量（takeFinalNotes）；支持断点续跑（resume point）。伶人 v1 不做流式与续跑：作业跑完一次性回 MIDI ＋ 回执，流式是后续项。
  - 权重：DamRsn/muscriptor-gguf，钉死修订版 d7045f94…，f16 GGUF 三档 209,425,152／618,442,496／2,739,142,176 B，sha256 钉死在代码里（不是读上游 SHA256SUMS，变了就验不过）。
- 对伶人的可迁移点只有三条：Automatic 语义（空 = 自选）、乐器提示是硬掩码（不是过滤器）、每乐器一轨 MIDI。C++ 引擎、GGUF、流式钢琴卷、断点续跑都不迁移（理由见 12.4）。

### 12.3 落点与接口形状

- 落点：新模块 app/transcribe.py（请求模式、作业记录、转录 worker）＋ app/muscriptor_driver.py（子进程驱动，与 vr_driver.py 同款隔离：torch／CUDA 代码不出 API 进程）。不复用 BaseAdapter（12.0 的理由），不碰 jobs.py 的合成路径（只加一把模型锁，见下）。
- 端点：
  - POST /v1/transcribe：multipart，file=音频（wav／mp3／flac 等，上游 _load_wav 接受的格式）；表单字段 model（small|medium|large，缺省见 12.3 档位）、instruments（逗号分隔，空 = Automatic）、bpm、beats_per_bar、first_downbeat、quantize、grid、detect_tempo（缺省 best-effort）。提交时校验乐器名（精确匹配 34 组表，未知名返回 400 并附全表；上游接受无歧义缩写，但服务层不接受缩写——避免静默错解，写死在文档里）。返回 {job_id, status}。
  - GET /v1/jobs/{id}：合成与转录共用。转录作业的记录含 kind="transcribe"、progress（已完成块／总块，来自 ProgressEvent）、audio_duration_s、notes_total；没有 parts／takes 字段（转录没有这两个概念，不造假字段）。
  - GET /v1/jobs/{id}/midi：转录作业的 MIDI 字节（上游 events_to_midi_bytes 的原样输出）。合成作业调此端点返回 400（没有 MIDI 可取，写明原因）。
  - GET /v1/jobs/{id}/audio：转录作业调此端点返回 400（转录不产 wav takes，写明去 /midi）。
  - GET /v1/jobs/{id}/receipt：转录回执 JSON：model、variant、device、instruments（请求值与实际检出值）、duration_s、rtf（音频时长／墙钟比，实测值）、beat_grid（bpm／beats_per_bar／first_downbeat，可能为 null）、notes（按乐器分组：[{instrument, pitch, start_s, end_s}]，start_s／end_s 是模型原始时值，未量化；量化只进 MIDI，不进回执）、input_rms_db、warnings（notes_total=0 且输入非静默时记 empty_transcription 警告，不谎报失败也不谎报成功）。
- 档位（部署缺省，请求 model 可覆盖）：
  - 本地 CPU：small。唯一在 CPU 上有 plausible 实时性的档；medium／large 的自回归解码在 CPU 上慢于实时数倍，长音频不可接受。
  - macOS MPS：small（dtype 缺省 float16，解码受内存带宽限制，float16 减半流量）。medium 可显式选，但只作质量选项，不作缺省。
  - Colab GPU（L4）：large（Wang 2026-11-02 定：都用 Colab 就用 large；1.3B，GPU only，L4 显存有余量）。medium 只作显式选项。
  - 环境变量：MUSCRIPTOR_MODEL（档位或本地 safetensors 路径）、MUSCRIPTOR_DEVICE（auto|cpu|cuda|mps，缺省 auto）、MUSCRIPTOR_DTYPE（缺省按设备）。请求 model 优先于环境变量。
- worker 关系：转录 worker 独立线程＋独立队列（不与合成 worker 共用队列，长转录不堵合成任务的状态查询）；但模型执行共用一把 _MODEL_LOCK：合成适配器调用与转录运行都持锁进 GPU／MPS。锁是内部串行保证，不改变 synthesize 端点的任何响应形状。后续若要真并行（CPU 转录 ＋ GPU 合成），须先有设备级隔离证据，再改锁粒度，不先拍脑袋放开。
- 部署：
  - 本地 macOS：pip／uvx 装 muscriptor，HF 登录并接受 CC BY-NC 许可，MUSCRIPTOR_MODEL=small，device=mps。Intel Mac 走上游文档的 Python ≤3.12＋torch 2.2.2 路线（本机不验，写明出处）。
  - Colab 远端：同一服务代码，device=cuda，model=large；权重走 §7.4 的 Drive 缓存模式（会话盘随会话死，无缓存时重下，成本写进启动预算）。
  - Docker：CUDA 基础镜像 ＋ 服务代码 ＋ 推理依赖；权重用 volume 挂载，永不烘进镜像（与 §8 同款理由：分发成本 ＋ 许可分离）。macOS 上 Docker 无 GPU 直通，Docker 模式不服务 Mac 本地用户。
- 驱动命令覆盖：MUSCRIPTOR_DRIVER_CMD（缺省：sys.executable ＋ 内置 app/muscriptor_driver.py）。测试用 fake CLI 走同一覆盖点，不是测试专用后门；算子可指向自装 muscriptor。

### 12.4 GGUF 与 safetensors 的取舍（证据在先）

- NeuralNote 用 GGUF 的原因是它是 C++/JUCE 插件，跑不了 torch；GGUF 来自 DamRsn/muscriptor-gguf（第三方转换，钉死修订版与 sha256）。伶人是 Python FastAPI 服务，上游 Python 包（pip／uvx 可装、safetensors、有文档的 Python API）是原生路线；为 GGUF 引入 C++ 运行时或新的 native 依赖，零收益。
- 因此：v1 走 Python safetensors 路线，子进程驱动（与 SoulX／VR 适配器同款隔离）。GGUF 只记为后续项：仅当出现 C++ 嵌入需求时再评估，不先做。
- 权重体积参考（GGUF f16，作部署预算的量级锚点，不是 safetensors 的实测值）：small 约 210MB、medium 约 620MB、large 约 2.7GB。safetensors 各档以 HF 页面标称与实测下载为准，不拿 GGUF 数冒充。

### 12.5 许可处理（写死，不藏）

- 代码 MIT（muscriptor 仓库 LICENSE）；权重 CC BY-NC 4.0（非商业使用），门控下载（HF 登录 ＋ 接受许可）。
- 四条铁律：权重永不进仓库（.gitignore 外加文档明示，CI 不验但 code review 验）；权重永不进 Docker 镜像与发布包；缺授权时任务响亮失败（上游 ModelDownloadError 原文进 error 字段，不翻译、不吞掉）；README、设计文档、本节、服务端点文档字符串四处明示 NC 约束（非商业使用）。
- Wang 立场已记：学习用途下 NC 不构成障碍；但 shipped 功能必须把约束写明，不能藏。上传端点文档字符串另记上游条款：不要转录无权音频（只作提示，不作法律门禁——服务无法验证版权归属，诚实写明边界）。
- 转录产物（MIDI、回执）是用户自有音频的转录结果，归用户；服务不二次分发。

### 12.6 与夜半的关系（本任务范围边界）

- 本任务只做服务侧：转录端点 ＋ 作业 ＋ 回执 ＋ 客户端方法（LingrenClient.transcribe／poll／fetch_midi／fetch_receipt，stdlib 同款）。
- 回执音符格式刻意贴近夜半的音符字段（instrument、pitch（MIDI）、start_s、end_s），未来 MCP 工具可 1:1 映射，不另设转换层。
- 后续项（不硬做）：夜半 MCP 工具（例如 transcribe_audio 提交 ＋ 转录结果导入：MIDI 走现有 import_midi 路径，或音符走 edit_notes）；sheets 格式（MusicXML／PDF，需 MuseScore 4+，服务侧不装）；流式进度推送（v1 只有轮询 progress 块数）；断点续跑（NeuralNote 有，伶人 v1 没有）。
