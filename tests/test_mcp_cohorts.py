"""§B12 的端到端：一个**没有操作者清单**的数据根，只走 MCP 走完一条链。

## 这一条证的是那句关闭条件，逐分句

「干净数据根、没有 inputs YAML，仅通过 MCP 发现/晋升后可列账号、选 cohort、维护
并起草；分页/新增账号/重启不丢记录；暂停 Scene 不接新工作；每批可回读成员/选择
依据/排除原因。」

| 分句 | 在哪一条上被判 |
|---|---|
| 干净数据根、没有 inputs YAML | :func:`test_a_clean_data_root_never_grows_a_yaml_list` |
| 只走 MCP：发现 → 晋升 → 列账号 → 选 cohort → 维护 | :func:`test_the_chain_runs_from_the_pool_without_a_yaml_list` |
| 起草 | :func:`test_drafting_from_a_cohort_selects_its_recipients` |
| 每批可回读成员/选择依据/排除原因 | :func:`test_a_frozen_batch_reads_back_member_by_member` |
| 分页 | :func:`test_a_capped_roster_reports_what_it_dropped` |
| 新增账号 | :func:`test_a_new_account_makes_the_next_freeze_a_second_batch` |
| 重启不丢记录 | :func:`test_a_second_session_reads_what_the_first_one_froze` |
| 暂停 Scene 不接新工作 | :func:`test_a_paused_scene_still_freezes_but_takes_no_work` |
| 两个来源恰好给一个 | :func:`test_the_two_subject_sources_refuse_together_and_apart` |
| 三个新工具没接线时各说各的 | :func:`test_the_cohort_tools_say_not_wired_rather_than_empty` |

## 为什么这件事必须在一个**新文件**里端到端地走一遍

``tests/test_domain_cohort.py`` 证的是冻结这个纯函数，``tests/test_cohort_store
_contract.py`` 证的是两个存储实现。两者全绿而链是断的，是一种**具体的**形态：
``freeze_cohort`` 冻出来一份名单、``get_maintenance_plan`` 却照旧去读
``inputs/<name>.yaml`` —— 两边的报告各自都自洽（「冻好了」与「跑的是那一份清单」），
而冻出来的那一份从来没有被派发过。所以下面每一条链都从**工具**进、从**工具**出，
中间不去碰存储。

## 「没有 inputs YAML」不是「inputs_dir 不存在」

发现要读一份**上游交进来的导出**（``import_motherpool``），它落在 ``inputs_dir``
里，而那不是一句假话：那份工件是系统读进来的原材料，不是一个操作者手写的账号清单。
判据因此分得开：``inputs_dir`` 里**一个 ``.yaml`` 都没有**，而发现照跑。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.deterministic_writer import DeterministicDraftWriter
from marketops.adapters.mcp_server import product_loop
from marketops.adapters.mcp_server.outreach import outreach_handlers
from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.reply_classifier import RuleBasedReplyClassifier
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.application.discovery_run import candidate_id_for
from marketops.config import Settings
from marketops.domain.enums import Grade, GradeReason
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence, make_observation

#: 本部署的 Scene（``conftest.DEFAULT_PROFILE``）。``require_scene`` 只认它。
PROFILE = "uk-acme-natural-grass"

#: 这个部署的发送箱。``prepare_wave`` 要它，而缺省 ``None`` 表示「没有发送箱」——
#: 那是部署的实话，不是这里的假设。
SENDBOX = "outreach@marketops.example"

#: 一次成立的立项，与 ``tests/test_mcp_product_loop.py`` 的 ``BASE_CREATE`` 同形。
#: 它是**测试输入**：真实那套住在 ``profiles/`` 里。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: 一份上游交进来的导出。**两行**而不是一行：一份只有一行的名单上，「成员顺序」
#: 与「上限砍掉了谁」都测不出来，而这两件事正是这一批要证的东西。
#:
#: ``source_identity`` 写的是 ``site:<主机>`` 这个形状，而不是 ``node/12345``：
#: ``promote_receipt`` 只把**发现那一轮写下的**形状折成账号（``site:<主机>``，对应
#: 一次真实的抓取），换个形状它会折成 ``asset_only`` —— 场地是存在的、账号是「有一个
#: 组织」这个结论，而本层拿不出依据支持它。这一批要的是一条**账号**链，所以这里给的
#: 是它要的那个形状；「另一种形状折不出账号」是 ``promote_receipt`` 自己那条判据的事。
ARTIFACT_LINES: tuple[str, ...] = (
    '{"source_identity": "site:example-village.example",'
    ' "name": "Example Village Hall", "location": "Devon",'
    ' "official_domain": "example-village.example"}',
    '{"source_identity": "site:example-sports.example",'
    ' "name": "Example Sports Club", "location": "Devon",'
    ' "official_domain": "example-sports.example"}',
)

CAMPAIGN = "campaign-uk-grass-2026q4"
WAVE = "wave-2026-09-27-0001"
POLICY = "policy-uk-grass-stage-1"
FAMILY = "family-uk-grass-stage-1"
LOCALE = "en-GB"


# --- 会话与断言助手 ------------------------------------------------------------


def _ring(tmp_path: Path, principal: str, roles: list[Role]):
    token, digest = mint(principal, roles)
    path = tmp_path / f"tokens-{principal}.json"
    path.write_text(
        json.dumps(ring_payload(ring_entry(digest, principal, roles))), encoding="utf-8"
    )
    return TokenRing.from_file(path), token


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


class Session:
    """一个 controller 会话，接上产品环与外联两段，外加一个**按需**重开的本事。

    ``again()`` 是「重启不丢记录」那条判据的执行器：它把两个存储工厂与处理器表
    全部**重新构造一遍**（新的闭包、新的 ``sqlite3`` 连接），只有盘上那个库文件
    是同一份。只重开一次会话而复用同一个工厂对象是实现做不到的事 —— 那样测的是
    「同一个进程里再调一次」，而真实重启之后在的只有盘上的字节。
    """

    def __init__(self, settings: Settings, tmp_path: Path) -> None:
        self.settings = settings
        self._tmp = tmp_path
        self._n = 0
        self.ring, self.token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])

    def _scenes(self):
        return default_scene_store(self.settings)

    def run(self, *calls: tuple[str, dict[str, Any]]):
        self._n += 1
        scenes = self._scenes()
        handlers = product_loop_handlers(
            settings=self.settings,
            scenes=scenes,
            maintenance=default_maintenance_store(self.settings),
            assets=default_asset_store(self.settings),
            relations=default_relation_store(self.settings),
            accounts=default_account_pool_store(self.settings),
            cohorts=default_account_cohort_store(self.settings),
            review=None,
            acquisition=None,
            router=None,
            documents=None,
        )
        outreach = outreach_handlers(
            settings=self.settings,
            scenes=scenes,
            store=lambda: SqliteOutreachStore(self.settings.database_path),
            writer=DeterministicDraftWriter(),
            classifier=RuleBasedReplyClassifier(),
            accounts=default_account_pool_store(self.settings),
            cohorts=default_account_cohort_store(self.settings),
        )
        outcome = drive_with_settings(
            settings=self.settings,
            tokens=self.ring,
            token=self.token,
            product_loop=handlers,
            outreach=outreach,
            scenes=scenes,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    def again(self) -> "Session":
        """同一个库、**全新的**一套处理器与存储工厂 —— 一次重启。"""
        fresh = Session(self.settings, self._tmp)
        fresh.ring, fresh.token = self.ring, self.token
        return fresh


@pytest.fixture
def clean(settings: Settings, tmp_path: Path) -> Session:
    """一个**干净数据根**上的部署：库文件不存在，``inputs_dir`` 里一个 yaml 都没有。

    发送箱补上（``replace``）：``prepare_wave`` 与 ``reply_cursor_file`` 都以
    ``outbound_from_address`` 为发送箱的来源，缺了它连一次「要发信的部署」都表达
    不出来 —— 而那是**部署的实话**。
    """
    boxed = replace(settings, outbound_from_address=SENDBOX)
    assert not boxed.database_path.is_file(), "干净数据根：这个库不该预先存在"
    return Session(boxed, tmp_path)


# --- 输入的生产者（夹具，不是被测对象） -----------------------------------------


def _write_artifact(session: Session) -> None:
    """把上游那份导出落进 ``inputs_dir``。**工具收名字，不收路径。**

    它**不是**一份账号清单：``.jsonl`` 里的行是上游对象（编号 + 名称 + 地点），
    折成账号是 ``promote_receipt`` 的事。所以下面那条「一个 yaml 都没有」的判据
    与它是相容的 —— 而相容这件事正是那句话要证的东西。
    """
    session.settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    path = session.settings.inputs_dir / "motherpool.jsonl"
    path.write_text("\n".join(ARTIFACT_LINES) + "\n", encoding="utf-8", newline="\n")


def _hosts() -> tuple[str, ...]:
    """工件里那两行的主机名。从**行自己**读出来，不在这里再抄一遍。"""
    return tuple(
        json.loads(line)["source_identity"].split(":", 1)[1] for line in ARTIFACT_LINES
    )


def _seed_discovery_evidence(session: Session) -> None:
    """发现那一轮取回的那几条证据。**上游产出的既有事实，不是这一层的动作。**

    ## 为什么少了它就什么都折不出来

    ``promote_receipt`` 只在**有证据**时才写账号（``MarketAccount`` 那条「没有证据的
    账号不落盘」），而发现期的证据挂在**候选**身份 ``site-<sha256(主机)[:16]>`` 上
    —— 那是发现那一轮的本仓句柄，不是主机名。折的时候它被重新锚到账号上。

    所以这里必须先用 ``candidate_id_for`` 把证据挂回那个句柄上：那是**生产的**那个
    函数，不是测试自己拼一个串。自己拼一个「看起来像」的 id 会拼出一个永远不会被读到的
    主体，而报告上完全看不出来 —— 只会看到池子是空的。

    ``field=None`` 与 ``tests/test_mcp_product_loop.py`` 那一边同一条分界：这一条证据
    说的不是「这个组织的联系方式是这个」，它说的是「这一页被取回来看过」。给它编一个
    联系字段会凭空造出一条 ``account.contacts.*`` 的依据，而 §6 明令发现期的证据不得
    满足那一族事实。
    """
    store = SqliteEvidenceStore(session.settings.database_path)
    for host in _hosts():
        store.put_evidence(
            make_evidence(
                evidence_id=f"evidence-{host}",
                account_id=candidate_id_for(host),
                source_url=f"https://{host}/",
                field=None,
            )
        )


def _seed_routes(session: Session, account_ids: tuple[str, ...]) -> None:
    """给这几个账号各落一条证据与一条 A 级路线。**上游产出的既有事实，不是动作。**

    与 ``tests/test_mcp_outreach.py::_seed`` 同一条分界：证据与路线是这一层只**消费**
    的输入（它们的生产者在研究侧），而「允许说什么」才是这一层自己的决定。
    """
    store = SqliteEvidenceStore(session.settings.database_path)
    for account_id in account_ids:
        evidence_id = f"evidence-{account_id}"
        store.put_evidence(
            make_evidence(
                evidence_id=evidence_id,
                account_id=account_id,
                source_url=f"https://example.co.uk/{account_id}",
                field=None,
            )
        )
        store.record_route(
            make_observation(
                account_id,
                organisation=f"Example {account_id}",
                email=f"{account_id}@example.co.uk",
                evidence_ids=[evidence_id],
                grade=Grade.A,
                grade_reason=GradeReason.RULE_MATCHED,
            )
        )


def _no_yaml(session: Session) -> tuple[str, ...]:
    """``inputs_dir`` 里现在的 ``.yaml`` 文件名。**判据取的就是这个数。**"""
    if not session.settings.inputs_dir.exists():
        return ()
    return tuple(sorted(path.name for path in session.settings.inputs_dir.glob("*.yaml")))


def _discovered(session: Session) -> tuple[str, ...]:
    """走一遍发现与晋升，返回池子里的 ``account_id``，**按池子的顺序**。

    这是「干净数据根上怎么会有账号」这条链的全部：一份上游导出落在盘上（外加发现
    那一轮已经取回的那几条证据）⇒ 读成回执 ⇒ 把回执里 resolved 的行折进池子 ⇒
    读池子。前三步都从工具进。

    ``disposition`` 那一栏**逐行**看一遍：``asset_only`` 是「场地折进去了、账号没有」
    —— 一条把主体读成空序列的链，与一条每一行都恰好是 ``asset_only`` 的链，在
    ``list_account_pool`` 上都只会看到一个空池子。所以这里把「折进去了」这句话
    在哪一行上不成立说出来。
    """
    _write_artifact(session)
    _seed_discovery_evidence(session)
    imported = _ok(session.run(("import_motherpool", {"artifact_ref": "motherpool"})))
    receipt_id = imported["receipt"]["receipt_id"]
    assert imported["receipt"]["resolved_row_count"] == len(ARTIFACT_LINES)
    promoted = _ok(session.run(("promote_receipt", {"receipt_id": receipt_id})))
    assert promoted["promotion"]["counts"]["promoted"] == len(ARTIFACT_LINES), (
        f"每一行都该折出一个账号。逐行去向：{promoted['rows_detail']!r}"
    )
    pool = _ok(session.run(("list_account_pool", {})))
    return tuple(row["account_id"] for row in pool["accounts"])


# --- 一、干净数据根 ------------------------------------------------------------


def test_a_clean_data_root_never_grows_a_yaml_list(clean: Session) -> None:
    """发现与晋升之后池子里有账号，而 ``inputs_dir`` 里**一个 yaml 都没有**。

    ## 这一条挡的正是那句「不要在后台生成影子 YAML」

    一个把「没有清单」修成「悄悄写一份清单」的实现，会让下游三条链照旧从
    ``inputs/<name>.yaml`` 读 —— 而这一整轮的主语从「系统从自己的池子里选」
    退回「操作者的路径上有那么一个文件名」，且每一份报告都正常。所以下面每走完
    一步都数一次 yaml：这种实现会在第一步之后就让这个数变成 1。

    ## 反面是同一份数

    只有「最后没有 yaml」是不够的：一份写了一半又删掉的影子清单在最后也数不到。
    所以这里在**每一步之后**都数。
    """
    assert _no_yaml(clean) == (), "干净数据根的一个前提：一开始一个 yaml 都没有"

    account_ids = _discovered(clean)
    assert len(account_ids) == len(ARTIFACT_LINES)
    assert _no_yaml(clean) == (), (
        "发现与晋升这一步之后出现了 yaml —— 而正常使用要的正是「不依赖它」。"
        "一份后台写出来的清单会让下游读的还是操作者路径上的那个文件名。"
    )

    # 旧路径仍在，而且它**照旧**读不到东西：兼容说的是「这条路还在」，不是
    # 「它现在是正常的入口」。两者混起来会让人以为必须先生成一份清单才能开工。
    refused = _refused(clean.run(("list_accounts", {"name": "known-accounts"})))
    assert "不存在" in refused, refused

    scene = _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    assert scene["scene"]["state"] == "active"

    frozen = _ok(clean.run(("freeze_cohort", {})))
    assert frozen["member_count"] == len(account_ids)
    assert _no_yaml(clean) == (), "冻结是一份读数，它没有理由在盘上多出一个 yaml"


# --- 二、只走 MCP 的那条链 -----------------------------------------------------


def test_the_chain_runs_from_the_pool_without_a_yaml_list(clean: Session) -> None:
    """列账号 → 选 cohort → 维护：三步的主体都来自**池子**，不来自任何清单文件。

    ## 维护那一步真的做掉了什么，而不是「没报错」

    ``run_maintenance`` 的 ``claimed_count`` 与 ``observations`` 是两个不同的数：
    前者是「这一格做过了」，后者是「这一面**真的**被看到了」。只断言第一个的话，
    一个把主体读成空序列的实现照样绿 —— 空序列 ⇒ 0 格 ⇒ 一条观测都没有 ⇒ 报告
    读起来像「这个市场没有格子要复核」，而它的下一步与「池子读不出来」相反。

    所以这里两头都钉：**两个成员各一条观测**，而那条观测的 ``source_ref`` 指回
    ``cohort:<id>`` —— 主体是谁、这一格是谁的，在报告里接得上。

    §B22 之后 ``claimed_count`` 与观测数的**比例**变成 3:1：名下有页面的那两个面
    这一轮做不成（池子里的账号没有任何证据行，所以「该看哪几页」是空集），于是
    六格 FAILED、两格 done + 两条观测。这一条不再断言两个数相等 —— 它们本来
    就不是同一件事，而**这一片正是把那个差做出来的那一片**。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))

    frozen = _ok(clean.run(("freeze_cohort", {})))
    cohort_id = frozen["cohort_id"]
    assert frozen["members"], "池子里两个账号，名单不该是空的"
    assert frozen["scene_id"] == PROFILE

    # 计划：主体来自那份名单，而且报出**看的是哪一份**。
    plan = _ok(clean.run(("get_maintenance_plan", {"cohort": cohort_id})))
    assert plan["subjects"]["kind"] == "cohort"
    assert plan["subjects"]["cohort_id"] == cohort_id
    assert plan["subjects"]["count"] == len(account_ids)

    tick = _ok(clean.run(("run_maintenance", {"cohort": cohort_id})))
    assert tick["claimed_count"] == 3 * len(account_ids), (
        "这一轮该把名单上每一格都做一遍 —— 而 §B22 之后**每一格**是三个面"
        f"（来源覆盖 + 角色 + 邮箱证据）。回执：{tick['done']!r} / {tick['failed']!r}"
    )
    observed = {row["subject_id"] for row in tick["observations"]}
    assert observed == set(account_ids), (
        f"这一轮真的记下观测的主体该是名单上那两个。观测：{tick['observations']!r}"
    )
    assert tick["subjects"]["kind"] == "cohort", (
        "这是**写**工具，而「这一轮跑的是谁」在它上面尤其要答得出来：``cohort`` 的空串"
        "是「用最近那一份」，调用方手里并没有那个 id"
    )
    assert tick["subjects"]["cohort_id"] == cohort_id
    assert tick["subjects"]["count"] == len(account_ids)

    # 显式导入仍然兼容：同一批账号写成一份清单，走的还是那条老路。
    _write_list(clean, account_ids)
    by_name = _ok(clean.run(("get_maintenance_plan", {"name": "known-accounts"})))
    assert by_name["subjects"]["kind"] == "name"
    assert by_name["subjects"]["count"] == len(account_ids), (
        "两条路的**形状**必须逐字一样（同一种序列、同一处派发）—— 数量不同就说明"
        "其中一条的读法与另一条分家了"
    )


