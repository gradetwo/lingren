# MuScriptor 转录 — 实施与实测报告（分支 lingren-muscriptor-transcribe）

设计：`../RESEARCH-AND-DESIGN.md` §12。服务目录：`service/`（本报告的
“服务”均指 `phase2/service/`）。

## 1. 落点（已实现）

转录是独立作业类型，不是第三个合成适配器（合成接口是（乐谱＋voice
＋seed）→ 一遍 wav；转录是（音频文件）→ 音符＋MIDI，无 voice、无
seed、无 takes 门，硬塞会搅浑接口语义）：

- 新模块 `service/app/transcribe.py`：34 组乐器表（与上游
  `MT3_FULL_PLUS_GROUP_NAMES` 逐项对过）、`TranscribeIn` 请求模式、
  独立队列＋独立线程的 `TranscriptionWorker`。
- 新模块 `service/app/muscriptor_driver.py`：子进程驱动（与 vr_driver.py
  同款隔离：torch/CUDA 代码不出 API 进程；muscriptor 导入延迟到
  `main()` 内，API 进程永不 import 它）。驱动复现上游
  `transcribe_and_postprocess` 的公开方法组合（`detect_beat_grid_for`
  ＋`transcribe` ＋`events_to_midi_bytes` ＋`resolve_grid`），只加一项：
  ProgressEvent 以 JSON 行输出，供 worker 报告块进度。
  `events_to_notes()` 是纯函数（duck-typed 事件），无 torch 也可单测。
- 端点（`service/app/main.py`，synthesize/svs_job 响应形状未动）：
  - `POST /v1/transcribe`（multipart 音频＋表单字段 model/instruments/
    bpm/beats_per_bar/first_downbeat/quantize/grid/detect_tempo）→
    `{job_id, status}`。乐器名提交时精确校验，未知名 400 并附全表
    （上游接受无歧义缩写，服务层故意不接受，避免静默错解）。
  - `GET /v1/jobs/{id}`：合成与转录共用；转录记录含 `kind:
    "transcribe"`、progress（completed_chunks/total_chunks）、
    audio_duration_s、notes_total，无 parts/takes 字段（不造假字段）。
  - `GET /v1/jobs/{id}/midi`：上游 `events_to_midi_bytes` 原样输出；
    合成作业调它得 400。`/audio` 对转录作业得 400（指向 `/midi`）。
  - `GET /v1/jobs/{id}/receipt`：双仓查找；回执含 model、device、请求
    与实测乐器、duration_s、elapsed_s、rtf、beat_grid（可为 null）、
    notes（按乐器分组的原始模型时值音符；量化只进 MIDI，不进回执——
    上游原话：量化是乐谱雕刻要的，不是听感要的）、input_rms_db、
    warnings（非静默输入 0 音符时为 `empty_transcription`）。
- 档位解析（请求 > 环境变量 > small）：请求 `model` 字段只接受三档
  关键词；`MUSCRIPTOR_MODEL` 环境变量可带档位或本地 safetensors 路径
  （运维控制，坏值由驱动响亮失败，不静默替换）。缺省 small（唯一 CPU
  可行档）；macOS MPS 缺省仍 small（float16 默认）；Colab GPU（L4）
  用 large（Wang 2026-11-02 定：Colab 就用 large）；medium 只显式指定。
- worker 关系：转录独立队列＋线程（长转录不阻塞合成作业状态），但模型
  执行与合成适配器共用 `app/jobs.py` 的 `MODEL_LOCK`，GPU/MPS 上两模型
  永不重叠；锁是内部串行保证，不改变 synthesize 端点响应形状。
  CPU 转录＋GPU 合成的真并行需设备隔离证据后才放开（未做）。
- 客户端：`service/client/svs_client.py` 新增 `transcribe()`（multipart，
  与 `upload_voice` 同款手工构造）与 `fetch_midi()`；`wait()` 与
  `fetch_receipt()` 复用。
