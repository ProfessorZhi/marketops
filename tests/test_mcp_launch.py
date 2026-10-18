"""启动预览走真实 MCP 会话时的行为（b01-d，B01 ``:104`` 第 5 项 / ``:102``）。

## 这一层测的是「接线之后它还对不对」

判据本身已经存在，本文件不重测它：

* ``tests/test_launch_preview.py`` —— 三张清单、``next_action`` 的五档与优先级、
  ``cited`` 不折平、``at`` 是承重的（同一份盘读两个时刻两个答案），全部在应用层直测；
* ``tests/test_product_readiness.py`` —— 被引的产品版本与方案那三张清单（这一层**逐字
  嵌着** b01-c 的读数）；
* ``tests/test_launch_preview_is_offline.py`` —— 应用层够不到网络（AST 量具 + 签名）；
* ``tests/test_mcp_launch_is_offline.py`` —— **接线面**够不到网络（import 闭包，含
  ``launch.py`` 自己）。

本文件测的是**只有把接线接起来才会出现**的那几件事：

* **参数表**：``preview_launch`` 收哪几格，以及 ``at`` / ``workspace`` **不在**里面。
* **跨两面**：一个 Scene 经 ``create_scene``（b01-a/b 的**登记面**）写下，经
  ``preview_launch`` 读回来。这一组是这一片接线的理由：两处各建一份场景存储的表现是
  「登记说立项了、预览说这个场景还没立项过」—— 两个回执各自都正常，分家只在把两面接在
  同一份状态上时才看得见。
* **时刻**：读数里那一格来自**服务端时钟**（带时区、就在此刻附近）。
* **两个拒绝**：场景没立项过、点名的版本不在盘上 —— 各自是**拒绝**，不是一张三张全空的
  读数（一张空表与「什么都不缺」在报告上同形）。
* **工作区**：记录写着别的工作区时拒。写侧写不出这样的行（``_create_scene`` 逐字
  ``workspace=settings.profile``），所以它只能经存储层种下去 —— 而那种库今天确实存在。
* **存储层的失败**：一条可读的结果，不是一次崩溃。

## 这一份不判的

* **零外部调用**：那是构造，判在 ``tests/test_mcp_launch_is_offline.py``（import 闭包）。
  这一份只判行为，两件事坏起来是不同的形状 —— 一层够不到网络却什么都答错，与一层答得
  都对却顺手调了一次检索，在报告上都只显示「预览成功」。
* **``next_action`` 的五档**：应用层逐档判过（``tests/test_launch_preview.py``）。这里只
  用它当**读数的一部分**来断言跨两面（「下一步是哪一件事」正是 PSM ``:122`` 要 preview
  的八项之一），不在这里重排优先级。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.launch import launch_handlers
from marketops.adapters.mcp_server.product_catalog import product_catalog_handlers
from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.scene import SceneBudgetLine, SceneRecord, SceneState, SceneVersion
from marketops.domain.util import utc_now
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_outreach_store,
    default_product_catalog_store,
    default_relation_store,
    default_scene_store,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

CONTROL_PRINCIPAL = "claude-control"

#: 一次成立的立项（照 ``tests/test_mcp_product_loop.py`` 的 ``BASE_CREATE``，逐字同一份）。
#: **不是业务事实** —— 区域、产品、来源计划都是测试输入，真实的那一套住在 ``profiles/``。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: 别的工作区。它**不是**本部署的 profile —— 那个差就是下面那条守卫要判的东西。
FOREIGN_WORKSPACE = "another-workspace"

#: 这一条工具收的六格，**恰好**。
#:
#: 比的是集合相等而不是「不含某几个」：后者只在有人加回一个**已知**的坏名字时才红，而
#: 这一条在任何一个新参数出现时都要先被看到一眼 —— 那个参数是不是又一条「让调用方替此刻
#: 说话」或「让调用方挑工作区」的入口，正是要在这里回答的问题
#: （``test_mcp_product_readiness.py`` 的 ``_EXPECTED_PARAMETERS`` 那一段逐字同源）。
_EXPECTED_PARAMETERS: frozenset[str] = frozenset(
    {
        "scene_id",
        "version",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    }
)

#: 这一族**不准**有的入参。它们的失败方式相同 —— 都会让一次读数把调用方那边的东西
#: 当成盘上的事实：
#:
#: * ``at`` / ``now`` / ``as_of`` —— 三张清单的每一行都拿同一个时刻判（被引的说法到这一刻
#:   过期了没有、被引的产品版本这一刻是不是 ``retired``）。一个能倒填的时刻会让**同一条
#:   盘上事实**在两个调用方那里得到相反的两份读数，而两边各自都正常。
#: * ``workspace`` / ``profile`` —— 启动预览读的是**本部署**的场景；能挑工作区的读接口
#:   会让一次跨工作区的读在回执上完全看不出来。
#: * ``usable`` / ``status`` / ``next_action`` —— 三样都是这一层**算出来**的读数，由调用方
#:   填就是让报告自己说自己的结论。
_FORBIDDEN_PARAMETERS: frozenset[str] = frozenset(
    {
        "at",
        "now",
        "as_of",
        "workspace",
        "profile",
        "usable",
        "status",
        "next_action",
    }
)


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

    **``scenes`` 只建一次**并交给两处（产品环那几条与启动预览），与组合根 ``_compose``
    里同款 —— 两处各建一份的话，「刚立项」与「这个场景还没立项过」可能来自两个不同的库。
    这一份与 ``tests/test_mcp_product_readiness.py`` 的 ``session`` 同款，多接的一面是
    **启动面**：那一条读的正是登记面写下去的那一版。

    产品目录那两份工厂也**只建一次**并交给两面（登记面与启动面）—— 同一条理由：「登记面
    写进了哪个库」与「启动面从哪个库读」是两个答案时，两边各自都正常。
    """

    def new_session():
        ring_path, token = _ring(tmp_path)
        ring = TokenRing.from_file(ring_path)
        scenes = default_scene_store(settings)
        catalogs = default_product_catalog_store(settings)
        claims = default_outreach_store(settings)
        handlers = {
            **product_loop_handlers(
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
            ),
            **product_catalog_handlers(settings=settings, catalogs=catalogs),
        }

        def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
            outcome = drive_with_settings(
                settings=settings,
                tokens=ring,
                token=token,
                product_loop=handlers,
                scenes=scenes,
                launch=launch_handlers(
                    settings=settings, scenes=scenes, catalogs=catalogs, claims=claims
                ),
                calls=calls,
                want_tool_list=want_tool_list,
            )
            assert not outcome.handshake_error, outcome.handshake_error
            return outcome

        return run

    return new_session


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