def _write_list(session: Session, account_ids: tuple[str, ...]) -> None:
    """那份显式导入的清单。**只在兼容那一条上用得到**，不在任何正常路径上。"""
    session.settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    body = "accounts:\n" + "".join(
        f"  - account_id: {account_id}\n"
        f"    organisation: Example {index} Ltd\n"
        "    cohort: known_success\n"
        for index, account_id in enumerate(account_ids)
    )
    (session.settings.inputs_dir / "known-accounts.yaml").write_text(
        body, encoding="utf-8", newline="\n"
    )


# --- 三、每批可回读 ------------------------------------------------------------


def test_a_frozen_batch_reads_back_member_by_member(clean: Session) -> None:
    """一份名单的三件东西：这一批是谁、按什么规则选的、谁没进来以及为什么。

    ## 回读不是「再读一遍刚才那份回执」

    ``freeze_cohort`` 与 ``get_cohort`` 都带上成员，而两者**带的方式不同**：
    ``get_cohort`` 连池子一起读一遍（成员还在不在、改动过没有），``freeze_cohort``
    不做那一趟（刚冻好的那一份就是刚读到的读数）。所以这里走的是 ``get_cohort``
    —— 「一批工作派出去之后」要回答的正是它。

    ## 三态要分得开

    ``readback.members_missing_from_pool`` 是 ``[]``（比过了、一个都没少），
    而 ``readback_gap`` 是 ``""``（这一趟真的核了）。只有前者的话，一个**根本没核**
    的实现照样给出一份空清单 —— 而「一个都没少」与「没问过」在报告上都读作好话。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    frozen = _ok(clean.run(("freeze_cohort", {})))
    cohort_id = frozen["cohort_id"]

    detail = _ok(clean.run(("get_cohort", {"cohort_id": cohort_id})))

    assert [row["account_id"] for row in detail["members"]] == sorted(account_ids), (
        "成员按 account_id 升序 —— 它会变成 subject_id 的次序，而 work_item_id 由"
        "那个次序决定，所以这一格不是排版"
    )
    assert detail["selection"]["default_statuses"] == _WORKABLE, (
        "没给 statuses 时用的是那三个可研究的状态，而缺省要在报告里看得见"
    )
    assert detail["selection"]["statuses"] == detail["selection"]["default_statuses"]
    assert detail["readback"]["members_missing_from_pool"] == []
    assert detail["readback"]["members_changed_since_freeze"] == []
    assert detail["readback_gap"] == "", "这一趟真的核过，所以没有要说出口的缺口"

    for row in detail["members"]:
        assert row["changed"] == [], (
            "[] 是「比过了、没变」，不是「没核」—— 与 current 为 null 分得开"
        )
        assert row["current"]["in_pool"] is True

    assert set(detail["excluded_by_code"]) == _EXCLUSION_CODES, (
        "七档每次都列全（零也列）：缺一个键有两种读法 ——「这一档是 0」与「这一档没算」"
    )
    assert sum(detail["excluded_by_code"].values()) == detail["exclusion_count"]


#: ``domain/cohort.py`` 的六个原因码。这里手抄一份而**不**从枚举取：判据要断言的
#: 正是「枚举里有没有多一个、少一个」，从枚举取的话它永远与自己对得上。
_EXCLUSION_CODES = {
    "on_hold",
    "not_relevant",
    "duplicate",
    "archived",
    "status_not_selected",
    "over_limit",
}

#: ``WORKABLE_STATUSES``（``domain/cohort.py``）。同上，手抄。
_WORKABLE = ["discovered", "eligible", "researched"]


def test_every_excluded_account_carries_its_own_reason_code(clean: Session) -> None:
    """排除是**逐条**的，每一行带自己的原因码 —— 不是「不在成员里」这一个事实。

    ## 为什么四个状态都要各来一行

    ``excluded = 4`` 里可能有 1 条是「有人在它身上等裁决」（正常）、1 条是「这个
    Scene 判过它不相关」、1 条是「它被判成与另一行重复」（要人看一眼）、1 条是
    「这一轮的上限砍到了它」（下一轮就轮到）。同一个数字，四种处置 —— 而只给一个
    数字的实现照样能过一条只断言条数的判据。

    所以这里把四种各自钉一次，用的是**它们自己的**原因码：一条把四行都记成
    ``status_not_selected`` 的实现会在第一条上红。
    """
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    rows = [
        _claim("pool-a", "hold"),
        _claim("pool-b", "irrelevant"),
        _claim("pool-c", "duplicate"),
        _claim("pool-d", "archived"),
        _claim("pool-e", "eligible"),
        _claim("pool-f", "eligible"),
    ]
    _seed_evidence(clean, tuple(row["account_id"] for row in rows))
    written = _ok(clean.run(("remember_accounts", {"accounts_rows": rows})))
    assert written["by_disposition"]["written"] == len(rows), written["outcomes"]

    # ``limit=3``：三个可研究的账号里只能进三个 —— 而这里只有两个可研究的，所以
    # 上限**没有**砍到谁。上限那一条在下面单独用一次更小的冻结来钉。
    frozen = _ok(clean.run(("freeze_cohort", {"limit": 1})))
    assert frozen["member_count"] == 1
    assert [row["account_id"] for row in frozen["members"]] == ["pool-e"]

    by_id = {row["account_id"]: row for row in frozen["exclusions"]}
    assert by_id["pool-a"]["code"] == "on_hold"
    assert by_id["pool-b"]["code"] == "not_relevant"
    assert by_id["pool-c"]["code"] == "duplicate"
    assert by_id["pool-d"]["code"] == "archived"
    assert by_id["pool-f"]["code"] == "over_limit", (
        "被上限砍掉的那一行带的是**自己的**码：它下一轮就在名单里，而把它读成"
        "「不相关」会让一次配置截断变成一句业务结论"
    )
    for row in frozen["exclusions"]:
        assert row["reason"].strip(), "原因码要有一句人读得懂的渲染，两格一起给"

    assert frozen["excluded_by_code"]["over_limit"] == 1
    assert frozen["excluded_by_code"]["on_hold"] == 1

    # 上限是**这条规则的一部分**：改一个数就是另一份名单，而旧的那份还在盘上。
    wider = _ok(clean.run(("freeze_cohort", {})))
    assert wider["cohort_id"] != frozen["cohort_id"]
    assert wider["member_count"] == 2
    assert {row["cohort_id"] for row in _ok(clean.run(("list_cohorts", {})))["cohorts"]} == {
        frozen["cohort_id"],
        wider["cohort_id"],
    }, "旧的那一份读得回来 ——「这一批当时研究的是谁」永远答得出来"


def _claim(account_id: str, status: str) -> dict[str, Any]:
    """一条 ``remember_accounts`` 的行。``status`` 是**原文**，不是枚举成员。"""
    return {
        "account_id": account_id,
        "organisation": f"Example {account_id} Ltd",
        "status": status,
        "domains": [f"{account_id}.example.co.uk"],
        "evidence_ids": [f"evidence-{account_id}"],
    }


def _seed_evidence(session: Session, account_ids: tuple[str, ...]) -> None:
    """每一行账号都要挂一条**已经落盘**的依据（``MarketAccount`` 的判据）。"""
    store = SqliteEvidenceStore(session.settings.database_path)
    for account_id in account_ids:
        store.put_evidence(
            make_evidence(
                evidence_id=f"evidence-{account_id}",
                account_id=account_id,
                source_url=f"https://example.co.uk/{account_id}",
                field=None,
            )
        )


# --- 四、分页 / 新增账号 / 重启 -------------------------------------------------


def test_a_capped_roster_reports_what_it_dropped(clean: Session) -> None:
    """回执的封顶与名单的封顶是**两件事**，而前者要说出自己砍了几行。

    ``limit`` 是选择规则的一部分（进 ``cohort_id`` 的原像、被砍的带 ``over_limit``），
    ``max_rows`` 只管这条回执列几行（不进原像、不改任何一份名单的内容）。合成一个
    参数会让「我要一份 50 个成员的名单」与「我要看见这 300 行」变成同一句话。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    frozen = _ok(clean.run(("freeze_cohort", {})))
    assert frozen["member_count"] == len(account_ids)

    capped = _ok(clean.run(("get_cohort", {"cohort_id": frozen["cohort_id"], "limit": 1})))
    assert len(capped["members"]) == 1
    assert capped["member_count"] == len(account_ids), (
        "member_count 是这一份名单的**规模**，不是这一条回执列了几行"
    )
    assert capped["truncation"]["members"]["dropped_items"] == [account_ids[-1]], (
        "被砍掉的行要逐条点名 —— 只报一个数的话，「砍了 1 行」与「本来就只有 1 行」"
        "在报告上长得一样"
    )
    assert capped["truncation"]["members"]["truncated"] is True

    whole = _ok(clean.run(("get_cohort", {"cohort_id": frozen["cohort_id"], "limit": None})))
    assert whole["truncation"]["members"]["truncated"] is False

    listed = _ok(clean.run(("list_cohorts", {"limit": 1})))
    assert listed["cohort_count"] >= 1
    assert listed["truncation"]["truncated"] in (True, False)


