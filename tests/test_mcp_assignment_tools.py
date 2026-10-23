"""Phase G3 的两个研究侧工具：``get_assignment`` 与 ``submit_findings``（``MCP_SERVICE.md`` §3）。

## 为什么这两个工具与 G2 那两条**不在**同一个文件里

它们不是同一张表：``submit_research`` / ``get_job`` 在目录里是 ``_CONTROL``，
这两条是 ``_RESEARCH``。用同一个文件会让「这份用例铸的是哪个角色的令牌」变成
一个读文件时要一路找下去的问题，而铸错角色的那一刻，用例会以**权限**的方式
红，看起来像被测的行为坏了。所以：文件按角色分，令牌铸在文件顶部。

## 脚手架准备的是**落点**，调用走的是**工具**

派发到执行器 这一条在工具面上现在是有的（``submit_research`` 受理那一刻、
``control_job(action=resume)`` 恢复那一刻，见 ``tests/test_mcp_job_tools.py``）。
但这个文件仍然用 ``SqliteJobStore`` 摆「已派发」：它要的是一个**指定的**
``dispatch_task_id`` 与一个**指定的** holder，而走工具面就得先铸一个 controller
令牌、再写一份账号清单，摆出来的东西却没多测到任何一样。三个动词
（``compile_work_items`` / ``map_execution`` / ``lease``）各自有自己的判据
（``tests/test_job_contract.py``），这里不重复测它们，只用它们。

**领活是唯一一处不靠脚手架的例子** —— 它就是被测对象（下面第三条判据）。

## 二十六条判据，每一条挡的都是一件**不报错**的事

| 判据 | 它挡的失败 |
|---|---|
| 领得到自己的 assignment | 通路本身 |
| 别人的 assignment 读不到 | 一个 researcher 读走另一个的任务 |
| 已派发、无人持着时**当场领活** | 「已派发」被读成「有人在跑」；以及「没人能领，所以永远没人持着」 |
| 作业不放行时领不到活 | 暂停的作业照样被人领走 |
| 没有 ``ExecutionRef`` 时按 §6 拒绝 | 「绕过派发 直接开一个 worker」在服务面上被挡下 |
| 作业取消之后持有者仍读得到 | 一次取消把一件在跑的工作变成**读不到**（租约并没有被谁收回） |
| 未知 work item 拒绝 | 静默返回一个空壳 |
| ``tool_rights`` 与 ``visible_tools`` 逐字一致 | assignment 承诺一个工具、中间件拒绝它 |
| 别人持有这条租约时结算被拒 | 有名无实的 attempt/lease protection |
| 陈旧的 attempt id 被拒（且说的不是「属于别人」） | stale attempts overwrite later versions |
| 未知 ``stop_reason`` 列出闭集 | 拼错的停止理由静静落盘 |
| 没有证据的结论被拒 | 一条挂不上任何东西的结论 |
| 引用一条**不存在**的证据被拒，且整份被拒 | 一条编出来的依据与一条有依据的结论长得一样 |
| ``replayed`` 可见 + 零产出与未结算分得开 | 「又写了一遍」与「没有写」不可区分 |
| 结算之后看板**真的**被回传 | ``transition`` 有实现、没调用者 —— 看板上每件任务永远停在 ``todo`` |
| 没接执行器时照常结算并说清 | 回传失败被报成结算失败，于是调用方去重发一次**已经落库**的产出 |
| 钉住的版本与此刻生效的版本都报出来 | 「这件工作按哪一份规则在跑」只有一个答案，而那一个是错的 |
| 两份不一致时 ``pinned_is_effective`` 为 ``False`` | 编译之后 profile 被改过，而回执读起来跟没事一样 |
| 没有钉住的版本时拒绝领活 | 一件按**另一份**规则跑完、却没人知道的工作 |
| 交出判定题面的 ``spec_id`` | 判定**可达而无人指路**：工具齐全、台账上 ``decision_record`` 恒零行，而那零行与「没有声明过判定」分不开（§F） |
| 交出的选项 key 就是 ``decide`` 校验的那一份 | 研究员照回执写下的那句话，永远对不上任何一条记录 |
| 没声明判定 ⇒ 空表；声明了却编译不过 ⇒ 整次拒绝 | 「这份 profile 没声明过判定」与「有人写错了选项」在回执上长得一样 |
| 已完全结算的作业仍读 ``queued``，且回执自己说了为什么 | 一格「看起来像进度、其实答的是另一个问题」的字段 —— 读的人在那之前就已经把它误读完了（§N.1） |
| 交出的步骤就是收下这一步的那条工具认的那一份 | 缺口 A：「没人把这一步交出去」与「交出去的那一步不是工具认的」在报告上同形（§B16 关闭条件 7） |
| 交出的步骤跟着**声明**走（撤掉一块声明，那两条路就没了） | 一张写死在适配器里的步骤表：它过得了每一条「值在闭集里」的判据，却与声明分家 |
| 说不出这一轮的步骤时**整次拒绝** | 「这份 Scene 说不出计划」被降级成一块空步骤表，而它与「这一轮没有步骤」同形 |

最后一条的主语是 ``get_job`` 而不是这个文件的两条工具。它住在这里，是因为它要的
是**一次真的结算**（``_dispatched_job`` + ``submit_findings``），而那套脚手架在这个
文件里；搬到 ``test_mcp_job_tools.py`` 会把它再抄一遍，而抄出来的那份一旦与这份漂开，
红的人会去查收据而不是查自己抄的那份。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from marketops.adapters.profile_loader import load_profile
from marketops.application.decision_specs import closed_choices, compile_specs
from marketops.application.dispatch import link_labels
from marketops.adapters.sqlite.db import session, write_transaction
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.config import Settings
from marketops.domain.enums import JobInputKind, JobState, StopReason
from marketops.domain.profile import SceneFile, SceneFiles
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import sha256_text
from marketops.security.catalog import Role, Principal, visible_tools
from marketops.security.tokens import TokenRing, mint
from tests.conftest import DEFAULT_PROFILE, PROFILES_DIR
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence

#: 两个主体：一个领活并交活，一个只负责证明「它领不走别人的」。
WORKER = "worker-a"
OTHER = "worker-b"


def _scene(settings: Settings) -> SceneFiles:
    """手搓编译时读的那批字节 —— 与工具面读的是**同一份**。

    ``_compiled_job`` 摆的是「已经编译好的一批工作单元」，而工具面（``submit_research``
    / ``resume``）摆的是同一样东西、只是顺路读了盘上此刻生效的那一批。两者用同一批
    字节时回执的 ``guidance.pinned_is_effective`` 才是 ``True`` —— 那是正常路径。
    冻着一批**别的**字节的那种情形另有判据（``test_...a_different_version...``），
    它要的是一条真的分歧，不是所有用例都分歧。
    """
    return load_profile(settings).scene_files


def _other_scene(settings: Settings) -> SceneFiles:
    """盘上那一份**被别人改过之后**的字节 —— 与 ``_scene`` 同源，只是多了一条注释。

    摆「编译之后有人改过 profile」这一格时，``_compiled_job`` 收到的必须是**一份真
    的、装载得出来的**规则：

    * 换成随便一批别的字节（``synthetic_scene_files``）也行，但那样测的其实是另一
      件事 —— 钉住的字节**装载不出来**时该不该拒绝。那条判据另有其格（见
      ``test_a_pinned_version_whose_bytes_do_not_load_cannot_be_claimed``），而它要造的是
      一个连 ``profiles/<id>/`` 都不像的快照。这一格要的是「盘上换了一份**好**规则，
      在飞的那件仍然按老的跑」，所以钉住的那一份也得是一份好规则。
    * 内容必须**真的不同**：``SceneFiles.version`` 是那批字节的摘要，改一条注释就够
      了 —— 版本变了、而它照样装载得出来（注释不进规则）。
    """
    return SceneFiles(
        entries=tuple(
            SceneFile(
                path=entry.path,
                content=(
                    entry.content + b"\n# the profile was edited after this job was compiled\n"
                    if entry.path == "profile.yaml"
                    else entry.content
                ),
            )
            for entry in _scene(settings).entries
        )
    )


#: 结论挂的那条证据引用。它**真的存在** —— 由下面的 ``_filed_evidence`` 写进本
#: 用例的证据库。
#:
#: 这里此前是一条形状合法的**假** id，理由是「端口上没有按 id 取的入口，所以只能
#: 校验形状」。那句话在 ``EvidenceStore.get_evidence`` 与 ``application/evidence_refs``
#: 落地之后过期了：``submit_findings`` 现在逐条查存在性，摆一条假证据会让这一整个
#: 文件以「引用解不开」的方式红 —— 而红的理由与被测的行为毫无关系。
EVIDENCE = sha256_text("page-1")[:32]


@pytest.fixture(autouse=True)
def _filed_evidence(settings: Settings) -> None:
    """把 ``EVIDENCE`` 那一条真的写进这个用例的证据库。

    走的是**同一个**存储（``SqliteEvidenceStore``，同一份 ``database_path``），与
    ``interfaces/mcp/__main__.py`` 在生产里接的是同一个实现、同一个位置。换成内存
    实现的话，「引用查得到」这件事只在测试里成立 —— 而那正是这一条要挡的。

    autouse 而不是显式依赖：这个文件里每一次交活都要它，而漏掉一处就会以
    「引用解不开」红 —— 一个与被测行为无关的理由。而这个文件本身**不是**在测
    「证据怎么进来的」，那条路径的判据在 ``tests/test_evidence_ingest.py``。
    """
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id=EVIDENCE, account_id="acme-devon-01")
    )

SCENE = "uk-acme-natural-grass"
SUBJECT = "acme-devon-01"


@pytest.fixture
def store_factory(settings: Settings):
    return lambda: SqliteJobStore(settings.database_path)


@pytest.fixture
def ledger_factory(settings: Settings):
    return lambda: SqliteBudgetLedger(settings.database_path)


def _ring_file(settings: Settings, digest: str, name: str, role: str):
    path = settings.data_root / f"test-tokens-{name}.json"
    path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, name, [role]))
        ),
        encoding="utf-8",
    )
    return path


def _session(
    settings: Settings, store_factory, ledger_factory, *, name: str, role: Role, dispatcher=None
):
    """一个会话驱动器。每次调用都是一段**全新的**会话（含一次新的握手）。

    ``dispatcher`` 缺省为 ``None``，与 ``drive_with_settings`` 的缺省一致：「没有执行器
    的部署」在这个文件里是主流，而且它自己就是一个要被测的对象（结算那张判据表
    的最后一行）—— 只有回传看板那条判据需要一个接了执行器的会话。
    """
    token, digest = mint(name, [role])
    ring = TokenRing.from_file(_ring_file(settings, digest, name, role.value))

    def call(tool: str, arguments: dict | None = None):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
            dispatcher=dispatcher,
            jobs=store_factory,
            ledger_factory=ledger_factory,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


@pytest.fixture
def worker(settings, store_factory, ledger_factory):
    return _session(settings, store_factory, ledger_factory, name=WORKER, role=Role.RESEARCHER)


@pytest.fixture
def outside() -> FakeDispatcher:
    """记账的外部执行器。回传看板那条判据读的是它的 ``transitions``。"""
    return FakeDispatcher()


@pytest.fixture
def wired(settings, store_factory, ledger_factory, outside):
    """一个**接了执行器**的 researcher 会话 —— 工具面唯一会走到回传的那种部署。

    单独一个 fixture 而不是给 ``worker`` 加参数：那个缺省（没执行器）是其余所有
    用例的被测对象，把执行器变成这里的常态会让它们测的东西悄悄变一样。
    """
    return _session(
        settings,
        store_factory,
        ledger_factory,
        name=WORKER,
        role=Role.RESEARCHER,
        dispatcher=outside,
    )


@pytest.fixture
def other(settings, store_factory, ledger_factory):
    return _session(settings, store_factory, ledger_factory, name=OTHER, role=Role.RESEARCHER)


def _settings_with(tmp_path: Path, mutate) -> Settings:
    """复制真实 profile、只按 ``mutate`` 改一处，再走**真实装载器**接线的一份 Settings。

    走真实装载器与真实路径是有意的：一个只在手搭的假 bundle 上成立的判据，接线到
    服务面上会碎 —— 而碎的那一次是在花了额度之后（``tests/test_discovery_openweb.py``
    的 ``_bundle_with_search`` 用的是同一条理由）。

    返回的是**整份 ``Settings``**而不是一个 bundle：``_assignment`` 读的是盘上此刻
    这一份（``load_profile(settings)``），而存储与账本也要落到同一份临时目录里，
    否则摆出来的那条工作与回执里的 Scene 会来自两个不同的根。
    """
    profiles_dir = tmp_path / "profiles"
    target = profiles_dir / DEFAULT_PROFILE
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, target)

    profile_yaml = target / "profile.yaml"
    document = yaml.safe_load(profile_yaml.read_text(encoding="utf-8"))
    mutate(document)
    profile_yaml.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    resolved = Settings(
        data_root=tmp_path / "runtime", profiles_dir=profiles_dir, profile=DEFAULT_PROFILE
    )
    resolved.ensure_data_dirs()
    return resolved


def _drop_decisions(document: dict) -> None:
    """整个 ``decisions`` 块不写 —— ``declared_decisions`` 为 ``None``，合法的状态。"""
    document.pop("decisions", None)


def _add_a_bogus_option(document: dict) -> None:
    """给 ``page_triage`` 多声明一个闭集外的选项。

    这正是 ``compile_spec`` 那条对齐断言挡的形态：多一条描述，是一个永远不会被
    选中的选项，而它在记录上看不出来。这里用它来摆「声明了判定、但编译不过」。
    """
    document["decisions"]["page_triage"]["options"]["bogus"] = "A band nobody declared."


def _session_for(settings: Settings):
    """一份临时 profile 上的 researcher 会话 —— ``_session`` 的落点跟着走。"""
    return _session(
        settings,
        lambda: SqliteJobStore(settings.database_path),
        lambda: SqliteBudgetLedger(settings.database_path),
        name=WORKER,
        role=Role.RESEARCHER,
    )


@pytest.fixture
def controller(settings, store_factory, ledger_factory):
    return _session(settings, store_factory, ledger_factory, name="cli", role=Role.CONTROLLER)


# ----------------------------------------------------------------------
# 落点：一个已派发、已领租约的 work item
# ----------------------------------------------------------------------
def _compiled_job(
    settings: Settings, store_factory, *, dispatch: bool, pinned: SceneFiles | None = None
):
    """摆好「受理 → 编译（→ 派发）」并返回 ``(store, job_id, work_item_id)``。

    ``map_execution`` 要求作业处于放行派发的状态（``queued``）；``dispatch=False``
    时**没有** ``ExecutionRef``，那正是 §6 那条边界要挡住的形态 —— 它也是这两条
    路径之间唯一的一格差别。

    ``pinned`` 换掉编译时读的那批字节，缺省是与盘上此刻**相同**的那一份。换成
    别的值摆的是「编译之后有人改过 profile」—— 「钉住」这个机制唯一要挡的失败，
    而它在正常路径上不会自己出现。
    """
    store = store_factory()
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key="discovery-v1-arm-b",
        request_sha256=sha256_text("devon-2026-09"),
    )
    (item,) = store.compile_work_items(
        job.job_id, synthetic_dossiers(SUBJECT), scene=pinned or _scene(settings)
    )
    if dispatch:
        store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")
    return store, job.job_id, item.work_item_id


def _dispatched_job(settings: Settings, store_factory, *, holder: str = WORKER):
    """摆好「受理 → 编译 → 派发 → 领租约」并返回 ``(work_item_id, attempt_id)``。

    ``lease`` 要求**已经**有 ``ExecutionRef``，所以第三条与第四条不能换位 ——
    四步都就位之后这个 work item 才是一个 assignment。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)
    attempt = store.lease(work_item_id, holder=holder)
    return work_item_id, attempt.attempt_id


