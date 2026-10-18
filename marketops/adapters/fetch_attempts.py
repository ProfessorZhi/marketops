"""把一份回执收成一条尝试账 —— **两个抓取器共用的那一份**。

## 为什么它现在是一个模块，而不是 ``http.py`` 里的几个私有函数

它原来住在 ``marketops/adapters/http.py`` 里，名字都带下划线。今天第二个抓取器
（``crawl4ai_fetcher.py``，浏览器那一层）也要落同一本账，于是「谁拥有这套换算」
成了一个必须回答的问题。

三个答案里只有一个是安全的：

* **各写一份**：两份换算会在下一次改字段时漂移一格。漂移的形式不是报错，而是
  同一天的两条尝试在报告上读成两件事（例如一边认 ``text/csv`` 是正文、另一边
  认它是「没能归类」），而两份报告各自都完整。
* **浏览器那一层编一个自己的拼法**：:func:`~marketops.domain.fetch_attempt.attempt_id`
  的 docstring 在写下 ``method`` 那一段时就拒绝过这件事 —— 两种拼法都能长出合法的
  ``attempt_id``，报告上分不出来，只是同一个 URL 的两条尝试从此不在一个命名空间里。
* **一份共用**（本模块）：这是 ``domain/`` 那一条的同一句话落在适配器层的版本。

``domain/`` 装不下它：这一份换算要 :class:`~marketops.config.Settings`（工作区名）
与 ``ports/acquisition`` 的回执类型，而 ``tests/test_domain_is_pure.py`` 禁止 domain
依赖 ``marketops.domain`` 之外的任何东西。**这是一条真的约束，不是顺手的选择** ——
它决定了共用件只能落在 ``adapters/``。

## 这一份里哪些是「换算」，哪些是「判定」

换算：``final_url`` / ``redirect_trace`` / ``observed_status`` / ``body_*`` /
诊断那四格，全部逐格照抄，不做任何解释。

判定：三处，各自都有判据钉住。

* :func:`refusal_diagnosis` —— 一次地址判据拒绝归到哪一栏（两张集合是**分区**，
  由 ``tests/test_http_fetch_attempts.py`` 逐个成员钉住）；
* :func:`body_kind` —— 有正文但归不了类时取 ``UNKNOWN``，与「没有正文」分开；
* :func:`require_diagnosis` —— 一份回执没有诊断时**报错**，不许折成「一切正常」；
* :func:`attempt_from` 的 ``method`` / ``material`` 两个参数 —— 见下面各自的段落。
  预算那两格**不在其中**：它们由 ``request.reservation_id`` 推出来
  （``domain/fetch_attempt.py::settlement_of``），理由写在它下面那一段。

## ``require_diagnosis`` 为什么是一个具名函数

它原来是 ``attempt_from`` 里的一段 ``if``。第二个调用方（浏览器那一层的收口点
``crawl4ai_fetcher.py::_run``）出现时，两条路都要求同一句话，而「同一句话写在两处」
的漂移形式是：一处抛、一处静默回退，于是同一个坏回执在两个抓取器上得到两种结局。
所以它按本仓那条规则提了出来 —— **等第二个实现出现再提取**，而它出现了。
"""

from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..domain.access import AccessDiagnosis, ActionTaken, SuspectedCause
from ..domain.errors import StoreError
from ..domain.fetch_attempt import (
    BodyKind,
    FetchAttempt,
    MaterialProvenance,
    MethodKind,
    attempt_id,
    settlement_of,
)
from ..ports.acquisition import FetchRequest, FetchResult
from ..security.network import TargetRefusal

__all__ = [
    "BODY_KIND_BY_TYPE",
    "UNROUTABLE_REFUSALS",
    "UNSAFE_REFUSALS",
    "attempt_from",
    "body_kind",
    "refusal_diagnosis",
    "require_diagnosis",
]

#: 七条「我们**不允许自己去那里**」的拒绝理由。
#:
#: 它们读起来是七句话（协议不对、带了凭据、没有主机名、字面地址是内网的、
#: 名字属于保留空间、主机名是数字变体、公开域名解析到内网），但处置是同一条：
#: 记下这个目标、放弃它、改走其他公开来源 —— 换 IP、换 UA 都不适用。
UNSAFE_REFUSALS: frozenset[TargetRefusal] = frozenset(
    {
        TargetRefusal.SCHEME,
        TargetRefusal.USERINFO,
        TargetRefusal.NO_HOST,
        TargetRefusal.PRIVATE_TARGET,
        TargetRefusal.RESERVED_HOST,
        TargetRefusal.NUMERIC_HOST,
        TargetRefusal.RESOLVES_TO_PRIVATE,
    }
)