def test_a_new_account_makes_the_next_freeze_a_second_batch(clean: Session) -> None:
    """新账号进来 ⇒ 再冻一次是**另一份**名单，而先前那一份逐字没动。

    这一条挡的是一种很省事的实现：把名单当成「这个 Scene 的当前池子快照」，于是
    第二次冻结**覆盖**第一份。覆盖之后「上一批跑的是谁」就没有答案了 —— 而它是
    §B22 复用判定要问的第一个问题。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    first = _ok(clean.run(("freeze_cohort", {})))

    _seed_evidence(clean, ("pool-new",))
    _ok(clean.run(("remember_accounts", {"accounts_rows": [_claim("pool-new", "eligible")]})))

    second = _ok(clean.run(("freeze_cohort", {})))
    assert second["cohort_id"] != first["cohort_id"]
    assert second["member_count"] == len(account_ids) + 1

    old = _ok(clean.run(("get_cohort", {"cohort_id": first["cohort_id"]})))
    assert old["member_count"] == len(account_ids), "先前那一份的成员数没变"
    assert [row["account_id"] for row in old["members"]] == sorted(account_ids)
    assert old["readback"]["members_changed_since_freeze"] == []

    headers = _ok(clean.run(("list_cohorts", {})))
    assert [row["cohort_id"] for row in headers["cohorts"]] == [
        first["cohort_id"],
        second["cohort_id"],
    ], "早的在前 —— 所以最后一条就是这个 Scene 当前的名单"


def test_a_second_session_reads_what_the_first_one_froze(clean: Session) -> None:
    """重启：换一套处理器与存储工厂，盘上那份名单照旧读得回来。

    ## 为什么必须换一套工厂

    复用同一个闭包测的是「同一个进程里再调一次」—— 那与重启不是同一件事。真实重启
    之后在的**只有盘上的字节**：排序键、成员顺序、排除那一栏全都要从库里重新读出来。
    一个把顺序或去重记在进程内存里的实现，会在这一条上第一次红。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    frozen = _ok(clean.run(("freeze_cohort", {})))

    restarted = clean.again()
    listed = _ok(restarted.run(("list_cohorts", {})))
    assert [row["cohort_id"] for row in listed["cohorts"]] == [frozen["cohort_id"]]

    detail = _ok(restarted.run(("get_cohort", {})))
    assert detail["cohort_id"] == frozen["cohort_id"], "空 cohort_id = 这个 Scene 最近那一份"
    assert [row["account_id"] for row in detail["members"]] == sorted(account_ids)
    assert detail["selection"] == frozen["selection"]
    assert detail["created_at"] == frozen["created_at"], (
        "冻于何时的那个时刻也要从盘上原样读回来 —— 它只往早走，不随重启刷新"
    )


