"""确定性邮件写作者：``DraftWriter`` 的第一个真实实现。

## 这个模块里**没有一封信**

§5 逐字禁止「预生成几百份静态模板」。这里因此没有一个字符串是一封信 —— 有的是每个
语言一小把**句子骨架**（开场、事实、行动号召、落款），正文在
:meth:`DeterministicDraftWriter.draft` 被调用的那一刻，由「骨架 × 阶段 × 事实 ×
已批准说法的原文」拼出来。

这个区分不是审美。静态模板的失效方式是**静默**的：改一处话术要动几百个文件，于是
没人改，于是所有人收到的都是三个月前那一版 —— 而盘上它们全都是「已批准」。骨架只有
一处，意思是「改一句话要改一行」，于是那件事真的会发生。判据是
``test_no_literal_in_the_writer_is_a_whole_letter``：本模块的任何字符串字面量都不许
含换行，因为一封信是由若干段落拼起来的，而段落之间的那个空行只出现在调用的那一刻。

## 正文里的每一个具体内容只有三个来源

1. 请求给的 ``organisation`` —— 调用方自己的账号记录，**不是**写作者的创造。
   它**不进** ``facts_used``：那一格要的是 ``(事实名, 证据 id)``，而这条没有一个
   证据行可引；给它编一个像样的 id 才是 §5 禁止的那件事 —— 下游会拿这一格去
   ``EvidenceStore`` 查，查不到就是一次 ``unverified``。**本实现只报查得到的引用。**
2. ``request.facts`` 的**值那一格**。事实名不进正文（``club_size`` 是机器名，不是
   一句人话），它只出现在 ``facts_used`` 里。
3. ``request.claims`` 里某条 ``ApprovedClaim.text`` 的**逐字**引用。一个字都不改 ——
   改了它就不再是那条被批准的说法，而库里记的仍然是同一个 ``claim_id``。

三条之外没有第四条路：本模块不查网、不读库、不读文件、不读环境变量、不看时钟、
不用随机数（见 ``test_the_same_request_twice_gives_the_same_bytes``）。

## 输入按规范序排，因为 ``generation_key`` 就是这么算的

``request.claims`` 与 ``request.facts`` 在拼装之前按 id/名字排序，而
:meth:`~marketops.ports.outreach.DraftRequest.generation_key` 也是按排序后的写法算的。
两处必须一致：key 相同而正文不同，就是那个 key 存在的理由（「这份输入变了没有」）
被反过来用 —— 同一份输入算出的 key 相同、生成出来的却是**另一封信**，调用方会把它
读成 ``unchanged`` 而把新的那一稿丢掉。判据是
``test_permuting_the_inputs_does_not_change_the_letter``。

## hold 是**结论**，而且本实现从不交回一份被 hold 的正文

写不出信不抛异常（端口 docstring：抛 = 「这次没跑成」）。缺输入、语言选不出、
占位符缺一个、正文里有没填上的变量、正文里有不许出现的链接、正文超长、
主题里带换行 —— 每一种都给一句话的 ``held_reason``，而那句话点名的是缺的那一样。

被 hold 的那一稿**没有正文**，即使正文已经拼出来了。理由不是洁癖：一份带正文的
``HELD`` 稿子会落进库里，而库里的正文是可以被复制的，hold 这个标记在**行**上、
不在**字**里 —— 某一天有人从库里捞一段话术去做模板，捞到的就是这一份。
``DraftProposal`` 允许「正文与理由同时为真」，本实现不用那一半。

## 这个实现**没有**守住的东西（不写在这里就会以为是守住了）

* **不是每一条编造都有一个数字。** 判据
  ``test_the_body_has_no_number_that_the_request_did_not_give`` 只覆盖含数字的那一类
  （预算、台数、年份）。一个编出来的地名、人名、产品能力里可以一个数字都没有，
  而这一层没有任何东西能识别它 —— 它靠的是「骨架里不写话术、话术只从
  ``ApprovedClaim.text`` 逐字进来」这条**结构**约束，不是一条判据。
* **裸域名与 ``mailto:`` 不算链接。** 见 :data:`_LINK_RE` 的说明。
* **主题的最大长度与换行是两条硬判据**，但主题里出现的**内容**只有
  ``organisation`` 与阶段措辞两样，所以「主题不许出现某类内容」这件事在这里没有位置。
* **``generic_wording_id`` 只是一张牌照，不是一句话。** 策略给的是那个通稿措辞的
  **id**，而本层拿不到它的正文。所以那一档落下来的是一封只用骨架的通用信，
  而不是「那段被批准的通稿措辞」；正文里也不会出现那个 id（它是个 id，不是措辞）。
  库里因此看不出这一稿是按哪一条通稿措辞写的 —— ``DraftProposal`` 上没有那一格。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..domain.outreach import (
    MAX_SUBJECT_CHARS,
    OutreachStage,
    WritingPolicy,
)
from ..domain.util import sha256_text
from ..ports.outreach import DraftProposal, DraftRequest, DraftWriter

#: 本模块在盘上那一份源码，**import 时读一次，此后不重读**。
#:
#: 不重读的理由同 ``adapters/build_stamp.py``：一个起了三小时的进程在这三小时里被
#: 改过之后重读，报出的是「现在盘上是什么」，而 :data:`WRITER_ID` 要答的是
#: 「**这个进程**加载的是哪一份写作者」。那个方向的错正是这一格存在的理由。
#:
#: 读不出来时**不兜底**，让 ``OSError`` 就地炸在 import 上：兜底只有一个写法
#: （填一个像指纹的值），而那正是这一格要修的东西 —— 一份兜过底的 ``writer_id``
#: 在回执上与真的一模一样，而它盖住的是一个没法复现的实现。
_SOURCE = Path(__file__).read_text(encoding="utf-8")


def writer_content_digest(source: str) -> str:
    """一份写作者源码 → 它的内容指纹（前 12 个十六进制字符）。

    精确的配方，好让**第二个人**拿一台 shell 独立算一遍核对：先把 CRLF 与 CR 都
    归一成 LF，再按 UTF-8 编码，sha256，取前 12 个十六进制字符（换行先归一，是因为
    一份 CRLF 检出与一份 LF 检出是同一份代码，不该得到两个写作者）。

    ``source`` 是**参数**而不是就地读本模块，是为了让判据能喂它一段改过的源码：
    「改一个字，指纹就换一个」这件事只有在指纹可被喂输入时才测得到。本模块自己
    传的是 :data:`_SOURCE`。
    """
    return sha256_text(source.replace("\r\n", "\n").replace("\r", "\n"))[:12]


#: 这个写作者**自己**是谁：实现名 + 由本模块源码算出来的内容指纹。
#:
#: ## 指纹那一半是**算出来的**，所以「忘了改」在结构上做不到
#:
#: 在这一版之前，这一格是一句手写的 ``"deterministic-composer-v1"``，上面写着一条
#: 要求：「改动本模块的**任何**一句骨架、任何一条判据，都要把版本那一半加一」。
#: 那条要求**没有任何东西在执行** —— 忘加一的那一天，``generation_key`` 算出的 key
#: 与上一版逐字相同，于是重跑一遍得到的那一稿被读成 ``unchanged`` 而不落库，换出去
#: 的还是按旧骨架写的那一句，而报告上一切正常。这条缺口记在上一轮的验收报告里
#: （``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §H 第 9 条）。
#:
#: 判据 ``test_the_version_half_of_the_writer_id_is_the_digest_of_this_file`` 把这一格
#: 与**现场重算的结果**钉在一起。它顺带钉住了另一件事：那个测试重读文件、这里用的是
#: import 那一刻读到的文本，两者对不上就说明**这个进程加载的代码不是盘上这一份**
#: —— 同尺寸的改动骗得过 ``.pyc`` 的校验，而那对 ``generation_key`` 就是一次静默的
#: 「换一个实现而 key 不动」。
#:
#: ## 它覆盖什么
#:
#: 本模块**全部**源码：每张骨架表的每一句话、每一条判据（``_length_reason`` /
#: ``_link_reason`` / …）、:func:`_render` 与 ``_compose`` 的拼装顺序、两个正则、
#: 保留槽位表 —— 连注释与 docstring 也算在内。于是「本模块改了一个字」与
#: 「这个 id 换了一个」是同一件事。
#:
#: ## 它**不**覆盖什么（这一层够不着，写在这里免得以为它覆盖了）
#:
#: * **被依赖的常量** —— ``domain/outreach.py`` 的 ``MAX_SUBJECT_CHARS`` 那类。
#:   改了它，同一份输入可能从「写得出来」变成「超长 hold」，而这个 id 一动不动。
#: * **调用方给的两样**：``policy_version`` 与请求本身。它们在 ``generation_key`` 里
#:   各自有自己的位置，不在这一格。
#: * **解释器与依赖库的行为差异。** 这是刻意的：指纹散的是**源码文本**而不是字节码，
#:   于是同一个 revision 在哪台机器、哪个 Python 上都得到同一个 id —— 回执上
#:   「哪一版的写作者产出这封信」因此可以被第二个人独立算一遍核对。
#:
#: ## 注释也算在内，是**方向**上的一次取舍
#:
#: 过宽的代价：动一句注释就换一个 id，重跑一遍会多写一版与上一版**逐字节相同**的
#: 草稿。而回执里 ``writer_id`` 与 ``body_sha256`` 是并排的两格，所以「写作者变了、
#: 正文没变」读得出来；多出来的那一版还是一件看得见的修订，不是一次覆盖（§12 要的
#: 正是修订而不是覆盖）。
#:
#: 过窄的代价：一个由**别的实现**写出来的结果被当成原来那一稿留下，而回执说它没变过
#: —— 没有任何一格看得出这件事发生过。两个方向不对称，所以这里选过宽。
WRITER_ID = f"deterministic-composer-v1+{writer_content_digest(_SOURCE)}"

#: 骨架里的占位符：``{{名}}``。名字里不许有花括号 —— 一个能嵌套的名字会让替换变成
#: 递归，而递归替换就是**注入**：一条事实的值里写下 ``{{organisation}}``，
#: 就能把别处的输入搬进这一句（见 :func:`_render`）。
_TOKEN_RE = re.compile(r"\{\{([^{}]*)\}\}")

#: 正文里算「一个链接」的东西：带协议的（``https://…``）、协议相对的（``//host``）、
#: 以及 ``www.`` 开头的。三种都认，因为策略那句话问的是「正文里出现了什么」，
#: 不是「它写成了哪种形式」。
#:
#: **它认不出来的**：没有协议也没有 ``www.`` 的裸域名（``example.com``）与
#: ``mailto:`` 这类只有 scheme 的串。这两样漏过去不是「允许」，是这一层看不见 ——
#: 所以一条空的许可清单配上一条含裸域名的说法时，本实现**不会**停下来。
_LINK_RE = re.compile(r"(?:[a-z][a-z0-9+.\-]*:)?//[^\s<>\"']+|\bwww\.[^\s<>\"']+", re.IGNORECASE)

#: 骨架自己带的那三个槽位名。``organisation`` 在开场句里，``stage_phrase`` 在开场句
#: 与主题里，``call_to_action`` 在行动号召那一句上（它随 ``require_call_to_action``
#: 出现或消失）。这三个名字是**保留**的：一条与它们同名的事实会 hold，
#: 理由是那个槽位会有两个值（见 :func:`_fact_name_reason`）。
_RESERVED_TOKENS: tuple[str, ...] = ("organisation", "stage_phrase", "call_to_action")


@dataclass(frozen=True)
class _LocaleSkeletons:
    """一种语言的**一小把句子**。**它是一把句子，不是一封信。**

    每个字段都是**一句话**（或者一句带占位符的话），它们之间没有顺序关系 —— 顺序在
    :meth:`DeterministicDraftWriter._compose` 里，是那一段代码的事。把顺序也写进数据
    结构（比如一个"整封信的模板"）的那一天，这个类就退化成了一份静态模板。

    ``fact_line`` 里的 ``{}`` 是**事实名放进去的位置**：那条句子靠它才知道自己该填
    哪一个槽。事实名本身不进正文 —— 见模块 docstring 第二条第 2 点。
    """

    subject: str
    opening: str
    stage_phrases: Mapping[OutreachStage, str]
    fact_line: str
    closer: str
    call_to_action: str


_EN_GB = _LocaleSkeletons(
    subject="{{stage_phrase}} to {{organisation}}",
    opening="This is {{stage_phrase}} to {{organisation}}.",
    stage_phrases={
        OutreachStage.STAGE_1: "a first note",
        OutreachStage.STAGE_2: "a short follow-up",
        OutreachStage.STAGE_3: "a second follow-up",
        OutreachStage.STAGE_4: "a closing note",
    },
    fact_line="One detail we hold on record: {}.",
    closer="Thank you for your time.",
    call_to_action="If a short conversation would be useful, please reply to this message.",
)

_EN_US = _LocaleSkeletons(
    subject="{{stage_phrase}} about {{organisation}}",
    opening="This is {{stage_phrase}} about {{organisation}}.",
    stage_phrases={
        OutreachStage.STAGE_1: "a first note",
        OutreachStage.STAGE_2: "a quick follow-up",
        OutreachStage.STAGE_3: "a second follow-up",
        OutreachStage.STAGE_4: "a closing note",
    },
    fact_line="One detail we keep on file: {}.",
    closer="Thanks for your time.",
    call_to_action="If this seems worth a short conversation, please reply to this message.",
)

#: 语言标签 → 骨架表。
#:
#: ``en-GB`` 与 ``en-US`` 是**两种语言**，不是同一套话换个标签：主题与事实那一句的
#: 措辞不同，于是同一份输入在两种语言下得到两封信（``generation_key`` 把 locale 算在
#: 里面，正是为这件事）。**表里没有的语言一律 hold**，不许拿英语凑数 ——
#: ``test_a_locale_without_skeletons_is_held_and_not_written_in_english``。
_SKELETONS: Mapping[str, _LocaleSkeletons] = {"en-GB": _EN_GB, "en-US": _EN_US}


def _tokens(text: str) -> tuple[str, ...]:
    """这段文本里的占位符名，按出现顺序。"""
    return tuple(_TOKEN_RE.findall(text))


def _render(template: str, values: Mapping[str, str]) -> str:
    """把 ``{{名}}`` 换成 ``values`` 里的值。**换不出来的原样留着，不静默丢掉。**

    一次替换，**不递归**：``re.sub`` 不会回头去看它刚插进去的那段文本。递归替换会把
    一条事实的值变成一处入口 —— 值里写 ``{{organisation}}`` 就能把别处的输入搬进
    这一句，而这一句的 ``facts_used`` 记的仍然是那条事实的证据 id。

    换不出来的那一个留着，是为了让**后面那条判据**看见它：一个被静默丢掉的占位符
    等于把一句必须出现的话删掉了，而删掉之后正文读起来完全正常。
    """
    return _TOKEN_RE.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def _sentence_case(text: str) -> str:
    """只用在**本模块自己的**句子上：主题行首字母大写。

    它**不得**用在 ``ApprovedClaim.text`` 或事实的值上 —— 那两样是逐字的输入，
    改一个字母就不再是同一条被批准的说法（而库里记的还是同一个 claim_id）。
    """
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class _Composed:
    """拼装的结果。**它与 :class:`DraftProposal` 分开**是为了让「没有正文」这件事
    有一个位置：``held_reason`` 非空的那一条上，另外四格全是空的（见
    :meth:`held`）—— 一个「什么都不用交回去」的结果，而不是一个各部分都有的提议。
    """

    subject: str = ""
    body: str = ""
    facts_used: tuple[tuple[str, str], ...] = ()
    claim_ids: tuple[str, ...] = ()
    held_reason: str = ""

    @classmethod
    def held(cls, reason: str) -> "_Composed":
        """停下来。**这一条不带正文、不带引用** —— 连已经拼出来的那一段也不带。

        理由见模块 docstring：一份带正文的 ``HELD`` 稿子会落进库里，而库里的正文是
        可以被复制的。写不出来的东西就不交出去，比交出去再在行上标一个记号更可靠。
        """
        return cls(held_reason=reason)


def _fact_name_reason(facts: tuple[tuple[str, str, str], ...]) -> str:
    """事实名能不能当占位符用。返回一句话的理由，或空串。

    三种情形都要停下来，而三种的处置是同一个（让调用方去修输入）：

    * **同名两条** —— 正文里那个值该引用哪一条证据没有答案。取第一条是**猜**，
      而猜错的那天报告上完全正常：下游拿一个**查得到**的证据 id 去核实，
      查到的那句话不是正文里的那一句。
    * **与保留槽位同名**（``organisation`` / ``stage_phrase`` / ``call_to_action``）——
      骨架本来就要填那个槽位，而两条路会给出两个值。同一个问题，同一个答案。
    * **名字里有花括号** —— 花括号是占位符的语法。``{{a{b}}`` 匹配不上任何占位符，
      于是这个名字永远填不进正文，而它照样得进 ``facts_used``。
    """
    seen: set[str] = set()
    for name, _value, _evidence_id in facts:
        if "{" in name or "}" in name:
            return (
                f"有一条事实的名字里有花括号（{name!r}），而花括号是占位符的语法 —— "
                "这个名字填不进正文，而它照样得进 facts_used。"
            )
        if name in _RESERVED_TOKENS:
            return (
                f"有一条事实的名字与保留槽位 {name!r} 同名 —— 骨架本来就要填那个槽位，"
                "而两条路给出两个值：正文里该写哪一个没有答案，不得猜。"
            )
        if name in seen:
            return (
                f"有两条事实同名（{name!r}）—— 正文里那个值该引用哪一条证据没有答案，"
                "不得猜。"
            )
        seen.add(name)
    return ""


def _encoding_reason(body: str, subject: str) -> str:
    """正文与主题能不能编成 UTF-8。返回一句话的理由，或空串。

    §5 逐字要求「校验 UTF-8」。Python 的字符串几乎总是编得出来，唯一的例外是
    **孤立的代理码位**（U+D800 那一段里单独出现的一个）—— 它从一份被截断的输入里
    流得进来，而它落在正文里的那一天坏的是**发信那一步**，不是这一层：SMTP 会话
    在中途断掉，而那一封的账记在「投递未知」上。

    所以它在这里是一条判据，而不是一个 ``try`` 包在别处。
    """
    for where, text in (("正文", body), ("主题", subject)):
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            return (
                f"{where}里有无法编成 UTF-8 的字符（孤立的代理码位）—— "
                "这一稿停下来，去看那一条输入的来源。"
            )
    return ""


def _unfilled_reason(body: str, subject: str) -> str:
    """正文或主题里还有没有被填上的占位符。返回一句话的理由，或空串。

    §5 逐字要求「校验……未填充变量」。这里能出问题的路只有一条：**值里又出现了
    占位符**（一条事实的值写成 ``{{x}}``），而 :func:`_render` 不递归，所以它原样
    落在正文里。这正是要停下来的那种事 —— 一封带着 ``{{x}}`` 发出去的信，
    收件人看到的是我们内部的变量名。

    判词里报的是**变量名**，因为「少了哪一样」正是要拿去补的那个东西。
    """
    for where, text in (("正文", body), ("主题", subject)):
        leftover = _tokens(text)
        if leftover:
            return (
                f"{where}里还有 {len(leftover)} 个没有被填充的变量（{list(leftover)}）—— "
                "未填充变量的正文不得发出去；缺的那一样要么补进请求，要么由写作者停在这里。"
            )
    return ""


def _required_placeholder_reason(policy: WritingPolicy, carried: set[str]) -> str:
    """策略要求的变量，这一稿的正文里有没有。返回一句话的理由，或空串。

    判据是「正文里**出现**了没有」，不是「算不算得出来」。这两者不同，而且不同的那
    一处是真的：``call_to_action`` 这个槽位随时算得出来，但
    ``require_call_to_action=False`` 时那一句根本不出现 —— 一个要求了行动号召、
    又把行动号召关掉的策略本身就是一份矛盾的策略，处置是让人去看策略，
    不是替它挑一边。

    ``{{x}}`` 与 ``x`` 是同一种写法的两半：只认一种，判据就变成了关于**拼写**的，
    而它本该是关于内容的。判词里报的是策略里的**原写法**。
    """
    missing = [
        raw
        for raw in policy.required_placeholders
        if raw.strip().strip("{}").strip() not in carried
    ]
    if not missing:
        return ""
    return (
        f"策略 {policy.policy_id!r} 要求正文里出现 {missing}，而这一稿的正文里没有 —— "
        f"写作者能填的槽位只有 {sorted(carried)}。缺变量的正文不得发出去。"
    )


def _link_reason(body: str, subject: str, allowed: tuple[str, ...]) -> str:
    """正文或主题里有没有不被允许的链接。返回一句话的理由，或空串。

    ``allowed`` 为空 = **一个链接都不许出现**（失败关闭）。这条方向是刻意的：一条
    没写链接规则的策略，被读成「随便放」的那天，一封带链接的信与一封不带链接的信在
    报告上完全一样 —— 而链接是这一步唯一能把收件人带到某处去的东西。

    主题也查，用的是同一条规则：一封关于链接的策略不该因为那个链接落在主题那一行
    就失效。

    判词里报**是哪一条链接**（它与说法/事实一样来自请求，库里本来就有）。
    这与 ``application/outreach.py::_uncited_inputs`` 的取舍相反，那一处只报条数：
    那里的字符串是**写作者**的输出，而这里的链接是**调用方自己给的**输入。
    """
    for where, text in (("正文", body), ("主题", subject)):
        for match in _LINK_RE.finditer(text):
            link = match.group(0)
            if any(link.startswith(prefix) for prefix in allowed):
                continue
            return (
                f"{where}里出现了链接 {link!r}，而这一族的 allowed_link_prefixes 是 "
                f"{list(allowed)} —— 一条不在许可清单里的链接不得出现在一封信里。"
            )
    return ""


def _length_reason(body: str, policy: WritingPolicy) -> str:
    """正文有没有超过策略的长度上限。返回一句话的理由，或空串。

    **不截断。** 截断会把一句话砍成两半，而砍掉的那一半可能正好是那个「不」字 ——
    发出去的正文与批准过的正文不再是同一份，而摘要记的是改之前那一个。
    拼不下就停下来，让人去看是事实太多还是上限太小。

    判词里两个数都报：只报一个（「超长了」）没法判断该修哪一边。
    """
    if len(body) <= policy.max_body_chars:
        return ""
    return (
        f"正文有 {len(body)} 个字符，超过策略 {policy.policy_id!r} 的 "
        f"max_body_chars={policy.max_body_chars} —— 写作者不截断，这一稿停下来。"
    )


def _subject_reason(subject: str) -> str:
    """主题能不能用。返回一句话的理由，或空串。

    ``OutreachDraft`` 对主题有两条硬约束（不得含换行、不得超过长度），而
    ``DraftProposal`` 是**外部写作者**给的 —— 让它在构造草稿那一步才炸，症状是
    「这一条 failed」，而真正该做的事是去看请求里那个组织名。

    换行那一条不是排版问题：一封主题里带换行的信在拼报文的那一步会变成**两个头**，
    而第二个头是这里写的 —— 那是头部注入。
    """
    if "\n" in subject or "\r" in subject:
        return (
            "主题里有换行 —— 拼报文时那会变成两个头，而第二个头是写作者写的。"
            "这一稿停下来。"
        )
    if len(subject) > MAX_SUBJECT_CHARS:
        return (
            f"主题有 {len(subject)} 个字符，超过 {MAX_SUBJECT_CHARS} —— "
            "这一稿停下来，去看请求里那个组织名。"
        )
    return ""


class DeterministicDraftWriter(DraftWriter):
    """按骨架、阶段、事实与已批准说法拼信。**确定性的：同一份输入得到同一批字节。**

    它存在的理由写在 ``ports/outreach.py::DraftWriter``：本仓的第一版实现是确定性的，
    第二版会是一个模型后端。这一版不是最终形态，它只是把「用途、输入、输出回执、
    校验」这几条边界先立起来，让模型那一版接进来时不需要改这些边界。

    它是**纯的**：没有构造期要读的东西、没有自带的缓存、没有可以变的字段。同一个实例
    被两个线程同时用也不会得到两种结果 —— 因为没有任何一次调用写过实例上的东西。
    """

    def __init__(self, *, policy_version: str = "") -> None:
        """``policy_version`` 是**构造参数**，不是本模块里的一个字面量。

        它是策略那一侧的东西：这个写作者拿不到「现在这一份策略是第几版」。写死一个
        值的意思，是策略改了而 ``writer_id`` 不变时，``generation_key`` 也不变 ——
        于是改了策略之后重新生成的那一稿会被读成 ``unchanged`` 而不落库，
        换出去的还是按旧策略写的那一句。

        缺省是空串（"我不知道"），而不是一个像版本号的值：本仓的纪律是**未知不得
        写成一个像样的值**，而空串在报告上看得出来。
        """
        self._policy_version = policy_version

    @property
    def writer_id(self) -> str:
        """实现名 + 本模块源码的内容指纹，见 :data:`WRITER_ID`。

        它**不是**一个可以被构造参数改掉的值（与下面那个 ``policy_version`` 正相反）：
        换一个策略版本是一次授权范围的改变，而换一个写作者是换一个实现 ——
        后者不该由调用方随口指定。
        """
        return WRITER_ID

    @property
    def policy_version(self) -> str:
        """这个写作者按的策略版本 —— 由调用方在构造时给，本层不认识它。"""
        return self._policy_version

    def draft(self, request: DraftRequest) -> DraftProposal:
        """拼一稿。**它不写库、不发信、不改状态** —— 那都是调用方的事。

        抛异常在这里是不可能的：本实现没有后端、没有网络、没有磁盘，凡是「这次没跑成」
        的那一类失败它都没有。写不出来的情形是一条 ``held_reason``（见 :class:`_Composed`）。
        """
        composed = self._compose(request)
        return DraftProposal(
            subject=composed.subject,
            body=composed.body,
            facts_used=composed.facts_used,
            claim_ids=composed.claim_ids,
            # 没有附件：调用方没有给过任何一份，而「凭空添一个附件」正是这一层
            # 不该有的那种自由（附件是清单与授权的一部分，不是写作者的）。
            attachments=(),
            held_reason=composed.held_reason,
            writer_id=self.writer_id,
        )

    def _compose(self, request: DraftRequest) -> _Composed:
        """拼装，以及**每一条判据**。顺序是有后果的，见下面每一段的注释。"""
        policy = request.policy

        # --- 语言：先定，因为定不下来时这一稿根本没有语言可写 ----------------
        #
        # 这一条排在最前面，是因为它之后的一切（骨架、主题、行动号召那一句）
        # 都是按语言选的。策略要求的语言与请求给的语言不一致时**不挑一边**：
        # 用哪一边写都是一个我们没有被授权做的选择，而两边的收件人不同。
        if policy.required_locale and policy.required_locale != request.locale:
            return _Composed.held(
                f"策略要求的语言是 {policy.required_locale!r}，而这一份请求的语言是 "
                f"{request.locale!r} —— 用哪一边写都是一个我们没被告知的选择，"
                "所以停下来。"
            )
        locale = policy.required_locale or request.locale
        skeletons = _SKELETONS.get(locale)
        if skeletons is None:
            return _Composed.held(
                f"写作者没有 {locale!r} 的骨架表（有的是 {sorted(_SKELETONS)}）—— "
                "拿另一种语言凑数，收件人收到的是一封他没读过那句话的信。"
            )
        stage_phrase = skeletons.stage_phrases.get(request.stage)
        if stage_phrase is None:
            # 今天不可达：表里四个阶段都在，由
            # ``test_every_locale_covers_every_stage`` 守着。它留着是因为将来多一个
            # 阶段时，症状应当是一句话的结论，而不是一个 KeyError（端口 docstring：
            # 抛 = 「这次没跑成」，而这是一条关于输入的结论）。
            return _Composed.held(
                f"写作者的 {locale} 骨架表里没有 {request.stage.value} 的阶段措辞 —— "
                "这一稿停下来，去看这一族是怎么配的。"
            )

        # --- 输入按**规范序**排：同一个 generation_key 必须给出同一封信 -------
        claims = tuple(sorted(request.claims, key=lambda claim: claim.claim_id))
        facts = tuple(sorted(request.facts, key=lambda fact: (fact[0], fact[1], fact[2])))

        ambiguous = _fact_name_reason(facts)
        if ambiguous:
            return _Composed.held(ambiguous)

        # §5 逐字：「当个性化缺失时，使用已批准的通稿措辞或停下，而不是猜」。
        # 两样都没有、而又没有通稿措辞那一档时，**什么都写不出来** —— 骨架能拼出一封
        # 语法正确的信，但那样的一封里一个具体内容都没有，它只是一封礼貌的噪音，
        # 而报告上它与一封个性化过的信长得一样。
        if not claims and not facts and not policy.generic_wording_id:
            return _Composed.held(
                f"这一族没有已批准的说法，也没有任何可引用的事实，而策略 "
                f"{policy.policy_id!r} 没有给出通稿措辞（generic_wording_id 为空）—— "
                "正文写不出来，不得猜。"
            )

        # --- 拼装：骨架 × 阶段 × 事实 × 已批准说法的原文 ----------------------
        values: dict[str, str] = {
            "organisation": request.organisation,
            "stage_phrase": stage_phrase,
        }
        sentences: list[str] = [skeletons.opening]
        carried: set[str] = set(_tokens(skeletons.opening))
        facts_used: list[tuple[str, str]] = []
        for name, value, evidence_id in facts:
            values[name] = value
            carried.add(name)
            facts_used.append((name, evidence_id))
            # 事实名只做 token 的名字，不进正文：``{}`` 那一格放的就是它。
            sentences.append(skeletons.fact_line.format("{{" + name + "}}"))
        # 已批准的说法**逐字**成段，一个字都不改写。
        sentences.extend(claim.text for claim in claims)
        if policy.require_call_to_action:
            sentences.append(skeletons.call_to_action)
            carried.add("call_to_action")
        sentences.append(skeletons.closer)

        body = _render("\n\n".join(sentences), values)
        subject = _render(
            skeletons.subject,
            {**values, "stage_phrase": _sentence_case(stage_phrase)},
        )

        # --- 判据：全文查一遍，任何一条不过就是一条 held_reason ----------------
        #
        # 每一条都**在这封信被交回去之前**判，而不是留给下游某一步：下游那一步看到的是
        # 一稿已经有正文的东西，而它的失败处置是「这一条 failed」——那说的是写作者坏了，
        # 而这里每一件事都说得清是哪一样输入不对。
        for reason in (
            _encoding_reason(body, subject),
            _unfilled_reason(body, subject),
            _required_placeholder_reason(policy, carried),
            _link_reason(body, subject, policy.allowed_link_prefixes),
            _length_reason(body, policy),
            _subject_reason(subject),
        ):
            if reason:
                return _Composed.held(reason)

        return _Composed(
            subject=subject,
            body=body,
            facts_used=tuple(facts_used),
            claim_ids=tuple(claim.claim_id for claim in claims),
        )


__all__ = ["WRITER_ID", "DeterministicDraftWriter", "writer_content_digest"]
