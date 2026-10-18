"""``application/contact_routes`` —— 路线与路线机会的**唯一**一趟生产者。

这个文件守的是 §4 那一条：``submit_findings`` 与 CLI 必须走**同一趟**，而那一趟
自己必须扛得住一份坏材料。四件事各有一组测试：

* **一个坏页面只影响它自己** —— §5 的 fail-soft。它有两种，而这两种的处置完全不同，
  所以下面分开测、分开计数：
  * 页面**绑得上**但时刻不成立（写着一个还没到的发布日期）⇒ 那一行落库，
    结论是 ``invalid_source_time``；
  * 页面**绑不上**（来源元数据畸形，构造不出一行合法的证据）⇒ 那一份作废，
    一行都不落，理由是判据原文。
* **结论与落盘是一致的** —— 判过时效的那一行就是落盘的那一行（``rebuild`` 而不是
  ``model_copy``）。
* **账号的域只有一个来源** —— ``BindingSubject.domains``。收一个 ``AccountRecord``
  会让池子里的账号（``MarketAccount``，``domains`` 是复数）喂一个 ``None`` 进来，
  于是同一条路线在两个入口下判出不同的等级，而报告上只是「多了几个 X」。

页面文本一律用 ``example.co.uk``：真实目标与真实抓取结果都不在仓库里。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketops.adapters.memory import InMemoryMaintenanceStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.contact_routes import (
    MaterialStatus,
    RouteContext,
    RouteMaterial,
    produce_routes,
)
from marketops.domain.bind import BindingSubject, RouteVocabulary
from marketops.domain.enums import Cohort, Grade, GradeReason, RoleClass, SourceKind
from marketops.domain.maintenance import Aspect
from marketops.domain.temporal import Currentness, FreshnessReason
from marketops.domain.util import utc_now
from tests.conftest import DEFAULT_PROFILE

WORKSPACE = DEFAULT_PROFILE

FETCHED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

#: 夹具默认的那一趟运行。真实取值由 ``pipeline._derived_run_id()`` 现算，或者由
#: ``run()`` 一路传下来 —— 这里给一个**看得出是测试的**值，免得一条回执上的
#: 「哪一趟」看起来像真的。
RUN_ID = "run-fixture"

#: 只给这一个词 —— 词表是 profile 的输入，不是绑定层内建的业务知识。
VOCAB = RouteVocabulary(
    matchable_role_terms=("grounds manager", "head groundskeeper"),
    role_classes={"grounds manager": RoleClass.OPERATIONAL},
)

NAMED_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@example.co.uk</p>
</body></html>
"""

#: 同一条链路的另一页 —— 换一个具名人，于是它是**另一份材料**（证据 id 是内容
#: 寻址的，同一个 id 配不同内容会当场被存储拒掉）。多材料测试需要两份**真的不同**
#: 的材料，否则测的是「同一条证据写两遍」。
OTHER_NAMED_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Head Groundskeeper: Sam Reed</p>
  <p>Email: sam.reed@example.co.uk</p>
</body></html>
"""

#: 同一页面上写着**还没到**的发布时刻。真实世界里它来自排期、模板变量或被解析错的
#: 年份 —— 而在本仓库里它曾经是一条异常，从判据里穿出来打死整轮研究。
FUTURE_DATED_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Published: 2027-01-01</p>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@example.co.uk</p>
</body></html>
"""


def _store(settings) -> SqliteEvidenceStore:
    """用**主存**，不用内存替身。

    这个用例的产出要落盘，而落盘那一半（行级判据过不过、幂等对不对）在替身上
    测不出真东西 —— 见 ``tests/test_pipeline.py::_store`` 的同一条说明。
    """
    return SqliteEvidenceStore(settings.database_path)


def _material(text: str, *, origin: str = "page-a", **overrides) -> RouteMaterial:
    payload = {
        "workspace": WORKSPACE,
        "subject": BindingSubject(
            account_id="example-coatings",
            organisation="Example Coatings Ltd",
            domains=("example.co.uk",),
        ),
        "source_url": "https://example.co.uk/contact",
        "source_kind": SourceKind.FETCHED_HTML,
        "text": text,
        "content_sha256": "abc123",
        "fetched_at": FETCHED_AT,
        "parser": "html",
        "origin": origin,
    }
    payload.update(overrides)
    return RouteMaterial(**payload)


