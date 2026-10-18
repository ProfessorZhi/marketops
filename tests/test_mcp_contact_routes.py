"""§4 的收口：**一次真实的 Research 结算绑出联系人路线**，而绑它的那一趟住在应用层。

## 这一条要证明的一句话

``submit_findings`` 一次结算之后，``contact_route`` 里有行，而那些行是**从这次引用的
证据里绑出来的** —— 不是 CLI 跑出来的，也不是研究员在产出里声称的。

在这一趟接上之前，路线全仓只有一个生产者，而它的入口是 ``cli.py`` 的 ``run-demo``
（§H 的阻塞项 2）。后果不是「少了一条命令」：研究员在服务面上把一轮工作做完、证据
真的落了盘、账号池里真的有了行，**一条路线也产不出来**，而回执上不报任何异常。路线
表的 0 与「这个市场里没有一个人有过联系方式」在报告里同形。

## 为什么必须走 MCP 面

「路线由应用用例产出」这句话有一半只能在服务面上看见：主语是从**这件工作冻住的
快照**里取的（不是研究员声称的）、材料是从**这次引用得解开的证据**里取的（不是它
自己传进来的文本）、而落库的每一行都挂在 ``item.subject_ref`` 上。直接调
``produce_routes`` 会把这三格绕过 —— 而绕过去之后，一个把主语读成 ``row-…`` 的接线
错误仍然会绿。

## 它按顺序做的五件事

1. 跑一轮发现并折一份回执（借 ``test_mcp_discovery_chain`` 的链 —— 账号与场地是它的
   主题，这里只取用，不重测）；
2. 写一页带联系人的材料，走**真的**证据落盘那一趟（``document_evidence``）；
3. 受理、领活；
4. 带着那一条引用从 MCP 面**结算**；
5. 回 ``get_account`` 读那几行，并与证据、账号池逐个对账。

## 每条「有产出」旁边都有一条「没有产出」

``test_a_page_without_contacts_...`` 与 ``test_a_replayed_settlement_...`` 断言的是
「一条都没多」，而那在「这一层整个坏掉」时同样成立。所以正控与它们在同一个文件里，
且形状相同。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.evidence_ingest import document_evidence
from marketops.config import Settings
from marketops.domain.enums import SourceKind
from marketops.domain.route_records import RouteRejectionCode
from marketops.domain.util import sha256_text
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.ports.documents import DocumentLink, ParsedDocument
from marketops.security.tokens import TokenRing, mint
from tests import test_mcp_discovery_chain as chain
from tests.fakes import FakeDispatcher
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence

#: 一页正常页面写好了一个决策职能上的人与一个**属于这个组织域名**的邮箱。
#: 域名取自折进池子的那一块场地（``garden.example.org``，见发现那一轮的替身站点）：
#: 写一个别处的域会让这一条测成「域名不符」，而那是一条**等级**的差别，不是
#: 「绑没绑出来」的差别 —— 本条判据问的是后者。
CONTACT_PAGE = """
<html><head><title>Example Garden Club — Contact</title></head><body>
  <h1>Example Garden Club</h1>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@garden.example.org</p>
</body></html>
"""

#: 一页**没有任何联系方式**的正常页面。绑定会成功、产出是零条 —— 「绑了、什么都没有」
#: 与「没绑成」在报告上是两件事（前者可以追问这一页写了什么，后者要去看判据）。
BARE_PAGE = """
<html><head><title>Example Garden Club — About</title></head><body>
  <h1>Example Garden Club</h1>
  <p>我们在这个村子打球，已经一百年了。</p>
</body></html>
"""

#: 有具名角色、**没有可联系的地址**。它按 SOP L126 不是一条路线 —— 而它也不是
#: 「这一页什么都没写」：丢掉它，报告里那一档就消失了。
ROLE_ONLY_PAGE = """
<html><head><title>Example Garden Club — Committee</title></head><body>
  <h1>Example Garden Club</h1>
  <p>Grounds Manager: Jane Doe</p>
