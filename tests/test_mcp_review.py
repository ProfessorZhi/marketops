"""§12 收件箱那七条工具走真实 MCP 会话时的行为，以及 §9 的提议 → 批准 → 激活。

## 这一层测的是**接线之后**才存在的那几件事

判据本身各有住处：``tests/test_review_gate.py``（域：原因 → 选项、去重键、恒等式、
``apply_resolution``）、``tests/test_review_store_contract.py``（端口与 sqlite）、
``tests/test_security_catalog.py``（目录与角色）。本文件不重测那些，它测的是把四段
（目录、处理器表、存储、令牌）接起来之后**才会出现**的东西：

* **人从哪里来**：``by`` / ``roles`` / ``at`` / ``workspace`` / ``resolution_id``
  一律不是入参，而是一个**集合相等**的判据 —— 多一个参数与少一个都会红，所以将来
  没人能悄悄加回一个「让调用方替自己签名」的入口。
* **角色矩阵**：三个角色 × 七条工具，每一格都要与 ``TOOL_CATALOG`` 的答案一致，
  而那张表里能审批的那两条**不在**控制者那一格上。
* **没接线**：``review=None`` 时那七条的回执说的是「本实例没接收件箱」，不是
  「收件箱是空的」—— 前者去接线，后者是读数，答成同一句话会让运维去查一份不存在的表。
* **闸门**：一件待办提议出来的那一版**激活不了**，而普通的一版照旧生效（取证结论二：
  要第二个人批准的是**规则变更**，不是「把一个 Scene 立项并激活」）。
* **生产者**：身份冲突那几行真的落成待办，且三种处置（新开 / 已在盘上未决 /
  已经裁决过）各说各的话 —— 已经裁决过的那一件**不被系统自己重开**。

## 为什么每条「能过」的旁边都有一条「过不去」的

只有正常路径的用例分不出「拒绝对了」与「拒绝根本没跑」。所以每一次认领、每一次
裁决都配了一条它自己的拒绝：陈旧版本、不存在的案件、不改变状态的决定、把批准塞进
裁决那条路、同一个人既提议又批准。闸门那一组同理：拦下第 2 版的用例旁边必须有一条
**第 1 版仍然能激活**的 —— 否则「一律要批准」这个错误实现也会全绿。
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.mcp_server.review import review_handlers
from marketops.adapters.mcp_server.tools import _open_refusal_cases
from marketops.application.review import open_for as open_review_case
from marketops.config import Settings
from marketops.domain.review import INTAKE, ReviewReason
from marketops.domain.util import utc_now
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_review_store,
    default_scene_store,
)
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 一次成立的立项。与 ``tests/test_mcp_product_loop.py`` 的 ``BASE_CREATE`` 同形 ——
#: 它是**测试输入**，真实那套住在 ``profiles/`` 里。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: §12 的七条。写死在这里而不是从目录里筛 phase：这一条要钉的正是**这七条**
#: （``tests/test_mcp_product_loop.py`` 从目录筛的是产品环那一半，两处各有各的用途）。
REVIEW_TOOLS: tuple[str, ...] = (
    "list_review_cases",
    "get_review_case",
    "get_review_flow",
    "claim_review_case",
    "resolve_review_case",
    "approve_policy_change",
    "get_scene_impact",
)

#: 每一条工具的入参集合**恰好**是它自己那一组。比集合相等而不是「不含某几个」：
#: 后者只在有人加回一个**已知**的坏名字时才红。
_EXPECTED_PARAMETERS: dict[str, set[str]] = {
    "list_review_cases": {"limit"},
    "get_review_case": {"case_id"},
    "get_review_flow": {"window_start", "window_end"},
    "claim_review_case": {"case_id", "expected_version"},
    "resolve_review_case": {
        "case_id",
        "outcome",
        "answer",
        "expected_version",
        "proposed_version",
    },
    # 批准**不挂版本**：挂起哪一版是那条提议的事。给它一个 proposed_version 会让
    # 「谁挂的」与「谁批的」在载荷上看起来是同一件事。
    "approve_policy_change": {"case_id", "answer", "expected_version"},
    "get_scene_impact": {"scene_id", "from_version", "to_version"},
}

#: 任何一条收件箱工具都**不准**有的入参。前三样是三条不同的判据，写在一起是因为
#: 它们的失败方式相同 —— 都会让一次调用把一句**没有事实基础的话**写进只增不改的历史：
#:
#: * ``by`` —— 当事人。取自传输层的令牌，不取自载荷。一个能传的 by 让调用方写下
#:   别人的名字，而那条记录看起来完全正常。
#: * ``roles`` —— 「这个人当年有没有资格做这件事」的唯一依据。令牌事后不可复现，
#:   由调用方填等于把那条判据变成自述。
#: * ``at`` —— 时间。能倒填的审计记录不是审计记录，而 ``inbox_flow`` 判「最后一个
#:   事件是什么」靠的正是这个先后。
#: * ``workspace`` —— 从**那一件案件**来。能传的话，一次调用就把决定写到另一个
#:   工作区的案件上。
#: * ``resolution_id`` —— 由这一次事件算出来。内容寻址的 id 由调用方指定，等于同一
#:   件事可以开出两条不同的历史。
_FORBIDDEN_PARAMETERS = frozenset(
    {
        "actor",
        "at",
        "by",
        "decided_at",
        "principal",
        "resolution_id",
        "roles",
        "subject",
        "updated_at",
        "version",
        "workspace",
    }
)

#: 三个角色各自应当**看得见**几条。这一格写死是这个文件承重的一处：往目录里新加
#: 一条收件箱工具、或者把 ``approve_policy_change`` 的角色放宽到控制者，都会在这里红。
#:
#: 授权者那一格是 **3**：他也读收件箱（``list_review_cases`` / ``get_review_case``，
#: §9 那句「获授权者批准」的前提是他看得见自己批的是哪一件），再加上只有他有的那一条。
#: 他**没有**认领、裁决、流水账、影响预览 —— 那是控制者做事的四条。
_VISIBLE_COUNTS: dict[Role, int] = {
    Role.CONTROLLER: 6,
    Role.AUTHORIZER: 3,
    Role.RESEARCHER: 0,
}

#: 权限矩阵里那一格**最要紧**的否定：控制者读得到收件箱，但批准不在他手上。
_NOT_THE_CONTROLLER_S = frozenset({"approve_policy_change"})


def _ring(tmp_path: Path, principal: str, roles: list[Role]):
    """种一个令牌并落成环文件，返回 ``(环, 令牌)``。"""
    token, digest = mint(principal, roles)
    path = tmp_path / f"tokens-{principal}-{'-'.join(r.value for r in roles)}.json"
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
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.text}"
    return call.text


class Deployment:
    """一个部署：一份场景存储、一份收件箱存储，以及按主体开工的若干会话。

    **存储只建一次**（组合根就是这么接的），而每个会话拿到的是同一组工厂 ——
    两处各建一份的形态是「闸门看的是 A 库的待办、控制者读的是 B 库的收件箱」，
    而两处各自都正常。

    每个会话都同时接产品环与收件箱：这样两条路径的**唯一**差别就是令牌上的角色，
    而「看不见」因此只可能来自角色。少接一半的会话会把「没接线」与「没权限」压成
    同一个观测。
    """

    def __init__(self, *, settings: Settings, tmp_path: Path) -> None:
        self.settings = settings
        self.tmp_path = tmp_path
        self.scenes = default_scene_store(settings)
        self.review = default_review_store(settings)
        self._sessions: dict[tuple[str, tuple[Role, ...]], Any] = {}

    def session(self, principal: str, roles: list[Role]):
        key = (principal, tuple(sorted(roles, key=lambda role: role.value)))
        if key in self._sessions:
            return self._sessions[key]
        ring, token = _ring(self.tmp_path, principal, roles)
        settings = self.settings
        handlers = product_loop_handlers(
            settings=settings,
            scenes=self.scenes,
            maintenance=default_maintenance_store(settings),
            assets=default_asset_store(settings),
            relations=default_relation_store(settings),
            accounts=default_account_pool_store(settings),
            cohorts=default_account_cohort_store(settings),
            review=self.review,
            acquisition=None,
            router=None,
            documents=None,
        )
        review_tools = review_handlers(
            settings=settings, scenes=self.scenes, review=self.review
        )

        def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
            outcome = drive_with_settings(
                settings=settings,
                tokens=ring,
                token=token,
                product_loop=handlers,
                review=review_tools,
                review_store=self.review,
                scenes=self.scenes,
                calls=calls,
                want_tool_list=want_tool_list,
            )
            assert not outcome.handshake_error, outcome.handshake_error
            return outcome

        self._sessions[key] = run
        return run

    def control(self):
        """控制者：读收件箱、认领、裁决、激活。"""
        return self.session("claude-control", [Role.CONTROLLER])

    def second_controller(self):
        """**另一个人**的控制者。用来测「一件待办被两个人同时领走」。"""
        return self.session("claude-control-2", [Role.CONTROLLER])

    def authorizer(self, principal: str = "claude-authorizer"):
        """获授权者：只有 ``approve_policy_change`` 那一格。"""
        return self.session(principal, [Role.AUTHORIZER])

    def other_controller(self):
        """第三个控制者。用来钉「一件待办上的提议人与批准人各是谁」。"""
        return self.session("claude-control-3", [Role.CONTROLLER])

    def researcher(self):
        return self.session("worker-1", [Role.RESEARCHER])

    def plant(
        self,
        subject_ref: str,
        *,
        reason: ReviewReason = ReviewReason.IDENTITY_CONFLICT,
        at: datetime | None = None,
        affected_refs: tuple[str, ...] | None = None,
    ) -> str:
        """开一件待办，返回它的 ``case_id``。

        走 :func:`~marketops.application.review.open_for` 而不是那几个工具：这个文件
        要测的是**读与写那几条工具**，所以待办由它自己的生产者路径之外的手段摆上去
        —— 用工具造待办会让一条写工具的缺陷同时改掉后面每一条断言的前置。
        """
        return open_review_case(
            store=self.review(),
            workspace=self.settings.profile,
            scene_id=self.settings.profile,
            reason=reason,
            subject_ref=subject_ref,
            affected_refs=(subject_ref,) if affected_refs is None else affected_refs,
            at=at,
        ).case.case_id


@pytest.fixture
def deploy(settings: Settings, tmp_path: Path) -> Deployment:
    return Deployment(settings=settings, tmp_path=tmp_path)


# ----------------------------------------------------------------------
# 入参面：谁替调用方说话
# ----------------------------------------------------------------------
@pytest.mark.parametrize("tool_name", list(REVIEW_TOOLS))
def test_no_review_parameter_can_speak_for_the_caller(
    settings: Settings, tool_name: str
) -> None:
    """每一条工具的入参集合**恰好**是它自己那一组，多一个都没有。"""
    handlers = review_handlers(
        settings=settings,
        scenes=default_scene_store(settings),
        review=default_review_store(settings),
    )
    parameters = set(inspect.signature(handlers[tool_name]).parameters)

    forbidden = parameters & _FORBIDDEN_PARAMETERS
    assert not forbidden, (
        f"{tool_name} 收下了 {sorted(forbidden)} —— 这些名字承载的事实"
        "（谁做的、什么时候、在哪个工作区、哪一条历史）都从传输层、存储或这一次事件"
        "推出来，不由载荷提供"
    )
    assert parameters == _EXPECTED_PARAMETERS[tool_name], (
        f"{tool_name} 的入参面变了。多的那一个要问「它是不是又一条让调用方替事实"
        "说话的入口」，少的那一个要问「调用方现在怎么表达那件事」——"
        f"而这个文件里写下的那一组是 {sorted(_EXPECTED_PARAMETERS[tool_name])}"
    )


def test_no_review_store_means_no_handlers_at_all(settings: Settings) -> None:
    """没有收件箱存储时**一条处理器都不注册**，而不是注册成恒回空表的实现。

    「现在没有待办」与「这个实例没有收件箱」在报告上必须分得开：前者去派人，后者
    去接线。一个恒回 ``[]`` 的实现让两者同形 —— 而这两句话的处置完全不同。
    （这与 ``decisions.py`` 在 ``records=None`` 时的取向不同，那是刻意的：那一边
    **判定本身**还在，只是存不下来；这一边没有存储就**没有待办可读**。）
    """
    assert (
        review_handlers(
            settings=settings,
            scenes=default_scene_store(settings),
            review=None,
        )
        == {}
    )


# ----------------------------------------------------------------------
# 角色矩阵：七条 × 三个角色
# ----------------------------------------------------------------------
@pytest.mark.parametrize("role", sorted(_VISIBLE_COUNTS, key=lambda item: item.value))
def test_the_role_matrix_matches_the_catalog(settings: Settings, tmp_path: Path, role):
    """每一格都要与目录的答案一致，而**看不见的每一条**都要与不存在的工具同形。

    会话是**接全了的**（产品环 + 收件箱都接），所以「看不见」只可能来自角色 ——
    少接一半的会话会把「没接线」与「没权限」压成同一个观测，而这两件事的处置不同。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    run = deploy.session(f"principal-{role.value}", [role])
    outcome = run(want_tool_list=True)

    expected = {name for name in REVIEW_TOOLS if role in TOOL_CATALOG[name].roles}
    observed = expected & set(outcome.tool_names)
    assert observed == expected, (
        f"{role.value} 在 tools/list 上看见的收件箱工具与目录说的不一样。\n"
        f"  目录给它的：{sorted(expected)}\n  会话列出的：{sorted(observed)}"
    )
    assert len(expected) == _VISIBLE_COUNTS[role], (
        f"{role.value} 那一格从 {_VISIBLE_COUNTS[role]} 条变成了 {len(expected)} 条 ——"
        "目录里收件箱这一段变了。要在这里被看见一眼，再决定那一格该不该变"
    )