#: 两条**不是**「目标不安全」的拒绝：我们跟着一个来源走到了第九跳，或那一次
#: 响应没说下一跳去哪。它们说的是「这一次的路线没法确定」，处置是停用这个方法。
UNROUTABLE_REFUSALS: frozenset[TargetRefusal] = frozenset(
    {TargetRefusal.REDIRECT_LIMIT, TargetRefusal.REDIRECT_WITHOUT_LOCATION}
)


def refusal_diagnosis(refusal: TargetRefusal) -> tuple[SuspectedCause, ActionTaken]:
    """一次地址判据拒绝 → 诊断里的（根因，处置）。

    ## 为什么这两张集合加起来必须等于 ``TargetRefusal``

    落在这两张表之外的理由码会走 ``UNKNOWN`` 那一支 —— 那是一个**诚实的**答案
    （见 :class:`SuspectedCause` 对 ``UNKNOWN`` 的说明），所以新加一个成员时这里
    不会先撒一个谎。但那意味着一个新的「地址不安全」理由会被记成「根因不明」，
    而两份报告上读起来是两句不同的话。

    所以判据（``tests/test_http_fetch_attempts.py``）把**每一个成员各自去哪一格**
    钉住：新增成员时它会红，而红的位置正是要做的那个决定。这里不 ``raise``，
    理由是本函数在 ``fetch`` 的返回路径上 —— 一个为了「枚举没写全」而抛出来的异常
    会把一份已经量好了的回执换成一次崩溃，代价比一次分类保守更大。

    Raises:
        KeyError: 不会。两张集合之外的成员取 ``UNKNOWN`` / ``STOPPED_METHOD``。
    """
    if refusal in UNSAFE_REFUSALS:
        return SuspectedCause.UNSAFE_TARGET, ActionTaken.REFUSED_UNSAFE_TARGET
    if refusal in UNROUTABLE_REFUSALS:
        return SuspectedCause.UNKNOWN, ActionTaken.STOPPED_METHOD
    return SuspectedCause.UNKNOWN, ActionTaken.STOPPED_METHOD


#: ``Content-Type`` 的标记 → 正文类别。**顺序即优先级**，与 ``_EXTENSION_BY_TYPE``
#: 同一个形状。表里没有的一律 ``UNKNOWN``（``BodyKind`` 的 docstring：那是
#: 「得到了正文但我们没能归类它」，与「没有正文」是两件事）。
BODY_KIND_BY_TYPE: tuple[tuple[str, BodyKind], ...] = (
    ("html", BodyKind.HTML),
    ("pdf", BodyKind.PDF),
    ("json", BodyKind.JSON),
    ("xml", BodyKind.XML),
    # csv 没有自己的成员，而它是一次**文本**响应 —— 归 ``UNKNOWN`` 会说「没能
    # 归类它」，那是一句假话。
    ("csv", BodyKind.TEXT),
    ("plain", BodyKind.TEXT),
)


def body_kind(content_type: str) -> BodyKind:
    lowered = content_type.lower()
    for needle, kind in BODY_KIND_BY_TYPE:
        if needle in lowered:
            return kind
    return BodyKind.UNKNOWN


