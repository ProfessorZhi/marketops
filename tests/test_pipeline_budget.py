"""检索额度真的挡在链路上了吗 —— 对着**跑起来的 pipeline** 验，不是对着账本验。

账本自己的语义由 ``tests/test_budget_contract.py``（两个实现）与
``tests/test_budget_ledger.py``（并发）证明。这里问的是另一件事：

    §8 的那条线有没有**接上**？

一个正确但没人调用的账本，与没有账本，在报告上一模一样。所以本文件的每一条判据
都从 ``ResearchPipeline`` 出发，最后落到账本的行上 —— 中间那一截正是会被漏掉的一截。

## 一条正对照

``test_the_reservation_is_what_stops_the_run`` 里有一个**故意不预留**的 pipeline。
它存在是因为「上限 20、实际只发了 20 次」这个观察本身并不说明是预留拦住的 ——
查询数本来也可能恰好是 20。正对照把「拿掉预留」这件事真的做出来，证明那条判据
量的是预留，而不是别的东西。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.config import Settings
from marketops.application.budget import RunBudget
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import BudgetNotConfiguredError, SearchError
from marketops.domain.evidence import AccountRecord
from marketops.adapters.profile_loader import load_profile
from marketops.application.queries import build_queries
from tests.conftest import DEFAULT_PROFILE, PROFILES_DIR
from tests.test_pipeline import (
    FakeHttpFetcher,
    PAGE_URL,
    RecordingSearch,
    _deps,
)
from marketops.application.pipeline import ResearchPipeline
from marketops.domain.enums import FetchOutcome
from marketops.ports.search import SearchProvider, SearchQuery, SearchResult

#: profile 里声明的上限。测试读它而不是写死 20 —— 写死会让「改了 profile」
#: 变成一堆测试变红，而红的原因（有人调了预算）与红的现象（数字不对）对不上。
DECLARED = 20

#: ``_profile_dir(max_queries=_MISSING)`` 的哨兵：**删掉**那个键。
#: 不用 ``None``：本文件的判据里 ``None`` 与「没声明」是同一件事，但写成
#: ``max_queries=None`` 读起来像是「声明为 null」，而那是 YAML 里另一种东西。
_MISSING = object()

#: ``max_documents`` 的缺省：**不动那一格**（保留真 profile 里那个数）。
#: 两条上限的判据各自只改自己那一格；顺手改了另一格，会让「读错了键」这个失败
#: 在测试里也变得可观察 —— 而那是下面那条判据**故意**要证明的事。
_UNTOUCHED = object()


def _profile_dir(
    tmp_path: Path, *, max_queries: object = DECLARED, max_documents: object = _UNTOUCHED
) -> Path:
    """复制一份真 profile，按需改写 ``search.max_queries`` / ``fetch.max_documents``。

    用**真** profile 改一个键，而不是造一份合成 profile：合成的那份一旦与真
    profile 漂开，「上限来自 profile」这条判据就变成在测一个没人用的文件。
    """
    dest = tmp_path / "profiles" / DEFAULT_PROFILE
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, dest)
    path = dest / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if max_queries is _MISSING:
        data["search"].pop("max_queries", None)
    else:
        data["search"]["max_queries"] = max_queries
    if max_documents is not _UNTOUCHED:
        if max_documents is _MISSING:
            data["fetch"].pop("max_documents", None)
        else:
            data["fetch"]["max_documents"] = max_documents
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_path / "profiles"


def _settings_and_bundle(tmp_path: Path, **kwargs):
    root = tmp_path / "runtime"
    settings = Settings(
        data_root=root,
        profiles_dir=_profile_dir(tmp_path, **kwargs),
        profile=DEFAULT_PROFILE,
    )
    settings.ensure_data_dirs()
    return settings, load_profile(settings)


def _pipeline(settings, bundle, *, ledger, search=None, http=None) -> ResearchPipeline:
    url = PAGE_URL
    return ResearchPipeline(
        _deps(
            settings,
            bundle,
            search=search if search is not None else RecordingSearch([url]),
            http=http
            if http is not None
            else FakeHttpFetcher(settings, {url: (FetchOutcome.OK, "<html></html>")}),
            budget=ledger,
        )
    )


def _scope(bundle, account: AccountRecord) -> str:
    return f"{bundle.profile_id}:{account.account_id}"


class _FailsTheFirstQuery(SearchProvider):
    """第一条查询抛错，其余返回空。

    用来问一个问题：**失败的调用要不要把额度还回去。** §8 的答复是不还 ——
    超时/出错之后你并不知道对方是否已经计费。
    """

    name = "fails-the-first"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.calls += 1
        if self.calls == 1:
            raise SearchError("模拟的连接中断")
        return SearchResult(query=query, hits=(), api_calls=1)


class _DoesNotReserve(ResearchPipeline):
    """正对照：一个**不预留**的 pipeline，别的都一样。

    存在的唯一理由是让 ``test_the_reservation_is_what_stops_the_run`` 能失败。
    没有它，那条判据量到的可能只是「查询数恰好等于上限」。
    """

    def _discover(self, account, metrics, errors, budget, context):  # noqa: ANN001
        queries = build_queries(account, self._deps.bundle)
        metrics.serp_queries = len(queries)
        urls: list[str] = []
        seen: set[str] = set()
        for query in queries:
            result = self._deps.search.search(query)
            metrics.serp_api_calls += result.api_calls
            for hit in result.hits:
                if hit.url not in seen:
                    seen.add(hit.url)
                    urls.append(hit.url)
        metrics.candidate_urls = len(urls)
        return urls


# --- 上限从哪来 -----------------------------------------------------------------


def test_the_ceiling_comes_from_the_profile(tmp_path, account) -> None:
    """上限的数值来自 profile，且在**第一次检索之前**就已经在账本里了。

    先播种后检索这件事在本地不可直接观察（延迟看不出来），能观察的是它的后果：
    跑完之后那个 scope 上有且只有一条 ``SEARCH`` 科目的上限。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()

    _pipeline(settings, bundle, ledger=ledger).run_account(account)

    usage = ledger.usage(_scope(bundle, account))
    assert len(usage) == 1
    assert usage[0].line is BudgetLine.SEARCH
    assert usage[0].ceiling == DECLARED


