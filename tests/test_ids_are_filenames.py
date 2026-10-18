"""id → 文件名必须是**一一映射**，而判据与界都只有一处。

## 这里守的是什么

这个仓库里有一类字段，它们的值会被拼成文件名或目录名：``account_id``
（``<account_id>.jsonl``、``mirrors/<account_id>/``）、``run_id``
（``benchmarks/<run_id>.json``）、``SearchQuery.scope``、``FetchRequest.scope``。

对它们来说，``slugify`` 必须是一个**恒等函数**。一旦不是，两个不同的 id 会落到
同一个名字上，而**没有任何一层会报错**：台账行数不变、目录内容变多、报告上只显示
「证据更多了」。这不是一次崩溃，是一次静默合并 —— 它比崩溃难查得多。

所以判据是「值必须是 ``slugify`` 的不动点」（``domain/util.check_canonical_id``），
处置是**拒绝**而不是「落盘时规范化」：规范化看起来更友好，但它把一次错误变成了
一次静默改写，而调用方永远不会知道自己的 id 被换了一个。

## 三条判据，三条都有执行器

1. **判据本身**：不动点。接受一切已经是文件名的值，拒绝一切会被改写的值。
2. **界只有一个定义处**：``max_length`` 不得以整数字面量出现在 ``domain/util.py``
   之外的任何地方。这一条是本次收口的根因 —— 同一个 id 空间里曾经有 40（检索镜像）、
   60（账号证据）、80（run 报告）三个界，而「按 40 落盘、按 60 读」在纸面上看不出来。
3. **判据被共享**：带 ``account_id`` 的每一个模型都拒绝**同一个**非规范值。
   一个模型严、一个模型松，就是「同一个字段两种命运」的成因。

## 与 ``tests/test_portability.py`` 的分工

那个文件问的是「这个值能不能安全地当文件名」（不许含路径分隔符）。本文件问的是
「这个值与它落盘时的名字是不是同一个」—— 含空格的 id 过得了前者，过不了后者。
两条判据都留着，因为它们的失败方式不同：前者会写出一个路径，后者会写出一个
**别人的文件**。
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.config import REPO_ROOT
from marketops.domain.account import MarketAccount
from marketops.domain.bind import BindingSubject, RejectedBinding
from marketops.domain.entities import AccountVenueRelation, Person, Role
from marketops.domain.cohort import (
    CohortExclusion,
    CohortExclusionCode,
    CohortMember,
    exclusion_reason,
)
from marketops.domain.enums import (
    AccountStatus,
    BusinessFunction,
    Cohort,
    RoleClass,
    VenueRelation,
)
from marketops.domain.enums import SourceKind
from marketops.domain.evidence import AccountRecord
from marketops.domain.fetch_attempt import FetchAttempt, MethodKind
from marketops.domain.route_records import (
    RouteRejectionCode,
    fold_observation,
)
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import (
    FILENAME_ID_MAX_LENGTH,
    check_canonical_id,
    sha256_text,
    slugify,
)
from marketops.domain.outreach import OutreachStage
from marketops.ports.acquisition import FetchRequest
from marketops.ports.outreach import DraftRequest
from marketops.ports.search import SearchQuery
from tests.conftest import DEFAULT_PROFILE
from tests.test_evidence_store_contract import (
    make_evidence,
    make_observation,
    make_opportunity,
    make_rejection,
    make_route,
)
from tests.test_outreach_store_contract import (
    make_draft as make_outreach_draft,
)
from tests.test_outreach_store_contract import (
    make_entry as make_manifest_entry,
)
from tests.test_outreach_store_contract import (
    make_attempt as make_outbound_attempt,
)
from tests.test_outreach_store_contract import (
    make_family as make_outreach_family,
)
from tests.test_outreach_store_contract import (
    make_policy as make_outreach_policy,
)
from tests.test_outreach_store_contract import (
    make_handoff as make_sales_handoff,
)

SOURCE_ROOT = REPO_ROOT / "marketops"

#: ``max_length`` 允许以字面量出现的**唯一**文件 —— 判据与界的定义处。
RULE_CARRIER = SOURCE_ROOT / "domain" / "util.py"

#: 全仓至少要看到这么多次 ``slugify`` 调用，否则「没有违规」是**扫描范围**失效
#: （``rglob`` 指向了空目录）造出来的假通过。
#:
#: 这里曾经是 5。那个数不是判据，是当时仓库里恰好有几处调用 —— 于是把镜像约定
#: 收进 ``adapters/page_mirror.py``（一处定义）之后，``http.py`` 少了一次调用，
#: 这条正对照就红了，而它红的原因与被判的对象**毫无关系**。
#:
#: 「扫描器本身还能用吗」与「仓库里还有多少处调用」是两件事，所以拆开：前者的
#: 承担者是下面那条**构造输入**的判据（已知答案，不依赖仓库现状），后者只要
#: 非空。
MIN_SLUGIFY_CALLS = 1


# ----------------------------------------------------------------------
# 1. 判据本身：不动点
# ----------------------------------------------------------------------
CANONICAL = (
    "a1",
    "acme-001",
    "discovery-v1-arm-b",
    "run-2026-09-22t10-00-00",
    "a" * FILENAME_ID_MAX_LENGTH,
)

#: 每一个都**必须**被拒绝，而且各自被拒绝的理由不同 —— 这张表刻意不只放一种形态。
REWRITABLE = (
    ("space", "My Account 001"),
    ("case", "Foo"),
    ("slash", "a/b"),
    ("colon", "discovery:arm-b"),
    ("dotdot", ".."),
    ("trailing-dot", "acme."),
    ("over-bound", "a" * (FILENAME_ID_MAX_LENGTH + 1)),
    ("non-ascii", "café-001"),
)


@pytest.mark.parametrize("value", CANONICAL, ids=lambda v: v[:20])
def test_a_canonical_id_comes_back_unchanged(value: str) -> None:
    """规范形式的值过判据之后**逐字节相同**。

    判据若是「规范化之后返回它」，这一条也会绿，而下一条会红 —— 两条一起才
    说明这是一条**恒等**判据。
    """
    assert check_canonical_id(value, "account_id") == value


@pytest.mark.parametrize(("_label", "value"), REWRITABLE, ids=[c for c, _ in REWRITABLE])
def test_a_rewritable_id_is_refused(_label: str, value: str) -> None:
    """会被改写的值一律拒绝，且理由要说清它会被改写成什么。

    **每条都有一个正对照**：先证明 ``slugify`` 真的会改写它。没有这一句，
    表里混进一个本来就是规范形式的值时，这条用例会**因为错误的原因变绿**
    （它被拒绝的原因是别的，比如空值）。
    """
    assert slugify(value, max_length=FILENAME_ID_MAX_LENGTH) != value, (
        f"{value!r} 其实是规范形式，它不该在这张表里 —— 这条用例会因为别的原因变绿"
    )

    with pytest.raises(ValueError) as excinfo:
        check_canonical_id(value, "account_id")

    assert "不是规范形式" in str(excinfo.value)


def test_the_bound_is_the_one_the_judge_uses() -> None:
    """判据的缺省界就是那个唯一的界。

    这一条防的是「改了一处、漏了另一处」：``slugify`` 的缺省是 80，判据若跟着
    用缺省而不是 ``FILENAME_ID_MAX_LENGTH``，一个 61–80 字符的 id 就会通过判据、
    然后在落盘时被截到 60 —— 校验看起来在保护一个它其实没保护的东西。
    """
    boundary = "a" * FILENAME_ID_MAX_LENGTH
    assert check_canonical_id(boundary, "account_id") == boundary
    with pytest.raises(ValueError):
        check_canonical_id(boundary + "a", "account_id")


# ----------------------------------------------------------------------
# 2. 界只有一个定义处
# ----------------------------------------------------------------------
def _literal_max_lengths(path: Path) -> list[tuple[int, int]]:
    """本文件里 ``slugify(..., max_length=<整数字面量>)`` 的出现位置。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "slugify":
            continue
        for keyword in node.keywords:
            if keyword.arg == "max_length" and isinstance(keyword.value, ast.Constant):
                found.append((node.lineno, keyword.value.value))
    return found