# ----------------------------------------------------------------------
# get_assignment
# ----------------------------------------------------------------------
def test_a_worker_can_read_its_own_assignment(settings, store_factory, worker) -> None:
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    assert body["work_item"]["work_item_id"] == work_item_id
    assert body["work_item"]["subject_ref"] == SUBJECT
    assert body["attempt"]["attempt_id"] == attempt_id
    assert body["attempt"]["holder"] == WORKER
    assert body["execution"]["dispatch_task_id"] == "dispatch-task-0001"
    assert body["job"]["scene_id"] == SCENE
    assert body["scene"]["scene_id"] == SCENE


def test_the_assignment_carries_the_frozen_scene_not_the_request(
    settings, store_factory, worker, controller
) -> None:
    """场景那一格来自**装载层**，不是这里另算的一遍。

    ``summary`` 与 ``get_scene`` 给的是同一份对象 —— 各算一遍的后果不是文案不
    一致，而是「assignment 说这个场景没有未知规则」而控制面说有三条，两边的
    读者各自照自己那份行事。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    assignment = worker("get_assignment", {"work_item_id": work_item_id}).as_json()
    # get_scene 是控制面工具，所以这里换一个 controller 会话去读同一份装载结果
    # —— 两个角色各读一次，才说明「同一份」不是同一个会话里的同一个对象。
    scene = controller("get_scene", {}).as_json()

    assert assignment["scene"]["summary"] == scene["summary"]
    assert assignment["scene"]["unknowns"] == scene["unknowns"]
    assert assignment["scene"]["has_unknown_rules"] == scene["has_unknown_rules"]


def test_the_assignment_reports_the_pinned_version_and_the_effective_one(
    settings, store_factory, worker
) -> None:
    """回执同时给出**两个**版本，以及服务端算好的那一格比较。

    §6 要的是 Running tasks retain their pinned **guidance**，而 ``scene`` 块装的
    是**此刻**从盘上装载的那一份。两者在编译之后没人动过 profile 时相等 —— 这条
    判据钉住的就是「相等时它如实说相等」，下面那条钉住「不相等时它不撒谎」。

    为什么比较要在服务端算：两个版本字符串一样长、一样是十六进制，读的人拿
    肉眼比是**比得出来**的，而写脚本的人会各自写一份比较 —— 其中一份写成
    ``in`` 或者大小写不敏感的那天，报告上不会有任何一处变红。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)
    effective = _scene(settings).version

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    guidance = body["guidance"]
    assert guidance["pinned"] == effective
    assert guidance["effective"] == effective
    assert guidance["pinned_is_effective"] is True
    # ``effective`` 要**真的**是盘上那一份，不是回执里另编的一串 —— 上面三行
    # 在一个把两者都写成 ``pinned`` 的实现上一样全绿，而那样一来「此刻生效的是
    # 哪一份」就没有人回答了。
    assert guidance["effective"] == _scene(settings).version
    assert guidance["pinned_at"]