def test_the_source_records_which_file_and_key_set_the_ceiling(tmp_path, account) -> None:
    """``source`` 指向 profile 的**具体那个键**，不是一个泛泛的「配置」。"""
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = _CapturingLedger()

    _pipeline(settings, bundle, ledger=ledger).run_account(account)

    assert ledger.sources == [f"profile:{bundle.profile_id}:search.max_queries"]


class _CapturingLedger(InMemoryBudgetLedger):
    """把 ``set_ceiling`` 的 ``source`` 记下来 —— 账本自己不暴露它。"""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def set_ceiling(self, scope, line, units, *, source):  # noqa: ANN001
        self.sources.append(source)
        return super().set_ceiling(scope, line, units, source=source)


def test_an_undeclared_ceiling_stops_the_run_instead_of_running_unbounded(tmp_path, account) -> None:
    """没声明上限 → 抛，不是「那就随便跑」。

    这是本文件里唯一一条**会让人不舒服**的判据：它意味着给 profile 漏写一个键，
    整个 run 直接失败。那正是要的 —— 缺省成「无上限」的话，「忘了写」与「故意放开」
    在任何一份报告上都不区分。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=_MISSING)
    pipeline = _pipeline(settings, bundle, ledger=InMemoryBudgetLedger())

    with pytest.raises(BudgetNotConfiguredError) as excinfo:
        pipeline.run_account(account)

    # 报错必须指出是哪个文件哪个键 —— 否则运维得去猜是哪一个 profile 的哪一项。
    assert "search.max_queries" in str(excinfo.value)
    assert bundle.profile_id in str(excinfo.value)


def test_a_declared_zero_is_refused_too(tmp_path, account) -> None:
    """``0`` 与「没声明」不是同一件事，但**都不能**表现为「跑完了、没有候选」。"""
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=0)

    with pytest.raises(BudgetNotConfiguredError):
        _pipeline(settings, bundle, ledger=InMemoryBudgetLedger()).run_account(account)


def test_for_account_refuses_an_undeclared_ceiling_before_touching_the_ledger(tmp_path) -> None:
    """失败发生在**播种之前** —— 账本上不该留下一条没有上限的 scope。"""
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=_MISSING)
    ledger = InMemoryBudgetLedger()

    with pytest.raises(BudgetNotConfiguredError):
        RunBudget.for_account(ledger, bundle, "any-account")

    assert ledger.usage("any-account") == ()


# --- 第二条上限：抓取（B22）-------------------------------------------------------
#
# 上面那一组量的是 ``search.max_queries`` 接没接上。这一组问同一个问题，主语换成
# ``fetch.max_documents`` —— 复核那一路的额度。它**不放在**
# ``tests/test_application_page_refresh.py``：那边已经证明「复核真的按页扣账」
# （对着 ``DOCUMENT`` 科目数 spent），而这里问的是另一件事 —— 那个数字是从
# **哪个键**读来的。上限的来源是装载与播种的事，与页面复核的编排无关；两处各写
# 一份「12 从哪来」的话，漂移的那一份会让复核按检索的额度抓页，而报告上一切正常。

#: 复核那一路的 label 形状：``<主体 id>@<这一轮的时刻>``（``refresh.py`` 拼的）。
#: 本文件只需要一个**能分辨的名字** —— 上限不由名字决定，名字只决定记在谁名下，
#: 而这几条判据要的恰恰是「上限不随名字变」。
_SCOPE_LABEL = "example-one@2026-09-26T00:00:00+00:00"


def test_the_document_ceiling_comes_from_its_own_key(tmp_path) -> None:
    """抓取上限读的是 ``fetch.max_documents``，**不是** ``search.max_queries``。

    两个数字故意取**不同**的值。不这样的话，把 ``declared_document_ceiling``
    写成去读 ``search_max_queries`` 也能全绿 —— 而那种写错不报错：复核会按检索的
    额度抓页（真 profile 里 12 与 20 差着将近一倍），两边同时错而都看不出来。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2, max_documents=7)
    ledger = InMemoryBudgetLedger()

    budget = RunBudget.for_documents(ledger, bundle, _SCOPE_LABEL)

    usage = ledger.usage(f"{bundle.profile_id}:{_SCOPE_LABEL}")
    assert [entry.line for entry in usage] == [BudgetLine.DOCUMENT]
    assert usage[0].ceiling == 7
    # 科目也必须落在抓取上：``for_scope`` 的缺省科目是 ``SEARCH``，一次写成
    # ``for_scope`` 的复核会把抓取扣在检索的账上 —— 两边同时错。
    assert budget.line is BudgetLine.DOCUMENT


