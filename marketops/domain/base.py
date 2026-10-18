"""领域模型的唯一基类。

两个设置都是承重的，不是风格偏好。

``frozen=True``：领域对象是**值**，不是可变状态。冻结之后，「谁改了它」这个问题
在类型上就不存在了 —— 想改必须造一个新的，而那会在调用点留下痕迹。一份报告
之所以能复现，前提是它引用的那些值从没被就地改过。

``extra="forbid"``：多出来的字段直接报错。这些模型是给外部系统看的（飞书
Bitable、契约 JSON、未来的 Hermes），**静默忽略一个字段**会让上游塞进来的信息
在下游消失，而且报告上看不出来 —— 那正是「字段名拼错，于是该字段永远是默认值」
这类事故的入口。宁可在这里响。

本基类只此一份。此前 ``entities``、``evidence``、``temporal`` 各自定义了一个
行为完全相同的 ``_Strict``；三个定义在同一个包里，就有了三处会各自漂移的地方。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """所有领域模型的基类。见模块 docstring。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["StrictModel"]