</body></html>
"""

#: 属于**另一个**账号的一条证据。跨账号负例要用它 —— 它真的在盘上、id 也是真的，
#: 任何只按存在性做的核对都会放行它。
FOREIGN_EVIDENCE = sha256_text("a-page-about-someone-else")[:32]

#: 一页**有邮箱、但没说这是谁**的材料。它落成一条**拒收回执**（``unbound_email``）：
#: 那一档的处置是「回那一页找那张卡片」，与「这一页一个人都没有」完全不是一件事。
UNBOUND_EMAIL_PAGE = """
<html><head><title>Example Garden Club — Contact</title></head><body>
  <h1>Example Garden Club</h1>
  <p>Reach us at sales@garden.example.org</p>
</body></html>
"""

_ok = chain._ok


def _document(text: str, *, url: str) -> ParsedDocument:
    """一份解析结果。``text`` 就是那一页的正文，与 ``_ingest`` 交给证据那一格的东西同形。"""
    return ParsedDocument(
        source_path=Path(f"/dev/null/{sha256_text(url)[:8]}.html"),
        sha256=sha256_text(text),
        media_type="text/html",
        parser="html",
        title="Example Garden Club",
        text=text,
        links=(DocumentLink(url=url, text="Contact"),),
        extracts_links=True,
        source_kind=SourceKind.FETCHED_HTML,
    )


def _file_evidence(settings: Settings, account_id: str, *, text: str, url: str) -> str:
    """把一页材料按**真的那一趟**落成一行文档级证据，返回它的 id。

    走 ``document_evidence`` 而不是手搓一个 ``Evidence``：这一条测的是「结算能不能从
    引用的证据里绑出路线」，所以摆进去的那一行必须与真实抓取那一侧落下的东西**同形**
    —— 手搓一份的话，``field`` / ``source_kind`` / ``workspace`` 里任意一格写错都会让
    这一条红在一个与被测行为无关的理由上（或者更坏：绿在一个不存在的形状上）。
    """
    evidence = document_evidence(
        workspace=settings.profile,
        account_id=account_id,
        source_url=url,
        document=_document(text, url=url),
        fetched_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        http_status=200,
        tier="http",
    )
    assert evidence is not None, "这一页没有可引用的正文 —— 夹具自己坏了"
    SqliteEvidenceStore(settings.database_path).put_evidence(evidence)
    return evidence.evidence_id


@pytest.fixture
def session(settings: Settings, tmp_path: Path):
    """一个同时持 controller 与 researcher 的会话驱动器，存储只建一次。

    与组合根 ``interfaces/mcp/__main__.py`` 同款：产品环、控制面、结算三处拿到的是
    **同一批**工厂。各建一份的形态是「折进去的账号」与「证据挂着的账号」来自两个库
    —— 而那一刻绑定出来的主语会是一个池子里并不存在的 id。
    """
    token, digest = mint("claude-both", ["controller", "researcher"])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-both", ["controller", "researcher"]))
        ),
        encoding="utf-8",
    )
    scenes = default_scene_store(settings)
    assets = default_asset_store(settings)
    relations = default_relation_store(settings)
    accounts = default_account_pool_store(settings)
    # 一个执行器：受理之后真的要派发，否则 ``get_assignment`` 拒绝「还没派发到
    # Quay」—— 而那一条拒绝是对的（租约由 ``ExecutionRef`` 反过来守）。整个用例
    # 共用一个：结算之后看板同步读的是同一个执行器里的任务。
    dispatcher = FakeDispatcher()

    def run(*calls: tuple[str, dict[str, Any]]):
        outcome = drive_with_settings(
            settings=settings,
            tokens=TokenRing.from_file(ring_path),
            token=token,
            product_loop=product_loop_handlers(
                settings=settings,
                scenes=scenes,
                maintenance=default_maintenance_store(settings),
                assets=assets,
                relations=relations,
                accounts=accounts,
                cohorts=default_account_cohort_store(settings),
                review=None,
                acquisition=None,
                router=None,
                documents=None,
            ),
            scenes=scenes,
            jobs=lambda: SqliteJobStore(settings.database_path),
            quay=dispatcher,
            assets=assets,
            relation_store=relations,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


#: §1 那条链跑出来的那一轮发现 —— ``(范围名, 回执 id)``。借过来而不是再跑一轮，
#: 理由与 ``test_mcp_responsibility_relations`` 逐字相同：两边各跑一轮会得到两条
#: 独立的事实，而这里要的正是「同一个账号、同一块场地」。
discovered = chain.discovered
CREATE = chain.CREATE


@pytest.fixture
def researched(session, settings: Settings, discovered: tuple[str, str]):
    """走到「已派发、已领活」为止的那条链 —— 材料由各条用例自己摆。

    材料不在这里写：不同的用例要摆不同的页（有邮箱的 / 有角色没邮箱的 / 什么都没有
    的），而摆哪一页恰好是被测的那一格。夹具只负责把它走到能结算的那一步。
    """
    scope, receipt_id = discovered
    outcome = session(
        ("create_scene", CREATE),
        ("control_scene", {"action": "activate", "version": 1}),
        ("promote_receipt", {"receipt_id": receipt_id}),
        ("list_account_pool", {}),
        (
            "submit_research",
            {
                "scene_id": settings.profile,
                "input_kind": "discovery_scope",
                "input_ref": scope,
                "idempotency_key": "contact-routes-1",
            },
        ),
    )
    pool = _ok(outcome.calls[3])
    assert pool["account_count"] == 1, f"发现那一轮没折出账号：{pool}"
    account = pool["accounts"][0]

    submitted = _ok(outcome.calls[4])
    assert submitted["state"] == "queued", (
        f"作业被阻塞了：{submitted['reason']}\n"
        "（这一句在这条链上最常见的成因是：折进去的账号与研究件算出来的主语不是"
        "同一个值，于是 unpromoted_subjects 整份拒绝）"
    )
    items = SqliteJobStore(settings.database_path).work_items(submitted["job_id"])
    assert len(items) == 1
    work_item_id = items[0].work_item_id
    assert items[0].subject_ref == account["account_id"], (
        "研究件的主语不是账号池里那一行 —— 后者是 §1 那条链的主题，这里只取用"
    )

    assignment = _ok(session(("get_assignment", {"work_item_id": work_item_id})).calls[0])
    return {
        "session": session,
        "account_id": account["account_id"],
        "work_item_id": work_item_id,
        "attempt_id": assignment["attempt"]["attempt_id"],
    }


def _settle(researched, **overrides) -> Any:
    """带着 ``researched`` 那几格结算一次。每次都是**一段新的会话**（与 MCP 面上
    结算的到达方式一致：它从来就是一次独立调用）。"""
    arguments: dict[str, Any] = {
        "work_item_id": researched["work_item_id"],
        "attempt_id": researched["attempt_id"],
        "stop_reason": "goal_met",
    }
    arguments.update(overrides)
    return researched["session"](("submit_findings", arguments)).calls[0]


def _routes(settings: Settings, account_id: str):
    """这个账号的路线表现在那几行。与 ``get_account`` 读的是**同一个**库。"""
    return SqliteEvidenceStore(settings.database_path).list_routes(account_id)


def _opportunities(settings: Settings, account_id: str):
    return SqliteEvidenceStore(settings.database_path).list_opportunities(account_id)


def _observation(evidence_id: str) -> dict[str, Any]:
    """一条引用了某份材料的观察。产出里至少要有一条这样的行，否则 ``evidence_refs``
    为空，绑定那一趟根本没有材料可拿。"""
    return {"statement": "这一页写了这个俱乐部的联系人与场地职能", "evidence_refs": [evidence_id]}


# ----------------------------------------------------------------------
# 正控：一次结算 → 一条路线
# ----------------------------------------------------------------------
def test_a_research_settlement_binds_contact_routes(
    researched, settings: Settings
) -> None:
    """**§4 的判据**：那次结算引用的材料真的绑出了路线，而路线落进了库。

    四格一起断言，因为任何一格单独成立都说明不了这件事：

    * 回执里那一块说**算出了**路线（不是 ``null``，也不是 0）；
    * 库里那几行真的在（回执上有一个条数与表里真有那几行是两件事）；
    * 那一行的主语**就是** canonical 账号，证据**就是**这次引用的那一条；
    * 材料那一侧逐份给了去向（``bound``），而不是整批沉默。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=CONTACT_PAGE,
        url="https://garden.example.org/contact",
    )
    submitted = _ok(_settle(researched, observations=[_observation(evidence_id)]))

    block = submitted["routes"]
    assert block["error"] is None, f"绑定没跑成：{block['error']}"
    assert block["routes"] is not None and block["routes"] >= 1, (
        f"一条路线都没绑出来：{block}\n"
        "（``routes`` 为 ``null`` 说的是这一格没算过；为 0 说的是算过了、材料里"
        "一个人都没有 —— 两者的下一步不同）"
    )
    assert sum(block["by_grade"].values()) == block["routes"]
    assert block["by_grade"]["B"] == 1, (
        f"这一页是一个决策职能上的人 + 本组织域下的真实邮箱，应当判 B：{block['by_grade']}\n"
        "（判成 X 多半意味着域名那一格没传下去 —— 而报告里那只是「多了几个 X」）"
    )
    assert [row["status"] for row in block["materials"]] == ["bound"]
    assert block["materials"][0]["routes"] == block["routes"], (
        "这一次只有一份材料，所以它的条数就是总数 —— 对不上说明逐份的账与总数"
        "不是同一趟算出来的"
    )

    store = SqliteEvidenceStore(settings.database_path)
    material = store.get_evidence(evidence_id)
    assert material is not None

    rows = _routes(settings, researched["account_id"])
    assert len(rows) == block["routes"], (
        "回执说的条数与表里的行数对不上 —— 读的人会据此认为路线已经落库"
    )
    for row in rows:
        assert row.account_id == researched["account_id"], (
            "路线挂到了另一个账号上 —— 而这一行在任何一份按账号读的报告里都正常"
        )
        # 路线的依据是**绑定层自己抽出来的字段行**（姓名、角色、邮箱各一行），不是
        # 那份整页材料 —— 判据因此不是「id 相等」，而是「那些行确实读自同一份材料」：
        # 同一个 ``source_url``、同一份 ``content_sha256``。按 id 相等去断言会把
        # 「绑定层抽了什么」当成「它读的是哪一页」，而后者才是可回溯的那一格。
        # 读的是投影里那一格事实：``list_routes`` 给回的是**当前读法**（规范键 +
        # 折叠账 + ``route``），依据挂在 ``route`` 上而不是投影自己身上。
        assert row.route.evidence_ids, "路线一条依据都没有 —— 那一行在下游读起来仍然是正常的"
        for ref in row.route.evidence_ids:
            cited = store.get_evidence(ref)
            assert cited is not None, f"路线引用的 {ref} 在库里解不开"
            assert cited.account_id == researched["account_id"]
            assert cited.source_url == material.source_url
            assert cited.content_sha256 == material.content_sha256
    # 这一页**有**邮箱，所以它产出的是路线而不是机会（SOP L126：没有邮箱就不是
    # 路线 —— 反过来，有邮箱时那条机会就不该出现）。有具名角色、没有邮箱的那一档
    # 在下面单独一条上测。
    assert _opportunities(settings, researched["account_id"]) == []


