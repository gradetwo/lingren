# 伶人 SVS API 服务 — Phase 2（+ VocalRender 适配器）

设计文档：`../RESEARCH-AND-DESIGN.md`；Phase 0 实测：`../phase0/REPORT.md`；
Phase 1 报告：`../phase1/REPORT.md`；补丁：`../phase1/patches/`（推理代码
的改动全部以补丁文件存档）。本目录是 Phase 1 服务的副本 + VR 接线，
Phase 1 原目录未动。

## 跑起来（本地开发，stub 适配器，不用 GPU）

```bash
cd service
python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
SVS_DATA_DIR=./data ../.venv/bin/uvicorn app.main:app --port 8000
```

健康检查：`curl localhost:8000/health`。API 契约见设计 §5：

- `POST /v1/voices`：multipart，`file`（参考音频）+ 可选 `prompt_meta`
  （SoulX prompt-meta JSON）。SoulX 合成必须有 prompt_meta；只有音频
  没有 prompt_meta 的 voice 会注册成功但 `soulx_ready=false`，合成时
  响亮失败（不静默）。全量 prompt 预处理流水线（从生音频自动对齐出
  prompt_meta）是 Phase 2 项。
- `POST /v1/synthesize`：`{"parts": [...]}`，每 part 含 label、score
  （统一乐谱 JSON）、voice_id、model（`soulx` 默认；`vocalrender` 走
  VR 适配器，见下）、takes、seeds、options。model 按 part 指定，一
  个作业可混装 soulx part 与 vocalrender part。单 part 即 parts
  长度 1，不另设端点。
- `GET /v1/jobs/{id}`：状态与 parts_completed/total、takes_completed。
- `GET /v1/jobs/{id}/audio?part=<label>&take=<n>`：取某遍 wav。
- `GET /v1/jobs/{id}/receipt`：回执（每遍 seed、耗时、对齐数组、门记录）。

鉴权：设 `SVS_API_KEY` 后要求 `Authorization: Bearer <key>`；远端
tunnel 部署必须设，本地开发可不设。

## SoulX 适配器（需 GPU 机器：Colab CUDA 或 Mac MPS）

### 可选的作业内模型复用与耗时回执

默认 `SOULX_REUSE_MODEL=0` 保留每遍一个子进程的执行方式。设置为 `1`
后，同一作业内连续的 SoulX 唱段和 takes 共用一个通过 `spawn` 启动的
子进程，只加载一次模型；切换到其它模型、任务完成、任务失败或单次
推理超时后释放子进程。不会跨作业长期占用 GPU，也不让两个模型同时
驻留。单次请求超时仍为 3600 秒。

复用模式每遍重新设置随机种子，并重新读取 prompt/target 元数据。
默认 CLI 仍保留原先“模型初始化前设种子”的行为；复用模式的同一 seed
不承诺与旧模式波形相同。需要逐采样复现旧结果时保持默认模式。

作业状态增加 `stage`、`current_part`、`current_take`、`current_attempt`
以及终态 `timings`。最终回执先原子发布，再暴露 completed/failed 状态；
普通转换错误、音频读取错误与子进程超时也会产生失败回执。存储不可写
仍可能导致回执无法落盘，客户端必须同时检查任务失败状态。

耗时字段单位均为秒，使用单调时钟；作业提供排队、运行和总耗时，每遍
提供合成、对齐及总耗时。SoulX `adapter_info.timings` 另含 runtime
初始化、模型加载、预处理、推理、写 WAV 与适配器墙钟时间。模型加载
包含在 runtime 初始化内，不能将这两个字段相加。MPS/CUDA 的相关阶段
同步设备后计时；热请求的初始化/加载时间为零，`model_reused=true`。

该开关需要在下一次服务启动时设置；修改源码不会自动重启现有服务。