def test_the_document_ceiling_source_names_the_fetch_key(tmp_path) -> None:
    """``source`` 指向 ``fetch.max_documents``，不是一个泛泛的「配置」。"""
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = _CapturingLedger()

    RunBudget.for_documents(ledger, bundle, _SCOPE_LABEL)

    assert ledger.sources == [f"profile:{bundle.profile_id}:fetch.max_documents"]


def test_the_two_ceilings_are_seeded_independently(tmp_path) -> None:
    """**一份没声明检索额度的 profile，仍然安排得了复核**；反过来也一样。

    两个 ``for_*`` 共用同一份播种实现（``RunBudget._seeded``），所以这里问的是一个
    真的会走错的形状：共用的若是 ``declared_search_ceiling``，一份没写
    ``search.max_queries`` 的 profile 会让复核也一起炸 —— 而那时报错文本说的是
    「这次复核没有可用的抓取上限」，指的是**另一个文件里的另一个键**，照着它去改
    只会把两个键一起写坏。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=_MISSING, max_documents=7)
    ledger = InMemoryBudgetLedger()

    assert RunBudget.for_documents(ledger, bundle, _SCOPE_LABEL).remaining() == 7

    with pytest.raises(BudgetNotConfiguredError, match=r"search\.max_queries"):
        RunBudget.for_account(ledger, bundle, "example-one")


def test_an_undeclared_document_ceiling_is_refused_instead_of_fetching_unbounded(
    tmp_path,
) -> None:
    """没声明抓取上限 → 抛，不是「那就随便抓」。

    与检索那一条逐字同一条理由，而后果更重一点：抓取会**写盘**，一次「没上限」
    的复核留下的字节不因为它是意外产生的就少一分。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_documents=_MISSING)
    ledger = InMemoryBudgetLedger()

    with pytest.raises(BudgetNotConfiguredError) as excinfo:
        RunBudget.for_documents(ledger, bundle, _SCOPE_LABEL)

    assert "fetch.max_documents" in str(excinfo.value)
    assert bundle.profile_id in str(excinfo.value)