def test_the_route_reads_back_from_the_account_the_evidence_belongs_to(
    researched, settings: Settings
) -> None:
    """**§14 那条抽查**：``Evidence.account_id == Settlement.account_id == Route.account_id``。

    三个值一起比，而不是「都不为空」：三方各自都正常而它们不是同一个账号，恰好是
    这一轮要消灭的那种形状 —— 证据挂在 A 上、路线挂在 B 上，两份报告各自都读得通。
    顺带把 ``get_account`` 也走一遍：路线要能在**服务面上**读回来，否则「绑出来了」
    只是一句内部状态。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=CONTACT_PAGE,
        url="https://garden.example.org/contact",
    )
    assert _ok(_settle(researched, observations=[_observation(evidence_id)]))["routes"]

    account_id = researched["account_id"]
    stored = SqliteEvidenceStore(settings.database_path).get_evidence(evidence_id)
    assert stored is not None and stored.account_id == account_id

    (route,) = _routes(settings, account_id)
    assert route.account_id == account_id

    view = _ok(researched["session"](("get_account", {"account_id": account_id})).calls[0])
    assert view["route_count"] == 1
    assert view["routes"][0]["account_id"] == account_id


# ----------------------------------------------------------------------
# 负控：三种「算过了，但一条都没有」
# ----------------------------------------------------------------------
def test_a_page_without_contacts_binds_but_produces_nothing(
    researched, settings: Settings
) -> None:
    """一页没有联系人的材料 → 绑定**成功**，产出是 0。

    这一格与「没绑成」分开是有理由的：前者要去看这一页写了什么，后者要去看判据。
    合成一个「没有产出」会把两个方向相反的下一步压成一句话。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=BARE_PAGE,
        url="https://garden.example.org/about",
    )
    block = _ok(_settle(researched, observations=[_observation(evidence_id)]))["routes"]

    assert block["error"] is None
    assert block["routes"] == 0, "一页没有联系人的材料绑出了路线 —— 那是编出来的"
    assert block["opportunities"] == 0
    assert block["materials"][0]["status"] == "bound"
    assert block["materials"][0]["routes"] == 0
    assert _routes(settings, researched["account_id"]) == []


