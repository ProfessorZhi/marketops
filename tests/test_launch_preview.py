"""b01-d 的读法判据：启动预览读的是**盘上那一版已经写下的声明**（B01 关闭条件 5）。

权威句 `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104` 第 5 项逐字八个字：

    启动授权前零外部调用

这条关闭条件**没有自己的正文**，它的展开在 `:102` 那条启动用例里逐字：
「启动用例先 preview……**有批准才持久受理** Scene/Workstream 及 Job」。两句话合起来
划出这一层的范围：**被预览的那个动作还没发生**，所以这一层读的是盘上已有的东西。

`docs/PRODUCT_LINE_AND_SCENE_MODEL.md:122`（§4.2 第 3 步）逐字要预览的八项，而
`:229` 逐字给出它的形状：

    启动前返回结构化 missing/invalid/awaiting_approval，不让模型读一段 traceback 猜。

## 这一份判的是**读法**，不是构造成

构造（「零外部调用」）那一条有自己的一份判据：`tests/test_launch_preview_is_offline.py`
（AST 量具 + 签名）。这一份判的是另一半：**给定盘上这些东西，这份读数是不是一个能照着
做事的东西**。两边分开写，是因为它们坏起来是两件事——一层拿不到端口却什么也没判对，
与一层判得都对却顺手调了一次检索，在报告上都只显示「预览成功」。

## 三张清单各有各的下一步，所以它们各有一族判据

* ``missing`` ⇒ 去补 / 去改那一格；
* ``invalid`` ⇒ 先修引用关系（指着不存在的东西，补多少资料都白补）；
* ``awaiting_approval`` ⇒ 人等签字（或标定）。

三张**不互斥**：一份又缺东西又没人签的声明两张都报。`next_action` 是一个**属性**，
它的五档与优先级一并在这里判死——一个由构造者填的那一格会在某次改动之后与三张清单
分家，而报告上它必须说的是同一件事（`RunPreview.outcome` 对同一件事论证过一次）。

## `at` 是承重的，所以它有一对判据

naive 时刻**拒**，以及**同一份盘上状态在两个时刻读出两个答案**。后一条是前一条的反面：
一个把 `at` 收下却不用的实现，在别的判据下全绿。

## 这一份不重测的东西

* 域层那十二条 `SceneVersion` 校验器 —— `tests/test_domain_scene.py`；
* b01-c 那一层的三张清单与 `applies_to` 的四轴 —— `tests/test_product_readiness.py`
  （这一层**逐字嵌着**它的读数，见「不折平」那一节，所以它自己的判定在这里只判**转达**）；
* 存储 —— `tests/test_product_catalog_contract.py` 与 `tests/test_scene_store_contract.py`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryOutreachStore, InMemoryProductCatalog
from marketops.application.launch_preview import (
    LAUNCH_NOT_REPORTED,
    LaunchNextAction,
    LaunchPreview,
    launch_preview,
    launch_subject,
)
from marketops.application.product_readiness import ReadinessGap, SubjectKind
from marketops.application.scene_receipt import version_receipt
from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import (
    ApprovedClaim,
    OutreachPolicyDecl,
    StageTouches,
    StageWait,
)
from marketops.domain.product import (
    ClaimRef,
    OfferVersion,
    ProductRef,
    ProductStatus,
    ProductVersion,
    QuotedPrice,
    SourceArtifactRef,
)
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
#: 一份读数取在**晚于** ``T0`` 的一刻 —— 用来证明时刻是承重的。
LATER = T0 + timedelta(days=400)

SCENE_ID = "probe-scene"
WORKSPACE = "probe-workspace"
#: 另一个工作区。它只出现在**方案**上（``OfferVersion`` 有 ``workspace`` 那一格，
#: ``ProductVersion`` 没有）—— 那是一处刻意的不对称，见 ``_offer`` 的注释。
OTHER_WORKSPACE = "other-workspace"
PRODUCT_ID = "probe-product"
OFFER_ID = "probe-offer"
CLAIM_ID = "probe-claim"


# ----------------------------------------------------------------------
# 夹具：一份最小的盘上状态
# ----------------------------------------------------------------------


def _scene(version: int = 1, **overrides: object) -> SceneVersion:
    """盘上那一版声明。逐格取自 ``tests/test_run_preview.py::_scene`` 的形状。

    ``supersedes`` 按版本号推（第 2 版取代第 1 版）—— 域层的 ``_supersedes_is_the_
    previous_one`` 要求「第 2 版必须写明它取代哪一版」，所以不推的话
    ``_scene(version=2)`` 会在一处与用例无关的地方抛。要造断链的那一处显式覆盖它。
    """
    fields: dict[str, object] = {
        "scene_id": SCENE_ID,
        "workspace": WORKSPACE,
        "version": version,
        "region": ("GB-England-Devon",),
        "scenario": "sports-surface-maintenance",
        "product": "acme-natural-grass",
        "admissions": ("sports-club",),
        "exclusions": ("golf-course",),
        "role_policy": "role-policy-v1",
        "source_plan": ("mother-pool-import",),
        "budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "refresh": RefreshPlan.uncalibrated_default(),
        "outreach": OutreachPolicyDecl.uncalibrated_default(),
        "profile_id": WORKSPACE,
        "profile_version": "2026-09-01",
        "created_at": T0,
        "supersedes": version - 1 if version > 1 else None,
    }
    fields.update(overrides)
    return SceneVersion(**fields)  # type: ignore[arg-type]


def _record(
    *,
    state: SceneState = SceneState.ACTIVE,
    latest_version: int = 1,
    effective_version: int | None = None,
    pause_reason: PauseReason | None = None,
) -> SceneRecord:
    """那一版所在的**当前状态**。

    ``effective_version`` 的缺省按状态推：``DRAFT`` 不许有生效版（域层校验器逐字
    「一个从未被批准的场景若带着 ``effective_version``，派发侧就会认为它已经可以
    开工」），其余状态必须有。推它而不是让每个用例各写一遍，是为了让「这一版生不生效」
    在用例里是一个**显式的覆盖**，而不是一个跟着状态漂的副作用。
    """
    if effective_version is None and state is not SceneState.DRAFT:
        effective_version = latest_version
    fields: dict[str, object] = {
        "scene_id": SCENE_ID,
        "workspace": WORKSPACE,
        "state": state,
        "latest_version": latest_version,
        "effective_version": effective_version,
        "created_at": T0,
        "updated_at": T0,
        "pause_reason": pause_reason,
    }
    return SceneRecord(**fields)  # type: ignore[arg-type]


def _material(artifact_id: str = "probe-spec") -> SourceArtifactRef:
    return SourceArtifactRef(
        artifact_id=artifact_id,
        locator="artifact-store://probe/spec.pdf",
        content_sha256="a" * 64,
        fetched_at=T0,
        media_type="application/pdf",
    )


def _product(
    version: int = 1,
    *,
    status: ProductStatus = ProductStatus.READY,
    **overrides: object,
) -> ProductVersion:
    """一份**各格都填满**的产品版本。

    填满是刻意的（`tests/test_product_readiness.py::_version` 对同一件事逐字论证过）：
    一张少了几格的样板会让「这一条判据判的是哪一格」看不出来。下面那些「让内层出缺口」
    的用例是**显式**把某一格掏空的，所以「掏空」在文件里是一个看得见的动作。
    """
    fields: dict[str, object] = {
        "product_id": PRODUCT_ID,
        "version": version,
        "display_name": "探针产品",
        "capabilities": ("surface-prep",),
        "limits": ("indoor-use-only",),
        "units": "litre",
        "source_refs": (_material(),),
        "unknowns": (),
        "owner": "probe-owner",
        "status": status,
    }
    if status is not ProductStatus.DRAFT:
        fields["approved_by"] = "probe-approver"
        fields["approved_at"] = T0
    fields.update(overrides)
    return ProductVersion(**fields)  # type: ignore[arg-type]


def _offer(
    version: int = 1,
    *,
    workspace: str = WORKSPACE,
    status: ProductStatus = ProductStatus.READY,
    **overrides: object,
) -> OfferVersion:
    """一份各格都填满的方案。

    ``workspace`` 是**方案独有**的一格（``ProductVersion`` 上没有它）。b01-c 为这一格
    写了一对判据（方案那一版属于一个工作区，而产品版本不属于），这一层判的是它的**转达**：
    引了别人工作区的那一版时进 ``invalid``，且**不嵌进** ``cited``。
    """
    fields: dict[str, object] = {
        "offer_id": OFFER_ID,
        "version": version,
        "workspace": workspace,
        "name": "探针方案",
        "purpose": "launch",
        "product_refs": (ProductRef(product_id=PRODUCT_ID, version=1),),
        "markets": ("GB",),
        "locales": ("en",),
        "approved_claim_refs": (ClaimRef(claim_id=CLAIM_ID, revision=1),),
        "price": QuotedPrice(
            currency="GBP",
            amount_minor=1250,
            basis="per litre",
            source_ref="probe-quote-2026",
            quoted_at=T0,
        ),
        "owner": "probe-owner",
        "status": status,
    }
    if status is not ProductStatus.DRAFT:
        fields["approved_by"] = "probe-approver"
        fields["approved_at"] = T0
    fields.update(overrides)
    return OfferVersion(**fields)  # type: ignore[arg-type]


def _claim(revision: int = 1, *, expires_at: datetime | None = None) -> ApprovedClaim:
    return ApprovedClaim(
        claim_id=CLAIM_ID,
        revision=revision,
        text="这一版的能力经核对",
        source_ref="probe-spec",
        approved_by="probe-approver",
        approved_at=T0,
        expires_at=expires_at,
    )


class _Board:
    """一份盘上状态：产品目录 + 已批准说法，外加一个「读一次预览」的入口。"""

    def __init__(self) -> None:
        self.catalog = InMemoryProductCatalog()
        self.outreach = InMemoryOutreachStore()

    # --- 盘上放东西（全部走端口，这一份不重测存储） --------------------

    def put_product(self, version: int = 1, **overrides: object) -> ProductVersion:
        row = _product(version, **overrides)
        self.catalog.put_version(row)
        return row

    def put_offer(self, version: int = 1, **overrides: object) -> OfferVersion:
        row = _offer(version, **overrides)
        self.catalog.put_offer(row)
        return row

    def put_claim(self, claim: ApprovedClaim) -> None:
        self.outreach.put_claim(claim)

    def seed_ready(self) -> None:
        """一份「什么都不缺」的盘面：产品、方案、说法三样都在。"""
        self.put_product()
        self.put_offer()
        self.put_claim(_claim())

    # --- 读法 -----------------------------------------------------------

    def preview(
        self,
        *,
        scene: SceneVersion | None = None,
        record: SceneRecord | None = None,
        workspace: str = WORKSPACE,
        at: datetime = T0,
        product_id: str | None = PRODUCT_ID,
        product_version: int | None = 1,
        offer_id: str | None = OFFER_ID,
        offer_version: int | None = 1,
    ) -> LaunchPreview:
        return launch_preview(
            scene=scene if scene is not None else _scene(),
            record=record if record is not None else _record(),
            workspace=workspace,
            at=at,
            catalog=self.catalog,
            claims=self.outreach,
            product_id=product_id,
            product_version=product_version,
            offer_id=offer_id,
            offer_version=offer_version,
        )


@pytest.fixture
def board() -> _Board:
    return _Board()


def _fields(gaps: tuple[ReadinessGap, ...]) -> set[str]:
    return {gap.field for gap in gaps}


def _find(gaps: tuple[ReadinessGap, ...], field: str) -> ReadinessGap:
    hits = [gap for gap in gaps if gap.field == field]
    assert len(hits) == 1, f"{field} 那一格上应该有且只有一条，实际 {hits!r}"
    return hits[0]


def _items(preview: LaunchPreview) -> set[str]:
    return {item.item for item in preview.not_reported}


def _cited_subjects(preview: LaunchPreview) -> list[str]:
    return [reading.subject for reading in preview.cited]


def _calibrated_outreach() -> OutreachPolicyDecl:
    """把 ``uncalibrated_default()`` 的每一格**标定**下来（数字照抄，只翻那一面）。

    标定与否在 ``calibrated`` 上，数字是不是同一组是另一件事 —— ``RefreshPlan`` 的
    ``policy_version`` 对同一条分界写过一段（「一个说『是这三组数』，一个说『有人认过
    这三组数』」）。所以这个助手只翻那一面，不编新数字。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    return OutreachPolicyDecl(
        max_per_wave=default.max_per_wave,
        max_per_recipient=default.max_per_recipient,
        waits=tuple(
            StageWait(stage=row.stage, after_hours=row.after_hours, calibrated=True)
            for row in default.waits
        ),
        prior_touches=tuple(
            StageTouches(stage=row.stage, allowed_prior=row.allowed_prior, calibrated=True)
            for row in default.prior_touches
        ),
    )


