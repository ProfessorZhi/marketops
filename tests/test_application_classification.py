"""``application/classification.py``：一页材料 → 四条轴上的资产事实。

## 这里钉的是三条判定规则本身

它们是 §2 那条链上最先出结论的一步，而三条规则的失败**都不会让输出变难看**：

* 读不出来写成 ``""`` 而不是 ``unknown``。``""`` 在规则方言里是**已确认的空**
  （「记了，是空」），于是点它的规则会确定地不命中、而且**不进** ``unchecked`` ——
  报告上那看起来像一条结论，其实是没有人查过。
* 两值相撞时**取一个**而不是判 ``unknown``。一次凭空的取舍会让「这一页没说」与
  「这一页说了两件矛盾的话」在判词上同形，而后者是要人去看的那一类。
* ``excludes`` 在歧义下也生效。这一条最贵：一次不该发生的排除会把一个候选
  **确定地**挡在池子外面，而池子里少了一行是没人会去数的一件事。

## 词表是**造的**

判据住 profile（``tests/test_no_hardcoded_market_knowledge.py`` 对 ``marketops/``
做 AST 扫描）。把市场词抄进这里，会让「词表变了」与「分类器坏了」长得一模一样，
而这两件事要的处置完全不同。造出来的取值是没有意义的词（``alpha`` / ``beta``）：
它们在这里的作用只是「一个取值」，不是任何一句市场事实。

真实词表那一侧由 ``tests/test_profiles.py`` 的装载判据与
``tests/test_discovery_run.py`` 的整条链覆盖。
"""

from __future__ import annotations

from marketops.application.classification import classify
from marketops.domain.profile import (
    CLASSIFICATION_AXES,
    CLASSIFICATION_UNKNOWN,
    ClassificationAxis,
    ClassificationValue,
    ClassificationVocabulary,
)

SURFACE = "surface_class"


def _value(
    name: str,
    *,
    phrases: tuple[str, ...] = (),
    host_suffixes: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
) -> ClassificationValue:
    return ClassificationValue(
        value=name, phrases=phrases, host_suffixes=host_suffixes, excludes=excludes
    )


def _axis(name: str, *values: ClassificationValue) -> ClassificationAxis:
    return ClassificationAxis(axis=name, values=values)


def _vocabulary(*axes: ClassificationAxis) -> ClassificationVocabulary:
    return ClassificationVocabulary(axes=axes)


# --- 三条规则 ---------------------------------------------------------------


def test_a_page_that_matches_no_criterion_reads_as_unknown() -> None:
    """一条命中都没有 ⇒ ``unknown``，而理由说的是「这一页没有命中」。

    这是第 1 条规则。它必须是 ``unknown`` 而**不是**空串：空串是「已确认的空」。
    """
    vocabulary = _vocabulary(_axis(SURFACE, _value("alpha", phrases=("alpha",))))
    result = classify("这一页什么也没说", host="example.org", vocabulary=vocabulary)

    reading = result.readings[SURFACE]
    assert reading.value == CLASSIFICATION_UNKNOWN
    assert reading.known is False
    assert "没有任何一个取值命中" in reading.reason
    assert reading.matched == (), "没有结论的读数不许带着一堆命中判据"


def test_exactly_one_matching_value_reads_as_that_value() -> None:
    """恰好一个取值命中 ⇒ 就是它，而 ``matched`` 说得出来是哪几条判据。"""
    vocabulary = _vocabulary(
        _axis(SURFACE, _value("alpha", phrases=("alpha",)), _value("beta", phrases=("beta",)))
    )
    result = classify("A page about beta surfaces", host="example.org", vocabulary=vocabulary)

    reading = result.readings[SURFACE]
    assert reading.value == "beta"
    assert reading.matched == ("beta",)
    assert result.value(SURFACE) == "beta"


def test_two_matching_values_read_as_unknown_and_the_reason_names_both() -> None:
    """两值相撞 ⇒ 仍是 ``unknown``，而理由里**写出来是哪两个**。

    第 3 条规则。它保守的方向是有意的：判成任何一个都是一次凭空的取舍，而这一层
    存在的理由就是不让「取舍过了」与「没查出来」在数据上合流。理由必须点名 ——
    否则复核的人只能看到「这一页没说」，而事实是这一页说了两件矛盾的话。
    """
    vocabulary = _vocabulary(
        _axis(SURFACE, _value("alpha", phrases=("alpha",)), _value("beta", phrases=("beta",)))
    )
    result = classify("A page about alpha and beta", host="example.org", vocabulary=vocabulary)

    reading = result.readings[SURFACE]
    assert reading.value == CLASSIFICATION_UNKNOWN
    assert "alpha" in reading.reason and "beta" in reading.reason, reading.reason
    assert reading.matched == ()