def test_a_named_role_without_an_email_becomes_an_opportunity(
    researched, settings: Settings
) -> None:
    """有具名角色、**没有邮箱** → 一条路线机会，而不是一条路线（SOP L126）。

    这一档在本仓里承重：它是「这个人在、但我们还没有一个能写信给他的地址」——
    丢掉它，报告上那一页看起来与「这一页什么都没写」完全一样。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=ROLE_ONLY_PAGE,
        url="https://garden.example.org/committee",
    )
    block = _ok(_settle(researched, observations=[_observation(evidence_id)]))["routes"]

    assert block["error"] is None
    assert block["routes"] == 0, "没有邮箱却绑出了路线 —— SOP L126 说那不是路线"
    assert block["opportunities"] == 1
    assert block["materials"][0]["routes"] == 0
    assert block["materials"][0]["opportunities"] == 1

    (opportunity,) = _opportunities(settings, researched["account_id"])
    assert opportunity.account_id == researched["account_id"]
    assert _routes(settings, researched["account_id"]) == []


def test_a_run_that_rejected_nothing_says_so_with_a_distribution(
    researched, settings: Settings
) -> None:
    """算过了、一条都没拒 → 那张分布**在**，而它是全 0 的（不是 ``None``）。

    §6.3 要的是「不能只报一个 ``rejected = 37``」；这条判据钉的是它的另一半：
    ``rejections`` 这一格必须分得出**算过没有**。``None`` 说的是这一格没算过，
    一张 ``{"total": 0}`` 的分布说的才是「这一页一条都没拒」—— 两者的下一步不同
    （前者要去看为什么没算，后者不用做任何事）。

    顺带钉住闭集全列：少列一个 0 与「这一类真的没发生过」在报告上长得一样，而
    「这一类没有生产者」正是报告要能一眼看出来的事。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=BARE_PAGE,
        url="https://garden.example.org/about",
    )
    block = _ok(_settle(researched, observations=[_observation(evidence_id)]))["routes"]

    assert block["error"] is None
    counted = block["rejections"]
    assert counted is not None, "算过了却报 None —— 那一格读起来像「没有算过」"
    assert counted["total"] == 0
    assert set(counted["by_code"]) == {code.value for code in RouteRejectionCode}
    assert set(counted["by_code"].values()) == {0}
    assert counted["items"] == []
    assert counted["by_run"] == []
    assert counted["truncated"] is False


