"""一条结论挂的证据必须**真的存在** —— ``domain/findings.py`` 里那条判据查不到的那一半。

## 它为什么住在这里，而不是领域层

领域层管得了形状（非空、去重、形如一条 id），管不了**存在性**：存在性是作业
存储与证据存储的**联合状态**，而领域层连 ``EvidenceStore`` 都不能 import
（``ports/`` 依赖 ``domain/``，反过来会成环）。所以它住在唯一同时看得见两边的
那一层。

## 为什么整份拒绝，而不是只丢解不开的那几条

一份产出里，一条结论的 statement 与该结论的全部引用是一个整体。丢掉解不开的
引用、留下剩下的，等于**改写**了 worker 说过的话：回执上仍是「受理了」，而盘上
那条结论的依据少了一半。所以查不到就整份拒绝，并把解不开的那些 id 点名列出来
—— 调用方据此能自己分辨是自己的引用写错了，还是这个库不是产生那条引用的那一个。

## 两种「解不开」必须分开说

* **这个部署没有证据库** —— 任何引用都无从查起，处置是去看部署。
* **这个库里没有这几条** —— 处置是去看那份产出从哪来。

合成一句会让第一种被读成产出的质量问题，于是排查方向整个反了。

## 同一件事在别处是**逐条**拒绝的，而那不是不一致

一条责任判定（``application/relations.py``）挂的证据解不开时，被拒的是**那一行**，
不是整次调用 —— 一批判定里的几行是彼此独立的结论，没有「整份产出」那个整体。
两处共用的只有「怎么把解不开的 id 排成一句」（:func:`listed_unknown_refs`），
拒绝的**范围**各自定，因为范围是由被拒对象的形状决定的。

## 「解不开」之上还有一条：**解得开，但不是这一件的**

存在性只是第一条。一条引用解得开、却是**另一个账号**或**另一个 Scene** 的证据，
与一条不存在的引用在报告上是同一件事（都读起来像有依据），而它更坏：那个账号
真的存在、那条证据也真的在盘上，所以任何按存在性做的核对都会放行它。

§8 的并发隔离要的正是这一条：B 的证据不能支撑 A 的结论。所以这一层现在问三件事
——**在不在**、**是不是这个 Scene 的**、**是不是这个账号的** —— 而三件一起问，
因为它们是同一次读的三个结论（``Evidence.workspace`` 与 ``Evidence.account_id``
就在那一行上）。分开问的代价是调用点可以只问第一件，而只问第一件的那次看起来
一样正常。

``workspace`` / ``account_id`` 是**必填关键字**，没有缺省。一个「不传就不查」
的开关，就是让这条判据变成调用点可以选的东西 —— 而选中的那一天没有一处会红。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.errors import EvidenceError
from ..domain.findings import Findings
from ..ports.store import EvidenceStore

__all__ = [
    "MAX_LISTED_REFS",
    "evidence_refs_of",
    "listed_foreign_refs",
    "listed_unknown_refs",
    "require_evidence_refs",
]

#: 一次拒绝里最多点名多少条 id，剩下的用数字带过。回执是给调用方读的，一份点名
#: 上千条的拒绝本身就会变成一次上下文耗尽 —— 与 ``tools.MAX_ARTIFACT_BYTES``
#: 是同一个道理。带过的那个数字必须报出来，否则「列了 10 条」会被读成「只错了
#: 10 条」，而那是两个不同的结论。
MAX_LISTED_REFS = 10


def evidence_refs_of(findings: Findings) -> tuple[str, ...]:
    """一份产出里出现的全部证据引用，排序去重。

    ``Finding`` 内部已经排过序去过重（见 ``domain/findings.py``），但**两份列表
    之间**可能重复：同一条证据支撑两条结论是正常的。所以这里再收一次 ——
    不合并的话，同一条解不开的引用会在理由里出现两遍，而那看起来像两条缺口。

    **责任关系挂在证据上的那些引用也在内。** 它们不属于 ``observations`` /
    ``candidates`` 那条结论序列，但它们同样是「这一份产出声称的依据」。漏掉它们
    的后果不是少查几条：那几条会绕过下面那条**账号归属**判据，于是 B 的证据可以
    支撑 A 的责任关系 —— 而 §8 要的并发隔离正是挡这一件事。
    """
    refs: set[str] = set()
    for finding in findings.observations + findings.candidates:
        refs.update(finding.evidence_refs)
    for relation in findings.relations:
        refs.update(relation.evidence_refs)
    return tuple(sorted(refs))


def listed_unknown_refs(unknown: Sequence[str]) -> str:
    """把解不开的那些 id 排成一句，**超过 ``MAX_LISTED_REFS`` 的部分折成数字**。

    这条渲染有两个调用方（本模块的整份拒绝与
    ``application/relations.py`` 的逐行拒绝），而它只有这一处实现：两处各写一遍
    的代价不是三行，是「截断到几条」这条口径会先漂一处 —— 于是一份回执说
    「共 40 条」、另一份说「共 12 条」，而两份说的是同一次调用。

    调用方负责给出**已经去重**的序列：两处的去重口径不同（那边是「一份产出里出现的
    引用」，这边是「一行里写的引用」），所以去重不在这里做 —— 一个会自己收一遍输入
    的渲染函数会让调用点的 ``unknown`` 与报出来的条数对不上。

    排不排序则由调用方按自己的理由定，两处的答案也**不同**：
    ``require_evidence_refs`` 的输入来自一份产出，那些引用的先后没有意义，所以它排序
    （同一份产出两次得到的理由逐字相同）；``remember_relations`` 的输入是一行里写的
    顺序，那是有意义的，所以它照原样报 —— 交上去的人要能把它跟自己写的那一行对上。
    这里跟着输入走，只截断、不重排。
    """
    listed = ", ".join(unknown[:MAX_LISTED_REFS])
    if len(unknown) > MAX_LISTED_REFS:
        listed += (
            f"（另有 {len(unknown) - MAX_LISTED_REFS} 条未列出，共 {len(unknown)} 条）"
        )
    return listed


def listed_foreign_refs(foreign: Sequence[tuple[str, str, str]]) -> str:
    """把「解得开、但不是这一件的」排成一句：``id（属于 X，来自 Y）``。

    与 :func:`listed_unknown_refs` 同一条纪律（截断成数字、点名而不概述），但
    **多一列**：这里要说的不是「这条不存在」，而是「这条存在、它属于别人」。
    只报 id 的话，读的人会去查它存不存在，而那一步永远查得到 —— 排查方向整个
    反了。
    """
    listed = ", ".join(
        f"{ref}（属于 {owner}，来自 {place}）" for ref, owner, place in foreign[:MAX_LISTED_REFS]
    )
    if len(foreign) > MAX_LISTED_REFS:
        listed += f"（另有 {len(foreign) - MAX_LISTED_REFS} 条未列出，共 {len(foreign)} 条）"
    return listed


def require_evidence_refs(
    store: EvidenceStore | None,
    findings: Findings,
    *,
    workspace: str,
    account_id: str,
) -> None:
    """每一条引用都要在这个库里解得到，**而且必须是这个 Scene 下这个账号的**。

    Args:
        store: 这个部署的证据库。``None`` 表示**没有库**（不是一个「空库」）——
            两者的区别见模块 docstring。
        findings: 待交付的产出。缺口（``gaps``）按定义没有证据可挂，所以只有
            缺口的产出连库都不需要。
        workspace: 这件工作属于哪个 Scene（``Settings.profile``）。
        account_id: 这件工作的主体。**两格都由调用方从工作单元读，不从产出里读**
            —— 产出是待核对的那些东西，拿它自己声称的主语去核对它自己，等于没查。

    Raises:
        EvidenceError: 有一条或多条引用解不开，或解得开但不属于这个 Scene /
            这个账号。**整份拒绝**，理由里点名那些 id。

    ## 两条判据的顺序

    先问「在不在」，再问「是不是这一件的」。反过来的话，一条不存在的引用会被
    报成「它属于别人」—— 而那句话是编的（它不属于任何人），读的人会去找一个
    不存在的库。

    ## ``workspace`` 这一格为什么不是存储绑定的一次重复

    ``EvidenceStore`` 的契约（``ports/store.py``）里已经有一条同形的判据：一个存储
    只装一个工作区，读别人的行报错。所以 **jsonl 与内存**两个实现对同一件事各有一次
    回答，看上去这里再问一遍是多余的。它不多余，因为三个实现**怎么知道「自己的工作区」
    并不一样**（那段写在 ``ports/store.py`` 里）：

    * ``adapters/jsonl`` 从 ``Settings`` 读；
    * ``adapters/sqlite`` **从库里读**（``db.py::workspace_binding``，由第一行写入建立）。

    于是 sqlite 那一侧有一条只有这里看得见的缺口：库里绑的是 A，而**这个部署的配置**
    （``Settings.profile``）说的是 B —— 那么 ``get_evidence`` 拿到 A 的行、与自己的
    绑定一比，**判「是的，这是本库的」**，一行也不错地放行。三行产出都带
    ``workspace``，而库不知道配置说了什么，配置也不知道库绑了什么；两个都握在手里的
    只有这个函数。配置与数据根配错正是这条路的到达方式（同一段理由在
    ``adapters/memory.py`` 写场景记录的那一处出现过），而它在报告上的表现是「B 的研究
    引用了 A 的证据」—— 与一条真依据同形。

    所以这一格与存储绑定**判的不是同一对东西**：存储判「这一行是不是本库的」，这里判
    「本库是不是这件工作的」。
    """
    refs = evidence_refs_of(findings)
    if not refs:
        # 只有缺口的产出：一次查库都不做。这不是一条捷径 —— 缺口按定义没有依据，
        # 让它走一趟存储会把「没有库」变成一个与它无关的拒绝理由。
        return

    if store is None:
        raise EvidenceError(
            f"这份产出挂了 {len(refs)} 条证据引用，而这个部署没有证据库 —— "
            "所以一条都无从查起。这与「这几条引用是假的」不是一件事："
            "没有库要去看部署，有库而没有那几条要去看这份产出是从哪来的。"
        )

    resolved = {ref: store.get_evidence(ref) for ref in refs}
    unknown = [ref for ref, row in resolved.items() if row is None]
    if unknown:
        raise EvidenceError(
            f"这些证据引用在这个库里解不开：{listed_unknown_refs(unknown)}。\n"
            "一条结论挂的依据必须是**已经落盘**的材料 —— 引用一条不存在的证据，"
            "与一条没有依据的结论是同一件事，只是它看起来像有依据。"
            "整份产出被拒绝：只丢掉解不开的那几条，等于改写了这份结论的依据。"
        )

    # ``row`` 在这一步一定不是 ``None``：上面那一步已经把 ``None`` 全拒绝了，
    # 而证据行只增不改。写成 ``row is not None and …`` 会让「它属于谁」这一条在
    # 一次将来的重构里**静默失效**（条件为假时既不放行也不报错？不 —— 它会放行），
    # 所以这里按已经验过的事实写，让类型检查与判据说的是同一句话。
    foreign = [
        (ref, row.account_id, row.workspace)
        for ref, row in resolved.items()
        if row is not None and (row.workspace != workspace or row.account_id != account_id)
    ]
    if not foreign:
        return

    raise EvidenceError(
        f"这些证据引用解得开，但**不是这一件工作的**：{listed_foreign_refs(foreign)}。\n"
        f"这一件工作属于 Scene {workspace!r} 的账号 {account_id!r}。\n"
        "一条引用解得开、却是另一个账号或另一个 Scene 的证据，与一条不存在的引用"
        "在报告上是同一件事（都读起来像有依据），而它更坏：那个账号真的存在，"
        "那条证据也真的在盘上，所以任何只按存在性做的核对都会放行它。\n"
        "整份产出被拒绝 —— 只丢掉那几条，等于**改写**了这份结论的依据里属于谁"
        "这一段。"
    )
