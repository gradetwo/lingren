# Tunnel 实测记录（2026-11-02，本 VM）

结论先写：命名 tunnel 未建成（缺登录，见卡点）；quick tunnel 在本机
也起不来（egress 拦）。两条都是实测失败，不是“没试”。

## 查到的现状（只看目录与配置名，未读、未复制任何凭据内容）

- `~/.cloudflared/` 内容：`token`、`user.pub`、`rescue.log`、
  `watchdog.log`、`ssh_host_keys/`。无 `cert.pem`、无 `config.yml`、
  无 `<tunnel-id>.json` 凭据文件。
- `/etc/cloudflared/` 只有 `hosts.edge`（SSH tunnel 的 edge 配置）。
- SSH tunnel 进程在跑（`cloudflared tunnel --no-autoupdate --metrics
  127.0.0.1:20241 run`，11-01 启动）。未动、未重启、未改配置。
- `cloudflared tunnel list` 实测输出：
  `Cannot determine default origin certificate path. No file cert.pem
  ... You need to specify the origin certificate path` ——
  即：没有登录过的 origin cert，list/create 都无从谈起。

## quick tunnel 两次实测（服务本地已起，/health 通）

- 第 1 次：`Requesting new quick Tunnel on trycloudflare.com...`
  → `failed to request quick Tunnel: Post
  "https://api.trycloudflare.com/tunnel": context deadline exceeded`。
- 第 2 次（60 秒限时）：同一步 →
  `quick tunnel provisioning failed with status 403: Your request was blocked.`
- 本机有 `https_proxy/HTTPS_PROXY/HTTP_PROXY`（egress 代理）；
  两次失败形态不同（超时、403），共同指向本机出口拦
  trycloudflare 建 tunnel 请求。不再重试（重复撞墙无意义）。
- 对照：同一时段本地 `curl http://127.0.0.1:8000/health` 通
  （`{"status":"ok","voices":0}`），说明服务本身没问题，卡在出口。

## 由此得出的部署结论

- tunnel 必须起在服务所在机（Colab 会话 / Linux GPU 机），不要
  在本 VM 上起 tunnel 去够远端服务——本 VM 连 tunnel 建 tunnel
  请求都被出口拦。
- 命名 tunnel 的剩余工作只剩三步，全是 Wang 侧一句话可定的事：
  ① 哪个 Cloudflare 账号登录；② 哪个 zone 挂子域（候选
  `svs.wangda.today` 待确认 zone 归属）；③ 在服务机上按 SETUP.md
  跑 `login → create → route dns → run`。
- 本线交付的是可直接用的模板与脚本（config.yml.template、
  run-named-tunnel.sh、run-service-colab.sh），不是一个编造出来的
  “已建成”tunnel。
