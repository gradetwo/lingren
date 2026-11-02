# SVS Phase 2 — VocalRender 适配器接线报告（执行线）

范围只含本线。服务副本在 `../service/`，本目录只含本线脚本与证据。
`phase2/REPORT.md` 是总协调的，本线未写。`phase0/` 与 `phase1/` 未动。

## 1. 接线说明

- 复制：`phase1/service/` 完整复制到 `phase2/service/`，只改副本。
  新增 `app/vr_driver.py` 与 `app/adapters/vocalrender.py`，
  `app/jobs.py` 的占位 `_VocalRenderPending` 换成真适配器。
- VR 推理走子进程：一遍一进程一种子，用补丁 03 的 `--seed`。
  环境变量：`VR_REPO`（打好补丁 03 的检出）、`VR_CKPT_DIR`
  （快照里的 `VocalRender` 子目录）。
- 乐谱转换：`score_converter.vr_label_entry`，音符 token 是
  `<NOTE_4>` 分母制（补丁 05）。label 写成单条目 list，
  item 名 `lingren_<label>`，与上游 CLI 的 list + item_name
  过滤一致（Colab 上已核脚本源码）。
- 超范围拒绝：音符超出 0.125–6.0 拍时适配器响亮失败，
  不静默量化到最近 token（补丁 05 的规则）。
- 作业 API：model 按 part 指定，一个作业可混装 soulx part 与
  vocalrender part。VR voice 只需参考音频，不需 SoulX prompt_meta；
  注册后 `soulx_ready=false` 是预期状态，不是错误。
- prompt 口径：VR prompt 应为 2–8 秒干净歌声。实际时长记进
  回执 `adapter_info.prompt_duration_s`，超出范围只告警不拒跑。
- 回执对齐：每音符一条，键为 `score_onset_s` /
  `measured_onset_s` / `deviation_ms` / `lyric` / `ignored`。
  对齐只对有歌词音符跑候选指派；SP（休止）条目记
  `ignored=true` 且实测起点与偏差为 null，不消耗候选。
  相对 Phase 1 的变化：Phase 1 会把邻音节的候选指给休止；
  本线改成有歌词音符先对齐、再展开补 SP。无休止乐谱的
  结果不受影响（SoulX 对照见 §2）。
- takes 门：VR 与 SoulX 共用同一门与阈值（非逐位相同、
  包络相关 < 0.99、F0 差 > 5 音分或起音差 > 10 ms 至少一处激活、
  最多重试 3 次、耗尽响亮失败）。
- 附带修正：`requirements.txt` 补 `python-multipart`。
  Phase 1 的 Colab 安装是单独装它的，服务自带依赖里漏了它，
  本地测试因此在收集期报错，现已补齐。

## 2. 端到端实测（Colab L4，会话 `svs-phase2-vr`，已停）

- 声音：SoulX 用官方 `zh_prompt`（音频 + prompt_meta）；
  VR 用官方 demo prompt `vr/examples/prompt_audio/2003000081.wav`
  （6.166 秒，44,100 Hz，在 2–8 秒内；Phase 1 证据里的 prompt，
  未用未授权音频）。
- 作业：一个作业两个 part，作业号 `e1b68cd64b394d9b`，
  约 140 秒完成，4/4 遍，全部首试通过，无重试。
  - lead（soulx）：4 音符（你/好 ×2，bpm 120），两遍各 2.0 秒、
    24,000 Hz、96,044 字节。门数与 Phase 1 端到端逐数一致：
    包络 0.834、F0 2.72 音分、起音 21.33 ms。
    SoulX 对齐也与 Phase 1 修正后一致（首音节 take0 为 null，
    其余 +97.3 / +2.7 / +100.0 ms）。
  - vr（vocalrender）：8 有歌词音符 + 1 SP（2.0 秒处），bpm 120，
    两遍 takes=2、seeds 0/1。take0 4.16 秒、399,404 字节；
    take1 4.0 秒、384,044 字节；均为 48,000 Hz。
    回执 `note_tokens` 9 条全为 `<NOTE_4>`（8 音符 + SP 各 1 拍）。
    门记录：非逐位相同、包络相关 0.1993、F0 差 3.47 音分、
    起音差 53.33 ms；F0 通道未激活（< 5 音分），起音通道激活，
    判定通过。门记录齐全（`gate_pairwise` 有 take1 首试记录）。
  - VR 对齐：有歌词音符 8/8 测到实测起点；SP 条目
    `ignored=true`、实测起点与偏差均为 null。
    VR 自定时值压缩真实存在：乐谱总长 4.5 秒，输出 4.16 /
    4.0 秒（-7.6% / -11.1%）；末音节偏差 -789.3 / -629.3 ms。
    幅度大于 Phase 0 demo 的总量 -2% 与单音节 -560 ms，
    方向一致（压缩），幅度随乐谱而异。结论不变：
    放置必须用回执实测起点，不能按乐谱硬贴。