def test_the_controller_can_read_the_inbox_but_cannot_approve(
    settings: Settings, tmp_path: Path
) -> None:
    """控制者读得到收件箱，而**批准不在他手上** —— 这一格是本片最要紧的否定。

    §9 那句「获授权者批准」的全部价值就是**它不是一个代理能自己走到的位置**。
    把这条工具的角色放宽到控制者，会让「提议 → 批准」变成同一个人点两下 ——
    而两下都成功、报告上也自洽。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    outcome = deploy.control()(want_tool_list=True)

    for name in sorted(_NOT_THE_CONTROLLER_S):
        assert name not in outcome.tool_names, (
            f"{name} 出现在控制者的 tools/list 上了 —— 它的角色被放宽了。"
        )
    # 而读得到的那几条真的在。
    assert "list_review_cases" in outcome.tool_names
    assert "resolve_review_case" in outcome.tool_names


def test_a_researcher_cannot_reach_the_inbox(settings: Settings, tmp_path: Path) -> None:
    """研究身份看不见、也调不动这七条 —— 与一个不存在的工具逐字同形。

    逐条比，而不是只比第一条：只比第一条的实现漏得掉后面几条的措辞分歧。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    outcome = deploy.researcher()(
        *((name, {}) for name in REVIEW_TOOLS),
        ("definitely_not_a_tool", {}),
        want_tool_list=True,
    )

    for name in REVIEW_TOOLS:
        assert name not in outcome.tool_names

    unknown = _refused(outcome, len(REVIEW_TOOLS))
    assert "Unknown tool" in unknown
    for index, name in enumerate(REVIEW_TOOLS):
        text = _refused(outcome, index)
        assert "Unknown tool" in text
        assert text.replace(name, "X") == unknown.replace("definitely_not_a_tool", "X"), (
            f"研究身份调 {name} 时拿到的不是「这个工具不存在」那句话 ——"
            "「不属于你的工具」与「没有这条工具」在人看来必须是同一件事，"
            "否则收件箱的存在与否从拒绝文案里泄出去了"
        )


