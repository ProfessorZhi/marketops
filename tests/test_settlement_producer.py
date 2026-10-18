"""账上那两格今天**真的有人写** —— 一次押着预留的抓取，在尝试账上指得出那份预留。

## 这一份关的是哪一格

验收矩阵 216 行此前记着一个缺口：抓取面的 ``budget_settlement`` **恒为
``NOT_APPLICABLE``**。两个抓取器的调用点各自写死那一个常量，而它在
``refresh._recheck_page`` 这条路径上是**假的** —— 那一页是先预留、后发请求的，
所以那一次抓取真的发生在**一份扣着额度的预留之下**，账上却写着「我没花钱」。

这一份钉三件事，缺一件那一格就还是一句看着像样的读数：

1. **推导规则**（``domain/fetch_attempt.py::settlement_of``）是全函数，且它产出的
   两格永远写得下去（过 ``FetchAttempt`` 自己的校验器）。推得出来却写不下去的东西
   不叫规则 —— 那种分家在现场表现为「一次抓取已经发出去、账却落不下来」。
2. **请求上那一格**（``FetchRequest.reservation_id``）过形状判据，且那一条判据与
   账本算 id 的推导是**同一个量具**（拒绝而非规范化）。
3. **接进页面复核之后，账上那一行指得回账本里的那一条。** 判据不是「那一格写着
   ``reserved``」这句话，而是「拿行里的 id 去账本里问，问得到那一条，且它记的
   正是这一页」—— 问法跟着那笔钱的结局走：拿到字节的那一次问得到 ``SPENT``
   （钱是它出的），一跳都没发出去的那一次问得到 ``RESERVED`` 且仍然扣着额度
   （那一行是那个泄漏在报告上唯一的痕迹）。用同一个问法套两处会红在一个与判据
   无关的地方，见 ``BudgetBox.ask`` 的 docstring。

## 与邻居的分工

* ``tests/test_fetch_attempt.py`` 钉的是那两格**互相说得通**（分区表 + 校验器）。
* ``tests/test_application_page_refresh.py`` 与 ``tests/test_page_reuse.py`` 钉的是
  一页被取了几次、算了几份。这里不重复它们。
* 零网络：抓取走 ``tests/fixtures/http_transport.py`` 的假传输与假解析器。
"""

from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.budget import RunBudget
from marketops.application.document_router import DocumentRouter
from marketops.application.refresh import build_refresher, document_scope
from marketops.config import Settings
from marketops.domain.budget import check_reservation_id, reservation_id
from marketops.domain.enums import (
    BudgetLine,
    ContactField,
    FetchOutcome,
    ReservationState,
    SourceKind,
)
from marketops.domain.evidence import Evidence
from marketops.domain.fetch_attempt import (
    FetchAttempt,
    MethodKind,
    Settlement,
    SettlementState,
    attempt_id,
    settlement_of,
)
from marketops.domain.maintenance import Aspect, DueReason, DueWork, RefreshPlan
from marketops.domain.util import utc_now
from marketops.ports.acquisition import (
    BrowserFetcher,
    FetchMode,
    FetchRequest,
    FetchResult,
    HttpFetcher,
)
from marketops.ports.dns import DnsFailureKind

from tests.fakes import PageParser
from tests.fixtures.http_transport import FailingResolver, make_fetcher, page

UTC = timezone.utc
T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
PROFILE = "uk-acme-natural-grass"
#: 工作区与 profile 在这里**必须是同一个串**：证据库与尝试库是同一个库
#: （``settings.database_path``），而一个库只装一个工作区。生产里也是同一个串 ——
#: ``interfaces/controller.py`` 用 ``settings.profile`` 当工作区。
WORKSPACE = PROFILE
SUBJECT = "acct-a"
KIND = "account"
PAGE = "https://example.co.uk/contact"
ROLE = Aspect.CONTACT_PERSON_ROLE
BODY = "Contact Us\nEmily Stone is the grounds manager".encode()
#: 一条真的从本账本的推导里算出来的预留 id —— 不是手写的一串十六进制。
HELD = reservation_id("ns:a", BudgetLine.DOCUMENT, f"document:{PAGE}")

