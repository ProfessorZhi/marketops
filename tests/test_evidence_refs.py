"""一条结论挂的证据必须**真的存在** —— ``domain/findings.py`` 里那条判据查不到的那一半。

## 这个文件在钉什么

``Finding.evidence_refs`` 由领域层保证非空、去重、形如一条证据 id。那三条都是
**形状**判据，它们对一个精心编造的 id 与一条真证据给出同一种回答 —— 而
「引用一条不存在的证据」与「一条没有依据的结论」是同一件事，只是前者看起来
像个有依据的结论。§4 逐字要的正是「literal occurrence 用的那份证据」，
一份被编出来的依据恰好把那条要求变成了形式。

存在性是**两个存储的联合状态**（作业与证据），任何一个单存储都答不了，所以它
住在新模块 ``marketops/application/evidence_refs.py`` —— 唯一同时看得见两边
的一层。

## 三个实现各跑一遍

``store`` 夹具与 ``tests/test_evidence_store_contract.py`` 同形：同一个规则，
jsonl / sqlite / memory 各验一次。只对着适配器**碰巧**构造的那一个实现验，
等于把「这个规则对 ``EvidenceStore`` 成立」缩成「它对我今天接上的那个实现
成立」—— 而两者的差别只在换实现的那一天才暴露。

## 与 ``tests/test_evidence_store_contract.py`` 的分工

那边判端口自己（``get_evidence`` 取得到、取不到给 ``None`` 而不是抛）；这边判
**基于那个端口的那条业务规则**（解不开就整份拒绝）。合成一个文件会让「端口坏了」
与「规则写错了」在红灯里同形。

## 第二条判据：**解得开，但不是这一件的**

存在性只是第一条。§8 逐字要的是「B 的证据不能支撑 A 的结论」，而这一条比第一条
更容易漏 —— 一条跨账号的引用**解得开**，所以任何只按存在性做的核对都会放行它，
而它在报告上与一条真依据同形。所以归属那半边单列一节，且**两个方向都要**：
跨账号（同一个 Scene 里的另一个账号）与跨 Scene。只验一个方向的话，`workspace`
与 `account_id` 合成一次比较（比如写成 `f"{workspace}/{account_id}"`）仍然能全绿。

两个方向的**构造方式不同**，这不是测试的写法问题，是那条判据在三个实现上的
到达方式本来就不一样（理由写在 ``application/evidence_refs.py`` 的
``require_evidence_refs`` docstring）：

* 跨账号：三个存储都装得下（同一个工作区里的两个账号是两行），所以它跟着
  ``store`` 夹具跑三遍。
* 跨 Scene：**装不进去** —— 存储锁工作区，第二个工作区的写入被拒。它到达的方式
  是「库绑在 A，而这个部署的配置说 B」，而那只对 ``adapters/sqlite`` 构得出来
  （它的绑定记在库里，另外两个从构造参数拿）。所以它单列一条，对着 sqlite 写。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.adapters.jsonl.store import LocalEvidenceStore
from marketops.adapters.memory import InMemoryEvidenceStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.evidence_refs import MAX_LISTED_REFS, require_evidence_refs
from marketops.config import REPO_ROOT, Settings
from marketops.domain.errors import EvidenceError
from marketops.domain.findings import Finding, Findings
from tests.test_evidence_store_contract import make_evidence

KNOWN = "e1"
GHOST = "ghost-1"

#: ``make_evidence`` 的缺省主体（``tests/test_evidence_store_contract.py`` 的
#: ``WORKSPACE`` / ``account_id="a1"``）。这一份产出就属于它们 —— 于是「解得开且
#: 是这一件的」与「解得开但不是这一件的」在测试里只差一次关键字实参。
WORKSPACE = "uk-acme-natural-grass"
ACCOUNT = "a1"

#: 另一个账号与另一个 Scene 的证据 —— **都解得开**，所以它们与真的依据在报告上同形，
#: 只能靠归属那一格分开。取值刻意都不含 ``a1`` 也不含上面那个 Scene 名，否则
#: 「理由里有没有出现归属」那几条断言会被这一件工作的归属自己满足。
OTHER = "e2"
OTHER_ACCOUNT = "a2"

#: 另一个 Scene 的名字。**只用在跨 Scene 那一条上**，因为它是唯一一处能把一个
#: 工作区写进存储的写法（那里整个库都绑在它上面）。
OTHER_WORKSPACE = "other-scene"


def _require(
    store,
    findings: Findings,
    *,
    workspace: str = WORKSPACE,
    account_id: str = ACCOUNT,
) -> None:
    """把两格归属实参的缺省值收在一处 —— **只为让正对照读起来短**。

    这两格在**生产**调用点上是必填关键字（``application/evidence_refs.py``
    的模块 docstring 说明为什么不能有缺省）：一个「不传就不查」的开关，就是让
    这条判据变成调用点可以选的东西。测试里给缺省是另一件事 —— 缺省值就是夹具
    真正写进去的那一对，所以「忘了传」在测试里等于「传了夹具的归属」，而下面
    那两条负例**显式传另一个值**。
    """
    require_evidence_refs(store, findings, workspace=workspace, account_id=account_id)


@pytest.fixture(params=["jsonl", "sqlite", "memory"])
def store(request, tmp_path: Path):
    """三个实现之一。名字进测试 id，红了的时候一眼看得见是哪一个。"""
    if request.param == "jsonl":
        return LocalEvidenceStore(_settings(tmp_path))
    if request.param == "sqlite":
        return SqliteEvidenceStore(tmp_path / "state" / "marketops.db")
    return InMemoryEvidenceStore()


@pytest.fixture
def filled(store):
    """一个只装了一条证据的库 —— 那条的 id 是 ``KNOWN``。"""
    store.put_evidence(make_evidence(evidence_id=KNOWN))
    return store


def _settings(tmp_path: Path) -> Settings:
    resolved = Settings(
        data_root=tmp_path / "runtime",
        profiles_dir=REPO_ROOT / "profiles",
        profile="uk-acme-natural-grass",
    )
    resolved.ensure_data_dirs()
    return resolved


def _findings(*refs: str, gaps: tuple[str, ...] = ()) -> Findings:
    """每一条引用的一个 statement 一条结论。引用为空的只有 ``gaps``。"""
    return Findings(
        observations=tuple(
            Finding(statement=f"observation {index}", evidence_refs=(ref,))
            for index, ref in enumerate(refs)
        ),
        candidates=(),
        gaps=gaps,
    )


# --- 正对照：解得到就放行 ------------------------------------------------------


def test_a_ref_that_resolves_is_accepted(filled) -> None:
    """正对照。没有这一条，「对一切都抛」会把下面每一条都变成绿的。"""
    _require(filled, _findings(KNOWN))


def test_a_submission_without_refs_needs_no_store() -> None:
    """只有缺口的产出，连库都不需要。

    正对照的另一半：``store=None`` 那一支若写成「无条件拒绝」，上面那条会红，
    而这一条红了会说出原因 —— 缺口按定义没有证据可挂（``domain/findings.py``
    的模块 docstring 逐字如此），逼它挂一条依据正是那条规则要防的事。
    """
    _require(None, Findings(gaps=("没能确认 X",)))
    _require(None, Findings())


# --- 解不开就整份拒绝 ----------------------------------------------------------


def test_a_ref_that_does_not_resolve_is_refused(filled) -> None:
    with pytest.raises(EvidenceError) as excinfo:
        _require(filled, _findings(GHOST))

    assert GHOST in str(excinfo.value)


def test_one_unresolvable_ref_refuses_the_whole_submission(filled) -> None:
    """一条解不开，整份被拒 —— **不是**丢掉那一条、留下别的。

    丢掉解不开的引用、留下剩下的，等于**改写**了 worker 说过的话：回执上仍是
    「受理了」，而盘上那条结论的依据少了一半。所以这条判据要两个方向同时成立：
    混着的一条也拒（尽管 ``KNOWN`` 真的解得到），而且理由里**只**点名解不开的
    那一条 —— 把解得到的也列出来，读者会以为它也有问题。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(filled, _findings(KNOWN, GHOST))

    message = str(excinfo.value)
    assert GHOST in message
    assert KNOWN not in message, "解得到的那条被当成解不开的报了出去"