# --- 五、暂停的 Scene ----------------------------------------------------------


def test_a_paused_scene_still_freezes_but_takes_no_work(clean: Session) -> None:
    """暂停停的是**新的派发**，不是「我手上现在有哪些账号」这个问题。

    冻结是一份读数（不派发、不花钱、不联网），所以暂停期间照样要答得出来 —— 而
    冻结出来的那份名单之后要能被读回。反过来说：维护那一轮**一格都不该做**，
    且要说出为什么。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    _ok(clean.run(("control_scene", {"action": "pause", "reason": "operator_pause"})))

    frozen = _ok(clean.run(("freeze_cohort", {})))
    assert frozen["state"] == "paused"
    assert frozen["accepts_new_work"] is False, (
        "回执要报出这个 Scene 现在收不收新工作 —— 冻结能跑不等于派发能跑"
    )
    assert frozen["members"], "暂停期间照样答得出「我手上有哪些账号」"

    tick = _ok(clean.run(("run_maintenance", {"cohort": frozen["cohort_id"]})))
    assert tick["ticking"] is False
    assert tick["claimed_count"] == 0, "暂停的 Scene 不接新工作"
    assert tick["observations"] == []
    assert tick["not_ticking"], (
        "「一格都没做」必须说出口：一条空的 done 与一次读不出主体在报告上同形"
    )
    assert tick["not_ticking"][0]["scene_id"] == PROFILE

    # 恢复之后同一份名单照跑，而且**立刻**就做 —— 主体那一侧没有丢东西。
    _ok(clean.run(("control_scene", {"action": "resume"})))
    resumed = _ok(clean.run(("run_maintenance", {"cohort": frozen["cohort_id"]})))
    assert resumed["ticking"] is True
    assert resumed["claimed_count"] == 3 * len(frozen["members"])


# --- 六、两个来源恰好给一个 ----------------------------------------------------


def test_the_two_subject_sources_refuse_together_and_apart(clean: Session) -> None:
    """``name`` 与 ``cohort`` 是**两条路**，给两个或一个都不给都是**具名拒绝**。

    ## 「两个都没给」不能兜底成「用最近那一份」

    那会让一次漏传参数变成一次静默的成功，而它跑的是这个 Scene 最近冻的那份名单
    —— 报告上完全看不出来跑的是哪一份。所以五条链（计划、tick、报表、起草、准备）
    上都要拒，而且拒的时候要说出**这个工具自己那个参数**叫什么：调用方要据它改东西。

    ## 两族的第一个参数名字不同

    维护那三条叫 ``name``，外联那两条叫 ``pool`` —— 同一个判定、两个参数名。所以
    下面按族走：给 ``draft_outreach`` 传 ``name=`` 是一次**参数校验**失败（它的 schema
    里没有这个名字），而那条拒绝说的是另一件事。两族分开，是因为合起来写会让
    「拒绝里那两个字对不对」这件事测不出来：一个写死 ``name`` 的实现两族都能过。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    _published(clean)
    cohort_id = _ok(clean.run(("freeze_cohort", {})))["cohort_id"]

    for label, tool, payload in (
        ("name", "get_maintenance_plan", {}),
        ("name", "run_maintenance", {}),
        ("name", "get_ops_report", {}),
        ("pool", "draft_outreach", _draft(wave="wave-a")),
        ("pool", "prepare_wave", _prepare(wave="wave-a")),
    ):
        both = _refused(
            clean.run((tool, {**payload, label: "known-accounts", "cohort": cohort_id}))
        )
        assert "两个都给了" in both, f"{tool}：{both}"
        assert label in both and "cohort" in both, (
            f"{tool} 的拒绝要把两条路各是什么说出来 —— 它是一条要据它改东西的答复：{both}"
        )

        neither = _refused(clean.run((tool, dict(payload))))
        assert f"既没有给 {label}" in neither, f"{tool}：{neither}"
        assert "不兜底" in neither and "没有答案" in neither, f"{tool}：{neither}"

    # 三个维护工具之外，跨 Scene 的那一份也要拒 —— 一份名单是逐 Scene 的。
    other = _refused(clean.run(("get_cohort", {"cohort_id": cohort_id, "scene_id": "elsewhere"})))
    assert "Scene" in other, other