def _produce(settings, bundle, *materials, now=None, maintenance=None, store=None, context=None):
    """跑一趟。``maintenance=`` / ``store=`` / ``context=`` 供那些要**拿着账本或
    回执看**的测试覆盖。

    缺省给一本**内存**维护账：这一趟在真的落下新证据行时会往里写一条变更信号
    （§6），而大多数判据不关心它 —— 但 ``produce_routes`` 上这一格没有缺省，
    所以每一条判据都得把它写出来。

    ``context`` 同理没有缺省（它是 §6.3 拒收回执上「这是哪一趟」的来源），所以这里
    给一个**命名清楚的**测试取值，而不是让每条判据各写一个 —— 一条回执说不说得出
    自己属于哪一趟，正是 :func:`test_a_rejection_receipt_says_which_run_it_belongs_to`
    要问的，而它自己传一个值。
    """
    return produce_routes(
        materials=list(materials),
        bundle=bundle,
        store=store if store is not None else _store(settings),
        maintenance=(
            maintenance if maintenance is not None else InMemoryMaintenanceStore()
        ),
        vocabulary=VOCAB,
        context=context if context is not None else RouteContext(run_id=RUN_ID),
        now=now,
    )


# --- 一条材料走完整趟 ----------------------------------------------------------


def test_a_named_page_becomes_a_route_and_lands_in_the_store(settings, bundle) -> None:
    """绑定 → 判时效 → 定级 → 落盘，四步都发生过，且库里读得回来。"""
    production = _produce(settings, bundle, _material(NAMED_PAGE))

    assert production.outcomes[0].status is MaterialStatus.BOUND
    assert production.outcomes[0].routes == 1
    assert production.outcomes[0].evidence > 0

    route = production.routes[0]
    assert route.email == "jane.doe@example.co.uk"
    assert route.grade is Grade.B
    assert route.grade_reason is GradeReason.RULE_MATCHED

    # 落盘的那一行与返回的那一行**内容一致** —— 两格（时效、等级）各自都过了
    # 一次行级判据才落到库里。
    #
    # 比的是 ``.route``：库里读回来的是**当前投影**（规范键 + 折叠账 + 那一格事实），
    # 而这一趟交出去的是那一格事实本身。拿投影整体去比，比的就是一条这里根本没
    # 产生的折叠账，红出来的原因与「内容一致吗」无关。
    stored = _store(settings).list_routes("example-coatings")
    assert len(stored) == 1
    assert stored[0].route.model_dump() == route.model_dump()


def test_an_empty_page_binds_and_produces_nothing(settings, bundle) -> None:
    """绑成了、什么都没有 —— 这与「没绑成」是两件事，报告上要分得开。"""
    production = _produce(settings, bundle, _material("<html><body>hello</body></html>"))

    assert production.routes == ()
    assert production.opportunities == ()
    assert production.outcomes[0].status is MaterialStatus.BOUND
    assert production.outcomes[0].reason == ""


def test_a_route_without_a_person_is_an_opportunity_not_a_route(settings, bundle) -> None:
    """SOP L126：没有字面邮箱就不是一条路线。有角色没有邮箱产出的是机会。"""
    production = _produce(
        settings,
        bundle,
        _material("<html><body><p>Grounds Manager: Jane Doe</p></body></html>"),
    )

    assert production.routes == ()
    assert len(production.opportunities) == 1
    assert production.outcomes[0].opportunities == 1


# --- §5 之一：时刻不成立的**绑得上**，结论落在那一行上 ------------------------


