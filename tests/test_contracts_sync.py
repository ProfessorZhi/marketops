"""契约同步测试。

``contracts/*.json`` 由 ``marketops.schema`` 从 pydantic 模型导出。
本文件断言磁盘上的契约与代码一致 —— 契约漂移会直接让测试失败，
而不是等到下游系统按旧 schema 解析时才炸。

本文件还有第二个职责：把**必须在契约里看得见**的几件事固定下来。契约是
下游唯一能看到的东西；一件事如果只存在于代码里，那它对下游就不存在。
"""

from __future__ import annotations

import json
import re

import pytest

from marketops.config import REPO_ROOT
from marketops.schema import CONTRACT_MODELS, CONTRACTS_DIR, build_contracts

EXPECTED_CONTRACTS = {
    # 研究输入与依据
    "account-record",
    # 发现期的候选账号（与 account-record 是不同的类型，见 schema.py 的注释）
    "account-candidate",
    # 市场里的一行账号（§6）：账号这一族的第三个名词，与上面两个是不同类型
    # （清单那一侧才有 ``cohort``，候选那一侧连 ``account_id`` 都还没有）
    "market-account",
    # 一份**冻结的成员名单**（§B12）：不是「有哪些账号」，而是**某一次读池子读到了谁、
    # 按什么规则读的、谁没被读进来以及为什么**。它与上面三个都不同 ——
    # ``cohort`` 属于清单那一侧、候选那一侧连 id 都还没有，而这一型是**一次读数**。
    "account-cohort",
    "evidence",
    "research-run",
    # 一次访问的实测记录（采集设计 §3）：与 evidence 是两个层次 —— evidence 说的是
    # 「据此认为业务事实是什么」，它说的是「那个 URL 那天到底发生了什么」。
    "fetch-attempt",
    # 母池入口：一次母池读取里**每一行去了哪里**（与 account-record 是不同类型，
    # 因为 cohort 是闭集而发现期没有诚实的 cohort，见 schema.py 的注释）
    "source-receipt",
    # 场地资产、责任实体与关系（SOP L78）
    "venue-asset",
    "responsible-entity",
    "account-venue-relation",
    "person",
    "role",
    # 产出：路线与它**不是**的东西
    "contact-route",
    "route-opportunity",
}


def test_expected_contracts_are_declared() -> None:
    assert set(CONTRACT_MODELS) == EXPECTED_CONTRACTS


def test_contract_files_exist() -> None:
    for name in EXPECTED_CONTRACTS:
        assert (CONTRACTS_DIR / f"{name}.schema.json").is_file(), (
            f"缺少 {name}.schema.json —— 运行 python -m marketops.schema 生成"
        )


def test_no_stale_contract_files_on_disk() -> None:
    """磁盘上不得留下**已不再有模型**的契约。

    收敛时最容易漏掉的一步：模型改名了、契约重新生成了，旧文件还在那儿。
    下游按目录扫描时会把它当成一个现存的接口，而它描述的是一个不存在的类型。
    """
    on_disk = {path.stem.replace(".schema", "") for path in CONTRACTS_DIR.glob("*.schema.json")}
    stale = on_disk - EXPECTED_CONTRACTS
    assert not stale, f"磁盘上有多余契约：{sorted(stale)} —— 删除它们"


#: 契约在文档里的那张表。路径按 ``REPO_ROOT`` 拼而不是按 ``__file__``：测试从哪个
#: 工作目录跑都该读同一份文档（``tests/`` 与 ``docs/`` 是同一个检出里的兄弟目录，
#: 而 ``__file__`` 会把这个假设写进测试自己的位置里）。
DATA_MODEL = REPO_ROOT / "docs" / "DATA_MODEL.md"


def _section(text: str, heading: str) -> str:
    """``heading`` 那一节：从这个标题到**下一个二至四级标题**为止。

    按标题切而不是按行号切：行号引用只在指定 revision 时才有意义，而这份文档在
    被人编辑。找不到标题就让 :meth:`str.index` 抛 —— 一个悄悄返回空串的实现会让
    下面那条断言在「标题被改了」时变成「表里一个契约都没有」，而那句话指的是另一
    件事。
    """
    start = text.index(heading) + len(heading)
    match = re.search(r"^#{2,4} ", text[start:], flags=re.MULTILINE)
    return text[start:] if match is None else text[start : start + match.start()]


def test_the_data_model_table_lists_exactly_the_contracts_that_exist() -> None:
    """``docs/DATA_MODEL.md`` §1.1 那张表**成员逐个**与 ``EXPECTED_CONTRACTS`` 相同。

    在此之前那张表只有一个**写在标题里的数**在管着它（「十条契约」），而那个数已经
    落后过两次：``account-candidate`` 与 ``source-receipt`` 各自落盘时都没有人回来
    改它。一句没有任何执行器的算术，在它错掉的那一天不会有人知道 —— 而那张表是
    「下游能看到哪些契约」这个问题在文档里的答案。

    判据比的是**成员**，不是行数（行数是下一个会漂的东西）。加一个契约而忘了改
    文档、或删了一个契约而文档还留着它，两种都会在这里红。
    """
    section = _section(DATA_MODEL.read_text(encoding="utf-8"), "### 1.1 ")
    listed = set(re.findall(r"^\| `([a-z0-9-]+)` \|", section, flags=re.MULTILINE))

    assert listed == EXPECTED_CONTRACTS, (
        f"文档里有、代码里没有：{sorted(listed - EXPECTED_CONTRACTS)}；"
        f"代码里有、文档里没有：{sorted(EXPECTED_CONTRACTS - listed)}"
    )


