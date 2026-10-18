"""**多会话的便利焦点**：一个客户端会话上次在看什么、报告读到哪里。

``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md`` §7.1 逐字：

    它不提供额外权限，不成为 Account/Job 的外键，不参与业务幂等。客户端没有稳定
    session id 时插件自己持有一个 context token，并且所有写动作仍带显式
    object/version。

``catalog.py`` 里 ``put_session_binding`` 的 summary 把这三句读成「三条是**承重的**」，
并说清了它们防的是什么：「一个『会话里已经选好了』若被读成权限或默认值，就会让一次
本要显式给对象的调用变成隐式继承上一条」。

## 三条否定在这里各是一个**形状**，不是一句注释

1. **不提供权限。** 这个模块里没有任何一格能表示「这个会话能做什么」：没有角色、
   没有 scope、没有 capability。一条绑定的存在与否，读代码看不出任何权限后果 ——
   因为能读出后果的那一格**不存在**。判据是一条负向用例：拿着一条已经绑好的会话
   去调一个工具，工具仍然要显式对象与版本（``tests/test_session_binding_negatives.py``）。
2. **不是 Account/Job 的外键。** ``focus_refs`` 是**一串不解析的字符串**，这一层
   没有、也不得有一个方法去问「这个 ref 指向的那一行在不在」。理由是可测试的：
   一旦这里有了「ref 必须在某某表里」，绑定就被挂回了一个业务对象，而
   「换个对象研究」会变成一次**约束失败**而不是一次普通的焦点移动 —— 那时人会去
   改绑定表，而他要改的其实是焦点。
3. **不参与业务幂等。** ``binding_id`` 不出现在任何别的表的主键或唯一键里。
   这一条在这一层表现为：绑定的键是「谁在哪个客户端上的哪一次会话」，而
   业务幂等键是「哪一次业务变化」——两个键的组成里没有一格是共用的。把
   ``binding_id`` 塞进一个业务幂等键，会让「同一个人在另一个 session 里重发同一件事」
   变成两件不同的事，而重复受理正是幂等键要挡的那一个。

## §7.1 禁止一个全局 ``current_scene``

那一节逐字：「服务端不能保存 ``member-a.current_scene=France`` 这种全局可变状态」。
所以这个模块的键**永远是四段**（工作区、主体、客户端种类、会话引用），没有
「本部署当前场景」这样一个不分主体的格子。一条按 ``client_kind`` 全局唯一的绑定
让两个 session 共用一格焦点，而它的表现不是报错：B 读到了 A 的对象。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import field_validator

from .base import StrictModel
from .util import SEP, check_canonical_id, check_reference, check_slug_safe, sha256_text, to_utc

__all__ = ["SessionBinding", "session_binding_id"]


#: ``binding_id`` 的前缀。它是内容寻址 id（推导见 :func:`session_binding_id`），
#: 而不是一个随机 uuid —— 随机 id 会让「同一个会话写两次」在同一条 UNIQUE 上撞车，
#: 而它撞出来的是一句「有人先写了」，处置却是「这两个其实是一个会话」。
_BINDING_PREFIX = "sb-"


def session_binding_id(
    workspace: str, principal: str, client_kind: str, client_session_ref: str
) -> str:
    """``(工作区, 主体, 客户端种类, 会话引用)`` → 一个内容寻址的 ``binding_id``。

    分隔符用 :data:`~marketops.domain.util.SEP`（U+001F），与 ``domain/job.py``
    的 ``job_id`` 是同一条推理：分段本身可能含 ``:`` 这类常见分隔符，于是
    ``("a:b", "c")`` 与 ``("a", "b:c")`` 会拼成同一个字符串 —— 两个不同的会话
    获得同一个绑定 id，而 id 决定的是「这次写入落在哪一行上」。

    **只在 ``client_session_ref`` 有值时可用。** §7.1 说「客户端没有稳定 session id
    时插件自己持有一个 context token」—— 也就是说，没有稳定 id 的那种情况下，
    那个 token **就是**引用。真的一个都没有时，调用方必须自己给一个 ``binding_id``
    （``put_session_binding`` 的那条拒绝），而不是让这一层用「同一个主体的另一条
    空引用」把它推出来：那条推导会让同一个人的两条无 id 绑定合成一行。
    """
    digest = sha256_text(SEP.join((workspace, principal, client_kind, client_session_ref)))[:32]
    return f"{_BINDING_PREFIX}{digest}"


class SessionBinding(StrictModel):
    """一个客户端会话的便利焦点。字段逐字取自 §7.1 那一段。

    ## ``expires_at`` 到了不等于「权限过期」

    一条过期绑定**什么都不改变**：它不授予任何东西，所以它过期也不收回任何东西。
    它只影响「这个会话的焦点还能不能当作一个提示读出来」—— ``:272`` 逐字
    「session A 关闭后 server-side job/schedule 继续」，那一句的另一面就是
    「绑定过期不该让任何工作停下来」。
    """

    binding_id: str
    workspace: str
    principal: str
    client_kind: str
    #: 客户端有稳定 id 时才写（§7.1 ``:271``）。``None`` 是**正常取值**，不是缺省错误。
    client_session_ref: str | None = None
    #: 在看哪些 Workstream / SavedView / 复核队列。**一串不解析的字符串** —— 见模块
    #: docstring 第 2 条。
    focus_refs: tuple[str, ...] = ()
    #: 报告读到哪里。一个不透明的游标，这一层不解释它。
    report_cursor: str | None = None
    expires_at: datetime | None = None
    last_seen: datetime

    @field_validator("binding_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "binding_id")

    @field_validator("client_kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        return check_slug_safe(value, "client_kind")

    @field_validator("focus_refs")
    @classmethod
    def _focus(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            check_reference(item, "focus_refs[]")
        if len(set(value)) != len(value):
            raise ValueError(f"focus_refs 里有重复项：{value!r}")
        return value

    @field_validator("expires_at", "last_seen")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)

    # ------------------------------------------------------------------ 读数

    def expired(self, at: datetime) -> bool:
        """这条绑定到这一刻是不是已经过期了。``expires_at=None`` = 不过期。"""
        return self.expires_at is not None and self.expires_at <= to_utc(at)

    def refocused(
        self,
        *,
        focus_refs: tuple[str, ...],
        report_cursor: str | None,
        at: datetime,
        expires_at: datetime | None = None,
    ) -> SessionBinding:
        """移动焦点之后的这一条。**同一个 ``binding_id``，同一行。**

        与 ``ScheduleDefinition`` 那边「一版一行」**刻意不同**，而理由是可测试的：
        那份声明是一次授权（改范围要重新过权限），这条绑定是一个便利指针
        （``Data_Model`` ``:42`` 逐字 ``never business authority``）。给一个不是
        权威的东西一版一行，会让「我换了一下在看哪个视图」在盘上留下一条需要
        运维清理的审计痕迹，而真正该留痕的那一次写入会被淹没在里面。
        """
        return SessionBinding.model_validate(
            {
                **self.model_dump(),
                "focus_refs": focus_refs,
                "report_cursor": report_cursor,
                "expires_at": expires_at,
                "last_seen": to_utc(at),
            }
        )
