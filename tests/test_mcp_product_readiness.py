"""查缺面走真实 MCP 会话时的行为（b01-c，PSM §10 / B01 ``:104``）。

## 这一层测的是「接线之后它还对不对」

判据本身已经存在，本文件不重测它：

* ``tests/test_product_readiness.py`` —— 三张清单、四轴归因、``not_reported`` 的减法、
  三条守卫、``price`` 与 ``region_variants`` 不判，全部在应用层直测；
* ``tests/test_domain_product.py`` —— ``why_not_applicable`` 的四轴与 ``applies_to``
  的委托关系；
* ``tests/test_registration_is_offline.py`` —— 登记面够不到网络（查缺面不在它的闭包里：
  它读的正是登记面写下去的那几版，而它自己不做外部调用，理由写在 ``_spec`` 那一行）。

本文件测的是**只有把接线接起来才会出现**的那几件事：时刻从哪来、主语不在盘上时交出去
的是拒绝还是一张空读数、**跨两面**（登记面写下去的那几版，查缺面读不读得回来）、
以及方案那一版的工作区守卫。

## 五组判据

* **参数表**：逐工具集合相等。钉住的是 ``at`` **不在**里面 —— 时刻取自服务端时钟。
* **跨两面**：一版产品资料 / 一版方案经登记面写下、经查缺面读回，读数里 ``usable``
  为真、三张清单为空、``not_reported`` 带着固定那一半。这一组是这一片存在的理由：
  两条路如果各算一份，分家的表现是「登记说写下了、查缺说不在盘上」，两边都正常。
* **不成主语的读数**：草案（缺格 + 没人签，两张清单同时非空）、归档版（三张全空而
  ``usable`` 假）、坏引用（引了一版不在盘上的产品资料）、引的不是最新版的说法。
* **读不到那一版**：**拒绝**，且那句话里写明了「不返回一张三张全空的读数」——
  一张空表与「什么都不缺」在报告上同形。
* **工作区**：方案那一版属于一个工作区，读到别的工作区的行时拒绝；产品版本没有那一列
  （B01 ``:104``），所以同一份库上它照读。两条在这里成对判，因为「不按工作区筛」的
  绿灯在一条**根本没绑定**的库上也会亮。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_catalog import product_catalog_handlers
from marketops.adapters.mcp_server.product_readiness import product_readiness_handlers
from marketops.config import Settings
from marketops.domain.outreach import ApprovedClaim
from marketops.domain.product import OfferVersion, ProductVersion
from marketops.domain.util import utc_now
from marketops.interfaces.composition import (
    default_outreach_store,
    default_product_catalog_store,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 会话主体。两条工具都是 ``_CONTROL``，所以这个身份同时是登记面那一侧写下 ``ready``
#: 时的核对者 —— 与 ``test_mcp_product_catalog.py`` 用的是同一个名字。
CONTROL_PRINCIPAL = "claude-control"

PRODUCT_ID = "probe-product"
OFFER_ID = "probe-offer"
CLAIM_ID = "probe-claim"

#: 别的工作区。取一个 ``slugify`` 的不动点（``check_canonical_id`` 要求），而它**不是**
#: 本部署的 profile —— 那个差就是下面那条守卫要判的东西。
FOREIGN_WORKSPACE = "another-workspace"

#: 一条自述完整的来源工件引用（``SourceArtifactRef`` 的字节不住在这里，它是一条索引）。
_ARTIFACT: dict[str, Any] = {
    "artifact_id": "probe-paint-spec",
    "locator": "https://example.invalid/paint-spec.pdf",
    "content_sha256": "a" * 64,
    "fetched_at": "2026-09-28T12:00:00+00:00",
    "media_type": "application/pdf",
}

#: 一版**各格都填满**的产品资料：查缺面读的是「缺什么」，所以「什么都不缺」那一份
#: 必须真的什么都不缺（``unknowns`` 空、``limits`` 非空、``source_refs`` 非空）。
_PRODUCT: dict[str, Any] = {
    "product_id": PRODUCT_ID,
    "version": 1,
    "display_name": "探针产品",
    "owner": "probe-owner",
    "status": "ready",
    "capabilities": ["surface-prep"],
    "limits": ["indoor-use-only"],
    "units": "litre",
    "source_refs": [_ARTIFACT],
}

#: **直写存储层**时要自己补上的两格。登记面那一趟由会话主体盖章（``_verifier``），而
#: 存储层没有主体可盖 —— ``ProductVersion._ready_means_verified`` 逐字「『已核对』这件事
#: 没有核对者，就只是一个更自信的 draft」，所以少了这两格的 ``ready`` 连构造都过不去。
#: 这不是夹具的麻烦，它是「登记面盖章」那件事不可省的反面证据。
_SEEDED_VERIFICATION: dict[str, Any] = {
    "approved_by": "probe-approver",
    "approved_at": "2026-09-28T12:00:00+00:00",
}

#: 一版**只差一个人核对**的产品资料：``limits`` 空着、状态是 ``draft``。
_DRAFT_PRODUCT: dict[str, Any] = {
    "product_id": PRODUCT_ID,
    "version": 2,
    "display_name": "探针产品（草案）",
    "owner": "probe-owner",
    "capabilities": ["surface-prep"],
    "units": "litre",
    "source_refs": [_ARTIFACT],
}

_OFFER: dict[str, Any] = {
    "offer_id": OFFER_ID,
    "version": 1,
    "name": "探针方案",
    "purpose": "launch",
    "owner": "probe-owner",
    "status": "ready",
    "product_refs": [{"product_id": PRODUCT_ID, "version": 1}],
    "markets": ["GB"],
    "locales": ["en"],
    "approved_claim_refs": [{"claim_id": CLAIM_ID, "revision": 1}],
}

#: 作用域全空的一条说法 = 旧读数（不限产品、不限市场、不限语言），且不过期。
#: 三条轴都空是刻意的：这一份判据要的是「什么都不缺」的读数，任何一条作用域都会
#: 让叉积里报出一条不属于本判据的缺口。
_CLAIM = ApprovedClaim(
    claim_id=CLAIM_ID,
    revision=1,
    text="这一版的能力经核对",
    source_ref="probe-paint-spec",
    approved_by="probe-approver",
    approved_at=datetime.fromisoformat("2026-09-28T12:00:00+00:00"),
    expires_at=None,
)

#: 两条工具只收主语的坐标，别的一格都没有。比的是集合相等而不是「不含某几个」：
#: 后者只在有人加回一个**已知**的坏名字时才红，而这一条在任何一个新参数出现时都要先
#: 被看到一眼 —— 那个参数是不是又一条「让调用方替此刻说话」的入口，正是要在这里回答
#: 的问题（``test_mcp_product_loop.py`` 的 ``_FORBIDDEN_PARAMETERS`` 那一段逐字同源）。
_FORBIDDEN_PARAMETERS = frozenset(
    {"at", "now", "as_of", "workspace", "profile", "approved_by", "status", "usable"}
)

_EXPECTED_PARAMETERS: dict[str, set[str]] = {
    "get_product_version_readiness": {"product_id", "version"},
    "get_offer_version_readiness": {"offer_id", "version"},
}


def _ring(tmp_path: Path) -> tuple[Path, str]:
    """种一个控制者令牌并落成环文件，返回 ``(环文件, 令牌)``。"""
    token, digest = mint(CONTROL_PRINCIPAL, [Role.CONTROLLER])
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(ring_payload(ring_entry(digest, CONTROL_PRINCIPAL, [Role.CONTROLLER]))),
        encoding="utf-8",
    )
    return path, token


@pytest.fixture
def session(settings: Settings, tmp_path: Path):
    """返回 ``new_session()`` —— 每调用一次就是一次全新的会话，都指向同一个库。

    与 ``test_mcp_product_catalog.py`` 的 ``_catalog_session`` 同款，多接了一面：
    **登记面**（写下去那一版）与**查缺面**（读回来那一版）合成同一个处理器表。两面共用
    同一句 ``default_product_catalog_store(settings)`` —— 组合根就是这么接的，而这里如果
    各建一个工厂，「登记面写进了哪个库」与「查缺面从哪个库读」就成了两个答案，且两个
    答案各自都正常（一个说「登记好了」，一个说「不在盘上」）。
    """

    def new_session():
        ring_path, token = _ring(tmp_path)
        ring = TokenRing.from_file(ring_path)
        catalogs = default_product_catalog_store(settings)
        handlers = {
            **product_catalog_handlers(settings=settings, catalogs=catalogs),
            **product_readiness_handlers(
                settings=settings,
                catalogs=catalogs,
                claims=default_outreach_store(settings),
            ),
        }

        def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
            outcome = drive_with_settings(
                settings=settings,
                tokens=ring,
                token=token,
                product_catalog=handlers,
                calls=calls,
                want_tool_list=want_tool_list,
            )
            assert not outcome.handshake_error, outcome.handshake_error
            return outcome

        return run

    return new_session


@pytest.fixture
def seeded(settings: Settings):
    """盘上的一份底座：一版 ``ready`` 的产品资料 + 一条不过期的说法。

    走**存储层**而不是登记面来种它，两个理由：

    * 说法没有登记面的工具（``put_claim`` 在外联那一段），而「这条说法已经批准」本来
      就是那一族的结果；
    * 产品资料那一条有登记面的工具，但**跨两面**那一组判据要的正是「经登记面写下」——
      先把同一版种进去，那条路会以「同 ``(id, 版)`` 内容不同」的冲突结束，而它红着，
      红的原因却不是跨两面不成立。

    种下去的这两行都**不绑工作区**（``put_version`` 的 ``workspace=None``；说法没有
    那一列），所以工作区那一组判据可以在这份底座上再加一行别的工作区的方案。
    """
    default_product_catalog_store(settings)().put_version(
        ProductVersion.model_validate({**_PRODUCT, **_SEEDED_VERIFICATION})
    )
    default_outreach_store(settings)().put_claim(_CLAIM)
    return settings


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    """第 ``index`` 次调用必须成功，并返回它的结构化结果。"""
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    """第 ``index`` 次调用必须**作为结果被拒**（不是把会话炸掉），返回那句话。"""
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


def _fields(reading: dict[str, Any], name: str) -> list[str]:
    """某一张清单里的 ``field`` 一列，按顺序。"""
    return [row["field"] for row in reading[name]]


# ----------------------------------------------------------------------
# 1. 参数表：``at`` **不在**里面
# ----------------------------------------------------------------------
@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_PARAMETERS))
def test_the_readiness_surface_takes_no_instant(settings: Settings, tool_name: str) -> None:
    """每一条工具的入参集合**恰好**是它自己那一组，``at`` 一个都没有。

    ``at`` 是这一片最要紧的缺席：三张清单的每一行都拿同一个时刻判（被引用的说法到
    这一刻过期了没有），而一个由调用方挑的时刻会让**同一条盘上事实**在两个调用方那里
    得到相反的两份读数 —— 两边各自都正常，没有一处会响。与 ``product_catalog._verifier``
    的 ``approved_at`` 是同一条纪律。
    """
    handler = product_readiness_handlers(
        settings=settings,
        catalogs=default_product_catalog_store(settings),
        claims=default_outreach_store(settings),
    )[tool_name]
    parameters = set(inspect.signature(handler).parameters)

    forbidden = parameters & _FORBIDDEN_PARAMETERS
    assert not forbidden, (
        f"{tool_name} 收下了 {sorted(forbidden)} —— 这些名字承载的事实"
        "（这一刻是什么时候、本部署是哪个工作区、这一版可不可用）都不由载荷提供："
        "时刻取自服务端时钟，工作区取自 Settings，而 usable 是这一层算出来的读数"
    )
    assert parameters == _EXPECTED_PARAMETERS[tool_name]


# ----------------------------------------------------------------------
# 2. 跨两面：登记面写下去的那一版，查缺面读得回来
# ----------------------------------------------------------------------
def test_a_version_registered_through_the_write_face_reads_back_clean(session) -> None:
    """一版经**登记面**写下的产品资料，**查缺面**读回来是干净的。

    这一条是这一片存在的理由：登记面与查缺面是两条路，而它们读写的必须是**同一份盘上
    状态**。两处各算一份的表现是「登记说写下了、查缺说不在盘上」—— 两个回执各自都正常，
    分家只在把两条路接在同一份状态上时才看得见。

    ``not_reported`` **不是空的**，而那不是缺格：它是这一层**答不上来**的固定那一半
    （来源工件的字节今天还在不在盘上）。一条什么都不缺的读数也照样带着它 —— 一份全空的
    「答不上来」清单会让人读成「这个检查表每一项都有人答了」。
    """
    outcome = session()(
        ("put_product_version", dict(_PRODUCT)),
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 1}),
    )
    reading = _ok(outcome, 1)

    assert reading["subject"] == f"product:{PRODUCT_ID}@1"
    assert reading["kind"] == "product"
    assert reading["status"] == "ready"
    assert reading["usable"] is True
    assert reading["missing"] == []
    assert reading["invalid"] == []
    assert reading["awaiting_approval"] == []
    assert [item["item"] for item in reading["not_reported"]] == ["来源工件在不在盘上"]


def test_an_offer_reads_back_clean_once_its_claim_is_approved(session, seeded) -> None:
    """一版方案：``usable`` 真 —— 它引的产品资料在盘上且已核对、引的说法在第 1 版。

    方案这一侧比产品那一侧多三层：它引的**产品版本**在盘上且已核对（``invalid`` 那一族），
    它引的**说法**在最新一版上，而那一条在这一刻作不作数（期限那一轴）。所以这一条
    同时是那三层的对照 —— 它读得干净，不是因为它们没被读。
    """
    outcome = session()(
        ("put_offer_version", dict(_OFFER)),
        ("get_offer_version_readiness", {"offer_id": OFFER_ID, "version": 1}),
    )
    reading = _ok(outcome, 1)

    assert reading["subject"] == f"offer:{OFFER_ID}@1"
    assert reading["kind"] == "offer"
    assert reading["status"] == "ready"
    assert reading["usable"] is True
    assert reading["missing"] == []
    assert reading["invalid"] == []
    assert reading["awaiting_approval"] == []


def test_the_reading_carries_the_server_clock_not_a_payload_instant(session, seeded) -> None:
    """读数里那一格时刻来自**服务端时钟**：带时区、且就在此刻附近。

    这是「``at`` 不是参数」那一条的另一半。只判参数表的话，一个把时刻硬写成常量的实现
    （``datetime(2020, 1, 1, tzinfo=UTC)``）会照旧全绿 —— 而它的表现是**所有**引用永远
    「不过期」，报告上读起来像资料很干净。
    """
    before = utc_now()
    outcome = session()(
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 1}),
    )
    reading = _ok(outcome, 0)
    at = datetime.fromisoformat(reading["at"])

    assert at.tzinfo is not None, reading["at"]
    assert 0 <= (at - before).total_seconds() < 60, (before.isoformat(), reading["at"])


# ----------------------------------------------------------------------
# 3. 不成主语的读数：草案、归档、坏引用、引的不是最新版
# ----------------------------------------------------------------------
def test_a_draft_reports_both_what_is_missing_and_what_is_unsigned(session, seeded) -> None:
    """一版草案：``missing`` 与 ``awaiting_approval`` **同时**非空，``usable`` 假。

    这一条判的是那两张清单**不互斥**这条设计。压成一张会让「把缺的格补上」被读成
    「补完就能跑」—— 而一版没人核对过的资料补得再满也还是 ``draft``。

    底座（``seeded``）在这里是**判据的一部分**，不是省事：登记面把第 2 版的
    ``supersedes`` 推成 1，而那份底座里正好有第 1 版 —— 少了它，这一条会以一条与本
    判据无关的 ``supersedes`` 断节红（那条形状单独有一条判据，见下一个）。
    """
    outcome = session()(
        ("put_product_version", dict(_DRAFT_PRODUCT)),
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 2}),
    )
    reading = _ok(outcome, 1)

    assert reading["status"] == "draft"
    assert reading["usable"] is False
    assert _fields(reading, "missing") == ["limits"]
    assert _fields(reading, "awaiting_approval") == ["approved_by"]
    assert reading["invalid"] == []


def test_a_retired_version_is_not_a_gap(session, seeded) -> None:
    """一版归档：三张清单**全空**而 ``usable`` 假。

    「归档」不是一种缺口：它是这一版不再被用，而不是它缺了什么东西。把它报成一张要补的
    清单会让人去补一个已经归档的版本 —— 而它缺的那一格补完也没有意义。
    """
    outcome = session()(
        ("put_product_version", {**_PRODUCT, "version": 3, "status": "retired"}),
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 3}),
    )
    reading = _ok(outcome, 1)

    assert reading["status"] == "retired"
    assert reading["usable"] is False
    assert reading["missing"] == []
    assert reading["invalid"] == []
    assert reading["awaiting_approval"] == []


def test_a_second_version_registered_alone_reports_a_dangling_supersedes(session) -> None:
    """空盘上直接登记第 2 版：查缺面报 ``supersedes`` 断节 —— 而这不是登记面的错。

    登记面照版本号推 ``supersedes``（第 1 版 ``None``、第 2 版 ``1``），它不问第 1 版在
    不在盘上。所以「只写第 2 版」在库里留下一行自称取代了一个不存在的版本 —— 这正是
    ``invalid`` 那张清单存在的理由（先修引用关系，补别的都白补）。

    这一条同时是上一条的对照：两处用的是**同一个** ``_DRAFT_PRODUCT`` 载荷，差别只在
    底座有没有第 1 版。
    """
    outcome = session()(
        ("put_product_version", dict(_DRAFT_PRODUCT)),
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 2}),
    )
    reading = _ok(outcome, 1)

    assert _fields(reading, "invalid") == ["supersedes"]
    assert reading["invalid"][0]["subject"] == f"product:{PRODUCT_ID}@2"


def test_an_offer_citing_a_version_that_is_not_on_disk_is_invalid(session, seeded) -> None:
    """方案点了一版不在盘上的产品资料：``invalid`` 非空且 ``usable`` 假。

    ``invalid`` 与 ``missing`` 的处置相反（前者是**先修引用关系**，补别的都白补），所以
    两件事不能落在同一张表上：一份指着不存在的东西的方案，它自称引用了什么本身就不可信。

    ``field`` 是**主语**上要动的那一格（``product_refs``），而 ``subject`` 是这条缺口
    **关于**的那一条 —— 这里是那版不存在的产品资料，不是方案自己。两格分开才数得出
    「这一版引用的东西里有几条有问题」。
    """
    outcome = session()(
        (
            "put_offer_version",
            {**_OFFER, "product_refs": [{"product_id": "ghost-product", "version": 1}]},
        ),
        ("get_offer_version_readiness", {"offer_id": OFFER_ID, "version": 1}),
    )
    reading = _ok(outcome, 1)

    assert reading["usable"] is False
    assert _fields(reading, "invalid") == ["product_refs"]
    assert reading["invalid"][0]["subject"] == "product:ghost-product@1"


def test_a_cited_claim_that_is_not_the_latest_revision_says_so_instead_of_substituting(
    session, seeded
) -> None:
    """引的说法不是最新版：如实进 ``not_reported``，**不拿最新版顶替**。

    ``approved_claims()`` 只给每个 ``claim_id`` 的最新一版（端口 docstring 逐字），而旧版
    在盘上 —— 缺的是端口上一个按 ``(claim_id, revision)`` 读的方法。拿最新版去判被引的
    那一版，会在最新一版**续期**的那天把一条有效引用报成过期：报告上读起来像「资料有
    问题」，实际是这一层拿错了对象。

    这一条同时钉住 ``not_reported`` 是**减法**：固定那一半与条件那一半都在，而条件那一半
    只在「引的说法旧于盘上最新版」时出现 —— 上一条读数（引的就是最新版）里它不在。
    """
    default_outreach_store(seeded)().put_claim(_CLAIM.model_copy(update={"revision": 2}))
    outcome = session()(
        ("put_offer_version", dict(_OFFER)),
        ("get_offer_version_readiness", {"offer_id": OFFER_ID, "version": 1}),
    )
    reading = _ok(outcome, 1)

    items = [item["item"] for item in reading["not_reported"]]
    assert len(items) == 2, items
    assert items[0] == "来源工件在不在盘上"
    assert f"{CLAIM_ID} 第 1 版" in items[1]
    assert "按 ``(claim_id, revision)`` 读的方法" in reading["not_reported"][1]["reason"]
    # 旧版读不出来**不是**一条要补的缺口：盘上那一条并没有坏。
    assert reading["invalid"] == []
    assert reading["missing"] == []


# ----------------------------------------------------------------------
# 4. 读不到那一版 ⇒ 拒绝，不是一张空读数
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tool", "payload", "what"),
    [
        (
            "get_product_version_readiness",
            {"product_id": "ghost-product", "version": 1},
            "产品资料",
        ),
        ("get_offer_version_readiness", {"offer_id": "ghost-offer", "version": 1}, "方案"),
    ],
)
def test_a_subject_that_is_not_on_disk_is_refused_not_answered_with_emptiness(
    session, seeded, tool: str, payload: dict[str, Any], what: str
) -> None:
    """主语不在盘上时交出去的是**拒绝**，而且那句话里写明了拒的是什么。

    这一条是本片最要紧的形状：**三张全空的读数与「什么都不缺」同形**，所以一个写错 id
    的调用方会拿到一份「可以开跑」的报告。两份读数在回执上也确实同形 —— 都只有那几格，
    区别只在三张清单空不空，而空正是这里要挡的那种答案。

    底座（``seeded``）是刻意加上的：没有它，「这一条不在盘上」与「库是空的」也同形。
    两个主语各自拒一次，而 ``what`` 那一格不同 —— 读的人才知道下一步去哪找那条记录。
    """
    outcome = session()((tool, dict(payload)))
    text = _refused(outcome, 0)

    assert what in text
    assert "不在盘上" in text
    assert "三张全空" in text, text


# ----------------------------------------------------------------------
# 5. 工作区：方案那一版有，产品版本没有
# ----------------------------------------------------------------------
def test_an_offer_of_another_workspace_is_refused_and_both_workspaces_are_named(
    session, settings: Settings
) -> None:
    """别的工作区的那一版方案：拒绝，且点名两个工作区。

    库那一侧只在**写**的时候绑工作区（``sqlite/db.py::bind_workspace``，由第一行写入
    建立），读路径不查（``SqliteProductCatalog.get_offer`` 是一行 SELECT），所以这一条
    守卫只能在这一层 —— 与 ``runs.py::_one_run`` 逐字同源。跨工作区的读不留痕：它在这
    一侧看起来只是一次成功的查询，而被读到的那一份此后会被当成本地事实读回去（它引的
    产品版本、它靠的已批准说法，都不是本部署核对过的东西）。

    这一行**只能**经存储层种下去：登记面把工作区钉成 ``settings.profile``，所以「别的工作
    区的方案」在那条路上写不出来 —— 而它写不出来的那份库今天确实存在（本部署的库文件被
    另一个部署写过），这正是这条守卫要挡的形状。

    那一版写成 ``draft`` 只是为了少两格盖章：拒绝发生在读那一行之后、判缺之前，与它的
    状态无关。
    """
    default_product_catalog_store(settings)().put_offer(
        OfferVersion.model_validate(
            {**_OFFER, "status": "draft", "workspace": FOREIGN_WORKSPACE}
        )
    )
    outcome = session()(
        ("get_offer_version_readiness", {"offer_id": OFFER_ID, "version": 1}),
    )
    text = _refused(outcome, 0)

    assert FOREIGN_WORKSPACE in text, text
    assert settings.profile in text, text
    assert "是别人的那一版" in text, text


def test_a_product_version_on_a_foreign_bound_database_is_still_readable(
    session, settings: Settings
) -> None:
    """同一份库上：方案那一读被拒，而产品资料那一读照走 —— 这不是遗漏。

    产品版本**没有**工作区那一列。B01 ``:104`` 逐字「一个获批产品版本可被两个 Scene
    共用」：产品资料是「我们卖什么」，它不属于任何一次投放。加一列来让上一条守卫成立，
    正好是那句话不要的读法 —— 所以两条读面对工作区的处理**不同**，而那个不同有出处。

    两半都判是这一条的要点：只判「产品读得出来」，绿灯在一条**根本没绑定**的库上也会亮
    （那时「不按工作区筛」与「库里什么都没有」同形）。所以先让方案那一读在这份**同一个
    库**上被拒 —— 证明绑定真的落在了它身上 —— 再看产品那一读。
    """
    default_product_catalog_store(settings)().put_offer(
        OfferVersion.model_validate(
            {**_OFFER, "status": "draft", "workspace": FOREIGN_WORKSPACE}
        )
    )
    refused = _refused(
        session()(("get_offer_version_readiness", {"offer_id": OFFER_ID, "version": 1})), 0
    )
    assert FOREIGN_WORKSPACE in refused, refused

    outcome = session()(
        ("put_product_version", dict(_PRODUCT)),
        ("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 1}),
    )
    reading = _ok(outcome, 1)
    assert reading["kind"] == "product"
    assert reading["usable"] is True


# ----------------------------------------------------------------------
# 6. 存储层的失败是一条结果，不是一次崩溃
# ----------------------------------------------------------------------
def test_a_store_that_cannot_be_opened_comes_back_as_a_result(session, settings) -> None:
    """库的 schema 版本对不上：一条可读的结果，不是一次崩溃。

    制造它的先例在 ``tests/test_budget_ledger.py``（把 ``schema_meta`` 的版本改成 999）。
    存储层那句话已经比这一层能补的更具体（它点名了库文件、两个版本号，以及为什么不做自动
    迁移），所以这里不重写 —— 只保证它**回得到工具结果上**。不接的形态是调用方读到一句
    光秃秃的 ``Error executing tool <工具名>``，存储层那句话整个丢掉
    （``_shared.store_failure`` 的 docstring 记的就是这件事），所以判据判的是**存储层自己
    那句话在不在**，而不是回执的抬头 —— 那个抬头是传输层给每一个工具错误都加的。

    这一条同时判到一件事：``StoreError`` 会从**工厂构造处**抛（``SqliteProductCatalog``
    的 ``__init__`` 里跑 ``ensure_schema``），所以 ``catalogs()`` / ``claims()`` 的调用也
    必须在那一对 try 里面 —— 少了它，这一条会以「工具崩了」的形状红。
    """
    from marketops.adapters.sqlite.db import connect

    default_product_catalog_store(settings)()  # 先把库建起来
    with connect(settings.database_path) as conn:
        conn.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")

    text = _refused(
        session()(("get_product_version_readiness", {"product_id": PRODUCT_ID, "version": 1})), 0
    )
    assert "状态库 schema 版本是 999" in text, text
    assert "不做自动迁移" in text, f"存储层那句话没有回到结果上：{text!r}"
