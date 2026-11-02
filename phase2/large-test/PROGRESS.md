# muscriptor large Colab 实测 — 进度
- 约 16:1x：连接器文档已建（custom.huggingface，helper hf_download.py，py_compile 过）；config.json 已验下载成功（125 B，variant large，dim 1536／24 头／48 层）→ token 有效、许可已接受，阻塞清除。
- model.safetensors 下载中（后台），Colab L4 新建被拒（已有 folk-v3-vr-pro L4 会话在跑，疑似 GPU 配额占满），等下载完成后再重试新建。
- L4 新建第二拒（两 L4 会话 folk-v3-vr-pro、folk-v5 同时在跑）。不再盲重试：等权重下载完成后再试，届时短任务会话可能已释放；若仍被拒，向父 agent 报容量阻塞。
- 传包待试命令形（ProxyCommand 桥接，目标 host 名由桥接忽略，需实测用户与 host 组合）：
  scp -o "ProxyCommand colab ssh --proxy-mode -s muscriptor-large-test" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null <本地文件> <user>@colab:/content/
- 传包确切写法已从 colabgen/ssh.py 核实（colabgen 是另一工具，只借其 SSH 写法）：ProxyCommand＝colab bin＋ssh --proxy-mode -s 会话；scp 须显式 -i /home/hatch/.ssh/id_ed25519＋IdentitiesOnly=yes（root 壳与 $HOME 不一致时默认 identity 会丢）；StrictHostKeyChecking=no、UserKnownHostsFile=/dev/null、ConnectTimeout=45；目标 root@colab-runtime；COLAB_AUTH=oauth2 走环境变量（subcommand 不加 --auth 旗标）。
- 待：scp 传权重＋驱动＋scale.wav → 驱动实测（--model 本地路径 --device cuda，计时＋8 音对照）→ 服务全链 → 写 REPORT-TRANSCRIBE-LARGE.md → 停会话 → 结算 CU。
- 约 16:4x：权重下载完成（5,465,642,136 B，sha256 ac4eb6ea…，文件头 394 张量可解析）；L4 会话 muscriptor-large-test 已建（folk-v5 会话已消失，配额让出）；安装已成（torch 2.11.0+cu130 cuda True，muscriptor 可导入）；小文件＋服务 app 目录 scp 成功。
- 教训：`colab exec -f /dev/stdin`＋heredoc 会挂住内核槽（两次实证，须 kill 掉）；此后探针一律先写实文件再 -f。
- 约 17:24：本机 VM 重启，scp 权重只传了 214 MB 即断；改 rsync --partial 断点续传（后台跑），传完后在 Colab 上验 sha256 再跑驱动。
- 完成 18:02 CST：rsync 续传成功，Colab 上大小＋sha256 与本地一致；驱动 7／8（首音 0.0 秒边界丢失，前置静音诊断 8／8）；服务全链通（job 9733f76c4aae4ffa，MIDI 153 B，rtf 0.5873）；会话已停（无活动会话，费率 0）；报告 REPORT-TRANSCRIBE-LARGE.md 已写，分支零改动，合入留给父 agent。