# ----------------------------------------------------------------------
# 没接线：两句不同的话
# ----------------------------------------------------------------------
def test_not_wired_says_so_rather_than_an_empty_inbox(
    settings: Settings, tmp_path: Path
) -> None:
    """``review=None`` 时那七条的回执说的是「本实例没接收件箱」。

    在这一格里断言的不是「调用失败」而是**它说了哪一句**：一个恒回空列表的实现
    同样会「成功」，而它把「去接线」答成了「现在没有待办」。
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
        cohorts=default_account_cohort_store(settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        product_loop=handlers,
        review=None,
        review_store=None,
        scenes=scenes,
        calls=(("list_review_cases", {}), ("control_scene", {"action": "activate", "version": 1})),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error

    text = _refused(outcome, 0)
    assert "尚未接线" in text or "没有接线" in text, (
        f"没接收件箱时那句话是：{text!r}。它要说的是**接线事实**，"
        "而这与「收件箱里没有待办」是两句不同的话"
    )


def test_the_activation_gate_reports_not_wired_instead_of_clear(
    settings: Settings, tmp_path: Path
) -> None:
    """没有收件箱时，激活闸门报的是 ``not_wired`` —— **不是** ``clear``。

    两者都会让激活通过，所以只看「激活成不成功」的实现分不出它们。而区别是承重的：
    ``not_wired`` 说的是 §9 那道批准在这一格上**没有被问过**（这个部署没有接收件箱），
    ``clear`` 说的是**问过了、判成不拦**。把前者报成后者，读的人会以为这一版被批准过。
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
        cohorts=default_account_cohort_store(settings),
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
            review=None,
            review_store=None,
            scenes=scenes,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    run(("create_scene", BASE_CREATE))
    activated = _ok(run(("control_scene", {"action": "activate", "version": 1})), 0)
    gate = activated["activation_gate"]
    assert gate["decision"] == "not_wired", (
        f"没有收件箱时闸门报的是 {gate['decision']!r} —— 而它必须说清这一格**判不了**"
    )
    assert "没有被问过" in gate["note"], gate["note"]