- 许可：代码 MIT，权重 CC BY-NC 4.0。权重永不进仓库、永不进 Docker
  镜像与发布产物；缺授权时作业响亮失败并原样携带上游
  ModelDownloadError 文本；NC 约束写进 service README、设计 §12、
  端点 docstring（上传端点另注明“不要转录无权音频”，只作提示，不作
  法律门禁——服务无法核验权属）。`requirements.txt` 不加 muscriptor
  （驱动环境另装，缺包时驱动响亮报错并告诉运维装包）。
- 选型（设计 §12.4，证据在先）：走 muscriptor Python 包（safetensors）
  路线，不走 NeuralNote 的 GGUF 路线。NeuralNote 用 GGUF 是因为它是
  C++/JUCE 插件、跑不了 torch，GGUF 来自第三方转换
  DamRsn/muscriptor-gguf；伶人是 Python FastAPI 服务，上游 Python 包是
  原生路线，为 GGUF 引入 C++ 运行时零收益。GGUF 只记为后续项（仅当
  出现 C++ 嵌入需求时再评估）。

## 2. 测试（全部通过，不含任何真实模型调用）

测试环境：phase1 venv（fastapi＋pytest＋numpy＋soundfile），命令
`pytest tests/ -q`：**20/20 通过**。

- `tests/test_transcribe.py`（5）：假 CLI 端到端（提交→轮询→完成；
  回执音符形状；MIDI 以 MThd 开头；`/audio` 对转录作业 400；未知乐器
  提交 400；假 CLI 失败时作业响亮失败且 error 含 ModelDownloadError
  文本；合成作业调 `/midi` 400；档位解析：无 model 字段时 env 胜出、
  请求 model 覆盖 env、未知请求 model 400）。
- `tests/test_muscriptor_driver.py`（3）：`events_to_notes` 按 index 配对
  与排序、未闭合 start 丢弃不编造 end、孤 end 忽略。
- 既有 `test_api.py`、`test_gate.py`、`test_vr.py` 全过（合成路径未动）。
- 客户端另做一次性 echo-server 校验（未进套件）：`transcribe()` 发出的
  multipart 含 file/model/instruments/bpm 四字段，值正确。
- 假 CLI 只证接线，不证转录精度（测试 docstring 已写明）。

## 3. 真实模型实测 — 被 HF 门控挡住（未编造任何数字）

实测环境：独立 venv `~/workspace/tools/muscriptor-test-venv/`（不在仓库
内），`pip install muscriptor` 得 **muscriptor 0.4.0＋torch 2.15.0**
（安装本身踩了两个坑，已解决，见 §4）。自合成测试音频
`service/logs-transcribe/scale.wav`：C 大调音阶 8 音，每音 0.5 s，
共 4.0 s，16 kHz 单声道，自有合成，无版权音频。

实测步骤与结果：

1. 直接跑驱动（`--model small --device cpu`）：驱动跑到
   `TranscriptionModel.load_model('small')` 即失败，退出码 3，stderr
   为上游门控文本原样：
   “cannot download 'MuScriptor/muscriptor-small' from HuggingFace:
   the MuScriptor model weights are gated … 1. Accept the model
   license at https://huggingface.co/MuScriptor/muscriptor-small …
   2. Authenticate … set the HF_TOKEN environment variable”。
2. 门控状态精确定位（不读、不复制任何 token，只做布尔级探测）：
   - 本 agent 进程内无 HF token：shell 环境变量未设置、python 环境
     变量未设置、`~/.cache/huggingface/token` 不存在。
   - 仓库元数据公开可读（`repo_info("MuScriptor/muscriptor-small")`
     无 token 也成功）：门控卡在权重文件下载，不在仓库可见性。
   - 因此 blocker 是两层，缺一不可：(a) 运维须提供 HF token（`hf auth
     login` 或 `HF_TOKEN`）；(b) 该账号须先在模型页接受 CC BY-NC 许可
     （接受后自动放行；未接受时即使 token 有效也会被挡——本机无 token，
     (b) 的状态无法代验）。
