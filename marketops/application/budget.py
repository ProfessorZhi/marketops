"""一次研究的预算：上限从**冻结的 profile** 来，额度扣在**起飞之前**。

## 为什么上限不来自环境变量

``Settings`` 读 ``os.environ``。§8 逐字：「the caller cannot silently expand them」
—— 而「调用方」正是设环境变量的那个人。一个 ``MARKETOPS_MAX_QUERIES=1000`` 就能把
上限扩大，而且不留痕：环境变量不落盘、不进报告、不在 diff 里。

所以上限来自 ``profiles/<id>/profile.yaml`` 的 ``search.max_queries``：它是版本化
的、可评审的，改了会在 diff 里出现。``set_ceiling`` 同时把这个来源写进账本 ——
于是「这个上限是谁定的」可以从账本本身回答，而不是靠某次会话的记忆。

**profile 没写上限时这里失败关闭**，不取缺省值。一个「没写就按 8 次跑」的缺省，
会让「忘了声明额度」与「故意给 8 次」在行为上不可区分 —— 与
``BudgetNotConfiguredError`` 拒绝把未配置读成无限是同一条纪律。

## 两种 scope：**账号**与**一次发现运行**（这一段在 E2 之后改过）

研究那一侧：§14 逐字「一个 Account = 一个研究任务」，所以 scope 是
``<profile>:<account_id>``，额度按账号各记各的。代价要写清楚：**一次运行的总
额度 = 每账号上限 × 账号数**，本层不假装知道那件事。收益是一个真实的失败模式被
移除了 —— 若 scope 是 run 级，一个跑疯了的账号会把整批账号的额度吃光，而报告上
只会显示「其余账号没有产出」。

发现那一侧（``application/discovery_run.py``）：scope 是
``<profile>:<发现范围名>``。这不是上面那段的反例，是它的**同一个规则**换了主语：
一个账号 = 一个研究任务，一个**范围** = 一次发现运行。两类 scope 之间互不影响
（账本本来就是这么设计的），所以一次发现花掉的钱不会挤掉任何一个账号的研究额度
—— 而两臂（``discovery-v1-arm-b`` / ``-arm-c``）各记各的，是「两臂预算独立且都由
工具强制」这句话唯一能成立的方式。

判据只有一个：**上限永远从冻结的 profile 来**（:func:`declared_search_ceiling`
与 :func:`declared_document_ceiling`），scope 的名字从不携带一个数字。
``for_scope`` 与 ``for_account`` 之间差的就是那个名字 —— 上限的播种只有一份实现
（``RunBudget._seeded``）。

## 第三类 scope：**一个主体一次复核**（B22 加上，科目是抓取）

维护那一侧（``application/refresh.py``）：scope 是
``<profile>:<主体 id>@<这一轮的时刻>``，科目是 ``BudgetLine.DOCUMENT``。

它**不**沿用「一个主体一个终身上限」，而这不是为了好看：账本没有充值机制，一个
终身上限意味着一个长期经营的部署在抓满那个数之后**再也复核不了**，而报告上
显示的是「没有到期的格子」。把这一轮的时刻放进 scope，上限说的就是它本来要说的
那句话 —— 「一次复核最多看几页」。理由与代价写在 :meth:`RunBudget.for_documents`
上。

## 异常路径上**什么都不做**

三个动作里只有两个会被调用：

* :meth:`RunBudget.reserve` —— 起飞之前；
* :meth:`RunBudget.spend` —— 拿到结果之后;
* 超时、连接中断、provider 报错 —— **没有第三个调用**。§8：超时或 provider-unknown
  时不得默认释放预算，因为那时你并不知道对方是否已经计费。

所以本模块里没有 ``try`` / ``except`` / ``finally``：默认行为（什么都不做）恰好
就是正确行为。要退款，得显式调用 ``ledger.refund(..., reason=...)`` 并写明理由。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.budget import BudgetUsage, Reservation
from ..domain.enums import BudgetLine
from ..domain.errors import BudgetNotConfiguredError
from ..domain.profile import ProfileBundle
from ..ports.budget import BudgetLedger


@dataclass(frozen=True)
class RunBudget:
    """一次研究的额度。

    ``scope`` 是 ``<profile>:<主语>`` —— 研究侧的主语是账号，发现侧是一次发现
    运行（见模块 docstring 的那一节）；``line`` 是科目（MVP 只扣检索，
    抓取与推理的科目等它们的消费者落地，见 ``BudgetLine``）。
    """

    ledger: BudgetLedger
    scope: str
    line: BudgetLine = BudgetLine.SEARCH

    @classmethod
    def for_scope(cls, ledger: BudgetLedger, bundle: ProfileBundle, label: str) -> RunBudget:
        """按 profile 声明的上限**播种**某一个 scope，并返回绑定好的预算。

        ``label`` 是 ``<profile>:`` 后面那一段：研究侧是账号 id，发现侧是一次
        发现运行的名字。**上限不由它决定** —— 无论哪一侧，数字都来自冻结的
        profile（§8：调用方不得静默扩大上限），而 label 只决定**记在谁名下**。

        Raises:
            BudgetNotConfiguredError: profile 没有声明 ``search.max_queries``，
                或声明为 0。两者都是「上限不可用」，与「没额度了」不是一回事。
        """
        return cls._seeded(
            ledger,
            bundle,
            label,
            line=BudgetLine.SEARCH,
            ceiling=declared_search_ceiling(bundle),
            declared_at="search.max_queries",
        )

    @classmethod
    def for_documents(
        cls, ledger: BudgetLedger, bundle: ProfileBundle, label: str
    ) -> RunBudget:
        """一个主体**一次复核**的抓取额度（``BudgetLine.DOCUMENT``）。

        第三个主语，与 :meth:`for_scope` / :meth:`for_account` 是同一个规则换了
        主语（见 ``domain/profile.py::DeclaredProfile.fetch_max_documents``）：
        一个账号 = 一次研究任务，一个主体 = 一次复核。调用点写得出
        ``for_documents(...)``，「这里的主语是一个主体、科目是抓取」这句话才在
        代码里看得见 —— 两处都写 ``for_scope`` 会让科目这一维在调用点消失，而
        ``for_scope`` 缺省的科目是 ``SEARCH``：一次写错的后果不是报错，是**抓取
        扣在检索的账上**，两边的数字同时错而都看不出来。

        **它管的是「一次复核」，不是「一辈子」**：scope 里带着这一轮的时刻
        （``application/refresh.py`` 拼的 label），所以一个主体下一轮复核拿到的是
        一份新的额度。账本今天**没有充值机制**（一次 scope 的上限只被播种、只被
        扣减），于是「一辈子一个上限」意味着一个长期经营的部署在抓满那个数之后
        就再也不复核了 —— 而那会在报告上表现为「没有到期的格子」，不是「额度用尽」。

        Raises:
            BudgetNotConfiguredError: profile 没有声明 ``fetch.max_documents``，
                或声明为 0。
        """
        return cls._seeded(
            ledger,
            bundle,
            label,
            line=BudgetLine.DOCUMENT,
            ceiling=declared_document_ceiling(bundle),
            declared_at="fetch.max_documents",
        )

    @classmethod
    def _seeded(
        cls,
        ledger: BudgetLedger,
        bundle: ProfileBundle,
        label: str,
        *,
        line: BudgetLine,
        ceiling: int,
        declared_at: str,
    ) -> RunBudget:
        """播种一个 ``(scope, 科目)`` 的上限，并把账本绑上去。

        **播种只有这一份实现**（模块 docstring 那句「上限永远从冻结的 profile 来」
        的执行器）：三个 ``for_*`` 之间只差主语、科目与那一条声明的位置，而三处
        各抄一遍的后果是某一个科目悄悄回到「没写就按 N 跑」的缺省上。
        """
        scope = f"{bundle.profile_id}:{label}"
        ledger.set_ceiling(
            scope, line, ceiling, source=f"profile:{bundle.profile_id}:{declared_at}"
        )
        return cls(ledger=ledger, scope=scope, line=line)

    @classmethod
    def for_account(
        cls, ledger: BudgetLedger, bundle: ProfileBundle, account_id: str
    ) -> RunBudget:
        """一个账号在这次研究里的额度 —— :meth:`for_scope` 换了个主语。

        单独留一个名字不是为了少敲几个字：调用点写 ``for_account(..., account)``
        时，「这里的主语是一个账号」这句话在代码里**是看得见的**；两处都写
        ``for_scope`` 会让研究侧那一条少一处提示。实现是同一份。
        """
        return cls.for_scope(ledger, bundle, account_id)

    def reserve(self, *, idempotency_key: str) -> Reservation:
        """起飞前扣住一次额度。

        重发同一个 ``idempotency_key`` 不会重复扣 —— 幂等由 id 本身保证
        （见 ``domain/budget.py``），不依赖调用方记得先查。
        """
        return self.ledger.reserve(self.scope, self.line, 1, idempotency_key=idempotency_key)

    def spend(self, reservation: Reservation) -> Reservation:
        """拿到结果之后落成实际花费。"""
        return self.ledger.spend(reservation.reservation_id)

    def usage(self) -> tuple[BudgetUsage, ...]:
        return self.ledger.usage(self.scope)

    def remaining(self) -> int:
        return self.ledger.remaining(self.scope, self.line)


def declared_search_ceiling(bundle: ProfileBundle) -> int:
    """从冻结的 profile 读**一个账号**的检索上限。缺失或为零都失败关闭。

    公开它是为了让「这个部署能不能出资跑一个作业」这条判据只有一个实现：
    ``submit_research`` 在受理时问的是同一个问题（一个没有额度声明的部署受理
    下来的作业，永远不可能有账号额度可用，于是它是一条排了队但排向虚无的工作），
    而两处各写一份阈值的后果是「工具说可以受理、预算说没配置」。
    """
    declared = bundle.profile.search_max_queries if bundle.profile else None
    if declared is None:
        raise BudgetNotConfiguredError(
            f"profile {bundle.profile_id!r} 没有声明 search.max_queries，"
            "因此这次运行没有可用的检索上限。\n"
            "本模块**不取缺省值**：一个「没写就按 N 次跑」的缺省会让『忘了声明额度』"
            "与『故意给 N 次』在行为上不可区分。请在 profile.yaml 的 search 块里"
            "写下这个数字 —— 它是版本化、可评审的，而环境变量不是。"
        )
    if declared == 0:
        raise BudgetNotConfiguredError(
            f"profile {bundle.profile_id!r} 声明 search.max_queries: 0，"
            "即一个账号一次检索都不允许。\n"
            "这与「没声明」不是同一件事，但两者都不能表现为「跑完了、没有候选」——"
            "那会把一次不可能的运行读成一次一无所获的运行。"
        )
    return declared


def declared_document_ceiling(bundle: ProfileBundle) -> int:
    """从冻结的 profile 读**一个主体一次复核**的抓取上限。缺失或为零都失败关闭。

    与 :func:`declared_search_ceiling` 逐字同一条判据、同一个理由：一个「没写就
    按 N 份跑」的缺省会让「忘了声明额度」与「故意给 N 份」在行为上不可区分。两个
    函数分开而不是带一个 ``line`` 参数，是因为**两条上限各有各的执行器**：把科目
    做成参数之后，``declared_ceiling(bundle, BudgetLine.SEARCH)`` 与
    ``declared_ceiling(bundle, BudgetLine.DOCUMENT)`` 在调用点长得一样，而
    ``BudgetLine`` 是枚举 —— 传错的那一次不会报错，只会读另一条上限。
    """
    declared = bundle.profile.fetch_max_documents if bundle.profile else None
    if declared is None:
        raise BudgetNotConfiguredError(
            f"profile {bundle.profile_id!r} 没有声明 fetch.max_documents，"
            "因此这次复核没有可用的抓取上限。\n"
            "本模块**不取缺省值**：一个「没写就按 N 份跑」的缺省会让『忘了声明额度』"
            "与『故意给 N 份』在行为上不可区分。请在 profile.yaml 的 fetch 块里"
            "写下这个数字 —— 它是版本化、可评审的，而环境变量不是。"
        )
    if declared == 0:
        raise BudgetNotConfiguredError(
            f"profile {bundle.profile_id!r} 声明 fetch.max_documents: 0，"
            "即一个主体一次复核一份文档都不允许抓。\n"
            "这与「没声明」不是同一件事，但两者都不能表现为「复核完了、没有变化」——"
            "那会把一次不可能完成的复核读成一次确认没变。"
        )
    return declared


__all__ = ["RunBudget", "declared_document_ceiling", "declared_search_ceiling"]
