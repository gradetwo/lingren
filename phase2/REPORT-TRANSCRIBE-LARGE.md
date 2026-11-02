# MuScriptor large — Colab L4 实测报告（分支 lingren-muscriptor-transcribe）

本报告是 `phase2/REPORT-TRANSCRIBE.md` 之外的追加实测，不改该报告正文。
分支状态：尖端 `a68baa7`（Colab 缺省 large 的文档已进），套件 20/20（父 agent 已验）。
本次实测未改分支任何代码与文档，推送与合入留给父 agent。

## 1. 连接器

- 连接器 `custom.huggingface` 已新建：文档
  `~/workspace/custom-connectors/custom.huggingface/CONNECTOR.md`，
  helper `~/workspace/custom-connectors/custom.huggingface/bin/hf_download.py`
 （`py_compile` 通过）。
- 机制：helper 经 `dynamic_credentials` 在 `Authorization` 头放 `hsurr:`
  surrogate，出口层在注册 host 上换成真实 token。helper 从不打印、
  不记录、不存储 surrogate 与真实 token。
- 注册信息：`provider huggingface`，`auth_scheme api_key`，
  `placement bearer_header`，`api_hosts huggingface.co、cdn-lfs.huggingface.co`。
- 认证与许可证明：`config.json` 首次下载即成功（125 B），说明 token
  有效且模型页 CC BY-NC 许可已接受。门控仓库在二者缺一时返回
  401／403，本次全程未遇到。

## 2. 权重下载（本机，经连接器）

- 仓库 `MuScriptor/muscriptor-large`（门控 `gated: auto`），文件：
  - `model.safetensors`：5,465,642,136 字节，
    sha256 `ac4eb6ea87dfc26b6ca6b954c6b967ab87ad4c7d08e078b25214f13ed051f397`，
    文件头可解析（44,176 字节头，394 个张量）。
  - `config.json`：125 字节，`variant large，dim 1536，num_heads 24，
    num_layers 48，card 1395`。
- 落点 `~/workspace/hf-weights/muscriptor-large/`（仓库之外，永不进仓）。
- 下载方式：先向 `resolve/main/<file>` 发带 surrogate 的请求（禁跟随
  跳转），200 直接流式落盘，302 则取签名存储 URL（不带 auth）流式
  落盘。权重 CC BY-NC 4.0，永不进仓库或镜像。

## 3. Colab 会话

- 会话 `muscriptor-large-test`，L4，创建约 08:43 UTC，停止 10:02 UTC
  （18:02 CST），停后查验：无活动会话，费率 0.00/hr。
- 余额：建会话前 521.08 CU，停后 515.81 CU，差 5.27 CU。
  该差值含同期 folk-v3-vr-pro L4 会话的消耗，不能全算在本会话头上。
  本会话墙钟约 1 小时 20 分，按 L4 1.94 CU/小时计约 2.6 CU。
- 本机环境：torch 2.11.0+cu130，cuda 可用；`pip install muscriptor
  pydantic scipy` 装通，muscriptor 可导入。
- 传包：`colab ssh --proxy-mode` 作 ProxyCommand 的 scp（显式
  `-i /home/hatch/.ssh/id_ed25519`＋`IdentitiesOnly=yes`，目标
  `root@colab-runtime`）。小文件与服务 app 目录一次成功；权重
  5.5 GB 在本机 VM 重启中断后改 `rsync --partial` 断点续传完成，
  到 Colab 后重验大小与 sha256，与本地下载完全一致。

## 4. 驱动实测（Colab L4，--model 本地路径 --device cuda）

测试音频 `scale.wav`：C 大调音阶 8 音（C4 D4 E4 F4 G4 A4 B4 C5，
每音 0.5 秒，共 4.0 秒，16 kHz 单声道，自有合成）。

### 4.1 原音频（scale.wav）

- 退出码 0。回执：`duration_s 4.0，notes_total 8，input_rms_db -13.47`，
  `beat_grid bpm 120.0、beats_per_bar 4、first_downbeat 0.0`（无节拍
  音频的检测值，不是音频自带的速度声明）。