环境变量：`SOULX_REPO`（打好 `../patches/00-02` 的检出）、
`SOULX_MODEL_PATH`、`SOULX_CONFIG`、`SOULX_PHONESET`、`SOULX_DEVICE`
（cuda/mps/cpu）、`SOULX_FP16`（cuda 缺省 1）。适配器以子进程跑
`app/soulx_driver.py`，一遍一进程，一遍一种子；驱动打印的最后一行
JSON 含 `f0_shift_semitones`（补丁 01；未打补丁时为 null）。

乐谱转换：`app/score_converter.py` 把统一乐谱转 SoulX target_meta
（一段）。note_type：1 休止、2 常规、3 延音（tie）。phoneme 缺省走
G2P（普通话 g2pM、粤语 ToJyutping、英语 g2p_en）；G2P 不可用时必须
在 note 里显式给 `phoneme`（如 `zh_zhi3`），否则响亮失败。

## VocalRender 适配器（Phase 2 新增）

环境变量：`VR_REPO`（打好 `../phase1/patches/03` 的检出）、
`VR_CKPT_DIR`（`--ckpt_dir`，即 pymaster/VocalRender 快照里的
`VocalRender` 子目录）。适配器以子进程跑 `app/vr_driver.py`，一遍
一进程，一遍一种子（补丁 03 的 `--seed`）；乐谱经
`score_converter.vr_label_entry` 转单条目 label（list 包裹，item 名
`lingren_<label>`），音符 token 是 `<NOTE_4>` 分母制（补丁 05）；
超出 0.125–6.0 拍的音符在适配器层响亮拒绝，不静默量化。

Voice 要求：VR 只需参考音频（作 `--prompt_audio`），不需要 SoulX
prompt_meta；prompt 应为 2–8 秒干净歌声，实际时长记进回执
`adapter_info.prompt_duration_s`，超出范围只告警不拒跑。
回执对齐：VR 自定时值，回执的实测起点就是贴轨依据；SP（休止）
条目记 `ignored=true` 且实测起点为 null，放置时跳过（对齐只对
有歌词音符跑候选指派，SP 不消耗候选）。

takes 门对 VR 同样生效（`app/gate.py`，阈值不变），VR 无原生 seed
时用补丁 03 的 `--seed`；门失败换 seed 最多重试 3 次，耗尽响亮
失败，绝不把相同音频当多遍返回。

## takes 差异门（`app/gate.py`，Phase 0 数据定阈值）

新遍与每个已接受遍逐对验三项：非逐位相同；包络 log-RMS 相关度
< 0.99；F0 中位差 > 5 音分或起音中位差 > 10 ms（至少一处激活）。
单遍失败换 seed 最多重试 3 次（seed + 1000×attempt），仍失败则任务
响亮失败，回执里留门记录。样本级波形相关度不用作门（相位敏感，
会误杀真微差遍）。

## 对齐（`app/alignment.py`）

能量起音 + F0 稳定变音点联合候选，相对乐谱时值单调指派；无候选
的音节记 `measured_onset_s: null`，不编造。SoulX：回执只作校验，
贴轨仍按乐谱时钟；VocalRender（Phase 2）：回执的实测起点就是贴轨
依据。

## Yeban 侧客户端（`client/svs_client.py`，stdlib-only）

`LingrenClient`：`upload_voice` / `synthesize` / `wait` / `fetch_receipt`
/ `fetch_audio`，对应设计 §6 的两个 MCP 工具（synthesize_singing、
svs_job）的 HTTP 层；Yeban 仓库内的 MCP 工具包装是后续集成项，不
在本目录（本 Phase 不改 yeban 仓库）。转录新增 `transcribe` /
`fetch_midi`（见下）。

## MuScriptor 转录（设计 §12；服务侧）

- `POST /v1/transcribe`：multipart，`file`（待转录音频）+ 表单字段
  `model`（`small` 缺省；`medium`/`large`）、`instruments`（逗号分隔
  的精确乐器组名，空 = Automatic，模型自选；给出的列表是硬掩码，
  不在列表里的乐器不会被解码，不是事后过滤）、`bpm` /
  `beats_per_bar` / `first_downbeat` / `quantize` / `grid` /
  `detect_tempo`（`best-effort` 缺省）。返回 `{job_id, status}`。
