# 伶人 SVS Phase 2 报告（2026-11-02）

总协调结论先写在前：**Phase 2 六项全部交付，其中四项实测闭合、两项诚实未建成/未构建（卡点已验明）**。
Yeban 仓库内 MCP 工具本体已提交在分支上，未推送（父 agent 推送并读分支 CI）。
`phase0/` 与 `phase1/` 未动；未来仓库名伶人只体现在文档里，未建新仓库。

默认口径（沿用 Phase 1，Wang 可纠正）：SoulX 默认模型；macOS v1 仅 PyTorch MPS；
声音库仅自有或授权参考音频；服务永不把相同音频当多遍返回（门失败有界重试、耗尽响亮失败）。

## 0. 花费（总协调亲验）

- Phase 2 开始余额 528.25 CU，结束 527.41 CU，**总花费 0.84 CU**，无活动会话。
- 各线自报数因并行窗口重叠，不能直接相加（VR 线 0.67、长乐句线 0.84 均以各自窗口量得）。

## 1. VocalRender 服务适配器接线 — 闭合

- 服务副本：`phase2/service/`（由 `phase1/service/` 完整复制，只改副本）。
  新增 `app/vr_driver.py`（子进程驱动，一遍一进程一种子，用补丁 03 的 `--seed`）
  与 `app/adapters/vocalrender.py`，`jobs.py` 占位符换成真适配器。
- 乐谱转换用 `<NOTE_4>` 分母制；超出 0.125–6.0 拍的音符响亮拒绝，不静默量化。
  一个作业可混装 soulx + vocalrender（按 part 指定模型）。
- 回执对齐每音符一条（`score_onset_s` / `measured_onset_s` / `deviation_ms` / `lyric` / `ignored`）；
  SP 条目 `ignored=true` 且实测起点为 null，不消耗检测候选（相对 Phase 1 的改进：
  Phase 1 会把邻音节候选指给休止）。
- 附带修正：`requirements.txt` 补 `python-multipart`（Phase 1 是 Colab 侧单独装的，服务依赖里漏了它）。
- 本地测试 12 项全过（门 5、API 1、VR 6：词表、超长音符拒绝、缺环境变量、SP 忽略、
  双模型混装作业、超长音符经 API 响亮失败）。
- Colab L4 端到端（会话 `svs-phase2-vr`，已停）：一个作业两个 part，作业号 `e1b68cd64b394d9b`，
  约 140 秒完成，4/4 遍首试通过。SoulX 门数与 Phase 1 逐数一致（包络 0.834、F0 2.72 音分、
  起音 21.33 ms），是对齐改动未伤无休止乐谱的回归证据。VR part（官方 demo prompt，6.166 秒，
  在 2–8 秒内）：两遍 4.16 / 4.0 秒、48,000 Hz；门记录齐全（包络 0.1993、F0 差 3.47 音分未激活、
  起音差 53.33 ms 激活），判定通过；对齐 8/8 有歌词音符测到起点，SP 正确忽略；
  `note_tokens` 9 条全为 `<NOTE_4>`。本地复算与回执逐数一致。
- VR 自定时值压缩如实记录：乐谱 4.5 秒 → 输出 4.16 / 4.0 秒，末音节偏差 -789.3 / -629.3 ms，
  幅度大于 Phase 0 demo，方向一致；放置必须用实测起点。
- 本线报告与证据：`phase2/vr-adapter/REPORT.md`、`phase2/vr-adapter/evidence/`、
  `phase2/vr-adapter/colab_e2e_vr.py`、`phase2/vr-adapter/recompute_vr.py`。
- 已知局限：VR 的 tie 未做 melisma 折叠（端到端未用 tie）；对齐检测器的单音节起点
  未经独立语音学核验，复算只证明回执由该 wav 经本线代码算出。

## 2. Yeban 仓库内 MCP 工具本体 — 提交在分支，未推送

- 分支：yeban 仓库 `line/svs-mcp-tools`，基底 `07315b14`，提交 `fe8e08e3`，
  12 文件 +3208/−20，工作树 `~/workspace/tmp-wt-yeban-svs` 干净（总协调亲验：分支、提交、
  文件数、干净状态、store.rs 未动均一致）。