def _seed_foreign_record(settings: Settings, *, scene_id: str, workspace: str) -> None:
    """经**存储层**种一版记录 + 一版声明，工作区写成别人的。

    写侧写不出这样的行：``_create_scene`` 逐字 ``workspace=settings.profile``（与
    ``require_scene`` 同一个来源）。所以它只能这样种下去 —— 而它对应的那份库今天确实
    存在：这份库文件被**另一个部署**写过（``MARKETOPS_PROFILE`` 换过，或者库从另一个
    环境拷了过来，而两边用的是同一个 Scene 名）。库那一侧只在**写**的时候绑工作区
    （``sqlite/db.py::bind_workspace``，由第一行写入建立），读路径不查，所以这条守卫
    只能在这一层。

    ## ``scene_id`` 必须传**本部署那一个**，否则这一条根本走不到守卫

    这一行是实测出来的，不是推的。先写成的版本用的是 ``scene_id="foreign-scene"``，
    拿回来的却是一句**别的话**：``require_scene`` 挡在所有存储访问之前，它要求
    ``scene_id`` 等于 ``settings.profile``，于是「不是本部署的场景」在第一格就被拒，
    ``_require_record`` 里那条工作区守卫一次都不会执行。**两条拒绝各自挡住一个方向**：
    ``require_scene`` 挡的是「调用方替**哪一个市场**说话」，工作区守卫挡的是「库里这
    一行是不是**本部署**写的」。后者只在**场景名相同**时才够得到 —— 而那正是它存在的
    理由（同一个场景名，两个工作区）。

    两行都种（记录 + 声明）而不是只种记录：守卫读的是**记录**那一行，所以只种记录照旧
    会让这一条绿 —— 而那样一来，将来把守卫挪到「先读声明再判它的工作区」时，这一条会
    以「这一版不在盘上」的形状红，红的原因却与本判据无关。
    """
    store = default_scene_store(settings)()
    store.put_version(
        SceneVersion(
            scene_id=scene_id,
            workspace=workspace,
            version=1,
            region=("GB-England-Devon",),
            scenario="sports-surface-maintenance",
            product="acme-natural-grass",
            admissions=("sports-club",),
            exclusions=("golf-course",),
            role_policy="role-policy-v1",
            source_plan=("mother-pool-import",),
            budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
            refresh=RefreshPlan.uncalibrated_default(),
            outreach=OutreachPolicyDecl.uncalibrated_default(),
            profile_id=workspace,
            profile_version="2026-09-01",
            created_at=utc_now(),
            supersedes=None,
        )
    )
    store.put_record(
        SceneRecord(
            scene_id=scene_id,
            workspace=workspace,
            state=SceneState.ACTIVE,
            latest_version=1,
            effective_version=1,
            created_at=utc_now(),
            updated_at=utc_now(),
        ),
        expected_revision=None,
    )


