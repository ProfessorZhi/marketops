"""收件人判据：**这一个地址准不准我们发出去**。

## 它守着三道闸里的哪一道

§6 逐字要求测试模式「从网络出口、凭据、收件人允许列表三个层面防止真实外发」。
本模块是**第三道**，也只有第三道 —— 另外两道分别住在别处，且它们必须分开：

* **网络出口**：在测试模式里，接线给的是 :class:`~marketops.ports.outreach.MailTransport`
  的一个**不联网实现**（``adapters/mail_capture_transport.py`` 把信写进一个目录）。
  它管的不是「这个地址行不行」，而是「这条路径根本不与外面通话」。
* **凭据**：一个 SMTP 传输需要一台主机、口令与一个自报发送箱
  （``adapters/smtp_mail.py``）。没有它们，那个传输**构造不出来** —— 这一道闸
  的性质是「装不起来」，而不是「装起来了但每次都拒绝」。
* **收件人允许列表**：本模块。它管的是「凭据都在、网络也通、而这个人不在名单上」。

三道是分开的，因为它们的**失败方式不同**，而要修的东西也不同：允许列表写错
要看配置，凭据缺了要去找凭据，而前两道都正常时唯一还挡着的那一道是这里。
合成一道（比如「测试模式 = 一个布尔」）会让三件各自会独立出错的事变成一个
开关，而那个开关一旦被打开，三道闸同时消失。

## 判据的形状：与 ``security/network.py`` 逐条同构

``check_recipient`` 对**一个地址**给出「允许 / 拒绝 + 理由码 + 一句人话」，理由码
是**闭集**（:class:`RecipientRefusal`）—— 理由与形状两条理由都在 ``network.py``
的模块 docstring 里写着，不在这里重抄。两处的差别只有主语：那边判「这个 URL
允不允许被访问」，这边判「这个地址准不准收我们的信」。

**一个地址一次判定**，而不是一次判一整批：允许发生在**每一封信真正出门之前**
（``adapters/guarded_mail.py`` 在 ``send`` 里逐封判），所以它必须是纯函数、
不持状态、可以在循环里被调 N 次。判一整个清单是另一件事，那是
``application/outreach.py`` 的计划期在做的，而那时还没到「谁准收信」这一步。

## 空名单是**谁都不许**，不是谁都可以

这是本模块唯一一个非显然的取值，而它的方向是刻意选的 —— 与
``security/catalog.py::Principal.workspaces`` 缺省为空集是同一条：
「没配」与「配成了空」的处置都是**停**，而那一次停是当场可见的（第一封信就被拒），
另一边的缺省（空 = 全部）会让一次漏配表现为「发得出去，看起来完全正常」。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.errors import ConfigError
from ..domain.util import is_plausible_email, normalise_email

__all__ = [
    "DOMAIN_PREFIX",
    "RecipientRefusal",
    "RecipientVerdict",
    "check_recipient",
    "parse_allowlist",
]

#: 域名条目的前缀：``@example.com`` 允许该域名下的**任何**本地部分。
#:
#: 需要这一种形态，是因为一份真实名单常常是「我们自己那个测试域下的随便哪个信箱」
#: 或者「那家客户的企业域」。把它压成一长串具体地址，会让每加一个地址都要改配置，
#: 而改配置的成本落在最不该鼓励随便改的那份文件上。
DOMAIN_PREFIX = "@"


class RecipientRefusal(str, Enum):
    """拒绝理由的**闭集**。

    开放字符串的第一个拼错的名字会静静开出一条新类别，而下游按类别分支的地方
    会落进 else —— 与 :class:`marketops.security.network.TargetRefusal` 同一条理由。
    """

    EMPTY = "empty"
    """空串。一个空的收件人不是「还没填」，是这一封信没有去处。"""

    MALFORMED = "malformed"
    """形状上不像一个可投递的地址（判据复用 ``domain.util.is_plausible_email``）。"""

    NO_ALLOWLIST = "no_allowlist"
    """允许列表是空的 ⇒ 谁都不许。见模块 docstring 最后一节。"""

    NOT_ALLOWED = "not_allowed"
    """地址完全正常，就是不在名单上。**它与上面三个的处置不同**：这个是「去找
    批这件事的人」，上面三个是「这份配置坏了」。"""


@dataclass(frozen=True)
class RecipientVerdict:
    """一次判定的结果。

    拒绝时 :attr:`detail` 必须非空：只有理由码的报告需要读代码才能解释，而读代码
    的人不是收到报告的人（``network.TargetVerdict`` 同款约定）。
    """

    allowed: bool
    refusal: RecipientRefusal | None = None
    detail: str = ""


def parse_allowlist(value: str | None) -> frozenset[str]:
    """把配置里那一格读成一个集合。**写错的条目抛 ``ConfigError``。**

    ## 为什么坏条目是**抛**，不是「跳过并记一条」

    跳过的后果取决于那个条目本来想表达什么，而这一层看不出意图：

    * 它本来是一条**允许**（``*``、``@``、多打了一个空格）—— 跳过它意味着名单比
      写的人以为的窄，第一封信被拒时才发现；
    * 它本来就是**笔误**（``a@@b.com``、``foo bar@x.com``）—— 跳过它意味着这条
      配置在盘上看起来是好的，而它从来没有生效过。

    两种都被这条规则接住：装载期一声响，比一封被拒的信早得多。

    ## 只有两种条目是合法的

    * ``someone@somewhere.example`` —— 一个具体地址；
    * ``@somewhere.example`` —— 一个域名下的任何地址。

    没有通配符。``*`` 不是「更多」的同义词，它是「这份名单不再是一道闸」——
    而一段能表达这个意思的配置，迟早会有人写上它。

    ## 这一格的形态判据比抓取那一格**严**，而那是刻意的

    收件人那一侧用的是 ``domain.util.is_plausible_email``，它对 ``a@@b.com``
    与 ``foo bar@x.com`` 都点头 —— 那是给**抓下来的**地址用的：在那一侧放严，
    代价是把一个真地址判成假的，而漏掉一个候选比多看一眼贵。

    这一格不同：它是**人手写**的一份配置，而写错的形态只有两种后果，两种都糟 ——
    一条永远匹配不上任何收件人的条目（在盘上看起来是一条覆盖，实际是空的），
    或者一条把两个地址粘成了一条的条目。所以这里在共用的形态判据之外，还要求
    这一格**恰好一个 ``@``、且不含空白与逗号**。多出来的这三条不是重复校验，
    它们判的是「这一格写的时候是不是想写一个地址」。
    """
    if value is None:
        return frozenset()
    entries: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if not item:
            continue
        if item.startswith(DOMAIN_PREFIX):
            domain = normalise_email(item[len(DOMAIN_PREFIX) :])
            if (
                not domain
                or "." not in domain
                or DOMAIN_PREFIX in domain
                or _has_separator(domain)
            ):
                raise ConfigError(
                    f"收件人允许列表里的域名条目 {item!r} 读不出一个域名。\n"
                    "域名条目的形态是 ``@example.com``：它允许该域名下的任何本地"
                    "部分。请写一个具体的域名，或者写一个具体的地址。"
                )
            entries.append(DOMAIN_PREFIX + domain)
            continue
        if "*" in item:
            raise ConfigError(
                f"收件人允许列表里的条目 {item!r} 含通配符。\n"
                "刻意不支持 —— ``*`` 不是「更多」的同义词，它说的是这份名单不再"
                "是一道闸，而那样一份配置迟早会有人写上。"
            )
        if item.count("@") != 1 or _has_separator(item):
            raise ConfigError(
                f"收件人允许列表里的条目 {item!r} 不是一个地址的写法。\n"
                f"它里面有 {item.count('@')} 个 ``@``"
                + ("，且/或含空白或逗号" if _has_separator(item) else "")
                + "。\n"
                "合法形态只有两种：``someone@example.com`` 与 ``@example.com``。\n"
                "这一格是人手写的：多打一个 ``@`` 或粘进一个空格，得到的是**一条"
                "永远匹配不上任何收件人的条目** —— 它在盘上看起来是一条覆盖，"
                "实际是空的。"
            )
        if not is_plausible_email(item):
            raise ConfigError(
                f"收件人允许列表里的条目 {item!r} 的形状不像一个可投递的地址。\n"
                "合法形态只有两种：``someone@example.com`` 与 ``@example.com``。"
            )
        entries.append(normalise_email(item))
    return frozenset(entries)


#: 名单条目里不许出现的字符：空白与逗号都是**分隔符**。它们漏进一个条目里，
#: 意味着这一格的写法与读法不一致 —— 两条粘在一起的条目会被读成一条永远
#: 匹配不上的地址，而报告上它看起来仍然是一条覆盖。
_SEPARATORS = frozenset(" \t,;")


def _has_separator(text: str) -> bool:
    return any(character in _SEPARATORS for character in text)


def _domain_of(address: str) -> str:
    local, sep, domain = address.rpartition("@")
    return domain if sep and local else ""


def check_recipient(address: str, *, allowlist: frozenset[str]) -> RecipientVerdict:
    """这个地址准不准我们发出去。

    判定顺序：**空 → 形态 → 名单是空的 → 不在名单上**。顺序有讲究，与
    ``network.check_target`` 的「先协议、再凭据、再主机」同一条：形态不成立时
    连名单都不查 —— 否则一份名单上的一个坏条目会得到「不在名单上」这个**错的**
    理由（它其实在名单上，只是那一格写坏了），而排查方向会被引到「谁批准了这个人」。
    """
    candidate = normalise_email(address)
    if not candidate:
        return RecipientVerdict(
            False,
            RecipientRefusal.EMPTY,
            "收件人是空的 —— 这一封信没有去处，而「没有去处」不是一次可以重试的失败。",
        )
    if not is_plausible_email(candidate):
        return RecipientVerdict(
            False,
            RecipientRefusal.MALFORMED,
            f"{address!r} 的形状不像一个可投递的地址（域里必须有点）。\n"
            "这一层不做 SMTP 验证、不做猜测补全 —— 它只回答「这个字符串看起来"
            "是不是一个地址」，而看起来不是的，不该被一封真信去验证。",
        )
    if not allowlist:
        return RecipientVerdict(
            False,
            RecipientRefusal.NO_ALLOWLIST,
            "收件人允许列表是空的 —— 谁都不许。\n"
            "空名单与没配是同一件事，而方向刻意选在「停」这一边：另一边的缺省"
            "（空 = 全部允许）会让一次漏配表现为「发得出去，看起来完全正常」。",
        )
    if candidate in allowlist:
        return RecipientVerdict(True)
    domain_entry = DOMAIN_PREFIX + _domain_of(candidate)
    if domain_entry in allowlist:
        return RecipientVerdict(True)
    return RecipientVerdict(
        False,
        RecipientRefusal.NOT_ALLOWED,
        f"{candidate} 不在收件人允许列表里。\n"
        "这一条与上面三条的处置不同：地址没写错、名单也没写坏 —— 差的是"
        "「谁批准过联系这个人」，而那件事只能由人来回答。",
    )
