"""MarketOps —— known-account contact research pipeline。

本阶段（Windows Demo）固定处于**研究模式**：

    research_only    = True
    send_authorized  = False

任何邮件发送 / follow-up / 回复监控 / SMTP 验证 / 邮箱猜测能力都**未启用**，
也不在 roadmap 的本阶段范围内。这两个常量是硬约束，不由环境变量覆盖，
并在证据模型层被校验（见 ``marketops.domain.evidence``）。
"""

from __future__ import annotations

__version__ = "0.1.0"

#: 本轮固定为研究模式。不要改。
RESEARCH_ONLY: bool = True

#: 本轮固定为未授权发送。不要改 —— 模型层会拒绝构造值为 True 的对象。
SEND_AUTHORIZED: bool = False

__all__ = ["__version__", "RESEARCH_ONLY", "SEND_AUTHORIZED"]