def test_the_same_unknown_ref_twice_is_named_once(filled) -> None:
    """同一条解不开的引用被两条结论各挂一次，理由里只出现一次。

    列两遍会让读者以为那是两条不同的引用 —— 而这里是**一处**缺口被引了两次，
    处置是同一个（去看那份产出从哪来）。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(filled, _findings(GHOST, GHOST))

    assert str(excinfo.value).count(GHOST) == 1


# --- 两种「解不开」必须分开 ----------------------------------------------------


def test_a_deployment_without_a_store_refuses_refs(filled) -> None:
    """没有库的时候引用**照旧被拒**，但说的必须是另一句话。

    「没有库」不是一个「空库」：任何引用都无从查起，处置是去看部署；而
    「库在、这几条不在」的处置是去看那份产出从哪来。合成一句会让第一种故障
    被读成产出的质量问题，于是排查方向整个反了。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(None, _findings(KNOWN))

    assert "没有证据库" in str(excinfo.value)


def test_the_two_kinds_of_missing_are_different_sentences(filled) -> None:
    with pytest.raises(EvidenceError) as no_store:
        _require(None, _findings(KNOWN))
    with pytest.raises(EvidenceError) as no_row:
        _require(filled, _findings(GHOST))

    assert str(no_store.value) != str(no_row.value)


