"""vocaledit 结构性修复（服务侧测量与分段基础设施）。

只含经离线验证的结构性修复：行间缺口一级切点分段（``segments``）与
边界感知重测仪器（``instrument``）。不含任何 warp 路线代码，不新增、
不改动任何服务端点。证据见 ``evidence/``。
"""

from app.vocaledit import instrument, segments  # noqa: F401
