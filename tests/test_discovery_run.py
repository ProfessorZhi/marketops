"""``discovery_scope`` 的跑法：从检索式走到一份**能重新喂进受理**的池子。

## 这个文件钉的是那条链本身，不是它的零件

零件各有各的测试（``test_discovery_openweb`` / ``_pairing`` / ``_qualify`` /
``test_caps`` / ``test_motherpool`` / ``test_billed_search``）。这里钉的是**把它们
串成一圈之后**才可能出现、而任何单个零件都测不出来的那几件事：

1. **出口真的能重新进受理**（§2 E2 逐字要求「output must be re-feedable as a
   SourceReceipt」）。判据是**走一遍受理那一步**（``inputs.subjects_for``），
   不是断言工件文件存在 —— 一份写出来但受理不认的工件，在报告里长得跟成功一样。
2. **``stops`` 分得开**：额度用尽 / 达到上限 / 没有证据是三件不同的事，而三者在
   「池子是空的」这件事上同形。每一种都要能**单独**摆出来。
3. **检索的 snippet 一次都不许当证据**。这是那份实验最容易被捷径走坏的一格：
   snippet 里几乎总有名字、偶尔有邮箱，而用它可以让每一条判据都变绿。
4. **抓取失败不会被伪装成「这一页没有内容」**，也不会把整轮掀掉。
5. **重放幂等**：同一轮跑两遍，盘上仍是一份工件一份回执。

## 全部零网络、零真实 key

检索是假的（``_Replies``），HTTP 是 ``fixtures/http_fakes.FakeHttp`` —— 它把
``(status, content_type, body)`` 交给**真的** ``assess_http_response``，所以
「200 的 WAF 页被判成 BLOCKED」这类判据测的是真规则，不是替身作者的印象。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.application import inputs
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.budget import RunBudget, declared_search_ceiling
from marketops.application.discovery_run import (
    CANDIDATE_ID_PREFIX,
    DiscoveryScope,
    DiscoveryStop,
    HitOutcome,
    _run_id_for,
    candidate_id_for,
    materialize_pool,
    run_discovery,
    source_identity_for,
)
from marketops.application.motherpool import load_receipt, receipt_path
from marketops.application.openweb import OPEN_WEB_SOURCE_METHOD
from marketops.config import Settings
from marketops.domain.enums import FetchOutcome, JobInputKind, QueryOutcome
from marketops.domain.errors import ConfigError, SearchError
from marketops.domain.profile import CLASSIFICATION_AXES, CLASSIFICATION_UNKNOWN
from marketops.domain.receipt import Disposition
from marketops.domain.util import check_canonical_id, slugify
from marketops.interfaces.composition import default_router
from marketops.ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult
from tests.fixtures.http_fakes import FakeHttp, Reply
from tests.fixtures.site_content import page_html

# --- 替身：检索这一侧 ---------------------------------------------------------


class _Replies(SearchProvider):
    """按**检索式文本**回答的检索端口。没被摆在表里的检索式回一个空结果。

    空结果与「没搜过」是两件事，而它们在报告里同形 —— 所以每一发都被记进
    ``asked``，判据问的是后者。``limit`` 也照收：一条要 0 条结果的检索式是调用
    方给错了，不是「这一次没有命中」。

    ``api_calls=1`` 逐字照一个真实的 provider 报（``SerperSearchProvider`` 每次
    真发一个请求）—— 报 0 会让「真实请求数」这条读数在替身里永远是零，而账单
    那一侧的判据就以它为依据。
    """

    name = "fake-search"

    def __init__(self, table: dict[str, list[SearchHit]]) -> None:
        self.table = table
        self.asked: list[str] = []
        self.limits: list[int] = []

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.asked.append(query.text)
        self.limits.append(limit)
        hits = tuple(hit for hit in self.table.get(query.text, []) if hit.rank <= limit)
        return SearchResult(query=query, hits=hits, api_calls=1)


def _hit(url: str, *, rank: int = 1, query: str = "", snippet: str = "") -> SearchHit:
    return SearchHit(
        url=url,
        title="",
        snippet=snippet,
        rank=rank,
        query=query,
    )


def _site_page(name: str) -> Reply:
    """一台正常的站点：一页带 ``<title>`` 的 HTML。

    ``page_html`` 有 ``<h1>`` 与 ``<p>``，所以 ``assess_http_response`` 会判成
    ``OK`` 而不是 ``EMPTY_SHELL`` —— 否则每一条「正常路径」的判据都会偷偷走在
    浏览器升级那条路上。
    """
    return Reply(body=page_html(name), content_type="text/html")


def _bundle(settings: Settings):
    from marketops.adapters.profile_loader import load_profile

    return load_profile(settings)


def _scope(**over: object) -> DiscoveryScope:
    base: dict[str, object] = {"scope": "discovery-run-test", "region": "Devon"}
    base.update(over)
    return DiscoveryScope(**base)  # type: ignore[arg-type]


def _run(
    settings: Settings,
    *,
    scope: DiscoveryScope,
    search: SearchProvider,
    replies: dict[str, Reply],
    budget_ledger=None,
    store=None,
):
    """把一次运行接起来。

    ``budget_ledger`` / ``store`` 可注入，是为了让「额度用尽」与「证据文件不合并」
    这两条判据能摆在各自需要的那一个实现上 —— 它们**不是**默认值的替代品。
    """
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger

    ledger = budget_ledger or SqliteBudgetLedger(settings.database_path)
    return run_discovery(
        bundle=_bundle(settings),
        scope=scope,
        search=search,
        acquisition=DocumentAcquisition(
            http=FakeHttp(settings.mirrors_dir / "fake-http", replies)
        ),
        store=store or SqliteEvidenceStore(settings.database_path),
        router=default_router(),
        ledger=ledger,
        inputs_dir=settings.inputs_dir,
    )


@pytest.fixture
def query_text(settings: Settings) -> str:
    """profile 真实展开出来的**第一条**检索式。

    不硬编一条文本：检索式的形状由 ``openweb`` 与 profile 共同决定，抄一条进来
    会让这些判据在词表变化时静默地测一个不存在的检索式（它回空结果，而一切都绿）。
    """
    from marketops.application.openweb import build_open_web_queries

    plan = build_open_web_queries(_bundle(settings), frozen_region="Devon")
    return plan.queries[0].text


_GARDEN = "https://garden.example.org/contact"
_MEADOW = "https://meadow.example.net/about"


# --- 正常路径：一条链走通，出口能重新进受理 ---------------------------------


def test_a_run_writes_an_artifact_a_receipt_and_the_pool_carries_names(
    settings: Settings, query_text: str
) -> None:
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )

    assert search.asked[0] == query_text, "第一条检索式要来自 profile 的展开器"
    assert [site.site for site in run.pool] == ["garden.example.org"]
    assert run.pool[0].name == "Example Garden Club"
    assert run.sites[0].fetch_outcome is FetchOutcome.OK
    assert run.sites[0].evidence_ids, "取到的材料要落成可引用的证据行"

    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None
    assert pool.artifact_path == settings.inputs_dir / "discovery-run-test.jsonl"
    assert pool.receipt_path == receipt_path(settings.inputs_dir, pool.receipt.receipt_id)
    assert pool.receipt.counts[Disposition.RESOLVED.value] == 1
    assert pool.receipt.source_method_id == OPEN_WEB_SOURCE_METHOD.id
    assert pool.receipt.artifact_ref == "discovery-run-test"


def test_the_search_side_spends_its_whole_ceiling_unless_the_plan_is_shorter(
    settings: Settings, query_text: str
) -> None:
    """**这一轮总是把检索额度用完**（除非计划本身更短）。这是一条要写下来的性质。

    它看起来像一个可以省的地方，而它不是无意的：检索与抓取是**两段**，而「池子
    满了吗」要取过页面才知道 —— 把两者交错起来（发一条、折一次、够了就停）会让
    这一次运行「取了哪几台」取决于交错点，而那正是这一层刻意做成两段的原因
    （确定性与可复算）。代价因此是明写在报告里的：**20 条检索式都会被发出去**，
    哪怕第 3 条之后就已经凑够了候选。

    判据钉的是「界限在哪」：发出去的数量等于**计划长度与额度上限的较小者**，
    不多不少。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(max_candidates=1),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    ceiling = declared_search_ceiling(_bundle(settings))

    assert len(run.pool) == 1, "上限是 1，池子里就只有一行"
    assert len(search.asked) == min(len(run.plan.queries), ceiling)
    assert len(search.asked) > 1, (
        "池子在第 1 条之后就满了，而检索仍然发到额度为止 —— 这正是上面记下的那条代价"
    )
    assert run.usage[0].spent == len(search.asked), (
        "账单说的是发出去了几条，不是「有几条有用」"
    )