# ----------------------------------------------------------------------
# 一、主语与四条守卫
# ----------------------------------------------------------------------


def test_the_subject_is_generated_by_one_function(board: _Board) -> None:
    """主语由 :func:`launch_subject` 生成，且报告里逐字是它。

    与 `SubjectKind.subject` / `claim_subject` 同一形状、同一理由：格式只有一处定义。
    ``launch:`` 这一族**不是** ``SubjectKind`` 的第三个成员 —— 那个闭集是「盘上的一条
    记录」，而这一份读数的主语是**一次尚未发生的启动**（它在盘上没有对应的行，这恰好
    也是它读得出来的原因：没有行可写）。
    """
    assert launch_subject(SCENE_ID, 2) == "launch:probe-scene@2"
    preview = board.preview()
    assert preview.subject == launch_subject(SCENE_ID, 1)


def test_the_reading_refuses_a_naive_moment(board: _Board) -> None:
    """naive 的 ``at`` 被拒，且拒绝的话说得出**为什么**。

    一个省掉时区的时刻会被按读它那台机器的本地时区解释，而这里拿它比的就是「被引用的
    东西到这一刻还作不作数」—— 于是「这一版能不能启动」取决于这份读数在哪里跑。
    """
    base = board.preview()
    with pytest.raises(ValueError) as caught:
        LaunchPreview(
            subject=base.subject,
            declared=base.declared,
            record=base.record,
            at=datetime(2026, 9, 29, 9, 0),
        )
    assert "naive" in str(caught.value)
    assert "时区" in str(caught.value)


