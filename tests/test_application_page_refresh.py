"""``application/refresh.py`` 的**页面复核**那一半：真的取一遍，然后如实报出取了什么。

## 这一层要挡的六件事，各自有一条判据

1. **同一批页面被取两次** —— 一轮 tick 里两个页面面（联系人与角色 / 邮箱证据）可以
   同时到期，而它们看的是同一个主体名下同一批 URL。取两次的账上写着「这一轮看了
   2 × N 页」，而那批页面只被看过一次。
2. **拿镜像目录当「已经有了」** —— ``Settings.mirrors_dir`` 跨工作区共用，一次别的
   Scene 的抓取会在里面留下文件。把它当成复用依据，等于让一个工作区拿别人的抓取
   给自己续期，而报告上只显示「这一轮没花钱」。
3. **没看全却给整格续期** —— 十页看了九页仍然是不确定：剩下那一页可能正写着这一格
   已经变了。判据是**不写观测**，而逐页的结论照旧逐条写出。
4. **额度不够时「挑几页看看，都挺好」** —— 那正是第 3 条的一个特例，而它更隐蔽：
   花的钱更少、报告更干净。
5. **缺接线时崩掉** —— 一个没接抓取的部署仍然要能 tick（来源供给那一面照常复核），
   而页面那几格的处置是「这一趟没做成，因为缺 X」。
6. **读到一份坏记录却报「0 个缺口」** —— 记录读不出来与读到一个好消息是两件事。

全部零网络：抓取走一个写盘的假 fetcher，解析走一个假 parser。判据是编排，不是适配器。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.budget import RunBudget
from marketops.application.document_router import DocumentRouter
from marketops.application.evidence_ingest import document_evidence, title_evidence
from marketops.application.refresh import (
    CENSUS_BACKED_ASPECTS,
    COVERAGE_GAPS,
    PAGE_BACKED_ASPECTS,
    REFRESHABLE_ASPECTS,
    WIRING_MEANING,
    build_refresher,
    content_digest,
    document_scope,
    load_census,
    load_recheck,
    missing_wiring,
    recheck_path,
)
from marketops.domain.enums import BudgetLine, ContactField, FetchOutcome, SourceKind
from marketops.domain.errors import BudgetNotConfiguredError
from marketops.domain.evidence import Evidence
from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    DueReason,
    DueWork,
    RefreshPlan,
    RefreshRule,
    uncovered_aspects,
)

from tests.fakes import FakeHttp, PageParser, synthetic_scene_files

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
WORKSPACE = "ws-natural-grass"
OTHER_WORKSPACE = "ws-kent"
PROFILE = "uk-acme-natural-grass"
SUBJECT = "acct-a"
KIND = "account"
PAGE = "https://example.co.uk/contact"
OTHER_PAGE = "https://example.co.uk/team"
THIRD_PAGE = "https://example.co.uk/about"

ROLE = Aspect.CONTACT_PERSON_ROLE
EMAIL = Aspect.EMAIL_EVIDENCE


# ----------------------------------------------------------------------
# 替身：一个写盘的 fetcher，一个从文件读的 parser
#
# 这两样住在 ``tests/fakes.py``（**不是**住在这里）：MCP 那一层要用**同一对**替身
# 问同一个问题（「一轮 tick 里那批页面被取了几次」）。两个文件各写一份的话，两份会在
# 「什么叫一次抓取」上漂移，而漂移的那一份恰好会让「工具层多抓了一次」看起来是干净的。
# ----------------------------------------------------------------------


class BudgetBox:
    """一个 ``documents`` 可调用物：按 label 播种一份抓取额度，并记下问过谁。

    播种那一步照抄 ``RunBudget.for_documents`` 的形状（scope 是
    ``<profile>:<label>``、科目是 ``DOCUMENT``），但**不经过 profile** ——
    这一份测的是编排对额度的用法，而「上限从哪来」有它自己的判据
    （``declared_document_ceiling`` 与 ``for_documents`` 上那一条）。
    """

    def __init__(self, ceiling: int) -> None:
        self.ledger = InMemoryBudgetLedger()
        self.ceiling = ceiling
        self.labels: list[str] = []

    def __call__(self, label: str) -> RunBudget:
        self.labels.append(label)
        scope = f"{PROFILE}:{label}"
        self.ledger.set_ceiling(
            scope, BudgetLine.DOCUMENT, self.ceiling, source="test:fixture"
        )
        return RunBudget(ledger=self.ledger, scope=scope, line=BudgetLine.DOCUMENT)

    def held(self, label: str) -> int:
        return self.ledger.usage(f"{PROFILE}:{label}")[0].held


# ----------------------------------------------------------------------
# 夹具与助手
# ----------------------------------------------------------------------


def _due(*, aspect: Aspect = ROLE, subject_id: str = SUBJECT) -> DueWork:
    return DueWork(
        scene_id="scene-a",
        scene_version=1,
        subject_kind=KIND,
        subject_id=subject_id,
        aspect=aspect,
        reasons=(DueReason.NEVER_OBSERVED,),
        period="never",
        due_at=T0,
    )


def _row(
    index: int,
    url: str,
    *,
    digest: str,
    parser: str = "html",
    fetched_at: datetime = T0,
    subject_id: str = SUBJECT,
    workspace: str = WORKSPACE,
    kind: SourceKind = SourceKind.FETCHED_HTML,
) -> Evidence:
    """一条**手工构造的**证据行：库那一侧「我们已经记得这一页」的那一行。

    刻意不走 ``evidence_ingest``：这一层的复用判定读的是**库里的行**，而这一条
    判据要说的是「库里放着这样一行时它会怎么做」。用生产的落盘路径种数据，会让
    「这一行的形状对不对」变成这个文件的第二个被测量对象。
    """
    return Evidence(
        workspace=workspace,
        evidence_id=f"e{index}",
        account_id=subject_id,
        source_url=url,
        source_kind=kind,
        literal_value="grounds@example.co.uk",
        content_sha256=digest,
        fetched_at=fetched_at,
        field=ContactField.EMAIL,
        parser=parser,
    )


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> SqliteEvidenceStore:
    return SqliteEvidenceStore(tmp_path / "state" / "marketops.db")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state" / "maintenance"


@pytest.fixture
def mirrors(tmp_path: Path) -> Path:
    return tmp_path / "state" / "mirrors"


def _wired(
    *,
    store: SqliteEvidenceStore,
    root: Path,
    mirrors: Path,
    bodies: dict[str, bytes],
    ceiling: int = 12,
    plan: RefreshPlan | None = None,
    parser: str = "html",
    failures: dict[str, FetchOutcome] | None = None,
) -> tuple[FakeHttp, BudgetBox, DocumentRouter, RefreshPlan]:
    http = FakeHttp(bodies, root=mirrors, failures=failures)
    box = BudgetBox(ceiling)
    router = DocumentRouter([PageParser(parser)])
    resolved = plan or RefreshPlan.uncalibrated_default()
    assert store is not None and root is not None
    return http, box, router, resolved


def _refresher(
    store: SqliteEvidenceStore | None,
    root: Path,
    *,
    at: datetime = T0,
    plan: RefreshPlan | None = None,
    http: FakeHttp | None = None,
    documents: BudgetBox | None = None,
    router: DocumentRouter | None = None,
):
    """照 ``build_refresher`` 的签名把接线拼起来；没给的那些**就是没接线**。"""
    return build_refresher(
        store=store,
        root=root,
        workspace=WORKSPACE,
        at=at,
        plan=plan,
        acquisition=None if http is None else DocumentAcquisition(http=http),
        router=router,
        documents=documents,
    )


# ----------------------------------------------------------------------
# 1. 一轮 tick 里两个面共用同一批页面
# ----------------------------------------------------------------------


def test_two_page_aspects_share_one_fetch_per_page(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """两个页面面同轮到期 ⇒ **每一页恰好抓 1 次**，而两格各自都写了一条观测。

    这是这一片最贵的一条：两个面看的是同一批 URL，而「抓了两次」在报告上表现为
    「这一轮覆盖面很广」—— 它不会被读成一个错误，只会被读成一个更好的数字。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest=_digest(raw), fetched_at=T0 - timedelta(days=400)))
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    role = refresh(_due(aspect=ROLE))
    email = refresh(_due(aspect=EMAIL))

    assert http.calls == [PAGE], "同一页在同一轮里被抓了不止一次"
    assert role.recheck.value == "unchanged"
    assert email.recheck.value == "reused", "第二个面应当复用第一趟的结果"
    assert role.observed_at == T0 and email.observed_at == T0
    # 两格的逐页结论说的是同一批页面上的同一件事 —— 备忘共享的是**取回**。
    assert load_recheck(Path(role.result_ref))["pages"] == load_recheck(Path(email.result_ref))[
        "pages"
    ]