def test_the_pool_is_re_feedable_as_a_discovery_scope_input(
    settings: Settings, query_text: str
) -> None:
    """**这一条是 E2 的验收判据。** 出口必须真的能走回受理那一步。

    判据不是「工件文件存在」：一份写出来而受理不认的工件，在报告里与成功同形。
    所以这里走的是 ``inputs.subjects_for(DISCOVERY_SCOPE, <范围名>)`` —— 受理
    那一步**真的会走**的那一段，然后断言它解出了主体、而且解出的正是那一行。

    ``input_ref`` 是**范围名**（工件名），不是回执 id：回执 id 是内容派生的，
    提交的人算不出来（见 ``inputs.discovery_scope_subjects``）。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    assert materialize_pool(settings.inputs_dir, run) is not None

    resolved = inputs.subjects_for(
        JobInputKind.DISCOVERY_SCOPE, "discovery-run-test", inputs_dir=settings.inputs_dir
    )

    assert resolved.ok, resolved.blocker
    assert resolved.blocker == ""
    assert len(resolved.subjects) == 1
    subject = resolved.subjects[0]
    # ``organisation`` 与 ``domain`` 是**真正成为身份**的那两个字段（``identity_refusal``
    # 核对的就是它们）；``account_id`` 是 canonical 账号 id（``acct-…``），由场地句柄
    # 派生 —— 里面同样没有主机名的痕迹，把来源标识当账号身份正是这条链要挡住的那种
    # 写法。它**不是** ``row_id``：那曾经是这里的主体，而它与账号池的主键没有交点。
    assert subject.organisation == "Example Garden Club"
    assert subject.domain == "garden.example.org"
    assert subject.account_id.startswith("acct-")
    assert "garden" not in subject.account_id
    assert subject.country is None, "回执这一层不知道国家 —— 它不该猜一个"
    assert subject.source_ref.endswith(".receipt.json")


def test_a_second_materialize_refuses_to_overwrite_the_artifact(
    settings: Settings, query_text: str
) -> None:
    """同一轮**不许**物化两次：工件是不可变的，第二发是拒绝而不是覆盖。

    判据是 ``ConfigError`` 而不是「文件还在」：一份被悄悄覆盖的工件会让「这份回执
    指着哪一份字节」这个问题的答案变成一个。所以这里同时断言盘上仍是一份工件、
    一份回执 —— 拒绝的**内容**与拒绝**发生了**要一起成立。
    """
    run = _run(
        settings,
        scope=_scope(),
        search=_Replies({query_text: [_hit(_GARDEN, query=query_text)]}),
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    first = materialize_pool(settings.inputs_dir, run)
    assert first is not None

    with pytest.raises(ConfigError) as excinfo:
        materialize_pool(settings.inputs_dir, run)

    assert "拒绝覆盖" in str(excinfo.value)
    assert len(list(settings.inputs_dir.glob("*.jsonl"))) == 1
    assert len(list(settings.inputs_dir.glob("*.receipt.json"))) == 1


def test_the_receipt_id_is_content_addressed_so_a_replay_lands_on_the_same_name(
    settings: Settings, query_text: str, tmp_path: Path
) -> None:
    """同一轮物化到**另一个部署**里 ⇒ 同一个回执 id。

    这一条是「重放幂等」在盘上的样子，而它成立的根据是 ``receipt_id`` 由
    「来源方法 + 批次 + 工件摘要 + 处置摘要」派生（``created_at`` 刻意**不在**里面）。
    少了这条性质，同一份工件在 10:00 与 10:05 读出来会是两份不同的回执 —— 而
    「重放」就再也不是幂等的了。
    """
    run = _run(
        settings,
        scope=_scope(),
        search=_Replies({query_text: [_hit(_GARDEN, query=query_text)]}),
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    first = materialize_pool(settings.inputs_dir, run)
    assert first is not None

    elsewhere = tmp_path / "another-deployment" / "inputs"
    elsewhere.mkdir(parents=True, exist_ok=True)
    second = materialize_pool(elsewhere, run)
    assert second is not None

    assert second.receipt.receipt_id == first.receipt.receipt_id
    assert second.receipt_path.name == first.receipt_path.name
    assert second.receipt_path.parent != first.receipt_path.parent


# --- snippet 一次都不许当证据 -------------------------------------------------


def test_the_search_snippet_never_reaches_the_pool_the_evidence_or_the_report(
    settings: Settings, query_text: str
) -> None:
    """§7：snippet 只用于**发现 URL**，一个字都不许进证据或池子。

    摆一条**内容与页面完全不同**的 snippet（里面有一个名字和一个邮箱）。判据是
    三处都找不到那两个字面：工件字节、落成的证据行、以及运行报告。只查一处会
    漏掉另外两处 —— 而这两处才是会被下游读到的地方。
    """
    marker = "Snippet Person"
    email = "snippet.person@elsewhere.example"
    search = _Replies(
        {
            query_text: [
                _hit(_GARDEN, query=query_text, snippet=f"{marker} {email} 请联络")
            ]
        }
    )
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    artifact = pool.artifact_path.read_text(encoding="utf-8")
    report = json.dumps(run.as_dict(), ensure_ascii=False)
    evidence = json.dumps(
        [_evidence_text(settings, eid) for eid in run.evidence_ids], ensure_ascii=False
    )

    for where, blob in (
        ("工件", artifact),
        ("运行报告", report),
        ("证据", evidence),
    ):
        assert marker not in blob, f"{where}里出现了 snippet 里的名字"
        assert email not in blob, f"{where}里出现了 snippet 里的邮箱"
    # 正控：页面自己写下的名字**必须**在。「找不到 snippet」这条断言有一个廉价的
    # 反面 —— 把整条链弄坏它同样成立。
    assert "Example Garden Club" in artifact


def _evidence_text(settings: Settings, evidence_id: str) -> str:
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    store = SqliteEvidenceStore(settings.database_path)
    found = store.get_evidence(evidence_id)
    assert found is not None, f"证据 {evidence_id} 不在库里"
    return found.literal_value


# --- 三条停止理由各自能单独摆出来 -------------------------------------------


def test_an_exhausted_budget_stops_the_rest_of_the_queries(
    settings: Settings, query_text: str
) -> None:
    """额度用尽 ⇒ ``BUDGET_EXHAUSTED``，而且**没发出去的检索式仍在读数里**。

    「没搜」与「搜了没结果」不是一件事。一个把剩下的检索式从报告里丢掉的实现
    会让这一次运行看起来像「整个计划都跑过了、只有第一条有命中」。
    """
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger

    # 上限的唯一来源是 profile，所以先把账本按 profile 播种，再把剩下的额度**占住**
    # （占住就够：``held`` 含在途的预留）。不去构造一个「上限为 1」的账本 ——
    # 那样测的就不是这一层的行为了。
    ledger = SqliteBudgetLedger(settings.database_path)
    budget = RunBudget.for_scope(ledger, _bundle(settings), "discovery-run-test")
    ceiling = declared_search_ceiling(_bundle(settings))
    assert ceiling > 1, "profile 的 ceiling 只有 1 时这条判据摆不出「用尽」"
    for index in range(ceiling - 1):
        budget.reserve(idempotency_key=f"held-open-{index}")
    assert budget.remaining() == 1

    search = _Replies({query_text: []})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={},
        budget_ledger=ledger,
    )

    assert DiscoveryStop.BUDGET_EXHAUSTED in run.stops
    assert len(search.asked) == 1, "额度用尽之后一条都不该再发"
    outcomes = [reading["outcome"] for reading in run.queries]
    assert QueryOutcome.EXHAUSTED.value in outcomes
    assert "not_attempted" in outcomes, (
        "没轮上的检索式必须留在读数里 —— 一份「跑了 1 条」的报告与一份"
        "「20 条里跑了 1 条」的报告是两件完全不同的事"
    )
    assert any("没有发出去" in caveat for caveat in run.caveats)


def test_a_ceiling_of_zero_is_not_the_same_reading_as_a_finished_plan(
    settings: Settings, query_text: str
) -> None:
    """额度一次都没有 ⇒ 池子是空的，而理由是**额度**，不是「没有候选」。

    这一条与上一条的差别是要摆出来给人看的：「跑了、网络里没有」与「压根没发出去」
    在屏幕上都是「池子空着」。
    """
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger

    ledger = SqliteBudgetLedger(settings.database_path)
    budget = RunBudget.for_scope(ledger, _bundle(settings), "discovery-run-test")
    ceiling = declared_search_ceiling(_bundle(settings))
    for index in range(ceiling):
        budget.reserve(idempotency_key=f"held-open-{index}")
    assert budget.remaining() == 0

    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(settings, scope=_scope(), search=search, replies={}, budget_ledger=ledger)

    assert search.asked == [], "一次检索都不该发出去"
    assert DiscoveryStop.BUDGET_EXHAUSTED in run.stops
    assert run.pool == ()
    assert materialize_pool(settings.inputs_dir, run) is None, (
        "一行都没有时**不许**写工件：一份空工件不是一次成功的母池读入"
    )
    assert list(settings.inputs_dir.glob("*.jsonl")) == []


def test_reaching_the_cap_is_recorded_and_is_not_candidate_exhaustion(
    settings: Settings, query_text: str
) -> None:
    """达到 ``max_candidates`` ⇒ ``CAP_REACHED``，而且**没去取**的台数被算进停止理由。

    上限是**我们**按自己的决定停的；候选枯竭是网络里没有再给出东西。两者都要有人
    做决定，而决定的内容不一样 —— 所以它们在 ``stops`` 里必须是两个成员。
    """
    hosts = [f"site{index}.example.org" for index in range(4)]
    search = _Replies(
        {query_text: [_hit(f"https://{host}/", rank=index + 1, query=query_text) for index, host in enumerate(hosts)]}
    )
    run = _run(
        settings,
        scope=_scope(max_candidates=2),
        search=search,
        replies={f"https://{host}/": _site_page(f"Example Club {host}") for host in hosts},
    )

    assert len(run.pool) == 2
    assert DiscoveryStop.CAP_REACHED in run.stops
    assert DiscoveryStop.NO_EVIDENCE not in run.stops, (
        "「达到上限」与「没有证据」不是同一条停止理由"
    )
    assert any("没有去取" in caveat for caveat in run.caveats)
    assert any(site.reason.startswith("池子在上限处满了") for site in run.sites)


def test_a_site_that_reads_but_never_names_itself_stops_as_no_named_site(
    settings: Settings, query_text: str
) -> None:
    """取回了材料、也落了证据，但这一页**没有写下能用的名字** ⇒ ``NO_NAMED_SITE``。

    判据是 ``NO_NAMED_SITE`` 而**不是** ``CAP_REACHED``：上限一次都没碰到。也
    **不是** ``NO_EVIDENCE``：材料读到了、证据也落了 —— 「我们读过这一页」与
    「这一行进了池子」是两个读数，而它们在这一条上分开了。
    """
    page = _GARDEN
    search = _Replies({query_text: [_hit(page, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        # 标题就是主机名本身 —— ``usable_name`` 拒绝它（拿主机名当名字会让这一行
        # 读起来像一个组织）。
        replies={page: Reply(body=page_html("garden.example.org"), content_type="text/html")},
    )

    assert run.pool == ()
    # 判据是 ``NO_NAMED_SITE``，**不是** ``NO_EVIDENCE``：材料读到了、证据落了，
    # 缺的是「这一页给自己的名字」。两条停止理由说的是一件完全不同的事 ——
    # 一条是「没有读到东西」，一条是「读到了，但这一行还没有指称对象」。
    assert DiscoveryStop.NO_NAMED_SITE in run.stops
    assert DiscoveryStop.NO_EVIDENCE not in run.stops
    assert DiscoveryStop.CAP_REACHED not in run.stops
    assert run.evidence_ids, "读到了材料就要落证据，哪怕它不进池子"
    assert "名字" in run.sites[0].reason


def test_a_blocked_site_is_a_reading_not_a_crash_and_not_an_empty_page(
    settings: Settings, query_text: str
) -> None:
    """一台主机拒了匿名访问 ⇒ 终态读数，整轮不受影响，理由**不等于**「这一页是空的」。

    200 + WAF 页会被真的 ``assess_http_response`` 判成 ``BLOCKED``（``FakeHttp``
    把字节交给真规则），所以这里测的是真分类而不是替身的印象。
    """
    waf = "<html><body>Just a moment... verifying your browser</body></html>"
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: Reply(body=waf, content_type="text/html")},
    )

    assert run.sites[0].fetch_outcome is FetchOutcome.BLOCKED
    assert run.pool == ()
    assert "拒" in run.sites[0].reason
    assert DiscoveryStop.BLOCKED in run.stops
    # 被拒是**那台主机**的读数，不是这一次运行的失败：运行照走完，读数照落盘。
    assert run.evidence_ids == ()
    assert DiscoveryStop.NO_EVIDENCE in run.stops


def test_a_transport_failure_end_to_end_does_not_take_the_run_down(
    settings: Settings, query_text: str
) -> None:
    """端口允许抛的只有 ``AccessBlockedError``；一次超时必须是 ``FetchResult``。

    这条与上一条的差别是**谁产生那句读数**：上一条是分类规则，这一条是传输层。
    两者都不许把整轮掀掉 —— 一次被拒的抓取会让「这一轮还有什么」永远说不出来。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: Reply(transport=FetchOutcome.TIMEOUT)},
    )

    assert run.sites[0].fetch_outcome is FetchOutcome.TIMEOUT
    assert run.pool == ()
    assert "没有取到可读的内容" in run.sites[0].reason


