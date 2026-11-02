# Infra：命名 tunnel + Docker（Phase 2）

两部分。命名 tunnel 见 `tunnel/`（SETUP.md 是步骤，TEST-LOG.md 是
实测记录）；Docker 见本文件。

## Docker：三步（Linux GPU 机上）

### 1. 构建

本机没有 Docker daemon，**本镜像未构建**（见 REPORT.md）。在
Linux GPU 机上：

```bash
cd ~/workspace/svs-api-service   # 构建上下文必须是项目根
docker build -f phase2/infra/Dockerfile -t lingren-svs:phase2 .
```

### 2. 运行

```bash
mkdir -p models/soulx models/vocalrender data
# 权重放进 models/（见“权重获取”）；SoulX 仓库检出放进 soulx/
SVS_API_KEY=<你的key> docker compose \
  -f phase2/infra/docker-compose.yml --project-directory . up --build
curl http://127.0.0.1:8000/health   # -> {"status":"ok",...}
```

关键点：

- GPU 预留：compose 里 `deploy.resources.reservations.devices`
 （nvidia 驱动、1 卡）。宿主机需 NVIDIA Container Toolkit。
- 端口：8000。API key 未设时 compose 直接失败（`${SVS_API_KEY:?}`），
  远端服务绝不裸奔。
- 服务代码是构建时 COPY 进镜像的（可复现）；开发期想跟 live 代码
  走，在 compose 里把 build 换成 bind mount（文件里写了注释方法），
  但那不是交付镜像。
- macOS 上不要用 Docker 跑本服务：macOS Docker 无 GPU 直通。
  Mac 本地永远走原生 MPS/MLX 路线。

### 3. 权重获取（绝不烘焙进镜像）

| 权重 | 来源 | 挂载后路径 |
| --- | --- | --- |
| SoulX `model.pt`（约 2.8 GB） | Hugging Face `Soul-AILab/SoulX-Singer`（Phase 0 实测下载 19.4 s，L4 机） | `./models/soulx/model.pt` → 容器内 `/models/soulx/model.pt` |
| SoulX 仓库检出（打好 phase1/patches/00-02） | `Soul-AILab/SoulX-Singer` 仓库 + 本项目补丁 | `./soulx/` → `/soulx`（`SOULX_REPO`） |
| VocalRender 权重（约 9.5 GB，6 文件） | `pymaster17/VocalRender`（Phase 0 实测） | `./models/vocalrender/`（适配器环境变量名由 VR 接线线定，本文件不编造） |

权重与代码许可分开处理（设计 §8）：镜像只含代码与依赖，
权重只走只读卷（`:ro`）。没有权重时服务仍能起（/health 通），
合成在缺权重处响亮失败，不静默。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `Dockerfile` | CUDA 12.4.1 runtime 基础镜像 + 服务代码 COPY + torch cu124；权重只走卷 |
| `requirements-gpu.txt` | phase1 服务依赖 + torch cu124（与 phase1 requirements.txt 保持同步） |
| `docker-compose.yml` | GPU 预留 + 权重/仓库只读卷 + 数据卷 + 8000 端口 + API key 强制 |
| `tunnel/config.yml.template` | 命名 tunnel 配置模板（占位符，不可直接跑） |
| `tunnel/run-named-tunnel.sh` | 起命名 tunnel 的守卫脚本（拦占位符、拦 SSH 域名） |
| `tunnel/run-service-colab.sh` | Colab 会话内起服务 + tunnel 的启动脚本 |
| `tunnel/SETUP.md` | 命名 tunnel 现状、卡点、建 tunnel 步骤、验证清单 |
| `tunnel/TEST-LOG.md` | 本机 tunnel 实测记录（两次失败原样记录） |
| `REPORT.md` | 三件的结论、实测数、未做项 |