def _draft(*, wave: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "wave_id": wave,
        "campaign_id": CAMPAIGN,
        "campaign_name": "UK 天然草 首次接触",
        "stage": "stage-1",
        "locale": LOCALE,
    }
    payload.update(overrides)
    return payload


def _prepare(*, wave: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "wave_id": wave,
        "campaign_id": CAMPAIGN,
        "stage": "stage-1",
        "window_start": "2026-09-01T00:00:00+00:00",
        "window_end": "2026-12-01T00:00:00+00:00",
        "valid_until": "2026-12-31T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# --- 七、起草 ------------------------------------------------------------------


def test_drafting_from_a_cohort_selects_its_recipients(clean: Session) -> None:
    """起草的收件人**就是**那份名单上的成员，而回执说得出看的是哪一份。

    ## 只断言「没报错」是不够的

    ``draft_outreach`` 在主体读不出来时拒，所以「成功了」这句话确实有信息量。但
    更强的读数在这一格上：``routes_considered`` 与 ``plan.planned`` 都是**成员数**
    —— 一份把主体读成别的什么东西的实现会在这两个数上红，而它照样「没报错」。

    ## 回执里那一格是事后回查的唯一入口

    调用方给空串表示「用最近那一份」时手里并没有那个 id，所以 ``subjects.cohort_id``
    必须报出来。「这一批是按哪一份名单选的」在事后要答得出来，而 ``routes_considered``
    换了另一份名单照样可以是同一个数。
    """
    account_ids = _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    _published(clean)
    _seed_routes(clean, account_ids)

    frozen = _ok(clean.run(("freeze_cohort", {})))
    assert frozen["member_count"] == len(account_ids)
    _seed_routes(clean, tuple(row["account_id"] for row in frozen["members"]))

    drafted = _ok(clean.run(("draft_outreach", _draft(wave=WAVE, cohort=""))))
    assert drafted["subjects"]["kind"] == "cohort"
    assert drafted["subjects"]["cohort_ref"] == ""
    assert drafted["subjects"]["cohort_id"] == frozen["cohort_id"], (
        "空串那条路要报出它**真的**读的是哪一份 —— 否则「最近一份」这句话在两份报告"
        "里都成立，而它们读的可以是两份不同的名单"
    )
    assert drafted["subjects"]["source_ref_prefix"] == "cohort:"
    assert drafted["subjects"]["routes_considered"] == len(account_ids)
    assert drafted["plan"]["planned"] == len(account_ids), (
        f"这一批的收件人该是名单上那些。计划：{drafted['plan']!r}"
    )
    assert drafted["send_authorized"] is False, "起草完成不是允许发送"

    # 同一批的下一半：冻清单那一条收的也是同一份名单，回执报的是同一格。
    prepared = _ok(clean.run(("prepare_wave", _prepare(wave=WAVE, cohort=frozen["cohort_id"]))))
    assert prepared["subjects"]["kind"] == "cohort"
    assert prepared["subjects"]["cohort_id"] == frozen["cohort_id"]


def _published(session: Session) -> None:
    """两发**发布**：先写作策略，再模板族（§7：用户不得直接写数据库）。

    夹具直接 ``put_family`` 会省掉两次调用，而它正是 §7 要修的那件事：一个干净
    客户端发布不了话术时，每一次 ``draft_outreach`` 的前提都是夹具替它造好的。
    """
    _ok(
        session.run(
            (
                "put_writing_policy",
                {"policy_id": POLICY, "revision": 1, "generic_wording_id": "wording-generic-1"},
            )
        )
    )
    _ok(
        session.run(
            (
                "put_family",
                {
                    "family_id": FAMILY,
                    "revision": 1,
                    "stage": "stage-1",
                    "role_class": "grounds_manager",
                    "locale": LOCALE,
                    "product": "natural-grass",
                    "writing_policy_id": POLICY,
                },
            )
        )
    )


# --- 八、没接线时三个工具各说各的 ----------------------------------------------


def test_the_cohort_tools_say_not_wired_rather_than_empty(settings: Settings, tmp_path: Path) -> None:
    """一个没接名单存储的部署：读工具给 ``not_wired``，写工具**拒绝**。

    这一对不是两种风格：``list_cohorts`` 答的是「这个部署冻过哪些」，名单不在
    **这个**部署里 —— 那与「一份都没冻过」的处置不同（一个是去接线，一个是去
    ``freeze_cohort``）。而 ``freeze_cohort`` / ``get_cohort`` 要读要写一个不存在的
    存储，给一个空答案会让它们读成「冻了一份空的」与「没有这一份名单」。
    """
    ring, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    scenes = default_scene_store(settings)
    handlers = product_loop_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=default_maintenance_store(settings),
        assets=default_asset_store(settings),
        relations=default_relation_store(settings),
        accounts=default_account_pool_store(settings),
        cohorts=None,
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )

    def run(*calls: tuple[str, dict[str, Any]]):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            product_loop=handlers,
            scenes=scenes,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    listed = _ok(run(("list_cohorts", {})))
    assert listed["cohorts"] == []
    assert listed["cohort_count"] == 0
    assert listed["cohort_state"] == "not_wired", (
        "空清单与 not_wired 是两句话：前者是「一份都没冻过」，后者是「这个部署里"
        "根本没有这一样」—— 而它们的下一步相反"
    )
    assert "接线" in listed["note"]

    for tool, payload in (("freeze_cohort", {}), ("get_cohort", {})):
        refused = _refused(run((tool, payload)))
        assert "没有接" in refused, f"{tool}：{refused}"
        assert "接线" in refused, f"{tool}：{refused}"