3. 服务端到端复核（TestClient＋真实驱动，`MUSCRIPTOR_DRIVER_CMD` 指向
   实测 venv）：作业如设计失败，error 字段原样携带上游门控文本——
   失败路径本身已通过真实驱动验证，不是只靠假 CLI。
4. 结论：**RTF 与音符抽查均未测出**（权重一步未下载，模型一次未加载）。
   不写任何 RTF、音高命中率数字。Colab 路线不能绕过此 blocker（权重
   下载同样要 token＋许可），故未烧 CU 去跑一个已知会在同一关失败的
   任务。

运维放行后复测方法（留给父 agent／运维）：token＋许可就绪后，同一条
驱动命令（`--model small --device cpu`，scale.wav）重跑，记录回执
rtf、notes 与 8 个已知音高（C4 D4 E4 F4 G4 A4 B4 C5，每音 0.5 s）的
对照；再跑服务提交→轮询→`/midi`＋`/receipt` 全链。Colab 实测用 large（Wang 2026-11-02 定）。

## 4. 实测中修掉的两个真问题

- **驱动 device="auto" 被拒（真 bug，已修）**：worker 缺省传
  `MUSCRIPTOR_DEVICE=auto`，驱动原样传给 `load_model`，torch 报
  `Expected one of cpu, cuda, … device type at start of device string:
  auto`，作业失败（服务一轮端到端复核抓到，直接跑 `--device cpu`
  时没暴露）。上游 CLI 的做法是 `auto`→`None` 再调 `load_model`
  （0.4.0 main.py:434/579）；驱动已按同一映射修，修后复核走到真正
  的 HF 门控，套件仍 20/20。
- **pip 安装“No space left”假象（环境坑，已解决）**：家目录实有 78G
  空闲，报错根源是 `/tmp` tmpfs 512M 已满（98% 被其他任务文件占满，
  非本任务），pip 在 `/tmp` 解 torch 大包失败。改
  `TMPDIR=~/workspace/tools/pip-tmp` 后装通。另：一次 `pkill -f 'pip
  install muscriptor'` 把重装命令自身的 shell 一并杀掉（模式串命中
  自身命令行），已改走直接重装，不再用 pkill。

## 5. 未做项（后续，不硬做）

- 夜半 MCP 工具接线（transcribe_audio 提交＋结果导入：MIDI 走现有
  import_midi 路径，或音符走 edit_notes；回执格式已贴近夜半音符字段，
  可 1:1 映射）。
- sheets 格式（MusicXML／PDF，需 MuseScore 4+，服务侧不装）。
- 流式进度推送（v1 只有轮询 progress 块数）、断点续跑（NeuralNote 有，
  伶人 v1 没有）。
- 真实模型 RTF 与音符抽查（被 §3 blocker 挡住，待 token＋许可）。
- Mac MPS 实测（本机 Linux 做不了；触发条件与 SoulX 线同款口径）。

## 6. 交付清单

- 分支：`lingren-muscriptor-transcribe`（基底 main e9e7e78，**未推送**，
  父 agent 验后推）。
- 设计：`~/workspace/svs-api-service/RESEARCH-AND-DESIGN.md` §12。
- 本报告：`~/workspace/svs-api-service/phase2/REPORT-TRANSCRIBE.md`。
- 代码改动（`git status` 可验）：新文件 `service/app/transcribe.py`、
  `service/app/muscriptor_driver.py`、`service/tests/fake_muscriptor_cli.py`、
  `service/tests/test_transcribe.py`、`service/tests/test_muscriptor_driver.py`；
  改动 `service/app/main.py`、`service/app/jobs.py`、
  `service/client/svs_client.py`、`service/README.md`、设计文档 §12。
- 刻意未进分支：`service/logs-transcribe/`（工作笔记＋自合成测试音频＋
  安装/实测日志，untracked）。
- 未改动：`requirements.txt`、任何 synthesize/svs_job 端点行为、任何
  已有测试的期望值。
