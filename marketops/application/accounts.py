"""账号池：**一次新的账号研究怎么折进已有的那一行**（合并规则），以及（§6 第三步）**一批
账号怎么落进池子里**（生产者）。

## 两份工作为什么住在同一个模块里

与 ``application/pool.py`` / ``application/relations.py`` 逐字同一条：它们说的是同一件
东西的两半 ——「这一行的哪些格子由生产者填」与「同一个键上的两行怎么折」。分开写会让
那两张清单各改各的，而它们**必须一起改**：一个只由生产者填、合并规则却不认识的字段，
第二次写入时传不进去（合并结果取的是已有的那一行），于是那一格会永远停在这一行第一次
落进池子时的样子，而报告上那只是「它一直没变」。

## 这份合并规则问的是另一件事，不是「哪些格子不许被抹掉」

资产池那条规则（``application/pool.py``）的一半篇幅在讲**上游说的那句话怎么处理** ——
这次来源没提这件事、上游以清空列的方式改口、两个官网判定撞上了。账号池上**一格这样的
东西都没有**：这里每一格都是我们自己研究出来的事实或时刻，没有一行是来源的转述，也
没有任何一格里装着一次**判定**。

所以没有冲突这一支，而这不是漏掉的（第三次了，三次的理由各不相同）：

* 资产池有冲突，是因为 ``domain_verdict`` 是**一个槽位、两个判定**；
* 关系表没有，是因为那个「槽位」不在同一行里（``relation`` 是键的一段，§5 明令不强制
  单选）；
* 账号池没有，是因为**这一行上根本没有判定那一格** —— ``status`` 是一格，但它由
  §6 第三步的生产者填，而它是一条**记录**（「这个 Scene 当时怎么判的」），不是一个
  两行之间会冲突的结论（见下）。

## 五条规则

1. ``first_seen_at`` 只往**早**走（``min``）；``last_verified_at`` 只往**晚**走
   （``None`` 不参与）。两条都调 ``application/merge.py`` 的同一处定义 —— 理由与
   另外两张表逐字相同（重放一次旧研究不该把「我们何时第一次记下它」推后；读一遍本地
   记录不是一次观察）。
2. 三个**清单格**（``aliases`` / ``domains`` / ``addresses``）与 ``evidence_ids`` 取
   **并集**（保序、去重）。它们是「关于这个世界的事实」的清单：第二次独立看到同一件事
   是**加强**；而一次后来的研究**没提到**某个域名，不是「它没有这个域名了」—— 本层
   表达不出「一条曾经的域名不再成立」（要有那件事自己的凭据），所以并集，只增不减。
3. ``country`` 取来行的值，**除非它是 ``None``**。``None`` = 还没查，``""`` = 查了、
   是没有 —— 这一格的三态与 ``AccountRecord.country`` 逐字相同，而按真值判断会把
   ``""`` 那条**证据**当成「什么都没说」丢掉。
4. ``status`` 取来行的值，**除非它是 ``discovered``**。这一格与 ``country`` 是同一个
   形状的第三条：``discovered`` 是**「还没有判定」**（模型 docstring 的原话），不是
   三种判定里的一种，所以它不许覆盖一个真判过的值 —— 一次重新研究把
   ``eligible`` 打回 ``discovered``，在报告上读起来只是「这一行更新了」。
5. ``organisation`` 那一格**第一次记下的为准**，来行里不同的那个名字进 ``aliases``。
   这是这份规则里唯一一处不按「后看到的为准」走的格子，理由见下一节。

## 为什么 ``organisation`` 是唯一一个「先看到的为准」

这一行上别的格子都是**关于主语的事实**，而 ``organisation`` 是**主语本身**：报告用它
称呼这个组织，草稿用它起头。事实可以被后一次观察改好（后看到的为准）；**名字不行**，
因为本层没有「两种写法里哪个更好」这条规则 —— ``Example Parish Council`` 与
``Example PC`` 都是它真的用过的名字，挑哪一个是一次**编辑决定**，不是一次观察的产出。

那么两次研究给出两个名字时，只有两种处置：按后来那个改，或者不改。按后来那个改，
意味着**一份报告的抬头会在没有任何证据变化的情况下换个名字** —— 那一次重新研究带上
来的证据全都一样，只有写法不同，而读报告的人没有任何一栏看得出这件事发生过。

代价要写下来：**第一次记下的名字如果是粗的那个，它就一直当着门面**（换名的路径是
一次显式编辑，而本层今天没有那条工具 —— 与 ``application/relations.py`` 末尾那条边界
落在同一个待办上）。选这一边的理由是两边的代价不对称：留着粗名字，代价是报告读起来
不够正式；让它随每次重研究漂，代价是**同一份报告的抬头可以变**，而后者在下游是
「这份报告前后不是一份」的证据。

名字一个都不会丢：``aliases`` 那一格是**见过名字的全集减去当前这一个**，所以折完之后
``{organisation} ∪ set(aliases)`` 只增不减，而当前那个名字不会同时出现在别名里
（一次构造得像 ``organisation="X", aliases=["X"]`` 的来行会被这一步就地修掉）。

## 折进来的键对不上时**报错**，不是照折

与 ``merge_assets`` / ``merge_relations`` 逐字同一条：折进来的对象与库里那一行必须说的是
同一个账号、同一个工作区，否则是调用方拿错了一个对象。少了这一条，一次写错的 id 会把
**另一个账号**的行整个改写掉，而报告上它看起来只是那个账号的信息变了。

## 它不判断的事（写下来，不是忘掉）

* **不判断哪个账号更值得联系。** ``status`` 那一格由 :func:`remember_accounts` 记下
  调用方交来的判定，本函数只负责不让「没有判定」把它抹掉。
* **不看域名。** 域名只是一个清单格，合并不按它认人（§6 明令不得因为同域名合并两个
  实体）——``ports/pool.py::AccountPoolStore`` 里连一个按域名找账号的入口都没有。
* **不撤销。** 一行账号被推翻（复核时发现这个组织不存在 / 认错了）在本层表达不出来。
  与另外两张表落在同一条边界上：本层分不开「还没被重新证实的行」与「已经被推翻的行」，
  而两者的下一步不同。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import ValidationError

from ..domain.account import MarketAccount
from ..domain.base import StrictModel
from ..domain.enums import AccountStatus
from ..domain.errors import StoreError
from ..ports.pool import AccountPoolStore
from ..ports.store import EvidenceStore
from .evidence_refs import listed_unknown_refs
from .merge import later_of, ordered_union
from .validation import as_text, one_line_reason

__all__ = [
    "AccountClaim",
    "AccountDisposition",
    "AccountOutcome",
    "merge_accounts",
    "remember_accounts",
]


def merge_accounts(existing: MarketAccount | None, incoming: MarketAccount) -> MarketAccount:
    """把 ``incoming`` 折进 ``existing``；``existing is None`` 就是它本身。

    返回值**重新过了一遍模型校验**（``model_validate`` 而不是 ``model_copy``）：模型上的
    ``_an_account_without_evidence_is_not_written`` 与 ``_timestamps_are_aware`` 都
    是各自判据的**唯一**执行器 —— 合并出来的行绕开校验器，正是「一行没有证据的账号」
    或者「一个 naive 的时刻」被写进池子的路径，而它在下游读起来是「我们查到了」。

    纯函数：不读盘、不写盘、不碰时钟。所以这一整条规则可以在没有库、没有配置的情况下
    被逐条测出来（``tests/test_application_accounts.py``）。
    """
    if existing is None:
        return incoming
    if (existing.account_id, existing.workspace) != (
        incoming.account_id,
        incoming.workspace,
    ):
        raise StoreError(
            f"要折进来的是一行 account_id={incoming.account_id!r} "
            f"workspace={incoming.workspace!r}，而池子里那一行是 "
            f"account_id={existing.account_id!r} workspace={existing.workspace!r}。\n"
            "两者说的是不同的东西，所以这不是一次合并 —— 调用方拿错了对象。\n"
            "照折的话，一次写错的 id 会把另一个账号的行整个改写掉，而报告上它看起来"
            "只是那个账号的信息变了。"
        )

    # 第 5 条：主语第一次记下的为准（理由见模块 docstring）。
    organisation = existing.organisation
    # 见过的名字**全集**（保序：第一次看到的在前），再减去当前这一个 —— 别名那一格
    # 的定义是「**其余**写法」，而当前这个名字不在其余里。用全集减当前、而不是只把
    # 来行那个名字接上去，是因为减的那一步顺手修掉了「来行自己把当前名字列成别名」
    # 这种构造（它折一次就自己消失了，不需要一条拒绝判据）。
    names_recorded = ordered_union(
        [existing.organisation, *existing.aliases],
        [incoming.organisation, *incoming.aliases],
    )

    changes: dict[str, object] = {
        "organisation": organisation,
        "aliases": [name for name in names_recorded if name != organisation],
        # 第 2 条：三个清单格与证据一起取并集、保序、去重。
        "domains": ordered_union(existing.domains, incoming.domains),
        "addresses": ordered_union(existing.addresses, incoming.addresses),
        "evidence_ids": ordered_union(existing.evidence_ids, incoming.evidence_ids),
        # 第 1 条：时刻各有各的方向。
        "first_seen_at": min(existing.first_seen_at, incoming.first_seen_at),
        "last_verified_at": later_of(existing.last_verified_at, incoming.last_verified_at),
    }

    # 第 3 条：``None`` = 还没查，所以它不参与；``""`` 是**证据**（查了，是没有），参与。
    if incoming.country is not None:
        changes["country"] = incoming.country

    # 第 4 条：``discovered`` = 还没有判定，所以它不覆盖一个真判过的值。
    if incoming.status is not AccountStatus.DISCOVERED:
        changes["status"] = incoming.status

    return MarketAccount.model_validate({**existing.model_dump(), **changes})


# ----------------------------------------------------------------------
# 生产者：一批账号怎么落进池子里
# ----------------------------------------------------------------------
class AccountDisposition(str, Enum):
    """一次 :func:`remember_accounts` 里，**每一行**落到哪里去了。

    四种，闭集。与 ``application/relations.py::RelationDisposition`` 逐字同一条
    「不复用旁边那个枚举」的理由：两个枚举各有一个成员恰好也叫「归并」而含义不同，
    共用一套取值会让任何一边加成员时另一边跟着改，而它们没有共同的下游。

    ``WRITTEN`` 与 ``FOLDED`` 分开，也与那边同一条：一批里两行说了同一个账号时，
    池子里只有一行，而「这次调用写了 4 行」与「这个市场有 4 个账号」是两句不同的话。
    """

    WRITTEN = "written"
    #: 这一行与**同一批里**前面的某一行说的是同一个 ``account_id``，两行折成了一行。
    FOLDED = "folded"
    #: 这一行有证据引用，而那些引用在这个库里解不开 —— 一行都没写。
    EVIDENCE_UNKNOWN = "evidence_unknown"
    #: 这一行本身构造不出一个能写下来的账号（id 不规范、名字空着、域名不是一个裸
    #: 主机、判定不在那七个取值里、一条证据都没有……）—— 一行都没写。
    REJECTED = "rejected"


class AccountClaim(StrictModel):
    """调用方交进来的一条账号。**它还不是一行账号** —— 这是它存在的全部理由。

    八个字段全是 ``str`` / 清单 / ``None``：``status`` 是**原文**而不是
    :class:`~marketops.domain.enums.AccountStatus`，理由与 ``RelationClaim.relation``
    逐字相同 —— 取值不在那七个里是**一行**的去向（``REJECTED``，理由里点名可用取值），
    而在更早的地方把它当类型收下，意味着一行写错了枚举会把整次调用炸掉，而同一批里
    其余的账号与它无关。

    ## 八个字段都有缺省，而 ``status`` 的缺省与 ``RelationClaim.relation`` 的相反

    ``RelationClaim.relation`` 的缺省是空串，而空串**不是一个取值**：一条关系必须说清
    是 ``owns`` 还是 ``unresolved``，因为那边连「还没定下来」也是一次判定
    （``VenueRelation.UNRESOLVED`` 的类文档：「知道它们有关系，但管的是维护还是预约
    控制没读出来」—— 那是一个要凭据的结论）。这里反过来：``discovered`` 就是
    **「还没有判定」**（``domain/account.py`` 的原话），不是七个判定里的一种。所以缺省
    取它，一次只说了「我又看到了这个账号」的调用不必编一个判定出来；而 ``""`` 仍然
    走 ``REJECTED`` —— 它是**给错了**，不是没给。

    这一格的方向也由 :func:`merge_accounts` 的第 4 条兜住：``discovered`` 不许覆盖一个
    真判过的值，所以「这次没判」在第二次写入时不会把上一次的判定抹掉。

    ``evidence_ids`` 是**指针**（``Evidence.evidence_id`` 同空间）。这里的 ``tuple``
    只保证形状：那条「至少要一条证据」的判据住在
    :meth:`~marketops.domain.account.MarketAccount._an_account_without_evidence_is_not_written`，
    这里不再写一遍 —— 两处各写一遍，先漂的那一处正是唯一被读取的那一处。
    """

    account_id: str = ""
    organisation: str = ""
    aliases: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()
    #: ``None`` = 还没查、``""`` = 查了、是没有（与 ``MarketAccount.country`` 同一个方言）。
    country: str | None = None
    #: 这一批的**原文**，取值不在 :class:`AccountStatus` 里就是这一行自己的去向。
    status: str = AccountStatus.DISCOVERED.value
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountOutcome:
    """一条账号交上去之后的去向。

    ``row`` 是**池子里现在那一行**（``upsert_account`` 的返回值），不是交上去的那一行：
    第二次折同一个账号之后，别名、域名、地址、证据都是并集，``organisation`` 是**第一次**
    记下的那个 —— 报出交上去的那一份，读的人会以为池子里就是那样。``None`` 只有一个意思：
    这一行没写进去（``EVIDENCE_UNKNOWN`` / ``REJECTED``）。

    ``account_id`` / ``organisation`` 在没写进去时是**调用方交来的原文**，只用于对账
    （「我交的第 3 行为什么没进去」）。写进去的那两格以 ``row`` 为准。两格而不是一格：
    一条被拒的行往往是 id 或名字本身有问题，只留 id 的话，交上来二十行的人认不出那是
    哪一行。
    """

    ordinal: int
    account_id: str
    organisation: str
    disposition: AccountDisposition
    row: MarketAccount | None = None
    #: 没写进去时的理由；也用来记「折进了第几行」。``WRITTEN`` 时是空串。
    reason: str = ""


def remember_accounts(
    rows: Sequence[Mapping[str, Any]],
    *,
    store: AccountPoolStore,
    evidence: EvidenceStore,
    workspace: str,
    seen_at: datetime,
) -> tuple[AccountOutcome, ...]:
    """一批账号 → 池子里那几行，**逐行给出去向**。

    ## 一次调用说的是**一个 Scene** 的一批账号

    与 ``remember_relations`` 的 ``venue_asset_id`` 相比，这里少一个「这一批是关于谁
    的」参数，而这不是漏掉的：责任判定是**逐块场地**做的（一次研究看的是这块场地由谁
    负责），账号不是 —— 一个账号不挂在某一块场地上，所以没有哪一个 id 配当这个参数。
    留下的那一个作用域参数是 ``workspace``，它的方向与另外三处逐字相同：**只从参数来，
    不从行里读**。一个能填 ``workspace`` 的入参等于让一次调用把行写进另一个 Scene，
    而两张表在同一个库文件里；§6 的「客观事实可以跨 Scene 复用」说的是第二个 Scene
    写**它自己的**行，不是一次调用替另一个 Scene 写。

    ## 逐行判，一行失败不牵连其余

    §6 与 ``application/outreach.py::_as_draft`` 是同一条：一行坏账号不该让整批都不做。
    所以这一层的**每一种失败都是一条去向**，不是异常 —— 除了
    :class:`~marketops.domain.errors.StoreError`：那个说的是存储本身（这一行属于别的
    工作区、键绑不上），它一次就不是「这一行的问题」，让它穿出去由调用点处置。

    ## 去重、折叠，都走**同一条**合并规则

    一行里的清单先按出现顺序去重（同一个域名写两遍不是两次独立看到），随后
    ``upsert_account`` 自己折 —— 同一批里两行说了同一个 ``account_id`` 时，第二次拿到的
    ``existing`` 就是第一次写进去的那一行，四个清单格按 :func:`merge_accounts` 的第 2 条
    取并集。这里**不**预先合并、也**不**先查一次池子：另一套合并规则就是第二个定义处，
    而两份合并规则迟早对同一对行给出两个结果。

    ``FOLDED`` 因此是一次**报告**，不是一次处置：这一行与同一批里某一个更早的序号说的是
    同一个账号，那个序号写在它的 ``reason`` 里。

    ## 它不填的格子，与不填的理由

    * ``last_verified_at`` 留 ``None``。记下一个账号**不是**重新核实了它（与
      ``remember_relations`` / ``assets_from_receipt`` 同一条纪律）。填上它，「这个账号
      多久没复核了」这个只在池子建起来之后才有意义的问句，会在第一次写入那天就得到
      「刚刚复核过」，而下一轮维护会把它们整批跳过。
    * ``first_seen_at`` 取 ``seen_at``（参数，不是时钟）：它是「我们**何时**第一次记下
      这个账号」，由调用方定义的那一刻给出 —— 一条要能被逐条断言的规则不该自己去问
      现在几点。

    ## 它判断不了的事

    * **不知道这个账号是不是池子里已经有的。** 那是 ``upsert_account`` 的事，也正是
      §6 不许在写入之前拿域名/名字/地址去撞的原因（``ports/pool.py::AccountPoolStore``
      上没有那条路）。先查一次是读—改—写，两个重叠的 worker 下它读到的答案可能已经
      过期了。
    * **不知道它管着哪几块场地。** 那是 §5 的关系表（``relations_for_account``），由读
      的那一侧接起来 —— 这一行上不存第二份（见 ``domain/account.py``）。
    """
    outcomes: list[AccountOutcome] = []
    #: 这一批里**第一次**出现某个 ``account_id`` 的那一行序号。
    first_row_at: dict[str, int] = {}

    for ordinal, raw in enumerate(rows):
        try:
            claim = AccountClaim.model_validate(raw)
        except ValidationError as exc:
            outcomes.append(
                AccountOutcome(
                    ordinal=ordinal,
                    account_id=as_text(raw.get("account_id")),
                    organisation=as_text(raw.get("organisation")),
                    disposition=AccountDisposition.REJECTED,
                    reason=f"这一行不是一个账号：{one_line_reason(exc)}",
                )
            )
            continue

        try:
            status = AccountStatus(claim.status)
        except ValueError:
            allowed = "、".join(member.value for member in AccountStatus)
            outcomes.append(
                AccountOutcome(
                    ordinal=ordinal,
                    account_id=claim.account_id,
                    organisation=claim.organisation,
                    disposition=AccountDisposition.REJECTED,
                    reason=(
                        f"{claim.status!r} 不是一种账号判定。可用取值：{allowed}。\n"
                        "这一行不写；同一批里其余的行不受影响。"
                    ),
                )
            )
            continue

        try:
            row = MarketAccount(
                workspace=workspace,
                account_id=claim.account_id,
                organisation=claim.organisation,
                # 同一个清单格里写两遍不是两次独立看到（``dict.fromkeys`` 保序去重）。
                aliases=list(dict.fromkeys(claim.aliases)),
                domains=list(dict.fromkeys(claim.domains)),
                addresses=list(dict.fromkeys(claim.addresses)),
                country=claim.country,
                status=status,
                evidence_ids=list(dict.fromkeys(claim.evidence_ids)),
                first_seen_at=seen_at,
            )
        except ValidationError as exc:
            outcomes.append(
                AccountOutcome(
                    ordinal=ordinal,
                    account_id=claim.account_id,
                    organisation=claim.organisation,
                    disposition=AccountDisposition.REJECTED,
                    reason=f"这一行不是一个能写下来的账号：{one_line_reason(exc)}",
                )
            )
            continue

        unknown = [ref for ref in row.evidence_ids if evidence.get_evidence(ref) is None]
        if unknown:
            outcomes.append(
                AccountOutcome(
                    ordinal=ordinal,
                    account_id=claim.account_id,
                    organisation=claim.organisation,
                    disposition=AccountDisposition.EVIDENCE_UNKNOWN,
                    reason=(
                        f"这些证据引用在这个库里解不开：{listed_unknown_refs(unknown)}。"
                        "一个账号挂的依据必须是**已经落盘**的材料 —— 引用一条不存在的"
                        "证据，与一个没有依据的账号是同一件事，只是它看起来像有依据。"
                        "这一行不写；同一批里其余的行不受影响。"
                    ),
                )
            )
            continue

        stored = store.upsert_account(row)
        earlier = first_row_at.get(stored.account_id)
        if earlier is None:
            first_row_at[stored.account_id] = ordinal
            outcomes.append(
                AccountOutcome(
                    ordinal=ordinal,
                    account_id=stored.account_id,
                    organisation=stored.organisation,
                    disposition=AccountDisposition.WRITTEN,
                    row=stored,
                )
            )
            continue
        outcomes.append(
            AccountOutcome(
                ordinal=ordinal,
                account_id=stored.account_id,
                organisation=stored.organisation,
                disposition=AccountDisposition.FOLDED,
                row=stored,
                reason=(
                    f"同一批里第 {earlier} 行已经说过这个账号，两行合成了一行"
                    "（清单格与证据取并集）—— 池子里仍然只有一行。"
                ),
            )
        )

    return tuple(outcomes)