- 实现：新模块 `crates/yeban-mcp/src/domain/svs.rs`（std-only HTTP 客户端，零新依赖，
  `Cargo.lock` 未动）：`POST /v1/synthesize`、`GET /v1/jobs/{id}`、receipt、audio；http only，
  超时 10 s / 120 s / 300 s；配置只从实参 `service_url` / 环境变量取，API key 只从环境变量取，
  不进工程文件。`synthesize_singing`（parts×takes 严格校验）与 `svs_job`（status / receipt / audio）
  按仓库 plan/apply 惯例实现；audio 落轨复用 `import_audio`，SoulX 按乐谱时钟、VocalRender 按回执
  实测起点贴轨、SP 休止忽略，稳定暂存路径 + 确定性 clipId 使重复取回幂等；
  服务不可达、任务失败、门耗尽全部响亮失败。
- 注册：`TOOL_COUNT` 26→28，扩展 16→18，schema enum / allOf / $defs 同步，对齐表 +80 行，
  新集成测试 `svs_tools.rs` 17 项（自含桩服务，覆盖提交/状态/回执/两种放置/幂等/失败/错配零请求）。
- 本地验证实测数：fmt 通过；clippy（yeban-mcp，--all-targets，-D warnings）0 告警；
  lib svs 单元 9/9；lib 全量 618 通过 + 1 败；svs_tools 17/17；tools_e2e 70 通过 + 1 败；
  contract 16/16；extension_tools 28/28；三门禁全绿（对齐门禁 80 行 / 28 工具 / 17 方法；
  schema 门禁；spec ID 审计 41 ID）。
- lib 与 tools_e2e 各 1 败均为环境性失败，非回归：本会话以 root 运行，root 无视权限位，
  `chmod 000` 文件实测仍可读，故两项夹具前提恒不成立；分支 diff 完全未碰 `store.rs` 与保存路径
  代码（总协调亲验 store.rs 未动）。需 CI（非 root）验证这两项通过。
- 本线说明：`phase2/yeban-mcp-tools/NOTES.md`（参数表、错误语义、验证实测数、缺口、带入待办）。
- **需父 agent 复核的两件事（已写进 NOTES.md §6）：**
  1. 工具名未加 `yeban_` 前缀：沿用规格名 `synthesize_singing` / `svs_job`（跨线契约，
     改名破坏规格），在 `tools.rs` 注册表测试与对齐门禁 `TABLE_TOOL_RE` 加了窄幅例外。
     后者改的是 integrator 专属 `scripts/**` 文件（总协调亲验
     `scripts/gates/check_feature_alignment.py` 在 diff 内），请复核是否接受；
     若不接受，替代方案是两工具改名并同步改规格。
  2. `phase-status.md` 历史引文数字同步：对齐表 79→80 行后，门禁判据 5（活文档手抄副本对账，
     无历史豁免）报出 4 处旧读数，其中 phase-status 两处是历史记录里引用的旧门禁 `[ok]` 输出。
     执行员已一律同步为 80（引文原意不受影响），请复核；若要求历史引文一字不动，
     则需门禁加历史豁免，须另行决定。`DEVELOPMENT_LEDGER.md` 的两处旧读数是 dated 历史条目
     且不在门禁扫描范围，故意未动。
- 已知缺口：https/TLS 不支持（std 无 TLS），`https` 地址配置时即响亮拒绝；
  远端隧道需本地 http 落点或后续批准 TLS 依赖。
- **带入待办（总协调安排，执行员未做）：** 推送后读分支 CI；带入新主干 `47f8ace4`
  （主干红修复批，执行员分支基底是 07315b14），带入时按计数复核规则重扫全部计数文字
  并重跑全部验证；合入等主干绿，合入前验父提交三者一致。
- **未证明项（总协调补记，执行员本线说明里有一处已过时）：** 执行员称“VR 服务端适配器
  仍是 Phase 1 占位”，但本 Phase VR 接线线已完成（见 §1），客户端接受 `vocalrender` 且
  服务端已实现；两者配对尚未端到端跑过（客户端只对自含桩服务测过），
  Yeban 工具 ↔ 真实 Phase 2 服务的端到端验证仍是待办。

## 3. 长乐句再验 — 闭合

- **SoulX pitch_shift 与独立乐谱路线：长乐句上等价成立，升降两档皆成立。**
  主推路线仍是独立乐谱，旋钮是可用备选。
  23.31 秒乐句，55 演唱音节，同 seed 0，auto_shift 关，六臂输出时长均为 23.30 秒：
  +4 档旋钮 vs 移调乐谱，包络相关 0.9966，逐音 F0 中位绝对差 0.0 音分，p90 10.0 音分，
  最大 19.6 音分；全局中位相对基线 +440 vs +460 音分（目标 +400，差来自模型 slack）。
  −3 档包络相关 0.9989，逐音中位差 0.0 音分，p90 10.0 音分，最大 30.5 音分；
  全局中位相对基线均为 −290 音分（目标 −300）。
  SoulX 乐谱时钟边界复核（基线臂）：找回 52/55（94.5%），偏差中位绝对值 53.6 毫秒，
  p90 146.3 毫秒，最大 293.3 毫秒；3 个未找回属检测器漏检，不是缺音节。