# ----------------------------------------------------------------------
# 1. 参数表：``at`` 与 ``workspace`` 都不在
# ----------------------------------------------------------------------
def test_the_launch_surface_takes_no_instant_and_no_workspace(settings: Settings) -> None:
    """这一条工具的入参集合**恰好**是它自己那一组，两个入口一个都没有。

    ``at`` 是这一片最要紧的缺席之一：三张清单的每一行都拿同一个时刻判，而一个由调用方
    挑的时刻会让同一条盘上事实在两个调用方那里得到相反的两份读数 —— 两边各自都正常，
    没有一处会响。``workspace`` 是另一条：启动预览读的是**本部署**的场景，一个能挑工作区
    的读接口会让一次跨工作区的读在回执上完全看不出来。

    与 ``test_mcp_product_readiness.py`` 的同名判据逐字同源，**工具名与期望集合不同** ——
    两条各自钉自己那一族，因为「这一族收哪几格」是一次读接口最要紧的一句话。
    """
    handler = launch_handlers(
        settings=settings,
        scenes=default_scene_store(settings),
        catalogs=default_product_catalog_store(settings),
        claims=default_outreach_store(settings),
    )["preview_launch"]
    parameters = set(inspect.signature(handler).parameters)

    forbidden = parameters & _FORBIDDEN_PARAMETERS
    assert not forbidden, (
        f"preview_launch 收下了 {sorted(forbidden)} —— 这些名字承载的事实"
        "（这一刻是什么时候、本部署是哪个工作区、这一版可不可用）都不由载荷提供："
        "时刻取自服务端时钟，工作区取自 Settings，而那三样读数是这一层算出来的"
    )
    assert parameters == _EXPECTED_PARAMETERS


def test_the_surface_is_exactly_one_read_only_tool(settings: Settings) -> None:
    """这一族**只有一条**工具，且它不在目录里那几条之外。

    两件事在这里被看见：

    * 启动面再多一条写工具，「零外部调用」这句话的主语就不再是一个读数面 —— 而
      ``tests/test_mcp_launch_is_offline.py`` 那个闭包会照旧全绿（它判的是**能力**，
      不是这一族**做了几件事**）；
    * 名字取 ``preview_launch`` 而不是切片行里写的 ``launch_preview``，是为了与
      ``preview_run`` / ``submit_run`` 那一对同形：目录里读工具一律动词前置，于是
      ``tools/list`` 上「预览一次启动」与「预览一轮 Run」挨着出现，而它们的区别
      （一个预览**受理**、一个预览**开跑**）在名字上就看得见。
    """
    handlers = launch_handlers(
        settings=settings,
        scenes=default_scene_store(settings),
        catalogs=default_product_catalog_store(settings),
        claims=default_outreach_store(settings),
    )
    assert set(handlers) == {"preview_launch"}