def test_a_subject_that_names_another_version_is_refused(board: _Board) -> None:
    """主语与那一版对不上就拒 —— 「哪一版」是这份读数最要紧的一格。"""
    base = board.preview()
    with pytest.raises(ValueError) as caught:
        LaunchPreview(
            subject=launch_subject(SCENE_ID, 7),
            declared=base.declared,
            record=base.record,
            at=T0,
        )
    assert "launch:probe-scene@7" in str(caught.value)
    assert "第 1 版" in str(caught.value)


def test_a_record_about_another_scene_is_refused(board: _Board) -> None:
    """记录与声明必须是同一个 Scene —— 两格读自同一次调用的同一个主语。

    这一条**只有直接构造才能违反**（两个入口都从同一次调用里拿这两格），而一个没人能
    违反的守卫与一个从不响的守卫在报告上长得一样。
    """
    base = board.preview()
    other = _record().model_copy(update={"scene_id": "another-scene"})
    with pytest.raises(ValueError) as caught:
        LaunchPreview(
            subject=base.subject,
            declared=base.declared,
            record=other,
            at=T0,
        )
    assert "another-scene" in str(caught.value)


def test_an_archived_record_cannot_await_a_signature(board: _Board) -> None:
    """归档的记录不许带「等批准」。

    归档是「这个市场不再经营」，不是「暂时不看」—— 一份已经归档的记录不会再有人去签
    它，那条「等批准」会让人去找一个不存在的待办。
    """
    base = board.preview()
    archived = _record(state=SceneState.ARCHIVED)
    with pytest.raises(ValueError) as caught:
        LaunchPreview(
            subject=base.subject,
            declared=base.declared,
            record=archived,
            at=T0,
            awaiting_approval=(
                ReadinessGap(subject=base.subject, field="outreach", why="探针"),
            ),
        )
    assert "archived" in str(caught.value)


