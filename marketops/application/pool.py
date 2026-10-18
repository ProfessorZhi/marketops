"""场地资产池：**母池回执怎么变成池子里的行**（生产者），以及**一次新观察怎么折进
库里已有的那一行**（合并规则）。

## 两份工作为什么住在同一个模块里

它们说的是同一件东西的两半：「这一行的哪些格子由来源给出」与「同一个句柄上的两行
怎么折」。分开写会让那两张清单各改各的 —— 而它们必须一起改：一个只由生产者填、
合并规则却不认识的字段，第二次导入时传不进去（合并结果取的是已有的那一行），
于是那一格会永远停在这一行第一次落进池子时的样子，而报告上那只是「它一直没变」。

## 为什么这条规则不写在存储里

``ports/pool.py`` 的模块 docstring 把边界画在这儿：端口负责「按 id 折一行、读回来
就是它」与「读与写在同一个事务里」，而**「折的时候哪些格子不许被抹掉」是业务规则**。
它不是可以两边各写一半的东西 —— 存储里写一遍、调用点再判一遍，两次的判断迟早不
一致，而不一致的那一次读起来像一次正常的更新。

规则也不写成 ``upsert_asset(existing, incoming)`` 之外的形状（比如让调用方每次都传
一个函数）：那么同一个库上就可能出现两条合并规则，而「这一行是怎么变成现在这样的」
就没有唯一答案了。所以它由组合根一次性地接进 store 的构造参数
（``interfaces/composition.py::default_asset_store``）。

## 一条规则，两句话

**后看到的为准；但「看到空的」不是一次观察。**

展开来是五条，每一条都对应一种**曾经会被静默吃掉**的东西：

1. ``first_seen_at`` 只往**早**走（``min``）。它是「我们何时第一次记下这块场地」，
   而重放一次旧导入不该把它推后 —— 推后之后，池子会声称一块早就认识的场地是刚
   发现的。
2. ``last_verified_at`` 只往**晚**走（``max``，``None`` 不参与）。它是「我们最后
   一次真的重新看过它」，与 ``Observation.observed_at`` 同一条纪律。
3. **非空赢，空不覆盖 —— 而「这一页没说」也不算说话。** 来行里一个空串的意思是
   「这一次的来源没提这件事」，不是「这件事是空的」。把它当覆盖写，会让一条只带了
   名字的搜索摘要把地址、地区、类型全部清掉 —— 而在报告上，那一行只是「信息变少了」。
   ``country`` 用 ``None`` 表示同一件事（``None`` = 这一份工件没有这一栏），所以同一
   句话在那里也成立。
   **分类那三格多一种写法**：``unknown``（读了、这一页没有说出词表认得的说法）。
   它是个非空字符串，在「非空赢」下是 truthy —— 于是它会把上一次真的读出来的值抹掉，
   正是这一条要防的那件事，只是换了个入口。所以那三格有一条自己的规则
   （``_CLASSIFICATION_FIELDS``），认识 ``unknown`` 的**只有那一处**。
   ``claimed_official_domain``（**上游说**的官网）走同一条，但它的代价要写下来：
   上游若以「把那列清空」的方式改口，池子会留着旧的那句话。选这一边的理由是两边
   的代价不对称 —— 留着过时的线索，代价是核验那一步白查一次；丢掉线索，代价是那
   一步**不再发生**，而「这条线索已经没有了」与「这条线索从来没出现过」在下游长得
   一模一样。
4. **域名判定：``unknown`` 不覆盖，``unknown`` 被覆盖。** 一条重新导入的原始行带着
   ``DomainVerdict.UNKNOWN``，它是「这次没查」，不是「查过没有」—— 直接照抄会把
   上一次证得的官网抹掉。反过来，一次**真的做了判定**的来行要能填上（第 3 条那种
   「空不覆盖」在这里会让池子永远停在第一次看到的样子）。
5. **两个判定不一致时，报错，不选一个。** 见下面的「冲突」一节。

第 1、2 条的**时刻方向**与第 3 条里的证据并集，与责任关系表（``application/relations.py``）
逐字相同，所以那两个原语住 ``application/merge.py``，两个模块都调那一个定义处。

## 冲突：本层不猜谁更权威

两边都判定了、而且判得不一样（两个 ``confirmed`` 配了两个不同的域名，或
``confirmed`` 撞上 ``absent``），抛 :class:`~marketops.domain.errors.AssetConflictError`。

选哪一条都说得通，而**没有任何一条说得清**：这一层没有「谁的判定更权威」这条规则
（来源可信度、时间先后都不是它 —— 后写的判定可能来自一次更差的抓取）。所以它既不
按新覆盖、也不按旧保留，而是把两条都点出来交给一次显式的决定。这与 ``put_evidence``
对「同一个内容寻址的 id 配了不同内容」报错是同一条处置：**一个 id、两句话，是错误，
不是合并**。

## 这几条**不**主张的事

- **不判断质量。** 第 3 条里「非空的两个值取后一个」不是一条质量判断：本层没有
  来源可信度的判据，所以按「后看到的为准」。把这一点写下来，是为了让下一个人知道
  这里缺的是判据而不是一行代码。
- **不合并证据的内容。** ``evidence_ids`` 取并集（保序、去重）—— 它们是**指针**，
  并集是「这些行都关于这块场地」。指向的那几行本身在证据库里只增不改。
- **不动 ``venue_asset_id`` 与 ``workspace``。** 两者的身份：折进来的对象与已有的行
  必须说的是同一块场地、同一个工作区，否则是调用方拿错了一个对象
  （:class:`~marketops.domain.errors.StoreError`）。
"""

