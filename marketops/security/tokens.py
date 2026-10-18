"""令牌环：主体从哪来（§7 / §0）。

## 这是「认证」那一半

``catalog.py`` 管的是「拿到主体之后怎么判」。本模块管的是主体**从哪来**。
两者之间那条界线是整个 §7 的着力点：主体只能由传输层（HTTP ``Authorization``
头）确立，绝不能由本次调用的参数确立。所以这里的每一个函数都不接受
「调用方自称是谁」。

## 为什么盘上存的是摘要

令牌环读的是 ``MARKETOPS_MCP_TOKENS`` 指向的文件。那个路径由部署方给出，
**必须落在仓库之外**（§0 的硬约束：任何凭据都不写进 ``WORKSPACE_ROOT``）——
本仓不写死它，因为它属于部署，不属于代码，写死一个路径只会让别的机器照抄。

文件里放 ``sha256(token)`` 而不是 token 本身，因此：

* 这个文件可以被备份、被 ``cat``、被误贴进工单，而**不会**泄露一个可用的凭据；
* 反过来，摘要也无法被还原成令牌。这正是「令牌只显示一次」的代价 ——
  它写在明处，而不是被藏起来。

## 两个失败模式必须区分开

* **文件缺失 / 解析失败 / 角色名拼错** ⇒ 拒绝装载（``ConfigError``）。
  这是部署错误，必须在启动时炸，而不是让服务起来之后什么都不许调。
* **令牌不匹配** ⇒ 返回 ``None``，由上层翻译成 401。
  这是正常事件（过期、轮换、猜错），不是配置错误，不该打堆栈。

## 每一条授权还带两样：受众与有效期（格式 2）

格式 1 的条目只有「摘要 → 主体 + 角色」。那不够，缺的两样都是承重的：

* ``audience`` —— RFC 8707 的资源指示符，**这条令牌是发给谁的**。没有它，
  为 A 端铸的令牌在 B 端一样有效，而两端的环文件长得一模一样、日志里也看不
  出来。受众的比对规则**不在这里发明**：它就是 SDK 自己的那条
  （``BearerAuthBackend._issued_for_this_resource``，见 :func:`same_resource`），
  于是将来若把校验移回 SDK，两处的判决从一开始就是同一个。
* ``expires_at`` —— 到点就失效。没有它，「轮换」是唯一的撤销手段，于是一次
  泄漏的寿命等于部署的寿命，而环里没有任何一处看得出这件事。

**装载期只判这两个值能不能读，不判它们合不合理。** 「有效期不得超过一年」
（:data:`MAX_TOKEN_LIFETIME_SECONDS`）是**铸造时**的事：把它写进装载器，
一次策略调整就会让既有的全部凭据变成装载错误，而那时服务起不来、错误信息
指向文件格式 —— 排查方向整整错一层。所以上界落在 :func:`parse_duration`，
装载器对任何能解析出来的时刻一视同仁。

## 每一条授权还带一样：工作区（格式 3）

受众答「这条令牌发给哪个**服务**」，它答不了「在那个服务里，这条令牌准动哪个
**Scene** 的对象」。缺这一格时，环里所有令牌在每一个部署上角色判定完全相同 ——
一条铸给 A 场景的令牌在 B 场景照常放行，而环文件与日志上都看不出区别
（§6.1：「Role checks alone do not establish multi-tenant object isolation」）。

所以格式 3 的条目多一格必填的 ``workspaces``（非空的 profile id 列表）。
取值规则不在本模块：``workspace.check_workspace_name``，与 ``mint-token``
用的是同一个函数。

## 格式升级为什么不能就地迁移

格式 1 的条目里**没有**受众与有效期这两个值，格式 2 里**没有**工作区，所以没有
任何「补一个默认值」的迁移是诚实的 —— 补出来的默认值要么是「谁都能用」（受众空）、
要么是「永不过期」（有效期空）、要么是「哪个 Scene 都能动」（工作区空），而这三件
事正是格式 2 与格式 3 要消灭的。所以遇到旧格式时装载器报错并说清出路：把旧文件
移到一边，逐个主体重铸。摘要本身也无法换算，这一点同样的话要写在错误信息里，
否则第一个踩到的人会去找一个不存在的迁移命令。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, ValidationError

from ..domain.errors import ConfigError
from .catalog import Role
# 只用 ``parse_workspaces``：名字那一格的判据（``check_workspace_name``）在它里面，
# 而这里没有任何一处是**一个**名字单独进来的 —— 铸造时可能是多个
# （``--workspace`` 可重复），装载时可能是一个列表。两处都走同一个函数，
# 于是「命令行收下的名字」与「环里读得出的名字」不可能在某一天分开。
from .workspace import parse_workspaces

__all__ = [
    "MAX_TOKEN_LIFETIME_SECONDS",
    "TOKEN_RING_ENV",
    "TokenGrant",
    "TokenRing",
    "add_token_to_ring",
    "check_credential_path",
    "digest_of",
    "format_timestamp",
    "mint",
    "normalize_resource",
    "parse_duration",
    "refuse_collapsed_gate",
    "same_resource",
]

#: 环文件在哪 —— 部署方给的路径。**这个名字只在这里定义一次。**
#:
#: 三个读者必须读同一个变量：服务（``config.py`` 把它交给装载器）、只读探问
#: （``interfaces/mcp`` 用它在起不来时点名）、以及 worker 的身份分配
#: （``security/identity.py`` 从**服务会判的那一份环**里取身份）。名字写三遍的
#: 那一天，worker 会从一个服务不认的环里挑令牌，而那还是 §G1 那个 401，
#: 只是排查方向从「轮换」被挪到了「路径」——两句症状一样、处置不同的话。
TOKEN_RING_ENV = "MARKETOPS_MCP_TOKENS"

#: 文件格式版本。写进文件、读时校验 —— 将来格式变了，旧文件必须**报错**，
#: 而不是被按新格式读成一份「每个字段都缺」的空环。后者会让一个格式升级
#: 表现为「所有令牌突然失效」，而排查方向会落在轮换上。
_FORMAT_VERSION = 3

#: 每一个旧格式缺的是哪几格。**一句话说清出路**的那条报错要用它 —— 版本号本身
#: 说了不「缺了什么」，而读到那句话的人此刻手上正拿着一份起不来的部署。
_OLDER_FORMATS = {
    1: "受众与有效期（audience / expires_at）",
    2: "工作区（workspaces）",
}

#: 一条令牌最长能活多久：一年。
#:
#: 这个数**没有外部依据**，它是一个写下来说明的人为上界，理由只有一个 ——
#: 有效期是这个格式里必填的一格，而「必填」要能被一条判据守住。不设上界时，
#: ``--expires-in 3650d`` 与「永不过期」在效果上完全一样，于是那一格又变回了
#: 可选。一年是"足够长到不会天天来铸、足够短到泄漏有尽头"的那个取值。
MAX_TOKEN_LIFETIME_SECONDS = 365 * 24 * 60 * 60

#: ``--expires-in`` 认的单位。``d`` 是 24 小时 = 86400 秒（不是工作日、不是
#: 日历天）—— 一个含闰秒与夏令时的解释会让「30d」在两次铸造之间不一样长。
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 60 * 60, "d": 24 * 60 * 60}

#: 令牌的随机位数。32 字节 = 256 位，与 sha256 的输出等宽。
_TOKEN_BYTES = 32

#: sha256 的十六进制长度。写在这里是为了让「摘要字段长度不对」在装载期就红，
#: 而不是等某次认证静默失败。
_DIGEST_HEX_LENGTH = 64


def digest_of(token: str) -> str:
    """令牌的摘要。**单向**：这是盘上唯一被保存的形态。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def format_timestamp(epoch_seconds: int) -> str:
    """把 epoch 秒写成人能读、也能被 ``fromisoformat`` 读回来的 UTC 时刻。

    盘上写 ISO-8601 而不是整数：这个文件会被运维 ``cat`` —— 一次事故里
    「这条到什么时候为止」最好是当场能读出来的东西，而不是一个要拿去换算的
    数字。两种写法在程序里等价，只有一种在人眼里等价。
    """
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(text: Any, *, where: str) -> int:
    """ISO-8601 → epoch 秒。**必须带时区**。

    不带时区的时刻是一个悬空的值（本机在哪个时区、那一天有没有夏令时都要另外
    知道），而这三件事凑起来正是「凭据在半夜悄悄失效一小时」这类事故的成因。
    所以 ``2026-12-22T10:30:00`` 被拒，``...Z`` 与 ``...+00:00`` 收下。
    """
    if not isinstance(text, str) or not text.strip():
        raise ConfigError(f"{where} 缺 expires_at（ISO-8601 的 UTC 时刻，如 2026-12-22T10:30:00Z）")
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise ConfigError(f"{where} 的 expires_at 不是一个 ISO-8601 时刻：{text!r}") from exc
    if moment.tzinfo is None:
        raise ConfigError(
            f"{where} 的 expires_at 没有时区：{text!r}\n"
            "不带时区的时刻要靠『本机在哪个时区、那天有没有夏令时』才能定位，"
            "而那条推理不在这个文件里。请写成 UTC（结尾 Z 或 +00:00）。"
        )
    return int(moment.timestamp())


