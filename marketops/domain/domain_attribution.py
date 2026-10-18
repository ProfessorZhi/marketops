"""一块场地/账号的**官网是哪一个** —— 一次有证据的判定，而不是「我们是从哪台主机上读到它的」。

## 这一层存在的理由

在它之前，全链路上「这个组织的官网」这句话只有一个来源：**取回那一页的那台主机**
（``DiscoverySite.site`` → 工件行的 ``official_domain`` → ``RowDisposition`` → 档案的
``domain``）。那台主机回答的是「我们在哪儿读到了这一段字」，而它被当成
「这个组织拥有哪个域名」用了 —— 于是：

* 一份 facebook 页面上的邮箱，因为「域与账号域不符」被拒（其实它可能正是那家组织
  自己的域名）；
* 一份目录页上的第三方邮箱，因为「域与账号域相符」被收下（其实那台主机不属于
  这个账号）。

两个方向的错都来自同一个动作：**把一次观察当成一次判定**。

所以这一步把「官网」拆成四个格子（:class:`~marketops.domain.entities.VenueAsset` 上
住着它们），而本模块只生产其中**有证据的那一格**：``verified_official_domain``。

## 判据：域名必须能由责任实体的名字解释

SOP v1 L103 逐字：**「确认官方入口公开可访问，最终域名与责任实体关系可解释；
第三方目录和社媒不能直接当官网。」** 「可解释」在这里被落成一个可执行、可复现、
市场中性的判据：

> 一台主机是这个组织的官网，**当且仅当**这台主机的名字里能读出这个组织自己的
> 名字（至少 ``min_name_matches`` 个不重叠的实词，且其中至少一个不短于
> ``min_word_length`` 个字符），**并且**这台主机不是 profile 声明过的第三方站点。

这条判据是**故意保守**的：读不出来 ⇒ ``unknown``（不是 ``absent``）。理由写在本模块
的「为什么读不出来时是 unknown」一节。

## 为什么是「名字能互相解释」而不是别的

别的候选判据都在盘上不成立，逐条写下来：

* **主机 == 取回它的那台主机。** 这正是要修的那条接缝。
* **主机上出现过这个组织的名字。** 一个目录页上出现过几百个组织的名字，
  这条判据会把目录站认成每一个组织的官网。
* **组织页面链接到了这台主机。** 链接是页面作者的一次指路，与「这是它的官网」
  不是同一句话（``VenueAsset.candidate_domain`` 的字段文档写的就是这一点）。
  而它同时**过宽**：社交页、目录页、赞助商页都会外链。
* **注册时间 / WHOIS / 备案。** 那是每一次都要联网、要凭据、要付费的外部查询，
  而它仍然只说明「谁注册了这个域名」，不说明「这个域名属于这个组织」。

「名字互相解释」是唯一一条**只用手上这份公开文本就能复现**、而且**错的方向是安全**
的判据：它读不出来时什么都不说（``unknown``），而不是猜一个。

## 为什么读不出来时是 ``unknown`` 而不是 ``absent``

``DomainVerdict.ABSENT`` 说的是「**查过，确实没有**（官网）」。本层没有能力做出
这句话：我们读过的那几页里没有一台主机能被解释成这个组织的域名，**不蕴含**
「这个组织没有官网」—— 更常见的情况是它的官网就在那里，而我们只读到了一份社交页。
把这种情形写成 ``ABSENT``，就是在「我们没找到」与「它不存在」之间签了一个字的假字。

所以本层的产出只有两种：

* 读出来了 ⇒ ``CONFIRMED`` + 域名 + 支撑它的那几行证据；
* 读不出来 ⇒ ``UNKNOWN``，并且**把「哪些候选主机被看过、各自为什么不算」写进
  :attr:`DomainFinding.rejected_hosts`**。

第二格是 §4.2 那条「``rejected`` 与 ``unknown`` 不是同一件事」在这个模型上的落点：
``DomainVerdict`` 是**账号级**的结论（证得了哪一个官网），而「这一台主机不是它的官网」
是**逐主机**的结论 —— 它有自己的主语、自己的理由，所以它是一个列表，不是那个枚举
的第四个成员。往枚举里加一个 ``REJECTED`` 会让「这个账号的主机被逐台看过、全否了」
与「这个账号的官网就是它」在同一个格子上表达不出来。

## 第三方站点

``facebook.com`` / ``instagram.com`` / 各类目录站与托管平台**不可能**是某个组织的官网
（SOP L103 逐字：「第三方目录和社媒不能直接当官网」）。这份名单是**市场知识**，
所以它住在 profile（``official_domain.third_party_hosts``），核心代码不认识任何一个
具体域名 —— 与 ``classification`` 那四条轴的取值同一条纪律。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .enums import DomainVerdict

__all__ = [
    "DomainFinding",
    "OfficialDomainVocabulary",
    "PageObservation",
    "attribute_official_domain",
    "mentioned_hosts",
    "organisation_tokens",
    "host_is_explained_by",
]


#: 各种连字符（含 en dash ``–``、em dash ``—``、非断连字符 ``‑``）折成 ASCII 的
#: ``-``。**这不是排版洁癖**：真实页面上写着 ``www.SW–client03.example.org``
#: （U+2013），而主机标签里的破折号在 IDNA 之后是 ASCII。两处不折到同一个字符上，
#: 「这一页提到的那个域名」就永远配不上「那个域名的规范形式」，而症状只是
#: 「这一页没提过域名」。
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−", "-"))

#: 主机名标签里允许的字符（折半角之后）。
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"

#: 正文里「一台主机」的三种写法。都不是市场知识：它们是 URL 与邮箱地址的形状。
#: * ``scheme://host…`` —— 一条绝对 URL；
#: * ``www.host…`` —— 显式写出来的万维网地址（真实页面上最常见的自指写法）；
#: * ``local@host…`` —— 一个邮箱地址的域名那一半。
_HOST_IN_TEXT = re.compile(
    rf"(?:[a-z][a-z0-9+.-]*://(?P<url>{_LABEL}(?:\.{_LABEL})*))"
    rf"|(?:www\.(?P<www>{_LABEL}(?:\.{_LABEL})*))"
    rf"|(?:[^\s@<>()\[\],;\"']+@(?P<mail>{_LABEL}(?:\.{_LABEL})*))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OfficialDomainVocabulary:
    """``profile.yaml`` 的 ``official_domain`` 块：**判定官网归属的全部市场知识**。

    三个格子都是判据的**参数**，不是判据本身 —— 判据住在
    :func:`attribute_official_domain` 里，它不认识任何一个具体域名。这一条与
    ``ClassificationVocabulary`` 逐字相同：``tests/test_no_hardcoded_market_knowledge.py``
    对 ``marketops/`` 做 AST 扫描（标识符与字符串字面量一起查），把 ``facebook``
    写进本模块会立刻变红。

    ``third_party_hosts``
        第三方目录、社媒、聚合与托管平台。这些主机**不可能**是某个组织的官网。
        名单里写的是 canonical host（``facebook.com``），匹配按**标签边界**做：
        ``facebook.com`` 命中 ``www.facebook.com`` 与 ``m.facebook.com``，
        **不**命中 ``notfacebook.com``（那是一条会静默错放的口子）。
        整块缺失 ⇒ 空元组 ⇒ 没有任何主机因此被排除 —— 一个「读不出官网」的账号
        仍然只是 ``unknown``，不会被误判成别的东西，所以缺名单的方向是安全的。

    ``name_stopwords``
        组织名里不承重的词（``the`` / ``and`` / ``of`` …）。它们的危害不是占位，
        是**凑数**：``min_name_matches`` 数的是「主机的名字里能读出组织名的几个
        实词」，而 ``the`` 这种词几乎出现在每一台主机的名字里 —— 拿它凑够两个，
        判据就废了。

    ``min_name_matches`` / ``min_word_length``
        判据的两个界。默认 ``2`` 与 ``4`` 是**在这份 profile 上量出来的**：
        一个词（``example-village``）能匹配上的主机太多了（一条新闻页的主机名里就有它），
        而两个不重复的实词已经要求主机名基本写出了组织的名字
        （``clientorg01`` ⇒ north + devon、``client-athletics-academy`` ⇒
        athletics + academy）。调低它们是放宽判据 —— 那要一次明确的批准，
        不是一次「让数字好看一点」的改动。
    """

    third_party_hosts: tuple[str, ...] = ()
    name_stopwords: tuple[str, ...] = ()
    min_name_matches: int = 2
    min_word_length: int = 4


@dataclass(frozen=True)
class PageObservation:
    """**我们真的读过的一页** —— 判定官网归属的全部原料。

    三个格子说的分别是「在哪读的」「读到了什么」「凭什么能引用」：

    ``host``
        取回这一页的主机（canonical host）。它是**候选**之一，不是结论 ——
        本模块存在的理由就是不让这一个格子自己决定官网。
    ``url``
        这一页的地址。它随判定结果一起进理由文本，让「读的是哪一页」在事后
        问得出来。
    ``text``
        这一页的**字面正文**。判据只在它上面跑。
    ``evidence_ids``
        这一页落下的证据行。判定成立时它们进
        ``VenueAsset.domain_evidence_ids`` —— 「确认官网」这一步的全部价值在于
        **证据**，所以一个没有证据可指的主机在本模块里**不可能**被确认
        （见 :func:`attribute_official_domain` 的第三步）。
    """

    host: str
    url: str = ""
    text: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainFinding:
    """一次官网判定的**全部产出** —— 包括它否掉了什么。

    ``verdict`` / ``official_domains`` / ``evidence_ids`` 三格与
    ``VenueAsset`` 上同名的三格逐字对应（``verdict`` 在那里叫 ``domain_verdict``），
    它们要一起写进那一行 —— ``_domain_verdict_agrees_with_the_fields`` 是这条
    耦合的唯一执行器，而本类的构造器不重复判它一遍（那会让「谁负责不许自相矛盾」
    有两个答案）。

    ``rejected_hosts`` 是**逐主机**的结论，不是账号级的：``(主机, 理由)`` 的列表，
    按主机字典序。把它写下来是因为「我们看过哪几台主机、各自为什么不算」是审计
    那一步唯一读得到的东西 —— 只报一个 ``unknown``，读的人分不清「没看」
    「看了不像」和「看了、是目录站」。
    """

    verdict: DomainVerdict
    official_domains: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    discovered_hosts: tuple[str, ...] = ()
    rejected_hosts: tuple[tuple[str, str], ...] = ()
    reason: str = ""

    @property
    def confirmed_domain(self) -> str:
        """单数读法：证得的官网。**只在恰好一个时给值**，多个时给空串。

        ``VenueAsset.verified_official_domain`` 是一个格子，而一个组织可以有多个
        官网（主站 + 分区/子品牌站）。所以这一格的判据是**恰好一个** ——
        多个的时候它留空是刻意的：随便挑一个会让「这个组织的官网」在两个运行里
        给出两个不同的答案，而报告上两次都看起来正常。
        """
        return self.official_domains[0] if len(self.official_domains) == 1 else ""


def _fold(text: str) -> str:
    """折半角、折连字符、折大小写。**提取包含两边都必须过同一个函数。**

    单边折的后果不是报错，是「这一页提到过那个域名」这句话悄悄变成假 ——
    而它在报告上与「这一页没提过」逐字相同。
    """
    return unicodedata.normalize("NFKC", text).translate(_DASHES).lower()


def organisation_tokens(organisation: str, vocabulary: OfficialDomainVocabulary) -> tuple[str, ...]:
    """组织名 → 可用于判据的**实词**（保序、去重）。

    去重是承重的：``min_name_matches`` 数的是「不重叠的实词」，而
    ``Example Village and Example Trust`` 里的两个 ``Example Village`` 只是一个词。
    保序是为了让理由文本在两次运行里一样 —— 判据本身不看顺序。
    """
    folded = _fold(organisation)
    words: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[^a-z0-9]+", folded):
        if len(raw) < 3:
            continue
        if raw in vocabulary.name_stopwords:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        words.append(raw)
    return tuple(words)


def host_is_explained_by(
    host: str, tokens: tuple[str, ...], vocabulary: OfficialDomainVocabulary
) -> tuple[bool, str]:
    """这台主机能不能由这些实词解释。返回 ``(成立与否, 理由)``。

    匹配是**子串**而不是标签相等，因为主机的名字会把组织名压成一个词
    （``clientorg01`` / ``client-athletics-academy``），而标签切分切不出那里面的
    三个词。压平之后再比，两边过的是同一个折叠函数。

    三个界一起构成判据，缺一个都会放宽：

    1. 匹配到的实词数 ≥ ``min_name_matches``；
    2. 其中至少一个词的**原文长度** ≥ ``min_word_length``；
    3. 至少有一个匹配词落在主机的**第一个标签**里 —— 也就是「名字被写进去的
       是这台主机的名字，不是它的后缀」。没有第 3 条，``example.co.uk`` 会被
       ``co``/``uk`` 这类后缀词匹配上（今天靠 ``min_word_length`` 挡着，
       而那是一道会被一份新 profile 的词表悄悄挪开的界）。
    """
    if not host:
        return False, "主机是空的"
    if not tokens:
        return False, "组织名里读不出任何实词 —— 没有可以对照的标准"
    folded = _fold(host)
    first_label = folded.split(".")[0]
    squashed = re.sub(r"[^a-z0-9]", "", folded)

    matched = [token for token in tokens if token in squashed]
    if len(matched) < vocabulary.min_name_matches:
        return False, (
            f"主机的名字里只读得出 {len(matched)} 个组织名的实词"
            f"（{'、'.join(matched) if matched else '一个都没有'}），"
            f"少于 {vocabulary.min_name_matches} 个"
        )
    longest = max(matched, key=len)
    if len(longest) < vocabulary.min_word_length:
        return False, (
            f"匹配到的实词里最长的是 {longest!r}（{len(longest)} 个字符），"
            f"短于 {vocabulary.min_word_length} —— 短词在主机名里到处都是，"
            "拿它们凑够数等于没有判据"
        )
    if not any(token in first_label for token in matched):
        return False, (
            f"匹配到的实词（{'、'.join(matched)}）一个都不在这台主机自己的名字"
            f"（{first_label!r}）里 —— 它们落在后缀上，而那不是这个组织的名字"
        )
    return True, f"主机名里读得出 {'、'.join(matched)}"


def _host_is_third_party(host: str, vocabulary: OfficialDomainVocabulary) -> bool:
    """这台主机是不是 profile 声明过的第三方站点。**按标签边界匹配。**

    ``facebook.com`` 命中 ``www.facebook.com`` / ``m.facebook.com``，
    **不**命中 ``notfacebook.com``。子串匹配在这里会静默错放：一台
    ``myfacebook.com`` 是本组织的官网，而它会被当成社媒排除掉 —— 一个
    「排除得太宽」的错误在报告上长得像一次正常判定。
    """
    folded = _fold(host)
    for declared in vocabulary.third_party_hosts:
        declared_folded = _fold(declared)
        if folded == declared_folded or folded.endswith("." + declared_folded):
            return True
    return False


def mentioned_hosts(text: str) -> tuple[str, ...]:
    """正文里提到的主机（字典序、去重、**未规范化**）。

    三种写法都认（见 :data:`_HOST_IN_TEXT`），而**不猜**别的：一段看起来像主机名
    的散文（``see. the. website``）不该被当成域名。判据是「这一页自己写出来的
    地址」，不是「像地址的字符串」。

    返回值未规范化是刻意的：规范化（``www.`` 前缀、大小写、punycode）是
    ``canonical_host`` 的判据，而它属于 ``application/pairing.py`` 那一层 ——
    本模块不做第二份实现。调用方负责把两边折到同一个写法上。
    """
    folded = _fold(text)
    found: set[str] = set()
    for match in _HOST_IN_TEXT.finditer(folded):
        host = match.group("url") or match.group("www") or match.group("mail") or ""
        if host:
            found.add(host)
    return tuple(sorted(found))


def attribute_official_domain(
    organisation: str,
    pages: tuple[PageObservation, ...],
    vocabulary: OfficialDomainVocabulary,
) -> DomainFinding:
    """**这一批页面**能证得的官网是谁。纯函数：不读盘、不联网、不读时钟。

    ## 三步，顺序是承重的

    1. **候选是哪些主机。** 取回这些页面的主机（每页一台），加上页面正文里自己
       写出来的主机。后者是「facebook 页面上写着 ``www.SW-client03.example.org``」
       那一类 —— 它在同一份公开文本里，所以是一条我们能重放的观察。
    2. **逐台问「能不能由组织名解释」。** 还得过第三方站点那一关。两台以上都能
       解释 ⇒ 它们**都是**这个组织的官网（一个组织有主站与子品牌站是常见的），
       而不是从中挑一台。
    3. **证据那一格。** 一台主机只有在**至少一页**支撑它、而那一页带着证据的情况下
       才能被确认。这一条不是形式：``VenueAsset.domain_verdict=confirmed`` 要求
       ``domain_evidence_ids`` 非空，而那正是「确认官网」与「有人说它是官网」的
       全部区别。

    ## 为什么这些页面里没有一台主机可解释时是 ``unknown``

    见模块 docstring 的「为什么读不出来时是 unknown」。一句话：本层能证明的只有
    「这几页里的主机都不是它的官网」，证明不了「它没有官网」。
    """
    tokens = organisation_tokens(organisation, vocabulary)
    discovered = tuple(sorted({page.host for page in pages if page.host}))

    # 候选 → 它出现在哪几页上。**保序去重**，因为理由文本与证据列表都要稳定。
    candidates: dict[str, list[PageObservation]] = {}
    for page in pages:
        if not page.host:
            continue
        candidates.setdefault(page.host, []).append(page)
        for mentioned in mentioned_hosts(page.text):
            candidates.setdefault(mentioned, []).append(page)

    if not tokens:
        return DomainFinding(
            verdict=DomainVerdict.UNKNOWN,
            discovered_hosts=discovered,
            rejected_hosts=tuple(
                (host, "组织名里读不出任何实词") for host in sorted(candidates)
            ),
            reason=(
                f"组织名 {organisation!r} 里读不出可用于对照的实词，所以没有任何"
                "主机能被解释成它的官网。这一格的读数是 unknown（没判定），"
                "不是「它没有官网」。"
            ),
        )

    explained: dict[str, list[PageObservation]] = {}
    rejected: list[tuple[str, str]] = []
    for host in sorted(candidates):
        pages_of = candidates[host]
        if _host_is_third_party(host, vocabulary):
            rejected.append((host, "profile 声明过的第三方站点 —— 社媒与目录不能当官网"))
            continue
        ok, why = host_is_explained_by(host, tokens, vocabulary)
        if not ok:
            rejected.append((host, why))
            continue
        if not any(page.evidence_ids for page in pages_of):
            rejected.append((host, "还没有任何一页证据支撑它 —— 一个没有出处的主机不叫确认"))
            continue
        explained[host] = pages_of

    if not explained:
        return DomainFinding(
            verdict=DomainVerdict.UNKNOWN,
            discovered_hosts=discovered,
            rejected_hosts=tuple(rejected),
            reason=(
                f"读过 {len(pages)} 页，其中没有一台主机能由组织名解释成它的官网。"
                "读数是 unknown（没判定）—— 「我们没找到」与「它不存在」不是同一句话，"
                "而 ABSENT 说的是后一句。"
            ),
        )

    evidence_ids: list[str] = []
    for host in sorted(explained):
        for page in explained[host]:
            for one in page.evidence_ids:
                if one not in evidence_ids:
                    evidence_ids.append(one)

    return DomainFinding(
        verdict=DomainVerdict.CONFIRMED,
        official_domains=tuple(sorted(explained)),
        evidence_ids=tuple(evidence_ids),
        discovered_hosts=discovered,
        rejected_hosts=tuple(rejected),
        reason=(
            f"由组织名解释了 "
            f"{'、'.join(sorted(explained))}（证据 {len(evidence_ids)} 条）。"
        ),
    )