def test_a_declared_zero_document_ceiling_is_refused_too(tmp_path) -> None:
    """``0`` 与「没声明」不是同一件事，但都不能表现为「复核完了、没有变化」。"""
    settings, bundle = _settings_and_bundle(tmp_path, max_documents=0)

    with pytest.raises(BudgetNotConfiguredError, match=r"fetch\.max_documents"):
        RunBudget.for_documents(InMemoryBudgetLedger(), bundle, _SCOPE_LABEL)


def test_for_documents_refuses_an_undeclared_ceiling_before_touching_the_ledger(
    tmp_path,
) -> None:
    """失败发生在**播种之前** —— 账本上不该留下一条没有上限的科目。

    断言的是「一次 ``set_ceiling`` 都没发生」，而不是「那个 scope 上没有行」：
    后者在播种写错了 scope 名字的时候**照样通过**，而那时账本上多出来的正是一条
    没有上限的行。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_documents=_MISSING)
    ledger = _CapturingLedger()

    with pytest.raises(BudgetNotConfiguredError):
        RunBudget.for_documents(ledger, bundle, _SCOPE_LABEL)

    assert ledger.sources == []


# --- 三段式 ---------------------------------------------------------------------


def test_every_sent_query_is_reserved_then_spent(tmp_path, account) -> None:
    """发出的每一次检索都留下**一条已结算**的预留。

    两种走错的方式各有各的现象，而且都不报错：只 reserve 不 spend → 额度永久悬着；
    只 spend 不 reserve（先花后记）→ 上限拦不住同时起飞的那一批。所以断言两侧。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()
    search = RecordingSearch([PAGE_URL])

    result = _pipeline(settings, bundle, ledger=ledger, search=search).run_account(account)

    scope = _scope(bundle, account)
    usage = ledger.usage(scope)[0]
    assert usage.spent == result.metrics.serp_api_calls == len(search.queries)
    assert usage.reserved == 0
    assert ledger.open_reservations(scope) == ()


