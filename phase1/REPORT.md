# SVS Phase 1 报告（2026-11-02）

执行：Phase 1（补丁可行性先行 + 服务骨架 + SoulX 适配器 + Yeban 客户端层）。
结论先写在前：**补丁验证全部闭合或有变通，无仍开放的硬缺口**；
服务骨架 + SoulX 适配器 + 作业 API 在 Colab L4 上真机端到端跑通；
Yeban 仓库内的 MCP 工具本体未动仓库（规格 + HTTP 层已交付，见 §4）。

默认口径（Wang 可纠正）：SoulX 默认模型；macOS v1 只做 PyTorch MPS
（MLX 触发条件不变：目标 Mac 上 SoulX 的 MPS RTF > 2）；开发期远端
走 Colab + quick tunnel；声音库只收自有或授权参考音频。

## 1. 补丁验证逐项结论（Colab L4，torch 2.11.0+cu130，Python 3.13）

| 要求 | 结论 | 实测数字 |
|---|---|---|
| SoulX seed（上游 CLI 无 seed） | **补丁关闭**（`02`） | 同 seed 两遍**逐位相同**；seed 0/1/2 三遍，门见 §2 |
| SoulX auto_shift 记录（+340 音分拉偏） | **补丁关闭**（`01`） | auto_shift 开录得 f0_shift=+3 半音，pyin 实测 +295 音分，两者一致；关时录得 0 |
| SoulX pitch_shift 和声 | **Phase 0 结论修正**（见 `patches/02-...-note.md`） | 干净对照（同 seed、auto_shift 关）：基线 335.4 Hz；pitch_shift=4 → 433.7 Hz（**+445 音分**）；乐谱整体 +4 半音 → 431.2 Hz（**+435 音分**）；两臂包络相关 0.9955，基本同一音频。Phase 0 的 +110 音分是拿 auto_shift 开的基线比出来的混杂值。主推仍是独立乐谱路线（语义干净），旋钮是可用备选；适用范围限本次 2 秒测试，长乐句需 Phase 2 再验 |
| SoulX 乐谱时钟贴轨 | **无需补丁** | 结构性的：CLI 按段起点贴段音频；score-mode 对齐实测边界偏差 +3 ~ +100 ms（含 F0 检测约 100 ms 系统滞后）；贴轨仍按乐谱时钟，回执只作校验 |
| VocalRender seed（上游无 seed 参数） | **补丁关闭**（`03`） | 同 seed 0 两遍**逐位相同**；seed 0/1/2：时长 6.24/6.24/6.40 s，包络相关 0.35–0.74，三对全过门（F0 差 3–30 音分，起音差 27–53 ms） |
| VocalRender 实测对齐回执（能量法 11/15） | **变通办法，实测改进** | 联合法（能量 + F0 稳定变音点）：2 秒片段 4/4 音节；Phase 0 真实 6 秒 demo（18 词，bpm 58）：**18/18**（能量法仅 8 候选）。注意：VR 自定时值漂移是真实的（总量 -2%，逐音节相对乐谱偏差可达 -560 ms），回执必须报偏差、按实测起点贴，不能按乐谱硬贴；SP（休止）条目无真实起音，放置时忽略（对齐条目已带 `lyric` 字段） |
| VocalRender RTF 1.11–1.18 | **无需改设计** | 异步任务 API 已是设计；本次加载约 20 s、生成约 7 s/6 秒音频，与 Phase 0 一致 |
| parts×takes 接口 | **服务实现** | 见 §3；单 part 即 parts 长度 1，不另设端点 |
| 批量 takes 差异门 | **服务实现 + 两处测量修正**（见 §2） | 门阈值沿用 Phase 0（包络 < 0.99；F0 > 5 音分或起音 > 10 ms；有界重试 3 次，耗尽响亮失败） |
| 和声附加评估：模型分裂（Wang ~09:09） | **可用，正交手段，不替代独立乐谱** | 见 §5 |

补丁文件（`patches/`，全部 `git apply` 可用，SoulX 三个已在 pristine
克隆上验过 `--check`）：`00` llama 关键字参数（Phase 0 教训存档）、
`01` 记录 f0_shift、`02` SoulX `--seed`、`03` VR `--seed`、
`05` VR 音符 token 词表核验（纠正：词表是 `<NOTE_4>` 分母制，初版
误写的 `<NOTE_QUARTER>` 等不存在，会静默回退到缺省一拍）。

## 2. takes 差异门：两次测量教训（门阈值未动，测量方法改了）

- **F0 分辨率**：整数 lag 自相关在 335 Hz / 24 kHz 处量化约 5 音分，
  真微差塌成 0 音分。改抛物线插值求分数 lag，恢复约 1 音分分辨率。
- **起音对齐**：候选数跨遍不同（颤音过线、起音瞬态、杂散候选），
  位置匹配会造出 ~640 ms 假差异；乐谱锚定缩放又在首音节位于 t=0
  时错配（实测 597 ms 假偏差）。改 DP 序列对齐（200 ms 内一对一、
  可跳过杂散候选）：A/C 起音差 21.33 ms（真抖动），不再是 640 ms。