# --- 七、as-of 锚：两个 session 读同一份冻结读数 -------------------------------
#
# §B25：「所有 session 都能读同一 server-side report/as_of/cursor」、
# 「Codex/WorkBuddy 读取同一 snapshot 业务键/单位/as_of 一致」。
#
# 回执上因此有两格**不同的问题**，这里逐条把它们的边界钉住：
#
# * ``as_of`` —— 这一批主体读的是**哪一份**冻住的读数，报的是**那份东西自己的
#   时刻**。同一份名单在任何 session、任何时刻读回来都是同一个字符串。
# * ``at``（读侧各自取名，池子回读那一格叫 ``checked_at``）—— **这一次读**发生在
#   什么时候。它是活的，随会话变、随两次调用变，那是它该有的样子。
#
# 合成的后果是不对称的，而两个方向都看不出来：把 ``at`` 塞进 ``as_of``，两个
# session 报的时刻当场不同（B25 直接不成立）；把 ``as_of`` 塞进 ``at``，新鲜度
# 判定会拿一个冻住的时刻当「现在」，于是每一格都判成刚复核过 —— 报告上一切正常。
#
# 两条判据**必须成对**，缺一条就有一条变异能全绿：只测「同一份 ⇒ 同一个值」的话，
# 一个永远返回常量的实现全绿；只测「两份 ⇒ 两个值」的话，一个返回 ``utc_now()``
# 的实现全绿。