# ----------------------------------------------------------------------
# 读
# ----------------------------------------------------------------------
def test_listing_the_inbox_counts_and_truncates_visibly(
    settings: Settings, tmp_path: Path
) -> None:
    """顺带截断一起报出来。一份静默变短的收件箱读起来像「就这几件」。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    early = deploy.plant("kent-001", at=utc_now() - timedelta(hours=2))
    late = deploy.plant("kent-002", at=utc_now() - timedelta(hours=1))
    run = deploy.control()

    listed = _ok(run(("list_review_cases", {})))
    assert [row["case_id"] for row in listed["cases"]] == [early, late], (
        "未决的在前、早的在前是端口契约的一部分 —— 而列表是最容易把它丢掉的那一处"
    )
    assert listed["counts"] == {
        "returned": 2,
        "total": 2,
        "outstanding": 2,
        "closed": 0,
    }
    assert listed["truncation"] is None
    # ``evidence_ids`` 只在按 id 读时给：列表要回答的是「现在有哪几件」。
    assert "evidence_ids" not in listed["cases"][0]

    capped = _ok(run(("list_review_cases", {"limit": 1})))
    assert capped["counts"]["returned"] == 1
    assert capped["counts"]["total"] == 2
    assert capped["truncation"]["dropped"] == 1, (
        "截断必须被报出来 —— 它掉下的是最晚开的那一件，而那只在 truncation 里看得见"
    )

    for bad in (0, -3):
        text = _refused(run(("list_review_cases", {"limit": bad})))
        assert "正数" in text, text


def test_an_empty_history_and_a_missing_case_are_two_answers(
    settings: Settings, tmp_path: Path
) -> None:
    """``found: false`` 是**返回值**而不是错误：一件被答复过的问题与一件从来不存在
    的问题必须分得开。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")
    run = deploy.control()

    present = _ok(
        run(
            ("get_review_case", {"case_id": case_id}),
            ("get_review_case", {"case_id": "rc-0000000000000000"}),
        ),
        index=0,
    )
    missing = _ok(run(("get_review_case", {"case_id": "rc-0000000000000000"})), index=0)
    assert missing["found"] is False and missing["case"] is None
    assert "没有 case_id 为" in missing["note"]
    # 这一条真的写在回执里，而不是让读的人自己比两个 id。
    assert present["resolutions"] == []
    assert present["found"] is True

    for bad in ("", "Case-1", "   "):
        text = _refused(run(("get_review_case", {"case_id": bad})))
        assert "case_id" in text, text


