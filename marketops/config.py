"""唯一配置入口。

纪律（由 ``tests/test_portability.py`` 强制，不是口头约定）：

* 本文件是**唯一**允许读 ``os.environ`` 的模块；其余模块只接收 ``Settings`` 实例。
* 所有路径一律 ``pathlib.Path``。禁止盘符字面量、禁止反斜杠拼接、禁止用户名。
* 运行目录全部由配置注入 —— 代码内不假定自己跑在哪个盘 / 哪个挂载点。
* 本阶段的两个硬约束（``research_only`` / ``send_authorized``）不走环境变量，
  避免「因为 env 写错就打开了发送」这一类事故。

## 三个平面，三把各自独立的锁

``send_authorized`` 与新的外联执行能力**不是同一把锁**，把它们合成一个开关是本
模块最不该做的一件事。三层的分工：

1. **研究平面**（``research_only`` / ``send_authorized``）—— 两个模块级常量，
   恒为 ``True`` / ``False``，没有 setter、不进环境、**没有一条代码路径能把它们
   读成别的值**。它们回答的是「一个研究身份能不能发信」，而那个答案是永远不能。
2. **部署接线**（:attr:`Settings.outbound_capable`）—— 回答「这个部署有没有拿到
   一条外发路径」。它是**派生**的，不是某一个布尔：要同时有中继端点、发送箱、
   凭据、**凭据的出示方式**与一份非空的收件人允许列表，缺一样就不成立
   （:attr:`outbound_missing` 说得出缺的是哪一样）。
3. **人类授权**（``domain.outreach.WaveAuthorization``）—— 回答「这一批、这些人、
   这一次，谁批准了」。它由 ``Role.AUTHORIZER`` 登记，且 ``authorized_by`` 取自
   已认证主体而不是载荷。

第 1 层与第 2 层的**方向是相反的**，而那正是它们必须分开的理由：第 1 层是「谁都
打不开」，第 2 层是「运维配得上就打开」。合成一个开关之后，一次 env 写错的后果
从「这个部署多了一条外发路径」变成「研究身份也能发信了」。
``test_outbound_capability_does_not_move_send_authorized`` 是这句话的执行器。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .domain.errors import ConfigError
from .security.egress import parse_allowlist

#: 仓库根由文件位置反推，不写死绝对路径 —— 这是可迁移性的第一块砖。
PACKAGE_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = PACKAGE_DIR.parent

#: 默认 profile。当前仅有一个，业务取值见 profiles/<id>/*.yaml。
DEFAULT_PROFILE = "uk-acme-natural-grass"

#: 本阶段硬约束。刻意不做成环境变量。
_RESEARCH_ONLY = True
_SEND_AUTHORIZED = False

#: 凭据可以按哪种方式出示。**闭集，两个成员，没有 "auto"** —— 理由写在
#: :attr:`Settings.smtp_security` 的注释里：一个自动选择的模式在下一次部署里
#: 会按中继支不支持 TLS 悄悄分成两种行为，而配置里看不出来。
SMTP_SECURITY_MODES: frozenset[str] = frozenset({"starttls", "ssl"})

#: 派发执行器的**闭集**。理由与 ``SMTP_SECURITY_MODES`` 同源：多一个拼错的取值
#: 会让「本部署不派发」与「本部署派发到一张谁也不会去领的板」在配置里长得一样，
#: 而两者在第一次受理之前都看不出来。装载期就拒掉闭集之外的字符串。
DISPATCH_EXECUTORS: frozenset[str] = frozenset({"local", "none"})

#: 飞书**对外写入**的独立授权位。与 ``_SEND_AUTHORIZED`` 同型，但**独立存在**：
#: 两者解除的条件不同 —— 那一个是「这封信可以发了」，这一个是「研究产出的那一行
#: 可以写进一个别人看得见的表了」。共用一个常量会在某一天被一起打开，而那一天
#: 没有人看得出来，因为**取值**是一样的（``domain/outreach.py`` 的模块 docstring
#: 对同一条纪律有更长的说明）。
#:
#: 它门住的是「真的往 Bitable 写」这一条路，而**不是**「读得到配置」：
#: 配了 app_id / app_secret 只说明凭据在位，与「我们被允许往那张表里写」是两件事。
_FEISHU_WRITE_AUTHORIZED = False

_DEFAULT_USER_AGENT = (
    "MarketOps/0.1 (+research; contact: local operator; respects robots and access controls)"
)


def _raw(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _raw_path(key: str) -> Path | None:
    value = _raw(key)
    if not value:
        return None
    # 允许 ``~``：展开规则由 pathlib 按当前平台决定，代码不假定平台。
    return Path(value).expanduser()


def _raw_float(key: str, default: float) -> float:
    value = _raw(key)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字，实际拿到 {value!r}") from exc


def _raw_int(key: str, default: int) -> int:
    value = _raw(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，实际拿到 {value!r}") from exc


def _raw_optional_int(key: str) -> int | None:
    """与 ``_raw_int`` 的差别只有一处，而那一处是本函数存在的全部理由。

    ``_raw_int`` 在未设置时落回一个**缺省整数**，那对「超时秒数」这类格子是对的。
    但有一类格子，**未设置**与**取某个合法整数**必须是两件事：SMTP 端口的 ``0``
    就是这样一个值 —— 它在这里的意思是「写了一个 0」（会在别处被拒），而
    「没配」的意思是「这个部署没有外发路径」。用 ``_raw_int`` 读它，两种情形
    都会得到 ``0``，于是 :attr:`Settings.outbound_missing` 分不出来。

    写坏的仍然抛：``MARKETOPS_SMTP_PORT=abc`` 是一次配置错误，不是「未设置」。

    它是 ``_raw*`` 家族的第五个成员，形状与另外三个逐字相同（转交自己的形参给
    ``_raw``）—— ``tests/test_config.py`` 的 ``_ENV_READ_HELPERS`` 因此多了一
    个名字。那不是那条判据放宽了：豁免的形状没变，只是这一族多了一个兄弟。
    """
    value = _raw(key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，实际拿到 {value!r}") from exc


def _optional_smtp_security(value: str | None) -> str | None:
    """把 ``MARKETOPS_SMTP_SECURITY`` 的原文收成 :data:`SMTP_SECURITY_MODES` 里的一个。

    未设置（含空串）返回 ``None`` —— 那是「这个部署没有外发路径」的一半，与
    ``_raw_optional_int`` 对端口那一格是同一条。

    写坏了的**抛**，而不是落回 ``None``：落回 ``None`` 会让一个拼错的值表现为
    「没配这一格」，而这两句话的处置不同（一个是去补配置、一个是去改拼写）。
    取值也不折大小写：``"StartTLS"`` 在这里被拒 —— 一个只在大小写上不同的配置项
    是下一台机器上的一处静默分歧，而它的症状是「同一份配置，两个部署的外发路径
    不一样」。

    它不是 ``_raw*`` 家族的一员（不读环境、只收一个字符串），所以
    ``tests/test_config.py`` 的 ``_ENV_READ_HELPERS`` 不需要动：那一族判的是
    「谁在读环境」，而这一支读的是调用方已经读回来的东西。
    """
    text = (value or "").strip()
    if not text:
        return None
    if text not in SMTP_SECURITY_MODES:
        raise ConfigError(
            f"MARKETOPS_SMTP_SECURITY={text!r} 不是可识别的出示方式。\n"
            f"  取值只有两个：{'、'.join(sorted(SMTP_SECURITY_MODES))}。\n"
            "  这两样不是同义词：starttls 在一条明文连接上再协商 TLS（中继不"
            "支持时**抛**，不退回明文），ssl 从一开始就连在 TLS 上。"
        )
    return text


def _dispatch_executor(value: str | None) -> str:
    """把 ``MARKETOPS_DISPATCH_EXECUTOR`` 的原文收成 :data:`DISPATCH_EXECUTORS` 里的一个。

    未设置（含空串）落回 ``"local"``：本机任务板是**正式**执行器，不需要任何第三方
    服务，所以「一个新部署没有派发路径」不是缺省状态（Issue #2 的验收条款：缺执行器
    不得成为 ``BLOCKED_EXECUTION``）。

    写坏了的**抛**，而不是落回缺省：落回会让一个拼错的取值表现为「用本地板」，
    而它与「本部署不派发」在第一次受理之前都看不出来。取值同样不折大小写，
    理由与 :func:`_optional_smtp_security` 逐字相同。
    """
    text = (value or "").strip()
    if not text:
        return "local"
    if text not in DISPATCH_EXECUTORS:
        raise ConfigError(
            f"MARKETOPS_DISPATCH_EXECUTOR={text!r} 不是可识别的执行器。\n"
            f"  取值只有：{'、'.join(sorted(DISPATCH_EXECUTORS))}。\n"
            "  local = 本机 SQLite 任务板（缺省）；none = 本部署不派发。"
        )
    return text


@dataclass(frozen=True)
class Settings:
    """一次运行的完整配置。

    ``frozen=True`` 不是为了好看：pipeline 会把 Settings 传给所有 adapter，
    冻结能保证没有哪个 adapter 偷偷改掉路径。
    """

    # --- 路径 ---
    data_root: Path
    profiles_dir: Path
    profile: str

    # --- 凭据（缺失即视为该集成未启用，不抛错）---
    serper_api_key: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_bitable_app_token: str | None = None
    feishu_bitable_table_id: str | None = None

    # --- 决策后端（§4 / §11）---
    #: Jev 的凭据。**本模块是全仓唯一读环境的地方**，所以它从这里进，再由构造方
    #: 显式传给 adapter —— ``adapters/jev.py`` 里一行 ``os.environ`` 都没有，而它
    #: 刻意**不接受 SDK 自己的环境回退**（``TYPESAFE_API_KEY``）。两者看似重复，
    #: 方向却相反：SDK 自己读环境时，那份凭据不在 ``as_public_dict()`` 的视野里，
    #: 也不受「凭据只在 vault 里」这条规则的约束。
    jev_api_key: str | None = None
    #: Laya 的本地权重目录。**要求绝对路径**：相对路径在 ``Agent`` 眼里不是本地
    #: 目录，于是它会走 ``snapshot_download`` —— 一次隐式的 421M 参数下载。
    #: 那个判断在 adapter 里（它才是知道这件事的地方），这里只承载取值。
    laya_checkpoint_dir: str | None = None
    #: assist 模式下**唯一**被咨询的那个后端，取 provider 的 ``name``。
    #:
    #: ``None`` 表示本部署没有指定 —— 而那**不是**「随便挑一个就绪的」。
    #: §4 说辅助答案进研究轨迹，那么「这条轨迹上的辅助答案来自哪个后端」必须是
    #: 一个显式的部署决定：一个静默的回落会让轨迹上写着「来自 jev」而实际来自
    #: 另一个，且没有任何一处看得出来。所以没指定时，``decide`` 是一条明确的
    #: 「本部署没有配置辅助后端」，而不是一个猜出来的答案。
    decision_provider: str | None = None

    # --- 运行参数 ---
    http_timeout_seconds: float = 20.0
    http_max_bytes: int = 8 * 1024 * 1024
    browser_timeout_seconds: float = 40.0
    max_candidates_per_account: int = 12
    user_agent: str = _DEFAULT_USER_AGENT

    # --- MCP 服务面（§6）---
    #: 绑定的**字面量**环回地址。默认值是环回而不是 ``0.0.0.0``：这台机器上
    #: 默认绑所有网卡的东西不止一个，而这一条带着一条能读证据、能提交发现、
    #: 将来能花钱的接口。落回环回是安全的那一侧，而「故意对外开放」要显式写。
    #: 名字（``localhost``）也会被拒 —— 绑定发生在解析之后，而解析是别人的事。
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8787
    #: 令牌环文件。**在凭据库里，不在仓库里**（§0）。缺失不等于「开放」，
    #: 等于「谁都不许」—— 见 ``marketops/security/tokens.py``。
    mcp_tokens_path: Path | None = None
    #: 本服务对外声称的资源指示符（RFC 8707），也是令牌受众必须等于的那个值。
    #: 未设置时按 ``mcp_host`` / ``mcp_port`` 推导（见 ``server.resource_identifier``）。
    #:
    #: **为什么它可以不等于监听地址**：绑在 ``127.0.0.1`` 上的进程，与客户端
    #: 实际用来访问它的那个地址，是两件事。放在反向代理或隧道后面时两者必然
    #: 不同，而那时把监听地址当作所有客户端的 audience 就是一条错的声明 ——
    #: ``docs/ARCHITECTURE.md`` 记的正是这一条。设置它之后，受众比对用的是这
    #: 个值；令牌受众与它不一致时一律 401（失败关闭），而那是一次**可诊断**的
    #: 错配：``--check`` 同时报本进程认定的资源与环里实际有哪些受众。
    #: 校验在 ``server.resource_identifier``（不是 http(s) 绝对 URL 就抛）。
    mcp_resource: str | None = None

    # --- 外部执行器（§6 dispatch）---
    #: 本部署用**哪一个**执行器。取值域 :data:`DISPATCH_EXECUTORS`，装载期就拒掉
    #: 别的字符串。
    #:
    #: * ``"local"``（默认）—— ``adapters/local_board.py`` 那张本机 SQLite 任务板。
    #:   它不需要任何第三方服务，因此「缺执行器 就不能研究」在这条默认值下不再成立
    #:   （Issue #2）。
    #: * ``"none"`` —— 本部署不派发。它是一条**被写下来的**事实，与「忘了配」不同：
    #:   能力表、受理回执与自检都按这个值说「本部署不派发」，而不是去猜。
    #:
    #: 外部 执行器 工作区那一路已经整体移除（Issue #2）：派发路径不再有任何第三方
    #: 依赖，也没有「配了哪几样就自动切过去」的隐式分支。
    dispatch_executor: str = "local"
    #: 本机任务板的**目录**。``None`` = ``<data_root>/dispatch``。
    #:
    #: 缺省值按 ``data_root`` 推而不是写死，理由与 ``state_dir`` 一族逐字相同：
    #: 写死一处，「同一份代码跑在不同机器上」就变成「每个环境一份代码」。
    dispatch_root: Path | None = None

    # --- 外联执行（§6）---
    #: SMTP 中继的端点。**两样都没有缺省值**，而理由与 ``dispatch_executor`` 那一组
    #: 同源、方向相反：``SmtpMailTransport`` 自己的 ``host`` 缺省是回环，
    #: 那一条决定的是「一次漏写连到哪里」；而这里的缺省决定的是「这个部署有没有
    #: 外发路径」，一个 ``127.0.0.1`` 的缺省会让 ``outbound_capable`` 凭一个
    #: **没人写过的值**成立。
    smtp_host: str | None = None
    #: 端口同样**显式**：一个缺省的 25 会让「漏配」与「配好了」在
    #: :attr:`outbound_capable` 上给出同一个答案。
    smtp_port: int | None = None
    #: 中继的用户名与口令。**两样必须同时给**（见 :attr:`outbound_missing`），
    #: 而要求凭据本身是一个刻意的取舍，写在那个属性的 docstring 里。
    smtp_username: str | None = None
    smtp_password: str | None = None
    #: 口令**怎么出示**：``"starttls"`` 或 ``"ssl"``。**没有缺省值，也没有
    #: "auto"**。
    #:
    #: 一个「有凭据就自动 STARTTLS」的实现看起来更省事，它把一次取舍藏进了一个
    #: 猜测：中继不支持 STARTTLS 时，``smtplib`` 抛（这一边是安全的），但一个
    #: 「不支持就退回明文」的实现在**同一份配置**下会把口令发到明文连接上 ——
    #: 而两者在配置里长得一模一样。写下这一格的人因此必须知道自己在选什么。
    #:
    #: 取值域是 :data:`SMTP_SECURITY_MODES`，装载期就拒掉别的字符串。
    smtp_security: str | None = None
    #: 发送箱。**不是**可选的装饰：``MailTransport`` 契约要求调用方核对「清单上的
    #: 发送箱」与「传输实际用的发送箱」，一个没配的发送箱让那次核对无从发生。
    outbound_from_address: str | None = None
    #: 收件人允许列表，**已经解析过**的那一份（``security.egress.parse_allowlist``）。
    #:
    #: 存解析后的集合而不是原始字符串：解析器对坏条目**抛**，而抛出发生在装载期
    #: 才是我们要的（``parse_allowlist`` 的 docstring 记了理由）。存字符串会让那次
    #: 解析推迟到某一次属性读取上 —— 而属性读取的调用点包括日志与自检输出，
    #: 那里的异常不会被读成「配置写错了」。
    outbound_allowlist: frozenset[str] = frozenset()
    #: **来信**的落点：一个装 ``MailCaptureSource`` 那批原件的目录
    #: （``MARKETOPS_MAIL_CAPTURE_DIR``）。空 = 本部署没有来信来源。
    #:
    #: ## 它为什么**不**并进 ``outbound_missing``
    #:
    #: 那六样回答的是「这个部署能不能构造出一条**外发**路径」。来信是反方向的：
    #: 一个只能收不能发的部署是**完整**的一种部署（§6 只要求发信那一侧有一道闸），
    #: 而一个能发不能收的部署也是完整的（回信要人工转进来）。把这一格并进去，
    #: 会让「配置了收信但没有发信」这个部署在自检里报成「缺了六样之 N」——
    #: 那是把「一个合法部署」写成了「一个残缺部署」。
    #:
    #: 但接线那一侧**真的**会看它：没有它就没有 ``ingest_replies`` 这个工具
    #: （§8「仅在部署能力启用时注册」）。所以它缺席的影响面是**一条工具**，
    #: 不是一条链。
    #:
    #: 存 ``str`` 而不是 ``Path``：与 ``data_root`` 之外的每一格同一处置 ——
    #: 一个 ``Path`` 字段的缺省需要一个哨兵（``None`` 之外的），而哨兵是
    #: 「没有配」与「配成了空」两种意思挤在一格里。
    mail_capture_dir: str | None = None

    # ------------------------------------------------------------------
    # 硬约束：不可由环境变量覆盖，也无 setter。
    # ------------------------------------------------------------------
    @property
    def research_only(self) -> bool:
        """本阶段恒为 True。"""
        return _RESEARCH_ONLY

    @property
    def send_authorized(self) -> bool:
        """**研究平面**恒为 False。任何环境变量都动不了它。

        这个属性从前读作「本仓库没有发送能力」。外联执行接线之后那句话不再准确
        —— 本仓库现在**有**一条能发信的路径，但它与研究平面是两回事：
        发送只从产品环那一条走（``outbound_capable`` + ``WaveAuthorization``
        + 收件人允许列表），而研究身份的权限集合里**没有**任何一个发送工具
        （``security/catalog.py`` 的 ``_RESEARCH``）。

        所以这个名字的意思被**收窄**了，不是被削弱：它回答的是「研究身份能不能
        发信」，答案仍是永远不能 —— ``tests/test_mcp_isolation.py`` 与
        ``test_outbound_capability_does_not_move_send_authorized`` 各守一半。
        把它删掉或改成可配置，才是削弱。
        """
        return _SEND_AUTHORIZED

    @property
    def feishu_write_authorized(self) -> bool:
        """飞书对外写入的业务授权。本阶段恒为 False（见 ``_FEISHU_WRITE_AUTHORIZED``）。

        它**不读环境变量**、**不是任何函数的参数**、**没有 setter** ——
        与 ``send_authorized`` 逐字同一条纪律。一个可以由 ``FEISHU_WRITE_AUTHORIZED=1``
        打开的开关，会在某个部署里因为一条抄错的 env 而打开，而症状是「研究产出
        悄悄出现在了别人看得见的表里」——它不报错，只是发生了。
        """
        return _FEISHU_WRITE_AUTHORIZED

    # ------------------------------------------------------------------
    # 集成启用判定
    # ------------------------------------------------------------------
    @property
    def serper_enabled(self) -> bool:
        return bool(self.serper_api_key)

    @property
    def feishu_enabled(self) -> bool:
        """飞书未配置时必须为 False —— pipeline 要能在无飞书凭据下独立跑完。"""
        return bool(self.feishu_app_id and self.feishu_app_secret)

    @property
    def jev_enabled(self) -> bool:
        """**有没有**那把钥匙。刻意不暴露它是什么，也不暴露它多长。"""
        return bool(self.jev_api_key)

    @property
    def laya_enabled(self) -> bool:
        """**有没有**配权重目录。刻意不暴露它在哪 —— 这个布尔值会进日志。"""
        return bool(self.laya_checkpoint_dir)

    @property
    def dispatch_root_resolved(self) -> Path:
        """本机任务板目录的**实际位置**。``dispatch_root`` 缺省时落在 ``data_root`` 下。

        这个推导只写在这里一处。组合根与 runner 各推一遍的话，它们会在有人改缺省值
        的那天各看一张板 —— 而两边都正常，只是活永远不会被领走。
        """
        return self.dispatch_root or (self.data_root / "dispatch")

    @property
    def dispatch_enabled(self) -> bool:
        """本部署**有没有**派发路径（配置层面；这个进程有没有把它装起来是另一件事）。

        * ``local`` —— 恒真：本机板不需要任何第三方服务，只有「目录建不出来」会让
          它假，而那在构造时已经抛了。
        * ``none`` —— 恒假：那是一句被写下来的话。

        闭集之外的值（直接构造 ``Settings`` 的调用方可以写出来）也返回假，同时由
        ``interfaces/composition.py`` 在构造执行器时抛 —— 两个读数不冲突：本属性
        答「配置层有没有意图」，那里答「这个意图能不能兑现」。
        """
        kind = (self.dispatch_executor or "").strip()
        return kind == "local"

    @property
    def outbound_missing(self) -> tuple[str, ...]:
        """这个部署**缺哪几样**才有一条外发路径。空元组 ⇔ :attr:`outbound_capable`。

        ## 为什么是逐项，而不是一个布尔

        单一开关的失败方式是这个部署里最难查的一种：置成 ``True`` 之后一切看起来
        正常，直到第一次真的尝试投递 —— 而那时的报错（连不上、发送箱没有被授权、
        收件人不在名单上、口令出不去）指向的是**几个不同的地方**。逐项各自缺的时候，
        :class:`~marketops.adapters.smtp_mail.SmtpMailTransport` 构造不出来、
        发送箱核对失败、名单挡下第一封信 —— 几种现象在报告上是几句话。

        名字（而不是取值）进公开投影：运维要能在启动前看见缺的是哪一样，而
        「口令是什么」那一格的价值只对不该看到它的人成立（与 ``laya_enabled``
        「刻意不暴露它在哪」同一条）。

        ## 凭据是**必给**的，这是一个取舍

        SMTP 可以有一类中继按**网络位置**认证（内网、可信主机），不需要口令。
        这里刻意**不**给它留口子，理由是这样要求的「凭据」会变成一个可以缺席的
        东西，而可以缺席的凭据不是一道闸 —— §6 要求凭据是三层防线里的第二层。

        另有一个更具体的理由：按网络位置认证的中继，它判的正是**网络出口**，
        而那已经是被 §6 列在第一层的东西。在这里承认它，等于把第一层与第二层折成
        一层，于是任何一层单独失效都会表现为「另一层还在」。

        这个取舍的代价是明确的：**内网无认证中继今天用不了**。它要能用的那天，
        需要一次点名的决定（一个显式的「这个中继按网络位置认证」的声明），
        而不是把这一格改成可选 —— 后者会让今天这条判据悄悄失效。

        ## 还有一格：凭据**怎么出示**

        ``smtp_security`` 与凭据是一对。少了它，上面那条「凭据必给」就只是
        「口令被读进来了」—— 而一个读进来却不出示的口令不构成任何一层防线，
        它只会让这个布尔值为真、让报告说这个部署有外发路径，同时那台中继仍在
        按网络位置放行。``SmtpMailTransport`` 两样必须同时给（它自己的构造器
        也拒**只给一样**），所以这一格缺了，那条路径根本构造不出来。
        """
        missing: list[str] = []
        if self.smtp_host is None:
            missing.append("smtp_host")
        if self.smtp_port is None:
            missing.append("smtp_port")
        if self.smtp_username is None or self.smtp_password is None:
            missing.append("smtp_credentials")
        if self.smtp_security is None:
            missing.append("smtp_security")
        if self.outbound_from_address is None:
            missing.append("outbound_from_address")
        if not self.outbound_allowlist:
            # 只报「空」，不报「一个都没配」与「配成了空」的区别：两者都落在
            # 「谁都不许」这一边，而那一边的处置是同一条。
            missing.append("outbound_allowlist")
        return tuple(missing)

    @property
    def outbound_capable(self) -> bool:
        """这个部署有没有拿到一条外发路径。**派生值，不是配置项。**

        它与 :attr:`outbound_missing` 是同一件事的两面，``outbound_capable``
        定义成 ``not self.outbound_missing`` 而不是把四样各写一遍：两处各写一遍
        的那一天会漂移，而漂移的方向恰好是「能力说 True、缺的东西还列着」。

        这个属性回答的**只是**「能不能构造出一条外发路径」。它**不**是「可以发信
        了」：每一封信出门之前还要过人类授权（``WaveAuthorization``）、清单当下
        的状态、新鲜度、抑制、回复与时间窗 —— 那些判据逐条列在
        ``application/outreach.py::dispatch_wave`` 里。
        """
        return not self.outbound_missing

    @property
    def mcp_auth_configured(self) -> bool:
        """**是否**配了令牌环。刻意不暴露它在哪里。

        服务面没有令牌环时的行为是「起得来，但谁都不许」—— 那是失败关闭的
        正确形态，而它必须能被运维在启动之前看见。这个布尔值就是那个信号，
        同时它不含路径：这个属性会进日志与自检输出，而「凭据在哪里」这条
        事实的价值只对不该看到它的人成立。
        """
        return self.mcp_tokens_path is not None

    # ------------------------------------------------------------------
    # 派生目录：全部在 data_root 之下，不散落到别处。
    # ------------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.data_root / "state"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def mirrors_dir(self) -> Path:
        """抓取到的原始页面 / API 原始响应留档。用于复现与审计。"""
        return self.data_root / "mirrors"

    @property
    def benchmarks_dir(self) -> Path:
        return self.data_root / "benchmarks"

    @property
    def inputs_dir(self) -> Path:
        """``submit_research`` 的输入清单放在这里。

        **在 ``data_root`` 下、不在 ``state/`` 下**，因为这个目录里装的是**调用方
        交进来的东西**（已知账号清单），不是我们自己写出来的状态。``state/`` 的
        语义是「重启之后还要接着用的东西」，而它确实也是 —— 但两者混在一起之后，
        「这个目录能不能整个删掉重来」这个问题就没有答案了：删 ``state/`` 会连
        别人的输入一起删。

        调用方给的是**名字**而不是路径（见 ``application/inputs.py``）：一个收路径的
        接口等于让控制端决定服务去读哪一份文件。
        """
        return self.data_root / "inputs"

    @property
    def test_data_dir(self) -> Path:
        return self.data_root / "test-data"

    @property
    def database_path(self) -> Path:
        """单机状态的 SQLite 文件。

        放在 ``state/`` 而不是 ``data_root/`` 根下：这个目录的语义是「重启之后
        还要接着用的东西」，而它确实就是。它**不在**任何按账号分目录的子树里 ——
        预算与准入是跨账号的，按账号分文件会让「总共花了多少」变成一个需要遍历
        目录才能回答的问题，而并发下的正确性恰好依赖回答这个问题时手里有锁。
        """
        return self.state_dir / "marketops.db"

    @property
    def evidence_dir(self) -> Path:
        return self.state_dir / "evidence"

    @property
    def routes_dir(self) -> Path:
        """联系路线的落盘目录。

        这里从前叫 ``contacts_dir``。改名不是整理——目录里装的从来不是
        「联系人」，而是**联系路线**：同一个人的工作邮箱与部门邮箱是两条路线，
        各自的强度与语境不同。名字叫 contact 会让人把它读成「一个人一行」，
        而那是这个产品最不该有的那个模型。
        """
        return self.state_dir / "routes"

    @property
    def route_observations_dir(self) -> Path:
        """路线**观察**的落盘目录：这是历史，只追加。

        与 ``routes_dir`` 分家（2026-09-26）是 §6 那一层的落盘形式。合在一个目录里
        曾经是这个 bug 的形状：一次研究往同一个文件里再追加一行「这个人是
        floorcare@」，而读的人问的是「现在是谁」。两件事共用一个文件，唯一的
        区分办法是读的人自己按时间排一遍 —— 而那件事没有任何执行器在保证。

        分家之后两边各自说得清：**这个目录只会变长**（一次重抓确实发生了一次），
        ``routes_dir`` 则每个规范键恒占一行。前者删掉重算要花钱，后者可以整个
        从前者折叠出来。
        """
        return self.state_dir / "route-observations"

    @property
    def route_rejections_dir(self) -> Path:
        """被拒绝的路线候选的落盘目录（``domain/route_records.RouteRejection``）。

        §6.3 要求拒绝被持久化，而且收据里要有账号、候选邮箱、来源 / 证据、原因、
        规则 / 策略、run / work item、时间。它**不并进** ``routes_dir``：
        那边装的是**成立的**路线，这边装的是**没有成立的**候选。混在一起之后，
        「这个账号有几条路线」就要靠读的人按状态过滤 —— 而报告里那句
        ``routes = N`` 会因此把被拒绝的也算进去，或者把成立的漏掉，两种都看不出来。
        """
        return self.state_dir / "route-rejections"

    @property
    def opportunities_dir(self) -> Path:
        """未解决的路线机会（有具名角色、没有 literal 邮箱）的落盘目录。"""
        return self.state_dir / "route-opportunities"

    @property
    def maintenance_dir(self) -> Path:
        """复核盘点的落盘目录（``application/refresh.py``）。

        **在 ``state/`` 下**，因为它装的是我们自己写出来的状态：一次来源供给复核
        读到了什么（``EvidenceCensus``）。它与 ``evidence_dir`` 是两样东西，
        所以不并进去 —— 那边装的是**抓到的东西**（原页面、解析结果），
        这边装的是**一次读的结果**；混在一起之后，「这个目录能不能整个重算出来」
        就没有答案了（证据重抓要花钱，盘点不用）。

        按 ``<workspace>/`` 再分一层：``EvidenceCensus`` 的第一段就是工作区，
        而工作区是授权链的第一段（``Observation`` 的 docstring 逐字如此）。
        """
        return self.state_dir / "maintenance"

    @property
    def outreach_receipts_dir(self) -> Path:
        """生成回执的落盘目录（``application/outreach.py`` 的 ``GenerationReceipt``）。

        **在 ``state/`` 下**，与 ``maintenance_dir`` 同一条理由：装的是我们自己写出来
        的状态。它与 ``evidence_dir`` 也**不并**：那边装的是抓到的东西（原页面、
        解析结果），这边装的是**一次生成动作的回执**（输入摘要、写作者与策略版本、
        正文摘要、幂等键）。混在一起之后，「这个目录删掉重跑要花多少钱」就没有
        答案了 —— 证据重抓要花钱与额度，回执重算不用。

        回执是**可被引用**的工件，所以它有文件名、有后缀、内容寻址
        （``generation_receipt_path``），而那个文件名的 id 与
        ``OutreachDraft.generation_receipt_ref`` 是同一个值。
        """
        return self.state_dir / "outreach-receipts"

    @property
    def reply_cursor_dir(self) -> Path:
        """来信游标文件的落盘目录（``adapters/mcp_server/outreach.py``）。

        游标回答「这**一个信箱**读到哪了」，所以它是**我们这边的进度**、要重启之后
        还接着用 —— 两条都指向 ``state/``。它单独一层而**不**并进
        ``maintenance_dir``：那边是一次复核读到什么（``EvidenceCensus``），这边是
        一个位置。合并之后，「这个目录能不能整个重算出来」就没有答案了。

        文件名的身份含发送箱（``reply_cursor_file``）：两个信箱的游标混成一个文件，
        第一个信箱的来信会让第二个信箱丢掉它前面那一段，而症状是「有些回信没有
        下文」—— 没有任何一处会红。
        """
        return self.state_dir / "reply-cursors"

    @property
    def notification_inbox_dir(self) -> Path:
        """本机收件箱 sink 的**根**（``adapters/notification.py::InboxSink``）。

        它是驱动那一侧唯一注册的那个出口的落点，往里一层按工作区分开：
        ``<root>/<workspace>/inbox.jsonl``（``InboxSink.path_for``）。根放在
        ``data_root`` 下、与 ``evidence_dir`` 那一族同一处置 —— 它装的是**我们自己
        投出去的东西**，不是调用方交进来的输入。

        ## 它**不**进 :meth:`ensure_data_dirs`，而这是一条判断

        ``ensure_data_dirs`` 是一个**共享**函数：服务面（``mcp/__main__._serve``）、
        控制器、CLI 都按它决定「运行目录树在不在」。往里加一格，等于让**一个 sink
        的落点**参与回答「这个部署能不能起来」—— 磁盘上是一个只读或不可写的
        ``data_root`` 时，那会把一条本来只该表现为「这一次投递没投出去」的事故
        升级成「服务起不来」。而那是两件不同的事故，处置也不同。

        目录因此**按需**建：``InboxSink.deliver`` 自己 ``mkdir(parents=True,
        exist_ok=True)``，建不出来时那一行如实落在 ``UNKNOWN`` 上，并把**真实的
        异常文本**带在 ``failure`` 里。于是「目录还不存在」不是一次需要人先准备的
        状态，也不是一次静默的丢弃。
        """
        return self.data_root / "inbox"

    def profile_dir(self) -> Path:
        """当前 profile 的目录。业务规则只从这里读。"""
        return self.profiles_dir / self.profile

    def ensure_data_dirs(self) -> None:
        """创建运行所需的目录树。幂等。"""
        for directory in (
            self.data_root,
            self.inputs_dir,
            self.state_dir,
            self.logs_dir,
            self.mirrors_dir,
            self.benchmarks_dir,
            self.test_data_dir,
            self.evidence_dir,
            self.routes_dir,
            self.route_observations_dir,
            self.route_rejections_dir,
            self.opportunities_dir,
            self.maintenance_dir,
            self.outreach_receipts_dir,
            self.reply_cursor_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_public_dict(self) -> dict[str, object]:
        """用于日志 / 报告的安全投影：**不含任何 secret**。"""
        return {
            "data_root": str(self.data_root),
            "profiles_dir": str(self.profiles_dir),
            "profile": self.profile,
            "serper_enabled": self.serper_enabled,
            "feishu_enabled": self.feishu_enabled,
            "jev_enabled": self.jev_enabled,
            "laya_enabled": self.laya_enabled,
            "decision_provider": self.decision_provider,
            "research_only": self.research_only,
            "send_authorized": self.send_authorized,
            "feishu_write_authorized": self.feishu_write_authorized,
            # 外联的那两格是**派生**的（见 ``outbound_missing`` 的 docstring）：
            # 名字进投影、取值不进。两个键都不含 secret 字样，而那不是为了绕开
            # ``test_public_projection_contains_no_secrets`` 那条正则 —— 那两个
            # 键里装的确实只是一个布尔与一串**字段名**，两者都不是凭据。
            # 凭据本身（``smtp_password``）在任何一格都不出现。
            "outbound_capable": self.outbound_capable,
            "outbound_missing": list(self.outbound_missing),
            "http_timeout_seconds": self.http_timeout_seconds,
            "browser_timeout_seconds": self.browser_timeout_seconds,
            "max_candidates_per_account": self.max_candidates_per_account,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "mcp_auth_configured": self.mcp_auth_configured,
            "dispatch_executor": self.dispatch_executor,
            "dispatch_root": str(self.dispatch_root_resolved),
            # 来信来源：**报路径**，与 ``data_root`` / ``profiles_dir`` 同一处置。
            # 一个布尔答不出「配错了目录」—— 而「配了一个存在但没有东西的目录」
            # 与「配对了」在自检里必须分得开，它们一个是去补投递、一个是去查收信。
            "mail_capture_dir": self.mail_capture_dir,
        }


def load_settings(*, data_root: Path | None = None) -> Settings:
    """从环境变量装载配置。

    ``data_root`` 显式传入时优先（测试用），否则读 ``MARKETOPS_DATA_ROOT``。

    运行数据目录**必须显式注入**：代码内不写死任何绝对路径。写死一处，
    「同一份代码跑在不同机器上」就变成「每个环境一份代码」。
    """
    resolved_root = data_root if data_root is not None else _raw_path("MARKETOPS_DATA_ROOT")
    if resolved_root is None:
        raise ConfigError(
            "MARKETOPS_DATA_ROOT 未设置。\n"
            "MarketOps 的运行数据目录必须显式注入，代码内不写死任何绝对路径。\n"
            "  该值应当来自部署配置（容器/编排的 env、或服务器的目录 SSOT），\n"
            "  而不是从某台机器上抄来的字面路径 —— 抄来的路径在下一台机器上就是错的，\n"
            "  而且它会一路静默地写在错误的地方。"
        )

    profiles_dir = _raw_path("MARKETOPS_PROFILES_DIR") or (REPO_ROOT / "profiles")

    return Settings(
        data_root=Path(resolved_root),
        profiles_dir=profiles_dir,
        profile=_raw("MARKETOPS_PROFILE") or DEFAULT_PROFILE,
        serper_api_key=_raw("SERPER_API_KEY") or None,
        feishu_app_id=_raw("FEISHU_APP_ID") or None,
        feishu_app_secret=_raw("FEISHU_APP_SECRET") or None,
        feishu_bitable_app_token=_raw("FEISHU_BITABLE_APP_TOKEN") or None,
        feishu_bitable_table_id=_raw("FEISHU_BITABLE_TABLE_ID") or None,
        # 变量名沿用 SDK 自己的那一个（``TYPESAFE_API_KEY``）：换一个名字会让
        # 运维把钥匙放进一个我们**不读**的变量里，而症状是「配了却还是 unavailable」。
        # 读它的是本模块，不是 SDK —— 那条区别写在字段的注释里。
        jev_api_key=_raw("TYPESAFE_API_KEY") or None,
        laya_checkpoint_dir=_raw("LAYA_CHECKPOINT_DIR") or None,
        decision_provider=_raw("MARKETOPS_DECISION_PROVIDER") or None,
        http_timeout_seconds=_raw_float("MARKETOPS_HTTP_TIMEOUT_SECONDS", 20.0),
        http_max_bytes=_raw_int("MARKETOPS_HTTP_MAX_BYTES", 8 * 1024 * 1024),
        browser_timeout_seconds=_raw_float("MARKETOPS_BROWSER_TIMEOUT_SECONDS", 40.0),
        max_candidates_per_account=_raw_int("MARKETOPS_MAX_CANDIDATES_PER_ACCOUNT", 12),
        # 空串（含只有空白的值）落回缺省，**不会**退化成「绑定一切」：
        # 一次漏配必须表现为「用了一台机器上的安全缺省」，而不是「对外开放」。
        mcp_host=_raw("MARKETOPS_MCP_HOST") or "127.0.0.1",
        mcp_port=_raw_int("MARKETOPS_MCP_PORT", 8787),
        # 名字**写在这里**而不是从 ``security.tokens`` 引进来：这一份读的是环境，
        # 而 ``tests/test_config.py`` 的读取面扫描器只认字面量首参（它拒绝常量的理由
        # 是「挪进一个名字的变量名在这条判据眼里完全隐形」）。两份写法靠
        # ``test_the_token_ring_variable_is_spelled_the_same_in_both_places`` 对齐。
        mcp_tokens_path=_raw_path("MARKETOPS_MCP_TOKENS"),
        # 空串落回「未设置」= 按绑定地址推导，不是一个空字符串的资源指示符。
        mcp_resource=_raw("MARKETOPS_MCP_RESOURCE") or None,
        # 外部执行器。三样都是**显式**的：没有一项有缺省值，因为每一项的缺省
        # 都会是「我猜你想跑的其实是……」，而这条路径上猜错一次就是一次真的派发。
        dispatch_executor=_dispatch_executor(_raw("MARKETOPS_DISPATCH_EXECUTOR")),
        dispatch_root=_raw_path("MARKETOPS_DISPATCH_ROOT"),
        # 外联执行（§6）。这几样都**没有缺省值** —— 缺省在这里的意思就是
        # 「这个部署没有外发路径」，而它是 :attr:`outbound_capable` 的答案本身，
        # 不该由任何一个「我猜你想连的其实是……」的值给出。
        smtp_host=_raw("MARKETOPS_SMTP_HOST") or None,
        # 端口单独读：``_raw_int`` 的缺省是 0，而 0 是一个**可以给出**的值。
        # 空串与「写了一个 0」在这些变量上必须是两件事，所以这里按字符串读，
        # 只有非空时才转成整数（同一形状的判据在 ``SmtpMailTransport`` 里）。
        smtp_port=_raw_optional_int("MARKETOPS_SMTP_PORT"),
        smtp_username=_raw("MARKETOPS_SMTP_USERNAME") or None,
        smtp_password=_raw("MARKETOPS_SMTP_PASSWORD") or None,
        # **装载期就拒掉别的字符串**：一个拼错的 "starlts" 若被原样收下，会在
        # 构造传输时变成一次「不认识这个模式」的异常 —— 而那已经是启动之后了，
        # 且症状与「配置没写」不是同一句话。闭集在这里判，与 profile 装载期
        # 拒绝未知事实名是同一条纪律。
        smtp_security=_optional_smtp_security(_raw("MARKETOPS_SMTP_SECURITY")),
        outbound_from_address=_raw("MARKETOPS_OUTBOUND_FROM") or None,
        # **装载期就解析**：坏条目在这里抛，而不是在第一次读属性时抛（理由见
        # ``outbound_allowlist`` 字段的注释）。空串与未设置都落回空集，
        # 而空集的意思是「谁都不许」，不是「谁都可以」。
        outbound_allowlist=parse_allowlist(_raw("MARKETOPS_OUTBOUND_ALLOWLIST") or None),
        # 来信那一侧。空串落回「未设置」而不是「配了一个空目录」—— 一个空目录会
        # 让接线误以为「本部署有来信来源而它恰好是空的」，而那是两件不同的事：
        # 前者是一条工具**存在**、后者是它不存在。与 ``mcp_resource`` 同一处置。
        mail_capture_dir=_raw("MARKETOPS_MAIL_CAPTURE_DIR") or None,
    )


__all__ = ["Settings", "load_settings", "REPO_ROOT", "PACKAGE_DIR", "DEFAULT_PROFILE"]
