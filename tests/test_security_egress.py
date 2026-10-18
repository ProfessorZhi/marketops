"""收件人允许列表：§6 三道闸里的第三道（§6 / §7）。

## 这一条约束的是哪件事

§6 逐字要求测试模式「从网络出口、凭据、收件人允许列表**三个层面**防止真实外发」。
本文件测的是第三层，而它单独成立的理由必须写在明处：网络那一层管的是「这条路径
根本不与外面通话」，凭据那一层管的是「那个传输构造不出来」，而两样都齐了之后，
唯一还站在人和收件箱之间的是这一层。

所以本文件里那些判据全都是**纯函数**的判据，不需要 SMTP、不需要网络、不需要
一个真的邮箱：它回答的只是「这个字符串准不准收我们的信」。

## 一条判据要能失败，就必须有对照

「不在名单上被拒」这一条单独看是永真的（一个恒返回 False 的函数照样通过）。
所以下面每一条拒绝判据旁边都有一条**同一个输入、同一个函数**的放行对照：
能放过一个真在名单上的地址，才说明「拒绝」是名单说的，不是函数说的。
"""

from __future__ import annotations

import pytest

from marketops.domain.errors import ConfigError
from marketops.security.egress import (
    RecipientRefusal,
    check_recipient,
    parse_allowlist,
)

#: 一份最小的真实名单。域名条目与具体地址各一条 —— 两种形态都要有，
#: 否则「解析器两种都认」这件事在测试里只被覆盖到一半。
#:
#: 两条**不同**的域是刻意的：一条地址条目、一条域名条目若落在同一个域上，
#: 「域名条目按精确匹配而不是后缀匹配」那条判据就没有对照可言 —— 它测的
#: 地址会被另一条**地址**条目放行，于是无论域名那一条怎么实现都是绿的。
LIST = parse_allowlist("ops@corp.example, @acme.example")


# ----------------------------------------------------------------------
# 放行：判据的对照面
# ----------------------------------------------------------------------
def test_an_address_on_the_list_is_allowed() -> None:
    verdict = check_recipient("ops@corp.example", allowlist=LIST)

    assert verdict.allowed
    assert verdict.refusal is None
    assert verdict.detail == "", (
        "放行的判定不该带一句话 —— 「为什么允许」没有可读的答案，"
        "而一格可读的文本会让调用方以为它是判据的一部分。"
    )


def test_a_domain_entry_allows_every_local_part_under_it() -> None:
    """``@acme.example`` 允许这个名字空间下的**任何**本地部分。

    与「名单上写了谁就是谁」的取向不同，而差别是刻意的：一份真实名单常常是
    「那家客户的企业域」，把它压成一长串具体地址会让每加一个人都要改配置 ——
    而改配置的成本会落在最不该鼓励随便改的那份文件上。
    """
    for address in ("someone@acme.example", "Anyone.Else@acme.example"):
        assert check_recipient(address, allowlist=LIST).allowed, address


# ----------------------------------------------------------------------
# 拒绝：四种理由，四种处置
# ----------------------------------------------------------------------
def test_an_address_not_on_the_list_is_refused_without_blaming_the_address() -> None:
    """不在名单上 = 第三种处置：地址没写错、名单也没写坏。

    这一条与「配置坏了」的区别必须在**理由码**上，而不只在措辞上：前者要去找
    批准人，后者要去改配置。合成一个码会让两类事在报告上长得一样。
    """
    verdict = check_recipient("stranger@elsewhere.example", allowlist=LIST)

    assert not verdict.allowed
    assert verdict.refusal is RecipientRefusal.NOT_ALLOWED
    assert "谁批准" in verdict.detail


def test_an_empty_allowlist_allows_nobody() -> None:
    """空名单 ⇒ 谁都不许。**与上面那条成对**：同一个地址，换了名单就放行。

    这是本模块唯一一个非显然的取值，方向与 ``Principal.workspaces`` 的缺省
    同源：另一边的缺省（空 = 全部允许）会让一次漏配表现为「发得出去，看起来
    完全正常」。
    """
    refused = check_recipient("ops@corp.example", allowlist=frozenset())
    assert not refused.allowed
    assert refused.refusal is RecipientRefusal.NO_ALLOWLIST

    # 对照：同一个地址，只把名单换成非空的那一份。
    assert check_recipient("ops@corp.example", allowlist=LIST).allowed, (
        "空名单下的拒绝若不是名单说的，上一条判据就只是「这个函数会拒」"
    )


def test_an_empty_address_is_refused_as_its_own_reason() -> None:
    verdict = check_recipient("   ", allowlist=LIST)

    assert not verdict.allowed
    assert verdict.refusal is RecipientRefusal.EMPTY


