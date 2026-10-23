"""§G2 的纵向验收：**把控制客户端拔掉，那批活照样被做完**。

## 这一条证的是 §G2 的哪几句话

> 断开控制客户端**之后**下一批自动执行；重启控制器后重放不重复计数；上一代遗留的
> 在途件被收回并真的被重跑；暂停 → 恢复；模拟到期；受控来源变化；三种时间分开
> （外部抓取 / 盘上新增 / 取回回执）；停机与「没有新数据」可区分。

这不是又一条 B 系列单元判据。§B20–B22 各自证过「某一条链在单次 tick 里做对了」，
而这里证的是把它们串起来的那个**性质**：这一批活的所有权在事件落账时就交了出去
（``domain/continuation.py`` 的 ``intents_for_settlement`` / ``intents_for_resolution``
与那一次写在同一个事务里），所以**此后没有任何第三方调用**也能把它做完。

## 量具（**必须写明**）

* **没有一次真实网络调用。** 抓取器是 :class:`tests.fakes.FakeHttp`，由 :func:`rig`
  在**组合根装配之前**换掉。它算的指纹是落地文件经 ``contact_routes._document`` 解析
  出来的正文摘要，所以「同一份正文再取一次」必须逐字节重放（见 :func:`_seed_the_pages`）。
* **没有一次真实的外部调用。** 派发那一步走本机任务板（``adapters/local_board.py``，
  一张写在 ``tmp_path`` 下的真 SQLite）—— 它是一次真的提交，但不是一次网络调用。
* **时钟是受控的，而且它走在真时钟前面**（:data:`SHIFT`）。理由要说准：MCP 面上没有
  ``now`` 注入口（``create_scene`` / ``submit_research`` 取的是 ``utc_now()``），所以
  那些工具落下行的 ``due_at`` 是**真**时刻；要让控制器（读的是被换掉的 ``utc_now``）
  看得见它们到期，那个读数就必须在真时刻之后。否则这一份文件里每一条都会红在
  「一行都没到期」上，而它长得像链路坏了。
* **控制器那一侧要的是「装配过配置」的 ``Settings``**（:attr:`Rig.composed`）：
  ``default_dispatcher`` 要从 ``dispatch_root`` 造出执行器，而
  ``run_once_cli`` 自己会 ``ensure_data_dirs()``。:func:`rig` 因此把
  :class:`tests.test_r1_vertical_acceptance.Deployment` 内部那份 ``replace(...)``
  重写了一遍 —— 这是一处**刻意的重复**，它由两条断言兜着：那两份配置指的必须是
  同一个令牌环与同一个 执行器 根。指错了的形态是「控制器推的是另一个库」，而它在每一
  份回执上都显示成「一行都没有到期」。
* **「受控过期」不由手改 ``observed_at`` 造**，而是走一次真的业务动作
  （:func:`_tighten_cadence`：加一版 Scene、把两个页面面的复核节奏压到 1 天、激活）。
  那几页材料是 3 天前取的，于是同一份事实在新规则下**真的**过期 —— 判据于是同时
  盖上「到期」与「真的重取」两半，而不是拿一条被改过的行当输入。

## 十条关闭条件各自落在哪一条上

| # | 关闭条件 | 判据 |
|---|---|---|
| 1 | 断开客户端之后下一批自动执行 | :func:`test_the_batch_runs_with_the_client_already_gone` |
| 2 | 重启之后重放不重复计数 | :func:`test_a_restart_does_not_double_count_and_still_drives_new_work` |
| 3 | 上一代遗留的在途件被收回**并真的被重跑** | :func:`test_a_row_left_claimed_by_the_previous_generation_is_reclaimed_and_rerun` |
| 4 | 暂停 → 恢复 | :func:`test_a_paused_scene_is_named_in_the_receipt_and_resuming_drives_the_same_rows` |
| 5 | 模拟到期 | :func:`test_expiry_is_a_pure_function_of_now` + :func:`test_tightening_the_cadence_makes_the_same_pages_really_fetched` |
| 6 | 受控来源变化 | :func:`test_a_controlled_source_change_lands_beside_an_unchanged_page_in_one_tick` |
| 7 | 裁决 → 受影响对象重评 | :func:`test_a_resolution_owes_one_recheck_on_the_case_own_subject_snapshot` |
| 8 | 草稿版本更新 | :func:`test_a_new_writing_policy_makes_a_second_draft_version_that_supersedes_the_first` |
| 9 | 三种时间分开 | :func:`test_three_kinds_of_time_are_three_different_accounts` |
| 10 | 停机与无新数据可区分 | :func:`test_a_missing_wire_a_dead_store_and_an_empty_queue_read_differently` |

第 4 条同时是「**场景停着**」与「盘上没有到期的活」分开的那一格（关闭条件 10 的
第五行），它落在那里而不是第 10 条里，因为要造出「停着而**有**到期的活」必须先有一条
链欠下活来 —— 那正是第 4 条在造的东西。

## 这一份文件**不**证什么

* **没有一次付费的 live run。** 检索与抓取都是夹具；本机没有授权预算，那一格在报告里
  记 ``BLOCKED``，不拿假后端冒充。
* **长期稳定性没被观察过。** 这里跑的是分钟量级的受控时钟与几次独立装配，不是几周的
  常驻进程：「一轮一轮跑下去不会漂」这句话这一份文件说不出。
* **真实公网来源不在射程内。** ``check_target`` 拒 loopback 与 ``.example``，而这里的
  主机就是 ``*.example`` —— 走得通恰恰因为**抓取层被整个换掉了**，所以这一份文件对
  「安全边界会不会拦住一个真实站点」零信息。

## 这一趟顺带修好的一处断链

``interfaces/controller.py::_run_payload`` 原先**丢掉**了 ``ContinuationRun`` 的
``not_ticking`` 与 ``truncation`` 两格 —— 而那个类自己的 docstring 逐字说这两笔是
「没有发生的账」，且截断那一笔「尤其不能省」。后果落在关闭条件 4 与 10 上：**一个被
暂停的场景**与**一个空队列**在这一份回执上曾经逐字相同，而 §G2 要的正是这两件事分
得开。修的是转发（两行），判据是本文件第 4 条的那两格，另有一条只判形状的兄弟在
``tests/test_resident_controller.py``。
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.continuation import continuation_handlers
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.maintenance import skip_reason
from marketops.application.refresh import (
    CENSUS_BACKED_ASPECTS,
    REFRESHABLE_ASPECTS,
    _page_targets,
)
from marketops.config import Settings
from marketops.domain.continuation import (
    ContinuationKind,
    ContinuationState,
    continuation_id,
    retry_delay,
)
from marketops.domain.maintenance import (
    Aspect,
    DueReason,
    Observation,
    RefreshPlan,
    what_is_due,
)
from marketops.domain.util import sha256_text, utc_now
from marketops.interfaces import composition, controller
from marketops.interfaces.controller import run_once_cli
from tests import test_r1_vertical_acceptance as r1
from tests.fakes import FakeHttp

#: 控制器认的那个 holder。关闭条件 3 要种一行「我领的、我这一代」—— 前两格判据
#: （``holder`` / ``epoch``）在那一趟里与下一代**逐字相同**（``registry.release``
#: 是删行，于是下一代 ``acquire`` 又是 ``epoch 1``），所以那一条测的只能是**第三格**
#: （``updated_at`` 早于接手的时刻）。
HOLDER = "claude-g2-controller"

#: 受控时钟走在真时钟前面多少。**必须为正**，理由见模块 docstring 的量具那一节。
#: 它同时要盖过 ``BLOCKED_BACKOFF``（60 秒）之下的一切真实等待：链上那些工具落下的
#: ``due_at`` 是真时刻，控制器看得见它们到期靠的就是这个正移位。
SHIFT = timedelta(minutes=5)

#: 两个页面面被压到几天 —— 比那几页材料的年龄（3 天）短，于是同一份事实真的过期。
TIGHTENED_DAYS = 1

#: 只压这两个面：``source_coverage`` 那条 180 天的规则留着，于是收紧之后认领的格子
#: **只**来自这两面，判据才数得清。
_PAGE_ASPECTS = (Aspect.CONTACT_PERSON_ROLE, Aspect.EMAIL_EVIDENCE)

#: 本部署承诺维护的复核面个数，以及其中**页面面**的个数。两个都从真代码读：
#: 写死「3 面」或「2 个页面面」会让这一份文件在某一次加面之后静默地只测一部分
#: —— 判据照旧全绿，而它已经不再覆盖那一轮了。
_REFRESH_FACES = len(REFRESHABLE_ASPECTS)
_PAGE_FACES = len(REFRESHABLE_ASPECTS) - len(CENSUS_BACKED_ASPECTS)

assert _PAGE_FACES == len(_PAGE_ASPECTS), (
    "「页面面」在真代码里数与这里收紧的那两个对不上 —— 先看清是哪一面变了"
)


# ----------------------------------------------------------------------
# 装置
# ----------------------------------------------------------------------
@dataclass
class Clock:
    """一个**可以拨**的时钟。``run_once_cli`` 一轮里取它三次（acquire / tick / survey）。"""

    at: datetime

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> None:
        self.at = self.at + timedelta(**kwargs)


@dataclass
class Rig:
    """一个真部署 + 一个假抓取器 + 一个受控时钟 + 一份**装配过**的配置。

    ``composed`` 与 ``deployment`` 是同一份部署的两面：前者给控制器那个进程
    （``run_once_cli`` 自己装配），后者给服务面（``Deployment`` 内部装配）。
    """

    deployment: r1.Deployment
    composed: Settings
    fake: FakeHttp
    clock: Clock

    def ok(self, *calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        """走服务面，**按错误通道先判**。失败带原文抛出来。"""
        return r1._ok(self.deployment.run(*calls))


@pytest.fixture
def rig(settings: Settings, tmp_path: Path, monkeypatch) -> Rig:
    """**先换工厂，再装配组合根。**

    顺序不是风格问题：``_compose`` 在 ``Deployment.__init__`` 里就把工厂的返回值取走了
    （``acquisition`` 是 ``_run_maintenance`` 的一个**参数**，不是它调用时现取的），
    于是「装配之后才换」得到的是一个假夹具配一份真抓取器 —— 而那在报告上只显示
    「测试跑得比较慢」。

    两个绑定都要换，因为它们不是同一个名字被解析了两次：

    * ``composition.default_acquisition`` —— ``_compose`` 在服务面那一侧取它；
    * ``controller.default_acquisition`` —— ``controller`` 模块级的 import，
      ``LiveContinuationExecutor.maintenance_tick`` 在控制器那一侧取它。

    只换一处，关闭条件 1（控制器自动那一趟）与 5/6/9（服务面那一趟）就各自走一条真的
    HTTP 路 —— 而那一头的失败长得像「这一格够不着」，是本仓最贵的那类假绿。
    """
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"
    fake = FakeHttp({}, root=tmp_path / "fetched")
    real_acquisition = composition.default_acquisition

    def with_fake(settings_: Settings, **_kwargs: Any):
        # ``allow_browser=False``：浏览器那一侧在 CI 上装不出来，而这一条链要的是
        # 「取回一页」这件事，不是「用哪一种取回」。
        return real_acquisition(settings_, http=fake, allow_browser=False)

    monkeypatch.setattr(composition, "default_acquisition", with_fake)
    monkeypatch.setattr(controller, "default_acquisition", with_fake)
    monkeypatch.setattr(controller, "utc_now", Clock(utc_now() + SHIFT))

    deployment = r1.Deployment(settings, tmp_path)

    # 控制器那一份配置。**这一处重复是刻意的**（见模块 docstring 的量具那一条），
    # 而它由下面两条断言兜着 —— 那两条挡的正是「控制器推的是另一个库」。
    composed = replace(
        settings,
        mcp_tokens_path=tmp_path / "tokens.json",
        outbound_from_address=r1.SENDBOX,
        dispatch_executor="local",
        dispatch_root=tmp_path / "board",
    )
    assert composed.mcp_tokens_path.is_file(), "两份配置指的不是同一个令牌环"
    assert composed.dispatch_root_resolved == (tmp_path / "board"), (
        "两份配置指的不是同一张任务板 —— 控制器那一侧写的是另一张，而它会安静地"
        "报 BLOCKED(缺接线)"
    )
    return Rig(deployment=deployment, composed=composed, fake=fake, clock=_clock(monkeypatch))


def _clock(monkeypatch) -> Clock:
    """:func:`rig` 装进去的那个钟。**从补丁里读回来，不另造一个。**

    另造一个的失败是静默的：``run_once_cli`` 读的是被换掉的那一个，而测试拨的是自己
    手里那一个 —— 于是「拨快 120 秒」这件事在报告上显示为「那一行还是没有到期」。
    """
    return controller.utc_now


# ----------------------------------------------------------------------
# 读数：一律**从工具进、从盘上出**
# ----------------------------------------------------------------------
def _chain(rig: Rig, *, second_route: str = "settled") -> dict[str, Any]:
    """r1 那条链（全程走 MCP）。**不重写它** —— 重写一遍就是两份夹具各说各话。"""
    return r1._run_chain(rig.deployment, rig.composed, second_route=second_route)


def _jobs(rig: Rig) -> SqliteJobStore:
    return SqliteJobStore(rig.composed.database_path)


def _rows(rig: Rig) -> dict[str, Any]:
    """这个场景上**全部**状态的续跑意图，按 id。终态行也在里面 —— 关闭条件 2 要看的
    正是「做完过」这一位在重启之后还在不在。"""
    return {
        row.continuation_id: row
        for row in _jobs(rig).continuations(scene_ids=(r1.PROFILE,), states=None)
    }


def _row(rig: Rig, continuation: str):
    return _jobs(rig).continuation(continuation)


def _claims(rig: Rig) -> tuple[Any, ...]:
    """维护账上的回执（认领过就算），最老的一条在前。"""
    return composition.default_maintenance_store(rig.composed)().claims(r1.PROFILE)


def _evidence(rig: Rig, account_id: str) -> list[Any]:
    return SqliteEvidenceStore(rig.composed.database_path).list_evidence(account_id)


def _tick(rig: Rig, *, limit: int | None = None) -> dict[str, Any]:
    """**一次独立的 ``run_once_cli``**，返回它写给 ``out`` 的那一份回执。

    「独立」是关闭条件 2 的全部内容：每一次调用都从头 ``_build`` 一遍（新的存储、
    新的注册表、新的执行器），而 ``registry.release`` 是**删行** —— 所以第二次拿到的
    ``epoch`` 又是 1，与上一次逐字相同。**那正是这一格要面对的形态**：一个逐字相同的
    新进程面对一份已经做过的账。
    """
    out = io.StringIO()
    code = run_once_cli(settings=rig.composed, holder=HOLDER, limit=limit, out=out)
    assert code == 0, f"这一轮没跑成（退 {code}）：{out.getvalue()}"
    return json.loads(out.getvalue())


#: 状态页在 stderr 上一行行写出去，而**一轮一份**。同一行测试里跑了两轮时那一股
#: 文本里就有两份文档，``json.loads`` 会整段失败。所以这里按「列首的 ``{``」切，
#: 取**最后**一份 —— 每一份都是 ``json.dumps(indent=2)`` 写的，顶层对象的 ``{``
#: 因此恒在第 0 列，而对象内部的那些都在缩进之后。
_DOC_START = re.compile(r"(?m)^\{$")


def _status_page(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """``run_once_cli`` 写在 **stderr** 上那一页（``_emit(..., stream=sys.stderr)``）。

    它是这一份文件里唯一读 stderr 的地方，而这一页必须被读：它答的是「**为什么**此刻
    没有更多工作在跑」，而 ``--once`` 的输出分两页（回执给机器、状态页给人）。只看
    前者会把「场景停着」与「盘上没有到期的活」读成同一件事。
    """
    text = capsys.readouterr().err
    assert text.strip(), "这一轮没有写状态页 —— 而它答的正是「为什么没有更多工作在跑」"
    decoder = json.JSONDecoder()
    pages = [decoder.raw_decode(text, match.start())[0] for match in _DOC_START.finditer(text)]
    assert pages, f"stderr 上读不出结构化的一页：{text!r}"
    page = pages[-1]
    assert set(page) == {"at", "workspace", "state", "inputs"}, (
        f"stderr 上最后那一份不是状态页 —— 而另外那几份可能来自别的写入方：{page!r}"
    )
    return page


# ----------------------------------------------------------------------
# 输入的生产者（受控的那两样）
# ----------------------------------------------------------------------
def _seed_the_pages(
    rig: Rig, *, accounts: tuple[str, ...], mutate: tuple[str, ...] = ()
) -> dict[str, str]:
    """把这一趟会被重取的那几页预置进抓取器，返回 ``{url: 正文}``。

    ## 要哪几页是**从盘上算出来的**，不是列出来的

    页面候选来自 ``application/refresh.py::_page_targets`` —— 复核那一侧用的就是它。
    这里调**同一个函数**读同一个库，于是「预置了哪几页」与「复核会去取哪几页」不是
    两份清单，而是同一个判定的两次求值。列一张写死的清单做过一次，后果是把
    **发现那一轮**取回的站根那一页漏在外面 —— 而漏一页的形态不是「少了一个数」：
    ``FakeHttp.fetch`` 先记下这一次调用、再去取正文，于是它变成
    ``KeyError: 'https://…/'``，整格判成够不着，报告上看起来像**这一格坏了**。

    ## 正文一律走**同一个产地**，并在这里核一次指纹

    ``_file_evidence`` / ``_seed_discovery_evidence`` 落下的行带着
    ``sha256_text(正文)`` 算出的 ``content_sha256``。复核那一侧取回来的字节要**同一个
    函数**再算一次才比得上 —— 所以这里取 :func:`r1._home_page` /
    :func:`r1._contact_page` / :func:`r1._unbound_page` 这三个**同一个产地**的函数，
    不在本文件里再抄一份 HTML。「同一份正文」是**字节**意义上的同一份。

    下面那条指纹断言是这一格的执行器：盘上说这一页的摘要是 X，而这里摆进去的字节
    必须真的算出 X。摆错一页（或摆一份「意思差不多」的正文）在**第一次收紧节奏之前**
    完全看不出来 —— 那一趟全是复用、一个请求都不发，而它看起来正是「这条判据绿了」。

    ``mutate`` 里点到的 URL 会被追加一段注释 —— 那是一处**真实的字节差异**，不带任何
    别的含义（HTML 注释不改变解析结果），而不是「换一份看起来不一样的正文」。
    """
    store = SqliteEvidenceStore(rig.composed.database_path)
    bodies: dict[str, str] = {}
    for account_id in accounts:
        domain = r1._domain_of(rig.composed, account_id)
        prefix = f"https://{domain}"
        producers = {
            "/": r1._home_page,
            "/contact": r1._contact_page,
            "/about": r1._unbound_page,
        }
        for target in _page_targets(store.list_evidence(account_id)):
            producer = producers.get(target.url[len(prefix) :])
            assert producer is not None, (
                f"盘上有一页 {target.url!r} 而这里没有它的正文产地 —— 补一个生产者，"
                "或者这一页本来就不该是复核目标"
            )
            text = producer(domain)
            assert sha256_text(text) == target.expected, (
                f"{target.url!r} 上摆的正文算不出盘上那个指纹：这一页在这一趟里会被"
                "读成「内容变了」，而它长得像来源变化"
            )
            bodies[target.url] = text
    for url, text in bodies.items():
        if url in mutate:
            text = f"{text}<!-- G2：这一趟里这一页真的变了（{url}） -->\n"
        rig.fake.bodies[url] = text.encode("utf-8")
    return bodies


def _tighten_cadence(rig: Rig) -> int:
    """把两个页面面的复核节奏压到 :data:`TIGHTENED_DAYS` 天并激活那一版。**走工具。**

    这是关闭条件 5 第二条腿的载体，而它是一次**业务动作**（第二版 Scene 上写着新的
    复核规则），不是一次对库的改写：改一行 ``observed_at`` 也能让格子到期，但那样测的
    是「夹具能不能改数据」，而这里要测的是「规则变了之后同一份事实真的过期了」。
    """
    added = rig.ok(
        (
            "add_scene_version",
            {
                "refresh_days": {aspect.value: TIGHTENED_DAYS for aspect in _PAGE_ASPECTS},
                "note": "G2：受控地把两个页面面的复核节奏压短",
            },
        )
    )
    version = added["version"]["version"]
    activated = rig.ok(("control_scene", {"action": "activate", "version": version}))
    assert activated["scene"]["effective_version"] == version, (
        f"新那一版没有生效 —— 后面每一轮读的都是旧的复核计划：{activated['scene']!r}"
    )
    return version


# ----------------------------------------------------------------------
# 一、断开控制客户端之后，那批活自己做完
# ----------------------------------------------------------------------
def test_the_batch_runs_with_the_client_already_gone(rig: Rig) -> None:
    """关闭条件 1。**链走完之后不再有任何一次工具调用。**

    这一条的全部内容是时序，而时序在这里是**结构性**的：``_chain`` 之后只有 ``_tick``，
    而 ``_tick`` 进的是 ``run_once_cli`` —— 它自己 ``_build``、自己 ``acquire``、自己
    驱动，中间不经过 MCP。所以「后面那批活是自动执行的」不是一句断言，是这一条测试
    没有别的入口这一件事。

    三格一起判，缺一格这一条就可以绿在一个空洞上：

    * **所有权在事件落账时就交出去了** —— 链的最后一步之后，盘上已经站着那几件
      ``PENDING`` 的活（受理欠一次派发、每次结算各欠一次复核）；
    * **它们真的被驱动** —— 盘上的每一件都到 ``DONE``，且每一条的理由说得出来做了什么；
    * **驱动它们的东西不是这一层** —— 复核那一趟在自己那一侧留下回执（维护账上按格
      的行），所以「done 里有一行」不只是状态机的自转。
    """
    chain = _chain(rig)
    owed = _rows(rig)
    assert owed, "这条链一件活都没欠下 —— 那这一条测的是「空跑」而不是「自动续跑」"
    assert {row.state for row in owed.values()} == {ContinuationState.PENDING}
    by_kind = {
        kind: sum(1 for row in owed.values() if row.kind is kind) for kind in ContinuationKind
    }
    assert by_kind[ContinuationKind.DISPATCH_BATCH] == 1, (
        f"派发那一件该是**一件**（整个作业一次），不是每个成员一件：{by_kind!r}"
    )
    assert by_kind[ContinuationKind.MAINTENANCE_TICK] == len(chain["accounts"]), (
        f"两个成员各自结算过一次，各欠一次复核：{by_kind!r}"
    )

    # ——— 从这里起，客户端已经走了。————
    _seed_the_pages(rig, accounts=chain["accounts"])
    payload = _tick(rig)

    assert payload["touched"] == len(owed), f"这一轮该把每一件都做掉：{payload!r}"
    assert payload["failed"] == [] and payload["retrying"] == [], payload
    assert payload["blocked"] == [] and payload["superseded"] == [], payload
    assert payload["state"] == "complete", (
        f"全部做完之后该是「做完过」那一格，而不是「从来没记过」：{payload!r}"
    )
    assert {row["continuation_id"] for row in payload["done"]} == set(owed)

    # 每一件都说得出来做了什么。派发那一件报的是**件数**，复核那一件报的是**格数**。
    reasons = {row["kind"]: row["reason"] for row in payload["done"]}
    assert "派发" in reasons[ContinuationKind.DISPATCH_BATCH.value], reasons
    assert "复核" in reasons[ContinuationKind.MAINTENANCE_TICK.value], reasons

    after = _rows(rig)
    assert {row.state for row in after.values()} == {ContinuationState.DONE}
    assert {row.attempts for row in after.values()} == {0}, (
        "一次做成的活不该吃 attempts（那是重试计数）"
    )

    # 复核那一趟真的在维护账上留了痕 —— 这是「不是状态机自转」的那一格。
    claims = _claims(rig)
    assert claims, "自动那一趟复核走完了，维护账上却一条回执都没有"
    assert {row.state.value for row in claims} == {"done"}, (
        f"回执该全是做成的：{[row.state.value for row in claims]!r}"
    )


# ----------------------------------------------------------------------
# 二、重启之后重放不重复计数
# ----------------------------------------------------------------------
def test_a_restart_does_not_double_count_and_still_drives_new_work(rig: Rig) -> None:
    """关闭条件 2。两轮**各自独立装配**，第二轮既不重复派发、又不重复花钱。

    第二轮不是「再跑一次看看」：它是一次真的新一代 —— ``_build`` 从头来过，
    ``registry.acquire`` 又是 ``epoch 1``（上一次 ``release`` 是删行）。所以这一格挡的
    不是「同一代跑两遍」，而是**一个逐字相同的新进程面对一份已经做过的账**。

    四格，缺一格都会让这一条绿在一个别的东西上：

    * **不重复领**：终态的行不在 ``_CLAIMABLE``（只有 ``PENDING`` / ``BLOCKED``）里，
      所以第二轮连领都不领它 —— 是「根本没碰」，不是「碰了但幂等」。``not_claimed``
      必须为空：它指的是「别人正在做」，出现在这里意味着终态行被送进了可领集合。
    * **盘上不动**：续跑行的**每一个字段**逐字相同（连 ``updated_at`` 在内）。只比状态
      会漏掉「有人把它改了但状态没变」。
    * **不重复花钱**：抓取次数与维护账上的回执数都不变 —— 一次重复认领在那一侧就是
      一条新行，而在报告上只显示「这一轮核了一格」。
    * **重启之后**新发生的事**还得能做**：这一条真的做一件事（把第二个成员那条还开着
      的活结算掉），然后第三轮把它做掉 —— 于是「重放不重复」不会与「重启之后什么都不
      做了」混成同一件事。

    ## 为什么先去收紧一次节奏

    那条「不重复花钱」的判据要一个**非空的**基线：这条链摆下的每一页都是几天前取回的，
    在部署自己声明的窗口里全部**复用**，于是一个字的节奏都不改时第一轮也一次都不会出去
    取 —— 那么第二个数判的是「0 不变」，是个空判。收紧到 :data:`TIGHTENED_DAYS` 天之后
    同一批页面对新窗口过期，第一轮真的花掉五笔抓取额度，第二轮的「一笔都没多」才算数。
    这一下走的是**业务动作**（第二版 Scene 上写着新的复核规则），不是对库的改写 ——
    改一行 ``observed_at`` 也能让格子到期，但那测的是夹具能不能改数据。
    """
    chain = _chain(rig, second_route="unjudged")
    _tighten_cadence(rig)
    _seed_the_pages(rig, accounts=chain["accounts"])

    owed = _rows(rig)
    first = _tick(rig)
    assert {row["continuation_id"] for row in first["done"]} == set(owed), (
        f"第一轮该把盘上那几件都做掉：{first!r}"
    )
    assert first["failed"] == [] and first["blocked"] == [], first

    before_rows = dict(_rows(rig))
    before_claims = len(_claims(rig))
    before_calls = len(rig.fake.calls)
    before_evidence = {a: len(_evidence(rig, a)) for a in chain["accounts"]}
    assert before_calls > 0, (
        "这一趟一页都没取 —— 那「不重复花钱」这一格就是空判"
    )

    second = _tick(rig)

    assert second["touched"] == 0, f"第二轮一件事都不该做：{second!r}"
    assert second["done"] == [] and second["failed"] == [] and second["blocked"] == []
    assert second["not_claimed"] == [], (
        "「没拿到」说的是别人正在做 —— 这里盘上全是做完的行，它出现在这里意味着有人"
        f"把终态的行送进了可领集合：{second['not_claimed']!r}"
    )
    assert second["state"] == "complete", (
        "「做完过」这一位在重启之后必须还在 —— 它读的是终态的行，而不是内存里的东西："
        f"{second!r}"
    )
    assert _rows(rig) == before_rows, "第二轮改了盘上的行"
    assert len(_claims(rig)) == before_claims, "第二轮在维护账上又认领了一遍"
    assert len(rig.fake.calls) == before_calls, f"第二轮又出去取了页：{rig.fake.calls!r}"
    assert {a: len(_evidence(rig, a)) for a in chain["accounts"]} == before_evidence, (
        "第二轮落了新证据"
    )

    # ——— 重启之后**新发生的事**照常被驱动 —————
    second_domain = r1._domain_of(rig.composed, chain["second"])
    settled = r1._settle(
        rig.deployment,
        rig.composed,
        account_id=chain["second"],
        work_item_id=chain["item_id"],
        pages=((r1._contact_page(second_domain), f"https://{second_domain}/contact"),),
    )["routes"]
    assert settled["error"] is None and settled["routes"] == 1, f"这一次结算没成：{settled}"

    third = _tick(rig)
    assert third["done"], f"新欠下的那一件没有被驱动：{third!r}"
    assert {row["kind"] for row in third["done"]} == {
        ContinuationKind.MAINTENANCE_TICK.value
    }, f"这一轮该只欠复核（派发那一件上一轮就做完了）：{third['done']!r}"
    assert len(_claims(rig)) > before_claims, "复核那一趟没有在维护账上留痕"


# ----------------------------------------------------------------------
# 三、上一代遗留的在途件
# ----------------------------------------------------------------------
def test_a_row_left_claimed_by_the_previous_generation_is_reclaimed_and_rerun(
    rig: Rig,
) -> None:
    """关闭条件 3。收回 → **放回队列**（不是这一轮跑掉）→ 下一轮真的跑掉。

    ## 为什么这一行必须由**这一代自己**种出来

    收回的判据是三格任意一格不对就算「不是我的」（``holder`` / ``epoch`` /
    ``updated_at`` 早于我接手的时刻）。前两格在这一趟里**逐字相同** —— ``release``
    是删行，于是下一代 ``acquire`` 拿到的 ``epoch`` 又是 1，``holder`` 也是同一个。
    只有第三格分得开它们。所以这里种的那一行是：``holder`` 是我、``epoch`` 是 1、而
    ``updated_at`` 落在链上那个工具写下它的时刻（真时刻，比这一代接手的时刻早
    :data:`SHIFT`）—— 也就是**那条唯一能生效的判据**，而不是一条随便哪一格都对不上
    的行（那种行在第一格上就被收走了，于是第三格从来没被走到过）。

    ## 「收回不是重跑」判在哪

    收回那一轮里这一行**不在** ``done`` 里，而盘上它的 ``due_at`` 是
    ``now + retry_delay(0)``（60 秒后），``attempts`` 还是 0 —— 被挡住不算一次尝试。
    下一轮（时钟拨过那个退避）它才真的被驱动，而那时 ``reclaimed`` 是空的。
    """
    chain = _chain(rig)
    _seed_the_pages(rig, accounts=chain["accounts"])
    # 先只做掉一件：本轮要用的那一行得留在 PENDING 上。上限为 1 时驱动哪一件由存储的
    # 次序（``due_at`` 升序、同刻按 id）决定，所以这里不假设是哪一件。
    opened = _tick(rig, limit=1)
    assert len(opened["done"]) == 1, f"上限是 1，却做了不止一件：{opened!r}"

    left = [row for row in _rows(rig).values() if row.state is ContinuationState.PENDING]
    assert len(left) == len(chain["accounts"]), (
        f"上一轮之后该还剩那几件没做：{[(r.kind.value, r.state.value) for r in left]!r}"
    )
    # 挑一件**复核**：它被驱动时会真的在维护账上写回执，于是「真的被重跑」看得见。
    victims = sorted(
        (row for row in left if row.kind is ContinuationKind.MAINTENANCE_TICK),
        key=lambda row: row.continuation_id,
    )
    assert victims, f"剩下来的几件里没有复核：{[r.kind.value for r in left]!r}"
    victim = victims[0]

    jobs = _jobs(rig)
    claimed = jobs.claim_continuation(
        victim.continuation_id, holder=HOLDER, epoch=1, now=victim.due_at
    )
    assert claimed.won, f"这一行该是能领的（PENDING 且已到期）：{claimed.continuation!r}"
    # 原地写一遍：这一代**领了、然后在那一刻之后没有再动过**。``now`` 取它自己的
    # ``due_at`` 而不是一个倒推出来的时刻 —— 往回写时间不是一个真实可达的状态，而它
    # 会把这个夹具变成一行生产路径产不出来的数据。
    jobs.finish_continuation(
        victim.continuation_id,
        state=ContinuationState.CLAIMED,
        holder=HOLDER,
        epoch=1,
        now=victim.due_at,
        checkpoint="上一代走到这里，进程没了",
    )
    seeded = _row(rig, victim.continuation_id)
    assert seeded is not None and seeded.state is ContinuationState.CLAIMED
    assert seeded.updated_at < rig.clock.at, (
        "这一行的 updated_at 没有早于这一代接手的时刻 —— 那样第三格判据就生效不了，"
        f"而这一条会绿在「收回恰好没发生」上：{seeded!r}"
    )

    assert victim.subject_ref, "这一条复核意图没有主语 —— 下面那两句就判不到点上"
    before_claims = _claims(rig)
    reclaimed = _tick(rig)

    assert victim.continuation_id in reclaimed["reclaimed"], f"它没有被收回：{reclaimed!r}"
    assert victim.continuation_id not in {row["continuation_id"] for row in reclaimed["done"]}, (
        f"收回**不是**重跑 —— 它只是把那一行放回队列，而这一轮把它做掉了：{reclaimed!r}"
    )
    assert reclaimed["done"], f"另外那几件还欠着的活该被正常做掉：{reclaimed!r}"
    back = _row(rig, victim.continuation_id)
    assert back is not None and back.state is ContinuationState.PENDING
    assert back.due_at == rig.clock.at + retry_delay(0), (
        "收回之后该按重试退避排下一次 —— 立刻重跑会在一次崩溃之后制造一次尖峰："
        f"{back!r}"
    )
    assert back.attempts == 0, "收回不是一次尝试"

    # 「收回不是重跑」在维护账上的形态：**那个主体**这一轮没有新的回执。判总数是错的
    # —— 同一个批次里另一个主体的复核本来就该照常跑，它一定会让总数变大，于是这条
    # 断言会把一件正确的事读成违规（而它在总数恰好不变的那些运行里看起来是绿的）。
    def _for_victim(claims):
        return [claim for claim in claims if claim.subject_id == victim.subject_ref]

    assert _for_victim(_claims(rig)) == _for_victim(before_claims), (
        "这一轮把收回的那一行也跑了 —— 它该只是被放回队列"
    )

    # ——— 下一轮：它真的被驱动 —————
    rig.clock.advance(seconds=120)
    driven = _tick(rig)
    assert driven["reclaimed"] == [], f"收回过一次的行不该被再收一次：{driven!r}"
    assert victim.continuation_id in {row["continuation_id"] for row in driven["done"]}, (
        f"放回队列的那一行没有被真的重跑：{driven!r}"
    )
    landed = _row(rig, victim.continuation_id)
    assert landed is not None and landed.state is ContinuationState.DONE
    assert len(_for_victim(_claims(rig))) > len(_for_victim(before_claims)), (
        "它跑完了，维护账上却没有新回执"
    )


# ----------------------------------------------------------------------
# 四、暂停 → 恢复（同时是「场景停着」与「没有到期的活」分开的那一格）
# ----------------------------------------------------------------------
def test_a_paused_scene_is_named_in_the_receipt_and_resuming_drives_the_same_rows(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """关闭条件 4。暂停期间**一件都不碰**，而回执必须说得出是哪一件、为什么。

    ## 两句话要分开，因为它们指的是两件事

    * **状态页**说的是「此刻为什么没有更多工作在跑」。场景级的暂停让那些行**进不了
      ``runnable``**（``gate[scene_id].accepts_new_work``），而 ``_inputs`` 把它们数进
      ``blocked_paused`` 而不是 ``due_now`` —— 判词因此是 ``paused``：它们是**决定
      不让它跑**，不是「还没到期」。
    * **回执**说的是「这一轮跳过了哪个场景、因为什么」。``ContinuationRun.not_ticking``
      算的正是这一格，而它原先**没有被转发**出去（见模块 docstring 末节）—— 于是
      「场景被暂停」与「盘上没活」在 ``--once`` 的输出里曾经逐字相同。

    两格一起读才分得开：回执里 ``not_ticking`` 点名了场景、理由是
    ``skip_reason(record)`` 那一句；状态页里 ``blocked_paused == 3`` 而
    ``waiting_due == 0``。只读一格，「有几件等着」与「有几件被挡着」都会消失。

    ## 「同一行」是字面的同一行

    恢复之后被驱动的那几条 ``continuation_id`` 与暂停之前那几条**逐字相同**。换了一行
    的实现（删掉重排、或者换了业务键）在报告上只显示「恢复了、活也做了」。
    """
    chain = _chain(rig)
    _seed_the_pages(rig, accounts=chain["accounts"])
    before = _rows(rig)
    assert len(before) == len(chain["accounts"]) + 1

    paused = rig.ok(("control_scene", {"action": "pause", "reason": "operator_pause"}))
    assert paused["scene"]["state"] == "paused", f"暂停没生效：{paused['scene']!r}"

    held = _tick(rig)
    assert held["touched"] == 0 and held["done"] == [], f"暂停期间动了手：{held!r}"
    assert held["state"] == "paused", (
        f"三件活都停在闸门外面，判词该是「暂停」而不是「等着到期」：{held!r}"
    )
    assert held["not_ticking"], (
        "回执里没有一处说得出「这个场景没在跑」—— 于是它与「盘上没活」读起来一样，"
        f"而那正是关闭条件 10 要分开的两件事：{held!r}"
    )
    assert [item["scene_id"] for item in held["not_ticking"]] == [r1.PROFILE]
    record = composition.default_scene_store(rig.composed)().get_record(r1.PROFILE)
    assert record is not None
    # 那句话**不在这里再抄一遍**：它只有一个产地（``skip_reason`` → ``dispatch_refusal``），
    # 而两处各写一份的漂移形态是「同一秒里一边说暂停、一边说可以开工」。
    assert held["not_ticking"][0]["reason"] == skip_reason(record), (
        f"回执上那句话与场景自己给的拒绝理由不是同一句：{held['not_ticking']!r} vs "
        f"{skip_reason(record)!r}"
    )

    page = _status_page(capsys)
    assert page["state"] == "paused", page
    assert page["inputs"]["blocked_paused"] == len(before), (
        f"被闸门挡住的正是盘上那几件 —— 而这一格是它们唯一的读数：{page['inputs']!r}"
    )
    assert page["inputs"]["due_now"] == 0 and page["inputs"]["waiting_due"] == 0, (
        "暂停是**更强**的那个理由：那些行同时「到期了」与「不许跑」，而它们只该进"
        f"「被挡住」那一格 —— 否则状态页答不出挡住它们的是什么：{page['inputs']!r}"
    )

    after = _rows(rig)
    assert {cid: row.state for cid, row in after.items()} == {
        cid: ContinuationState.PENDING for cid in before
    }, "暂停期间那些行的状态动了"
    assert {cid: row.attempts for cid, row in after.items()} == {
        cid: row.attempts for cid, row in before.items()
    }, "暂停期间 attempts 动了 —— 被挡住不是一次尝试"
    assert rig.fake.calls == [], "暂停期间碰了外面"

    resumed = rig.ok(("control_scene", {"action": "resume"}))
    assert resumed["scene"]["state"] == "active", f"恢复没生效：{resumed['scene']!r}"

    driven = _tick(rig)
    assert {row["continuation_id"] for row in driven["done"]} == set(before), (
        f"恢复之后被驱动的不是**同一批**行：{driven!r}"
    )
    assert driven["not_ticking"] == [], "场景已经恢复了，回执里还写着它没在跑"


# ----------------------------------------------------------------------
# 五、模拟到期
# ----------------------------------------------------------------------
def test_expiry_is_a_pure_function_of_now() -> None:
    """关闭条件 5 的第一条腿：**受控时钟下的纯函数**。

    ``what_is_due`` 的签名里没有进程内状态，末尾那一格 ``now`` 就是全部时序。所以
    同一条观测、同一份计划，两个 ``now`` 给出两个答案 —— 这就是「重启后仍知道谁到期」
    的机制：重启之后从库里读回来的东西一模一样，于是选出来的格子也一模一样。

    三格一起判，因为「不到期」有两种来路而它们只差一个数：**还没到周期**与**这一格
    从来没人看过**。把它们合成一格的实现会让「按周期重看的系统」在第一次跑起来时看
    起来完全正常 —— 每一格都是 ``NEVER_OBSERVED``，而每一格都到期。
    """
    seen_at = datetime(2026, 1, 1, tzinfo=utc_now().tzinfo)
    plan = RefreshPlan.uncalibrated_default()
    observation = Observation(
        workspace=r1.PROFILE,
        subject_kind="account",
        subject_id="acct-g2-pure",
        aspect=Aspect.CONTACT_PERSON_ROLE,
        observed_at=seen_at,
        source_ref="g2:pure",
    )

    def due(*, days: int) -> dict[Aspect, Any]:
        cells = what_is_due(
            scene_id=r1.PROFILE,
            scene_version=1,
            plan=plan,
            observations=(observation,),
            signals=(),
            now=seen_at + timedelta(days=days),
            subjects=(),
        )
        return {cell.aspect: cell for cell in cells}

    cadence = plan.rule_for(Aspect.CONTACT_PERSON_ROLE).after_days
    assert cadence == 90, f"这一条判据写的是未标定的缺省节奏 90 天：{cadence}"

    inside = due(days=cadence - 1)
    assert Aspect.CONTACT_PERSON_ROLE not in inside, (
        "还没到周期就被选出来了 —— 那样每一轮都在重核同一格，而账上只显示「核得比较勤」"
    )
    assert {cell.reason for cell in inside.values()} == {DueReason.NEVER_OBSERVED}
    assert len(inside) == len(Aspect) - 1, (
        f"这一格有人看过，剩下几面都没有：{sorted(a.value for a in inside)!r}"
    )

    outside = due(days=cadence + 1)
    cell = outside[Aspect.CONTACT_PERSON_ROLE]
    assert cell.reason is DueReason.CADENCE_ELAPSED, f"{cell!r}"
    assert cell.period == "cycle-1", cell.period
    assert cell.due_at == seen_at + timedelta(days=cadence), (
        "到期时刻该是**上一次本该到期的时刻**（拖得最久的一格排在最前），而不是这一刻"
    )
    assert {a for a in outside if a is not Aspect.CONTACT_PERSON_ROLE} == set(inside), (
        "除了那一格，别的几格的判词不该跟着变"
    )

    later = due(days=cadence * 2 + 1)[Aspect.CONTACT_PERSON_ROLE]
    assert later.period == "cycle-2", later.period
    assert later.due_at == seen_at + timedelta(days=cadence * 2), (
        "周期号要跟着走，而到期时刻**不含 now** —— 含了它，去重键每一轮都是新的，"
        "而账单上只显示「核得比较勤」"
    )


def test_tightening_the_cadence_makes_the_same_pages_really_fetched(rig: Rig) -> None:
    """关闭条件 5 的第二条腿：**接线那一趟真的重取**，而钱花在该花的那一支上。

    到期是纯函数算出来的（上一条），而「算出来之后有没有真的去外面拿」是另一件事：
    ``build_refresher`` 拿着一份**复用窗口**，窗口内的一页连请求都不发。所以这一条判
    两半：

    * 规则收紧**之前** —— 全部 ``REUSED``，抓取器一本账是空的（3 天前的页在 90 天的
      窗口里，**复用是对的**：一次重取会白花一次抓取额度）；
    * 规则收紧**之后** —— 同一批页面对 1 天的窗口已经过期，于是真的发出去请求，而
      两次 ``email_evidence`` 靠**本轮的内存备忘**复用：两格页面各自第一次取到页，
      另一格在同一个 tick 里直接用同一批页。
    """
    chain = _chain(rig)
    pages = _seed_the_pages(rig, accounts=chain["accounts"])
    assert rig.fake.calls == []

    before = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    assert sorted(before["refreshable_aspects"]) == sorted(
        aspect.value for aspect in REFRESHABLE_ASPECTS
    ), f"这一轮跑的不是本部署承诺的那几面：{before['refreshable_aspects']!r}"
    assert before["claimed_count"] == _REFRESH_FACES * len(chain["accounts"]), (
        f"每一个可复核面 × 两个主体，一格都还没看过：{before['done']!r}"
    )
    assert before["rechecks_by_state"] == [["reused", _PAGE_FACES * len(chain["accounts"])]], (
        "90 天的窗口里，3 天前那几页该**全部复用** —— 而数本地记录的那一面不产生"
        f"复核状态，所以这一行比认领数少：{before['rechecks_by_state']!r}"
    )
    assert rig.fake.calls == [], "「复用」那一支不该发出去任何请求"
    # 这一格钉的是**这条链真的摆下了哪几页**，而不是「预置了几页」：前者是复核会去
    # 处理的集合。三个人为的对称（「两个成员各两页」）在这里是错的 —— 第一个成员交
    # 了两页材料、第二个只交了一页，而**两个成员各有一页站根**来自发现那一轮。少了
    # 站根这一页，第一个数会绿在一个少一页的集合上。
    assert sorted(pages) == sorted(
        f"https://{r1._domain_of(rig.composed, account)}{page}"
        for account, paths in (
            (chain["first"], ("/", "/about", "/contact")),
            (chain["second"], ("/", "/contact")),
        )
        for page in paths
    )

    version = _tighten_cadence(rig)
    after = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    assert after["effective_version"] == version, (
        f"这一趟读的还是旧那一版的复核计划：{after['effective_version']!r}"
    )
    assert after["claimed_count"] == _PAGE_FACES * len(chain["accounts"]), (
        f"只有两个页面面的格子该到期（source_coverage 还是 180 天）：{after['done']!r}"
    )
    assert after["rechecks_by_state"] == [
        ["reused", len(chain["accounts"])],
        ["unchanged", len(chain["accounts"])],
    ], (
        "每个主体两次真的抓取（两个页面面之一）、一次本轮内复用（另一个面共用同一"
        f"批页）：{after['rechecks_by_state']!r}"
    )
    assert sorted(rig.fake.calls) == sorted(pages), (
        "两个主体各有两页要重取，而每一页**只该取一次** —— 数的是抓了几次，不是预置了"
        "几页，所以「取了两页」与「同一页取了两次」在这里是不同的两个列表："
        f"{sorted(rig.fake.calls)!r}"
    )
    assert after["coverage_gaps"], (
        "承诺要维护、而本部署没接的那几面必须被报出来（§11：不能靠 out_of_scope "
        f"把它们从验收分母里删掉）：{after['coverage_gaps']!r}"
    )


# ----------------------------------------------------------------------
# 六、受控来源变化
# ----------------------------------------------------------------------
def test_a_controlled_source_change_lands_beside_an_unchanged_page_in_one_tick(
    rig: Rig,
) -> None:
    """关闭条件 6。**同一轮里**一页没变、一页变了 —— 两种结局并排出现。

    「受控」落在两处：换的是**一个工厂**（两个绑定都点名，见 :func:`rig`），而「换哪
    一页」是 :func:`_seed_the_pages` 的 ``mutate``。所以这一条里没有任何一处改了被测
    代码的输入形状 —— 变的只是**那个外部站点**的字节，而变与不变各自被真的取了一次。

    ## 为什么必须挑一页**有真实指纹**的来变

    第二个成员若走 ``unjudged`` 那条路，它的证据是夹具用 ``make_evidence`` 写下的合成
    行（``content_sha256="abc"``）—— 那一页**任何**真实抓取都会读成 ``CHANGED``，
    于是这一条会绿在一个与来源无关的理由上。所以这里两个成员都是**真的结算过**的，
    变的是其中一页的字节。

    判据落在三处，因为它们答的是三个不同的问题：**状态**（这一轮各自走到了哪一步）、
    **逐页**（``changed_pages`` 点出来的是哪几页）、**落账**（变的那一页留下新证据行
    没有）。``UNCHANGED`` 与 ``CHANGED`` 都会写观测、都指向一份内容，所以只看观测分不
    开它们。
    """
    chain = _chain(rig)
    _seed_the_pages(rig, accounts=chain["accounts"])
    rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    _tighten_cadence(rig)

    changed_url = f"https://{r1._domain_of(rig.composed, chain['second'])}/contact"
    intact_before = len(_evidence(rig, chain["first"]))
    changed_before = len(_evidence(rig, chain["second"]))
    _seed_the_pages(rig, accounts=chain["accounts"], mutate=(changed_url,))

    result = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))

    assert result["rechecks_by_state"] == [["reused", 2], ["unchanged", 1], ["changed", 1]], (
        f"同一轮里两种结局必须并排出现：{result['rechecks_by_state']!r}"
    )
    rows = {
        row["subject_id"]: row
        for row in result["done"]
        if row["aspect"] == Aspect.CONTACT_PERSON_ROLE.value
    }
    mutated = rows[chain["second"]]["outcome"]
    intact = rows[chain["first"]]["outcome"]
    assert mutated["changed_pages"] == [changed_url], (
        f"变了的那一页必须被逐页点出来：{mutated!r}"
    )
    assert mutated["evidence_ids"], "变了的那一页该落一行新证据"
    assert intact["changed_pages"] == [], intact["changed_pages"]
    assert intact["evidence_ids"] == [], (
        "没变的页面落了新证据 —— 那样「世界变了」与「我们换了指纹口径」在账上就同形了"
    )
    assert len(_evidence(rig, chain["second"])) > changed_before, (
        "回执说落了新证据，盘上却没有新行 —— 产出引用指向的那一份要能自己复核"
    )
    assert len(_evidence(rig, chain["first"])) == intact_before, (
        "没变的成员那一侧也落了新行"
    )


# ----------------------------------------------------------------------
# 七、裁决 → 受影响对象重评
# ----------------------------------------------------------------------
def _identity_chain(rig: Rig) -> tuple[str, ...]:
    """走到「受理整份拒绝、待办开着」为止，返回池子里的账号。

    它与 :func:`r1._run_chain` 是**两条不同的路**而不是同一条的参数：那一条里每个成员
    都有名字（受理过得去），而这一条要的正是**过不去**的那一格。所以这里只复用那些
    「上游夹具」与「怎么调」的部分，每一步都从工具进。
    """
    scene = rig.ok(("create_scene", {**r1.BASE_CREATE, "activate": True}))
    assert scene["scene"]["state"] == "active", scene["scene"]
    r1._write_artifact(rig.composed)
    r1._seed_discovery_evidence(rig.composed)
    imported = rig.ok(("import_motherpool", {"artifact_ref": "motherpool"}))
    promoted = rig.ok(("promote_receipt", {"receipt_id": imported["receipt"]["receipt_id"]}))
    assert promoted["promotion"]["counts"]["promoted"] == len(r1.ARTIFACT_LINES), (
        f"每一行都该折出一个账号。逐行去向：{promoted['rows_detail']!r}"
    )
    r1._remember_a_name_that_is_only_an_id(rig.deployment, rig.composed)

    accounts = tuple(row["account_id"] for row in rig.ok(("list_account_pool", {}))["accounts"])
    frozen = rig.ok(("freeze_cohort", {}))

    blocked = rig.ok(
        (
            "submit_research",
            {
                "scene_id": r1.PROFILE,
                "input_kind": "cohort",
                "input_ref": frozen["cohort_id"],
                "idempotency_key": "g2-identity-1",
            },
        )
    )
    assert blocked["state"] == "blocked", f"那一行没有名字，受理必须整份拒绝：{blocked!r}"
    assert blocked["work_items"]["count"] == 0, blocked
    assert r1.IDENTITYLESS in blocked["reason"], (
        f"阻塞理由要点得出是哪一个成员：{blocked['reason']!r}"
    )
    return accounts


def test_a_resolution_owes_one_recheck_on_the_case_own_subject_snapshot(rig: Rig) -> None:
    """关闭条件 7。裁决**落账的同时**欠下一行重评，而它的主体是**案件自己带的快照**。

    这一条走的是那条真的会开出待办的路：名单里有一个成员，它的「名称」那一格填的是
    自己的内部 id —— §1 那次事故的形状。受理于是整份拒绝、开出 ``IDENTITY_CONFLICT``，
    然后人裁一次。

    判据落在三处：

    * **欠账与决定同生共死** —— 裁决的回执一回来，那一行**已经**在盘上了（不是等下一次
      tick 才补）；
    * **业务键是 ``resolution_id`` 而不是 ``case_id``** —— 同一件案件能离开待办两次
      （关掉之后 ``reopen`` 再裁一次），按 ``case_id`` 做键会让第二次重看**永远不会
      发生**，而它在报告上只显示「那件案子已经处理过了」；
    * **主体只有一处来源** —— ``case.subject_ref``。``affected_refs`` 那一串是给人读的
      快照，领域说不出里面每一条是哪一个种类 —— 拿它编主体会让这一趟照常跑完、报
      「0 格到期」，而那与「核过了、没有到期的」逐字相同。
    """
    accounts = _identity_chain(rig)
    cases = rig.ok(("list_review_cases", {"limit": None}))
    assert cases["counts"]["outstanding"] == 1, f"这一份名单该开出且只开出一件待办：{cases!r}"
    case = cases["cases"][0]
    assert case["reason"] == "identity_conflict", case
    assert case["subject_ref"] in accounts, (
        f"案件的主语该是名单里那个没有名字的成员：{case!r}"
    )

    resolved = rig.ok(
        (
            "resolve_review_case",
            {
                "case_id": case["case_id"],
                "outcome": "correct_object",
                "answer": "名单那一行的名称写的是内部键，主体是这块场地的运营方。",
                "expected_version": case["version"],
            },
        )
    )
    resolution_id = resolved["resolution"]["resolution_id"]
    owed_id = continuation_id(r1.PROFILE, ContinuationKind.MAINTENANCE_TICK, resolution_id)
    owed = _row(rig, owed_id)
    assert owed is not None, (
        "裁决落账了，而「受影响对象重看一眼」这一行不在盘上 —— §9 那句「裁决后自动重评」"
        f"在盘上不成立。盘上有的行：{sorted(_rows(rig))!r}"
    )
    assert owed.subject_kind == "account" and owed.subject_ref == case["subject_ref"], (
        f"重评的主体该是案件自己带的那一份快照：{owed!r}"
    )
    assert owed.state is ContinuationState.PENDING
    assert owed.business_key == resolution_id, (
        "业务键该是**这一次决定**：按 case_id 做键会让第二次裁决的重看永远不发生"
    )

    # 让这一趟走得通：那个主体的目录页真的取得到。**与夹具写下的那一页逐字同一份**。
    rig.fake.bodies[f"https://{r1._hosts()[0]}/directory"] = r1.DIRECTORY_PAGE.encode("utf-8")

    driven = _tick(rig)
    done = {row["continuation_id"]: row for row in driven["done"]}
    assert owed_id in done, f"欠下的那一行没有被驱动：{driven!r}"
    assert f"主体 {case['subject_ref']}" in done[owed_id]["reason"], (
        f"这一趟的理由要点出是**哪个主体**在被重看：{done[owed_id]!r}"
    )


# ----------------------------------------------------------------------
# 八、草稿版本更新
# ----------------------------------------------------------------------
def test_a_new_writing_policy_makes_a_second_draft_version_that_supersedes_the_first(
    rig: Rig,
) -> None:
    """关闭条件 8。输入变了之后重起草 → 版本加一、``supersedes`` 指回上一版。

    这一条**不新开起草那一路**：它走的是已经有的 ``draft_outreach``，变的只是输入
    （``put_writing_policy`` 换一版话术）。判据是「同一件事重做一次」这句话在这条链上
    成不成立 —— 而 ``generation_key`` 里带着写作者与策略版本，一次换了话术的重发与
    一次**逐字重发**因此必须分开：

    * 换了话术 ⇒ 新的一版（``draft_version`` 加一、``supersedes`` 指回上一版）；
    * 一个字都没换 ⇒ **没有新版本**（§5 逐字：「只给新增的或输入变了的对象生成」）。
      不做这一格的话，每一次重试都会造出一个新版本，而报告上只显示「草稿变多了」。

    上一版**逐字未动**也在这里判：草稿是「发出去过什么」的记录，被后来那一版覆盖掉的
    话，§12 的恒等式在盘上就不成立了。
    """
    chain = _chain(rig)
    r1._publish(rig.deployment)
    payload = r1._drafting_payload(chain["cohort_id"])
    # 起草按成员一件：这里只跟**一个**成员的那件草稿，因为「同一件的新版本」这句话
    # 只在一条 draft_id 上成立。
    account_id = chain["first"]
    store = composition.default_outreach_store(rig.composed)()

    def drafted() -> dict[str, Any]:
        receipt = rig.ok(("draft_outreach", payload))
        return {row["account_id"]: row for row in receipt["drafts"]}[account_id]

    first = drafted()
    assert first["draft_version"] == 1, first
    assert first["supersedes"] is None, first
    assert first["state"] == "generated", first
    version_one = store.draft(first["draft_id"], 1)
    assert version_one is not None

    again = drafted()
    assert again["draft_version"] == 1, (
        f"输入一个字都没变，重发不该造出新版本 —— 否则每一次重试都会造一个：{again!r}"
    )
    assert again["draft_id"] == first["draft_id"], (first["draft_id"], again["draft_id"])
    assert store.draft(first["draft_id"], 2) is None, "重发造出了第 2 版"

    rig.ok(
        (
            "put_writing_policy",
            {"policy_id": r1.POLICY, "revision": 2, "generic_wording_id": "wording-generic-2"},
        )
    )
    second = drafted()
    assert second["draft_version"] == 2, f"话术换了一版，重起草该是第 2 版：{second!r}"
    assert second["draft_id"] == first["draft_id"], (
        "同一件草稿的新版本 —— 换一个 id 会让两份草稿各自「没有历史」"
    )
    assert second["supersedes"] == 1, f"新那一版必须指回上一版：{second!r}"
    assert second["generation_key"] != first["generation_key"], (
        "话术换了而生成键没变 —— 那么下一轮的幂等判定会把这一版当成重复"
    )
    assert store.draft(first["draft_id"], 1) == version_one, (
        "上一版被改动了 —— 它是「发出去过什么」的记录，只增不改"
    )


# ----------------------------------------------------------------------
# 九、三种时间分开
# ----------------------------------------------------------------------
def test_three_kinds_of_time_are_three_different_accounts(rig: Rig) -> None:
    """关闭条件 9：**抓取次数 / 盘上新增 / 取回回执**是三个数，不能互相替代。

    它们答的是三个不同的问题，而三者在报告上都能长得像「这一轮没做什么」：

    * **抓取次数** —— 这一轮花了多少钱。它只在那本外部账上（``FakeHttp.calls``）。
    * **盘上新增** —— 这一轮世界变了多少。它只在证据库上。
    * **取回回执** —— 这一格各自走到了哪一步、为什么。它只在 ``RecheckRecord`` 上。

    四步各按一种变化推进，而**每一步都同时读三个数**，于是「这一格没变」与「这一格根本
    没看」在每一行上都分得开：

    1. 什么都不变 → 每格认领、全部复用，**外面零次请求**。
    2. 紧接着再来一趟 → ``claimed_count`` 是 **0**，而它照样被报出来（一个把它读成
       「没跑」的调用方会去查一个不存在的故障）。到期判定读的是观测，而观测没变 ——
       这是「重启之后仍知道谁到期」的另一面：**它也不会凭空变出新工作**。
    3. 一个**事件**落下（第二个成员那条还开着的活被结算，新来源进池）→ 那一格的复核
       不必等满一个周期：它按 ``CHANGE_SIGNALLED`` 到期，而盘上真的多了一行证据。
    4. 规则收紧 → 同一批页面真的被重取，而回执上是 ``UNCHANGED``：**钱花了、结论是
       没变** —— 这一格正是「抓取次数」与「盘上新增」不能互相替代的地方。
    """
    chain = _chain(rig, second_route="unjudged")
    _seed_the_pages(rig, accounts=chain["accounts"])
    second_domain = r1._domain_of(rig.composed, chain["second"])
    second_evidence = len(_evidence(rig, chain["second"]))

    # —— 第一步：什么都不变 ——
    first = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    assert first["claimed_count"] == 3 * len(chain["accounts"]), f"{first['done']!r}"
    assert first["rechecks_by_state"] == [["reused", 2 * len(chain["accounts"])]], first[
        "rechecks_by_state"
    ]
    assert rig.fake.calls == [], "第一趟不该花钱：那几页都还在时效内"
    assert len(_evidence(rig, chain["second"])) == second_evidence, "第一趟落了新证据"

    # —— 第二步：紧接着再来一趟 ——
    repeat = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    assert repeat["claimed_count"] == 0, (
        f"观测没变，到期判定不该再选出格子：{repeat['done']!r}"
    )
    assert repeat["ok"] is True, "「没有到期的格子」是正常状态，不是一次故障"
    assert repeat["due_count"] == 0, repeat["due_count"]
    assert repeat["rechecks_by_state"] == [], (
        f"一格都没跑，回执里却有复核状态：{repeat['rechecks_by_state']!r}"
    )
    assert rig.fake.calls == []
    assert len(_evidence(rig, chain["second"])) == second_evidence

    # —— 第三步：一个事件落下 ——
    settled = r1._settle(
        rig.deployment,
        rig.composed,
        account_id=chain["second"],
        work_item_id=chain["item_id"],
        pages=((r1._contact_page(second_domain), f"https://{second_domain}/contact"),),
    )["routes"]
    assert settled["error"] is None and settled["routes"] == 1, settled
    assert len(_evidence(rig, chain["second"])) > second_evidence, (
        "结算该在盘上留下新的一行证据 —— 没有它，下面那一格就不是一个「事件」"
    )

    signalled = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))
    assert signalled["claimed_count"] == 1, (
        "只有那一格按信号到期："
        f"{[(r['aspect'], r['subject_id'], r['reason']) for r in signalled['done']]!r}"
    )
    only = signalled["done"][0]
    assert only["aspect"] == Aspect.SOURCE_COVERAGE.value, only
    assert only["subject_id"] == chain["second"], only
    assert only["reason"] == DueReason.CHANGE_SIGNALLED.value, (
        f"这一格到期的理由该是「有人报了这一格变了」，而不是周期到了：{only!r}"
    )
    assert rig.fake.calls == [], "数本地记录的那一面不联网"

    # —— 第四步：规则收紧 ——
    calls_before = len(rig.fake.calls)
    rows_before = len(_evidence(rig, chain["first"]))
    _tighten_cadence(rig)
    refetched = rig.ok(("run_maintenance", {"cohort": chain["cohort_id"]}))

    assert len(refetched["done"]) == 2 * len(chain["accounts"]), f"{refetched['done']!r}"
    assert len(rig.fake.calls) > calls_before, (
        "钱没有花出去 —— 而这一格要的正是「同一批页面对新窗口过期之后真的重取」"
    )
    assert refetched["rechecks_by_state"] == [["reused", 2], ["unchanged", 2]], (
        f"抓了、结论是没变 —— 这两件事必须分开记：{refetched['rechecks_by_state']!r}"
    )
    assert len(_evidence(rig, chain["first"])) == rows_before, (
        "抓回来的指纹与记录一致，就不该落新证据 —— 落了的话「世界变了」这个数会跟着"
        "「我们看得勤不勤」一起涨"
    )


# ----------------------------------------------------------------------
# 十、停机与无新数据可区分
# ----------------------------------------------------------------------
def test_a_missing_wire_a_dead_store_and_an_empty_queue_read_differently(
    rig: Rig, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """关闭条件 10。四种「没有在做的事」各自说得出**是哪一种**。

    | 形状 | 判据 |
    |---|---|
    | 工具**不在** | ``continuation_handlers`` 交了空表 ⇒ 那一族工具压根没注册 |
    | 库里**什么都没有** | ``get_continuation_status`` 报 ``no_store``，``status`` 是 ``None`` |
    | 缺**一样接线** | 那一行 ``BLOCKED(CAPACITY)`` 且理由点名缺的是哪一样；状态页 ``missing_capacity`` |
    | 盘上**没有到期** | ``state == "complete"``、``due_now == 0``、外面一件都没动 |

    第一、二行是**工具层**的两句话（「还没接线」与「没有工作在跑」），后两行是**控制器**
    那一侧的两句话。合成一句「没有活」的后果是：一个还没接线的部署与一个正常空转的部署
    在运维眼里逐字相同 —— 而前者要人去补东西，后者什么都不用做。

    （第五种形状「场景停着」在第 4 条那一条上判，因为要造出「停着而**有**到期的活」
    必须先有一条链欠下活来。）
    """
    # —— 工具不在 ——
    absent = continuation_handlers(
        settings=rig.composed,
        jobs=None,
        registry=composition.default_wakeup_registry(rig.composed),
        scenes=composition.default_scene_store(rig.composed),
    )
    assert absent == {}, (
        "缺一样工厂时那一族工具该**整族不注册**（而不是注册几个会报错的），否则调用方"
        f"看到的是一个坏掉的工具而不是一个不存在的工具：{sorted(absent)!r}"
    )

    # —— 库里什么都没有 ——
    empty = rig.ok(("get_continuation_status", {}))
    assert empty["store_state"] == "no_store", (
        f"库还不存在时这一页必须说得出这一点：{empty!r}"
    )
    assert empty["status"] is None and empty["driver"] is None
    assert empty["note"].strip(), "这一页要说得出「这不是『没有工作在跑』」"

    # —— 有活：先看一眼它怎么报，再让派发那一件缺接线 ——
    chain = _chain(rig)
    _seed_the_pages(rig, accounts=chain["accounts"])
    present = rig.ok(("get_continuation_status", {}))
    assert present["store_state"] == "present" and present["status"] is not None, present
    assert present["status"]["inputs"]["open_total"] == len(_rows(rig)), present["status"]
    assert present["status"]["state"] == "waiting_due", present["status"]
    assert present["driver"] is None, (
        "这一条判据没有跑过控制器，所以归属那一格该是空的 —— 它答的是「有没有人在推」，"
        f"而一个恒非空的实现会让「没有控制器在跑」永远报不出来：{present['driver']!r}"
    )

    real_dispatcher = controller.default_dispatcher
    monkeypatch.setattr(controller, "default_dispatcher", lambda _settings: None)
    missing = _tick(rig)
    assert [row["kind"] for row in missing["blocked"]] == [
        ContinuationKind.DISPATCH_BATCH.value
    ], f"派发那一件该报缺接线：{missing!r}"
    assert missing["blocked"][0]["on"] == "capacity", missing["blocked"][0]
    assert "执行器" in missing["blocked"][0]["reason"], (
        f"被挡住必须说得出来缺的是哪一样：{missing['blocked'][0]!r}"
    )
    assert missing["not_ticking"] == [], "接线缺了不是场景停着 —— 这两件事要分得开"
    assert _status_page(capsys)["state"] == "missing_capacity", (
        "状态页把「缺一样接线」与「没有到期的活」读成同一件事"
    )
    blocked_row = _rows(rig)[missing["blocked"][0]["continuation_id"]]
    assert blocked_row.state is ContinuationState.BLOCKED and blocked_row.attempts == 0, (
        f"缺接线不是一次尝试 —— 它不该吃 attempts：{blocked_row!r}"
    )

    # —— 接上那一样：同一行排到点之后照常被驱动 ——
    monkeypatch.setattr(controller, "default_dispatcher", real_dispatcher)
    rig.clock.advance(seconds=120)
    caught_up = _tick(rig)
    assert {row["continuation_id"] for row in caught_up["done"]} == {
        blocked_row.continuation_id
    }, f"补上接线之后那一件活必须还在、而且真的做了：{caught_up!r}"

    # —— 盘上没有到期 ——
    drained = _tick(rig)
    assert drained["touched"] == 0 and drained["not_ticking"] == [], f"{drained!r}"
    assert drained["blocked"] == [] and drained["retrying"] == [], (
        f"一件都不该还挂着 —— 那一行上一轮就做完了：{drained!r}"
    )
    page = _status_page(capsys)
    assert page["state"] == "complete", page
    assert page["inputs"]["ever_done"] is True and page["inputs"]["due_now"] == 0, page
