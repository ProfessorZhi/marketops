"""飞书适配层的测试。

验收标准是**反向**的：不是「能推数据」，而是「没有凭据时不阻塞」。
"""

from __future__ import annotations

from marketops.domain.entities import ContactRoute, RouteOpportunity
from marketops.adapters.feishu import (
    ADAPTER_STATE,
    BITABLE_FIELD_MAP,
    BITABLE_OPPORTUNITY_FIELD_MAP,
    FeishuBitableClient,
    to_bitable_opportunity_record,
    to_bitable_record,
)
from tests.conftest import DEFAULT_PROFILE

#: 这一批构造用的工作区 = 测试用的那个 profile（模型上必填、无默认值；这里是测试
#: 构造器，所以有一个值是合法的 —— 见 ``tests/test_evidence_store_contract.py``）。
WORKSPACE = DEFAULT_PROFILE

#: 刻意不进入飞书列的字段：内容寻址的内部 id。
#:
#: 它们是对内的标识（同一个人在不同运行里得到同一个 id），不是研究表要看的
#: 事实。写进表里只会多两列没人读的十六进制串。
INTERNAL_ONLY_FIELDS = {"person_id", "role_id"}

#: 另一类刻意不导出的字段：**部署自己的那一维**（``workspace``）。
#:
#: 与上面那两个不是同一个理由，所以分开写：``workspace`` 不是内部 id，它是这一行
#: 的 Scene 血统 —— 而它**不进飞书**恰恰是因为同一件事：一张 Bitable 表属于一个
#: 部署（同一个 app token、同一份 ``feishu_*`` 配置），所以这一列在这张表里逐行
#: 相同，它不是「这一条路线」的事实，是「这张表」的事实。
#:
#: 与状态库那边的处置是一致的：``adapters/sqlite/db.py`` 的 ``bind_workspace``
#: 让一个库只装一个工作区（第二个写入被拒）。两处都在说「这些行属于谁是一个部署级
#: 判断，不是一行级的字段」。要用另一张表装另一个 Scene 的行，那是另一个部署。
#:
#: 反过来的做法（映射加一列 ``"workspace": "Workspace"``）在运维上是一次**破坏性**
#: 改动：``to_bitable_record`` 逐列取值，表里没有这一列时写入会被 Bitable 以
#: ``FieldNameNotFound`` 拒掉 —— 一次血统修复不该顺手让线上的表开始写不进去。
#: ``SalesHandoff`` 那张表导出 ``workspace`` 与这里不矛盾：那一张从设计起就有这两列。
#: 第三类刻意不导出的字段：**§9 的四个时刻与当前性结论**。
#:
#: 不与上面两组合并，因为理由与它们都不同：
#:
#: * 它们是**本部署在某一刻的判断**，不是研究主表要看的市场事实。``freshness``
#:   尤其 —— 它是 ``temporal.evaluate_freshness`` 按某一版 ``FreshnessPolicy``
#:   算出来的结论，离开那份策略与那个 ``evaluated_at`` 就无法复核。把它写进表里，
#:   读表的人会读成「这个联系人现在是有效的」，而这句话成立的条件在表里一个字
#:   都没有。
#: * 运维上的理由与 ``workspace`` 那一组相同：表里没有这些列，而
#:   ``to_bitable_record`` 逐列取值，写入会被 Bitable 以 ``FieldNameNotFound``
#:   拒掉 —— 一次「多存两个时刻」的改动不该顺手让线上的表开始写不进去。
#:
#: 这不表示它们不重要：它们是**读得到**的（``get_account`` 逐字段 dump 整行），
#: 只是这张表的列集合是研究口径，不等于契约的全部字段。
TEMPORAL_FIELDS = {
    "first_seen_at",
    "last_verified_at",
    "source_published_at",
    "valid_from",
    "valid_to",
    "freshness",
}

NOT_EXPORTED_FIELDS = INTERNAL_ONLY_FIELDS | {"workspace"} | TEMPORAL_FIELDS


def _route(**overrides) -> ContactRoute:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "organisation": "Example Trust",
        "email": "grounds@example.co.uk",
        "email_domain": "example.co.uk",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return ContactRoute(**payload)


def _opportunity(**overrides) -> RouteOpportunity:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "organisation": "Example Trust",
        "role_title": "Grounds Manager",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return RouteOpportunity(**payload)


# --- 无凭据时必须无害 ---------------------------------------------------------


def test_client_is_not_configured_without_credentials(settings) -> None:
    assert FeishuBitableClient(settings).configured is False


