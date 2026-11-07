# 伶人 SVS API 服务 — Phase 1（骨架 + SoulX 适配器 + 作业 API）

设计文档：`../RESEARCH-AND-DESIGN.md`（四条管线见 §13）；Phase 0 实测：`../phase0/REPORT.md`；
补丁：`../patches/`（推理代码的改动全部以补丁文件存档）；四管线设计：`DESIGN-four-pipelines.md`。

> **行为变化（2026-11-07，Wang 定）**：`PartIn.model` 默认值由 `'soulx'` 改为 `'yue2'`。老调用方不传 `model` 时，以前走 SoulX，现在走 YuE2。显式传 `model="soulx"` 仍走 SoulX（行为不变）。`vocalrender` 由占位（调用即响亮失败）变为可用管线；新增 `yue2`、`mulacover`；未知 `model` 仍大声失败。

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
  （统一乐谱 JSON）、voice_id、model（`'soulx' | 'vocalrender' | 'yue2'`（默认）| `'mulacover'` | `'stub'`（测试用））、takes、seeds、options。单 part 即 parts
  长度 1，不另设端点。**不传 `model` 即走 YuE2**（见顶部行为变化）。
  YuE2/MuLaCover 生成本身不需要 voice（无参考音频输入），只在
  `options.svc` 打开时才要求 voice 存在；SoulX/VocalRender 仍要求
  voice 存在（未知 voice_id 响亮失败）。
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

## VocalRender 适配器（2026-11-07 由占位变为可用，VR 直出）

从 `phase2/service/` 原样移植（`app/adapters/vocalrender.py` +
`app/vr_driver.py`，只改 import 路径与注册，不改逻辑）。环境变量：
`VR_REPO`（打好 `../patches/03` 的检出）、`VR_CKPT_DIR`（快照里的
`VocalRender` 子目录）；缺失响亮失败。Voice 只需参考音频（作
`--prompt_audio`，2–8 秒干净歌声为宜；实际时长记进回执
`adapter_info.prompt_duration_s`，超出范围只告警不拒跑）。音符
token 是 `<NOTE_4>` 分母制（补丁 05），超出 0.125–6.0 拍响亮失败，
不静默量化。`options` 可传 `temperature`、`fsq_temperature`、
`max_len`（照抄 phase2 的透传）。VR 自己定律时值：回执的实测起点
就是贴轨依据，绝不按乐谱时钟硬贴。

## YuE2 适配器（2026-11-07 新增，默认直出干声）

环境变量：`YUE2_REPO`（必填，`yue2-v0.1.6` 检出，`uv pip install .`
已装）、`YUE2_MODEL`（默认 `m-a-p/YuE2-3B`）、`YUE2_MODEL_REVISION`
（默认 `8f24312f…`）、`YUE2_VAE_REVISION`（默认 `152733a1…`）、
`YUE2_DEVICE`（默认 `cuda`）。适配器把乐谱转原生 ABC（Vocal +12
移调、Ins 默认休止），生成整个 part，再用 Demucs 4.1.0 htdemucs
分离出干声（固定步骤，不可关闭）。`options`：`yue2_style`（必填，
缺失响亮失败；不存在通用默认值）、`yue2_cot`（默认 `"melody"`）、
`yue2_cfg_scale`（默认 1.0）、`yue2_temperature`（默认 1.0）、
`yue2_top_p`（默认 0.95）、`yue2_top_k`（默认 100）、
`yue2_repetition_penalty`（默认 1.2）、`ins_abc`（Ins 声部 ABC 原文，
缺省全休止）、`yue2_lyrics`（歌词全文，缺省按音符顺序取）、`yue2_key`
（默认 `"C"`）。已知局限：单次调用生成整个 part；超过约 108 秒
输出的时长保真度未测；尾部与段间静音区噪声在民谣实验里出现过
（Wang 耳验）。

## MuLaCover 适配器（2026-11-07 新增，默认直出干声）

环境变量：`MULACOVER_REPO`（必填，`e27fa7fa` 检出，`uv pip install -e '.[audio]'`
已装）、`MULACOVER_MODEL_PATH`（必填，权重目录，约 15.6 GB）、
`MULACOVER_DEVICE`（默认 `cuda:0`）。配方（new-control）：拉伸 ×1.5
旋律+和弦 MIDI、cfg 2.5、temp 1.0、topk 250、同一 base seed。
`options`：`tags`（必填，缺失响亮失败；不存在通用默认值）、
`chords`（显式和弦表，覆盖推导）、`mulacover_cfg_scale`（默认 2.5）、
`mulacover_temperature`（默认 1.0）、`mulacover_topk`（默认 250）、
`transpose12`（默认关）、`mulacover_lyrics`（歌词全文，缺省按音符
顺序取）。和弦默认从谱子推导（4/4 假设；根音 = 总时值最大的音级；
大／小三度判定；空小节沿用上一小节；首小节为空响亮失败；只出三和
弦），推导与传入的和弦表都写进回执 `adapter_info`。长段按段生成
（每段至多 12 小节）再拼接（DIO 裁剪 + 相位声码器拉伸到段长）。
已知局限：4/4 假设（ScoreIn 无拍号字段）；按段挑优不在 v1。

## SVC 可选后处理（YuE2 / MuLaCover，默认不接）

`part.options.svc`（bool，默认 `false`）。只对 `yue2` 与 `mulacover`
生效；`soulx` 与 `vocalrender` 传此开关时忽略（不报错）。打开时：
干声生成后接 SoulX SVC 换音色，目标音色为 `part.voice_id` 的已注册
voice 参考音频；校验响亮失败（voice 不存在、voice 无参考音频、
参考音频 > 30 秒）。SVC 参数写死：pitch_shift 0、auto_shift 关、
fp16 按设备。环境变量（开关打开时才必填）：`SVC_REPO`、
`SVC_MODEL_PATH`（默认 `pretrained_models/SoulX-Singer/model-svc.pt`，
相对 `SVC_REPO`）、`SVC_DEVICE`（默认 `cuda`）。可选
`SVC_PYTHON`：SoulX 两个子进程用的解释器，默认服务进程自己的
解释器；服务跑在 YuE2 环境（torch/transformers 太新，SoulX 自带
llama.py 的旧调用签名与新版 transformers 不兼容）时，指向一个按
SoulX requirements 钉死版本的 venv（torch 2.2.0、transformers
4.41.2）。回执
`adapter_info.svc` 记 `svc: true` 与目标 voice_id、时长、seed。

## 未接入 / 已封存

DiffSinger 不是第五条可用管线。DiffSinger + SVC 两段管线位于
lingren 仓库分支 `lingren-diffsinger-svc-twostage`，尖端 `8ad4180`，
代码提交后即停下来，不合入 main（Wang 2026-11-07 15:10 定）。

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

## Phase 2 清单（本 Phase 明确不做的事）

- VocalRender 服务适配器接线：已做（2026-11-07，四管线工作，见上）。
- Yeban 仓库内 MCP 工具（`synthesize_singing` / `svs_job`）透传新
  `model` 值：另一步。Yeban 侧若有调用方省略 `model`，默认改动会使
  它改走 YuE2，须在该步里处理。
- 全量 prompt 预处理流水线（生音频 → prompt_meta 自动对齐）。
- 命名 tunnel 固定端点、Docker 打包（设计 §7/§8，三期）。
- Mac MPS 实测（本机 Linux 做不了，Wang 的 Mac 上待跑；触发条件：
  SoulX MPS RTF > 2 则启动 MLX 二期）。
- Yeban 仓库内 MCP 工具本体（本目录只给 HTTP 客户端层）。
