"""这一层全部处理器共用的四样：``Handler`` / ``readable`` / ``store_failure`` /
``require_scene``。

``Handler`` 是工具处理器的形状，中间两样是「domain 的两种拒绝怎么变成一条可读回执」，
最后一样是「这次调用说的是哪个场景」。它们原来分住在 ``tools.py``（前两样与第四样）
与 ``product_loop.py``（第三样），而 ``readable`` 的 docstring 当时就写着它「**住在这里**
（而不是住在第一个用到它的那一族里）：它服务的是「这一层全部处理器怎么把 domain 的拒绝
翻成一条可读回执」这一件事」，并预告了搬家的条件。条件现在满足：出现了一个**不是**
产品环那一段的处理器族，而它 import 不到那两个模块。

``require_scene`` 走的是**同一条**路、且理由是同一件事的第二个面：启动预览那一族
（``launch.py``，切片 b01-d）既 import 不到 ``tools.py``（那一族的第一件事就是「启动
授权前零外部调用」，而 ``tools.py`` 经 ``adapters.build_stamp`` 够得到 ``subprocess``），
也不能让「这个请求说的是哪个 Scene」在那里**另写一遍** —— 那正是这个函数自己 docstring
里论证过的分家（「受理时放行的场景与读取时放行的场景会先分家」）。

## 搬家的理由是可实测的，不是风格问题

``tests/test_registration_is_offline.py`` 从
``marketops/adapters/mcp_server/product_catalog.py`` 出发做 import 闭包，要求登记面
（B01 ``:104`` 逐字「ProductLine/产品资料登记不产生网络调用」）够不到任何网络能力。
那两个父模块各自够得到一样东西：

* ``marketops.adapters.mcp_server.tools``
  → ``marketops.adapters.build_stamp`` → ``subprocess``
* ``marketops.adapters.mcp_server.product_loop``
  → ``marketops.application.acquisition`` → ``marketops.ports.acquisition``

第一条值得说清楚：``build_stamp`` 用 ``subprocess`` 读 git 版本，**那不是网络调用**。
判据仍然把它算作网络能力，因为它的口径逐字是「真的会对外发起请求、**或起一个外部
进程**」，而它自己声明了这个口径是白名单、**故意粗**（上限也写在那里）。粗的那一面
在这里的代价是一个具体形状：凡是 import ``tools.py`` 的模块都进不了登记面。

不把那两样**抄**一份给登记面，因为 ``readable`` 的 docstring 已经论证过抄写会漂移 ——
漂移的方向是「其中一处不再回显字段名」，而一条缺了字段名的回执读起来只是「构造不出来」。
搬到这里是唯一一处同时满足两边的写法：**一份定义**，而登记面 import 得到。

既有的调用点一个都没改：四个 import 点（``product_loop`` / ``review`` / ``runs`` /
``outreach``）拿到的仍是同一个名字，只是那一行现在指向**定义**，不再指向一个转手的
模块。``outreach.py`` 那一条例外 —— 它 import 的是 ``product_loop`` 的 ``_readable``
与 ``_store_failure``，而那两个名字现在是 ``product_loop`` 从本模块重导出的，所以
它也照旧成立（它没有理由为此改一行）。

## 这个模块自己必须是 I/O 中性的

它只 import ``mcp`` 的异常类型、``pydantic`` 的 ``ValidationError``、``marketops.config``
的 ``Settings``、``domain.errors`` 与标准库。多引一样就可能把上面那两条链之一引回来，
而引回来之后红的是登记面那一条判据 —— 报错位置指向 ``product_catalog.py``，离真正的原因
有两跳。判据本身守得住这一条：它算的是闭包，不是直接 import。

``Settings`` 是搬 ``require_scene`` 带进来的唯一一样新东西，而它**不是**一条新路：
``product_catalog.py`` 早就直接 import 它（它要 ``settings.database_path``），所以它已经
在那条闭包里。判据算的是闭包，这一行因此不改变任何一个面够得到什么。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...config import Settings
from ...domain.errors import StoreError

__all__ = ["Handler", "readable", "require_scene", "store_failure"]

#: 工具的处理器：吃关键字参数，返回可 JSON 序列化的 dict。
Handler = Callable[..., dict[str, Any]]


def readable(exc: ValidationError) -> str:
    """把 pydantic 的校验错误摊成几行可读的话。

    ``str(ValidationError)`` 会把输入值原样回显，在一条工具回执里那是几十行 ——
    而调用方要的只是「哪一个字段、为什么不行」。

    **住在这里**（而不是住在第一个用到它的那一族里）：它服务的是「这一层全部
    处理器怎么把 domain 的拒绝翻成一条可读回执」这一件事，而三个处理器族
    （``decisions`` / ``product_loop`` / ``review``）都要做这件事。留在某一族里、
    另外两族各抄一份的三处实现，漂移的方向是**其中一处不再回显字段名**——
    而那一条的回执读起来只是「构造不出来」，没有任何定位信息。
    """
    lines = []
    for item in exc.errors():
        where = ".".join(str(part) for part in item["loc"]) or "(整体)"
        lines.append(f"  - {where}: {item['msg']}")
    return "\n".join(lines)


def store_failure(exc: StoreError) -> ToolError:
    """存储层说过的失败 —— 它是一条**结果**，不是一次崩溃。

    ``StoreError`` 与 ``SceneConflictError`` 是兄弟，处置也不同（见
    ``domain/errors.py`` 里两者的 docstring）：前者是「这个库里已经有一样东西，
    而它不是你这次要写的那份」，后者是「有人先写了」。但两条都是**调用方要据它
    改东西**的结局，所以两条都要从这里的处理器回到工具结果上去。

    ## 不接它会发生什么（实测过）

    不接的时候它**逃出去**，而逃出去之后调用方读到的是一句**光秃秃的**
    ``Error executing tool create_scene`` —— 那句话里一个字的信息都没有。
    存储层原本给了一句很准的话（「已有 version=1 的版本，但摘要与本次不同……
    **不覆盖** —— 覆盖会让按版本号引用它的工作单元静默换了规则」），而它在这条
    路径上**整个丢掉了**。

    这不是假想的：会走到这里的一条真路径是「用同样的 Scene 名、改一个预算再立项
    一次」。那一刻库里的第 1 版摘要与本次不同，于是 ``put_version`` 先抛 ——
    而它与「有人先写了」在回执上必须都能读出来。

    ``research.py`` 里同一条纪律（「只接存储层**说过**它会抛的那几类」）已经写着，
    这一条是它在产品环这一侧的落点。
    """
    return ToolError(str(exc))


def require_scene(settings: Settings, scene_id: str | None) -> str:
    """本部署唯一服务的那个 Scene，或一条拒绝。

    抽成函数是因为 ``submit_research`` 问的是同一个问题，而两处各写一份的
    后果不是文案不一致，是**判定不一致**：受理时放行的场景与读取时放行的
    场景会先分家，分家的形态是一个作业挂在一个 ``get_scene`` 拒绝承认的
    Scene 上 —— 于是它读得回来，却永远看不到自己的规则。

    **公开的**（不在名字前加下划线）是因为第二个调用者出现了：产品环的三个
    Scene 工具（``product_loop.py``）问的是同一个问题 —— 立项、改版、生命周期
    动作都要先回答「这个请求说的是不是本部署服务的那个市场」。让它们各写一遍
    「``scene_id`` 必须等于 ``settings.profile``」的后果，与上面那一段逐字相同，
    只是分家的形态换成了「研究侧拒绝、产品侧放行」。

    ## 搬到 ``_shared.py`` 是第三个调用者带来的（``launch.py``）

    定义原来住在 ``tools.py``，而**这一行搬家不改变任何一个面够得到什么**：三个模块
    （``tools`` / ``product_loop`` / ``review``）按名字引回的那个名字逐字没变，只是它
    现在指向**定义**而不是一次转手 —— 与 ``readable`` / ``store_failure`` 那次搬家
    同一条做法，也是本模块 docstring 里那两句预告的兑现。

    第三个调用者是启动预览那一族，而它**必须**够不到 ``tools.py``：``tools.py`` 经
    ``adapters.build_stamp`` 够得到 ``subprocess``，而这一族存在的理由逐字是「启动
    授权前零外部调用」—— 让它在那一格上靠自觉，就是 ``readable`` 那段论证过的
    「两处各写一份会漂移」在一个更贵的地方重演一遍。
    """
    wanted = (scene_id or "").strip() or settings.profile
    if wanted != settings.profile:
        raise ToolError(
            f"没有名为 {wanted!r} 的 Scene。本部署配置的 Scene 是 {settings.profile!r}"
            "（MARKETOPS_PROFILE）。一个部署只服务一个 Scene —— 证据、预算与凭据都属于它；"
            "静默退回默认值会让调用方以为读的是另一个场景的规则。"
        )
    return wanted
