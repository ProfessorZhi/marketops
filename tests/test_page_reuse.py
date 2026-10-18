"""``application/reuse.py``：同一个许可范围里，同一个 URL 只取一次。

## 这一层要挡的三件事

1. **同一个 URL 被两个主体各取一次** —— 两行证据说着同一页的同一件事，而账单上
   是两次取回。设计 §6 逐字：「相同内容给多个 Scene **只付一次获取费用**，归属
   关联另算。」省下的必须真的是**一次取回**，不是一句注释。判据落在两处：抓取器
   的 ``calls``（发了几次）与额度账本的 ``held``（扣了几份）。
2. **共享跨出许可范围** —— 「仅授权范围内」这四个字的边界在本仓有一个已经写下来
   的答案（``Evidence.workspace``：「复用范围**止于工作区边界**」），而台账必须
   说得出自己在哪个范围里判的。这一条**不能**靠一次比较来执行（请求上没有「范围」
   这一格），所以它由构造方式成立：一次 tick 一张台账，范围就是那次 tick 的工作区。
   这里钉的是那个构造点，以及台账的寿命**不跨轮**。
3. **落不下第二份却照样共享** —— 一行证据要能走回它的材料，靠的是接收方自己目录里
   有那份字节。落不下去的部署一次都不该共享，而它的判定必须**说得出**为什么
   （``no_mirror_writer``），不是悄悄给出一份读不回去的引用。

## 与邻居的分工

* ``tests/test_application_page_refresh.py`` 钉的是**同一个主体、两个面**共享同一趟
  取回（``pages_seen``）。那一条与这里**不是同一件事**：那一份备忘键在主体上，跨不出
  一个主体；这里管的是「两个主体拿到同一个 URL」。
* ``tests/test_page_mirror.py`` 钉的是镜像的**位置约定**与被复制的那份字节本身。
  这一层判的是「什么时候该复制」，判据不重复它那一边的措辞。

全部零网络：抓取走 ``tests/fakes.py::FakeHttp``（一个写盘的假 fetcher），解析走
``PageParser``。判据是编排，不是适配器。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.adapters.page_mirror import page_copier, page_path
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.budget import RunBudget
from marketops.application.document_router import DocumentRouter
from marketops.application.refresh import build_refresher, document_scope, load_recheck
from marketops.application.reuse import SHARED_PREFIX, ReuseLedger, ReuseVerdict
from marketops.config import Settings
from marketops.domain.enums import (
    BudgetLine,
    ContactField,
    FetchOutcome,
    FetchTier,
    SourceKind,
)
from marketops.domain.evidence import Evidence
from marketops.domain.maintenance import Aspect, DueReason, DueWork, RefreshPlan
from marketops.ports.acquisition import FetchMode, FetchRequest, FetchResult

from tests.fakes import FakeHttp, PageParser

UTC = timezone.utc
T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 29, 1, 0, tzinfo=UTC)
WORKSPACE = "ws-natural-grass"
PROFILE = "uk-acme-natural-grass"
DONOR = "acct-a"
RECEIVER = "acct-b"
KIND = "account"
PAGE = "https://example.co.uk/contact"
ROLE = Aspect.CONTACT_PERSON_ROLE
BODY = b"Contact Us\nEmily Stone is the grounds manager"


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------


class Copier:
    """一个记下每一次复制的 ``PageCopier``：``(source, scope, url) -> 落点``。

    落在自己的目录里而**不是** ``settings`` 那棵树：这一层测的是「什么时候调用
    复制」，盘上的位置约定有它自己的判据（``tests/test_page_mirror.py``）。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.landed: list[tuple[Path, str, str]] = []

    def __call__(self, source: Path, scope: str, url: str) -> Path:
        self.landed.append((source, scope, url))
        landed = self.root / f"copy-{len(self.landed)}-{scope}.html"
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_bytes(source.read_bytes())
        return landed


def _request(url: str = PAGE, scope: str = DONOR) -> FetchRequest:
    return FetchRequest(url=url, scope=scope, purpose="recheck")