# --- 解得开，但不是这一件的 ------------------------------------------------------


@pytest.fixture
def shared(store):
    """库里两条证据：``KNOWN`` 是 ``ACCOUNT`` 的，``OTHER`` 是另一个账号的。

    两条**都解得开** —— 这正是下面那几条要的形状。只放一条别人的、不放自己的，
    区分不出「按引用比对归属」与「库里有没有别人的行」。
    """
    store.put_evidence(make_evidence(evidence_id=KNOWN))
    store.put_evidence(make_evidence(evidence_id=OTHER, account_id=OTHER_ACCOUNT))
    return store


def test_a_ref_of_another_account_is_refused(shared) -> None:
    """解得开、但不是这个账号的 —— 拒，且理由里说得出它属于谁。

    只报 id 是不够的：读的人会去查它存不存在，而那一步永远查得到。这条引用的
    病灶不是「不存在」，是「属于别人」，所以理由必须带上归属。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(shared, _findings(OTHER))

    message = str(excinfo.value)
    assert OTHER in message
    assert OTHER_ACCOUNT in message, "拒了，但没说它属于谁 —— 排查方向会整个反掉"


def test_a_foreign_ref_is_not_reported_as_missing(shared) -> None:
    """归属那一条说的必须是另一句话，不能复用「解不开」那句。

    「解不开」的处置是去看那份产出从哪来；「解得开但不是这一件的」的处置是去看
    **这次调用是不是派给了另一个主体**。合成一句会让第二种故障被读成第一种，
    于是每次都会往「引用写错了」那个方向查 —— 而那条引用一个字都没写错。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(shared, _findings(OTHER))

    assert "解不开" not in str(excinfo.value)


def test_a_foreign_ref_does_not_taint_the_others(shared) -> None:
    """别人的那条混在自家引用里，也整份拒绝。

    与解不开那半边同一条纪律，且理由相同：只丢掉那几条，等于改写了这份结论的
    依据里**属于谁**这一段 —— 而回执上仍写着「受理了」。
    """
    with pytest.raises(EvidenceError) as excinfo:
        _require(shared, _findings(KNOWN, OTHER))

    message = str(excinfo.value)
    assert OTHER in message
    assert KNOWN not in message, "自家那条被当成别人的报了出去"


