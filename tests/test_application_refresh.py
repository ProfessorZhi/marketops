"""``application/refresh.py`` 的判据：一次复核读得出什么、读不出什么。

## 这一层要挡的四件事，各自有一条判据

1. **把「没测」写成 0** —— ``new_venues`` 只能是一条带理由的 ``unmeasured``，
   不能是一个数字。这一格是本仓 §3.4 最贵的一次沉默：``0`` 读作「这一轮找过、
   没有新场地」，而这一层根本没有找过。
2. **一次损坏读成一次好消息** —— ``load_census`` 对「文件不在 / 不是 JSON /
   不是对象 / 摘要与文件名对不上 / 内容与自己声明的摘要对不上」五种情形都抛，
   不返回空盘点。后两条各有执行器（``..._whose_content_was_edited_is_refused``
   与 ``..._whose_name_was_changed_is_refused``）：只核对文件里写的那个字符串与
   文件名一致，等于让文件自己给自己作证。
3. **两份盘点被静默合并** —— ``subject_id`` 不是规范形式时**拒绝**，不 ``slugify``。
   一个会替调用方做规范化的实现把两个 id 落进同一个文件名，而报告上只显示
   「证据更多了」。
4. **声明与派发表分家** —— ``REFRESHABLE_ASPECTS`` 里写着的每一个面都必须真的
   做得出来（调用不抛），而 ``refresh.py`` 里点过名的面必须都在声明里（源码扫描，
   两个方向各一条判据）。

## 「这一层不写观测」在这一层长什么样

``Observation`` 是维护账的一格，而 ``refresh.py`` **一行都不往里面写** —— 它只在
outcome 里**声明**这一趟真的看到了那一面（``observed_at=<这一趟的时刻>``），写那一行
的是维护账的主人（``application/maintenance.run_once``）。判别的方式不是读源码说
「它没调用那个函数」，而是读它**可观测的后果**：同一份证据连读两次，两次
``result_ref`` 相同、盘上仍然只有一份盘点 —— 这一层没有留下第二样东西。真正的记账
与收敛在 ``tests/test_application_maintenance.py`` 里，那一层才有账可对。

（这条纪律 2026-09-25 反转过一次：原来它连 ``observed`` 都不声明，理由是「一次假
观测会把真正的复核推迟半年」。反过来的那个理由写在 ``refresh.py`` 的模块 docstring
上，判据是**这一面的主语是不是那份本地记录**，而不是「数据在本地还是远程」。

B22 之后这里多了一句限定：**声明观测的是「来源供给」这一面**（它的主语就是那份本地
记录）。另外两个页面面的观测由 ``tests/test_application_page_refresh.py`` 判 —— 它们
声明 ``observed_at`` 的判据是「这一趟究竟有没有人把那些页面看过」，而不是「这一面是
本地还是远程」。)
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application import refresh as refresh_use_cases
from marketops.application.refresh import (
    NEW_VENUES_REASON,
    REFRESHABLE_ASPECTS,
    build_refresher,
    census_for,
    census_path,
    load_census,
    refresh_source_supply,
    write_census,
)
from marketops.domain.enums import ContactField, SourceKind
from marketops.domain.errors import ConfigError
from marketops.domain.evidence import Evidence
from marketops.domain.maintenance import ALL_ASPECTS, Aspect, DueReason, DueWork

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
ONE_DAY = timedelta(days=1)
WORKSPACE = "ws-natural-grass"
SUBJECT = "acct-a"
KIND = "account"


def _due(*, aspect: Aspect = Aspect.SOURCE_COVERAGE, subject_id: str = SUBJECT) -> DueWork:
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


def _evidence(index: int, kind: SourceKind, *, account_id: str = SUBJECT) -> Evidence:
    # ``workspace`` 与盘点里的那一个是同一个值：存储按**第一行写入**建立绑定，
    # 而读路径对「行的工作区与库的绑定不一致」那一行是点名拒绝，不是跳过 ——
    # 所以这两处不是可以各自随手写的两个字符串。
    return Evidence(
        workspace=WORKSPACE,
        evidence_id=f"e{index}",
        account_id=account_id,
        source_url=f"https://example.co.uk/page-{index}",
        source_kind=kind,
        literal_value="grounds@example.co.uk",
        content_sha256=f"sha-{index}",
        fetched_at=T0,
        field=ContactField.EMAIL,
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteEvidenceStore:
    return SqliteEvidenceStore(tmp_path / "state" / "marketops.db")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "state" / "maintenance"


# --- 声明与派发表互为子集 -----------------------------------------------------


def test_every_declared_aspect_is_something_this_deployment_can_do(tmp_path: Path) -> None:
    """声明 ⊆ 派发表：写进 ``REFRESHABLE_ASPECTS`` 的面必须跑得起来。

    **一条一格地调**，不是拿集合比一次：一条「七个面里跑得动的有几个」的判据
    在某一个面坏掉时仍然绿（另外六个撑着），而这里要问的正是「哪一面」。
    """
    refresh = build_refresher(store=None, root=tmp_path / "m", workspace=WORKSPACE, at=T0)

    assert REFRESHABLE_ASPECTS, (
        "声明是空的 —— 那这个部署一条维护工作都不该被派发，判据本身也就失去意义"
    )
    for aspect in REFRESHABLE_ASPECTS:
        outcome = refresh(_due(aspect=aspect))
        assert outcome.result_ref, f"{aspect.value} 声明可做，却没产出东西"


def test_an_aspect_outside_the_declaration_is_refused_not_silently_skipped(
    tmp_path: Path,
) -> None:
    """派发表 ⊆ 声明：不在声明里的面必须**抛**，不能答「做完了、什么都没变」。

    后者会让「这一格核过了」与「这一格没实现」在账上长得一样。枚举的是
    ``ALL_ASPECTS`` 而不是「七个减去一个」的差集 —— 将来 domain 加了第八个面，
    这一条要自己跟上。
    """
    refresh = build_refresher(store=None, root=tmp_path / "m", workspace=WORKSPACE, at=T0)
    outside = [aspect for aspect in ALL_ASPECTS if aspect not in REFRESHABLE_ASPECTS]
    assert outside, "声明覆盖了全部七个面 —— 这条判据此刻没有可测的输入"

    for aspect in outside:
        with pytest.raises(ConfigError, match=aspect.value):
            refresh(_due(aspect=aspect))


def test_the_module_names_no_aspect_it_does_not_declare() -> None:
    """源码扫描：``refresh.py`` 里点过名的 ``Aspect`` 成员必须都在声明里。

    上一条测的是「声明之外的点名会被拒」，它挡不住**死分支** —— 一个写了
    ``Aspect.ASSET_ATTRIBUTE`` 却从没被走到的分支，在先前的判据下完全隐形。
    这一条读的是源码本身，所以它看得见那一段不流通的代码。

    按 AST 读，不按文本匹配：注释里提到一个成员名不该让这条判据变红。
    """
    source = Path(refresh_use_cases.__file__).read_text(encoding="utf-8")
    named = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Aspect"
    }

    declared = {aspect.name for aspect in REFRESHABLE_ASPECTS}
    assert named == declared, (
        f"refresh.py 点名了 {sorted(named)}，声明里是 {sorted(declared)}。\n"
        "  少一个 = 声明了一件做不到的事；多一个 = 一段没被声明的实现。"
    )


# --- 数的是几笔待办，不是几个来源 ---------------------------------------------


def test_a_subject_with_only_search_snippets_still_owes_one_source(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """四十条检索摘要与零条证据，在这一层是**同一笔**待办。

    ``SEARCH_SNIPPET`` 永远不可绑定（``BINDING_ELIGIBLE_SOURCES``），所以它
    数不进 ``bindable_kinds``。而 ``new_source_gaps`` 数的是「几笔待办」——
    手上那 N 条摘要不是 N 笔，它们是一件事。
    """
    for index in range(3):
        store.put_evidence(_evidence(index, SourceKind.SEARCH_SNIPPET))

    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )

    assert outcome.source_delta.new_source_gaps == 1
    census = load_census(Path(outcome.result_ref))
    assert census["rows"] == 3, "条数是条数 —— 它不能被折进缺口数里"
    assert census["bindable_kinds"] == []
    assert census["kinds"] == [SourceKind.SEARCH_SNIPPET.value]


def test_one_fetched_page_closes_the_gap(store: SqliteEvidenceStore, root: Path) -> None:
    """一条真的抓下来的页面就够。**0 是一个测出来的结论**，与「没测」不同。"""
    store.put_evidence(_evidence(0, SourceKind.SEARCH_SNIPPET))
    store.put_evidence(_evidence(1, SourceKind.FETCHED_HTML))

    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )

    assert outcome.source_delta.new_source_gaps == 0
    census = load_census(Path(outcome.result_ref))
    assert census["bindable_kinds"] == [SourceKind.FETCHED_HTML.value]
    assert census["newest_fetched_at"] == T0.isoformat()


def test_another_subjects_evidence_is_not_counted(store: SqliteEvidenceStore, root: Path) -> None:
    """盘点按主体读。把别人的页面算进这一格，正是「一个主体看起来有依据」的来源。"""
    store.put_evidence(_evidence(0, SourceKind.FETCHED_HTML, account_id="acct-b"))

    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )

    assert outcome.source_delta.new_source_gaps == 1


# --- 未测不等于 0 -------------------------------------------------------------


def test_new_venues_is_unmeasured_rather_than_zero(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """``new_venues`` 恒为 ``None``，而理由随它一起出去。

    这是本文件里最贵的一条：写成 ``0`` 会让一份「这一层没联网」的报告读起来像
    「这一轮找过新场地、一个都没有」。
    """
    store.put_evidence(_evidence(0, SourceKind.FETCHED_HTML))

    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )
    delta = outcome.source_delta

    assert delta.new_venues is None, "未测的一项不能长成一个数字"
    assert delta.unmeasured == ("new_venues",)
    assert delta.measured is False, "只要还有一项没测，这一格就不算测过"
    assert delta.new_source_gaps == 0, "缺口的 0 是测出来的，与上面那条并存不矛盾"


def test_the_reason_is_written_onto_the_census_not_left_to_the_reader(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """理由**一处定义**，盘上也带一份：读盘的人与读回执的人看到同一句话。"""
    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )

    census = load_census(Path(outcome.result_ref))
    assert census["not_measured"] == {"new_venues": NEW_VENUES_REASON}
    assert census["not_measured"]["new_venues"], "理由不能是一句空话"


# --- 读不到 ≠ 空 ---------------------------------------------------------------


def test_a_store_that_does_not_exist_yet_is_a_read_of_nothing(tmp_path: Path) -> None:
    """库文件还不存在是一种**读到了空**，不是一次读失败。

    而 ``store_present`` 仍要单独报出来：一个刚起、还没跑过任何作业的部署与一个
    「跑过、颗粒无收」的部署在这一层给出的缺口数相同，分开报的那一格是读的人
    唯一的诊断面。
    """
    census = census_for(
        store=None, workspace=WORKSPACE, subject_kind=KIND, subject_id=SUBJECT, at=T0
    )

    assert census.store_present is False
    assert census.rows == 0
    assert census.new_source_gaps == 1
    assert census.newest_fetched_at == "", "没有抓取时刻时是空串，不是 epoch"


def test_a_census_whose_content_was_edited_is_refused(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """改了内容而没改 ``digest`` —— 重算那一步会红。

    这是本文件里唯一一条「判据自己是活的」的证明：把内容与摘要对上的那一步去掉，
    这一条会绿而下面的判据仍然是绿的 —— 所以**两次核对都要有各自的执行器**。
    """
    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )
    path = Path(outcome.result_ref)

    assert load_census(path)["rows"] == 0, "先确认这一份本来是自洽的"

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["rows"] = 99
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="内容与它自己声明的摘要对不上"):
        load_census(path)


def test_a_census_whose_name_was_changed_is_refused(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """内容自洽、名字对不上 —— 另一条通道，另一条判据。

    两份盘的摘要各写在自己的文件名里，所以「这一份是哪一份」是**文件名**答的。
    一次改名（或有人把别处的一份搬过来）在这里被挡住。
    """
    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )
    path = Path(outcome.result_ref)
    renamed = path.with_name(f"account--acct-a--{0 * 'x'}deadbeef.json")
    renamed.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ConfigError, match="摘要与文件名对不上"):
        load_census(renamed)


def test_a_missing_census_file_is_not_an_empty_census(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不在"):
        load_census(tmp_path / "never-written.json")


# --- 同一份盘点只写一次 -------------------------------------------------------


def test_re_reading_the_same_records_produces_the_same_census(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """连读两次：同一个 ``result_ref``，盘上仍然只有一份。

    这是「这一层不写观测」可观测的那一半 —— 一次复核不留第二样东西下来。
    （真正的记账与收敛在 ``test_application_maintenance.py``。）
    """
    store.put_evidence(_evidence(0, SourceKind.FETCHED_HTML))

    first = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )
    second = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0 + ONE_DAY
    )

    assert first.result_ref == second.result_ref
    written = sorted(p.name for p in (root / WORKSPACE).iterdir())
    assert len(written) == 1, f"盘上多了东西：{written}"


def test_the_digest_ignores_when_the_census_was_taken(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """摘要取自**内容**。含 ``taken_at`` 的话，同一份记录读两次永远是两份盘点。"""
    store.put_evidence(_evidence(0, SourceKind.FETCHED_HTML))
    kwargs = {
        "store": store,
        "workspace": WORKSPACE,
        "subject_kind": KIND,
        "subject_id": SUBJECT,
    }

    early = census_for(at=T0, **kwargs)
    late = census_for(at=T0 + ONE_DAY, **kwargs)

    assert early.digest == late.digest
    assert census_path(root, early) == census_path(root, late)
    assert early.as_dict()["taken_at"] != late.as_dict()["taken_at"], (
        "内容相同、时刻不同 —— 盘上那份仍然写着它是什么时候取的"
    )


def test_write_census_leaves_an_existing_file_alone(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """已经存在就不重写：重写会让 mtime 变新，于是「这一份是哪次取的」就没了。"""
    census = census_for(
        store=store, workspace=WORKSPACE, subject_kind=KIND, subject_id=SUBJECT, at=T0
    )

    path = write_census(census, root=root)
    written_at = path.stat().st_mtime_ns
    again = write_census(census, root=root)

    assert again == path
    assert path.stat().st_mtime_ns == written_at


# --- 拒绝而不是规范化 ---------------------------------------------------------


def test_a_non_canonical_subject_id_is_refused_rather_than_slugified(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """``My Account 001`` 与 ``my-account-001`` 不能落进同一个文件名。

    执行器只断言「抛」，同时**说出**那条被拒的映射曾经成立（下面第二段）：
    如果实现改成了规范化，第一段会红；如果有人把校验挪到落盘之后，第一段还是绿，
    但第二段（它本来是同一个文件名）会红。
    """
    with pytest.raises(ValueError, match="subject_id"):
        census_for(
            store=store,
            workspace=WORKSPACE,
            subject_kind=KIND,
            subject_id="My Account 001",
            at=T0,
        )

    good = census_for(
        store=store,
        workspace=WORKSPACE,
        subject_kind=KIND,
        subject_id="my-account-001",
        at=T0,
    )
    assert census_path(root, good).name.startswith(f"{KIND}--my-account-001--"), (
        "规范形式是恒等的；被拒的那个规范化之后会落到这个名字上，"
        "而那就是两份盘点被静默合并的地方"
    )


def test_a_non_canonical_workspace_is_refused_too(store: SqliteEvidenceStore) -> None:
    """工作区是授权链的第一段，它同样进路径 —— 同一套判据，不是两套。"""
    with pytest.raises(ValueError, match="workspace"):
        census_for(
            store=store,
            workspace="Natural Grass",
            subject_kind=KIND,
            subject_id=SUBJECT,
            at=T0,
        )


def test_a_census_for_another_aspect_is_refused(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """派发侧按声明过滤过，所以这一支是**调用方给错了**，走异常那条通道。"""
    with pytest.raises(ConfigError, match=Aspect.ASSET_ATTRIBUTE.value):
        refresh_source_supply(
            due=_due(aspect=Aspect.ASSET_ATTRIBUTE),
            store=store,
            root=root,
            workspace=WORKSPACE,
            at=T0,
        )


def test_a_naive_clock_is_refused() -> None:
    """时刻必须带时区：一次没有时区的复核会让「到期了没有」变成一道算术题。"""
    with pytest.raises(ValueError, match="时区"):
        census_for(
            store=None,
            workspace=WORKSPACE,
            subject_kind=KIND,
            subject_id=SUBJECT,
            at=datetime(2026, 9, 23, 1, 0),
        )


def test_the_census_row_carries_the_subject_it_was_taken_for(
    store: SqliteEvidenceStore, root: Path
) -> None:
    """盘上那一份得自己说得出它是谁的 —— 文件名可以被改，内容里的摘要不会。"""
    outcome = refresh_source_supply(
        due=_due(), store=store, root=root, workspace=WORKSPACE, at=T0
    )

    census = load_census(Path(outcome.result_ref))
    assert census["subject_id"] == SUBJECT
    assert census["subject_kind"] == KIND
    assert census["workspace"] == WORKSPACE
    assert census["new_source_gaps"] == 1


def test_the_census_path_is_inside_the_root_it_was_given(root: Path) -> None:
    """落点由调用方给的 root 决定，不由模块自己拼一个 —— 组合根负责选择位置。"""
    census = census_for(
        store=None, workspace=WORKSPACE, subject_kind=KIND, subject_id=SUBJECT, at=T0
    )

    assert census_path(root, census).parent == root / WORKSPACE


def test_a_subject_that_is_not_an_account_is_workable_too() -> None:
    """主语是**主体**，不是「合格账号」：这一层不认 ``Cohort``，也不该认。

    一条「盘点里必须出现 cohort」的判据看起来像防呆，实际会把发现期的主体
    （还没有合格性可言的候选）挡在复核之外 —— 而它们正是最需要复核的那些。
    所以这条判据反过来钉：换一个主体种类照常跑得通，且它落进自己的文件名。
    """
    census = census_for(
        store=None, workspace=WORKSPACE, subject_kind="venue", subject_id="venue-a", at=T0
    )

    assert census.subject_kind == "venue"
    assert census.subject_id == "venue-a"
    assert census.new_source_gaps == 1