def test_an_archived_record_without_a_pending_signature_is_allowed(board: _Board) -> None:
    """反面：归档**不**带「等批准」时一切照常。

    上一条判据的守卫写成「``state is ARCHIVED`` 就拒」也读得通 —— 而那样一份正常的
    归档预览会被拒掉。这一对判据把守卫的两半都钉住。
    """
    board.seed_ready()
    preview = board.preview(
        scene=_scene(outreach=_calibrated_outreach()),
        record=_record(state=SceneState.ARCHIVED),
    )
    assert preview.record.state is SceneState.ARCHIVED
    assert preview.awaiting_approval == ()
    assert preview.next_action is LaunchNextAction.REQUEST_ACTIVATION


# ----------------------------------------------------------------------
# 二、产品那一对：四条分支
# ----------------------------------------------------------------------


def test_a_missing_product_pair_says_which_half_to_supply(board: _Board) -> None:
    """``(名字, 版本)`` 的四条分支：两格都给 / 两格都没给 / 只给名字 / 只给版本。

    后两条的 ``field`` 指向**没给的那一格** —— 写成一条「引用不完整」的话会让读的人
    自己去猜该补哪一半。

    两格都没给时进 ``missing`` 而不是什么都不报：``SceneVersion.product`` 是一个**裸
    名字**（域层只要求非空），而启动要的是**一个获批产品版本**（B01 `:104` 逐字「一个
    获批产品版本可被两个 Scene 共用」）—— 共享的是版本，不是名字。
    """
    board.seed_ready()

    neither = board.preview(product_id=None, product_version=None)
    assert _fields(neither.missing) == {"product_id"}
    assert neither.invalid == ()
    assert "裸名字" in _find(neither.missing, "product_id").why

    name_only = board.preview(product_version=None)
    assert _fields(name_only.missing) == {"product_version"}

    version_only = board.preview(product_id=None)
    assert _fields(version_only.missing) == {"product_id"}


def test_a_product_that_is_not_on_disk_is_invalid_and_not_cited(board: _Board) -> None:
    """不在盘上的产品进 ``invalid``，且**不进** ``cited``。

    「指着不存在的东西时，补别的都白补」（b01-c 对同一件事的判词逐字如此）—— 所以要动
    的是那一格引用，不是去补资料。
    """
    board.put_product()
    board.put_claim(_claim())
    # 方案这一对**关掉**：这一条只判产品那一对，而方案默认引着第 1 版产品 ——
    # 让它开着会多出一条「方案不引这一版产品」（那是对的另一条判据，不是这一条）。
    preview = board.preview(
        product_id="absent-product", product_version=1, offer_id=None, offer_version=None
    )
    assert _fields(preview.invalid) == {"product_id"}
    assert preview.cited == ()
    assert preview.next_action is LaunchNextAction.FIX_CITATIONS
    assert "不在盘上" in _find(preview.invalid, "product_id").why


def test_a_product_on_disk_is_cited_with_its_own_reading_intact(board: _Board) -> None:
    """在盘上的一版被嵌进 ``cited``，读数**逐字**是 b01-c 那一层的对象。

    ``cited`` 里那一条的 ``subject`` 是**它自己的**（``product:probe-product@1``），
    不是这一层的主语 —— 它是 b01-c 的读数，这一层只转达。
    """
    board.seed_ready()
    preview = board.preview()
    assert _cited_subjects(preview) == ["offer:probe-offer@1", "product:probe-product@1"]
    product_reading = next(
        reading for reading in preview.cited if reading.subject.startswith("product:")
    )
    assert product_reading.subject == "product:probe-product@1"
    assert product_reading.usable is True