def _result(
    request: FetchRequest,
    *,
    body_path: Path | None,
    outcome: FetchOutcome = FetchOutcome.OK,
    reason: str = "",
) -> FetchResult:
    return FetchResult(
        request=request,
        outcome=outcome,
        tier=FetchTier.HTTP,
        final_url=request.url,
        status_code=200 if body_path is not None else None,
        content_type="text/html" if body_path is not None else "",
        body_path=body_path,
        reason=reason,
    )


# ----------------------------------------------------------------------
# 台账本身：两格判定 + 那条理由链
# ----------------------------------------------------------------------


def test_the_first_caller_in_a_range_always_fetches() -> None:
    """台账空着时给出的不是「共享」而是一句**说得出理由**的「自己去取」。"""
    ledger = ReuseLedger(permission_range=WORKSPACE)
    decided = ledger.decision(_request(), mode=FetchMode.AUTO)

    assert decided.verdict is ReuseVerdict.FETCH
    assert not decided.shared
    assert decided.reason == "not_seen_in_range"
    assert decided.permission_range == WORKSPACE, "判定要说得出自己在哪个范围里判的"
    assert decided.donor_scope == "", "还没有人取过，没有捐赠方"
    assert ledger.reuse(_request(), mode=FetchMode.AUTO) is None


def test_a_donor_without_bytes_is_not_a_donor(tmp_path: Path) -> None:
    """够不着的那一次照样进台账，但**不能**被别人当材料用。

    一次超时或一次 403 不是可以发给别人的东西：发给别人会把它变成**别人**的观测，
    而那一次请求发生在捐赠方的名下、捐赠方的时刻。拿不到字节的那几页照旧自己去取
    —— 代价是这一轮多花一次，而不是一条假读数。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=Copier(tmp_path))
    ledger.remember(
        _request(),
        mode=FetchMode.AUTO,
        result=_result(_request(), body_path=None, outcome=FetchOutcome.TIMEOUT),
        decision="no_escalation_rule:transfer_error",
    )

    decided = ledger.decision(_request(), mode=FetchMode.AUTO)

    assert decided.verdict is ReuseVerdict.FETCH
    assert decided.reason == "donor_had_no_body"
    assert decided.donor_scope == DONOR, "有人取过这件事必须留下来 —— 与「没人取过」不同"
    assert ledger.reuse(_request(), mode=FetchMode.AUTO) is None


def test_a_deployment_that_cannot_write_a_second_copy_does_not_share(tmp_path: Path) -> None:
    """落不下第二份就不共享，而且**说得出**是这一条。

    缺省 ``copy_page=None`` 不是「省掉这一步」：接收方自己的目录里没有那份字节时，
    它的一行证据读不回自己的材料。所以这一支必须是「不共享 + 一条点名的理由」，
    而不是「共享 + 一个读不回去的引用」。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE)  # 没有 copy_page
    ledger.remember(
        _request(), mode=FetchMode.AUTO, result=_result(_request(), body_path=tmp_path / "d.html")
    )

    decided = ledger.decision(_request(), mode=FetchMode.AUTO)

    assert decided.verdict is ReuseVerdict.FETCH
    assert decided.reason == "no_mirror_writer"
    assert decided.donor_scope == DONOR, "理由里要带上本来会捐赠的是谁 —— 否则无从查起"
    assert ledger.reuse(_request(), mode=FetchMode.AUTO) is None


