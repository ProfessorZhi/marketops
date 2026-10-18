"""把认证、判定与工具装进一个 ``MCPServer``（§6）。

## 逐条注册，逐条实现

见 ``__init__.py`` 的说明：目录里的每一条**全部**注册，而每一条都有一处实现
（Phase C 的五个控制面 + Phase D 的 ``search_web`` / ``fetch_document`` +
Phase E 的 ``crawl_site`` + Phase F 的 ``decide`` / ``get_decision`` +
Phase G 的 ``submit_research`` / ``get_job`` / ``control_job`` /
``get_assignment`` / ``submit_findings`` + 产品环的 ``create_scene`` /
``add_scene_version`` / ``control_scene``）。**「有实现」不等于「这个实例接了」**：
同一条工具在两份处理器表里各有一处来源，而这个函数只拿得到**传进来的**那两份。
``_stub`` 那条路**留着**，而且不是死代码：一个只接控制面、或没接作业存储的
部署仍然会走到它 —— 「没接线」与「不存在」在那时仍然分得开。

stub 的签名**没有参数**，那是一份准确的 schema —— 本构建确实不接受这些
工具的输入。带一个 ``**arguments: Any`` 的占位签名会生成
``{"properties": {"arguments": …}, "required": ["arguments"]}``，那既不是这些
工具的契约，也会让 ``tools/list`` 的比对失去意义。

## §3 那一侧的接线是**传进来的**，不是在这里按 settings 造的

``research`` 参数是一份已经构造好的处理器表 —— 名字取的是 ``MCP_SERVICE.md``
的表格划分：§2 是控制目录（``tools.py``），§3 是 **Task-scoped research
catalog**，而那一条目里同时住着 ``search_web`` 与 ``decide``。所以这张表在
Phase F 之后装的是四个工具，而不是三个；合成一个 ``research`` 参数、而不是每
个阶段加一个形参，靠的正是这条划分本身。

组装它的是 ``interfaces/mcp/__main__.py``（组合根）。这样 ``build_server`` 不必
知道 ``SerperSearchProvider`` / ``DocumentAcquisition`` / ``default_decisions``
怎么拼 —— 也避免在 ``adapters/`` 里再写一份 ``default_acquisition()`` 的规则。

``advisory`` 是**单独**进来的，因为它不是一个工具，而是 ``decide`` 会咨询的那
一个后端。``get_capabilities`` 要按它回答「decide 会调用谁」，而那是控制面的一
格 —— 见 ``tools._decision_rows``：``wired`` 在决策那一族上是**逐后端**的。

不传 ``research`` 时服务面照常起得来：D–F 那四条工具落回 stub，而 ``RoleGuard``
按 ``backed`` 集合给出带阶段号的回答。控制面单独部署是合法的一种部署，
「没接线」与「不存在」在那时仍然分得开。

## 认证与判定的两半

* ``token_verifier`` —— 传输层。没有 Bearer 头 / 令牌不认识 ⇒ 401，
  请求根本进不到这一层。
* ``RoleGuard`` —— 中间件。拿到主体之后，谁看得见什么、谁调得动什么。

**没配令牌环时两半都不装。** 于是服务面起得来、握手成功、``tools/list``
返回空表、每一次调用都被拒绝。这不是「没配就当开放」，是把「忘配了」与
「故意开放」变成两种不同的行为 —— 后者在这个构建里没有实现方式。

## 不再宣告一个不存在的授权服务器

本阶段没有外部授权服务器：令牌由 ``marketops mint-token`` 在带外铸出，盘上只有
摘要，``iss`` 是一个常量。而只要 ``AuthSettings.resource_server_url`` 是真值，
SDK 就会挂上 ``create_protected_resource_routes``，在 RFC 9728 的发现文档里把
``AuthSettings.issuer_url`` 宣告成授权服务器 —— 于是这个服务对外说了一句假话，
而按发现文档走的客户端会走进死路。

**所以 ``resource_server_url`` 显式设为 ``None``。** 三件事随之发生，每一件都要
说清楚，因为「关掉一格」很容易连带关掉别的东西：

* 发现文档那两条路由**不再存在**（``/mcp`` 与
  ``/.well-known/oauth-protected-resource/*``）。这正是目的：一个说不出真话的
  路由，删掉比修好更诚实。
* ``WWW-Authenticate`` 头里不再带 ``resource_metadata=`` 指针 —— 那个指针本来
  指向的就是那份假文档。401 仍然是 401（``RequireAuthMiddleware`` 只要求
  ``token_verifier`` 存在，与这格无关），只是不再指路。
* 受众比对**因此必须由本层自己做**（SDK 那条读的正是这一格）。它落在
  ``auth.TokenRingVerifier`` 里，用的是 SDK 自己的 ``AnyHttpUrl`` 归一化规则 ——
  理由写在那个模块的 docstring 里，与「这个 AS 不存在」是同一件事的两面。

``AuthSettings.issuer_url`` 仍然是必填字段，而在本构建里**没有任何路径读它**
（读它的两条路各需要一个本服务没有的东西：授权服务器 provider，或
``resource_server_url``）。这里填本服务自己的资源地址，并在
``tests/test_mcp_auth_interop.py`` 里钉住「那份发现文档取不到」——
不是钉「我没写那句话」，是钉「它没有出现在线上」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from ... import __version__
from ...config import Settings
from ...ports.budget import BudgetLedger
from ...ports.decisions import DecisionProvider, DecisionRecordStore
from ...ports.dispatch import Dispatcher, WorkerExitRecord
from ...ports.jobs import JobStore
from ...ports.pool import (
    AccountCohortStore,
    AccountPoolStore,
    AccountVenueRelationStore,
    VenueAssetStore,
)
from ...ports.review import ReviewCaseStore
from ...ports.scenes import MaintenanceStore, SceneStore
from ...security.catalog import TOOL_CATALOG
from ...security.tokens import TokenRing, normalize_resource
from .auth import TokenRingVerifier
from .guard import RoleGuard, unbacked_message
from .tools import Handler, control_handlers

__all__ = [
    "ToolBacking",
    "build_server",
    "loopback_transport_security",
    "mcp_url",
    "resource_identifier",
    "tool_backing",
]

_SERVER_NAME = "marketops"
_MCP_PATH = "/mcp"

_INSTRUCTIONS = (
    "MarketOps 的研究控制面。**研究身份恒为只读研究**：research_only = true、"
    "send_authorized = false，这一条与部署配置无关 —— 研究角色的权限集合里没有"
    "任何一个发送或提交表单的工具（`security/catalog.py` 的 `_RESEARCH`），"
    "任何环境变量都不改变这一点。"
    "\n\n"
    "产品环另有自己的两把锁，而它们与研究身份是分开的：外发能力由**部署**决定"
    "（中继端点、发送箱、凭据、一份非空的收件人允许列表，四样缺一不可；缺哪一样"
    "见 `get_capabilities` 的 `outbound_missing`），而每一批信由**人**授权"
    "（`authorize_wave`，只有 authorize 角色能调，且批准人取自认证主体而不是载荷）。"
    "两样都齐之前，任何一波都停在 prepared_pending_authorization。"
    "\n\n"
    "工具按角色隔离，判定来自传输层的 Bearer 令牌，不来自任何调用参数 —— "
    "把身份放进参数不会有任何作用。"
    "\n\n"
    "目录里有一部分工具属于后面的阶段（Phase D–G），调用它们会得到一句带阶段号"
    "的回答，而不是「工具不存在」。两者的区别是有意的：「不存在」意味着你写错了"
    "名字，「还没实现」意味着该等。有个别工具在**本部署**里根本没注册（外发能力"
    "没接线时的 `send_wave`）—— 那既不是写错了名字，也不是还没实现，"
    "而是这个部署没有被给到那条路径。"
    "\n\n"
    "证据优先：任何结论都应当能追到一条已抓取的公开文档。"
)


def mcp_url(settings: Settings) -> str:
    """本服务**绑在**哪个地址上（推导出来的端点地址）。

    IPv6 字面量要加方括号，否则 ``http://::1:8787/mcp`` 不是一个合法 URL。

    这个值不等于「本服务是谁」—— 后者见 :func:`resource_identifier`。两者在
    今天默认相同，而在反向代理/隧道后必然不同，把这一条分开是为了让那次不同
    是一个**决定**（``MARKETOPS_MCP_RESOURCE``）而不是一次推导。
    """
    host = settings.mcp_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.mcp_port}{_MCP_PATH}"


def resource_identifier(settings: Settings) -> str:
    """本服务认定的资源指示符（RFC 8707）：令牌的受众必须等于它。

    两处取值的先后顺序是有意的：**显式声明的那个赢**。绑在 ``127.0.0.1`` 上的
    进程与客户端实际访问它的地址是两件事，放在反向代理或隧道后面时两者必然
    不同；那时若仍按绑定地址比，症状是「令牌明明是给这个服务铸的，却全部 401」，
    而排查方向会落在环上。

    显式声明在**这里**校验（而不是在 ``load_settings`` 里）：一个写坏的
    ``MARKETOPS_MCP_RESOURCE`` 应当在服务起不来的时候被看见，而这条路径与
    ``resolve_bind_host`` 是同一条 —— 组合根一跑就抛 ``ConfigError``，
    ``--check`` 与 ``_serve()`` 都退 2（「原因在启动参数或环境」）。
    """
    declared = settings.mcp_resource
    if declared is None:
        return mcp_url(settings)
    return normalize_resource(declared, where="MARKETOPS_MCP_RESOURCE")


def loopback_transport_security(host: str) -> TransportSecuritySettings:
    """本服务面自己的 DNS-rebinding 保护配置。

    **不依赖 SDK 的自动开启。** ``Server.streamable_http_app`` 只在
    ``host in ("127.0.0.1", "localhost", "::1")`` 时才装上
    ``enable_dns_rebinding_protection=True``，而本服务的地址闸接受
    ``127.0.0.5`` 这类**其他**环回地址 —— 于是那条路径上保护会静默地完全
    缺席，而 ``127.0.0.1`` 上有。同一份配置在两个合法地址上得到两种安全
    姿态，是最难被发现的那种不一致。

    所以这里按**解析出来的那个地址**自己构造：绑在哪，就允许哪个 Host。

    保护挡的是 DNS rebinding —— 一个浏览器里的页面把主机名解析到 127.0.0.1
    之后访问本服务。请求到达时 ``Host`` 会是那个攻击者的域名，于是被拒。
    它不替代认证：认证是另一层（``auth.py``）。
    """
    bracketed = f"[{host}]" if ":" in host else host
    allowed = sorted({f"{bracketed}:*", "localhost:*", "127.0.0.1:*", "[::1]:*"})
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        allowed_origins=sorted(f"http://{entry}" for entry in allowed),
    )


def _load_ring(settings: Settings) -> TokenRing | None:
    """按配置装载令牌环；没配时返回 ``None``。

    装载失败（文件缺失 / 不是 JSON / 角色名拼错）会抛 ``ConfigError`` —— 服务
    起不来。这是刻意的：一个「起来了但谁都不许」的服务，在没有异常的情况下
    会被当成部署成功，而第一个发现真相的人会是调用方。
    """
    path = settings.mcp_tokens_path
    if path is None:
        return None
    return TokenRing.from_file(path)


def build_server(
    *,
    settings: Settings,
    tokens: TokenRing | None = None,
    research: Mapping[str, Handler] | None = None,
    product_loop: Mapping[str, Handler] | None = None,
    product_catalog: Mapping[str, Handler] | None = None,
    product_readiness: Mapping[str, Handler] | None = None,
    launch: Mapping[str, Handler] | None = None,
    outreach: Mapping[str, Handler] | None = None,
    review: Mapping[str, Handler] | None = None,
    continuation: Mapping[str, Handler] | None = None,
    attempts: Mapping[str, Handler] | None = None,
    reservations: Mapping[str, Handler] | None = None,
    runs: Mapping[str, Handler] | None = None,
    decisions: Mapping[str, DecisionProvider] | None = None,
    advisory: DecisionProvider | None = None,
    quay: Dispatcher | None = None,
    jobs: Callable[[], JobStore] | None = None,
    ledger_factory: Callable[[], BudgetLedger] | None = None,
    worker_outcomes: Callable[[str], tuple[WorkerExitRecord, ...]] | None = None,
    scenes: Callable[[], SceneStore] | None = None,
    assets: Callable[[], VenueAssetStore] | None = None,
    relation_store: Callable[[], AccountVenueRelationStore] | None = None,
    records: Callable[[], DecisionRecordStore] | None = None,
    maintenance: Callable[[], MaintenanceStore] | None = None,
    accounts: Callable[[], AccountPoolStore] | None = None,
    cohorts: Callable[[], AccountCohortStore] | None = None,
    review_store: Callable[[], ReviewCaseStore] | None = None,
    feedback: Mapping[str, Handler] | None = None,
    views: Mapping[str, Handler] | None = None,
    followups: Mapping[str, Handler] | None = None,
    scheduling: Mapping[str, Handler] | None = None,
    workstream: Mapping[str, Handler] | None = None,
    effective_scene: Mapping[str, Handler] | None = None,
    claim_withdrawal: Mapping[str, Handler] | None = None,
    unavailable: Mapping[str, str] | None = None,
) -> MCPServer:
    """组装一个可用的服务实例。

    Args:
        settings: 配置。工具与判定都从它取参数。
        tokens: 令牌环。``None`` 表示走 ``settings.mcp_tokens_path``；
            两者都没有时，服务面**起得来但什么都不许**。
        research: §3 那一侧（Phase D–F）的处理器，由组合根构造。``None`` 表示
            这个实例只接控制面 —— 那时这些工具落回 stub，理由见模块 docstring。
        product_catalog: B01 的**登记面**（``mcp_server/product_catalog.py``）：
            ``put_product_version`` / ``put_offer_version`` / ``put_product_line``
            三条。与 ``review`` 同一条：成员关系不随部署变（接了就三条，没接就是
            空表），所以它不需要 ``unavailable`` 那样的第二份。
            **它与别的处理器表有一处不同，而那一处正是它存在的理由**：它 import
            不到 ``ports.search`` / ``ports.acquisition``，也 import 不到那两个会
            间接够到它们的同层模块（``tools`` / ``product_loop``）—— B01 ``:104``
            逐字要求「ProductLine/产品资料登记不产生网络调用」，而这句话在这里是
            **接线的事实**，不是它自律遵守的一条规则。判据在
            ``tests/test_registration_is_offline.py``，它算的是 import 闭包。
        product_readiness: B01 的**查缺面**（``mcp_server/product_readiness.py``）：
            ``get_product_version_readiness`` / ``get_offer_version_readiness``
            两条只读工具。与 ``product_catalog`` 同一条：成员关系不随部署变（接了就
            两条，没接就是空表）。两族分开传而不是合成一张表，是因为它们的**接线
            条件不同** —— 登记面只需要 ``catalogs``，查缺面还需要已批准说法的读面
            （``claims``，方案那一版引用的说法就是靠它判的），而一个只接了登记面的
            实例仍然是一个合法的实例（那时这两条落 ``unbacked_message``）。
        launch: B01 的**启动预览面**（``mcp_server/launch.py``，切片 b01-d）：
            ``preview_launch`` 一条只读工具。与 ``product_readiness`` 分开传，理由
            同它分开传的理由 —— 两族的接线条件不同：查缺面只要产品目录与说法，
            启动预览还要**场景存储**（主语那一版与它的立项记录），而一个只接了查缺面
            的实例仍然是一个合法的实例。

            它收的三样与 ``product_loop`` 收的是**同一组工厂**（``scenes`` /
            ``catalogs`` / ``claims``）：两份实例指向两个库的形态是「预览说这一版
            可以启动、受理按另一个库判定」，而两边的回执各自都正常。它 import 不到
            ``tools`` / ``product_loop``（B01 ``:104`` 第 5 项逐字「启动授权前零外部
            调用」），判据在 ``tests/test_mcp_launch_is_offline.py``，它算的是闭包。
        outreach: 产品环**外联那一段**（``mcp_server/outreach.py``）。与
            ``product_loop`` 分开传，因为它的成员关系**随部署变**：没有传输时
            ``send_wave`` 压根不在这一份里。分开传让「这个实例装了哪几条外联工具」
            在调用点就看得见，而不是藏在一个合并后的字典里。
            它的另一半是 ``unavailable``（缺的那几条与为什么），两份由
            ``outreach.unavailable_outreach_tools`` 从同一组常量算出来 ——
            于是「注册了却报成没有」这一类的分家只有一种成因。
        review: §12 收件箱那七条工具（``mcp_server/review.py``）。``None`` 与
            「空的一份」**不是**同一件事，而这一族是全部处理器表里唯一一处
            由接线事实决定**注册与否**的：``review_handlers`` 在没有收件箱存储
            时返回空表，于是那七条落回 stub —— 与 ``outreach`` 那两三条同一条
            理由（§8：「新的危险操作对普通研究身份不可见、不可调用，仅在部署能力
            启用时注册」）。一个恒回空列表的收件箱读起来与「现在没有待办」同形，
            而它们的处置一个是接线、一个是等。
        review_store: 收件箱存储的**工厂**（§12）。名字带 ``_store`` 后缀是
            ``relation_store`` 的先例：不带后缀的 ``review`` 已经是上面那一份
            **处理器表**，两者同名会让「这个实例装了哪几条收件箱工具」与「它接没接
            收件箱存储」在签名上变成同一件事 —— 而它们是两件：表由组合根构造，
            存储是控制面 ``_resolve_and_compile`` 的生产者要写的地方（受理时被身份
            判据拒掉的那几行落成待办）。``review_handlers`` 必须收到**同一个**工厂，
            否则会出现「受理说这几行开了待办、读收件箱什么都没有」。
        continuation: §B21 的续跑状态页（``mcp_server/continuation.py``），
            **一条只读工具**。与 ``review`` 分开传的理由同一条，而它多一条自己的：
            这一条答的是「为什么此刻没有更多工作在跑」外加「现在有没有人在推」，
            而**后一格只有这一个参数答得出** —— 它读的是唤醒归属注册表，而归属
            不是任何一个 ``get_capabilities`` 那一列能表达的东西（控制器进程在不在，
            与这个部署装了什么工具无关）。传 ``None``（或组合根没接那两样存储）
            时这一条落回 stub，``get_capabilities`` 的 ``wired`` 那一格随之如实报
            它不在。**不要给它一个恒回「没有工作」的替身**：那与一个真的什么都不欠
            的市场在报告上同形，而两者的处置相反（接线 / 什么都不用做）。
        attempts: §B15 的**访问尝试账读面**（``mcp_server/fetch_attempt.py``），
            **一条只读工具**。与 ``continuation`` 分开传的理由同一条，再加两条自己的：
            它的落点（那本账）与 ``continuation`` 读的三样没有一处重合，合成一个参数
            之后「这一条装的是账还是状态页」就只能靠名字猜；而它的成员关系**不随部署
            变但有落点才注册** —— 组合根交回 ``None``（本实例没有那本账）时这一条不注册，
            调用落回 stub，``get_capabilities`` 的 ``wired`` 那一格如实报它不在。
            **不要给它一个恒回空列表的替身**：一本空的账与一本没接线的账在报告上同形
            （都是「一条都没有」），而前者读作「这个来源最近没被问过」、后者读作
            「去接线」—— 这正是 ``review`` 那一族立下的规矩。
            它读的那本账**跨主体**（同一个 URL 上的尝试会记在不同 ``scope`` 名下），
            所以目录里那一条的角色是 ``_CONTROL``：研究侧本来就在自己那一跳的
            ``FetchResult.diagnosis`` 上拿到了诊断，它不需要账。
        reservations: §8/§9 的**对账面**（``mcp_server/budget.py``），**一条只读工具**。
            它答的是 ``open_reservations`` 那个端口方法自己写着要答的问题
            （「有多少额度悬着，否则只能靠翻库」），而与 ``attempts`` 分开传的理由
            是两者的落点不同：``attempts`` 读的是**尝试账**（每一次抓取一行，与钱
            无关），这一条读的是**预算账**（押出去、还没结算的额度）。合成一个参数
            之后「这一条读的是哪本账」就只能靠名字猜 —— 而这两本账的处置完全
            不同（一本的处置是排查来源，另一本是结算或退款）。
            传给它的 ``ledger_factory`` 与 ``get_job`` / ``get_assignment`` 用的是
            **同一个**：两处各建一份的形态是「作业报的用量与对账面看到的悬账来自
            两个地方」，而两边各自都正常。
            组合根交回 ``None`` 时这一条不注册（本实例没有账本），**不要给它一个
            恒回空列表的替身** —— 理由与 ``attempts`` 那一段逐字相同。
        runs: §B17 的 **Run 面**（``mcp_server/runs.py``）。它与前面几个处理器表
            **分开传**的理由不是「又一块」，而是它自己那一条里最要紧的那一半：
            受理要的 Scene 那一版规则**不在这个参数里**。``runs`` 只带那一行落在
            哪里，读场景的是 ``scenes`` —— 两个工厂**必须指向同一个
            ``settings.database_path``**。两个库时两边各自都正常，而那一行的
            ``workspace`` 会指向一个读不到的场景：受理成功了，而它圈的范围此后
            没有任何一处答得出来。
            组合根交回 ``None``（本实例没有 Run 记录）时这一条不注册，调用落回
            ``unbacked_message`` —— **不要给它一个恒回「没有这一轮」的替身**：
            后者与「这一轮从来没被受理过」在报告上同形，而两者的处置相反（去接线 /
            去受理一次），理由与 ``attempts`` / ``reservations`` 那两段逐字相同。
        feedback: §B23 那八条工具的处理器表（``mcp_server/feedback.py::
            feedback_handlers`` 的返回值）。与 ``review`` / ``continuation`` /
            ``attempts`` / ``reservations`` / ``runs`` 同一种透传：**一份已经构造好的
            表**，成员关系不随部署变（接了就是八条，没接就是空表），所以它不需要
            ``unavailable`` 那样的第二份 —— 组合根交回 ``None`` 时这八条落回 stub，
            ``get_capabilities`` 的 ``wired`` 那一格如实报它们不在。
            **不要给它一个恒回空列表的替身**：一份「今天还没有人给过反馈」与一份
            「这一族没接线」在报告上同形（都是空），而两者的处置相反（继续 / 去接线）
            —— 与 ``attempts`` / ``reservations`` / ``runs`` 那三段逐字相同。

            它比前面几张表多一条**引用**，而那条引用是这一族唯一的批准来源：一份补丁
            的批准读数从**既有的** ``ReviewCase`` 推（``application/feedback.py`` 的
            ``standing_of``），而**批准本身不在这八条里** —— 它仍然只走
            ``approve_policy_change``。所以组合根构造这张表时交给它的 ``review``
            工厂，必须与上面 ``review_store`` 收到的是**同一个**（``_compose`` 传的
            就是同一份 ``default_review_store(settings)``）：两个库时的形态是「提案说
            在等一个人」与「收件箱里没有这件案子」同时成立，而两个回执各自都正常 ——
            一次拒绝给了一个不存在的案号。

            ``build_server`` 只收表、不收那两个工厂（证据的读面与 ``review``），理由
            与模块开头那条相同：怎么拼是组合根的事，这一层不必知道
            ``FeedbackStore`` / ``EvidenceStore`` 怎么造 —— 也就不会在 ``adapters/``
            里再写一份 ``default_evidence_reader()`` 的规则。
        views: §B24 视图那四条工具的处理器表（``mcp_server/views.py::
            views_handlers`` 的返回值）。与 ``review`` / ``continuation`` /
            ``attempts`` / ``reservations`` / ``runs`` / ``feedback`` 同一种透传：
            **一份已经构造好的表**，成员关系不随部署变（接了就是四条，没接就是
            空表），所以它不需要 ``unavailable`` 那样的第二份 —— 组合根交回
            ``None`` 时这四条落回 stub，``get_capabilities`` 的 ``wired`` 那一格
            如实报它们不在。**不要给它一个恒回空列表的替身**：一份「这个工作区
            还没有存过视图」与一份「这一族没接线」在报告上同形（都是空），而两者的
            处置相反（继续 / 去接线）—— 与 ``attempts`` / ``reservations`` /
            ``runs`` / ``feedback`` 那几段逐字相同。

            它比前面几张表多一条**共用**，而那条共用是这一族唯一的跨表一致性来源：
            视图存储那一个工厂**必须同时交给 ``followups``**（``_compose`` 传的
            就是同一份 ``default_saved_view_store(settings)``）。两个实例时，
            ``run_saved_view`` 读的那一版与 ``put_watch`` 订阅的那一版会来自两个
            地方，而两个回执各自都正常 —— 一次 run 说「跑的是第 3 版」，而监控
            此后按另一个库里的第 3 版到期。
        followups: §B24 监控与跟进那四条工具的处理器表（``mcp_server/followups.py::
            followups_handlers`` 的返回值）。与 ``views`` **分开传**，而这不是
            「又一块」：两族的接线条件不同 —— 视图那四条只要视图存储与账号池，
            这一族还要**场景存储**（``put_watch`` 把监控挂在一个场景上）、
            **监控存储**、**待办存储**与**账本**（启用监控是一次要预算的承诺）。
            合成一格之后，「这个实例装了哪几条跟进工具」就只能靠名字猜，而一个
            只接了视图那四条的实例仍然是一个合法的实例 —— 那时这四条落回
            ``unbacked_message``，而不是回一句「没有待办」。

            它收的 ``scenes`` 与 ``ledger_factory`` 与别处**是同一个对象**，
            理由逐字写在 ``runs`` 与 ``reservations`` 那两段：启用监控写的上限与
            对账面读的账本各指一个库时，两边各自都正常，而「这一条监控把预算
            顶掉了多少」此后没有任何一处答得出来。
        scheduling: §B25 调度与通知那八条工具的处理器表（``mcp_server/
            scheduling.py::scheduling_handlers`` 的返回值）。与 ``views`` /
            ``followups`` 同一种透传：一份已经构造好的表，成员关系不随部署变
            （接了就是八条，没接就是空表）。**但它比前面每一张表都多一样东西，
            而多出来的那一样不是存储**：``scheduling_handlers`` 还收一个
            ``action`` —— 到点了要做的那一件事。

            三座存储这个部署有（``composition.py`` 里
            ``default_schedule_store`` / ``default_notification_store`` /
            ``default_session_binding_store`` 都在），而**执行那个动作的那一半
            今天还不存在**：一次 ``research_run`` 要走受理面
            （``application/run_admission.py``）去开一轮 Run，那是 §B01/B02 那
            一片的面。所以组合根今天**不传这一格**，那八条落回 stub
            （``get_capabilities`` 如实报它们不在）。

            **不要为了「看起来接上了」给它一个恒回 ``UNRESOLVED`` 或恒回
            ``SKIPPED`` 的替身。** 前者让每一次点火都产生一条 ``NEEDS_HUMAN``
            事件，后者更坏 —— 一个「什么都不做、回 SKIPPED」的动作会让每一次
            点火在清单上显示 ``ok``，而它一次研究都没开过
            （``scheduling_handlers`` 把这句写在它自己的 docstring 里，并把
            ``action`` 定成必填、没有缺省，为的就是这一条）。真值属于
            ``UNRESOLVED`` 说的那一件事「这个部署没有能执行它的那一半」，而那句
            话只有在**这个部署真的没有那一半**时才是真的；等受理面落地、
            真的 ``ScheduleAction`` 接上时，这一格与它一起接 —— 两件事分两次做
            会造出一个「接了但一次研究都开不出来」的中间态，而那个中间态在
            ``tools/list`` 上与接好的形态逐字同形。
        workstream: §B01/B02 的**启动面**（``mcp_server/workstream.py::
            workstream_handlers`` 的返回值）：``start_workstream`` /
            ``get_workstream`` / ``list_workstreams`` / ``control_workstream``
            四条。与 ``views`` / ``scheduling`` 同一种透传：一份已经构造好的表，
            成员关系不随部署变。

            它收十样，而其中**八样与别处是同一个对象**，这不是凑巧：
            ``scenes`` / ``catalogs`` / ``runs`` / ``maintenance`` /
            ``ledger`` / ``accounts`` / ``cohorts`` 各自有两处以上的读者，
            而 ``start_workstream`` 是**唯一一条会自己开一轮 Run 的工具** ——
            它走的是 ``application/run_admission.py`` 那条既有受理路径（见下）。
            两份实例指向两个库时，「这一轮按哪一份计划跑」在盘上有两个答案，而
            两边的回执各自都正常。

            两处**必须与 ``effective_scene:`` 那一格同源**：

            * ``effective`` —— 同一族自己在 ``freeze()`` 里写、在
              ``get_effective_scene`` 里读，两个实例指向两个库的形态是「刚冻结的
              那一版读不出来」，而那与「它从来没被冻结过」**同形**（判据都只是
              「没有这一版」）。所以组合根提一个具名工厂，两格引用**同一个对象**。
            * ``scenes`` —— 必须是产品环、查缺面、启动预览面、监控那几处收的
              **同一个** ``default_scene_store``。``start_workstream`` 绑的是
              「某一版已批准的 Scene」，而那个「批准」是产品环写下去的。

            **受理那一趟不是这一格参数。** ``workstream_handlers`` 自己 import
            既有的 ``admit_run``（``application/run_admission.py``）并在
            ``_first_round`` 里调它 —— ``submit_run`` 与 ``start_workstream``
            走的是**同一段**（``_preamble`` → ``round_of`` → ``admit_run``），
            一处都没有另写。把它做成一个注入点会诱出「另接一条」的写法，而那正是
            承重点 5 禁止的：两处各接一次的话，同一份声明在两条入口上会得到两份
            规格，而两份规格的指纹不同 —— 那时「这一轮按哪一份计划跑」在盘上有
            两个答案，两个回执各自都正常。
        effective_scene: §B01/B02 的**有效场景面**（``mcp_server/
            effective_scene.py::effective_scene_handlers`` 的返回值）：
            ``preview_effective_scene`` / ``get_effective_scene`` 两条。与
            ``workstream`` **分开传**，理由与 ``product_readiness`` 分开传的
            理由同一条 —— 两族的接线条件不同：这一族只要场景、产品目录与有效场景
            存储，而启动面还要受理面那一整套；一个只接了这两条的实例仍然是一个
            合法的实例（那时启动那四条落回 ``unbacked_message``）。

            它的 ``effective=`` 与 ``workstream_handlers`` 收到的是**同一个工厂
            对象**，而这一条是按对象同一性说的，不是「两句同形的调用也算」：本仓
            别处（``saved_views`` / ``budget_ledger`` / ``attempts``）用过同一条
            纪律，理由逐字相同 —— 两处各写一句
            ``default_effective_scene_store(settings)`` 在今天效果一样（同一个
            ``database_path``），但读起来像两份；而「像两份」的形态一旦哪天真成了
            两份，预览给你看的那一版与启动落库的那一版会是两份不同的东西，而两份
            都自洽。
        claim_withdrawal: §B02 的**撤回面**（``mcp_server/claim_withdrawal.py::
            claim_withdrawal_handlers`` 的返回值）：``withdraw_claim`` 一条。
            与别处分开传的理由是它的两样东西答的是**两个不同的问题**：
            ``claims`` 答「``claim_id`` 指的是哪一条已批准说法」（读面，
            ``default_outreach_store``），``withdrawals`` 答「这一家已经撤过哪几
            版」（撤回账，``default_claim_withdrawal_store``）。合成一格会让
            「这条说法不存在」与「这一版从来没撤过」在报告上同形，而处置相反 ——
            前者是调用方给错了 id，后者是一条正常的、值得记下的结果。
        decisions: 本实例构造出来的决策后端，按 ``provider.name`` 索引。
            ``get_capabilities`` 要**逐后端**报「配了没 / 能不能真的产出一条
            记录」，所以它要的是整份表，不是一个被选中的后端。
        advisory: 其中 ``decide`` 会咨询的那一个。两者**必须来自同一次构造**：
            表按对象同一性回答「会不会被调用」，而 ``tools._decision_rows``
            对不一致的组合直接报错 —— 否则那张表会说着 ``wired: false``
            而调用路径正在跑。
        jobs: 持久作业记录的工厂（Phase G）。``None`` 表示这个实例没有落点，
            于是五条 G 工具（``submit_research`` / ``get_job`` /
            ``control_job`` / ``get_assignment`` / ``submit_findings``）落回
            stub —— 与 ``research`` 同一个取向：**没有的入口优于有入口加一条
            注意事项**。
            这里刻意不提供「拿内存存储兜底」的缺省，因为那会让一个受理了却在
            重启后消失的作业看起来完全正常。
        quay: 本实例构造出来的外部执行器（§6）。它**不是**一个工具落点 ——
            没有哪条工具因为没有它就退回 stub；缺它的时候 ``submit_research``
            照常受理与编译，只是那一批工作一件都不交出去（回执里的 ``dispatch``
            如实这么说）。所以它在这一层有两个作用：接线（传给
            ``submit_research`` 与 ``control_job(resume)``）与让
            ``get_capabilities`` 的 quay 行答得出「**这个进程**装起来没有」——
            那一格不从 ``Settings`` 重推（理由见 ``tools.control_handlers``
            的 ``quay`` 一段）。
        ledger_factory: 账本工厂，``get_job`` 与 ``get_assignment`` 用它读用量。
            ``None`` 时那一格如实报告「这个实例没有账本可读」—— 而**不是**报 0。
        worker_outcomes: 外部那本 worker 退出账的读器（§1 的研究终态回执）。
            与 ``quay`` 同源但**分开传**：一个是写入口（派发与回传），一个是读入口
            （外面怎么判那一次退出）。把它们合成一个参数会让「借一个只读的读器
            就要拿到派发权」成为一种自然的写法。``None`` 时 ``get_job`` 的
            ``research_terminals`` 那一段如实报读不到 —— 报告其余部分照出。
        scenes: 场景存储的工厂。它**在控制面里**被用到（``get_scene`` 报落盘的那
            一份立项记录），所以与 ``research`` 不同，它不是「另一个目录的实现」，
            而是控制面自己的一个落点。两份都传：``control_handlers`` 要它读记录，
            产品环的处理器要它写 —— 传**同一个**工厂，以免「读的那一份」与「写的
            那一份」是两个库。
        assets / relation_store / records: §3 里 ``submit_findings`` 那一条**多要的三样**
            东西（场地资产池、责任关系表、判定台账），三者的工厂都从组合根来。缺一样
            只影响那一格：``assets`` / ``relation_store`` 缺了，责任结论那一段逐行
            如实报「本服务实例没有接它」；``records`` 缺了，每一行的理由是「没有接
            判定台账」。**都不是「这条关系不成立」** —— 接线事实与业务结论的处置是
            两个地方。缺省 ``None`` = 本实例没有接，而不是「全都接上了」。

            ``records`` 与研究侧 ``decide`` 用的必须是**同一处构造**（``_compose``
            传同一个工厂给两边）：判定台账落在同一个库的同一张表上，两个实例指向两个
            库的形态是「研究员问的那次判定在 A 里、结算时读的是 B」—— 于是每一条
            责任结论都会得到「它指的判定解不开」，而两份记录各自都正常。
        maintenance: 维护账的**工厂**（§6）。§3 的 ``submit_findings`` 那一侧用它：
            这一次结算**真的**落下了新证据行时，``produce_routes`` 当场给维护账记
            一条变更信号 —— 那是 ``put_signal`` 的生产路径。缺省 ``None`` = 本
            实例没有接，于是这一趟照常绑定、判时效、定级、落盘，只是回执里
            ``routes.signals`` 为空并**明写这是接线事实**（与 ``assets`` 同一条：
            空信号与「真的没有新来源」在产出上同形，回执是唯一分得开它们的地方）。
            它**必须与 ``product_loop`` 收到的是同一个工厂**：产品环的三条维护工具
            读的正是这里写的信号，两份实例指向两个库的形态是「结算写了信号、维护
            tick 永远看不见」，而两边的报告各自都正常。
        unavailable: **本部署没有这一项能力**的工具，按名字索引到「缺的是哪一样」。
            这些工具**根本不注册**（``_register`` 跳过它们），所以它们既不在
            ``tools/list`` 上、也不可调用 —— 调用会拿到 :func:`unavailable_message`
            那一句，而不是「尚未接线」。

            ## 为什么它必须是 ``build_server`` 的参数，而不是 `Settings` 的一格

            它不是一个配置项，是**接线事故的三种结局**里的第三种（前两种是「接上
            了」与「代码还没写」）。判据是「接线那一侧有没有一份东西交出来」——
            ``settings.outbound_capable`` 只回答「SMTP 那六样配齐没有」，而一台
            部署可以配齐 SMTP 却没有一条传输（组合根没构造它），也可以没有 SMTP
            而在测试里拿一台捕获传输。**注册与否跟着交出来的东西走**，不跟着
            ``Settings`` 的派生值走 —— 后者会让「配置齐了但没有传输」变成一条
            注册了却调不动、调了会抛的工具。

            缺省 ``None`` = 本部署没有裁掉任何一条（不是「全都裁掉」）。
    """
    ring = tokens if tokens is not None else _load_ring(settings)
    # 控制面先算，但它要知道 §3 那一侧与产品环**有没有**接线（``get_capabilities``
    # 的第三格问的就是这个）。所以先有那些名字，再有控制面 —— 顺序不能反。
    extra = {
        **(research or {}),
        **(product_loop or {}),
        **(product_catalog or {}),
        **(product_readiness or {}),
        **(launch or {}),
        **(outreach or {}),
        **(review or {}),
        **(continuation or {}),
        **(attempts or {}),
        **(reservations or {}),
        **(runs or {}),
        **(feedback or {}),
        **(views or {}),
        **(followups or {}),
        **(scheduling or {}),
        **(workstream or {}),
        **(effective_scene or {}),
        **(claim_withdrawal or {}),
    }
    handlers: dict[str, Handler] = {
        **control_handlers(
            settings=settings,
            wired=frozenset(extra),
            decisions=decisions,
            advisory=advisory,
            quay=quay,
            jobs=jobs,
            ledger_factory=ledger_factory,
            worker_outcomes=worker_outcomes,
            scenes=scenes,
            assets=assets,
            relation_store=relation_store,
            records=records,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            review=review_store,
        ),
        **extra,
    }
    resource = resource_identifier(settings)

    verifier: TokenVerifier | None = None
    auth: AuthSettings | None = None
    if ring is not None:
        verifier = TokenRingVerifier(ring, resource_url=resource)
        auth = AuthSettings(
            # 本构建里没有任何路径读这个字段：读它的两条路各需要一个本服务没有
            # 的东西（授权服务器 provider / resource_server_url）。填自己的资源
            # 地址，而不是留一个看起来像真 AS 的域名 —— 万一将来某条路径开始读它，
            # 读到的也是一个本服务认识的值。
            issuer_url=resource,
            # **None 是有意的**，见模块 docstring：只要它是真值，SDK 就会挂上
            # RFC 9728 的发现路由，把上面那个并不存在的 AS 宣告出去。
            resource_server_url=None,
            # 受众比对因此落在 TokenRingVerifier 里（SDK 那条读的正是上一格）。
            # 这不是「关掉校验」，是把它换到一个能失败的地方：环里的受众与
            # 本进程认定的资源是各自独立的值。
            validate_token_resource=False,
        )

    server = MCPServer(
        name=_SERVER_NAME,
        instructions=_INSTRUCTIONS,
        version=__version__,
        token_verifier=verifier,
        auth=auth,
        # 本部署的 Scene 就是工作区的值，与 ``_require_scene`` 用的是同一个来源
        # （``settings.profile``）。两处各推一次，会在将来某一天分开 —— 而分开的
        # 形态是「令牌被判定属于这里，而它的对象判定说不属于」。
        middleware=[
            RoleGuard(
                backed=frozenset(handlers),
                workspace=settings.profile,
                unavailable=unavailable,
            )
        ],
    )
    _register(server, handlers=handlers, unavailable=unavailable)
    return server


@dataclass(frozen=True)
class ToolBacking:
    """目录里每一条工具在这**一个服务实例**里落在三种结局的哪一种。

    三种结局是 :func:`_register` 的，不是这里的：``backed`` 接了实现、
    ``not_registered`` 被本部署裁掉（``tools/list`` 上都不出现）、``stubbed``
    还在等它的阶段。三格**互不相交**且并集是 ``TOOL_CATALOG`` —— 少了最后这一条，
    一个「谁都没接住」的名字会从三格里一起漏掉，而它的表现是自检说一切就绪。

    它不是 :class:`~marketops.security.catalog.ToolSpec` 的替代：目录答的是
    「承诺了哪几条」，这一份答的是「这一个进程把它们做到哪了」。两个问题在
    部署之间只有第一个不变。
    """

    backed: frozenset[str]
    not_registered: frozenset[str]
    stubbed: frozenset[str]


def tool_backing(server: MCPServer) -> ToolBacking | None:
    """读回 ``server`` 安装时的那三分；**装不出判定中间件时返回 ``None``**。

    ## 为什么读中间件，而不是让 ``build_server`` 多返回一格

    ``RoleGuard`` 收到的那两个集合**就是** ``_register`` 用的那两个 —— 同一次
    ``build_server`` 调用里算出来的同一对值。让 ``build_server`` 改成返回一个
    元组，三个调用点要各拆一次（其中两个只想要那个 ``MCPServer``），而多出来的
    那一格仍然是这两个集合的第二份。读回来是**同一份**。

    ## ``None`` 与「空集」是两件事

    ``None`` 表示这个 ``server`` 上没有判定中间件 —— 那是一个手搓的实例，
    关于它没有任何一句话可说。返回三只空集合会让它看起来像一个「一条都没接」
    的正常部署，而那是**相反的**结论：手搓的实例照样能调它注册过的每一条工具。
    调用方要按 ``None`` 分支，不要按空集分支。
    """
    for middleware in server.middleware:
        if isinstance(middleware, RoleGuard):
            backed = middleware.backed
            skipped = frozenset(middleware.unavailable)
            return ToolBacking(
                backed=backed,
                not_registered=skipped,
                # 目录里减去前两格。用它而不是再问一次 `_register` 拿到了什么：
                # 那个函数按名字顺序注册，而这一格的判据是**集合**，两者一致由
                # 上面 `ToolBacking` 的 docstring 那三句钉住。
                stubbed=frozenset(TOOL_CATALOG) - backed - skipped,
            )
    return None


def _register(
    server: MCPServer,
    *,
    handlers: dict[str, Handler],
    unavailable: Mapping[str, str] | None = None,
) -> None:
    """按目录注册工具：有的接实现、**有的不注册**、其余接 stub。

    ## 三种结局，不是两种

    ``handlers`` 里有 → 注册实现。``unavailable`` 里有 → **跳过**（连
    ``tools/list`` 上都不出现）。都没有 → 接一个 stub，它的回答是阶段号。

    第三种与前两种的差别是**可见性**：stub 是一条能被列举、能被调用的工具，
    它的回答是「我还没长出来」；没注册的工具在协议上**不存在**，而 §8 逐字要求
    的正是这个（「新的危险操作对普通研究身份不可见、不可调用，仅在部署能力启用时
    注册」）。一个「列得出来、点了报尚未接线」的 ``send_wave`` 会让「这一格能不能
    调」与「它存不存在」混成同一件事 —— 而前者的答案会随部署变，后者不会。

    ``tools/call`` 仍然答得出「为什么不注册」（``RoleGuard`` 的第五种回答）：
    中间件在工具解析**之前**跑，所以那句解释不依赖这条工具存在。

    ## 目录里**没有**的名字是一个构造期的拒绝，不是第四种结局

    上面三种结局都是关于**目录里的名字**说的。``handlers`` 的键若不在目录里，
    这个循环根本走不到它：它不注册、不 stub、也不报错 —— 而 ``build_server``
    那一边 **``RoleGuard`` 的 ``backed`` 拿的是 ``frozenset(handlers)``**，于是
    同一个名字在判定中间件眼里是「有实现」：调用方过了角色那一道，然后收到协议层
    一句 ``Unknown tool``，而不是这一层写的任何一种回答；``tool_backing`` 的三格
    也因此不再覆盖目录（``backed`` 里出现了目录外的名字）。

    这一种接线错误只在**一次真的调用**里显形，而它的报告面是一句协议错误 ——
    与本模块 docstring 拒绝的那种「起来了但谁都不许」同一种失败：安静、且看起来
    像部署成功。所以构造期就拒。
    """
    unknown = sorted(set(handlers) - set(TOOL_CATALOG))
    if unknown:
        raise ValueError(
            f"这些工具名不在目录里，注册不出任何东西：{unknown}。\n"
            "``_register`` 只走 ``TOOL_CATALOG``，所以这些名字既不会有实现、也不会"
            "有 stub；而 ``RoleGuard`` 的 ``backed`` 拿的是 ``handlers`` 的键，"
            "于是判定中间件会认为它们有实现 —— 调用方过了权限那一道，然后拿到一句"
            "``Unknown tool``。\n"
            "要么把名字拼对（改产出它的那个 ``*_handlers``），要么在 "
            "``security/catalog.py`` 里给它补一行目录 —— 后者是一次对外承诺，"
            "不只是让这条错误消失。"
        )
    skipped = set(unavailable or {})
    for name, spec in sorted(TOOL_CATALOG.items()):
        if name in skipped:
            continue
        handler = handlers.get(name)
        if handler is None:
            handler = _stub(name)
            description = f"{spec.summary}\n\n（Phase {spec.phase}，本构建尚未接线）"
        else:
            description = spec.summary
        server.add_tool(handler, name=name, description=description)


def _stub(name: str) -> Callable[[], dict[str, Any]]:
    """一个只回「还没接线」的处理器。

    它**不应该**被调用到：``RoleGuard`` 在参数校验之前就把这条路挡住了。留着
    它是因为「中间件一定装了」不应该是这条路径唯一的依据 —— 一个没装判定
    中间件的 ``MCPServer``（比如有人在别处手搓一个）也应当给出同一句话，
    而不是 ``Unknown tool``。
    """

    def stub() -> dict[str, Any]:
        raise ToolError(unbacked_message(name))

    stub.__name__ = name
    stub.__doc__ = f"{TOOL_CATALOG[name].summary}（Phase {TOOL_CATALOG[name].phase}，本构建尚未接线）"
    return stub
