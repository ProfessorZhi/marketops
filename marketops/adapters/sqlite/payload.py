"""SQLite 各表共用的 payload 编解码。

## 为什么它值得单独一个模块

「把领域对象存成一列 JSON 文本」这件事在本目录里已经有三处做（证据、决策回执、
现在的场景与维护），而这三处的编解码**必须逐字节一致**：同一份内容在两处编成
两串不同的字节，做 diff 的人会以为内容变了；读回时一处报错一处静默跳过，则
「少了记录」会表现为「这条流程没找到东西」。

这三处曾经各自写了一遍 ``json.dumps`` + ``json.loads``。漂移的形式不是语法错，
而是 ``sort_keys`` 在一处写了一处在另一处忘了 —— 那不会报错，只会让
「同一条记录两次入库得到两个哈希」这件事偶尔发生一次。

## 两个方向的失败处置**不同**

* 编码不会失败（领域对象都是 pydantic 模型，``mode="json"`` 之后必然可序列化）；
* 解码会失败，而且**必须报错，不能跳过**。这个库里只有本模块写，一行读不出来
  意味着库被外部改过或者损坏了。静默跳过会让「证据少了」看起来像「没找着」。
"""

from __future__ import annotations

import json

from ...domain.errors import StoreError


def dump_payload(payload: dict) -> str:
    """写出去的那一串字节。

    ``sort_keys`` 只为让同一份内容在库里是同一串字节 —— 便于人直接读库、做 diff。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_payload(raw: str, where: str) -> dict:
    """读回来一行。``where`` 是给报错用的定位串（表名 + 键），不是数据。"""
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"状态库里的 payload 不是合法 JSON（{where}）：{exc}。"
            "人工修复后再继续 —— 静默跳过会污染后续所有统计。"
        ) from exc
    if not isinstance(loaded, dict):
        raise StoreError(f"状态库里的 payload 不是对象（{where}）：{type(loaded).__name__}")
    return loaded


__all__ = ["dump_payload", "load_payload"]