def test_a_failed_query_keeps_holding_its_budget(tmp_path, account) -> None:
    """检索抛错之后，额度**不还** —— 预留停在 ``RESERVED``。

    §8 逐字：「在超时或 provider-unknown 时，不要默认释放预算」。这条判据里
    「不还」是可观察的：``reserved`` 是 1，``remaining`` 少 1，而且那条预留
    出现在 ``open_reservations`` 里等人对账。

    反面（自动退款）在本地看起来完全一样 —— 只有账单上不一样。所以这里只能靠
    账本自己的状态把它钉死。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()

    result = _pipeline(
        settings, bundle, ledger=ledger, search=_FailsTheFirstQuery()
    ).run_account(account)

    scope = _scope(bundle, account)
    usage = ledger.usage(scope)[0]
    assert usage.reserved == 1
    assert usage.spent == len(build_queries(account, bundle)) - 1
    assert ledger.remaining(scope, BudgetLine.SEARCH) == DECLARED - usage.held
    open_ids = [item.reservation_id for item in ledger.open_reservations(scope)]
    assert len(open_ids) == 1
    assert any("SearchError" in item for item in result.errors)


# --- 已发出、而用量未知（B15）-----------------------------------------------------
#
# 上面那一组量的是「额度不还」—— 账本面。这一组问的是**报告面**怎么把同一件事说
# 出来：一次已经发出的检索失败了，它的用量没有任何地方知道（§8 正因此不肯退款），
# 报告上必须能说出「这一发发出去了，而它的用量我们不知道」。
#
# 缺的从来不是账（那笔预留停在 ``RESERVED``，``open_reservations`` 逐笔读得回来），
# 是报告口径：那一发不进 ``serp_api_calls`` —— 而 ``api_calls`` 在失败时读出来的 0
# 说的是「没有结果可以读」，不是「用量是 0」。两件事压在一个 0 上，一次已经发出去
# 的检索在报告上就与「压根没发」同形，而前者是一条悬着的预留与一笔可能已经记在
# 对方账上的钱。


def test_a_failed_query_is_reported_as_dispatched_with_unknown_usage(
    tmp_path, account
) -> None:
    """发出去了、失败的那一发进「用量未知」那一格，且**不**进 ``serp_api_calls``。

    两个断言必须一起写：只要求「那一发有去向」的话，一个把失败也算进
    ``serp_api_calls`` 的实现照样全绿 —— 而那是**另一个**谎（真实的用量按定义
    未知，编一个 1 与编一个 0 一样没有依据）。所以这里同时钉住两格，并让它们各自
    与账本上的两格对齐：**已知 = 已结算，未知 = 悬着的预留**。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()
    pipeline = _pipeline(settings, bundle, ledger=ledger, search=_FailsTheFirstQuery())

    result = pipeline.run_account(account)

    planned = len(build_queries(account, bundle))
    usage = ledger.usage(_scope(bundle, account))[0]
    metrics = result.metrics

    assert metrics.serp_queries == planned
    assert metrics.serp_api_calls == planned - 1, "失败的那一发被折进了「已知」那一格"
    assert metrics.serp_api_calls_unknown == 1, "那一发在报告上没有去向"
    assert metrics.serp_api_calls_unknown != 0
    assert (metrics.serp_api_calls, metrics.serp_api_calls_unknown) == (
        usage.spent,
        usage.reserved,
    ), "报告面的两格与账本面的两格说的不是同一件事"
    # 这一格要落到报告上（账号那一层），而不是只活在内存里。
    assert metrics.to_dict()["serp_api_calls_unknown"] == 1


def test_the_run_level_report_rolls_the_unknown_up(tmp_path, account) -> None:
    """汇总层也要有这一格：run 级读者不该自己去把 ``accounts[]`` 加起来。

    与 ``serp_queries_skipped`` 同一个理由。注意 ``serp_api_calls`` 在运行那一层
    本来就没有对应格 —— 那是既有的形状，本片不动它；这一格不能跟着缺，因为
    「这一轮有多少发去向不明」是一次要处置的读数，而不是一个可以事后相加的细节。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    report = _pipeline(
        settings, bundle, ledger=InMemoryBudgetLedger(), search=_FailsTheFirstQuery()
    ).run([account])

    assert report.serp_api_calls_unknown == 1
    assert report.to_dict()["serp_api_calls_unknown"] == 1


def test_a_skipped_query_is_not_counted_as_unknown(tmp_path, account) -> None:
    """重跑：付过账的查询**没有发出去**，所以它不进「未知」那一格。

    这是上一条的反面对照。少了它，把「没发」与「发了但不知道花了多少」合进同一格
    的实现仍然全绿（``>= 1`` 照样成立），而报告上「这次一分钱没花」与「这一发去向
    不明」就再也分不开了 —— 两者的处置完全相反：前者是正常重跑，后者要去看账单。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    pipeline = _pipeline(settings, bundle, ledger=InMemoryBudgetLedger())

    pipeline.run_account(account)
    second = pipeline.run_account(account)

    planned = len(build_queries(account, bundle))
    assert second.metrics.serp_queries_skipped == planned
    assert second.metrics.serp_api_calls == 0
    assert second.metrics.serp_api_calls_unknown == 0, "「没发」被记成了「去向不明」"