def test_the_same_value_hit_twice_is_still_one_value() -> None:
    """同一个取值命中两条判据 ⇒ 仍是那一个取值，不是「相撞」。

    判据是 ``len(hits) > 1``，而 ``hits`` 按**取值**收集。一个把命中数当取值数的
    实现会让「这一页两处写着 alpha」判成歧义 —— 而那是这一批里最常见的一页。
    """
    vocabulary = _vocabulary(
        _axis(
            SURFACE,
            _value("alpha", phrases=("alpha", "first surface")),
            _value("beta", phrases=("beta",)),
        )
    )
    result = classify("alpha and first surface", host="example.org", vocabulary=vocabulary)

    assert result.readings[SURFACE].value == "alpha"
    assert result.readings[SURFACE].matched == ("alpha", "first surface")


# --- 排除项：只在有结论时才生效 ----------------------------------------------


def test_an_exclusion_fires_only_with_the_value_that_declares_it() -> None:
    vocabulary = _vocabulary(
        _axis(
            SURFACE,
            _value("alpha", phrases=("alpha",), excludes=("not_alpha",)),
            _value("beta", phrases=("beta",)),
        )
    )

    hit = classify("A page about alpha", host="example.org", vocabulary=vocabulary)
    assert hit.value(SURFACE) == "alpha"
    assert hit.exclusions_hit == ("not_alpha",)

    quiet = classify("A page about nothing", host="example.org", vocabulary=vocabulary)
    assert quiet.exclusions_hit == (), "没有结论就没有排除项"


def test_an_exclusion_does_not_fire_when_two_values_collide() -> None:
    """**歧义不排除任何人。** 这一条是这三条规则里最贵的一条。

    一次两值相撞的判定里，两个取值各自带着自己的排除项；照单全收会把候选
    **确定地**挡在池子外面，而池子里少了一行是没人会去数的一件事。判成
    ``unknown`` 的后果只是 hold —— 有人会看到它。
    """
    vocabulary = _vocabulary(
        _axis(
            SURFACE,
            _value("alpha", phrases=("alpha",), excludes=("not_alpha",)),
            _value("beta", phrases=("beta",), excludes=("not_beta",)),
        )
    )
    result = classify("A page about alpha and beta", host="example.org", vocabulary=vocabulary)

    assert result.readings[SURFACE].value == CLASSIFICATION_UNKNOWN
    assert result.exclusions_hit == ()


def test_two_values_declaring_the_same_exclusion_report_it_once() -> None:
    """排除项去重且保序 —— 「命中了几条排除」不是这一层在数的东西。"""
    vocabulary = _vocabulary(
        _axis(
            "organisation_kind",
            _value("alpha", phrases=("alpha",), excludes=("shared",)),
            _value("beta", phrases=("beta",), excludes=("shared",)),
        )
    )
    result = classify("alpha", host="example.org", vocabulary=vocabulary)

    assert result.readings["organisation_kind"].value == "alpha"
    assert result.exclusions_hit == ("shared",)


# --- 判据的两种来源：页面文字与主机名 -----------------------------------------


def test_matching_is_on_word_boundaries_not_substrings() -> None:
    """``5GHz`` 不命中 ``5g``；而 ``students' union`` 与 ``students union`` 是同一件事。

    归一化只做三件事：小写、非字母数字折成单空格、折叠空白。不做词干化、不做同义词、
    不做模糊匹配 —— 三种都会让「为什么这一页被判成这个取值」变成一句说不清的话，
    而这一层的每一条读数都要能回到一个字符串上。
    """
    vocabulary = _vocabulary(
        _axis(
            SURFACE,
            _value("alpha", phrases=("5g",)),
            _value("beta", phrases=("students union",)),
        )
    )

    assert classify("a 5GHz link", host="x", vocabulary=vocabulary).value(SURFACE) == (
        CLASSIFICATION_UNKNOWN
    ), "5ghz 里的 5g 不是一个词"
    assert classify("the students' union here", host="x", vocabulary=vocabulary).value(
        SURFACE
    ) == "beta"