- 计时（run-timed 切分：首条 progress 行之前算加载）：
  墙钟 15.566 秒，加载 11.766 秒，转录 3.271 秒。
  转录单段速度 4.0／3.271＝1.22 倍实时；含加载 4.0／15.566＝0.26 倍实时。
  短音频下加载占主导，这是正常现象，不是转录慢。
- 音高对照（容差：同音高且起点偏差 ≤0.30 秒）：
  7／8 命中，命中起点偏差 0.00–0.01 秒，乐器全判为 electric_piano
  （Automatic 选择，对自合成音阶合理）。
- 未命中：首音 C4（60，起点 0.0 秒）未检出；另有 1 条多余音：
  74 音，4.01–4.02 秒（10 毫秒，紧贴尾部）。

### 4.2 诊断：前置 0.5 秒静音（scale-padded.wav）

- 为判断首音丢失是否边界 artifact，把同一音阶前置 0.5 秒静音
  （8 音移到 0.5–4.5 秒）重跑：8／8 全中，起点偏差 0.00–0.01 秒。
- 计时：墙钟 6.892 秒，加载 4.603 秒，转录 1.77 秒（加载比首跑快，
  推断为页面缓存变暖，未做隔离证实）。
- 多余音仍是 74 音 10 毫秒（4.51–4.52 秒，紧贴尾部）。
- 结论：首音丢失是起点 0.0 秒边界 artifact，不是音高听不出来；
  尾部 10 毫秒 74 音是释放处小毛刺，两处都不影响音高判读。
  正式 8 音对照以 4.1 为准（7／8），诊断结果只作原因说明。

## 5. 服务全链（Colab L4，服务跑在同一会话）

- 启动：`SVS_DATA_DIR=/content/svs-data
  MUSCRIPTOR_MODEL=/content/muscriptor-large/model.safetensors
  MUSCRIPTOR_DEVICE=cuda uvicorn app.main:app --port 8000`
  （不设 SVS_API_KEY，本地服务 auth 直接放行；请求未带 model 字段，
  走环境变量的本地路径）。
- `/health` 200。提交 `POST /v1/transcribe`（仅音频文件）得
  `job_id 9733f76c4aae4ffa，status queued`；轮询一次即
  `completed`，`kind transcribe，audio_duration_s 4.0，notes_total 8，
  progress 1／1 块，error null`。
- `GET /v1/jobs/{id}/midi`：200，153 字节，魔数 `MThd`。
- `GET /v1/jobs/{id}/receipt`：`model` 为本地路径，
  `device cuda，duration_s 4.0，elapsed_s 6.811`，
  `rtf 0.5873`（服务定义：duration／elapsed，含驱动子进程加载），
  `warnings []`，音符与 4.1 的直接驱动结果逐项一致
  （62、64、65、67、69、71、72＋尾部 74 毛刺，C4 在 0.0 秒处同样缺失）。

## 6. 结论与遗留

- large 在 Colab L4 上真正跑通：驱动与服务全链均成功，音高对照
  7／8（原音频）／8／8（前置静音诊断），MIDI 与回执齐全。
- 已知小毛刺（不阻塞）：起点 0.0 秒首音边界丢失；尾部 10 毫秒
  74 音。两者都是边界 artifact，不是模型音高错误。
- 未做：分支代码与文档零改动；合入 main 由父 agent 定。
- 过程问题（已解决，记下来防重踩）：
  - `colab exec -f /dev/stdin`＋heredoc 会挂住内核槽（两次实证），
    探针一律先写实文件再 `-f`。
  - L4 新建曾三拒（两路他方 L4 会话占满配额），第四次在 folk-v5
    会话消失后成功；不要把容量拒绝当成配置错误反复重试。
  - 本机 VM 重启会断 scp，改 `rsync --partial` 续传后完成。

## 7. 证据

- Colab 侧原始日志与产物已取回：
  `~/workspace/svs-api-service/phase2/large-test/evidence/`（notes.json、
  notes-padded.json、out.mid、out-padded.mid、driver.log、
  driver-padded.log、chain.log）。
- 本地脚本：
  `~/workspace/svs-api-service/phase2/large-test/`（run-timed.py、
  check-scale.py、chain.py、setup-detached.py、PROGRESS.md）。
- 权重：`~/workspace/hf-weights/muscriptor-large/`（仓库之外）。