def test_the_flow_equation_is_computed_here_not_left_to_the_reader(
    settings: Settings, tmp_path: Path
) -> None:
    """回执里带着**当场算过**的恒等式，而不是一串让人自己加的计数。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    now = utc_now()
    deploy.plant("kent-001", at=now - timedelta(hours=3))
    resolved = deploy.plant("kent-002", at=now - timedelta(hours=3))
    run = deploy.control()
    _ok(run(("resolve_review_case", _answer(resolved, "correct_object", version=1))))

    flow = _ok(
        run(
            (
                "get_review_flow",
                {
                    "window_start": (now - timedelta(hours=4)).isoformat(),
                    "window_end": (now + timedelta(hours=1)).isoformat(),
                },
            )
        )
    )
    assert flow["pending_start"] == 0, "两件都是在窗口**内**开的"
    assert (flow["opened"], flow["resolved"]) == (2, 1)
    assert flow["pending_end"] == 1
    assert flow["reconciles"] is True, flow["equation"]
    # 那条式子是可读的（``0 + 2 + 0 - 1 - 0 - 0 = 1``），而不是一串要读的人自己加的
    # 计数：``reconciles`` 是判词，``equation`` 是判词的依据，两者一起才排得动错。
    assert flow["equation"] == (
        f"{flow['pending_start']} + {flow['opened']} + {flow['reopened']} - "
        f"{flow['resolved']} - {flow['dismissed']} - {flow['merged']} = {flow['pending_end']}"
    ), flow["equation"]

    for bad, field in (
        (
            {
                "window_start": (now + timedelta(hours=1)).isoformat(),
                "window_end": now.isoformat(),
            },
            "倒过来",
        ),
        ({"window_start": now.isoformat().replace("+00:00", "")}, "时区"),
        ({"window_end": "not-a-time"}, "ISO-8601"),
    ):
        text = _refused(run(("get_review_flow", bad)))
        assert field in text, f"{bad} 的拒绝是：{text!r}"


# ----------------------------------------------------------------------
# 写：认领
# ----------------------------------------------------------------------
def test_claiming_is_not_a_decision_and_two_people_cannot_claim_one_case(
    settings: Settings, tmp_path: Path
) -> None:
    """认领改的是**当前状态**，不追加历史；而第二个人领不走同一件。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")

    claimed = _ok(
        deploy.control()(
            ("claim_review_case", {"case_id": case_id, "expected_version": 1})
        )
    )
    case = claimed["case"]
    assert case["state"] == "claimed"
    assert case["claimed_by"] == "claude-control"
    assert case["version"] == 2, "认领要推进乐观锁序号，否则 CAS 挡不住第二次"
    assert case["outstanding"] is True, "认领过的待办仍然是未决的"

    # **认领不是一次决定**：只增不改的那张表上没有落点。
    read = _ok(deploy.control()(("get_review_case", {"case_id": case_id})))
    assert read["resolutions"] == [], (
        "认领在历史里留下了东西 —— 而「谁在做」与「谁做过决定」是两件事"
    )

    # 第二个人领同一件：盘上已经不是第 1 版了。
    text = _refused(
        deploy.second_controller()(
            ("claim_review_case", {"case_id": case_id, "expected_version": 1})
        )
    )
    assert "本次调用读到的是第" in text, text
    # 同一个人拿着新鲜版本再领一次：状态不是 OPEN 了，仍然拒。
    text = _refused(
        deploy.control()(("claim_review_case", {"case_id": case_id, "expected_version": 2}))
    )
    assert case_id in text, text

    for bad in (0, -1):
        text = _refused(
            deploy.control()(
                ("claim_review_case", {"case_id": case_id, "expected_version": bad})
            )
        )
        assert "expected_version" in text, text

    text = _refused(
        deploy.control()(
            (
                "claim_review_case",
                {"case_id": "rc-0000000000000000", "expected_version": 1},
            )
        )
    )
    assert "一个字都没写" in text, text


# ----------------------------------------------------------------------
# 写：裁决
# ----------------------------------------------------------------------
def _answer(case_id: str, outcome: str, *, version: int, **extra: Any) -> dict[str, Any]:
    """一条裁决的载荷。``version`` 是调用方**在自己屏幕上看到的**那一版。"""
    payload: dict[str, Any] = {
        "case_id": case_id,
        "outcome": outcome,
        "answer": "已核对名录",
        "expected_version": version,
    }
    payload.update(extra)
    return payload


def test_a_decision_lands_in_the_history_with_who_and_when(
    settings: Settings, tmp_path: Path
) -> None:
    """一次成功的裁决：状态推进、历史里多一条，且**谁/何时/按哪一版**都在上面。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")
    run = deploy.control()

    done = _ok(run(("resolve_review_case", _answer(case_id, "correct_object", version=1))))
    assert done["case"]["state"] == "resolved"
    assert done["case"]["outstanding"] is False
    assert done["case"]["closed_at"] is not None
    resolution = done["resolution"]
    assert resolution["by"] == "claude-control"
    assert resolution["roles"] == ["controller"], (
        "历史里那一格答的是「这个人当年有没有资格做这件事」—— 令牌事后不可复现，"
        "它是唯一读得到的依据"
    )
    assert resolution["resolution_id"].startswith("rr-")
    assert resolution["at"].endswith("+00:00"), "时刻一律带时区落盘"

    read = _ok(run(("get_review_case", {"case_id": case_id})))
    assert [row["outcome"] for row in read["resolutions"]] == ["correct_object"]

    # 一件已裁决的待办不能再被裁决第二次 —— 换一个关闭结果也不行，而**理由**不是
    # 「这条决定不合法」，是「它已经关了，关掉之后唯一还能做的是重开它」。
    text = _refused(run(("resolve_review_case", _answer(case_id, "dismiss", version=2))))
    assert "现在能做的决定" in text, text
    assert "REOPEN" in text, (
        "拒绝里必须说出**出路**（先重开）—— 关掉的那一件与开着的那一件出路不同。\n" + text
    )

    # 陈旧版本：盘上已经不是你以为的那一版。
    other = deploy.plant("kent-002")
    text = _refused(run(("resolve_review_case", _answer(other, "dismiss", version=99))))
    assert "本次调用读到的是第" in text, text


def test_approval_does_not_travel_through_the_resolution_tool(
    settings: Settings, tmp_path: Path
) -> None:
    """``approve_policy_change`` 塞进裁决那条路时被拒，且拒绝**指向另一条工具**。

    两条路的角色要求不同，而这件事只在目录上看得见。放行等于让控制面做一次授权面的
    动作 —— 而 §9 那句「获授权者批准」的全部价值就是它不是一个代理能自己走到的位置。

    这条判据**必须在控制者的会话里问**，而且这正是「同一个人不能既提议又批准」在
    传输层上的形态：一枚同时持 CONTROLLER 与 AUTHORIZER 的令牌**铸不出来**
    （``refuse_collapsed_gate``），所以唯一的问法是「控制者把手伸到批准那一格会发生
    什么」。他会撞在**裁决工具**上 —— 而拒绝必须告诉他走哪一条路、以及那一条路要他
    是什么角色。只说「你不能这么做」的回执会让人去改代码。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")
    run = deploy.control()

    text = _refused(
        run(("resolve_review_case", _answer(case_id, "approve_policy_change", version=1)))
    )
    assert "approve_policy_change" in text, text
    assert "authorizer" in text, text

    # 而反面那一半：换一条路**也**走不通 —— 授权者连裁决工具都看不见，而看不见的
    # 那一条与不存在的工具有一句话。两半合起来才是「批准不是一个代理能自己走到的位置」。
    authorizer = deploy.authorizer()
    assert "resolve_review_case" not in authorizer(want_tool_list=True).tool_names
    hidden = _refused(authorizer(("resolve_review_case", _answer(case_id, "dismiss", version=1))))
    assert "Unknown tool" in hidden, hidden