# ----------------------------------------------------------------------
# 2. 跨两面：登记面写下的那一版，启动面读得回来
# ----------------------------------------------------------------------
def test_a_scene_registered_through_the_write_face_is_previewable(
    session, settings: Settings
) -> None:
    """一个 Scene 经**登记面**写下，**启动面**读回来 —— 读的是同一份盘上状态。

    这一条是这一片接线的理由。两处各建一份场景存储的表现是「登记说立项了、预览说这个
    场景还没立项过」—— 两个回执各自都正常，分家只在把两面接在同一份状态上时才看得见。

    读数**不是干净的**，而那不是这一条的缺陷，是它的内容 —— 它同时报出两张清单，
    而那两张恰好把它们各自那一条纪律各说了一遍：

    * ``missing`` 里是 ``product_id``：**一次要启动的预览必须点名它要引的是哪一个
      获批产品版本**。这一版声明的 ``product`` 是一个裸名字（``SceneVersion.product``
      是 ``str``，域层只要求非空），而 ``:104`` 逐字说共享的是**版本**。入参可选是
      「先看看这一版声明长什么样」那种用法要的，不是「可以一直空着」。
    * ``awaiting_approval`` 里是 ``outreach``：触达节奏的六格是**缺省值**，
      标定是一个经验主张，没有任何入参能把它置真
      （``test_mcp_product_loop.py::test_given_outreach_numbers_stay_uncalibrated``
      判死了这件事）。

    于是 ``next_action`` 停在 ``supply_missing`` 而不是 ``await_approval``：三张清单之间
    的优先级是 ``invalid`` ⇒ ``missing`` ⇒ ``awaiting_approval``（还缺东西的声明就算
    今天签了字也不能跑，所以「等签字」不是一个更近的终点）。一个「什么都不缺」的读数
    在这里反而会是一条**假消息**：它在说这一版可以开跑了。

    记录那一侧同时报着**第三件**事：``state='draft'``、``effective_version=None``、
    ``accepts_new_work=False``，记录自己那句理由是「draft: 尚未批准生效，不派发」。
    三张清单排在记录状态**之前**，所以这里读到的仍是 ``supply_missing`` —— 这一点值得
    判出来：``create_scene`` 的缺省留下了**两处**未完成（内容缺一格、记录未受理），
    而报告只说一句下一步。把「记录状态」排在三张清单之后不是分层好看：内容上的缺口是
    调用方（或他的上游）要去动的，记录状态是人去按一个动作（``control_scene``）。
    """
    outcome = session()(
        ("create_scene", dict(BASE_CREATE)),
        ("preview_launch", {"scene_id": settings.profile}),
    )
    created = _ok(outcome, 0)
    reading = _ok(outcome, 1)

    #: 登记面写下的那个场景名就是本部署的 profile（``require_scene`` 拒绝别的取值），
    #: 所以下面这一句判的是「两面说的是同一个场景」，不是一句同义反复。
    assert created["scene"]["scene_id"] == settings.profile
    assert reading["subject"] == f"launch:{settings.profile}@1"
    assert reading["scene_id"] == settings.profile
    #: 记录那一侧四格一起判，而**它们四个说的不是同一件事**：第 1 版就是
    #: ``latest_version``，而 ``effective_version`` 是 ``None`` —— 登记时没有
    #: ``activate``，所以这一版还没被受理。这正是 ``:102`` 那句「有批准才持久受理」
    #: 在读数上的形状，也是 ``is_effective`` 与 ``accepts_new_work`` 分成两格的用处。
    assert reading["version"] == 1
    assert reading["latest_version"] == 1
    assert reading["effective_version"] is None
    assert reading["is_effective"] is False
    assert reading["state"] == "draft"
    assert reading["accepts_new_work"] is False
    #: 记录**自己**那句「为什么现在不能派发」，逐字带着（由 domain 算）。
    assert reading["dispatch_refusal"] == "draft: 尚未批准生效，不派发"
    assert reading["invalid"] == []
    #: 两张清单各报一条，**顺序就是优先级**（上面 :attr:`next_action` 那三行的第二种
    #: 写法）。写成「只判 awaiting_approval 非空」会让「这一版还缺产品版本」这件事在
    #: 这一条判据里看不见 —— 而它才是那一次 ``next_action`` 的答案。
    assert _fields(reading, "missing") == ["product_id"]
    assert _fields(reading, "awaiting_approval") == ["outreach"]
    assert reading["next_action"] == "supply_missing"
    #: 两条点名的是**这一版声明上的一格**，主语都是这一次启动 —— 「下一步去哪一格补」
    #: 是这张表存在的全部理由。
    subject = f"launch:{settings.profile}@1"
    assert reading["missing"][0]["subject"] == subject
    assert reading["awaiting_approval"][0]["subject"] == subject