from __future__ import annotations

from datetime import datetime

from ..domain.entities import VenueAsset, venue_asset_id_for
from ..domain.enums import DomainVerdict
from ..domain.errors import AssetConflictError, StoreError
from ..domain.profile import CLASSIFICATION_UNKNOWN
from ..domain.receipt import RowDisposition, SourceReceipt, resolved_dedupe_key
from .merge import later_of, ordered_union

__all__ = ["assets_from_receipt", "merge_assets", "venue_handle_for_row"]

#: 「这一次来源给了内容，就算一次说话；给的是空的，就不算」的字段 —— 每一样都是
#: 「非空赢、空不覆盖」。
#:
#: 里面住着**两类**东西，它们在这一条规则下的处置相同，但理由不同：
#:
#: * 我们自己的观察（``discovered_url`` / ``candidate_domain`` 等）—— 空的意思是
#:   「这次看到的那个页面没写这件事」；
#: * 上游的那句转述（``claimed_official_domain``）—— 空的意思是「这一份导出那一列
#:   是空的」。
#:
#: 两类都归到这一条，是因为它们要的处置逐字相同（见模块 docstring 第 3 条），而
#: 分成两张表会让读的人以为它们的规则不同。代价是：上游以清空列的方式改口时，池子
#: 会留着旧的那句话 —— 那一条的取舍写在模块 docstring 第 3 条里。
#:
#: 列成表而不是在函数体里写八遍 if，是因为**漏一个字段的后果是一样的**：合并出来的
#: 行是 ``{**existing.model_dump(), **changes}``，所以表外的格子**永远取已有的那一
#: 行** —— 一次新观察再怎么带着它，也传不进去。那一格会停在这一行落进池子时的样子，
#: 而报告上读起来只是「这块场地的这一栏一直没变」。
#:
#: 加字段时这里必须被想到 —— 它是这份合并规则的字段清单，而漏在表外的那个字段不会
#: 有人发现（``tests/test_application_pool.py::test_every_cell_on_a_pool_row_has_a_
#: decided_fate`` 是这一点唯一的执行器）。
_NON_EMPTY_WINS_FIELDS: tuple[str, ...] = (
    "name",
    "address",
    "notes",
    "claimed_official_domain",
    "discovered_url",
    "candidate_domain",
)