def test_owning_the_refs_is_what_is_checked_not_the_stores_contents(shared) -> None:
    """正对照：库里**有**别人的行，不影响只引用自家那一条的产出。

    没有这一条，上面每一条都能被一个「只要库里出现任何一条别人的证据就拒」的
    实现满足 —— 而那个实现会把同一个部署里两个账号的正常研究互相打死。
    """
    _require(shared, _findings(KNOWN))


# --- 跨 Scene：存储自己放行的那一半 ----------------------------------------------


def test_a_store_bound_to_another_scene_is_refused(tmp_path: Path) -> None:
    """库绑在 ``OTHER_WORKSPACE``，这件工作属于 ``WORKSPACE`` —— 拒。

    **存储那一层在这条路上是放行的**，所以这条判据不是它的一次重复：前两行断言
    的正是那件事（同一个库的第二个句柄从库里读到绑定 ``OTHER_WORKSPACE``，于是
    ``get_evidence`` 判「是的，这是本库的」）。库不知道这个部署的配置说了什么，
    配置也不知道库绑了什么，两个都握在手里的只有
    ``require_evidence_refs``。

    到达方式是真的那条：数据根与 Scene 配置配错，于是 B 的研究引用了 A 的证据。
    只在 sqlite 上构得出来 —— 另外两个实现的工作区来自构造参数（jsonl 读
    ``Settings``、内存由本实例第一次写入建立），一个句柄造不出这对错位。
    """
    path = tmp_path / "state" / "marketops.db"
    SqliteEvidenceStore(path).put_evidence(
        make_evidence(evidence_id=KNOWN, workspace=OTHER_WORKSPACE)
    )

    reopened = SqliteEvidenceStore(path)
    assert reopened.get_evidence(KNOWN) is not None, (
        "这一条的前提没了：存储那一层本该放行（绑定与那一行一致），"
        "若它现在自己就拒，这条测试要说明的是另一件事"
    )

    with pytest.raises(EvidenceError) as excinfo:
        _require(reopened, _findings(KNOWN), workspace=WORKSPACE)

    message = str(excinfo.value)
    assert OTHER_WORKSPACE in message, "拒了，但没说那一行属于哪个 Scene"
    assert "解不开" not in message, "把「不是这一件的」报成了「不存在」"


# --- 有界 ----------------------------------------------------------------------


def test_the_foreign_refusal_lists_a_bounded_number_of_ids(store) -> None:
    """归属那一侧的渲染也是同一条纪律：点名有界，截掉的折成数字。

    ``listed_foreign_refs`` 是**另一个**渲染函数（每一条多带一列归属），所以它
    自己那条口径要自己验一次 —— 只验 ``listed_unknown_refs`` 的话，这个函数里
    少写一次截断不会让任何一条红，而它的输出同样进回执。
    """
    others = tuple(f"e-{index:03d}" for index in range(MAX_LISTED_REFS + 5))
    for index, ref in enumerate(others):
        store.put_evidence(
            make_evidence(evidence_id=ref, account_id=f"a{index + 2}")
        )

    with pytest.raises(EvidenceError) as excinfo:
        _require(store, _findings(*others))

    message = str(excinfo.value)
    assert message.count("（属于 ") == MAX_LISTED_REFS
    assert str(len(others)) in message, "被截掉的那些没有在理由里留下数字"


def test_the_refusal_lists_a_bounded_number_of_ids(filled) -> None:
    """一次拒绝里点名多少条 id 是有界的，剩下的用数字带过。

    一份引用上千条证据的产出（或一次手工构造的输入）会让理由本身变成一次
    上下文耗尽 —— 而回执是给调用方读的。带过的那个数字要说出来，否则
    「列了 10 条」会被读成「只错了 10 条」。
    """
    ghosts = tuple(f"ghost-{index:03d}" for index in range(MAX_LISTED_REFS + 30))

    with pytest.raises(EvidenceError) as excinfo:
        _require(filled, _findings(*ghosts))

    message = str(excinfo.value)
    assert message.count("ghost-") == MAX_LISTED_REFS
    assert str(len(ghosts)) in message, "被截掉的那些没有在理由里留下数字"