def test_a_work_item_pinned_to_another_version_says_the_two_disagree(
    settings, store_factory, worker
) -> None:
    """编译之后 profile 被改过时，回执**说出**这件事，而不是悄悄按新的跑。

    这是这个机制唯一要挡的失败：一件正在飞的工作在一份**不同的**规则下继续跑，
    而它自己、以及任何读报告的人都不知道。

    这条判据断的是**「它没有假装两份是同一份」**：``pinned`` 报旧的那个、
    ``effective`` 报新的那个、布尔为 ``False``。三者少任何一个，读的人都只能
    看到一个版本 —— 而那一个会让他以为那就是全部。

    同时断**旧的那一份还读得回来**：``content_from`` 是 ``"snapshot"``、
    ``files`` 报的就是当时那批字节。只说「不一致」而说不出「那当时是什么」的
    实现，把这件工作变成一件谁也复原不了的事 —— 而它在这一格的前三条断言上
    全绿。

    钉住的那一份由 ``_other_scene`` 造：**一份真的、装载得出来的**规则，只是与盘上
    这一份不同。这里不换成一批装载不出来的字节 —— 那测的是装载失败该怎么处置，
    是另一格的事。
    """
    pinned = _other_scene(settings)
    store, _, work_item_id = _compiled_job(
        settings, store_factory, dispatch=True, pinned=pinned
    )
    store.lease(work_item_id, holder=WORKER)

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    guidance = body["guidance"]
    assert guidance["pinned"] == pinned.version
    assert guidance["effective"] != pinned.version
    assert guidance["pinned_is_effective"] is False
    # 而且要**说出**新的那一份是哪个：一个「反正不相等」的实现（比如把 effective
    # 原样抄成 pinned 的反面、或者干脆写空串）在这里会绿，而它答不了「现在生效的是
    # 哪一份」—— 那是这一格存在的全部理由。
    assert guidance["effective"] == _scene(settings).version
    assert guidance["content_from"] == "snapshot"
    # 逐文件比对，而不是只数个数：一个「报了文件名、内容却是现在这一份的」实现
    # 会把摘要报成 effective 的那些，而它在前几条上全绿。
    assert guidance["files"] == [
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "bytes": len(entry.content),
        }
        for entry in pinned.entries
    ]
    assert guidance["files"] != [
        {"path": entry.path, "sha256": entry.sha256, "bytes": len(entry.content)}
        for entry in _scene(settings).entries
    ]


def test_a_historical_row_whose_bytes_were_never_kept_is_still_servable(
    settings, store_factory, worker
) -> None:
    """**正对照**：钉住的版本没有留字节，但它与盘上此刻逐字节相同 ⇒ 照常发放。

    这一格不是编出来的边界，它是 ``marketops-runtime`` 那个库里**真实的样子**：
    ``scene_snapshot`` 是这次收口才有的表，而 ``work_item_guidance`` 回填进新表的
    那几行只有版本、没有内容。它们钉的版本与盘上此刻算出来的**恰好相同**
    （2026-09-23 实测 ``same=5 diff=0``），所以那批工作的规则是拿得到的。

    这条判据挡的是一个「看到没有内容就一律拒绝」的实现 —— 它会把这批工作全部
    判死，而它读起来像是「我们很严格」。严格不是目的：**说不清**才是要挡的那件事，
    而这里说得清（版本号是内容的摘要，相等就是同一批字节），所以说得出
    ``content_from: "effective"``。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)
    store.lease(work_item_id, holder=WORKER)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM scene_snapshot")

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    guidance = body["guidance"]
    assert guidance["pinned_is_effective"] is True
    assert guidance["content_from"] == "effective"
    assert guidance["files"] == [
        {"path": entry.path, "sha256": entry.sha256, "bytes": len(entry.content)}
        for entry in _scene(settings).entries
    ]


def test_a_pinned_version_whose_bytes_are_gone_cannot_be_claimed(
    settings, store_factory, worker
) -> None:
    """两头落空 ⇒ 不发放：快照里没有那批字节，盘上这一份又不是它。

    「钉住」这句话的全部内容就是**说得清当时按什么规则跑**。这一格两个来源都没有
    —— 一个 work item 会被派出去做一件没有人能说清规则的事，而它在报告上与一件
    正常的工作长得完全一样。拒绝是唯一诚实的处置。

    造法与真实历史一致：让 ``scene_snapshot`` 那一行不在（更早的进程写下的库），
    而钉的版本是一个与盘上不同的值（编译之后有人改过 profile）。
    """
    pinned = synthetic_scene_files(tag="lost-scene")
    store, _, work_item_id = _compiled_job(
        settings, store_factory, dispatch=True, pinned=pinned
    )
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM scene_snapshot")

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert pinned.version in outcome.text
    # 两个版本都要在报文里：只说「说不清」会让处置的人不知道去哪一边找。
    assert _scene(settings).version in outcome.text
    # 而**没有**留下一条租约：这一次拒绝发生在领活之前（见 tools.py 里的顺序）。
    assert store.attempts(work_item_id) == ()


def test_a_pinned_version_whose_bytes_do_not_load_cannot_be_claimed(
    settings, store_factory, worker
) -> None:
    """字节**在**，但装载不出来 ⇒ 不发放。**不回落**到盘上那一份。

    这是上一条的兄弟，也是「不要发明第三种回落」这句话真正要挡的那一格：钉住的
    字节还在那里、只是读不成一份 profile（改坏了、或者是另一台机器上另一个时刻的
    半份快照）。回落到盘上此刻那一份的后果，正是这次收口要关掉的那件事 ——
    一件在飞的工作**按另一份规则**跑下去，而回执上 ``pinned`` 一格还写着老的版本。

    判据分三块：**拒绝了**、**报文里两个版本都在**（处置的人要知道去哪一边找）、
    **没有落下租约**（拒绝发生在领活之前）。

    造法：钉住的那一批字节用 ``synthetic_scene_files`` —— 它是**刻意的**一批不成
    任何 profile 的字节（见 ``tests/fakes.py`` 里的那段说明），顶层的 ``synthetic
    scene`` 是一个字符串而不是映射，装载在 ``_parse_yaml`` 那里就停。
    """
    pinned = synthetic_scene_files(tag="broken-scene")
    store, _, work_item_id = _compiled_job(
        settings, store_factory, dispatch=True, pinned=pinned
    )

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    # 说清是**哪一件**工作、钉的是**哪一版**：只说「装载失败」会让处置的人不知道
    # 该去修哪一件工作的哪一批字节。
    assert work_item_id in outcome.text
    assert pinned.version in outcome.text
    assert _scene(settings).version in outcome.text
    # 而这一条的依据是「钉住的那一批读不出来」，不是「盘上这一份读不出来」——
    # 后者会把处置指向盘上那个目录，而盘上那份是好的。
    assert store.attempts(work_item_id) == ()
    assert store.get_work_item(work_item_id) is not None


def test_a_work_item_without_a_pinned_version_cannot_be_claimed(
    settings, store_factory, worker
) -> None:
    """说**不出**自己按哪一份规则跑的工作，在这里不是一个可以领的 assignment。

    §6 逐字：Running tasks retain their pinned guidance。一件既没有钉、也说不出
    自己是在钉住这个机制出现之前编译的工作，只能按**现在**这份规则跑 —— 而那是
    另一个作业。拒绝比照常发放好，因为照常发放的后果是静默的。

    这一格复现的是一份**更早的进程**写下的库：公开入口只有 ``compile_work_items``
    一个，而它的写事务把工作单元与钉写在一起。所以这里用与真实情况相同的方式
    造它 —— 让那一行不在。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)
    store.lease(work_item_id, holder=WORKER)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "DELETE FROM work_item_snapshot WHERE work_item_id = ?", (work_item_id,)
        )

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "没有冻住的快照" in outcome.text
    # 也要说得清**为什么**可以拒绝 —— 一句「没有版本」读起来像一个缺字段，
    # 而这条边界是 §6 的一条规则，不是一个数据缺口。
    assert "pinned guidance" in outcome.text
    # 而作业与租约**都还在**：拒绝的是这一次领活，不是把这个 work item 判死。
    assert store.get_work_item(work_item_id) is not None