def normalize_resource(value: Any, *, where: str = "资源指示符") -> str:
    """校验并归一化一个资源指示符（RFC 8707）。

    归一化用的是 :class:`pydantic.AnyHttpUrl` —— **与 SDK 同一个工具**。这不是
    图省事：SDK 那条受众比对（``BearerAuthBackend._issued_for_this_resource``）
    用的就是它，于是「本仓认为这两个字符串是同一个资源」与「SDK 认为它们是同一
    个资源」从第一天起就是同一个判决。自己写一份大小写/默认端口的规矩，会让两
    份判决在某一天分开，而那一天的表现是「令牌在自己的验证器眼里对、在 SDK 眼
    里错」，取决于哪一半先跑。

    只收 http/https：``AnyHttpUrl`` 自己就拒绝别的 scheme，报错信息里因此不必
    再列一遍。

    两处调用它：环里的受众（``where`` 指到第几个条目）与 ``MARKETOPS_MCP_RESOURCE``
    （``where`` 指到那个变量）。同一个规则、两句说得清出处的报错。
    """
    try:
        return str(AnyHttpUrl(str(value).strip()))
    except ValidationError as exc:
        raise ConfigError(
            f"{where} 必须是一个绝对 http(s) URL（RFC 8707 资源指示符）：{value!r}"
        ) from exc