def test_the_mode_is_part_of_the_key(tmp_path: Path) -> None:
    """``http`` 与 ``browser`` 取回来的不是同一份材料，键里必须分开。

    ``FetchMode`` 是设计里「请求变体」那一格在本仓**唯一**的生产者。按 URL 合并
    两个变体，会让一个显式只要直连的调用方拿到一份渲染结果，而它没有任何字段看得出
    这件事。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=Copier(tmp_path))
    ledger.remember(
        _request(), mode=FetchMode.HTTP, result=_result(_request(), body_path=tmp_path / "d.html")
    )

    assert ledger.decision(_request(), mode=FetchMode.HTTP).shared
    crossed = ledger.decision(_request(), mode=FetchMode.BROWSER)
    assert not crossed.shared and crossed.reason == "not_seen_in_range"


def test_the_url_is_byte_exact(tmp_path: Path) -> None:
    """``?page=1`` 与 ``?page=2`` 是两个 URL —— 不做规范化。

    镜像文件名由**被请求的那个** URL 算出来（``page_mirror.page_prefix``）。做规范化
    会让接收方拿到属于另一个 URL 的字节，而它那条证据的 ``source_url`` 就成了一句
    假话 —— 那是这一层最不该制造的东西。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=Copier(tmp_path))
    first = _request(f"{PAGE}?page=1")
    ledger.remember(
        first, mode=FetchMode.AUTO, result=_result(first, body_path=tmp_path / "d.html")
    )

    assert ledger.decision(_request(f"{PAGE}?page=1"), mode=FetchMode.AUTO).shared
    assert not ledger.decision(_request(f"{PAGE}?page=2"), mode=FetchMode.AUTO).shared
    assert not ledger.decision(_request(f"{PAGE}?page=1#top"), mode=FetchMode.AUTO).shared


def test_the_permission_range_must_be_canonical() -> None:
    """范围进路径，于是它与工作区是同一套判据 —— 不是两套。

    ``Natural Grass`` 与 ``natural-grass`` 被静默当成同一个范围，在这张台账上就是
    一次跨工作区的读。所以给不规范的值时**拒绝**，而不是替调用方规范化。
    """
    with pytest.raises(ValueError, match="permission_range"):
        ReuseLedger(permission_range="Natural Grass")

    with pytest.raises(ValueError, match="permission_range"):
        ReuseLedger(permission_range="")

    assert ReuseLedger(permission_range=WORKSPACE).permission_range == WORKSPACE


def test_a_share_hands_over_the_receivers_own_request_and_a_copy(tmp_path: Path) -> None:
    """共享出去的是**字节**，不是那次会话。

    结果里的 ``request`` 换成接收方的那一个：``FetchRequest.scope`` 会**逐字**成为
    ``Evidence.account_id``，留着捐赠方的那个就是一次串账号。``body_path`` 换成
    接收方自己那一份 —— 于是「一行证据走回它的材料」一个字节都不用改。
    """
    copy = Copier(tmp_path)
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=copy)
    donor_request = _request(scope=DONOR)
    donor_body = tmp_path / "donor.html"
    donor_body.write_bytes(BODY)
    ledger.remember(
        donor_request,
        mode=FetchMode.AUTO,
        result=_result(donor_request, body_path=donor_body, reason="http_ok"),
        decision="no_escalation_rule:thin_body",
    )

    receiver_request = _request(scope=RECEIVER)
    shared = ledger.reuse(receiver_request, mode=FetchMode.AUTO)

    assert shared is not None
    assert shared.request is receiver_request, "归属必须是接收方的 —— 那是「不串账号」"
    assert shared.body_path != donor_body
    assert shared.body_path.read_bytes() == BODY, "共享出去的是同样的字节"
    assert copy.landed == [(donor_body, RECEIVER, PAGE)], "落点是**接收方**的主体 id"
    assert shared.status_code == 200 and shared.tier is FetchTier.HTTP