def require_diagnosis(result: FetchResult, *, method: MethodKind) -> AccessDiagnosis:
    """这一份回执的根因与处置。**没有就是报错，不是「一切正常」。**

    ## 它守的是哪一句话

    ``FetchResult.diagnosis`` 的 ``None`` 意思是「这一层没有产生诊断」。账本读的是
    ``AccessDiagnosis`` 的四个字段（``cause`` / ``action`` / ``retry_after_s`` /
    ``challenge_detected``），所以一份没有诊断的回执只能靠**缺省值**落账 —— 也就是
    ``cause=none, action=proceeded``：**「这一次没有任何异常、正常继续」**。§5 要
    消灭的正是这种把「不知道」折成「正常」的读数，而它在报告上看不出与真的正常
    有什么区别。

    ## 为什么两个调用方都要它

    调用方各自读的是这份诊断的不同后果，而**两者都要求它存在**：

    * :func:`attempt_from` —— 少了它，那一行账会写着「一切正常」；
    * ``crawl4ai_fetcher.py::_run`` —— 少了它，``FetchPermit.record`` 就无从调用，
      而**来源账于是学不到这次失败**。§5 那条坏结局（同一个来源上一个挑战页让
      20 个账号各自无界重试）长出来的方式正是「某一次失败没有被记进那本账」。

    合成一个函数而不是在两处各写一遍：分成两处之后，漂移的形式是一处抛、一处
    静默回退 —— 同一个坏回执在两个抓取器上得到两种结局，而**报告上只有其中一种
    看得见**。

    ## 为什么抛 ``StoreError`` 而不是断言

    两者都在**账**的路径上（一本是落盘的尝试账，一本是内存里的来源账），而这一条
    要挡的错是「账上多了一句假话」。用断言的写法在 ``-O`` 下会整条消失，让这一格
    重新变回那句假话（``attempt_from`` 从写下它的那一版起就是这个理由）。

    Raises:
        StoreError: 这一份回执不带诊断。消息里点名是哪一层的回执（``method``），
            因为「谁加了一条不产生诊断的出口」在报告上要能直接读出来。
    """
    diagnosis = result.diagnosis
    if diagnosis is None:
        raise StoreError(
            f"这一份回执没有诊断（{result.outcome.value} / {result.request.url}），"
            "所以这次尝试没有根因与处置可记。\n"
            f"  {method.value} 这一层的每一个出口都带诊断，走到这里说明有人加了一条不带的。"
            "让它落成 ``cause=none, action=proceeded`` 会把「不知道这一次怎么了」"
            "记成「这一次没有任何异常」—— 而报告上看不出这两句话的差别。"
        )
    return diagnosis


