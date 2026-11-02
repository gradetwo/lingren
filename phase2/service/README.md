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
在本目录（本 Phase 不改 yeban 仓库）。

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
