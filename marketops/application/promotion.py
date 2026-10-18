"""候选 → 场地资产 → **canonical 账号**：两个身份空间之间的那一步。

## 它接的是哪一段断口

发现那一轮的产出与研究那一侧的输入此前**一个交点都没有**：

* 研究侧的主体是 ``row-<16 位十六进制>``（``domain/receipt.py::row_id_for``）——
  一行在**某一份工件里**的位置，所以工件一重排它就会变；
* 账号池那侧的主键是研究那一轮之后才写下的 slug（``market_account.account_id``）。

于是 ``research_work_item.account_id ∩ market_account.account_id`` 真的是空集：
检索发生了、证据落了盘、路线也算出来了，而账号池里一个账号都没有 ——
两份报告各自看起来都正常，这是这一条最难被发现的地方。

## 那一步是什么

    receipt.resolved_rows ──assets_from_receipt──▶ VenueAsset ──┐
                          （句柄的唯一定义处）                  │ venue_asset_id
    site 上那条发现期证据 ──re-anchor──▶ Evidence under acct-… ─┴──▶ MarketAccount

三件事，顺序是承重的：

1. **场地先落进资产池。** 走的是 ``application/pool.py::assets_from_receipt`` ——
   句柄的规则只有那一处定义，这里**再算一遍**就是第二条规则，而两条迟早对同一行
   给出两个句柄。
2. **canonical 账号 id 由场地句柄派生**（:func:`canonical_account_id_for`，纯函数）。
3. **账号要有证据才写得进去**（``MarketAccount`` 自己那条校验
   「没有证据的账号不落盘」），而发现期的证据挂在 ``site-<…>`` 这个**候选**身份上，
   所以要先把它重新锚到这个账号上（见下）。

## 为什么 canonical 账号 id 派生自**场地句柄**，而不是 ``(receipt_id, row_id)``

``(receipt_id, row_id)`` 也能算出一个纯函数 id，而且显然它是 1:1 对着一份回执里
的一行的。放弃它只有一条理由，但那一条是硬的：``row_id`` 的输入里有 ``ordinal``，
也就是**那一行在这份工件里的位置**（``venue_asset_id_for`` 的类文档里写着同一条），
而 ``receipt_id`` 的输入里有工件摘要。母池下个季度多插一行、或导出的字节变了，
同一个账号就会拿到一个新的 ``account_id`` —— 而账号池的折叠键**只有**
``account_id`` 一格（``MarketAccount`` 明令不得按域名/名字/地址认人），
所以池子里会多出一行**看起来是刚发现的新账号**，实际上同一个组织已经在里面躺着。
``venue_asset_id_for`` 的全部理由就是把这件事挡在句柄那一层，而账号 id 跟着它走，
就免费拿到了同一条性质。

这条规则合并了两个实体吗？**没有，而且这正是它成立的原因。** 场地句柄是回执**自己**
的去重键（``resolved_dedupe_key``：同名同地点是同一个东西；有来源编号时按编号）的
产物 —— 「这两行说的是不是同一个东西」这条判断已经被回执做完了，这里只是把它的
结论换个名字写出。§1 禁止的是**本层**按形状（同域名、名字像、页面标题、URL slug）
去合并两个账号，这里一格都没做。

## 为什么账号 id 不派生自 ``row_id``（另一条，独立于上面）

``row-…`` 是**行**的身份，而账号是**组织**。一份母池里同一个组织管着两块场地是常态，
那时两块场地的资产句柄不同 → 两个账号；这与 §3 的关系表是同一件事的两半：
「一个场地可以由多个实体负责」的镜像。而反过来，同一块场地的两行（不同来源各自
导出一次）本来是两行、两个 ``row_id``，却会被池子折进**同一块场地** —— 那时账号
必须也只有那一个，否则一次重导出就把账号池翻一倍。

## 它**不做**的四件事（写下来，不是忘掉）

* **不合并账号。** 一个场地句柄一个账号，句柄不同就是两个账号，哪怕它们名字很像。
  合并要有说得出来的证据，而这条路径上一格这样的证据都没有 —— 本层最多写着
  「这个名字是从这一页读来的」。
* **不写域名、地址、别名。** 回执确实带着 ``official_domain`` 与 ``resolved_location``，
  但它们是**这块场地**的属性（那一页从哪台主机取回、那行说它在哪），而
  ``MarketAccount.domains`` / ``addresses`` 是**这个组织**的属性。把前者当成后者，
  正是 §3 禁止的那一类「从形状推责任」的错，只不过主语换成了地址。
  域名的三态（``discovered_url`` / ``candidate_domain`` / ``verified_official_domain``）
  住在场地资产那一行上（§2），提升要证据。
* **不填 ``country``。** 留 ``None`` = 「还没查」。回执的 ``scope`` 是**整批**的属性，
  「这一批的范围是 GB」不是逐行的国家声明。
* **不写关系。** 这块场地由谁负责是 §3，要的是证据，不是这一层能推的。

## 它不判断的第五件事：哪一行**配不上**账号

``pending`` / ``merged`` / ``excluded`` / ``failed`` 四种行不会走到这里 ——
``resolved_rows`` 已经把它们的去向写在回执上带着理由了。而 ``resolved`` 的行里，
**发现那一轮没真的取到内容**的那些也写不出账号：``MarketAccount`` 至少要一条证据，
而它们的证据一条都不存在。那是 ``ASSET_ONLY``：场地照样进池子（场地是存在的，
它只是还没被看过），账号**不写**，理由逐行给出。编一条证据把它们凑成账号，
恰恰是这一层唯一不能做的事。

## 重放幂等

三件写入都是幂等的，而且各自有各自的判据：场地与账号走的是「同一个键上折一次」，
证据走的是内容寻址的 id（``evidence_id`` 的输入里有 ``account_id``，所以同一个字面
在两个账号下是两行 —— 那正是这一层要的）。所以**同一份回执折两遍**之后池子与
证据库与折一遍逐字节相同。这不是省事：一次重跑要是让池子多出一批行，报告上它读起来
只是「这一批更大」。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..domain.account import MarketAccount
from ..domain.entities import VenueAsset
from ..domain.enums import AccountStatus
from ..domain.evidence import Evidence, evidence_id
from ..domain.fold import FoldAttribution
from ..domain.receipt import RowDisposition, SourceReceipt
from ..domain.subject import SubjectDossier
from ..domain.util import SEP, check_canonical_id, sha256_text
from ..ports.pool import AccountPoolStore, VenueAssetStore
from ..ports.store import EvidenceStore
from .accounts import AccountDisposition, remember_accounts
from .discovery_run import SOURCE_IDENTITY_NAMESPACE, candidate_id_for
from .pool import assets_from_receipt, venue_handle_for_row

__all__ = [
    "ACCOUNT_ID_PREFIX",
    "PromotionDisposition",
    "PromotionOutcome",
    "canonical_account_id_for",
    "canonical_account_id_for_row",
    "discovery_candidate_id",
    "promote_receipt",
    "unpromoted_subjects",
    "venue_handle_for_account",
]

#: canonical 账号 id 的前缀。它同时是**看得见**的一格：一个以 ``acct-`` 开头的
#: ``account_id`` 只可能由这一层写下，所以「这个账号是从哪来的」在报告上答得出。
ACCOUNT_ID_PREFIX = "acct-"

#: 发现那一轮写下的 ``source_identity`` 的形状（``<namespace>:<主机>``）。
#: 从 ``discovery_run.source_identity_for`` 那边反着读回来，判据只有一处定义。
_DISCOVERY_IDENTITY_PREFIX = f"{SOURCE_IDENTITY_NAMESPACE}:"


def canonical_account_id_for(venue_asset_id: str) -> str:
    """场地句柄 → canonical 账号 id。

    **纯函数**，而且这是承重的：一条会读时钟、会看集合迭代顺序的派生会让重放
    造出**新**的账号，而报告上那只是「这一轮多发现了几个组织」。

    形状是 ``acct-<16 位十六进制>``：``[a-z0-9-]``，长度 21，因此它通过了
    ``check_canonical_id`` —— 它会变成证据文件的名字（``<account_id>.jsonl``），
    而一个需要被规范化的 id 会与另一个 id 落到同一个文件上，两个账号静默合并。
    """
    check_canonical_id(venue_asset_id, "venue_asset_id")
    digest = sha256_text(SEP.join(["account", venue_asset_id]))
    return f"{ACCOUNT_ID_PREFIX}{digest[:16]}"


def canonical_account_id_for_row(row: RowDisposition) -> str:
    """回执的一行 → 它在账号池里的身份。**研究件的主语就是它。**

    两句话：句柄怎么来（``application/pool.py::venue_handle_for_row``，与
    ``assets_from_receipt`` 是同一处定义），以及账号 id 怎么由句柄来
    （:func:`canonical_account_id_for`）。这里只把它们接起来，**不再算第三遍** ——
    研究侧自己算一遍句柄的话，它研究的那个账号与池子里被折进去的那块场地会各自
    漂移，而两边看起来都正常。

    ``row_id`` 不进这个函数。它是**来源行的身份**（哪份工件里的第几行），只配出现在
    出处那一栏里 —— 拿它当账号身份就是这一轮要修的那个断点本身。
    """
    return canonical_account_id_for(venue_handle_for_row(row))


def venue_handle_for_account(receipt: SourceReceipt, account_id: str) -> str:
    """这份回执里**折成这个账号**的那一行是哪块场地；没有这一行时返回空串。

    ## 为什么这一格必须有人算得出来，而不是「反正账号是从场地派生的」

    账号 id 是 ``sha256``，**不可逆**。所以「``acct-…`` 是哪块场地」这个问题在
    池子里答不出来，而研究侧恰恰需要它：§3 要的责任关系说的是「这个账号对**哪块
    场地**负哪种责」，而一次研究的产出必须能指认它研究的是哪一块。

    今天唯一能回答它的地方就是这份回执 —— 那里同时有行与由行派生出的账号 id。

    ## 为什么按 ``account_id`` 比，而不是按行号取

    行号是「这份工件里的第几行」，而账号 id 是从场地句柄派生的。两者都能定位
    同一行，但只有前者会被**一次重导出或一份换过的回执**悄悄指到别处 —— 那时
    按行号取会得到一块**别的**场地，而它看起来完全正常（一块真场地、一条真句柄），
    于是关系会被写到一块不相干的场地上。

    按账号 id 比则不可能指错：对不上就是空串，调用点据此拒绝这一批关系，
    而不是猜一块。

    ## 为什么返回空串而不是抛

    三种真实的到达方式（这份回执不是当初编译这件工作的那一份、这一行在回执里
    不是 ``resolved``、这份回执根本没有这一行）**处置完全一样**：这一批关系不写，
    理由由调用点给出并带上这里的上下文。抛异常会让一次「某一行对不上」升级成
    整次结算的失败，而同一份产出里其余的结论与它无关。
    """
    for row in receipt.resolved_rows:
        handle = venue_handle_for_row(row)
        if canonical_account_id_for(handle) == account_id:
            return handle
    return ""


def unpromoted_subjects(
    subjects: Sequence[SubjectDossier], *, evidence: EvidenceStore | None
) -> tuple[tuple[str, str], ...]:
    """这一批主体里，哪几个**今天在账号池里不存在** —— 逐条给一条理由。

    回执来的主体，它的 ``account_id`` 是一个**派生值**（上面那个函数）：算得出来，
    不等于池子里真的有那一行。「算得出来」与「真的折进去了」差着一次
    :func:`promote_receipt`，而这一次没做过的话，后面整条链会**静默地**按一个
    不存在的账号跑完 —— 检索发生、证据落盘、路线算出来，而 ``get_account`` 一条都
    找不到。这正是 §1 那条断点原来的样子，只是主语从 ``row-*`` 换成了 ``acct-*``：
    **换了个名字的空集还是空集。**

    所以这个判据必须在下发之前响。它读的是**这个账号名下的证据**：

    * 折进去的那些行（``PROMOTED``）一定带着证据 —— ``MarketAccount`` 自己那条
      「没有证据的账号不落盘」挡在前面，而 ``_promote_one`` 是先把证据写进去、
      再折账号的；
    * 没折进去的那些行（``ASSET_ONLY``：那一页没取到；``REJECTED``：跨工作区、
      或名字那格过不了）一条证据都没有。

    于是「这个 id 名下有证据」与「这一行真的成了一个账号」在**今天可达的每一种
    状态下**是同一句话，而前者正好是 §1 验收里那句「它的 Evidence 读得出来」。

    ## 为什么不直接去读账号池

    那要多把账号池的工厂穿到两个调用点上（``tools._resolve_and_compile`` 与
    ``product_loop._import_motherpool``），而它们在同一个库文件上读的是**同一份事实**
    的两种写法。宁可读那份更靠下游的（证据是账号存在的原因，账号是它的结论），
    也不要为了「读起来更直接」在这里多接一条线：两条判据并存的那天，先动的那一条
    不会让另一条转红。

    ## ``evidence=None`` 不是「没法判」

    ``None`` 是「这个部署还没有那个库文件」—— ``tools.read_store`` 对一份还不存在的
    库给的就是它。那时没有一条证据，所以**每一个**主体都是未折进去的，返回整批。
    把 ``None`` 读成「跳过这一条判据」会让一个刚建起来的部署安静地接受一份从没折过
    的回执，而它的报告与一个正常部署长得一样。

    Returns:
        ``(account_id, 理由)``，**按主体的顺序**。空元组 = 全部都在池子里。
        顺序不是风格：调用点把第一条按顺序渲染出来，而主体的顺序决定 ``ordinal``，
        因而决定 ``work_item_id``。
    """
    refusals: list[tuple[str, str]] = []
    for dossier in subjects:
        rows = [] if evidence is None else evidence.list_evidence(dossier.account_id)
        if rows:
            continue
        refusals.append(
            (
                dossier.account_id,
                f"账号池里没有 {dossier.account_id}（{dossier.organisation}），"
                "它名下一条证据都没有。",
            )
        )
    return tuple(refusals)


def discovery_candidate_id(row: RowDisposition) -> str | None:
    """这一行的发现期身份，或 ``None``（它不是发现那一轮写下的行）。

    ``source_identity`` 是 ``site:<主机>`` 时，取值段**就是**那一轮的主机名，
    而 :func:`~marketops.application.discovery_run.candidate_id_for` 是主机 →
    候选 id 的**唯一**一处派生。所以这里不做第二种读法，也不去猜
    ``official_domain`` 那一格 —— 两者今天同值，但它们是**两句话**
    （「上游说这行属于谁」与「这一页从哪台主机取回」），而拿第二句去替第一句
    正是「一次看起来完全正常的静默替换」。

    不是发现的回执行返回 ``None``，**这是结果不是失败**：一份手工导入的 CSV 没有
    任何发现期证据，所以它下面一行账号都写不出来（见模块 docstring 最后一节）。
    """
    identity = row.source_identity.strip()
    if not identity.startswith(_DISCOVERY_IDENTITY_PREFIX):
        return None
    site = identity[len(_DISCOVERY_IDENTITY_PREFIX) :].strip()
    if not site:
        return None
    return candidate_id_for(site)


class PromotionDisposition(str, Enum):
    """一行 ``resolved`` 折完之后**去了哪**。闭集，三个成员。

    ``promoted``
        场地进了资产池，账号也进了账号池。这一行现在**可以被派发研究**了。
    ``asset_only``
        场地进了资产池，账号**没有**写。理由逐行给出，两种情形说的是两句不同的话：
        这一行不是发现那一轮写下的（没有发现期证据可取），或者那一页根本没取到 /
        解析不出可引用的正文。场地是存在的，它只是还没被看过；账号是「有一个组织」
        这个结论，那需要依据。
    ``rejected``
        这一行写不进去，而且理由说得出来。今天只有一种情形会走到这里：它按发现期
        的身份取回了证据，而其中有一条的 ``workspace`` 不是这次要写的那个 ——
        那是**跨工作区**，一次授权链上的越界，它不该被当成「这一行没有证据」静默
        降级（后者是 ``asset_only``，一个正常结果）。见 :func:`_discovery_evidence`。

        这一支**一行都不写**：拒绝发生在资产落池之前，所以没有半个结果留在盘上。

    ## 为什么没有 ``folded``

    没有第四个成员，不是漏掉的。折叠发生在 ``upsert_asset`` / ``upsert_account``
    里，而它们按句柄折 —— 两份回执说同一块场地是**两次调用**，各自都读回池子里
    那一行，两次都叫 ``promoted``；那是真话（这一行的账号确实在池子里）。

    那「一份回执里两行折进同一个账号」呢？**到不了**：一份发现回执里每一行带着
    自己那台主机的 ``source_identity``，而 ``venue_asset_id_for`` 在有来源编号时
    **只看编号** —— 两个不同的主机不可能给出同一个句柄。而两行真取到同一个句柄
    （没有编号、名字与地点又相同）时，它们没有 ``site:`` 身份，于是走的是
    ``asset_only``，压根到不了账号那一步。

    所以 ``folded`` 在这条路径上是一个**永远不会出现的取值**，而一个不会出现的
    取值会在报告里造出「有一种情况我们处理过」的错觉。它该出现的那一天，
    判据是「同一份回执里两行取到同一个句柄**且**都带着发现期证据」，那时再加。
    """

    PROMOTED = "promoted"
    ASSET_ONLY = "asset_only"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PromotionOutcome:
    """一行折完之后的那份读数。

    ``asset`` / ``account`` 是**池子里现在的那一行**（``upsert_*`` 的返回值），
    不是交上去的那一份：第二次折同一个句柄之后证据是并集、``first_seen_at`` 是
    更早的那一个。报出交上去的那一份，读的人会以为池子里就是那样。

    ``evidence_ids`` 是这一次**重新锚到账号上的**那些发现期证据。场地那一行上的
    ``evidence_ids`` 与它同值 —— 两处各存一份不是重复：它们说的是两件事
    （「这块场地有依据」与「这个账号有依据」），而今天它们恰好来自同一次抓取。
    """

    ordinal: int
    row_id: str
    source_identity: str
    venue_asset_id: str
    account_id: str
    disposition: PromotionDisposition
    organisation: str = ""
    asset: VenueAsset | None = None
    account: MarketAccount | None = None
    evidence_ids: tuple[str, ...] = ()
    #: 逐行的理由。``promoted`` 时是空串，其余两个成员必填。
    reason: str = ""

    def __post_init__(self) -> None:
        if self.disposition is not PromotionDisposition.PROMOTED and not self.reason:
            raise ValueError(
                f"第 {self.ordinal} 行的处置是 {self.disposition.value}，却没有理由。\n"
                "一条说不出理由的处置，与「这一行被静默丢掉了」在报告上不可区分。"
            )


def promote_receipt(
    receipt: SourceReceipt,
    *,
    workspace: str,
    seen_at: datetime,
    assets: VenueAssetStore,
    accounts: AccountPoolStore,
    evidence: EvidenceStore,
    run_id: str | None = None,
) -> tuple[PromotionOutcome, ...]:
    """一份来源回执 → 资产池里那几行 + 账号池里那几行。**逐行给出去向。**

    Args:
        receipt: 发现那一轮（或任何一次导入）写下的回执。只读它，不回头去读原始
            工件 —— 回执是「这一批读成了什么」的唯一权威，与
            ``assets_from_receipt`` 逐字同一条理由。
        workspace: 这一批属于哪个工作区（本部署的 Scene，``Settings.profile``）。
            **只从参数来，不从行里读** —— 一个能填 ``workspace`` 的入参等于让一次
            调用把行写进另一个 Scene，而三张表在同一个库文件里。
        seen_at: 「我们何时第一次记下它」。**由调用方给**，不是时钟：一条要能被逐条
            断言的规则不该自己去问现在几点（与 ``assets_from_receipt`` 同一条）。
        assets / accounts / evidence: 三张表。分成三个参数而不是一个「上下文」
            对象，是因为这一层要能在一个不接任何 MCP 的测试里被逐条测出来。
        run_id: 这一次折入**归属于哪一轮 Run**（§B17 条件 2）。给了它，每一行池子的
            写都会在**写那一行的同一个事务里**记下一笔归属（``upsert_asset`` 的
            ``fold=``），于是 ``close_run`` 能拿这份账去核对调用方的主张。

            **缺省 ``None`` 是一个被声明出来的事实，不是「还没接上」**：研究流程今天
            不在 Run 里跑，把它变成必填会当场打断产品环。但「这一批折入不属于任何
            一轮」因此是**数得出来的**（这一批的池子行没有任何一笔归属），而不是消失
            在 ``folded=()`` 后面。

            给了一个不存在、或者已经结束的 ``run_id`` 是一句拒词（``RunStateError`` /
            ``RunFoldLedgerError``），不是一次静默的忽略；那时**池子一个字节都不写**。

    Returns:
        与 ``receipt.resolved_rows`` **逐位对应**的一个元组。空回执行返回空元组 ——
        「这一批一行都没有」与「这一批全都折进去了」是两句话，而它们的区别在回执
        自己的 ``counts_line`` 上，不在这个返回值的长度上。

    Raises:
        StoreError: 存储本身的问题（这一行属于别的工作区、键绑不上、同一笔归属折进了
            另一个句柄）。与 ``remember_accounts`` 逐字同一条：它一次就不是「这一行的
            问题」，让它穿出去由调用点处置，而不是把它记成二十条一模一样的理由。
        RunStateError: ``run_id`` 指的那一轮已经结束了。
        RunFoldLedgerError: 那一轮的折入账不在（受理本该把它写下来）。
    """
    check_canonical_id(workspace, "workspace")

    rows = receipt.resolved_rows
    # 1:1 且同序 —— ``assets_from_receipt`` 只按 ordinal 走一遍。两处各写一遍
    # 「回执的哪一行对着哪一块场地」的话，漂移的表现是证据挂到了别的场地上，
    # 而报告上每一格看起来都正常。所以这里**不重算**句柄，只把两个序列对齐着走。
    built = assets_from_receipt(receipt, workspace=workspace, seen_at=seen_at)
    if len(built) != len(rows):
        raise ValueError(
            f"回执 {receipt.receipt_id} 有 {len(rows)} 行 resolved，"
            f"而 assets_from_receipt 给出了 {len(built)} 块场地。\n"
            "这两个数必须相等（那是同一个循环的两个视图）。不等意味着有一处改了"
            "而另一处没改，此时**按位置**把证据挂到账号上会挂错人 —— "
            "而挂错的证据在报告上与挂对的完全一样。"
        )

    return tuple(
        _promote_one(
            ordinal=ordinal,
            row=row,
            asset=asset,
            workspace=workspace,
            seen_at=seen_at,
            assets=assets,
            accounts=accounts,
            evidence=evidence,
            # 归属在这里构造一次、逐行带上：``receipt_id`` 与这一轮来自参数，
            # ``ordinal`` 是这一行在**本回执**里的行号 —— 三者一起构成重放键。
            # 构造在 ``folded`` 为 ``None`` 时**不发生**：``FoldAttribution`` 的
            # 两个身份格不接受空串，而「不属于任何一轮」不是一句要猜的话。
            fold=None
            if run_id is None
            else FoldAttribution(
                run_id=run_id, receipt_id=receipt.receipt_id, ordinal=ordinal
            ),
        )
        for ordinal, (row, asset) in enumerate(zip(rows, built, strict=True))
    )


def _promote_one(
    *,
    ordinal: int,
    row: RowDisposition,
    asset: VenueAsset,
    workspace: str,
    seen_at: datetime,
    assets: VenueAssetStore,
    accounts: AccountPoolStore,
    evidence: EvidenceStore,
    fold: FoldAttribution | None,
) -> PromotionOutcome:
    """折这一行。

    ## 三步的顺序

    **取证据 → 重锚并写证据 → 折场地 → 折账号。** 唯一的硬约束是最后一步：
    账号那一行引用的 id 必须**已经解析得出来**（``remember_accounts`` 会逐条
    ``get_evidence``，解析不出的那一条算 ``EVIDENCE_UNKNOWN``，账号就不写）。

    「先取、再写」还有第二个理由：跨工作区那一支要在**任何写入之前**拒绝掉。
    先折场地再取证据的话，被拒的那一行会在池子里留下一块场地，而报告上它是
    「这一行的处置是 rejected」—— 两句都对，合起来读却是「拒绝了一半」。

    ``fold`` 只交给 ``upsert_asset`` 一处：**上面那两支提前返回的拒绝不会折任何
    东西**，所以它们也不该在账上留一笔。账记的是「池子那一行落了盘」，而那两支行
    压根没走到池子那一步 —— 这与 ``ReconcileBooks.folded`` 认的是 ``asset is not
    None`` 是同一条口径（见 ``domain/fold.py`` 的模块 docstring）。
    """
    account_id = canonical_account_id_for(asset.venue_asset_id)

    found, refusal = _discovery_evidence(
        row, evidence, workspace=workspace, account_id=account_id
    )
    if refusal:
        return PromotionOutcome(
            ordinal=ordinal,
            row_id=row.row_id,
            source_identity=row.source_identity,
            venue_asset_id=asset.venue_asset_id,
            account_id=account_id,
            disposition=PromotionDisposition.REJECTED,
            organisation=row.resolved_name,
            reason=refusal,
        )

    anchored = tuple(
        _re_anchor(one, account_id=account_id, workspace=workspace) for one in found
    )
    for one in anchored:
        evidence.put_evidence(one)
    anchored_ids = tuple(one.evidence_id for one in anchored)

    # 发现期 id → 账号空间 id。``found`` 与 ``anchored`` 是**逐位对应**的两次遍历
    # （同一份列表、同一个顺序），所以这张表不是猜出来的。
    remap = {old.evidence_id: new.evidence_id for old, new in zip(found, anchored, strict=True)}
    domain_evidence_ids, missing = _remap_domain_evidence(row, remap)
    if missing:
        # 判定指着的那几条证据在这一步取不回来。**降级成拒绝，不是照旧成立**：
        # ``confirmed`` 的全部价值在于凭据，而证据没了之后那一行会是一个
        # 「有结论、没出处」的域名 —— 它在报告上与一个查证过的域名逐字相同。
        # 正常路径下到不了这里（同一次运行里刚写下的证据不可能消失），
        # 所以它响的那一天，说明有人动过证据库。
        return PromotionOutcome(
            ordinal=ordinal,
            row_id=row.row_id,
            source_identity=row.source_identity,
            venue_asset_id=asset.venue_asset_id,
            account_id=account_id,
            disposition=PromotionDisposition.REJECTED,
            organisation=row.resolved_name,
            reason=(
                f"这一行的官网判定（{row.domain_verdict.value}）指着 "
                f"{len(row.domain_evidence_ids)} 条发现期证据，其中 {len(missing)} 条"
                f"这一步取不回来：{missing!r}。\n"
                "「确认官网」的全部价值在于**凭据**：证据取不回来的域名与随手写下的"
                "域名，在审计里不是同一件事。所以这一行不写 —— 不降级成"
                "「没有域名」，因为那会把一次**完整性事故**读成一次正常的"
                "「这个组织没有官网」。"
            ),
        )

    stored = assets.upsert_asset(
        # ``model_validate`` 而不是 ``model_copy``：``VenueAsset`` 上那几条校验器是
        # 各自判据的**唯一**执行者，而拼出来的行绕开它正是「域名填了但判定还是
        # unknown」这种行被写进池子的路径（``merge_assets`` 同一条）。
        VenueAsset.model_validate(
            {
                **asset.model_dump(),
                "evidence_ids": list(anchored_ids),
                "domain_evidence_ids": list(domain_evidence_ids),
            }
        ),
        fold=fold,
    )

    if not anchored:
        return PromotionOutcome(
            ordinal=ordinal,
            row_id=row.row_id,
            source_identity=row.source_identity,
            venue_asset_id=stored.venue_asset_id,
            account_id=account_id,
            disposition=PromotionDisposition.ASSET_ONLY,
            organisation=row.resolved_name,
            asset=stored,
            reason=_no_evidence_reason(
                row, identity_known=discovery_candidate_id(row) is not None
            ),
        )

    written = remember_accounts(
        (
            {
                "account_id": account_id,
                "organisation": row.resolved_name,
                # 缺省就是 discovered（「还没有判定」）。写出来是因为这一行**就是**
                # 一次发现：§6 第三步那条准入判定要的是研究结果，不是一次导入。
                "status": AccountStatus.DISCOVERED.value,
                "evidence_ids": list(anchored_ids),
            },
        ),
        store=accounts,
        evidence=evidence,
        workspace=workspace,
        seen_at=seen_at,
    )[0]

    if written.disposition is not AccountDisposition.WRITTEN:
        # ``EVIDENCE_UNKNOWN`` / ``REJECTED`` / ``FOLDED``。证据刚刚才写进去，
        # 所以走到这里只可能是名字那一格过不了 —— 它带得出自己的理由。
        return PromotionOutcome(
            ordinal=ordinal,
            row_id=row.row_id,
            source_identity=row.source_identity,
            venue_asset_id=stored.venue_asset_id,
            account_id=account_id,
            disposition=PromotionDisposition.REJECTED,
            organisation=row.resolved_name,
            asset=stored,
            evidence_ids=anchored_ids,
            reason=f"账号那一行没写进去（{written.disposition.value}）：{written.reason}",
        )

    return PromotionOutcome(
        ordinal=ordinal,
        row_id=row.row_id,
        source_identity=row.source_identity,
        venue_asset_id=stored.venue_asset_id,
        account_id=account_id,
        disposition=PromotionDisposition.PROMOTED,
        organisation=written.organisation,
        asset=stored,
        account=written.row,
        evidence_ids=anchored_ids,
    )


def _discovery_evidence(
    row: RowDisposition, evidence: EvidenceStore, *, workspace: str, account_id: str
) -> tuple[tuple[Evidence, ...], str]:
    """这一行在发现期落下的证据，或一条说得出口的拒绝理由。

    第二格的判据只有一条：**跨工作区**。``list_evidence`` 走的存储是绑了工作区的
    （``bind_workspace``），所以正常路径下这里一行都筛不掉 —— 留着这一条不是因为
    预计它会响，而是因为它把「这条证据属于这次要写的那个工作区」这个前提放在
    取它的地方，而不是放在两个模块之间的一句口头约定上。

    筛掉的证据**不静默降级成「这一行没有证据」**：那会把一次授权链上的越界读成
    一个正常的 ``asset_only``，而两者的处置完全相反（一个要人看，一个不用）。
    """
    candidate = discovery_candidate_id(row)
    if candidate is None:
        return (), ""
    found = evidence.list_evidence(candidate)
    foreign = tuple(one for one in found if one.workspace != workspace)
    if foreign:
        lines = "\n".join(
            f"  - {one.evidence_id}（workspace={one.workspace!r}，来源 {one.source_url}）"
            for one in foreign[:5]
        )
        return (), (
            f"{row.row_id} 的发现期证据里有 {len(foreign)} 条不属于这次要写的工作区"
            f"（{workspace!r}）：\n{lines}\n"
            f"账号 {account_id} 因此一个字的证据都没有，所以这一行**不写** —— "
            "资产也不折。跨工作区引用不是「这一行没有证据」（那是一个正常结果，"
            "处置是 asset_only），它是一次授权链上的越界，必须看得见。"
        )
    return tuple(found), ""


def _remap_domain_evidence(
    row: RowDisposition, remap: dict[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """官网判定那几条证据 → 账号空间的 id。返回 ``(新 id 保序去重, 取不回来的旧 id)``。

    ``unknown`` 的行一条都没有，所以这里对绝大多数行是一条空路（这也是它便宜的
    原因）。有判定的行要求**每一条**都解析得出来：一条解析不出来就整行拒绝
    （理由写在那处调用的上方），而不是把剩下的几条当成「凭据够了」。
    """
    if not row.domain_evidence_ids:
        return (), ()
    resolved: list[str] = []
    missing: list[str] = []
    for old in row.domain_evidence_ids:
        new = remap.get(old)
        if new is None:
            missing.append(old)
            continue
        if new not in resolved:
            resolved.append(new)
    return tuple(resolved), tuple(missing)


def _re_anchor(one: Evidence, *, account_id: str, workspace: str) -> Evidence:
    """一条发现期证据 → 同一个观测、挂在账号底下的一行新证据。

    ## 为什么不是「把原来那条 id 引过来」

    ``Evidence.account_id`` 是**这条证据关于谁**。发现那一轮记的是
    ``site-<…>``（这一页是关于这个站点的），而 ``get_account`` /
    ``list_evidence`` 都是**按主体**读的 —— 直接引用的后果是账号那一行写着
    若干 ``evidence_ids``，而 ``get_account(acct-…)`` 的 ``evidence_count`` 是 0。
    那不是「少了一个数」，是报告上出现了两句互相矛盾的话，而读的人只会相信
    对自己方便的那一句。

    ## 为什么这不算伪造

    观测本身一格都没变：``source_url`` / ``content_sha256`` / ``literal_value`` /
    ``fetched_at`` / ``source_published_at`` / ``page_locator`` / ``parser`` /
    ``tier`` 逐格照抄，所以「谁在什么时候从哪一页读到了哪一段字面」这句话
    与原来逐字相同。变的只有**主语**，而那是这次提升的全部内容。

    ``evidence_id`` 的输入里本来就有 ``account_id``，而且文档写明那正是为了
    「两个账号在同一个页面上记同一个字面时拿到两个 id」。所以这不是这条路径
    绕过了什么，是它按设计的方式被使用。

    ## 代价（写下来）

    同一个站点上有多块场地时，那一页的证据会在每个账号下各存一份。它们是
    **同一个观测的多个主语**，不是重复观测 —— 但它们在盘上确实是多行，
    而 ``content_sha256`` 相同这一点是唯一看得出它们同源的地方。
    """
    return Evidence(
        workspace=workspace,
        evidence_id=evidence_id(
            account_id=account_id,
            source_url=one.source_url,
            field=one.field,
            literal_value=one.literal_value,
            digest=one.content_sha256,
        ),
        account_id=account_id,
        source_url=one.source_url,
        source_kind=one.source_kind,
        literal_value=one.literal_value,
        content_sha256=one.content_sha256,
        fetched_at=one.fetched_at,
        source_published_at=one.source_published_at,
        field=one.field,
        context=one.context,
        page_locator=one.page_locator,
        parser=one.parser,
        http_status=one.http_status,
        tier=one.tier,
    )


def _no_evidence_reason(row: RowDisposition, *, identity_known: bool) -> str:
    """``asset_only`` 的那一条理由。两种情形说的是**两句不同的话**。"""
    if not identity_known:
        return (
            f"这一行的 source_identity（{row.source_identity or '(空)'}）不是发现那一轮"
            "写下的形状（site:<主机>），所以它在发现期没有对应的抓取，也就没有"
            "任何证据可以挂到一个账号上。\n"
            "场地照样折进资产池 —— 场地是存在的，它只是还没被看过。账号是"
            "「有一个组织」这个结论，而本层拿不出任何依据支持它。"
        )
    return (
        f"发现那一轮没有为 {discovery_candidate_id(row)}（主机取自这一行的 "
        f"source_identity：{row.source_identity}）落成任何证据：那一页没取到，"
        "或者取到了但解析不出可引用的正文。\n"
        "场地照样折进资产池，账号**不写** —— ``MarketAccount`` 那一条"
        "「没有证据的账号不落盘」是唯一一条挡在「凭一次没成功的抓取建一个账号」"
        "前面的东西，而那样的账号在报告里与一个真的见过的组织长得一样。"
    )