def attempt_from(
    settings: Settings,
    request: FetchRequest,
    result: FetchResult,
    *,
    method: MethodKind,
    material: MaterialProvenance,
    started_at: datetime,
    ended_at: datetime,
) -> FetchAttempt:
    """把一份 ``FetchResult`` 收成一条 :class:`FetchAttempt`。

    ## 逐格对应，以及**刻意留空**的那几格

    有生产者的：``attempt_id``（``domain/fetch_attempt.py::attempt_id``）、
    ``scope``／``url``（来自**请求**，不是回执）、``final_url``／``redirect_trace``／
    ``observed_status``／``response_headers``／``error_code``／``body_*``、
    以及诊断那四格（``retry_after_s`` / ``challenge_detected`` / ``suspected_cause`` /
    ``cause_evidence_ref`` / ``action_taken``）。

    ``material`` 来自**抓取器**（与 ``method`` 同一侧，而**不是**与 ``scope``／``url``
    同一侧）：这一次拿到的正文是现场的第三方页面还是我们自己造的，取决于**这一次
    抓取是谁做的** —— 一个替换掉了网络的实现拿到的正是它自己造的材料，而构造
    ``FetchRequest`` 的那个人可能完全不知道这件事（他确实不知道：测试注入
    ``MockTransport`` 的那一处，与生产里构造请求的那一处，是同一段业务代码）。
    所以这一格在这里是一次**转抄**，不是一次判定：一个从回执、或从 URL 形状、或
    从「这看起来像生产配置」去推它的实现，会在某一天把一份自造页面记成现场覆盖，
    而报告上看不出那一天。

    今天**没有生产者**的，写在明处而不是让它们看起来完整：
    ``run_id`` / ``work_item_id`` / ``target_ref``（业务归属随 B17 的 Run 与 WorkItem
    到来）、``egress_profile``（受管出口的档位名随出口层到来）、``parser_version``
    （解析器版本随文档解析那一段到来）。它们取缺省值 —— 而缺省值在这里是**空**。

    ## ``url`` 取请求、``final_url`` 取回执

    两者在重定向之后不同，而这个不同正是这一格存在的理由（附上
    ``redirect_trace``，一条链答得出「中间经过谁」）。``list_attempts`` 的 ``url``
    筛的也是**请求的那个地址** —— 同一个选择，见 ``ports/fetch_attempt.py``。

    ## ``diagnosis`` 为 ``None`` 是**报错**，不是「一切正常」

    这一份收账经过 :func:`require_diagnosis`：本层的每一个出口都带诊断
    （``_refused`` / ``_dns_failure`` / ``_governed`` / ``_completed`` 各一处），
    所以它落在 ``None`` 上说明有人加了一条不产生诊断的出口 —— 而那会记成
    ``cause=none, action=proceeded``。为什么不许、为什么抛的是 ``StoreError``，
    写在那个函数的 docstring 里（两层的收口点共用同一句话）。

    ## ``method`` 是参数，**没有缺省**

    它有两个承重的作用，所以两个抓取器都必须自己说出来：

    1. 它是 :func:`attempt_id` 的种子之一。写死 ``HTTP`` 的实现会让浏览器那一跳
       算出一个**HTTP 尝试的 id** —— 而那个 id 与真正的 HTTP 尝试在同一个命名空间
       里，两条不同的尝试从此可能撞成一条（``put_attempt`` 对「同 id 不同内容」是
       报错，于是撞上的那一对会以一次落账失败的形式暴露，而不是静默合并；
       但那已经是一次错误了）。
    2. 它让「这一跳是用什么方法拿的」在账上可读 —— §5 的替代策略回执要的正是
       这一格（降级到浏览器之后，报告上要能看出**这一次真的走了浏览器**）。

    没有缺省值是刻意的：缺省值会让「这一层判定过」与「这一层没想过」在盘上同形。

    ## 预算那两格**不再是参数**，也不是缺省

    ``budget_reservation_id`` / ``budget_settlement`` 由 ``request.reservation_id``
    推出来（``domain/fetch_attempt.py::settlement_of``）：押着一份预留就写
    ``RESERVED`` 并记下它的名字，没押就写 ``NOT_APPLICABLE`` 且什么都不指。

    它此前是**参数**，两个抓取器各自写死 ``NOT_APPLICABLE`` —— 写死的理由是当时
    「抓取这一层不消费计费额度」这句话为真。那句话今天仍然为真，但**「这一次请求
    押不押着一份预留」与它无关**：``application/refresh.py::_recheck_page`` 每一页
    都先 ``reserve`` 再抓，于是那一页的抓取尝试**真的**是在一份预留之下发生的，
    而账上那句写死的「我没花钱」把它记成了没有。这一格的信息量因此不在于谁记得改
    参数，而在于**判据本来就该从请求上读**：变成推导之后，两个抓取器不可能各判
    各的，也不存在「谁忘了改这一格」。

    那个「将来要改的正是调用点」的变更点因此**搬进了一个函数**：真正需要它的是
    一个有计费的抓取出口，而它要加的是一个**新的输入**（对方扣了没有），不是某一
    个调用点上的一句新常量。完整论证写在 ``settlement_of`` 的 docstring 上。

    ## ``material`` 也没有缺省，而这一处缺省的是**同一个值**

    字段的缺省是 ``SYNTHETIC``（收回一个主张，理由写在
    ``domain/fetch_attempt.py::MaterialProvenance`` 上），让它同时当参数的缺省
    看起来是免费的。它不是：

    缺省值在这里**不会**把自造的材料记成现场覆盖（那个方向是安全的方向），
    但它会**吞掉抓取器已经做出、并且已经写在自己身上的那个声明** —— 一个忘了
    转抄 ``self.material`` 的实现从此把一次真出网的抓取记成自造材料，而两条
    路径在盘上同形。这一格的整个用途就是让「现场」与「自造」在报告上分得开，
    所以「没人说过」与「这是自造的」在盘上必须是两句不同的话：前者在这里是
    ``TypeError``，后者是一条写着 ``synthetic`` 的账。
    """

    diagnosis = require_diagnosis(result, method=method)
    # 那一格从**请求**上读，不从回执上推：回执答的是「这一跳怎么样了」，而「这一次
    # 抓取押着哪一份额度」是请求的性质 —— 它在那次调用里根本没被观测过。
    settlement = settlement_of(request.reservation_id)
    return FetchAttempt(
        attempt_id=attempt_id(
            workspace=settings.profile,
            scope=request.scope,
            method=method,
            url=request.url,
            started_at=started_at,
        ),
        scope=request.scope,
        workspace=settings.profile,
        method=method,
        url=request.url,
        final_url=result.final_url,
        redirect_trace=tuple(result.redirect_trace),
        started_at=started_at,
        ended_at=ended_at,
        observed_status=result.status_code,
        response_headers=dict(result.response_headers),
        error_code=result.error,
        material=material,
        body_kind=(
            body_kind(result.content_type)
            if result.body_sha256 and result.body_bytes > 0
            else BodyKind.NONE
        ),
        body_bytes=result.body_bytes,
        body_sha256=result.body_sha256,
        budget_reservation_id=settlement.reservation_id,
        budget_settlement=settlement.state,
        retry_after_s=diagnosis.retry_after_s,
        challenge_detected=diagnosis.challenge_detected,
        suspected_cause=diagnosis.cause,
        cause_evidence_ref=diagnosis.cause_evidence_ref,
        action_taken=diagnosis.action,
    )
