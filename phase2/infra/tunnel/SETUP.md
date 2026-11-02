# 命名 tunnel：现状、卡点、建 tunnel 步骤

## 现状（2026-11-02 本机实测，见 TEST-LOG.md）

- 本机已有一条 SSH 用命名 tunnel（`bigbangbang.wangda.today`），
  进程在跑。**本线绝不动它**：配置、凭据、进程一律不碰；SVS 用
  独立域名、独立 tunnel、独立配置文件
  （`~/.cloudflared/config-svs.yml`，不用默认 config.yml，避免
  与 SSH tunnel 的配置串线）。
- 本机 `~/.cloudflared/` 只有 SSH tunnel 的 token（内容未读、未复制）
  与 `user.pub`，**没有 `cert.pem`、没有 config、没有 tunnel 凭据
  json**。`cloudflared tunnel list` 实测报错（缺 origin cert）。
- 因此本机现在**建不了命名 tunnel**：缺 `cloudflared tunnel login`
  （浏览器 OAuth，登录一个 Cloudflare 账号）这一步，而这一步需要
  人在浏览器里完成，不能由 agent 代做。

## 卡点（建 tunnel 前必须先有，缺一不可）

1. 一个 Cloudflare 账号的浏览器登录（`cloudflared tunnel login`
   产生 `cert.pem`）。
2. 一个该账号名下的域名 zone（DNS 归该账号管），用来挂 SVS 子域。
3. 域名定夺：候选 `svs.wangda.today`（与 SSH tunnel 同后缀），但
   **未确认 `wangda.today` 是否在该登录账号名下**——若不在，改用
   该账号内任一 zone 下的 `svs.<该域名>`，不要硬套 wangda.today。
   定夺人：Wang（告诉 agent 账号与 zone 即可，不用给密码）。

## 建 tunnel 步骤（在服务所在机上做——Colab 会话或 Linux GPU 机，
## 不是本 VM；tunnel 必须与服务同机）

```bash
cloudflared tunnel login
cloudflared tunnel create lingren-svs
# 记下输出的 TUNNEL-ID；凭据写进 ~/.cloudflared/<TUNNEL-ID>.json
cloudflared tunnel route dns lingren-svs svs.<你的域名>
cp tunnel/config.yml.template ~/.cloudflared/config-svs.yml
# 把 config-svs.yml 里的 <TUNNEL-ID>、<HOSTNAME> 换成真实值
SVS_API_KEY=<你的key> bash tunnel/run-service-colab.sh  # SVS_TUNNEL_MODE=named
```

## 验证清单（tunnel 建好后逐项过）

1. 本机：`curl http://127.0.0.1:8000/health` → `{"status":"ok",...}`。
2. 外网：`curl https://svs.<你的域名>/health` → 同上。
3. 外网无 key：`curl https://svs.<你的域名>/v1/voices` → 401。
4. 外网带 key：`curl -H "Authorization: Bearer $SVS_API_KEY"
   https://svs.<你的域名>/v1/voices` → 200。
5. Yeban 侧把服务地址填成 tunnel URL + key，跑一次 synthesize 往返。

## 开发期 fallback（命名 tunnel 建好之前）

开发期继续用 quick tunnel（`cloudflared tunnel --url
http://localhost:8000`，免账号、URL 每次变、Yeban 侧每次更新地址）。
注意：本 VM 的 egress 代理会拦 quick tunnel 建 tunnel 请求（实测
超时 + 403，见 TEST-LOG.md），所以 quick tunnel 也要在服务所在机
（Colab 会话）上起，不要在本 VM 上起。
