"""§8/§9 的**只读**对账面：``list_open_reservations``。

## 为什么这一本账必须有工具面

``ports/budget.py::open_reservations`` 的 docstring 自己写着它的存在理由：

    「不自动退款意味着一定会有停在 ``RESERVED`` 的行，那么必须有一个地方能
    把它们列出来，否则「有多少额度悬着」只能靠翻库。」

今天那个「地方」在代码里**不存在** —— 端口方法没有生产调用者，只有契约测试在
叫它。于是 §9 与 §5 那条「未知计费**先对账**」的第一步（从账上挑出悬着的那几笔）
在服务面上无从开始：提问的人得先知道那个 SQLite 文件在哪、表叫什么、哪些行算悬着。

三件具体的事，只有在这一页上才答得出来：

* **哪几笔悬着，以及它们的 id。** 对账的下一步是把每一笔**结算**（活儿真的做了、
  钱真的花了）或**退款**（活儿没做，且要写明理由）。两个动作都要 ``reservation_id``，
  而报告上只有一个「已占用」的数字 —— 那是个答不出「拿哪一笔去核」的数。
* **悬着的是哪个科目。** 四类科目的量纲不同（§8），所以「悬着 3 个单位」在
  ``search`` 上与在 ``browser_subrequest`` 上的处置不同：一个要去看检索跑得对不对，
  一个要去看那个子请求策略。``counts.by_line`` 把四个科目都列出来，零写零。
* **一笔都没有时，是真的没有。** 一本被读过、确认没有悬账的账，与一本没接线的账，
  在同一份报告里必须长得不一样 —— 前者的处置是「继续」，后者是「去接线」。
  这与 ``list_fetch_attempts`` 是逐字同一条（见那个模块的 docstring）。

## 一条工具，而它只读

* 它只走 ``BudgetLedger.open_reservations`` 这一条**读**入口，而它不写任何行。
  「每个科目还剩多少」**不在这一页上** —— 那是 ``usage`` 的事，由 ``get_job`` 按
  ``RunBudget`` 报（这一页给的是名单与 id，余量在那个报得出来的地方报）。
  **这一页上没有 ``refund`` / ``spend``**：§8 要求退款必须由一次
  写明理由的显式调用产生，把它做成本工具的一个参数，等于把「对账」与「顺手退掉」
  合成一步 —— 而后者正是那条纪律要挡的。
* 它**不**为了报一个数去扫全表：``open_reservations`` 本来就是按创建时间排序的一次
  索引读，``counts`` 数的是**这一次返回的那一批**。
* **读一次不建库。** 与该工具同一族的还有一处必须说的边界：``SqliteBudgetLedger``
  的构造（``__init__`` 里那句 ``ensure_schema``）**会建库**，而组合期不建运行目录。
  所以这一条在 ``database_path`` 不存在时直接回 ``no_store``，不去构造账本 ——
  理由与 ``fetch_attempt.py`` 那一段逐字相同，措辞也复用同一组常量。

## 谁看得见

``_CONTROL``。这本账**跨主体**：``scope`` 就是业务主体的名字（``<profile>:<run>``），
研究身份读得到它，等于让一次研究看见**别的账号**的额度还剩多少、花了多少。
研究侧本来就在自己那一跳的 ``search_web`` 回执上拿到了 ``budget`` 那一格 —— 它不需要账。

## 这一页**答不出**什么

* **一笔开销该不该被退。** 那要读那次调用到底做成没做成（
  ``FetchResult`` / 检索回执 / ``get_job``），账上只有「押了什么」。
  这一页给的是**名单与 id**，处置由看报告的人做出。
* **供应商账单上那一笔对应哪一个 id。** 账本记的是我们的预留；把它与供应商的
  标单对上，需要那张标单上的引用 —— 那是另一件事，不在这里。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...config import Settings
from ...domain.budget import Reservation
from ...domain.enums import BudgetLine
from ...ports.budget import BudgetLedger
from .tools import _SCENE_RECORD_NO_STORE, _SCENE_RECORD_PRESENT, Handler

__all__ = ["budget_handlers"]

_NOTE = (
    "``reservations`` 里每一行是账上那一条预留的逐字段投影，**最早创建的在先**"
    "（``open_reservations`` 的顺序，见 ``ports/budget.py``）。"
    "这一页**没有**当场算的读数（``list_fetch_attempts`` 那四格在这里不存在）——"
    "每一格都是账上的列。\n"
    "**悬着不是出错。** 一条停在 ``reserved`` 的预留说的是「这笔额度押出去了，"
    "而那一次调用的结局还没有人拿证据来定」。§8 逐字要求在超时或 provider 未知时"
    "**不要**默认释放预算，所以本系统没有任何一条路径会自动把它退回来 —— "
    "默认行为（什么都不做）恰好是正确的那一个。\n"
    "对账的两个动作都要 ``reservation_id``：活儿真的做了就用它去**结算**，"
    "没做就带着理由去**退款**。两个动作都不在这一页上 —— 这一页只给名单。"
)

_FILTER_NOTE = (
    "``filters.scope`` 为 ``null`` 是「**不筛**」（全部主体），为 ``\"\"`` 是「筛"
    "**不属于任何主体**的那一批」—— 与 ``list_fetch_attempts`` 逐字同一条。"
    "``scope`` 的形状是 ``<profile>:<run>``（``RunBudget`` 拼的），所以按一个主体"
    "筛时查的是它在**这一次运行**里的额度，而不是它历来的全部。"
)


def budget_handlers(
    *,
    settings: Settings,
    ledger_factory: Callable[[], BudgetLedger] | None,
) -> dict[str, Handler]:
    """本构建的对账读面处理器，按工具名索引。

    Args:
        settings: 配置。这一层用它的 ``profile``（回执里那个工作区）与
            ``database_path``（核「这个部署写过东西没有」）。
        ledger_factory: 账本的**工厂**。``None`` 表示这个服务实例没有落点 ——
            那时**这一条工具不注册**，而不是注册成恒回空列表的实现：一本空的账与
            一本没接线的账在报告上同形，而前者的处置是「继续」、后者是「去接线」
            （与 ``fetch_attempt_handlers`` 逐字相同）。

    工厂是惰性调用、且在这一条工具**被调用之后**才展开：``SqliteBudgetLedger``
    一构造就 ``ensure_schema``，也就是会**建库**。
    """
    if ledger_factory is None:
        return {}

    def list_open_reservations(scope: str | None = None) -> dict[str, Any]:
        """列这个部署还没结算的预算预留 —— **对账的入口**。"""
        filters = {"scope": scope}

        if not settings.database_path.is_file():
            return {
                "workspace": settings.profile,
                "store_state": _SCENE_RECORD_NO_STORE,
                "filters": filters,
                # 两格都是 ``None`` 而不是空列表 / 空计数：一个 ``[]`` 与一个全零的
                # ``counts`` 会被读成「账读过了，一条都没悬着」—— 而这里什么都没读到。
                "reservations": None,
                "counts": None,
                "note": (
                    f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                    "所以这本账读不出任何东西。这**不是**「没有悬着的额度」—— "
                    "前者的处置是去跑一次付费调用，后者说明该结算的都已结算、"
                    "该退的都已退（那要按 ``scope`` 筛一遍才知道）。"
                ),
            }

        rows = ledger_factory().open_reservations(scope)

        return {
            "workspace": settings.profile,
            "store_state": _SCENE_RECORD_PRESENT,
            "filters": filters,
            "reservations": [_row(row) for row in rows],
            "counts": _counts(rows),
            "note": f"{_NOTE}\n{_FILTER_NOTE}",
        }

    return {"list_open_reservations": list_open_reservations}


def _row(reservation: Reservation) -> dict[str, Any]:
    """账上那一条预留的投影：逐字段，**不加任何算出来的格**。

    逐字段写出来而不是 ``dataclasses.asdict``：枚举格要落成它们的**值**
    （``"search"`` / ``"reserved"``），与 ``list_fetch_attempts`` 那一页的口径一致
    —— 两页在报告里并排读，同一件事（一个枚举格）在两页上长相不同，读的人会以为
    它们是两个字段。``asdict`` 交回来的是枚举**对象**，能不能序列化取决于下游
    用什么编码器；那不是这一层该赌的事。
    """
    return {
        "reservation_id": reservation.reservation_id,
        "scope": reservation.scope,
        "line": reservation.line.value,
        "units": reservation.units,
        "state": reservation.state.value,
        "idempotency_key": reservation.idempotency_key,
        "created_at": reservation.created_at,
        "settled_at": reservation.settled_at,
        "note": reservation.note,
    }


def _counts(rows: tuple[Reservation, ...]) -> dict[str, Any]:
    """这一批的读数，按科目分一遍。

    **零是真读数**：这里数的是一批我们真的读过、真的看得见的行，所以 ``0`` 说的是
    「这一批里没有这个科目的悬账」，而不是「没测」。四个科目**全部列出**，哪怕是 0
    —— 只列出出现过的那些值，会让 ``by_line`` 的形状随数据变，读的人分不出
    「这个科目没有悬账」与「这个科目没被数」。

    按科目分是为了回答那个处置问题（§8）：四类科目的量纲不同，「悬着 3 个单位」在
    ``search`` 上与在 ``browser_subrequest`` 上是两件要分别去查的事。

    ``held_units`` 是这一批的单位总数 —— 它**不是**额度的占用额（那是 ``usage``
    的事，``get_job`` 报），因为额度上限是**按 (scope, line) 各设一份**的，
    把不同 scope 的单位加在一起只会得到一个没有上限可比的总数。它在这里的作用是
    「这一批有多重」，逐 scope 的余量要去读 ``usage``。

    **这里没有 ``by_state``。** ``open_reservations`` 的契约是「只回没结算的」，
    所以那一格恒是 ``reserved: N`` 加两个零 —— 一格里放一个常量的代价是读的人会去找
    它的意义。那条契约由 ``tests/test_budget_contract.py`` 对**两个实现**各判一次；
    一个把已结算的行也回出来的实现在那里红，而不是在这一页上多出一行计数。
    """
    return {
        "returned": len(rows),
        "held_units": sum(row.units for row in rows),
        "by_line": {
            line.value: sum(1 for row in rows if row.line is line) for line in BudgetLine
        },
    }
