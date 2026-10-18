"""§B15 的**只读**访问尝试账：``list_fetch_attempts``。

## 为什么这一本账必须有工具面

采集设计 §5 逐字要的是「每一次尝试都留一行账」，而那一行回答的问题是**事后**的：
「这个 URL 那天到底怎么样了」。账落在盘上、而工具面上没有落点时，这个问题只有一种
回答方式 —— 拿 ``sqlite3`` 打开那个库。而那个库在一台部署机上的位置、那张表的列名、
以及「哪些行属于这次排查」，都不是提问的人手里有的东西。

五件具体的事，只有在这一页上才答得出来：

* **同一个来源上一批账号都撞了墙。** 20 个账号在同一天、同一个挑战页上各留一行，
  在账上是 20 行同 URL 的记录。没有这一页，那 20 次尝试的唯一痕迹是「这 20 个账号
  一条证据都没产出」—— 而那与「这些账号本来就是空的」在报告上同形。
* **这一次为什么没有证据。** 一次 403 或一次地址判据拦下**产不出一条证据**
  （``ports/fetch_attempt.py`` 的模块 docstring 把这条差异写全了），所以证据账上
  一个字都没有。而它恰恰是最需要被记下来的一类尝试。
* **等待期记在哪。** 429 之后的那段 ``Retry-After`` 记在**来源**上，下一次抓取那个
  来源的调用会撞上它 —— 而那一格在回执里只有一个 ``deferred_backoff``，
  说不出「还要等多久、是谁把那扇门关上的」。账上有 ``retry_after_s``。
* **哪几笔还悬着。** §9 与 §5 要求「未知计费先对账」，而对账的第一步是从账上挑出
  悬着的那几笔 —— 那是 ``counts.by_settlement`` 里 ``unknown_pending_reconciliation``
  那一格。它必须**是一个数出来的数**，而不是「比别的行少的那些」：后者只在整本账被
  全量读过时才对得上，而这一页有 ``limit``。
* **这一次到底有没有出过网。** 这本账把「抓一个真实的第三方公开页面」与「在一个受控
  夹具或本机靶子上跑一遍」记在一起，而两者在回执上同形。``docs/MCP_SERVICE.md``
  §8.C.2 要求两者都必须被标注，理由它自己也写了：一份受控夹具**不能**算作市场产出或
  真实站点改善。``counts.by_material`` 是这件事在服务面上的落点，也是为什么它必须
  像上面那三张表一样枚举每个成员 —— 「现场 200、自造 0」与「没人标过」不是同一句话。

## 一条工具，而它只读

* 它走 ``FetchAttemptStore`` 的**读**入口（``list_attempts``），而那个端口没有
  ``update`` / ``delete`` / ``prune``：这本账只增不改（见端口 docstring）。所以
  「这一次查询会不会顺手改了什么」在签名上就答完了。
* 它**不**构造 ``HttpxFetcher``、不接治理层、不驱动任何一轮抓取：没有出网的东西
  经过这一条工具。
* **读一次不建库。** 库文件不存在时直接回 ``no_store``，不去构造存储 ——
  ``SqliteFetchAttemptStore.__init__`` 会 ``ensure_schema``，也就是**会建库**。
  措辞与 ``get_scene`` / ``list_scenes`` / ``get_continuation_status`` 用的是
  同一组常量，不在这里另编一套（两处各编一套的话，同一件事在报告里会有两种说法）。
  反过来，库**已经在了**、只是还没有这张表，构造存储会把缺的表补上 —— 那是
  ``ensure_schema`` 的自愈，不是「一次读建了一个库」（见 ``test_sqlite_fetch_attempt``
  的自愈判据）。

## 谁看得见

``_CONTROL``。这本账**跨主体**：同一个 URL 上的尝试会被记在不同 ``scope`` 名下，
而 ``scope`` 就是业务主体的名字。研究身份读得到它，等于让一次研究看见**别的账号**
在问什么；而研究侧本来就在自己那一跳的回执上拿到了诊断（``FetchResult.diagnosis``）
—— 它不需要账。

## 这一页**答不出**什么

* **熔断此刻开不开。** 那个状态是**进程级**的（``AccessGovernor`` 活在内存里），
  盘上没有它的行。所以这一页读得出「最近三次都被拒了」，读不出「门现在是关着的」。
  这一格要说出来，而不是留给读的人从「已熔断」三个字里猜。
* **账上一共有多少行。** 有 ``limit`` 时截断会被报出来，但**不**为了报一个数去扫
  全表（那是一次全表计数，而这一条工具是给排查用的，不是给统计用的）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...config import Settings
from ...domain.access import ActionTaken, SuspectedCause
from ...domain.fetch_attempt import FetchAttempt, MaterialProvenance, SettlementState
from ...domain.util import to_utc
from ...ports.fetch_attempt import FetchAttemptStore
from .tools import _SCENE_RECORD_NO_STORE, _SCENE_RECORD_PRESENT, Handler

__all__ = ["MAX_ATTEMPTS", "fetch_attempt_handlers"]

#: 一次读最多回多少行。缺省值，不是上限 —— 传 ``limit: null`` 要全部是有意的
#: （照 ``review.MAX_CASES`` 的先例）。
#:
#: 200 是有依据的：这本账是**唯一**一张按「每一次尝试一行」增长的表，量级与别的表
#: 不同（一次发现跑下来轻轻松松几百行）。排查要看的是「最近这一批怎么样了」，
#: 而 200 条尝试足够覆盖一次运行的尾巴。
MAX_ATTEMPTS = 200

#: 四格**当场算的**读数。账上没有它们的列 —— 它们的定义在
#: ``domain/fetch_attempt.py`` 的属性里，这一层不重新定义一遍（两处各写一份的形态是
#: 「同一个字段在两份报告里不一样」）。写在这里是为了让回执说得出「哪几格不是账上的」。
_DERIVED = ("elapsed_ms", "body_kept", "status_unknown", "ok")

_NOTE = (
    "``attempts`` 里每一行是账上那一行的逐字段投影，**最新在前**（按开始时刻降序，"
    "同刻的按 id 降序）。其中 " + "、".join(f"``{name}``" for name in _DERIVED) +
    " 四格是**当场算的**：账上没有它们的列，定义在 ``domain/fetch_attempt.py``。\n"
    "``counts`` 数的是**这一页**，不是整本账（见 ``truncation``）—— 一份把当页数读成"
    "全库数的报告，会在账长起来之后越报越小。\n"
    "``observed_status`` 为 ``null`` 是「这一次没有观测到 HTTP 状态」，**不是** 200、"
    "也不是 0；``body_kept`` 为 ``false`` 而 ``body_bytes`` 非零是「拿到过那些字节、"
    "而我们没有留下它」—— 这两组读法各有一条判据钉着。"
)

_FILTER_NOTE = (
    "``filters.scope`` 为 ``null`` 是「**不筛**」（全部主体），为 ``\"\"`` 是「筛"
    "**不属于任何主体**的那一批」—— 两者不是同一件事，三个筛子都照这条："
    "``null`` 是不筛，给了值就是筛那个值。"
)


def fetch_attempt_handlers(
    *,
    settings: Settings,
    attempts: Callable[[], FetchAttemptStore] | None,
) -> dict[str, Handler]:
    """本构建的访问尝试账处理器，按工具名索引。

    Args:
        settings: 配置。这一层只用它的 ``profile``（回执里那个工作区）与
            ``database_path``（核「这个部署写过东西没有」）。
        attempts: 尝试账的**工厂**。``None`` 表示这个服务实例没有落点 —— 那时
            **这一条工具不注册**，而不是注册成恒回空列表的实现：一本空的账与一本
            没接线的账在报告上同形，而前者的处置是「这个来源最近没被问过」、
            后者是「去接线」（与 ``continuation_handlers`` 在三个工厂缺任何一个时的
            取向逐字相同）。

    工厂是惰性调用、且在这一条工具**被调用之后**才展开：``_compose`` 的契约是
    「构造服务面需要的一切，不绑端口、不建运行目录」，而 ``SqliteFetchAttemptStore``
    的构造会 ``ensure_schema``，也就是会**建库**。
    """
    if attempts is None:
        return {}

    def list_fetch_attempts(
        scope: str | None = None,
        url: str | None = None,
        since: str = "",
        limit: int | None = MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        """列这个部署留下的访问尝试，可以按主体、地址与起始时刻筛。"""
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
        ):
            raise ToolError(
                f"limit 是 {limit!r}。它要么是正数，要么是 null（要全部）—— "
                "0 与负数在这里没有别的意思，而它们看起来像「这本账是空的」。"
            )
        since_at = _moment(since)
        filters = {
            "scope": scope,
            "url": url,
            "since": None if since_at is None else since_at.isoformat(),
            "limit": limit,
        }

        if not settings.database_path.is_file():
            return {
                "workspace": settings.profile,
                "store_state": _SCENE_RECORD_NO_STORE,
                "filters": filters,
                # 两格都是 ``None`` 而不是空列表 / 空计数：一个 ``[]`` 与一个全零的
                # ``counts`` 会被读成「账读过了，一次都没有」—— 而这里什么都没读到。
                "attempts": None,
                "counts": None,
                "truncation": None,
                "note": (
                    f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                    "所以这本账读不出任何东西。这**不是**「没有尝试过」—— "
                    "前者的处置是去跑一次抓取，后者说明这一批尝试都成功了、"
                    "或者根本没有人问过这些地址（那要按 ``scope`` 筛一遍才知道）。"
                ),
            }

        # 多要一行只为回答「还有没有更旧的」：截断必须被报出来，而 ``limit`` 是
        # 推进 SQL 的（这本账会长到不该全量读回内存的量级）。多要一行比数一次全表便宜。
        asked = None if limit is None else limit + 1
        rows = attempts().list_attempts(
            scope=scope, url=url, since=since_at, limit=asked
        )
        truncated = limit is not None and len(rows) > limit
        shown = rows if limit is None else rows[:limit]

        return {
            "workspace": settings.profile,
            "store_state": _SCENE_RECORD_PRESENT,
            "filters": filters,
            "attempts": [_row(row) for row in shown],
            "counts": _counts(shown),
            "truncation": (
                None
                if not truncated
                else {
                    "limit": limit,
                    "note": (
                        "账上还有**更旧的**没回：这一页截的是最旧的那一头（顺序按开始"
                        "时刻降序），所以被丢下的是最早的尝试。到底还有多少行**没有数**"
                        " —— 那是另一次全表计数，而这一条工具不为了报一个数去扫全表。"
                        "要往更早看就给一个更早的 ``since``。"
                    ),
                }
            ),
            "note": f"{_NOTE}\n{_FILTER_NOTE}",
        }

    return {"list_fetch_attempts": list_fetch_attempts}


def _moment(text: str) -> datetime | None:
    """收下一个 ISO-8601 时刻。空串 = 没给。

    带时区是**要求**（``to_utc`` 逐字：naive 不得进入本系统）。一个省掉时区的时刻
    在这里能被 ``fromisoformat`` 解析出来，却会按服务器所在机器的本地时区解释 ——
    于是「从这里往后的尝试」取决于这个服务跑在哪台机器上。

    与 ``review._moment`` / ``outreach`` 里那两处同形，是**第三份**。没有在这一片
    抽出来共用：抽它要同时动那两处的调用点，而这一片要收的是尝试账的读面。
    三份同形的代价记在这里，抽取留给下一片（先例：端口提取等第二个实现出现）。
    """
    value = (text or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(
            f"since 是 {value!r}，不是一个 ISO-8601 时刻"
            "（例如 2026-09-26T00:00:00+00:00）。"
        ) from None
    try:
        return to_utc(parsed)
    except ValueError as exc:
        raise ToolError(
            f"since 是 {value!r}，它没有带时区：{exc}\n"
            "  一个省掉时区的时刻会被按服务所在机器的本地时区解释，于是「从这里往后"
            "的尝试」取决于服务跑在哪里。"
        ) from exc


def _row(attempt: FetchAttempt) -> dict[str, Any]:
    """账上那一行的投影：逐字段 + 那四格当场算的读数。"""
    view = attempt.model_dump(mode="json")
    view["elapsed_ms"] = attempt.elapsed_ms
    view["body_kept"] = attempt.body_kept
    view["status_unknown"] = attempt.status_unknown
    view["ok"] = attempt.ok
    return view


def _counts(rows: list[FetchAttempt]) -> dict[str, Any]:
    """这一页的读数，按根因、处置、预算结算与**材料来源**各分一遍。

    **零是真读数**：这里数的是一批我们真的读过、真的看得见的行 —— 与
    「一个没人测过的指标」不同，后者写 0 是把「没测」说成了「测出来是零」。
    所以下面四张表把枚举里**每一个**成员都列出来，哪怕是 0。

    按根因分是为了回答 §5 那个问题（「20 个账号撞同一个来源」在账上长什么样）：
    同一条 URL 上 ``waf_challenge`` 的行数就是那件事的形状。

    按材料分是为了回答一个**这一页最容易被读错**的问题。这一本账会把两种行记在
    一起：一种抓的是真实的第三方公开页面，一种是我们在受控夹具或本机靶子上跑出来
    的。回执上两行同形，所以一份「抓了 200 次、成功 180 次」的读数在有 ``by_material``
    之前**说不出**那 180 次里有多少次根本没出过网 —— 而 ``docs/MCP_SERVICE.md``
    §8.C.2 逐字要求两者都必须被标注，正是因为这个：一份受控夹具**不能**算作市场
    产出或真实站点改善。零仍然是读数：``synthetic: 0`` 说的是「这一页上的每一行都
    出过网」，那是一句可以被质疑、可以被对账的话，而不是一个没人算过的数。

    按结算分是为了回答**另一个**方向的问题：§9 与 §5 要求「未知计费先对账」，而对账
    的入口是一次筛选 —— 「哪几笔还悬着」。``unknown_pending_reconciliation`` 那一格
    如果是靠数出「比别的行少的那些」来得到，它就**只在这本账被全量读过时**才对得上；
    而这一页有 ``limit``（见 ``truncation``），那时少的那几行恰好落在被截掉的那一头，
    读法会变成「没有悬着的账」—— 正是这一格最不该撒的那个谎。所以它与其他两表同形：
    枚举每个成员，零写零。
    """
    return {
        "returned": len(rows),
        "with_body": sum(1 for row in rows if row.body_kept),
        "status_unknown": sum(1 for row in rows if row.status_unknown),
        "challenged": sum(1 for row in rows if row.challenge_detected),
        "by_cause": {
            cause.value: sum(1 for row in rows if row.suspected_cause is cause)
            for cause in SuspectedCause
        },
        "by_action": {
            action.value: sum(1 for row in rows if row.action_taken is action)
            for action in ActionTaken
        },
        "by_settlement": {
            state.value: sum(1 for row in rows if row.budget_settlement is state)
            for state in SettlementState
        },
        "by_material": {
            provenance.value: sum(1 for row in rows if row.material is provenance)
            for provenance in MaterialProvenance
        },
    }