def test_the_report_carries_the_inner_reading_whole(board: _Board) -> None:
    """内层读数**整份**在报告里，三张清单与 ``not_reported`` 一格不少。

    `ReadinessGap` 的契约是「``field`` 永远是**这一份读数的主语**上的一格」—— 内层
    那些格子（``capabilities`` / ``limits`` / ``units``）在这一层的主语（一次启动）上
    **不存在**，照着它们改不了。所以它整份嵌着，而不是折平上来。
    """
    board.seed_ready()
    preview = board.preview()
    written = preview.as_dict()["cited"]
    assert [row["subject"] for row in written] == [
        "offer:probe-offer@1",
        "product:probe-product@1",
    ]
    for row in written:
        assert set(row) == {
            "subject",
            "kind",
            "status",
            "usable",
            "at",
            "missing",
            "invalid",
            "awaiting_approval",
            "not_reported",
        }


# ----------------------------------------------------------------------
# 三、方案那一对
# ----------------------------------------------------------------------


def test_no_offer_is_not_a_gap_but_is_reported_as_unanswered(board: _Board) -> None:
    """没引方案**不是**缺口，但是一条「答不上来」。

    切片行把 Product/Offer 写成**可选**（逐字「**可选** Product/Offer」），而可选不等于
    不需要：一次纯市场研究本来就不引方案（那是 b01-f 那条关闭条件），一次投放则要引。
    这一层没有能力分辨「本来就不需要」与「该引而忘了引」—— 那是一个业务判断，所以它
    如实报「这一次的读数里没有方案那一组判定」。
    """
    board.seed_ready()
    preview = board.preview(offer_id=None, offer_version=None)
    assert "offer_id" not in _fields(preview.missing)
    assert preview.invalid == ()
    assert _cited_subjects(preview) == ["product:probe-product@1"]
    assert "这一次要不要引一个已获批方案" in _items(preview)
    # 没引方案时那一条**没有主语**，所以不该出现。
    assert "地区与市场的相容性" not in _items(preview)


def test_a_half_given_offer_pair_says_which_half_to_supply(board: _Board) -> None:
    board.seed_ready()
    assert _fields(board.preview(offer_version=None).missing) == {"offer_version"}
    assert _fields(board.preview(offer_id=None).missing) == {"offer_id"}


def test_an_offer_of_another_workspace_is_invalid_and_not_cited(board: _Board) -> None:
    """跨工作区的那一版进 ``invalid``，**不嵌进** ``cited``，且两个工作区都点名。

    这一条**不是**「不在盘上」—— 它在盘上，是**别人的那一版**。``runs.py::_one_run``
    与查缺面两个处理器对同一件事有一条共用的判词：跨工作区的读不留痕，它在这一侧看起来
    只是一次成功的查询，而被读到的那一份此后会被当成本地事实用下去。

    嵌进 ``cited`` 就等于把它当本地那一版读了 —— 所以这一格是**处置相反**的地方：
    查缺面那两条工具在同一种情形上拒绝整次调用（它们的主语就是那一版），而这里的主语是
    一次启动，那一版只是它引的一个东西。
    """
    board.put_product()
    board.put_offer(workspace=OTHER_WORKSPACE)
    board.put_claim(_claim())
    preview = board.preview()
    gap = _find(preview.invalid, "offer_id")
    assert OTHER_WORKSPACE in gap.why
    assert WORKSPACE in gap.why
    assert "跨工作区的读不留痕" in gap.why
    assert _cited_subjects(preview) == ["product:probe-product@1"]


def test_an_offer_that_does_not_cite_that_product_version_is_invalid(board: _Board) -> None:
    """方案自己不引那一版产品 ⇒ ``invalid``。

    方案里「卖什么」与启动时点名的产品一旦分家，跑起来的那一批资料是按哪一边算的，
    报告上读不出来 —— 所以这一条必须在启动**之前**说出来。

    这一条是**盘上两条记录之间**的关系，不含任何市场知识，所以在这一层判得了。
    """
    board.put_product(1)
    board.put_product(2)
    board.put_offer(product_refs=(ProductRef(product_id=PRODUCT_ID, version=1),))
    board.put_claim(_claim())
    preview = board.preview(product_version=2)
    gap = _find(preview.invalid, "product_id")
    assert "product_refs" in gap.why
    # 这一版方案自己写下的那一条引用要点出来 —— 读的人拿它去核。
    assert f"('{PRODUCT_ID}', 1)" in gap.why
    # 产品那一版自己在盘上，所以它仍然被嵌进来（问题在**引用关系**上，不在那一版上）。
    assert "product:probe-product@2" in _cited_subjects(preview)


def test_a_cited_offer_adds_the_region_and_market_question(board: _Board) -> None:
    """引了方案时**额外**多一条答不上来的：地区与市场可不可比。

    ``SceneVersion.region`` 是自由文本（本仓 fixture 里就是 ``("GB-England-Devon",)``
    那一类值），而 ``OfferVersion.markets`` 是**国家码**。判它要一份地理知识，而本仓
    明令 ``marketops/`` 里不得出现市场词
    （``tests/test_no_hardcoded_market_knowledge.py`` 是执行器）。

    这一条是这一层最要紧的一条 ``not_reported``，因为它挡住的是一次**假的**判定：在
    字符串比较下 ``Devon`` 与 ``GB`` 是「无交集」，而那是一次错的判定 —— 一个
    ``("GB-England-Devon",)`` 的场景配一版 ``("GB",)`` 的方案是**正常**的。
    """
    board.seed_ready()
    preview = board.preview()
    assert "地区与市场的相容性" in _items(preview)
    reason = next(
        item.reason for item in preview.not_reported if item.item == "地区与市场的相容性"
    )
    assert "自由文本" in reason
    assert "国家码" in reason
    assert "b02-a" in reason