- **VR 长乐句回执仍可用，但只可用实测起点贴轨。乐谱时钟贴轨在长乐句上尾部误差达 2.49 秒，不可用。**
  乐谱 25.60 秒，60 非 SP 词，bpm 58：输出 23.36 秒（−8.75%）；音节跨度压缩率 0.897（−10.3%）。
  联合法找回 60/60（100%，缩放期望 ±350 毫秒窗）；但相对乐谱时钟偏差中位绝对值 1217 毫秒，
  最大 2487.7 毫秒，且逐遍均匀累积（第 1 遍末 −563 毫秒 → 第 4 遍末 −2488 毫秒）。
  漂移形态是近线性压缩：拟合 `实测 = 0.105 + 0.8935 × 乐谱`，残差中位 55.2 毫秒，
  p90 150.9 毫秒，最大 203.8 毫秒。按实测起点贴，误差就是这个量级。
  **漂移率不稳定：Phase 0 的 6 秒 demo 是 −2%，本次是 −10.3%，固定缩放系数不能跨长度复用，
  必须用逐音节实测起点。**
  SP 处理：含 SP 的词间 gaps 中位 1237 毫秒（无 SP 的 231 毫秒），SP 保留为真实分隔，
  压缩率 0.87 与整体一致；SP 窗不是静音（RMS 比值 0.354，含前后音节尾气与约 ±200 毫秒窗误差），
  放置时不得假设 SP 处是静音段；SP 未偷走任何指派。
- **长乐句 takes 门两模型首试即过：** SoulX 非逐位相同，包络相关 0.9008，F0 差 14.73 音分，
  起音差 21.33 毫秒；VR 非逐位相同，包络相关 0.5553，F0 差 67.02 音分，起音差 42.67 毫秒。
- 本线报告与证据：`phase2/long-phrase/REPORT.md`、`phase2/long-phrase/evidence/`（8 条 wav、
  全部结果 JSON、对齐与逐音对照、3 份 Colab 日志）、脚本与乐谱。
- 适用边界与未做项：等价性结论只覆盖 1 prompt、1 seed、普通话、23.31 秒、移调 +4/−3；
  未验其他 prompt 音域、其他 seed、粤语/英语、超 60 秒、大于 ±4 半音。SoulX 乐句的 B' 段
  复用了 zh_prompt 参考音频的那句歌词，两臂共用同一 prompt，对照对称，但绝对音质不是
  prompt 独立的。VR 长乐句多 seed 稳定性只跑了两遍；未做人耳听感验收（本线只做数字量测）。

## 4. 全量 prompt 预处理流水线 — 闭合

- 交付：`phase2/prompt-pipeline/prompt_pipeline.py`（仅需 numpy）、`requirements.txt`、
  `README.md`（阈值表 + POST /v1/voices 字段映射）、`TEST-REPORT.md`、`tests/`（corpus 生成器、
  可复现的 `run_tests.sh`、12 案完整报告）。
- 流程：生音频 → 去头尾静音 → 选 2–8 秒 VR 窗口（响且干净优先）→ 在窗口上量峰值 / RMS / SNR /
  F0 / 静音洞 / 建议增益 → accept / accept_with_warnings / reject（退出码 0 / 0 / 2）。
  上传文件是窗口切片 `prompt_window.wav`，不是整段；无歌词标注时只出 `prompt_meta.template.json`，
  绝不发明歌词/音素/音高，`soulx_ready=false` 是诚实状态。
- 实测 12 案：accept 1、accept_with_warnings 5、reject 6，`run_tests.sh` 12/12 PASS。
  关键数：VR 证据歌声 vr_take0（5.85 s 窗，SNR 37.2 dB，有音高 0.83）带唯一近削波警告通过；
  51.24 s 长歌切出 18.8–26.8 s 的 8.0 s 窗；2.00 s 整过门、1.92 s 被拒。
- 与 Phase 1 服务本地对接实测通过：标注模式产出的 `prompt_meta.json`（time [0,5850]，
  duration 和 = 5.85 s）上传 → `soulx_ready=true`；不带 meta → `false`。
  附带观察（不属本线，未改）：multipart 的 `name` 字段返回 null，Phase 1 把 name 当 query 参数收。