def test_a_future_publish_time_is_a_conclusion_not_a_crash(settings, bundle) -> None:
    """未来日期落成 ``invalid_source_time``，**不抛、不钳、不丢**。

    这是 §5 要的判词逐字：``status = invalid_source_time``，
    ``reason = source_published_at > observed_at``。

    三种偷懒的修法各自错在哪，写在这里以免下一个人再想一遍：

    * 抛出去让调用点接 —— 那一页还是会让整轮停（只不过停在一个更好的位置）；
    * 静默丢掉这一行 —— 报告上少一行，看不出为什么；
    * 把日期钳到今天 —— 最坏的一种：一行算不出年纪的证据会变成刚刚验证过的。
    """
    production = _produce(settings, bundle, _material(FUTURE_DATED_PAGE))

    assert production.outcomes[0].status is MaterialStatus.BOUND, "它绑得上"
    assert len(production.routes) == 1

    route = production.routes[0]
    assert route.freshness is not None
    assert route.freshness.currentness is Currentness.INVALID_SOURCE_TIME
    assert route.freshness.reason is FreshnessReason.INVALID_SOURCE_TIME
    assert route.freshness.is_actionable is False
    assert route.freshness.age_days is None, "算不出的年纪不许拿一个负数顶上"
    assert route.source_published_at == datetime(2027, 1, 1, tzinfo=timezone.utc), (
        "日期**原样留着** —— 钳过的日期会让这一行看起来是一份正常材料"
    )

    # 而它照常落库：作废的是「这条事实今天算不算数」，不是「这一行存不存」。
    stored = _store(settings).list_routes("example-coatings")
    assert stored[0].route.freshness is not None
    assert stored[0].route.freshness.currentness is Currentness.INVALID_SOURCE_TIME


def test_one_bad_page_does_not_kill_the_batch(settings, bundle) -> None:
    """含坏页面的那一批照常跑完，**其余材料一份不少**。

    这条测试的全部价值在它的排布：坏材料排在**中间**。排在最后的话，一个「遇到
    坏的就把后面全丢掉」的实现也能通过 —— 而那正是 fail-soft 要防的那一半。
    """
    production = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, origin="ok-1"),
        # 换一个 URL：同一个 URL 配两份不同的字节是一份自相矛盾的材料，
        # 存储会（正确地）拒掉它 —— 那就不是在测 fail-soft 了。
        _material(FUTURE_DATED_PAGE, origin="bad", source_url="https://example.co.uk/team"),
        _material(OTHER_NAMED_PAGE, origin="ok-2"),
    )

    assert [item.origin for item in production.outcomes] == ["ok-1", "bad", "ok-2"]
    assert all(item.status is MaterialStatus.BOUND for item in production.outcomes)
    assert len(production.routes) == 3, "三份材料各出一条，谁都没被带走"

    # 三条路线里只有一条的时刻不成立 —— 而且报告里数得出来是哪一条。
    verdicts = [
        route.freshness.reason for route in production.routes if route.freshness is not None
    ]
    assert verdicts.count(FreshnessReason.INVALID_SOURCE_TIME) == 1


# --- §5 之二：来源元数据畸形的那一份**绑不上**，一行都不落 --------------------


def test_a_malformed_material_is_rejected_without_taking_the_batch(settings, bundle) -> None:
    """畸形来源元数据 ⇒ 那一份作废、留判据原文、**其余材料照常**。

    这里用的畸形值是：``fetched_at`` **不带时区**。它是适配器最容易犯的那个错
    （一个 naive 时间戳看起来很正规），而它会一路走到证据的判据上才被拒 ——
    拒的地方在 ``bind_from_text`` 里面，也就是说，在 ``produce_routes`` 之前，
    这是一条从判据里穿出来的 ``ValidationError``，会把整轮研究打死在一个页面上。
    """
    naive = datetime(2026, 9, 15, 12, 0)
    production = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, origin="ok-1"),
        _material(OTHER_NAMED_PAGE, origin="bad", fetched_at=naive),
    )

    statuses = {item.origin: item.status for item in production.outcomes}
    assert statuses["ok-1"] is MaterialStatus.BOUND
    assert statuses["bad"] is MaterialStatus.INVALID_MATERIAL

    bad = production.invalid_materials[0]
    assert bad.routes == 0
    assert bad.evidence == 0
    assert "tzinfo" in bad.reason or "时区" in bad.reason, (
        "理由要给判据原文（点名是哪一格、为什么），不是一句「这一页有问题」"
    )


