"""literal 抽取的测试。

重点在**不做什么**：不猜测、不补全、不解码。
这些「不做」和「做」一样需要被测试固定住。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain.extract import (
    block_ordinal,
    extract_emails,
    extract_labelled_names,
    extract_obfuscated_emails,
    extract_publication_times,
    extract_roles,
    in_the_same_entry,
    page_publication_time,
    parse_publication_time,
)


# --- 邮箱 ---------------------------------------------------------------------


def test_extracts_plain_emails() -> None:
    hits = extract_emails("Write to sales@example.co.uk or trade@example.co.uk.")
    assert [hit.value for hit in hits] == ["sales@example.co.uk", "trade@example.co.uk"]


def test_email_dedupe_is_case_insensitive() -> None:
    hits = extract_emails("Sales@example.co.uk and sales@example.co.uk")
    assert len(hits) == 1


def test_email_context_is_captured() -> None:
    hits = extract_emails("For trade enquiries please contact sales@example.co.uk directly.")
    assert "trade enquiries" in hits[0].context
    assert hits[0].kind == "email"


def test_trailing_punctuation_is_not_part_of_email() -> None:
    """句末标点不应被吃进地址里。"""
    hits = extract_emails("Contact sales@example.co.uk, thank you.")
    assert hits[0].value == "sales@example.co.uk"


def test_no_email_guess_is_generated() -> None:
    """只知道人名与域名时，不得拼出邮箱。"""
    assert extract_emails("Jane Doe works at example.co.uk") == []


# --- 混淆写法 -----------------------------------------------------------------


def test_obfuscated_email_is_detected_but_not_decoded() -> None:
    hits = extract_obfuscated_emails("Reach us at sales (at) example (dot) co.uk")
    assert len(hits) == 1
    assert hits[0].kind == "obfuscated_email"
    # 不还原：抽取值里保留原始写法
    assert "sales" in hits[0].value


def test_plain_email_is_not_reported_as_obfuscated() -> None:
    assert extract_obfuscated_emails("sales@example.co.uk") == []


# --- 姓名 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Contact: Jane Doe",
        "Sales Manager: Jane Doe",
        "Managing Director - Jane Doe",
        "Mr Jane Doe",
    ],
)
def test_labelled_names_are_extracted(text: str) -> None:
    hits = extract_labelled_names(text)
    assert [hit.value for hit in hits] == ["Jane Doe"]


def test_unlabelled_names_are_not_guessed() -> None:
    """无标签的姓名识别本阶段不做 —— 误报会直接污染 false-attribution 指标。"""
    assert extract_labelled_names("Jane Doe visited the trade counter.") == []


# --- 角色 ---------------------------------------------------------------------


def test_roles_match_configured_terms() -> None:
    hits = extract_roles("Our Purchasing Manager handles trade accounts.", ("Purchasing Manager",))
    assert [hit.value for hit in hits] == ["Purchasing Manager"]


def test_roles_match_case_insensitively() -> None:
    hits = extract_roles("contact the purchasing manager", ("Purchasing Manager",))
    assert len(hits) == 1


def test_empty_role_terms_yield_nothing() -> None:
    """profile 未提供词表（UNKNOWN）时返回空 —— 不内建通用词表。"""
    assert extract_roles("Our Purchasing Manager is here.", ()) == []


def test_roles_are_sorted_by_position() -> None:
    text = "Managing Director Jane Doe; Purchasing Manager John Smith"
    hits = extract_roles(text, ("Managing Director", "Purchasing Manager"))
    assert [hit.offset for hit in hits] == sorted(hit.offset for hit in hits)


# --- 发布时刻（§9）------------------------------------------------------------
#
# 这一节守的是一句话：**一个日期要挨着它自己的标签**。它比本文件其他几节都更容易
# 回退，因为「找到发布词、再找后面的日期」这个写法看起来完全正常，而且在多数页面
# 上给出正确结果 —— 它只在一种页面上错，而那种页面正是最该读对的一页。


def test_a_published_label_with_a_date_is_extracted() -> None:
    hits = extract_publication_times("<p>Published: 1 March 2026</p>")
    assert [hit.value for hit in hits] == ["1 March 2026"]
    assert hits[0].kind == "publication_time"


def test_a_date_without_a_label_is_not_a_publication_time() -> None:
    """页面上到处是日期，而一个没说自己是**什么**的日期什么都不证明。"""
    text = "<p>The committee meets on 1 March 2026 in the village hall.</p>"
    assert extract_publication_times(text) == []
    assert page_publication_time(text) is None


def test_the_nearest_label_wins_and_updated_is_not_published() -> None:
    """**这一节最重要的一条。** 日期属于它前面最近的那个标签。

    ``Published. Last updated 3 April 2026``：只找发布词的写法会在这里读到
    3 April，而那一天的日期属于 ``Last updated``。两者相差七年，方向也不一样 ——
    「发布词之后最近的日期」让一个 2019 年的说法看起来是 2026 年的，于是它不会
    被重验。§9 禁止的缝法是「A 页的姓名 + B 页的邮箱」，同一条纪律在这里的样子是
    「A 标签的日期配在 B 标签上」。
    """
    assert extract_publication_times("<p>Published. Last updated 3 April 2026</p>") == []
    assert page_publication_time("<p>Published. Last updated 3 April 2026</p>") is None


def test_a_published_date_beside_an_updated_date_keeps_only_the_published_one() -> None:
    """两个标签各带一个日期时**各归各的**：留下的是发布那一个，不是更新的那个。

    两页都在这里，而它们挡的不是同一件事 —— 这一点是量出来的，不是推的：
    分成两段的写法里，第二个日期距发布词 33 个字符，``MAX_LABEL_GAP``（32）
    **自己**就挡住了那个错误，于是「最近的那个标签说了算」这一层有没有，那一页
    都读得对。压进一行的 byline 只隔 22 个字符，两层里只剩最近标签这一层 ——
    所以**钉住这条规则的是第二页**，第一页钉的是「两段排版下也一样」。
    """
    two_paragraphs = "<p>Published: 2019-05-01</p><p>Last updated: 2026-03-01</p>"
    one_line = "Published 2019-05-01 · Updated 2026-03-01"
    for text in (two_paragraphs, one_line):
        assert [hit.value for hit in extract_publication_times(text)] == ["2019-05-01"], text
        assert page_publication_time(text) == datetime(2019, 5, 1, tzinfo=timezone.utc), text


def test_a_machine_readable_publication_time_is_a_publication_time() -> None:
    """``<meta>`` 里那种写法是同一件事的另一种表述，而它更可靠（不是人写的句子）。"""
    text = '<meta property="article:published_time" content="2019-05-01T09:00:00+01:00">'
    assert page_publication_time(text) == datetime(
        2019, 5, 1, 9, 0, tzinfo=timezone(timedelta(hours=1))
    )


def test_a_label_too_far_from_a_date_is_not_a_match() -> None:
    """标签与日期之间隔着的话越多，那个日期越可能不是它的。"""
    text = "<p>Published</p>" + "<p>x</p>" * 12 + "<p>1 March 2026</p>"
    assert extract_publication_times(text) == []


def test_a_numeric_day_month_is_not_an_anchor_even_with_a_label() -> None:
    """``03/04/2026`` 是三月四日还是四月三日，取决于写的人。

    它连命中都不该有 —— 一条「有标签、日期也在旁边、只是没人知道它说哪一天」
    的记录比没有记录更糟：它看起来有一个锚点。所以这一条的判据落在
    ``extract_publication_times``（0 处），而不是只落在解析器上。
    """
    text = "<p>Published: 03/04/2026</p>"
    assert extract_publication_times(text) == []
    assert page_publication_time(text) is None


def test_two_different_publication_dates_on_one_page_leave_no_anchor() -> None:
    """一页两个互不相同的发布日 → **不挑**。

    挑更近的那一个看起来更保守，实际是在替这份材料认领「哪一天管这一处内容」，
    而依据不在页面上。修法的方向不是选得更聪明，是不选。
    """
    text = "<p>Published 1 March 2026</p><p>Published 4 April 2026</p>"
    assert len(extract_publication_times(text)) == 2
    assert page_publication_time(text) is None


def test_the_same_day_written_twice_is_one_statement() -> None:
    """页头页脚各写一遍同一句话不是两个说法 —— 按**瞬间**去重，不按字面去重。"""
    same_words = "<p>Published 1 March 2026</p><footer>Published 1 March 2026</footer>"
    two_forms = "<p>Published 1 March 2026</p><p>Published 2026-03-01</p>"
    expected = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert page_publication_time(same_words) == expected
    assert page_publication_time(two_forms) == expected


@pytest.mark.parametrize(
    "literal",
    [
        "2026-03-01",
        "2026-03-01T09:00:00Z",
        "2026-03-01 09:00",
        "1 March 2026",
        "1st March 2026",
        "1st of March 2026",
        "March 1, 2026",
        "March 1st, 2026",
        "1 Mar 2026",
    ],
)
def test_accepted_date_shapes(literal: str) -> None:
    parsed = parse_publication_time(literal)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 3, 1)
    assert parsed.tzinfo is not None


@pytest.mark.parametrize(
    "literal",
    [
        # 日月谁在前取决于写的人，读的人分不出。两者相差不到一个月，可它要回答的
        # 问题是「这个说法是什么时候的」—— 一个不知道自己说哪一天的锚点不叫精度低。
        "03/04/2026",
        "3.4.2026",
        "03-04-2026",
        # 只有月份：日期是这一格里唯一可用的精度，缺了它就只能自己挑一天。
        "March 2026",
        "2026-03",
        # 这一天不存在。
        "30 February 2026",
        # ``fromisoformat`` 收而本仓不收的写法：多认一种就多一个数字序列能被读成一天。
        "20260301",
        "2026-W09-1",
        # 不是日期。
        "",
        "   ",
        "yesterday",
    ],
)
def test_refused_date_shapes(literal: str) -> None:
    assert parse_publication_time(literal) is None


def test_a_date_only_literal_lands_at_the_start_of_that_day() -> None:
    """只有日期时取这一天的**最早**时刻，而方向是刻意的。

    页面说的是哪一天，而那一天里最早的瞬间是唯一一个所有读法都同意的时刻。往早
    里取，误差只会让这条事实显得更旧：另一个方向才是付代价的那个（一个还成立的
    说法被算成当前的，于是没人再去核）。上界一天，而策略窗口是九十天与一百八十天。
    """
    parsed = parse_publication_time("1 March 2026")
    assert parsed == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert parsed.hour == parsed.minute == parsed.second == 0


def test_an_offset_in_the_literal_is_kept() -> None:
    """来源写的是 ``+01:00``，读出来就还是 ``+01:00`` —— 同一个瞬间，同一种写法。"""
    parsed = parse_publication_time("2019-05-01T09:00:00+01:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=1)


# --- 条目窗口：两处命中挨不挨着（§9 的「不得把 A 处的姓名配在 B 处的邮箱上」）-------


def test_two_hits_on_one_line_are_in_the_same_entry() -> None:
    text = "Grounds Manager: Jane Doe — jane.doe@example.co.uk"
    assert in_the_same_entry(text, text.index("Grounds"), text.index("jane.doe@"))


def test_a_hit_on_the_neighbouring_line_is_still_the_same_entry() -> None:
    """一张卡片排成两行是最常见的写法之一，而它必须算挨着。

    取 0（要求同一行）会让这类页面一条路线都产不出来 —— 而那正是 §9·4 反对的
    那个方向：把「我们没能把两处印在一起的证据读出来」写成「这里没有联系人」。
    """
    text = "Contact: Jane Doe\nEmail: jane.doe@example.co.uk"
    assert in_the_same_entry(text, text.index("Contact"), text.index("jane.doe@"))


def test_blank_lines_do_not_count_towards_the_distance() -> None:
    """同一条内容，中间多空几行，结论必须一样。

    空的多少是**排版**：同一段 HTML 压缩前后空行数不同，而两份是同一个页面。
    让空行参与比较，「隔了几条」就会随缩进变（压缩过的算挨着、缩进过的算隔开）。
    """
    tight = "Contact: Jane Doe\nEmail: jane.doe@example.co.uk"
    airy = "Contact: Jane Doe\n\n\n\nEmail: jane.doe@example.co.uk"
    assert in_the_same_entry(tight, tight.index("Contact"), tight.index("jane.doe@"))
    assert in_the_same_entry(airy, airy.index("Contact"), airy.index("jane.doe@"))


def test_two_entries_away_is_not_the_same_entry() -> None:
    """从另一边把边界钉住：隔一条就已经不是同一张卡片了。

    没有这一条，``ENTRY_WINDOW_BLOCKS`` 取多大都测不出来 —— 上面那几条只证明
    「挨着算挨着」。
    """
    text = (
        "Grounds Manager: Jane Doe\n"
        "The site is open to members all year round.\n"
        "Email: jane.doe@example.co.uk"
    )
    assert not in_the_same_entry(text, text.index("Grounds"), text.index("jane.doe@"))
    # 而正文那一行与两处都在一条之内 —— 这条判据量的是**距离**，不是内容。
    assert in_the_same_entry(text, text.index("The site"), text.index("Grounds"))


def test_an_offset_inside_a_blank_line_claims_nothing() -> None:
    """落在一个空行上的位置不替它挑邻居：窗口是一个**结论**，不能建立在猜测上。

    真的命中不会落在空行上（命中是正则匹配出来的，至少有一个非空白字符）。
    这一条守的是这个函数作为公开判据的边界 —— 它被人独立调用时也得说真话。
    """
    text = "Contact: Jane Doe\n\nEmail: jane.doe@example.co.uk"
    blank = text.index("\n\n") + 1
    assert block_ordinal(text, blank) is None
    assert not in_the_same_entry(text, blank, text.index("jane.doe@"))


def test_a_name_body_never_crosses_a_line() -> None:
    """姓名的三个词必须印在**同一行**上。

    三个词之间的空白此前用的是「任意空白」那一档，而它包含换行 —— 于是
    ``Contact: Jane Doe`` 的下一行以大写字母开头时，那个词被吃进了姓名里（这一段
    抽出来的是 ``Jane Doe`` 连上下一行的 ``Grounds``）：一个不存在的人，而
    ``Person`` 的 id 由这个名字派生，所以它同时是一个不存在的身份。它当时
    没被发现，是因为 ``context`` 会把换行折成空格，那一段读起来完全正常。
    """
    hits = extract_labelled_names(
        "Contact: Jane Doe\nGrounds Manager — jane.doe@example.co.uk"
    )
    assert [hit.value for hit in hits] == ["Jane Doe"]


def test_a_label_may_sit_on_the_line_above_the_name() -> None:
    """标签与姓名之间的换行是**两处**之间的间隔，仍然算命中。

    这一侧与上一条是同一件事的两半：不许跨的是姓名**内部**，不是标签与它之间。
    """
    hits = extract_labelled_names("Contact:\nJane Doe")
    assert [hit.value for hit in hits] == ["Jane Doe"]