def test_a_malformed_address_on_the_list_is_still_refused() -> None:
    """**在名单上**的坏地址照样被拒，理由仍然是形态。

    这一条判的是**顺序**，而且它刻意把坏地址放进名单里 —— 顺序只有在
    「查名单」与「判形态」都会开火时才是一个可观测的事实。若实现先查名单，
    这条地址会被**放行**：它在名单上。而它是一条谁也投递不了的地址。

    名单是手搓的（``parse_allowlist`` 在装载期就不让这种条目存在），因为这一条
    要证的是判定顺序，不是解析 —— 解析那一半由下面那组参数化用例守着。
    """
    broken = frozenset({"not-an-address"})
    verdict = check_recipient("not-an-address", allowlist=broken)

    assert not verdict.allowed, (
        "一条形状不成立的地址，因为名单里有同样的字符串就被放行了 —— "
        "收件人允许列表是「谁准收我们的信」这一格的最后一道，它不该被一条"
        "装载期就写坏了的条目接管"
    )
    assert verdict.refusal is RecipientRefusal.MALFORMED, (
        "先查名单的实现会在这里给出 NOT_ALLOWED —— 而那个理由指向一个不存在的人"
    )


def test_a_domain_entry_does_not_cover_a_lookalike_domain() -> None:
    """域名条目按**精确**匹配：``@acme.example`` 不覆盖 ``notacme.example``。

    这不是洁癖。域名条目若按字符串后缀比，``acme.example`` 会覆盖
    ``notacme.example`` 与 ``evil-acme.example`` —— 收件人允许
    列表是「谁准收我们的信」这一格的最后一道，而一条按后缀判的规则会让一串
    名字看着很像的域整个进来。

    子域同样不覆盖（``sub.acme.example``）：那条规则更宽，而这里选的是
    更窄的那一边 —— 要覆盖子域，就在名单上把那个域写出来。
    """
    for address in (
        "someone@notacme.example",
        "someone@sub.acme.example",
        "someone@acme.example.evil",
    ):
        verdict = check_recipient(address, allowlist=LIST)
        assert verdict.refusal is RecipientRefusal.NOT_ALLOWED, (
            f"{address} 被域名条目放行了 —— 那条规则比它该管的更宽"
        )


# ----------------------------------------------------------------------
# 解析：坏条目在装载期响，而不是在发信时
# ----------------------------------------------------------------------
def test_a_comma_separated_value_reads_back_as_a_set() -> None:
    assert parse_allowlist("a@example.com,b@example.com") == frozenset(
        {"a@example.com", "b@example.com"}
    )
    assert parse_allowlist("  a@example.com ,\n  b@example.com  ") == frozenset(
        {"a@example.com", "b@example.com"}
    )


def test_the_domain_part_is_normalised_and_the_local_part_is_not() -> None:
    """与 ``normalise_email`` 逐字同一条纪律：本地部分大小写敏感。

    随意 ``lower()`` 会让 ``A@x.com`` 与 ``a@x.com`` 合并成一个 —— 而它们
    在 RFC 5321 下是两个信箱（也是「false attribution」的一种来源）。
    """
    assert parse_allowlist("Operator@AcmeCorp.EXAMPLE") == frozenset(
        {"Operator@acme.example"}
    )


@pytest.mark.parametrize("value", ["*", "*@example.com", "someone@example.com*"])
def test_a_wildcard_entry_is_refused_at_load(value: str) -> None:
    """通配符不是「更多」的同义词，它是「这份名单不再是一道闸」。

    而一段能表达这个意思的配置，迟早会有人写上它 —— 所以它不是「读不出来」的
    那一类错，它被一条**点名的**规则拒绝，理由里说得清为什么不支持。
    """
    with pytest.raises(ConfigError, match="通配符"):
        parse_allowlist(value)


@pytest.mark.parametrize(
    "value",
    ["@", "@nodot", "@exa mple.com", "a@@b.com", "foo bar@x.com", "someone", "x@y", "a,b@"],
)
def test_a_junk_entry_is_refused_at_load(value: str) -> None:
    """写坏的条目**抛**，不跳过。

    跳过的后果取决于那个条目本来想表达什么，而解析器看不出意图：跳过一条
    「本来想允许」的条目意味着名单比写的人以为的窄（第一封信被拒时才发现），
    跳过一条笔误意味着这条配置从来没有生效过。两种都被装载期的一声响接住，
    而那比一封被拒的信早得多。

    参数表里刻意留着 ``a@@b.com`` 与 ``foo bar@x.com`` 这两条：它们**通过**
    ``is_plausible_email``（那一格是给抓下来的地址用的，故意宽松），而在这里
    仍然被拒。这两条是「这一格的形态判据比抓取那一格严」那句话的执行器 ——
    没有它们，那句话只是一段注释。
    """
    with pytest.raises(ConfigError):
        parse_allowlist(value)