# ----------------------------------------------------------------------
# 四、触达未标定 ⇒ 等批准
# ----------------------------------------------------------------------


def test_uncalibrated_outreach_asks_for_a_signature(board: _Board) -> None:
    """``outreach.uncalibrated`` 非空 ⇒ ``awaiting_approval``，且点名是哪几格。

    §6 要求「有人批准过的数」与「随手写的缺省」必须分得开。它进「等批准」而不是「去补」：
    要动的不是调用方自己填一格（那一格填多少、按什么标定，是一次业务决定），是一位负责人
    把它标定下来。
    """
    board.seed_ready()
    preview = board.preview()
    gap = _find(preview.awaiting_approval, "outreach")
    assert "outreach.waits[stage-2]" in gap.why
    assert "未标定" in gap.why


def test_a_calibrated_outreach_policy_stops_asking(board: _Board) -> None:
    """反面：全部标定过的触达策略不再进「等批准」。

    上一条判据在「``OutreachPolicyDecl`` 恒有未标定项」的实现下也是绿的 —— 而那时
    「等人签字」会永远出现在每一份读数里，读的人会学会忽略它。
    """
    board.seed_ready()
    preview = board.preview(scene=_scene(outreach=_calibrated_outreach()))
    assert preview.awaiting_approval == ()
    assert preview.next_action is LaunchNextAction.PREVIEW_RUN


# ----------------------------------------------------------------------
# 五、转达：内层没就绪时，外层出**同类**一行
# ----------------------------------------------------------------------


def test_an_inner_missing_lands_on_the_outer_missing_sheet(board: _Board) -> None:
    """内层 ``missing`` ⇒ 外层 ``missing``（不是「等批准」）。

    把四档并成一档（例如一律报「等批准」）的代价是：内层缺的是资料时，外层会让调用方
    去找一个人签字 —— 签了字也跑不起来，而报告上那句「下一步」是错的。
    """
    board.put_product(capabilities=(), units="")
    board.put_claim(_claim())
    preview = board.preview(offer_id=None, offer_version=None)
    outer = _find(preview.missing, "product_id")
    assert "还没就绪" in outer.why
    assert "product:probe-product@1" in outer.why
    assert preview.next_action is LaunchNextAction.SUPPLY_MISSING


def test_an_inner_awaiting_lands_on_the_outer_awaiting_sheet(board: _Board) -> None:
    """内层只是 ``draft``（等人签）⇒ 外层 ``awaiting_approval``。"""
    board.put_product(status=ProductStatus.DRAFT)
    board.put_claim(_claim())
    preview = board.preview(offer_id=None, offer_version=None)
    outer = _find(preview.awaiting_approval, "product_id")
    assert "内层只差一个人核对" in outer.why
    assert preview.next_action is LaunchNextAction.AWAIT_APPROVAL


def test_an_inner_invalid_lands_on_the_outer_invalid_sheet(board: _Board) -> None:
    """内层指着盘上没有的东西 ⇒ 外层 ``invalid``（那时补外层的是白补）。

    这里用 ``supersedes`` 断链造内层的 ``invalid``：第 2 版写着它取代第 1 版，而第 1 版
    不在盘上（``_product_invalid`` 逐字判这一格）。
    """
    board.put_product(2, supersedes=1)
    board.put_claim(_claim())
    preview = board.preview(product_version=2, offer_id=None, offer_version=None)
    outer = _find(preview.invalid, "product_id")
    assert "内层指着一个不在盘上的东西" in outer.why
    assert preview.next_action is LaunchNextAction.FIX_CITATIONS


def test_a_retired_cited_version_lands_on_invalid(board: _Board) -> None:
    """被引的那一版已经 ``retired`` ⇒ 外层 ``invalid``。

    撤下来的一版仍然读得回来（它是记录，不是删除），所以这一条**不是**「不在盘上」——
    它进 ``invalid`` 是因为**指着它启动**这件事本身不成立：要引的是它的后继版本。

    这一档只有在「三张清单都空却不 ``usable``」时才走得通，所以它自己单独一条。
    """
    board.put_product(status=ProductStatus.RETIRED)
    board.put_claim(_claim())
    preview = board.preview(offer_id=None, offer_version=None)
    gap = _find(preview.invalid, "product_id")
    assert "retired" in gap.why
    assert preview.next_action is LaunchNextAction.FIX_CITATIONS


# ----------------------------------------------------------------------
# 六、``next_action``：五档与优先级
# ----------------------------------------------------------------------