def test_a_rejected_material_writes_nothing(settings, bundle) -> None:
    """被拒的材料**一行都不落** —— 包括它那份本来会写进去的证据。

    中途失败而证据已经落盘，会让「这份材料被拒了」与「它的一部分留下了」同时
    成立；下一次重跑读到那些证据时，没人说得清它们是哪一次来的。
    """
    store = _store(settings)
    produce_routes(
        materials=[_material(NAMED_PAGE, fetched_at=datetime(2026, 9, 15, 12, 0))],
        bundle=bundle,
        store=store,
        maintenance=InMemoryMaintenanceStore(),
        vocabulary=VOCAB,
        context=RouteContext(run_id=RUN_ID),
    )

    assert store.list_routes("example-coatings") == []
    assert store.list_opportunities("example-coatings") == []


def test_the_two_failures_are_counted_apart(settings, bundle) -> None:
    """「时刻不成立」与「材料不合法」进的不是同一个计数器 —— 修的是两处。

    前者要去看**页面写了什么**（多半是发布日期被解析错了），后者要去看**适配器
    给了什么**。合成一个数，「这一轮丢了几页」就分不出该修哪一头。
    """
    from marketops.application.metrics import AccountMetrics

    metrics = AccountMetrics(account_id="example-coatings", cohort=Cohort.KNOWN_SUCCESS)

    production = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, fetched_at=datetime(2026, 9, 15, 12, 0)),
    )
    metrics.invalid_materials += len(production.invalid_materials)
    for judgement in production.judgements:
        metrics.record_freshness(judgement)

    assert metrics.invalid_materials == 1
    assert metrics.parse_failures == 0, "这不是解析失败 —— 文本解析得好好的"
    assert metrics.freshness_judged == 0
    assert metrics.freshness_without_a_time_anchor == 0, (
        "材料不合法时判据压根没开工，两件事都要分开数"
    )
    assert metrics.to_dict()["invalid_materials"] == 1


# --- 结论写回的那一行，就是落盘的那一行 ----------------------------------------


def test_every_produced_row_gets_exactly_one_judgement(settings, bundle) -> None:
    """每一行产出都有一次判定被记下来 —— 报告里的分布是按这个数算的。

    两格分别 ``rebuild`` 两次会得到两行，而落盘的只有第二行：库里那一行的
    ``freshness`` 来自第一次重建、``grade`` 来自第二次，两者各自的判据都没被
    完整复核过。所以这里比的是**数量对上**，加上断言 id 与行自己算出的那个一致。
    """
    production = _produce(settings, bundle, _material(NAMED_PAGE), _material(OTHER_NAMED_PAGE))

    assert len(production.judgements) == len(production.routes) + len(production.opportunities)
    for route in production.routes:
        assert route.freshness is not None
        assert route.freshness.assertion_id == route.expected_assertion_id, (
            "结论挂的是这一行自己算出的那个断言 id，否则行级判据会红"
        )


def test_the_batch_shares_one_judgement_instant(settings, bundle) -> None:
    """同一个 ``now`` 传进去时整批共用 —— 同一批产出里的 ``evaluated_at`` 可比。"""
    now = utc_now()
    production = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, origin="a"),
        _material(OTHER_NAMED_PAGE, origin="b"),
        now=now,
    )

    stamps = {route.freshness.evaluated_at for route in production.routes if route.freshness}
    assert stamps == {now}


# --- 账号的域只有一个来源 ------------------------------------------------------


def test_the_account_domains_come_from_the_subject(settings, bundle) -> None:
    """定级读的域来自 ``BindingSubject.domains``，所以池子里的账号与清单里的账号同判。

    这条测试的判据是**两次跑出同一个等级**：同一个组织，一次按单域投影、一次按
    多域投影（池子里的 ``MarketAccount`` 就是复数），等级必须一样。第二格多出来的
    那个不相干的域不该改变任何东西 —— 它是**别**的组织的主站。
    """
    from_one = _produce(settings, bundle, _material(NAMED_PAGE))
    from_two = _produce(
        settings,
        bundle,
        _material(
            NAMED_PAGE,
            subject=BindingSubject(
                account_id="example-coatings",
                organisation="Example Coatings Ltd",
                domains=("example.co.uk", "subsidiary.example"),
            ),
        ),
    )

    assert from_one.routes[0].grade is Grade.B
    assert from_two.routes[0].grade is from_one.routes[0].grade