#: 会写这一格的四个模块。**读**这一格的工具（``adapters/mcp_server/fetch_attempt.py``）
#: 不在其中：筛选与落账不是一件事。
PRODUCERS = (
    "marketops/adapters/http.py",
    "marketops/adapters/crawl4ai_fetcher.py",
    "marketops/adapters/fetch_attempts.py",
    "marketops/application/refresh.py",
)


# ----------------------------------------------------------------------
# 1. 推导规则：全函数，且产出的东西永远写得下去
# ----------------------------------------------------------------------


def _row(*, settlement: Settlement) -> FetchAttempt:
    """把推导产物落成一行账 —— 与 ``attempt_from`` 落账时的两格逐字对应。"""
    started = utc_now()
    return FetchAttempt(
        attempt_id=attempt_id(
            workspace=WORKSPACE,
            scope=SUBJECT,
            method=MethodKind.HTTP,
            url=PAGE,
            started_at=started,
        ),
        scope=SUBJECT,
        workspace=WORKSPACE,
        method=MethodKind.HTTP,
        url=PAGE,
        started_at=started,
        ended_at=started,
        budget_reservation_id=settlement.reservation_id,
        budget_settlement=settlement.state,
    )


def test_the_rule_answers_both_ways_and_the_answer_is_the_ledger_row() -> None:
    """空名字 ⇔ 不走预算；有名字 ⇔ 押着**那一条**。名字不许被改写。"""
    assert settlement_of("") == Settlement(SettlementState.NOT_APPLICABLE)
    assert settlement_of(HELD) == Settlement(SettlementState.RESERVED, HELD)


def test_what_the_rule_produces_can_always_be_written_down() -> None:
    """推出来的东西直接落账 —— 没有一个输入会推出校验器要拒的那一格。

    「推得出」与「写得下」一旦分家，红的地方是**落账**：请求已经发出去了，账写不
    下来（或者更坏：被写成另一个样子）。这一条把那个分家挡在规则这一侧。
    """
    for reservation in ("", HELD):
        settlement = settlement_of(reservation)
        row = _row(settlement=settlement)
        assert row.budget_settlement is settlement.state
        assert row.budget_reservation_id == settlement.reservation_id


def test_no_fetch_path_claims_a_settlement_it_cannot_know() -> None:
    """**没有哪条抓取路径**声称 ``SETTLED`` 或 ``UNKNOWN_PENDING_RECONCILIATION``。

    那两个成员今天没有生产者，而理由在账的这一侧（见 ``settlement_of`` 的
    docstring）：抓取这一层不消费任何计费额度，而本账本里的预留不会对我们自己未知。
    「没有生产者」是一句关于**全仓**的话，所以判据扫源码，而不是靠读的人记得。

    扫的是**码**不是散文：注释与 docstring 里出现这两个名字，正是上面那些理由所在。
    """
    #: 一个生产者要么取成员、要么写字面量；两条都算。
    forbidden = {
        "SETTLED",
        "UNKNOWN_PENDING_RECONCILIATION",
        SettlementState.SETTLED.value,
        SettlementState.UNKNOWN_PENDING_RECONCILIATION.value,
    }
    root = Path(__file__).resolve().parents[1]
    for relative in PRODUCERS:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        named = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden
        } | {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in forbidden
        }
        assert not named, (
            f"{relative} 点名了 {sorted(named)} —— 这两个成员今天在抓取这一侧没有"
            "诚实的值可填。真有计费出口的那一天，要改的是 settlement_of（给它加一个"
            "那个出口才拿得到的输入），不是在这里补一句常量"
        )