#: 我们**从页面读出来的**那几格资产事实（§2）。它们与上面那一表的规则只差一句：
#: **``unknown`` 也不算一次说话。**
#:
#: 为什么它们不能留在上表里：``unknown`` 是一个**非空字符串**，在「非空赢」那条
#: 规则下它是 truthy —— 于是「这一页这次没提」会把上一次真的读出来的值抹掉。
#: 一次重导入（换了一份导出、或者页面改口了）足以让池子里最贵的那几格消失，
#: 而报告上那只是「这一块的信息变少了」。
#:
#: 为什么不是「把 ``unknown`` 折成空串再进上表」：``unknown`` 与空串在这个模型上
#: 说的**不是同一件事**（一个是「这一页没有说」，一个是「这一份工件没有这一栏」，
#: 见 ``domain/entities.py`` 的字段文档），而折掉之后两者在池子里长得一样 ——
#: 那正是「一次重导入」与「页面改口了」分不开的那一天。
#:
#: ``unknown`` 这个写法**只有这一处被认识**：分类词表禁止一个取值叫这个名字
#: （``adapters/profile_loader.py`` 的 ``_load_classification``），所以它在这些格子上
#: 恒是那一句保留的话，不会与某个真实取值撞车。
_CLASSIFICATION_FIELDS: tuple[str, ...] = (
    "venue_type",
    "venue_environment",
    "surface",
)


def venue_handle_for_row(row: RowDisposition) -> str:
    """回执的一行 → 它在场地资产池里的句柄。**这条规则只有这一处定义。**

    ## 为什么它是一个函数，而不是函数体里的两行

    它原来只在 :func:`assets_from_receipt` 的循环里出现一次，那时「就地写两行」是
    对的。现在有了第二个消费者：发现期的候选要**换成 canonical 账号 id**，而账号 id
    由场地句柄派生（``application/promotion.py``）—— 而那条路径不折资产，它只是要
    说得出来「这一行将是哪一块场地、因而是哪一个账号」。

    两个调用点各写一遍的后果不是「多两行」，是**研究件的主语与池子里的行指向不同的
    东西**：两处都按名字与地点取句柄，改动其中一处（加一个字段、换一次规范化）不会
    有任何东西转红，而报告上两边各自看起来都正常 —— 研究件研究的那个账号，与池子里
    被折进去的那一块场地，从此不是同一个。这条是 §1 那条断点的形状。

    名字与地点这一对**只有一处定义**（``resolved_dedupe_key``，回执的去重键就是
    这一对）：两行在回执里算「同一个东西」，与它们在池子里取到同一个句柄，是同一件
    判断。写成元组解包而不是两次取字段，是为了让这条耦合结构化地成立 —— 那一对改了
    形状，这里会当场炸，而不是悄悄按旧的一对取句柄。
    """
    name, location = resolved_dedupe_key(row)
    return venue_asset_id_for(
        source_identity=row.source_identity, name=name, location=location
    )