def test_committed_contracts_match_models() -> None:
    """核心断言：磁盘契约 == 从模型导出的契约。"""
    expected = build_contracts()
    mismatched: list[str] = []

    for name, schema in expected.items():
        path = CONTRACTS_DIR / f"{name}.schema.json"
        if not path.exists():
            mismatched.append(f"{name}: 文件缺失")
            continue
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        if on_disk != schema:
            mismatched.append(f"{name}: 与模型不一致")

    assert not mismatched, (
        "契约与模型漂移：\n  "
        + "\n  ".join(mismatched)
        + "\n运行 python -m marketops.schema 重新生成并提交。"
    )


def test_contracts_forbid_additional_properties() -> None:
    """契约必须严格 —— 多出来的字段应被拒绝，而不是被静默丢弃。"""
    for name, schema in build_contracts().items():
        assert schema.get("additionalProperties") is False, f"{name} 允许了额外字段"


def test_contracts_are_written_with_lf() -> None:
    """契约文件统一 LF —— 换行差异会让 diff 与哈希在不同平台上不一致。"""
    for name in sorted(EXPECTED_CONTRACTS):
        raw = (CONTRACTS_DIR / f"{name}.schema.json").read_bytes()
        assert b"\r\n" not in raw, f"{name}.schema.json 含 CRLF"


# --- 硬约束必须在契约里可见 ---------------------------------------------------


def test_send_authorized_is_constrained_in_contract() -> None:
    """send_authorized 在契约里必须是不可为 true 的 —— 下游据此判断。

    pydantic 把 ``const: False`` 导出到 schema；这里断言这个约束确实存在，
    否则下游系统会以为它可以被打开。
    """
    contact = build_contracts()["contact-route"]
    properties = contact["properties"]

    assert "send_authorized" in properties
    assert properties["send_authorized"].get("const") is False
    assert properties["research_only"].get("const") is True


def test_opportunity_contract_carries_the_same_safety_flags() -> None:
    """「不发送」不是路线独有的约束 —— 机会记录同样不可发送。"""
    opportunity = build_contracts()["route-opportunity"]["properties"]
    assert opportunity["send_authorized"].get("const") is False
    assert opportunity["research_only"].get("const") is True


# --- 账号 ≠ 路线：契约层面也必须看得出来 ---------------------------------------


def test_route_contract_requires_an_email() -> None:
    """路线必须有字面邮箱 —— 这不是代码里的一个 if，是契约的 required。"""
    contact = build_contracts()["contact-route"]
    assert "email" in contact["required"]


def test_opportunity_contract_has_no_email_and_no_grade() -> None:
    """机会**不是**「字段不全的路线」。

    给它留一个空的 email 列，读的人分不清「邮箱没抓到」与「按定义它没有邮箱」
    —— 前者要重跑，后者不该重跑。给它留一个 grade 列，更糟：等级会被读成
    「这条路线有多好」，而它根本不是路线。
    """
    properties = build_contracts()["route-opportunity"]["properties"]
    assert "email" not in properties
    assert "grade" not in properties
    assert "block_priority" in properties, "机会应当标明它停在哪一级记录块绑定上"


def test_default_route_grade_is_x_not_a_positive_grade() -> None:
    """等级的缺省是 X：没被判过 ≠ 被判成了 A/B/C/D。"""
    grade = build_contracts()["contact-route"]["properties"]["grade"]
    assert grade.get("default") == "X"


def test_venue_relation_contract_is_keyed_by_ids_not_names() -> None:
    """SOP L78：关系由 ``(account_id, venue_asset_id, relation)`` 确定。"""
    properties = build_contracts()["account-venue-relation"]["properties"]
    assert "venue_asset_id" in properties
    assert "venue_name" not in properties


# --- 证据的可溯源字段必须在契约上 ---------------------------------------------


def _deref(schema: dict, node: dict) -> dict:
    """把 ``{"$ref": "#/$defs/X"}`` 解析成 ``$defs`` 里的实际定义。

    pydantic 对枚举字段导出的是 ``$ref``，直接读 ``properties[...]`` 只会拿到
    一个引用字符串 —— 那样断言等于什么都没验证。
    """
    ref = node.get("$ref")
    if not ref:
        return node
    return schema["$defs"][ref.rsplit("/", 1)[-1]]


def test_evidence_contract_lists_source_kinds() -> None:
    """契约里必须能看出「哪些来源算数」—— 下游据此判断证据是否合格。"""
    evidence = build_contracts()["evidence"]
    source_kind = _deref(evidence, evidence["properties"]["source_kind"])

    assert "search_snippet" in source_kind["enum"]
    assert "fetched_html" in source_kind["enum"]
    # SERP snippet 在类型层面就是非法的绑定来源（见 entities/bind.py）。
    assert len(source_kind["enum"]) >= 4


def test_evidence_contract_makes_provenance_mandatory() -> None:
    """一条证据必须能追回**具体字节**，而不只是「某个 URL 当时的某个版本」。

    没有 ``content_sha256``，「这句话来自哪儿」就只有一个 URL 在回答 ——
    而页面是会变的。
    """
    evidence = build_contracts()["evidence"]
    required = set(evidence["required"])
    assert {"source_url", "content_sha256", "fetched_at"} <= required


@pytest.mark.parametrize("name", sorted(EXPECTED_CONTRACTS))
def test_contract_is_valid_json_schema_shape(name: str) -> None:
    schema = build_contracts()[name]
    assert schema["type"] == "object"
    assert "properties" in schema
    assert schema.get("title")
