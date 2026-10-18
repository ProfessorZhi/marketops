"""``SessionBindingStore``：一个客户端会话的便利焦点怎么落盘。

一张表（``db.py`` 的 ``session_binding``），两个实现（``adapters/sqlite/session.py``
主存、``adapters/memory.py`` 测试替身），跑同一份契约测试 —— 与
``ports/scenes.py`` / ``ports/views.py`` 是同一条纪律。

## 这个端口只有三个方法，而少掉的那些每一个都是一条纪律

* **没有** ``expire(binding_id)`` 或任何「作废」入口。一条过期的绑定**什么都不
  改变**（``domain/session.py::SessionBinding.expired``）：它不授予任何东西，所以
  它过期也不收回任何东西 —— 而一个 ``expire`` 入口让「会话关了」看起来像一次权限
  回收。§7.1 ``:272`` 逐字「session A 关闭后 server-side job/schedule 继续」。
* **没有** ``for_principal(principal) -> 权限/能力``。这个模块里没有任何一格能表示
  「这个会话能做什么」—— 一条能读出权限后果的查询就是那条纪律的撤销。
* **没有** ``bindings_for_object(ref)``（按焦点反查会话）。它读起来很方便，而它
  立刻会用来回答「谁在看这个对象」——那是一个**存在别处**的问题（Workstream /
  Watch / 复核队列），这个端口答不了它，只能给出一个过期的缓存答案。
* **没有**任何完整性查询。``focus_refs`` 是一串**不解析的字符串**
  （``domain/session.py`` 第 2 条），所以这里没有、也不得有一个方法去问
  「这个 ref 指向的那一行在不在」—— 那正是「不是 Account/Job 的外键」这句话的
  可执行形态。

## 为什么只有一个 ABC，而调度那一侧是两个

调度那一侧（``ports/scheduling.py``）的第二个契约是**执行**：它对外说话、会花钱。
这一边**没有任何执行**：一条绑定是一个便利指针，写它不产生任何外部效果，
读它也不授权任何东西。把「移动焦点」包成一个 ports 方法只会让「改一下在看哪个
视图」看起来像一次需要被授权的动作。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..domain.session import SessionBinding

__all__ = ["BindingWrite", "SessionBindingStore"]


@dataclass(frozen=True)
class BindingWrite:
    """一次绑定写入的结果。两个字段的理由与 ``ports/views.py::ViewWrite`` 逐字相同。

    ``binding`` 是**读回来的那一行**，不是入参：调用方给的 ``binding_id`` 有可能
    不是这一行上真正用的那个（见 :meth:`SessionBindingStore.put_binding`），
    而一个直接回传入参的实现会让调用方拿着一个**盘上不存在的 id** 去做下一次写入。
    """

    binding: SessionBinding
    created: bool


class SessionBindingStore(ABC):
    """客户端会话便利指针的持久化契约。**没有版本号，也没有 CAS。**

    ``session_binding`` 的主键是 ``binding_id``（不是 ``(id, revision)``）：
    这个对象不是一次授权，所以它不版本化 —— ``domain/session.py::SessionBinding.refocused``
    论证过理由（给一个不是权威的东西一版一行，会让「我换了一下在看哪个视图」在盘上
    留下一条需要运维清理的审计痕迹）。

    ``UNIQUE (workspace, principal, client_kind, client_session_ref)`` 是那一行的形状：
    ``client_session_ref`` 为 NULL 时 SQLite 认为互不相等（这正是想要的，
    ``db.py`` 那一格逐字），所以「同一个人的两条无 id 绑定」各自成立；
    非 NULL 时它挡的是「同一个会话被写成两条」—— 而那种情形下两条绑定会在
    「这个会话上次在看什么」上给出两个答案。
    """

    @abstractmethod
    def put_binding(self, binding: SessionBinding) -> BindingWrite:
        """写入或**就地更新**一条绑定，返回盘上那一行。

        ## 身份是那个**会话**，不是调用方给的那个 id

        判据按两步走，次序是承重的：

        1. ``binding_id`` 已经在盘上、而它的会话四格（``workspace`` /
           ``principal`` / ``client_kind`` / ``client_session_ref``）与这一次**不同**
           ⇒ :class:`~marketops.domain.errors.StoreError`，消息里同时点名那个 id 与
           盘上那四格。同一个 id 配两个会话只可能是调用方自己造了一个撞车的 id
           （规范 id 是由会话推出来的，见 ``domain.session.session_binding_id``），
           而静默覆盖会让**前一个会话**的焦点凭空变成另一个人的。
        2. 按会话四格找：找到了就**就地更新**那一行（``focus_refs`` /
           ``report_cursor`` / ``expires_at`` / ``last_seen`` 换成新的），
           ``created=False``；没找到就插入，``created=True``。

        第 2 步里那条「按会话找」而不是「按 id 找」是刻意的：一条绑定的身份是
        **哪个客户端上的哪一次会话**，``binding_id`` 只是它的内容寻址写法。一个
        按 id 找的实现会让「同一个会话带着一个新铸的 id 写第二次」在 UNIQUE 上撞车
        —— 而那句报错读起来像「有人先写了」，实际发生的事是「同一个会话又动了一下
        焦点」，处置完全不同（一个要重读，一个什么都不用做）。

        ## 它不做任何完整性检查

        ``focus_refs`` 里那些 ref 指向的东西在不在，这一层不查也不该查
        （模块 docstring 第 4 条）。
        """
        raise NotImplementedError

    @abstractmethod
    def get_binding(self, binding_id: str) -> SessionBinding | None:
        """按 id 读一条。没有就是 ``None``。"""
        raise NotImplementedError

    @abstractmethod
    def bindings(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[SessionBinding, ...]:
        """本工作区的绑定，按 ``(principal, client_kind, binding_id)`` 升序。

        顺序是契约的一部分（``ReviewCaseStore.list_cases`` 逐字同一条）：
        ``list_session_bindings`` 要报的是「谁在哪儿、上次在看什么」，
        而一个由存储层随便给的顺序会让两次调用给出两个「第一个人」。

        ``principal=None`` = 不过滤。**给 None 的调用方必须自己已经做过授权判定**
        （与 ``ports/scenes.py::records`` 逐字同一条）。
        """
        raise NotImplementedError