# ----------------------------------------------------------------------
# 2. 请求上那一格：形状，且与账本用一个量具
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrong",
    [
        pytest.param(f"document:{PAGE}", id="幂等键而不是预留 id"),
        pytest.param(PAGE, id="URL"),
        pytest.param("a" * 31, id="少一位"),
        pytest.param("A" * 32, id="大写十六进制"),
        pytest.param("g" * 32, id="不是十六进制"),
    ],
)
def test_the_request_refuses_anything_that_is_not_a_ledger_row(wrong: str) -> None:
    """这一格写错时落出来的行**仍然说得通** —— 所以只能在这里拒。

    一个幂等键或别处的一个 id 都长得像模像样，只是那一份预留在账本里不存在：
    拿这一行去核账的人核不出东西，而报告上看不出它从一开始就不对。
    """
    with pytest.raises(ValueError, match="FetchRequest.reservation_id"):
        FetchRequest(url=PAGE, reservation_id=wrong)


def test_the_request_accepts_the_empty_string_as_no_reservation() -> None:
    """空串是**一个含义**（这一次与预算没关系），不是「还没填」。"""
    assert FetchRequest(url=PAGE).reservation_id == ""
    assert FetchRequest(url=PAGE, reservation_id="").reservation_id == ""


def test_the_shape_rule_and_the_ledger_derivation_are_one_instrument() -> None:
    """账本算出来的那一条**就是**这一格要的形状。

    两个量具（一个算 id、一个判 id）会漂移，而漂移的形式是「账本里真有一条预留，
    请求上却写不进去」—— 那时唯一的处置是有人把请求那一格留空，于是一份扣着额度的
    预留重新变成看不见的。所以这里直接拿 ``reservation_id`` 的输出喂形状规则。
    """
    for scope, key in (
        ("ns:a", f"document:{PAGE}"),
        ("ns:b", "document:https://x.co/"),
        ("a:b", "c:d"),
    ):
        produced = reservation_id(scope, BudgetLine.DOCUMENT, key)
        assert check_reservation_id(produced, "FetchRequest.reservation_id") == produced


def test_a_fetch_under_no_reservation_still_reads_as_no_reservation(
    settings: Settings,
) -> None:
    """对照：没有押着预留的抓取照旧是「与预算没关系」。

    少了这一条，上面那条推导规则可以退化成「永远写 reserved」而这一份仍然全绿。
    """
    attempts = SqliteFetchAttemptStore(settings.database_path)
    fetcher = make_fetcher(settings, {PAGE: page()}, attempts=lambda: attempts)

    fetcher.fetch(FetchRequest(url=PAGE, scope=SUBJECT))

    (row,) = attempts.list_attempts()
    assert row.budget_settlement is SettlementState.NOT_APPLICABLE
    assert row.budget_reservation_id == ""


# ----------------------------------------------------------------------
# 3. 请求押着的预留要走到**每一跳**去
# ----------------------------------------------------------------------


class _Recorder:
    """记下**请求对象本身**，不是它的 URL —— 这一条判据问的就是同一个对象。"""

    def __init__(self) -> None:
        self.seen: FetchRequest | None = None

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.seen = request
        return FetchResult(
            request=request,
            outcome=FetchOutcome.EMPTY_SHELL,
            tier=self.tier,
            final_url=request.url,
            content_type="text/html",
        )


class RecordingHttp(_Recorder, HttpFetcher):
    name = "recording-http"


class RecordingBrowser(_Recorder, BrowserFetcher):
    name = "recording-browser"


def test_the_reservation_reaches_both_hops_of_one_auto_fetch() -> None:
    """一次 ``auto`` 抓取的两跳押的是**同一份**预留。

    升级那一跳拿到的是**同一个请求对象**（``application/acquisition.py`` 逐字把
    ``request`` 传下去），所以两行账押着同一个预留号是对账**归并**的依据：那一页只有
    一份额度。在升级那一步重建请求（例如顺手改一个 ``purpose``）不会报错，只会让
    第二行账悄悄说「我没花钱」—— 而这一格的全部信息量就在这句话上。
    """
    request = FetchRequest(url=PAGE, scope=SUBJECT, purpose="recheck", reservation_id=HELD)
    http, browser = RecordingHttp(), RecordingBrowser()

    DocumentAcquisition(http=http, browser=browser).fetch_document(request, mode=FetchMode.AUTO)

    assert browser.seen is http.seen is request, "两跳拿到的必须是同一个请求对象"
    assert browser.seen.reservation_id == HELD