def test_a_replayed_settlement_reports_no_tally_at_all(
    researched, settings: Settings
) -> None:
    """重发不重算：``rejections`` 是 ``None``，而**不是**一张全 0 的分布。

    这一格是上一条的反面，而它是这块里最容易写错的一处：把「没算过」写成 0，
    「这一页一条都没拒」就与「绑定根本没跑」同形 —— 前者是关于材料的结论，后者是
    这件事没发生。

    之所以用重发来制造它（而不是别的路径）：这一格正是重发那条纪律的落点 ——
    ``put_opportunity`` 仍然只追加，所以重算会把同一批机会写成两批；而拒收回执本身
    是幂等的（内容寻址），所以「不再算一遍」在这里是**诚实的**：第一次那批收据已经
    在库里，重报一张新分布反而会让读的人以为这一趟又拒了一遍。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=UNBOUND_EMAIL_PAGE,
        url="https://garden.example.org/contact",
    )
    arguments = {"observations": [_observation(evidence_id)]}

    assert _ok(_settle(researched, **arguments))["routes"]["rejections"]["total"] == 1

    again = _ok(_settle(researched, **arguments))["routes"]
    assert again["rejections"] is None, "重发报出了一张分布 —— 它会被读成「这一趟又拒了一遍」"
    assert again["by_grade"] is None, "同一趟里的几格应当一起变成「没算过」"


def test_the_rejections_a_run_left_behind_are_read_back_by_account(
    researched, settings: Settings
) -> None:
    """**§6.3 的正控**：这一趟拒了什么，回执里逐条说得出；而它按账号读得回来。

    两半一起断言，因为只写不读的表与「算出来就丢掉」在报告上同形 —— 而它更糟：
    它看起来像已经处理过了。所以这里既要看 ``submit_findings`` 那一趟的产出
    （``run_id`` 说的是**这一趟**），也要看 ``get_account`` 从库里读回来的历史
    （那是**这个账号**历来被拒的），两者是两句不同的话。

    逐格读的是「哪一个候选、从哪一列来、哪一类、依据哪条规则」：只剩一个数字的话，
    「这一页没有联系人」（正常）与「域名判错了」（要修）在那张报告里长得一样。
    """
    account_id = researched["account_id"]
    evidence_id = _file_evidence(
        settings,
        account_id,
        text=UNBOUND_EMAIL_PAGE,
        url="https://garden.example.org/contact",
    )
    block = _ok(_settle(researched, observations=[_observation(evidence_id)]))["routes"]

    assert block["error"] is None
    assert block["routes"] == 0, "这一页没有具名的人，不该绑出路线"
    counted = block["rejections"]
    assert counted["total"] == 1, f"这一页应当拒一条：{counted}"
    assert counted["by_code"]["unbound_email"] == 1
    assert counted["by_code"]["foreign_domain"] == 0

    (receipt,) = counted["items"]
    assert receipt["account_id"] == account_id
    assert receipt["candidate"] == "sales@garden.example.org", (
        "候选那一格要留**页面上看到的原文** —— 报告靠它去那一页核"
    )
    assert receipt["code"] == "unbound_email"
    assert receipt["field"] == "email"
    assert receipt["run_id"], "收据上没记是哪一趟拒的 —— §6.3 那一格就落空了"
    assert receipt["occurred_at"], "收据上没记时间"

    # 而它按**账号**读得回来 —— 与 ``submit_findings`` 那一趟的账分开。
    view = _ok(researched["session"](("get_account", {"account_id": account_id})).calls[0])
    history = view["rejections"]
    assert history["total"] == 1
    assert history["items"][0]["rejection_id"] == receipt["rejection_id"], (
        "回执里那一条与账号历史里那一条不是同一张收据 —— 两份报告的读法就对不上了"
    )


def test_a_replayed_settlement_does_not_bind_a_second_time(
    researched, settings: Settings
) -> None:
    """同一次结算重发 → **不重算**，表里的行数不变。

    ``settle`` 按内容判重（``replayed``），而 ``put_route`` 是一次 ``INSERT``，不按
    id 幂等。重发是这条路径最常见的到达方式（响应丢了），所以少了这一格，一个人会
    在同一条路线上被写两遍 —— 而报告只会说「路线更多了」。
    """
    evidence_id = _file_evidence(
        settings,
        researched["account_id"],
        text=CONTACT_PAGE,
        url="https://garden.example.org/contact",
    )
    arguments = {"observations": [_observation(evidence_id)]}

    first = _ok(_settle(researched, **arguments))
    assert first["replayed"] is False
    assert first["routes"]["routes"] >= 1
    before = _routes(settings, researched["account_id"])
    assert len(before) == first["routes"]["routes"]

    again = _ok(_settle(researched, **arguments))
    assert again["replayed"] is True, "同一份产出重发没有被判成重发 —— 这一格测不到了"
    assert again["routes"]["routes"] is None, (
        "重发又算了一遍 —— 路线表会多出同样多的行，而报告只会说「路线更多了」"
    )
    assert again["routes"]["evidence_refs"] == 1
    assert _routes(settings, researched["account_id"]) == before


def test_evidence_that_is_not_a_material_is_named_with_its_reason(
    researched, settings: Settings
) -> None:
    """被引用的证据里**不是材料**的那些逐条给出理由，而其余材料照常绑。

    一条字段依据（``field`` 非空）不是一份材料：绑定要的是材料本身那一行。静默跳过
    会让「这一条不算」与「这一条算过了、什么都没绑出来」同形。
    """
    page_id = _file_evidence(
        settings,
        researched["account_id"],
        text=CONTACT_PAGE,
        url="https://garden.example.org/contact",
    )
    field_row = make_evidence(
        evidence_id=sha256_text("a-field-level-row")[:32],
        account_id=researched["account_id"],
    )
    SqliteEvidenceStore(settings.database_path).put_evidence(field_row)

    block = _ok(
        _settle(
            researched,
            observations=[
                _observation(page_id),
                {
                    "statement": "这一页给出了联系人与场地职能",
                    "evidence_refs": [field_row.evidence_id],
                },
            ],
        )
    )["routes"]

    assert [row["evidence_id"] for row in block["not_material"]] == [field_row.evidence_id]
    assert "字段" in block["not_material"][0]["reason"]
    assert block["routes"] >= 1, "另一份材料被那一条不算数的引用连累掉了"


def test_evidence_belonging_to_another_account_refuses_the_whole_settlement(
    researched, settings: Settings
) -> None:
    """**跨账号的证据** → 整份产出被拒，而路线表一行都不多。

    它比「引用不存在」更值得留一条判据：那条证据**真的在盘上**、id 也是真的，任何
    只按存在性做的核对都会放行它 —— 而报告上那一行读起来是有依据的。整份拒绝（而
    不是丢掉那一条引用）是判据的一部分：丢掉它等于**改写**了这份结论的依据里属于谁。
    """
    _file_evidence(
        settings,
        researched["account_id"],
        text=CONTACT_PAGE,
        url="https://garden.example.org/contact",
    )
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id=FOREIGN_EVIDENCE, account_id="acct-elsewhere")
    )

    call = _settle(researched, observations=[_observation(FOREIGN_EVIDENCE)])
    assert call.is_error, "跨账号的证据被放行了 —— 那条路线会带着别人的依据落库"
    assert "acct-elsewhere" in call.text
    assert researched["account_id"] in call.text
    assert _routes(settings, researched["account_id"]) == []