def test_an_unconfirmed_domain_never_lets_a_clause_pass(settings, bundle) -> None:
    """账号的域**没确认**（空集）与域**不符**在结果上同形，理由也同一条。

    未确认不得让任何子句成立 —— 与本仓其余各处同一条。这里直接问定级那一层，
    因为「空集」到不了 ``produce_routes``：池子里的 ``MarketAccount`` 至少有一个域，
    而手写清单里的 ``AccountRecord`` 允许没有。
    """
    from marketops.application.grading import grade_route

    from_unconfirmed = _produce(
        settings,
        bundle,
        _material(
            NAMED_PAGE,
            subject=BindingSubject(
                account_id="example-coatings", organisation="Example Coatings Ltd"
            ),
        ),
    )

    route = from_unconfirmed.routes[0]
    assert grade_route(route, bundle, account_domains=()).grade is Grade.B, (
        "这条路线不靠域命中任何等级 —— 它靠的是有邮箱、有角色"
    )
    assert (
        grade_route(route, bundle, account_domains=("example.co.uk",)).grade
        is grade_route(route, bundle, account_domains=()).grade
    )


# --- 拒绝数不是装饰 ------------------------------------------------------------


def test_the_production_reports_nothing_rejected_for_a_clean_page(settings, bundle) -> None:
    """没被拒 ≠ 没在看。这一条防的是把「拒绝数」变成永远为 0 的装饰。"""
    clean = _produce(settings, bundle, _material(NAMED_PAGE))
    snippet = _produce(
        settings,
        bundle,
        _material(
            "<p>Trade enquiries: sales@example.co.uk</p>",
            source_kind=SourceKind.SEARCH_SNIPPET,
        ),
    )

    assert clean.rejected == ()
    assert clean.snippet_bindings_rejected == 0
    assert snippet.routes == ()
    assert snippet.snippet_bindings_rejected == 1


@pytest.mark.parametrize("text", [NAMED_PAGE, FUTURE_DATED_PAGE, OTHER_NAMED_PAGE])
def test_no_route_is_ever_produced_without_evidence(settings, bundle, text) -> None:
    """每一行产出都要有证据撑着 —— 空证据的路线在定级时就是 X（SOP L119）。"""
    production = _produce(settings, bundle, _material(text))

    for route in production.routes:
        assert route.evidence_ids, "落盘的路线必须带 evidence id"


def test_a_rejection_receipt_says_which_run_it_belongs_to(settings, bundle) -> None:
    """§6.3 的那几格记账：**哪一趟、哪一版规则、什么时候、哪个账号**。

    这四格是这一版**加上去的**（绑定层交出来的那条判词里没有它们，也不该有 ——
    它不知道本次是谁在跑）。所以这条判据问的不是「收据里有没有理由」——那是有理由
    之前就有的 —— 而是「一条回执能不能回答『这是哪一趟拒的』」。

    三个值一起钉：

    * ``run_id`` 取自这一趟传下来的 :class:`RouteContext`，**不是**现算的。现算的
      实现（``utc_now()`` 或另起一个 id）在同一次运行里会给每一条拒收发一个不同的
      「哪一趟」，而报告上那只是「多了几个 run」；
    * ``work_item_id`` 同理，且它**可以是空串** —— 「没有这件工作」与「这一趟」是
      两句不同的话；
    * ``rule`` 取的是 ``bundle.scene_version``：拒收的判据来自 profile 那批字节，
      而这一格是「那批字节是哪一批」的机器可读答案。断言它**等于**那一版，而不是
      「非空」—— 后者在一个抄了 SOP 条款号的实现上也成立，而条款号会在改版时过期。
    """
    context = RouteContext(run_id="run-from-the-test", work_item_id="wi-42")
    production = _produce(
        settings,
        bundle,
        _material(
            "<p>Trade enquiries: sales@example.co.uk</p>",
            source_kind=SourceKind.SEARCH_SNIPPET,
        ),
        context=context,
    )

    (receipt,) = production.rejections
    assert receipt.account_id == "example-coatings"
    assert receipt.candidate == "sales@example.co.uk", "收据上留的是候选**原文**"
    assert receipt.run_id == context.run_id
    assert receipt.work_item_id == context.work_item_id
    assert receipt.rule == bundle.scene_version
    assert receipt.occurred_at.tzinfo is not None, "时刻不带时区就没有办法对账"

    # 而它**真的**落到了盘上、且按账号读得回来：只落不读的那张表与不做持久化在
    # 报告上同形（见 ``application/route_rejections`` 的模块 docstring）。
    stored = _store(settings).list_route_rejections("example-coatings")
    assert [item.rejection_id for item in stored] == [receipt.rejection_id]