def same_resource(left: str, right: str) -> bool:
    """两个资源指示符指的是不是同一个资源。**不可解析即不匹配**。

    规则与 SDK 的 ``_issued_for_this_resource`` 逐条相同：按 URL 解析（于是
    scheme 与 host 的大小写、以及默认端口的写法都不影响判决），尾斜杠不计。
    路径不折叠、查询串不忽略 —— ``/mcp`` 与 ``/mcp/v2`` 是两个资源。

    失败关闭：任何一边解析不出来就返回 ``False``。一个「解析不了就放行」的实现
    会让一条写坏了的受众变成一张万能通行证，而它在日志里长得像一次正常的认证。
    """
    try:
        a = str(AnyHttpUrl(str(left).strip()))
        b = str(AnyHttpUrl(str(right).strip()))
    except ValidationError:
        return False
    return a.removesuffix("/") == b.removesuffix("/")


def parse_duration(text: str) -> int:
    """``30d`` / ``12h`` / ``45m`` / ``90s`` → 秒。

    **拒绝 ``never`` 一类取值**，而且是有意的一格：这个模式要求每条授权都有
    有效期（见模块 docstring），一个收下 ``never`` 的解析器会把那条要求变回
    一句倡议。上界是 :data:`MAX_TOKEN_LIFETIME_SECONDS`，理由写在那里。

    只收一个单位、一个整数：``1d12h`` 这类复合写法会让「这条到底活多久」需要
    心算，而它出现在一个安全参数上。
    """
    raw = (text or "").strip()
    unit = _UNIT_SECONDS.get(raw[-1:].lower())
    amount = raw[:-1] if unit is not None else ""
    if unit is None or not amount.isdigit():
        raise ConfigError(
            f"有效期写法不认识：{text!r}。要一个整数加一个单位：d（天）/ h（小时）/ m（分）/ s（秒），"
            "例如 30d、12h、45m。\n"
            "``never`` 不在这里的取值里，是有意的：这个模式要求每条授权都有有效期，"
            "而没有有效期的那一格正是它要消灭的东西。"
        )
    seconds = int(amount) * unit
    if seconds <= 0:
        raise ConfigError(f"有效期必须大于零：{text!r}")
    if seconds > MAX_TOKEN_LIFETIME_SECONDS:
        raise ConfigError(
            f"有效期 {text!r} 超过上界 {MAX_TOKEN_LIFETIME_SECONDS // 86400} 天"
            f"（{MAX_TOKEN_LIFETIME_SECONDS} 秒）。\n"
            "这个上界是写下来说明的一个取舍：不设它的时候，一个足够大的数字"
            "就是「永不过期」，而那一格又变回了可选。"
        )
    return seconds