# ----------------------------------------------------------------------
# 4. 接进页面复核：账上那一行指得回账本里的那一条
# ----------------------------------------------------------------------


class BudgetBox:
    """一个 ``documents`` 可调用物：按 label 播种一份抓取额度，并记下问过谁。

    与 ``tests/test_application_page_refresh.py`` 里那一个同形但**各自一份**：那两个
    文件测的不是同一个问题，共用一份夹具会让「额度按几份扣」只有一处判据。
    """

    def __init__(self, ceiling: int) -> None:
        self.ledger = InMemoryBudgetLedger()
        self.ceiling = ceiling
        self.labels: list[str] = []

    def __call__(self, label: str) -> RunBudget:
        self.labels.append(label)
        scope = self.scope(label)
        self.ledger.set_ceiling(scope, BudgetLine.DOCUMENT, self.ceiling, source="test:fixture")
        return RunBudget(ledger=self.ledger, scope=scope, line=BudgetLine.DOCUMENT)

    def scope(self, label: str) -> str:
        return f"{PROFILE}:{label}"

    def held(self, label: str) -> int:
        return self.ledger.usage(self.scope(label))[0].held

    def open_ids(self, label: str) -> set[str]:
        """账本里**此刻仍然扣着额度**的那些预留 —— 对账要问的就是它。"""
        return {row.reservation_id for row in self.ledger.open_reservations(self.scope(label))}

    def ask(self, label: str, held_id: str):
        """拿账上那一格里的 id **去账本里问**，把问到的整行交回来。

        两条判据问的是同一件事 —— 「这个 id 在这个账本里真的有东西吗」—— 但**问法
        必须跟着那笔钱的结局走**，否则判据会红在一个与它无关的地方：

        * 拿到了字节的那一次，``spend`` 已经落地，那份预留**不再是 open 的**
          （``InMemoryBudgetLedger.open_reservations`` 只挑 ``RESERVED``），
          所以按 ``open_ids`` 问会得到空集 —— 而空集与「id 是编的」长得一模一样。
        * 一跳都没发出去的那一次不 ``spend``，它就**还在** open 里。

        所以这里统一走 ``get``（按 id 取，不看状态），由判据自己说清它期望哪种状态。
        """
        found = self.ledger.get(held_id)
        if found is None or found.scope != self.scope(label):
            return None
        return found


def _due() -> DueWork:
    return DueWork(
        scene_id="scene-a",
        scene_version=1,
        subject_kind=KIND,
        subject_id=SUBJECT,
        aspect=ROLE,
        reasons=(DueReason.NEVER_OBSERVED,),
        period="never",
        due_at=T0,
    )


def _refresher(
    settings: Settings,
    *,
    attempts: SqliteFetchAttemptStore,
    box: BudgetBox,
    store: SqliteEvidenceStore,
    root: Path,
    resolver=None,
):
    """一个**真的**抓取器接进复核入口 —— 只有它会在尝试账上落行。"""
    fetcher = make_fetcher(
        settings,
        {PAGE: page(body=BODY.decode())},
        resolver=resolver,
        attempts=lambda: attempts,
    )
    return build_refresher(
        store=store,
        root=root,
        workspace=WORKSPACE,
        at=T0,
        plan=RefreshPlan.uncalibrated_default(),
        acquisition=DocumentAcquisition(http=fetcher),
        router=DocumentRouter([PageParser("html")]),
        documents=box,
    )


