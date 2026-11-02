# Phase 2 本线报告：prompt 流水线 + 命名 tunnel + Docker

执行线范围：只做本线三件。未碰 `phase2/service/`（另一条线的 VR
接线区，本线未建也未改），未写 `phase2/REPORT.md`（总协调的），
未动 `phase0/` 与 `phase1/`（只读参照 + 本地起服务验证）。

## 1. Prompt 预处理流水线：完成，实测通过

交付：`../prompt-pipeline/`（脚本 + requirements + README +
TEST-REPORT + tests/）。

- 脚本 `prompt_pipeline.py`（仅需 numpy；soundfile 可选）：
  生音频 → 去头尾静音 → 选 2–8 秒 VR 窗口（响且干净优先）→
  在窗口上量峰值/RMS/SNR/F0/静音洞/建议增益 → accept /
  accept_with_warnings / reject 三档 verdict（退出码 0/0/2）。
- 校验规则全部可执行：过短、超长（窗口切片）、含静音洞、削波、
  纯音/语音/无音高均有拒绝或警告分级，阈值见 README。
- 实测 12 案：accept 1、accept_with_warnings 5、reject 6，全部符合
  预期；`tests/run_tests.sh` 复现 12/12 PASS。逐案报告在
  `prompt-pipeline/tests/results/`。
- 关键实测值：VR 证据歌声 vr_take0（5.85 s 窗，SNR 37.2 dB，有音高
  0.83）accept_with_warnings（仅峰值 −0.9 dBFS 近削波警告）；51.24 s
  长歌切出 18.8–26.8 s 的 8.0 s 窗；2.00 s 整过门、1.92 s 被拒（边界
  各差 0.08 s 量级内见真章）。
- 与 Phase 1 对接实测通过：标注模式产出的 `prompt_meta.json`
  （time [0,5850]、duration 和 = 窗口 5.85 s）上传
  `POST /v1/voices` → `soulx_ready=true`；不带 meta →
  `soulx_ready=false`。字段映射写在 prompt-pipeline/README.md。
- 实测修掉 4 个真 bug（voiced run 统计、帧越界、整段量 verdict
  改窗口量、F0 插值出负致 nan），修后全量重跑。
- 未做 / 未覆盖：真人自有录音未实测（本地没有；歌声档用 Phase 0/1
  模型合成输出）；真人语音、纯音乐各一档未实测（见 TEST-REPORT §2）；
  流水线不发明歌词/音素/音高——无标注时只出模板，
  `soulx_ready=false` 是诚实状态，不是缺口掩盖。

## 2. 命名 tunnel：未建成，卡点已验明，模板与脚本已交付

交付：`tunnel/`（config.yml.template、run-named-tunnel.sh、
run-service-colab.sh、SETUP.md、TEST-LOG.md）。

- 结论：**没有编造一个已建成的 tunnel**。本机建不了，原因实测：
  `~/.cloudflared/` 无 `cert.pem`/config/凭据 json（只看目录名，
  token 内容未读未复制）；`cloudflared tunnel list` 报缺 origin cert。
  建 tunnel 需要 `cloudflared tunnel login`（浏览器 OAuth），这步
  必须人做。
- quick tunnel 在本机也起不来：两次实测（服务本地 /health 通），
  一次 `api.trycloudflare.com` 请求超时，一次 403 被拦——本机 egress
  代理拦建 tunnel 请求。不再重试。
- 由此定部署口径：tunnel 起在服务所在机（Colab 会话 / Linux GPU
  机），不在本 VM 上起。剩余工作只剩 Wang 定三件事：登录账号、
  挂子域的 zone（候选 `svs.wangda.today` 待确认 zone 归属）、在服务
  机上按 SETUP.md 跑 login → create → route dns → run。
- SSH tunnel（`bigbangbang.wangda.today`）全程未动：未改配置、未
  读凭据、未重启进程；新脚本用独立配置文件并设两道拒跑守卫
  （占位符未填、hostname 撞 SSH 域名），两道均实测触发。
- 未做：命名 tunnel 本体、DNS 记录、外网 /health 实测——全部等
  登录 + 域名定夺后才能做，不属于“本线可独立完成”的部分。

## 3. Docker：交付物完成，构建未做（无 daemon）

交付：`Dockerfile`、`requirements-gpu.txt`、`docker-compose.yml`、
本目录 `README.md`（构建、运行、权重获取三步）。

- Dockerfile：CUDA 12.4.1-cudnn8 runtime 基础镜像，Python 依赖 +
  服务代码构建时 COPY（上下文 = 项目根）；权重绝不进镜像，只走
  `/models` 只读卷；`SOULX_DEVICE=cuda`；healthcheck 打 /health。
- compose：GPU 预留（nvidia 驱动、1 卡）、权重卷 + SoulX 仓库卷
  （只读）+ 数据卷、8000 端口、`SVS_API_KEY` 用 `${...:?}` 强制
  （未设直接失败，不裸奔）；YAML 已用解析器验过，关键字段断言通过。
- 未构建：本机无 `docker` 命令、无 daemon，Dockerfile 只作交付物
  审查，不冒充已验。构建必须在 Linux GPU 机上做（README 有命令）。
- 未做：VR 适配器环境变量名（另一条 VR 接线线定，本文件不编造，
  只留 `./models/vocalrender/` 挂载位）；镜像内跑 GPU 推理实测
  （等构建 + GPU 机）。
- 边界重申：Docker 只面向 Linux GPU；macOS Docker 无 GPU 直通，
  不是 macOS 本地路径。

## 4. 本线 touching 清单（供总协调核对）

- 新建：`phase2/prompt-pipeline/`、`phase2/infra/` 全部文件。
- 未动：`phase2/service/`、`phase2/vr-adapter/`、`phase2/long-phrase/`
  （其他线的工作区，本线未改其中任何文件；终验时才发现这些目录
  在本线工作期间已由其他线建起）。
- 未改：`phase0/`、`phase1/` 源码（phase1 服务仅以临时数据目录起
  来做上传验证）、SSH tunnel 相关任何文件、任何凭据文件。
- 临时过程文件在 /tmp（测试输出、yaml 校验 venv），不属交付。