def test_an_assignment_whose_subject_was_never_recorded_is_refused(
    settings, store_factory, worker
) -> None:
    """**第二个缺席状态**：规则钉着，针对谁从来没记过。工具面上要拒绝，且不发租约。

    它与「没有冻住的快照」不是同一件事，所以拒绝的话也不能是同一句：那一件说不出
    「按哪一份规则跑」，这一件说得出来（``scene_version`` 就在那一行里），只是没有
    「这次研究的是谁」。存储层已经把这两条分开（``tests/test_job_contract.py``），
    但**工具面**上没有任何一条判据看着它们 —— 而 worker 遇到的是工具面。

    这一格的形状是``work_item_guidance`` 那张遗留表回填进新表时的
    ``subject_json = NULL``（见 ``sqlite/db.py`` 的 DDL），所以它是盘上真实出现过的
    取值，不是编出来的边界。

    「不发租约」这半句与另一条一样承重：领活会推高 attempt 序数、占掉一次重试预算，
    于是一次拒绝会在账上留下「被尝试过」的痕迹，而它其实什么都没试。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE work_item_snapshot SET subject_json = NULL WHERE work_item_id = ?",
            (work_item_id,),
        )

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "没有主体身份" in outcome.text
    # 规则那一半还在这件事要**说出来**：一句「什么都没有」会让处置的人去补规则，
    # 而这里缺的是身份。
    assert _scene(settings).version in outcome.text
    assert store.attempts(work_item_id) == ()


def test_an_assignment_naming_only_an_internal_slug_is_refused(
    settings, store_factory, worker
) -> None:
    """**第三个缺席状态**：身份那一格写的是内部 id。这是 2026-09-23 那次的形状。

    那次 worker 领到 ``kent-003``，花掉 9 次 Serper 去搜这个 slug，交回一份「什么
    都没找到」的结算 —— 而它在报告里与「这个账号真的没有公开信息」长得一样。

    这一条挡的是**受理侧那条判据够不到的地方**：受理时 ``identity_refusal`` 会整份
    拒绝（一件都不编译，见 ``tests/test_mcp_job_tools.py``），但一份只有 slug 的身份
    在**存储层是合法的一行** —— ``compile_work_items`` 收的是 ``SubjectDossier``，
    它不判内容。所以这一行是真的存在过的（历史那一批工作就是那么编译出来的），
    而它必须在这一侧也被挡下：判据是同一个函数，被问第二次。

    另一半还是租约：拒绝不造租约。少了它，一条「先领活再判身份」的实现会让这批
    工作在账上多出几条 attempt，而回执上看起来只是「领不到」。
    """
    store = store_factory()
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="kent-2026-09-21",
        idempotency_key="kent-2026-09-21",
        request_sha256=sha256_text("kent-2026-09-21"),
    )
    slug_only = SubjectDossier(
        account_id=SUBJECT,
        # ← 这一格把内部 id 又写了一遍。这就是「没有可用来做身份发现的依据」。
        organisation=SUBJECT,
        domain="",
        country="GB",
        source_ref="tests/test_mcp_assignment_tools.py",
        input_sha256=sha256_text("slug-only-row"),
    )
    (item,) = store.compile_work_items(job.job_id, (slug_only,), scene=_scene(settings))
    store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")

    outcome = worker("get_assignment", {"work_item_id": item.work_item_id})

    assert outcome.is_error
    # 拒绝里要带着**那一行**的名字与去处：处置是去改这份清单，不是去改记录。
    assert SUBJECT in outcome.text
    assert slug_only.source_ref in outcome.text
    assert store.attempts(item.work_item_id) == ()


def test_the_assignment_says_who_this_is_and_where_that_came_from(
    settings, store_factory, worker
) -> None:
    """正控：一份**够开工**的身份照常发出去，而且带着它的出处。

    上面两条「不发」有一个廉价的反面 —— 把身份整个删掉，它们一样绿。所以这里要
    真的读到那几格：名字、域名、这份身份来自哪份清单的哪一版字节、受理那一刻。

    出处跟着线索走，不是美观问题：``identity_refusal`` 的判据边界写着消歧**是
    worker 的活**（「Example School One」与「Example School Two」在它眼里同样通过），
    而做那件事所需的材料就是「这一行来自哪里」。只给一个名字，等于把消歧交给一个
    无从核对的名字。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    dossier = synthetic_dossiers(SUBJECT)[0]
    subject = body["subject"]
    assert subject["account_id"] == SUBJECT
    assert subject["organisation"] == dossier.organisation
    assert subject["domain"] == dossier.domain
    assert subject["country"] == dossier.country
    assert subject["source_ref"] == dossier.source_ref
    assert subject["input_sha256"] == dossier.input_sha256
    assert subject["frozen_at"] == body["guidance"]["pinned_at"]
    # 主语与内部主键**不是**同一个东西 —— 这正是那两条拒绝在挡的形状，而这里
    # 它们必须不同，否则这一格只是在读一个巧合。
    assert subject["organisation"] != body["work_item"]["subject_ref"]