def test_the_next_action_follows_the_sheets_first(board: _Board) -> None:
    """三张清单之间的优先级：``invalid`` ⇒ ``missing`` ⇒ ``awaiting_approval``。

    与 `ProductReadiness.usable` 逐字同一条理由 —— 指着不存在的东西时补多少都白补；
    而一份还缺东西的声明就算今天签了字也不能跑（所以「等签字」排在「去补」之后：它不是
    一个更近的终点）。
    """
    board.put_product()
    board.put_claim(_claim())
    board.put_offer(product_refs=(ProductRef(product_id=PRODUCT_ID, version=1),))
    both = board.preview(product_version=2, offer_id=None, offer_version=None)
    # 产品第 2 版不在盘上（invalid）而 outreach 未标定（awaiting）—— invalid 先。
    assert both.invalid and both.awaiting_approval
    assert both.next_action is LaunchNextAction.FIX_CITATIONS

    supply = board.preview(product_id=None, product_version=None, offer_id=None, offer_version=None)
    assert supply.missing and supply.awaiting_approval
    assert supply.next_action is LaunchNextAction.SUPPLY_MISSING


def test_the_next_action_asks_for_activation_when_the_record_does_not_accept_work(
    board: _Board,
) -> None:
    """三张单子都空、记录却不让派发 ⇒ 去受理。

    「这一版生不生效」与「记录让不让派发」是**两件事**（``accepts_new_work`` 与
    ``is_effective``），两件都成立才谈得上开跑：一版 ``active`` 记录底下可以躺着一个
    还没生效的新版本，一版已生效的版本也可以落在一个暂停了的记录里。
    """
    board.seed_ready()
    # 三读都用**已标定**的触达声明：这一条判的是「三张单子都空、记录却不让派发」，
    # 而缺省那份声明自带一条 ``awaiting_approval``（``outreach``），它会让
    # ``next_action`` 停在 ``AWAIT_APPROVAL`` —— 那是另一条判据（见五）。
    draft = board.preview(
        scene=_scene(outreach=_calibrated_outreach()),
        record=_record(state=SceneState.DRAFT),
    )
    assert draft.missing == () and draft.invalid == () and draft.awaiting_approval == ()
    assert draft.next_action is LaunchNextAction.REQUEST_ACTIVATION

    # 记录是 active（让派发），而这一版还没生效 —— 记录里 latest 2、生效 1。
    pending = board.preview(
        scene=_scene(version=2, outreach=_calibrated_outreach()),
        record=_record(state=SceneState.ACTIVE, latest_version=2, effective_version=1),
    )
    assert pending.is_effective is False
    assert pending.record.accepts_new_work is True
    assert pending.next_action is LaunchNextAction.REQUEST_ACTIVATION

    # 反过来：这一版就是生效版，而记录被暂停了 —— 记录那一侧的拒绝理由要在报告里。
    paused = board.preview(
        scene=_scene(outreach=_calibrated_outreach()),
        record=_record(state=SceneState.PAUSED, pause_reason=PauseReason.OPERATOR),
    )
    assert paused.is_effective is True
    assert paused.record.accepts_new_work is False
    assert paused.next_action is LaunchNextAction.REQUEST_ACTIVATION
    assert paused.as_dict()["dispatch_refusal"].startswith("paused: operator_pause")


def test_the_next_action_is_preview_run_when_everything_holds(board: _Board) -> None:
    """什么都不缺、这一版生效、记录也让派发 ⇒ 下一步是**生成一份 Run 预览**。

    这是这一层能给出的**最远**的一句话：它自己不开跑（那是受理面与 ``run_preview``
    的事），只说「下一次调用该是那一个」。
    """
    board.seed_ready()
    preview = board.preview(scene=_scene(outreach=_calibrated_outreach()))
    assert (preview.missing, preview.invalid, preview.awaiting_approval) == ((), (), ())
    assert preview.is_effective is True
    assert preview.next_action is LaunchNextAction.PREVIEW_RUN


# ----------------------------------------------------------------------
# 七、``at`` 是承重的
# ----------------------------------------------------------------------


def test_the_same_board_read_at_two_moments_gives_two_answers(board: _Board) -> None:
    """**同一份盘上状态**在两个时刻读出两个答案。

    这是「``at`` 被真的用上了」的唯一判据：一个把 ``at`` 收下却不往下传的实现，在
    「naive 被拒」那条之外全部绿。这里用一条**有期限的已批准说法**造那个差别 ——
    方案引着它，而它到 ``LATER`` 已经过期。
    """
    board.put_product()
    board.put_offer()
    board.put_claim(_claim(expires_at=T0 + timedelta(days=30)))
    scene = _scene(outreach=_calibrated_outreach())

    earlier = board.preview(scene=scene, at=T0)
    assert earlier.missing == ()
    assert earlier.next_action is LaunchNextAction.PREVIEW_RUN

    # **盘面一个字没改**：只有 ``at`` 换了，而答案跟着换。
    later = board.preview(scene=scene, at=LATER)
    assert later.missing != earlier.missing
    # 外层那一行是**同类**缺口，格名是那条读数的主语那一格（``_rollup`` 那一档）；
    # 「哪一格过期了」的原文逐字在下一条断言里 —— 内层读数整份嵌在 ``cited``。
    assert _fields(later.missing) == {"offer_id"}
    inner = [reading for reading in later.cited if reading.kind is SubjectKind.OFFER]
    assert "approved_claim_refs" in {gap.field for gap in inner[0].missing}
    assert later.next_action is LaunchNextAction.SUPPLY_MISSING
    assert later.at == LATER


