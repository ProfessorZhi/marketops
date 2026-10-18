"""Scene 的生命周期用例：立项、改版、激活、暂停、恢复、归档。

## 这个模块的每一处写都走「读 → 改 → CAS 写」

``SceneRecord`` 上的三个变更方法（``with_version`` / ``activate`` / ``transition``）
**不落盘**，落盘要靠 ``SceneStore.put_record(record, expected_revision=...)``。把这两半
接起来是这一层的事，而接法只有一种是对的：

    record = store.get_record(scene_id)        # 读
    changed = record.activate(...)             # 改（纯函数）
    store.put_record(changed, expected_revision=record.revision)   # 带着读到的版本写

三行里的第三行是全部要点。写成 ``expected_revision=changed.revision - 1`` 也等价，
但那样「我读到的是哪一版」就从调用点消失了 —— 而它正是这个参数要回答的问题。

**冲突不重试。** ``SceneConflictError`` 直接抛给调用方：它会重新读一次当前状态、
重新决定，那是一个**业务决定**（有人已经把场景归档了，我还要不要激活？）。在
这一层自动重试等于替人做了那个决定，而重试一次「激活一个已归档的场景」永远不会
成功 —— 它只会把一次真实的冲突变成一条看不出原因的报错。

## 为什么排除清单的核对发生在**这里**（而不是 domain 或 profile 装载）

§4 逐字：「未获批的新场景只能 draft/preview，不得用产品扩展绕开已批准 profile 的
排除清单」。那句话有两个主语 —— 场景与 profile —— 而 domain 只看得到一个：
``SceneVersion.exclusions_cover`` 只能回答「我给的这个清单覆不覆盖你给的那些词」，
它不知道 profile 是谁。

交叉核对需要同时握着两份东西，`application/` 正是那个位置（与
``application/grading.py`` 握着 profile 与账号是同一个理由）。判据本身仍是纯函数
（``SceneVersion.exclusions_cover``），这一层只负责**把 profile 的那一份喂进去**，
以及「不合就拒绝」。

## 版本与影响是一起写下的

``add_version`` 返回 ``(record, impact)``。影响不是一句事后说明，它是这一次写操作的
**产物**：读者要回答「改这一版之后，哪些已经做过的事要重做」，而那个答案只有在
新旧两版同时在手时才算得出来。等报告阶段再回头算，就要求那时的读者还握着上一版 ——
而那时它通常已经不在了（版本是 append-only，但**当前生效的**只有一版）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import ConfigError
from ..domain.maintenance import Aspect
from ..domain.profile import ProfileBundle
from ..domain.scene import (
    PauseReason,
    SceneImpact,
    SceneRecord,
    SceneState,
    SceneVersion,
    scene_impact,
)
from ..ports.scenes import SceneStore


@dataclass(frozen=True)
class SceneValidation:
    """一版 Scene 与它声称的 profile 之间对不对得上。

    ``problems`` 是**空元组表示通过**，而不是一个 ``ok: bool`` 加一份理由 —— 后者
    会让「通过」与「没跑过核对」在返回值的形状上不可区分，而这个模块存在的理由
    就是让那次核对无法被跳过。

    ``uncalibrated_aspects`` **不是** problem：§4 允许有效期来自「已批准的策略」**或**
    被显式标注的未校准缺省值。把它算成 problem 会让每一个尚未校准的场景都装不出来，
    而那会把「未校准」推成「不要写这个字段」—— 正是 §4 要防的那件事。它必须被
    报出来，只是不阻断。
    """

    scene_id: str
    version: int
    problems: tuple[str, ...] = ()
    uncalibrated_aspects: tuple[Aspect, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(version: SceneVersion, bundle: ProfileBundle) -> SceneValidation:
    """纯函数：一版 Scene 对着一个已装载的 profile。**不碰存储。**"""
    problems: list[str] = []

    if version.profile_id != bundle.profile_id:
        problems.append(
            f"这一版声称用的是 profile {version.profile_id!r}，装载进来的是 "
            f"{bundle.profile_id!r}。两者的规则不同，核对下去毫无意义。"
        )

    declared = bundle.profile
    required = declared.exclusion_ids if declared is not None else ()
    missing = version.exclusions_cover(required)
    if missing:
        problems.append(
            f"排除清单没有覆盖 profile 已批准的那几条：{list(missing)!r}。\n"
            "一个「范围更大」的场景不是一次更宽松的配置，是一次没有边界的采集 ——"
            "而它长得与正常场景一模一样。"
        )

    if not version.budget:
        problems.append(
            "没有声明任何预算科目。未声明不等于无限：一份没有上限的场景会让"
            "「忘了写额度」与「故意放开」在行为上不可区分。"
        )
    for line in version.budget:
        if line.ceiling <= 0:
            problems.append(
                f"科目 {line.line.value!r} 的上限是 {line.ceiling} —— 零额度是一次都跑不了，"
                "必须显性失败，而不是留在这里等第一次调用撞上去。"
            )

    return SceneValidation(
        scene_id=version.scene_id,
        version=version.version,
        problems=tuple(problems),
        uncalibrated_aspects=version.refresh.uncalibrated,
    )


def create_draft(
    *, store: SceneStore, version: SceneVersion, bundle: ProfileBundle
) -> SceneRecord:
    """立项：写入第 1 版与一条 ``DRAFT`` 记录。

    **不接受 ``version > 1``。** 一个从第 3 版开始的场景，其历史里有两个从未写过的
    编号，而没人能回答它们是丢了还是不存在 —— 与 ``SceneVersion.supersedes`` 那条
    判据是同一个理由，只是这里连「上一版」都不存在，所以更要拦在前面。

    「第 1 版的 ``supersedes`` 必须是 ``None``」这半句**不在这里**：它是
    ``SceneVersion`` 的 ``_supersedes_is_the_previous_one`` 判的，而那个校验器
    原先漏掉了 ``supersedes=0``（``0 == 1 - 1``，于是「第 1 版取代第 0 版」被判为
    合法）。修在 domain 而不是在这里再补一遍，是因为绕过本函数构造一个
    ``SceneVersion`` 的路径不止一条。
    """
    if version.version != 1:
        raise ConfigError(
            f"立项只能写第 1 版，本次给的是第 {version.version} 版。"
        )

    report = validate(version, bundle)
    if not report.ok:
        raise ConfigError(
            "这一版 Scene 装不出来：\n" + "\n".join(f"  - {item}" for item in report.problems)
        )

    store.put_version(version)
    record = SceneRecord(
        scene_id=version.scene_id,
        workspace=version.workspace,
        state=SceneState.DRAFT,
        latest_version=1,
        created_at=version.created_at,
        updated_at=version.created_at,
    )
    store.put_record(record, expected_revision=None)
    return record


def add_version(
    *, store: SceneStore, version: SceneVersion, bundle: ProfileBundle
) -> tuple[SceneRecord, SceneImpact]:
    """登记新的一版（**不生效**），并给出它相对上一版的影响。

    影响相对的是 ``latest_version``（被取代的那一版），不是 ``effective_version``
    —— 「上一版」是作者写下的顺序，而生效版本可能落后好几版（暂停期间改了很多次）。
    拿生效版本去比，会漏掉中间几版累积的改动。
    """
    report = validate(version, bundle)
    if not report.ok:
        raise ConfigError(
            "这一版 Scene 装不出来：\n" + "\n".join(f"  - {item}" for item in report.problems)
        )

    record = _require_record(store, version.scene_id)
    previous = store.get_version(record.scene_id, record.latest_version)
    if previous is None:
        raise ConfigError(
            f"scene={record.scene_id!r} 的记录说最新是第 {record.latest_version} 版，"
            "而库里读不到那一版。\n"
            "记录与版本必须能互相指称 —— 读不到只可能意味着有人删过版本，"
            "而版本是 append-only 的。"
        )

    store.put_version(version)
    changed = record.with_version(version, at=version.created_at)
    store.put_record(changed, expected_revision=record.revision)
    return changed, scene_impact(previous, version)


def activate(
    *, store: SceneStore, scene_id: str, version: int, at: datetime, by: str
) -> SceneRecord:
    """让某一版生效 —— **这就是「批准」这个动作**。"""
    record = _require_record(store, scene_id)
    changed = record.activate(version=version, at=at, by=by)
    store.put_record(changed, expected_revision=record.revision)
    return changed


def pause(
    *, store: SceneStore, scene_id: str, at: datetime, reason: PauseReason
) -> SceneRecord:
    """暂停：停止**新的**研究派发与外联派发。

    受理侧（回信/退信/退订/投诉）不看这个状态 —— 见 ``SceneRecord.accepts_new_work``
    的 docstring。暂停一个场景不会让已经在路上的回信收不到。
    """
    record = _require_record(store, scene_id)
    changed = record.transition(SceneState.PAUSED, at=at, pause_reason=reason)
    store.put_record(changed, expected_revision=record.revision)
    return changed


def resume(*, store: SceneStore, scene_id: str, at: datetime) -> SceneRecord:
    """恢复。**生效版本保持不变** —— 恢复不重发已发邮件、不重吃整个母池。"""
    record = _require_record(store, scene_id)
    changed = record.transition(SceneState.ACTIVE, at=at)
    store.put_record(changed, expected_revision=record.revision)
    return changed


def archive(*, store: SceneStore, scene_id: str, at: datetime) -> SceneRecord:
    """归档。**终态** —— ``SCENE_TRANSITIONS`` 里没有出边。"""
    record = _require_record(store, scene_id)
    changed = record.transition(SceneState.ARCHIVED, at=at)
    store.put_record(changed, expected_revision=record.revision)
    return changed


def get_scene(store: SceneStore, scene_id: str) -> SceneRecord | None:
    return store.get_record(scene_id)


def list_scenes(
    store: SceneStore, *, workspace: str | None = None
) -> tuple[SceneRecord, ...]:
    """列出场景。``workspace=None`` 列出全部 —— **调用方必须自己已做过授权判定**。

    ``ports/scenes.py`` 那条「给 None 的调用方必须自己已经做过授权判定」在这里的
    落点是：本函数是一个透明的转发，不做判定也不假装做过。真正的判定在
    ``security/`` 那一层。
    """
    return store.records(workspace=workspace)


def impact_between(
    store: SceneStore, *, scene_id: str, from_version: int, to_version: int
) -> SceneImpact:
    """任意两版之间的影响。**读不到的版本显性失败**，不拿一个空影响糊过去。"""
    before = store.get_version(scene_id, from_version)
    after = store.get_version(scene_id, to_version)
    if before is None or after is None:
        missing = [
            number
            for number, found in ((from_version, before), (to_version, after))
            if found is None
        ]
        raise ConfigError(
            f"scene={scene_id!r} 读不到第 {missing!r} 版。\n"
            "一次算不出来的影响不是一次「没有影响」—— 后者会让「改了但没记录」"
            "看起来像「没改」。"
        )
    return scene_impact(before, after)


def _require_record(store: SceneStore, scene_id: str) -> SceneRecord:
    record = store.get_record(scene_id)
    if record is None:
        raise ConfigError(f"没有 scene_id={scene_id!r} 这个场景。")
    return record


__all__ = [
    "SceneValidation",
    "validate",
    "create_draft",
    "add_version",
    "activate",
    "pause",
    "resume",
    "archive",
    "get_scene",
    "list_scenes",
    "impact_between",
]
