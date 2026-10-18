"""一页材料 → 资产事实。**确定性**匹配，不是模型，也不认识任何一个取值。

这一层是 §2 那句话的落点：「candidate URL → 抓取 → 抽取资产事实 → 场地资产」。
在此之前那四格（``organisation_kind`` / ``venue_environment`` / ``surface_class`` /
``country``）在候选行上恒为 ``""`` —— 而 ``""`` 在规则方言里读作**已确认的空**
（「记了，是空」），于是点它们的规则会**确定地**不命中、并且**不进** ``unchecked``。
报告上那看起来像一条结论，其实是没有人查过。

# 三条判定规则

1. 一条轴上**没有任何取值命中** ⇒ ``unknown``（「这一页没说」）。
2. 一条轴上**恰好一个取值命中** ⇒ 就是它。
3. 一条轴上**两个以上不同取值命中** ⇒ 仍是 ``unknown``，理由里写出是哪几个。

第 3 条是保守的方向：``unknown`` 不决定任何子句，账号停在 ``discovered``、
判词 ``UNRESOLVED_FACTS`` —— 也就是**留给人看**，不是丢掉。本仓的纪律是
「没有证据就丢弃账号，比留着它危险得多」，而一条同时写着「3G 场地」和「草地场地」
的页面，恰恰是判不出来就该说判不出来的那一类。

**``excludes`` 只在第 2 条成立时生效**：歧义不排除任何人。

# 匹配是字面的，而且只做一件事

归一化 = 小写 + 非字母数字折成单空格 + 折叠空白，然后按**词边界**比对
（在归一化后的串两侧各补一个空格做子串查找，这就是词边界：字母与数字都算词字符）。
于是 ``5GHz``（归一化后 ``5ghz``）不会命中 ``5g``，而 ``students' union``
与 ``students union`` 是同一件事 —— 判据照人读的方式写。

**不做词干化、不做同义词、不做模糊匹配。** 三种都会让「为什么这一页被判成室内」
变成一句说不清的话，而这一层的每一条读数都要能回到一个字符串上。

# 它不产出 ``surface_evidence_confirmed``

那一格问的是「**这个组织没有独立真草证据**吗」—— 一页写着「3G 场地」说的是
「这块场地是人造表面」，推不出那个否定（同一个组织可以另有草地场地）。
发现期没有任何东西能建立那句否定，所以本模块**不碰**那一格：它留在 ``None``，
``artificial_surface_only`` 因此由第一个子句决定（判成人造表面时走
``exclusions_hit`` → ``excluded_segment``）。

# 词表从哪里来

``ProfileBundle.profile.classification``。核心代码不认识任何一个取值 ——
那四条轴**的名字**是例外，因为它们同时是事实名（``domain/facts.py``），
而事实名 → 引擎的路径上没有映射表（一张映射表漂移的那一次不会有任何症状，
规则只是永不命中）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.profile import (
    CLASSIFICATION_AXES,
    CLASSIFICATION_UNKNOWN,
    ClassificationAxis,
    ClassificationVocabulary,
)

#: 说明某一条轴为什么是这个读数。与读数分开存：读数进事实投影，理由进报告。
_NO_VOCABULARY = "profile 没有为这条轴声明词表 —— 没有任何东西查过它"
_NO_PHRASE = "这一页没有任何一个取值命中"
_NO_HOST = "主机名不落在任何一个已声明的后缀里"


def _normalize(text: str) -> str:
    """小写，非字母数字折成单空格，折叠空白。

    ``str.isalnum()`` 对非 ASCII 字母也为真（``é`` 是），所以带重音的写法**保留**
    成一个词 —— 它不会与它的 ASCII 写法互相命中，这是刻意的：本层不做音译，
    而一次「看起来命中了」的音译比一次没命中更难查。
    """
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.lower()).split())


def _mentions(haystack: str, phrase: str) -> bool:
    """``phrase`` 是否在 ``haystack`` 里作为一个**词序列**出现。

    ``haystack`` 必须是归一化过的（见 :func:`_normalize`）。两侧补空格是为了让
    首尾的词也能命中 —— 子串查找在边界上的正确性完全依赖这两个空格。
    """
    needle = _normalize(phrase)
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def _host_matches(host: str, suffix: str) -> bool:
    """主机名是否落在这个后缀下。``example.co.uk`` 落在 ``uk`` 下，``notuk`` 不落。

    比的是**标签边界**而不是字符串结尾：``notuk`` 以 ``uk`` 结尾，但它不是
    英国域名。少这一条判断，「uk 后缀」这条判据会开始命中外面的东西 ——
    而命中的方向恰好是**放宽**区域限制。
    """
    clean = suffix.strip().lower().lstrip(".")
    if not clean or not host:
        return False
    lowered = host.strip().lower()
    return lowered == clean or lowered.endswith("." + clean)


@dataclass(frozen=True)
class AxisReading:
    """一条轴的读数、它命中的判据、以及为什么。

    ``value`` 是 :data:`CLASSIFICATION_UNKNOWN` 时 ``matched`` 为空 ——
    「没有结论」与「有结论」在这一层是**互斥**的两种形态，不允许出现
    「值是 unknown 但命中了一堆判据」的行。
    """

    axis: str
    value: str
    matched: tuple[str, ...] = ()
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.value != CLASSIFICATION_UNKNOWN

    def as_dict(self) -> dict[str, object]:
        return {
            "axis": self.axis,
            "value": self.value,
            "matched": list(self.matched),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AssetClassification:
    """一次分类的全部读数。**每一条轴都有一格**，包括判不出来的那些。

    「一条轴不在读数里」与「这条轴判成 unknown」被刻意排除掉一种写法：
    只放判出来的那些，调用方就得用 ``.get(axis) or unknown`` 去补 ——
    而那一次补的时候，「这一轴查了没命中」与「这一轴压根没进读数」
    就再也分不开了。四条轴的名字是闭集（:data:`CLASSIFICATION_AXES`），
    所以恒有四格并不贵。
    """

    host: str
    readings: dict[str, AxisReading] = field(default_factory=dict)
    exclusions_hit: tuple[str, ...] = ()

    def value(self, axis: str) -> str:
        """这条轴的读数；没有这一条轴时是 ``unknown``（不是 ``KeyError``）。"""
        reading = self.readings.get(axis)
        return reading.value if reading is not None else CLASSIFICATION_UNKNOWN

    def groups(self) -> dict[str, str]:
        """``{事实名: 取值}`` —— 直接进候选行的每一格。"""
        return {axis: self.value(axis) for axis in CLASSIFICATION_AXES}

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "readings": [self.readings[axis].as_dict() for axis in CLASSIFICATION_AXES if axis in self.readings],
            "exclusions_hit": list(self.exclusions_hit),
        }


def _read_axis(
    axis: ClassificationAxis, *, haystack: str, host: str
) -> tuple[AxisReading, tuple[str, ...]]:
    """一条轴的读数，以及它**确认为该取值**时要记的排除项。

    返回的第二个值是空元组，除非第 2 条规则成立（恰好一个取值命中）——
    歧义不排除任何人，见模块 docstring。
    """
    hits: list[tuple[str, list[str]]] = []
    for value in axis.values:
        matched = [phrase for phrase in value.phrases if _mentions(haystack, phrase)]
        matched += [
            f"host:{suffix}"
            for suffix in value.host_suffixes
            if _host_matches(host, suffix)
        ]
        if matched:
            hits.append((value.value, matched))

    if not hits:
        return AxisReading(axis=axis.axis, value=CLASSIFICATION_UNKNOWN, reason=_NO_PHRASE), ()
    if len(hits) > 1:
        return (
            AxisReading(
                axis=axis.axis,
                value=CLASSIFICATION_UNKNOWN,
                reason=(
                    "这一页同时命中了 "
                    + "、".join(f"{value}（{'/'.join(matched)}）" for value, matched in hits)
                    + " —— 两个以上不同取值相撞时本层不取舍：判成任何一个都是一次"
                    "凭空的结论，而这条轴的读数是 hold，不是排除。"
                ),
            ),
            (),
        )

    value, matched = hits[0]
    return (
        AxisReading(
            axis=axis.axis, value=value, matched=tuple(matched), reason=f"命中 {'、'.join(matched)}"
        ),
        _exclusions_of(axis, value),
    )


def _exclusions_of(axis: ClassificationAxis, value: str) -> tuple[str, ...]:
    for candidate in axis.values:
        if candidate.value == value:
            return candidate.excludes
    return ()


def classify(
    text: str, *, host: str = "", vocabulary: ClassificationVocabulary | None
) -> AssetClassification:
    """一页材料（正文，通常还接上标题）加上它的主机名 → 资产事实读数。

    Args:
        text: 页面原文。调用方给的是 ``标题 + 正文``：标题是这一页给自己写的名字，
            而名字几乎总是这一页最直白的一句自我介绍 —— 只读正文会让
            「Example Indoor Cricket Centre」这种把关键信息全放在标题上的页面判不出来。
        host: 取回这一页的主机名（canonical host）。**不是 URL**：
            判据比的是标签后缀，带 scheme 或路径的串在这里只会恒不命中。
        vocabulary: ``ProfileBundle.profile.classification``。``None`` 合法 ——
            一份 profile 可以先只有规则、后有分类，那时四条轴全部 ``unknown``，
            而理由说的是「profile 没有声明词表」，不是「这一页没命中」。

    Returns:
        四条轴的读数（判不出来的也是 ``unknown``，不是缺席）与命中的排除项。
    """
    haystack = _normalize(text)
    readings: dict[str, AxisReading] = {}
    exclusions: list[str] = []

    for axis_name in CLASSIFICATION_AXES:
        axis = vocabulary.axis(axis_name) if vocabulary is not None else None
        if axis is None:
            readings[axis_name] = AxisReading(
                axis=axis_name, value=CLASSIFICATION_UNKNOWN, reason=_NO_VOCABULARY
            )
            continue
        reading, hit = _read_axis(axis, haystack=haystack, host=host)
        readings[axis_name] = reading
        exclusions.extend(hit)

    return AssetClassification(
        host=host,
        readings=readings,
        exclusions_hit=tuple(dict.fromkeys(exclusions)),
    )


__all__ = [
    "AssetClassification",
    "AxisReading",
    "classify",
]
