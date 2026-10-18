"""一版 Scene 的回执 —— **一个定义，两个出口**。

这个模块是一次**搬家**的产物，不是一处新设计。``version_receipt`` 原本住在
``adapters/mcp_server/product_loop.py``（那时叫 ``_version_receipt``），它由三条工具
交出去：``create_scene`` 与 ``add_scene_version`` 的回执里那一格 ``version``，以及
``get_scene`` 那一族的 ``effective``。

搬到这里是因为**第二个出口出现了**：启动预览（``application/launch_preview.py``，
切片 b01-d）要报「这一版声明里固定了什么」，而它与上面那三处说的必须是**逐字同一份**
—— 少一格 ``calibrated``、或者多一句措辞，都会让「同一条盘上事实，回执里说一套、
预览里说另一套」。两处各写一遍的那天，它们会在某次改动之后分家，而报告上两边各自都
正常，没有一处会响。

## 为什么它在 ``application/`` 而不是在 ``domain/``

它交出去的是**一份报告的形状**（一个 ``dict``），不是一条规则。``domain/scene.py``
判的是「这一版合不合法」；这里说的是「这一版是什么」。判据在域、形状在应用层、由
适配层交出去 —— 与 ``application/product_readiness.py`` 的 ``as_dict()`` 同一条分界
（那个方法的 docstring 逐字写着它「也是 MCP 只读面交出去的那一份」）。

## ``instant`` 为什么也在这里

它是 ``version_receipt`` 用到的时刻写法，而 ``product_loop.py`` 里另有三十来处用它
（回执里每一个时刻）。留在那边一份、这边一份，就是同一个格式的第二个定义；搬到这儿
再让那边按名字引回去，是 ``adapters/mcp_server/_shared.py`` 那次搬家的同一条做法
（那个模块的 docstring 逐字：「既有的调用点一个都没改：四个 import 点拿到的仍是同一个
名字，只是那一行现在指向**定义**」）。

**这一层不做任何判定、不读任何存储、不取任何时刻**：两个函数都是从已经读出来的域
对象到 ``dict`` 的纯映射。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..domain.scene import SceneVersion


def instant(value: datetime) -> str:
    """一个带时区的时刻在这个仓库里的写法：ISO-8601。

    带时区由**域**保证（``SceneVersion._aware`` 与 ``SceneApproval._at_aware`` 都拒绝
    naive 时刻），所以这里不重复判它 —— 一个在这一层再判一遍的守卫，只会让「域放行的
    东西这一层拒绝」成为一次可能的自我矛盾。
    """
    return value.isoformat()


def version_receipt(version: SceneVersion) -> dict[str, Any]:
    """一版的回执。

    ``frozen_sha256`` 在这里，因为它才是「这一批东西是按哪份规则做的」的答案；
    ``version`` 那个整数不是（同一个整数在两次部署里可以是两份不同的规则）。
    """
    return {
        "version": version.version,
        "supersedes": version.supersedes,
        "frozen_sha256": version.frozen_sha256,
        "region": list(version.region),
        "scenario": version.scenario,
        "product": version.product,
        "admissions": list(version.admissions),
        "exclusions": list(version.exclusions),
        "role_policy": version.role_policy,
        "source_plan": list(version.source_plan),
        "budget": {
            line.line.value: line.ceiling
            for line in sorted(version.budget, key=lambda item: item.line.value)
        },
        "refresh": [
            {
                "aspect": rule.aspect.value,
                "after_days": rule.after_days,
                "calibrated": rule.calibrated,
            }
            for rule in version.refresh.rules
        ],
        # 触达节奏与它的标定状态**一起**报出来。少一个 ``calibrated`` 会让
        # 「这几个数是有人批准过的」与「这几个数是我们随手写的缺省」在回执上
        # 逐字相同 —— 而 §6 的整条要求就是这两件事必须分得开。
        "outreach": {
            "max_per_wave": version.outreach.max_per_wave,
            "max_per_recipient": version.outreach.max_per_recipient,
            "max_per_account": version.outreach.max_per_account,
            "waits_hours": {
                row.stage.value: row.after_hours for row in version.outreach.waits
            },
            "prior_touches": {
                row.stage.value: row.allowed_prior for row in version.outreach.prior_touches
            },
            "uncalibrated": list(version.outreach.uncalibrated),
        },
        "profile_id": version.profile_id,
        "profile_version": version.profile_version,
        "created_at": instant(version.created_at),
        "note": version.note,
    }


__all__ = ["instant", "version_receipt"]