@pytest.fixture
def wire(tmp_path: Path, settings: Settings):
    """库那一侧「我们记得这一页，而且记得够久了」的一行。

    复核计划由它长出页面目标，而**过期**这一半是承重的：新鲜的证据不触发取回，
    于是这条判据会在「没有尝试账」上红（``REUSED``），而不是在预留那一格上。
    """
    store = SqliteEvidenceStore(settings.database_path)
    store.put_evidence(
        Evidence(
            workspace=WORKSPACE,
            evidence_id="e0",
            account_id=SUBJECT,
            source_url=PAGE,
            source_kind=SourceKind.FETCHED_HTML,
            literal_value="grounds@example.co.uk",
            content_sha256=hashlib.sha256(BODY).hexdigest(),
            fetched_at=T0 - timedelta(days=400),
            field=ContactField.EMAIL,
            parser="html",
        )
    )
    return (
        settings,
        store,
        SqliteFetchAttemptStore(settings.database_path),
        BudgetBox(ceiling=12),
        tmp_path / "state" / "maintenance",
    )


def test_the_attempt_row_points_back_at_the_reservation_that_paid_for_it(wire) -> None:
    """够得到的那一页：账上那一行指着账本里**真的**那一条，且钱真的是它出的。

    判据是**连接**不是措辞：拿行里的 id 去账本里问，问得到那一行，它的科目、作用域
    与幂等键都指回**这一页**，而且它的状态是 ``SPENT`` —— 这一次抓取拿到字节，
    ``refresh`` 就在那一份预留上落了账。只断言「那一格写着 reserved」的实现可以编一个
    id 出来而这条判据不会红 —— 而编出来的 id 正是对账的人核不出东西的那一种。

    这一条**不**断言它还在 ``open_reservations`` 里：``spend`` 之后它本来就不在了，
    而「仍然扣着额度」那一半由够不着的那一条判据承担（那里一分钱没花，所以它还在）。
    """
    settings, store, attempts, box, root = wire

    _refresher(settings, attempts=attempts, box=box, store=store, root=root)(_due())

    (row,) = attempts.list_attempts()
    label = document_scope(SUBJECT, T0)
    assert row.budget_settlement is SettlementState.RESERVED
    held = box.ask(label, row.budget_reservation_id)
    assert held is not None, "这一行指的那份预留，在账本里按 id 问得到"
    assert held.line is BudgetLine.DOCUMENT and held.idempotency_key == f"document:{PAGE}"
    assert held.state is ReservationState.SPENT, "字节落地了，钱是从这一份预留上出的"
    assert row.budget_reservation_id == reservation_id(
        box.scope(label), BudgetLine.DOCUMENT, f"document:{PAGE}"
    ), "同一个量具：账上那一格与账本算出来的是同一个串"


def test_a_page_that_never_left_this_machine_still_names_the_reservation(wire) -> None:
    """**够不着**的那一页：一跳都没发出去、一分钱没花，而额度仍然扣着。

    这是这一格最该被看见的一行。够不着时不 ``spend``（§8：那时你并不知道对方是否
    已经计费），所以那份预留会一直扣着，直到有人显式退款 —— 而在此之前，这一行是
    那笔「没对账」在报告上**唯一的**痕迹。写成「我没花钱」会让这个泄漏完全看不见：
    额度少了一份，而账上找不出是谁扣的。
    """
    settings, store, attempts, box, root = wire

    _refresher(
        settings,
        resolver=FailingResolver(DnsFailureKind.NONEXISTENT),
        attempts=attempts,
        box=box,
        store=store,
        root=root,
    )(_due())

    (row,) = attempts.list_attempts()
    label = document_scope(SUBJECT, T0)
    assert row.redirect_trace == () and row.observed_status is None, "这一次真的没发出去"
    assert not row.body_kept
    assert row.budget_settlement is SettlementState.RESERVED, "没花掉不等于没押着"
    held = box.ask(label, row.budget_reservation_id)
    assert held is not None, "这一行指的那份预留，在账本里按 id 问得到"
    assert held.state is ReservationState.RESERVED, "一分钱没花，所以它还扣着额度"
    assert row.budget_reservation_id in box.open_ids(label), "而且在「还扣着」的那一批里"
    assert box.held(label) == 1, "额度确实扣着 —— 那一行是它唯一的痕迹"
