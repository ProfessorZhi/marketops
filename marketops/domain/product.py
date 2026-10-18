"""产品目录：**能被批准、能被引用、能被查缺**的四型，以及它们之间的那条边界。

权威是 `docs/PRODUCT_LINE_AND_SCENE_MODEL.md`（下文记作 PSM）。B01 的关闭条件在
`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:96-104`，逐字要求「每项必须有生产者、
存储/事务、实际调用者、MCP 读取/控制、故障回执及关闭测试」。**本模块只落第一半：
形状**。没有存储、没有调用者、没有 MCP 面 —— 那些各是后面几片的事，而在此之前
先要把「什么东西可以被批准」这件事在类型上定下来。

## 这一族与池子里那些型的分工

PSM §2 那张表把两个词各自「不等于」什么写死了：

* ``Product``/``ProductVersion`` **不等于**一个 ``VenueAsset`` —— 前者是「我们卖
  什么」，后者是「那个地方有什么」。一张天然草坪 SOP 是产品侧的资料，不是场地资产。
* ``Offer``/``OfferVersion`` **不等于**一个 ``Scene`` —— 前者是「按什么条件可成交」，
  后者是「这次为哪个客户、哪个场地、在哪个市场做，以及批准了什么」。一次 Offer
  可以被多个 Scene 引用；一个 Scene 只绑一份 EffectiveScene 编译结果。

所以这一族里**没有** account / venue / scene 字段。它们之间的连接是后面几片的
引用，住在那几片的类型里，不住在这里。

## 价格只在**一个**地方，而且那一格不是数字

PSM `:19` 逐字「价格可缺省而**不可编造**」，`:156` 逐字「无价格可照样研究；草稿
不得生成猜价或规格」。所以价格在 :class:`OfferVersion` 上**可缺省**（``None``），
但它的类型是 :class:`QuotedPrice` 而不是数字 —— 一条没有出处、没有计价基准、
没有时刻的报价与一条真的报价在报告上长得完全一样，而前者正是「猜价」。

这一格做成构造而不是纪律：全仓**唯一**能放价格的地方是它，而它造不出来一个
没有 ``source_ref`` 的报价。金额用整数的最小货币单位（与本仓
``domain/budget.py`` 的 ``units: int`` 同一条理由）。

> 这一节是**改过一次**的：本片的第一稿把价格格整个拿掉了（读作「缺席即不可
> 编造」），落地前对照 PSM `:19` 时发现那让「价格可缺省」这个**被写下来的状态**
> 在类型上表达不出来，于是走回去补齐。差量记在
> `docs/B01_A_PRODUCT_CATALOG_2026-09-28.md`。

## 两条缺席是故意的，不是还没写

1. **这一族里没有原始字节格。** B01（`:100`）逐字「原始资料进受管 artifact store；
   Git 仅获准索引/schema/规则，不上传私有手册/联系人」。所以
   :class:`SourceArtifactRef` 只有定位与**摘要** —— 判据按字段名的词元查，且只放行
   名字自己声明是摘要的那一种写法。
2. **``ProductLine`` 上没有布尔态。** PSM §4.1 逐字「「是否正在经营」由已批准
   launch/active Scene **投影**，不维护互相打架的布尔值」。

## 这一片不做的判定（写下来，免得读成「已经想过并决定不做」以外的东西）

* 版本链的单调性（「第 N 版必须紧接第 N-1 版」）要读盘才知道，是存储片的事。
* 「一个已批准的产品版本被两个 Scene 共用」是 b01-g；这里是它的前提。
* 这一族**不落 ``contracts/``**：那 15 个文件实测不含 ``ApprovedClaim`` /
  ``TemplateFamily`` / ``WritingPolicy`` / ``SceneVersion``，所以这一轮加的类型
  不动任何已提交契约。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel
from .util import (
    check_canonical_id,
    check_market_country,
    check_market_language,
    to_utc,
)

__all__ = [
    "ClaimRef",
    "OfferVersion",
    "ProductLine",
    "ProductRef",
    "ProductStatus",
    "ProductVersion",
    "QuotedPrice",
    "RegionVariant",
    "SourceArtifactRef",
]

#: 小写十六进制 sha256。全仓的证据链（`Evidence` / `SceneVersion` / 页面镜像）用
#: 的是同一个长度，因为它们是同一个量具 —— 一个 `[:16]` 的前缀在这里读起来像
#: 「同一个东西」，而它没法与任何一条别的记录对上。
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

#: 货币：ISO-4217 alpha-3，**大写**。与 ``util.check_market_country`` 同一条理由 ——
#: 它是拒绝而不是 ``upper()``：一个自造的单位名会进报价的账。
_CURRENCY = re.compile(r"[A-Z]{3}")


class ProductStatus(str, Enum):
    """PSM §4.1 逐字「生命周期可为 ``draft -> ready -> retired``」。

    闭集而不是自由文本：这三格各自挂着**一个不同的允许动作** —— ``draft`` 随便改、
    ``ready`` 才能被 Offer 引用、``retired`` 停止创建新 Offer 但不删历史。一个
    第四种写法的存在，等于那三个动作里至少有一个没有执行器。
    """

    DRAFT = "draft"
    READY = "ready"
    RETIRED = "retired"


class SourceArtifactRef(StrictModel):
    """一条原始资料的**索引**。字节不住在这里（见模块 docstring 第 2 条缺席）。

    它是「这个能力从哪来」这条链的地面：``ProductVersion`` 的能力、``OfferVersion``
    的方案、``ApprovedClaim`` 的说法，最后都要落到某一条这个记录上。所以它自己
    必须是可核对的两件事 —— **在哪**（``locator``）与**是哪一份**（``content_sha256``）。
    只有一个 locator 的引用会在来源被更新之后仍然指向旧内容，而报告上看不出来。
    """

    artifact_id: str
    locator: str
    content_sha256: str
    fetched_at: datetime
    media_type: str = ""
    note: str = ""

    @field_validator("artifact_id")
    @classmethod
    def _artifact_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "artifact_id")

    @field_validator("locator")
    @classmethod
    def _locator_is_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("locator 不能为空 —— 一条没有出处的资料不是资料")
        return value

    @field_validator("content_sha256")
    @classmethod
    def _digest_is_a_whole_sha256(cls, value: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError(
                "content_sha256 必须是 64 位小写十六进制。"
                "截断过的前缀读起来像摘要，却与任何一条别的记录都对不上 —— "
                "而「对不上」在下游表现为「这是一份新的资料」。"
            )
        return value

    @field_validator("fetched_at")
    @classmethod
    def _fetched_at_is_aware(cls, value: datetime) -> datetime:
        return to_utc(value)


class RegionVariant(StrictModel):
    """一个地区的**能力收窄**。PSM §6 逐字「地区版不能偷偷继承无效 claim」。

    它只能从产品主档里**拿掉**能力，不能加上 —— 那半句的判据在
    :meth:`ProductVersion._region_variants_narrow_only` 上，因为它要读到主档。

    为什么不带 ``limits``：限制的**增加**是一种扩张（`limits` 越少越能做）。一个
    既能加能力又能加限制的变体，不是一个方向的收窄，而是一个可以与主档互相打架
    的第二份主档。
    """

    market: str
    capabilities: tuple[str, ...] = ()
    note: str = ""

    @field_validator("market")
    @classmethod
    def _market_is_a_country_code(cls, value: str) -> str:
        return check_market_country(value, "market")


class ProductRef(StrictModel):
    """对**恰好那一版**产品版本的引用：``(product_id, version)``。

    只写 ``product_id`` 再去解释一次「哪一版」，是把那次解释留给每个调用点各做
    一遍 —— 而两次部署之间，「第 2 版」可以是两份不同的东西。
    """

    product_id: str
    version: int = Field(ge=1)

    @field_validator("product_id")
    @classmethod
    def _product_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "product_id")


class ClaimRef(StrictModel):
    """对**恰好那一版** approved claim 的引用。

    与 :class:`ProductRef` 同一形状、同一理由：一条 claim 的 rev 2 可以把 rev 1
    的说法收窄甚至撤回，所以「哪一版」必须是引用的一部分。
    """

    claim_id: str
    revision: int = Field(ge=1)

    @field_validator("claim_id")
    @classmethod
    def _claim_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "claim_id")


class QuotedPrice(StrictModel):
    """一条**有出处**的报价。

    PSM `:19` 逐字「价格可缺省而**不可编造**」，`:156` 逐字「无价格可照样研究；
    草稿不得生成猜价或规格」。这两句要能被执行，价格就不能是一个裸数字 ——
    一个裸数字没有出处、没有计价基准、没有时刻，而它在报告上读起来与一条真的报价
    完全一样。所以「不可编造」在这里是**构造**：这一型没有 ``source_ref`` 与
    ``quoted_at`` 就造不出来（判据在 ``tests/test_domain_product.py``）。

    **金额是整数的最小货币单位，不是浮点。** 与本仓 ``domain/budget.py`` 的
    ``units: int`` / ``ceiling: int`` 同一条理由：用浮点算钱的地方会在某一次加法里
    造出一个没有任何人批准过的数，而那个数看起来完全正常。

    ``basis``（计价基准）是必填的：``units`` 已经在产品版本上说了是 litre，但
    「每升 12.50」与「每 25 升桶 300.00」是两个不同的报价，缺了这一格的价格
    会被读成前者。
    """

    currency: str
    amount_minor: int = Field(ge=0)
    basis: str
    #: 这条报价从哪来。**必填** —— 它就是「不可编造」这一句的执行器。
    source_ref: str
    quoted_at: datetime
    note: str = ""

    @field_validator("currency")
    @classmethod
    def _currency_is_iso_4217(cls, value: str) -> str:
        if not _CURRENCY.fullmatch(value):
            raise ValueError(
                "currency 必须是 ISO-4217 的三位大写字母码（收到 "
                f"{value!r}）。一个自造的单位名会在对账时变成一个没人认得的量。"
            )
        return value

    @field_validator("basis", "source_ref")
    @classmethod
    def _non_empty(cls, value: str, info: ValidationInfo) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{info.field_name} 必填且不得带首尾空白")
        return value

    @field_validator("quoted_at")
    @classmethod
    def _quoted_at_is_aware(cls, value: datetime) -> datetime:
        return to_utc(value)


class ProductVersion(StrictModel):
    """产品的一个版本：**能力 / 限制 / 区域变体 / 未知项 / 出处 / 负责人 / 状态**。

    PSM §4.1 的最小主档，逐格落在字段上。两处判定值得单说：

    * ``unknowns`` 与 ``capabilities`` 是**两格**，不是一个字段的两种值。「不知道
      能不能做」与「不能做」在报告上是同一格，而它们各自要的是不同的下一步 ——
      前者要一份资料，后者要一次改设计。
    * ``status == READY`` 要求 ``source_refs`` 非空**且**有人签名。B01（`:100`）
      逐字「新型号无核实资料只可 draft」；「已核对」这件事要有核对者，否则它只是
      一个更自信的 ``draft``。
    """

    product_id: str
    version: int = Field(ge=1)
    display_name: str
    capabilities: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    units: str = ""
    region_variants: tuple[RegionVariant, ...] = ()
    source_refs: tuple[SourceArtifactRef, ...] = ()
    unknowns: tuple[str, ...] = ()
    owner: str
    status: ProductStatus = ProductStatus.DRAFT
    approved_by: str = ""
    approved_at: datetime | None = None
    supersedes: int | None = None
    note: str = ""

    @field_validator("product_id")
    @classmethod
    def _product_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "product_id")

    @field_validator("owner")
    @classmethod
    def _owner_is_a_principal(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("owner 不能为空 —— 没有负责人的主档没人能去问")
        return value

    @field_validator("approved_at")
    @classmethod
    def _approved_at_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return to_utc(value)

    @model_validator(mode="after")
    def _region_variants_narrow_only(self) -> "ProductVersion":
        """PSM §6 逐字「地区版不能偷偷继承无效 claim」。

        判据是**子集**：变体列的每一条能力都要在主档里出现。反过来那一半 ——
        主档列了而变体没列 —— 是**合法**的，那正是「这个地区做不到」的写法。
        """
        allowed = set(self.capabilities)
        seen: set[str] = set()
        for variant in self.region_variants:
            if variant.market in seen:
                raise ValueError(
                    f"市场 {variant.market} 出现了两个区域变体 —— 那一格于是有两个"
                    "互相打架的答案，而两个都读起来像「该地区的实际情况」。"
                )
            seen.add(variant.market)
            extra = [cap for cap in variant.capabilities if cap not in allowed]
            if extra:
                raise ValueError(
                    f"区域变体 {variant.market} 列出了主档没有的能力 {extra!r}。"
                    "地区版只能收窄 —— 一个能扩张的变体会成为「某个地区做得到」"
                    "这个说法的唯一出处，而它没有出处。"
                )
        return self

    @model_validator(mode="after")
    def _ready_means_verified(self) -> "ProductVersion":
        """B01（`:100`）逐字「新型号无核实资料只可 draft」。

        ``draft`` 不受这条约束，那一半同样是判据的一部分：两条一起，这一族才
        既不许自称已核对，也不逼着人「先填点什么」才能登记一个新型号。
        """
        if self.status is not ProductStatus.READY:
            return self
        if not self.source_refs:
            raise ValueError(
                "source_refs 不能为空：ready 的产品版本必须有核实资料 —— "
                "没有核实资料只可 draft。「已批准的能力」与「某个人说的能力」"
                "不能是盘上的同一格。"
            )
        if not self.approved_by.strip() or self.approved_at is None:
            raise ValueError(
                "approved_by / approved_at 不能为空：ready 的产品版本要有核对者 —— "
                "「已核对」这件事没有核对者，就只是一个更自信的 draft。"
            )
        return self

    @property
    def accepts_new_offers(self) -> bool:
        """PSM §4.1 逐字「产品归档停止创建新的 Offer，不删除历史结果」。

        「不删除历史结果」那一半不在这个属性上 —— 它由「引用是
        :class:`ProductRef`、按 ``(product_id, version)`` 定位」保证：归档不改写
        任何已存在的引用。
        """
        return self.status is not ProductStatus.RETIRED


class OfferVersion(StrictModel):
    """一个可成交方案的版本：**产品版本组合 / 用途 / 可用市场 / 允许 claims**。

    PSM §6 列的五件事里，「价格」被**有意**排除（模块 docstring 第 1 条缺席）。
    另四件逐格在此：``product_refs`` / ``purpose`` / ``markets`` / ``approved_claim_refs``。

    ``status == READY`` 要求产品组合与可用市场都非空。两条都不是形式要求：

    * 零个产品版本的「已批准方案」，是「这次到底卖什么」没有答案；
    * 零个可用市场的「已批准方案」，读起来是「哪儿都能用」—— 那是**一次授权**，
      而不是一个缺省值。一条没有写下来的授权会在别的市场里被当成既有事实。
    """

    offer_id: str
    version: int = Field(ge=1)
    workspace: str
    name: str
    purpose: str
    product_refs: tuple[ProductRef, ...] = ()
    markets: tuple[str, ...] = ()
    locales: tuple[str, ...] = ()
    approved_claim_refs: tuple[ClaimRef, ...] = ()
    #: PSM `:19` 逐字「价格可缺省而不可编造」。``None`` = 还没报价，而**照样可以
    #: 研究**（`:156` 逐字「无价格可照样研究」）。这一格是全仓**唯一**能放价格的地方，
    #: 而它的类型是 :class:`QuotedPrice` 而不是数字 —— 一个裸 ``price: str`` 格存在
    #: 的那一天，「不可编造」就只剩下自觉了。
    price: QuotedPrice | None = None
    owner: str
    status: ProductStatus = ProductStatus.DRAFT
    approved_by: str = ""
    approved_at: datetime | None = None
    supersedes: int | None = None
    note: str = ""

    @field_validator("offer_id")
    @classmethod
    def _offer_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "offer_id")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("owner")
    @classmethod
    def _owner_is_a_principal(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("owner 不能为空 —— 没有负责人的方案没人能去问")
        return value

    @field_validator("markets")
    @classmethod
    def _markets_are_country_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(check_market_country(item, "markets") for item in value)

    @field_validator("locales")
    @classmethod
    def _locales_are_language_subtags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """语言不是国家（``util.check_market_language`` 逐字）。

        所以这一格收 ``en`` 而不是 ``en-GB`` —— 地区部分属于 :class:`RegionVariant`
        与 :attr:`markets` 那一格。收下 ``en-GB`` 就等于把「英国 = 英语」焊进类型，
        于是「法国、英文」这种真实组合在这一族里表达不出来。
        """
        return tuple(check_market_language(item, "locales") for item in value)

    @field_validator("approved_at")
    @classmethod
    def _approved_at_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return to_utc(value)

    @model_validator(mode="after")
    def _ready_means_approved(self) -> "OfferVersion":
        if self.status is not ProductStatus.READY:
            return self
        if not self.product_refs:
            raise ValueError(
                "product_refs 不能为空：ready 的 Offer 必须至少引用一个产品版本 —— "
                "一个指着零个产品的「已批准方案」是「这次到底卖什么」没有答案。"
            )
        if not self.markets:
            raise ValueError(
                "markets 不能为空：ready 的 Offer 必须写明可用市场 —— 空市场读起来"
                "是「哪儿都能用」，而那是一次授权，不是一个缺省值。"
            )
        if not self.approved_by.strip() or self.approved_at is None:
            raise ValueError(
                "approved_by / approved_at 不能为空：ready 的 Offer 要有批准者 —— "
                "没有批准者的「已批准」是一个自称。"
            )
        return self


class ProductLine(StrictModel):
    """PSM §4.1 的最小主档：**可选分组，零行为**。

    `:110` 逐字「ProductLine 不是运行边界」。它是给人看的分组 —— 把几个产品归到
    一个「草坪养护」下面 —— 而**不是**任何判定的主语：产品与 Offer 都不引用它
    （判据在 ``tests/test_domain_product.py::test_a_product_line_is_not_a_running_boundary``）。

    所以这一型里没有布尔格：「是否正在经营」由已批准 launch / active Scene
    **投影**出来，不在这里维护第二份互相打架的读数（PSM §4.1 逐字）。它也没有
    自己的 ``scene``/``account`` 字段 —— 那会让「零行为」变成一句空话。
    """

    line_id: str
    workspace: str
    name: str
    customer_problem: str = ""
    capability_family: str = ""
    owner: str
    status: ProductStatus = ProductStatus.DRAFT
    product_refs: tuple[ProductRef, ...] = ()
    approved_material_refs: tuple[str, ...] = ()

    @field_validator("line_id")
    @classmethod
    def _line_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "line_id")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("approved_material_refs")
    @classmethod
    def _material_refs_are_canonical(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(
            check_canonical_id(item, "approved_material_refs") for item in value
        )
