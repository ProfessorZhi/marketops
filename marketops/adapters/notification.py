"""本机收件箱 sink：**唯一一个 ``accepted`` 证明得出来的出口**。

## 为什么这里需要真有一个 sink

``ports/notification.py`` 那一节的判据是「我们只能知道对方收下了，或者不知道」。
一条停在端口上的契约没有任何一处会红：``InboxSink`` 是那个**能被观测**的落点 ——
它把事件追加进一份本机的 JSONL，于是 ``accepted`` 有内容可指：那一行**此刻在盘上，
并且读得回来**。没有这一份，「投递成功」在整个仓库里就只是一句声明。

## 它不对外说话

这一份不起网络连接、不读任何凭据、不认识飞书。理由不是「先不做」，而是**判据**：
一条要联网的 sink 的 ``accepted`` 无法在测试里被证明（对端可能收了、可能没收到而我们
的回执丢了 —— 那正是 ``UNKNOWN`` 的两种情形之一）。把真出口做出来之前，
这里刻意只提供**本机可复核**的那一个，而 ``UNKNOWN`` 那一档由
``ports/notification.py::DeliveryOutcome`` 如实承载，不靠假成功绕过去。

## 它是**目录**，不是一台订阅者

一份收件箱装一个工作区的产出，落点是 ``<root>/<workspace>/inbox.jsonl``。目录里的
每一行都带 ``workspace``，而写入前会判一次它是否与本份配置一致 —— 与
``adapters/jsonl/store.py`` 那一节逐字相同：一个目录只装一个工作区的产出，
不一致就拒绝，因为「这一行不属于这里」与「这里没有这一行」在调用点长成同一个空结果。

## 同一条投递重复一次**不会**追加第二行

``deliver`` 先扫一遍这份收件箱，看 ``(event_id, subscription_id)`` 是否已经有一行。
这不是为了省事：投递失败只重试通知（§13 ``:410``），而重试的判据是**收据**里的
``attempt``。如果网络回执丢了（我们写了盘、返回的路上崩了），调用方会记一条
``UNKNOWN`` 并重试 —— 那次重试**必须**落在同一行上，否则「投了两次」在收件箱里
读起来就是两条事件，而它们其实是同一条。

代价与 ``adapters/jsonl/store.py`` 那一节逐字相同：投第 N 次要先把 N-1 行读一遍。
这一份因此是**审计/落点通道**，不是主存 —— 收件箱会长，而它长到多大是可预期的
（一个人一天能读几条通知）。

## ``failure`` 说得出是哪一种坏

写盘失败（目录不可写、磁盘满、编码错误）落在 ``UNKNOWN`` 上，且 ``failure`` 里写的是
**真实的异常文本**加它是哪一次写 —— 不是一句「投递失败」。读的人要靠它决定是自己
重试还是去修目录，而这两件事的处置完全不同。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.notification import (
    DeliveryAttempt,
    DeliveryOutcome,
    NotificationEvent,
    NotificationSubscription,
)
from ..domain.util import to_utc

__all__ = ["InboxSink"]


class InboxSink:
    """把事件追加进 ``<root>/<workspace>/inbox.jsonl`` 的本机出口。

    构造参数只有 ``root`` 与 ``name``。``name`` 必须与订阅上 ``sink`` 那一格
    **逐字相同** —— 收据上的 ``sink`` 列取自订阅（``ports/notification.py`` 那一句），
    所以一个名字对不上的 sink 在投递时是一条记在案的 ``unknown``。
    默认的 ``"inbox"`` 就是这一族在本机部署里用的那个名字。
    """

    def __init__(self, root: Path, *, name: str = "inbox", filename: str = "inbox.jsonl") -> None:
        self.name = name
        self._root = Path(root)
        self._filename = filename

    def path_for(self, workspace: str) -> Path:
        """这条工作区的收件箱落在哪。**公开**，因为运维要能 `grep` 它。"""
        return self._root / workspace / self._filename

    def deliver(
        self, event: NotificationEvent, subscription: NotificationSubscription
    ) -> DeliveryAttempt:
        """写一行，回 ``ACCEPTED`` 或 ``UNKNOWN``。**没有第三个答案。**

        ``ACCEPTED`` 时的 ``message_ref`` 是 ``<相对路径>#<行号>``（从 1 起）。
        它可复核：把那行读出来，``event_id`` 与 ``subscription_id`` 对上就是这次投递。
        """
        path = self.path_for(event.workspace)
        line = {
            "event_id": event.event_id,
            "subscription_id": subscription.subscription_id,
            "workspace": event.workspace,
            "dedupe_key": event.dedupe_key,
            "kind": event.kind,
            "severity": event.severity.value,
            "summary": event.summary,
            "occurred_at": to_utc(event.occurred_at).isoformat(),
            "sink": subscription.sink,
            "principal": subscription.principal,
        }
        try:
            existing = self._find(path, event.event_id, subscription.subscription_id)
            if existing is not None:
                # 同一条投递已经在盘上 ⇒ **不追加第二行**，回同一行。
                # 见模块 docstring 最后那一节：重试必须落在同一行上。
                return DeliveryAttempt(
                    outcome=DeliveryOutcome.ACCEPTED,
                    message_ref=existing,
                    detail={"sink": self.name, "replayed": True},
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            # 行号 = 现有行数 + 1，而它是在**打开之前**数出来的 —— 追加写完再数
            # 会把「这一行是第几行」变成一个与并发有关的值。
            lineno = self._count(path) + 1
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                handle.flush()
        except OSError as exc:
            # 这里**不能**是一件静默的跳过：一次写盘失败而调用方以为投出去了，
            # 会让一条事件在报告上「已投递」，而它一行都不在盘上。
            return DeliveryAttempt(
                outcome=DeliveryOutcome.UNKNOWN,
                failure=f"收件箱写入失败（{path}）：{exc.__class__.__name__}: {exc}",
                detail={"sink": self.name, "path": str(path)},
            )
        return DeliveryAttempt(
            outcome=DeliveryOutcome.ACCEPTED,
            message_ref=f"{self._relative(path)}#{lineno}",
            detail={"sink": self.name, "path": str(path)},
        )

    # -------------------------------------------------------------- 内部
    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._root))
        except ValueError:  # pragma: no cover - root 是这一份自己拼出来的
            return str(path)

    def _count(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _find(self, path: Path, event_id: str, subscription_id: str) -> str | None:
        """这一条投递已经在盘上吗。是的话回它的 ``message_ref``。

        损坏的行**不跳过**：读不出来就抛（``json.JSONDecodeError`` 不是 ``OSError``，
        所以它会穿出去）—— 一份读不动的收件箱报成「这一条还没投过」会让下一次投递
        追加第二行，而「投过没有」这个问题在那一刻已经答错了。
        """
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if (
                    row.get("event_id") == event_id
                    and row.get("subscription_id") == subscription_id
                ):
                    return f"{self._relative(path)}#{lineno}"
        return None