def test_push_without_credentials_does_not_raise(settings) -> None:
    """关键行为：缺凭据 → 显式跳过，不抛异常、不阻塞 pipeline。"""
    client = FeishuBitableClient(settings)

    for push, items in (
        (client.push_routes, [_route(), _route()]),
        (client.push_opportunities, [_opportunity()]),
    ):
        result = push(items)
        assert result.ok is False
        assert result.reason == "feishu_not_configured"
        assert result.pushed == 0
        assert result.skipped == len(items)


def test_push_with_empty_list_is_still_explicit(settings) -> None:
    result = FeishuBitableClient(settings).push_routes([])
    assert result.reason == "feishu_not_configured"


def test_configured_but_unwired_is_reported_not_silent(settings) -> None:
    """已配置但未接线 → 返回 route_not_wired，而不是静默 no-op。

    静默 no-op 会让「以为推出去了」这种事发生在几个月后。
    """
    from marketops.config import Settings

    configured = Settings(
        data_root=settings.data_root,
        profiles_dir=settings.profiles_dir,
        profile=settings.profile,
        feishu_app_id="cli_fake",
        feishu_app_secret="secret-not-printed",
        feishu_bitable_app_token="token",
        feishu_bitable_table_id="tbl",
    )
    client = FeishuBitableClient(configured)

    assert client.configured is True
    result = client.push_routes([_route()])
    # 理由不是「代码没写」，而是「这一种记录没有一条可判定的业务键」——
    # 两者在报告里的处置完全不同，所以理由本身要说得出这件事（见 push_routes）。
    assert result.reason == "route_not_wired"
    assert "业务键" in result.detail
    assert result.skipped == 1


def test_adapter_state_is_honest() -> None:
    """接线状态**逐种记录分开**，一个常量说不清「哪几种推得了」。

    本轮：交接那条写入路径接上了（隔离测试跑得完整，真实写入 BLOCKED），
    路线与机会没有 —— 因为它们缺一个业务决定，不是缺代码。状态改动时这里会失败，
    提醒同步更新文档与测试。
    """
    from marketops.adapters.feishu import RECORD_KIND_STATE

    assert ADAPTER_STATE == "partially_wired"
    assert RECORD_KIND_STATE["handoff"] == "wired"
    assert RECORD_KIND_STATE["route"] == "no_business_key_declared"
    assert RECORD_KIND_STATE["opportunity"] == "no_business_key_declared"


# --- 字段映射（现在就能测，不需要凭据）----------------------------------------


def test_route_field_map_covers_every_contract_field_except_internal_ids() -> None:
    """映射必须完整覆盖 ContactRoute，否则接线时会出现「字段没人知道放哪」。"""
    uncovered = set(ContactRoute.model_fields) - set(BITABLE_FIELD_MAP)
    assert uncovered == NOT_EXPORTED_FIELDS, f"未映射字段：{sorted(uncovered)}"
    assert set(BITABLE_FIELD_MAP) <= set(ContactRoute.model_fields)


def test_opportunity_field_map_covers_every_contract_field_except_internal_ids() -> None:
    uncovered = set(RouteOpportunity.model_fields) - set(BITABLE_OPPORTUNITY_FIELD_MAP)
    assert uncovered == NOT_EXPORTED_FIELDS, f"未映射字段：{sorted(uncovered)}"
    assert set(BITABLE_OPPORTUNITY_FIELD_MAP) <= set(RouteOpportunity.model_fields)


def test_opportunity_records_have_no_email_column() -> None:
    """机会记录上**不出现** Email 列，而不是出现且为空。

    一个空的 Email 列看起来像「邮箱没抓到」；事实是「按定义它就没有邮箱」。
    这两件事在运维上要做的事完全不同。
    """
    record = to_bitable_opportunity_record(_opportunity())
    assert "Email" not in record
    assert "email" not in {str(key).lower() for key in record}


def test_route_record_carries_the_binding_facts() -> None:
    """C/D 不得与 A/B 混称关键联系人（SOP L141）—— 入口性质因此是一列。"""
    record = to_bitable_record(_route())
    assert "Entry Kind" in record
    assert "Record Block Priority" in record
    assert record["Email"] == "grounds@example.co.uk"


def test_bitable_record_keeps_safety_flags() -> None:
    record = to_bitable_record(_route())
    assert record["Research Only"] is True
    assert record["Send Authorized"] is False

    opportunity = to_bitable_opportunity_record(_opportunity())
    assert opportunity["Research Only"] is True
    assert opportunity["Send Authorized"] is False


def test_bitable_record_has_no_secret_like_keys() -> None:
    for record in (to_bitable_record(_route()), to_bitable_opportunity_record(_opportunity())):
        lowered = " ".join(record).lower()
        for needle in ("secret", "token", "password", "api_key"):
            assert needle not in lowered
