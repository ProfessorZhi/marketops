"""Feishu 适配层。

未配置凭据时必须可独立跳过，**不阻塞** Research Pipeline。

本层里有两件**性质不同**的东西，分开看才读得对：

* ``bitable.py`` 是**纯映射**（字段 → 列名），一行网络代码都没有，因此今天就被
  完整测到；
* ``client.py`` 是写入路径（含 HTTP），它对真实飞书租户的那一次写入是 **BLOCKED**
  的（缺业务授权 + 缺凭据 + 这条路**从未对厂商执行过一次**），而它在隔离
  环境里被一组用传输替身的测试完整跑过 —— 那些通过是 ``sandbox_pass``，
  不是真实厂商的 PASS。见 ``client.py`` 的模块 docstring。
"""

from __future__ import annotations

from .bitable import (
    BITABLE_FIELD_MAP,
    BITABLE_HANDOFF_FIELD_MAP,
    BITABLE_HANDOFF_KEY_COLUMN,
    BITABLE_HANDOFF_ROW_ID_COLUMN,
    BITABLE_HANDOFF_VERSION_COLUMN,
    BITABLE_OPPORTUNITY_FIELD_MAP,
    BITABLE_REPLY_BODY_CHARS_COLUMN,
    MAX_BITABLE_TEXT_CHARS,
    oversized_text_fields,
    to_bitable_handoff_record,
    to_bitable_opportunity_record,
    to_bitable_record,
)
from .client import (
    ADAPTER_STATE,
    LIVE_WRITE_BLOCKERS,
    LIVE_WRITE_STATE,
    RECORD_KIND_STATE,
    WRITE_STEPS,
    BitableWriteReceipt,
    FeishuBitableClient,
    FeishuSink,
    PushResult,
)

__all__ = [
    "ADAPTER_STATE",
    "LIVE_WRITE_BLOCKERS",
    "LIVE_WRITE_STATE",
    "RECORD_KIND_STATE",
    "WRITE_STEPS",
    "FeishuSink",
    "FeishuBitableClient",
    "PushResult",
    "BitableWriteReceipt",
    "BITABLE_FIELD_MAP",
    "BITABLE_OPPORTUNITY_FIELD_MAP",
    "BITABLE_HANDOFF_FIELD_MAP",
    "BITABLE_HANDOFF_KEY_COLUMN",
    "BITABLE_HANDOFF_ROW_ID_COLUMN",
    "BITABLE_HANDOFF_VERSION_COLUMN",
    "BITABLE_REPLY_BODY_CHARS_COLUMN",
    "MAX_BITABLE_TEXT_CHARS",
    "to_bitable_record",
    "to_bitable_opportunity_record",
    "to_bitable_handoff_record",
    "oversized_text_fields",
]