# ----------------------------------------------------------------------
# 八、声明的回声与报告的四格
# ----------------------------------------------------------------------


def test_the_declared_grid_is_the_same_function_that_the_receipts_use(board: _Board) -> None:
    """``declared`` 那一格逐字是 ``scene_receipt.version_receipt`` 的输出。

    少一格 ``calibrated``、或者多一句措辞，都会让**同一条盘上事实**在 ``create_scene``
    的回执里说一套、在启动预览里说另一套 —— 而两处各自都正常，没有一处会响。
    """
    board.seed_ready()
    preview = board.preview()
    assert preview.as_dict()["declared"] == version_receipt(preview.declared)


def test_the_report_carries_all_four_version_grids(board: _Board) -> None:
    """``version`` / ``latest_version`` / ``effective_version`` / ``is_effective`` 四格一起报。

    一次**没有**点名的调用会把最新版读上来，而四格一起报出来，所以「它读的是不是最新版」
    在报告上是一个读数，不是一个假设。这是 b01-c 那一片 M4 教训的直接后果：**被引**的那
    一版不许替换，而**主语**那一版恰恰是调用方在点的 —— 两件事形状相同、处置相反。
    """
    board.seed_ready()
    preview = board.preview(
        scene=_scene(version=2),
        record=_record(state=SceneState.ACTIVE, latest_version=2, effective_version=1),
    )
    written = preview.as_dict()
    assert written["version"] == 2
    assert written["latest_version"] == 2
    assert written["effective_version"] == 1
    assert written["is_effective"] is False
    assert written["scene_id"] == SCENE_ID
    assert written["state"] == "active"
    assert written["at"] == T0.isoformat()


# ----------------------------------------------------------------------
# 九、§4.2 那张检查表：这一层的两半合起来是它，且不重不漏
# ----------------------------------------------------------------------


def test_the_eight_items_of_the_checklist_are_split_in_two_without_overlap() -> None:
    """§4.2 第 3 步的八项 = 「答得上」三项 ∪ 「答不上来」五项，且两处不相交。

    这一条钉住的是一件**会漂**的事：那八项写在 `docs/PRODUCT_LINE_AND_SCENE_MODEL.md:122`
    上，而这一层把它们分在两处（``_ANSWERED_ITEMS`` 与 ``LAUNCH_NOT_REPORTED``）——
    分成两处是因为答得上的三项在这一层的**别处**已经有了落点（``declared`` 回声与三张
    清单），再抄一遍就是第二处定义。

    分开写的代价是：文档加了第九项时，两处都不会自己更新。所以这里把并集、交集与
    条数三条都钉住。
    """
    from marketops.application.launch_preview import _ANSWERED_ITEMS

    answered = set(_ANSWERED_ITEMS)
    unanswered = {item.item for item in LAUNCH_NOT_REPORTED}
    #: 逐字取 ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:122`` 的那八项。
    checklist = {
        "来源入口",
        "准入/排除",
        "角色策略",
        "采集访问边界",
        "资源需求",
        "Cohort 计划",
        "成本可观测范围",
        "待批准缺项",
    }
    assert answered & unanswered == set()
    assert answered | unanswered == checklist
    assert len(answered) == 3
    assert len(unanswered) == 5
    for item in LAUNCH_NOT_REPORTED:
        assert item.reason.strip(), item.item


def test_every_unanswered_item_names_who_can_answer_it() -> None:
    """每一条 ``not_reported`` 都说得出**谁答得上**（不是一个「暂不支持」）。

    ``NotReported.reason`` 的契约逐字（``plan_preview``）：读的人的下一个问题永远是
    「那谁答得上」。所以这里判的不是措辞好不好看，是**句子里有没有一个落点** ——
    一个本仓的模块名、一个类型名、或者一片切片。
    """
    for item in LAUNCH_NOT_REPORTED:
        assert any(
            marker in item.reason
            for marker in (
                "``",
                "b0",
                "§",
            )
        ), item.item


# ----------------------------------------------------------------------
# 十、``launch_preview`` 是纯读：调用它不改变盘上任何东西
# ----------------------------------------------------------------------


def test_the_reading_writes_nothing(board: _Board) -> None:
    """读一次预览，盘上**一个字节都不变**。

    B01 `:102` 逐字「**有批准才持久受理** Scene/Workstream 及 Job」—— 反过来说，
    没有批准时那次调用必须是纯读。构造上的那一半（零外部调用）在
    ``tests/test_launch_preview_is_offline.py``；这一条判的是**行为**上的另一半：
    预览一次之后，三条读法的读数与预览前逐字相同。
    """
    board.seed_ready()
    before = _snapshot(board)
    board.preview()
    board.preview(product_id=None, product_version=None, offer_id=None, offer_version=None)
    assert _snapshot(board) == before


def _snapshot(board: _Board) -> dict[str, object]:
    return {
        "product": board.catalog.get_version(PRODUCT_ID, 1),
        "offer": board.catalog.get_offer(OFFER_ID, 1),
        "claims": board.outreach.approved_claims(),
    }
