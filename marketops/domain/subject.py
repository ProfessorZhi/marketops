"""一个 WorkItem 针对**谁** —— 受理时冻住的主体身份，以及「这份身份够不够开工」。

## 为什么它必须是一个值，而不是 work item 上的一个字符串

``WorkItem.subject_ref`` 是一个内部 slug（``kent-003``）。它够当 id，不够当身份。
2026-09-23 那次真实运行的 worker 拿到 ``kent-003`` 之后，花掉 9 次检索去搜这个名字，
十条命中全是无关的同名物。盘上没有任何一处告诉它这是在说 Example School One ——
因为它拿到的回执里只有 slug 与两个散列。

slug 不是身份。把 slug 当身份用，失败形态不是报错，是**一次看起来很正常的检索
把预算花光**，然后交回一份「什么都没找到」的结算。这类失败在报告里与「这个账号
真的没有公开信息」不可区分。

## 这里装的是**线索**，不是已核实事实

受理那一刻，本部署对主体的一切了解都来自调用方给的那份清单 —— 一份**输入**。
所以本记录的字段全部是输入线索（input clues）：它们说「有人说这是谁」，
不说「这是谁」。

已核实事实走另一条路：它们要有来源、要有 URL、要有内容摘要，落在
``Evidence`` 上（``domain/evidence.py``）。两条路不能互相顶替 —— 一个
``organisation`` 字段里的名字是**待核实的线索**，把它读成已核实事实，
正是「账号为什么还是 discovered」这个问题的答案被人跳过的原因。

## 刻意**没有**的字段

* **没有 ``cohort`` / ``status`` / 任何先前等级。** 它们是**判定**，是上一次工作
  的结论。把判定放进任务包，就是让历史标签决定这一次怎么做 —— 而这份清单的
  ``cohort`` 那一格是本仓已知的张力（``artifacts/marketops-runtime/inputs/
  kent-2026-09-21.yaml`` 的文件头自己写着这件事），它来自一次另一口径的筛选。
  判定要重做，依据要重新走证据。
* **没有 ``notes`` / 任何自由文本。** 用户逐字：notes/来源文本是**数据**，不是
  可以改变权限的指令。而任务包会被一个模型逐字读 —— 把自由文本塞进去，等于给
  来源文本修一条通往指令的路。笔记留在输入文件里；任务包只带走对**整行**的摘要
  （:attr:`SubjectDossier.input_sha256`），于是「这一行被改过没有」答得出来，
  而「这一行写了些什么」不进入任何一个会被当作指令读的位置。

省略是有代价的，代价写在这里：worker 因此**看不到**运营者写在清单里的备注。
如果将来确实需要把某条备注交给 worker，那需要的是一条有来源、有归属的通道，
不是把 ``notes`` 原样塞回任务包。
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import DomainVerdict
from .util import check_canonical_id

#: :meth:`SubjectDossier.payload` 里必须是字符串的那五格。
#: 六格里唯一不在其中的是 ``country`` —— 它允许是 ``null``。
_TEXT_FIELDS = ("account_id", "organisation", "domain", "source_ref", "input_sha256")

#: 账号这一类主体的 ``subject_kind`` 取值 —— **唯一一处定义**。
#:
#: 它原来住在 ``application/inputs.py``（那里是账号池的读点），2026-09-27 搬到这里：
#: 续跑意图（``domain/continuation.py``）也要用它，而领域层不得 import 应用层 ——
#: 于是那个字面量会有第二个产地。两处各写一遍的后果不是文案不一致：``Observation``
#: 的主键里就有这一列，对不上的表现是**同一格被当成两格**（一格「从来没看过」、
#: 另一格也是），而 ``what_is_due`` 会因此一直说这一格该看。
#:
#: 取值 ``account`` 与 ``domain/maintenance.py`` 的 ``Observation`` docstring 举的
#: 例子（``account / route / venue``）逐字一致 —— 那一句是这套词表的出处。
SUBJECT_KIND_ACCOUNT = "account"


@dataclass(frozen=True)
class SubjectDossier:
    """受理时冻住的**主体身份线索** —— 一个 work item 针对谁。

    ## 字段

    ``account_id``
        主体在**本仓**的 id，同时也是 ``WorkItem.subject_ref``。必须是
        :func:`~marketops.domain.util.check_canonical_id` 的不动点：它会变成
        证据文件的名字（``<account_id>.jsonl``），而一个需要被规范化的 id 会与
        另一个 id 落到同一个文件上 —— 两个不同的账号静默合并，报告上只显示
        「证据更多了」。

    ``organisation``
        输入声称的组织名。这是**身份发现的依据**：有了它，worker 才知道
        ``kent-003`` 说的是 Example School One。

    ``domain``
        官方域名，若输入给了。**不知道不必拒绝** —— 没有域名的身份发现照样能做
        （先搜名字、再确认官网）。但「有名字没域名」与「只有 slug」是两个不同
        的世界，见 :func:`identity_refusal`。

        这一格是**线索**：它是给人（与检索）看的那一个值，取值优先用证得的那个，
        没有再退回别人说的那个。真正被拿去判定的**不是它** —— 是
        :attr:`domains` 与 :attr:`domain_verdict` 那一对，理由见下。

    ``domains`` / ``domain_verdict``
        这个主体的**已知域名集合**，以及那个集合是**怎么知道的**。

        两格一起才是一句话。``domains=("example.org",)` 配上
        ``domain_verdict=unknown`` 说的是「清单里写着它是 ``example.org``，
        但没有人证过」；配上 ``confirmed`` 说的是「我们证得了它的官网就是它」。
        绑定那一层（``domain/bind.py``）**只把** ``confirmed`` 读成权威：

        * 证得过的账号，跨域邮箱可以判成**确证的外来域**（SOP L121 那条拒信）；
        * 只有转述、没有证得的账号，跨域邮箱**不得**被拒 —— 它只是「我们还没证得
          这个账号的域」，而那不是「这条邮箱不属于它」。诚实的做法是让这一条走
          「条目内证据」那条路，证据不足时落 ``unbound_email``（hold/机会）。

        这一条是 2026-09-26 那次审计的直接产物：此前 ``discovered_host``
        （**我们取回这一页的那台主机**）被当成账号域名权威，于是社媒页上那个
        真实域名会被判成外来域，而目录站的主机会被当成它自己的域名。三句话
        （别人说的 / 我们看到的 / 我们证得的）在报告里长得一样，而处置相反。

    ``country``
        输入给出的地理线索。它是**线索**：``AccountRecord.country`` 的缺省曾经是
        ``"UK"``，那是一次没人做过的判定（见 ``domain/evidence.py``），所以这里
        保持可空 —— ``None`` 就是「输入没说」，不是一个地理结论。

    ``source_ref``
        这一行是从哪来的：哪一份清单、哪一次导出。回执要能指向它，否则
        「这个名字是谁说的」在事后无从问起。

    ``input_sha256``
        受理那一刻**这一整行输入**的字节摘要。它的用途只有一个：让同一份输入与
        改过的输入分得开。作业级的 ``request_sha256`` 覆盖的是
        ``(scene, input_kind, input_ref)`` 三个名字，**不含文件内容** —— 所以在
        没有这个字段之前，「同一个幂等键、同一个文件名、内容换过了」是一次静默
        的重编译，而重编译出来的工作单元看起来与原来一模一样。
    """

    account_id: str
    organisation: str = ""
    domain: str = ""
    country: str | None = None
    source_ref: str = ""
    input_sha256: str = ""
    #: 已知域名**集合**。缺省空 = 这一份身份没有任何域名知识（不是「它没有官网」）。
    domains: tuple[str, ...] = ()
    #: 上面那个集合到哪一步了。缺省 ``unknown``：**没有证过**。
    #:
    #: 缺省的方向是刻意的：一个没写这一格的调用点拿到的是「没证过」，而它买到的是
    #: 一次**不做判定**（跨域邮箱不拒、也不强绑），不是一次默认通过。
    domain_verdict: DomainVerdict = DomainVerdict.UNKNOWN

    def __post_init__(self) -> None:
        check_canonical_id(self.account_id, "account_id")
        for field_name in ("source_ref", "input_sha256"):
            value = getattr(self, field_name)
            if not value:
                raise ValueError(
                    f"SubjectDossier.{field_name} 不能为空。\n"
                    "这两个字段答的是「这一行从哪来」与「它是哪一版的字节」—— "
                    "一个空值在回执里与「没有这一项」长得一样，而收下它会让一份"
                    "来历不明的身份看起来像是冻住过的。"
                )
        # 域名集合**归一**：小写、去空白、去重、定序。变换是幂等的，而这很重要 ——
        # 这个值会被折进 ``work_item_snapshot`` 的载荷，而载荷的字节决定作业的
        # 幂等键。同一批身份在两次编译里给出两种顺序，会变成两批工作。
        #
        # 不做 ``canonical_host`` 那一层：这里收的是**已经过判定的**域名
        # （回执那一侧的校验器与资产那一侧的都过过），在这一层再折一次会造出
        # 第二个判定点，而两个判定点漂移的那天，同一句话会有两个答案。
        folded = tuple(sorted({item.strip().lower() for item in self.domains if item.strip()}))
        object.__setattr__(self, "domains", folded)

    def payload(self) -> dict[str, object]:
        """规范形状。**两个适配器共用这一份**（理由同 ``findings_payload``）：
        两处各序列化一遍，漂移的那天同一份身份在库里会有两个形状。"""
        return {
            "account_id": self.account_id,
            "organisation": self.organisation,
            "domain": self.domain,
            "country": self.country,
            "source_ref": self.source_ref,
            "input_sha256": self.input_sha256,
            "domains": list(self.domains),
            "domain_verdict": self.domain_verdict.value,
        }

    def as_clues(self) -> dict[str, str]:
        """回执里给 worker 看的那一份：**只含非空线索**。

        空字段不进回执。一个 ``"domain": ""`` 会被读成「确认过没有官网」，而
        事实是「输入没给」—— 两者对身份发现的动作完全相反（前者让人放弃这条线，
        后者让人去找）。所以缺的线索是**缺席**，不是空串。
        """
        clues = {
            "account_id": self.account_id,
            "organisation": self.organisation,
            "domain": self.domain,
            "country": self.country or "",
        }
        return {name: value for name, value in clues.items() if value}


def dossier_from_payload(payload: object) -> SubjectDossier:
    """``payload`` 的逆。形状不对时**抛** —— 不猜、不补默认值。

    ## 为什么每一格都必须在，而且必须是字符串

    ``payload.get(name, "")`` 那种兜底看起来更宽容，代价是一格 ``null`` 会被
    ``str()`` 变成字符串 ``"None"`` —— 一个名叫 ``None`` 的组织。它非空、也不
    等于 ``account_id``，于是 :func:`identity_refusal` 会**放行**它，worker 拿着
    这个名字去检索。一条把缺席读成内容的兜底，比一次拒绝坏得多：拒绝看得见，
    这个看不见。

    唯一允许为 ``null`` 的是 ``country``（``None`` 是「输入没说」，见字段说明）。

    ## 为什么 ``domains`` / ``domain_verdict`` 那两格**允许缺席**

    上一条纪律的理由是「把缺席读成内容」：一个兜底出来的 ``""`` 会在下游长成一句
    结论。``domains`` 与 ``domain_verdict`` 的兜底方向相反 —— 它们缺席时**没有**
    任何域名知识，读到的是「没证过」，而「没证过」买到的是**不做判定**，
    不是默认通过（见 :class:`SubjectDossier` 的字段说明）。所以这一条兜底不会把
    缺席读成内容，它把缺席读成**缺席的正式写法**。

    这一格是**后加的**，而盘上已经有按旧形状写下的 ``work_item_snapshot``：那些
    作业名下的身份是在「域名只有一格」的代码里冻住的。拒绝读它们会把一件**已经
    受理**的工作变成读不出来的行，而它当时是合法的。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"主体身份的载荷应该是一个对象，拿到 {type(payload).__name__}。")
    for name in _TEXT_FIELDS:
        if name not in payload:
            raise ValueError(
                f"主体身份的载荷里没有 {name} 这一格：{payload!r}。\n"
                "这个载荷是本仓自己写下的（``SubjectDossier.payload``），所以缺一格"
                "意味着那一行不是这份代码写的 —— 补一个默认值会让它看起来像是。"
            )
        if not isinstance(payload[name], str):
            raise ValueError(
                f"主体身份的载荷里 {name} 应该是字符串，"
                f"拿到 {type(payload[name]).__name__}：{payload[name]!r}。"
            )
    country = payload["country"]
    if country is not None and not isinstance(country, str):
        raise ValueError(
            f"country 应该是字符串或 null，拿到 {type(country).__name__}：{country!r}。"
        )
    domains = _domains_from_payload(payload)
    return SubjectDossier(
        account_id=payload["account_id"],
        organisation=payload["organisation"],
        domain=payload["domain"],
        country=country,
        source_ref=payload["source_ref"],
        input_sha256=payload["input_sha256"],
        domains=domains,
        domain_verdict=_verdict_from_payload(payload),
    )


def _domains_from_payload(payload: dict[str, object]) -> tuple[str, ...]:
    """``domains`` 那一格。缺席 = 空集合；**在而不对** = 抛。"""
    if "domains" not in payload:
        return ()
    value = payload["domains"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(
            f"主体身份的载荷里 domains 应该是字符串列表，拿到 {value!r}。\n"
            "它写的时候是 ``list(self.domains)``，所以不是这个形状意味着那一行"
            "不是这份代码写的。"
        )
    return tuple(value)


def _verdict_from_payload(payload: dict[str, object]) -> DomainVerdict:
    """``domain_verdict`` 那一格。缺席 = ``unknown``；**在而不认得** = 抛。

    认不出的取值**必须抛**，不能退回 ``unknown``：一个写坏了的判定值会被读成
    「没跑过判定」，而这两句话的处置相反（前者要修，后者是正常的）。三值枚举的
    全部价值就在于这两个字面上的不同。
    """
    if "domain_verdict" not in payload:
        return DomainVerdict.UNKNOWN
    value = payload["domain_verdict"]
    if not isinstance(value, str):
        raise ValueError(f"主体身份的载荷里 domain_verdict 应该是字符串，拿到 {value!r}。")
    try:
        return DomainVerdict(value)
    except ValueError as exc:
        known = [member.value for member in DomainVerdict]
        raise ValueError(
            f"主体身份的载荷里 domain_verdict 是 {value!r}，不在 {known} 里。\n"
            "退回 unknown 会把一个写坏了的判定读成「没跑过判定」—— 前者要修，"
            "后者是正常的，而报告上两句长得一样。"
        ) from exc


def identity_refusal(dossier: SubjectDossier) -> str:
    """这份身份够不够开工。返回空串 = 够；否则是一条说得出的理由。

    在**任何一次付费请求之前**问这个问题。它是本模块存在的理由：那次 ``kent-003``
    的运行真的花了 9 次 Serper 查询去搜一个内部 ID，而正确答案是**一次都不该花**。

    ## 判据

    至少要有一样**能拿去做身份发现的依据**：

    * ``organisation`` 是一个名字 —— 非空，且**不等于** ``account_id``；
    * 或者 ``domain`` 非空。

    两条都不成立才拒绝。``domain`` 不知道**不必**拒绝（先搜名字、再确认官网是
    一条完整的路径）；只有 slug 才是拒绝的。

    ## 判据的边界

    它**不**判断名字是真是假，也**不**判断名字够不够独特 —— 「Example School One」
    与「Example School Two」在这条判据下同样通过，而后者需要消歧。要消歧就得有
    关于这个市场的知识，而 ``marketops/`` 里不许有那种东西
    （``tests/test_no_hardcoded_market_knowledge.py``）。所以这条判据答的是一个
    **结构**问题：**包里有没有一个人类可读的指称对象**。消歧是 worker 的活，
    它拿到的正是做那件事所需要的材料。

    唯一一条内容判据是 ``organisation != account_id``：它挡的正是被报上来的那个
    情形 —— 清单里把内部 id 填进了名称那一格。这条比较去空白、不分大小写，
    因为 ``KENT-003`` 与 ``kent-003`` 是同一个内部 id 的两种写法。
    """
    if dossier.organisation.strip() and (
        dossier.organisation.strip().lower() != dossier.account_id.strip().lower()
    ):
        return ""
    if dossier.domain.strip():
        return ""
    return (
        f"主体 {dossier.account_id} 没有可用来做身份发现的依据："
        "organisation 那一格是空的（或只是把内部 id 又写了一遍），domain 也是空的。\n"
        "一份只有内部 slug 的任务包发出去，worker 唯一能做的是拿这个 slug 去检索 —— "
        "那会把预算花在同名物上，然后交回一份「什么都没找到」的结算；而它在报告里与"
        "「这个账号真的没有公开信息」不可区分。所以这里**一次检索、一次模型调用都不发**，"
        f"直接拒绝。\n请在这份清单（{dossier.source_ref}）里给 {dossier.account_id} 填上真实名称"
        "（域名可以没有）。"
    )


__all__ = [
    "SUBJECT_KIND_ACCOUNT",
    "SubjectDossier",
    "dossier_from_payload",
    "identity_refusal",
]
