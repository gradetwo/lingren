# 《时光》谱子 · DiffSinger 第一阶段多音色切片报告

日期：2026-11-05。分支：`lingren-diffsinger-svc-twostage`。只做第一阶段（DiffSinger 合成），不做 SVC 第二段。

## 结论

- 3 段切片 × 3 个声库，共 9 份音频，全部落盘，全部通过烟囱门。
- 关键结论：琅与齑在时光音区（MIDI 51–68）上能唱准。凛冬低音轨上两库烟囱未过（检出率 26.5%／38.8%），是音区问题，不是库本身不能唱中文。
- 9 份切片检出率全部 100%（47/47 音），偏差中位全部在 ±0.1 半音内，±0.5 半音内占比最低 87.5%（其余 8 份 100%）。
- 先听哪份：A 切片三库差异最干净（同句同音区，音色对比直接）；B 切片听最高音（MIDI 68）；C 切片听桥段句。
- 音色方向只由耳验定。本报告只写数字，不写听感判断。

## 切片定义

谱子：`~/workspace/song-shiguang-20261102-v3/.work/score_v3.json`，208 音，MIDI 51–68，96 BPM，普通话。只读谱子，未动时光工程的母带与干声。

| 切片 | 窗口 | 音数 | 歌词 | 音区 |
|---|---|---|---|---|
| A（开头） | 0.50–10.94 s | 16 | 假如时光能够倒流我要回到那个路口 | MIDI 56–63 |
| B（高音版同句） | 51.85–63.20 s | 16 | 假如时光能够倒流我要回到那个路口 | MIDI 61–68 |
| C（桥段） | 103.77–113.82 s | 15 | 那些未说的话是否还是没勇气开口 | MIDI 59–66 |

## 量测表（pyworld dio 独立测 f0，与谱面比；与前两轮同法）

烟囱门：检出率 ≥80%，有声帧占比 ≥50%，|偏差中位| ≤0.5 半音。9 份全部通过。

| 切片 | 声库 | 检出 | 偏差中位 | \|偏差\| p90 | ±0.5 内占比 | 有声帧占比 |
|---|---|---|---|---|---|---|
| A | 绮萱 Qixuan v2.7.0 | 16/16 | +0.002 | 0.077 | 100% | 96.0% |
| A | 琅 power | 16/16 | +0.078 | 0.165 | 100% | 99.3% |
| A | 齑 | 16/16 | −0.033 | 0.141 | 100% | 99.0% |
| B | 绮萱 Qixuan v2.7.0 | 16/16 | −0.000 | 0.082 | 100% | 93.2% |
| B | 琅 power | 16/16 | −0.005 | 0.464 | 87.5% | 95.8% |
| B | 齑 | 16/16 | −0.043 | 0.310 | 100% | 96.2% |
| C | 绮萱 Qixuan v2.7.0 | 15/15 | −0.004 | 0.086 | 100% | 87.5% |
| C | 琅 power | 15/15 | +0.095 | 0.171 | 100% | 88.6% |
| C | 齑 | 15/15 | −0.008 | 0.148 | 100% | 96.1% |

单音最差偏差：琅 B 切片“回”（MIDI 68）−0.769 半音；其余全部单音偏差在 ±0.55 半音内。无一音未检出。

## 声库与参数

- 绮萱 Qixuan v2.7.0（童声女声，中英混杂）：`yqzhishen/qixuan-diffsinger` Releases。声码器共享 pc_nsf_hifigan 44.1k。
- 琅 power（BaiShuo 系，`DiffSinger_lang.zip`，510 MB）：双说话人，本试验用 power（speaker-id 0）。声码器自带 pc_nsf_hifigan 2025.02。许可 NC（LICENCES-zh-250112-NC-NS.txt）。
- 齑（BaiShuo 系，`DiffSinger_ji.zip`，562 MB）：单说话人。声码器共享同款。许可 NC。
- Leif 未做：中文音素仅 48 个，缺 20+ 复合韵母，中文 map 建不了（见 REPORT-banks.md），本轮跳过。
- 推理参数与前两轮一致：steps 20，depth 0.6，expr 1.0，`--dur score`。dsdur 预测时长比谱面长 4–21%（各片中位比 1.04–1.21），`--dur score` 已按谱面重缩放。
- 交付音频：44.1 kHz，16-bit，单声道，峰值归一 0.89。A 每份 10.99 s，B 每份 11.90 s，C 每份 10.60 s。

## 试听文件

- `qixuan_slices.zip`（2,494,911 字节，SHA256 ea0f92840c89078466f0f123c8ff771e82c429a91ca439c8a49a34acbd0d0f81）：绮萱 3 切片 wav＋sidecar＋量测 JSON。
- `lang_slices.zip`（2,546,867 字节，SHA256 c40d5c0d7680532edc2b50949d165a1fee51efba8931cd44c0b8c78e5917076f）：琅 power 3 切片。
- `ji_slices.zip`（2,540,178 字节，SHA256 4f5197637c392026d69b6173f1850803ad197b28f78a0bc49c967c9ad0decc0c）：齑 3 切片。