def test_an_unknown_outcome_is_refused_rather_than_falling_back(
    settings: Settings, tmp_path: Path
) -> None:
    """自由字符串会被实现静默当成某一条分支 —— 所以未知的一律拒绝，并列出闭集。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")
    run = deploy.control()

    for bad in ("Dismiss", "dismissed", ""):
        text = _refused(run(("resolve_review_case", _answer(case_id, bad, version=1))))
        assert "correct_object" in text, text

    # 空答案不是「没填」—— ``answer`` 是人的那句话本身。
    text = _refused(
        run(("resolve_review_case", _answer(case_id, "dismiss", version=1, answer="")))
    )
    assert "answer" in text, text


def test_a_proposal_must_suspend_a_version_that_exists(
    settings: Settings, tmp_path: Path
) -> None:
    """提议挂在一版不存在的规则上，那一版**永远等不到批准** —— 当场拒。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    case_id = deploy.plant("kent-001")
    run = deploy.control()
    run(("create_scene", BASE_CREATE))
    run(("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}))

    text = _refused(
        run(
            (
                "resolve_review_case",
                _answer(case_id, "propose_policy_change", version=1, proposed_version=99),
            )
        )
    )
    assert "99" in text and "add_scene_version" in text, text

    # 缺版本号也是拒 —— 一条提案不带它挂起的是第几版，是构造不出的一条决定。
    text = _refused(
        run(("resolve_review_case", _answer(case_id, "propose_policy_change", version=1)))
    )
    assert "proposed_version" in text, text

    proposed = _ok(
        run(
            (
                "resolve_review_case",
                _answer(case_id, "propose_policy_change", version=1, proposed_version=2),
            )
        )
    )
    assert proposed["case"]["state"] == "awaiting_approval", (
        "提议**不生效** —— 案件转 awaiting_approval，等另一位获授权者批准"
    )
    assert proposed["case"]["proposed_version"] == 2
    assert proposed["case"]["outstanding"] is True


# ----------------------------------------------------------------------
# 写：批准（两个人）
# ----------------------------------------------------------------------
def test_only_the_authorizer_approves_and_the_proposer_is_reported(
    settings: Settings, tmp_path: Path
) -> None:
    """提议 → 批准：批准人只来自**令牌**，而提议人是报在回执里的那一格。

    这一条只走得到「两个人」那一种形状 —— 那不是省事，是这套东西的**构造**：
    ``security/tokens.py::refuse_collapsed_gate`` 拒绝一枚同时带 controller 与
    authorizer 的令牌，而 ``TokenRing`` 又拒绝同一个主体出现两次。于是「同一个人
    既提议又批准」在**传输层构造不出来**，它的执行器在域与用例层
    （``tests/test_review_gate.py`` 钉 ``may_decide``，
    ``application/review.py`` 的 ``approve_policy_change`` 比那两格的名字）。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    run = deploy.control()
    run(("create_scene", BASE_CREATE))
    run(("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}))

    # 1) 一件**没有**提议过的待办：批准它没有对象。
    bare = deploy.plant("kent-001")
    text = _refused(
        deploy.authorizer()(("approve_policy_change", _answer(bare, "同意", version=1)))
    )
    assert "没有一条等着被批准的提议" in text, text

    # 2) 一件提议过的：**另一位**获授权者批准，成了。
    proposed = deploy.plant("kent-002")
    _ok(
        run(
            (
                "resolve_review_case",
                _answer(proposed, "propose_policy_change", version=1, proposed_version=2),
            )
        )
    )
    approved = _ok(
        deploy.authorizer()(
            ("approve_policy_change", _answer(proposed, "同意", version=2))
        )
    )
    assert approved["case"]["state"] == "resolved"
    assert approved["resolution"]["outcome"] == "approve_policy_change"
    assert approved["resolution"]["by"] == "claude-authorizer"
    assert approved["resolution"]["roles"] == ["authorizer"]
    assert approved["resolution"]["proposed_version"] is None, (
        "批准**不挂版本**：挂起哪一版是那条提议的事（它在只增不改的历史上）"
    )
    # 而提议人是**读得出来**的那一格 —— §9 要的「谁提议的、谁批的」在只增不改的
    # 那张表上分得开。
    read = _ok(run(("get_review_case", {"case_id": proposed})))
    assert [row["by"] for row in read["resolutions"]] == [
        "claude-control",
        "claude-authorizer",
    ]

    # 3) 同一件再批一次：它已经不在等批准了。
    text = _refused(
        deploy.authorizer()(
            ("approve_policy_change", _answer(proposed, "再同意一次", version=3))
        )
    )
    assert proposed in text, text

    # 4) 而第 1 件仍然开着 —— 两次被拒的批准一个字都没写。
    still = _ok(run(("get_review_case", {"case_id": bare})))
    assert still["case"]["state"] == "open"
    assert still["resolutions"] == []


def test_a_collapsed_gate_cannot_even_be_minted() -> None:
    """一枚令牌不能同时握住闸门两半 —— 所以「既提议又批准」在传输层不可表达。

    这一条钉的是那两格判据**能被构造出来**的那个前提：``by`` 取自令牌上的主体，
    而两条令牌上的名字不同是这套东西唯一能提供的「两个人」。把这条规则放宽，等于
    让同一个人用两枚令牌走完提议与批准 —— 而每一步都正常。
    """
    from marketops.domain.errors import ConfigError

    with pytest.raises(ConfigError) as collapsed:
        mint("claude-one", [Role.CONTROLLER, Role.AUTHORIZER])
    assert "同时持有 controller 与 authorizer" in str(collapsed.value)


# ----------------------------------------------------------------------
# 闸门：提议出来的那一版激活不了
# ----------------------------------------------------------------------
def test_the_gate_blocks_the_proposed_version_and_nothing_else(
    settings: Settings, tmp_path: Path
) -> None:
    """§9 的提议 → 批准 → 激活，而**普通的一版照旧直接生效**。

    ## 少了后半句这条用例会骗人

    「一律要批准」的实现同样会让提议的那一版激活不了。所以这里必须有一条**不需要
    批准**的版本：第 1 版（由 ``add_scene_version`` 直接写下）激活成功，且回执里
    闸门那一格说的是 ``clear``（``判过了、不拦``）而不是 ``not_wired``。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    run = deploy.control()
    run(("create_scene", BASE_CREATE))
    run(("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}))

    # 第 1 版：没有待办提议过它 —— 直接激活，报告里是 clear。
    first = _ok(run(("control_scene", {"action": "activate", "version": 1})))
    assert first["activation_gate"]["decision"] == "clear", first["activation_gate"]

    case_id = deploy.plant("kent-001")
    _ok(
        run(
            (
                "resolve_review_case",
                _answer(case_id, "propose_policy_change", version=1, proposed_version=2),
            )
        )
    )

    # 第 2 版：一件待办正等着它被批准 —— 而拒绝里要点名那一件与提议人。
    text = _refused(run(("control_scene", {"action": "activate", "version": 2})))
    assert case_id in text, text
    assert "claude-control" in text, (
        "拒绝里必须点名**提议人**：只报一句「在等批准」会让调用方去问提议人自己，"
        "而 §9 要的是另一个人批"
    )

    # 批准之后同一版就激活得了了 —— 闸门解除的是它自己拦下的那一格。
    _ok(
        deploy.authorizer()(("approve_policy_change", _answer(case_id, "同意", version=2)))
    )
    second = _ok(run(("control_scene", {"action": "activate", "version": 2})))
    assert second["activation_gate"]["decision"] == "clear"


# ----------------------------------------------------------------------
# 影响预览
# ----------------------------------------------------------------------
def test_the_impact_preview_is_a_read_and_refuses_bad_versions(
    settings: Settings, tmp_path: Path
) -> None:
    """预览不写任何东西，也不改变哪一版在生效；而版本号有一个说得出口的范围。"""
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    run = deploy.control()
    run(("create_scene", BASE_CREATE))
    run(("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}))

    preview = _ok(run(("get_scene_impact", {"from_version": 1, "to_version": 2})))
    assert preview["scene_id"] == settings.profile
    assert isinstance(preview["impact"], dict) and preview["impact"]

    state = _ok(run(("get_scene", {})))
    assert state["record"]["latest_version"] == 2, "预览不写任何东西"

    for field, payload in (
        ("from_version", {"from_version": 0, "to_version": 2}),
        ("to_version", {"from_version": 1, "to_version": -1}),
    ):
        text = _refused(run(("get_scene_impact", payload)))
        assert field in text, text

    text = _refused(
        run(("get_scene_impact", {"from_version": 1, "to_version": 2, "scene_id": "other"}))
    )
    assert "uk-acme-natural-grass" in text, (
        "另一个场景要被**拒绝**，且拒绝里点名本部署服务的那个 —— 静默退回默认值会让"
        "调用方以为读的是另一个场景的规则"
    )


# ----------------------------------------------------------------------
# 生产者：身份冲突那几行
# ----------------------------------------------------------------------
def test_a_refused_row_opens_a_case_and_the_second_batch_says_already_pending(
    settings: Settings, tmp_path: Path
) -> None:
    """拒绝发生的那一处开案，第二次同一批说「盘上已有一件未决的」。

    这两句话必须分开：去重是端口契约的一部分，而「又问了一遍」与「新开了一件」在
    报告上的处置不同 —— 前者不必再派人。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    refusals = (("kent-003", "名称那一格是它自己的内部 id"),)

    cases, note = _open_refusal_cases(
        review=deploy.review,
        settings=settings,
        scene_id=settings.profile,
        refusals=refusals,
    )
    assert len(cases) == 1 and cases[0].opened is True
    assert "新开 1 件" in note, note
    case_id = cases[0].case.case_id

    listed = _ok(deploy.control()(("list_review_cases", {})))
    row = listed["cases"][0]
    assert row["case_id"] == case_id
    assert row["reason"] == "identity_conflict"
    assert row["subject_ref"] == "kent-003"
    assert row["affected_refs"] == ["kent-003"], (
        "受影响对象是开案那一刻的**快照**：写空元组在这一格的意思是「不针对任何对象」"
    )
    assert row["state"] == "open"

    again, second_note = _open_refusal_cases(
        review=deploy.review,
        settings=settings,
        scene_id=settings.profile,
        refusals=refusals,
    )
    assert again[0].opened is False
    assert "盘上已有一件**未决**的" in second_note, second_note
    assert len(_ok(deploy.control()(("list_review_cases", {})))["cases"]) == 1, (
        "同一个问题问第二遍不新开案件 —— 判据在去重键上，而它的表现就在这里"
    )


def test_a_closed_case_is_reported_and_not_reopened_by_the_system(
    settings: Settings, tmp_path: Path
) -> None:
    """已经裁决过的那一件**不重开**，而它必须被**报出来**。

    ``ports/review.py`` 逐字：重开是**人**做的，由系统自己重开等于把人的一句「不值得
    处理」在下一个批次里静默作废。所以这一格的行为是「报出来 + 不动它」，而不是
    「当作已经问过了」——一件被吞掉的问题与一件从来不存在的问题在报告里完全同形。
    """
    deploy = Deployment(settings=settings, tmp_path=tmp_path)
    refusals = (("kent-003", "名称那一格是它自己的内部 id"),)
    planted, _ = _open_refusal_cases(
        review=deploy.review,
        settings=settings,
        scene_id=settings.profile,
        refusals=refusals,
    )
    case_id = planted[0].case.case_id
    run = deploy.control()
    _ok(run(("resolve_review_case", _answer(case_id, "dismiss", version=1))))

    again, note = _open_refusal_cases(
        review=deploy.review,
        settings=settings,
        scene_id=settings.profile,
        refusals=refusals,
    )
    assert again[0].opened is False
    assert "已经裁决过" in note, note
    assert "不重开" in note, note

    read = _ok(run(("get_review_case", {"case_id": case_id})))
    assert read["case"]["state"] == "dismissed", "系统自己把它重开了"
    assert [row["outcome"] for row in read["resolutions"]] == ["dismiss"]


def test_a_refusal_without_an_inbox_says_it_has_no_landing_place(
    settings: Settings, tmp_path: Path
) -> None:
    """``review=None`` 时如实报「没有落点」，而不是静默地什么都不做。

    被拒的行处置不变（一个 work item 都没建），但「谁去把名录里那一格补上」这件事
    今天没有落点 —— 而这句话必须出现在回执里，否则下一次读收件箱也读不到它，
    唯一的差别是那次调用多了一句谁也不看的 git 记录。
    """
    cases, note = _open_refusal_cases(
        review=None,
        settings=settings,
        scene_id=settings.profile,
        refusals=(("kent-003", "名称那一格是它自己的内部 id"),),
    )
    assert cases == ()
    assert "落成待办" in note and "没有" in note, note
    assert "review 未接线" in note, note


def test_a_batch_with_no_refusals_writes_nothing_and_says_nothing(
    settings: Settings, tmp_path: Path
) -> None:
    """没有拒绝时既不开案也不加那句话 —— 空串与「开了 0 件」是两句话。"""
    cases, note = _open_refusal_cases(
        review=default_review_store(settings),
        settings=settings,
        scene_id=settings.profile,
        refusals=(),
    )
    assert cases == () and note == ""


# ----------------------------------------------------------------------
# INTAKE 表：每一个原因都要有一个答案
# ----------------------------------------------------------------------
_MODULE_PATH = re.compile(r"marketops/[a-z_/]+\.py")


def test_every_reason_names_exactly_one_way_it_gets_produced(
    settings: Settings, repo_root: Path
) -> None:
    """每一个原因要么点名一个**可 import 且可调用**的生产者，要么写清缺的是哪一段。

    两样都空 = 这一格没有生产者而没人知道；两样都填 = 两句话必有一句已经过期。
    而且那两句话各自都要**可核**：

    * ``producer`` 是 ``模块::符号``，当场 import 回来核一遍 —— 一个被改名或删掉的
      符号留在这张表里，表现是「清单说有人生产，而收件箱永远是空的」。
    * ``no_producer`` 点名本仓已有的模块文件。一句「缺的是某某接线」在模块改名之后
      会指着一个不存在的地方，而读的人会去那里找一个不存在的东西。
    """
    assert set(INTAKE) == set(ReviewReason), (
        "INTAKE 的键与 ReviewReason 对不上 —— 新加一个原因而它开出来的待办长什么样"
        "还没有被想过（``intake_for`` 会抛，而那是运行期才看得见的）"
    )

    for reason, intake in INTAKE.items():
        assert bool(intake.producer) != bool(intake.no_producer), (
            f"{reason.value} 那一格两样都{'有' if intake.producer else '没有'} ——"
            "生产者要么有一个，要么有一句写下来的理由，不能两者都空也不能两者都满"
        )
        if intake.producer:
            module_name, _, symbol = intake.producer.partition("::")
            assert symbol, f"{reason.value} 的 producer 不是 模块::符号 的形状"
            module = importlib.import_module(module_name)
            producer = getattr(module, symbol, None)
            assert callable(producer), (
                f"{reason.value} 的 producer {intake.producer!r} 核不回来 ——"
                "清单上写着有人生产，而那个符号不在"
            )
        else:
            named = _MODULE_PATH.findall(intake.no_producer)
            assert named, (
                f"{reason.value} 的 no_producer 没有点名任何模块文件："
                f"{intake.no_producer!r}"
            )
            for relative in named:
                assert (repo_root / relative).is_file(), (
                    f"{reason.value} 的理由点名的 {relative} 不在本仓 ——"
                    "那句话在模块改名之后指着不存在的地方"
                )
