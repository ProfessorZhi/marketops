"""范围判据：**这一个目标在不在操作员批准的那个域名集合里**。

## 它守着哪一道闸，以及它为什么不是 ``network.py`` 的一部分

``network.py`` 回答的是「这个地址**安不安全**」—— 私网、环回、链路本地、
数字形态主机名、保留名字空间。那是一条**安全性**判据，它的取值对所有人一样。

本模块回答的是另一句话：「这个地址**是不是你批准过的**」。同一台机器上，
两个项目可以批准完全不同的域名集合，而两个集合的**安全性**判据一模一样。
把两者合成一句「允不允许」，会让「拒绝」这一个词同时承载两件处置完全不同的事：
前者要改的是目标（换来源），后者要改的是配置（去要一份批准）。
``security/egress.py`` 已经是这条分法的一个先例 —— 收件人判据单独成一个模块，
而不是塞进 ``network.py``。

## 判据的形状：与另外两道逐条同构

``check_domain`` 对一个主机名给出「允许 / 拒绝 + 理由码 + 一句人话」，理由码是
**闭集**（:class:`ScopeRefusal`）—— 理由与 ``network.py`` 的模块 docstring 里
那条相同，不在这里重抄。``parse_domain_allowlist`` 对一份人手写的配置做解析，
坏条目**抛** ``ScopeConfigError``：与 ``security/egress.py::parse_allowlist`` 同款
理由（写错的条目在盘上看起来是一条覆盖，实际匹配不上任何东西）。

## 为什么坏条目抛的是 ``ScopeConfigError`` 而不是 ``domain.errors.ConfigError``

因为**本模块必须能在不装核心包的前提下被 import**。

``marketops/security/network.py`` 与 ``marketops/ports/dns.py`` 都是纯标准库的
模块；本模块与它们一起被**逐字节**复制进独立发行的插件包
（``plugins/research-tools/marketops-research-assistant/scripts/marketops_safe_egress/``，
见 ``tests/test_wave1_egress_bundle.py``）。那个包不装核心运行时，
所以它的任何一个文件 import 了 ``marketops.domain.*`` 都会让整个出口层在客户机上
import 不了 —— 而 ``domain/errors.py`` 自己还牵着 ``domain/access.py``。

``ScopeConfigError`` 继承 ``ValueError``，所以「坏配置」这件事在调用方那里仍然
是一个可以按类型接住的信号；核心侧若需要 ``ConfigError`` 的语义，在调用点包一层
就是一行，而反过来（为了异常类型统一，让一整个发行面跟着核心树走）代价大得多。
**这一条不是风格选择，是发行边界的落点。**

## 空名单是**谁都不许**，而这是本模块唯一一个非显然的取值

与 ``security/egress.py`` 和 ``security/catalog.py::Principal.workspaces`` 同向：
「没配」与「配成了空」的处置都是**停**。另一个方向（空 = 全部允许）会让一次漏配
表现为「抓得出去，看起来完全正常」—— 而那正是范围闸存在的理由被抹掉的那种失败。

## 它**不**做的两件事

* **不解析。** 它判的是写下来的那个名字。名字解析成什么地址由
  ``network.check_resolved`` 那一半回答，两条判据在调用方那里**都要过**。
* **不做子域通配的同义词猜测。** 一条 ``example.com`` 覆盖的是它自己与它的子域
  （``a.example.com``），**不**覆盖 ``notexample.com``，也不覆盖
  ``example.com.evil.test``。判据是「等于，或者以 ``.<条目>`` 结尾」，而不是
  ``endswith`` —— 后者会把 ``evil-example.com`` 读成 ``example.com`` 的子域。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ScopeConfigError",
    "ScopeRefusal",
    "ScopeVerdict",
    "check_domain",
    "normalise_host",
    "parse_domain_allowlist",
]


class ScopeConfigError(ValueError):
    """批准名单里有一条读不出域名的条目。

    **不是** ``domain.errors.ConfigError`` —— 理由写在模块 docstring 那一节里：
    本模块要能被逐字节复制进不装核心包的独立插件，所以它不能 import 核心树。
    """


#: 一个域名标签。刻意**不**接受下划线以外的东西 —— 域名里允许的字符集比这个宽
#: （国际化域名的 punycode、某些注册局的怪标签），但一份**人手写的批准名单**不
#: 该在这里放宽：窄的判据拒绝一次，写的人当场就知道要改哪一格；宽的判据放行一次，
#: 谁都不会知道。
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class ScopeRefusal(str, Enum):
    """拒绝理由的闭集。"""

    NO_HOST = "no_host"
    """没有主机名 —— 一个空目标不是「还没填」，是这一跳没有去处。"""

    IP_LITERAL = "ip_literal"
    """目标是 IP 字面量。见 :func:`check_domain` 里那一段理由。"""

    EMPTY_ALLOWLIST = "empty_allowlist"
    """批准名单是空的 ⇒ 谁都不许。见模块 docstring 最后一节。"""

    NOT_APPROVED = "not_approved"
    """域名完全正常，就是不在名单上。**它与上面三个的处置不同**：这个是「去找
    批这件事的人」，上面三个是「这份配置坏了」—— 与
    ``security/egress.py::RecipientRefusal.NOT_ALLOWED`` 那条同款。"""


@dataclass(frozen=True)
class ScopeVerdict:
    """一次判定的结果。拒绝时 :attr:`detail` 必须非空 —— 只有理由码的报告需要
    读代码才能解释，而读代码的人不是收到报告的人。"""

    allowed: bool
    refusal: ScopeRefusal | None = None
    detail: str = ""


def normalise_host(host: str) -> str:
    """主机名的比较形态：小写，去掉末尾那个根点。

    FQDN 末尾的根点不改变解析结果（``network.check_target`` 同一条），所以
    ``example.com.`` 与 ``example.com`` 必须是同一个判定 —— 否则一份名单会因为
    写没写那个点而变成两个人眼中的两份名单。
    """
    candidate = host.strip().lower()
    if len(candidate) > 1 and candidate.endswith("."):
        candidate = candidate[:-1]
    return candidate


def parse_domain_allowlist(value: str | None) -> frozenset[str]:
    """把配置里那一格读成一个集合。**写错的条目抛 :class:`ScopeConfigError`。**

    合法形态只有一种：一个域名，例如 ``example.com``。没有 ``*``、没有 ``http://``、
    没有端口、没有路径、没有 ``@``。刻意不支持子域通配 —— 一条 ``*.example.com``
    与一条 ``example.com`` 在**判据**上是同一件事（子域本来就被覆盖），而多出来的
    那个 ``*`` 只会在某一份实现里变成 ``endswith`` 那个错。

    ``None`` 与空串都读成空集，而空集是**谁都不许**（见模块 docstring）。
    """
    if value is None:
        return frozenset()
    entries: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if "*" in item:
            raise ScopeConfigError(
                f"范围名单里的条目 {item!r} 含通配符。\n"
                "刻意不支持 —— 子域本来就被一条 ``example.com`` 覆盖，而 ``*`` 在"
                "某一份实现里迟早会变成 ``endswith`` 那个错（``evil-example.com`` "
                "会被读成 ``example.com`` 的子域）。"
            )
        if not _is_plausible_domain(item):
            raise ScopeConfigError(
                f"范围名单里的条目 {item!r} 读不出一个域名。\n"
                "合法形态只有一种：``example.com``。不要写协议、端口、路径、``@``，"
                "也不要写一个 IP 字面量 —— 这一格是**人手写的批准**，而一个写坏的"
                "条目在盘上看起来是一条覆盖，实际匹配不上任何目标。"
            )
        entries.append(item)
    return frozenset(entries)


def _is_plausible_domain(value: str) -> bool:
    """一个域名标签序列，且**不是** IP 字面量。"""
    if not value or "." not in value or len(value) > 253:
        return False
    if any(character in value for character in "/:@?#[] \t"):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return False
    labels = value.split(".")
    if any(not label or len(label) > 63 for label in labels):
        return False
    return all(_LABEL.match(label) for label in labels)


def _matches(host: str, entry: str) -> bool:
    """``example.com`` 覆盖 ``example.com`` 与 ``a.example.com``。

    判据是「等于，**或者**以 ``.<条目>`` 结尾」—— 那个点是有意的：只写
    ``endswith`` 会把 ``evil-example.com`` 读成 ``example.com`` 的子域，
    而那个错不会报错，只会多放行一个域名。
    """
    return host == entry or host.endswith("." + entry)


def check_domain(host: str, *, allowlist: frozenset[str]) -> ScopeVerdict:
    """这个主机名在不在批准的域名集合里。

    判定顺序：**空 → IP 字面量 → 名单是空的 → 不在名单上**。顺序有讲究：一份空的
    名单配上一个坏目标时，理由是「名单是空的」而不是「不在名单上」—— 后者会把
    排查方向引向「谁批准了这个域名」，而真正要改的是那一格配置。

    ## 为什么 IP 字面量被拒，而不是被当成一个可以批准的域名

    一份批准是一句可以被人复核的话：「我们批准抓 ``example.com``」。一个 IP
    字面量说不出这句话 —— 它没有名字、没有到期日，也没有一条 DNS 记录能解释
    它明天会变成谁的机器。要放行某个地址，先给它一个名字，再批准那个名字。
    这一条同时把「名单里混进一个 IP」这条静默放宽的路关掉了。
    """
    candidate = normalise_host(host)
    if not candidate:
        return ScopeVerdict(
            False,
            ScopeRefusal.NO_HOST,
            "目标里没有主机名 —— 一个空目标不是「还没填」，是这一跳没有去处。",
        )
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        return ScopeVerdict(
            False,
            ScopeRefusal.IP_LITERAL,
            f"{candidate!r} 是 IP 字面量。一份批准是一句可以被人复核的话"
            "（「我们批准抓 example.com」），而一个地址说不出这句话：它没有名字、"
            "没有到期日，明天可能是另一个人的机器。要放行它，先给它一个名字。",
        )
    if not allowlist:
        return ScopeVerdict(
            False,
            ScopeRefusal.EMPTY_ALLOWLIST,
            "批准名单是空的 —— 谁都不许。\n"
            "空名单与没配是同一件事，而方向刻意选在「停」这一边：另一边的缺省"
            "（空 = 全部允许）会让一次漏配表现为「抓得出去，看起来完全正常」。",
        )
    if any(_matches(candidate, entry) for entry in allowlist):
        return ScopeVerdict(True)
    return ScopeVerdict(
        False,
        ScopeRefusal.NOT_APPROVED,
        f"{candidate} 不在批准的域名集合里。\n"
        "这一条与上面三条的处置不同：域名没写错、名单也没写坏 —— 差的是"
        "「谁批准过这个来源」，而那件事只能由人来回答。",
    )