def assets_from_receipt(
    receipt: SourceReceipt, *, workspace: str, seen_at: datetime
) -> tuple[VenueAsset, ...]:
    """一份来源回执 → 要折进池子的那几行。**每条 ``resolved`` 行一条，按 ordinal。**

    这是「母池 → 资产池」这一段里唯一的一步。它只读回执，不回头去读原始工件：
    回执是「这一批读成了什么」的唯一权威（``SourceReceipt`` 的类文档），再去读一遍
    那份 CSV 会让同一批行有两套读法，而两套读法不一致时没有任何一栏说得出来。

    ## 它不做的四件事

    * **不合并。** 一批里两行可能取到同一个句柄（没有来源编号、名字与地点又相同的
      那两行）。合并规则只有一条，住在 :func:`merge_assets` 里、由存储在写事务内
      调用 —— 在这里再折一次就是第二条规则。所以本函数**按 ordinal 一进一出**，
      同句柄的折叠交给 ``upsert_asset``。
    * **不读时钟。** ``seen_at`` 是参数：它在池子里是 ``first_seen_at``，而那是
      「我们何时第一次记下这块场地」。这个「何时」由调用方定义（工具层给
      ``utc_now()``），因为一条要能被逐条断言的规则不该自己去问现在几点。
    * **不填 ``last_verified_at``。** 读一份回执**不是**重新看过这些场地一眼 ——
      与 ``Observation.observed_at`` 同一条纪律。填上它，「这块场地多久没复核了」
      这个只在这一层建起来之后才有意义的问句，会在第一次导入那天就得到「刚刚复核
      过」，而下一轮维护会把它们整批跳过。
    * **不写证据。** ``evidence_ids`` 留空。回执里的 ``resolved_name`` 是**身份补全的
      产出**，不是一条可引用的证据 —— 把它当成证据会让「凭什么说它叫这个名字」
      指向一份回执，而回执指向的是上游的一句话。
      （``domain_evidence_ids`` 是唯一的例外，理由写在下面它出现的那一处：
      ``confirmed`` 那一格在模型上要求有凭据，而这几条 id 会在
      ``promotion._promote_one`` 那一步整体重锚，所以**盘上**不会出现两个 id 空间
      混在一行里。）

    ## 它填的格子

    ``name`` ← ``resolved_name``、``address`` ← ``resolved_location``、
    ``claimed_official_domain`` ← ``claimed_official_domain``、
    ``discovered_url`` ← ``discovered_url``、``candidate_domain`` ←
    ``discovered_host``、``verified_official_domain`` / ``verified_official_domains``
    / ``domain_verdict`` / ``domain_evidence_ids`` ← 回执上同名的几格、
    ``venue_type`` ← ``organisation_kind``、``venue_environment`` ←
    ``venue_environment``、``surface`` ← ``surface_class``、
    ``country`` ← ``country``、``source_identity`` 照抄。

    这几格的内容由**发现期**产出（``application/classification.py`` 读页面文字与
    主机名、``domain/domain_attribution.py`` 读页面文字与责任实体的名字），随回执的
    ``resolved`` 行走到这里。接法只有这一条 —— ``promotion.py`` 只读回执、不回头读工件，
    所以一条没进 ``RowDisposition`` 的事实，无论发现期算得多准，都到不了池子里。

    **这几格在这一层照抄回执的写法，含 ``unknown``。** 读不出来就是 ``unknown``，
    而 ``unknown`` 不是一次「这块场地是什么」的观察 —— 合并规则认识它、不让它覆盖
    （``_CLASSIFICATION_FIELDS``），而它**不被折成空串**：空串在这一层说的是另一件
    事（「这一份工件没有这一栏」），两者合成一种之后，「拿一份没有那一栏的导出重导」
    与「页面改口了」在池子里长得一样。

    ``candidate_domain`` 与 ``discovered_url`` **同时填或同时空**：前者是后者折出来的
    主机，所以只有前者而没有后者，读起来像一条写坏了的观察（``entities.py`` 的类文档
    写下了这一条）。回执没有 URL 时两格一起空 —— 空在这里是「这一份工件没有这一栏」。

    ``claimed_official_domain`` 照旧只拿上游那一句，合并规则**不会**把它提升成已确认
    （见 ``domain/entities.py`` 的类文档）。而 ``verified_official_domain`` 现在**有
    生产者了**：它拿的是回执上判定过的那几格，不是这一层自己推的。

    ``address`` 这一格拿的是来源给的地点文本，它可能是一个村、一个郡，而不是一个
    门牌地址 —— 本层不判断那份文本的粗细，也不去补全它。

    ``country`` 不再一律留 ``None`` 了。回执的 ``scope`` 仍然是**整批**的属性、
    仍然不按行写下去（「这一批的范围是 GB」不是逐行的国家声明）；这一格现在拿的是
    这一行**自己**判出来的那个值。只有回执那一行**根本没有这一栏**时它才是 ``None``
    —— 而不是回执的 ``scope`` 顶上来的一个替代品。
    """
    assets: list[VenueAsset] = []
    for row in receipt.resolved_rows:
        name, location = resolved_dedupe_key(row)
        assets.append(
            VenueAsset(
                workspace=workspace,
                # 句柄取自**唯一那一处**（:func:`venue_handle_for_row`）——
                # 账号 id 由同一个句柄派生，所以这里和第二处必须是同一句话。
                venue_asset_id=venue_handle_for_row(row),
                source_identity=row.source_identity,
                name=name,
                # 资产事实照抄回执那一行，**两种空缺写法都原样搬过来**：空串 = 这一份
                # 工件没有这一栏，``unknown`` = 读了、这一页没说出词表认得的说法。
                # 只有 ``country`` 要过一次类型：回执上「没有这一栏」是空串，资产上
                # 是 ``None``；而 ``unknown`` 两边是同一个字符串，**不折** ——
                # 折了之后「这一份导出没有这一栏」与「这一页没说」就分不开了。
                venue_type=row.organisation_kind,
                venue_environment=row.venue_environment,
                surface=row.surface_class,
                country=row.country or None,
                address=location,
                claimed_official_domain=row.claimed_official_domain,
                # 观察那两格成对：一个 ``candidate_domain`` 而没有 URL 的行读起来像
                # 一条写坏了的观察。回执没给 URL 时两格一起空。
                discovered_url=row.discovered_url,
                candidate_domain=row.discovered_host if row.discovered_url else "",
                # 判定那几格照抄。``domain_evidence_ids`` 里的 id 是**发现期**空间的
                # （``site-…``），而这里的 ``evidence_ids`` 留空是刻意的（见上）——
                # 它们由 ``promotion._promote_one`` 重锚到账号之后才写进池子。
                verified_official_domain=row.verified_official_domain,
                verified_official_domains=list(row.verified_official_domains),
                domain_verdict=row.domain_verdict,
                # 这几条 id 是**发现期空间的**（``site-…`` 那一边）。照抄过来是因为
                # ``confirmed`` 这一格在模型上**要求**有凭据 —— 一个「确认了官网」
                # 而没有出处的行写不出来。重锚到账号空间是
                # ``promotion._promote_one`` 那一步的事：它把发现期空间整体映射一遍，
                # 然后才写进池子。所以**盘上**永远不会出现两个 id 空间混在一行里。
                domain_evidence_ids=list(row.domain_evidence_ids),
                first_seen_at=seen_at,
            )
        )
    return tuple(assets)