def mint(principal: str, roles: list[Role]) -> tuple[str, str]:
    """铸一个令牌，返回 ``(token, digest)``。

    令牌**只在这个返回值里出现过一次**：调用方负责把它交给使用者（打印一次、
    写进对方的密钥库），而落盘的只有摘要。这是一个刻意的取舍 —— 令牌丢了
    只能重铸，不能找回，而重铸是安全的。
    """
    if not principal.strip():
        raise ConfigError("令牌必须属于一个有名有姓的主体 —— 空名字让审计无从下手")
    if not roles:
        raise ConfigError(f"主体 {principal!r} 没有任何角色 —— 这样的令牌什么都调不动")
    refuse_collapsed_gate(roles, where=f"主体 {principal!r}")
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    return token, digest_of(token)


def refuse_collapsed_gate(roles: object, *, where: str) -> None:
    """一个角色集合不得同时握住外联闸门的两半。**这里是那条规则唯一的落点。**

    §6 逐字：「授权必须来自可信、独立授权的责任人」。目录把「批准」与「投递」
    分给了两个不同的角色（``authorize_wave`` 只给 ``authorizer``，``send_wave``
    只给 ``controller``，见 ``security/catalog.py``），而**角色分开了不等于主体
    分开了** —— 一份同时带这两个 scope 的令牌让同一个主体握住两半，中间那道
    分界就只剩下目录上的一行字。

    ## 为什么在这里拒绝，而不是在 ``authorize`` 里

    ``authorize`` 是一个纯函数，它只看 ``(Principal, 工具名)``，而「这个主体
    另外还持有什么角色」在它那里恰恰是**合法**的信息：一个同时是控制者与授权人
    的主体，调 ``send_wave`` 与调 ``authorize_wave`` 各自都该被允许。要拒绝的
    是**那个角色集合的产生**，也就是铸令牌与装载环这两个动作 —— 它们各自都有
    一个能说出「第几个条目、哪个主体」的地方。

    ## 一个主体两个令牌也过不去

    装载期在 ``_parse_entry`` 里对每个条目单独判，于是「同一主体、两条条目、
    各带一半」也会红：那两条在会话层本来就不可区分（``_from_payload`` 那段
    「一个主体只能有一个角色集」），而在这一层它就是一次把闸门合回去的写法。

    规则只写在这里一处，``mint`` 与 ``TokenRing._parse_entry`` 都调它 —— 两份
    各写一遍的话，第一次改动就会分家，而分家的形态是「铸得出来但装不进去」。
    """
    granted = frozenset(roles) if isinstance(roles, (list, tuple, set, frozenset)) else frozenset()
    if {Role.CONTROLLER, Role.AUTHORIZER} <= granted:
        raise ConfigError(
            f"{where} 同时持有 controller 与 authorizer。\n"
            "这两个角色是外联闸门的两半：authorizer 批准要发给谁，controller 准备"
            "并发出去。§6 要求批准来自一个**独立**的责任人，而一个同时握住两半的"
            "主体把「谁批准的」与「谁发出去的」合成同一个答案 —— 授权记录照旧会"
            "写下来，只是它记的是同一个人的两次签名。\n"
            "出路：把这两半铸给两个主体（两个人，各自一个令牌）。代码分不出两个"
            "令牌是不是铸给了同一个人，但至少可以让「同一个人一次握住两半」需要"
            "一条改环文件的动作，而不是一条能被工具走出来的路径。"
        )


