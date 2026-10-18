"""驱动 MarketOps MCP 服务面的测试脚手架。

## 为什么是真的走一遍，而不是直接调工具函数

一个被直接调用的工具函数**绕过**了整条权限链：中间件不在、认证上下文不在、
``tools/list`` 的过滤也不在。用那种方式写出来的隔离测试，在权限被整段删掉
之后依然全绿 —— 而它测的恰好是本阶段唯一承重的东西。

所以这里起一个**真的**服务端：真的 ``MCPServer``、真的 Streamable HTTP 应用、
真的 ``BearerAuthBackend``、真的 ``AuthContextMiddleware``，客户端用 SDK 自己的
``streamable_http_client`` + ``ClientSession``。

## 为什么没有 socket

``httpx2.ASGITransport`` 把请求直接送进 ASGI 应用，令牌走的是真实的
``Authorization`` 头，经过的是真实的认证中间件 —— 只是没有内核网络栈参与。
于是这些测试可以在 CI 上跑，也不会占端口、不会与同一台机器上别人的服务抢
8787。唯一必须显式做的是**跑 lifespan**：会话管理器的事件循环在 lifespan
里起，漏掉它得到的是 ``Task group is not initialized``，一个与权限毫无关系
的错误信息。

## 它捕获异常而不是抛出

「没有令牌」这一类用例的**期望结果**就是握手失败。让脚手架把传输层错误
一并收进返回值，测试才能对两条路径（传输失败 / 调用被拒）下断言 ——
而这两条路径的区别本身是需要被钉住的：它们不该被混为一谈。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from marketops.config import Settings
from tests.conftest import DEFAULT_PROFILE

#: 测试用的服务地址。``MCP_URL`` **就是**缺省配置下服务端认定的资源指示符
#: （``server.resource_identifier`` 在没有 ``MARKETOPS_MCP_RESOURCE`` 时按绑定
#: 地址推导），因此它同时是环里受众应当取的值 —— 两处写成一个来源，一次
#: 「测试铸的受众与服务端等的不一样」就不会伪装成「认证配错了」。
BASE_URL = "http://127.0.0.1:8787"
MCP_PATH = "/mcp"
MCP_URL = f"{BASE_URL}{MCP_PATH}"

#: 测试环里条目的受众：缺省服务端认定的那一个。需要**错配**的用例显式传别的值
#: （``expires_at`` 同理），所以这两个参数在 :func:`ring_entry` 上是关键字参数。
RING_AUDIENCE = MCP_URL

#: 测试环里条目的失效时刻：一个远在未来的固定时刻。
#:
#: 固定而不是「现在 + N」：这些用例没有一条在测有效期（那几条在
#: ``test_security_tokens.py`` 与 ``test_mcp_auth_interop.py``，它们自己写时刻），
#: 而一个随运行时刻漂移的值会让「同一个环文件的两次运行」不可比对。
#: 它是**一个取值**，不是一条策略 —— 策略的上界在 ``mint-token --expires-in`` 上。
RING_EXPIRES_AT = "2999-01-01T00:00:00Z"

#: 测试环里条目的工作区：**测试用的那个 profile**（``conftest.DEFAULT_PROFILE``），
#: 也就是那些用例起的服务实例认作自己 Scene 的那一个。
#:
#: 取同一个来源而不是再写一遍字符串：写两遍的话，一次「测试铸的工作区与服务端
#: 服务的 Scene 不一样」会让整个文件里的用例全部变成「明明配了令牌却被拒」——
#: 而两条路径的报错长得很像，排查方向会落在认证上。需要**错配**的用例显式传别的
#: 值，所以这个参数在 :func:`ring_entry` 上是关键字参数。
RING_WORKSPACE = DEFAULT_PROFILE


def ring_entry(
    digest: str,
    principal: str,
    roles: Any,
    *,
    audience: str = RING_AUDIENCE,
    workspaces: Any = (RING_WORKSPACE,),
    expires_at: str = RING_EXPIRES_AT,
) -> dict[str, Any]:
    """令牌环里的一条授权（格式 3）。

    放在这里而不是让每个测试自己拼 dict：格式升级过两次，而有 11 个文件在拼它 ——
    那两次每处都要改，改漏的一处**不会报错**，只会让那个文件里的用例全部变成
    「明明配了令牌却 401」。合成一处之后，下次升级只改这里。
    """
    from marketops.security.catalog import Role

    return {
        "digest": digest,
        "principal": principal,
        "roles": [r.value if isinstance(r, Role) else str(r) for r in roles],
        "audience": audience,
        "workspaces": list(workspaces),
        "expires_at": expires_at,
    }


def ring_payload(*entries: dict[str, Any]) -> dict[str, Any]:
    """整个环文件的内容。格式版本号在测试里只出现这一次。"""
    return {"version": 3, "tokens": list(entries)}

#: 握手与本轮调用的总预算。超时即失败 —— 一个挂住的测试比一个红的测试更贵。
_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class CallOutcome:
    """一次 ``tools/call`` 的观察结果。"""

    name: str
    is_error: bool
    text: str
    structured: Any = None

    def as_json(self) -> Any:
        """把结果读成结构化数据。

        工具返回 dict，SDK 会同时给出 ``structuredContent`` 与一段 JSON 文本。
        优先读结构化的那一份；退回到解析文本，是为了在 SDK 改变呈现方式时
        测试仍然测的是**内容**而不是序列化细节。
        """
        if self.structured is not None:
            return self.structured
        text = self.text.strip()
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return None


@dataclass
class SessionOutcome:
    """一整段会话（一次握手 + 若干次调用）。"""

    tool_names: tuple[str, ...] = ()
    calls: tuple[CallOutcome, ...] = ()
    handshake_error: str = ""
    notes: list[str] = field(default_factory=list)
    #: 这一段会话里到达的**每一个** HTTP 状态码，按到达顺序。
    #:
    #: 为什么要记：客户端 SDK 把「401 被拒」与「服务端 500」都摊成同一句
    #: ``MCPError: Server returned an error response``。只看那句话，一条
    #: 「传输层确实拒绝了你」的断言就无法与「服务端炸了」区分 —— 而那两者
    #: 的排查方向完全不同。状态码是这条路径上唯一没有被转述过的事实。
    statuses: tuple[int, ...] = ()

    def call(self, name: str) -> CallOutcome:
        for outcome in self.calls:
            if outcome.name == name:
                return outcome
        raise AssertionError(f"本次会话没有调用过 {name!r}；调用过的是 {[c.name for c in self.calls]}")


class _RecordingTransport(httpx2.AsyncBaseTransport):
    """包住 ``ASGITransport``，把每一个响应状态码记下来。

    只是记录：请求与响应原样透传，所以被测的行为一个字节都没变。
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport, sink: list[int]) -> None:
        self._inner = inner
        self._sink = sink

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        self._sink.append(response.status_code)
        return response