def test_the_same_rejection_in_two_runs_is_one_receipt(settings, bundle) -> None:
    """同一份材料重跑一次：**回执不翻倍**，而两次的「哪一趟」都还在。

    两件要同时成立，而它们看起来互相矛盾：

    * ``rejection_id`` 的原像里**没有** ``run_id`` —— 一条拒收说的是「这一页上的这个
      候选没有被收下」，重看一次是同一件事。把它放进去会让每一轮维护都往表里加一批
      只差一个数字的行，而报告上那是「拒收在涨」；
    * 但「哪一趟」不能因此丢掉。所以第二次跑写的是**同一张**收据（主键相同 ⇒ 覆盖），
      而不是加一行。

    这条判据量的是「两次跑完表里还是一行」。没有它，一个把 ``run_id`` 拼进主键的
    实现会让这条表随着维护轮次无界增长，而每一行自己看着都对。
    """
    text = "<p>Trade enquiries: sales@example.co.uk</p>"
    material = dict(source_kind=SourceKind.SEARCH_SNIPPET)

    first = _produce(settings, bundle, _material(text, **material), context=RouteContext(run_id="run-1"))
    second = _produce(settings, bundle, _material(text, **material), context=RouteContext(run_id="run-2"))

    assert [item.rejection_id for item in second.rejections] == [
        item.rejection_id for item in first.rejections
    ]
    assert [item.run_id for item in second.rejections] == ["run-2"], (
        "第二次跑写下的那一张收据要记的是**第二次**是哪一趟 —— 覆盖之后留着的若是"
        "第一次的 run，报告会把一次刚刚发生的拒收记在上一次运行名下"
    )
    stored = _store(settings).list_route_rejections("example-coatings")
    assert len(stored) == 1, "同一条拒收重跑一次变成两行 —— 那一列会随维护轮次无界增长"


# --- §6：真的添了新来源时，当场给维护账记一条变更信号 --------------------------


def test_new_sources_write_one_change_signal_per_account(settings, bundle) -> None:
    """这一趟**真的**添了新来源 ⇒ 一条变更信号，主语是那个账号，理由是那一批来源。

    这是 §6 要的那条生产路径，而它是 ``put_signal`` 在全仓唯一的生产调用点
    （就在这一趟里）。信号存在的全部理由是**及时性**：复核按周期跑（默认 180 天），
    而一次新研究是一次**事件** —— 不报的话，这一格的来源供给要等满一个周期才会被
    重新数一遍，而报告上什么都看不出来。
    """
    store = _store(settings)
    maintenance = InMemoryMaintenanceStore()
    at = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)

    production = _produce(
        settings, bundle, _material(NAMED_PAGE), now=at, store=store, maintenance=maintenance
    )

    assert production.signals, "这一趟落了新证据行，信号不能是空的"
    assert len(production.signals) == 1
    signal = production.signals[0]
    assert signal.subject_kind == "account"
    assert signal.subject_id == "example-coatings"
    assert signal.workspace == WORKSPACE
    assert signal.aspect is Aspect.SOURCE_COVERAGE, (
        "变的是**来源供给**这一面 —— 信号只有指对了那一格才有用："
        "what_is_due 按 (主体, aspect) 分格，指错面等于提前复核了别的格子"
    )
    assert signal.signalled_at == at, (
        "整批共用同一个 ``judged_at`` —— 一次结算的产物共享一个时刻，"
        "顺带让同一次结算写出的信号是幂等的"
    )
    assert f"新落了 {len(production.evidence)} 条来源" in signal.reason, (
        "理由要把数量说出来 —— 复核报告要回答的正是「为什么这次会重跑这一格」，"
        "而「有人说变了」答不了那个问题"
    )
    assert maintenance.signals(workspace=WORKSPACE) == production.signals, (
        "「当场记下」的意思是它已经在账上了 —— 返回一个只在内存里的对象会让"
        "维护 tick 读不到它，而回执看起来完全正常"
    )


