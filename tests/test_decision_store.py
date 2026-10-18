"""§2：``decision_record`` 的**持久存储**与它旁边那三条边界。

## 这一条修的是哪一种不一致

这一条修的是那段被删掉的 ``_NOT_STORED_NOTE``（``adapters/mcp_server/decisions.py``
里它叫 ``_STORED_NOTE`` 了）逐字承认过的事：

    record_id 是**内容寻址**的（输入 + 后端 + 判定时刻）… 本构建**不持久化**
    DecisionRecord —— 没有任何一处能按这个 id 把它取回来，回执里带的是那条记录
    **本身**，要留着就得调用方自己留。

于是「一条判定发生过」这件事只活在**那一次工具调用的返回值**里。服务一重启，它
就没有了；换一个客户端，它也没有了；而一条判定是**付过费**的（Jev 是外部 API）。
§10 要验收的是「decide 回执 → DB 行 → 服务重启 / 新客户端 → 同一个 decision_id
读回」—— 前半截当时根本不存在。

**判据本身没有跟着那段注释一起改。** 上面引的是那件事的**记述**，不是这条判据的
主人公：本文件测的是工具面的行为，无论那一段注释的措辞将来怎么变。

## 五条边界，各自一条判据

1. **能按 id 精确读回**，而且是在**另一个服务实例**里 —— 见 ``_session()``：每一段
   会话都重新 ``build_server``，作业存储与判定存储是同一个文件。这是本文件里唯一
   能让「持久」这件事被真的测到的形状；一个进程内的服务连着调两次，两次都会通过，
   而那条记录可能根本不在盘上。
2. **同一个调用重放不付第二次钱。** ``record_id`` 含 ``decided_at``，所以同一份
   输入重跑一次算出的是**另一个** id —— 光靠内容寻址挡不住重复付费。幂等键因此
   是 ``(work_item_id, provider, case_id)``：后两段都由输入与**问谁**派生（不含
   时钟），而它在库里是 ``UNIQUE``。重放要能回答「库里那一条是哪一条」。
   ``provider`` 那一段是承重的，见第 5 条。
3. **``evidence_refs`` 必须真的存在，且在**这一件工作**的可引用范围内。今天只校验
   它是不是一个规范 id —— 一条指向别人的账号、甚至指向不存在的证据的引用，会被
   原样写进记录，而那条记录看起来完全正常。
4. **判定不得改变一件已验证的业务事实。** 它是一次咨询的**记录**，不是一个执行
   动作：跑完一条判定之后，那个 work item 的 ``stop_reason`` 与那个账号的证据集
   都必须逐位不变。特别是 **Jev 的输出不是 Evidence** —— 把它写成一条证据，等于
   让一句模型的话变成一条「已抓取的公开文档」。
5. **同一份输入问两个后端 = 两条记录、两次付费**，而同一个后端问第二遍 = 一次
   重放、零次付费。两半都要，因为一个只按 ``(work_item, case_id)`` 去重的实现会
   满足第 2 条而违反 §6 —— 那一节要的正是「在同一个已冻结的 case 上做一次 Jev 与
   Laya 的比较」，而那个实现会把比较的后半截拒成重复付费。
   还有第三半：真付了两次的那一次（两个 worker 同时到达，调用前那次检查两边都过），
   落点必须**报出来**而不是合并 —— 见本文件最后一条，它直接调存储。

## 这一条**不**建通用模型记忆库

记录只能挂在**一件工作**上（``work_item_id`` 是必填、且是真外键）。一条无法归属的
判定记录正是「通用记忆库」的第一块砖：它没有主语，于是谁都可以往里写、谁都可以
从里读。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from marketops.adapters.mcp_server.decisions import decision_handlers
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.decisions import SqliteDecisionRecordStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.config import Settings
from marketops.domain.enums import JobInputKind
from marketops.domain.errors import StoreError
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.fakes import synthetic_dossiers
from tests.mcp_harness import CallOutcome, drive_with_settings, ring_entry, ring_payload, write_accounts
from tests.test_evidence_store_contract import make_evidence
from tests.test_mcp_decision_tools import _Backend

SCENE = "uk-acme-natural-grass"
ACCOUNT = "kent-004"
OTHER = "kent-005"
#: 题面是服务端从冻结 profile 编译的，所以 ``spec_id`` 是**带 profile 前缀的全名**
#: —— ``decide`` 只认编译出来的那几个，不认短名。
SPEC_ID = f"{SCENE}:page_triage"
STATE = "A frozen block of page text about a grounds maintenance contract."


@pytest.fixture
def inputs(settings: Settings) -> Path:
    return write_accounts(settings, "store-2026-09", (ACCOUNT, OTHER))


def _ring(settings: Settings, name: str) -> tuple[TokenRing, str]:
    """一枚有两种角色的令牌。判定那两条工具在研究侧，读回那条两侧都能调。"""
    token, digest = mint("claude-worker-1", [Role.RESEARCHER, Role.CONTROLLER])
    path = settings.data_root / f"{name}-tokens.json"
    path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-worker-1", ["researcher", "controller"]))
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(path), token


def _work_item(settings: Settings) -> str:
    """受理并编译一个作业，返回**第一个**工作单元的 id（就是 ``ACCOUNT`` 那件）。"""
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="store-2026-09",
        idempotency_key="decision-store-v1",
        request_sha256="0" * 64,
    )
    items = store.compile_work_items(
        job.job_id,
        synthetic_dossiers(ACCOUNT, OTHER),
        # 钉住的那一版必须是**一份装得出来的 profile**：判定那条路按钉住的字节编译
        # 题面（``tools.pinned_bundle``），合成的那批字节装载会失败。见
        # ``test_mcp_decision_tools.work_item`` 的同一条注释。
        scene=load_profile(settings).scene_files,
    )
    return items[0].work_item_id


def _records(settings: Settings) -> Callable[[], SqliteDecisionRecordStore]:
    """判定记录的落点工厂。与组合根在 ``wire_decisions`` 里建的是同一个东西。

    工厂而不是实例：``sqlite3`` 的连接默认不允许跨线程使用，而工具处理器可能在
    别的线程上跑。
    """
    return lambda: SqliteDecisionRecordStore(settings.database_path)


def _call(
    settings: Settings,
    *,
    tool: str,
    arguments: dict,
    backend: _Backend,
    ring: TokenRing,
    token: str,
    record_store: bool = True,
) -> CallOutcome:
    """一段**全新的** MCP 会话。每次重新 ``build_server`` —— 见模块 docstring。

    ``record_store=False`` 造一个**没有判定记录落点**的实例。那是一种合法的部署
    （与 ``build_server`` 的 ``jobs=None`` 同一条），而它必须与「有落点、判定判过
    了」分得开。

    ``SessionOutcome.call`` 在**这一轮**没调到那个工具时会抛 ``AssertionError``，
    而握手失败也会走到那里 —— 于是「服务没起来」与「工具没被调到」会得到同一句
    话。先看一眼 ``handshake_error``，让前者的原因（缺令牌、缺后端、构造抛了）
    留在消息里。
    """
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        research=decision_handlers(
            settings=settings,
            advisory=backend,
            records=_records(settings) if record_store else None,
        ),
        calls=((tool, arguments),),
        want_tool_list=False,
        decisions={"stub": backend},
        advisory=backend,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome.call(tool)


def _decide(
    settings: Settings, *, arguments: dict, backend: _Backend, ring: TokenRing, token: str
) -> dict:
    call = _call(
        settings, tool="decide", arguments=arguments, backend=backend, ring=ring, token=token
    )
    assert not call.is_error, call.text
    return call.as_json()


def _record(body: dict) -> dict:
    """一份回执里**属于那条记录**的部分。

    两份回执都带一格 ``replayed``（「这一次调用有没有问过后端」）。它是**这一次
    调用**的事实，不是那条记录的一部分 —— 而「记录本身逐键相同」正是这一条要判
    的东西，所以比较前把它去掉，而不是让它在两边各出现一次、然后靠一个更松的
    比较绕过。
    """
    return {key: value for key, value in body.items() if key != "replayed"}


def _judgment(work_item_id: str, state: str = STATE, refs: list[str] | None = None) -> dict:
    arguments: dict = {"spec_id": SPEC_ID, "state": state, "work_item_id": work_item_id}
    if refs is not None:
        arguments["evidence_refs"] = refs
    return arguments


def _refused(
    settings: Settings, *, arguments: dict, backend: _Backend, ring: TokenRing, token: str
) -> str:
    """调一次判定并要求它被拒，返回那一段拒的理由。"""
    call = _call(
        settings, tool="decide", arguments=arguments, backend=backend, ring=ring, token=token
    )
    assert call.is_error, f"这一条要的是拒绝，拿到的是 {call.text}"
    assert len(backend.calls) == 0, "被拒的那一次不许付钱 —— 校验必须在后端之前"
    return call.text


# ----------------------------------------------------------------------
# 1. 存下来，且换一个服务实例读得回来
# ----------------------------------------------------------------------
def test_a_decision_outlives_the_service_instance_and_reads_back_by_id(
    settings: Settings, inputs: Path
) -> None:
    """§10 逐字要的那条链：decide 回执 → DB 行 → 新客户端 → 同一个 id 读回。"""
    work_item_id = _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "store")

    first = _decide(
        settings,
        arguments=_judgment(work_item_id),
        backend=backend,
        ring=ring,
        token=token,
    )
    # 第二段会话是**另一个服务实例**：新的 build_server、新的连接、新的进程内状态。
    again = _call(
        settings,
        tool="get_decision",
        arguments={"record_id": first["record_id"]},
        backend=backend,
        ring=ring,
        token=token,
    ).as_json()

    assert len(backend.calls) == 1, "读回一条记录不是一次新的判定"
    assert again["record_id"] == first["record_id"]
    assert again["work_item_id"] == work_item_id
    assert again["subject_ref"] == ACCOUNT
    assert again["decided_at"] == first["decided_at"]
    assert again["provider"] == "stub"
    assert again["spec_id"] == SPEC_ID
    assert _record(again) == _record(first), (
        "读回来的那条必须与当时写下去的那条逐键相同 —— 包括 recorded_at：落盘时刻"
        "是那一行的事实，换个读法不该变"
    )
    assert again["replayed"] is False, "读回一条已经存在的记录不是一次重放（没有输入被重算）"


# ----------------------------------------------------------------------
# 2. 重放：同一个调用不付第二次钱
# ----------------------------------------------------------------------
def test_replaying_the_same_invocation_does_not_call_the_backend_twice(
    settings: Settings, inputs: Path
) -> None:
    """``record_id`` 含 ``decided_at``，所以内容寻址**挡不住**重复付费。

    幂等键是 ``(work_item_id, provider, case_id)`` —— 后两段由输入与问谁派生、
    不含时钟。第二次调用必须**在后端之前**就短路：判据是 ``backend.calls`` 的长度，
    不是返回值里那句自述（一个每次都重试的实现会让自述照旧全绿）。
    """
    work_item_id = _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "replay")
    arguments = _judgment(work_item_id)

    first = _decide(settings, arguments=arguments, backend=backend, ring=ring, token=token)
    second = _decide(settings, arguments=arguments, backend=backend, ring=ring, token=token)

    assert len(backend.calls) == 1, "同一个输入判两次就是付两次费（§6：Jev ≤1 per Account）"
    assert second["record_id"] == first["record_id"], "重放要拿回**那一条**，不是新的一条"
    assert second["replayed"] is True
    assert first["replayed"] is False


def test_a_different_input_under_the_same_work_item_is_a_new_record(
    settings: Settings, inputs: Path
) -> None:
    """幂等键里有 ``case_id``，不是 ``work_item_id`` 一个人。

    没有这一格，上面那条会被一个「按 work item 去重」的实现满足 —— 而那个实现会把
    **换过输入**的第二次判定读成重放，于是研究者改了题面或换了证据之后拿到的是上
    一条的答案，且记录上看起来完全正常。
    """
    work_item_id = _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "distinct")

    first = _decide(
        settings,
        arguments=_judgment(work_item_id, "The first frozen block of page text."),
        backend=backend,
        ring=ring,
        token=token,
    )
    second = _decide(
        settings,
        arguments=_judgment(work_item_id, "A different frozen block of page text."),
        backend=backend,
        ring=ring,
        token=token,
    )

    assert len(backend.calls) == 2
    assert second["case_id"] != first["case_id"]
    assert second["record_id"] != first["record_id"]
    assert second["replayed"] is False


def test_the_same_case_asked_of_two_backends_is_two_records_and_two_payments(
    settings: Settings, inputs: Path
) -> None:
    """幂等键三段里的 ``provider`` 是**承重**的，不是凑数。

    同一件工作单元、同一份输入、**两个不同的后端** = 两次判定、两次付费、两条记录。
    ``case_id`` 由输入派生、**不含 provider**，所以少了那一段，第二次会被读成「同一个
    问题问了两次」而整次拒掉。

    那不是一次谨慎，是把一条需求从产品上删掉：§6 要的正是「在同一个已冻结的
    DecisionSpec / 证据 case 上做一次 Jev 与 Laya 的比较」。合并两条更坏 —— 一份记录
    只能有一个 ``provider``，选哪一个都是把另一个后端的话记在了别人名下。

    同一条判据的另一半在同一个测试里跑：**同一个**后端再问一遍，仍然是一次重放、
    零次付费。只测一半会让「干脆不做幂等」也全绿。
    """
    work_item_id = _work_item(settings)
    jev = _Backend("jev")
    laya = _Backend("laya")
    ring, token = _ring(settings, "two-backends")
    arguments = _judgment(work_item_id)

    first = _decide(settings, arguments=arguments, backend=jev, ring=ring, token=token)
    second = _decide(settings, arguments=arguments, backend=laya, ring=ring, token=token)

    assert len(jev.calls) == 1 and len(laya.calls) == 1, "两个后端各判一次，各付一次"
    assert second["case_id"] == first["case_id"], "case_id 由输入派生，两个后端看的是同一个"
    assert second["record_id"] != first["record_id"], "配方里含 provider，两条记录是两条"
    assert second["provider"] == "laya" and first["provider"] == "jev"
    assert second["replayed"] is False, "换后端是另一次判定，不是重放"

    # 另一半：同一个后端问第二遍仍然是重放，而刚才那次换后端没有把这条路弄坏。
    again = _decide(settings, arguments=arguments, backend=jev, ring=ring, token=token)
    assert len(jev.calls) == 1, "换过后端之后，同一个后端问第二遍照样不许再付一次"
    assert again["replayed"] is True
    assert again["record_id"] == first["record_id"]

    # 三条记录各读各的，没有一条盖掉另一条。
    for body in (first, second):
        stored = _call(
            settings,
            tool="get_decision",
            arguments={"record_id": body["record_id"]},
            backend=jev,
            ring=ring,
            token=token,
        ).as_json()
        assert stored["provider"] == body["provider"]
        assert stored["record_id"] == body["record_id"]


# ----------------------------------------------------------------------
# 3. 证据引用必须存在、且在范围内
# ----------------------------------------------------------------------
def test_a_reference_to_evidence_that_does_not_exist_is_refused(
    settings: Settings, inputs: Path
) -> None:
    """一条解不开的引用会让「任何结论都能追到一条已抓取的文档」变成一句空话。"""
    work_item_id = _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "missing-ref")

    reason = _refused(
        settings,
        arguments=_judgment(work_item_id, refs=["no-such-evidence-anywhere"]),
        backend=backend,
        ring=ring,
        token=token,
    )
    assert "no-such-evidence-anywhere" in reason


def test_a_reference_to_another_accounts_evidence_is_refused(
    settings: Settings, inputs: Path
) -> None:
    """范围是**这一件工作**的可引用集，不是「库里所有证据」。

    没有这一条，一个 worker 可以引用另一个账号的证据，而记录上只能看出「它引了
    两条证据」。§8 要的并发隔离里，这一格正是「B 的判定不得引 A 的证据」。
    """
    work_item_id = _work_item(settings)
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id="other-account-evidence", account_id=OTHER)
    )
    backend = _Backend("stub")
    ring, token = _ring(settings, "cross-ref")

    reason = _refused(
        settings,
        arguments=_judgment(work_item_id, refs=["other-account-evidence"]),
        backend=backend,
        ring=ring,
        token=token,
    )
    assert OTHER in reason, "拒的理由要点名那条引用属于谁，否则读的人只能猜"
    assert ACCOUNT in reason, "也要点名这件工作的主语是谁"


def test_evidence_in_scope_is_accepted_and_recorded_verbatim(
    settings: Settings, inputs: Path
) -> None:
    """正对照。没有它，上面两条「拒绝了」会被一个恒拒绝的实现满足。"""
    work_item_id = _work_item(settings)
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id="in-scope-evidence", account_id=ACCOUNT)
    )
    backend = _Backend("stub")
    ring, token = _ring(settings, "in-scope")

    body = _decide(
        settings,
        arguments=_judgment(work_item_id, refs=["in-scope-evidence"]),
        backend=backend,
        ring=ring,
        token=token,
    )

    assert body["evidence_refs"] == ["in-scope-evidence"]
    assert len(backend.calls) == 1


# ----------------------------------------------------------------------
# 4. 判定是记录，不是动作
# ----------------------------------------------------------------------
def test_a_decision_changes_no_verified_business_fact(
    settings: Settings, inputs: Path
) -> None:
    """跑一条判定之后：停止理由与证据集**逐位不变**，而 Jev 的输出不是 Evidence。

    这一条挡的是两类混淆。第一类是把「问了一个模型」当成「做了一件事」—— 判定若
    改变了 ``stop_reason``，一个 worker 就能靠调用 ``decide`` 把工作单元标成做完。
    第二类是把模型的话写成一条证据：``Evidence`` 的每一行都自称「一条已抓取的公开
    文档」（``source_url`` + ``content_sha256`` + ``fetched_at``），而一条 Jev 的
    输出没有这三样里的任何一样。
    """
    work_item_id = _work_item(settings)
    evidence = SqliteEvidenceStore(settings.database_path)
    evidence.put_evidence(make_evidence(evidence_id="before-evidence", account_id=ACCOUNT))
    backend = _Backend("stub")
    ring, token = _ring(settings, "no-side-effect")

    before = [row.evidence_id for row in evidence.list_evidence(ACCOUNT)]
    _decide(
        settings,
        arguments=_judgment(work_item_id, refs=["before-evidence"]),
        backend=backend,
        ring=ring,
        token=token,
    )

    jobs = SqliteJobStore(settings.database_path)
    item = jobs.get_work_item(work_item_id)
    assert item is not None
    assert item.stop_reason is None, "判定不得把工作标成做完"
    assert [row.evidence_id for row in evidence.list_evidence(ACCOUNT)] == before, (
        "判定不得往证据表里写一行 —— Jev 的输出不是一条已抓取的公开文档"
    )


# ----------------------------------------------------------------------
# 5. 归属是结构性的
# ----------------------------------------------------------------------
def test_a_record_cannot_be_stored_without_a_real_work_item(settings: Settings) -> None:
    """``work_item_id`` 必须是真外键 —— 一条无法归属的判定就是通用记忆库的第一块砖。

    真外键而不是「写之前查一下」：查一下的版本在并发下会让两个写者都通过，而且
    它对**任何**写者都只是自觉。这一条判的是库自己会不会拒。
    """
    store = SqliteDecisionRecordStore(settings.database_path)
    with pytest.raises(StoreError) as raised:
        store.put_decision(
            "no-such-work-item", {"record_id": "r1", "case_id": "c1", "provider": "jev"}
        )
    assert "no-such-work-item" in str(raised.value)


def test_an_unknown_work_item_is_refused_before_the_backend_is_called(
    settings: Settings, inputs: Path
) -> None:
    """工具面也要在**付费调用之前**拒 —— 库会拒，但那时钱已经花掉了。"""
    _work_item(settings)  # 让库存在，且证明拒绝不是因为库不存在
    backend = _Backend("stub")
    ring, token = _ring(settings, "unknown-item")

    reason = _refused(
        settings,
        arguments=_judgment("no-such-work-item"),
        backend=backend,
        ring=ring,
        token=token,
    )
    assert "no-such-work-item" in reason


def test_a_judgment_without_a_work_item_is_refused_before_the_backend_is_called(
    settings: Settings, inputs: Path
) -> None:
    """归属是**必填**的，没有「不给就退化成一条无主记录」这条路。"""
    _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "no-item")

    call = _call(
        settings,
        tool="decide",
        arguments={"spec_id": SPEC_ID, "state": STATE},
        backend=backend,
        ring=ring,
        token=token,
    )

    assert call.is_error, "缺 work_item_id 必须是一次拒绝"
    assert len(backend.calls) == 0


def test_an_instance_with_no_record_store_does_not_pay_for_a_judgment(
    settings: Settings, inputs: Path
) -> None:
    """没有落点时**拒绝**，而不是「判了但没记下来」。

    这一条挡的是最容易被顺手做掉的那个折中：让判定照常发生、只是不落盘。那样一条
    付过费的记录会在服务重启或换个客户端之后消失，而这一次调用在回执上看起来与
    一次成功的判定完全一样 —— 那正是这一整节要修的那个缺口，只是换了个位置。

    与 ``build_server`` 的 ``jobs=None`` 同一条判据：**没有的入口优于有入口加一条
    注意事项**。
    """
    work_item_id = _work_item(settings)
    backend = _Backend("stub")
    ring, token = _ring(settings, "no-store")

    call = _call(
        settings,
        tool="decide",
        arguments=_judgment(work_item_id),
        backend=backend,
        ring=ring,
        token=token,
        record_store=False,
    )

    assert call.is_error
    assert "没有判定记录的落点" in call.text
    assert len(backend.calls) == 0, "注定存不下来的判定不许先付一次费"


# ----------------------------------------------------------------------
# 6. 真的付了两次的那一次：报出来，不合并
# ----------------------------------------------------------------------
def test_a_second_payment_for_the_same_case_is_reported_and_not_merged(
    settings: Settings, inputs: Path
) -> None:
    """直接调存储，因为这一格**只能在存储层**被造出来。

    工具面的路径走不到这里：``decide`` 在付费之前就按幂等键查一次，命中即短路。会
    走到这里的只有**两个 worker 同时到达** —— 那次检查两边都会通过，于是后端真的被
    调了两次、钱真的付了两次，而两条记录的 ``record_id`` 因为 ``decided_at`` 不同而
    不同，主键拦不住。

    落点必须**报出来**，不许合并：两条记录里哪一条算数没有诚实的答案。合并会让账上
    少一笔 —— 而这一整节的题目就是「付过费的判定必须留下痕迹」。
    """
    store = SqliteDecisionRecordStore(settings.database_path)
    work_item_id = _work_item(settings)
    base = {"case_id": "case-paid-twice", "provider": "jev", "evidence_refs": []}

    store.put_decision(work_item_id, {**base, "record_id": "first-payment"})
    with pytest.raises(StoreError) as caught:
        store.put_decision(work_item_id, {**base, "record_id": "second-payment"})

    message = str(caught.value)
    assert "first-payment" in message and "second-payment" in message, (
        "两条都要点名 —— 只说『重复了』，读的人无从找起是哪两条"
    )
    assert "两次" in message, "要把成因说出来（两个 worker 同时到达），否则它会被读成一次笔误"
    stored = store.decision_for_case(work_item_id, "jev", "case-paid-twice")
    assert stored is not None and stored["record_id"] == "first-payment", (
        "冲突不许覆盖：先写的那一条原样留着，第二条走的是报错这条路"
    )