试听链接：上传未完成。remote-storage 上传需审批卡确认，三次调用均因审批超时未执行（`approval_expired`，未上传任何文件，无待批卡）。三包已在本地备好（SHA 见上），需父 agent 重跑上传命令取链接：
`/opt/hatch/bin/remote-storage upload-file --path ~/workspace/tmp-wt-lingren-diffsinger-svc/phase2/shiguang-slices/<qixuan|lang|ji>_slices.zip`
链接过期时间以上传返回的 expires_at 为准，届时补进本节。

## 方法与复现

- 驱动：`sing_bank.py`（通用驱动，前两轮同款）＋ `measure_bank.py`（dio 量测）。谱子转换：`shiguang_slices.json`（时光 score_v3 转 tracks 格式，字段 start_tick／start_s／end_s／duration_s／pitch／pitch_name／lyric／velocity／lyric_line_index）。
- map：`build_bank_map.py` 按各库 acoustic 音素表实测构建（逐项核对在表里，缺失即退出）。三库 MAP-OK（zh=93 拼音条目思路同前；琅／齑无英文音素，英文词不在 map 内——时光谱子全中文，无影响）。
- 会话：Colab `ds-svc-l4`（L4）。原会话已死，新会话同名重建；环境脚本 `svc-colab-setup.sh` 跑完，§6 自检 SVC-ENV-CHECK-OK（venv torch 2.10.0+cu128；补装 rotary-embedding-torch 后通过）。
- 过程修正（两处，均已重跑验证）：
  - 窗口端点取整漏音：首版 A 切片 end=10.93 漏了末音“口”（end 10.9328），C 切片 start=103.78 漏了首音“那”（start 103.7752）。已按精确端点重跑（A end 10.94，C start 103.77），9 份最终版均为完整句。教训：窗口端点须用音符精确 start_s／end_s，不用打印的四舍五入值。
  - 琅／齑下载资产名大小写：`DiffSinger_lang.zip`／`DiffSinger_ji.zip` 为全小写 lang／ji，首版按 "Lang"／"Ji" 匹配漏掉。已改不区分大小写匹配，重下成功（BANK-OK）。
  - exec 通道卡住一次（查日志的 exec 超 2 分钟无返回），按前两轮做法 `colab restart-kernel` 恢复；detached 的渲染任务不受影响。

## 环境记录（供复现者避坑）

- 新会话系统 python 自带 torch 2.11.0+cu130，`svc-colab-setup.sh` §0b 走 uv+venv 路径建 `/content/lindong_venv`（torch 2.10.0+cu128）。会话内跑驱动一律用 venv python（`run_shiguang_*.sh` 已按此处理）；裸 `python3` 指系统 python，缺 pretty_midi 等包，§6 在此 python 下跑会误报失败。
- `fix_rotary_check6.sh` 初版误跑系统 python；已修正为 venv python＋LD_LIBRARY_PATH 指向 venv 内 nvidia/npp/lib，否则 torchcodec 报 `libnppicc.so.12` 缺失。
- 共享脚本缺口（待父 agent 折入 `svc-colab-setup.sh`）：§6 自检 import rotary_embedding_torch，但该包只在 SoulX preprocess/requirements.txt 里；未克隆 SoulX 的新会话会跳过该 requirements，§6 必然失败。应在 §3 显式 `pip install rotary-embedding-torch`，不再依赖 SoulX 克隆。

## 来源与许可

- 绮萱 Qixuan v2.7.0：`yqzhishen/qixuan-diffsinger`，仓库记载明确允许商业使用（需署名并标注 AI 合成）。
- 琅／齑：`github.com/BaiShuoQwQ/DiffSinger_VoiceBanks_release`，全部标注禁止商用、禁止二次分发，仅限个人实验。
- 声码器 `pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.oudep`：openvpi/vocoders Releases。
- 谱子：《时光》score_v3。只作本地实验，不分发。Wang 2026-11-05 18:33 定：不考虑协议和授权作排除条件。只做实验，不发布，不涉及商业。来源与许可信息如实记录，发布前重核。

## 文件清单

本目录：REPORT-shiguang-slices.md（本报告），PROGRESS.md（进度），shiguang_slices.json（转换后谱子），dl_shiguang_banks.sh／dl_lang_ji.sh（声库下载），run_shiguang_a.sh／run_shiguang_all.sh／run_shiguang_c_fix.sh／run_shiguang_bc.sh（渲染脚本），fix_rotary_check6.sh／fix_check6_venv.sh（§6 补装），qixuan_slices.zip／lang_slices.zip／ji_slices.zip（交付包），stage1_shiguang/（9 份 wav＋sidecar＋量测 JSON 原件），stage1_shiguang.tar.gz（会话打包下载件）。

会话内：/content/stage1_shiguang/（9 份产物），/content/voicebanks/（绮萱＋声码器），/content/voicebanks_banks/（琅／齑），/content/*_map.json（三库 map），/content/lindong_venv/（torch 2.10 环境）。
