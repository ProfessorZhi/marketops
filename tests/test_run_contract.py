"""``ports.runs.RunStore`` 的契约 —— 两个实现跑同一份判据（§B17 的受理面）。

## 为什么这一族必须有两个实现各跑一遍

一个实现跑着的判据，说的是「这个实现今天的写法」，不是「这个契约」。两份实现
之间真正的分歧形态是：其中一份**静默地受理**了另一份拒绝的规格 —— 它不抛异常、
不留日志，只在报告上表现为「同一轮跑了两遍」或者「一个从出生就过期的 Run 跑
起来了」。所以下面每一条都对着两个实现各判一次。

## 这一族里最有分量的一条：拒绝在两个实现里是**同一句话**

判定本身住在 ``ports/runs.py::admission_refusal``，两个实现都调它。抄进实现里
的话，两份会各自漂移，而「两个实现都通过」这句话就没有依据了。所以
:func:`test_the_two_implementations_refuse_in_the_same_words` 逐条比对两边给出的
**逐字相同**的拒绝理由 —— 一条只断言「抛了 ``RunSpecError``」的判据，在「两边
拒的理由不一样」时是绿的，而那一刻正是这份契约失效的第一天。

## 三条与存储无关、但只有这里能判的性质

* **四个 id 空间不相交。** ``job_id`` / ``continuation_id`` / ``run_id`` /
  ``SceneVersion.frozen_sha256`` 各自在主键上活着，而它们的分开只靠推导里那个
  ``kind`` 前缀 —— 那是**代码**，不是注释。所以这里拿同一组输入在四个空间里各算
  一次，断言两两不等。
* **``RunStatus`` 的词表与表上的 ``CHECK`` 是同一份。** 一个在枚举里加了成员而没
  更新 DDL 的改动，会在**写入那一刻**才炸（而且只在真的写下那个结局时炸）——
  那时它已经在另一个进程里跑完了一次。这里把两边读出来逐字比一次。
* **受理出来的 Run 还没有结束。** ``stop_reason`` 必须是 ``None``。它是本切片
  （受理面）唯一说得出口的结局判词 —— 「这一轮停了吗」的答案在受理那一刻是
  「没有」，而那句话一旦可以被填成别的值，§B17 条件 2 的 Delta 就没有起点。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryRunStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.config import REPO_ROOT
from marketops.domain.continuation import ContinuationKind, continuation_id
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import IdempotencyConflictError, RunSpecError, StoreError
from marketops.domain.job import job_id
from marketops.domain.run import (
    AuthorizationWindow,
    RunBudgetLine,
    RunSpec,
    run_id,
    spec_fingerprint,
)
from marketops.domain.util import SEP, sha256_text

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-a"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)

DB = REPO_ROOT / "marketops" / "adapters" / "sqlite" / "db.py"


# --- 构造器 -------------------------------------------------------------------


#: 授权窗口的截止。**晚于** :data:`CUTOFF` —— 「本轮的截止超出窗口」是 ``RunSpec``
#: 另一条校验器判的，而这一族的用例问的不是它。
WINDOW_UNTIL = T0 + timedelta(days=30)


def _window() -> AuthorizationWindow:
    return AuthorizationWindow(
        max_objects=50,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=100),),
        not_after=WINDOW_UNTIL,
    )


def _spec(**overrides: object) -> RunSpec:
    """一份最小可受理的规格。改哪一格由调用点显式给 —— 见各条判据自己的说明。

    ## 它**不**替谁补窗口

    域层收得下一份没有窗口的 ``bounded_auto``（那条判据住在受理面，见
    ``ports/runs.py::admission_refusal``）—— e2 之后受理面对**有没有窗口**给的
    是两个不同的答案（没有窗口 ⇒ 拒；有窗口 ⇒ 再问这条链累计走到哪了），所以
    「非 manual 模式」这一族用例靠**不给窗口**来问其中一句。带窗口的那一半不在这里：
    它要读盘，住在 ``tests/test_run_window_admission.py``。窗口本身的一致性由
    :func:`_window` 那一族用例各自带给 ``RunSpec``，不经这个助手。
    """
    payload: dict[str, object] = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.MANUAL,
        "window": None,
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)  # type: ignore[arg-type]


def _admit(store, spec: RunSpec, *, at: datetime = T0):
    return store.admit(spec, spec_sha256=spec_fingerprint(spec), at=at)


@pytest.fixture(params=["sqlite", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteRunStore(tmp_path / "state" / "marketops.db")
    return InMemoryRunStore()


# --- 受理幂等 -----------------------------------------------------------------


def test_the_same_key_returns_the_same_run(store) -> None:
    """重发拿回的是**同一行**：id 由 ``(工作区, 键)`` 内容寻址，不是服务端生成的。"""
    spec = _spec()
    first = _admit(store, spec)
    second = _admit(store, spec)

    assert first.run_id == second.run_id == run_id(WORKSPACE, KEY)


def test_the_second_admit_does_not_move_created_at(store) -> None:
    """重发**不重写**那一行的时间。

    一个每次重发都刷新 ``created_at`` 的实现，会让「这一轮是什么时候受理的」在一次
    网络重试之后悄悄变成另一个时刻 —— 而报告上它看起来完全正常。
    """
    spec = _spec()
    first = _admit(store, spec, at=T0)
    second = _admit(store, spec, at=T0 + timedelta(hours=3))

    assert second.created_at == first.created_at
    assert second.updated_at == first.updated_at


def test_a_different_spec_under_the_same_key_is_a_conflict(store) -> None:
    """同一个键配另一份内容**不是**重发。它要抛，而不是静默返回那一行。"""
    _admit(store, _spec())
    with pytest.raises(IdempotencyConflictError) as caught:
        _admit(store, _spec(objective="换一个目标"))

    assert KEY in str(caught.value)


def test_the_conflict_leaves_the_first_row_alone(store) -> None:
    """冲突之后盘上那一行还是原来那一份 —— 被拒的提交一个字节都没落。"""
    spec = _spec()
    before = _admit(store, spec)
    with pytest.raises(IdempotencyConflictError):
        _admit(store, _spec(objective="换一个目标"))

    after = store.get(before.run_id)
    assert after is not None
    assert after.spec.objective == spec.objective
    assert after.spec_sha256 == before.spec_sha256


# --- 受理拒绝 -----------------------------------------------------------------


#: 不带窗口的 ``bounded_auto`` 今天**被哪一句拒掉**。
#:
#: 这一格是 e2 拆句之后新加的，而切片 f 之后它**只剩一行**：``watch`` 曾经也在这张
#: 表里（它的那一句是「它的来源还没有生产者」），而切片 f 把那个来源接上了 —— 那句
#: 话不再成立，判据换到了**要读盘**的那一层（``application/run_admission.py`` 的
#: ``_require_due``，它答的是「这一批主体上今天有没有到期」，那要先有一批主体）。
#: 那张表因此不再叫「非 manual 的两个模式」，而叫**这一句还成立的那一个模式**：
#: 一个保留着一行已经失效的条目的表，会让下一个人以为 ``watch`` 还是受理即拒。
_REFUSALS: dict[ExecutionMode, str] = {
    ExecutionMode.BOUNDED_AUTO: "没有带授权窗口",
}


@pytest.mark.parametrize("mode", sorted(_REFUSALS, key=lambda one: one.value))
def test_a_windowless_automatic_mode_is_refused_at_admission(
    store, mode: ExecutionMode
) -> None:
    """不带窗口的 ``bounded_auto`` **受理即拒** —— §B17「mode 不是权限捷径」。

    不是「那一格没有形状」：切片 e1 之后 ``RunSpec`` 收得下授权窗口，而这份规格也
    确实是形制合法的（``_spec`` 不替谁补窗口 —— 域层不要求 ``bounded_auto`` 带一个）。

    拒它的理由是**这一份请求没有写下授权** —— 窗口那一格（``submit_run`` 的第七个
    参数）今天是有生产者的，读者也有（``ports/runs.py::window_refusal``），少的只是
    **这一次**没写；而没有写下来的窗口与「没有人授权过」在能做的事上完全一样。

    这句话换过两次措辞（e1 点读者、e2 点生产者、e2c 起点这一份请求），理由与逐字的
    报文见 ``ports/runs.py::admission_refusal`` 那一节。**这一条断言的拒词没跟着换**
    （``没有带授权窗口`` 三个字三次都在），所以判据一直是绿的 —— 变的是它上面的这段
    话，而它必须跟着事实走，否则它会在下一次有人读它时，把一次「这一份请求没写」读成
    「这个功能还没造」。

    另外两个模式都不在这里，而它们不在的理由**各不相同**：

    * **带窗口的 ``bounded_auto`` 今天走得通** —— 判它的那条链是
      ``test_run_window_admission.py``（那条判据要读盘，形态与这一条不同）。
    * **``watch`` 今天也走得通**，但它**要有一条到期的格子**：那条判据同样要读盘，
      住在 ``tests/test_mcp_runs.py`` 第 11 节（端到端，走真的 MCP 调用面）。
      这一条不替它判，也不假装它还拒在 ``ports/runs.py``。
    """
    with pytest.raises(RunSpecError) as caught:
        _admit(store, _spec(execution_mode=mode))

    refusal = str(caught.value)
    assert mode.value in refusal
    assert _REFUSALS[mode] in refusal


def test_a_cutoff_in_the_past_is_refused(store) -> None:
    with pytest.raises(RunSpecError) as caught:
        _admit(store, _spec(cutoff=T0 - timedelta(seconds=1)))

    assert "不在将来" in str(caught.value)


def test_a_cutoff_exactly_at_the_admission_instant_is_refused(store) -> None:
    """边界：``cutoff == at`` 是**不在将来**。

    这一条单列，因为它是 ``<=`` 与 ``<`` 的分界，而两者在一份「差一秒」的测试里
    都绿。一个正好在受理那一刻到期的 Run，它的第一件事就是发现自己在截止之后。
    """
    with pytest.raises(RunSpecError):
        _admit(store, _spec(cutoff=T0))


def test_a_refused_spec_writes_nothing(store) -> None:
    """拒绝之后盘上**没有**这一行 —— 不是「写下了但标记为拒绝」。

    一个先落盘再标注拒绝的实现，会让次日的「已受理 Run」列表里多出一轮从没跑过
    的东西，而那个列表正是「自行扩下一批」开始的地方（``ports/runs.py`` 里那条
    「没有 ``list_runs``」的理由）。

    用的是**不带窗口的** ``bounded_auto`` 这一份被拒规格 —— 切片 f 之前这里用的是
    ``watch``，而 ``watch`` 今天受理得下来（前提是有一条到期的格子）。这一条判的是
    「被拒的那一份写不写得下盘」，所以它要的是一份**今天真的被拒**的规格。
    """
    spec = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO)
    with pytest.raises(RunSpecError):
        _admit(store, spec)

    assert store.get(run_id(WORKSPACE, KEY)) is None


def test_the_spec_digest_shape_is_enforced(store) -> None:
    """``spec_sha256`` 必须是 64 位小写十六进制。

    形状不对的值不会报错 —— 它只会让**每一次**提交都与别的不同，于是重发被读成
    第二轮。所以这里判的是那个 ``ValueError`` **先于**任何写入发生。
    """
    spec = _spec()
    with pytest.raises(ValueError):
        store.admit(spec, spec_sha256="ABC", at=T0)

    assert store.get(run_id(WORKSPACE, KEY)) is None


def test_the_two_implementations_refuse_in_the_same_words(tmp_path: Path) -> None:
    """**本文件最有分量的一条。** 两边拒的理由必须逐字相同。

    判定住在 ``ports/runs.py``，两个实现都调它。抄进实现里的话，两份会各自漂移，
    而漂移的形态是其中一份**静默地受理**一份另一份拒绝的规格 —— 没有异常、没有
    日志，只有盘上多了一轮。一条只断言「抛了 ``RunSpecError``」的判据在那天是绿的。

    其中一份是切片 e1 特意加进来的：**带着窗口的 ``bounded_auto``** —— 它在这里**不是**
    为了 ``admission_refusal``，而是为了 ``window_refusal``（这一条不传 ``spent``，
    于是那句「账本读数没接上」是它唯一的出口）。它存在的理由是「受理面拒一个自动模式」
    与「它带没带窗口」是两句话：若哪一天带上窗口那一份被静默受理，盘上多出的一轮是
    **看起来最正常**的那一轮（有窗口、有预算、有截止，什么都齐全，只是没人会去问它
    累计走到哪了）。

    ``watch`` 曾经也在这张表里，切片 f 之后**不在了** —— 它在两个实现上都受理得下来
    （前提是有一条到期的格子，而那一条判据要读盘，住在 ``tests/test_mcp_runs.py``
    第 11 节）。这一条判的是「两份实现的话是不是逐字相同」，所以表上留下的必须都是
    **今天真的被拒**的规格；留一份今天走得通的，红的话会说「两份都没抛」，
    而那句话指向的是这张表过期了，不是两个实现分家了。
    """
    sqlite = SqliteRunStore(tmp_path / "state" / "marketops.db")
    memory = InMemoryRunStore()

    bad_specs = [
        _spec(execution_mode=ExecutionMode.BOUNDED_AUTO),
        _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=_window()),
        _spec(cutoff=T0),
        _spec(cutoff=T0 - timedelta(days=400)),
    ]
    for spec in bad_specs:
        with pytest.raises(RunSpecError) as left:
            _admit(sqlite, spec)
        with pytest.raises(RunSpecError) as right:
            _admit(memory, spec)
        assert str(left.value) == str(right.value), spec.execution_mode


# --- 读回来 -------------------------------------------------------------------


def test_an_admitted_run_has_not_stopped_yet(store) -> None:
    """受理出来的 Run，``stop_reason`` 是 ``None`` —— 受理**不是**结束。

    ``None`` ⟺ 还在跑。「这一轮停了吗」在受理那一刻只有一个诚实答案，而它一旦
    可以被填成别的值，§B17 条件 2 要求的 Delta 就没有起点。
    """
    admitted = _admit(store, _spec())

    assert admitted.stop_reason is None
    assert admitted.is_stopped is False
    assert store.get(admitted.run_id).stop_reason is None


def test_the_spec_reads_back_unchanged(store) -> None:
    """规格**原样**吐回 —— 受理之后要答「这一轮当时是按什么跑起来的」。

    存的是它自己的 JSON，不是重算的：重算的那一份会在散列函数或校验器改动之后
    与当时那一份悄悄分家，而报告上它们都叫「这一轮的规格」。
    """
    spec = _spec()
    admitted = _admit(store, spec)

    read_back = store.get(admitted.run_id)
    assert read_back is not None
    assert read_back.spec.model_dump_json() == spec.model_dump_json()


def test_get_returns_none_for_an_unknown_id(store) -> None:
    """「没有这一轮」是答案，不是异常。"""
    assert store.get("0" * 32) is None


# --- 绑定：一个库一个工作区 ----------------------------------------------------


def test_a_second_workspace_cannot_be_admitted_into_the_same_store(store) -> None:
    """第一行写入把这个库绑在那个工作区上，第二行属于别处就写不进去。

    ``research_run`` 在 ``db.py::OWNED_TABLES`` 里 —— 它的每一行都写着 ``workspace``，
    所以「按 ``run_id`` 取到的这一行是谁的」必须有一个答案。混装之后那个问题没有
    答案，而它的表现不是报错：两批行被静静地混在一起返回，报告上只显示「这一轮更
    多了」。

    两个实现都要有这条拒绝形态（``tests/test_cohort_store_contract.py`` 起的那条
    规矩），拒的那句话逐字相同 —— 判词一份，住在 ``db.bind_workspace``。
    """
    _admit(store, _spec())
    with pytest.raises(StoreError):
        _admit(store, _spec(workspace="another-workspace"))


def test_admitting_binds_the_database_so_it_can_be_opened_again(tmp_path: Path) -> None:
    """**受理那一行同时把库绑上** —— 于是这个库还能被下一次打开。

    ``SqliteRunStore.__init__`` 每次都跑 ``ensure_schema``，而它有一条反向判据：
    **库里有带所有者的行、却没有工作区绑定 ⇒ 当场拒绝打开**（那种状态的处置是找
    运维，因为它按写路径构造不出来）。少了绑定这一句，受理就会亲手造出它：这一次
    写成功、盘上真有一行，而**下一次**打开这个库 —— 任何 store 的构造、部署前那次
    自检、以及 ``get_run`` 的每一次调用（组合根交给 ``run_handlers`` 的是
    ``default_run_store(settings)`` 这个**工厂**，所以每次调用都新建一个）—— 全部
    退 ``StoreError``。而回执上说这一轮被受理了。

    所以这条判据比它看起来重要：它测的不是「能不能读回来」，而是「**再开一次**还能
    不能开」。前者的实现可以让那一行留在盘上而库已经不可用了。
    """
    db = tmp_path / "state" / "marketops.db"
    _admit(SqliteRunStore(db), _spec())

    # 另建一个实例（不是复用上面那个）—— 结构上就是下一次工具调用的形状。
    reopened = SqliteRunStore(db)
    assert reopened.get(run_id(WORKSPACE, KEY)) is not None


# --- 三条与存储无关的性质 ------------------------------------------------------


def test_the_four_id_spaces_do_not_collide() -> None:
    """同一组输入在四个 id 空间里各算一次，两两不等 —— ``kind`` 前缀承重。

    ``job_id`` / ``continuation_id`` / ``run_id`` / ``SceneVersion`` 的内容摘要
    共用同一个 ``sha256_text(SEP.join(...))[:32]`` 形状。把它们分成四个空间的那
    一样东西是**第一段里的 ``kind``**，而它是一行代码。这一条把「它还在」变成一句
    可执行的话：哪天有人为了「统一一下」把它去掉，两次不同的事就会拿到同一个 id，
    而主键冲突的表现是「一件工作莫名其妙已经存在」。
    """
    scene_id = "scene-devon"
    key = "k-1"
    ids = {
        "job": job_id(scene_id, key),
        "continuation": continuation_id(
            scene_id, ContinuationKind.MAINTENANCE_TICK, "maintenance/v1"
        ),
        "run": run_id(SCENE, key),
        "scene-version": sha256_text(SEP.join(["scene-version", scene_id, WORKSPACE, key]))[:32],
    }

    assert len(set(ids.values())) == len(ids), ids
    assert all(len(value) == 32 for value in ids.values()), ids


def test_the_id_spaces_truncate_to_the_same_length() -> None:
    """四个空间截断到同一个长度。

    长度不一致本身不致命，致命的是一条**比另外三条长**的推导：它在被截断的字符串
    上仍然唯一，于是「长度不一样」这件事会被读成「这个空间更安全」。四个数一样，
    是为了让「换长度」必须一次改四处 —— 而改一处会让分隔符撞车的概率变回一条
    没有人算过的值。
    """
    lengths = {
        len(job_id("s", "k")),
        len(continuation_id("s", ContinuationKind.MAINTENANCE_TICK, "b")),
        len(run_id("s", "k")),
        len(sha256_text(SEP.join(["scene-version", "s"]))[:32]),
    }

    assert lengths == {32}, lengths


def test_the_status_words_match_the_table_constraint() -> None:
    """``RunStatus`` 的成员与 ``research_run`` 上的 ``CHECK`` 是同一份词表。

    一个在枚举里加了成员而没更新 DDL 的改动，只会在**真的写下那个结局时**才炸
    —— 而那已经是另一次运行、另一个进程里的事了。这里把两边读出来逐字比一次。
    """
    ddl = DB.read_text(encoding="utf-8")
    table = ddl[ddl.index("CREATE TABLE IF NOT EXISTS research_run") :]
    table = table[: table.index(";")]
    match = re.search(r"stop_reason TEXT CHECK \(stop_reason IN \(([^)]*)\)\)", table)
    assert match is not None, f"research_run 的 stop_reason 没有 CHECK 约束：\n{table}"

    declared = {value.strip().strip("'") for value in match.group(1).split(",")}
    assert declared == {member.value for member in RunStatus}, (
        f"表上写着 {sorted(declared)}，枚举里是 "
        f"{sorted(member.value for member in RunStatus)}"
    )