def merge_assets(existing: VenueAsset | None, incoming: VenueAsset) -> VenueAsset:
    """把 ``incoming`` 折进 ``existing``；``existing is None`` 就是它本身。

    返回值**重新过了一遍模型校验**（``model_validate`` 而不是 ``model_copy``）：
    ``model_copy(update=...)`` 不跑校验器，而 ``VenueAsset`` 上
    ``_domain_verdict_agrees_with_the_fields`` 是三格一致性的**唯一**执行器 ——
    合并出来的行绕开它，正是「域名填了但判定还是 unknown」这种行被写进池子里的路径。

    纯函数：不读盘、不写盘、不碰时钟。所以这一整条规则可以在没有库、没有配置的情况下
    被逐条测出来（``tests/test_application_pool.py``）。
    """
    if existing is None:
        return incoming
    if (existing.venue_asset_id, existing.workspace) != (
        incoming.venue_asset_id,
        incoming.workspace,
    ):
        raise StoreError(
            f"要折进来的是一行 venue_asset_id={incoming.venue_asset_id!r} "
            f"workspace={incoming.workspace!r}，而池子里那一行是 "
            f"venue_asset_id={existing.venue_asset_id!r} "
            f"workspace={existing.workspace!r}。\n"
            "两者说的是不同的东西，所以这不是一次合并 —— 调用方拿错了对象。\n"
            "照折的话，一次写错的 id 会把另一块场地的行整个改写掉，而报告上它看起来"
            "只是那块场地的信息变了。"
        )

    changes: dict[str, object] = {
        # 只往早走 / 只往晚走：见模块 docstring 的第 1、2 条。
        "first_seen_at": min(existing.first_seen_at, incoming.first_seen_at),
        "last_verified_at": later_of(existing.last_verified_at, incoming.last_verified_at),
    }

    for field in _NON_EMPTY_WINS_FIELDS:
        value = getattr(incoming, field)
        if value:
            changes[field] = value

    # 分类那三格：非空**且**不是 ``unknown``。见 _CLASSIFICATION_FIELDS。
    for field in _CLASSIFICATION_FIELDS:
        value = getattr(incoming, field)
        if value and value != CLASSIFICATION_UNKNOWN:
            changes[field] = value

    # ``country`` 与上面同一条规则，而它有**两种**「没说」：``None``（这一份工件
    # 没有这一栏）与 ``unknown``（这一页没说）。
    if incoming.country is not None and incoming.country != CLASSIFICATION_UNKNOWN:
        changes["country"] = incoming.country

    changes.update(_merge_domain(existing, incoming))

    # 证据取并集、保序：已有的在前，新来的按顺序接上。并集是「这些行都关于这块场地」，
    # 而证据行本身在证据库里只增不改 —— 所以这里不需要（也不该）去重之外的处理。
    changes["evidence_ids"] = ordered_union(existing.evidence_ids, incoming.evidence_ids)

    return VenueAsset.model_validate({**existing.model_dump(), **changes})


