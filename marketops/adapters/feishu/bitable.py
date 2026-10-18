"""飞书多维表格的**字段映射契约**（纯函数，无凭据、无网络）。

把映射单独写出来，是为了让「接线」变成一件机械的事：
写入侧只需要实现 HTTP 调用，映射关系已经是可评审的代码而不是口头约定。
本模块因此**一行网络代码都没有**，也就不需要任何凭据就能被完整测到 ——
真实写入的那条路是 BLOCKED 的（见 ``client.py``），而这张表今天就可测。

字段名与 ``contracts/contact-route.schema.json`` 一一对应。任何新增字段
都必须同时改这里和契约，否则下游会出现「字段存在但没人知道该放哪」。
「同时改这里」有两种合法结果：**进映射表**，或者被
``tests/test_feishu_adapter.py`` 的 ``NOT_EXPORTED_FIELDS`` 连同理由收下
（那一处判据要求两者的并集恰好覆盖模型的全部字段，所以不存在「谁都没管」的
第三种状态）。

三张映射表在这里，**列名只在这里**：``BITABLE_FIELD_MAP``（路线）、
``BITABLE_OPPORTUNITY_FIELD_MAP``（路线机会）、``BITABLE_HANDOFF_FIELD_MAP``
（销售交接）。客户端不得自己写列名 —— 一个散在客户端里的字面量列名，
改一次列名要改两处，而漏掉的那一处的症状是「写进去了，只是写到了一列没人看的
地方」，不报错。

下游是**研究主表**（SOP L141）：它是合格路线的统一可见层，
**不是发送指令**。两件事在这里被字段结构固定下来：

* ``Grade`` 是**路线**的等级 —— 同一个账号的 A 级路线与 X 级路线是两行；
* ``C/D 必须标为业务入口/通用入口，不得与 A/B 混称关键联系人``（L141）——
  因此入口性质 ``Entry Kind`` 是一列，而不是从 Grade 里反推。
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.entities import ContactRoute, RouteOpportunity
from ...domain.outreach import MAX_BODY_CHARS, SalesHandoff

#: ContactRoute 字段 → 飞书 Bitable 列名。
#: 接线后由 §「新飞书 Sandbox 应用申请完成」时确认列名是否一致。
BITABLE_FIELD_MAP: dict[str, str] = {
    "account_id": "Account ID",
    "organisation": "Organisation",
    "venue_asset_id": "Venue Asset ID",
    "person_name": "Name",
    "role_title": "Role",
    "role_class": "Role Class",
    "business_function": "Business Function",
    "entry_kind": "Entry Kind",
    "block_priority": "Record Block Priority",
    "binding": "Binding",
    "email": "Email",
    "email_domain": "Email Domain",
    "grade": "Grade",
    "grade_reason": "Grade Reason",
    "status": "Route Status",
    "has_conflict": "Conflict Flag",
    "evidence_ids": "Evidence IDs",
    "research_only": "Research Only",
    "send_authorized": "Send Authorized",
}

#: RouteOpportunity 字段 → 飞书 Bitable 列名。
#:
#: 机会与路线进**同一张表**但带不同的 ``Record Block Priority``（机会恒为 P3）：
#: 分两张表会让「这个账号到底有没有可联系的路线」需要 JOIN 两次才答得出来，
#: 而那个问题一天要问很多遍。字段刻意与路线对齐，缺的只有邮箱。
BITABLE_OPPORTUNITY_FIELD_MAP: dict[str, str] = {
    "account_id": "Account ID",
    "organisation": "Organisation",
    "venue_asset_id": "Venue Asset ID",
    "person_name": "Name",
    "role_title": "Role",
    "role_class": "Role Class",
    "business_function": "Business Function",
    "block_priority": "Record Block Priority",
    "reason": "Not A Route Because",
    "evidence_ids": "Evidence IDs",
    "research_only": "Research Only",
    "send_authorized": "Send Authorized",
}


def to_bitable_record(route: ContactRoute) -> dict[str, object]:
    """把一条联系路线转成 Bitable 记录体。

    纯函数，因此现在就能测 —— 不需要任何凭据。
    """
    payload = route.model_dump(mode="json")
    return {column: payload.get(field_name) for field_name, column in BITABLE_FIELD_MAP.items()}


def to_bitable_opportunity_record(opportunity: RouteOpportunity) -> dict[str, object]:
    """把一条路线机会转成 Bitable 记录体。

    ``Email`` 列在机会记录上**不出现**，而不是出现且为空 ——
    一个空的 Email 列看起来像「邮箱没抓到」，而事实是「按定义它就没有邮箱」。
    """
    payload = opportunity.model_dump(mode="json")
    return {
        column: payload.get(field_name)
        for field_name, column in BITABLE_OPPORTUNITY_FIELD_MAP.items()
    }


#: ``SalesHandoff`` 字段 → 飞书 Bitable 列名。
#:
#: 交接与路线**不进同一张表**：路线那张表是研究主表（合格路线的统一可见层），
#: 而交接是给销售看的待办 —— 它的读者、生命周期与可见范围都不同，混在一张表里
#: 会让「销售看到的行」与「研究确认过的行」变成同一个东西。
BITABLE_HANDOFF_FIELD_MAP: dict[str, str] = {
    "handoff_id": "Handoff ID",
    "workspace": "Workspace",
    "scene_id": "Scene ID",
    "campaign_id": "Campaign ID",
    "account_id": "Account ID",
    "route_ref": "Route Ref",
    "event_id": "Event ID",
    "recipient": "Recipient",
    "reply_body": "Reply Body",
    "evidence_ids": "Evidence IDs",
    "state": "Handoff State",
    "created_at": "Created At",
    "external_row_id": "External Row ID",
    "external_version": "External Version",
    "accepted_at": "Accepted At",
}

#: 交接往返的那两列。映射表覆盖模型的每一个字段（完整性由测试执行），所以
#: ``SalesHandoff`` 上那两个写回字段在这里各有一列。
#:
#: **这两列不由客户端回写**：值从回执（行 id + 版本）来，由调用方填进本地那条
#: ``SalesHandoff``。客户端回写会让一次推送变成两次写，而第二次写又会换一个新的
#: 版本号 —— 于是「这一行是什么版本」永远停在「刚刚」。
BITABLE_HANDOFF_ROW_ID_COLUMN: str = BITABLE_HANDOFF_FIELD_MAP["external_row_id"]
BITABLE_HANDOFF_VERSION_COLUMN: str = BITABLE_HANDOFF_FIELD_MAP["external_version"]

#: **幂等写入靠的那一列**。
#:
#: 取值就是 ``handoff_id`` 本身 —— 也就是**我们给的键**，不是提供方给的。
#: 这一句必须写在这里，因为「表里会不会自己认出去重」这个问题只有提供方答得了，
#: 而本轮的提供方行为未经核实（真实写入 BLOCKED，见 ``client.py``）：所以去重
#: 的承诺由我们这一侧给，判据是「按这一列查、查到几行」。
BITABLE_HANDOFF_KEY_COLUMN: str = BITABLE_HANDOFF_FIELD_MAP["handoff_id"]

#: 派生的列（**不是** ``SalesHandoff`` 的字段）：回信正文的字符数。
#:
#: 存在的理由只有一条：正文长度要在**读表时**看得见，而不是要打开那一格正文才数得
#: 出来。所以**写进去的**每一行都带着它 —— 一件越来越接近上限的交接因此能在表上被
#: 认出来。
#:
#: 它**不是**超长那条路的处置信息：放不下的正文会被拒写（:func:`oversized_text_fields`），
#: 表上什么都不留。那种情况下实际长度在**失败回执**里（``text_field_over_limit`` 的
#: ``detail`` 报的是每一列的真实字符数），因为回执才是那一次尝试唯一留下的东西。
BITABLE_REPLY_BODY_CHARS_COLUMN = "Reply Body Chars"

#: 一格文本列的长度上限。超了就**拒绝写入**，见 :func:`oversized_text_fields`。
#:
#: 取值与 ``domain/outreach.py`` 的 ``MAX_BODY_CHARS`` 同一个数，而且**不得更小**：
#: 外面那一侧比里面那一侧松，是「提供方悄悄截断了我们的一封信」的成因；
#: 比里面那一侧紧，则会拒掉一封在本仓合法的信。两个方向的偏差都有一条判据
#: （``tests/test_feishu_push.py``）。提供方**真实**的截断行为本轮未经核实 ——
#: 真实写入是 BLOCKED，所以这里是一个我们愿意为之负责的守门值，不是对提供方
#: 行为的声明。
MAX_BITABLE_TEXT_CHARS: int = MAX_BODY_CHARS


def to_bitable_handoff_record(handoff: SalesHandoff) -> dict[str, object]:
    """把一条销售交接转成 Bitable 记录体。

    纯函数，因此现在就能测 —— 不需要任何凭据。

    ## ``reply_body`` **逐字**进记录，且这一层不做任何长度处理

    交接的核心就是让销售读原文（§7）。所以这里**没有**截断、没有省略号、
    没有「太长就取前 N 个字符」：一个被静默切掉结尾的回信，读的人看不出少了什么
    ——他只会读到一封语气怪异的信，而没有人会想到问题在投影这一层。

    放不下的时候由**写入侧**拒写并说清楚超了多少（:func:`oversized_text_fields`
    是那条判据的纯函数部分），而不是在这里改内容。改内容发生在映射层会让
    「表里的正文与原件不一致」变成一个**没有痕迹**的事实。
    """
    payload = handoff.model_dump(mode="json")
    record = {
        column: payload.get(field_name)
        for field_name, column in BITABLE_HANDOFF_FIELD_MAP.items()
    }
    record[BITABLE_REPLY_BODY_CHARS_COLUMN] = len(handoff.reply_body)
    return record


def oversized_text_fields(record: Mapping[str, object]) -> tuple[str, ...]:
    """``record`` 里超过 :data:`MAX_BITABLE_TEXT_CHARS` 的那些列名。空元组 = 放得下。

    判据是``len(str)``，只对**字符串**取值生效：``None`` 与 ``False`` 不是文本，
    对它们做长度判断会把「这一格没有值」读成「这一格超长」。
    """
    return tuple(
        column
        for column, value in record.items()
        if isinstance(value, str) and len(value) > MAX_BITABLE_TEXT_CHARS
    )


__all__ = [
    "BITABLE_FIELD_MAP",
    "BITABLE_OPPORTUNITY_FIELD_MAP",
    "BITABLE_HANDOFF_FIELD_MAP",
    "BITABLE_HANDOFF_KEY_COLUMN",
    "BITABLE_HANDOFF_ROW_ID_COLUMN",
    "BITABLE_HANDOFF_VERSION_COLUMN",
    "BITABLE_REPLY_BODY_CHARS_COLUMN",
    "MAX_BITABLE_TEXT_CHARS",
    "to_bitable_record",
    "to_bitable_opportunity_record",
    "to_bitable_handoff_record",
    "oversized_text_fields",
]
