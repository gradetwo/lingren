# DiffSinger 第一阶段换音库对照试验报告

日期：2026-11-05。分支：lingren-diffsinger-svc-twostage。只做第一阶段，不做 SVC 第二段。

## 结论

无一库通过烟囱门。琅与齑各渲 30 秒切片，量测见表。Leif 中文 map 建不出，本轮未试验。新短名单 6 库均无法由 agent 获取。全表无库下到 MIDI 23。

## 对比表

烟囱窗口：vox 25.6–55.6 秒，49 音，MIDI 34–46。量测：pyworld dio 独立测 f0。

| 库 | 检出音数 | 检出率 | 偏差中位 | ±0.5 半音内占比 | 有声帧占比 | p90 绝对偏差 | 结论 |
|---|---|---|---|---|---|---|---|
| 绮萱失败轨（基线） | — | — | — | — | 2–4% | — | 失败 |
| 绮萱 vox3（成功对照） | 45/46 | 97.8% | -0.022 半音 | — | — | — | 成立 |
| 琅 power | 13/49 | 26.5% | -0.038 半音 | 47.1% | 38.9% | 7.24 半音 | 未过门 |
| 齑 | 19/49 | 38.8% | +0.25 半音 | 48.0% | 52.0% | 8.53 半音 | 未过门 |

过门标准：检出率与有声帧占比须接近成功对照（vox3 检出 97.8%）。琅与齑检出率 26.5% 与 38.8%，多数音无稳定基频，p90 偏差超 7 半音。检出音的中位偏差虽小，但不代表成立。两库不进 4 轨全量。

补充现象：琅末句 dspitch 偏差 -5.59 半音，音高模型本身在低音区失真。齑后 4 句 dspitch 系统性偏高 +0.7–0.8 半音。

## 每库取舍（曲风＞性别＞音区）

琅：曲风流行（BaiShuo 系流行唱法，无摇滚训练记录）。性别文件未标明。音区宣称 C1–B7（MIDI 24–107），实测 30 秒窗检出率 26.5%。双说话人 power／soft，本试验用 power。许可 NC（LICENCES-zh-250112-NC-NS.txt）。来源 GitHub Release 直链。

齑：曲风流行。性别文件未标明。音区未声明，实测检出率 38.8%，高于琅。单说话人。许可 NC（同名文件）。来源 GitHub Release 直链。

Leif：曲风最匹配（Uprooted 高能摇滚、Soil 深低近砂砾）。性别男声。音区多语种。但中文音素仅 48 个，缺 20+ 复合韵母（ian／iao／ie／ing／uan／en／an／ang／uai／in／eng／ong／ui／un 等）。dsconfig 引用的 dictionary.txt 在发行包内不存在。作者站无切分规则文档。标准 DIFFS ZH G2P 输出与该库存量不匹配。中文 map 无法在可验证的前提下建立，本轮未试验。英文音素完整。许可 MIT（Copyright 2023 beikon）。来源 Google Drive 直链。

潋妤：全表唯一摇滚匹配。女声，中日英韩，2 小时数据。获取须加 QQ 群（群链接 qm.qq.com/q/F3SpPdbiFO）加百度网盘。性别不符，参考，不优先下载。

## 新短名单获取状态

华晓熊（男声 F2–A4，0.98 小时，310k 步）：百度网盘分享页可打开（标题“请输入提取码”）。verify 接口在两地出口均返回 errno -12，正确提取码 0527 与错误提取码 0000 返回相同。无法取得文件列表与下载直链。无登录无法下载。Bilibili 试听视频简介无其他下载链接。结论：须人工用百度账号下载，或作者提供新链。

Alemon（男中音，中文普通话，2 小时，320k 步）：获取方式仅 QQ 群 118932174。agent 无法加群。结论：须人工加群。

瑜瑾（男声 G2–F#5，四声线 normal／soft／solid／bright，2 小时，352k 步）：获取方式仅 QQ 群 519542908。agent 无法加群。结论：须人工加群。

Sirius（男声 G2–C5，3 小时）：表内获取方式为空，仅联系人 @昏惩。无下载路径。结论：无路可走。

洛希（男声 B2–F4，民谣低音炮）：走 zhibin.club#singers。站内下载走夸克网盘（站页脚链接 pan.quark.cn；姜柯先例为夸克链接），须登录或客户端，无直链。规约页返回 403。结论：须人工走夸克。

千夕（男声 C2–G4，日语库）：表内概述“男声日语声库”，试听为日语翻唱。无中文支持记录。结论：唱不了中文词，排除，不下载。

## 关键限制

全表 63 库最低只到 C2（MIDI 36，千夕，日语库）。vox4 有 22 音低于 MIDI 36，最低 MIDI 23（30.9 Hz，低于人声音区）。任何 DiffSinger 库都唱不到 vox4 最低音。须另想办法：+12 半音试验已完成（2026-11-05 22:42，提交 3a8eba01）：第一阶段 4/4 轨下移后偏差中位 ≤0.04 半音，第二段 SoulX 4/4 在 ±0.5 半音内；音色代价为谱质心约减半、音色变暗、逐音尾部拉宽。或改编谱面（移调或重写低音线），待 Wang 定。

## 先听哪个

先听齑切片（检出率与有声帧占比高于琅）。再听琅切片。两库均未过门。试听只判断音色方向，不作可用结论。

## 试听文件

ji_smoke.zip：vox 30 秒切片 wav＋sidecar JSON＋量测 JSON。SHA256：648e3416e79806c661a5d8c6963922e7f31107303012878adfda7197f2351bdd。

lang_smoke.zip：同上（琅 power）。SHA256：a33fa0fdf752214128db1d22e463f10d202d18a39eafac654d1c966b60a86361。

切片窗口 vox 25.6–55.6 秒，49 音，MIDI 34–46。峰值归一 0.89。

## 方法

推理参数与绮萱轮一致：steps 20，depth 0.6，expr 1.0，--dur score。声码器：琅用自带 pc_nsf_hifigan 2025.02；齑用共享同款。

量测：pyworld dio 独立测 f0，f0_floor 27 Hz，f0_ceil 800 Hz，帧长 5 ms。每音取中部 50% 帧求中位 f0。检出音定义：中部帧有声占比 ≥50% 且 |偏差| < 2 半音。

通用驱动 sing_bank.py：按模型声明输入喂参（run_filtered），已在绮萱上验证（probe 偏差中位 0.027 半音）。琅与齑的 acoustic 模型需 tension 输入，齑的 pitch／variance 模型声明 spk_embed 但无 .emb 文件（喂零向量），variance retake 末维因库而异（绮萱 2，琅／齑 3），均按声明处理。

英文词处理：琅与齑无英文音素，英文词作 melisma（不发新 token，音高与时长保留，不伪造歌词）。

会话 ds-svc-l4（L4）在线，未停。GPU 空闲。

## 文件清单

本目录：REPORT-banks.md（本报告），PROGRESS.md（进度），sing_bank.py（通用驱动），inspect_bank.py（声库探查），build_bank_map.py（拼音到音素 map 构建），measure_bank.py（dio 量测），baidu_share_list.py（百度分享取列表脚本，未成），lang_smoke.zip，ji_smoke.zip，vox_stage1_lang.wav／.json／_measure.json，vox_stage1_ji.wav／.json／_measure.json。

会话内：/content/banks_out/（两库切片与量测），/content/banks_tools/（脚本与 map），/content/voicebanks_banks/（琅／齑／Leif 三库原包），/content/banks_download.log（下载日志）。