- 实测修掉 4 个真 bug（voiced run 统计错致真歌声误报 speech-like、帧索引越界致短文件崩溃、
  verdict 按整段量改按窗口量、F0 插值出负致 nan），修后全量重跑。
- 未覆盖（明写在 TEST-REPORT §2）：真人自有录音、真人语音、纯音乐三档未实测——
  本地没有真人录音与官方 demo prompt 音频（SoulX `zh_prompt.mp3`、VR `2003000081.wav` 只在
  Colab 上；另一线目录下的 `zh_prompt.json` 只是 meta 文本，本线未用它）。

## 5. 命名 tunnel — 未建成，卡点已验明，模板与脚本已交付

- 交付：`phase2/infra/tunnel/`（`config.yml.template`、`run-named-tunnel.sh`、
  `run-service-colab.sh`、`SETUP.md`、`TEST-LOG.md`）。
- 未建成原因（实测，非推测）：`~/.cloudflared/` 无 `cert.pem` / config / 凭据 json，
  `cloudflared tunnel list` 报缺 origin cert；建 tunnel 需 `cloudflared tunnel login`（浏览器 OAuth），
  必须人做。quick tunnel 在本机也起不来：两次实测（本地 /health 通），一次
  `api.trycloudflare.com` 请求超时，一次 403 被拦——本机 egress 代理拦建 tunnel 请求，不再重试。
- 由此定口径：tunnel 起在服务所在机（Colab 会话 / Linux GPU 机），不在本 VM 起。
  剩余工作只剩 Wang 定三件事：登录账号、挂子域的 zone（候选 `svs.wangda.today` 待确认 zone 归属）、
  在服务机上按 SETUP.md 跑 login → create → route dns → run。
  守卫脚本两道拒跑（占位符未填、hostname 撞 SSH 域名）均实测触发。
  SSH tunnel（`bigbangbang.wangda.today`）全程未动；执行员未读未复制任何凭据内容。

## 6. Docker — 交付物完成，构建未做（无 daemon，不冒充已验）

- 交付：`phase2/infra/Dockerfile`（CUDA 12.4.1 runtime，代码构建时 COPY，权重绝不进镜像）、
  `phase2/infra/docker-compose.yml`（GPU 预留 nvidia 1 卡、权重/仓库只读卷、数据卷、8000 端口、
  `SVS_API_KEY` 用 `${...:?}` 强制）、`phase2/infra/requirements-gpu.txt`、
  `phase2/infra/README.md`（构建、运行、权重获取三步）。
- 校验：compose YAML 已用解析器验过，关键字段断言通过；两个 shell 脚本 `bash -n` 通过；
  本机无 `docker` 命令、无 daemon，镜像未构建，必须在 Linux GPU 机上构建。
  VR 适配器环境变量名未编造（等 VR 接线线定，只留 `./models/vocalrender/` 挂载位）。
  macOS Docker 无 GPU 直通、不是 macOS 本地路径，已写进 README。

## 7. 待 Wang / 待后续（未做，不冒充已做）

- **Mac MPS 实测**：本 VM 做不了，仍是 Wang 的 Mac 上待跑项；
  触发条件不变：SoulX 的 MPS RTF > 2 则启动 MLX 二期。
- **Yeban 工具分支推送与 CI**：`line/svs-mcp-tools`（fe8e08e3）待父 agent 推送、读分支 CI；
  带入新主干 `47f8ace4` 后重扫计数文字并重跑验证；合入等主干绿，合入前验父提交三者一致。
  另需复核 §2 的两件事（`yeban_` 前缀例外 + scripts 门禁改动；phase-status 历史引文同步）。
- **命名 tunnel 激活**：需 Wang 定登录账号与 zone，在服务机上跑 login → create → route dns → run。
- **Docker 构建**：需在 Linux GPU 机上构建并跑通镜像。
- **Yeban 工具 ↔ 真实 Phase 2 服务端到端**：客户端只对桩服务测过，配对实测待办。
- **Prompt 真人三档补测**：真人自有录音、真人语音、纯音乐。
- **VR tie melisma 折叠、对齐检测器独立语音学核验、长乐句覆盖扩展**（其他 prompt / seed /
  语种 / 超 60 秒 / 大于 ±4 半音）、**人耳听感验收**、**yeban 客户端 https/TLS**（需批准 TLS 依赖）。
