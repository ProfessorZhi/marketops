"""配对键 —— 把两支臂的候选落到**同一个单位**上。

报告里要输出 ``C - B = Skill 净新增合格 Account``。这个差值成立的**前提**
是两支臂的候选能被配对到同一个单位：配不上时，「Skill 多找到了一个」
与「同一件事被两边各写了一遍」在报告上是同一行。所以配对键必须比
「这一行的 URL」粗，而且粗细程度必须是**写下来的、逐条可测的**规则，
不是某次实现的副作用。

# 为什么这一层在 ``application/`` 而不是 ``domain/``

它是一个纯函数、零 I/O、零市场知识 —— 按这些特征它看起来该与
``domain/util.py`` 的 ``normalise_email`` 并列。它没有去那里，只因为一件事：
``_host_of`` 用 ``urllib.parse.urlsplit``，而 ``urllib`` 在
``tests/test_domain_is_pure.py`` 的 ``FORBIDDEN_MODULES`` 里。

那条清单是 import 级的、刻意粗的（它把 ``urllib.request`` 与 ``urllib.parse``
放在同一条线上），而本仓的纪律是**改设计去适应判据，不是改判据去适应设计**。
手写一个 URL 解析器来绕过这条 import 是另一个方向上的坏：``urlsplit`` 处理
IPv6 字面量、userinfo、端口合法性、百分号编码的那些边角，是本模块**故意**
不自己猜的东西（见 ``_host_of`` 的 docstring）。

于是落点选在这里，并把这个理由写下来 —— 免得下一个人以为是没人管。

# 规范化规则（每条都有正反例，见 ``tests/test_discovery_pairing.py``）

**输入形状**：只接受两种。``https://host/path`` 这样的绝对 URL（scheme 必须是
http 或 https），或者一个裸主机名（``OfficialDomain`` 存的就是这种，见
``marketops/domain/candidate.py`` 的 ``official_domain``）。没有 scheme 却带路径的串
（``host/contact``）**拒绝**：它既可读成「主机 + 路径」也可读成别的，
猜错的那一次会把两条不相干的候选并成一个单位。

* **scheme** —— ``http`` 与 ``https`` 是**同一台主机**，scheme 完全不进键。
  配对单位是主机（责任实体的近似），不是传输层；同一个站点的两种 scheme
  是同一个所有者，而把它们分开会让同一块场地被两边各记一条。
  别的 scheme（``ftp`` / ``mailto`` / ``javascript`` / ``file`` …）**拒绝**：
  它们要么没有主机，要么指的不是一个网页归属，折成同一个键等于断言
  「两者是同一个所有者」，而这件事没有证据。
* **``www.`` —— 删，且只删一次、只删最前面那一个。** 这是本模块**显式**
  做的决定，不能靠 ``slugify`` 的副作用：``slugify`` 不摘 ``www.``
  （``www.example.co.uk`` 原样保留），于是同一台主机的两种拼写会变成两组
  互不相干的配对。删完必须仍是一个域名（``www.example`` 少一个标签就留下不动）。
  **别的子域一律保留**（``ww2.`` / ``web.`` / ``m.`` 不是约定俗成的别名），
  也**不折成注册域**（eTLD+1）—— 理由见下面那条缝。
* **尾斜杠 / 路径 / 查询串 / fragment —— 全丢**，只留主机。这是本模块粗化的
  主要来源：``/contact`` 与 ``/team`` 是同一个所有者的两个页面。
* **端口 —— 都不进键**，默认端口（80/443）与非默认端口一样丢。
  端口是传输端点，不是主机身份；把 ``:8443`` 编码进键会造出一个永远配不上
  裸主机拼写的键，于是**同一台主机被算成两条净新增** —— 那正是本模块要避免的
  失真方向。代价：同一台主机上两个不同的服务会被并成一个单位。
  另有一条硬约束：键要存进 ``AccountCandidate.pairing_key``，那里走
  ``check_canonical_id``，而它禁止 ``:`` —— 带端口的键在构造期就会报错。
* **大小写 —— 折叠。** 不能指望库替我们做：``idna`` 只对非 ASCII 标签做
  nameprep，纯 ASCII 标签**原样返回**（实测 ``'PROBE.Example.ORG'.encode('idna')``
  仍是 ``PROBE.Example.ORG``），所以大小写必须显式折叠。
* **IDN —— 一律转成 punycode。** ``münchen.example`` 与 ``xn--mnchen-3ya.example``
  是同一台主机的两种写法，必须是同一个键；转不动的（空标签、超长标签）拒绝。
* **主机名语法** —— 每个标签是 ``[a-z0-9-]``、不以连字符开头或结尾、至少两个
  标签（单标签的 ``localhost`` 不是公开域名）、末标签不是纯数字（IP 字面量不是
  域名）、总长不超过 ``PAIRING_KEY_MAX_LENGTH``。末尾的根点（``host.``）与不带
  根点是同一台主机。这些都是 DNS 语法，不含任何注册知识。
* **失败关闭** —— 拿不到可用的主机就**抛**，不返回 ``""``。空键在契约里的
  含义是「这一行没有可配对的域名」；把「域名写坏了」也塌缩成空键，两者就
  在数据上长得一样了（``marketops/domain/candidate.py`` 为同一理由把「空」与
  「非空但不规范」分成两条路）。

# 一条必须写明的边界：这一段**不**做抓取层去重

仓库里已有的 URL 同一性判定是**字节精确**的：``marketops/adapters/http.py`` 的
``_persist`` 用 ``sha256_text(url)[:16]`` 给镜像命名，刻意不规范化。实测后果是
同一个页面的 6 个 ``?page=N`` 变体会变成 6 次抓取、6 次解析、**1 个身份**
（``tests/test_discovery_pairing.py::test_page_variants_are_one_pairing_key_and_six_fetches``
把这两个数一起钉住；上一轮的探针
``ARTIFACTS_ROOT/skills/marketops-contact-research/probes/page-variants-are-separate-fetches.py``
就是为这件事写的）。

配对键是另一件**更粗**的事，两者不要互相迁就：抓取层去重会改掉「抓到几页」
这个读数，而它是指标的一部分；配对层不粗化则会让同一块场地的两条候选在
报告里算成两条。所以抓取层维持字节精确，粗化只发生在这一层。

# 一条**没有**完全堵上的缝：主机名不是所有者

同一台 host 下可能挂着多个不同的责任实体（聚合域名），它们会被配成
一对，而报告上原来**看不出来**。本模块为此做了两件事，都刻意不是域名黑名单 ——
``tests/test_no_hardcoded_market_knowledge.py`` 禁止 ``marketops/`` 里出现任何
具体市场的取值，而「哪些域名是聚合域名」恰恰是随注册与运营变化的**数据**，
不是引擎能拥有的知识：

1. **不为它扩权**：只删最前面那一个 ``www``，不折成注册域。折成 eTLD+1 会把
   这条缝**最大化** —— 那需要一张公共后缀表（随注册局变化的注册数据），
   而本模块一个域名都不认识。
2. **让它看得见**：:func:`keys_with_multiple_organisations` 由候选行自己算出
   「哪些配对键底下不止一个责任实体」。判定完全来自行上的证据
   （同一个键、不同的 ``organisation``），没有任何一个域名被写进代码。

**没做**的是：这件事仍然只是**被指出来**，不是被阻止。两个实体共用一个 host
时，:func:`pairing_key_for` 仍然给出同一个键，仍然会配成一对 ——
把 host 级的合并拆开需要「责任实体」这一层的证据，而这一段还没有它。
判定「没有配对身份 ⇒ 进 hold」的那一侧由下游实现，它的依据是行上的证据
（有没有域名、有没有指着域名的证据），不是一张域名表。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from ..domain.candidate import PAIRING_KEY_MAX_LENGTH, AccountCandidate

#: 唯一被当作**别名**去掉的标签。它是协议约定，不是市场知识。
_ALIAS_LABEL = "www"

#: 只有这两个 scheme 是「同一个网页归属」的两种传输方式。
_WEB_SCHEMES = frozenset({"http", "https"})

#: DNS 标签的合法字符。大小写折叠与 punycode 转换之后，标签里只该剩下这些。
_LABEL_SHAPE = re.compile(r"[a-z0-9-]+")

#: 裸主机名形状里不允许出现的字符：出现任何一个就说明它不是「主机名」，
#: 而是别的东西（路径、查询串、userinfo、协议相对 URL）。
_NOT_A_BARE_HOST = frozenset("/?#@")


def _host_of(text: str) -> str:
    """从两种被接受的形状里取出主机；其余一律抛。

    形状判定是**显式**的，不靠 ``urlsplit`` 猜：``urlsplit("probe.example.org:8443")``
    会把 ``probe.example.org`` 当成 scheme（它确实是合法的 scheme 字符集），
    于是同一个输入在两台机器上会走两条不同的分支。
    """
    if "://" in text:
        try:
            parts = urlsplit(text)
            scheme = parts.scheme.lower()
            username = parts.username
            host = parts.hostname
            _ = parts.port  # 只为校验端口合法：它的值**不进键**
        except ValueError as exc:  # 非法 IPv6、非数字端口等
            raise ValueError(f"{text!r} 不是一个可用的 URL：{exc}") from exc
        if scheme not in _WEB_SCHEMES:
            raise ValueError(
                f"{text!r} 的 scheme 是 {scheme!r}；配对只接受 http/https。"
                "别的 scheme 要么没有主机，要么指的不是一个网页归属。"
            )
        if username is not None or parts.password is not None:
            raise ValueError(
                f"{text!r} 带 userinfo。userinfo 不是主机身份的一部分，"
                "而且它常常是凭据 —— 不解析、不保留。"
            )
        if host is None:
            raise ValueError(f"{text!r} 里没有主机")
        return host

    if any(ch in _NOT_A_BARE_HOST for ch in text):
        raise ValueError(
            f"{text!r} 既不是一个绝对 URL（不含 scheme 分隔符），也不是一个裸主机名。"
            "没有 scheme 的串里带路径/查询串/fragment 时，"
            "「主机 + 路径」与别的读法分不开 —— 不猜。"
        )
    host, separator, port = text.partition(":")
    if separator and not port.isdigit():
        raise ValueError(
            f"{text!r} 不是一个裸主机名：冒号后面 {port!r} 不是端口号。"
            "带 scheme 但不是 http/https 的串也走这条（mailto: / tel: / javascript: …）——"
            "它们都不是一个网页归属。"
        )
    return host


def _normalize(host: str) -> str:
    """把一台主机折成它的规范形式；折不动就抛。"""
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise ValueError("主机是空的")
    if ":" in host:
        raise ValueError(
            f"{host!r} 里还有冒号 —— 那是 IPv6 字面量，不是域名。"
            "IP 字面量不是「谁拥有这个站点」的证据，不能当配对单位。"
        )

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"{host!r} 不能转成 punycode：{exc}") from exc
    # ``idna`` 只对非 ASCII 标签做 nameprep —— 纯 ASCII 标签原样返回，所以
    # 大小写必须在这里折叠，而不是指望编码器替我们做。
    labels = ascii_host.lower().split(".")

    # ``www.`` 只删最前面那一个，且删完必须仍是一个域名。
    if len(labels) > 2 and labels[0] == _ALIAS_LABEL:
        labels = labels[1:]

    if len(labels) < 2:
        raise ValueError(
            f"{host!r} 只有 {len(labels)} 个标签 —— 单标签主机不是公开域名，"
            "把它当配对单位会把所有单标签主机并成一个"
        )
    for label in labels:
        if not _LABEL_SHAPE.fullmatch(label) or label.startswith("-") or label.endswith("-"):
            raise ValueError(f"{host!r} 里的标签 {label!r} 不是合法的主机名标签")
    if labels[-1].isdigit():
        raise ValueError(f"{host!r} 的末标签是纯数字 —— 它是一个 IP 字面量，不是域名")

    canonical = ".".join(labels)
    if len(canonical) > PAIRING_KEY_MAX_LENGTH:
        raise ValueError(
            f"{canonical!r} 长 {len(canonical)}，超过了 "
            f"PAIRING_KEY_MAX_LENGTH={PAIRING_KEY_MAX_LENGTH}"
        )
    return canonical


def canonical_host(url: str) -> str:
    """把一个 URL 或裸主机名折成**配对键**（canonical host）。

    Args:
        url: 绝对 http/https URL，或者一个裸主机名。

    Returns:
        配对键。它满足 ``check_canonical_id(..., max_length=PAIRING_KEY_MAX_LENGTH)``，
        因此可以直接存进 ``AccountCandidate.pairing_key``。

    Raises:
        ValueError: 输入里没有一个可用的主机（含空串、非 URL、单标签主机、
            IP 字面量、非 http/https scheme、带 userinfo 的 URL 等）。
            规则逐条写在模块 docstring 里；**不返回空串**的理由也在那里 ——
            空键的含义是「没有域名」，不是「域名写坏了」。
    """
    if not url or url != url.strip() or any(ch.isspace() for ch in url):
        raise ValueError(f"{url!r} 是空的，或者含空白 —— 主机名里没有空白")
    return _normalize(_host_of(url))


def pairing_key_for(candidate: AccountCandidate) -> str:
    """**证据驱动**的产出者：这一行该配到哪个单位上（``""`` = 不配对）。

    域名这一栏自己不算数：还要有至少一条**指着它的证据**
    （``domain_evidence_ids``）。一条没有出处的域名，与随手写的域名，在报告里
    长得一样；配上对之后两支臂会在一个没人能证明的单位上对齐，而那个对齐
    会直接进 ``C - B``。

    两种来路都返回 ``""``（含义都是「不可配对 ⇒ 下游 hold」）：本来就没有
    官方域名，或者有域名但没有证据。**域名写坏了**是第三条路，它抛 ——
    见 :func:`canonical_host`。
    """
    if not candidate.official_domain or not candidate.domain_evidence_ids:
        return ""
    return canonical_host(candidate.official_domain)


def keys_with_multiple_organisations(
    candidates: Iterable[AccountCandidate],
) -> dict[str, tuple[str, ...]]:
    """哪些配对键底下**不止一个责任实体** —— 聚合域名那条缝的可见化。

    用行上**记着的** ``pairing_key``（报告就是按它配对的），不是现场重算的：
    重算会与报告分叉，于是「报告说配上了、检测说没有」而差别看不出来。

    这不是黑名单，也不阻止任何配对：判定只来自候选行自己（同一个键、
    不同的 ``organisation``），引擎里没有任何一个域名。返回的每个值按字典序
    排好，两次运行给出同一个结果。

    空键不参与聚类 —— 它不是「一个大家都属于的单位」，它是「不配对」。
    """
    organisations: dict[str, set[str]] = {}
    for candidate in candidates:
        if not candidate.pairing_key:
            continue
        organisations.setdefault(candidate.pairing_key, set()).add(candidate.organisation)
    return {
        key: tuple(sorted(names))
        for key, names in sorted(organisations.items())
        if len(names) > 1
    }


__all__ = [
    "canonical_host",
    "keys_with_multiple_organisations",
    "pairing_key_for",
]
