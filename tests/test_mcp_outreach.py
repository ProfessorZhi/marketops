"""外联那一段工具走真实 MCP 会话时的行为（``docs/MCP_PRODUCT_LOOP.md`` §2）。

## 这一层测的是「接线之后它还对不对」，而不是业务规则

业务规则住在 ``application/outreach.py`` 与 ``domain/outreach.py``，各自的判据
已经存在（``tests/test_outreach_contract.py``）。本文件**不重测那些**。它测的是
**只有把四段（域层、编排层、存储、MCP 面）接起来才会出现**的那几件事：

* **注册**：这一段里有哪几条工具在这个部署上**存在**。§8 逐字要求「新的危险操作
  对普通研究身份不可见、不可调用，仅在部署能力启用时注册」—— 于是「注册了几条」
  必须有一个总数与一次划分，否则「少注册了一条」与「那一条本来就没有」同形。
* **缺席的两种说法**：没注册（本部署没有这个能力）与没接线（这个实例还没装
  这一段）是两句不同的话，而下一步也完全不同。一个只有后者能表达的测试面，
  会把前者压成后者。
* **顺序**：缺席那句话必须在**工作区核对之后**给出，否则一条错工作区的令牌
  能靠它把目录枚举出来。
* **一次真的走完的链**：立项 → 起草 → 冻清单 → 读批次 → 授权 → 投递。这是
  §9 那三条验收线里「邮件链在隔离环境做端到端验收」的可执行形态 —— 传输是
  一个**记账替身**，一个字节都不出网络。

## 两个主体，不是一条万能令牌

链上那一步「授权」不是 controller 能做的：``authorize_wave`` 在目录里是
``_AUTHORIZE``（**J 段唯一一条不是 controller 的行**），而
``security/tokens.py::refuse_collapsed_gate`` 拒绝一条同时带 controller 与
authorizer 的环条目。所以下面那条端到端判据用**两个令牌、两次会话**走完 ——
这不是为了好看，而是「谁批准了」与「谁准备并发出去了」不能落在同一个主体上，
而那句要求只有在这一层才可能被违反。环能装起来本身，就是那条拒绝规则在跑的证据。

## 为什么每条「能过」的用例都配了一条「过不去」的

只有正常路径的用例无法区分「拒绝对了」与「拒绝根本没跑」。所以每一条放行都
配了一条拦下：清单冻出来之后**还没批**就投递、窗口还没开就投递、传输自报的
发送箱与清单不一致 —— 三条的症状都是「传输一封都没收到」，所以它们必须各自
被单独钉住，否则三条会一起被一条的绿盖住。

## 传输替身与「真实外发」的距离

:class:`RecordingTransport` 只往一个列表里记，没有任何一行代码会去连一台中继。
它**不是** ``GuardedMailTransport`` 的替身 —— 收件人允许列表那一层闸由
``tests/test_guarded_mail.py`` 单独守。这里验的是**编排有没有把信交出去、交了
几封、交的是哪一封**，而那与「交出去之后会不会被拦」是两件事。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from marketops.adapters.deterministic_writer import DeterministicDraftWriter
from marketops.adapters.mcp_server.outreach import (
    _MAX_REPLY_CHARS,
    OUTREACH_TOOLS,
    outreach_handlers,
    reply_cursor_file,
    unavailable_outreach_tools,
)
from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.reply_classifier import RuleBasedReplyClassifier
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.application.freshness import judge
from marketops.config import Settings
from marketops.domain.entities import ContactRoute
from marketops.domain.enums import Grade, GradeReason, RouteBinding
from marketops.domain.temporal import FreshnessPolicy
from marketops.domain.util import sha256_text
from marketops.domain.outreach import (
    Wave,
    WaveState,
)
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.ports.correspondence import (
    CorrespondenceBatch,
    CorrespondenceItem,
    CorrespondenceSource,
)
from marketops.ports.outreach import (
    DraftProposal,
    DraftWriter,
    MailTransport,
    SendReceipt,
)
from marketops.security.catalog import (
    TOOL_CATALOG,
    Principal,
    Role,
    visible_tools,
)
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import (
    drive_with_settings,
    ring_entry,
    ring_payload,
    write_accounts,
)
from tests.test_evidence_store_contract import (
    FETCHED_AT,
    make_evidence,
    make_observation,
)

UTC = timezone.utc

#: 本部署的 Scene。**不是随便挑的**：``require_scene`` 只认 ``settings.profile``，
#: 而处理器里每一处归属核对（``_bound_wave``）比的都是这一格。
#:
#: 它与 ``tests/test_evidence_store_contract.py`` 的 ``WORKSPACE`` 是同一个值，
#: 所以 ``make_evidence`` / ``make_route`` 可以直接用而不必逐条覆盖工作区 ——
#: 两处写两个值的话，这一条链上每一个「这条路线属于本部署」的判定都会落空，
#: 而那看起来像「池子是空的」。
PROFILE = "uk-acme-natural-grass"

#: 账号池那份清单的名字（``inputs/<名字>.yaml``）。``pool`` 参数指的就是它。
POOL = "uk-natural-grass"

CAMPAIGN = "campaign-uk-grass-2026q3"
WAVE = "wave-2026-09-24-0001"
SENDBOX = "outreach@marketops.example"
FAMILY = "family-uk-grass-stage-1"
POLICY = "policy-uk-grass-stage-1"

#: 这一族在没有个性化事实时可以用的**通稿措辞** id。
#:
#: 它是 ``WritingPolicy`` 上那格「§5：个性化缺失时用已批准的通稿措辞或停下」的
#: 牌照。空着的话这一批会全部落 ``held``（理由逐字：「正文写不出来，不得猜」），
#: 于是整条链在第二步就断了 —— 而断在第二步的链在报告上长得像「这个池子没人」。
GENERIC_WORDING = "wording-generic-stage-1"

#: 判时效用的时限口径。**与 :data:`POLICY` 不是同一样东西**：那一个是写作策略的
#: id（「允许说什么」），这一个是一组天数（「这一行今天还算不算数」）。
#:
#: 生产路径上口径由部署的 profile 声明，``freshness.policy_of(bundle)`` 取出来 ——
#: 而这里要装一整份 ``ProfileBundle`` 才走得到那一步，本文件测的是接线不是装载，
#: 所以就地给一组。天数是**够松**的：池子里的时刻都在 ``FETCHED_AT`` 前后，判出来
#: 恒为 ``current``，于是「这一条够格起草」成立。
FRESHNESS = FreshnessPolicy(
    policy_version="test-v1",
    current_within_days=180,
    historical_after_days=730,
)

#: 窗口。**必须包含「现在」** —— 投递时刻取自服务端时钟（``utc_now``），不是参数。
#: 一个写死在过去的窗口会让「能过」的那条用例真的发不出去，而它红的样子与
#: 「授权没生效」一模一样。
WINDOW_START = "2026-09-01T00:00:00+00:00"
WINDOW_END = "2026-12-01T00:00:00+00:00"
VALID_UNTIL = "2026-12-31T00:00:00+00:00"
#: 一段**还没开始**的窗口。给「窗口没到就投递」那一条用：它的起点必须在
#: 「现在」之后，而终点又必须晚于起点（清单自己就这么要求），所以三个时刻
#: 一起往后挪，授权也一并挪 —— 否则红的会是「授权过期」，而不是「窗口没到」。
FUTURE_WINDOW_START = "2027-01-01T00:00:00+00:00"
FUTURE_WINDOW_END = "2027-03-01T00:00:00+00:00"
FUTURE_VALID_UNTIL = "2027-06-01T00:00:00+00:00"

#: 一封来信**发生**的瞬间。必须在「现在」之前 —— 库里会拒一个早于 ``received_at``
#: 的处理时刻（我们不可能在它发生之前就知道）。
RECEIVED_AT = datetime(2026, 9, 20, 8, 30, tzinfo=UTC)

#: 一份**配齐**的触达节奏。未标定的那一份（``uncalibrated_default``）在库里是
#: 合法的一版 Scene，但它读不出一条规则 —— 于是「按未标定的策略起草」在这条链上
#: 走不到第二步。这里给的是一份配齐的声明，好让下面诸条判据测的是接线而不是标定。
_OUTREACH_DECL: dict[str, Any] = {
    "max_per_wave": 20,
    "max_per_recipient": 3,
    "max_per_account": 1,
    "waits": {"stage-2": 48, "stage-3": 96, "stage-4": 144},
    "prior_touches": {"stage-2": 1, "stage-3": 2, "stage-4": 3},
}

#: 立项那一次的载荷。**每条用例都要先有它** —— 触达规则写在 Scene 的一版上，
#: 没有生效版本就没有规则可读，而那是链上的第一颗扣子。
BASE_SCENE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
    "outreach": _OUTREACH_DECL,
    "activate": True,
}

#: 两条账号。**两个**而不是一个：一条判据要断言「正好两封」，而只有一个试件时
#: 「只发了一封」与「一共就一封」分不开。
ACCOUNTS = ("account-0001", "account-0002")

CONTROL_PRINCIPAL = "claude-control"
AUTHORIZER_PRINCIPAL = "claude-authorizer"


# --- 替身 ---------------------------------------------------------------------


class RecordingTransport(MailTransport):
    """只记账，从不真的发。**这个类里没有一行会去连网络。**

    它继承 :class:`MailTransport` 而不是自己长一个同形状的类：接口漂了的时候，
    这里会因为少实现一个抽象方法而在**导入期**就红 —— 而一个鸭子类型的替身只会
    在某个用例真的用到那个方法时才失败，那时它看起来像一条业务判据红了。

    ``sender_identity`` 是它能自报的那一格，而 ``dispatch_wave`` 在逐封循环**之外**
    拿它与清单上的发送箱比对 —— 所以这个值必须与清单上的那个一致，否则一条都不发。
    """

    def __init__(self, *, sender_identity: str = SENDBOX) -> None:
        self._sender = sender_identity
        self.sent: list[dict[str, Any]] = []
        self.asked: list[str] = []

    @property
    def sender_identity(self) -> str:
        return self._sender

    def send(self, *, entry, subject: str, body: str, from_address: str) -> SendReceipt:
        self.sent.append(
            {
                "recipient": entry.recipient,
                "subject": subject,
                "body": body,
                "from": from_address,
            }
        )
        return SendReceipt(
            outcome="accepted", accepted=True, message_id=f"<mid-{len(self.sent)}@x>"
        )

    def status(self, message_id: str) -> SendReceipt:
        self.asked.append(message_id)
        return SendReceipt(outcome="accepted", accepted=True, message_id=message_id)


class StubSource(CorrespondenceSource):
    """一叠固定的批次，按次交出去，并把每次收到的游标记下来。

    真实来源要按游标去问提供方；这里要验的是「游标从盘上来、写完才推进」这条
    接线，所以它把每次调用收到的游标都记下来，让判据能看见**第二次收到的是
    第一次写下的那个位置**。
    """

    def __init__(self, *batches: CorrespondenceBatch) -> None:
        self._batches = list(batches)
        self.seen_cursors: list[str] = []

    def read(self, *, cursor: str = "") -> CorrespondenceBatch:
        self.seen_cursors.append(cursor)
        if not self._batches:
            return CorrespondenceBatch(items=(), cursor=cursor, from_cursor=cursor)
        return self._batches.pop(0)


class VersionedWriter(DraftWriter):
    """一个正文固定、但**策略版本与众不同**的写作者。

    ``policy_version`` 是本文件要钉的那一格：它由 ``DraftWriter`` 端口自己报，
    不是处理器的一个常量。判据要在回执里看到**这个**值。
    """

    def __init__(self, *, policy_version: str) -> None:
        self._version = policy_version

    @property
    def writer_id(self) -> str:
        return "writer-probe"

    @property
    def policy_version(self) -> str:
        return self._version

    def draft(self, request) -> DraftProposal:
        return DraftProposal(
            subject=f"About {request.organisation}",
            body=f"A short note to {request.organisation}.",
            # 逐对抄自请求。引用**没有被给定**的证据会被编排层拒
            # （``application/outreach.py`` 里那条「引用了没给过的事实」的判定），
            # 而这里没有传过任何 ``facts``，所以这一组通常落成空元组。
            facts_used=tuple(
                (name, evidence_id) for name, _value, evidence_id in request.facts
            ),
            writer_id=self.writer_id,
        )


# --- 夹具 ---------------------------------------------------------------------


@dataclass
class Wiring:
    """一次「这个部署装了哪几样」的完整描述。

    传输与来源都在里面，因为**它们各自决定了几条工具存在** —— 判据要同时看得见
    「注册了哪几条」与「那台传输收到了什么」，而后者只有持有它的那一方能回答。
    """

    handlers: dict[str, Any]
    unavailable: dict[str, str]
    transport: Any
    source: Any
    scenes: Any
    ring: TokenRing
    token: str
    authorizer: str


@dataclass
class Deployment:
    """一个测试部署：一个装好数据的库 + 一个按需装配的构造器。

    ``settings`` 是 ``conftest`` 那个夹具的**副本**，只多一格发送箱。
    ``Settings`` 是 frozen dataclass，所以这里用 ``replace``。多这一格不是顺手：
    ``reply_cursor_file`` 与 ``_prepare_wave`` 都以 ``outbound_from_address`` 为
    发送箱的来源，缺了它连一次「做一次要发信的部署」都表达不出来 —— 而那是
    **部署的实话**，不是测试的假设（缺省 ``None`` 表示这个部署没有发送箱，
    ``prepare_wave`` 会照实拒绝）。
    """

    settings: Settings
    scenes: Callable[[], Any]
    ring_dir: Path

    def build(
        self,
        *,
        transport: Any = "default",
        source: Any = "default",
        writer: Any = "default",
        classifier: Any = "default",
        workspaces: Any = None,
    ) -> Wiring:
        chosen_transport = RecordingTransport() if transport == "default" else transport
        chosen_source = StubSource() if source == "default" else source
        chosen_writer = DeterministicDraftWriter() if writer == "default" else writer
        chosen_classifier = (
            RuleBasedReplyClassifier() if classifier == "default" else classifier
        )
        handlers = outreach_handlers(
            settings=self.settings,
            scenes=self.scenes,
            # 工厂，不是实例 —— 与组合根里那一处同款：``sqlite3`` 的连接默认不允许
            # 跨线程使用，所以「读一次开一次」是接线的事实，不是风格。
            store=lambda: SqliteOutreachStore(self.settings.database_path),
            writer=chosen_writer,
            classifier=chosen_classifier,
            accounts=default_account_pool_store(self.settings),
            cohorts=default_account_cohort_store(self.settings),
            transport=chosen_transport,
            source=chosen_source,
        )
        ring, control, authorizer = _make_ring(self.ring_dir, workspaces=workspaces)
        return Wiring(
            handlers=handlers,
            # 「缺了哪几条」由**同一组常量**从同样的两个 ``None`` 算出来，
            # 与组合根逐条同款。
            unavailable=unavailable_outreach_tools(
                transport=chosen_transport,
                transport_reason="本部署没有配置外发路径。",
                source=chosen_source,
                source_reason="本部署没有配置来信来源。",
            ),
            transport=chosen_transport,
            source=chosen_source,
            scenes=self.scenes,
            ring=ring,
            token=control,
            authorizer=authorizer,
        )


def _make_ring(
    directory: Path, *, workspaces: Any = None
) -> tuple[TokenRing, str, str]:
    """一个装两条条目的环：一条 controller、一条 authorizer。

    **两条，不是一条带两个角色。** ``security/tokens.py::refuse_collapsed_gate``
    在装载期就拒绝同时握着闸门两半的条目，所以「一个主体既批准又发出去了」在这套
    东西里连表达都表达不出来 —— 而这个环能装起来，正是那条规则真的在跑的证据。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tokens.json"
    kwargs = {} if workspaces is None else {"workspaces": workspaces}
    control, control_digest = mint(CONTROL_PRINCIPAL, [Role.CONTROLLER])
    authorizer, authorizer_digest = mint(AUTHORIZER_PRINCIPAL, [Role.AUTHORIZER])
    path.write_text(
        json.dumps(
            ring_payload(
                ring_entry(
                    control_digest, CONTROL_PRINCIPAL, [Role.CONTROLLER], **kwargs
                ),
                ring_entry(
                    authorizer_digest,
                    AUTHORIZER_PRINCIPAL,
                    [Role.AUTHORIZER],
                    **kwargs,
                ),
            )
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(path), control, authorizer


def _seeded_route(account_id: str) -> ContactRoute:
    """一条**与生产产出同形**的路线观察里的那一行。

    ## 为什么不能只写 ``grade=A``

    池子里的路线是**已经定过级**的，而定级读的是判过时效的那一行、写的是
    ``binding`` 判过的那一行（``application/contact_routes.py`` 的
    ``_judge_and_grade``，逐字：「先判时效、再定级」）。所以真实的一行同时带着
    ``binding`` 与 ``freshness``，而这两格正是 §8 那句「准入、当前性或绑定不够时
    只给明确标注的 review-only / held」读的两格（``application/outreach.py`` 的
    ``review_reasons``）。

    只写等级的夹具造的是一条**生产产不出来**的行：绑定没人判过（``NONE`` 是
    「从来没有人判过」，见 ``domain/bind.py``）、时效没判过。那种行在
    ``draft_outreach`` 里会落成 ``review_only`` 而**不进清单**，于是这条链上
    「立项 → 起草 → 冻清单 → 投递」的每一次都会停在「清单是空的」上 —— 而那个
    症状看起来像编排坏了。

    判时效走的是**生产的那一个函数**（:func:`~marketops.application.freshness.judge`），
    不是手写一个 :class:`Freshness`：行级判据要求 ``freshness.assertion_id`` 等于
    本行自己算出来的那个 id，手写的那个绕开了 ``assertion_for``，于是这里造的
    「够格」会与生产判出来的「够格」不是同一件事。

    时限口径（:data:`FRESHNESS`）是本**部署**的那个参数：它由 profile 声明，生产
    路径从 ``ProfileBundle`` 取（``freshness.policy_of``）。这里那一条路要装一整份
    bundle，而本文件测的是接线之后的链，不是装载。口径取一组够松的天数，于是
    ``FETCHED_AT`` 前后的时刻恒判成 ``current``。
    """
    route = ContactRoute(
        workspace=PROFILE,
        account_id=account_id,
        organisation=f"Example {account_id}",
        email=f"{account_id}@example.co.uk",
        grade=Grade.A,
        grade_reason=GradeReason.RULE_MATCHED,
        evidence_ids=[f"evidence-{account_id}"],
        binding=RouteBinding.STRONG,
        first_seen_at=FETCHED_AT,
        source_published_at=FETCHED_AT - timedelta(days=10),
    )
    return judge(route, FRESHNESS, now=FETCHED_AT).row


def _seed(settings: Settings) -> None:
    """把这条链需要的**输入**全部落到 ``settings.database_path`` 这一个库上。

    路线与证据进的是**证据库** —— 组合根让两个端口落在同一个文件上，所以这里
    构造一次就够了。

    路线带 ``grade=A`` 而不是让定级留空：池子里的候选是**已经定过级**的路线
    （定级是上游的事，这条链只消费它）。留空的等级会让 ``plan_wave`` 把两条
    路线都按「等级不够」跳过 —— 那一批就是空的，而空批次在报告上长得像
    「这个池子没人」。

    路线还带**判过的绑定与时效** —— 为什么见 :func:`_seeded_route`。少了那两格，
    上述那条链「到起草为止」是通的，而**起草这一段的产出进不了清单**。

    ## 这里**不**写话术，虽然它写的全是链的输入

    写作策略与模板族当然也是这条链的输入，但它们的入口是**产品工具**（§7 逐字：
    发布要经产品入口、用户不得直接写数据库），所以它们由 :func:`published` 在
    会话里发布。夹具绕过那一步会省掉两次调用，而它正好把 §7 要修的那件事盖住：
    那三条工具在本轮之前根本不存在，于是「一个干净客户端发布不了话术」在套件里
    没有一条用例看得出来 —— 而 ``draft_outreach`` 的每一条用例都会因此建立在
    一个**产品做不到的前提**上。

    这一条分界是**输入 vs 动作**：证据与路线是上游产出的既有事实（这一层只消费
    它们），而「允许说什么」是这一层自己的一次决定，决定要走它自己的门。
    """
    evidence = SqliteEvidenceStore(settings.database_path)
    for account_id in ACCOUNTS:
        evidence.put_evidence(
            make_evidence(
                evidence_id=f"evidence-{account_id}",
                account_id=account_id,
                source_url=f"https://example.co.uk/{account_id}",
            )
        )
        evidence.record_route(
            make_observation(account_id, route=_seeded_route(account_id))
        )


@pytest.fixture
def deployment(settings: Settings, tmp_path: Path) -> Deployment:
    """一个装好输入清单与证据库的部署，外加一个按需装配的构造器。"""
    boxed = replace(settings, outbound_from_address=SENDBOX)
    write_accounts(boxed, POOL, ACCOUNTS)
    _seed(boxed)
    return Deployment(
        settings=boxed, scenes=default_scene_store(boxed), ring_dir=tmp_path / "tokens"
    )


def driver(deployment: Deployment, wiring: Wiring, *, authorizer: bool = False):
    """一个 ``run(*calls, want_tool_list=False)`` 驱动器。

    ``authorizer=True`` 用那一条 authorizer 令牌。两次会话是**两个主体**，与链上
    那两个动词的权限归属同形：准备并发出外联的那个人，不能是把闸门放行的那个人。

    产品环那几条也装上：``create_scene`` 是这条链的第一颗扣子，而一个只装外联
    那一段的实例里根本没有它。
    """
    product_loop = product_loop_handlers(
        settings=deployment.settings,
        scenes=wiring.scenes,
        maintenance=default_maintenance_store(deployment.settings),
        assets=default_asset_store(deployment.settings),
        relations=default_relation_store(deployment.settings),
        accounts=default_account_pool_store(deployment.settings),
        cohorts=default_account_cohort_store(deployment.settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )
    token = wiring.authorizer if authorizer else wiring.token
    assert token, "这个部署没有为这个主体铸过令牌"

    def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
        outcome = drive_with_settings(
            settings=deployment.settings,
            tokens=wiring.ring,
            token=token,
            product_loop=product_loop,
            outreach=wiring.handlers,
            unavailable=wiring.unavailable,
            scenes=deployment.scenes,
            calls=calls,
            want_tool_list=want_tool_list,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


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


def founding(**overrides) -> tuple[str, dict[str, Any]]:
    payload = dict(BASE_SCENE)
    payload.update(overrides)
    return ("create_scene", payload)


def published() -> tuple[tuple[str, dict[str, Any]], ...]:
    """链上那两发**发布**：先写作策略，再模板族。

    ## 为什么这两条在每一次调用里，而不是在夹具里

    §7 逐字要求这三样配置有**产品入口**，用户不得直接写数据库。夹具直接
    ``SqliteOutreachStore.put_family`` 会省掉两次调用 —— 而它正是本轮要修的那件
    事：在那三条工具存在之前，整个套件里没有一条用例能看出「一个干净客户端发布
    不了话术」，因为每一处 ``draft_outreach`` 的前提都是夹具替它造好的。

    所以这两条留在这里，位置就是链上的位置：**立项之后、起草之前**。发布要的是
    这个 Scene 的记录存在（``_plan_context`` 那次解析），而它**不**要求 Scene
    有生效版本 —— 备货不必等市场开张。

    顺序也承重：模板族指着 ``writing_policy_id``，所以策略必须先落。

    ## ``put_claim`` 不在这里，而这不是遗漏

    那一条的判定全在「谁发布的」上（``approved_by`` 取自会话主体），所以它只能由
    一次**带令牌的会话**证明，没法用一个返回调用元组的助手证明 —— 那一组在
    ``test_a_clean_client_can_publish_and_draft_from_what_it_published`` 里连同
    起草一起走完。
    """
    policy: dict[str, Any] = {
        "policy_id": POLICY,
        "revision": 1,
        "generic_wording_id": GENERIC_WORDING,
    }
    family: dict[str, Any] = {
        "family_id": FAMILY,
        "revision": 1,
        "stage": "stage-1",
        "role_class": "grounds_manager",
        "locale": "en-GB",
        "product": "natural-grass",
        "writing_policy_id": POLICY,
    }
    return (("put_writing_policy", policy), ("put_family", family))


def drafting(**overrides) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "pool": POOL,
        "wave_id": WAVE,
        "campaign_id": CAMPAIGN,
        "campaign_name": "UK 天然草 首次接触",
        "stage": "stage-1",
        "locale": "en-GB",
    }
    payload.update(overrides)
    return ("draft_outreach", payload)


def preparing(**overrides) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "pool": POOL,
        "wave_id": WAVE,
        "campaign_id": CAMPAIGN,
        "stage": "stage-1",
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "valid_until": VALID_UNTIL,
    }
    payload.update(overrides)
    return ("prepare_wave", payload)


def approving(**overrides) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "wave_id": WAVE,
        "authority_ref": "approval-ticket-0001",
        "expires_at": VALID_UNTIL,
        "approved_count": len(ACCOUNTS),
    }
    payload.update(overrides)
    return ("authorize_wave", payload)


# --- 一、注册：这一段里有哪几条工具在这个部署上存在 ------------------------------


def test_outreach_registration_is_three_way_total(deployment: Deployment) -> None:
    """四种接线组合下，**注册的 ∪ 缺席的 == ``OUTREACH_TOOLS``**，且两两不相交。

    注册侧不重新手写一份名单，而是**照着真的处理器表取键** —— 手抄一份的话，
    这一条判据与 ``outreach_handlers`` 分家的时候就只剩「两边都写着
    ``send_wave``」这一个共同点，而那正是它不该红的时候它也不红的原因。
    """
    transport = RecordingTransport()
    source = StubSource()

    cases = (
        (transport, source, frozenset()),
        (transport, None, frozenset({"ingest_replies"})),
        (None, source, frozenset({"send_wave", "reconcile_wave"})),
        (None, None, frozenset({"send_wave", "reconcile_wave", "ingest_replies"})),
    )

    for transport_case, source_case, expected_absent in cases:
        absent = unavailable_outreach_tools(
            transport=transport_case,
            transport_reason="缺传输",
            source=source_case,
            source_reason="缺来源",
        )
        assert set(absent) == expected_absent, (transport_case, source_case, absent)
        for reason in absent.values():
            assert reason.strip(), "缺席必须带上理由 —— 一句空的解释与没解释同形"

        registry = outreach_handlers(
            settings=deployment.settings,
            scenes=deployment.scenes,
            store=lambda: SqliteOutreachStore(deployment.settings.database_path),
            writer=DeterministicDraftWriter(),
            classifier=RuleBasedReplyClassifier(),
            accounts=default_account_pool_store(deployment.settings),
            cohorts=default_account_cohort_store(deployment.settings),
            transport=transport_case,
            source=source_case,
        )

        assert set(registry) | set(absent) == OUTREACH_TOOLS, (
            "注册的加上缺席的必须正好是 ``OUTREACH_TOOLS`` —— 第九态不存在。"
            f"算出来的是 {sorted(set(registry) | set(absent))}"
        )
        assert not (set(registry) & set(absent)), "一个名字不可能既注册又缺席"


def test_the_outreach_half_is_exactly_the_catalog_s_outreach_rows() -> None:
    """``OUTREACH_TOOLS`` 与目录里 J 段中**外联那一半**是同一批名字。

    J 段分成两半：产品环那一半与本段这一半。所以这条判据比的是**划分**（两半互不
    相交、并起来正好是 J），而不是「J 段只有这八个」—— 后者会把整个产品环读成外联，
    而在那份目录上它恰好是多出来的那几条。

    这句話原来写着「产品环十条与本段八条」，而它已经落后过两次：散文里的算术没有
    任何机器检查，而下面那两条断言每次都被跑。所以这里不再给数 ——
    ``docs/MCP_PRODUCT_LOOP.md`` 对同一件事写过同一句（那段话原来也带一个行数）。

    多一个意味着有一段代码在注册一条目录里查不到的工具（于是它没有任何角色
    判定），少一个意味着目录里有一条永远注册不出来的行。

    ## 两半各自钉，而不是数 J 段的总数

    这里原来写的是 ``len(catalog_j) == 16``，后来是两半各给一个数。两个数都比一个数
    好（红了之后指得出是哪一半变的），但**产品环那一半的那个数**还是没有事实基础：
    没有任何要求说它该是几条，它是**一处手抄的副本**，而按名字钉住那几条的地方在
    ``tests/test_mcp_product_loop.py`` —— 那张载荷表逐条列出产品环的每一条工具，并
    断言它与「J 段里控制者可用、且不属于 ``OUTREACH_TOOLS`` 的那几条」相等。所以这里
    只留本段这一半的数（``OUTREACH_TOOLS`` 是代码里的一个常量，钉它与钉清单是同一件
    事），产品环那一半交给那份按名字列出的清单 —— 同一句话只有一处抄写。
    """
    catalog_j = {name for name, spec in TOOL_CATALOG.items() if spec.phase == "J"}
    product_loop_half = catalog_j - set(OUTREACH_TOOLS)
    assert len(OUTREACH_TOOLS) == 11
    assert not (product_loop_half & set(OUTREACH_TOOLS))
    assert product_loop_half | set(OUTREACH_TOOLS) == catalog_j


def test_no_outreach_tool_is_visible_to_a_researcher() -> None:
    """一个研究身份在 ``tools/list`` 上看不到这一段里的**任何一条**。

    §8 逐字：「新的危险操作对普通研究身份**不可见、不可调用**」。只做「不可调用」
    的话，研究侧 worker 仍然能列出整个外联目录，而那既是一次信息泄漏，也是一条把
    「不属于你的工具」与「不存在的工具」区分开的旁路。
    """
    researcher = Principal(
        name="worker-1",
        roles=frozenset({Role.RESEARCHER}),
        workspaces=frozenset({PROFILE}),
    )
    assert not (OUTREACH_TOOLS & set(visible_tools(researcher)))


def test_the_gate_s_two_halves_are_two_principals() -> None:
    """controller 看得见 ``send_wave``、看不见 ``authorize_wave``；authorizer 反之。

    §6 逐字：「授权必须来自可信、独立授权的责任人……模型写下『我批准』、普通
    控制者自授一个角色、或者 ``send_authorized=true`` 都产生不了授权」。目录把
    「批准」与「投递」分给了两个角色，而这条判据是那句要求的可执行形态：把
    ``authorize_wave`` 并回 ``_CONTROL`` 的那一天，这里会红。
    """
    controller = Principal(
        name=CONTROL_PRINCIPAL,
        roles=frozenset({Role.CONTROLLER}),
        workspaces=frozenset({PROFILE}),
    )
    authorizer = Principal(
        name=AUTHORIZER_PRINCIPAL,
        roles=frozenset({Role.AUTHORIZER}),
        workspaces=frozenset({PROFILE}),
    )
    visible_to_control = set(visible_tools(controller))
    visible_to_authorizer = set(visible_tools(authorizer))

    assert "send_wave" in visible_to_control
    assert "authorize_wave" not in visible_to_control
    assert "authorize_wave" in visible_to_authorizer
    assert not (OUTREACH_TOOLS & (visible_to_authorizer - {"authorize_wave"}))


def test_an_absent_name_cannot_also_be_backed(deployment: Deployment) -> None:
    """同一个名字既在 ``backed`` 又在 ``unavailable`` 里 —— **装配期就拒**。

    这一格是「两条账本各自自洽而互相矛盾」的那类事故：注册层按 ``handlers``
    装上了它，而缺席层正准备给出一句「本部署没有这个能力」。两种回答各自都对，
    只是在同一台部署上不可能都对 —— 而它的表现是一句随顺序变化的答复，所以
    必须在构造期显形，而不是等一次真的调用。

    走的是**组合根那条路**（``build_server``）而不是直接构造 ``RoleGuard``：
    这个矛盾只可能在接线那一侧产生，而直接构造中间件测的是那个类记不记得检查。
    """
    wiring = deployment.build()
    assert "send_wave" in wiring.handlers, "这一条判据的前提是它真的接上了"

    # 自洽的那一份照常装得起来 —— 否则下面那一句拒的是「哪里都不对」，
    # 而不是「这两份表互相矛盾」。
    driver(deployment, wiring)(want_tool_list=True)

    with pytest.raises(ValueError) as caught:
        drive_with_settings(
            settings=deployment.settings,
            tokens=wiring.ring,
            token=wiring.token,
            outreach=wiring.handlers,
            unavailable={"send_wave": "本部署没有配置外发路径。"},
            scenes=deployment.scenes,
            want_tool_list=False,
        )
    assert "send_wave" in str(caught.value), str(caught.value)


def test_a_capability_that_is_absent_is_not_even_listed(
    deployment: Deployment,
) -> None:
    """没有传输的部署：``send_wave`` / ``reconcile_wave`` **不在 tools/list 上**。

    一条「列得出来、点了报尚未接线」的 ``send_wave`` 会让「这一格能不能调」与
    「它存不存在」混成同一件事 —— 而前者的答案会随部署变，后者不会。

    清单里**不含** ``authorize_wave``：那一条是 authorizer 的，controller 看不见它。
    这一条会话用的是 controller 令牌，所以这里能断言的是 controller 该看见的那五条 ——
    把 ``authorize_wave`` 写进期望值，红的会是「闸门的另一半没有归到另一个主体」，
    而那与这一条要测的东西无关。
    """
    wiring = deployment.build(transport=None)
    outcome = driver(deployment, wiring)(want_tool_list=True)

    assert "send_wave" not in outcome.tool_names
    assert "reconcile_wave" not in outcome.tool_names
    assert "authorize_wave" not in outcome.tool_names, "闸门的另一半不属于 controller"
    # 另外五条照旧在 —— 这一条断言的不是「整个外联面对外关了」。
    assert {
        "draft_outreach",
        "prepare_wave",
        "get_wave",
        "list_handoffs",
        "ingest_replies",
    } <= set(outcome.tool_names), sorted(outcome.tool_names)


def test_the_absence_explanation_is_not_the_unbacked_one(
    deployment: Deployment,
) -> None:
    """「本部署没有这项能力」与「这个实例尚未接线」是**两句不同的话**。

    两条路都在同一台部署上取：一条是 ``send_wave`` 因为没配外发路径而**没有注册**，
    另一条是整段外联都没接线（``outreach=None``）时那条工具落回的 stub —— 两者调的
    是同一个名字，而下一步完全不同（改部署 vs 看接线）。

    比的是**两句话本身**，不是某一句话里有没有某个词。``unavailable_message`` 的
    结尾正好逐字写着「这与「尚未接线」不是同一件事」—— 于是「不含这四个字」这种
    断言在这里是**恒假**的，而它会红得看起来像接线错了。
    """
    wiring = deployment.build(transport=None)
    absent = _refused(driver(deployment, wiring)(("send_wave", {"wave_id": WAVE})))

    unwired = _refused(
        drive_with_settings(
            settings=deployment.settings,
            tokens=wiring.ring,
            token=wiring.token,
            # 两样都不给：这一条工具在目录里，而**这个实例**没接它。
            outreach=None,
            unavailable=None,
            scenes=deployment.scenes,
            calls=(("send_wave", {"wave_id": WAVE}),),
            want_tool_list=False,
        )
    )

    assert absent != unwired, "两句不同的话被写成了一句"
    assert "没有注册" in absent
    assert "没有注册" not in unwired
    assert "本部署没有配置外发路径。" in absent, (
        "缺席的理由由接线那一侧给，必须逐字带出来 —— 它说的才是缺的是哪一样"
    )


def test_the_absence_explanation_comes_after_the_workspace_check(
    deployment: Deployment,
) -> None:
    """一条错工作区的令牌调用缺席的工具，拿到的是**工作区那句话**。

    顺序在这里是承重的：把「本部署没有这个能力」放在工作区核对之前，一条错
    工作区的令牌就能靠它把目录枚举出来 —— 每一次调用都会告诉它这个名字存在。

    两条路用的是同一个部署（同一条没注册的 ``send_wave``），只有令牌的
    ``workspaces`` 不同，所以这一条红的唯一原因就是那两句话的先后。

    而那句话**不报出本部署的工作区是哪一个**（与受众错配同款：一条不属于这里的
    令牌不该从拒绝里学到这里的名字）。所以这里能断言的是它说了「工作区」，
    而不是它说了那个名字。
    """
    wiring = deployment.build(transport=None, workspaces=["some-other-market"])
    outcome = driver(deployment, wiring)(("send_wave", {"wave_id": WAVE}))

    text = _refused(outcome)
    assert "工作区" in text, text
    assert "没有注册" not in text, (
        "缺席那句话被提前说了 —— 于是这条令牌知道了一个它不该知道的名字"
    )


# --- 二、走一遍完整的链 --------------------------------------------------------


def test_the_chain_runs_from_founding_to_a_delivered_batch(
    deployment: Deployment,
) -> None:
    """立项 → 起草 → 冻清单 → 读批次 → 授权 → 投递，两个主体走完。

    断言的是**状态与传输收到的那两封**，而不是「调用没报错」：一条停在
    ``prepared_pending_authorization`` 的链与一条走到已投递的链，在「没报错」
    这个尺度上长得一样。
    """
    wiring = deployment.build()
    control = driver(deployment, wiring)
    control(founding(), *published(), drafting(), preparing())
    # 读批次与授权是两次会话：第二次用的是 authorizer 那条令牌。
    read_back = _ok(driver(deployment, wiring)(("get_wave", {"wave_id": WAVE})))
    approved = _ok(
        driver(deployment, wiring, authorizer=True)(approving())
    )
    sent = _ok(driver(deployment, wiring)(("send_wave", {"wave_id": WAVE})))

    assert read_back["state"] == "prepared_pending_authorization", (
        "§6：缺省停点就是这一格 —— 冻清单不等于可发"
    )
    assert read_back["manifest"]["allowed_count"] == len(ACCOUNTS)
    assert len(read_back["entries"]) == len(ACCOUNTS)
    assert read_back["attempts"] == [], "还没投递，就不该有尝试记录"
    assert any("还没有任何授权记录" in line for line in read_back["stopped_by"]), (
        "「为什么停在这里」必须说得出下一步 —— 那是这个工具存在的理由："
        f"实际报的是 {read_back['stopped_by']}"
    )

    assert approved["state"] == "authorized"
    # **批准人是 authorizer，不是 controller。**
    assert approved["authorized_by"] == AUTHORIZER_PRINCIPAL, (
        "批准人取自传输层已认证的主体，不由参数给"
    )
    assert approved["approved_count"] == len(ACCOUNTS)

    assert sent["counts"] == {"sent": len(ACCOUNTS)}, sent
    assert len(wiring.transport.sent) == len(ACCOUNTS), (
        f"应该正好 {len(ACCOUNTS)} 封，实际 {wiring.transport.sent}"
    )
    assert all(row["reached_the_transport"] for row in sent["outcomes"])
    assert sorted(row["recipient"] for row in wiring.transport.sent) == sorted(
        f"{account_id}@example.co.uk" for account_id in ACCOUNTS
    )


def test_drafting_hands_back_a_stage_that_is_not_yet_sendable(
    deployment: Deployment,
) -> None:
    """起草那一份回执里**没有一格**能被读成「可以发了」。

    §5 逐字：「批量生成完成 ≠ 允许发送」。这一格是那句话的可执行形态：一条把
    「生成完毕」折成「准备就绪」的实现在报告上完全正常，而它省掉的是整条闸门。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(founding(), *published(), drafting())

    founded = _ok(outcome, 0)
    assert founded["scene"]["state"] == "active"
    assert founded["scene"]["effective_version"] == 1

    drafted = _ok(outcome, 3)
    assert drafted["plan"]["planned"] == len(ACCOUNTS), drafted["plan"]
    assert drafted["send_authorized"] is False, (
        "「批量生成完成 ≠ 允许发送」—— 这一格是那一句的可执行形态"
    )
    assert drafted["stage"] == "stage-1"
    assert {row["state"] for row in drafted["drafts"]} == {"generated"}, drafted["drafts"]
    assert wiring.transport.sent == [], "起草不得碰传输"


def test_nothing_leaves_before_an_authorization(deployment: Deployment) -> None:
    """清单冻出来之后、还没批之前投递：**传输一封都没收到**。

    这是 §6 那句话的负半：只测「批了就发得出去」无法区分「授权真的把门守住了」
    与「门根本没装」。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(
        founding(), *published(), drafting(), preparing(), ("send_wave", {"wave_id": WAVE})
    )

    report = _ok(outcome, 5)
    assert wiring.transport.sent == [], "授权之前，一封都不该到传输"
    assert report["counts"] == {"skipped": 1}, report
    assert "prepared_pending_authorization" in report["outcomes"][0]["detail"]


def test_a_window_that_has_not_opened_stops_the_batch(deployment: Deployment) -> None:
    """窗口还没到就投递：授权是有的，但**一封都不发**。

    与上一条配成一对：那一半守的是「授权」，这一半守的是「时间窗」。两条拒绝
    都会在「传输什么都没收到」上留下同一个痕迹，所以两条都必须各自能变红。
    """
    wiring = deployment.build()
    driver(deployment, wiring)(
        founding(),
        *published(),
        drafting(),
        # 整段窗口挪到未来：**两端一起挪**，因为清单自己就拒绝一个终点不晚于
        # 起点的窗口。只把起点挪走，这一条会红在清单构造上而不是红在窗口判定上
        # —— 那时它测的是别的东西。
        preparing(
            window_start=FUTURE_WINDOW_START,
            window_end=FUTURE_WINDOW_END,
            valid_until=FUTURE_VALID_UNTIL,
        ),
    )
    driver(deployment, wiring, authorizer=True)(approving(expires_at=FUTURE_VALID_UNTIL))
    read_back = _ok(driver(deployment, wiring)(("get_wave", {"wave_id": WAVE})))
    report = _ok(
        driver(deployment, wiring)(("send_wave", {"wave_id": WAVE}))
    )

    assert wiring.transport.sent == [], "窗口没开，一封都不该到传输"
    assert any("还没到窗口" in line for line in read_back["stopped_by"]), (
        read_back["stopped_by"]
    )
    assert report["counts"] == {"skipped": 1}, report


def test_a_sendbox_that_does_not_match_stops_the_whole_batch(
    deployment: Deployment,
) -> None:
    """传输自报的发送箱与清单上的不是一个：**一封都不发**。

    这一条在逐封循环**之外**判（它不是某一封的问题，是整个部署的问题），所以
    它的症状是「一封都没发，而逐封的理由一条都没有」—— 那是一个容易读错的
    形状，因此必须被单独钉住。
    """
    stranger = RecordingTransport(sender_identity="someone-else@marketops.example")
    wiring = deployment.build(transport=stranger)
    driver(deployment, wiring)(founding(), *published(), drafting(), preparing())
    driver(deployment, wiring, authorizer=True)(approving())
    report = _ok(driver(deployment, wiring)(("send_wave", {"wave_id": WAVE})))

    assert stranger.sent == []
    assert report["counts"] == {"skipped": 1}, report
    assert "发送箱" in report["outcomes"][0]["detail"], report["outcomes"][0]


# --- 三、写作策略版本由写作者报 ------------------------------------------------


def test_the_policy_version_in_the_receipt_comes_from_the_writer(
    deployment: Deployment,
) -> None:
    """回执里的 ``policy_version`` **逐字**取自写作者，不取自处理器的一个常量。

    它是 ``generation_key`` 的一半（另一半是 ``writer_id``）：写死一个值的意思，
    是策略改了而键不变 —— 于是重新生成的那一稿会被读成 ``unchanged`` 而不落库，
    换出去的还是按旧策略写的那一句，而报告上一切正常。
    """
    wiring = deployment.build(writer=VersionedWriter(policy_version="probe-rev-42"))
    outcome = driver(deployment, wiring)(founding(), *published(), drafting())

    drafted = _ok(outcome, 3)
    assert {row["policy_version"] for row in drafted["receipts"]} == {"probe-rev-42"}, (
        drafted["receipts"]
    )
    assert {row["writer_id"] for row in drafted["receipts"]} == {"writer-probe"}


def test_the_default_writer_reports_an_unknown_policy_version() -> None:
    """缺省的写作者报**空串**，而不是一个像版本号的值。

    本部署今天没有任何一份带版本的写作策略，所以「我不知道」是实话；一个像样的
    版本号会让「策略改了但键没变」这件事在报告上消失。这一条同时钉住组合根里
    那一行**不传** ``policy_version`` 的决定。
    """
    assert DeterministicDraftWriter().policy_version == ""


# --- 四、批次归属与只读那一格 --------------------------------------------------


def test_get_wave_has_no_scene_parameter(deployment: Deployment) -> None:
    """``get_wave`` 的入参里**没有** ``scene_id``。**比的是集合相等。**

    另外几条收 ``scene_id`` 是因为它们要**做**一件属于某个市场的事；这是一个
    **读**，要读的东西自带它的归属（``Wave.scene_id`` / ``Wave.workspace``）。
    一个可以让读的人指定归属的参数，只会让「读到的这份属于谁」多出一个来源。
    """
    handlers = deployment.build().handlers
    assert set(inspect.signature(handlers["get_wave"]).parameters) == {
        "wave_id",
        "limit",
    }


def test_a_wave_of_another_workspace_is_refused_not_read(
    deployment: Deployment,
) -> None:
    """一个属于别的 workspace 的批次：**拒绝**，而不是读出来。

    ``dispatch_wave`` / ``record_authorization`` / ``reconcile_unknown`` 都不收
    workspace 参数（它们从批次自己身上读），所以「这一批是不是我们的」在
    application 那一层没有人问 —— 那是**接线**保证的，而接线保证的东西必须在
    一个地方被检查，否则它不是一个检查、是一句备注。
    """
    SqliteOutreachStore(deployment.settings.database_path).put_wave(
        Wave(
            wave_id="wave-someone-else",
            campaign_id="campaign-someone-else",
            workspace="another-market",
            scene_id="another-scene",
            state=WaveState.PREPARED_PENDING_AUTHORIZATION,
            manifest_sha256="0" * 64,
            revision=0,
            created_at=RECEIVED_AT,
            updated_at=RECEIVED_AT,
        ),
        expected_revision=None,
    )
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(("get_wave", {"wave_id": "wave-someone-else"}))

    text = _refused(outcome)
    assert "another-market" in text, text
    assert PROFILE in text, text


def test_a_wave_that_was_never_prepared_is_a_readable_refusal(
    deployment: Deployment,
) -> None:
    """一个从没存在过的批次：拒得**说得出口**，而不是一次内部错误。

    「还没有清单」与「还没建过」在这里是同一件事（两者都没有可读的行），而那句
    话必须把这一点说清楚，否则运维会去查一个不存在的批次到底建过没有。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(("get_wave", {"wave_id": "wave-never-existed"}))

    text = _refused(outcome)
    assert "wave-never-existed" in text, text
    assert "读不到" in text, text


# --- 五、来信与交接 ------------------------------------------------------------


def _unsubscribe(send_key: str, sender: str) -> CorrespondenceBatch:
    """一封退订。正文里那句 ``take me off`` 是分类器的退订提示词之一。

    这一条刻意走**分类器真的会认的那句话**，而不是直接造一个「已分类好」的
    条目：中间那一步（正文 → 类 → 处置）正是 §7 要的那条路，绕过去测的就只是
    后半段。

    ``send_key`` **必须是一次真发出去的尝试的键**，而 ``sender`` 是那一封的收件人。
    不填它的版本会落 ``manual_review``（理由逐字：「关联不上：这条事件没有带
    send_key……」）—— 那不是这一步没接好，是 ``ingest`` 的一条**设计**：一封不知道
    对应哪一次发送的回信，既不知道该停哪条跟进、也不该凭空造一条抑制。
    所以这一条判据的前提是链已经走到**投递过**。
    """
    return CorrespondenceBatch(
        items=(
            CorrespondenceItem(
                provider_message_id="pm-1",
                sendbox=SENDBOX,
                raw_ref="raw/pm-1.eml",
                raw_sha256="0" * 64,
                received_at=RECEIVED_AT,
                sender=sender,
                subject="unsubscribe",
                body="Please take me off your list.",
                send_key=send_key,
            ),
        ),
        cursor="cursor-2",
        from_cursor="cursor-1",
    )


def _delivered(deployment: Deployment, wiring: Wiring) -> tuple[Any, dict[str, Any]]:
    """立项 → 起草 → 冻清单 → 授权 → 投递，返回 controller 的驱动器与投递回执。

    来信那几条判据要的是**一条真的发出过信、库里有尝试记录**的部署 ——
    关联得上才谈得上分类与处置。把它写成一段共用，是因为「投递过」这件事
    在四条判据里各写一遍时，任何一处写错都会让那条判据测的东西变成别的。
    """
    driver(deployment, wiring)(founding(), *published(), drafting(), preparing())
    driver(deployment, wiring, authorizer=True)(approving())
    sent = _ok(driver(deployment, wiring)(("send_wave", {"wave_id": WAVE})))
    assert sent["counts"] == {"sent": len(ACCOUNTS)}, sent
    return driver(deployment, wiring), sent


def test_ingest_replies_advances_the_cursor_and_writes_suppression(
    deployment: Deployment,
) -> None:
    """一封退订进来：立刻落抑制，游标推进，而且游标存在**盘上**。

    三件事各有各的失败形态：没落抑制 → 那个人还会收到下一封；游标没推进 →
    下一段来信被重读（幂等，但会重跑分类器）；游标只在内存里 → 进程一重启就
    退回上一次落盘的位置。三件一起断言，因为它们在同一条路径上，而任何一件
    单独看都不足以说明这条路径对了。
    """
    # 先真的发出去一批：回信要关联得上一条**已存在的尝试**才谈得上处置。
    probe = deployment.build()
    _, sent = _delivered(deployment, probe)
    delivered = probe.transport.sent[0]

    wiring = deployment.build(
        source=StubSource(_unsubscribe(sent["outcomes"][0]["send_key"], delivered["recipient"]))
    )
    outcome = driver(deployment, wiring)(("ingest_replies", {}))

    receipt = _ok(outcome)
    assert receipt["advanced"] is True
    assert receipt["cursor_before"] == "cursor-1"
    assert receipt["cursor_after"] == "cursor-2"
    assert wiring.source.seen_cursors == [""], (
        "第一次读之前盘上没有游标（空串 = 从头读）—— 实际读到的是 "
        f"{wiring.source.seen_cursors}"
    )

    on_disk = json.loads(Path(receipt["cursor_file"]).read_text(encoding="utf-8"))
    assert on_disk == {"cursor": "cursor-2"}, "游标必须落在盘上，不能只在内存里"

    ingested = receipt["ingested"]
    assert len(ingested) == 1, ingested
    assert ingested[0]["disposition"] == "suppress", (
        "退订 → 立刻进持久抑制（§7 逐字）"
    )


def test_the_cursor_is_read_from_disk_on_the_second_call(
    deployment: Deployment,
) -> None:
    """第二次调用读的是**第一次写下的那个位置**，不是一个缺省的空串。

    这一条与上一条分开：上一条钉的是「写下了」，这一条钉的是「读回来了」。
    两处各写一半的实现在上一条上完全是绿的。
    """
    probe = deployment.build()
    _, sent = _delivered(deployment, probe)
    delivered = probe.transport.sent[0]

    wiring = deployment.build(
        source=StubSource(_unsubscribe(sent["outcomes"][0]["send_key"], delivered["recipient"]))
    )
    run = driver(deployment, wiring)
    run(("ingest_replies", {}))
    outcome = run(("ingest_replies", {}))

    assert wiring.source.seen_cursors == ["", "cursor-2"], (
        f"第二次应当从 cursor-2 读起，实际 {wiring.source.seen_cursors}"
    )
    # 第二次来源里已经空了 —— 空批次是正常的，它回的是同一个游标。
    assert _ok(outcome)["advanced"] is True


def test_a_source_that_cannot_be_read_is_not_an_empty_batch(
    deployment: Deployment,
) -> None:
    """来源读不成与「这一段没有来信」是两件事 —— 而游标**不动**。

    一个把读不成当成没有来信的实现在报告上完全正常，而它的病是**漏收**。
    """

    class Broken(StubSource):
        def read(self, *, cursor: str = "") -> CorrespondenceBatch:
            raise OSError("信箱连不上")

    wiring = deployment.build(source=Broken())
    outcome = driver(deployment, wiring)(("ingest_replies", {}))

    text = _refused(outcome)
    assert "OSError" in text, text
    assert "游标没有动" in text, text
    assert not reply_cursor_file(deployment.settings).exists(), (
        "一次读失败不得写出一个游标文件 —— 那会让下一次从「读失败的那一段之后」开始"
    )


def test_list_handoffs_reports_the_missing_owner_instead_of_inventing_one(
    deployment: Deployment,
) -> None:
    """一条交接都没有时，``owner`` 是 ``null`` 且**带一句缺口说明**。

    ``SalesHandoff`` 上没有负责人字段。回执里编一个名字会让「有没有人管」看起来
    已经答完了 —— 而那是一个要报出来的缺口，不是一次读取失败。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(("list_handoffs", {}))

    payload = _ok(outcome)
    assert payload["count"] == 0
    assert payload["owner"] is None
    assert "SalesHandoff" in payload["owner_unavailable"]


def test_the_reply_body_is_bounded_in_the_receipt_only() -> None:
    """回执里的回复正文有一个**截断上限**。

    这一条只钉住那个常量本身（``_MAX_REPLY_CHARS``）—— 「库里那一份是全文」
    由 ``SalesHandoff`` 的字段判据守着，而它在 application 那一层。
    """
    assert 0 < _MAX_REPLY_CHARS < 10_000


# --- 六、没有传输的部署上，另外几条**照旧工作** ---------------------------------


def test_drafting_still_works_without_any_transport(deployment: Deployment) -> None:
    """没有外发路径的部署照旧起草得出草稿，也照旧冻得出清单。

    与「``send_wave`` 不在 tools/list 上」配成一对：§8 那句话的意思是**危险
    操作**不注册，不是整个外联面关掉。把整个面关掉会让一个只做研究的部署连
    草稿都产不出来，而那不是任何一句要求说的。
    """
    wiring = deployment.build(transport=None)
    assert set(wiring.unavailable) == {"send_wave", "reconcile_wave"}
    outcome = driver(deployment, wiring)(
        founding(), *published(), drafting(), preparing(), ("list_handoffs", {})
    )

    assert _ok(outcome, 3)["plan"]["planned"] == len(ACCOUNTS)
    assert _ok(outcome, 4)["state"] == "prepared_pending_authorization"
    assert _ok(outcome, 5)["count"] == 0


# --- 七、发布入口：话术只从产品进来 --------------------------------------------


def test_a_clean_client_can_publish_and_draft_from_what_it_published(
    deployment: Deployment,
) -> None:
    """一个干净客户端把「允许说什么」发布进去，然后**用它起草出草稿**（§7）。

    这条链在 §7 之前**走不通**，而且不是走不通在某一格上：那三条发布工具根本
    不存在。所以这一条判据的形状是「一次会话里从零到草稿」—— 它红的时候说不出
    是哪一格坏的，但它绿的时候说的是**这件事从外面真的做得到**，而那是这一整段
    唯一没法从别处读出来的信息。

    起草那一步刻意用**刚刚发布的那一套**去写：库里有别的族、别的策略时，「发布
    进去了」与「起草用的是它」是两件事，而只测前者的判据在「发布写进 A、起草读
    自 B」的实现上照样绿。

    ## 覆盖面是「发布 → 起草」，不是「发布」

    三发发布各自返回一份 ``stored``，这里逐条读回**落库的那一份**（不是调用方传
    进去的那几个参数）：工具层有没有替调用方补缺省，只有从落库那一份上看得出来。

    ``put_claim`` 也在这条链上，虽然起草用不到它。它在这一条里承的判据是
    ``approved_by`` —— 那格**不是参数**，只能由一次真的带令牌的会话证明。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(
        founding(),
        *published(),
        (
            "put_claim",
            {
                "claim_id": "claim-uk-grass-0001",
                "revision": 1,
                "text": "本族通稿只说这一句已被批准的话。",
                "source_ref": "sop-uk-natural-grass#claims",
            },
        ),
        drafting(),
    )

    policy = _ok(outcome, 1)
    assert policy["published"] == "写作策略"
    assert policy["scene_id"] == PROFILE, "发布的归属要报出来，且取自会话解析"
    assert policy["stored"]["policy_id"] == POLICY
    assert policy["stored"]["revision"] == 1
    assert policy["next_revision"] == 2, "回执要给下一步 —— 这三样东西改不了"

    family = _ok(outcome, 2)
    assert family["published"] == "模板族"
    assert family["stored"]["writing_policy_id"] == POLICY, (
        "族指着刚发布的那一套策略 —— 两发之间没有第三处可以改写这个指向"
    )

    claim = _ok(outcome, 3)
    assert claim["published"] == "已批准说法"
    assert claim["stored"]["approved_by"] == CONTROL_PRINCIPAL, (
        "批准人取自会话已确立的主体，不由参数给 —— 与 authorize_wave 同一条理由"
    )
    assert claim["stored"]["approved_at"], "批准时刻取自服务端时钟，也不由参数给"

    drafted = _ok(outcome, 4)
    assert drafted["plan"]["planned"] == len(ACCOUNTS), drafted["plan"]
    assert {row["state"] for row in drafted["drafts"]} == {"generated"}, drafted["drafts"]
    assert drafted["send_authorized"] is False, "发布了话术也不等于可以发"


def test_the_same_revision_with_other_words_is_refused_not_overwritten(
    deployment: Deployment,
) -> None:
    """同一 ``(id, 版)`` 配另一份内容：**拒**，且拒绝说得出下一步（§7）。

    「历史修订不可变」这句话的可执行形态。一条覆盖式实现的症状不是报错，而是一份
    已经按第 1 版写过的草稿从此指向一份它当时没见过的配置 —— 盘上看起来一切正常，
    而这是这一格唯一会坏的方式，所以必须有负例。

    与它配对的下一条（跳版）症状完全不同：那一条里没有任何一版被改写，只是版号
    中间空了一格。两条拒绝都在「写不进去」上留一样的痕迹，所以两条各钉一次。
    """
    wiring = deployment.build()
    run = driver(deployment, wiring)
    run(founding(), *published())

    outcome = run(
        (
            "put_writing_policy",
            {
                "policy_id": POLICY,
                "revision": 1,
                # 同一 (id, 版)，另一句话术。
                "generic_wording_id": "wording-generic-stage-1-rewritten",
            },
        )
    )

    text = _refused(outcome)
    assert "第 1 版已经在库里" in text, text
    assert "不覆盖" in text, text
    assert "请发布下一版" in text, (
        "拒绝必须说出下一步 —— 一个只报「不行」的接口会让人去改库:\n" + text
    )


def test_a_revision_that_skips_ahead_is_refused(deployment: Deployment) -> None:
    """跳版发布：**拒**，且报出接在该接的那一版之后。

    与上一条配对。这一条里没有任何一版的内容会被改写，所以它红的形态是另一件事：
    中间那些版号的去向没有答案，而报告上只显示「多了一个版本」。
    """
    wiring = deployment.build()
    run = driver(deployment, wiring)
    run(founding(), *published())

    outcome = run(
        (
            "put_family",
            {
                "family_id": FAMILY,
                "revision": 4,
                "stage": "stage-1",
                "role_class": "grounds_manager",
                "locale": "en-GB",
                "product": "natural-grass",
                "writing_policy_id": POLICY,
            },
        )
    )

    text = _refused(outcome)
    assert "最新的版本是第 1 版" in text, text
    assert "第 2 版" in text, "拒绝要说清楚本次应当是第几版:\n" + text


def test_publishing_before_the_market_opens_is_allowed(deployment: Deployment) -> None:
    """Scene 还**没有生效版本**时也发布得进去 —— 备货不必等市场开张。

    这条链上别的工具要 Scene 的生效版本（触达规则写在那一版上），发布不要：
    ``_plan_context`` 对一份 ``DRAFT`` 记录照样答得出归属，而 ``_rules_and_scene``
    那三句拒绝里的「还没有生效版本」会在这里拒掉一次**合法的**事前准备。

    所以这一条红的形态很具体：三条发布工具被接到那条更严的解析上，于是「先把
    话术备好、再开市场」这条顺序整个做不出来 —— 而它红的位置看起来像是权限问题。
    """
    wiring = deployment.build()
    outcome = driver(deployment, wiring)(
        founding(activate=False), *published()
    )

    founded = _ok(outcome, 0)
    assert founded["scene"]["effective_version"] is None, (
        "这一条的前提是 Scene 真的还没有生效版本 —— 否则它测的是别的"
    )
    assert _ok(outcome, 1)["stored"]["policy_id"] == POLICY
    assert _ok(outcome, 2)["stored"]["family_id"] == FAMILY


def test_a_published_revision_says_which_it_is_and_what_comes_next(
    deployment: Deployment,
) -> None:
    """回执里的 ``next_revision`` 与落库那一份的版号**都是服务端算出来的数**。

    第二版发布给的是 ``revision=2``，回执里的下一版是 3，落库那一份也是 2 ——
    三处必须一致。一个把 ``next_revision`` 写成 ``revision + 1`` 的实现在第一版
    上完全正确（1 → 2），所以这里发到第二版再看一次。
    """
    wiring = deployment.build()
    run = driver(deployment, wiring)
    run(founding(), *published())

    second = run(
        (
            "put_writing_policy",
            {
                "policy_id": POLICY,
                "revision": 2,
                "generic_wording_id": "wording-generic-stage-1-v2",
            },
        )
    )

    receipt = _ok(second)
    assert receipt["stored"]["revision"] == 2, receipt["stored"]
    assert receipt["stored"]["generic_wording_id"] == "wording-generic-stage-1-v2"
    assert receipt["next_revision"] == 3


# --- 八、草稿的可追溯性（§8） ---------------------------------------------------


#: §8 逐字点名的那张清单。**逐条列出来**，而不是在断言里散着写 —— 一条漏掉的格
#: 在「这一条绿了」上完全看不出来，而它就是这张清单存在的理由。
TRACE_FIELDS = (
    "draft_id",
    "account_id",
    "route_ref",
    "scene_id",
    "scene_version",
    "writer_id",
    "model_id",
    "family_id",
    "family_revision",
    "writing_policy_id",
    "writing_policy_revision",
    "facts_used",
    "evidence_ids",
    "subject_sha256",
    "body_sha256",
    "generated_at",
    "state",
)


def _draft_of(payload: dict[str, Any], account_id: str) -> dict[str, Any]:
    rows = [row for row in payload["drafts"] if row["account_id"] == account_id]
    assert len(rows) == 1, f"{account_id} 应当正好有一稿，实际 {rows}"
    return rows[0]


def test_a_draft_row_carries_the_whole_traceability_set(
    deployment: Deployment,
) -> None:
    """一稿自己回答得了「它是怎么做出来的」（§8）。

    逐格读的不是「有没有这一格」而是**它是不是那一件事**：``scene_version`` 是规则
    的版号（整数），``family_revision`` 是这一族当时的那一版，``evidence_ids`` 与
    ``facts_used`` 对得上，两个摘要与主题/正文对得上。

    ``model_id`` 是这一条里唯一一格**没有值**的，而它照样要在：本部署的写作者是
    确定性的，没有模型可报。一个把这一格省掉的实现会让 §8 的清单看起来已经齐了
    —— 而「本部署没有模型」与「这个字段没人想过」是两件下一步不同的事。
    """
    wiring = deployment.build()
    facts = {
        account_id: [["surface", "天然草", f"evidence-{account_id}"]]
        for account_id in ACCOUNTS
    }
    outcome = driver(deployment, wiring)(
        founding(), *published(), drafting(facts=facts)
    )
    drafted = _ok(outcome, 3)
    row = _draft_of(drafted, ACCOUNTS[0])

    missing = [name for name in TRACE_FIELDS if name not in row]
    assert not missing, f"§8 的清单里少了几格：{missing}"

    assert row["draft_id"], "draft_id 不得为空"
    assert row["account_id"] == ACCOUNTS[0]
    assert row["route_ref"], "route_ref 是发送侧找收件人的那一格，不得为空"
    assert row["scene_id"] == PROFILE
    assert row["scene_version"] == 1, (
        "这一稿是拿第 1 版 Scene 的规则写的 —— 整数版号，不是 profile 目录的字节摘要"
    )
    assert row["family_id"] == FAMILY
    assert row["family_revision"] == 1
    assert row["writing_policy_id"] == POLICY
    assert row["writing_policy_revision"] == 1
    assert row["writer_id"], "这一稿是谁写的，不得为空"

    assert row["model_id"] is None and row["model_unavailable"], (
        "本部署没有模型可报 —— 要报的是「这里没有」，并说明白为什么"
    )

    # ``facts_used`` 是 ``(事实名, 证据 id)`` —— **取值那一格不在里面**，而且这不是
    # 遗漏：取值是写作者报上来的一句话，证据是库里那一行。把取值抄进草稿等于给它
    # 一个与证据并列的地位，而下游要核的是「这一对引用存不存在」。
    assert row["facts_used"] == [["surface", f"evidence-{ACCOUNTS[0]}"]]
    assert row["evidence_ids"] == [f"evidence-{ACCOUNTS[0]}"], (
        "evidence_ids 是从 facts_used 推出来的那一格 —— 两者对不上就说明有一处是编的"
    )
    assert row["subject_sha256"] == sha256_text(row["subject"])
    assert row["body_sha256"] == sha256_text(row["body"])
    assert row["state"] == "generated"
    moment = datetime.fromisoformat(row["generated_at"])
    assert moment.tzinfo is not None, "生成时刻必须带时区"

    receipt = _receipt_of(drafted, row["draft_id"])
    assert receipt["writer_id"] == row["writer_id"], (
        "草稿上那一格与回执上那一格必须是同一个值 —— 两处各算一遍就迟早会漂"
    )
    assert receipt["subject_sha256"] == row["subject_sha256"], (
        "回执少一格主题摘要，一份只改了主题的回执在这一行上就与什么都没改的一样"
    )


def _receipt_of(payload: dict[str, Any], draft_id: str) -> dict[str, Any]:
    rows = [row for row in payload["receipts"] if row["draft_id"] == draft_id]
    assert len(rows) == 1, f"{draft_id} 应当正好有一份回执，实际 {rows}"
    return rows[0]


def test_a_draft_keeps_the_revision_it_was_written_under(
    deployment: Deployment,
) -> None:
    """发布第二版之后，**已经写好的那一稿仍然说得出它当时用的是第几版**（§7+§8）。

    这是那三张配置表在 v4 之前**做不到**的事：主键只有 id 一格时，第二版会覆盖
    第一版，而库里那条草稿记的正是那个 id —— 于是它指向一份自己没见过的配置，而
    盘上一切正常。所以这一条判据的两半都要：第二版发得进去（新配置能用），而第一
    版那一稿的**记录没被改写**。

    ## 为什么这里读了一次库

    验的是「产品写下去的那一行」而不是「回执上说的那一行」——两者在**写错**的实现
    上会不一样（回执照调用方的意图写，而库里那一行是另一回事）。这是**读**，
    不是绕开产品写库；写全部走 :func:`published` 与 ``draft_outreach``。
    """
    wiring = deployment.build()
    run = driver(deployment, wiring)
    run(founding(), *published())

    # 这一次调用是**自己的会话**（上一次的 `run` 是四发），所以起草是第 0 发。
    drafted = _ok(run(drafting()), 0)
    row = _draft_of(drafted, ACCOUNTS[0])
    assert row["writing_policy_revision"] == 1

    second = run(
        (
            "put_writing_policy",
            {
                "policy_id": POLICY,
                "revision": 2,
                "generic_wording_id": "wording-generic-stage-1-v2",
            },
        )
    )
    assert _ok(second)["stored"]["revision"] == 2, "第二版要发得进去"

    stored = SqliteOutreachStore(deployment.settings.database_path).draft(
        row["draft_id"], row["draft_version"]
    )
    assert stored is not None, "这一稿应当在库里"
    assert stored.writing_policy_revision == 1, (
        "第二版发布之后，这一稿记录的仍然是它当时读到的那一版 —— 历史修订不可改"
    )
    assert stored.writer_id == row["writer_id"]

    # 再起草一次：新的一版记的必须是**新发布的那一版**。少了这一半，「这一稿记的是
    # 它当时用的第几版」这句话就只剩一种实现——把常数 1 写进每一稿——而它在上一半
    # 上完全是绿的。
    again = _ok(run(drafting()), 0)
    second_row = _draft_of(again, ACCOUNTS[0])
    assert second_row["draft_version"] == 2, (
        "输入变了就该出新的一版，而不是就地改掉第 1 版"
    )
    assert second_row["writing_policy_revision"] == 2, (
        "这一次读到的是刚发布的第 2 版 —— 记成 1 的实现说明这一格是写死的"
    )
    assert second_row["supersedes"] == 1, "方向由新版本记录：它指向被它取代的那一版"
    assert _receipt_of(again, second_row["draft_id"])["superseded_version"] == 1


# --- 九、§B13：起草的产出对得上它的输入 ----------------------------------------
#
# 三格：**拒绝**（引了一条不属于这个账号的证据）、**读回来**（那一稿引的证据真的
# 在库里、真的是它的）、**对账**（``business`` 那一格与它下一层的清单对得上）。
# 三条都在同一个交界上：这一层的每一个结论最终都要落到「库里哪一行」上，而三条
# 各自能红的方式互不重叠 —— 一条只查拒绝、一条只查引用、一条只查条数。


def _facts_from(account_id: str, evidence_id: str) -> list[list[str]]:
    """一条给某个账号的事实行。``evidence_id`` 由调用方点名 —— 这一组判据要的
    正是「同一个形状、换一个证据 id」，所以取值与事实名都写死，只有那一格可变。"""
    return [["surface", "天然草", evidence_id]]


def _drafts_in(settings: Settings, wave_id: str) -> tuple[Any, ...]:
    """这批在库里**此刻**有几稿。用来判「整次被拒之后一行都没多」。"""
    return SqliteOutreachStore(settings.database_path).latest_drafts(wave_id=wave_id)


def test_a_fact_that_cites_another_account_s_evidence_is_refused_whole(
    deployment: Deployment,
) -> None:
    """§B13 逐字：「实体内容与证据关联验证**不能仅检查非空/hash**」。

    ## 这一条判的是**归属**，而形状那两条判据在它上面是绿的

    被拒的那一行形状完全正常：证据 id 非空，库里**真的有**这一行（所以摘要对得
    上），唯一不对的是那一行属于**另一个账号**。于是「查非空」与「查 hash」两种
    实现在它上面都不会红 —— 而放它过去，会让一个账号的判断出现在另一个账号的
    信里，本仓把虚假归属当作最严重的一类错误。

    ## 两半都要，而第二半才是这一条能说话的原因

    没有第一半，「整次被拒」可能只是这个工具对任何 ``facts`` 都拒；没有第二半，
    「被拒了」与「本来就写不出草稿」在读数上一样。所以先跑一次**引用对了**的，
    看它写出两稿；再跑**引用错了**的，看它整次被拒、而库里仍然是那两稿。

    ## 拒绝发生在起草**之前**，所以草稿一行都没多

    ``_checked_facts`` 在 ``generate_drafts`` 之前被调用（见 ``outreach.py`` 那一处
    的注释）。它**不**意味着这次调用什么都没写：活动那一行在它之前就落了
    （``_campaign``）—— 那是**声明**不是草稿，所以这里数的是草稿。

    ## 库里没有的那一条也在这一条里

    它与「属于别人」是两个不同的拒绝（两句不同的话），而同一条判据要同时盯住
    两者：只测其中一个的实现，会把另一个做成静默跳过。跳过比拒绝轻得多 ——
    一封信少一句话，而报告上完全看不出来。
    """
    wiring = deployment.build()
    good = {account_id: _facts_from(account_id, f"evidence-{account_id}") for account_id in ACCOUNTS}
    run = driver(deployment, wiring)
    run(founding(), *published())

    _ok(run(drafting(facts=good)), 0)
    assert len(_drafts_in(deployment.settings, WAVE)) == len(ACCOUNTS)

    for label, cited, said in (
        ("别人的证据", f"evidence-{ACCOUNTS[1]}", "属于"),
        ("库里没有的证据", "evidence-account-9999", "不存在"),
    ):
        bad = dict(good)
        bad[ACCOUNTS[0]] = _facts_from(ACCOUNTS[0], cited)
        refused = _refused(run(drafting(facts=bad)), 0)
        assert said in refused, f"{label}：拒绝的理由里要说得出是哪一种，实际：{refused!r}"
        assert len(_drafts_in(deployment.settings, WAVE)) == len(ACCOUNTS), (
            f"{label}：整次被拒，草稿一行都不该多"
        )


def test_the_evidence_a_draft_cites_really_is_in_the_store_and_is_its_own(
    deployment: Deployment,
) -> None:
    """一稿引的证据要**读得回来、且是这个账号的**（§B13 的另一半）。

    ## 为什么回执说的不算

    回执里的 ``evidence_ids`` 是从 ``facts_used`` 推出来的（见 ``_draft_row``），
    所以它说的是「写作者引用过什么」，不是「库里有没有那一行」。这两件事在
    ``_checked_facts`` 放行之后才相等，而产品环上读草稿的地方只有回执与清单 ——
    两处都说「引了 ``evidence-x``」而库里没有那一行时，报告上没有任何一处会红。
    所以这一条**站到库那一侧**再验一次，而不是再读一遍回执。

    ## 反面那一半：拿一个**同形但不存在的** id 问一次

    ``get_evidence`` 若不是真的在查表，这一条就退化成「非空即通过」—— 而那正是
    §B13 那句话点名不许的读法。同形的假 id 读不回来，才说明前面那个循环在查表。
    """
    wiring = deployment.build()
    facts = {account_id: _facts_from(account_id, f"evidence-{account_id}") for account_id in ACCOUNTS}
    outcome = driver(deployment, wiring)(founding(), *published(), drafting(facts=facts))
    drafted = _ok(outcome, 3)
    evidence = SqliteEvidenceStore(deployment.settings.database_path)

    for account_id in ACCOUNTS:
        row = _draft_of(drafted, account_id)
        assert row["evidence_ids"], "引用的证据不得为空 —— 空的那一稿在库里落不下来"
        for cited in row["evidence_ids"]:
            stored = evidence.get_evidence(cited)
            assert stored is not None, f"草稿引的证据 {cited!r} 在库里读不回来"
            assert stored.account_id == account_id, (
                f"{account_id} 那一稿引的是属于 {stored.account_id!r} 的证据 —— "
                "一次虚假归属"
            )
    assert evidence.get_evidence(f"evidence-{ACCOUNTS[0]}-surface") is None, (
        "同形但不存在的 id 必须读不回来，否则上面那个循环查的是『非空』而不是『在库里』"
    )


def test_the_business_row_reconciles_with_the_manifest_it_is_frozen_into(
    deployment: Deployment,
) -> None:
    """§B13 逐字：「以**冻结对象集**对账 …… 及**下一层写入**」。

    ## 两份回执放在一起算，各自单独读都不够

    ``business`` 是**起草**的产出，清单是它**下一层**的产出，中间隔着一次
    ``prepare_wave`` 的重算（抑制、回信、历史尝试都可能在两次之间变过）。所以
    「起草接受了两条、写了两行」与「清单里冻进去 0 行」可以同时为真，而两份回执
    各自都说得通 —— 这一条把两个工具的回执与 ``get_wave`` 读回来的那份清单放在
    一起算，三处必须指同一批人。

    ## 逐条对收件人，不只对条数

    条数对得上而人换了一个，是一份清单绑在另一批人身上的形状 —— 那种情况下
    ``allowed_count``、``rows_written`` 与 ``counts`` 三个数全都自洽。所以这里比的
    是**收件人集合**，并且逐行回到证据那一侧核一次归属：清单上的一行最终要说得出
    「凭什么发这封信」，而那一句的依据必须是这个账号的证据。
    """
    wiring = deployment.build()
    facts = {account_id: _facts_from(account_id, f"evidence-{account_id}") for account_id in ACCOUNTS}
    outcome = driver(deployment, wiring)(
        founding(), *published(), drafting(facts=facts), preparing(), ("get_wave", {"wave_id": WAVE})
    )
    business = _ok(outcome, 3)["business"]
    prepared = _ok(outcome, 4)
    wave = _ok(outcome, 5)
    entries = wave["entries"]

    assert business["counts"]["accepted"] == len(ACCOUNTS), business["counts"]
    assert business["rows_written"] == len(ACCOUNTS), (
        "这一趟真的往库里写了几行 —— 它与 `accepted` 在这一批里都该是两条"
    )
    assert prepared["allowed_count"] == business["rows_written"], (
        "起草说写了两行，冻出来的清单就该正好有两行"
    )
    assert prepared["plan"]["planned"] == business["counts"]["accepted"], (
        "筛选说进了两条、起草说接受了两条 —— 两个数分家说明中间有一处改了判断"
    )
    assert prepared["excluded"] == [], (
        "两条都写成了稿，清单里就不该有缺稿的候选"
    )

    accepted = {
        row["recipient"] for row in business["rows"] if row["disposition"] == "accepted"
    }
    assert {entry["recipient"] for entry in entries} == accepted, (
        "清单里的那批人必须正好是起草接受的那批人"
    )
    assert len(entries) == prepared["allowed_count"]

    evidence = SqliteEvidenceStore(deployment.settings.database_path)
    for entry in entries:
        assert entry["evidence_ids"], f"{entry['recipient']} 那一行没有依据"
        for cited in entry["evidence_ids"]:
            stored = evidence.get_evidence(cited)
            assert stored is not None, f"清单引的证据 {cited!r} 在库里读不回来"
            assert stored.account_id == entry["account_id"], (
                f"清单上 {entry['account_id']} 那一行引了属于 {stored.account_id!r} 的证据"
            )