def test_fetch_pages_off_gives_an_empty_pool_and_says_why(
    settings: Settings, query_text: str
) -> None:
    """``fetch_pages=False``：只发检索、不取页面 ⇒ 池子必然空，而这件事被说出来。

    它存在的理由是让「检索这一侧接线了没有」可以单独验证 —— 而它的读数不能与
    「取过了、都没有名字」同形。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(settings, scope=_scope(fetch_pages=False), search=search, replies={})

    assert search.asked[0] == query_text, "检索这一侧仍然要真的跑"
    assert run.pool == ()
    assert run.evidence_ids == ()
    assert any("没有取页面" in caveat for caveat in run.caveats)
    assert any(site.reason.startswith("这一轮没有取页面") for site in run.sites)
    assert DiscoveryStop.NO_NAMED_SITE in run.stops
    # ``NO_EVIDENCE`` **必须不在**：一页都没取过，说「没有证据」就是替一次没做过的
    # 取证下结论。这条与上面那条 caveat 是同一件事的两种说法，而它们的区别是承重的
    # —— caveat 说的是「这一轮没有取页面」，而停止理由说的是「这一轮停下来时是什么
    # 状态」，后者不许把「没做」写成「做了、什么都没有」。
    assert DiscoveryStop.NO_EVIDENCE not in run.stops


# --- 命中折叠成站点 -----------------------------------------------------------


def test_two_hits_on_one_host_are_one_row_and_the_second_url_is_kept(
    settings: Settings, query_text: str
) -> None:
    """池子的单位是**站点**：同一台主机的第二条命中进 ``also_seen_urls``，不新开一行。"""
    second = "https://garden.example.org/team"
    search = _Replies(
        {
            query_text: [
                _hit(_GARDEN, rank=1, query=query_text),
                _hit(second, rank=2, query=query_text),
            ]
        }
    )
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )

    assert len(run.pool) == 1
    assert run.pool[0].also_seen_urls == (second,)
    assert run.hit_counts()[HitOutcome.SAME_SITE.value] == 1
    assert any("其余命中" in caveat for caveat in run.caveats), (
        "没去取第二条 URL 这件事要进 caveats"
    )


def test_a_url_without_a_host_is_counted_and_never_fetched(
    settings: Settings, query_text: str
) -> None:
    """折不出主机的命中（``mailto:`` / 相对地址）要被**计数**，而不是静默丢弃。"""
    search = _Replies(
        {query_text: [_hit("mailto:info@example.org", query=query_text), _hit(_GARDEN, rank=2, query=query_text)]}
    )
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )

    assert run.hit_counts()[HitOutcome.UNUSABLE_URL.value] == 1
    assert [site.site for site in run.sites] == ["garden.example.org"]


# --- 工件与候选行的形状：几个不变量 -------------------------------------------


def test_the_artifact_row_carries_the_evidence_pointers_and_the_frozen_region(
    settings: Settings, query_text: str
) -> None:
    """工件那一行逐字带着证据指针与**冻结**区域 —— 而区域刻意不叫 ``region``。

    ``region``（或任何 ``LOCATION_KEYS`` 里的名字）会被读成
    ``resolved_location``，而「这一轮的范围是德文郡」与「这个站点在德文郡」
    是两句话。这条钉住的是**那个名字**，不是那个值。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    row = json.loads(pool.artifact_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["source_identity"] == source_identity_for("garden.example.org")
    # 2026-09-26：这一行不再写 ``official_domain``。那一列在下游的意思是「上游
    # **说**的官网」，而发现期没有任何一句话是那个意思 —— 我们看到的是一台主机。
    assert "official_domain" not in row
    assert row["discovered_host"] == "garden.example.org"
    assert row["discovered_url"] == _GARDEN
    # 而判定那四格是**真的被算出来的**：这一页的标题把组织名说全了，而主机名
    # （``garden.example.org``）读得出那个名字，于是域名权威成立 ——
    # 这正是 §4.2 要的「一个真实的 verified_official_domain 生产者」。
    # 判定与它引的证据一起写，一个带着 ``confirmed`` 却没有证据的行读起来像
    # 一句没有出处的断言。
    assert row["domain_verdict"] == "confirmed"
    assert row["verified_official_domains"] == "garden.example.org"
    assert row["verified_official_domain"] == "garden.example.org"
    # 判定引的证据必须是这一行**真的写过**的证据之一 —— 一个是逗号连接的串，
    # 所以判据是子集，不是相等。
    assert set(row["domain_evidence_ids"].split(",")) <= set(row["evidence_ids"].split(","))
    assert row["discovery_region"] == "Devon"
    assert "region" not in row and "location" not in row
    assert row["title_evidence_id"] in row["evidence_ids"].split(",")
    assert row["name"] == "Example Garden Club"


def test_the_candidate_id_is_a_slug_fixed_point_and_never_carries_the_url(
    settings: Settings, query_text: str
) -> None:
    """候选 id 是**哈希**派生的规范 id，不是从 URL 拼出来的。

    ``check_slug_safe`` 的禁字符集含 ``:`` 与 ``/``，所以从 URL 直接派生会在
    **第一台站点**上炸；而一个会截断的拼法（``slugify`` 的 ``max_length``）会把
    两台不同的主机静默合到同一个文件名上。判据是「它是 slugify 的不动点」加上
    「它不是主机的任何字面切片」。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )

    candidate_id = run.pool[0].candidate_id
    assert candidate_id.startswith(CANDIDATE_ID_PREFIX)
    assert slugify(candidate_id) == candidate_id, "候选 id 必须是 slugify 的不动点"
    assert candidate_id == candidate_id_for("garden.example.org")
    assert "garden" not in candidate_id and "." not in candidate_id


def test_candidates_on_two_hosts_do_not_share_an_evidence_file(
    settings: Settings, query_text: str
) -> None:
    """两台不同的主机 → 两个不同的 ``Evidence.account_id`` → 两份不同的证据文件。

    这条钉的是一个**静默合并**：``LocalEvidenceStore`` 按 ``account_id``
    **取文件名**，而一个需要规范化的 id 会与另一个 id 落到同一个文件上 ——
    报告上只会显示「证据更多了」。所以这里用的是那个**有这条风险**的实现
    （``adapters/jsonl``），而不是主存那一个：SQLite 不按名字分文件，用它来测
    这条判据的结论是空的。
    """
    from marketops.adapters.jsonl.store import LocalEvidenceStore

    search = _Replies(
        {
            query_text: [
                _hit(_GARDEN, rank=1, query=query_text),
                _hit(_MEADOW, rank=2, query=query_text),
            ]
        }
    )
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={
            _GARDEN: _site_page("Example Garden Club"),
            _MEADOW: _site_page("Example Meadow Trust"),
        },
        store=LocalEvidenceStore(settings),
    )

    ids = [site.candidate_id for site in run.pool]
    assert len(set(ids)) == 2
    files = {path.name for path in settings.evidence_dir.glob("*.jsonl")}
    assert len(files) == 2, f"两台主机必须落成两份证据文件，实际 {sorted(files)}"
    assert files == {f"{candidate_id}.jsonl" for candidate_id in ids}


# --- 判定进报告、不进池子 -----------------------------------------------------


def test_qualification_is_reported_and_never_written_into_the_pool(
    settings: Settings, query_text: str
) -> None:
    """准入判定只在报告里。工件里那一行**没有**任何判定字段。

    「发现」与「判定」是两件事，而把它们写进同一行之后，「这条候选合格吗」这个
    问题就有了两个答案（文件里那个与判定引擎算出来的那个），而漂移的方向永远是
    「文件里那个看起来更像结论」。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    summary = run.qualification_summary()
    assert summary["count"] == 1
    assert summary["unchecked_rules"], (
        "候选行上的 country / has_notes 还没有生产者 —— 那些规则必须落 unchecked，"
        "而「一条 qualified 都出不来」这件事要能在报告里被看见"
    )

    row = json.loads(pool.artifact_path.read_text(encoding="utf-8").splitlines()[0])
    for forbidden in ("status", "grade", "outcome", "qualified", "eligibility"):
        assert forbidden not in row, f"工件里不许出现判定字段 {forbidden!r}"


def test_the_receipt_and_the_report_disagree_on_purpose_not_on_bytes(
    settings: Settings, query_text: str
) -> None:
    """回执说的是**池子里的行**，报告说的是**这一轮读了什么**，两者都自洽。

    这一条钉的是一个具体的误读：``resolved=1`` 会被读成「发现了一个单位」，
    而它其实是「一行进了池子」。报告里另外那几个数（涉及几台主机、抓了几次）
    是同一个事实的另外几面。
    """
    search = _Replies({query_text: [_hit(_GARDEN, query=query_text)]})
    run = _run(
        settings,
        scope=_scope(),
        search=search,
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    reloaded = load_receipt(settings.inputs_dir, pool.receipt.receipt_id)
    assert reloaded.receipt_id == pool.receipt.receipt_id
    assert reloaded.artifact_sha256 == pool.receipt.artifact_sha256

    report = run.as_dict()
    assert report["pool"]["kept"] == 1
    assert report["sites"]["count"] == 1
    assert report["sites"]["named"] == 1
    assert report["evidence"]["count"] == len(run.evidence_ids)


# --- 四格读数一路走到场地资产那一行 -------------------------------------------


def _cells_along_the_chain(settings: Settings, run, pool) -> dict[str, dict[str, str]]:
    """四格读数在这条链上的**每一站**。顺序就是数据实际走的那条路。

    每一站都是一次可能的静默丢失，而四种丢失在下游看起来一模一样 ——
    都只是「这一格没有信息」：

    * 分类器没写（``_read_one`` 少了那一步）；
    * 工件行漏了那三列（``_artifact_row`` 只写了 ``name``）；
    * 回执读丢了（``read_motherpool`` 的列名对不上 —— 而回执那一侧的缺省是
      **空串**，正是「这一份工件没有这一栏」的写法，于是它读起来像一份没有这一栏的导出）；
    * 资产构造折了（``country`` 那一处 ``row.country or None`` 之类的折法）。

    **一次一条站点**：三站的链要靠名字或 id 对齐，而对齐本身是这套断言之外的一件事。
    所以调用方摆的命中只有一条，这里按下标取。
    """
    from marketops.application.pool import assets_from_receipt

    site = run.sites[0]
    row = json.loads(pool.artifact_path.read_text(encoding="utf-8").splitlines()[0])
    disposition = pool.receipt.dispositions[0]
    assets = assets_from_receipt(
        pool.receipt,
        workspace=_bundle(settings).profile_id,
        seen_at=run.created_at,
    )
    assert len(assets) == 1, (
        f"这一条判据要的是「一条站点 → 一行资产」，实际 {len(assets)} 行 —— "
        "多出来的行会让下面按下标取的读数指错东西"
    )
    asset = assets[0]

    return {
        "运行内的读数": dict(site.classification_values()),
        "工件行": {axis: row[axis] for axis in CLASSIFICATION_AXES},
        "回执行": {
            "organisation_kind": disposition.organisation_kind,
            "venue_environment": disposition.venue_environment,
            "surface_class": disposition.surface_class,
            "country": disposition.country,
        },
        "场地资产": {
            "organisation_kind": asset.venue_type,
            "venue_environment": asset.venue_environment,
            "surface_class": asset.surface,
            "country": asset.country if asset.country is not None else "",
        },
    }


def _a_phrase_without_an_exclusion(bundle) -> tuple[str, str, str]:
    """词表里**第一条不带排除项**的判据：``(轴, 取值, 判据短语)``。

    不写市场词。抄一句进测试会让「词表变了」与「链断了」长得一样，而这两件事
    要的处置完全不同（一个是改测试，一个是查代码）。

    挑「不带排除项」的，是因为带了排除项的那些取值会让这一行落进 ``excluded`` ——
    于是**没有资产**，链的后半截摆不出来。
    """
    for axis_name in CLASSIFICATION_AXES:
        axis = bundle.profile.classification.axis(axis_name)
        if axis is None:
            continue
        for value in axis.values:
            if value.phrases and not value.excludes:
                return axis_name, value.value, value.phrases[0]
    raise AssertionError(
        "词表里没有一条不带排除项的判据 —— 这条判据摆不出来，而「摆不出来」"
        "不能静默变成通过"
    )


def test_a_reading_reaches_the_asset_row_unchanged(
    settings: Settings, query_text: str
) -> None:
    """§2 的那条链：**页面 → 读数 → 工件 → 回执 → 场地资产**，四格原样走到最后一行。

    这一条测的**不是分类器判得对不对**（那是 ``test_discovery_qualify.py`` 与分类器
    自己的判据的事），而是读数的**传递**：同一个字符串在四个地方是不是同一个。
    所以期望值由**同一个分类器**在同一段文字上给出，而这一条钉的是四份拷贝彼此相等
    且都等于它。

    链上任何一处漏写都会在这里现形，而且**四种漏法在下游长得一模一样** ——
    「这一格没有信息」—— 所以四处一次全查，不逐处写四条判据。
    """
    from marketops.application.classification import classify

    bundle = _bundle(settings)
    axis_name, _value, phrase = _a_phrase_without_an_exclusion(bundle)
    title = f"Example {phrase}"
    host = "garden.example.org"

    # 期望读数：同一段文字、同一台主机、同一份词表，走**同一个**分类函数。
    # ``page_html`` 会把标题原样放进 ``<title>``、``<h1>`` 与每一段 —— 所以整页文字
    # 里含的判据不会比标题里多，而「标题里含它」在归一化后是词序列包含，逐段重复
    # 不改变结论。
    expected = classify(title, host=host, vocabulary=bundle.profile.classification)
    assert expected.value(axis_name) != CLASSIFICATION_UNKNOWN, (
        f"这条判据要从词表里挑一句**读得出来**的话，而 {phrase!r} 在 {axis_name!r} "
        "上没读出来 —— 换一句，别让这条断言在空转"
    )

    run = _run(
        settings,
        scope=_scope(),
        search=_Replies({query_text: [_hit(_GARDEN, query=query_text)]}),
        replies={_GARDEN: _site_page(title)},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    cells = _cells_along_the_chain(settings, run, pool)
    reference = cells["运行内的读数"]
    assert reference == {axis: expected.value(axis) for axis in CLASSIFICATION_AXES}, (
        "运行内的读数与同一段文字走分类器的结果对不上 —— 链的第一站就没接对"
    )
    for where, got in cells.items():
        assert got == reference, f"{where}的四格与运行内的读数对不上：{got} != {reference}"


def test_a_reading_that_could_not_be_taken_is_unknown_and_not_an_empty_string(
    settings: Settings, query_text: str
) -> None:
    """**「这一页没说」写成 ``unknown``，不折成空串 —— 一路折到最后一行也不行。**

    §2 逐字要求未确认字段为 ``unknown`` 且「绝不能是冒充事实的空字符串」。而这条链
    上有**两处**天然会把 ``unknown`` 折成 ``""``：回执行的四格缺省就是空串，资产那
    一侧 ``country`` 的类型是 ``str | None``。两处折了都不会有任何症状 —— 池子里那
    一行只是「这一格没有信息」，与一份真的没有这一栏的导出长得一模一样。

    所以判据是：**读不出来的那些轴，四处都必须是那个字面字符串**；而读数本身
    （哪几条轴没读出来）由运行内的读数给出，不在这里替分类器猜。
    """
    run = _run(
        settings,
        scope=_scope(),
        search=_Replies({query_text: [_hit(_MEADOW, query=query_text)]}),
        replies={_MEADOW: _site_page("Quarterly Notices")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None

    cells = _cells_along_the_chain(settings, run, pool)
    reference = cells["运行内的读数"]
    unread = [axis for axis in CLASSIFICATION_AXES if reference[axis] == CLASSIFICATION_UNKNOWN]
    assert unread, (
        "这一页（标题 Quarterly Notices、主机 meadow.example.net）应当有读不出来的轴，"
        "而四条轴全部读出来了 —— 换一页没有判据的页面，别让下面那条断言在空转"
    )

    for where, got in cells.items():
        assert got == reference, f"{where}的四格与运行内的读数对不上：{got} != {reference}"
        for axis in unread:
            assert got[axis] == CLASSIFICATION_UNKNOWN, (
                f"{where} 上的 {axis} 是 {got[axis]!r} —— 读不出来的那一格被折成了别的写法"
            )
            assert got[axis] != "", f"{where} 上的 {axis} 是空串，冒充成一次已确认的观察"


# --- 参数与名字的失败关闭 -----------------------------------------------------


def test_a_non_canonical_scope_is_refused(settings: Settings) -> None:
    """范围名会变成文件名 —— 需要规范化的名字是调用方给错了，不是部署事实。"""
    with pytest.raises(ValueError) as excinfo:
        DiscoveryScope(scope="Discovery Run", region="Devon")

    assert "不是规范形式" in str(excinfo.value)


def test_the_run_id_a_run_produces_is_its_own_canonical_form() -> None:
    """这一轮的名字要能被**读取那一半的同一条判据**接受。

    ``run_id`` 会被拼成文件名（``benchmarks/<run_id>.json``），所以
    ``domain/util.check_canonical_id`` 管它 —— ``get_report`` 读一份报告时逐字
    调的就是它（``adapters/mcp_server/tools.py::_report``）。名字在写入侧成立、
    在读取侧不成立时，报告照样落在盘上，而读的人拿到的是「没有这份报告」：
    盘上有一份，工具说没有。

    负对照是旧写法本身（``strftime`` 的大写 ``T``/``Z``）：同一个时刻、同一个
    范围名，只差大小写。没有它，这条用例在「判据其实不看大小写」时会因为别的
    原因变绿。
    """
    scope = _scope(scope="canonical-run-id")
    started = datetime(2026, 9, 29, 9, 19, 20, tzinfo=timezone.utc)

    run_id = _run_id_for(scope, started)

    assert check_canonical_id(run_id, "run_id") == run_id
    assert run_id == "canonical-run-id-20260929t091920z", f"名字的形状变了：{run_id!r}"

    uppercase = f"{scope.scope}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    assert uppercase != run_id, "两种写法一个大小 —— 这条负对照什么都没测"
    with pytest.raises(ValueError):
        check_canonical_id(uppercase, "run_id")


def test_a_scope_that_would_not_fit_a_run_id_is_refused_before_any_query(
    settings: Settings,
) -> None:
    """名字太长时拒绝，而不是截断 —— 而且是在花掉任何一次检索之前。

    截断不是一一映射：两个不同的范围名会落到同一份报告上（拼出来的文件名少了
    尾巴），而报告上只看得出「内容更多了」。拒绝发生在任何一次检索之前，所以改个
    名字就能重跑；若它发生在跑完之后，那一轮的报告已经写成了一份读不回来的文件。
    """
    scope = _scope(scope="s" * 45)
    search = _Replies({})

    with pytest.raises(ConfigError) as excinfo:
        _run(settings, scope=scope, search=search, replies={})

    assert "太长" in str(excinfo.value)
    assert search.asked == [], "拒绝发生在检索之后 —— 这一轮已经花过钱了"


def test_a_limit_below_one_is_refused(settings: Settings) -> None:
    with pytest.raises(ValueError) as excinfo:
        _scope(limit=0)

    assert "至少是 1" in str(excinfo.value)


def test_an_empty_region_is_refused_by_the_expander(settings: Settings) -> None:
    """空地区不许静默变成一条「没有地区的检索式」。

    判据不在这里自己写一条：``openweb.build_open_web_queries`` 是那一处的唯一
    实现，这里只断言**它确实拒绝了** —— 一份在 ``region=""`` 上照发的实现会发出
    一条全局检索式，而它在报告里与一次正常的、只是没有命中的运行同形。
    """
    from marketops.application.openweb import build_open_web_queries

    with pytest.raises(Exception) as excinfo:
        build_open_web_queries(_bundle(settings), frozen_region="")

    assert excinfo.value is not None


# --- 失败那一行的根因要能被机器读，而不是只能读那句话 -------------------------


class _Refusing(SearchProvider):
    """对每一条检索式都**拒绝**的检索端口：抛一个带访问判定的 ``SearchError``。

    它**逐字照** ``SerperSearchProvider`` 的形状构造那个异常（建 ``Observation``
    → 交给共享的 ``diagnose``），不自己拼一个 ``SearchError("一句话")`` —— 后者
    构造得出来，但 ``suspected_cause`` 会是 ``UNKNOWN``，于是这一条判据会在一个
    假的对象上变绿：它测的会是「这个替身会不会拼异常」，而不是「报告那一格读的是
    不是真诊断」。
    """

    name = "fake-refusing"

    def __init__(self, status: int, *, body: str = "", headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.asked: list[str] = []

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        from marketops.domain.access import Observation, safe_response_headers, diagnose

        self.asked.append(query.text)
        observation = Observation(
            observed_status=self.status,
            headers=safe_response_headers(self.headers),
            text=self.body,
            url="https://example.invalid/search",
            final_url="https://example.invalid/search",
        )
        diagnosis = diagnose(observation)
        raise SearchError(
            f"检索未完成（{query.identity}）：{diagnosis.detail}",
            observation=observation,
            diagnosis=diagnosis,
        )


def _rows_with(run, outcome: QueryOutcome) -> list[dict[str, object]]:
    """读数里**恰好是这一种终态**的那些行。

    每一行都必须有去向，而三种去向在这一格上的正确取值不同 —— 所以判据要按去向
    取子集，不能对整个 ``run.queries`` 下一个笼统的断言（那会在「计划比额度长」
    时把一条**额度用尽**的行也读成来源拒绝）。
    """
    return [reading for reading in run.queries if reading["outcome"] == outcome.value]


def test_a_refused_query_reports_the_cause_as_a_value_not_as_prose(
    settings: Settings, query_text: str
) -> None:
    """失败那一行的根因是**闭集里的一个值**，报告因此不必被谁去解析。

    这一条钉的是一个具体的缺口：报告里以前只有 ``error`` 那一句散文（
    ``str(receipt.error).splitlines()[0]``），于是「这一次是 403 还是 429」只存在于
    一句给人读的话里，任何想按根因分组的人只能去做子串匹配 —— 而那正是本片在
    ``adapters/serper.py`` 里刚刚拆掉的那种事。

    ``access_denied_unknown`` 是 **403 的默认结论**（``domain/access.py`` 的
    ``diagnose``）。判据逐字钉它，是为了让「403 被读成 IP 封禁」这种越权归类
    一旦出现就在这里红 —— 而不是等到有人读那句话才看出来。
    """
    search = _Refusing(403)
    run = _run(settings, scope=_scope(), search=search, replies={})

    failed = _rows_with(run, QueryOutcome.FAILED)
    assert len(search.asked) >= 1, "一条都没发出去的话下面几条断言测的是空集合"
    assert len(failed) == len(search.asked), (
        "每一条被拒的检索式都该落一行 failed："
        f"发了 {len(search.asked)} 条，却只有 {len(failed)} 行"
    )
    assert {row["cause"] for row in failed} == {"access_denied_unknown"}, (
        "403 的根因是 access_denied_unknown；"
        f"实际 {sorted({row['cause'] for row in failed})} —— 一个空集说明这一格根本没被填"
    )
    assert all(row["cause"] != "ip_policy" for row in failed), (
        "IP_POLICY 只能由 with_cause_evidence 产生，403 本身推不出它"
    )


def test_a_row_that_never_reached_a_source_carries_no_cause(
    settings: Settings, query_text: str
) -> None:
    """**没失败**的行这一格是空的，而空的原因不是「没填」。

    ``cause`` 说的是「来源为什么拒绝」。一行 ``sent``（问到了）与一行
    ``not_attempted``（压根没问）都没有来源拒绝可报，取值只能是空串 —— 它们**不是**
    ``UNKNOWN``：``UNKNOWN`` 是一个诊断结论（「问了，判不出来」），把它塞进这两行
    会是本片刚拆掉的那种折数。

    这一条与上一条配对：上一条证明失败行**有**根因，这一条证明别的行**不冒充**有。
    """
    search = _Refusing(403)
    run = _run(settings, scope=_scope(), search=search, replies={})

    for outcome in (QueryOutcome.SENT, QueryOutcome.SKIPPED, QueryOutcome.EXHAUSTED):
        for row in _rows_with(run, outcome):
            assert row["cause"] == "", f"{outcome.value} 的行不该有根因：{row}"

    elsewhere = [r for r in run.queries if r["outcome"] != QueryOutcome.FAILED.value]
    assert elsewhere, "这一次运行里必须真的存在非 failed 的行，否则这一条是空集合上的真话"
    assert all(row["cause"] != "unknown" for row in run.queries), (
        "unknown 是「判不出来」，不是「没有可判的」—— 两件事不能在这格里同形"
    )


def test_each_status_reports_its_own_cause_in_the_row(
    settings: Settings, query_text: str
) -> None:
    """401 与 429 在这一格里**不是同一个值**。

    两个状态码分开说，是本片在适配器那一侧已经立过的判据；这里钉的是它**穿到了
    报告里**。只测一个 403 的话，一个把根因写死成常量的实现会全绿。
    """
    seen: dict[int, str] = {}
    for status in (401, 429, 500):
        run = _run(settings, scope=_scope(), search=_Refusing(status), replies={})
        causes = {row["cause"] for row in _rows_with(run, QueryOutcome.FAILED)}
        assert len(causes) == 1, f"HTTP {status} 的每一行都该有同一个根因，实际 {sorted(causes)}"
        seen[status] = causes.pop()

    assert seen[401] == "auth_required"
    assert seen[429] == "rate_limited"
    assert seen[500] == "transient_or_unknown"
    assert len(set(seen.values())) == 3, f"三个状态码必须是三个根因，实际 {seen}"


def test_the_exhausted_row_carries_no_cause_and_the_report_still_exists(
    settings: Settings, query_text: str
) -> None:
    """额度用尽那一行**没有根因**，而写报告这一行不能因此崩掉。

    ``BudgetExhaustedError`` 在任何请求发出之前就发生了：它身上没有、也不该有一个
    「来源为什么拒绝」的判定。这一格因此必须走 ``isinstance`` 那条路 —— 直接取
    ``error.suspected_cause`` 会在这里抛 ``AttributeError``，而它会选在**额度刚好
    用尽**的那一次运行上抛，也就是最需要这份报告的那一次。
    """
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger

    ledger = SqliteBudgetLedger(settings.database_path)
    budget = RunBudget.for_scope(ledger, _bundle(settings), "discovery-run-test")
    ceiling = declared_search_ceiling(_bundle(settings))
    assert ceiling > 1, "ceiling 只有 1 时这条判据摆不出「额度在计划中途用尽」"
    for index in range(ceiling - 1):
        budget.reserve(idempotency_key=f"held-open-{index}")
    assert budget.remaining() == 1

    run = _run(
        settings,
        scope=_scope(),
        search=_Replies({query_text: [_hit(_GARDEN, query=query_text)]}),
        replies={},
        budget_ledger=ledger,
    )

    exhausted = _rows_with(run, QueryOutcome.EXHAUSTED)
    assert len(exhausted) == 1, (
        "额度用尽恰好该落一行 —— 它不是「跳过」，也不是「没轮上」："
        f"实际 {[r['outcome'] for r in run.queries[:3]]}"
    )
    assert exhausted[0]["cause"] == "", (
        "额度用尽不是来源拒绝；填一个根因会让「没问」与「问了被拒」在这一格上同形"
    )
    assert exhausted[0]["error"] != "", (
        "这一格空着才是不填 —— ``error`` 那句散文必须照旧在，本片没有把它换掉"
    )