async def _drive(
    *,
    server: Any,
    token: str | None,
    calls: tuple[tuple[str, dict[str, Any]], ...],
    want_tool_list: bool,
) -> SessionOutcome:
    outcome = SessionOutcome()
    app = server.streamable_http_app(host="127.0.0.1")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    statuses: list[int] = []

    def client_factory(**_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=_RecordingTransport(httpx2.ASGITransport(app=app), statuses),
            base_url=BASE_URL,
            headers=headers,
        )

    client = client_factory()
    async with app.router.lifespan_context(app), client:
        try:
            async with streamable_http_client(MCP_URL, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if want_tool_list:
                        listed = await session.list_tools()
                        # 顺序不参与断言，但**去重**参与：同一个名字出现两次
                        # 会让「谁能看见什么」两套都成立。
                        outcome.tool_names = tuple(sorted(t.name for t in listed.tools))
                    observed: list[CallOutcome] = []
                    for name, arguments in calls:
                        result = await session.call_tool(name, arguments)
                        observed.append(_to_outcome(name, result))
                    outcome.calls = tuple(observed)
        except BaseException as exc:  # noqa: BLE001 - 握手失败是**期望结果**之一
            outcome.handshake_error = _render(exc)
    outcome.statuses = tuple(statuses)
    return outcome


def _to_outcome(name: str, result: Any) -> CallOutcome:
    texts = [
        block.text for block in (result.content or []) if getattr(block, "type", "") == "text"
    ]
    return CallOutcome(
        name=name,
        is_error=bool(result.is_error),
        text="\n".join(texts),
        structured=getattr(result, "structuredContent", None),
    )


def _render(exc: BaseException) -> str:
    """把异常组摊平成一行可断言的字串。"""
    parts: list[str] = []

    def walk(item: BaseException) -> None:
        if isinstance(item, BaseExceptionGroup):  # type: ignore[name-defined]
            for child in item.exceptions:
                walk(child)
            return
        parts.append(f"{type(item).__name__}: {item}")

    walk(exc)
    return " | ".join(parts) or repr(exc)


def drive(
    *,
    server: Any,
    token: str | None,
    calls: tuple[tuple[str, dict[str, Any]], ...] = (),
    want_tool_list: bool = True,
) -> SessionOutcome:
    """同步入口：把整段会话跑完并返回可断言的观察结果。"""

    async def main() -> SessionOutcome:
        with anyio.fail_after(_TIMEOUT_SECONDS):
            return await _drive(
                server=server, token=token, calls=calls, want_tool_list=want_tool_list
            )

    return anyio.run(main)


def drive_with_settings(
    *,
    settings: Settings,
    tokens: Any,
    token: str | None,
    calls: tuple[tuple[str, dict[str, Any]], ...] = (),
    want_tool_list: bool = True,
    research: Any = None,
    product_loop: Any = None,
    product_catalog: Any = None,
    product_readiness: Any = None,
    launch: Any = None,
    outreach: Any = None,
    unavailable: Any = None,
    scenes: Any = None,
    decisions: Any = None,
    advisory: Any = None,
    quay: Any = None,
    jobs: Any = None,
    ledger_factory: Any = None,
    worker_outcomes: Any = None,
    assets: Any = None,
    relation_store: Any = None,
    records: Any = None,
    maintenance: Any = None,
    accounts: Any = None,
    cohorts: Any = None,
    review: Any = None,
    review_store: Any = None,
    continuation: Any = None,
    attempts: Any = None,
    reservations: Any = None,
    runs: Any = None,
    feedback: Any = None,
    views: Any = None,
    followups: Any = None,
    scheduling: Any = None,
    workstream: Any = None,
    effective_scene: Any = None,
    claim_withdrawal: Any = None,
) -> SessionOutcome:
    """``build_server`` 的薄包装 —— 让每个测试不必重复这一行。

    ``research`` 是研究侧处理器表（``research_handlers()`` 的返回值）。缺省
    ``None`` 表示这个实例**只接控制面** —— 那是一种合法部署，也是一个需要被
    测的对象（研究侧的工具那时落回 stub）。谁构造它由组合根决定，见
    ``interfaces/mcp/__main__.py::wire_research``；这里只把结果递进去。

    ``decisions`` / ``advisory`` 同理由：一个是后端表，一个是 ``decide`` 会
    咨询的那一个。**两者必须来自同一次构造** —— ``_decision_rows`` 按对象同一性
    回答「谁会真的被调用」，所以这里是透传，不是拼装。

    ``quay`` 同样透传，但它的缺省（``None``）在测试里是**主流**：绝大多数用例
    关心的是受理、编译、读取，而不是派发。缺省为 ``None`` 时 ``submit_research``
    照常工作，只是那一批工作一件都不交出去 —— 而那正是另一条判据要断言的形状，
    所以这个缺省既是省事，也是「没有执行器的部署」那个被测对象。

    ``product_loop`` 与 ``scenes`` 是同一种透传，但两个缺省的**含义不同**，
    这一点值得写下来：

    * ``product_loop=None`` —— 这个实例没有接产品环那几条工具。名字照旧在
      ``tools/list`` 上（目录是目录），而调用会拿到 ``unbacked_message`` 那句
      「在目录里，但当前服务实例尚未接线」—— 由 ``RoleGuard`` 按 ``backed``
      在处理器之前给出，与 ``research=None`` 时那几条完全同款。
    * ``scenes=None`` —— ``get_scene`` 因此报 ``record_state: "not_wired"``，
      而不是报「这个场景没有立项过」。前者是接线事实，后者是业务结论；把前者
      答成后者，会让一个没接存储的部署看起来像一个空的市场。

    ``product_catalog`` 是登记面那三条工具（``product_catalog_handlers()`` 的返回值），
    与 ``product_loop`` 同一种透传，而两者的缺省**在同一格上含义不同**：``None`` 时
    那三条落回 ``unbacked_message`` 而不是别的什么。这一条在这里尤其要紧 —— 一个
    「没接产品目录的部署」与一个「产品目录里什么都没有的部署」在报告上同形（都是
    「一条都读不出来」），而处置相反（去接线 / 去登记一版）。它的工厂**必须**与
    ``product_loop`` 收到的那些指向同一个 ``settings.database_path``：产品版本会被
    草稿回链、Offer 会被 Scene 引用，而两个库时那些引用各自都自洽、只是解不开。

    ``product_readiness`` 是查缺面那两条只读工具（``product_readiness_handlers()``
    的返回值），透传与 ``product_catalog`` 同款，而缺省的含义多一层：``None`` 时那
    两条落回 ``unbacked_message``，而那与「这一版不在盘上」是**两句不同的话** ——
    后者的下一步是去登记一版，前者的下一步是去接线。查缺面读的正是登记面写下去的
    那几版，所以两个工厂要与这里的 ``product_catalog`` 指同一个库。

    ``launch`` 是启动预览那一条只读工具（``launch_handlers()`` 的返回值，切片
    b01-d），同一种透传，而它比上面那一面**多一样东西**：它还读场景存储，所以缺省
    ``None`` 在这里的含义里多一句「这一版声明在不在盘上」—— 而那句话与
    ``scenes=None`` 时报的 ``record_state: "not_wired"`` 是同一条分界的两面。需要
    断言「研究身份看不见它」的用例显式传，且它的 ``scenes`` **必须与 ``product_loop``
    / ``runs`` 收到的是同一个工厂** —— 预览读的那一版与受理读的那一版指向两个库时，
    两边各自都正常，而「预览说可以开跑」与「受理说这一版不在盘上」会同时成立。

    ``outreach`` 与 ``unavailable`` 是**两种不同的缺席**，而这一对区别是本轮
    （§8）真正新增的东西，所以缺省也分开写：

    * ``outreach=None`` —— 那八条外联工具**没接线**。它们都在目录里，而调用
      落回 ``unbacked_message``。这是「这个实例还没装这一段」。
    * ``unavailable=`` 一张 ``{工具名: 理由}`` 表 —— 那几条**不在这个实例的
      ``tools/list`` 上**。这是「本仓有它、但这个部署没有让它出现的条件」，
      与上一条不是同一件事：前者读作「尚未接线」，后者读作「没有外发能力」，
      而一个只有前者能表达的测试面，会把「没注册」与「没接线」压成同一格。

    两个缺省都是 ``None``，而 ``outreach_handlers`` 自己会按 ``transport`` /
    ``source`` 决定返回几条 —— 所以「接线」与「注册」在测试里可以分开摆，
    就像它们在组合根里那样。

    ``assets`` / ``relation_store`` / ``records`` 是 §3 那条责任关系链上的三个
    存储工厂，透传给 ``control_handlers``。三者缺省都是 ``None``，而缺省在这里
    是**一个被测对象而不是省事**：``submit_findings`` 交上来一条责任结论而这三格
    没接时，回执里那一块要逐行说「这一行没有落到任何地方」，而不是把整条链
    变成「提交成功」—— 一个没装这一段存储的部署本来就会长成那样。需要断言
    「真的写进去了」的用例显式传，且**必须传 ``records``**：``decide`` 落在哪个
    台账、``submit_findings`` 去哪一张里查那份判定，是同一个问题的两半，两个
    工厂不同源时，一份刚刚落地的判定会读成「查无此判定」。

    ``maintenance`` 是维护账工厂（§6），同一种透传。它在这里的缺省 ``None`` 也是
    一个被测对象：只接控制面的实例没有这本账，那时 ``submit_findings`` 的回执里
    ``routes.signals`` 为空并**明写那是接线事实** —— 与「这一趟真的没有新来源」
    分得开。需要断言「信号真的写进去了」的用例显式传，且**必须与 ``product_loop``
    收到的是同一份**：结算写的信号正是维护 tick 读的那本账。

    ``accounts`` / ``cohorts`` 是账号池与冻结成员名单的两个工厂（§B12），同一种
    透传，而它们的缺省 ``None`` 也是一个被测对象：那时
    ``submit_research(input_kind="cohort")`` 返回一条**接线理由**（缺的是这两个
    工厂），而不是「这份名单里没有成员」—— 前者是部署的事实，后者是库里的读数。
    走通「把一份冻好的名单送去研究」的用例显式传，且**必须与 ``product_loop``
    收到的是同一组**：冻名单的那一侧与读名单的这一侧指向两个库时，两边都正常而
    名单永远读不出来。

    ``review`` 与 ``review_store`` 是同一件事的**两半**，所以分开两个参数而不是
    一个：前者是收件箱那七条工具的处理器表（``review_handlers()`` 的返回值），
    后者是那个案件存储工厂。两个缺省都不是省事，而是两个不同的被测对象：

    * ``review_store=None`` —— 这个部署没有接收件箱。那时 ``control_scene`` 的回执
      里 ``activation_gate.decision == "not_wired"``，激活**照旧**：那一格答的是
      「这一版有没有一件待办在等批准**没被问过**」，不是「问过了、判成不拦」。
    * ``review=None`` —— 那七条工具没接线，名字仍在 ``tools/list`` 上（对看得见它们
      的角色），调用落回 ``unbacked_message``。

    需要断言「控制者读得到收件箱、研究身份读不到」的用例两个都要传，且
    ``review_store`` **必须与 ``product_loop`` 收到的是同一个工厂** —— 立案件的那
    一侧与读案件这一侧指向两个库时，两处各自都正常而收件箱永远是空的，而那种红
    会指向七条工具里每一条，唯独不指向那一处接线。

    ``continuation`` 是 §B21 那条续跑状态页的处理器表（
    ``continuation_handlers()`` 的返回值），同一种透传。缺省 ``None`` 也是一个被
    测对象：那一条工具那时落回 ``unbacked_message``，而不是回一句「没有工作在跑」
    —— 后者与一个真的什么都不欠的市场同形，而两者的处置相反（去接线 / 什么都不用
    做）。需要断言「判词与驱动者两格真的读得出来」的用例显式传，且那三个工厂**必须
    与 ``product_loop`` 收到的是同一组**：状态页读的场景与控制器读的那个不一致时，
    两边会各说一套而各自都正常。

    ``attempts`` 是 §B15 访问尝试账那条只读工具的处理器表（
    ``fetch_attempt_handlers()`` 的返回值），同一种透传。缺省 ``None`` 也是一个被测
    对象：那一条那时落回 ``unbacked_message``，而不是回一句「没有尝试过」或一个空
    列表 —— 一本空的账与一本没接线的账在报告上同形（都是「一条都没有」），而前者
    读作「这个来源最近没被问过」、后者读作「去接线」。需要断言「读得出盘上那些行」
    或「一次读不建库」的用例显式传，且那个工厂**必须指向 ``settings.database_path``**
    ——它指向别的库时，写进去的那些行与读出来的那些会来自两个地方，而两边各自都正常。

    ``reservations`` 是 §8/§9 那条**对账面**的处理器表（``budget_handlers()`` 的
    返回值），同一种透传，缺省 ``None`` 同样是**一个被测对象**：那一条那时落回
    ``unbacked_message``，而不是回一个空列表 —— 一本读过的空账与一本没接线的账在
    报告上同形，而前者的处置是「继续」、后者是「去接线」。它读的是**预算账**
    （押出去还没结算的额度），与 ``attempts`` 读的**尝试账**是两本，所以它是另一个
    参数而不是同一条工具的第二形态。需要断言「悬着的那些读得出来、且那个 id 真的
    能拿去结算」的用例显式传，且 ``ledger_factory`` **必须同样给 ``control_handlers``**
    ——``get_job`` 报的用量与这一页看到的悬账来自两个地方时，两边各自都正常。

    ``runs`` 是 §B17 那条 Run 面的处理器表（``run_handlers()`` 的返回值），同一种
    透传，缺省 ``None`` 同样是**一个被测对象**：那三条那时落回
    ``unbacked_message``，而不是回一句「没有这一轮」—— 后者与「这一轮从来没被
    受理过」在报告上同形，而前者的处置是接线、后者是去受理一次。需要断言「受理
    真的落了一行」或「读得回盘上那一行」的用例显式传，且那个工厂**必须与
    ``scenes`` 指向同一个 ``settings.database_path``** —— Run 的 ``workspace`` 与
    ``selector`` 是从 Scene 取来的，两个库时那一行的归属会指向一个读不到的场景。

    ``feedback`` 是 §B23 那八条工具的处理器表（``feedback_handlers()`` 的返回值），
    同一种透传，缺省 ``None`` 同样是**一个被测对象**：那八条那时落回
    ``unbacked_message``，而不是回一句「今天还没有人给过反馈」—— 后者与「这一族
    没接线」在报告上同形（都是空），而前者的处置是继续、后者是去接线。

    它与前面几张表有一处不同，而那一处要求这个脚手架**只透传、不拼装**：这张表
    是三样东西拼出来的（反馈存储、证据的读面、收件箱存储），而其中**收件箱那一格
    必须与 ``review_store`` 同源** —— 一份补丁的批准读数从既有的 ``ReviewCase`` 推，
    两个库时「提案说在等一个人」与「收件箱里没有这件案子」会同时成立。所以需要断言
    「提完之后批准读数真的会变」的用例，要把 ``review_store`` 与构造 ``feedback``
    时用的那个工厂**指向同一份**，而这一层不加任何默认拼装（拼一处就在这里多一份
    ``feedback_handlers`` 的接线，而它与组合根那一份迟早会不一样）。

    ``views`` / ``followups`` 是 §B24 那两族的处理器表（``views_handlers()`` /
    ``followups_handlers()`` 的返回值），同一种透传，两个缺省都是**一个被测
    对象**：那时这八条落回 ``unbacked_message``，而不是回一句「还没有存过视图」
    或「没有待办」—— 后者与「这一族没接线」在报告上同形（都是空），而前者的处置
    是继续、后者是去接线。

    两族**分开两个参数**，而它们**共用一格**，两件事都要在这一层看得见：

    * 分开，是因为两族的接线条件不同 —— 视图那四条只要视图存储与账号池，跟进
      那四条还要场景、监控、待办与账本。合成一格之后，「这个实例装了哪几条」
      就只能靠名字猜，而一个只接了视图那四条的实例仍然是一个合法的实例。
    * 共用，是因为两族收到的那份**视图存储工厂必须是同一个对象**：一份视图被
      保存的那一版与监控此后按它到期的那一版是同一行。两个库时，一次 run 说
      「跑的是第 3 版」与监控按另一个库里的第 3 版到期会同时成立，而两个回执
      各自都正常 —— 与 ``scenes`` / ``review_store`` / ``maintenance`` 那几处
      逐字同一条。

    所以需要断言「存一版、然后订阅它、再按它跑一次」的用例，要把两格**指向同一份
    视图工厂**，而这一层不加任何默认拼装（理由与 ``feedback`` 那一段逐字相同：
    在这里拼一处，就会出现第二份接线，而它与组合根那一份迟早会不一样）。

    ``scheduling`` 是 §B25 那八条工具的处理器表（``scheduling_handlers()`` 的
    返回值），同一种透传，缺省同样是**一个被测对象**。但它有一处与前面每一格
    都不同，而那一处正是它值得单独写一段的理由：**构造它的那一边还要一个
    ``action``**（到点了要做的那件事，``scheduling_handlers`` 把它定成必填）。
    所以这一格的缺省 ``None`` **不是**「这个部署没有调度能力」—— 三座存储在
    组合根里都有，缺的是**执行那个动作的那一半**（一次 ``research_run`` 要走
    受理面 ``application/run_admission.py``）。这里只是照旧透传，不替调用方
    决定那一格填什么：本层**不提供**任何动作的缺省，因为一个恒回 ``SKIPPED``
    或恒回 ``UNRESOLVED`` 的替身会让「这个部署开不出研究」这句话在报告上变成
    「今天没有到期的调度」或者一条看起来正常的结论 —— 两件事的处置相反。

    ``workstream`` / ``effective_scene`` / ``claim_withdrawal`` 是 §B01/B02
    那三族的处理器表（``workstream_handlers()`` / ``effective_scene_handlers()``
    / ``claim_withdrawal_handlers()`` 的返回值），同一种透传，缺省同样
    **是一个被测对象**而不是「这个部署没有这一族」。三格分三格传，是因为三族的
    **接线条件互不相同**：启动面那四条还要受理面那一整套（``runs`` /
    ``maintenance`` / ``ledger`` / ``accounts`` / ``cohorts``），有效场景面只要
    场景、产品目录与有效场景存储，撤回面只要说法读面与撤回账 —— 一个只接了
    后两族的实例仍然是一个合法的实例。

    有一处与 ``views`` / ``followups`` 那一段逐字相同、而在这一族里更要紧：
    ``effective_scene`` 与 ``workstream`` 两格收到的**有效场景工厂必须是同一个
    对象**。断言「冻结一版、再把它读回来」的用例要把两格指向同一份工厂 ——
    两份工厂时，``preview_effective_scene`` 给你看的那一版与
    ``start_workstream`` 落库的那一版会是两份不同的东西，而两份都自洽（判据都
    只是「没有这一版」）。这一层不加任何默认拼装，理由与 ``feedback`` 那一段
    逐字相同。

    受理那一趟**不在这里**：``workstream_handlers`` 自己 import 既有的
    ``application/run_admission.py``，两条入口（``submit_run`` 与
    ``start_workstream``）走同一段。本层不提供注入点。
    """
    from marketops.adapters.mcp_server.server import build_server

    return drive(
        server=build_server(
            settings=settings,
            tokens=tokens,
            research=research,
            product_loop=product_loop,
            product_catalog=product_catalog,
            product_readiness=product_readiness,
            launch=launch,
            outreach=outreach,
            unavailable=unavailable,
            scenes=scenes,
            decisions=decisions,
            advisory=advisory,
            quay=quay,
            jobs=jobs,
            ledger_factory=ledger_factory,
            worker_outcomes=worker_outcomes,
            assets=assets,
            relation_store=relation_store,
            records=records,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            review=review,
            review_store=review_store,
            continuation=continuation,
            attempts=attempts,
            reservations=reservations,
            runs=runs,
            feedback=feedback,
            views=views,
            followups=followups,
            scheduling=scheduling,
            workstream=workstream,
            effective_scene=effective_scene,
            claim_withdrawal=claim_withdrawal,
        ),
        token=token,
        calls=calls,
        want_tool_list=want_tool_list,
    )


def write_accounts(settings: Settings, name: str, subjects: tuple[str, ...]) -> Path:
    """把一个已知账号清单写进 ``Settings.inputs_dir``，返回它落在哪。

    放在这里而不是某一个测试文件里：``submit_research`` 与
    ``control_job(resume)`` 都会把 ``input_ref`` 解析成 work item，于是
    「盘上要有一份真的清单」是**两个**文件的共同前提。清单里的内容会被当成
    候选池 —— 这是测试数据，不是配置。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.inputs_dir / f"{name}.yaml"
    body = "accounts:\n" + "".join(
        f"  - account_id: {subject}\n"
        f"    organisation: Example {index} Ltd\n"
        "    cohort: known_success\n"
        for index, subject in enumerate(subjects)
    )
    path.write_text(body, encoding="utf-8")
    return path
