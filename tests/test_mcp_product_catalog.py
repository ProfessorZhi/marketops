"""产品目录**登记面**走真实 MCP 会话时的行为（b01-b，B01 ``:104``）。

## 这一层测的是「接线之后它还对不对」

下游各自的判据已经存在，本文件不重测它们：

* ``tests/test_domain_product.py`` —— 域类型上的校验器（``ready`` 要核实资料与核对者、
  地区变体只能收窄、价格只能住在 ``QuotedPrice`` 里）；
* ``tests/test_product_catalog_contract.py`` —— 同一份存储契约在两个实现上跑；
* ``tests/test_registration_is_offline.py`` —— 登记面够不到网络（import 闭包）。

本文件测的是**只有把三段接起来才会出现**的那几件事：那三格字段从哪来、同一个 id
写两次会发生什么、回执里有什么、以及「拼错一个词」与「库坏了」是不是两句话。

## 五组判据

* **参数表**：逐工具集合相等。这一组钉住的是**三个缺席的名字** —— ``approved_by`` /
  ``approved_at`` / ``supersedes``。它们不是「顺手没收」，而是取自传输层令牌、本机
  时钟与版本号（理由见 ``product_catalog.py`` 的模块 docstring 那一节）；集合相等让
  「将来有人把它们加回来」当场变红，而不是等一次自签的记录出现在库里。
* **回执**：``registered`` / ``stored`` / ``next_version`` 是真的那一套。
  ``put_product_line`` **没有** ``next_version``（它没有版本号），产品版本那一格
  **没有** ``workspace``（B01 ``:104`` 逐字「一个获批产品版本可被两个 Scene 共用」，
  一个恒等于本部署名字的字段会被读成「这一版属于这个工作区」）。
* **幂等与冲突**：同 ``(id, 版)`` 同内容再写一次是**成功**（重启后重放一批登记走的
  就是这条路），内容不同被拒且拒词点名主语；产品线的同一个 ``line_id`` 同一对。
* **派生格**：``ready`` 那一版的 ``approved_by`` 是**会话主体**、``approved_at`` 非空；
  ``draft`` 那一版两格皆空；``supersedes`` 按版本号 —— 第 1 版 ``None``、第 2 版 ``1``。
* **拒得下来**：``ready`` 缺 ``source_refs``、``status`` 拼错、Offer 的 ``ready`` 缺
  ``markets`` 各拒绝一次，且拒词点名那一格。只有正常路径的用例没法区分「拒绝对了」
  与「拒绝根本没跑」，所以每条「能过」的都配了一条「过不去」的。

## 为什么这里另建一个会话工厂而不复用 ``test_mcp_product_loop`` 的那一个

那一个接的是产品环那十几条工具（场景、池子、责任关系……），构造要七个存储工厂；
登记面只用得上一个。更要紧的是它没有「第二次会话」这个形状，而**重启后重放**正是
本文件要测的一条 —— 见 :func:`_catalog_session` 里 ``new_session`` 那一段。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from marketops.adapters.mcp_server.product_catalog import product_catalog_handlers
from marketops.config import Settings
from marketops.interfaces.composition import default_product_catalog_store
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 会话主体。三条工具都是 ``_CONTROL``，所以这个身份同时是 ``ready`` 那一版的
#: 核对者 —— 判据里那张脸要对上它是**从令牌来的**，不是从载荷来的。
CONTROL_PRINCIPAL = "claude-control"

#: 一份核实资料。``SourceArtifactRef`` 的字节不住在这里（它是一条索引），所以
#: 定位符与摘要是这一格里唯一能被核对的东西。
_ARTIFACT: dict[str, Any] = {
    "artifact_id": "probe-paint-spec",
    "locator": "https://example.invalid/paint-spec.pdf",
    "content_sha256": "a" * 64,
    "fetched_at": "2026-09-28T12:00:00+00:00",
    "media_type": "application/pdf",
}

#: 一版产品资料。``capabilities`` 与 ``region_variants`` 配成一对收窄的写法 ——
#: 变体只能收窄这一条由域层判（``tests/test_domain_product.py`` 第 1 组），这里
#: 用它只是为了让「能过」的那一版是一条真的能过的。
_PRODUCT: dict[str, Any] = {
    "product_id": "probe-product",
    "version": 1,
    "display_name": "探针产品",
    "owner": "probe-owner",
    "capabilities": ["surface-prep", "aftercare"],
    "limits": ["no-synthetic-turf"],
    "units": "litre",
    "region_variants": [{"market": "GB", "capabilities": ["aftercare"]}],
    "source_refs": [_ARTIFACT],
    "unknowns": ["cure-time-in-humid-climate"],
}

_OFFER: dict[str, Any] = {
    "offer_id": "probe-offer",
    "version": 1,
    "name": "探针方案",
    "purpose": "launch",
    "owner": "probe-owner",
    "product_refs": [{"product_id": "probe-product", "version": 1}],
    "markets": ["GB"],
    "locales": ["en"],
    "approved_claim_refs": [{"claim_id": "probe-claim", "revision": 1}],
    "price": {
        "currency": "GBP",
        "amount_minor": 1250,
        "basis": "per litre",
        "source_ref": "probe-paint-spec",
        "quoted_at": "2026-09-28T12:00:00+00:00",
    },
}

_LINE: dict[str, Any] = {
    "line_id": "probe-line",
    "name": "探针产品线",
    "owner": "probe-owner",
    "customer_problem": "天然草坪的划线反复脱落",
    "capability_family": "surface-marking",
    "product_refs": [{"product_id": "probe-product", "version": 1}],
    "approved_material_refs": ["probe-paint-spec"],
}

#: 三个工具各一份**最小**载荷 —— 只有必填那几格。用来断言回执形状与幂等边界，
#: 不掺任何可选格：形状判据混进可选格之后，「少了哪一格」就说不清了。
_MINIMAL: dict[str, dict[str, Any]] = {
    "put_product_version": {
        "product_id": "probe-minimal-product",
        "version": 1,
        "display_name": "最小的一版",
        "owner": "probe-owner",
    },
    "put_offer_version": {
        "offer_id": "probe-minimal-offer",
        "version": 1,
        "name": "最小的一版",
        "purpose": "launch",
        "owner": "probe-owner",
    },
    "put_product_line": {
        "line_id": "probe-minimal-line",
        "name": "最小的一条",
        "owner": "probe-owner",
    },
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


def _catalog_session(tmp_path: Path, settings: Settings):
    """返回 ``new_session()`` —— 每调用一次就是**一次全新的会话**。

    新令牌、新处理器表、新存储工厂，而它们都指向同一个 ``settings.database_path``。
    这一点是刻意的：「重启后重放一批登记」要测的是**同一份盘上的东西被第二个进程
    读出来**，而在一个会话里连调两次只证明了那个对象还没坏 —— 后者的红会指向
    存储层，前者才是工具层该管的那条边界。

    每次会话都用新加工厂，不重用一个实例：``default_product_catalog_store`` 的
    返回值是一个「每次调用开一条新连接」的工厂（sqlite 连接不跨线程），复用实例就
    把这条约定悄悄绕过去了。
    """

    def new_session():
        ring_path, token = _ring(tmp_path)
        ring = TokenRing.from_file(ring_path)
        handlers = product_catalog_handlers(
            settings=settings,
            catalogs=default_product_catalog_store(settings),
        )

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
def new_session(settings: Settings, tmp_path: Path):
    """登记面的会话工厂；用法是 ``new_session()(("工具名", 载荷), …)``。"""
    return _catalog_session(tmp_path, settings)


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    """第 ``index`` 次调用必须成功，并返回它的结构化结果。

    失败时把工具自己那句话带进断言消息 —— 一个只报 ``is_error is True`` 的用例在
    排查时要先手工重跑一遍才知道它到底说了什么。
    """
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


# ----------------------------------------------------------------------
# 1. 参数表：三个名字**不在**里面
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name",
    ["put_product_version", "put_offer_version", "put_product_line"],
)
def test_the_registration_surface_takes_no_certifying_parameter(
    settings: Settings, tool_name: str
) -> None:
    """每一条工具的入参集合**恰好**是它自己那一组。

    比的是集合相等而不是「不含某几个」：后者只在有人加回一个**已知**的坏名字时才红，
    而这一条在任何一个新参数出现时都要先被看到一眼 —— 那个参数是不是又一条「让调用方
    替事实说话」的入口，正是要在这里回答的问题。

    三个名字是**刻意缺席**的，而它们各有出处：

    * ``approved_by`` —— 取自传输层已确立的主体，与 ``outreach.authorize_wave`` 的
      ``authorized_by``、``product_loop`` 的 ``by=`` 同一条；
    * ``approved_at`` —— 本机时钟（``utc_now()``）；
    * ``supersedes`` —— 由 ``version`` 推出来（``None`` / ``version - 1``）。

    前两格意味着：一条 ``status="ready"`` 的登记，其核对者**不是调用方写的**。
    第三格意味着：「第 5 版取代第 2 版」这句话进不来 —— 而 ``domain/product.py``
    里没有一条把 ``supersedes`` 绑到 ``version - 1`` 的校验器，所以本层是这条链
    唯一的守卫。
    """
    handlers = product_catalog_handlers(
        settings=settings,
        catalogs=default_product_catalog_store(settings),
    )
    parameters = set(inspect.signature(handlers[tool_name]).parameters)

    expected = {
        "put_product_version": {
            "product_id",
            "version",
            "display_name",
            "owner",
            "status",
            "capabilities",
            "limits",
            "units",
            "region_variants",
            "source_refs",
            "unknowns",
            "note",
        },
        "put_offer_version": {
            "offer_id",
            "version",
            "name",
            "purpose",
            "owner",
            "status",
            "product_refs",
            "markets",
            "locales",
            "approved_claim_refs",
            "price",
            "note",
        },
        # 产品线**没有** ``workspace``：与 Offer 一样取本部署的那一个，不由载荷给
        # （理由见 ``product_catalog.py`` 的模块 docstring）。它也没有 ``version``
        # —— ``line_id`` 自己就是那个唯一版本。
        "put_product_line": {
            "line_id",
            "name",
            "owner",
            "status",
            "customer_problem",
            "capability_family",
            "product_refs",
            "approved_material_refs",
        },
    }[tool_name]

    assert parameters == expected, (
        f"{tool_name} 的参数表变了：多的 {sorted(parameters - expected)}、"
        f"少的 {sorted(expected - parameters)}。"
        "多的那几格里若出现 approved_by / approved_at / supersedes，"
        "就是一条可以由调用方自己签名、自己盖章的记录 —— 而记录上完全看不出来。"
    )


def test_a_payload_cannot_name_a_verifier_even_when_it_asks_to(new_session) -> None:
    """载荷里写的那两句话**没有落进库里** —— 落进去的是推出来的那两句。

    上一条判的是签名，这一条判的是**多给的字段会怎样**。签名里没有那三格时，「多给
    一个」只有两种可能的形状：被拒，或者被丢掉。这里把量出来的那一种写死，因为两种
    在回执之外完全同形，而读代码的人会以为「签名里没有」等于「调用方会被拦下」。

    实测的形状是**丢掉**（工具参数按签名构造，多出来的关键字不进绑定），于是这条判据
    断言的是一句更强的話：载荷说 ``approved_by="operator:member-a"``、``supersedes=1``，
    而库里那一版的核对者是**会话主体**、``supersedes`` 是 **``None``**（它在第 1 版）。
    两句话互相矛盾，库里留下的是推出来的那一句。

    一句话留给调用方：多给的字段不会报错，所以「我填了核对者」这件事只能从回执的
    ``stored`` 上读回来 —— 那里写的是**真的落库的那一份**，不是调用方给的那几个参数。
    """
    outcome = new_session()(
        (
            "put_product_version",
            {
                **_PRODUCT,
                "status": "ready",
                "approved_by": "operator:member-a",
                "approved_at": "2020-01-01T00:00:00+00:00",
                "supersedes": 1,
            },
        )
    )
    stored = _ok(outcome, 0)["stored"]
    assert stored["approved_by"] == CONTROL_PRINCIPAL, (
        "载荷里那个名字没有进库 —— 进库的是会话主体"
    )
    assert stored["approved_by"] != "operator:member-a"
    assert stored["supersedes"] is None, "第 1 版取代不了任何一版，载荷说了也不算"


# ----------------------------------------------------------------------
# 2. 回执
# ----------------------------------------------------------------------
def test_the_receipt_says_what_was_stored_and_what_to_write_next(new_session) -> None:
    """三条工具各登记一次：回执里的三格是真名，而版本号那一格只属于有版本的。"""
    outcome = new_session()(
        ("put_product_version", dict(_MINIMAL["put_product_version"])),
        ("put_offer_version", dict(_MINIMAL["put_offer_version"])),
        ("put_product_line", dict(_MINIMAL["put_product_line"])),
    )

    product = _ok(outcome, 0)
    assert product["registered"] == "产品资料"
    assert product["stored"]["product_id"] == "probe-minimal-product"
    assert product["stored"]["version"] == 1
    assert product["next_version"] == 2, "回执要指出下一版是几 —— 它改不了，只能再写一版"
    assert product["stored"]["status"] == "draft", "没给 status 就是 draft"
    assert product["stored"]["approved_by"] == "", "draft 上没有核对者"
    assert product["stored"]["approved_at"] is None, "draft 上没有核对时刻"
    assert "workspace" not in product["stored"], (
        "产品资料不属于任何一个工作区 —— B01 :104 逐字要求它可以被两个 Scene 共用，"
        "一个归属列会让第二次共用读起来像越界"
    )
    assert product["_note"], "回执要带一句人能读的下一步"

    offer = _ok(outcome, 1)
    assert offer["registered"] == "Offer"
    assert offer["next_version"] == 2
    assert offer["stored"]["offer_id"] == "probe-minimal-offer"

    line = _ok(outcome, 2)
    assert line["registered"] == "产品线"
    assert "next_version" not in line, (
        "产品线没有版本号（零行为分组），所以它没有「下一版」可发 —— "
        "这一格在的话，调用方会去写一个不存在的第 2 版"
    )
    assert line["stored"]["line_id"] == "probe-minimal-line"


def test_an_offer_and_a_product_line_get_the_deployment_workspace(new_session, settings) -> None:
    """两处 ``workspace`` 取本部署的那一个 —— 一次登记写不进别的工作区。"""
    outcome = new_session()(
        ("put_offer_version", dict(_OFFER)),
        ("put_product_line", dict(_LINE)),
    )
    assert _ok(outcome, 0)["stored"]["workspace"] == settings.profile
    assert _ok(outcome, 1)["stored"]["workspace"] == settings.profile


# ----------------------------------------------------------------------
# 3. 幂等与冲突
# ----------------------------------------------------------------------
def test_replaying_the_same_registration_after_a_restart_is_idempotent(new_session) -> None:
    """同一个 ``(id, 版)`` 同一份内容，在**第二次会话**里再写一次仍然成功。

    这是「重启后重放一批登记」那条路：第一次会话把库建在 ``database_path`` 上，
    第二次会话是一个新的处理器表、新的存储工厂、新的令牌 —— 它与第一次之间只共享
    那个库文件。断言的还有一件事：第二次读回来的**还是第一次那一份**（``display_name``
    与 ``next_version`` 都对得上），因为幂等若不成立，这里会读到一个「新写的」第二版。
    """
    first = new_session()(("put_product_version", dict(_PRODUCT)))
    assert _ok(first, 0)["next_version"] == 2

    second = new_session()(("put_product_version", dict(_PRODUCT)))
    again = _ok(second, 0)
    assert again["stored"]["display_name"] == _PRODUCT["display_name"]
    assert again["next_version"] == 2, "库里仍然只有第 1 版"


def test_the_same_version_with_a_different_body_is_refused_and_names_the_subject(
    new_session,
) -> None:
    """同 ``(id, 版)`` 配另一份内容 ⇒ 拒，且拒词点名主语与「不覆盖」。

    点主语不是修辞：这一族的异常类型是 ``StoreError``，而它也承载「这个库打不开」。
    一条不分主语的拒词让两件处置相反的事共用一个名字（去对齐版号 / 去查存储），
    判据正是靠「点名了没有」把两者分开的。
    """
    changed = {**_PRODUCT, "display_name": "改了一个字的同一版"}
    outcome = new_session()(
        ("put_product_version", dict(_PRODUCT)),
        ("put_product_version", changed),
    )
    _ok(outcome, 0)
    text = _refused(outcome, 1)
    assert "probe-product" in text, f"拒词要点名主语，实际是：{text!r}"
    assert "version=1" in text, f"拒词要点名版号，实际是：{text!r}"
    assert "不覆盖" in text, f"拒词要说出处置，实际是：{text!r}"


def test_a_product_line_is_its_own_single_version(new_session) -> None:
    """产品线没有版号：同一个 ``line_id`` 同一份内容幂等、不同内容被拒。

    这一对与产品版本那一对同款，而它值得再写一遍：产品线唯一的那个「版本」就是
    ``line_id`` 本身，所以「改一份分组」在这里**只能**换一个新 id —— 而覆盖会让
    「这一组的成员什么时候变的」从记录里消失，那一刻正是有人要查的东西。
    """
    outcome = new_session()(
        ("put_product_line", dict(_LINE)),
        ("put_product_line", dict(_LINE)),
        ("put_product_line", {**_LINE, "name": "改了名字的同一组"}),
    )
    _ok(outcome, 0)
    _ok(outcome, 1), "同一份内容再写一次是幂等，不是冲突"
    text = _refused(outcome, 2)
    assert "probe-line" in text, f"拒词要点名 line_id，实际是：{text!r}"


# ----------------------------------------------------------------------
# 4. 三格派生：核对者、核对时刻、取代了哪一版
# ----------------------------------------------------------------------
def test_a_ready_version_is_stamped_by_the_session_not_by_the_payload(new_session) -> None:
    """``ready`` 那一版的核对者**是这一次会话的主体**，核对时刻是本机时钟。

    这一条是 b01-b 上最承重的一格：``status="ready"`` 是一个「已核对」的断言，而
    ``ProductVersion._ready_means_verified`` 逐字写着「『已核对』这件事没有核对者，
    就只是一个更自信的 draft」。核对者若能从载荷来，它就在盘上没有第二个来源 ——
    与 ``outreach.authorize_wave`` 那条「一个能由调用方填写的批准人字段，等于一份
    可以自己签名、自己盖章的审批记录」是同一条理由。
    """
    outcome = new_session()(
        (
            "put_product_version",
            {**_PRODUCT, "status": "ready"},
        )
    )
    stored = _ok(outcome, 0)["stored"]
    assert stored["status"] == "ready"
    assert stored["approved_by"] == CONTROL_PRINCIPAL, (
        "核对者取自传输层已确立的主体，不取自载荷"
    )
    assert stored["approved_at"], "核对时刻要真的落在盘上"

    stamped = datetime.fromisoformat(stored["approved_at"])
    assert stamped.tzinfo is not None, "本仓禁止 naive datetime —— 落盘的时刻要带偏移量"


def test_a_draft_cannot_carry_a_verifier_at_all(new_session) -> None:
    """``draft`` 那一版的两格是空的 —— 这不是缺省，是「表达不出来」。

    一份挂在 ``draft`` 上的批准记录是一句假话（它还没被核对过），而它在这里连写法
    都没有：上一条判据钉住了签名里没有那两格。
    """
    outcome = new_session()(("put_product_version", {**_PRODUCT, "status": "draft"}))
    stored = _ok(outcome, 0)["stored"]
    assert stored["approved_by"] == ""
    assert stored["approved_at"] is None


def test_a_ready_version_registered_without_a_subject_is_refused_by_name(
    settings: Settings,
) -> None:
    """没有主体时，``ready`` **登记不进去**，而且拒词说清了它要的是什么。

    ## 这条判据判的是哪一条路

    **不是** MCP 那条路 —— 实测：不带令牌的会话连握手都过不去
    （``drive_with_settings(token=None)`` 拿回 ``Server returned an error response``），
    所以工具面根本到不了这里。这条判据判的是**绕过传输层直接调 handler** 那条路，
    而它是真实存在的：handler 就是组合根里的一只普通函数（``product_catalog_handlers``
    返回 ``dict[str, Handler]``），任何一个不走 MCP 的调用方 —— CLI、后台循环、将来某
    个进程内的 orchestrator —— 都会落到这里，而那时 contextvar 是空的。

    ## 判据为什么是「点名」而不是「不崩」

    去掉 ``_verifier`` 里那个 ``if principal is None`` 分支，直接调会**照样报错**
    （``AttributeError: 'NoneType' object has no attribute 'name'``）—— 一条只断言
    ``pytest.raises`` 的判据在那种实现下全绿，而调用方拿到的是一个崩在现场的异常，
    不是一句「这件事要什么」。所以这里钉住的是那句话本身，以及它点名的两样东西：
    「核对者」那一格与「Bearer 令牌」那个来源。

    ## 同一把尺子的另一半

    紧接着的 ``draft`` 走**完全相同**的直达路径并成功：拒的是 ``ready`` 要记的那个
    核对者，不是「直达调用不成立」。少了这一条，一个把 ``_verifier`` 改成无条件抛的
    实现在这里也是绿的。

    ## 载荷里的其余每一格都是**齐的**

    ``source_refs`` 也照给（``_ARTIFACT``）。少了它，一个把 ``_verifier`` 的守卫拿掉
    的实现会**因为另一条理由**报错（``_ready_means_verified`` 说「ready 必须有核实
    资料」），于是这条判据红着，红的原因却不是它要守的那一条 —— 而真正该发生的事是
    **登记成功、盘上留下一个没有人核对过的 ``ready``**。判据的形状要让它只在那一件事
    上红。
    """
    handlers = product_catalog_handlers(
        settings=settings,
        catalogs=default_product_catalog_store(settings),
    )
    put = handlers["put_product_version"]

    with pytest.raises(ToolError) as caught:
        put(
            product_id="probe-anon-product",
            version=1,
            display_name="匿名的这一版",
            owner="probe-owner",
            status="ready",
            source_refs=[dict(_ARTIFACT)],
        )
    said = str(caught.value)
    assert "核对者" in said, f"拒词要点名它要拿去填的那一格，实际是：{said!r}"
    assert "Bearer" in said, f"拒词要点名它从哪来，实际是：{said!r}"

    # 另一半：没有主体时 `draft` 照登记 —— 它不记核对者，所以不需要主体。
    stored = put(
        product_id="probe-anon-draft",
        version=1,
        display_name="匿名的草稿",
        owner="probe-owner",
    )["stored"]
    assert stored["approved_by"] == ""
    assert stored["approved_at"] is None


def test_supersedes_follows_the_version_number(new_session) -> None:
    """``supersedes`` 由版本号定：第 1 版 ``None``，第 2 版 ``1``。

    第 1 版取代不了任何一版，而 ``supersedes=0`` 那种写法在本仓被明确判过是坏的
    （``SceneVersion`` 那条校验器的 docstring 记着这次修正）。这里断言的是**读数**：
    库里那一格是推出来的，不是谁填的。
    """
    outcome = new_session()(
        ("put_product_version", dict(_PRODUCT)),
        ("put_product_version", {**_PRODUCT, "version": 2, "display_name": "第二版"}),
    )
    assert _ok(outcome, 0)["stored"]["supersedes"] is None
    second = _ok(outcome, 1)["stored"]
    assert second["version"] == 2
    assert second["supersedes"] == 1


# ----------------------------------------------------------------------
# 5. 拒得下来
# ----------------------------------------------------------------------
def test_a_ready_version_without_evidence_is_refused_by_the_domain(new_session) -> None:
    """``ready`` 而 ``source_refs`` 空 ⇒ 拒，且拒词点名那一格。

    拒绝来自 ``ProductVersion`` 自己的校验器（``_ready_means_verified``），不是这一层
    另写一句：同一个字段的两个校验器不会分家。这里断言的是**回执的形状** —— 它摊成
    「哪一个字段、为什么」，而不是一句「构造不出来」。
    """
    outcome = new_session()(
        ("put_product_version", {**_PRODUCT, "status": "ready", "source_refs": []})
    )
    text = _refused(outcome, 0)
    assert "source_refs" in text, f"拒词要点名那一格，实际是：{text!r}"
    assert "ready" in text, f"拒词要说出是哪种状态下的要求，实际是：{text!r}"


def test_a_ready_offer_without_markets_is_refused(new_session) -> None:
    """Offer 的 ``ready`` 缺 ``markets`` ⇒ 拒。

    「空市场读起来是『哪儿都能用』，而那是一次授权，不是一个缺省值」——
    这一格与产品资料那一格不同（``source_refs``），所以各配一条判据。
    """
    outcome = new_session()(
        ("put_offer_version", {**_OFFER, "status": "ready", "markets": []})
    )
    text = _refused(outcome, 0)
    assert "markets" in text, f"拒词要点名那一格，实际是：{text!r}"


def test_a_misspelled_status_names_that_field(new_session) -> None:
    """``status="readdy"`` ⇒ 拒，且拒词点名 ``status``。

    ``readable`` 存在的理由就是这一条：``str(ValidationError)`` 会把输入值原样回显成
    几十行，而调用方要的只是「哪一个字段、为什么」。
    """
    outcome = new_session()(("put_product_version", {**_PRODUCT, "status": "readdy"}))
    text = _refused(outcome, 0)
    assert "status" in text, f"拒词要点名那一格，实际是：{text!r}"


def test_an_offer_version_of_the_same_id_twice_names_the_offer(new_session) -> None:
    """Offer 那一侧的冲突同样点名主语 —— 与产品版本那一对逐字同款。"""
    outcome = new_session()(
        ("put_offer_version", dict(_OFFER)),
        ("put_offer_version", {**_OFFER, "name": "改了一个字的同一版"}),
    )
    _ok(outcome, 0)
    text = _refused(outcome, 1)
    assert "probe-offer" in text, f"拒词要点名主语，实际是：{text!r}"
    assert "version=1" in text, f"拒词要点名版号，实际是：{text!r}"