def test_the_shared_pages_do_not_buy_the_other_aspect_a_fresh_window(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """**共享的是取回，不是判断。** 一页都没取的那一趟不进备忘。

    两个页面面的时效窗口可以不同（``refresh.rules`` 逐面声明）。让 A 面（窗口 90 天）
    判定「还很新鲜」的判断替 B 面（窗口 30 天）作证，就是拿一个没被问过的问题的答案
    去回答另一个问题 —— 而它省下的是零次抓取（A 面本来就没抓）。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest=_digest(raw), fetched_at=T0 - timedelta(days=60)))
    plan = RefreshPlan(
        rules=tuple(
            RefreshRule(
                aspect=aspect,
                after_days={ROLE: 90, EMAIL: 30}.get(aspect, 180),
            )
            for aspect in ALL_ASPECTS
        )
    )
    http, box, router, resolved = _wired(
        store=store, root=root, mirrors=mirrors, bodies={PAGE: raw}, plan=plan
    )
    refresh = _refresher(store, root, plan=resolved, http=http, documents=box, router=router)

    role = refresh(_due(aspect=ROLE))
    assert http.calls == [], "90 天的窗口里，60 天前取的那一页不该再抓"
    assert role.recheck.value == "reused"
    assert role.observed_at == T0 - timedelta(days=60), "复用写的是那份证据被取回的时刻"

    email = refresh(_due(aspect=EMAIL))
    assert http.calls == [PAGE], "30 天的窗口里，60 天前取的那一页必须重取"
    assert email.recheck.value == "unchanged"


# ----------------------------------------------------------------------
# 2. 复用判定读证据行，不读文件系统
# ----------------------------------------------------------------------


def test_a_fresh_page_costs_no_fetch(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """手上的证据还在时效内、解析器也没换 ⇒ 一次请求都不发。"""
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest=_digest(raw), fetched_at=T0))
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    assert http.calls == []
    assert box.labels == [], "一次抓取都没发生，就不该去问额度"
    record = load_recheck(Path(outcome.result_ref))
    assert record["state"] == "reused"
    assert record["reused_evidence_ids"] == ["e0"]
    assert record["evidence_ids"] == []


def test_a_mirror_file_on_disk_does_not_confer_freshness(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """镜像目录里躺着一份文件，**不构成复用依据** —— 那一页仍然要去取。

    ``mirrors_dir`` 跨工作区共用：一次别的 Scene（或别的部署）的抓取会在里面留下
    文件。把它读成「我们已经有这一页了」，报告上只会显示「这一轮没花钱」。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    stale = ReuseWindow.expired(ROLE)
    store.put_evidence(_row(0, PAGE, digest=_digest(raw), fetched_at=T0 - stale))
    mirrors.mkdir(parents=True, exist_ok=True)
    # 一份「看起来刚刚写下」的镜像：内容还一样，文件名与真抓取出来的那个同名。
    (mirrors / f"{hashlib.sha256(PAGE.encode()).hexdigest()[:16]}.html").write_bytes(raw)
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    assert http.calls == [PAGE], "盘上有文件不是复用依据 —— 那一页的时效是按证据行算的"
    assert outcome.recheck.value == "unchanged"


class ReuseWindow:
    """按计划里那一面的 ``after_days`` 造一个「已经过期」的时长。

    数值不当场手写：窗口改过之后，「过期」这个前提跟着改，而一条写死 400 天的
    判据会在窗口变成 500 天之后**仍然绿**（它测的其实是「够老」而不是「过期」）。
    """

    @staticmethod
    def expired(aspect: Aspect) -> timedelta:
        days = RefreshPlan.uncalibrated_default().rule_for(aspect).after_days
        return timedelta(days=days + 5)


def test_another_subjects_pages_are_not_this_cells_pages(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """别人的页面不是这一格的页面 —— 一格都没有时**说不出「没变」**。

    这一趟的处置是 ``UNREACHABLE`` 加一条失败理由（于是下一轮还会被领一次），
    不是一次「复核完了、没有变化」。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0, subject_id="acct-b")
    )
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    assert http.calls == []
    assert outcome.recheck.value == "unreachable"
    assert outcome.observed_at is None
    assert "没有一页可复核" in outcome.failure
    assert load_recheck(Path(outcome.result_ref))["pages"] == []


def test_the_same_url_is_one_page_however_many_rows_it_has(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """同一页的正文行与标题行是**一页**，不是两页。

    按行去重的形态是「这一页上有几个字段就抓几次」，而报告上显示为「看了很多页」。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest="old-digest", fetched_at=T0 - ReuseWindow.expired(ROLE)))
    store.put_evidence(
        _row(1, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    assert http.calls == [PAGE]
    assert load_recheck(Path(outcome.result_ref))["page_set_size"] == 1


# ----------------------------------------------------------------------
# 3. 变了、没变、够不着
# ----------------------------------------------------------------------


def test_a_changed_page_lands_new_evidence_and_says_what_changed(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """指纹变了 ⇒ 整格 ``CHANGED``，而且**新证据落到了盘上**。

    ``CHANGED`` 的契约里那半句「于是落了新证据」是这一条判据的全部要点：一个「变了
    但没落上」的结论下一轮还会再看到同一件事。
    """
    before = b"Contact Us\nEmily Stone is the grounds manager"
    after = b"Contact Us\nMarcus Reed is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest=_digest(before), fetched_at=T0 - ReuseWindow.expired(ROLE)))
    before_rows = len(store.list_evidence(SUBJECT))
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: after})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    record = load_recheck(Path(outcome.result_ref))
    page = record["pages"][0]
    assert record["state"] == "changed"
    assert page["cause"] == ["content"]
    assert page["observed"] == _digest(after)
    assert page["expected"] == _digest(before)
    assert record["evidence_ids"], "变了却没有落新证据 —— 下一轮还会再看到同一次变更"
    landed = store.list_evidence(SUBJECT)
    assert len(landed) > before_rows
    assert {row.content_sha256 for row in landed[before_rows:]} == {_digest(after)}


def test_a_parser_swap_alone_is_a_change(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """同一份字节、换了抽法 ⇒ 也是 ``CHANGED``，而理由那一格写的是 ``parser``。

    「世界变了」与「我们换了抽法」对业务的影响完全不同，所以它们分开记 ——
    合起来记的后果是解析器一升级，报告上全是「场地信息变了」。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), parser="old-parser", fetched_at=T0 - timedelta(days=400))
    )
    http, box, router, plan = _wired(
        store=store, root=root, mirrors=mirrors, bodies={PAGE: raw}, parser="html"
    )
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    page = load_recheck(Path(outcome.result_ref))["pages"][0]
    assert page["cause"] == ["parser"]
    assert page["parser_before"] == "old-parser" and page["parser"] == "html"


def test_an_unreachable_page_leaves_the_whole_cell_open(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """一页够不着 ⇒ 整格 ``UNREACHABLE``、**不写观测**，另一页的结论照旧在记录里。

    没看全就不给整格续期：没看到的那一页里可能正写着这一格已经变了。而「哪一页没看成」
    必须读得出来 —— 一句笼统的不可达会让人从头查起。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    stale = ReuseWindow.expired(ROLE)
    store.put_evidence(_row(0, PAGE, digest=_digest(raw), fetched_at=T0 - stale))
    store.put_evidence(_row(1, OTHER_PAGE, digest=_digest(raw), fetched_at=T0 - stale))
    http, box, router, plan = _wired(
        store=store,
        root=root,
        mirrors=mirrors,
        bodies={PAGE: raw},
        failures={OTHER_PAGE: FetchOutcome.TIMEOUT},
    )
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    record = load_recheck(Path(outcome.result_ref))
    assert outcome.recheck.value == "unreachable"
    assert outcome.observed_at is None, "没看全就不许写观测"
    by_url = {page["url"]: page for page in record["pages"]}
    assert by_url[PAGE]["state"] == "unchanged", "看成了的那一页的结论要留着"
    assert by_url[OTHER_PAGE]["state"] == "unreachable"
    assert "timeout" in by_url[OTHER_PAGE]["error"].lower()
    assert record["examined"] == 1 and record["page_set_size"] == 2


# ----------------------------------------------------------------------
# 4. 额度
# ----------------------------------------------------------------------


def test_a_budget_smaller_than_the_page_set_defers_the_rest(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """额度只够两页、而页面有三页 ⇒ 整格不可达，逐页写出**哪一页没被看**。

    「挑两页看看，都挺好」是这一条要挡的形态：花的钱更少、报告更干净，而整格因此
    续期一个周期。所以额度不够的处置是**整格不成立**，不是部分成立。
    """
    pages = (PAGE, OTHER_PAGE, THIRD_PAGE)
    bodies = {url: f"Contact Us\nEmily Stone at {url}".encode() for url in pages}
    stale = ReuseWindow.expired(ROLE)
    for index, url in enumerate(pages):
        store.put_evidence(_row(index, url, digest=_digest(bodies[url]), fetched_at=T0 - stale))
    http, box, router, plan = _wired(
        store=store, root=root, mirrors=mirrors, bodies=bodies, ceiling=2
    )
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    record = load_recheck(Path(outcome.result_ref))
    assert outcome.recheck.value == "unreachable"
    assert outcome.observed_at is None
    assert len(http.calls) == 2, "额度是 2，抓取就该是 2 次"
    deferred = [page for page in record["pages"] if page["deferred"]]
    assert len(deferred) == 1
    assert deferred[0]["deferred"].startswith("document_budget_exhausted")
    assert record["examined"] == 2 and record["page_set_size"] == 3
    assert "没看全" in outcome.failure


def test_the_grant_is_named_for_one_subject_at_one_time(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """额度的 scope 里带着**主体**与**这一轮的时刻**，两样都进 label。

    不带时刻的 label 意味着「这个主体一辈子就这么多」—— 账本没有充值机制，于是
    一个长期经营的部署在抓满之后再也复核不了，而报告上显示的是「没有到期的格子」。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    refresh(_due(aspect=ROLE))

    assert box.labels == [document_scope(SUBJECT, T0)]
    assert document_scope(SUBJECT, T0) == f"{SUBJECT}@{T0.isoformat()}"
    assert T0.isoformat() in box.labels[0]


def test_a_profile_without_a_declared_document_ceiling_fails_loudly(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """profile 没声明 ``fetch.max_documents`` ⇒ **抛**，而那句话说的是「去写哪一格」。

    它被 ``run_once`` 接住之后记成这一格的 FAILED（见 ``application/maintenance.py``），
    而那句话逐字保留。**刻意不接住它**：一个「没写就按 N 份跑」的缺省会让「忘了声明
    额度」与「故意给 N 份」在行为上不可区分。
    """
    from marketops.domain.profile import DeclaredProfile, ProfileBundle

    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    http = FakeHttp({PAGE: raw}, root=mirrors)
    files = synthetic_scene_files()
    bundle = ProfileBundle(
        profile_id=PROFILE,
        status="TEST",
        rules={},
        role_terms=(),
        scene_version=files.version,
        scene_files=files,
        profile=DeclaredProfile(
            schema="marketops/profile.v2",
            name=PROFILE,
            display_name="synthetic",
            country="GB",
            exclusions=(),
            compliance={},
        ),
    )
    assert bundle.profile.fetch_max_documents is None, "这一条判据要的就是「没声明」"

    def documents(label: str) -> RunBudget:  # pragma: no cover —— 它必须不被调到
        return RunBudget.for_documents(InMemoryBudgetLedger(), bundle, label)

    refresh = _refresher(
        store,
        root,
        plan=RefreshPlan.uncalibrated_default(),
        http=http,
        documents=documents,
        router=DocumentRouter([PageParser()]),
    )

    with pytest.raises(BudgetNotConfiguredError, match="fetch.max_documents"):
        refresh(_due(aspect=ROLE))


def test_the_declared_ceiling_is_what_the_profile_says(
    settings, bundle, store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """真 profile 的那一条上限**真的接得上**：没接线与接线完了是两件事。

    这一条对着 ``profiles/uk-acme-natural-grass/profile.yaml`` 跑，用真
    ``for_documents`` —— 上面那条判据证明「没声明时会炸」，这一条证明「声明了就能跑」。
    """
    store.put_evidence(_row(0, PAGE, digest="stale", fetched_at=T0 - timedelta(days=400)))
    http = FakeHttp({PAGE: b"Contact Us\nEmily Stone is the grounds manager"}, root=mirrors)
    ledger = InMemoryBudgetLedger()
    refresh = _refresher(
        store,
        root,
        plan=RefreshPlan.uncalibrated_default(),
        http=http,
        documents=lambda label: RunBudget.for_documents(ledger, bundle, label),
        router=DocumentRouter([PageParser()]),
    )

    outcome = refresh(_due(aspect=ROLE))

    assert outcome.recheck.value == "changed"
    usage = ledger.usage(f"{bundle.profile_id}:{document_scope(SUBJECT, T0)}")
    assert [entry.spent for entry in usage] == [1], "抓了一页就该在抓取科目上记 1"


# ----------------------------------------------------------------------
# 5. 缺接线是一条被记录下来的失败
# ----------------------------------------------------------------------


def test_missing_wiring_is_recorded_not_raised(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """四样接线一样都没有 ⇒ 页面那一格记成不可达 + 逐页写出缺了谁，**不抛**。

    同一台 refresher 上 ``source_coverage`` 照常跑完 —— 一个没接抓取的部署仍然要能
    tick，而它页面那几格的状态是「这一趟没做成，因为缺 X」，不是一次崩溃。
    """
    store.put_evidence(_row(0, PAGE, digest="sha-0", fetched_at=T0 - timedelta(days=400)))
    refresh = _refresher(store, root)

    outcome = refresh(_due(aspect=ROLE))
    supply = refresh(_due(aspect=Aspect.SOURCE_COVERAGE))

    assert outcome.recheck.value == "unreachable"
    assert outcome.observed_at is None
    record = load_recheck(Path(outcome.result_ref))
    assert record["pages"][0]["deferred"] == "not_wired:" + ",".join(WIRING_MEANING)
    for name in WIRING_MEANING:
        assert WIRING_MEANING[name] in outcome.failure
    assert supply.source_delta is not None, "来源供给那一面不依赖这四样接线"
    assert supply.observed_at is not None


def test_the_wiring_names_and_the_meaning_table_are_the_same_set() -> None:
    """``missing_wiring`` 报出来的名字就是 :data:`WIRING_MEANING` 的键。

    回执那一侧逐字转发这几个名字（``product_loop`` 的 coverage 栏），所以这是一份
    对外契约：两张表分家的后果是报告里出现一个**没有解释**的名字。
    """
    assert missing_wiring() == tuple(WIRING_MEANING)
    assert missing_wiring(plan=RefreshPlan.uncalibrated_default()) == tuple(
        name for name in WIRING_MEANING if name != "plan"
    )


def test_a_page_backed_aspect_refuses_the_other_aspects(root: Path) -> None:
    """来源供给那一面不走这一条路；走错了**抛**，不返回一个「做完了、什么都没变」。"""
    from marketops.application.refresh import recheck_pages
    from marketops.domain.errors import ConfigError

    with pytest.raises(ConfigError, match=Aspect.SOURCE_COVERAGE.value):
        recheck_pages(
            due=_due(aspect=Aspect.SOURCE_COVERAGE),
            store=None,
            root=root,
            workspace=WORKSPACE,
            at=T0,
        )


# ----------------------------------------------------------------------
# 6. 记录：读得出来、读不出来、以及读成另一种
# ----------------------------------------------------------------------


def test_the_same_recheck_twice_lands_on_one_record(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """两次结论相同 ⇒ 同一个 ``result_ref``，盘上只有一份。

    ``taken_at`` **不进摘要**：含进去的话，「这一格现在是什么样」这个问题的答案
    会永远是「变了」（只有时刻那一格不同）。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    bodies = {PAGE: raw}
    first_http, first_box, router, plan = _wired(
        store=store, root=root, mirrors=mirrors, bodies=bodies
    )
    second_http, second_box, _, _ = _wired(store=store, root=root, mirrors=mirrors, bodies=bodies)

    first = _refresher(store, root, at=T0, plan=plan, http=first_http, documents=first_box, router=router)(
        _due(aspect=ROLE)
    )
    second = _refresher(
        store, root, at=T1, plan=plan, http=second_http, documents=second_box, router=router
    )(_due(aspect=ROLE))

    assert first.result_ref == second.result_ref
    assert first.observed_at != second.observed_at, "两趟是两次跑，各自的时刻不同"
    written = list((root / WORKSPACE).iterdir())
    assert len(written) == 1, f"同一份结论落了两份：{written}"


def test_a_recheck_record_whose_content_was_edited_is_refused(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """内容被改过 ⇒ 读不出来，而**不是**读成一个「没变」。

    只核对「文件里写的那个字符串与文件名一致」等于让文件自己给自己作证：任何一次
    对内容的编辑都不会被发现。
    """
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    outcome = _refresher(store, root, plan=plan, http=http, documents=box, router=router)(
        _due(aspect=ROLE)
    )
    path = Path(outcome.result_ref)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["state"] == "unchanged"
    payload["state"] = "changed"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception, match="内容与它自己声明的摘要对不上"):
        load_recheck(path)


def test_a_census_cannot_be_read_as_a_recheck(store: SqliteEvidenceStore, root: Path) -> None:
    """两种记录落在同一个目录下，而**读成对方**要被拒。

    ``kind`` 进摘要，所以改它会在摘要那一步就红；这一条额外钉的是另一件事：一份
    写着 ``census`` 的记录拿去当复审记录读，必须在**类型**那一关上被挡住。
    """
    from marketops.application.refresh import census_for, write_census

    census = census_for(
        store=store, workspace=WORKSPACE, subject_kind=KIND, subject_id=SUBJECT, at=T0
    )
    path = write_census(census, root=root)

    assert load_census(path)["kind"] == "census"

    with pytest.raises(Exception, match="不是 recheck 记录"):
        load_recheck(path)


def test_a_record_from_before_this_slice_still_reads(store: SqliteEvidenceStore, root: Path) -> None:
    """B22 之前写下的盘点（**没有 ``kind`` 那一格**）照旧读得出来。

    重查记录从写下的第一天起就带 ``kind``，而写下旧文件的那个版本只写过盘点 ——
    所以缺省有唯一解，不是猜。这一条把那个前提变成一条判据：将来谁把 ``kind``
    改成必填，这里会先红。
    """
    from marketops.application.refresh import census_for

    census = census_for(
        store=store, workspace=WORKSPACE, subject_kind=KIND, subject_id=SUBJECT, at=T0
    )
    payload = census.as_dict()
    del payload["kind"]
    content = {
        key: value
        for key, value in payload.items()
        if key not in ("taken_at", "digest", "new_source_gaps", "not_measured")
    }
    payload["digest"] = content_digest(content)
    path = root / WORKSPACE / f"{KIND}--{SUBJECT}--{payload['digest']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    assert load_census(path)["rows"] == 0
    assert "kind" not in load_census(path)


def test_a_recheck_file_path_carries_what_a_reader_needs(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """``recheck_path`` 的四段都在文件名里：工作区目录、主体、面、内容摘要。"""
    raw = b"Contact Us\nEmily Stone is the grounds manager"
    store.put_evidence(
        _row(0, PAGE, digest=_digest(raw), fetched_at=T0 - ReuseWindow.expired(ROLE))
    )
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: raw})
    outcome = _refresher(store, root, plan=plan, http=http, documents=box, router=router)(
        _due(aspect=ROLE)
    )

    path = Path(outcome.result_ref)
    assert path.parent == root / WORKSPACE
    assert path.name.startswith(f"recheck--{KIND}--{SUBJECT}--{ROLE.value}--")
    record = load_recheck(path)
    assert path.name == recheck_path(root, _record_of(outcome, root)).name
    assert record["policy_version"] == plan.policy_version


def _record_of(outcome, root: Path):
    """从一份回执回到它指向的那份记录（``recheck_path`` 需要一个对象）。"""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Record:
        workspace: str
        subject_kind: str
        subject_id: str
        aspect: str
        digest: str

    payload = load_recheck(Path(outcome.result_ref))
    return _Record(WORKSPACE, KIND, SUBJECT, ROLE.value, payload["digest"])


# ----------------------------------------------------------------------
# 7. 声明与分母
# ----------------------------------------------------------------------


def test_the_declared_page_aspects_are_a_subset_of_the_declaration() -> None:
    """``PAGE_BACKED_ASPECTS`` ⊆ ``REFRESHABLE_ASPECTS``，两侧都非空。"""
    assert PAGE_BACKED_ASPECTS
    assert PAGE_BACKED_ASPECTS < REFRESHABLE_ASPECTS


def test_the_two_kinds_of_aspect_partition_the_declaration() -> None:
    """两个新桶**恰好切分** ``REFRESHABLE_ASPECTS``：不相交、不重不漏。

    这一片之前只有一个桶（``REFRESHABLE_ASPECTS`` 自己），而现在它被分成
    「数本地记录的」（``CENSUS_BACKED_ASPECTS``）与「重取页面的」
    （``PAGE_BACKED_ASPECTS``）。两句话都要钉住，因为漏掉的方式不同：

    * **漏一个**（并集少一面）—— 那一面会到 ``build_refresher`` 的派发里才发现没人
      认领，而那时已经是运行期，那一格记成 ``ConfigError``。分母（
      ``REFRESHABLE_ASPECTS``）与实现都不动的话，报告上这一面**照样算已实现**。
    * **重一个**（交集非空）—— ``build_refresher`` 按 ``if/elif`` 派发，第二个桶永远
      走不到；而它仍然算在分母里，于是「接了」的那一面实际上从没跑过。

    两条都不是「多一个少一个」的量级：它们让 §11 那三个数（承诺 / 实现 / 缺口）
    看上去是齐的。
    """
    assert not (CENSUS_BACKED_ASPECTS & PAGE_BACKED_ASPECTS)
    assert CENSUS_BACKED_ASPECTS | PAGE_BACKED_ASPECTS == REFRESHABLE_ASPECTS


def test_the_coverage_gap_is_the_complement_of_the_declaration() -> None:
    """未接的那几个面由 ``uncovered_aspects`` **算**出来，不是一个手写的差集。

    手写的那一份在 ``REFRESHABLE_ASPECTS`` 变大的那天不会跟着变，而它看起来仍然
    正常 —— 少报的正是刚刚接上的那一面（§11 逐字：未接的 aspect 报 coverage gap，
    不能通过 out_of_scope 把必需功能从验收分母删掉）。
    """
    assert COVERAGE_GAPS == uncovered_aspects(REFRESHABLE_ASPECTS)
    assert COVERAGE_GAPS, "四个面还没接 —— 这一条在有实现之前不该变成空"
    assert not set(COVERAGE_GAPS) & set(REFRESHABLE_ASPECTS)
    assert set(COVERAGE_GAPS) | set(REFRESHABLE_ASPECTS) == set(ALL_ASPECTS)


# ----------------------------------------------------------------------
# 8. 落盘：id 相撞与写不进去
# ----------------------------------------------------------------------


def test_an_empty_shell_page_lands_one_row_not_two(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """正文逐字等于标题的空壳页 ⇒ 只落**一行**（标题那一行）。

    两行的 ``evidence_id`` 是内容寻址的，而 ``page_locator`` 不参与寻址 —— 所以它们
    算出同一个 id 而内容不同，存储会**正确地**拒绝。拒绝在这里是一条页级错误，
    而那会把一次成功的变更读成「没落成」。
    """
    before = b"Grounds"
    after = b"Grounds Team"
    store.put_evidence(_row(0, PAGE, digest=_digest(before), fetched_at=T0 - ReuseWindow.expired(ROLE)))
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: after})
    refresh = _refresher(store, root, plan=plan, http=http, documents=box, router=router)

    outcome = refresh(_due(aspect=ROLE))

    record = load_recheck(Path(outcome.result_ref))
    assert record["state"] == "changed"
    assert len(record["evidence_ids"]) == 1, "空壳页的两行 id 相撞，只该留下一行"
    assert record["pages"][0]["error"] == ""
    assert set(record["evidence_ids"]) == {
        row.evidence_id for row in store.list_evidence(SUBJECT)
    } - {"e0"}


def test_a_change_that_cannot_land_is_an_error_not_a_change(
    store: SqliteEvidenceStore, root: Path, mirrors: Path
) -> None:
    """变了而**落不下去**（另一个工作区的库）⇒ 页级错误，整格因此不可达。

    ``CHANGED`` 的契约后半句是「于是落了新证据」。把它记成一次成功的变更，会让这一格
    续期一个周期，而那份新内容一行都不在盘上 —— 下一轮不会再有人去看它。库自己的
    那句话逐字带出去：它唯一的读者要靠它决定去查什么。
    """
    before = b"Contact Us\nEmily Stone is the grounds manager"
    after = b"Contact Us\nMarcus Reed is the grounds manager"
    store.put_evidence(_row(0, PAGE, digest=_digest(before), fetched_at=T0 - ReuseWindow.expired(ROLE)))
    http, box, router, plan = _wired(store=store, root=root, mirrors=mirrors, bodies={PAGE: after})
    # 一格属于**另一个工作区**的复核：读得出本工作区的行（list 不看工作区），
    # 而写进去会被库拒绝 —— 那正是「一个库只服务一个工作区」这条绑定的形态。
    refresh = build_refresher(
        store=store,
        root=root,
        workspace=OTHER_WORKSPACE,
        at=T0,
        plan=plan,
        acquisition=DocumentAcquisition(http=http),
        router=router,
        documents=box,
    )

    outcome = refresh(_due(aspect=ROLE))

    record = load_recheck(Path(outcome.result_ref))
    assert outcome.recheck.value == "unreachable"
    assert outcome.observed_at is None
    assert record["pages"][0]["cause"] == ["content"], "世界确实变了 —— 这一格要说得出"
    assert record["pages"][0]["error"].startswith("changed_but_nothing_landed")
    assert "工作区" in record["pages"][0]["error"] or "workspace" in record["pages"][0]["error"]
    assert record["evidence_ids"] == []
    assert len(store.list_evidence(SUBJECT)) == 1, "另一个工作区一行都不该落进来"


def test_the_two_row_ids_agree_with_the_ingest_layer() -> None:
    """空壳页那种相撞是**真的**：这里直接对着 ``evidence_ingest`` 算一次。

    上一条判据依赖「两行 id 会相撞」这个前提。前提错了，那条判据会变成一个**永远
    绿**的测试（它测的是「留了一行」，而两行本来就不同 id 时它也留一行）。所以这里
    把前提本身钉住：同一份文档，两行的 id 相同而 ``page_locator`` 不同。
    """
    raw = b"Grounds"
    path = Path(__file__).parent / "_tmp_empty_shell.html"
    path.write_bytes(raw)
    try:
        document = PageParser().parse(path, content_type="text/html")
    finally:
        path.unlink()
    common = {
        "workspace": WORKSPACE,
        "account_id": SUBJECT,
        "source_url": PAGE,
        "document": document,
        "fetched_at": T0,
    }
    body = document_evidence(**common)
    title = title_evidence(**common)

    assert body is not None and title is not None
    assert body.evidence_id == title.evidence_id
    assert body.page_locator != title.page_locator