def test_the_donors_story_is_not_erased_from_the_receivers_record(tmp_path: Path) -> None:
    """接收方那一趟没有做过升级判定，而它的记录里那一格写的就是判定。

    换成一句「共享来的」会让「这一页当初为什么进了浏览器」在接收方的记录里消失，
    而它的 ``tier`` 明明写着浏览器。三段串成一句：谁共享的 → 捐赠方的升级判定 →
    捐赠方那次抓取自己怎么说。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=Copier(tmp_path))
    donor_request = _request(scope=DONOR)
    donor_body = tmp_path / "d.html"
    donor_body.write_bytes(BODY)
    ledger.remember(
        donor_request,
        mode=FetchMode.AUTO,
        result=_result(donor_request, body_path=donor_body, reason="rendered_after_js"),
        decision="escalate:browser",
    )

    shared = ledger.reuse(_request(scope=RECEIVER), mode=FetchMode.AUTO)

    assert shared is not None
    assert shared.reason == f"{SHARED_PREFIX}{DONOR};escalate:browser;rendered_after_js"
    assert shared.reason.startswith(SHARED_PREFIX), "逐页记录按这个前缀认共享来的那一页"


def test_an_unnamed_donor_still_leaves_a_name(tmp_path: Path) -> None:
    """``scope`` 允许为空（那一次抓取不属于任何主体），而空着的那一格要读得出来。

    留成空串会让理由变成 ``shared_within_range:;…`` —— 两个分号之间什么都没有，
    读的人分不出「没有名字」与「拼错了」。
    """
    ledger = ReuseLedger(permission_range=WORKSPACE, copy_page=Copier(tmp_path))
    unnamed = FetchRequest(url=PAGE, purpose="recheck")
    ledger.remember(
        unnamed, mode=FetchMode.AUTO, result=_result(unnamed, body_path=tmp_path / "d.html")
    )

    decided = ledger.decision(_request(scope=RECEIVER), mode=FetchMode.AUTO)

    assert decided.shared
    assert decided.reason == f"{SHARED_PREFIX}unnamed"


# ----------------------------------------------------------------------
# 接进页面复核：一轮 tick 里两个主体拿到同一个 URL
# ----------------------------------------------------------------------


class BudgetBox:
    """一个 ``documents`` 可调用物：按 label 播种一份抓取额度，并记下问过谁。

    与 ``tests/test_application_page_refresh.py`` 里那一个同形，但**各自一份**：两个
    文件测的是不同的问题，共用一份夹具会让「额度按几份扣」这件事只有一处判据。
    """

    def __init__(self, ceiling: int) -> None:
        self.ledger = InMemoryBudgetLedger()
        self.ceiling = ceiling
        self.labels: list[str] = []

    def __call__(self, label: str) -> RunBudget:
        self.labels.append(label)
        scope = f"{PROFILE}:{label}"
        self.ledger.set_ceiling(scope, BudgetLine.DOCUMENT, self.ceiling, source="test:fixture")
        return RunBudget(ledger=self.ledger, scope=scope, line=BudgetLine.DOCUMENT)

    def held(self, label: str) -> int:
        return self.ledger.usage(f"{PROFILE}:{label}")[0].held


def _due(subject_id: str) -> DueWork:
    return DueWork(
        scene_id="scene-a",
        scene_version=1,
        subject_kind=KIND,
        subject_id=subject_id,
        aspect=ROLE,
        reasons=(DueReason.NEVER_OBSERVED,),
        period="never",
        due_at=T0,
    )


def _row(index: int, subject_id: str, url: str, *, digest: str) -> Evidence:
    """一条**手工构造的**证据行：库那一侧「我们已经记得这一页」的那一行。"""
    return Evidence(
        workspace=WORKSPACE,
        evidence_id=f"e{index}",
        account_id=subject_id,
        source_url=url,
        source_kind=SourceKind.FETCHED_HTML,
        literal_value="grounds@example.co.uk",
        content_sha256=digest,
        fetched_at=T0 - timedelta(days=400),
        field=ContactField.EMAIL,
        parser="html",
    )


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> SqliteEvidenceStore:
    return SqliteEvidenceStore(tmp_path / "state" / "marketops.db")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state" / "maintenance"


def _refresher(
    store: SqliteEvidenceStore,
    root: Path,
    *,
    settings: Settings,
    http: FakeHttp,
    box: BudgetBox,
    at: datetime = T0,
    workspace: str = WORKSPACE,
    copy: bool = True,
):
    """照 ``build_refresher`` 的签名把接线拼起来，四样都接上（含 ``copy_page``）。"""
    return build_refresher(
        store=store,
        root=root,
        workspace=workspace,
        at=at,
        plan=RefreshPlan.uncalibrated_default(),
        acquisition=DocumentAcquisition(http=http),
        router=DocumentRouter([PageParser("html")]),
        documents=box,
        copy_page=page_copier(settings) if copy else None,
    )


def test_two_subjects_on_one_url_pay_once_a_tick(
    store: SqliteEvidenceStore, root: Path, settings: Settings
) -> None:
    """两个主体、同一个 URL、同一轮 ⇒ **取回一次**，费用**一份**。

    这是这一片最贵的一条：抓两次在报告上表现为「覆盖面很广」，而它不会被读成一个
    错误，只会被读成一个更好的数字。
    """
    store.put_evidence(_row(0, DONOR, PAGE, digest=_digest(BODY)))
    store.put_evidence(_row(1, RECEIVER, PAGE, digest=_digest(BODY)))
    http = FakeHttp({PAGE: BODY}, root=settings.mirrors_dir)
    box = BudgetBox(ceiling=12)
    refresh = _refresher(store, root, settings=settings, http=http, box=box)

    first = refresh(_due(DONOR))
    second = refresh(_due(RECEIVER))

    assert http.calls == [PAGE], "同一个 URL 在同一轮里被取了不止一次"
    assert first.recheck.value == "unchanged" and second.recheck.value == "unchanged"

    assert box.held(document_scope(DONOR, T0)) == 1, "真的发过请求的那一方付这一份"
    assert box.held(document_scope(RECEIVER, T0)) == 0, "共享来的那一页不该扣第二份"

    donor_page = load_recheck(Path(first.result_ref))["pages"][0]
    assert donor_page["reason"] == "http_ok", "捐赠方那一趟拿到的是「直连成功」"
    assert not donor_page["reason"].startswith(SHARED_PREFIX)

    record = load_recheck(Path(second.result_ref))
    assert "其中 1 页的取回是从本工作区另一个主体共享来的（acct-a）" in record["note"], (
        "整格那一句也要说得出来。逐页记录那一格只有把记录打开才看得到，而回执的读者"
        "先读到的是这一句 —— 它不说的后果是一次共享在报告上长得与「这一格没事」一样，"
        "而「不重复付费」这句话要能被读的人看见才算数"
    )

    pages = record["pages"]
    assert len(pages) == 1
    page = pages[0]
    assert page["reason"].startswith(SHARED_PREFIX), "逐页记录要说得出来它是共享来的"
    assert DONOR in page["reason"], "记录里要读得出来是谁的那一次取回"
    assert page["tier"] == FetchTier.HTTP.value, "档位是捐赠方那一次的真实档位"
    # 接收方那一趟**没有**为这一页发请求，而这一句落在记录里的是 ``reason`` 那个前缀：
    # ``PageRecheck.fetched`` 不进 ``payload()``（那一份是 ``RecheckRecord.digest``
    # 的构成部分，加一格会让每一份已经写在盘上的记录都换了形状），所以「没发请求」
    # 这件事在盘上只有 ``SHARED_PREFIX`` 这一个说法 —— 与 ``state`` 一起读：
    # 「没发请求却拿到了读数」不是 ``REUSED``（那一格说的是复用手上的证据、也没发）。
    assert page["state"] == "unchanged"


def test_the_receiver_gets_its_own_copy_on_disk(
    store: SqliteEvidenceStore, root: Path, settings: Settings
) -> None:
    """省下的是一次取回，**不是一份磁盘**。

    接收方自己那一份文件名按 ‹它自己, 那个 URL› 算，字节与捐赠方那份相同 ——
    于是「不串账号」在盘上就是两棵各归各的目录，而一行证据照旧能走回它自己的字节。
    """
    store.put_evidence(_row(0, DONOR, PAGE, digest=_digest(BODY)))
    store.put_evidence(_row(1, RECEIVER, PAGE, digest=_digest(BODY)))
    http = FakeHttp({PAGE: BODY}, root=settings.mirrors_dir)
    refresh = _refresher(store, root, settings=settings, http=http, box=BudgetBox(ceiling=12))

    refresh(_due(DONOR))
    refresh(_due(RECEIVER))

    own = page_path(settings, scope=RECEIVER, url=PAGE, extension=".html")
    assert own.read_bytes() == BODY, "接收方目录里要有一份它自己的字节"
    flat = http.root / f"{hashlib.sha256(PAGE.encode()).hexdigest()[:16]}.html"
    assert own != flat, "落点不是捐赠方那一份，也不是那个扁平目录"


def test_without_a_mirror_writer_nothing_is_shared(
    store: SqliteEvidenceStore, root: Path, settings: Settings
) -> None:
    """落不下第二份的正对照：**这一条判据真的在起作用**。

    没有它，「两个主体只取一次」有可能是因为别的原因（比如幂等键恰好也生效了），
    而这一条把那件事排除掉：只把 ``copy_page`` 拿掉，取回次数就变回两次。
    """
    store.put_evidence(_row(0, DONOR, PAGE, digest=_digest(BODY)))
    store.put_evidence(_row(1, RECEIVER, PAGE, digest=_digest(BODY)))
    http = FakeHttp({PAGE: BODY}, root=settings.mirrors_dir)
    box = BudgetBox(ceiling=12)
    refresh = _refresher(store, root, settings=settings, http=http, box=box, copy=False)

    refresh(_due(DONOR))
    second = refresh(_due(RECEIVER))

    assert http.calls == [PAGE, PAGE], "落不下第二份时**不共享**，两次都自己去取"
    assert box.held(document_scope(RECEIVER, T0)) == 1
    assert not load_recheck(Path(second.result_ref))["pages"][0]["reason"].startswith(
        SHARED_PREFIX
    )


def test_the_ledger_lives_one_tick_and_not_the_process(
    store: SqliteEvidenceStore, root: Path, settings: Settings
) -> None:
    """台账是**调用方的东西**，寿命由调用方给 —— 一趟 tick 一张。

    挂在 ``DocumentAcquisition`` 上（它在服务面活得比一轮 tick 长得多）等于给整个
    进程开一个页面缓存：几小时后的一次复核会把一次取回当成「刚刚取到的」，而那份
    新鲜度**没有**经过 plan 的时效判定。这一条钉的是那个构造点。

    判据是**每一轮各付一次**（一共两次取回），而不是「一共一次」。后者只有在台账
    活过了那一轮时才会出现：第二轮里捐赠方自己那个键还在，于是它连自己都不用去取
    —— 报告上那一轮显示「没花钱」，而那笔钱是上一轮付的、上一轮的时刻。
    """
    store.put_evidence(_row(0, DONOR, PAGE, digest=_digest(BODY)))
    store.put_evidence(_row(1, RECEIVER, PAGE, digest=_digest(BODY)))
    http = FakeHttp({PAGE: BODY}, root=settings.mirrors_dir)
    box = BudgetBox(ceiling=12)

    first_tick = _refresher(store, root, settings=settings, http=http, box=box)
    first_tick(_due(DONOR))
    first_tick(_due(RECEIVER))
    assert http.calls == [PAGE], "第一轮里那两个主体共享了同一次取回"

    # 下一轮：同一个工作区、同一批主体、同一份接线，**只有时刻变了**。
    next_tick = _refresher(store, root, settings=settings, http=http, box=box, at=T1)
    second = next_tick(_due(DONOR))
    next_tick(_due(RECEIVER))

    assert http.calls == [PAGE, PAGE], "每一轮各付一次 —— 第二轮不该白拿上一轮那一次取回"
    assert not load_recheck(Path(second.result_ref))["pages"][0]["reason"].startswith(
        SHARED_PREFIX
    ), "第二轮里捐赠方自己那一页是**取**回来的，不是从上一轮的自己那里共享来的"
    assert box.held(document_scope(DONOR, T1)) == 1, "第二轮的账记在第二轮那个 scope 上"


def test_a_non_canonical_workspace_is_refused_at_construction(
    store: SqliteEvidenceStore, root: Path, settings: Settings
) -> None:
    """工作区进路径，于是非规范的工作区在**建回调时**就被拒。

    拒在构造处而不是等到某格真的去复核页面：许可范围是这个台账唯一的执行方式
    （请求上没有「范围」这一格），一个不成立的范围不该被接受之后再逐格生效。

    报错里的名字是 ``permission_range`` 而不是 ``workspace``：同一个值的两个名字
    —— 调用方叫它工作区，台账叫它许可范围，而校验发生在台账那一层。
    """
    http = FakeHttp({PAGE: BODY}, root=settings.mirrors_dir)

    with pytest.raises(ValueError, match="permission_range"):
        _refresher(
            store,
            root,
            settings=settings,
            http=http,
            box=BudgetBox(ceiling=12),
            workspace="Natural Grass",
        )
