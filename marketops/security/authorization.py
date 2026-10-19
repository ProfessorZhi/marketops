"""批准记录的**来源验证**：一个拒绝型的接口，不是第二套身份系统。

## 它补的是哪一格

:class:`~marketops.domain.outreach.WaveAuthorization` 已经绑定了
actor（``authorized_by``）、范围（workspace / scene）、目标（清单摘要）、
有效期（``expires_at``）与撤回（``revoked_at``）。``authorized_by`` 在 MCP 的
``authorize_wave`` 上取自**传输层已认证的 Bearer 主体**，不是载荷字段 ——
这一段的身份是可信的。

缺的是下一句：**``authority_ref`` 指向的那份外部批准记录，本进程没有验证过。**
它可以是一个真实工单，也可以是一个随手写下的字符串；两者在盘上长得一样。
本模块把「验证」做成一个显式的、可注入的端口，而不是把它写成一条乐观的假设。

## 为什么默认判决是 ``BLOCKED_TRUST_SOURCE``

本仓今天**没有**接入任何受管的批准记录来源（工单系统、审批库、签名服务）。
一个没有来源的验证器只有两种诚实的回答：

* ``BLOCKED_TRUST_SOURCE`` —— 「我不知道，因此不允许」；
* 一个假的 ``VERIFIED`` —— 它会让「没有验证」与「验证通过」在记录上同形。

第二种是这条边界最不能犯的错。所以 :class:`NoAuthoritySource` 对每一次询问
都答 ``BLOCKED_TRUST_SOURCE``，而 :func:`require_verified_authority` 把它
当成拒绝。

## 它不做什么

* **不产生授权。** 它只回答「这份外部引用可不可信」，不回答「该不该批准」。
* **不引入第二个身份源。** actor 仍然来自 ``security.identity`` / Bearer 令牌环；
  本模块只验证**外部批准记录**这一格。
* **不读环境变量、不读网络、不读时钟。** 需要外部系统的实现由部署注入；
  本模块的默认实现是纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..domain.errors import MarketOpsError
from ..domain.outreach import WaveAuthorization

__all__ = [
    "AuthorityCheck",
    "AuthoritySource",
    "AuthorityVerdict",
    "NoAuthoritySource",
    "require_verified_authority",
]


class AuthorityVerdict(str, Enum):
    """一次来源验证的四种结局。**四种都要能分辨。**

    ``VERIFIED`` 是唯一放行的一种。其余三种分开，因为处置不同：
    ``NOT_FOUND`` 要补记录，``UNTRUSTED`` 要查是谁写的，``BLOCKED_TRUST_SOURCE``
    是「本部署根本没有验证器」—— 那是一条部署事实，不是一次记录错误。
    """

    VERIFIED = "verified"
    NOT_FOUND = "not_found"
    UNTRUSTED = "untrusted"
    BLOCKED_TRUST_SOURCE = "blocked_trust_source"


@dataclass(frozen=True)
class AuthorityCheck:
    """验证器的一次回答。``detail`` 是给人读的理由，不是判据。"""

    verdict: AuthorityVerdict
    detail: str = ""

    @property
    def allows(self) -> bool:
        """只有 ``VERIFIED`` 放行。**这一格是唯一真值判断。**"""
        return self.verdict is AuthorityVerdict.VERIFIED


class AuthoritySource(Protocol):
    """外部批准记录的来源。部署注入，测试给替身，默认没有。

    参数是**这次批准绑定的全部事实**，不是一段自由文本：验证器要能回答
    「这条引用是否覆盖这个主体、这个范围、这个动作、这份内容」，否则它只能
    核对「引用存在」，而存在不等于覆盖。
    """

    def check(
        self,
        *,
        authority_ref: str,
        authorized_by: str,
        workspace: str,
        scene_id: str,
        action: str,
        target: str,
    ) -> AuthorityCheck: ...


class NoAuthoritySource:
    """本部署没有受管批准记录来源。**每一次询问都拒绝。**

    这不是一个「空实现」的占位符：它是今天唯一诚实的实现。把它换成
    ``VERIFIED`` 而不接一个真的来源，等于把 ``BLOCKED_TRUST_SOURCE``
    从记录里删掉。
    """

    def check(
        self,
        *,
        authority_ref: str,
        authorized_by: str,
        workspace: str,
        scene_id: str,
        action: str,
        target: str,
    ) -> AuthorityCheck:
        return AuthorityCheck(
            AuthorityVerdict.BLOCKED_TRUST_SOURCE,
            "本部署没有配置可验证的批准记录来源；"
            f"{authority_ref!r} 不能被核对，因此不得当成一次已完成的批准。",
        )


def require_verified_authority(
    authorization: WaveAuthorization, source: AuthoritySource
) -> None:
    """要求 ``authorization.authority_ref`` 被 ``source`` 验证为 ``VERIFIED``。

    Raises:
        MarketOpsError: 任何非 ``VERIFIED`` 的判决，或验证器抛出异常。

    验证器抛出异常也按拒绝处理：一个在验证途中崩掉的来源不能把一次调用变成
    「没答案，先放行」。那是 ``fail-open``，而这条边界的全部价值是 ``fail-closed``。
    """
    try:
        check = source.check(
            authority_ref=authorization.authority_ref,
            authorized_by=authorization.authorized_by,
            workspace=authorization.workspace,
            scene_id=authorization.scene_id,
            action="outreach.send",
            target=authorization.manifest_sha256,
        )
    except MarketOpsError:
        raise
    except Exception as exc:  # noqa: BLE001 - 验证器崩溃必须变成拒绝，不能放行
        raise MarketOpsError(
            f"批准记录来源在验证 {authorization.authority_ref!r} 时失败：{exc}。\n"
            "验证失败按**拒绝**处理 —— 一个没有答案的来源不是一次批准。"
        ) from exc
    if not check.allows:
        raise MarketOpsError(
            f"授权 {authorization.authorization_id!r} 的批准记录"
            f"（{authorization.authority_ref!r}）未通过验证："
            f"{check.verdict.value} —— {check.detail}\n"
            "没有可验证来源时，这条授权不得登记；把 BLOCKED_TRUST_SOURCE "
            "写成通过，会让「没有验证」与「验证通过」在记录上长得一样。"
        )