def test_re_sending_the_same_material_writes_no_signal(settings, bundle) -> None:
    """同一条材料再跑一遍**不是**变化 —— 判据是**新行**，不是**行**。

    证据 id 是内容寻址的，所以重发写的是同一个 id。把「这一趟绑出了几行」当成
    「变化了」会让每一次重发都提前整整一个复核周期，而理由是一句假话。
    """
    store = _store(settings)
    maintenance = InMemoryMaintenanceStore()

    first = _produce(
        settings, bundle, _material(NAMED_PAGE), store=store, maintenance=maintenance
    )
    second = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, origin="page-a-again"),
        store=store,
        maintenance=maintenance,
    )

    assert first.signals, "第一次是新的来源"
    assert second.evidence, "第二次照样绑出了行 —— 绑定这一层不受影响"
    assert second.signals == (), "但一条都不是新的，所以一条信号都不该有"
    assert len(maintenance.signals(workspace=WORKSPACE)) == 1, (
        "账上仍然只有第一次那一条 —— 重发不制造第二行"
    )


def test_a_second_different_page_writes_a_second_signal(settings, bundle) -> None:
    """换一份**真的不同**的材料 ⇒ 又是一条新来源 ⇒ 又是一条信号。

    与上一条合起来才钉得住判据：只测「重发不写」的话，一个**永远不写**的实现
    照样绿；只测「首次写」的话，一个**每次都写**的实现也绿。
    """
    store = _store(settings)
    maintenance = InMemoryMaintenanceStore()

    _produce(settings, bundle, _material(NAMED_PAGE), store=store, maintenance=maintenance)
    second = _produce(
        settings, bundle, _material(OTHER_NAMED_PAGE), store=store, maintenance=maintenance
    )

    assert len(second.signals) == 1
    assert len(maintenance.signals(workspace=WORKSPACE)) == 2
    assert {signal.subject_id for signal in maintenance.signals(workspace=WORKSPACE)} == {
        "example-coatings"
    }
    assert all(
        signal.aspect is Aspect.SOURCE_COVERAGE
        for signal in maintenance.signals(workspace=WORKSPACE)
    ), "两条说的都是同一面（来源供给），只是各说各的那一批"


def test_a_material_that_writes_nothing_writes_no_signal(settings, bundle) -> None:
    """一份被拒的材料什么都没落 ⇒ 也就没有变化可报。

    与 ``test_a_rejected_material_writes_nothing`` 是同一条的另一半：那一半说
    「库里的路线与机会仍然是空的」，这一半说「账上也不许多一条」。
    """
    maintenance = InMemoryMaintenanceStore()
    production = _produce(
        settings,
        bundle,
        _material(NAMED_PAGE, fetched_at=datetime(2026, 9, 15, 12, 0)),
        maintenance=maintenance,
    )

    assert production.evidence == ()
    assert production.signals == ()
    assert maintenance.signals(workspace=WORKSPACE) == ()


def test_no_maintenance_account_still_produces_routes(settings, bundle) -> None:
    """没有维护账时这一趟照常跑完，只是不记信号。

    这一格留给**只接控制面**的服务实例（组合根没接维护账）。少的是那一格接线，
    不是绑定、判时效、定级、落盘那四步 —— 拒绝结算会让一个只差一本账的部署连
    路线都产不出来。

    这里**直接调** ``produce_routes`` 而不是走 ``_produce``：那个助手把
    ``maintenance=None`` 当成「没传，给一本新的」。
    """
    production = produce_routes(
        materials=[_material(NAMED_PAGE)],
        bundle=bundle,
        store=_store(settings),
        maintenance=None,
        vocabulary=VOCAB,
        context=RouteContext(run_id=RUN_ID),
    )

    assert production.routes, "绑定那一趟照常跑过"
    assert production.evidence, "证据照常落盘"
    assert production.signals == ()
