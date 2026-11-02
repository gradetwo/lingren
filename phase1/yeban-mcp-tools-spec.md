# Yeban 侧 MCP 客户端工具规格（Phase 1 交付：规格 + HTTP 层；仓库实现待排期）

状态说明：HTTP 客户端层已交付（`service/client/svs_client.py`，
stdlib-only，`LingrenClient`）。Yeban 仓库内的 MCP 工具本体（工具注册、
schema、测试）**未在本 Phase 动 yeban 仓库**，按下述规格实现即
可，不需再设计。

设计依据：`RESEARCH-AND-DESIGN.md` §6（两工具、配置、导入、放置语义）。

## 工具 1：synthesize_singing（提交）

- 入参：`parts` 数组，每项 `{label, score, voice_id, model, variant?,
  takes?, seeds?, options?}`，与 `POST /v1/synthesize` 的 part 形状
  一致；另有 `service_url?`（缺省读配置，见下）。
- 行为：只做 HTTP 提交，不含推理；返回 `{job_id, status, parts_total,
  takes_total, service_url}`。提交失败（连不上、4xx/5xx）直接报错，
  不返回空任务。
- 配置：服务地址 + API key 存在 Yeban 配置里，不进工程文件。本地
  缺省 `http://127.0.0.1:8000`，远端填 tunnel URL。

## 工具 2：svs_job（查状态、取音频与回执）

- 入参：`job_id`，`want`（`status` | `audio` | `receipt`，缺省 `status`）。
- `status`：返回服务端的任务状态与 parts_completed/total、
  takes_completed；`failed` 时同时返回 `error` 原文。
- `audio`：按 `part` + `take` 取 wav，返回本地暂存路径（或 base64，
  按 yeban-mcp 既有音频返回惯例二选一）；多 part 多遍时每次取一遍，
  不替用户混音。
- `receipt`：返回服务端回执全文（每遍 seed、耗时、对齐数组、门记录）。
- 导入：取回的 wav 作为音频轨片段落入工程（走现有音频片段机制），
  导入时重采样到工程采样率；每 part、每遍各成一个独立片段。

## 放置语义（工具文档里必须写明，不许含糊）

- SoulX：按乐谱位置直接贴；回执实测起点只作校验，偏差超过阈值时
  在回执里标出，不自动移位。
- VocalRender（Phase 2 接线后）：按回执实测起点贴；首音节对齐乐谱
  起点，其余按实测相对位置落位；回执同时标每个音节相对乐谱的偏差。
- 休止（SP）：回执对齐数组里 `lyric` 为 SP 的条目没有真实歌唱起
  点，放置时忽略（服务侧已在对齐条目里带 `lyric` 字段）。

## 失败语义（与服务端一致）

- 任务失败不返回静音 wav，不返回空音频冒充成功。
- takes 门耗尽（换 seed 重试 3 次仍不过门）时任务失败，回执里留
  门记录（逐对的包络相关度、F0 差、起音差），供用户判断是换 seed
  还是接受单遍。

## 实现清单（给接线执行员）

1. 在 yeban-mcp 按既有工具模式注册两工具（参数 schema、输出
   schema、工具数文案同步——仓库有工具数普查，见 yeban AGENTS.md）。
2. HTTP 层直接用 `service/client/svs_client.py` 的逻辑（或译成仓库
   既有 HTTP 客户端风格），不重写重试/轮询语义。
3. 测试：stub 服务（`service/` + stub 适配器）端到端——提交、轮询、
   取音频、取回执、失败路径（未知 voice、门耗尽）各一例；SoulX 真机
   链路用 Phase 1 的 Colab E2E 脚本（`colab_e2e.py`）复核。