def test_a_query_that_never_ran_is_not_counted_as_unknown(tmp_path, account) -> None:
    """另一种「没发出去」：撞穿上限的那几条也不进这一格。

    它们的用量**确切地**是 0（``reserve`` 没成立，调用根本没发生），不是「不知道」。
    把它们读成「去向不明」，会让一次被额度截断的运行在报告上多出几笔根本没发生的
    悬案 —— 而真正的未知（发出去了、没回来）恰恰因此更难被看见。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2)
    search = RecordingSearch([PAGE_URL])

    result = _pipeline(settings, bundle, ledger=InMemoryBudgetLedger(), search=search).run_account(
        account
    )

    assert len(search.queries) == 2
    assert result.metrics.search_budget_exhausted is True
    assert result.metrics.serp_api_calls == 2
    assert result.metrics.serp_api_calls_unknown == 0


def test_the_unknown_is_not_eliminated_by_inflating_the_known_counter(
    tmp_path, account
) -> None:
    """反例方向：**不能**靠把未知塞进 ``serp_api_calls`` 来「消除」未知。

    判据落在「派发过几次」这件事上：提供方被进入了 ``planned`` 次，而回到报告面的
    只有 ``planned - 1`` 次结果。那少掉的一次**不是** 0 次调用（它发出去了），也
    **不是** 1 次调用（真实的用量按定义不知道）—— 所以它既不能落在
    ``serp_api_calls`` 里，也不能被那一格吸收掉。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()
    search = _FailsTheFirstQuery()

    result = _pipeline(settings, bundle, ledger=ledger, search=search).run_account(account)

    dispatched = search.calls
    planned = len(build_queries(account, bundle))
    assert dispatched == planned, "本判据要的是「全部查询都派发了」，前提不成立"
    assert result.metrics.serp_api_calls == dispatched - 1
    assert result.metrics.serp_api_calls < dispatched, "未知被折进了已知那一格"
    # 账本那一侧说的同一个事实：已结算的比派发出去的少一次。
    assert ledger.usage(_scope(bundle, account))[0].spent == dispatched - 1


# --- 上限真的拦得住 --------------------------------------------------------------


def test_the_ceiling_stops_the_run_and_the_report_says_why(tmp_path, account) -> None:
    """额度用尽 → 停下来，并且**报告上能看出是这个原因**。

    「预算用尽」与「候选枯竭」在两个地方都不同形：``search_budget_exhausted``
    是个标志位，而不是让 ``candidate_urls`` 停在 0 上由人去猜。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2)
    ledger = InMemoryBudgetLedger()
    search = RecordingSearch([PAGE_URL])

    result = _pipeline(settings, bundle, ledger=ledger, search=search).run_account(account)

    planned = len(build_queries(account, bundle))
    assert planned > 2, "这条判据要有意义，profile 的查询数必须多于上限"
    assert len(search.queries) == 2
    assert result.metrics.serp_api_calls == 2
    assert result.metrics.serp_queries == planned
    assert result.metrics.search_budget_exhausted is True
    assert ledger.remaining(_scope(bundle, account), BudgetLine.SEARCH) == 0


def test_the_reservation_is_what_stops_the_run(tmp_path, account) -> None:
    """**正对照**：同一个上限，拿掉预留之后拦不住了。

    没有这一条，``test_the_ceiling_stops_the_run_and_the_report_says_why`` 的
    前两行断言可能只是因为 ``build_queries`` 恰好产出两次查询。对照组把预留整个
    拿掉，于是它发出全部查询 —— 判据量的是预留本身。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2)
    real = RecordingSearch([PAGE_URL])
    control = RecordingSearch([PAGE_URL])

    real_result = _pipeline(settings, bundle, ledger=InMemoryBudgetLedger(), search=real).run_account(
        account
    )
    control_result = _DoesNotReserve(
        _deps(
            settings,
            bundle,
            search=control,
            http=FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.OK, "<html></html>")}),
            budget=InMemoryBudgetLedger(),
        )
    ).run_account(account)

    assert len(real.queries) == 2 and real_result.metrics.search_budget_exhausted is True
    assert len(control.queries) == len(build_queries(account, bundle))
    assert control_result.metrics.search_budget_exhausted is False