def test_another_worker_cannot_read_the_assignment(settings, store_factory, worker, other) -> None:
    """归属取自传输层的令牌，不取自参数。

    放开这一步，任何研究侧主体只要知道一个 work item id 就能读走另一件任务的
    场景、输入引用与预算 —— 而它在日志里看起来完全正常，因为调用方读到的
    ``holder`` 正是它自己写进去的那个值。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    outcome = other("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "不属于你" in outcome.text
    assert WORKER in outcome.text


def test_a_dispatched_but_unleased_work_item_is_claimed_by_this_call(
    settings, store_factory, worker
) -> None:
    """「已派发到执行器」与「有人正在跑」是两件事 —— 而**这一次调用**把第二件做出来。

    §3 的工具目录里没有 ``lease``。所以这条路径如果只肯回答「没有活的租约」，
    这件工作就永远没有人能持着：没人能领，所以永远没有人持着 —— 而那句话读起来
    完全像一个事实（确实没人持着）。一个没有入口的边界不是保护，是一块读起来
    像保护的招牌。§6 的「real process/task ownership」要的是**有人**持着它，
    所以领活就住在这里。

    正对照有四格，缺一不可：

    * 回执里的 ``holder`` 是**本次调用的主体**（不是某个参数说的名字）；
    * 回执里的 ``attempt_id`` 与交还的那一条**不同**（新的 ordinal，不是旧行改个字）；
    * 盘上真的多了第二条、且它是 ``leased`` —— 只看回执的话，一个把旧行读一遍
      就返回的实现照样绿；
    * 再调一次拿回**同一条**（幂等：丢响应之后重发是最正常的动作，重发不该在
      盘上再开一条租约）。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)
    store = store_factory()
    released = store.attempts(work_item_id)[0]
    store.release(released.attempt_id, reason="测试用：交还之后再读")

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    assert body["attempt"]["holder"] == WORKER
    assert body["attempt"]["attempt_id"] != released.attempt_id
    assert [attempt.attempt_id for attempt in store.attempts(work_item_id)] == [
        released.attempt_id,
        body["attempt"]["attempt_id"],
    ]
    assert store.attempts(work_item_id)[-1].is_live
    # 幂等 —— 同一个主体第二次读，拿回的是同一条，盘上没有第三条。
    again = worker("get_assignment", {"work_item_id": work_item_id}).as_json()
    assert again["attempt"]["attempt_id"] == body["attempt"]["attempt_id"]
    assert len(store.attempts(work_item_id)) == 2


