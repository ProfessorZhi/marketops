"""§B21 的**只读**状态页：``get_continuation_status``。

## 为什么这一页必须有工具面

「为什么此刻没有更多工作在跑」这个问题，在工具面上没有落点之前只有一种回答方式：
去看控制器进程的日志。而日志在容器里、在某个终端的回滚缓冲里，且只在他恰好那一刻
看着的时候存在。一个停在 ``WAITING_ON_HUMAN`` 上等了一天的市场与一个健康地等着
下一次到期的市场，在没有这一页时**长得完全一样** —— 而两者的处置相反。

这一页不是控制器的日志搬运工：它读的是**盘上那些行**（``continuation`` 的未结意图、
``continuation_owner`` 的归属行），所以它答得出控制器进程此刻在不在。

## 一条工具，而它只读

* 它走 ``application.continuation.survey`` 而不是 ``ContinuationController.tick`` ——
  ``survey`` 的签名里没有执行器这个参数，所以「这一页会不会顺手推一轮」这个问题在
  签名上就答完了。
* 它**不** ``acquire``。认领归属是一次 CAS，它会**改盘**；而一个查询工具改盘是这一族里
  最不该有的事（``get_scene`` / ``list_scenes`` 同一条）。
* 它读一次归属行，报「现在谁在推、那一代还活着吗」。这一格不是附属信息：**一个
  ``WAITING_DUE`` 而没有任何持有者的部署，与一个正在被推着的部署，判词逐字相同**，
  而处置相反（去启动控制器 / 什么都不用做）。

## 它答不出「欠着哪几件」，而那是有意的

判词旁边给的是**六个数**（在跑 / 到期 / 等到期 / 暂停挡着 / 缺接线 / 等人），不是
一行行意图。列出那几行要在这里再写一遍「哪些行算未结」的判据，而那一条已经写在
``domain/continuation.py`` 的 ``classify`` 与 ``application/continuation.py`` 的
``_read_status`` 里 —— 两个产地漂移的形态是「状态页说到期 3 件、控制器一件都没领」。

## 谁看得见

``_CONTROL``，与 ``get_ops_report`` / ``list_scenes`` 同一组角色。研究侧读不到：
未结意图里住着主体与业务键，那是控制面的事实。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...application.continuation import survey
from ...config import Settings
from ...domain.util import utc_now
from ...ports.continuation import WakeupRegistry
from ...ports.jobs import JobStore
from ...ports.scenes import SceneStore
from .tools import _SCENE_RECORD_NO_STORE, _SCENE_RECORD_PRESENT, Handler

__all__ = ["continuation_handlers"]


def continuation_handlers(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore] | None,
    registry: Callable[[], WakeupRegistry] | None,
    scenes: Callable[[], SceneStore] | None,
) -> dict[str, Handler]:
    """本构建的状态页处理器，按工具名索引。

    Args:
        settings: 配置。这一层只用它的 ``profile``（本部署服务的那个 Scene）与
            ``database_path``（核「这个部署写过东西没有」）。
        jobs: 持久作业记录的**工厂**。``None`` 表示这个服务实例没有落点 —— 那时
            **这一条工具不注册**，而不是注册成恒回「没有工作」的实现：一个恒回
            ``NO_WORK`` 的状态页与一个真的什么都不欠的市场在报告上同形，而前者的
            处置是接线（与 ``review_handlers`` 在 ``review is None`` 时的取向逐字
            相同）。
        registry: 唤醒归属的**工厂**。``None`` 时这一条也不注册 —— 理由多一条：
            归属那一格答的是「有没有人在推」，而一个报 ``driver: null`` 的实现让
            「没有控制器在跑」与「这个部署没有接归属注册表」同形。
        scenes: 场景存储的**工厂**。它与 ``product_loop`` / ``review`` 收到的是
            **同一个**：控制器读的是那一份场景的放行状态，而这一页若读另一份，
            「状态页说没有暂停挡着、控制器说场景暂停」会各说一套。

    三个工厂都是惰性调用、且都在这一条工具**被调用之后**才展开：``_compose`` 的
    契约是「构造服务面需要的一切，不绑端口、不建运行目录」，而
    ``SqliteWakeupRegistry`` / ``SqliteJobStore`` 的构造会 ``ensure_schema``，
    也就是会**建库**。
    """
    if jobs is None or registry is None or scenes is None:
        return {}

    def get_continuation_status() -> dict[str, Any]:
        """此刻为什么没有更多工作在跑，以及现在有没有人在推。"""
        if not settings.database_path.is_file():
            # **读一次不建库。** 库文件不存在时不去构造存储：那两个构造函数都会
            # ``ensure_schema``，于是在一个「还没有任何产物」的部署上，一次查询会
            # 凭空建出一个空库，而此后「还没有产物」与「本该有东西」在盘上就再也
            # 分不开了。措辞与 ``get_scene`` / ``list_scenes`` 用的是同一组常量，
            # 不在这里另编一套（两处各编一套的话，同一件事在报告里会有两种说法）。
            return {
                "workspace": settings.profile,
                "store_state": _SCENE_RECORD_NO_STORE,
                "status": None,
                "driver": None,
                "note": (
                    f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                    "所以这一页读不出任何东西。"
                    "这**不是**「没有工作在跑」—— 前者的处置是受理一次作业，"
                    "后者不用管。"
                ),
            }

        now = utc_now()
        status = survey(
            jobs=jobs(), scenes=scenes(), workspace=settings.profile, now=now
        )
        owner = registry().current(settings.profile)
        return {
            "workspace": settings.profile,
            "store_state": _SCENE_RECORD_PRESENT,
            "status": status.as_dict(),
            "driver": _driver(owner, now=now),
            "note": (
                "``status`` 答「为什么此刻没有更多工作在跑」，``driver`` 答「现在有没有"
                "人在推」。**两句一起读**：``state`` 是 ``WAITING_DUE`` 而 ``driver`` "
                "为空，意思是「等得到，但没人来推」—— 判词本身看不出这一格。\n"
                "这一页只读：它不认领归属、不驱动任何一轮、不写任何行。"
            ),
        }

    return {"get_continuation_status": get_continuation_status}


def _driver(owner, *, now) -> dict[str, Any] | None:
    """归属那一格的形状。``None`` = 此刻没有任何持有者。

    ``stale`` 与 ``holder`` 一起给，而不是把过期的持有者报成「没有人」：那两件事
    的处置不同 —— 「这一代卡住了，等它过期」与「没有控制器，去启动一个」。而这个
    分别只有 ``heartbeat_at`` 读得出来（``OWNER_STALE_AFTER`` 是 90 秒，人看不到
    那一格时会以为控制器活着）。
    """
    if owner is None:
        return None
    return {
        "holder": owner.holder,
        "epoch": owner.epoch,
        "acquired_at": owner.acquired_at.isoformat(),
        "heartbeat_at": owner.heartbeat_at.isoformat(),
        "stale": owner.is_stale(now),
    }
