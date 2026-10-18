"""JSONL 存储：**这个文件格式自己**的性质。

## 为什么这里只剩下这么点东西

B5 之后三个实现共享同一组判据，住在 ``tests/test_evidence_store_contract.py``。
留在这里的每一条都是**只有文件格式才会有的**问题：文件名怎么来、编码怎么写、
一行坏了怎么办、目录什么时候建。这些量在 SQLite 那边不存在（那边叫表、叫行、
叫 schema 版本），所以它们不是契约的一部分，写进契约只会变成「SQLite 那边跳过」。

## 它现在不是主存

主存是 ``adapters/sqlite/evidence_store.py``。本格式的角色是**导出与审计通道**
—— 它唯一不可替代的性质是不需要任何工具就能读。那一侧的取舍写在
``marketops/adapters/jsonl/store.py`` 的模块 docstring 里，改这个文件之前先读它。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from marketops.adapters.jsonl.store import LocalEvidenceStore
from marketops.domain.errors import EvidenceError
from marketops.domain.evidence import Evidence
from tests.test_evidence_store_contract import (
    FETCHED_AT,
    make_evidence,
    make_observation,
    make_opportunity,
    make_rejection,
)

if TYPE_CHECKING:  # 只给注解用：``Settings`` 在用到它的那条测试里才需要真的构造
    from marketops.config import Settings


def _store(settings) -> LocalEvidenceStore:
    return LocalEvidenceStore(settings)


# --- 文件名与编码 -------------------------------------------------------------


def test_a_canonical_account_id_is_its_own_file_name(settings) -> None:
    """id 落盘时**不被改写**：文件名就是 id。

    这一条曾经叫 ``test_account_id_is_slugified_for_filename``，测的是「空格与
    大小写不许原样进文件名」—— 它用的例子是 ``My Account 001`` 落进
    ``my-account-001.jsonl``。那条测试**自己在 docstring 里说出了它的毛病**：
    它演示的正是那个洞（``My Account 001`` 与 ``my-account-001`` 是同一个文件），
    只是当时没有判据去堵它。

    现在堵它的判据住在模型层（``domain/util.check_canonical_id``），所以模型
    只接受**已经是文件名**的 id，落盘这一侧没有第二种写法可言。
    """
    _store(settings).put_evidence(make_evidence(account_id="acme-001"))

    assert [path.name for path in settings.evidence_dir.glob("*.jsonl")] == [
        "acme-001.jsonl"
    ]


def test_a_non_canonical_account_id_never_reaches_storage(settings) -> None:
    """一个会被改写的 id 在**构造模型时**就被拒绝，而不是在写文件时。

    拒绝点在前而不是在后，是这一族判据的全部价值：写在 ``_path_for`` 里只能
    保护这一个实现（``jsonl``），而 ``sqlite`` 与将来的第三个实现看到的会是
    另一个 id 空间。落盘之前拒绝，三个实现就都只可能见到规范形式。
    """
    with pytest.raises(ValidationError) as excinfo:
        make_evidence(account_id="My Account 001")

    message = str(excinfo.value)
    assert "不是规范形式" in message
    assert "my-account-001" in message, "拒绝理由要说清它会被改写成什么"
    assert not settings.evidence_dir.exists() or not list(
        settings.evidence_dir.glob("*.jsonl")
    ), "被拒绝的写入在盘上留下了东西"


def test_store_writes_utf8_lf(settings) -> None:
    """统一 UTF-8 + LF，Linux 上读同一文件不应产生差异。"""
    _store(settings).put_evidence(make_evidence(literal_value="контакт@例え.jp"))
    raw = next(settings.evidence_dir.glob("*.jsonl")).read_bytes()

    assert b"\r\n" not in raw
    assert "контакт@例え.jp" in raw.decode("utf-8")


def test_a_row_is_one_line(settings) -> None:
    """一行一条 JSON 是这个格式的全部承诺：多一行少一行都会让 ``grep`` 失去意义。

    ``ensure_ascii=False`` 让中文与非 ASCII 邮箱按原样落盘，但**不能**把换行带进去
    —— 所以这条断言查的是「写出来的行数等于写入次数」，不是「文件里有内容」。
    """
    store = _store(settings)
    store.put_evidence(make_evidence(literal_value="a\nb@example.co.uk"))

    lines = [
        line
        for line in (settings.evidence_dir / "a1.jsonl").read_text(encoding="utf-8").split("\n")
        if line
    ]
    assert len(lines) == 1
    assert json.loads(lines[0])["literal_value"] == "a\nb@example.co.uk"


def test_store_uses_settings_paths_not_constants(settings) -> None:
    """证据目录必须来自 Settings —— 换运行根目录时数据跟着走。"""
    _store(settings).put_evidence(make_evidence())

    assert (settings.data_root / "state" / "evidence" / "a1.jsonl").exists()


def test_routes_and_opportunities_use_their_own_directories(settings) -> None:
    """路线（观察、投影、拒收）与机会落在各自的目录里 —— 名字本身说明它们不是
    同一种东西，而拆开之后「这个账号有几条路线」不再要去一份追加日志里数。

    **三个目录**都要看：只查投影那一份的话，一次把观察写错目录的实现会让这条
    用例照样绿，而历史那一份从此不见了。
    """
    store = _store(settings)
    store.record_route(make_observation())
    store.put_route_rejection(make_rejection())
    store.put_opportunity(make_opportunity())

    assert (settings.route_observations_dir / "a1.jsonl").exists()
    assert (settings.routes_dir / "a1.jsonl").exists()
    assert (settings.route_rejections_dir / "a1.jsonl").exists()
    assert (settings.opportunities_dir / "a1.jsonl").exists()
    assert settings.routes_dir != settings.evidence_dir
    assert settings.routes_dir != settings.route_observations_dir


def test_local_store_creates_directories_lazily(tmp_path: Path) -> None:
    """首次写入前目录可能不存在 —— store 应当自己创建，而不是要求调用方先建。

    这里刻意不用 ``settings`` 夹具：它已经调用过 ``ensure_data_dirs()``，
    目录早就在了，测不出「惰性创建」这件事。用裸 Settings 才是有意义的断言。
    """
    from marketops.config import REPO_ROOT, Settings

    resolved = Settings(
        data_root=tmp_path / "runtime",
        profiles_dir=REPO_ROOT / "profiles",
        profile="uk-acme-natural-grass",
    )
    assert not resolved.evidence_dir.exists(), "构造 Settings 不应产生任何目录"

    LocalEvidenceStore(resolved).put_evidence(make_evidence())
    assert resolved.evidence_dir.is_dir()


def test_a_second_store_over_the_same_directory_sees_the_records(settings) -> None:
    """换一个实例读同一个目录，读到的是同一批证据 —— 这是持久化实现各自的性质。"""
    _store(settings).put_evidence(make_evidence())

    assert len(LocalEvidenceStore(settings).list_evidence("a1")) == 1


# --- 损坏处理 -----------------------------------------------------------------


def test_corrupted_line_raises_instead_of_being_skipped(settings) -> None:
    """静默跳过损坏行会污染后续所有统计 —— 必须报错让人来修。"""
    store = _store(settings)
    store.put_evidence(make_evidence())

    path = next(settings.evidence_dir.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json }\n")

    with pytest.raises(EvidenceError, match="损坏"):
        store.list_evidence("a1")


def test_unknown_field_in_stored_row_raises(settings) -> None:
    """契约是严格的：存储层也不接受幽灵字段。"""
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    (settings.evidence_dir / "a1.jsonl").write_text(
        '{"evidence_id":"e1","account_id":"a1","source_url":"u","source_kind":"fetched_html",'
        '"literal_value":"x","content_sha256":"c","fetched_at":"2026-09-15T12:00:00Z",'
        '"ghost":1}\n',
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        _store(settings).list_evidence("a1")


def test_a_conflicting_line_on_disk_is_refused(settings) -> None:
    """幂等检查读的是**磁盘上的行**，不只是本进程写过的东西。

    这个格式没有任何机制阻止另一个进程、或一个人手工往文件里追加 —— 所以那条
    「同 id 必须同内容」的判据必须对着文件判，而不是对着内存里的已知集合判。
    """
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    conflicting = make_evidence(literal_value="someone.else@example.co.uk")
    (settings.evidence_dir / "a1.jsonl").write_text(
        json.dumps(conflicting.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceError, match="内容与本次不同"):
        _store(settings).put_evidence(make_evidence())


def test_the_round_trip_keeps_the_timestamp(settings) -> None:
    """``fetched_at`` 是新鲜度判定的输入（§5），落盘一趟不能变形。"""
    _store(settings).put_evidence(make_evidence())

    assert LocalEvidenceStore(settings).list_evidence("a1")[0].fetched_at == FETCHED_AT


def test_the_stored_row_is_the_whole_model(settings) -> None:
    """存的是 ``model_dump``，不是挑出来的几个字段 —— 否则读回来会缺字段。"""
    _store(settings).put_evidence(make_evidence())

    raw = json.loads((settings.evidence_dir / "a1.jsonl").read_text(encoding="utf-8"))
    assert set(raw) == set(Evidence.model_fields)


# ----------------------------------------------------------------------
# 读取边界：``list_*`` 的入参是**裸字符串**，不经过任何模型
# ----------------------------------------------------------------------
def test_a_non_canonical_id_is_refused_at_the_read_boundary_too(settings) -> None:
    """读一个会被改写的 id 不是「读不到」，是**读到了另一个账号**。

    上面两条判据把住的是**写**（模型层拒绝）。``list_evidence`` / ``list_routes`` /
    ``list_opportunities`` 的入参却是一个裸字符串 —— 它不经过任何模型，于是
    ``list_evidence("Foo")`` 会去读 ``foo.jsonl``。

    修好之前的实际后果不是报错，是一份**别人的**存档被当成自己的读了出来：
    台账行数不变、报告上只显示「证据更多了」。``Foo`` 与 ``foo`` 在 ``slugify``
    下是同一件事，所以这一对足以演示，不需要编造更复杂的 id。
    """
    store = _store(settings)
    store.put_evidence(make_evidence(account_id="foo"))

    assert len(store.list_evidence("foo")) == 1, "正控：规范 id 读得到，判据不是「谁都读不到」"

    with pytest.raises(EvidenceError) as excinfo:
        store.list_evidence("Foo")

    message = str(excinfo.value)
    assert "不是规范形式" in message
    assert "foo" in message, "拒绝理由要说清它会被读成哪一个 id —— 修法是改名，不是别读"


@pytest.mark.parametrize("collection", ["evidence", "routes", "opportunities"])
def test_every_collection_refuses_a_non_canonical_id(settings, collection: str) -> None:
    """三个集合各自拦一次 —— 判据只有一份，但它守的是三条路。

    ``_path_for`` 是三者**唯一**的命名入口，所以这条参数化用例测的不是三份实现，
    而是「三条路都真的走那一个入口」。
    """
    store = _store(settings)
    put, read = {
        "evidence": (store.put_evidence, store.list_evidence),
        "routes": (store.record_route, store.list_routes),
        "opportunities": (store.put_opportunity, store.list_opportunities),
    }[collection]
    payload = {
        "evidence": make_evidence,
        "routes": make_observation,
        "opportunities": make_opportunity,
    }[collection]
    put(payload(account_id="foo"))

    assert len(read("foo")) == 1
    with pytest.raises(EvidenceError):
        read("Foo")


def test_the_refusal_happens_before_any_directory_is_created(tmp_path: Path) -> None:
    """校验在 ``mkdir`` **之前**：被拒的调用在盘上什么也不留。

    留一个空目录不是「无害的副作用」—— 它让下一个排查的人看到一棵**有内容的样子**
    的树，而里面什么都没有，于是「这里没写过东西」与「写失败过」看起来一样。

    为了断言的是「校验在 mkdir 之前」而不是「这个函数从来不建目录」，下面配了
    正控：同一棵树上，一次**规范**的读取确实会建出目录。
    """
    store = LocalEvidenceStore(_bare_settings(tmp_path / "refused"))

    with pytest.raises(EvidenceError):
        store.list_evidence("My Account 001")

    assert not _bare_settings(tmp_path / "refused").evidence_dir.exists(), (
        "被拒的调用建出了目录 —— 校验排在了 mkdir 后面"
    )

    ok = _bare_settings(tmp_path / "allowed")
    LocalEvidenceStore(ok).list_evidence("acme-001")
    assert ok.evidence_dir.is_dir(), "正控失效：规范 id 的读取本来就会建目录"


def _bare_settings(root: Path) -> Settings:
    """一棵**还没建过**的运行目录树。

    刻意不用 ``settings`` 夹具：它已经调用过 ``ensure_data_dirs()``，目录早就在了，
    「有没有留下空目录」这件事就测不出来。
    """
    from marketops.config import REPO_ROOT, Settings

    return Settings(
        data_root=root,
        profiles_dir=REPO_ROOT / "profiles",
        profile="uk-acme-natural-grass",
    )
