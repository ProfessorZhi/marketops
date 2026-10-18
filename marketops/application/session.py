"""**会话绑定那一个应用用例**：记下一个客户端会话的便利焦点。§7.1。

``catalog.py`` 里 ``put_session_binding`` 那条 summary 说了一句话和三件不做的事，
而这三条**每一条都在这个文件里有一处可执行的落点**：

* **它不授予任何权限。** 本模块的判据里没有 ``Role``、没有 scope、没有
  ``ToolSpec``，返回类型里也没有任何一格能表示「这个会话能做什么」——
  :class:`~marketops.domain.session.SessionBinding` 上根本不存在那样一格。
  它能被观测到的形态是：**写一条绑定不会让任何一次调用从被拒变成通过**，
  而这条判据住在 ``tests/test_session_binding_negatives.py``。
  ``principal`` 在签名里，而它进去之后只是**数据**（那一行属于谁），
  不是**凭据**：这个函数拿它去判定的次数是 0。
* **它不是任何 Account 或 Job 的外键。** ``focus_refs`` 是一串**不解析的字符串**
  （``domain/session.py`` 第 2 条），本模块不查它们指向的东西在不在 ——
  写一条指向「还没有的视图」的绑定是成功的，而这是**对的**：一个客户端在视图
  被建出来之前先记住「我在看这个」，不该被一次完整性检查拒掉。
* **它不参与业务幂等。** 这个文件里没有一处 ``dedupe_key``，也没有一处
  ``fire_id`` —— 那两样东西分别住在业务处置点与调度主键上。一条绑定写十次与写
  一次在**业务**上没有任何区别，而它的写入因此**不产生新版本**：同一个会话再写
  一次是就地更新（``ports/session.py::put_binding`` 第 2 步），不是「第 2 版」。

## 「每一次写入仍然自己说清对象与版本」是**签名**在保证

三个内容参数（``focus_refs`` / ``report_cursor`` / ``expires_at``）**没有缺省值**，
所以调用方不能「只说变了的那一格」—— 一个省略掉的参数在这个函数里不存在。
理由就是那条 summary 的后半句：一次本要显式给对象的调用，若可以从盘上那一条
继承上一个对象，那它就变成了**隐式继承**，而「我在看哪个视图」读错一格与
「我在看哪一个」在报告上同形（两者都是一条合法的 ``focus_refs``）。

## 过期不是回收

``expires_at`` 到了**什么都不改变**（``domain/session.py`` 逐字）：它不收回权限
（它本来就没授予）、不停下任何 job/schedule（§7.1 ``:272``）。本模块因此**不拒绝**
写入一条已经过期的绑定，也不在写入时清掉任何东西 —— 一次「顺手把过期的清掉」的
写入会让「这个会话上次在看什么」在过期那一刻**消失**，而那是唯一一份能回答
「他当时在做什么」的记录。
"""

from __future__ import annotations

from datetime import datetime

from ..domain.session import SessionBinding, session_binding_id
from ..domain.util import to_utc, utc_now
from ..ports.session import SessionBindingStore

__all__ = ["put_session_binding"]


def put_session_binding(
    bindings: SessionBindingStore,
    *,
    workspace: str,
    principal: str,
    client_kind: str,
    client_session_ref: str | None,
    focus_refs: tuple[str, ...],
    report_cursor: str | None,
    expires_at: datetime | None,
    binding_id: str | None = None,
    at: datetime | None = None,
) -> SessionBinding:
    """写入（或就地更新）一条会话绑定，回**盘上那一行**。

    Args:
        bindings: 绑定存储。
        workspace / principal / client_kind / client_session_ref: 这一行的会话四格。
            ``client_session_ref=None`` 是正常取值（§7.1 ``:271``：客户端有稳定 id
            时才写），而它不是「随便填一个」的位置 —— 见下面那段。
        focus_refs / report_cursor / expires_at: 内容。**没有缺省值**，见模块 docstring。
        binding_id: 一般不给。给的时候必须与四格推出来的那一个一致 ——
            不一致会被 ``put_binding`` 那两步判据挡住（它按 ``binding_id`` 找到的
            行属于另一个会话，抛 ``StoreError``）。
        at: 这一刻，写进 ``last_seen``。``None`` = ``utc_now()``。

    Raises:
        ValueError: ``binding_id`` 与 ``client_session_ref`` **都没有**。
            那时没有任何东西能推出这一行的身份 —— 而一个「那我自己造一个 uuid」
            的实现会让**同一个人的两条无 id 绑定**变成两行永不复用的记录，
            于是「这个会话上次在看什么」在两个答案之间没有裁判。

    ``binding_id`` 的两条路是刻意的：

    * 有 ``client_session_ref`` ⇒ **由它推**（``session_binding_id``，内容寻址）。
      于是同一个会话的第二次写入拿到同一个 id，落回**同一行**；
    * 没有 ⇒ 调用方必须自己给一个 id（§7.1：那种情况下插件自己持有一个 context
      token，**那个 token 就是引用**）。这一层不会替它编一个：编出来的 id 与那个
      会话没有任何可推导的关系，而它下一次写入时会再编一个新的 —— 那时同一行变成
      两行，而两行都自称是这个会话的焦点。
    """
    moment = utc_now() if at is None else to_utc(at)
    if binding_id is None:
        if client_session_ref is None:
            raise ValueError(
                "client_session_ref 与 binding_id 都没有：这一行的身份无处可推。\n"
                "客户端有稳定 session id（或插件自持的 context token）时把它放在 "
                "client_session_ref 里，id 会由它推出来；真的一个都没有时，"
                "调用方必须自己给一个 binding_id —— 这一层替它编一个，会让同一个"
                "会话的下一次写入拿到另一个 id，于是同一行变成两行。"
            )
        binding_id = session_binding_id(
            workspace, principal, client_kind, client_session_ref
        )
    binding = SessionBinding(
        binding_id=binding_id,
        workspace=workspace,
        principal=principal,
        client_kind=client_kind,
        client_session_ref=client_session_ref,
        focus_refs=focus_refs,
        report_cursor=report_cursor,
        expires_at=expires_at,
        last_seen=moment,
    )
    return bindings.put_binding(binding).binding