def _slugify_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "slugify":
            count += 1
    return count


def test_the_scanner_reads_a_known_module(tmp_path: Path) -> None:
    """正对照之一：扫描器在一份**已知**输入上读得出调用与字面量。

    一条只证明「没有违规」的扫描，在扫描本身失效时同样是绿的 —— 所以正对照的
    输入必须**已知答案**。这里构造一份内容确定的模块，断言两个扫描器都能读懂它：
    ``_slugify_calls`` 数得出 2 次调用，``_literal_max_lengths`` 只挑得出带
    ``max_length=<整数字面量>`` 的那一次。

    此前这一半的承担者是「全仓至少 5 次调用」，而那个数是仓库当时的现状 ——
    一次正当的重构就能让它转红，红的原因又与被判的对象无关。扫描器的正确性与
    仓库里有几处调用是两件事。
    """
    module = tmp_path / "known.py"
    module.write_text(
        "slugify(a)\n"
        "slugify(b, max_length=60)\n"
        "value.slugify(c, max_length=7)\n"
        "other(value, max_length=80)\n",
        encoding="utf-8",
    )

    assert _slugify_calls(module) == 3
    assert _literal_max_lengths(module) == [(2, 60), (3, 7)]


def test_the_scan_sees_the_call_sites() -> None:
    """正对照之二：全仓扫描的**范围**非空 —— ``rglob`` 真的落到了文件上。

    这一半分担的只有「范围」：一个指向空目录的根会让下面那条「没有模块自己钉
    界」在什么都没看的情况下绿掉。
    """
    files = sorted(SOURCE_ROOT.rglob("*.py"))
    calls = sum(_slugify_calls(path) for path in files if "__pycache__" not in path.parts)
    assert calls >= MIN_SLUGIFY_CALLS, (
        f"全仓只扫到 {calls} 次 slugify 调用（期望至少 {MIN_SLUGIFY_CALLS}）—— 扫描范围可能是空的"
    )