def test_two_sessions_reading_one_frozen_batch_agree_on_as_of(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一份名单、两个 session：``as_of`` 逐字相同，而 ``at`` 是**这一次读**的时刻。

    ## 为什么把读侧的时钟钉住，而不是靠两次调用「自然不同」

    「两次读的时刻不同」如果靠睡眠去凑，测的是机器快不快，而不是代码对不对。
    下面把 ``product_loop`` 的 ``utc_now`` 换成一个**离冻结时刻很远的**固定点：
    于是这一条变成一句可以逐字核的话 —— ``at`` 等于那个固定点（读侧确实取了自己的
    时钟），而 ``as_of`` 仍然等于那份名单冻下来的时刻（锚没有跟着时钟走）。

    一个把 ``as_of`` 写成 ``utc_now()`` 的实现，会在这里把 ``at`` 的值交出来，
    而它与 ``frozen["as_of"]`` 差着十万八千里。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    frozen = _ok(clean.run(("freeze_cohort", {})))
    cohort_id = frozen["cohort_id"]

    #: 一个**远在冻结之后**的固定时刻。它只该出现在 ``at`` 那一格上。
    later = datetime(2027, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(product_loop, "utc_now", lambda: later)

    first = _ok(clean.run(("get_cohort", {"cohort_id": cohort_id})))
    restarted = clean.again()
    second = _ok(restarted.run(("get_cohort", {"cohort_id": cohort_id})))
    #: 空 ``cohort_id`` = 「这个 Scene 最近冻的那一份」，走的是**另一条**分支
    #: （``current_cohort`` 而不是 ``get_cohort``）—— 两条都要给出同一个锚。
    latest = _ok(restarted.run(("get_cohort", {})))

    assert first["as_of"] == second["as_of"] == latest["as_of"], (
        "两个 session 读同一份冻结名单，as_of 必须逐字相同 —— 它答的是「这一批读的"
        "是哪一份」，而那一份并没有因为换了个会话就变"
    )
    assert first["as_of"] == frozen["as_of"], (
        "as_of 必须等于**冻结那一刻**报出的同一个值：它是那份读数自己的时刻，"
        "不是任何一次读的时刻"
    )
    assert first["as_of"] == first["created_at"], (
        "同一份名单的身份一半（created_at）与 as_of 说的是同一个时刻 —— 在这一格上"
        "它们同值，但角色不同：created_at 是这份名单的身份，as_of 是读侧回执统一的"
        "那一格"
    )

    assert first["at"] == second["at"] == latest["at"] == later.isoformat(), (
        "at 是**这一次读**的时刻：读侧的时钟被钉在哪，它就该报哪 —— 它没有跟着"
        "as_of 走"
    )
    assert first["at"] != first["as_of"], (
        "两格不许合成一格：一个答「这份读数什么时候冻的」，一个答「我什么时候读的」"
    )


def test_two_different_frozen_batches_have_two_different_as_of(clean: Session) -> None:
    """两份不同的冻结名单：``as_of`` 必须跟着换 —— 否则「同一个值」是个常量。

    这一条是上面那一条的**另一半**，缺了它，一个 ``return "1970-01-01T00:00:00Z"``
    的实现能让上面那条全绿。这里真的冻两份（同一池子、两条不同的选择规则 ⇒ 内容
    寻址的两个 id ⇒ 两次冻结、两个时刻），再逐份读回来。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))

    first = _ok(clean.run(("freeze_cohort", {})))
    # ``limit`` 是**选择规则的一部分**，所以它进 cohort_id 的原像：改它就是另一份
    # 名单，而旧的那一份还在盘上读得回来。
    second = _ok(clean.run(("freeze_cohort", {"limit": 1})))

    assert first["cohort_id"] != second["cohort_id"], (
        "这一条判据要有主语：两份名单真的是两份（不同的 id），后面比 as_of 才有意义"
    )
    assert first["as_of"] != second["as_of"], (
        "两份不同的冻结读数必须有两个不同的 as_of —— 否则那一格答的不是「哪一份」"
    )

    # 读回来也一样：两份名单、两个锚，逐份对得上。
    read_first = _ok(clean.run(("get_cohort", {"cohort_id": first["cohort_id"]})))
    read_second = _ok(clean.run(("get_cohort", {"cohort_id": second["cohort_id"]})))
    assert read_first["as_of"] == first["as_of"]
    assert read_second["as_of"] == second["as_of"]
    assert read_first["as_of"] != read_second["as_of"]


def test_the_maintenance_receipts_carry_the_batch_same_as_of(clean: Session) -> None:
    """跨工具：读同一份名单的四条工具，``as_of`` 那一格逐字相同。

    这正是 §B25 那句「Codex 和 WorkBuddy 读到同一 snapshot 的 as_of」的落点 ——
    两个客户端不必读**同一条**工具，它们读同一份名单就够了。所以这一条比的是
    **四个回执之间**，不是同一个回执的两次调用。

    ``run_maintenance`` 那一格尤其要说清：它报两个时刻，``at`` 是**这一轮跑在何时**
    （它同时是派发与新鲜度判定的基准），``as_of`` 是**这一轮看的是哪一份**。
    把它俩合成一格，要么让「这一轮什么时候跑的」答不出来，要么让到期判定拿一个
    冻住的时刻当现在。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    frozen = _ok(clean.run(("freeze_cohort", {})))
    cohort = frozen["cohort_id"]

    detail = _ok(clean.run(("get_cohort", {"cohort_id": cohort})))
    plan = _ok(clean.run(("get_maintenance_plan", {"cohort": cohort})))
    report = _ok(clean.run(("get_ops_report", {"cohort": cohort})))
    tick = _ok(clean.run(("run_maintenance", {"cohort": cohort})))

    assert {
        detail["as_of"],
        plan["as_of"],
        report["as_of"],
        tick["as_of"],
    } == {frozen["as_of"]}, (
        "同一份名单在四条工具上必须给出同一个 as_of —— 否则两个客户端各读一条工具，"
        "就会拿到两个「同一批」的说法"
    )
    assert tick["as_of"] != tick["at"], (
        "run_maintenance 的两格是两个问题：at 是这一轮跑在何时，as_of 是这一轮看的"
        "是哪一份"
    )


def test_a_named_list_has_no_as_of_and_says_so(clean: Session) -> None:
    """``name`` 那条路**没有**冻住的东西，所以 ``as_of`` 如实报 ``null``。

    operator 手写的 ``inputs/<名字>.yaml`` 不是一份冻结读数：它不被内容寻址、没有
    冻结时刻、随写随变。那里不编一个 —— 一个编出来的锚会让一份没冻过的清单看起来
    和一份冻过的名单一样可引用，而两者的处置相反（前者去改文件，后者去查是哪一份）。
    """
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))
    clean.settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (clean.settings.inputs_dir / "hand-written.yaml").write_text(
        "accounts:\n"
        "  - account_id: hand-written-one\n"
        "    organisation: Hand Written Ltd\n"
        "    cohort: known_success\n"
        "    domain: hand-written.example\n",
        encoding="utf-8",
    )

    plan = _ok(clean.run(("get_maintenance_plan", {"name": "hand-written"})))
    assert plan["subjects"]["kind"] == "name", "这一条要走的正是 name 那条路"
    assert plan["as_of"] is None, (
        "这份清单没有冻结时刻，所以这一格如实说答不上来，而不是编一个"
    )
    assert "cohort_id" not in plan["subjects"], (
        "name 那条路报的是 list_path 那一格，根本没有 cohort_id —— 两条路的形状"
        "一样、内容不一样，而「没有这一格」比「这一格是 null」说得更准"
    )