# ----------------------------------------------------------------------
# 3. 时刻来自服务端时钟
# ----------------------------------------------------------------------
def test_the_reading_carries_the_server_clock_not_a_payload_instant(session, settings) -> None:
    """读数里那一格时刻来自**服务端时钟**：带时区、且就在此刻附近。

    这是「``at`` 不是参数」那一条的另一半。只判参数表的话，一个把时刻硬写成常量的实现
    会照旧全绿 —— 而它的表现是**所有**被引用的说法永远「不过期」，报告上读起来像资料
    很干净。承重那一条（同一份盘读两个时刻两个答案）在应用层
    （``tests/test_launch_preview.py``），这里判的是**这一格是从哪来的**。
    """
    before = utc_now()
    outcome = session()(
        ("create_scene", dict(BASE_CREATE)),
        ("preview_launch", {"scene_id": settings.profile}),
    )
    reading = _ok(outcome, 1)
    at = datetime.fromisoformat(reading["at"])

    assert at.tzinfo is not None, reading["at"]
    assert at.utcoffset() == timezone.utc.utcoffset(at)
    assert 0 <= (at - before).total_seconds() < 60, (before.isoformat(), reading["at"])


# ----------------------------------------------------------------------
# 4. 两个拒绝：没立项过 / 点名的版本不在盘上
# ----------------------------------------------------------------------
def test_a_scene_this_deployment_does_not_serve_is_refused(session, settings: Settings) -> None:
    """不是本部署那个 Scene 的名字 ⇒ **拒绝**，且那句话报出本部署是哪一个。

    这一条挡在最前面（``require_scene``），早于任何一次存储访问。理由不是省一次读：
    「一个部署只服务一个 Scene」是证据、预算与凭据都挂在它身上这件事的另一种说法
    —— 静默退回默认值会让调用方以为他读的是另一个场景的规则，而两份读数在回执上
    同形。

    与上面那条「参数表」是一件事的两面：参数表挡的是「调用方替此刻 / 工作区说话」，
    这一条挡的是「调用方替**哪一个市场**说话」。
    """
    text = _refused(session()(("preview_launch", {"scene_id": "ghost-scene"})), 0)

    assert "ghost-scene" in text
    assert settings.profile in text, f"拒绝里要写着本部署服务的是哪一个：{text!r}"
    assert "MARKETOPS_PROFILE" in text, text


def test_the_deployments_own_scene_is_refused_before_it_was_ever_registered(session) -> None:
    """本部署的 Scene，而库里还没有它的记录 ⇒ **拒绝**，且那句话点名下一步。

    上面那一条挡在 ``require_scene``，这一条走得更远：场景名对、存储也打得开，
    只是**这一版声明还不存在**。一张三张全空的读数与「什么都不缺」同形，所以一个
    还没立项的市场会拿到一份「可以开跑」的报告 —— 两份读数在回执上也确实同形，
    区别只在三张清单空不空，而空正是这里要挡的那种答案。

    ``scene_id`` 这里**省略**（不是传 profile）：缺省取本部署的 Scene，而那正是
    「一个新部署的第一次启动预览」的形状 —— 仓库里没有第二处会说这句话。
    """
    text = _refused(session()(("preview_launch", {})))

    assert "还没有立项过" in text, text
    assert "create_scene" in text, "拒绝必须给出下一步，否则调用方只能猜"