def test_no_module_pins_its_own_bound() -> None:
    """``max_length`` 不许以字面量出现在定义处之外。

    同一个 id 空间里三个界（40 / 60 / 80）就是本次收口要修的根因。界本身没有
    对错，**有两个**才是错的：校验按一个数、落盘按另一个数，而纸面上看不出来。
    """
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path == RULE_CARRIER:
            continue
        for lineno, value in _literal_max_lengths(path):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} max_length={value}")

    assert not offenders, (
        "这些地方自己钉了一个界，而不是用 domain/util.FILENAME_ID_MAX_LENGTH：\n  "
        + "\n  ".join(offenders)
        + "\n同一个 id 空间的界只能有一个定义处 —— 两个界必然先漂移一个，"
        "而漂移的那一次会让校验通过的值在落盘时被截断。"
    )


# ----------------------------------------------------------------------
# 3. 判据被共享：同一个值，每一个模型都拒绝
# ----------------------------------------------------------------------
def _build(model: str, account_id: str) -> object:
    """按模型名构造一个实例。**只用测试自己的工厂**，不复制一份构造代码。"""
    if model == "AccountRecord":
        return AccountRecord(account_id=account_id, organisation="X", cohort=Cohort.KNOWN_SUCCESS)
    if model == "Evidence":
        return make_evidence(account_id=account_id)
    if model == "ContactRoute":
        return make_route(account_id=account_id)
    if model == "RouteOpportunity":
        return make_opportunity(account_id=account_id)
    if model == "FetchRequest":
        return FetchRequest(url="https://example.co.uk/", scope=account_id)
    if model == "SearchQuery":
        # 这条用例问的是 ``scope`` 那一格与 ``AccountRecord.account_id`` 是否受同一条
        # 判据，所以市场那两格填测试自己的定值（写法与 profile 的声明一致）。
        return SearchQuery(text="cricket club", scope=account_id, country="GB", language="en")
    if model == "FetchAttempt":
        # 时刻那两格不是这条用例问的东西，所以填测试自己的定值；问的是
        # ``scope`` 那一格与 ``FetchRequest.scope`` 是否受同一条判据。
        return FetchAttempt(
            attempt_id="att-0001",
            scope=account_id,
            workspace=DEFAULT_PROFILE,
            method=MethodKind.HTTP,
            url="https://example.co.uk/",
            started_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
            ended_at=datetime(2026, 9, 27, 0, 0, 1, tzinfo=timezone.utc),
        )
    if model == "AccountVenueRelation":
        # ``workspace`` 与 ``first_seen_at`` 都是必填、``evidence_ids`` 至少一条 ——
        # 见 ``AccountVenueRelation`` 的类文档。这条用例问的只是 ``account_id``
        # 那一格的判据，所以另外三格填测试自己的值。
        return AccountVenueRelation(
            workspace=DEFAULT_PROFILE,
            account_id=account_id,
            venue_asset_id="venue-001",
            relation=VenueRelation.OPERATES,
            evidence_ids=["ev-001"],
            first_seen_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
    if model == "MarketAccount":
        # §6 的账号池行 —— 与 ``AccountVenueRelation`` 逐字同一条纪律（``workspace``
        # 必填、``evidence_ids`` 至少一条，见 ``MarketAccount`` 的模块 docstring）。
        # 这条用例问的只是 ``account_id`` 那一格，其余填测试自己的值。
        return MarketAccount(
            workspace=DEFAULT_PROFILE,
            account_id=account_id,
            organisation="Example Trust",
            evidence_ids=["ev-001"],
            first_seen_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
    if model == "Person":
        # 走 ``for_account`` 而不是直接构造：``person_id`` 是从 account_id 派生的，
        # 绕开派生函数就测不到「这个 id 真的是从那个 account_id 来的」。
        return Person.for_account(account_id=account_id, full_name="A Person")
    if model == "Role":
        return Role.for_account(
            account_id=account_id,
            title="Grounds Manager",
            role_class=RoleClass.DECISION,
            business_function=BusinessFunction.UNKNOWN,
        )
    if model == "SubjectDossier":
        # 身份与前面九个不是同一类东西，但**同一个字段同一条命运**：它进的是任务
        # 包与快照，读它的人会拿它去检索、去对应回盘上的清单行 —— 一个含空格的
        # id 在这里被静默改写，后果与别处一样是「两个人以为在说同一个账号」。
        return SubjectDossier(
            account_id=account_id,
            organisation="X",
            source_ref="tests/test_ids_are_filenames.py",
            input_sha256=sha256_text("row"),
        )
    if model == "OutreachDraft":
        return make_outreach_draft(account_id=account_id)
    if model == "ManifestEntry":
        return make_manifest_entry(account_id=account_id)
    if model == "OutboundAttempt":
        return make_outbound_attempt(account_id=account_id)
    if model == "SalesHandoff":
        return make_sales_handoff(account_id=account_id)
    if model == "BindingSubject":
        # 绑定这一层持有的 ``account_id`` 是**路线与证据的主语**：写进去的是什么，
        # 落库的每一行路线就挂在那一个 id 上。它比清单那一侧松一点都会让路线挂到
        # 一个池子里并不存在的账号上，而报告完全正常。
        return BindingSubject(account_id=account_id, organisation="Example Trust")
    if model == "RejectedBinding":
        # 拒收这一侧的主语。它**不是**「反正要拒了，id 无所谓」：这一行会变成一张
        # 落盘的收据（§6.3），而收据是按账号检索的 —— 松一点的后果是「这个账号的
        # 拒收」比真的多几条，且每一条自己看着都完整。
        return RejectedBinding(
            source_kind=SourceKind.FETCHED_HTML,
            literal_value="someone.else@example.co.uk",
            code=RouteRejectionCode.ENTRY_ELSEWHERE,
            detail="页面上是另一个人",
            account_id=account_id,
            workspace=DEFAULT_PROFILE,
        )
    if model == "RouteObservation":
        return make_observation(account_id=account_id)
    if model == "RouteRejection":
        return make_rejection(account_id=account_id)
    if model == "CanonicalContactRoute":
        # 走 ``fold_observation`` 而不是直接构造：当前读法是**折出来的**，它的
        # ``canonical_key`` / ``revision`` / ``observation_ids`` 都是那一折的产物。
        # 直接填一个投影等于把「这两格是否真的由那条观察决定」这件事测掉。
        observation = make_observation(account_id=account_id)
        return fold_observation(None, observation, at=observation.observed_at).projection
    if model == "CohortMember":
        return CohortMember(
            account_id=account_id,
            organisation="Example Garden Club",
            status=AccountStatus.ELIGIBLE,
        )
    if model == "CohortExclusion":
        # 原因是 ``code`` 的一个纯函数（``exclusion_reason``），所以这里照抄那条
        # 规则生成它 —— 手写一句措辞会让这条用例测到一句文案。
        return CohortExclusion(
            account_id=account_id,
            organisation="Example Garden Club",
            status=AccountStatus.HOLD,
            code=CohortExclusionCode.ON_HOLD,
            reason=exclusion_reason(AccountStatus.HOLD, CohortExclusionCode.ON_HOLD),
        )
    if model == "DraftRequest":
        # 它是**写作者的输入**，是 id 从账号一路走到「信是谁的」的最后一段。
        return DraftRequest(
            account_id=account_id,
            organisation="Example Cricket Club",
            family=make_outreach_family(),
            policy=make_outreach_policy(),
            stage=OutreachStage.STAGE_1,
            locale="en-GB",
        )
    raise AssertionError(f"没有为 {model} 准备构造方式 —— 新模型要一并加进来")


#: 带 ``account_id`` 的每一个模型。新加一个就加进来 —— 一条路径漏掉，
#: 就是「同一个字段两种命运」。
#:
#: ``FetchRequest`` 与 ``SearchQuery`` **不在这张清单上**，它们在
#: :data:`SCOPED_CARRIERS` 上：这两个字段的名字是 ``scope`` 而不是
#: ``account_id``（理由见 ``ports/search.py::SearchQuery``），而它们受的是
#: **同一条判据**。两张清单合成一组跑下面的参数化用例，所以「换个字段名"绕开
#: 这条判据」不成立。
CARRIERS = (
    "AccountRecord",
    "Evidence",
    "ContactRoute",
    "RouteOpportunity",
    "AccountVenueRelation",
    # §6 的账号池行。它记的是**同一个** ``account_id``（与 ``AccountRecord`` 同一个
    # id 空间）—— 放宽这一处，池子里那一行指向的账号与清单里那条会变成两个 id，
    # 而「这个账号在池子里吗」就再没有答案。
    "MarketAccount",
    "Person",
    "Role",
    "SubjectDossier",
    # 外向链上的四个。它们与前面十个**不是同一类东西** —— 这几个 id 不被当作
    # 文件名用（清单、尝试、交接都进 SQLite，不进按 id 命名的目录）。它们在这里，
    # 是因为**同一个字段必须共享同一条判据**：这四处记的 ``account_id`` 指的是
    # ``AccountRecord.account_id`` 那一个值。放宽任意一处，清单上那一行指向的账号
    # 就与账号记录里那条变成两个不同的 id，而两处都不会报错 —— 发送侧按账号查
    # 历史时查不到，于是同一个人在两批里各收到一封。
    "OutreachDraft",
    "ManifestEntry",
    "OutboundAttempt",
    "SalesHandoff",
    # 写作者的输入。它不在库里，但那个字段指的是**同一个** ``account_id`` ——
    # 放宽它，写作者看到的账号与发送侧按账号查到的历史就不是同一个人。
    "DraftRequest",
    # 绑定主语。它与 ``AccountRecord`` / ``MarketAccount`` 是**同一个 id 空间**
    # （两个 ``from_*`` 投影点各从其中一个取 ``account_id``，逐字转交、不改写）——
    # 这一层放松的后果与上面每一条同形：路线挂到一个不存在的账号上，而不报错。
    "BindingSubject",
    # §6 那一层的四个。前三个是**路线自己**（历史、当前读法、拒收收据），第四个是
    # 绑定层交出去的那条判词 —— 后面那个本来是 ``BindingSubject`` 的伴生对象，
    # 一旦它比主语松，"这条拒收是关于哪个账号的" 就有了两个答案，而其中一个是错的。
    #
    # 它们的 ``account_id`` 还有一处与上面每一条不同：这些值会被**按账号检索**
    # （``list_routes`` / ``list_route_observations`` / 拒收分布），所以一个含空格的
    # id 在这里的后果不是「文件重名」，是「按这个账号查，查出来的比真的少」——
    # 同一类静默，换了一个出口。
    "RouteObservation",
    "CanonicalContactRoute",
    "RouteRejection",
    "RejectedBinding",
    # §B12 那两份名单上的两行。它们与 §6 那一组同型（进 SQLite，不按 id 命名文件），
    # 而它们的 ``account_id`` 同样**会被按账号检索**：``get_cohort`` 的读回就是拿它
    # 去池子里对「这个成员还在不在」。放宽它，那条读回会把一个规范化的 id 拿去查，
    # 查不到 —— 而那在报告上长得像「这个账号已经不在池子里了」，一条**业务结论**。
    "CohortMember",
    "CohortExclusion",
)

#: 字段叫 ``scope`` 的那两个。它们与上面那张清单**受同一条判据**，所以下面两组
#: 参数化用例跑的是两张清单的并集 —— 分开登记只是为了「清单与代码一致」那一条
#: 能分别去问对的那个字段名。
SCOPED_CARRIERS = (
    "FetchRequest",
    "SearchQuery",
    # 一次抓取的**实测记录**。它不在上面那张 ``not_filenames`` 具名清单里，理由
    # 正是那张清单的反面：它的 ``scope`` 与 ``FetchRequest.scope`` 是**同一个
    # 字符串**（后者逐字成为 ``Evidence.account_id`` 与页面镜像目录名），不是
    # ``Reservation.scope`` 那种「预算账本的一格」。两处各判一遍而判得不一样的那
    # 一天，同一次抓取在尝试账与证据账上会有两个拼法。
    "FetchAttempt",
)


@pytest.mark.parametrize("model", CARRIERS + SCOPED_CARRIERS)
def test_every_model_that_carries_an_account_id_shares_the_judge(model: str) -> None:
    """同一个非规范值，每一个模型给出**同一种**回答。

    分成多条而不是一条，是为了在漏掉一个模型时能直接看出是哪一个；用同一个
    值，是为了让「谁更严」这个问题有一个可执行答案。
    """
    with pytest.raises(ValueError) as excinfo:
        _build(model, "My Account 001")

    assert "不是规范形式" in str(excinfo.value), (
        f"{model} 没有用共享判据，或用了别的理由拒绝 —— 拒绝理由不一致时，"
        "调用方会以为自己写错了别的东西"
    )


@pytest.mark.parametrize("model", CARRIERS + SCOPED_CARRIERS)
def test_every_model_that_carries_an_account_id_accepts_a_canonical_one(model: str) -> None:
    """正对照：同一个模型对规范值**不**报错。

    没有这一条，上一组在「所有模型都因为别的原因构造失败」时也是绿的。
    """
    assert _build(model, "acme-001") is not None


def _models_declaring(field: str) -> set[str]:
    """AST 扫出 ``domain/`` 与 ``ports/`` 里声明了 ``field`` 字段的类。

    两张清单的**来源**，不是它们的复述：清单靠人维护，这一条读代码。两者不一致
    就红 —— 否则「新加一个模型忘了加进来」会表现为「新增的模型不受任何判据管」，
    而那看起来与「它没问题」一模一样。
    """
    declared: set[str] = set()
    for directory in (SOURCE_ROOT / "domain", SOURCE_ROOT / "ports"):
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for statement in node.body:
                    target = getattr(statement, "target", None)
                    if (
                        isinstance(statement, ast.AnnAssign)
                        and isinstance(target, ast.Name)
                        and target.id == field
                    ):
                        declared.add(node.name)
    return declared


def test_the_carrier_list_matches_what_the_code_declares() -> None:
    """清单与代码一致。不一致时给出两个方向的差集，直接说清该加还是该删。"""
    declared = _models_declaring("account_id")
    listed = set(CARRIERS)

    missing = sorted(declared - listed)
    stale = sorted(listed - declared)

    assert not missing, (
        f"这些模型声明了 account_id 但没进 CARRIERS：{missing} —— "
        "它们不受本文件的任何一条判据管，而「不受管」与「没问题」长得一样"
    )
    assert not stale, (
        f"CARRIERS 里这些名字在代码里找不到对应字段了：{stale} —— "
        "删掉类却留着清单，会让这条对照在缩水之后仍然是绿的"
    )


def test_the_scoped_carrier_list_names_models_that_still_declare_a_scope() -> None:
    """``scope`` 那张清单的**两个方向**，与上一条同一个形状。

    它不能只查「清单里的人是不是还声明着 ``scope``」：``domain/`` 与 ``ports/``
    里另有 ``Reservation.scope`` 与来源回执的 ``scope``，它们记的是**别的东西**
    （预算账本的一格、上游交来的范围），本来就不该按文件名判。所以正向那一半是
    有边界的 —— 而边界写在下面这张具名清单上，不是靠「名字里有没有 scope」猜。

    反向那一半没有边界问题：清单里每一个名字都必须真的声明着 ``scope``。少了
    这一半，一个被改回 ``account_id`` 的模型会从两张清单里**同时**掉出去，而
    那条对照只会说「CARRIERS 少了它」，读起来像「删掉一个类」。
    """
    #: 声明了 ``scope`` 但**不是**文件名的那几个，逐个点名。加一个就加进来。
    not_filenames = {
        "Reservation",  # 预算账本的一格：``BudgetLedger`` 的主键，不落成目录
        "SourceReceipt",  # 上游交来的范围，进 SQLite，不落成目录
    }
    declared = _models_declaring("scope")
    listed = set(SCOPED_CARRIERS)

    unaccounted = sorted(declared - listed - not_filenames)
    assert not unaccounted, (
        f"这些模型声明了 scope 却既不在 SCOPED_CARRIERS、也不在那张具名清单里："
        f"{unaccounted} —— 「没想过」与「想了之后决定不管」在这里必须分得开"
    )
    stale = sorted(listed - declared)
    assert not stale, (
        f"SCOPED_CARRIERS 里这些名字在代码里找不到 scope 字段了：{stale} —— "
        "它可能被改回了 account_id，那时它会从两张清单上一起消失"
    )


def test_an_unattributed_fetch_is_not_the_same_as_an_empty_id() -> None:
    """``FetchRequest`` 的空 ``scope`` 是合法的一种取值，不是漏填。

    它的含义是「这次抓取不属于任何主体」（镜像落进 ``unnamed``）。把它当成
    错误会让「没有归属的抓取」无法表达，而那个能力是抓取层的入口契约
    （``FetchRequest`` 的缺省值就是空）—— 逼出一个编造的 id 只会更糟。
    """
    assert FetchRequest(url="https://example.co.uk/").scope == ""
    assert FetchRequest(url="https://example.co.uk/", scope="").scope == ""