- `GET /v1/jobs/{id}`：转录作业共用（`kind: "transcribe"`，进度为
  `completed_chunks/total_chunks`，无 parts/takes 字段）。
- `GET /v1/jobs/{id}/midi`：转录产物（MIDI 字节，上游
  `events_to_midi_bytes` 原样）；合成作业调它得 400。`/audio`
  对转录作业得 400（指向 `/midi`）。
- `GET /v1/jobs/{id}/receipt`：回执含 model、device、请求与实测乐器、
  duration_s、elapsed_s、rtf、beat_grid（可为 null）、notes（按乐器
  分组的原始模型时间音符；量化只进 MIDI，不进回执）、input_rms_db、
  warnings（非静默输入 0 音符时为 `empty_transcription`）。

环境变量：`MUSCRIPTOR_MODEL`（档位或本地 safetensors 路径；请求
`model` 字段只接受三档关键词）、`MUSCRIPTOR_DEVICE`（auto 缺省；
cpu/cuda/mps）、`MUSCRIPTOR_DTYPE`（缺省按设备走上游默认）、
`MUSCRIPTOR_DRIVER_CMD`（驱动命令，缺省为本机 python +
`app/muscriptor_driver.py`；测试用假 CLI 也走同一口子）。
驱动以子进程跑，torch/CUDA 代码不出 API 进程；API 进程永不 import
muscriptor，`requirements.txt` 不加 muscriptor（驱动环境另装，缺包
时驱动响亮报错，告诉运维装包）。

档位缺省：`small`（唯一 CPU 可行档；medium/large 在 CPU 上自回归
解码慢于实时数倍）。macOS MPS 缺省仍 `small`（MPS 下 float16
默认，解码带宽受限；medium 是显式质量选项）。Colab GPU（L4）用
`large`（Wang 2026-11-02 定）。`medium` 只显式指定。

权重许可：MuScriptor 代码 MIT，权重 CC BY-NC 4.0。权重经
HuggingFace 门控下载（账号 + 接受许可），绝不进仓库、绝不进
Docker 镜像与发布产物；未授权时作业响亮失败并原样携带上游
ModelDownloadError 文本。学习用途下 NC 不构成障碍，但约束必须
写明（本节 + 设计 §12 + 端点 docstring），不能藏；上传端点另注明
“不要转录无权音频”（告知义务，非法律门禁——服务无法核验权属）。

worker 关系：转录有独立队列 + 线程（长转录不阻塞合成作业状态），
但模型执行与合成适配器共用一把 `MODEL_LOCK`（`app/jobs.py`），
GPU/MPS 上两模型永不重叠；CPU 转录 + GPU 合成的真并行需设备隔离
证据后才放开。

本任务只做服务侧；转录 → 夜半的 MCP 工具接线、sheets 格式
（需 MuseScore 4+，服务不装）、流式进度推送、断点续跑均为后续项
（见设计 §12 末尾）。

## 声音库口径与许可

参考音频只收用户自有或授权音频（Wang 定口径）。服务不训练、不
二次分发参考音频；合成输出是普通录音片段，走 Yeban 现有音频片段
机制。两模型均为 Apache-2.0（代码与权重同许可，见设计 §1）。

## 本线不做的事（Phase 2 其他线）

- 全量 prompt 预处理流水线（生音频 → prompt_meta 自动对齐）。
- 命名 tunnel 固定端点、Docker 打包（设计 §7/§8，三期）。
- Mac MPS 实测（本机 Linux 做不了，Wang 的 Mac 上待跑；触发条件：
  SoulX MPS RTF > 2 则启动 MLX 二期）。
- Yeban 仓库内 MCP 工具本体（本目录只给 HTTP 客户端层）。
- phase2/REPORT.md（总协调的）；phase0/ 与 phase1/ 未动。