def test_a_version_that_is_not_on_disk_is_refused_and_names_what_is(session, settings) -> None:
    """点名的版本不在盘上 ⇒ **拒绝**，且那句话里同时写着盘上现在有什么。

    「记录说有的那一版读不出来」与「你点名的版本号写错了」是两句不同的话（前者是存储
    不自洽，严重得多），所以这一条拒绝把两个读数都写出来。这里种下去的正是后者：记录说
    ``latest_version=1``，而调用方点名第 2 版 —— 那句话里因此必须出现 ``latest_version=1``,
    否则读的人分不出是哪一种。
    """
    outcome = session()(
        ("create_scene", dict(BASE_CREATE)),
        ("preview_launch", {"scene_id": settings.profile, "version": 2}),
    )
    text = _refused(outcome, 1)

    assert "第 2 版" in text
    assert "latest_version=1" in text, text
    assert "add_scene_version" in text, "拒绝必须给出下一步"


# ----------------------------------------------------------------------
# 5. 工作区：记录写着别人的工作区时拒
# ----------------------------------------------------------------------
def test_a_scene_record_of_another_workspace_is_refused_and_both_workspaces_are_named(
    session, settings: Settings
) -> None:
    """别的工作区那一行记录：拒绝，且点名两个工作区。

    跨工作区的读不留痕：它在这一侧看起来只是一次成功的查询，而被读到的那一份此后会被
    当成本地事实读回去（这一版声明的区域、场景、产品、预算与角色策略，都不是本部署
    核对过的东西）。

    这一行**只能**经存储层种下去（``_seed_foreign_record`` 的 docstring 写了理由）——
    而它对应的那份库今天确实存在，这正是这条守卫要挡的形状。

    ``scene_id`` 传的是**本部署那一个**（不是另起一个名字）：另起一个名字的话，
    ``require_scene`` 会先一步拒掉，这条守卫根本不会执行 —— 详见那个种子的 docstring。
    两句话都要判出来（别的工作区名 + 本部署的工作区名）：只判前者的话，一句「读到的是
    别人的那一份」而**不说读的是谁的**也照样绿，而那句话正是叫操作员去看库的那一半。
    """
    _seed_foreign_record(settings, scene_id=settings.profile, workspace=FOREIGN_WORKSPACE)
    text = _refused(session()(("preview_launch", {"scene_id": settings.profile})), 0)

    assert FOREIGN_WORKSPACE in text, text
    assert settings.profile in text, text
    assert "是别人的那一份" in text, text


# ----------------------------------------------------------------------
# 6. 存储层的失败是一条结果，不是一次崩溃
# ----------------------------------------------------------------------
def test_a_store_that_cannot_be_opened_comes_back_as_a_result(session, settings: Settings) -> None:
    """库的 schema 版本对不上：一条可读的结果，不是一次崩溃。

    制造它的先例在 ``tests/test_budget_ledger.py``（把 ``schema_meta`` 的版本改成 999）。
    存储层那句话已经比这一层能补的更具体，所以这里不重写 —— 只保证它**回得到工具结果
    上**。不接的形态是调用方读到一句光秃秃的 ``Error executing tool preview_launch``，
    存储层那句话整个丢掉，所以判据判的是**存储层自己那句话在不在**。

    这一条同时判到一件事：``StoreError`` 会从**工厂构造处**抛（``SqliteSceneStore`` 的
    ``__init__`` 里跑 ``ensure_schema``），所以 ``scenes()`` 的调用也必须在那个 try 里面
    —— 少了它，这一条会以「工具崩了」的形状红。
    """
    from marketops.adapters.sqlite.db import connect

    default_scene_store(settings)()  # 先把库建起来
    with connect(settings.database_path) as conn:
        conn.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")

    text = _refused(session()(("preview_launch", {"scene_id": settings.profile})), 0)
    assert "状态库 schema 版本是 999" in text, text
    assert "不做自动迁移" in text, f"存储层那句话没有回到结果上：{text!r}"