def test_a_paused_job_hands_out_no_lease(settings, store_factory, worker, controller) -> None:
    """领活真的走存储层那条排他写 —— 所以作业的状态真的拦得住它。

    这里挡的是**第二份实现**：如果 ``_claim`` 自己判一次「作业能不能出租」，
    暂停就该在别处也判一次，而两处漂移的那一次会让一个暂停的作业照样被领走。
    所以判据落在存储层那句措辞上（``不放行新的租约``）—— 它是这句话唯一的
    出口，也就说明它确实是这一条在回答。

    加这一条还因为它是「领活不是一次无条件的写」的**唯一**证据面：另外两条
    判据（本人重发、别人持着）都走不到存储层那一段。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)
    store = store_factory()
    released = store.attempts(work_item_id)[0]
    store.release(released.attempt_id, reason="测试用：暂停之前先交还")
    job_id = store.get_work_item(work_item_id).job_id
    store.set_state(job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="测试用：暂停")

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "不放行新的租约" in outcome.text
    # 拒绝是真的：盘上没有多出一条租约。
    assert [attempt.attempt_id for attempt in store.attempts(work_item_id)] == [
        released.attempt_id
    ]


def test_an_undispatched_work_item_is_refused_with_the_six_six_wording(
    settings, store_factory, worker
) -> None:
    """§6 逐字「the controller cannot substitute an outer-shell worker launch for
    执行器 dispatch」在**服务面**上的执行器。

    这条判据在 H2c 之前**走不到**：那时没有活租约的 work item 先被一句「没有活的
    租约」挡下，而活租约又造不出来（``lease`` 自己也要求 ExecutionRef）—— 于是
    ``_assignment`` 里那段 §6 的措辞是一段**没人能读到的散文**。领活搬进这条路径
    之后它才真的可达，所以它现在需要自己的判据，而不是靠「反正有人会先拒绝」。

    措辞要真的落在 §6 那句话上：存储层也会拒绝同一个 work item，但它的措辞是关于
    租约的 —— 拿到那句的人会以为自己该先去派发别的，而这里该做的是**先派发这一件**。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=False)

    outcome = worker("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "还没有派发到执行器" in outcome.text
    assert "不是一个 assignment" in outcome.text
    # 这两句一起才把**顺序**钉住。存储层对同一个 work item 会拒绝同一件事（措辞是
    # 「不能出租」），所以两句话里都含「还没有派发到执行器」—— 判词只查这一句的话，
    # 把 §6 那条判挪到领活**之后**，用例照样绿，而那正是要挡的形态（先去签一条
    # 签不下来的租约，再回头说「你该先派发」）。所以负的那半才是判据：
    # 存储层那句话出现，就说明领活已经先跑过了。
    assert "不能出租" not in outcome.text
    # 拒绝是真的：盘上一条租约都没有（领活没有在拒绝之前先把租约签下来）。
    assert store.attempts(work_item_id) == ()


def test_a_cancelled_job_still_lets_its_holder_read_the_assignment(
    settings, store_factory, worker, other
) -> None:
    """取消的是**作业**，不是手上那条租约 —— 而这两件事不能混。

    取消之后存储层不再出租（``lease`` 先判 ``accepts_results``），所以这条路径若
    每次调用都去 ``lease`` 一遍，那个**正持有租约的 worker 自己也读不到自己的
    assignment 了**：它会拿到「作业已取消，不再出租」，然后交不出手里那份结果 ——
    而它手上的租约并没有被谁收回，是这条读路径把它变成了读不到。

    另一半同样要紧：取消**不**解锁。别人来读仍然是「不属于你」，否则一次取消会
    顺带把一件还没结算的工作交给任何一个知道 id 的主体。
    """
    store, _, work_item_id = _compiled_job(settings, store_factory, dispatch=True)
    store.lease(work_item_id, holder=WORKER)
    job_id = store.get_work_item(work_item_id).job_id
    store.set_state(job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="测试用：取消")

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()

    assert body["attempt"]["holder"] == WORKER
    assert body["job"]["state"] == JobState.CANCELLED.value
    refused = other("get_assignment", {"work_item_id": work_item_id})
    assert refused.is_error
    assert "不属于你" in refused.text


def test_an_unknown_work_item_is_refused(settings, store_factory, worker) -> None:
    outcome = worker("get_assignment", {"work_item_id": "0" * 32})

    assert outcome.is_error
    assert "没有这个 work item" in outcome.text


def test_tool_rights_are_the_same_list_the_guard_filters_with(
    settings, store_factory, worker
) -> None:
    """工具权限与判定**同源**，不是各算一遍。

    两处各写一份的后果不是文案不一致，是判定不一致：assignment 承诺一个工具，
    而中间件在调用它的那一刻拒绝 —— 两边都按自己的规则行事，而调用方看到的是
    「说好的工具不见了」。

    另一条同样重要：这张表里**不含**控制面工具。一个 worker 领活时读到
    ``submit_research`` 是可调的，会以为自己能受理新的研究。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    rights = worker("get_assignment", {"work_item_id": work_item_id}).as_json()["tool_rights"]

    assert rights == list(visible_tools(Principal(WORKER, frozenset({Role.RESEARCHER}))))
    assert "submit_research" not in rights
    assert "get_job" not in rights
    assert "control_job" not in rights
    # 这两条是本次阶段落的 —— 它们必须在里面，否则「领到的活没法交」。
    assert "get_assignment" in rights
    assert "submit_findings" in rights


# ----------------------------------------------------------------------
# decision_specs：``decide`` 那条路的路标（§F）
# ----------------------------------------------------------------------
def test_the_assignment_hands_over_the_spec_ids_the_decision_tool_wants(
    settings, store_factory, worker
) -> None:
    """**§F 的那条根因**：判定可达、而无人指路。

    ``decide`` 收的是 ``spec_id``，题面由服务端从 profile 编译。在此之前没有
    任何一处把 id 交到研究员手里 —— 于是没有人会去问，台账上 ``decision_record``
    恒为零行，而那零行与「这份 profile 从来没有声明过判定」在报告上分不开。

    判据取的是**编译出来的那一份**，不是回执里另抄的几个字符串：``compile_specs``
    是 ``decide`` 自己用来解析 id 的那个函数（``decisions._specs``），所以两者
    逐条相等说的正是「研究员照回执去问，问得到」。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    body = worker("get_assignment", {"work_item_id": work_item_id}).as_json()
    handed = body["decision_specs"]

    compiled = compile_specs(load_profile(settings))
    assert compiled, "这条判据要求这份 profile 真的声明了判定，否则它什么都没测"
    assert [entry["spec_id"] for entry in handed] == [spec.spec_id for spec in compiled.values()]

    for entry in handed:
        assert entry["version"] and entry["task"]
        assert entry["questions"], f"{entry['spec_id']} 一道题都没有"


def test_the_handed_over_options_are_the_ones_the_decision_tool_enforces(
    settings, store_factory, worker
) -> None:
    """交出去的选项 key **就是** ``decide`` 校验回答时用的那一份闭集。

    这是这一族判据里唯一一条会在**结论写下来之后**才发作的：研究员照回执里的
    一个 key 去写结论，而那个 key 在判定那一侧是闭集外的取值 —— 于是那句话永远
    对不上任何一条记录，而两边都各自「看起来正常」。

    比的是 ``closed_choices``（profile 自己的闭集）**与** ``choices_by_question``
    （``decide`` 实际校验用的那一份）。只比前者会漏掉「回执抄错了但恰好也在闭集里」
    之外的另一半：两个来源里只要有一个与回执不一致，这条就红。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    handed = {
        entry["spec_id"]: entry
        for entry in worker("get_assignment", {"work_item_id": work_item_id}).as_json()[
            "decision_specs"
        ]
    }

    bundle = load_profile(settings)
    compiled = compile_specs(bundle)
    for spec in compiled.values():
        entry = handed[spec.spec_id]
        assert entry["version"] == spec.version
        assert entry["task"] == spec.task.value
        assert [(q["question_id"], q["kind"]) for q in entry["questions"]] == [
            (q.question_id, q.kind.value) for q in spec.questions
        ]
        # ``decide`` 校验「回答里出现了一个闭集之外的选项」时用的就是这一份。
        assert {
            q["question_id"]: [option["key"] for option in q["options"]]
            for q in entry["questions"]
        } == {k: list(v) for k, v in spec.choices_by_question.items()}
        # 闭集本身：profile 自己的那一份。三道题各自从不同的地方来（页面档位 /
        # VenueRelation / role-matrix 实际用到的 business_function），所以这一条
        # 逐 task 比，不比一个总数。
        assert [
            [option["key"] for option in q["options"]] for q in entry["questions"]
        ] == [list(closed_choices(bundle, spec.task))]
        # 描述也在：一个只有 key 的选项让研究员没法写出那句话在说什么。
        assert all(
            option["description"] for q in entry["questions"] for option in q["options"]
        )


def test_a_scene_declaring_no_decisions_hands_over_an_empty_list_not_an_error(
    settings, store_factory, tmp_path
) -> None:
    """没声明判定时是**一块空表**，不是一次拒绝 —— 两者在回执上读得出区别。

    这条同时是上一条的反面：一块空表只有在「没声明」时才是正确的话。把它当作
    编译失败的兜底，「有人写错了选项」就会以「这份 profile 没有声明过判定」的
    样子出现，而两者处置完全不同。
    """
    without = _settings_with(tmp_path, _drop_decisions)
    session_call = _session_for(without)
    work_item_id, _ = _dispatched_job(without, lambda: SqliteJobStore(without.database_path))

    body = session_call("get_assignment", {"work_item_id": work_item_id}).as_json()

    assert body["decision_specs"] == []
    assert body["scene"]["scene_id"] == without.profile


def test_a_profile_whose_options_do_not_match_its_own_choices_refuses_the_assignment(
    tmp_path,
) -> None:
    """声明了判定却编译不过时**整次拒绝**，不降级成一块空表。

    一块空表读起来就是「这份 profile 没有声明过判定」，于是研究员不会去问，
    台账上又是零行 —— 而这一次连「有人写错了选项」都从回执上看不出来。``decide``
    对这一份 profile 一样会失败（``decisions._specs`` 抛同一处 ``ConfigError``），
    所以这里拒绝并没有挡掉任何本来能做成的事。
    """
    broken = _settings_with(tmp_path, _add_a_bogus_option)
    session_call = _session_for(broken)
    work_item_id, _ = _dispatched_job(broken, lambda: SqliteJobStore(broken.database_path))

    outcome = session_call("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error
    assert "判定题面编译失败" in outcome.text
    assert "整次拒绝" in outcome.text


# ----------------------------------------------------------------------
# guidance.plan_steps —— 服务端把「这一轮允许从哪儿拿行」交给 worker
# ----------------------------------------------------------------------
def _drop_site_discovery_routes(document: dict) -> None:
    """撤掉站点发现那两条路的入口：``page_discovery_hints`` 块不写。

    **``decisions.page_triage`` 必须跟着一起撤**，这不是顺手：那道题的闭集逐字是
    ``tuple(bundle.profile.page_discovery_hints)``（见
    ``application/decision_specs.py::closed_choices``），块没了闭集就是空的，而
    ``compile_spec`` 对空闭集是**硬拒**（「一道没有选项的选择题会让每一次判定都
    答同一项」）。只撤一块的话，这条判据会以「领活被拒」的样子红 —— 而它测的是
    步骤表跟不跟着声明走，红在别处就说明不了任何事。

    所以这一条判据的可判别性有两半：那两条路**没了**（跟着声明走），而检索那一条
    **还在**（不是一个交空表的实现）。
    """
    document.pop("page_discovery_hints", None)
    decisions = document.get("decisions")
    if isinstance(decisions, dict):
        decisions.pop("page_triage", None)


def _drop_query_templates(document: dict) -> None:
    """整个 ``search.query_templates`` 块不写 —— 检索那一条路因此没有入口。"""
    document.pop("search", None)


def test_the_handed_out_steps_are_this_scenes_openings_not_a_constant(tmp_path) -> None:
    """交出来的步骤跟着**声明**走：撤掉站点发现那两块，那两条路就没了。

    这一条挡的是「服务端交出去的是一张写死在适配器里的表」。那种实现能过上面每一条
    判据 —— 交出来的确实在闭集里、工具也确实收得下 —— 而它与声明的分家在报告上
    完全看不见，直到有人改了一份 profile：worker 手上还是那三条老路，其中两条
    这一份声明已经不给入口了。
    """
    without = _settings_with(tmp_path, _drop_site_discovery_routes)
    session_call = _session_for(without)
    work_item_id, _ = _dispatched_job(
        without, lambda: SqliteJobStore(without.database_path)
    )

    steps = session_call(
        "get_assignment", {"work_item_id": work_item_id}
    ).as_json()["guidance"]["plan_steps"]

    assert "search.engine.v1" in steps, (
        "撤掉的是站点发现那一块，检索模板那一块还在，这一条路不该跟着一起消失"
    )
    for gone in ("search.engine.site_scoped.v1", "crawl.site.v1"):
        assert gone not in steps, f"声明已经不给 {gone} 入口了，回执还在交它"


def test_a_scene_that_cannot_say_what_this_rounds_steps_are_refuses_the_assignment(
    tmp_path,
) -> None:
    """读不到「这一轮允许从哪儿拿行」时**整次拒绝**，不退化成一块空表。

    这是本片选的那条承诺：**读不到计划就拒**。另一条路（读不到就放行）在这里的
    形态是一个几乎无声的降级 —— ``plan_steps`` 交出一块空表或不交这一格，worker
    于是没有任何依据，而报告上「这份 Scene 说不出这一轮的计划」与「这一轮计划里
    没有步骤」同形。而它们处置相反：前者要回去改声明，后者是一个不可能的状态
    （声明至少开一条路，见 ``research_strategy._source_methods_cell``）。

    整次拒绝的代价是一句话说得清的：这一次领活拿不到 assignment，而不是拿到一份
    读起来正常的、装着一个空步骤表的回执。
    """
    broken = _settings_with(tmp_path, _drop_query_templates)
    session_call = _session_for(broken)
    work_item_id, _ = _dispatched_job(broken, lambda: SqliteJobStore(broken.database_path))

    outcome = session_call("get_assignment", {"work_item_id": work_item_id})

    assert outcome.is_error, "说不出这一轮的步骤，却照样把 assignment 交了出去"
    assert "步骤" in outcome.text, f"拒绝理由没说清是哪一格读不到：{outcome.text}"


# ----------------------------------------------------------------------
# submit_findings：成功路径
# ----------------------------------------------------------------------
def _findings_args(work_item_id: str, attempt_id: str, **overrides) -> dict:
    arguments = {
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "stop_reason": "goal_met",
        "observations": [
            {"statement": "俱乐部有 3 片天然草球场", "evidence_refs": [EVIDENCE]},
        ],
        "candidates": [],
        "gaps": ["没能确认它的主场容量"],
    }
    arguments.update(overrides)
    return arguments


def test_a_worker_can_settle_its_own_lease(settings, store_factory, worker) -> None:
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    body = worker("submit_findings", _findings_args(work_item_id, attempt_id)).as_json()

    assert body["work_item_id"] == work_item_id
    assert body["attempt_id"] == attempt_id
    assert body["stop_reason"] == "goal_met"
    assert body["counts"] == {
        "observations": 1,
        "candidates": 0,
        "gaps": 1,
        "relations": 0,
    }
    # 没有交责任结论 ⇒ 回执里**没有**那一块。不是「空的一块」：这一格问的是
    # 「这次结算交上来的责任结论去了哪」，而一条都没交时这个问题没有对象。
    assert "relations" not in body
    assert body["replayed"] is False
    assert len(body["content_sha256"]) == 64


def test_the_settlement_is_readable_from_get_job(
    settings, store_factory, worker, controller
) -> None:
    """交回来的产出**从控制面读得到** —— 这条是「落点真的存在」的唯一证据。

    它同时是 ``test_get_job_does_not_report_zero_results`` 的另一侧：那条断言
    没有人结算时 ``available`` 为假；这条断言结算过之后它转真。只有一条的话，
    一个恒假的 ``available`` 也能让前者变绿。

    读的人是 **controller**，不是交活的 worker：``get_job`` 在目录里是
    ``_CONTROL``，而这条用例顺带把那个划分也钉住了（研究侧看得到自己那一格，
    看得到的集合里没有控制面 —— 见 ``test_tool_rights_are_the_same_list…``）。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    job_id = store_factory().get_work_item(work_item_id).job_id
    worker("submit_findings", _findings_args(work_item_id, attempt_id))

    results = controller("get_job", {"job_id": job_id}).as_json()["results"]

    assert results["available"] is True
    assert results["totals"] == {
        "work_items_with_findings": 1,
        "observations": 1,
        "candidates": 0,
        "gaps": 1,
    }
    (stopped,) = [row for row in results["stopped_work_items"] if row["work_item_id"] == work_item_id]
    assert stopped["stop_reason"] == "goal_met"
    assert stopped["findings"]["observations"][0]["statement"] == "俱乐部有 3 片天然草球场"
    # 缺口不计入结论 —— 它是结论的缺席（见 domain/findings.py）。
    assert stopped["findings"]["gaps"] == ["没能确认它的主场容量"]


def test_a_fully_settled_job_still_reads_queued_and_says_why(
    settings, store_factory, worker, controller
) -> None:
    """``job.state`` **不是进度格**，而这一条把它钉在读得出来的地方。

    验收报告 §N.1 的读数逐字：「三个 Job 全是 ``queued``，**包括已完全结算的
    那个**」，于是 ``state`` 看起来像一格不动的进度。它答的是另一个问题：``JobState``
    里刻意没有 ``done`` / ``settled``（枚举自己的 docstring 写了为什么），
    「这个作业做完了吗」由工作单元回答。

    所以这一条是**两半一起**：一半是「已完全结算的作业读起来仍是 ``queued``」
    （那条容易让人以为坏了的事实），另一半是「回执自己说了这件事」。只有后半的话，
    一句可以与读数无关的注释也能变绿；只有前半的话，读数是对的而**读的人不知道**，
    而一格「看起来像进度、其实答的是另一个问题」的字段比没有字段更坏。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    job_id = store_factory().get_work_item(work_item_id).job_id
    worker("submit_findings", _findings_args(work_item_id, attempt_id))

    body = controller("get_job", {"job_id": job_id}).as_json()

    # 前半：这件工作已经结算完了，而作业状态没有跟着动 —— 那是**正确**的读数。
    assert body["progress"]["stopped"] == 1
    assert body["progress"]["not_started"] == 0
    assert body["job"]["state"] == "queued"
    # 后半：回执自己把这件事说清楚，且与 ``progress`` 那一格对得上。
    note = body["job"]["state_note"]
    assert "不是进度" in note
    assert "queued" in note and "settled" in note
    assert "progress" in note and "research_terminals" in note


def test_an_empty_result_is_an_available_result(
    settings, store_factory, worker, controller
) -> None:
    """跑了、什么都没找到：``available`` 为真而 ``totals`` 全为 0。

    这是本阶段唯一一处「0 是诚实的」—— 那个 0 背后有一次真实的运行
    （§8B 逐字：zero is an honest business outcome）。它与「还没有人结算过」
    必须分得开，因为处置不同：一个要去看这次跑得对不对，一个要等。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    job_id = store_factory().get_work_item(work_item_id).job_id
    body = worker(
        "submit_findings",
        _findings_args(work_item_id, attempt_id, observations=[], gaps=[]),
    ).as_json()

    assert body["counts"] == {
        "observations": 0,
        "candidates": 0,
        "gaps": 0,
        "relations": 0,
    }

    results = controller("get_job", {"job_id": job_id}).as_json()["results"]

    assert results["available"] is True
    assert results["totals"]["work_items_with_findings"] == 1
    assert results["totals"]["observations"] == 0


def test_the_same_submission_twice_is_idempotent_and_says_so(
    settings, store_factory, worker
) -> None:
    """``replayed`` 是这一格唯一能区分「又写了一遍」与「没有写」的东西。

    幂等回执如果只是「静默返回同一份」，调用方就无法判断它的重发有没有产生
    第二条记录 —— 而它下次重试的理由正是这个不确定。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    arguments = _findings_args(work_item_id, attempt_id)

    first = worker("submit_findings", arguments).as_json()
    again = worker("submit_findings", arguments).as_json()

    assert first["replayed"] is False
    assert again["replayed"] is True
    assert again["content_sha256"] == first["content_sha256"]
    assert again["recorded_at"] == first["recorded_at"]


def test_settling_writes_the_outcome_back_to_the_board(
    settings, store_factory, outside, wired
) -> None:
    """回传**真的发生了** —— 这一条钉的是「这条路径有没有调用者」。

    ``transition`` 是外部看板唯一的写动词。在它接上之前，端口定义了它、适配器实现
    了它、判据也测了它，而**生产代码里零调用点** —— 于是看板上每一件任务永远停在
    ``todo``，做完的与没做的在界面上长得一模一样。

    所以这条判据走**工具面**，而不是 ``WorkDispatch``：只测 ``reconcile()`` 本身
    恰好就是那个「有实现、没调用者」的形状。断言的是**外面被碰成了什么样**，不是
    回执里那几个布尔 —— 后者在一条根本不回传的实现上也是绿的。

    两段都要断：``expected`` 那一端也要。只断目标状态的话，一个把 ``expected`` 也
    写成目标的实现（自说自话的迁移）在这里全绿，而它在第二次重发时会撞上自己造出
    来的 CAS 冲突 —— 一条凭空出现的并发。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    # 手搓的 ``map_execution`` 只写了本地那一半，外面还没有这个任务。
    job_id = store_factory().get_work_item(work_item_id).job_id
    outside.seed("dispatch-task-0001", link_labels(job_id, work_item_id), status="todo")

    body = wired("submit_findings", _findings_args(work_item_id, attempt_id)).as_json()

    assert outside.transitions == [("dispatch-task-0001", "todo", "done")], (
        "结算之后外面必须被写成映射要求的那一个状态，且 CAS 的期望值是**读到的**那一个"
    )
    board = body["board_sync"]
    assert board["synced"] is True
    assert board["changed"] is True
    assert board["observed_status"] == "todo"
    assert board["target_status"] == "done"
    assert board["error"] is None


def test_settling_without_an_executor_still_settles_and_says_which_it_is(
    settings, store_factory, worker
) -> None:
    """没接执行器：结算照常成功，回执说清是**没接线**，而不是「同步失败」。

    两件事的处置不同：「同步失败」是去看外面怎么了；「没接线」是去看这个进程是怎么
    构造的（与能力表 dispatch 行的 ``runtime_ready`` 同源）。合成一句会让运维去查一个
    这个部署里根本不存在的东西。

    而**结算本身不受影响** —— ``replayed`` 与 ``content_sha256`` 在这里钉住的是那条
    纪律：回传失败不改变「产出已经落盘」这个事实，所以这个调用**不是**一次错误。
    把它报成错误，调用方会去重发一份已经落库的产出。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    body = worker("submit_findings", _findings_args(work_item_id, attempt_id)).as_json()

    assert body["replayed"] is False
    assert len(body["content_sha256"]) == 64
    board = body["board_sync"]
    assert board["synced"] is False
    assert board["changed"] is False
    assert board["task_id"] is None
    assert board["target_status"] == "done", "失败也要报出它本来要去哪"
    assert board["error"] is not None and "没有接线" in board["error"]


# ----------------------------------------------------------------------
# submit_findings：拒绝路径
# ----------------------------------------------------------------------
def test_another_worker_cannot_settle_the_lease(settings, store_factory, worker, other) -> None:
    """attempt/lease protection 的执行器 —— 不是一句声明。

    放开这一步，任何研究侧主体都能结算别人的租约，而报告里留下的会是它自己的
    名字（``holder`` 取自它自己的令牌）。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    outcome = other("submit_findings", _findings_args(work_item_id, attempt_id))

    assert outcome.is_error
    assert "不属于你" in outcome.text


def test_an_attempt_id_from_another_work_item_is_refused(
    settings, store_factory, worker
) -> None:
    """两个 id 配错时**拒绝**，并列出这个 work item 上真正有的那几条。

    调用方手上的 id 只可能来自 ``get_assignment``，所以「对不上」在多半情况下
    意味着它拿的是**别人**的 assignment —— 而那两个 id 都要成对使用。一个只说
    「不属于你」的回答会让它去查权限，而它该做的是回去看自己那份 assignment。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    store = store_factory()

    outcome = worker("submit_findings", _findings_args(work_item_id, "0" * 32))

    assert outcome.is_error
    assert "没有" in outcome.text
    assert attempt_id in outcome.text
    assert store.get_work_item(work_item_id) is not None  # 落点没被动过


def test_a_stale_attempt_cannot_be_settled_after_it_was_released(
    settings, store_factory, worker, other
) -> None:
    """§6「stale attempts cannot overwrite later versions」的执行器。

    这条租约被交还、同一个 work item 又被别人领走之后，旧 worker 回来结算它 ——
    必须被拒。裁定的人是**存储层**，因为「活不活」只有它手上的那个写事务说得准；
    这一层只判归属，而归属在这里是过得去的（那条 attempt 确实是它领的）。
    """
    work_item_id, first_attempt = _dispatched_job(settings, store_factory)
    store_factory().release(first_attempt, reason="测试用：换一个持有者")
    second = store_factory().lease(work_item_id, holder=OTHER)

    outcome = worker("submit_findings", _findings_args(work_item_id, first_attempt))

    assert outcome.is_error
    assert first_attempt in outcome.text
    assert "released" in outcome.text
    # 新的那一条不受影响 —— 拒绝要落在正确的那一条上。
    assert not other("submit_findings", _findings_args(work_item_id, second.attempt_id)).is_error


def test_settling_twice_with_different_findings_is_refused(
    settings, store_factory, worker
) -> None:
    """同一条租约的结局是一次性写下的。

    允许改写它，「重发」就成了一条改结论的旁路 —— 而重发与改写在那条路径上
    长得一样。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    worker("submit_findings", _findings_args(work_item_id, attempt_id))

    outcome = worker(
        "submit_findings",
        _findings_args(
            work_item_id,
            attempt_id,
            observations=[{"statement": "换一条结论", "evidence_refs": [EVIDENCE]}],
        ),
    )

    assert outcome.is_error


def test_an_unknown_stop_reason_lists_the_closed_set(settings, store_factory, worker) -> None:
    """闭集，且成员一起列出来。

    调用方该知道「我写错了」还是「这个词不存在」—— 而前者在多半情况下就是
    后者。顺带：列表里**没有** ``completed``，那条不存在是最要紧的一条。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    outcome = worker(
        "submit_findings", _findings_args(work_item_id, attempt_id, stop_reason="completed")
    )

    assert outcome.is_error
    for member in StopReason:
        assert member.value in outcome.text
    assert "goal_met" in outcome.text


def test_a_conclusion_citing_evidence_that_is_not_there_is_refused(
    settings, store_factory, worker
) -> None:
    """引用一条**不存在**的证据被拒 —— 而它此前与引用一条真的完全一样。

    领域层保证 ``evidence_refs`` 非空、去重、形如一条 id。三条都是**形状**判据，
    对一个精心编造的 id 与一条真证据给出同一种回答 —— 而 §4 逐字要的是
    literal occurrence 用的**那份**证据，一份被编出来的依据恰好把那条要求变成
    了形式。收口的那两条路是 ``application/evidence_refs``（规则）与
    ``EvidenceStore.get_evidence``（端口上的按 id 取），判据在
    ``tests/test_evidence_refs.py``；这里证明工具面没有绕过去。

    而它拒绝的方式是**整份** —— 参考那一条解得到的 ``EVIDENCE`` 也在同一份产出
    里，它没有被「留下」。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    ghost = sha256_text("never-fetched")[:32]

    outcome = worker(
        "submit_findings",
        _findings_args(
            work_item_id,
            attempt_id,
            observations=[
                {"statement": "它有三片天然草球场", "evidence_refs": [EVIDENCE]},
                {"statement": "它是社区所有的", "evidence_refs": [ghost]},
            ],
        ),
    )

    assert outcome.is_error
    assert ghost in outcome.text
    assert EVIDENCE not in outcome.text, "解得到的那条被当成解不开的报了出去"
    # 落点没被动过：拒绝发生在结算**之前**，否则「拒绝」与「落库」会同时为真。
    assert store_factory().get_work_item(work_item_id) is not None


def test_a_conclusion_without_evidence_is_refused(settings, store_factory, worker) -> None:
    """没有证据的结论不是结论 —— 而这条规则**只在领域层写了一遍**。

    工具层只把它翻成一条 ``ToolError``（``_findings_from_arguments``）。判据在
    这里，规则在 ``domain/findings.py``；重写一遍就意味着同一份规则有两个实现，
    而漂移的那一次会让工具收下一份领域层本来会拒绝的产出。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    outcome = worker(
        "submit_findings",
        _findings_args(
            work_item_id,
            attempt_id,
            observations=[{"statement": "它有三片天然草球场", "evidence_refs": []}],
        ),
    )

    assert outcome.is_error
    assert "这份产出不合法" in outcome.text
    assert "证据" in outcome.text


def test_a_malformed_conclusion_is_refused_not_guessed(settings, store_factory, worker) -> None:
    """形状不对时**不猜** —— 不把 ``evidence_refs`` 缺失读成「没有证据」。

    后者会把它变成一条「证据为空」的结论，而那是一个不同的错误（见上一条）。
    调用方改的地方完全不同：一个是补字段，一个是补证据。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)

    outcome = worker(
        "submit_findings",
        _findings_args(
            work_item_id,
            attempt_id,
            candidates=[{"statement": "缺了 evidence_refs 这一格"}],
        ),
    )

    assert outcome.is_error
    assert "evidence_refs" in outcome.text


def test_the_same_statement_twice_is_refused(settings, store_factory, worker) -> None:
    """同一条 statement 写两遍会直接变成一个虚高的条数。

    这是 ``Findings`` 拒绝重复的那条判据在工具面上的**执行器** —— 领域层拒绝
    它，但只有这条用例能证明工具面没有绕过去。
    """
    work_item_id, attempt_id = _dispatched_job(settings, store_factory)
    twice = {"statement": "俱乐部有 3 片天然草球场", "evidence_refs": [EVIDENCE]}

    outcome = worker(
        "submit_findings",
        _findings_args(work_item_id, attempt_id, observations=[twice, twice]),
    )

    assert outcome.is_error
    assert "这份产出不合法" in outcome.text


def test_a_researcher_cannot_use_the_control_tools(settings, store_factory, worker) -> None:
    """这两条工具落在 §3 那一侧，所以它们的存在**不**顺带打开控制面。

    一条用例同时钉住两件事：新工具真的接上了（研究侧调得动），而它没有把
    ``submit_research`` 一起带进来。
    """
    work_item_id, _ = _dispatched_job(settings, store_factory)

    assert not worker("get_assignment", {"work_item_id": work_item_id}).is_error
    assert worker("submit_research", {"scene_id": SCENE}).is_error
