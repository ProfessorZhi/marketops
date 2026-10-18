"""飞书集成适配器 —— Bitable 的**写入路径**。

需求原文：「飞书暂时做 adapter，不阻塞 Demo。新飞书 Sandbox 应用申请完成后再接。」
本模块守的是**两个方向相反**的失败，缺任何一个都不是一条正确的实现：

1. **没有飞书凭据时，本地 Research Pipeline 照样跑完整条链路。** 所以未配置凭据
   → ``configured`` 为 False，推送是一个显式结果（``reason="feishu_not_configured"``），
   **不抛异常、不阻塞**。静默 no-op 会让「以为推出去了」这种事发生在几个月后。
2. **一次写入不得被报成成功，直到我们读回它。** 所以写入的答案不是「HTTP 201」，
   而是一张 :class:`BitableWriteReceipt`：它带着行 id、版本、写入前后的条数，
   以及失败发生在**哪一步、读到了什么**（§7）。

## 一条写入要走过哪几步

    授权 → 凭据 → 记录体（含正文上限） → 令牌 → 按业务键查 → 写入（新建/更新）
    → 再查一次并核对条数、行 id、版本

每一步失败都返回一张说得出「哪一步、读到的是什么」的回执。**没有任何一处
``except Exception: pass``**，也没有任何一处把失败写成成功 —— 后者在本模块里
最难发生的一种形态是「写了就报成功」，所以写入之后**必须**再查一次，
且条数不等于预期时那**是一次失败**（见 :meth:`FeishuBitableClient.push_handoff`）。

## 幂等：去重的键是**我们给的**，不是提供方的承诺

同一件事推两次不得在表里产生两行。做法是：先把 ``handoff_id`` 写进
:data:`~marketops.adapters.feishu.bitable.BITABLE_HANDOFF_KEY_COLUMN` 那一列，
再按它查 —— 查到 0 行就新建，查到 1 行就更新那一行，查到 2 行以上**拒绝写入**
（表里的重复已经存在，此时再写只会让它更糟）。**这个键是我们给的**
（``SalesHandoff.handoff_id``），提供方不认它 —— 真实提供方是否会自己做去重
本轮未经核实（见下），所以承诺由我们这一侧给，判据是「按这一列查、读到几行」。

## 对外写入需要**独立授权**，而它默认关、且不可由环境变量打开

门在 :attr:`FeishuBitableClient.write_authorized`，取值来自
``Settings.feishu_write_authorized`` —— 模块级常量 + 只读属性，与
``send_authorized`` 逐字同型：不读 ``os.environ``、不是任何函数的参数、没有
setter。两者**独立存在**（解除的条件不同：一个是「这封信可以发了」，一个是
「研究产出可以写进一个别人看得见的表了」）。

未授权时这条路径**存在且可调用**：它返回一张说得出「缺的是哪一份授权」的回执，
**不是** ``NotImplementedError``、**不是** ``interface_only``、**也不是**一个空函数
（§9 逐字：缺少生产凭据只阻塞真实部署验收）。它同样**一次 HTTP 都不发**。

## 真实飞书写入 = BLOCKED（缺三样，不是缺一样）

:data:`LIVE_WRITE_STATE` 与 :data:`LIVE_WRITE_BLOCKERS` 就是这句话的执行器：
业务授权缺、凭据缺，且这条路**从未对厂商执行过一次**。三者缺一，写入都不发生 ——
前两样是缺东西，第三样说的是「没有一次执行过，所以通过与失败今天不可知」。因此本模块
的测试**全部是隔离的**：它们用一个同进程的传输替身（``httpx.MockTransport``）跑完整条
路径，一次网络都不发，测试名里带 ``sandbox_pass`` 的那几条说的就是这件事 ——
**隔离环境的通过**，不是真实厂商的通过。把隔离的通过报成真实厂商的 PASS 会让
「接线还没验过」这件事在报告上消失。

（本模块**没有**一个「本机到不了厂商」的断言：DNS 名字解析是能解出来的。
缺的是授权、凭据与一次真实执行，不是网络。）

## 本模块**不**做的事

* 不重试、不续期令牌、不做限流退避。``docs/NOT_BUILT.md`` 明写「重试 / 限流策略
  未定 —— 等真实 API 配额」，所以一次失败就是一次失败，重跑由调用方决定。
* 不截断任何正文。放不下就拒写（见 ``bitable.oversized_text_fields``）。
* 不回写 ``external_row_id`` / ``external_version`` 两列 —— 值由调用方拿回执
  填进本地那条 ``SalesHandoff``，回写会让一次写入变成两次，而第二次的版本
  又变了一次。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from ...config import Settings
from ...domain.entities import ContactRoute, RouteOpportunity
from ...domain.outreach import HandoffState, SalesHandoff
from .bitable import (
    BITABLE_HANDOFF_KEY_COLUMN,
    MAX_BITABLE_TEXT_CHARS,
    oversized_text_fields,
    to_bitable_handoff_record,
)

#: 本轮接线状态。**逐种记录分开**（见 :data:`RECORD_KIND_STATE`），因为把三条记录
#: 挤进一个常量之后，「handoff 推得了、路线推不了」这个问题在盘上没有答案 ——
#: 而两者的处置完全不同：前者缺一条可判定的业务键（一个**业务决定**），
#: 后者已经做完（一个**实现事实**）。
ADAPTER_STATE = "partially_wired"

#: 逐种记录的写入状态。取值本身是一个句子，不是一个布尔 ——
#: 「没接线」有几种不同的原因，而它们要做的事不一样。
RECORD_KIND_STATE: dict[str, str] = {
    "handoff": "wired",
    "route": "no_business_key_declared",
    "opportunity": "no_business_key_declared",
}

#: 对**真实**飞书租户那一次写入的当前状态与缺的东西。
#:
#: 做成常量而不是一句注释，是为了让「BLOCKED」在报告里是一个**可被读到的取值** ——
#: 一句写在文档里的 BLOCKED 会在第三次改版的时候消失，而取值的消失会被人看见。
LIVE_WRITE_STATE = "BLOCKED"

#: 上面那个状态缺的具体三样。**三样都要列全**：只说「缺凭据」会让运维补完凭据之后
#: 撞上另外两堵墙，而每一次都以为快好了。
#:
#: 前两样是**缺东西**。第三样不是 —— 它说的是「这条路从未对厂商走过一次」，因此
#: 真实写入的通过与失败今天**不可知**，只能报 BLOCKED。
#:
#: 曾经写成 ``no_route_to_the_vendor_from_this_sandbox``，那是**没量过就写下的**：
#: 2026-09-24 实测本机 DNS 能把 ``open.feishu.cn`` 解出地址（只做了名字解析这一个
#: 观察，**没有**发任何请求去验连通性 —— 本轮明确不发）。一个「没有路由」的断言如果
#: 是错的，它的害处不是保守，而是让下一个人以为「本机根本到不了」从而不去查真正的
#: 拦路虎；一个「没验过」的断言则永远不会说错话。
LIVE_WRITE_BLOCKERS: tuple[str, ...] = (
    "missing_business_write_authorization",
    "missing_credentials",
    "never_exercised_against_the_vendor",
)

#: 飞书开放平台的入口。**常量，不读环境变量**：一个可配置的基地址意味着「写到哪里」
#: 取决于一份配置，而这份配置的错法（指向另一个域名）不会报任何错。
_API_BASE = "https://open.feishu.cn"

_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"

#: 一次写入回执里的**步骤名**。它回答「哪一步失败」—— 一个只说「失败了」的回执，
#: 读的人分不清是授权没开、还是对面 500、还是写完读回来的不是那一行。
STEP_AUTHORIZATION = "authorization"
STEP_CONFIGURATION = "configuration"
STEP_SERIALISATION = "serialisation"
STEP_TOKEN = "token"
STEP_QUERY = "query"
STEP_WRITE = "write"
STEP_READBACK = "readback"

WRITE_STEPS: tuple[str, ...] = (
    STEP_AUTHORIZATION,
    STEP_CONFIGURATION,
    STEP_SERIALISATION,
    STEP_TOKEN,
    STEP_QUERY,
    STEP_WRITE,
    STEP_READBACK,
)

#: 按业务键检索时**只要两行**：第一行是我们要找的那一行，第二行是「表里已经有两行」
#: 这件事本身。要更多行没有用途，而一次把整表拉回来的检索会在真实表上变成一个
#: 分页问题 —— 那个问题的处置（翻页）与去重无关。
_SEARCH_PAGE_SIZE = 2

#: 我们从提供方的记录对象上认的版本字段。
#:
#: **这是一份待核实的假设，不是一条契约**：真实写入是 BLOCKED 的，所以「提供方到底
#: 回不回版本」没有人核实过。它的失效方式被刻意设计成安全的 —— 读不到就是空串，
#: 回执里写「提供方没有回版本号」，而**不是**合成一个（拿时间戳或内容摘要冒充版本号，
#: 会让 ``external_version`` 变成一个每次都不一样的东西，于是「这一行变过没有」
#: 这个问题永远得到「变过」）。
_VERSION_KEY = "version"


@dataclass(frozen=True)
class PushResult:
    """批量推送的结果。永远是显式的 —— 成功、跳过、未接线各有自己的 reason。"""

    pushed: int = 0
    skipped: int = 0
    reason: str = ""
    #: 为什么是这个 reason。**可有可无**，但一个拒写总该说得出缺什么：
    #: 只报 ``record_kind_not_wired`` 的回执，读的人不知道是缺代码还是缺一个决定。
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason


@dataclass(frozen=True)
class BitableWriteReceipt:
    """**一行**写入的回执。成功与失败是同一种对象 —— 于是「成功」也有内容可读。

    §7 要求「行 id/版本/条数读回与失败回执」：行 id 与版本回答「写成了哪一行」，
    条数回答「读回来的东西是不是我们以为的那一件」，失败回执回答「哪一步、读到
    什么」。四样都是字段，不是日志里的某一句话。

    ``ok`` 为真时 :attr:`step` 是**最后走完的那一步**（``readback``）；
    为假时它是**失败发生的那一步**。两者是同一个字段，因为「走到了哪一步」
    与「在哪一步停下的」本来就是同一句话的两种读法。
    """

    record_kind: str
    #: 这一次写入用的业务键，以及它在表里的列名。**去重靠的就是它**，
    #: 所以它必须出现在回执里：一次「没有产生第二行」的结论要能指出比的是哪一格。
    business_key: str = ""
    key_column: str = ""
    ok: bool = False
    step: str = ""
    reason: str = ""
    detail: str = ""
    row_id: str = ""
    version: str = ""
    count_before: int | None = None
    count_after: int | None = None
    expected_count: int | None = None
    #: 这一次是**新建**还是**更新**。幂等这个性质的可观测形态就是它：
    #: 同一件事推两次，第二次必须是 ``created=False``。
    created: bool = False

    def as_public_dict(self) -> dict[str, object]:
        """用于日志 / 报告的安全投影。回执里本来就不该有凭据 —— 这一层是底线。"""
        return {
            "record_kind": self.record_kind,
            "business_key": self.business_key,
            "key_column": self.key_column,
            "ok": self.ok,
            "step": self.step,
            "reason": self.reason,
            "detail": self.detail,
            "row_id": self.row_id,
            "version": self.version,
            "count_before": self.count_before,
            "count_after": self.count_after,
            "expected_count": self.expected_count,
            "created": self.created,
        }

    def notified_handoff(self, handoff: SalesHandoff) -> SalesHandoff:
        """把读回来的东西**填进**一条新的 ``SalesHandoff``（``state=NOTIFIED``）。

        它只做映射，不做写入 —— 于是「读回来的行 id 与版本进了哪一格」是一个可测的
        纯函数，而不是一句写在调用点里的约定。

        **失败的写入调用它会抛**：一次没读回来的写入产出一条 ``NOTIFIED`` 交接，
        就是 §7 要挡的那件事（通知没出去，而记录说通知了）。用
        ``model_validate`` 而不是 ``model_copy(update=...)``：后者**不校验**，
        于是一条自相矛盾的交接（说通知了、却没有行 id）能被构造出来。
        """
        if not self.ok:
            raise ValueError(
                f"这一次写入没有成功（{self.step}/{self.reason}），不得据此把交接标成"
                f" {HandoffState.NOTIFIED.value}。\n"
                "``NOTIFIED`` 是「推给外部系统并拿到了写回确认」这件事的投影，"
                "而这份回执没有那个确认。"
            )
        if not self.row_id:
            raise ValueError(
                "回执是成功的，却没有行 id —— 这是一条自相矛盾的回执，"
                "不得用它推进任何状态。"
            )
        return SalesHandoff.model_validate(
            {
                **handoff.model_dump(),
                "state": HandoffState.NOTIFIED,
                "external_row_id": self.row_id,
                "external_version": self.version,
            }
        )


@dataclass(frozen=True)
class _Lookup:
    """一次按业务键的检索**读到的东西**。

    与回执分开，因为它回答的是「查这一步看到了什么」，而回执回答的是「整次写入
    是什么结果」。合并两者会让「查到 2 行」这件事在成功与失败的两种读法里
    各有一个形状。
    """

    #: 提供方回的匹配条数。``None`` = **没读到条数** —— 那不是 0。
    total: int | None = None
    record_id: str = ""
    version: str = ""
    error: str = ""


class FeishuSink(ABC):
    """飞书侧落库接口。换实现（Bitable / 自建服务）时 pipeline 不需要改。"""

    @abstractmethod
    def push_routes(self, routes: list[ContactRoute]) -> PushResult:
        """推送联系路线。**不得**因集成不可用而中断调用方。"""

    @abstractmethod
    def push_opportunities(self, opportunities: list[RouteOpportunity]) -> PushResult:
        """推送未解决的路线机会。与路线分开，见 bitable 的列结构说明。"""

    @abstractmethod
    def push_handoff(self, handoff: SalesHandoff) -> BitableWriteReceipt:
        """推送**一条**销售交接，并读回行 id / 版本 / 条数。

        ``SalesHandoff`` 是这里唯一的单位：交接的去重键就是 ``handoff_id``，
        而它本来就是「一条交接」的标识 —— 批量接口会逼出一个「这一批的键是什么」
        的问题，而那个问题在 §7 里没有对应物。
        """

    @property
    @abstractmethod
    def configured(self) -> bool:
        """凭据是否齐备。"""

    @property
    @abstractmethod
    def write_authorized(self) -> bool:
        """对外写入的**独立**授权是否在位。本阶段恒为 False。"""
        # 说明为什么它不是 ``configured`` 的一部分：配了凭据只说明「我们可以出示
        # 身份」，与「我们被允许往那张表里写」是两件事，而后者是一个业务决定。


class FeishuBitableClient(FeishuSink):
    """多维表格实现。

    Args:
        settings: 提供 app_id / app_secret / bitable app token / table id，
            以及那个**独立的写入授权位**（``feishu_write_authorized``）。
        client: 测试注入的 ``httpx.Client``。注入点存在是**为了证明未授权时一次
            HTTP 都不发**：注入一个「被调用就炸」的传输替身，那条测试才有判别力。
    """

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
        )
        #: 令牌缓存。**每个实例一份**，不落模块级 —— 一个模块级缓存会在测试之间
        #: 串味，而串味的表现是「第二条测试用着第一条的令牌却不报错」。
        self._token = ""

    # ------------------------------------------------------------------
    # 两个门：凭据（能不能出示身份）与授权（允不允许写）
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self._settings.feishu_enabled and bool(
            self._settings.feishu_bitable_app_token and self._settings.feishu_bitable_table_id
        )

    @property
    def write_authorized(self) -> bool:
        """见 ``Settings.feishu_write_authorized``：模块级常量，不可由 env 打开。"""
        return self._settings.feishu_write_authorized

    # ------------------------------------------------------------------
    # 未接线的两种记录：拒绝，但说得出缺的是**什么**
    # ------------------------------------------------------------------
    def push_routes(self, routes: list[ContactRoute]) -> PushResult:
        return self._push(len(routes), kind="route")

    def push_opportunities(self, opportunities: list[RouteOpportunity]) -> PushResult:
        return self._push(len(opportunities), kind="opportunity")

    def _push(self, count: int, *, kind: str) -> PushResult:
        if not self.configured:
            return PushResult(skipped=count, reason="feishu_not_configured")
        state = RECORD_KIND_STATE[kind]
        if state != "wired":
            return PushResult(
                skipped=count,
                reason=f"{kind}_not_wired",
                detail=(
                    f"{kind} 这一种记录没有接线的**原因不是代码没写**，而是它没有一条"
                    "可判定的业务键：同一件事推两次会不会产生两行，这个问题今天答不出来。\n"
                    "一条路线（或机会）的身份由账号、人、角色与邮箱共同决定"
                    "（``ContactRoute``），而本仓没有任何一个字段是它的稳定标识 ——"
                    "拿「正文摘要」当键会把一次重新定级变成两行，"
                    "拿「账号 + 邮箱」当键会把两条不同的路线合成一条。\n"
                    "这是一个业务决定，不是一次实现；定下来之前不写。"
                ),
            )
        return PushResult(
            skipped=count,
            reason=f"{kind}_not_wired",
            detail=(
                f"``RECORD_KIND_STATE[{kind!r}]`` 是 {state!r}，而本方法里没有它的写入"
                "实现 —— 状态表与实现不一致，这条拒写是那个不一致在盘上的样子。"
            ),
        )

    # ------------------------------------------------------------------
    # §7：一条交接的写入路径
    # ------------------------------------------------------------------
    def push_handoff(self, handoff: SalesHandoff) -> BitableWriteReceipt:
        """写一条交接并读回。**四张失败回执，一张成功回执。**

        顺序刻意是「授权 → 凭据 → 正文 → 令牌 → 查 → 写 → 读回」：授权在最前，
        因为它是唯一一个**运维补不了**的缺项 —— 凭据可以申请，而「允许往那张表里
        写」是一个业务决定。把它排在后面，会让操作员在补完凭据之后撞上一堵他打不开
        的墙，而之前每一次都以为快好了。
        """
        record = to_bitable_handoff_record(handoff)
        receipt_fields: dict[str, Any] = {
            "record_kind": "handoff",
            "business_key": handoff.handoff_id,
            "key_column": BITABLE_HANDOFF_KEY_COLUMN,
        }

        if not self.write_authorized:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_AUTHORIZATION,
                reason="feishu_write_not_authorized",
                detail=(
                    "缺的是**业务写入授权**，不是凭据："
                    "``Settings.feishu_write_authorized``（``marketops/config.py`` 的 "
                    "``_FEISHU_WRITE_AUTHORIZED``）本阶段恒为 False。\n"
                    "它刻意不可由环境变量打开、不是任何函数的参数、没有 setter；"
                    "打开它是一次业务批准，不是一次配置改动。\n"
                    f"真实飞书写入当前状态：{LIVE_WRITE_STATE}（缺 "
                    f"{'、'.join(LIVE_WRITE_BLOCKERS)}）。"
                ),
            )

        if not self.configured:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_CONFIGURATION,
                reason="feishu_not_configured",
                detail=(
                    "缺的是**凭据**：app_id / app_secret / bitable app token / table id "
                    "四样没有配齐（本仓不读、也不存它们 —— 见 ``docs/DEPLOYMENT.md``）。"
                ),
            )

        oversized = oversized_text_fields(record)
        if oversized:
            # 报出**实际长度**，不只是列名：一个只说「Reply Body 超了」的回执，读的人
            # 没法决定是拆分还是让人读原件 —— 41 万字符和 100001 字符是两种处置。
            measured = "、".join(f"{column}={len(record[column])}" for column in oversized)
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_SERIALISATION,
                reason="text_field_over_limit",
                detail=(
                    f"这些列超过了 {MAX_BITABLE_TEXT_CHARS} 字符：{measured}。\n"
                    "**不截断**：一封被切掉结尾的回信，读的人看不出少了什么，"
                    "他只会读到一封语气怪异的信。放不下就拒写，并由人决定怎么处置"
                    "（拆开？还是让他去读原件？）。"
                ),
            )

        token, error = self._tenant_token()
        if error:
            return BitableWriteReceipt(
                **receipt_fields, step=STEP_TOKEN, reason="feishu_token_failed", detail=error
            )

        before = self._lookup(BITABLE_HANDOFF_KEY_COLUMN, handoff.handoff_id, token=token)
        if before.error:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_QUERY,
                reason="feishu_query_failed",
                detail=before.error,
            )
        if before.total is None:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_QUERY,
                reason="feishu_query_total_missing",
                detail=(
                    "提供方回了记录，却没有回**条数** —— 读不出条数就核对不了条数，"
                    "而「核对不了」与「对得上」不是一回事。"
                ),
            )
        if before.total > 1:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_QUERY,
                reason="duplicate_rows_for_business_key",
                count_before=before.total,
                expected_count=1,
                detail=(
                    f"表里按 {BITABLE_HANDOFF_KEY_COLUMN}={handoff.handoff_id} 查到 "
                    f"{before.total} 行，而预期最多 1 行。\n"
                    "此时**不得**写入：去重的前提已经被破坏（有人手工复制过行，"
                    "或者前一次推送真的写重了），再写一次只会让它更糟。"
                ),
            )

        if before.total == 1:
            wrote = self._write(
                "PUT", self._records_path(before.record_id), body={"fields": record}, token=token
            )
            created = False
        else:
            wrote = self._write("POST", self._records_path(), body={"fields": record}, token=token)
            created = True
        if wrote.error:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_WRITE,
                reason="feishu_write_failed",
                detail=wrote.error,
                count_before=before.total,
                expected_count=1,
                created=created,
            )
        if not wrote.record_id:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_WRITE,
                reason="write_receipt_missing_row_id",
                detail=(
                    "写入的响应里没有 ``record_id`` —— 也就是说我们**说不出**写到了哪一行。"
                    "没有行 id 的写入不得被当成成功：那条记录此后无法被更新，"
                    "也无法被核对。"
                ),
                count_before=before.total,
                expected_count=1,
                created=created,
            )

        after = self._lookup(BITABLE_HANDOFF_KEY_COLUMN, handoff.handoff_id, token=token)
        if after.error:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_READBACK,
                reason="feishu_readback_failed",
                detail=after.error,
                row_id=wrote.record_id,
                count_before=before.total,
                expected_count=1,
                created=created,
            )
        if after.total is None:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_READBACK,
                reason="feishu_readback_total_missing",
                detail="写完再查，提供方没有回条数 —— 这一次写入**不算成功**，因为没见过它。",
                row_id=wrote.record_id,
                count_before=before.total,
                expected_count=1,
                created=created,
            )
        if after.total != 1:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_READBACK,
                reason="readback_count_mismatch",
                detail=(
                    f"写完再按 {BITABLE_HANDOFF_KEY_COLUMN}={handoff.handoff_id} 查，"
                    f"读到 {after.total} 行，而预期恰好 1 行。\n"
                    "**这一次不算成功。** 写入之前的 HTTP 结果是 2xx，而「写了就报成功」"
                    "与「这一次真的写成了」之间的差别，正是这一格。"
                ),
                row_id=wrote.record_id,
                count_before=before.total,
                count_after=after.total,
                expected_count=1,
                created=created,
            )
        if after.record_id != wrote.record_id:
            return BitableWriteReceipt(
                **receipt_fields,
                step=STEP_READBACK,
                reason="readback_row_id_mismatch",
                detail=(
                    f"写回的是 {wrote.record_id}，而按同一个键查回来的是 "
                    f"{after.record_id} —— 条数对得上，行却换了一行。\n"
                    "这比条数不对更危险：它说明我们去重的那个键在这张表上不唯一。"
                ),
                row_id=wrote.record_id,
                count_before=before.total,
                count_after=after.total,
                expected_count=1,
                created=created,
            )

        return BitableWriteReceipt(
            **receipt_fields,
            ok=True,
            step=STEP_READBACK,
            detail=(
                ""
                if after.version
                else "提供方没有回版本号，``external_version`` 因此留空（**不合成**）。"
            ),
            row_id=after.record_id,
            version=after.version,
            count_before=before.total,
            count_after=after.total,
            expected_count=1,
            created=created,
        )

    # ------------------------------------------------------------------
    # HTTP。**所有出网动作都在下面这几个函数里**
    # ------------------------------------------------------------------
    def _records_path(self, record_id: str = "") -> str:
        table = (
            f"/open-apis/bitable/v1/apps/{self._settings.feishu_bitable_app_token}"
            f"/tables/{self._settings.feishu_bitable_table_id}/records"
        )
        return f"{table}/{record_id}" if record_id else table

    def _tenant_token(self) -> tuple[str, str]:
        """取（并缓存）tenant_access_token。返回 ``(token, error)``。

        令牌只活在内存里，**不进任何回执、任何日志、任何异常消息** ——
        :meth:`_safe_text` 是这条纪律的执行器。
        """
        if self._token:
            return self._token, ""
        payload, error = self._request(
            "POST",
            _TOKEN_PATH,
            body={
                "app_id": self._settings.feishu_app_id,
                "app_secret": self._settings.feishu_app_secret,
            },
        )
        if error:
            return "", error
        assert payload is not None  # _request 的契约：error 为空时 payload 必然非空
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            return "", "提供方的令牌响应里没有 ``tenant_access_token``"
        self._token = token
        return token, ""

    def _lookup(self, key_column: str, key: str, *, token: str) -> _Lookup:
        """按业务键查一次。``total`` 是**提供方回的条数**，不是 ``len(items)``。

        两者的差别就是「这一批有多少行」这个问题的答案：items 是这一页，
        total 是匹配的全部。拿 ``len(items)`` 当条数，会让一条被分页截断的检索
        看起来「只有一页那么多行」—— 而那个数正好等于我们要核对的那个数。
        """
        payload, error = self._request(
            "POST",
            f"{self._records_path()}/search",
            body={
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": key_column, "operator": "is", "value": [key]}
                    ],
                },
                # 自动字段（创建时间之类）与我们的判据无关，关掉它让响应只带我们
                # 要核对的东西。
                "automatic_fields": False,
                "page_size": _SEARCH_PAGE_SIZE,
            },
            token=token,
        )
        if error:
            return _Lookup(error=error)
        assert payload is not None
        data = payload.get("data")
        if not isinstance(data, dict):
            return _Lookup(error="提供方的检索响应里没有 ``data``")
        # 「响应里没有 total 这一格」与「total 不是整数」是**两件事**，分开返回：
        # 前者是 ``total=None``（调用方据此报「读不出条数」），后者是一句话的错误。
        # 把它们合成一个 error 会让 ``push_handoff`` 里那两张说得出「读不出条数」的
        # 回执变成不可达的死代码 —— 而「核对不了」与「对得上」的区别正是要说出来的那件事。
        total = data.get("total")
        missing_total = total is None
        if not missing_total and (not isinstance(total, int) or isinstance(total, bool)):
            return _Lookup(error="提供方的检索响应里 ``total`` 不是一个整数")
        items = data.get("items")
        if not isinstance(items, list):
            return _Lookup(error="提供方的检索响应里 ``items`` 不是一个列表")
        first = items[0] if items and isinstance(items[0], dict) else {}
        return _Lookup(
            total=None if missing_total else total,
            record_id=str(first.get("record_id", "")),
            version=_read_version(first),
        )

    def _write(
        self, method: str, path: str, *, body: dict[str, Any], token: str
    ) -> _Lookup:
        """发一次写入，读回提供方给的行 id。**不判断成功** —— 判据在读回那一步。"""
        payload, error = self._request(method, path, body=body, token=token)
        if error:
            return _Lookup(error=error)
        assert payload is not None
        data = payload.get("data")
        record = data.get("record") if isinstance(data, dict) else None
        if not isinstance(record, dict):
            return _Lookup(error="写入的响应里没有 ``data.record``")
        return _Lookup(record_id=str(record.get("record_id", "")), version=_read_version(record))

    def _request(
        self, method: str, path: str, *, body: dict[str, Any], token: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        """发一次请求。返回 ``(payload, error)``，**error 非空时 payload 必然是 None**。

        错误一律是**一句话**而不是异常：调用方要把它放进回执里，而回执是一份
        给人读的东西。异常会在调用栈上被丢掉，回执不会。
        """
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._client.request(
                method, f"{_API_BASE}{path}", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            return None, f"传输层失败：{type(exc).__name__}: {self._safe_text(str(exc))}"
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            return None, f"HTTP {response.status_code}，而响应不是 JSON"
        if not isinstance(payload, dict):
            return None, f"HTTP {response.status_code}，而响应不是一个 JSON 对象"
        code = payload.get("code")
        if code != 0:
            return None, (
                f"提供方 code={code} msg={self._safe_text(str(payload.get('msg', '')))}"
            )
        return payload, ""

    def _safe_text(self, value: str) -> str:
        """截断并按**已知的**秘密做脱敏。

        提供方的 ``msg`` 是我们读不懂的一段外部文本，而它有可能把请求里的东西
        原样回显 —— 其中就有 ``app_secret`` 与令牌。所以：一切来自响应体的文本
        在进入回执之前都过这一道。截到 200 字符是因为回执要给人读到底，
        而不是因为长度本身有什么讲究。
        """
        text = value[:200]
        for secret in (self._settings.feishu_app_secret, self._token):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text


def _read_version(record: dict[str, Any]) -> str:
    """从提供方的一条记录里读出**版本**。读不到就是空串。

    见 :data:`_VERSION_KEY` 的说明：这是一份待核实的假设，而它的失效方式是安全的。
    """
    version = record.get(_VERSION_KEY)
    if version is None:
        return ""
    return str(version)


__all__ = [
    "ADAPTER_STATE",
    "BitableWriteReceipt",
    "FeishuSink",
    "FeishuBitableClient",
    "LIVE_WRITE_BLOCKERS",
    "LIVE_WRITE_STATE",
    "PushResult",
    "RECORD_KIND_STATE",
    "WRITE_STEPS",
]