def test_host_suffixes_match_on_a_label_boundary() -> None:
    """``example.co.uk`` 落在 ``uk`` 下，``notuk`` **不**落。

    少这一条判断，「uk 后缀」这条判据会开始命中外面的东西 —— 而命中的方向恰好是
    **放宽**区域限制，那是最不该由一次字符串匹配失误决定的一格。
    """
    vocabulary = _vocabulary(
        _axis("country", _value("somewhere", host_suffixes=("uk",)))
    )

    assert classify("", host="example.co.uk", vocabulary=vocabulary).value("country") == (
        "somewhere"
    )
    assert classify("", host="uk", vocabulary=vocabulary).value("country") == "somewhere"
    assert classify("", host="notuk", vocabulary=vocabulary).value("country") == (
        CLASSIFICATION_UNKNOWN
    )
    assert classify("", host="example.notuk", vocabulary=vocabulary).value("country") == (
        CLASSIFICATION_UNKNOWN
    )


def test_the_host_is_not_part_of_the_page_and_the_page_is_not_the_host() -> None:
    """两种判据**各看各的**：主机后缀不进正文匹配，正文判据不拿主机名去比。

    合成一张表的后果是「这条证据来自哪里」在审计上再也说不清 —— 而这一层唯一
    让人信得过的地方，就是每一条读数都能指回一个字符串（而且说得出那个字符串是
    页面上的还是主机名里的）。``matched`` 用 ``host:`` 前缀区分两者。
    """
    vocabulary = _vocabulary(
        _axis("country", _value("byhost", host_suffixes=("zz",))),
        _axis(SURFACE, _value("bypage", phrases=("bypage",))),
    )

    # 主机名里有 `bypage` 这个词干，但它不是页面内容 —— 页面判据不认主机名。
    assert classify("nothing here", host="bypage.zz", vocabulary=vocabulary).value(
        SURFACE
    ) == CLASSIFICATION_UNKNOWN
    # 反过来：正文里写着 `zz`，但主机名不在那个后缀下 —— 主机判据不认正文。
    assert classify("we are zz", host="example.org", vocabulary=vocabulary).value(
        "country"
    ) == CLASSIFICATION_UNKNOWN

    assert classify("", host="a.zz", vocabulary=vocabulary).readings["country"].matched == (
        "host:zz",
    )


# --- 读数永远有四格，而且「没有词表」与「没有命中」分得开 -----------------------


def test_every_axis_has_a_cell_even_when_nothing_was_read() -> None:
    """四条轴恒有四格 —— 「这一轴查了没命中」与「这一轴压根没进读数」必须分得开。

    只放判出来的那几条，调用方就得用 ``.get(axis) or unknown`` 去补，而那一次补
    的时候这两种情况就再也分不开了。
    """
    vocabulary = _vocabulary(_axis(SURFACE, _value("alpha", phrases=("alpha",))))
    result = classify("nothing", host="example.org", vocabulary=vocabulary)

    assert set(result.groups()) == set(CLASSIFICATION_AXES)
    assert all(value == CLASSIFICATION_UNKNOWN for value in result.groups().values())
    assert result.value("an_axis_that_is_not_a_fact") == CLASSIFICATION_UNKNOWN, (
        "不在读数里的轴名不是 KeyError —— 那一格的取值就是「没有结论」"
    )


def test_an_axis_the_profile_does_not_give_a_vocabulary_reads_as_unknown_and_says_so() -> None:
    """``profile`` 没声明词表 ⇒ 四条轴全 ``unknown``，而理由说的是**没声明**。

    「没给词表」与「给了词表但这一页没命中」在判词上同形（都是 hold），但在
    **为什么**上必须分得开 —— 前者要人去补 profile，后者要人去看页面。
    """
    absent = classify("anything", host="example.org", vocabulary=None)
    assert all(value == CLASSIFICATION_UNKNOWN for value in absent.groups().values())
    assert all(
        "没有为这条轴声明词表" in absent.readings[axis].reason for axis in CLASSIFICATION_AXES
    )

    partial = classify(
        "anything",
        host="example.org",
        vocabulary=_vocabulary(_axis(SURFACE, _value("alpha", phrases=("alpha",)))),
    )
    assert "没有为这条轴声明词表" in partial.readings["country"].reason
    assert "没有任何一个取值命中" in partial.readings[SURFACE].reason, (
        "这条轴**有**词表，只是这一页没命中 —— 两条理由不能混成一条"
    )