# --- 重跑、账号隔离 --------------------------------------------------------------


def test_a_query_already_paid_for_is_not_sent_again(tmp_path, account) -> None:
    """重跑同一账号：付过账的查询**不重发**，并且这个跳过**是个数字**。

    两个走法都错得起：重发 → 钱花了而账本不记（那条预留早就是 ``spent``），
    账本开始少记；静默跳过 → 「检索过、没东西」与「压根没检索」在报告上同形。
    """
    settings, bundle = _settings_and_bundle(tmp_path)
    ledger = InMemoryBudgetLedger()
    pipeline = _pipeline(settings, bundle, ledger=ledger)

    first = pipeline.run_account(account)
    second = pipeline.run_account(account)

    planned = len(build_queries(account, bundle))
    assert first.metrics.serp_api_calls == planned
    assert first.metrics.serp_queries_skipped == 0
    assert second.metrics.serp_api_calls == 0
    assert second.metrics.serp_queries_skipped == planned
    assert second.metrics.search_budget_exhausted is False
    # 账本上没有多出一条：整轮重跑一分钱没花。
    assert ledger.usage(_scope(bundle, account))[0].spent == planned


def test_two_accounts_do_not_share_one_budget(tmp_path, account, blocked_account) -> None:
    """§14 的「一个 Account = 一个研究任务」在额度上的形状：**各记各的**。

    如果 scope 是 run 级的，一个跑疯了的账号会把整批账号的额度吃光，而报告上只会
    显示「其余账号没有产出」—— 一个把「A 用超了」读成「B 什么都没找到」的现象。
    """
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2)
    ledger = InMemoryBudgetLedger()
    pipeline = _pipeline(settings, bundle, ledger=ledger)

    pipeline.run_account(account)  # 把 001 的额度用尽
    result_b = pipeline.run_account(blocked_account)

    assert ledger.remaining(_scope(bundle, account), BudgetLine.SEARCH) == 0
    # 002 自己那份额度是**完整的**：它还发得出两次 —— 如果两个账号共用一个 scope，
    # 001 用尽之后 002 一次都发不出去，而报告上只会显示「002 没有产出」。
    assert result_b.metrics.serp_api_calls == 2
    assert result_b.metrics.search_budget_exhausted is True  # 耗尽的是**它自己**那 2 次


def test_the_report_counts_the_accounts_that_hit_the_ceiling(tmp_path, account, blocked_account) -> None:
    """汇总层要能回答「有几个账号被额度截断了」，而不是只有一个布尔。"""
    settings, bundle = _settings_and_bundle(tmp_path, max_queries=2)
    pipeline = _pipeline(settings, bundle, ledger=InMemoryBudgetLedger())

    report = pipeline.run([account, blocked_account])

    assert report.budget_exhausted_accounts == 2
    assert report.serp_queries == 2 * len(build_queries(account, bundle))
    assert report.serp_queries_skipped == 0