- **短片段边缘**：2 秒 4 音符片段上，A/D 对中位 F0 差 3.56 音分、
  中位起音差 0 ms，未激活微差通道，门判失败——而包络相关 0.85 说明
  两遍确实不同。这是中位统计在极短片段上的已知弱点，不是门阈值错：
  worker 的有界重试（换 seed 再试，最多 3 次）就是为此设计的覆盖；
  51 秒 Phase 0 数据上双通道均稳健激活（F0 10–20 音分、起音 25–30 ms）。
  **不为 2 秒片段放宽阈值**。

## 3. 服务骨架 + SoulX 适配器 + 作业 API（`service/`）

- FastAPI：`POST /v1/voices`（音频 + 可选 SoulX prompt-meta JSON；
  无 prompt-meta 的 voice 注册成功但 `soulx_ready=false`，合成时响亮
  失败——全量 prompt 预处理流水线是 Phase 2 项）、`POST /v1/synthesize`
  （parts×takes×seeds）、`GET /v1/jobs/{id}`（parts/takes 进度）、
  `/audio`、`/receipt`。鉴权：`SVS_API_KEY` 设则要求 Bearer（远端必设）。
- Worker：单线程串行（防显存争抢），门失败重试、耗尽响亮失败、
  回执落盘（含门记录与对齐数组）。
- SoulX 适配器：子进程跑 `soulx_driver.py`，一遍一进程一种子；
  乐谱转换器处理 note_type（1 休止 / 2 常规 / 3 延音）、G2P（普通话
  g2pM、粤语 ToJyutping、英语 g2p_en）或显式 `phoneme`；缺 G2P 且无
  显式音素时响亮失败。
- 本地测试（无 GPU，stub 适配器）：**6 passed**（门 4 项 + API 提交/
  轮询/取音频/取回执 1 项，含 lyric 字段断言）。
- **Colab 真机端到端（SoulX 适配器）通过**：官方 zh_prompt 声音注册
  （soulx_ready=True）→ 4 音符乐谱、takes=2、seeds 0/1 → 任务完成；
  两遍各 2.0 s、24 kHz；服务端门记录与本地复算**逐数一致**（包络
  0.834、F0 2.72 音分、起音 21.33 ms，首试即过）；音频各 96,044 字节。
  说明：端到端跑的是对齐修正前的代码；修正（score-mode 对齐）已在
  本地用同一确定性 takes 复验（A：[None, +97, +3, +100 ms]），无需
  重烧 GPU 再验。

## 4. Yeban 侧：交付与未做（诚实分界）

- 已交付：`service/client/svs_client.py`（stdlib-only 的
  `LingrenClient`：upload_voice / synthesize / wait / fetch_receipt /
  fetch_audio）+ `yeban-mcp-tools-spec.md`（synthesize_singing 与
  svs_job 的参数、响应、放置语义、失败语义、接线清单）。
- **未做**：yeban 仓库内的 MCP 工具本体（注册、schema、工具数文案、
  测试）——本 Phase 未动 yeban 仓库，按规格接线即可，不需再设计。

## 5. 和声附加评估：模型分裂（不替代独立乐谱路线）

同乐谱（你/好 ×2，64/67，bpm 120）SoulX vs VR，prompt 不同是已知
混杂因素（SoulX zh_prompt vs VR 官方 prompt，音色差不能全归因于模型）：

- 组 A（音色区分）：跨模型包络相关 **0.2808**，远低于同模型换 seed
  （SoulX 0.83–0.85、VR 0.34–0.74）——“两个歌手”的区分度成立；
  F0 中位差仅 +25 音分（同音高唱对了）；VR 频谱质心高 586 Hz（更亮）；
  时长差 -0.08 s（VR -4%，与 Phase 0 的 ±5% 一致）。
- 组 B（和声模拟）：SoulX 原调主旋律（乐谱时钟）+ VR +4 半音和声部
  （实测起点贴轨）。VR 和声部 F0 中位 421.3 Hz（相对 VR 原调 +369
  音分，目标 +400，模型 slack 约 30 音分）；能量法仅找回 1 个起音，
  联合法找回 4/4，逐音节偏差 +10 ~ -115 ms——和声部必须按实测起点贴，
  不能按乐谱硬贴。
- 结论：模型分裂与独立乐谱路线**正交**（音高靠独立乐谱，音色靠分
  模型），可叠加使用；分裂本身不产生和声（不改音高就没有和声），
  不能替代独立乐谱路线；VR 和声部的时钟漂移是使用成本，需在回执里
  可见。

## 6. 花费与证据

- Colab：开始 529.41 CU，结束 528.40 CU，**花掉 1.01 CU**；会话
  `svs-phase1-20261102`（L4）已停，无活动会话。
- 证据：`colab_logs/`（soulx_test.log 含 SOULX_RESULT、vr_test.log、
  split_test.log、e2e.log、e2e_receipt.json、下载的实测 wav、
  opencpop_demo.json）；补丁 `patches/`；服务 `service/`（README
  含 Phase 2 清单）。

## 7. Phase 2 清单（本 Phase 明确不做的事）

VocalRender 服务适配器接线；全量 prompt 预处理流水线（生音频 →
prompt_meta 自动对齐）；命名 tunnel 固定端点；Docker 打包；Mac MPS
实测（本机 Linux 做不了，Wang 的 Mac 上待跑；触发条件 MPS RTF > 2
则启动 MLX 二期）；Yeban 仓库内 MCP 工具本体；长乐句上 pitch_shift
等价性再验；VR 长乐句对齐再验（本 Phase 最长只验到 6 秒 demo）。
