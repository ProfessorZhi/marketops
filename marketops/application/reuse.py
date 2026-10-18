"""获取前的 **ReuseDecision**：同一个许可范围里，同一个 URL 只取一次。

## 需求原文，以及它拆成哪两半

采集设计 §6 逐字两句，承重的各是一半：

* 「相同内容给多个 Scene **只付一次获取费用**，归属关联另算。」
* 「获取前执行 ReuseDecision，缓存键包含**许可范围**、canonical URL、请求变体、
  render/locale/解析版本、时间有效性；**有登录态的环境不进入公共缓存。本阶段默认
  匿名、无跨主体 cookie 复用。**」

第一句说的是**钱**：一次取回可以被多个 Scene 用，而费用只付一次。第二句说的是
**键**：复用只在同一个许可范围内发生，且键要把「这次取回是什么」说清楚。

第二句后面那两个分句是**边界**，不是补充：共享的**只能是字节**，不能是会话。
本仓今天两个抓取器都是匿名公开浏览（``ports/acquisition.py`` 的模块 docstring），
浏览器那一侧每次抓取起一个自己的 context，所以「跨主体 cookie 复用」在这里不是
被禁止，而是**没有可复用的东西** —— 共享的是一个 ``FetchResult``，不是一次会话。

## 许可范围 = 工作区，而这不是本模块定的

``domain/evidence.py::Evidence.workspace`` 逐字写着这条边界：「**工作区是授权链的
第一段**……原始事实在一个已授权工作区内部**可以跨 Scene 复用**，复用范围**止于
工作区边界**。」（``domain/maintenance.py::Observation`` 同一句话。）

所以「仅授权范围内」在这里有一个**已经写下来的**答案，不需要另立一套。台账把它
记在每一次判定上（:attr:`ReuseDecision.permission_range`），而**执行**它的不是这
张台账：请求上没有「范围」这一格，台账分不出两个工作区的请求有什么不同。执行在
构造处 —— ``refresh.build_refresher`` 一次 tick 造一张台账，范围取自它自己那个
``workspace`` 参数，而那个函数一次 tick 只服务一个工作区。做成这样而不是让台账
持有一个可变的范围，是因为**只有一种做法**：一次 tick 一张台账，寿命就是一轮
（与 ``TickPages`` 逐字同一条理由：放长它的后果是拿上一轮的结论给这一轮续期，
而账上显示的是「这一轮没花钱」）。

## 键里有哪几样，以及**没有**哪几样

设计要的键有五样。这一仓今天有生产者的只有两样，另外三样没有 —— 写在这里是为了
让下一个人知道那是缺口，而不是「我们忘了」：

=============  ==========================================================
许可范围        台账的构造参数，进每一次判定（不是键的一段，见上）。
canonical URL  **逐字节的 URL**。本仓的 URL 同一性判定就是字节精确的
               （``page_mirror.page_prefix`` 与 ``document_scope`` 的幂等键
               都如此）。做规范化在这里是**错的**：镜像文件名由被请求的
               那个 URL 算出来，而接收方拿到的字节属于一个它没请求过的
               URL 时，证据的 ``source_url`` 就成了一句假话。
请求变体        有生产者的是 ``FetchMode``（``http`` / ``browser`` / ``auto``
               决定这次取回是哪一个 tier，而 tier 不同结果就不是同一份材料）。
               设计里另外几个变体（locale / render 版本 / 解析版本）今天
               **没有生产者**：抓取请求上没有这几格。
时间有效性      台账的寿命就是一轮 tick，所以「这一轮之内」是它唯一的时效
               口径，没有第二个。它**不**沿用任何跨轮缓存 —— 见下。
=============  ==========================================================

## 共享的落点是**档位**，而 tier 是那一次取回的事实

``FetchMode`` 进键，而 ``FetchMode.AUTO`` 的含义逐字是「由 ``escalation.decide``
决定要不要进浏览器」。所以同一个 ``auto`` 键下的捐赠方可能给出一个**浏览器**渲染的
结果，而接收方自己那一趟会不会升级，在取回之前**没有人知道** —— 升级判定要读那次
HTTP 的初始结果。

于是有一件必须说明白的事：接收方可能拿到一个与「它自己发这一趟会拿到的」不同的
tier。这不是共享判错，而是**共享的价码**：接收方要的是一次 ``auto`` 取回的字节，
而那是捐赠方真的做出的一次 ``auto`` 取回，满足同一个请求；升级判定对同一页是确定
的，两次结果不同只可能来自那个站点在两次请求之间给了不同答案，而那时两个答案都是
真读数。

不去「只共享没升级的那些」的理由是钱：升级到浏览器正是最贵的那一次，而它才是一次
共享真正省下的东西。落在盘上的代价有界，且**看得见**：逐页记录的 ``tier`` 写的是
捐赠方那一次的真实档位，``reason`` 以 ``shared_within_range:`` 开头并接上捐赠方的
升级判定（见 :meth:`ReuseLedger.reuse`），所以读的人一眼看得到「这一页这次是从哪
来的」。接收方的指纹比对照旧做，变了就照旧落新证据行 —— 一条共享的字节不会让
「这一页有没有变」这个判断换个依据。

## 这一轮之内，而不是一个进程级缓存

``DocumentAcquisition`` 在一个进程里活得比一轮 tick 长得多（服务面就是如此），
把台账挂在它上面等于给整个进程开一个页面缓存：一次取回会被几小时后的一次复核
当成「刚刚取到的」，而那份新鲜度**没有**经过 plan 的时效判定（``recheck_pages``
的第二、五步）。所以台账是**调用方的东西**，寿命由调用方给，缺省是「没有台账」。

## 没有台账时**不共享**，而不是「随便共享」

缺省 ``copy_page=None`` 的含义不是「省掉这一步」，而是**这个部署落不下第二份
字节**：一行证据要能走回它的材料，靠的是 ``(account_id, source_url,
content_sha256)`` 三格（``adapters/page_mirror.py::read_stored_page``），而接收方
自己的目录里没有那份字节时，读取会报「名字对不上」—— 那是「这台机器没有那次
抓取的字节」，一句**真话**，但它是我们自己的沉默造成的。所以落不下就**不共享**，
理由写在判定里（``no_mirror_writer``），而不是悄悄地给出一份读不回去的引用。

## 一次复用**不写** ``FetchAttempt``

尝试账上一行说的是「我们发了这次请求，对方这样回答」。复用时我们**没有**发请求，
所以那一行不该存在 —— 写一行的话，它的 ``material`` 与 ``settlement`` 都得照抄
捐赠方，而 ``material`` 是一条关于**这一次**取回的肯定声明（见
``domain/fetch_attempt.py``）：抄一条 ``SYNTHETIC`` 会把一次自造材料的复用记成
现场覆盖。

被复用的那一页在记录里说得出这件事，靠的是 ``PageRecheck.reason`` 以
:data:`SHARED_PREFIX` 开头的那一句。**不是**靠 ``PageRecheck.fetched``：那一格
留在内存里（``state`` 用它把 ``REUSED`` 与 ``UNREACHABLE`` 分开），而它不进
``PageRecheck.payload()`` —— 那一份是 ``RecheckRecord.digest`` 的构成部分，
加一格会让每一份已经写在盘上的记录都换了形状，而这件事与本次要回答的问题无关。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ..domain.util import check_canonical_id
from ..ports.acquisition import FetchMode, FetchRequest, FetchResult
from ..ports.documents import PageCopier

__all__ = [
    "SHARED_PREFIX",
    "ReuseDecision",
    "ReuseLedger",
    "ReuseVerdict",
]

#: 复用过的页面在逐页记录里的理由前缀。**一处定义**：台账照它拼、复核那一侧照它认。
#:
#: 拼成一句话而不是一个新字段，是为了不动 ``PageRecheck.payload()`` —— 那一份是
#: 落盘记录的构成部分（``RecheckRecord.digest`` 按它算），加一格会让每一份已经写在
#: 盘上的复核记录都「换了形状」，而这件事与本次要回答的问题无关。
SHARED_PREFIX = "shared_within_range:"

#: 捐赠方没有名字时的落款（``FetchRequest.scope`` 允许为空，含义是「这次抓取不属于
#: 任何主体」，镜像落进 ``unnamed`` —— 见那个字段的 docstring）。
_UNNAMED = "unnamed"


class ReuseVerdict(str, Enum):
    """这一次取回是**自己发**还是**用别人的**。两格，各自都有处置。

    做成闭集而不是一个布尔：报告里要能数出「这一轮省了几次取回」，而一个布尔在
    序列化之后与「没有过的字段」同形。
    """

    FETCH = "fetch"
    REUSE = "reuse"


@dataclass(frozen=True)
class ReuseDecision:
    """一次 ReuseDecision 的完整说法：判成什么、凭什么、在哪个范围里。

    ``reason`` 在 :attr:`ReuseVerdict.FETCH` 时也非空，而且**必须**非空 —— 「没共享」
    有好几种（这一轮没人取过、有人取过但没拿到字节、这个部署落不下第二份），它们
    的处置不同，合成一句会让其中一个永远没人查。这与
    ``application/escalation.py`` 的 ``no_escalation_rule:`` 是同一条纪律。
    """

    verdict: ReuseVerdict
    permission_range: str
    url: str
    mode: str
    #: 是谁的那一次取回。``FETCH`` 时为空串（还没有人取过，或者捐赠方没有名字）。
    donor_scope: str = ""
    reason: str = ""

    @property
    def shared(self) -> bool:
        return self.verdict is ReuseVerdict.REUSE


class ReuseLedger:
    """**一轮之内、一个许可范围内**的共享台账：键 → 那一次取回及其字节。

    Args:
        permission_range: 许可范围，由调用方的构造处决定（见模块 docstring）。
            它进每一次判定，是这个台账对外说得出「我在哪个范围里判的」的唯一办法。
        copy_page: 把捐赠方落盘的字节落进接收方自己的目录。``None`` = 这个部署
            落不下第二份，于是**一次都不共享**（理由见模块 docstring）。

    Raises:
        ValueError: ``permission_range`` 不是规范 id。校验不做规范化 —— 与
            ``Evidence.workspace`` 同一条判据：两个不同的工作区被静默合成一个，
            在这张台账上就是一次跨主体的读。
    """

    def __init__(
        self, *, permission_range: str, copy_page: PageCopier | None = None
    ) -> None:
        self._range = check_canonical_id(permission_range, "permission_range")
        self._copy = copy_page
        #: 键 → 取过它的那个主体。**任何**一次取回都记（含没拿到字节的），因为
        #: 「这一轮没人取过」与「这一轮有人取过、没拿到字节」的处置不同。
        self._seen: dict[tuple[str, str], str] = {}
        #: 键 → 真正拿到字节的那一次结果，以及**捐赠方那一次的升级判定**。只有这一张
        #: 表能让复用发生；带上判定是因为接收方的记录里 ``reason`` 那一格平时写的就是
        #: 升级判定（``DocumentFetch.decision``），而它自己这一趟没有做过那个判定 ——
        #: 换成一句「共享来的」会让「这一页当初为什么进了浏览器」在记录里消失。
        self._bodies: dict[tuple[str, str], tuple[FetchResult, str]] = {}

    @property
    def permission_range(self) -> str:
        return self._range

    # ------------------------------------------------------------------
    def key(self, request: FetchRequest, *, mode: FetchMode) -> tuple[str, str]:
        """这一次取回在台账里的键。**逐字节 URL**，理由见模块 docstring。

        ``mode`` 进键而不是被忽略：``http`` 与 ``browser`` 取回来的不是同一份材料
        （一个是原始响应、一个是渲染之后的 DOM），把两次取回按同一个 URL 合起来，
        会让一个显式只要直连的调用方拿到一份渲染结果 —— 而它没有任何字段看得出
        这件事。这是设计里「请求变体」的那一段在本仓今天**唯一**的生产者。
        """
        return (request.url, mode.value)

    def decision(self, request: FetchRequest, *, mode: FetchMode) -> ReuseDecision:
        """这一次要不要用别人的字节。**不改台账**，可以在同一个键上问很多次。"""
        key = self.key(request, mode=mode)
        donor = self._seen.get(key)
        if donor is None:
            return ReuseDecision(
                ReuseVerdict.FETCH,
                self._range,
                request.url,
                mode.value,
                reason="not_seen_in_range",
            )
        body = self._bodies.get(key)
        if body is None:
            return ReuseDecision(
                ReuseVerdict.FETCH,
                self._range,
                request.url,
                mode.value,
                donor_scope=donor,
                reason="donor_had_no_body",
            )
        if self._copy is None:
            return ReuseDecision(
                ReuseVerdict.FETCH,
                self._range,
                request.url,
                mode.value,
                donor_scope=donor,
                reason="no_mirror_writer",
            )
        return ReuseDecision(
            ReuseVerdict.REUSE,
            self._range,
            request.url,
            mode.value,
            donor_scope=donor,
            reason=f"{SHARED_PREFIX}{donor or _UNNAMED}",
        )

    def remember(
        self,
        request: FetchRequest,
        *,
        mode: FetchMode,
        result: FetchResult,
        decision: str = "",
    ) -> None:
        """记下一次**真的发生过**的取回。捐赠方就是 ``result`` 自己。

        ``decision`` 是这一趟 ``DocumentAcquisition`` 给出的升级判定
        （``DocumentFetch.decision``）—— 它一起被记下来，因为接收方的记录里那一格
        平时写的就是它，见 :meth:`reuse`。

        ``body_path`` 为空的结果照样进 ``_seen``（「有人试过、没拿到字节」），但
        不进 ``_bodies`` —— 一次超时或一次 403 不是可以发给别人的材料：发给别人
        会把它变成**别人**的观测，而那一次请求发生在别人的名下、别人的时刻。
        拿不到字节的那几页照旧自己去取，代价是这一轮多花一次，而不是一条假读数。
        """
        key = self.key(request, mode=mode)
        scope = result.request.scope or request.scope
        self._seen[key] = scope
        if result.body_path is not None:
            self._bodies[key] = (result, decision)

    def reuse(
        self, request: FetchRequest, *, mode: FetchMode
    ) -> FetchResult | None:
        """拿到捐赠方的那份结果，**字节已经落进接收方自己的目录**；没有就 ``None``。

        ## 结果里的 ``request`` 被换成接收方的那一个

        ``FetchResult.request`` 会被下游逐字读（``FetchRequest.scope`` 的 docstring：
        它的值会**逐字**成为 ``Evidence.account_id``）。留着捐赠方的那个，会让一条
        证据归到一个**没有做过这次归属判定**的主体上 —— 而那正是「不串账号」要挡的。

        ``body_path`` 换成接收方自己那一份（``copy_page`` 的落点）。两份文件字节相同、
        名字各自按 ‹主体, URL› 算，于是「一行证据走回它的材料」一个字节都不用改。

        ## 捐赠方自己的那两句话被接在后面

        接收方这一趟**没有做过升级判定**，而它的记录里 ``reason`` 那一格平时写的就是
        那个判定。所以捐赠方那一次的判定原样接在后面，而不是被一句「共享来的」顶掉
        —— 否则「这一页当初是升级到浏览器才拿到的」会在接收方的记录里消失，而它的
        ``tier`` 明明写着浏览器。捐赠方 ``FetchResult.reason``（为什么是这个结果）也
        接上，它答的是另一个问题。

        三段串成一句的次序是**从接收方到捐赠方**：谁共享的 → 捐赠方为什么升级 →
        捐赠方那次抓取自己怎么说。
        """
        decided = self.decision(request, mode=mode)
        if not decided.shared:
            return None
        assert self._copy is not None  # decision 已经挡掉没有落盘能力的部署
        donor, decision = self._bodies[self.key(request, mode=mode)]
        assert donor.body_path is not None  # _bodies 里只放有字节的
        landed = self._copy(donor.body_path, request.scope, request.url)
        reason = ";".join(
            part
            for part in (decided.reason, decision, donor.reason)
            if part
        )
        return replace(donor, request=request, body_path=landed, reason=reason)