- 本地复算：`recompute_vr.py` 用本线服务代码对下载的两遍
  VR wav 重算，门四项与两遍对齐逐数等于回执
  （包络 0.1993、F0 3.47 音分、起音 53.33 ms；对齐 9 条全等）。
  复算只证明回执由这两段 wav 经本线代码算出，
  不证明检测到的起点在语音学上全对。
  例如 take0 里乐谱 1.0 / 1.5 秒两音节测到 0.907 / 1.013 秒
  （间隔仅约 107 ms），这是检测器输出，未做独立语音学核验。

## 3. 回执样例（VR part，take0，节选）

```json
{"take": 0, "seed": 0, "attempts": 1, "duration_s": 4.16,
 "sample_rate": 48000,
 "adapter_info": {"adapter": "vocalrender", "seed": 0,
  "label_item": "lingren_vr",
  "note_tokens": ["<NOTE_4>", "<NOTE_4>", "<NOTE_4>", "<NOTE_4>",
   "<NOTE_4>", "<NOTE_4>", "<NOTE_4>", "<NOTE_4>", "<NOTE_4>"],
  "prompt_duration_s": 6.166, "prompt_duration_warning": false,
  "timing_note": "VR self-timing: place by measured onset in the receipt alignment, not by the score clock"},
 "alignment": [
  {"score_onset_s": 0.0, "measured_onset_s": 0.03,
   "deviation_ms": 30.0, "lyric": "你", "ignored": false},
  {"score_onset_s": 2.0, "measured_onset_s": null,
   "deviation_ms": null, "lyric": "<SP>", "ignored": true},
  {"score_onset_s": 4.0, "measured_onset_s": 3.2107,
   "deviation_ms": -789.3, "lyric": "好", "ignored": false}]}
```

门记录（VR part）：

```json
{"take": 1, "attempt": 0, "vs_accepted": [
 {"bit_identical": false, "envelope_corr": 0.1993,
  "f0_diff_cents": 3.47, "onset_diff_ms": 53.33,
  "f0_available": true, "passed": true, "fail_reasons": []}]}
```

## 4. 本地测试

`phase2/service` 内 12 项全过：门测试 5 项、API 测试 1 项、
VR 测试 6 项（词表分母制、超长音符拒绝、缺环境变量响亮失败、
SP 忽略对齐、双模型混装作业、超长音符经 API 响亮失败）。
测试用 48,000 Hz 伪 VR 适配器替真模型，不烧 GPU。

## 5. CU 花费

- 开始 528.25 CU（执行前实测，无活动会话）。
- 结束 527.58 CU，会话 `svs-phase2-vr` 已停。
- 本线花费 0.67 CU。停后仍显示 1 个活动会话，
  那是兄弟线 `svs-phase2-long`，不是本线残留。

## 6. 未做项与已知局限

- 未做：`phase2/REPORT.md`（总协调）、prompt 预处理流水线、
  命名 tunnel、Docker、Mac MPS 实测（本 VM 做不了，未编造）、
  Yeban 仓库内 MCP 工具本体、VR 长乐句对齐（兄弟线）。
- 连音（tie）在 VR 转换里未做 melisma 折叠：一个乐谱音符对应
  一个 label 条目，tie 音符会展开成重复歌词条目。
  端到端乐谱未用 tie，此局限未在真机上验证。
- VR 对齐检测器在短乐谱上的单音节起点未经独立语音学核验
  （见 §2 末尾的例子）；回执数值可复算，不等于语音学真值。
- 证据：`evidence/`（回执、端到端日志、安装日志、6 段 wav）、
  `colab_e2e_vr.py`、`colab_setup_phase2.sh`、`recompute_vr.py`。