def check_credential_path(path: Path, *, repo_root: Path) -> Path:
    """凭据文件的落点必须在**仓库之外**（§0）。返回解析后的绝对路径。

    这条判据的输入只有一个路径，所以它可以被单独调用、单独测试，而不必先跑一遍
    CLI —— 「凭据永不进仓」若只写在文档里，第一个赶时间的人会把它 `--out` 到仓库里，
    而那次提交看起来和别的提交没有区别。

    ``repo_root`` 由调用方给出（而不是在这里 import 一个全局常量），因为这条判据
    本身与「本仓在哪」无关：它问的是「这个路径在不在这棵树下」。
    """
    resolved = Path(path).expanduser().resolve()
    root = Path(repo_root).resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise ConfigError(
            f"凭据文件不能落在仓库里：{resolved}\n"
            f"仓库根是 {root} —— 令牌环、私钥、cookie 一律不进版本控制（§0）。"
            f"请把它放到仓库之外的凭据库，再用 {TOKEN_RING_ENV} 指过来。"
        )
    if not resolved.parent.is_dir():
        # 不建目录：为一份凭据文件顺手把目录树造出来，正是「凭据出现在不该出现的
        # 地方」最常见的成因。路径写错了应当是**报错**，不是被默默补全。
        raise ConfigError(
            f"凭据文件的目录不存在：{resolved.parent}\n"
            "本命令不创建目录 —— 路径写错时静默补全，会让凭据落在谁都没想到的地方。"
        )
    return resolved