def test_the_cohort_id_and_the_as_of_name_the_same_batch(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回执里「哪一份名单」（``cohort_id``）与「那份名单的时刻」（``as_of``）同源。

    ## 这条判据要挡的是一个**真实可能**的分家

    ``subjects`` 那一栏的 ``cohort_id`` 与顶层的 ``as_of`` 说的是同一份名单。它们
    分家的形态是：解析主体时读到的是 A，渲染回执时又读了一遍存储、那一次读到的是
    B —— 中间又冻了一份。一份回执指着两份名单，而它自己读起来完全自洽。

    那一次重读只在 ``cohort: ""``（「用最近那一份」）这条路上发生，而它**正常**是
    读不出这个差的（同一个调用里两次读都很快）。所以这里把那个「两次读之间事情变了」
    的时刻**注入**出来：兜底那次读被换成一份**后冻的**名单。这不是在测一个假场景，
    是把那条路上唯一的危险摆成一个确定的输入。

    ## 断言不依赖注入的细节

    它只问一句：``cohort_id`` 指的那一份，是不是 ``as_of`` 说的那一份。
    """
    _discovered(clean)
    _ok(clean.run(("create_scene", {**BASE_CREATE, "activate": True})))

    # 先冻「上限 1 个」的那份（B），再冻不限的那份（A）。于是 **A 是最近那一份**，
    # ``cohort: ""`` 解析出来的是 A。
    latecomer = _ok(clean.run(("freeze_cohort", {"limit": 1})))
    latest = _ok(clean.run(("freeze_cohort", {})))
    assert latest["cohort_id"] != latecomer["cohort_id"]

    # 注入：兜底那次读现在答**B**（模拟「两次读之间又冻了一份」）。
    store = default_account_cohort_store(clean.settings)()
    other = store.get_cohort(latecomer["cohort_id"])
    assert other is not None
    monkeypatch.setattr(
        product_loop, "current_cohort", lambda _store, *, scene_id: other
    )

    plan = _ok(clean.run(("get_maintenance_plan", {"cohort": ""})))

    assert plan["as_of"] == latest["as_of"], (
        "主体是按最近那一份（A）解析的，所以锚必须是 A 的"
    )
    assert plan["subjects"]["cohort_id"] == latest["cohort_id"], (
        "同一份回执里的 cohort_id 与 as_of 必须说同一份名单 —— 那一栏若是自己再读"
        "一遍存储，就会报出后冻的那一份（B），而 as_of 说的还是 A"
    )