def _merge_domain(existing: VenueAsset, incoming: VenueAsset) -> dict[str, object]:
    """官网那几格怎么折。**这是这份规则里唯一会抛的地方。**

    * 来行是 ``unknown``（这次没查）→ 各格全部不动。这是 §4 那句「重新导入不得抹掉
      已确认的官方域名」的落点。
    * 来行做了判定、已有的还是 ``unknown`` → 各格全取来行的（池子被填上）。
    * 两边都做了判定且**判得一样** → 域名与判定取来行的（同值），证据取并集 ——
      第二次独立看到同一件事是**加强**，不是重复。
    * 两边都做了判定且**判得不一样** → 抛 :class:`AssetConflictError`。

    比的是**集合**（``verified_official_domains``），单数那一格跟着集合走、不单独比：
    两格比两次等于让「读法」也拥有一次否决权，而它只是集合在恰好一个元素时的读法。
    """
    if incoming.domain_verdict is DomainVerdict.UNKNOWN:
        return {}
    if existing.domain_verdict is DomainVerdict.UNKNOWN:
        return {
            "verified_official_domain": incoming.verified_official_domain,
            "verified_official_domains": list(incoming.verified_official_domains),
            "domain_verdict": incoming.domain_verdict,
            "domain_evidence_ids": list(incoming.domain_evidence_ids),
        }
    if (
        existing.domain_verdict is not incoming.domain_verdict
        or existing.verified_official_domains != incoming.verified_official_domains
    ):
        raise AssetConflictError(
            f"场地 {existing.venue_asset_id} 上两次官网判定不一致：\n"
            f"  池子里那一行：{existing.domain_verdict.value}"
            f"、域名={existing.verified_official_domains or '(无)'}"
            f"、证据={existing.domain_evidence_ids}\n"
            f"  本次这一行：  {incoming.domain_verdict.value}"
            f"、域名={incoming.verified_official_domains or '(无)'}"
            f"、证据={incoming.domain_evidence_ids}\n"
            "两次都是**做过判定**的（不是「这次没查」），所以这不是一条「新观察推翻旧的」"
            " —— 本层没有「谁的判定更权威」这条规则（来源可信度、时间先后都不是它），"
            "所以它不替人挑一条。\n"
            "处置是回看两边各自的证据，然后显式改池子里那一行 —— 照折会把其中一条判定"
            "静默作废，而报告上那看起来只是一次正常的更新。"
        )
    return {
        "verified_official_domain": incoming.verified_official_domain,
        "verified_official_domains": list(incoming.verified_official_domains),
        "domain_verdict": incoming.domain_verdict,
        "domain_evidence_ids": ordered_union(
            existing.domain_evidence_ids, incoming.domain_evidence_ids
        ),
    }