def add_token_to_ring(
    path: Path,
    *,
    principal: str,
    roles: list[Role],
    audience: str,
    workspaces: list[str],
    expires_in_seconds: int,
    rotate: bool = False,
) -> tuple[str, str]:
    """铸一个令牌并把它写进环文件，返回 ``(令牌, 一句说明)``。

    **令牌只在这一刻存在一次。** 落盘的是它的摘要，而摘要不可逆 —— 所以这个返回
    值是拿回令牌的唯一机会，调用方负责把它交给使用者。说明那一句里既没有令牌也
    没有摘要，可以被安全地写进日志。

    ``audience`` / ``workspaces`` / ``expires_in_seconds`` 是**必填**的，理由写在
    模块 docstring 里：这三个位置空了的时候，令牌在没有任何人察觉的情况下变成
    「哪都能用、永远有效、哪个 Scene 都能动」，而环文件本身看不出区别。

    ``workspaces`` 先逐个过 :func:`workspace.check_workspace_name` 再写 ——
    **在写之前**。写进环之后再发现名字不规范，那份环就是一份装载失败的环，而
    它看起来与一次成功的铸造没有区别。

    ``rotate=True`` 表示「这个主体本来就在环里，我是在换它的令牌」。不带这个开关
    时，主体已存在会**报错**：默认把一条既有授权悄悄换掉，是让「谁还能调」在没人
    察觉的情况下改变的那种操作。

    写盘是**原子**的（同目录临时文件 + ``os.replace``），权限是 ``0600`` —— 一份
    0644 的凭据文件与一份公开的凭据文件没有区别。
    """
    location = Path(path)
    if location.exists() and not location.is_file():
        raise ConfigError(f"令牌环的落点不是一个普通文件：{location}")

    destination = normalize_resource(audience, where="--audience")
    # 命名规则与装载器**同一个函数**（``parse_workspaces`` 也走它）。这里的顺序
    # 与下面那条范围判据一样：全部校验在**打开环文件之前**，因为「拒绝了却已经
    # 写了一半」是这份文件最不该有的形态。
    granted_workspaces = parse_workspaces(workspaces, where="--workspace", principal=principal)
    if expires_in_seconds <= 0 or expires_in_seconds > MAX_TOKEN_LIFETIME_SECONDS:
        # 参数是整数，所以这里的判据是**范围**：上界与单位解析产出的那个值是
        # 同一个取舍，抄一句而不是引用它，会让两处上界在某一天分开。
        raise ConfigError(
            f"有效期必须是 1 到 {MAX_TOKEN_LIFETIME_SECONDS} 秒，实际拿到 {expires_in_seconds}"
        )
    expires_at = int(datetime.now(tz=timezone.utc).timestamp()) + expires_in_seconds

    payload: dict[str, Any] = {"version": _FORMAT_VERSION, "tokens": []}
    if location.is_file():
        # 先按装载器的判据读一遍：一份已经坏掉的环不该被本次写入「修好」——
        # 那会把一次故障变成一次静默的凭据替换。
        # 它对格式 1 的环会报错，这是**对的**：格式 1 里没有受众与有效期这两个
        # 值，本次写入也就无法替别的条目决定它们。旧环的出路写在那个报错里。
        TokenRing.from_file(location)
        try:
            payload = json.loads(location.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - from_file 已经拦过
            raise ConfigError(f"令牌环文件读不出来：{location}（{exc}）") from exc

    entries = payload["tokens"]
    existing = [entry for entry in entries if entry.get("principal") == principal]
    token, digest = mint(principal, roles)
    if existing and not rotate:
        raise ConfigError(
            f"主体 {principal!r} 已经在环里了（{location}）。\n"
            "要换掉它的令牌，加 --rotate；换主体名，或者手工编辑那个文件。"
            "不默认替换：悄悄换掉一条既有授权，会让「谁还能调」在没人察觉时改变。"
        )
    entries[:] = [entry for entry in entries if entry.get("principal") != principal]
    entries.append(
        {
            "digest": digest,
            "principal": principal,
            "roles": [role.value for role in roles],
            "audience": destination,
            # 排序后写出：集合的写法到这里为止 —— 盘上那一格是一个**列表**，
            # 而一份「同样的授权两次铸出来字节不同」的环会让 diff 里全是噪音。
            "workspaces": sorted(granted_workspaces),
            "expires_at": format_timestamp(expires_at),
        }
    )
    payload["tokens"] = sorted(entries, key=lambda entry: str(entry.get("principal")))

    handle, temporary = tempfile.mkstemp(dir=location.parent, prefix=f".{location.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, location)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

    verb = "替换了" if existing else "新增了"
    return token, (
        f"{verb}主体 {principal!r} 的令牌（角色 {sorted(role.value for role in roles)}），"
        f"受众 {destination}，工作区 {sorted(granted_workspaces)}，"
        f"有效期至 {format_timestamp(expires_at)}（{expires_in_seconds} 秒），"
        f"写入 {location}；环里现在有 {len(payload['tokens'])} 个主体。"
        "令牌只显示一次，盘上只有它的 sha256。"
    )


@dataclass(frozen=True)
class TokenGrant:
    """一个令牌解析出来的东西：谁、哪些角色、发给谁、准动哪个 Scene、到什么时候为止。

    **不含摘要、不含令牌。** 这个对象会出现在日志、异常与调试输出里，
    而它的每一个字段都是可以安全出现的东西（主体名、角色名、资源地址、
    工作区名、时刻）。

    后三格（``audience`` / ``workspaces`` / ``expires_at``）在这里**不是可选的**：
    它们进这个对象的方式与进环文件的方式一样。让其中任何一格可以是 ``None``
    （「这条没有受众」/「这条哪都能动」）会把格式 2 与格式 3 的全部意义退化成
    一句倡议 —— 而那条倡议的失效方式恰好是：服务照常工作，令牌照常通过，只是
    它从此哪都能用、哪个 Scene 都能动、永远有效。
    """

    principal: str
    roles: frozenset[Role]
    #: RFC 8707 资源指示符，已归一化。
    audience: str
    #: 这条令牌准在哪些工作区（Scene）里动手，已按规范形式校验。非空。
    workspaces: frozenset[str]
    #: 失效时刻，epoch 秒。
    expires_at: int


@dataclass(frozen=True, repr=False)
class TokenRing:
    """一份装载好的令牌环。

    摘要 → 授权 的映射。``repr`` 被刻意关掉并重写：默认的 dataclass repr 会把
    摘要打出来，而这些对象会出现在异常链与日志里。
    """

    _by_digest: dict[str, TokenGrant]

    def __repr__(self) -> str:
        return f"TokenRing({len(self._by_digest)} 项，摘要不外显)"

    def grant_for(self, token: str) -> TokenGrant | None:
        """按令牌找授权。不匹配返回 ``None``（正常事件，不是错误）。

        比较用 ``hmac.compare_digest``：字符串 ``==`` 会在第一个不同的字符处
        返回，于是「前缀对了多少位」可以从耗时里读出来。摘要比较本来就不该
        泄漏这个，而写对它的成本是一次函数调用。
        """
        if not isinstance(token, str) or not token:
            return None
        wanted = digest_of(token)
        found: TokenGrant | None = None
        # 遍历**全部**条目而不是命中即返回：命中即返回会让「第一个条目 / 第
        # 五个条目」在耗时上可区分，而那是一次对环内容的探测。
        for digest, grant in self._by_digest.items():
            if hmac.compare_digest(digest, wanted):
                found = grant
        return found

    @property
    def principal_names(self) -> tuple[str, ...]:
        """环里出现的全部主体名（排序）。用于自检输出，不是权限判定。"""
        return tuple(sorted({grant.principal for grant in self._by_digest.values()}))

    @property
    def roles_present(self) -> tuple[str, ...]:
        """环里出现的全部角色名（排序）。"""
        return tuple(
            sorted({role.value for grant in self._by_digest.values() for role in grant.roles})
        )

    @property
    def audiences(self) -> tuple[str, ...]:
        """环里出现的全部受众（排序）。

        它是「**这条环是给哪个资源铸的**」的机器可读答案，存在的理由是诊断：
        受众不匹配与令牌写错在传输层上是同一个 401，而两者要改的东西完全不同。
        把本部署认定的资源（``check-config`` 里的 ``mcp_resource``）与这里列出的
        值放在一起看，一次错配就是两行的比对，而不是一次猜。
        """
        return tuple(sorted({grant.audience for grant in self._by_digest.values()}))

    @property
    def workspaces(self) -> tuple[str, ...]:
        """环里出现的全部工作区（排序）。

        与 :attr:`audiences` 同一条理由、同一个形状，只是回答的是另一半问题：
        受众答「这条环是给哪个**服务**铸的」，这一格答「里面这些令牌准动哪些
        **Scene**」。

        诊断面只有这一处是**已认证**的：工作区不匹配与受众不匹配一样，在调用面
        上是一条没有理由的拒绝（理由不报出本部署的 Scene 是刻意的，见
        ``workspace.require_workspace``），所以「这条令牌到底准动哪里」必须能从
        服务端自己这一侧读到。
        """
        return tuple(
            sorted({name for grant in self._by_digest.values() for name in grant.workspaces})
        )

    @classmethod
    def from_file(cls, path: Path) -> TokenRing:
        """从盘上装载。任何形态问题都抛 ``ConfigError``。

        **空环也抛。** 一个合法的空文件与一个配错的文件，在行为上是同一件事：
        服务起来，然后谁都不许。让它在装载期就炸，那件事才会在部署时被人看见。
        """
        location = Path(path)
        if not location.is_file():
            raise ConfigError(
                f"令牌环文件不存在：{location}\n"
                f"该文件由 {TOKEN_RING_ENV} 指定，应当位于凭据库（不在仓库里）。"
                "缺了它服务面**起得来但谁都不许** —— 请先用 "
                "``python -m marketops.interfaces.cli mint-token`` 铸一个。"
            )
        try:
            payload: Any = json.loads(location.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"令牌环文件读不出来或不是 JSON：{location}（{exc}）") from exc
        return cls._from_payload(payload, where=str(location))

    @classmethod
    def _from_payload(cls, payload: Any, *, where: str) -> TokenRing:
        if not isinstance(payload, dict):
            raise ConfigError(f"令牌环的顶层必须是一个对象：{where}")
        version = payload.get("version")
        if version != _FORMAT_VERSION:
            # 缺的是哪几格按**它自己的版本号**说，不是按上一版说：一句只提
            # workspaces 的报错，会让手上拿着格式 1 的人以为只差那一格，
            # 而重铸之后仍然装载失败 —— 两次失败指向同一条错误信息。
            missing = _OLDER_FORMATS.get(version, "本构建不认识的那几格")
            raise ConfigError(
                f"令牌环格式版本是 {version!r}，本构建只认 {_FORMAT_VERSION}：{where}\n"
                f"格式 {version!r} 里没有 {missing}，所以**没有**就地迁移：补出来的"
                "默认值只能是「谁都能用」、「永不过期」或「哪个 Scene 都能动」，"
                "而这三件事正是后继格式要消灭的。\n"
                "版本不符时按新格式读会把一份完整的环读成「每个字段都缺」，"
                "而那看起来像一次令牌轮换。\n"
                "出路：把旧文件移到一边（别删，它是既有授权的记录），再对每个主体重新跑一次\n"
                "``marketops mint-token --principal P --role R --audience A "
                "--workspace W --expires-in D --out 同一路径``。\n"
                "摘要无法换算 —— 令牌本来就在带外，盘上只有它的 sha256。"
            )
        entries = payload.get("tokens")
        if not isinstance(entries, list):
            raise ConfigError(f"令牌环的 ``tokens`` 必须是一个列表：{where}")
        if not entries:
            raise ConfigError(
                f"令牌环里一个令牌都没有：{where}\n"
                "空环与配错是同一件事（服务起来，然后谁都不许），所以在装载期报错。"
            )

        by_digest: dict[str, TokenGrant] = {}
        by_principal: dict[str, frozenset[Role]] = {}
        for index, entry in enumerate(entries):
            digest, grant = cls._parse_entry(entry, index=index, where=where)
            if digest in by_digest:
                raise ConfigError(f"两个条目共用一个摘要（第 {index} 项）：{where}")
            previous = by_principal.get(grant.principal)
            if previous is not None:
                # 一个主体只能有一个角色集。这不是洁癖：会话归属
                # （``streamable_http_manager`` 的 ``_session_owners``）按
                # ``(client_id, issuer, subject)`` 比对，**不看 scope**。
                # 同一个主体的两个角色集因此会被归属判成同一个人 —— 于是
                # 「研究侧令牌」与「控制侧令牌」在会话层不可区分，一次串用
                # 不会被拦下。让它在装载期就不可能存在，比事后补一条比对规则可靠。
                raise ConfigError(
                    f"主体 {grant.principal!r} 出现了两次（角色 {sorted(r.value for r in previous)} "
                    f"与 {sorted(r.value for r in grant.roles)}）：{where}\n"
                    "会话归属只按主体比对、不看角色，所以一个主体带两套角色会让两侧令牌"
                    "在会话层不可区分。"
                )
            by_digest[digest] = grant
            by_principal[grant.principal] = grant.roles
        return cls(_by_digest=by_digest)

    @staticmethod
    def _parse_entry(entry: Any, *, index: int, where: str) -> tuple[str, TokenGrant]:
        if not isinstance(entry, dict):
            raise ConfigError(f"第 {index} 个条目不是一个对象：{where}")
        digest = entry.get("digest")
        principal = entry.get("principal")
        roles = entry.get("roles")

        if not isinstance(digest, str) or len(digest) != _DIGEST_HEX_LENGTH:
            raise ConfigError(f"第 {index} 个条目的 digest 不是 {_DIGEST_HEX_LENGTH} 位十六进制：{where}")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ConfigError(f"第 {index} 个条目的 digest 含非十六进制字符：{where}")
        if not isinstance(principal, str) or not principal.strip():
            raise ConfigError(f"第 {index} 个条目缺 principal：{where}")
        if not isinstance(roles, list) or not roles:
            raise ConfigError(
                f"第 {index} 个条目（{principal!r}）的角色必须是非空列表：{where}\n"
                "空角色与拼错的角色名都会让这个令牌什么都调不动 —— 那是权限 bug 的样子，"
                "所以宁可装载失败。"
            )

        granted: set[Role] = set()
        for name in roles:
            try:
                granted.add(Role(name))
            except ValueError as exc:
                raise ConfigError(
                    f"第 {index} 个条目（{principal!r}）的角色名 {name!r} 不认识：{where}\n"
                    f"已知角色：{sorted(role.value for role in Role)}"
                ) from exc
        refuse_collapsed_gate(granted, where=f"第 {index} 个条目（{principal!r}）在 {where} 里")
        # 受众、工作区与有效期是**必填**的，同一条理由：让它们可以缺席，等于允许
        # 一条「哪都能用、永远有效、哪个 Scene 都能动」的授权混在正常的条目里，
        # 而环文件上看不出来。三者的取值在这里都只判**能不能读**，不判合不合理
        # —— 见模块 docstring。
        label = f"第 {index} 个条目（{principal!r}）"
        audience = normalize_resource(entry.get("audience"), where=f"{label} 的 audience，在 {where} 里")
        workspaces = parse_workspaces(entry.get("workspaces"), where=label, principal=principal)
        expires_at = _parse_timestamp(entry.get("expires_at"), where=f"{label} 在 {where} 里")
        return digest, TokenGrant(
            principal=principal,
            roles=frozenset(granted),
            audience=audience,
            workspaces=workspaces,
            expires_at=expires_at,
        )
