"""配置层的测试。

重点：运行目录必须显式注入、secret 不得进入任何对外投影、
以及那两个硬约束**不可能**被环境变量打开。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from marketops import RESEARCH_ONLY, SEND_AUTHORIZED
from marketops.config import REPO_ROOT, Settings, load_settings
from marketops.domain.errors import ConfigError
from marketops.security.tokens import TOKEN_RING_ENV

#: 任何**具体机器上**的绝对路径形态。错误提示里出现这些，就等于让下一个人
#: 把某个环境的路径抄到另一个环境 —— 抄来的路径在下一台机器上就是错的。
#:
#: 第一条是「盘符 + 分隔符」的形状，第二条是常见的 POSIX 顶层目录。
#: 刻意不在这里写出一条真实路径当例子：那行注释本身就会被
#: ``tests/test_portability.py`` 的扫描判成违规，而为了让它通过而放宽扫描，
#: 正是这类规则退化的方式。
MACHINE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"(^|\s)/(home|opt|srv|data|mnt|Users)/"),
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch) -> None:
    """清掉可能从真实环境漏进来的变量，保证测试可重复。"""
    for key in (
        "MARKETOPS_DATA_ROOT",
        "MARKETOPS_PROFILES_DIR",
        "MARKETOPS_PROFILE",
        "SERPER_API_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "MARKETOPS_SEND_AUTHORIZED",
        "MARKETOPS_RESEARCH_ONLY",
        "MARKETOPS_SMTP_HOST",
        "MARKETOPS_SMTP_PORT",
        "MARKETOPS_SMTP_USERNAME",
        "MARKETOPS_SMTP_PASSWORD",
        "MARKETOPS_SMTP_SECURITY",
        "MARKETOPS_OUTBOUND_FROM",
        "MARKETOPS_OUTBOUND_ALLOWLIST",
        "MARKETOPS_MAIL_CAPTURE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


# --- 运行目录必须注入 ---------------------------------------------------------


def test_missing_data_root_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="MARKETOPS_DATA_ROOT"):
        load_settings()


def test_error_message_gives_actionable_hint() -> None:
    """提示要给出**去路**，而不是给出一条可抄的路径。

    这条以前断言的是「消息里提到 Linux」—— 那是在把 Windows 时代的部署假设
    固定进测试。现在断言的是它的反面：消息里不得出现任何具体机器的路径形态，
    并且必须说清楚该值从哪里来。
    """
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)

    assert "部署配置" in message, "提示必须说明该值的来源"
    assert "不写死" in message or "显式注入" in message

    for pattern in MACHINE_PATH_PATTERNS:
        assert not pattern.search(message), f"提示里出现了具体机器路径：{message!r}"


def test_data_root_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path / "runtime"))
    settings = load_settings()
    assert settings.data_root == tmp_path / "runtime"
    assert settings.state_dir == tmp_path / "runtime" / "state"


def test_explicit_data_root_overrides_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path / "from-env"))
    settings = load_settings(data_root=tmp_path / "explicit")
    assert settings.data_root == tmp_path / "explicit"


def test_ensure_data_dirs_is_idempotent(settings: Settings) -> None:
    settings.ensure_data_dirs()
    settings.ensure_data_dirs()
    assert settings.evidence_dir.is_dir()
    assert settings.routes_dir.is_dir()
    assert settings.opportunities_dir.is_dir()


def test_routes_and_opportunities_have_distinct_directories(settings: Settings) -> None:
    """两个产出落在两个目录里 —— 名字本身就在说它们不是同一种东西。"""
    assert settings.routes_dir != settings.opportunities_dir
    assert settings.routes_dir != settings.evidence_dir
    assert settings.opportunities_dir != settings.evidence_dir


# --- 硬约束不可被环境变量打开 -------------------------------------------------


def test_send_authorized_cannot_be_enabled_by_env(monkeypatch, tmp_path: Path) -> None:
    """即使有人往环境里塞了开关，也打不开发送。

    这是刻意的：把「不会误发」做进结构里，而不是依赖配置正确。
    """
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_SEND_AUTHORIZED", "true")
    monkeypatch.setenv("SEND_AUTHORIZED", "true")

    settings = load_settings()
    assert settings.send_authorized is False
    assert settings.research_only is True


def test_module_level_flags() -> None:
    assert RESEARCH_ONLY is True
    assert SEND_AUTHORIZED is False


def test_feishu_write_authorized_cannot_be_enabled_by_env(monkeypatch, tmp_path: Path) -> None:
    """飞书**对外写入**的授权与 ``send_authorized`` 同型：环境变量打不开，也没有 setter。

    与发送授权**独立存在**：一封邮件可以发，不等于研究产出可以写进一张别人看得见
    的表里 —— 解除的条件不同，所以是两个位而不是一个。
    """
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_FEISHU_WRITE_AUTHORIZED", "true")
    monkeypatch.setenv("FEISHU_WRITE_AUTHORIZED", "true")

    settings = load_settings()
    assert settings.feishu_write_authorized is False
    with pytest.raises(AttributeError):
        settings.feishu_write_authorized = True  # type: ignore[misc]


def test_feishu_write_authorization_is_a_module_level_constant() -> None:
    """取值在模块级常量上，**默认关**。

    这条判据在有人把常量改成 True 的那天会红 —— 那一次改动需要一次业务批准，
    而这里的红就是「批准在哪」这个问题被提出来的地方。
    """
    import marketops.config as config

    assert config._FEISHU_WRITE_AUTHORIZED is False


def test_public_projection_carries_the_write_authorization(settings: Settings) -> None:
    """它进公开投影，因为运维要能**看见**它 —— 一个只能读代码才知道的开关，
    在事故复盘里等于不存在。取值本身不是秘密，所以不撞 secret 那一条判据。"""
    projection = settings.as_public_dict()
    assert projection["feishu_write_authorized"] is False


# --- secret 不进入投影 --------------------------------------------------------


def test_public_projection_contains_no_secrets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SERPER_API_KEY", "super-secret-value")
    monkeypatch.setenv("FEISHU_APP_SECRET", "another-secret")

    projection = load_settings().as_public_dict()
    serialized = repr(projection)

    assert "super-secret-value" not in serialized
    assert "another-secret" not in serialized
    for key in projection:
        assert not re.search(r"(secret|api_key|password|token)", key, re.IGNORECASE)


def test_settings_repr_does_not_leak_secret(monkeypatch, tmp_path: Path) -> None:
    """dataclass 的默认 repr 会带上字段值 —— 确认这个风险是已知的。"""
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SERPER_API_KEY", "leak-me-if-you-can")

    settings = load_settings()
    # Settings 是 frozen dataclass，repr 会包含 key —— 因此**不要**直接打印它。
    # 本测试把这个事实固定下来：一旦有人以为 repr 是安全的，这里会提醒他。
    assert "leak-me-if-you-can" in repr(settings)
    assert "leak-me-if-you-can" not in repr(settings.as_public_dict())


# --- 集成启用判定 -------------------------------------------------------------


def test_feishu_disabled_without_credentials(monkeypatch, tmp_path: Path) -> None:
    """没有飞书凭据时 pipeline 必须能独立跑完。"""
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    settings = load_settings()
    assert settings.feishu_enabled is False
    assert settings.serper_enabled is False


def test_numeric_env_parsing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_HTTP_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("MARKETOPS_MAX_CANDIDATES_PER_ACCOUNT", "7")

    settings = load_settings()
    assert settings.http_timeout_seconds == 3.5
    assert settings.max_candidates_per_account == 7


def test_invalid_numeric_env_raises(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_HTTP_TIMEOUT_SECONDS", "not-a-number")
    with pytest.raises(ConfigError, match="MARKETOPS_HTTP_TIMEOUT_SECONDS"):
        load_settings()


# --- 变量清单：读了没写进 .env.example 的名字，运维读不到 ---------------------

_CONFIG_PATH = REPO_ROOT / "marketops" / "config.py"
_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

#: ``config.py`` 里那一族读环境变量的助手。``_raw_path`` / ``_raw_float`` /
#: ``_raw_int`` / ``_raw_optional_int`` 内部都各自转交给 ``_raw``，所以五个名字
#: 都要在这里。
#:
#: ``_raw_optional_int`` 是 2026-09-24 随外联接线加进来的第五个。记一笔是为了让
#: 读到这里的人看得出这条豁免**没有放宽**：豁免的形状还是那一句「本族之间转交
#: 自己的形参」，动的只是这一族有几个成员。它多出来的语义（未设置返回 ``None``
#: 而不是一个缺省整数）是 ``config.py`` 里那个函数自己的 docstring 的事。
_ENV_READ_HELPERS = frozenset(
    {"_raw", "_raw_path", "_raw_float", "_raw_int", "_raw_optional_int"}
)


def _helper_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _env_calls(tree: ast.AST) -> list[tuple[ast.Call, str | None, frozenset[str]]]:
    """每个 ``_raw*`` 调用点，连同它所在函数的**名字**与形参名。"""
    found: list[tuple[ast.Call, str | None, frozenset[str]]] = []

    def walk(node: ast.AST, enclosing: str | None, params: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declared = frozenset(
                    arg.arg for arg in (*child.args.args, *child.args.kwonlyargs)
                )
                walk(child, child.name, declared)
                continue
            if isinstance(child, ast.Call) and _helper_name(child.func) in _ENV_READ_HELPERS:
                found.append((child, enclosing, params))
            walk(child, enclosing, params)

    walk(tree, None, frozenset())
    return found


def _env_names_read_by_config(source: Path | None = None) -> set[str]:
    """``config.py`` 到底读了哪些环境变量名。

    首参非字面量的调用点**抛出**，不是被跳过。跳过会让「有人把变量名挪进一个常量
    或一个参数里」这种改动在这条判据眼里完全隐形 —— 而一条扫不到的边界，它的读数
    与「没有这条边界」逐位相同。

    唯一的例外窄到一行：**``_raw*`` 助手本族之间**转交自己的形参
    （``_raw_path(key)`` 里那句 ``_raw(key)``）。那是同一次读取的延续。窄到这个
    形状是有意的 —— 「形参名出现在首参上」本身不能当豁免，因为
    ``def _load(name): return _raw(name)`` 长得一模一样，而它是**一次真读取**
    被藏进了一个参数里，正是这条判据要抓的东西。
    """
    tree = ast.parse((source or _CONFIG_PATH).read_text(encoding="utf-8"))
    names: set[str] = set()
    for call, enclosing, params in _env_calls(tree):
        first = call.args[0] if call.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
        elif (
            isinstance(first, ast.Name)
            and enclosing in _ENV_READ_HELPERS
            and first.id in params
        ):
            continue
        else:
            where = f"{(source or _CONFIG_PATH).name}:{call.lineno}"
            raise AssertionError(
                f"{where} 的变量名不是字面量。本扫描器只认字面量首参，"
                "所以这里停下来而不是记一笔「没看见」—— 请把名字写成字面量。"
            )
    return names


def _env_example_names(source: Path | None = None) -> set[str]:
    """``.env.example`` 里出现过的变量名。"""
    names: set[str] = set()
    for line in (source or _ENV_EXAMPLE_PATH).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.add(match.group(1))
    return names


def test_env_example_names_every_variable_config_reads() -> None:
    """``.env.example`` 是运维手上唯一一份变量清单，读了却没写进去的名字等于没有。

    这条判据的立论是 house style 的「**一条边界没有执行器就只是自觉**」：
    ``MARKETOPS_DATA_ROOT`` 缺失时 ``load_settings`` 会抛一条点名变量的
    ``ConfigError``，``composition.default_dispatcher`` 配了一半时抛的那条消息
    更是逐字点名三个环境变量 —— 而运维拿到那些名字之后，去 ``.env.example``
    里找不到它们的任何踪影。**一条只存在于错误消息里的变量名不是配置面。**

    写这条的时候它当场抓到了七个：MCP 三个（Phase C 遗留）+ 外部执行器四个。
    这正说明它不是一条恒真的判据 —— 它在被写下的那一刻就红过。

    刻意只查一个方向（读的 ⊆ 写的）。反方向（写了但没人读）不断言：
    ``.env.example`` 记下一个由别处读取的变量是合法的，把它钉死会让这份清单
    多一条与「配置面完整」无关的约束。
    """
    read = _env_names_read_by_config()
    assert {"MARKETOPS_DATA_ROOT", "SERPER_API_KEY"} <= read, (
        "正对照不成立：扫描器连这两个每天都读的变量都没扫到，"
        "那么下面那条「没有遗漏」的断言是空言。"
    )

    missing = sorted(read - _env_example_names())
    assert not missing, (
        f"config.py 读这些变量，而 .env.example 里没有它们：{missing}。\n"
        "  补进 .env.example 时**只写变量名、值一律留空** —— 那条判据在 "
        "tests/test_portability.py::test_no_absolute_paths_in_env_example_keys。"
    )


def test_the_token_ring_variable_is_spelled_the_same_in_both_places() -> None:
    """环文件那个变量名有两份写法，这条判据把它们钉在一起。

    ``security/tokens.py`` 里那个常量是**唯一的声明**（worker 领身份时读它、几条
    拒绝消息点名它），而 ``config.py`` 里那一处必须是**字面量** —— 上面那个扫描器
    只认字面量首参，它拒绝常量首参的理由是「一个挪进名字里的变量名在这条判据眼里
    完全隐形，而读数的缺失与没有这条判据逐位相同」。两条规则一起要求这两份写法
    同时存在，所以必须有一条执行器让它们漂移不了。

    两个断言各钉一半，合起来才是一条判据：前一个钉常量的**值**，后一个钉
    ``config.py`` 真的在读这个名字。只留前一个，「读了一个别的名字」看不出来；
    只留后一个，「常量被改成另一个名字」看不出来。
    """
    assert TOKEN_RING_ENV == "MARKETOPS_MCP_TOKENS"
    assert TOKEN_RING_ENV in _env_names_read_by_config()


#: 一份**可接受**的样例：一个字面量读取，外加助手把自己的形参转交出去。
_SCANNER_ACCEPTED = '''
def _raw(key, default=""):
    return default


def _raw_path(key):
    value = _raw(key)
    return value


def _load():
    return _raw("MARKETOPS_DATA_ROOT"), _raw_path("MARKETOPS_PROFILES_DIR")
'''

#: 一份**必须被拒**的样例：变量名从参数进来，扫描器看不见它。
_SCANNER_REFUSED = '''
def _load(name):
    return _raw(name)
'''


def test_the_env_scanner_sees_literals_and_refuses_a_name_it_cannot_read(tmp_path: Path) -> None:
    """扫描器自己的正负对照 —— 否则上面那条判据绿得没有理由。

    正的这一半钉住两件事：字面量读得到，以及 ``_raw*`` 本族之间转交形参不被当成
    新读取（不放过它，扫描器在真实 ``config.py`` 上会一上来就抛，而那个抛出会被
    读成「判据坏了」）。
    负的这一半是这条判据真正的靶子：``def _load(name): return _raw(name)`` 与上面
    那句转交**形状相同**，但它是一次真读取被藏进参数里。少任何一半，这条扫描都
    可能退化成恒真或恒抛。
    """
    accepted = tmp_path / "accepted.py"
    accepted.write_text(_SCANNER_ACCEPTED, encoding="utf-8")
    assert _env_names_read_by_config(accepted) == {
        "MARKETOPS_DATA_ROOT",
        "MARKETOPS_PROFILES_DIR",
    }

    refused = tmp_path / "refused.py"
    refused.write_text(_SCANNER_REFUSED, encoding="utf-8")
    with pytest.raises(AssertionError, match="不是字面量"):
        _env_names_read_by_config(refused)


# --- 外联执行：四样缺一不可，而它打不开 send_authorized（§6 / §0）--------------
#
# 这一组判据守的是「三个平面，三把各自独立的锁」里第 1 层与第 2 层的**分离**。
# 两者的方向相反：第 1 层是「谁都打不开」，第 2 层是「运维配得上就打开」。合并成
# 一个开关之后，一次 env 写错的后果会从「这个部署多了一条外发路径」变成
# 「研究身份也能发信了」—— 而那个后果在盘上看不出来。

#: 一份**完整**的外发接线。字典而不是一串 setenv：下面那个参数化用例要做的动作是
#: 「从这个完整状态里拿掉一样」，而拿掉这个动作只有在一份**具名的**清单上才写得
#: 干净 —— 七行 monkeypatch 里漏掉一行，那条判据就变成在测别的东西。
_OUTBOUND = {
    "MARKETOPS_SMTP_HOST": "smtp.example.invalid",
    "MARKETOPS_SMTP_PORT": "587",
    "MARKETOPS_SMTP_USERNAME": "sender@example.invalid",
    "MARKETOPS_SMTP_PASSWORD": "not-a-real-password",
    # 凭据的**出示方式**。它进这份清单是因为它同样不是装饰：少了它，口令被读进来
    # 却不出示，而那台路径仍然是按网络位置放行 —— ``outbound_missing`` 说得出来。
    "MARKETOPS_SMTP_SECURITY": "starttls",
    "MARKETOPS_OUTBOUND_FROM": "outreach@example.invalid",
    "MARKETOPS_OUTBOUND_ALLOWLIST": "ops@corp.example, @acme.example",
}

#: 拿掉哪一样，``outbound_missing`` 里就该出现哪个名字。两格凭据共用一格名字。
_MISSING_WHEN_REMOVED = {
    "MARKETOPS_SMTP_HOST": "smtp_host",
    "MARKETOPS_SMTP_PORT": "smtp_port",
    "MARKETOPS_SMTP_USERNAME": "smtp_credentials",
    "MARKETOPS_SMTP_PASSWORD": "smtp_credentials",
    "MARKETOPS_SMTP_SECURITY": "smtp_security",
    "MARKETOPS_OUTBOUND_FROM": "outbound_from_address",
    "MARKETOPS_OUTBOUND_ALLOWLIST": "outbound_allowlist",
}


def _set_outbound(monkeypatch, tmp_path: Path, **overrides: str) -> Settings:
    """一份完整接线，可逐格覆盖（传空串 = 拿掉那一格）。"""
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    for key, value in _OUTBOUND.items():
        monkeypatch.setenv(key, overrides.get(key, value))
    return load_settings()


def test_a_complete_wiring_means_the_deployment_has_an_outbound_path(
    monkeypatch, tmp_path: Path
) -> None:
    """正对照 —— 少了它，下面「拿掉一样就不行」那组判据会在一份恒假的实现上全绿。"""
    settings = _set_outbound(monkeypatch, tmp_path)

    assert settings.outbound_capable
    assert settings.outbound_missing == ()


@pytest.mark.parametrize("removed", sorted(_MISSING_WHEN_REMOVED))
def test_removing_any_single_piece_takes_the_path_away(
    monkeypatch, tmp_path: Path, removed: str
) -> None:
    """**清单里的任何一样都不是装饰**：拿掉它，这个部署就没有外发路径。

    参数化而不是逐条手抄的判据，理由与 ``_OUTBOUND`` 那份字典同源：手抄的那几条里
    漏掉一条，漏掉的那一样从此没有任何东西在守 —— 而它的症状是「这一格配不配都
    一样」，那正是本仓把 ``outbound_capable`` 做成派生值而不是一个布尔要防的事。
    """
    settings = _set_outbound(monkeypatch, tmp_path, **{removed: ""})

    assert not settings.outbound_capable
    assert settings.outbound_missing == (_MISSING_WHEN_REMOVED[removed],)


def test_outbound_capability_does_not_move_send_authorized(monkeypatch, tmp_path: Path) -> None:
    """§0 逐字：任何环境变量都不得把研究权限转成发送权限。

    这一条是那句话的执行器，而它必须是**同一个进程里的两个读数**：接线齐全
    （``outbound_capable`` 为真）的同时，研究平面那两个常量逐位不变。分两条测试
    写会失去意义 —— 那时它们可以各自成立而不回答「一次配置有没有把另一个打开」。
    """
    settings = _set_outbound(monkeypatch, tmp_path)

    assert settings.outbound_capable, "接线是齐的 —— 不然这一条测的是别的东西"
    assert settings.send_authorized is False
    assert settings.research_only is True
    assert SEND_AUTHORIZED is False and RESEARCH_ONLY is True


def test_a_junk_allowlist_entry_is_refused_at_load(monkeypatch, tmp_path: Path) -> None:
    """写坏的条目在**装载期**抛，不是等第一封信被拒才发现。

    与 ``security/egress.parse_allowlist`` 那条判据是同一件事在两个位置上的表现：
    那边测解析器会拒，这里测**装载真的调了它**。少了这一条，把解析从 ``load_settings``
    挪到某个属性上（或者干脆不解析、存原始字符串）不会有任何东西变红。
    """
    with pytest.raises(ConfigError, match="通配符"):
        _set_outbound(monkeypatch, tmp_path, MARKETOPS_OUTBOUND_ALLOWLIST="*")


def test_an_unset_port_is_not_the_port_zero(monkeypatch, tmp_path: Path) -> None:
    """未设置与「写了一个 0」是两件事。

    这是 ``_raw_optional_int`` 存在的全部理由：用 ``_raw_int`` 读它，两种情形都会
    得到 ``0``，于是 ``outbound_missing`` 会把「没配」报成「配了」。而 ``0`` 在
    ``SmtpMailTransport`` 里会被明确拒掉（客户端一侧的 0 没有意义）—— 写下来的人
    应当收到那句拒绝，不应当收到一句「已经配好了」。
    """
    unset = _set_outbound(monkeypatch, tmp_path, MARKETOPS_SMTP_PORT="")
    assert unset.smtp_port is None
    assert "smtp_port" in unset.outbound_missing

    explicit_zero = _set_outbound(monkeypatch, tmp_path, MARKETOPS_SMTP_PORT="0")
    assert explicit_zero.smtp_port == 0
    assert "smtp_port" not in explicit_zero.outbound_missing


def test_a_port_that_is_not_a_number_is_a_configuration_error(monkeypatch, tmp_path: Path) -> None:
    """写坏的仍然抛 —— 「未设置返回 None」不等于「读不出来也返回 None」。"""
    with pytest.raises(ConfigError, match="MARKETOPS_SMTP_PORT"):
        _set_outbound(monkeypatch, tmp_path, MARKETOPS_SMTP_PORT="abc")


def test_the_public_projection_names_what_is_missing_and_carries_no_password(
    monkeypatch, tmp_path: Path
) -> None:
    """缺哪一样要让运维看见，口令本身一个字符都不进投影。

    两半必须同时断：只断后半条（「口令没进去」），一个把 ``outbound_missing`` 永远
    返回空元组的实现照样全绿 —— 而那时启动自检会告诉运维「什么都配好了」。
    """
    settings = _set_outbound(monkeypatch, tmp_path, MARKETOPS_OUTBOUND_ALLOWLIST="")
    projection = settings.as_public_dict()
    serialized = repr(projection)

    assert projection["outbound_capable"] is False
    assert projection["outbound_missing"] == ["outbound_allowlist"]
    assert _OUTBOUND["MARKETOPS_SMTP_PASSWORD"] not in serialized
    for key in projection:
        assert not re.search(r"(secret|api_key|password|token)", key, re.IGNORECASE)
