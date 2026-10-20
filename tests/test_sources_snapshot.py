"""``sources/`` 冻结快照的不变量。

``sources/`` 是业务原始材料的冻结快照（见 ``sources/README.md``）。它之所以值得用测试守，
是因为它承载的是一条**不可回滚**的声明：某个字节在某一天就是它现在这个样子。

两类不变量，分开测，因为它们**能不能独立验证**不一样：

1. **自足不变量** —— 快照与 ``manifest.yaml`` 自洽：哈希对得上、声明的文件都在、
   每一条被选中的消息都有据可查。这类**不需要上游 checkout**，是默认 CI 的部分。
2. **上游对照** —— 快照确实是 ``upstream-records`` 的子集、blob 哈希确实吻合。
   上游不在时这三条显式 skip：一个 canonical repo 的普通 CI 不应该因为旁边没有
   另一个仓库而变红（见 ``docs/SOURCES.md`` §7）。

第 2 类被 skip 不等于被放弃：``test_upstream_checks_are_the_only_optional_ones``
把「哪些检查是可选」这件事本身钉住了，所以少掉一条上游检查会在 diff 里显形。

## 上游对照读的是 **pin**，不是工作树（§2.19）

``manifest.yaml`` 的两条 provenance 都在 **commit 空间**：``source_repository_commit``
是一个 commit，``upstream_blob_sha`` 是一个 git 对象哈希。所以第 2 类判据只能读**对象库**。

它此前读的是工作树文件。检出干净时两者**恰好**相同，而那正是这个 bug 的藏身处：
records 的工作树按本仓规范**可以**带着别人未提交的 WIP（CLAUDE.md：不得 reset /
clean / 不得丢别人的 WIP），于是这条判据会因为一件与快照无关的事转红，而它的报文
说的是「上游被重新同步过」—— 一个关于**历史**的结论，处置是去改 provenance，
而真正该做的事是**什么都别做**。实测 2026-09-22：六条条目的 ``upstream_blob_sha``
**全部**等于 ``rev-parse <commit>:<upstream_path>``；唯一的红来自
``member-d-private`` 的工作树被别人改过。

## 两个变量，两个决定

``MARKETOPS_RECORDS_ROOT`` 回答的是「**自足不变量**那半用哪个检出」，在
``tests/test_business_sources.py`` 里**找不到就 FAIL**。本文件的上游对照另有
``MARKETOPS_UPSTREAM_RECORDS_ROOT``，找不到就 SKIP。两个决定缝在一个变量上会互相
抵消：本仓规范的套件调用把前者指向一个不存在的路径（让需求级不变量脱开真实 records
的在场/缺席），而那**正好**让上游对照每一次都 skip —— 于是这三条从来没跑过，
「绿」是没跑换来的（债表第 5 条）。

**这里不测 prose。** ``sources/README.md`` 讲了七条纪律，但断言「文档里出现过某句话」
只会让改一次措辞就红一次，而它并不能证明那句话是真的。文档的可执行部分在
``tests/test_business_sources.py`` 与各自的扫描器里。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = REPO_ROOT / "sources"
MANIFEST_PATH = SOURCES_DIR / "manifest.yaml"

#: 快照允许的选取方式。``complete-*`` 表示整份材料原样进来；
#: ``keyword-relevant`` 表示按关键词筛过行；``segment-relevant`` 表示按**时间边界**
#: 取一整段（段的起点是可机械判定的日期，段内不做逐条判断）；``none-matched`` 表示
#: 筛过之后一条都不符合。几种的含义差别很大，所以取值是封闭的 ——
#: 出现第六种必须先想清楚它和已有几种的关系。
#:
#: ``segment-relevant`` 与 ``keyword-relevant`` 的分界值得单独说：两者都带
#: ``message_ids``，都不是整份复制，差别在**判据可不可复算**。关键词能让人重跑一遍
#: 词表得到同一份清单；「我读了一遍、挑出相关的那些」不能 —— 后者会把
#: 「什么算相关」这个隐式判据藏进一个没人能复现的动作里。所以把整段按日期取下来、
#: 连同段内的寒暄一起收，是对「判据必须可复算」的让步，不是判断力的缺失。
SELECTION_KINDS = frozenset(
    {
        "complete-chat",
        "complete-document",
        "complete-file",
        "keyword-relevant",
        "segment-relevant",
        "none-matched",
    }
)

#: 会带 ``message_ids`` 的那几种选取方式。**这个集合是分流的依据**：
#: ``complete-*`` 的空列表意思是「清单就是全部」，``none-matched`` 的空列表意思是
#: 「一条都不是」—— 两种语义相反却都是空列表，所以判据分流必须按 selection 走，
#: 不能按「列表空不空」走（``test_selected_message_ids_match_the_file_in_order``）。
SELECTIONS_WITH_MESSAGE_IDS = frozenset({"keyword-relevant", "segment-relevant"})

#: 私聊行必须保留的原始字段（``sources/README.md`` §3）。缺一个，这条消息就不再是证据，
#: 而是我们对它的转述 —— 转述不能用来核对。
REQUIRED_MESSAGE_FIELDS = (
    "chat_id",
    "message_id",
    "create_time",
    "sender",
    "msg_type",
    "content",
)

#: ``manifest.yaml`` 之外，允许出现在 ``sources/`` 根下的解释性文件。
_NON_SNAPSHOT_FILES = frozenset({"manifest.yaml", "README.md"})

#: records 相对前缀。``channel_root`` 必须是其中之一开头 —— 它同时指向上游目录与
#: 上游 git 仓库内路径，绝对路径在这里既无法校验也无法迁移。
RECORDS_RELATIVE_PREFIXES = ("projects/", "information/")

#: 凭据形态。**只管值，不管变量名** —— ``DEEPSEEK_API_KEY`` 这种名字出现在原文里是正常的，
#: 一个跟着值的 key 才是事故。
_CREDENTIAL_SHAPES = (
    (r"sk-[A-Za-z0-9_\-]{20,}", "OpenAI 风格 key"),
    (r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}", "Bearer token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥块"),
    (
        r"(?i)(app_?secret|client_?secret|api_?key|access_?token|password|passwd)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_\-]{16,}",
        "赋值形态的密钥",
    ),
)

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def manifest() -> dict:
    assert MANIFEST_PATH.is_file(), f"{MANIFEST_PATH} 不存在 —— sources/ 没有 provenance 就不是快照"
    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "manifest.yaml 不是 mapping"
    return document


#: 上游对照**自己**的根。与 ``MARKETOPS_RECORDS_ROOT`` 是两个决定，见模块 docstring。
UPSTREAM_ROOT_VAR = "MARKETOPS_UPSTREAM_RECORDS_ROOT"


def _resolve_upstream_root(env: Mapping[str, str], start: Path) -> Path | None:
    """上游对照用哪个 records 检出。**不看** ``MARKETOPS_RECORDS_ROOT``。

    显式指定的目录不存在时返回 ``None``，**不回退**到上溯：一个写错的路径静默退化
    成「碰巧在旁边找到的那个检出」，读的人会以为他跑的是自己点名的那个。上溯只在
    **没有**显式指定时发生。

    ``start`` 是仓库根（不是它的父目录）：上溯从 ``start.parents`` 起，与
    ``tests/test_business_sources.py`` 的 ``_records_candidates`` 同一形状，
    但**探测判据不同且必须不同** —— 那边要 ``projects/`` 与 ``src/``（它找的是
    dossier 落在哪个分支的检出面），这边要 ``information/``（快照的六条来源都在
    那条路径下）。两者相等只是本机当下的巧合。
    """
    override = env.get(UPSTREAM_ROOT_VAR, "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        return root if root.is_dir() else None
    for ancestor in start.parents:
        candidate = ancestor / "records"
        if (candidate / "information").is_dir():
            return candidate
    return None


def _git(root: Path, *args: str) -> tuple[int, bytes]:
    """在 ``root`` 里跑一次 git，返回 ``(返回码, stdout 原始字节)``。

    **字节**而不是 ``str``：六条来源里有两条是图片（``group-image-*``），
    以文本读一遍再写回去会把它们改掉。参数里从不出现工作树的文件路径 ——
    用对象哈希点名，于是也没有「``-C`` 把相对路径拼两遍」那条风险。
    """
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=300, check=False
    )
    return done.returncode, done.stdout


def _commit_exists(root: Path, commit: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")[0] == 0


def _blob_at_commit(root: Path, commit: str, path: str) -> str | None:
    """``<commit>:<path>`` 解析出的 blob 哈希；这个 commit 上没有该路径时 ``None``。"""
    code, out = _git(root, "rev-parse", "--verify", "--quiet", f"{commit}:{path}")
    return out.decode().strip() if code == 0 and out.strip() else None


def _blob_bytes(root: Path, blob_sha: str) -> bytes | None:
    """按对象哈希从对象库取字节；取不到时 ``None``。"""
    code, out = _git(root, "cat-file", "blob", blob_sha)
    return out if code == 0 else None


def _is_git_checkout(root: Path) -> bool:
    """上游对照读对象库，所以它要的是一个**检出**，不是一份解开的目录副本。"""
    code, out = _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == b"true"


def _reachable_from_remote(root: Path, commit: str) -> bool | None:
    """这个 commit 是否在**上游远端**的 main 上；问不出答案时 ``None``。

    判据是本地那份 ``origin/main`` 镜像：一个提交一旦被 fetch 到过，除非有人
    force-push，它就永远是那次 ``origin/main`` 的祖先。所以「是 ``origin/main``
    的祖先」能证明「远端当时有它」。

    而 ``_commit_exists``（本机可读）**证明不了任何关于远端的事**：一个只活在本地
    ``refs/preserve/`` 上的提交，在两件事上的取值正好相反 —— 这正是本函数存在的理由，
    也是 4 条 member-c 条目的 pin 曾经指错的那个形态。
    """
    code, _ = _git(root, "merge-base", "--is-ancestor", commit, "origin/main")
    if code == 0:
        return True
    if code == 1:
        return False
    return None


#: 四条上游对照共用的缺检出理由。**必须出现在测试体里**（见下）。
_NO_UPSTREAM = (
    "没有可用的 upstream-records 检出，上游对照跳过（这是可选检查）。"
    f"要跑它：{UPSTREAM_ROOT_VAR}=/abs/path/to/records"
)


@pytest.fixture(scope="session")
def upstream_root() -> Path | None:
    """``upstream-records`` 检出；没有就返回 ``None``，**由调用方 skip**。

    与 ``tests/test_business_sources.py`` 的 ``records_root`` 找的是同一个东西，
    但**失败语义相反**：那边找不到就 FAIL（需求级不变量查不了），
    这边找不到就 SKIP（上游对照是增强项，不是本仓库的自我一致性）。

    返回 ``None`` 而不是在里面 ``pytest.skip`` 掉自己，是
    ``tests/test_no_stale_assumptions.py::test_every_test_needing_the_upstream_checkout_can_skip``
    要求的：那条判据用 AST 回答「这个测试要不要外部检出」，而一个在夹具里跳过的
    实现会让「哪些测试是可选的」从源码里消失 —— 本仓 CI 必须能独立跑完这件事
    就不再有任何东西守着。所以这里的契约是「答 ``None``」，跳过由每个调用方写。

    显式指定与自动发现在 ``_resolve_upstream_root`` 里是两个分支，且**失败语义
    相同**（都给 ``None``）：不同只在于「写错的路径不会退化成旁边那个检出」。
    """
    return _resolve_upstream_root(os.environ, REPO_ROOT)


def _entry_commit(entry: dict, manifest) -> str:
    """这个条目该用哪个上游 commit：**条目自己的优先**，缺省回退到顶层那个。

    这不是为了方便。``sources/README.md`` §8 第 2 条要求「新条目的
    ``source_repository_commit`` 记下当时 upstream-records 的 HEAD」—— 即每个条目记的是
    **它自己那一版**的上游。把顶层那个值当成「所有条目共用一条上游」，会让后来追加的
    条目被拿去和一条更早的上游比，而失败报文说「blob 已变」（处置：按 §8 追加新条目），
    真实的处置却是「这一条没写 pin」。两种处置相反，所以解析必须在条目这一级发生。

    **解析出的 sha 是不是可读不在这里判** —— 那是 ``_upstream_commit`` 的活，
    它要检出在手。分开是因为「格式不对」与「检出里没有」是两种缺失：
    前者不需要上游就能发现，后者不能。
    """
    commit = str(
        entry.get("source_repository_commit") or manifest.get("source_repository_commit", "")
    )
    if not _FULL_SHA_RE.match(commit):
        pytest.fail(
            f"{entry.get('id')}: 上游 commit 不是 40 位 sha：{commit!r}。"
            "短 sha 会随仓库增长而歧义，分支名会随上游前进而指向别的东西 —— "
            "provenance 不接受一个会自己变的指针。"
        )
    return commit


def _upstream_commit(root: Path, manifest, entry: dict) -> str:
    """上面解析出的那个 commit，并证明它在这个检出里可读。

    单独一层，因为它与「检出不在地」是两种缺失：检出在、声明的 commit 不在
    （浅克隆、换了检出面，或者那条路径上根本不是个 git 仓）会让用到它的那两条判据
    同时读不到东西，而那时报「blob 不符」会把读的人支去查 provenance。

    这两件都不 skip：走到这里说明检出**是**找到的，拿到了却不完整是配置问题，
    静默跳过会读成「跑过了而且绿了」。
    """
    if not _is_git_checkout(root):
        pytest.fail(f"{root} 不是 git 检出 —— 上游对照读对象库，不读工作树")
    commit = _entry_commit(entry, manifest)
    if not _commit_exists(root, commit):
        pytest.fail(
            f"{entry.get('id')}: 声明的 commit {commit[:12]} 不在 {root} 里 —— "
            "上游对照没有可读的对象，这不是「blob 不符」"
        )
    return commit


# --- 1. 自足不变量 ---------------------------------------------------------


def test_every_selection_kind_is_classified_by_whether_it_lists_message_ids():
    """**新加一种 selection 必须被归位**，不能悄悄落进「没人检查那一类」。

    ``SELECTION_KINDS`` 是封闭集，但「封闭」本身只挡住拼错的取值；它挡不住
    「新加了一种、却没被加进任何一个下游判据的分流」—— 那时那条判断会**静默跳过**
    新条目（``if selection != "keyword-relevant": continue`` 的形态），
    于是新条目的 ``message_ids`` 一个字都没被核对过，而套件是绿的。

    **这个空转是实测的，不是推的**（2026-09-23 加 ``segment-relevant`` 时的负对照）：
    把分流退回写死 ``keyword-relevant``、同时把新条目里第一条 ``message_id`` 改成一个
    文件里不存在的值，``test_selected_message_ids_match_the_file_in_order``
    **仍然通过**（1 passed）；只把分流改回按常量判，同一条变异立刻转红。
    所以下面第 3 句是断言，不是戒律 —— 它守的是一个真的空过一次的岔口。

    所以这里断言这个二分覆盖了全部取值：每一种要么会带 ``message_ids``（清单要核对），
    要么是 ``complete-*``（清单必须是空）或 ``none-matched``（清单必须是空）。
    加第七种时，这条会先红，逼着回答「它带不带清单」—— 而不是等它哪天漏过核对。
    """
    without_ids = {kind for kind in SELECTION_KINDS if kind.startswith("complete-")} | {
        "none-matched"
    }
    assert SELECTIONS_WITH_MESSAGE_IDS | without_ids == SELECTION_KINDS, (
        "有 selection 取值没被归位："
        f"未归类 {sorted(SELECTION_KINDS - SELECTIONS_WITH_MESSAGE_IDS - without_ids)}；"
        f"多出来 {sorted(SELECTIONS_WITH_MESSAGE_IDS | without_ids - SELECTION_KINDS)}"
    )
    assert not (SELECTIONS_WITH_MESSAGE_IDS & without_ids), (
        "同一种 selection 不能既带清单又不带清单 —— 两边的语义会各自漂移"
    )


def test_manifest_declares_its_schema(manifest):
    assert manifest.get("schema") == "marketops/business-source-snapshot.v1", (
        "schema 变了就必须同时改 consumers；不声明 schema 的快照文件无法被程序消费"
    )


def test_source_repository_commit_is_a_full_sha(manifest):
    commit = manifest.get("source_repository_commit", "")
    assert _FULL_SHA_RE.match(str(commit)), (
        f"source_repository_commit 必须是完整的 40 位 sha，得到 {commit!r}。"
        "短 sha 会随仓库增长而歧义 —— provenance 不接受歧义。"
    )


def test_every_entry_resolves_to_a_full_sha_commit(manifest):
    """每条条目都要解析出一个完整的 40 位 sha —— **不需要上游检出**就能判。

    顶层那个 pin 有 ``test_source_repository_commit_is_a_full_sha`` 守着，但那是
    **回退值**；条目自己写的那一份是另一处输入，而它以前没人看格式 —— 因为解码它的
    那个函数还需要检出在手才不会 skip，于是「pin 写成了分支名」这件事只在有上游的
    机器上才会被发现，且那时的报文是「blob 已变」。这条把格式判断搬到自足的一半。

    不聚合报文：``_entry_commit`` 自己那条失败信息已经点名了是哪个条目、值是什么，
    而一条 pin 写错只有一种修法。把它包进 try 里收集会把那份信息吞掉。
    """
    entries = _declared_entries(manifest)
    for entry in entries:
        _entry_commit(entry, manifest)
    assert entries, "没有条目可解析 —— 上面那个循环为空时这条会假绿"


#: 快照条目才有的字段。声明为「刻意未捕获」的条目**一个都不许带** ——
#: 两个状态都写会让「它到底收没收」在文件里没有唯一答案。
_SNAPSHOT_ONLY_FIELDS = ("snapshot_path", "snapshot_sha256", "selection", "message_ids")


def _declared_entries(manifest: dict) -> list[dict]:
    """返回所有**已捕获**条目 —— 下游每条判据都在文件上做。

    条目分两类，且只有两类：

    * **已捕获**：有 ``snapshot_path`` 与封闭取值里的 ``selection``；
    * **刻意未捕获**：有 ``why_not_captured``，且不带任何快照字段。

    两类都没有的条目仍然失败。这一点是承重的：没有它，「删掉 snapshot_path」
    就会成为让整套文件判据静默放行的开关，而报错会变成「没有条目可检查」。
    未捕获条目的形状（理由、locator、无快照字段）由
    :func:`test_an_uncaptured_entry_declares_why_and_no_snapshot_fields` 守着。
    """
    entries = manifest.get("sources")
    assert isinstance(entries, list) and entries, "manifest.sources 必须是非空列表"
    captured: list[dict] = []
    for entry in entries:
        assert isinstance(entry, dict), f"sources 条目不是 mapping: {entry!r}"
        assert entry.get("id"), f"sources 条目没有 id: {entry!r}"
        if entry.get("why_not_captured"):
            present = [field for field in _SNAPSHOT_ONLY_FIELDS if entry.get(field)]
            assert not present, (
                f"{entry['id']} 声明了 why_not_captured，却仍带着快照字段 {present} —— "
                "「未捕获」与「有快照」只能有一个是真的"
            )
            continue
        assert entry.get("snapshot_path"), f"{entry.get('id')} 没有 snapshot_path"
        assert entry.get("selection") in SELECTION_KINDS, (
            f"{entry.get('id')} 的 selection={entry.get('selection')!r} 不在封闭取值里"
        )
        captured.append(entry)
    return captured


def test_an_uncaptured_entry_declares_why_and_no_snapshot_fields(manifest):
    """「刻意未捕获」必须是一条**记录过的决定**，不是缺字段的另一种写法。

    ``member-c-scene-email-pool-sheet`` 是一条活文档，且正文很可能是联系方式；
    本仓对它的边界是**只记 locator、正文不入库**（见 manifest 里那条
    ``why_not_captured``）。所以「没有 snapshot_path」是结论，不是遗漏。这条
    判据要求那半边同样完整：理由可复核、locator 还在、没有任何快照字段。缺了
    理由又没有快照的条目，仍由 ``_declared_entries`` 判失败 —— 两处一起把
    「删掉 snapshot_path」挡在门外。
    """
    uncaptured = [
        entry
        for entry in manifest.get("sources", [])
        if isinstance(entry, dict) and entry.get("why_not_captured")
    ]
    assert uncaptured, (
        "manifest 里没有 why_not_captured 条目 —— 若最后一条已被正式捕获，"
        "请连同这条测试与 _declared_entries 的分类一起复核，而不是让它悄悄失去执行器"
    )
    for entry in uncaptured:
        reason = str(entry.get("why_not_captured", "")).strip()
        assert len(reason) >= 80, (
            f"{entry['id']}: why_not_captured 太短（{len(reason)} 字）——"
            "「不收」必须写出可复核的理由，而不是一句状态"
        )
        assert entry.get("locator"), (
            f"{entry['id']}: 未捕获条目必须留下 locator —— 否则它连「在哪」都没有"
        )
        present = [field for field in _SNAPSHOT_ONLY_FIELDS if entry.get(field)]
        assert not present, (
            f"{entry['id']}: 声明未捕获却带着 {present} —— 两种状态只能有一个"
        )


def test_a_source_without_a_snapshot_must_say_why_it_is_not_captured():
    """分类是二选一：缺 ``snapshot_path`` 且没有理由，仍然失败。

    这是上面那条分类的**反向控制**。如果「没有 snapshot_path」本身就能让条目被
    跳过，那整套文件判据可以被逐条静默关掉，而报告只会说「没有条目可检查」。
    """
    captured = {"id": "c", "snapshot_path": "sources/x", "selection": "complete-chat"}
    uncaptured = {
        "id": "u",
        "why_not_captured": "活文档，正文不入库；边界说明见 docs/。",
        "locator": "https://example.feishu.cn/sheets/TOKEN",
    }
    assert _declared_entries({"sources": [captured, uncaptured]}) == [captured]

    with pytest.raises(AssertionError, match="没有 snapshot_path"):
        _declared_entries({"sources": [{"id": "x", "selection": "complete-chat"}]})

    with pytest.raises(AssertionError, match="只能有一个是真的"):
        _declared_entries(
            {"sources": [dict(uncaptured, snapshot_path="sources/u")]}
        )


def test_every_declared_snapshot_path_exists(manifest):
    missing = [
        f"{entry['id']}: {entry['snapshot_path']}"
        for entry in _declared_entries(manifest)
        if not (REPO_ROOT / entry["snapshot_path"]).is_file()
    ]
    assert not missing, "manifest 声明了不存在的快照文件：\n  " + "\n  ".join(missing)


def test_snapshot_hashes_match_the_files(manifest):
    """**hash integrity**：manifest 里写下的 sha256 必须就是文件现在的 sha256。

    这条是整份快照的证据力所在：它让「我没改过它」从一个承诺变成一次计算。
    """
    offenders: list[str] = []
    checked = 0
    for entry in _declared_entries(manifest):
        path = REPO_ROOT / entry["snapshot_path"]
        if not path.is_file():
            continue
        declared = str(entry.get("snapshot_sha256", ""))
        assert _SHA256_RE.match(declared), (
            f"{entry['id']} 的 snapshot_sha256 不是 64 位小写 hex: {declared!r}"
        )
        actual = _sha256(path)
        checked += 1
        if actual != declared:
            offenders.append(f"{entry['id']}: 声明 {declared[:16]}… 实际 {actual[:16]}…")
    assert checked == len(_declared_entries(manifest)), (
        "有声明条目被跳过了 —— 少了几个 checked 就少几份证明"
    )
    assert not offenders, (
        "快照被改过（或 manifest 被改过）:\n  " + "\n  ".join(offenders)
    )


def test_every_file_under_sources_is_declared(manifest):
    """``sources/`` 里不能有 manifest 不认识的文件。

    防的是「顺手往里放一份材料」—— 那份材料没有 upstream_blob_sha、没有 selection、
    没有哈希，它在仓库里看着和别的一样，但它不是快照，是不可追溯的二手材料。
    """
    declared = {entry["snapshot_path"] for entry in _declared_entries(manifest)}
    undeclared: list[str] = []
    for path in sorted(SOURCES_DIR.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if path.name in _NON_SNAPSHOT_FILES and path.parent == SOURCES_DIR:
            continue
        if relative not in declared:
            undeclared.append(relative)
    assert not undeclared, (
        "sources/ 下有未在 manifest.yaml 中声明的文件：\n  "
        + "\n  ".join(undeclared)
        + "\n每一份进入 sources/ 的材料都必须有 provenance 条目。"
    )


def test_manifest_channels_are_well_formed_and_covered(manifest):
    """``approved_channels`` 必须自洽，且每条都被至少一个快照条目覆盖。

    通道集合的定义点是 ``manifest.yaml`` —— 它是那个「加一条通道」时必须改的文件，
    而且它同时带着 label 与 chat_id。``tests/test_business_sources.py`` 从它读取，
    不另立一份，因为两份定义会漂移，而漂移的那份是没人看的那份。

    这里不重复断言「通道是合法的」（那是 test_business_sources 的活），
    只断言这份声明自身完整、无重复、无未覆盖项。
    """
    declared = manifest.get("approved_channels")
    assert isinstance(declared, list) and declared, "manifest 必须声明 approved_channels"
    for channel in declared:
        assert channel.get("id"), f"approved_channels 条目缺 id: {channel!r}"
        assert channel.get("chat_id"), f"{channel.get('id')} 缺 chat_id"
        # label 不是装饰：非文件型条目只有 id 与 chat_id 时，人无法读懂这是个什么通道。
        assert channel.get("label"), f"{channel.get('id')} 缺 label"
        # channel_root 是 source_ref 合法性判定的作用域，必须以 / 结尾 ——
        # 否则 `.../member-a` 会前缀匹配上 `.../member-a` 这种不存在的邻居。
        root = str(channel.get("channel_root", ""))
        assert root.endswith("/"), f"{channel.get('id')} 的 channel_root 必须以 / 结尾: {root!r}"
        assert root.startswith(RECORDS_RELATIVE_PREFIXES), (
            f"{channel.get('id')} 的 channel_root 必须以 records 相对前缀开头 "
            f"{RECORDS_RELATIVE_PREFIXES}，得到 {root!r}"
        )

    declared_ids = [str(channel["id"]) for channel in declared]
    assert len(set(declared_ids)) == len(declared_ids), f"approved_channels 有重复 id: {declared_ids}"

    entries = _declared_entries(manifest)
    declared_chat_ids = {str(channel["chat_id"]) for channel in declared}
    covered = {str(entry.get("chat_id")) for entry in entries}

    uncovered = declared_chat_ids - covered
    assert not uncovered, f"以下 approved 通道没有任何快照条目: {sorted(uncovered)}"
    stray = covered - declared_chat_ids
    assert not stray, (
        f"以下快照条目的 chat_id 不在 approved_channels 里: {sorted(stray)} —— "
        "快照里进来了一个没被批准的来源"
    )
    # entry.id 与 channel.id 是同一套命名，两边各写一遍就是为了在这里撞上。
    assert covered == declared_chat_ids and len(entries) >= len(declared), (
        f"快照条目 {len(entries)} 条 / 通道 {len(declared)} 条：至少每条通道要有一条快照"
    )


def test_every_snapshot_entry_lives_under_its_channels_root(manifest):
    """条目的 ``upstream_path`` 必须落在它自己那条通道的 ``channel_root`` 下面。

    ``channel_root`` 是给 ``source_ref`` 判合法性用的（``test_business_sources`` 读它）。
    如果它与 ``upstream_path`` 对不上 —— 少一级目录、chat_id 抄错一位、通道根写成了
    另一条通道 —— 那条通道的合法性判定就会在**错误的目录**上生效：真实的证据被判非法，
    而另一条通道的目录被误判为合法。两个方向都是静默的，所以这里显式撞一次。
    """
    channels = {str(c["id"]): c for c in manifest.get("approved_channels", [])}
    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        channel = channels.get(str(entry.get("id")))
        if channel is None:
            # SOP 与图片条目的 id 是材料名，不是通道名：它们按 chat_id 归属通道。
            channel = next(
                (c for c in channels.values() if str(c.get("chat_id")) == str(entry.get("chat_id"))),
                None,
            )
        if channel is None:
            offenders.append(f"{entry['id']}: 找不到归属通道")
            continue
        root = str(channel.get("channel_root", ""))
        upstream = str(entry.get("upstream_path", ""))
        if not root or not upstream.startswith(root):
            offenders.append(f"{entry['id']}: upstream_path {upstream!r} 不在 {root!r} 之下")
    assert not offenders, "\n  ".join(offenders)


# --- 2. 消息来源（message provenance）-------------------------------------


def _jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - 失败即缺陷
            pytest.fail(f"{path.name}:{lineno} 不是合法 JSON：{exc}", pytrace=False)
        assert isinstance(record, dict), f"{path.name}:{lineno} 不是 JSON object"
        records.append(record)
    return records


def test_private_chat_lines_keep_every_original_field(manifest):
    """私聊快照行必须保留六个原始字段。

    这条防的是「整理」：把 sender 拍平成一个人名、把 content 里的换行清掉 ——
    做完之后文件更好读了，但它不再是原文的副本，而我们对它的任何引用都无法回查。
    """
    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        if entry.get("type") != "feishu-p2p-chat":
            continue
        if not entry.get("message_ids"):
            continue  # 零命中的那一份，没有行可查
        path = REPO_ROOT / entry["snapshot_path"]
        for lineno, record in enumerate(_jsonl(path), 1):
            missing = [field for field in REQUIRED_MESSAGE_FIELDS if field not in record]
            if missing:
                offenders.append(f"{entry['id']}:{lineno} 缺字段 {missing}")
    assert not offenders, "\n  ".join(offenders)


def test_selected_message_ids_match_the_file_in_order(manifest):
    """``message_ids`` 必须是快照行的 message_id，**同序等长**。

    等长说明没有多出来的行；同序说明这个列表是这份文件的行清单，
    而不是「大概选了这些」的注记。
    """
    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        # 只有**选择复制**才有 message_ids 可言。``complete-*`` 的空列表意思是
        # 「清单就是全部」（见 test_complete_selections_declare_no_filter），
        # 不是「一条都没选」—— 后者是 ``none-matched``，两者都是空列表但语义相反，
        # 所以这里按 selection 分流，不按列表是否为空分流。
        #
        # 分流用 ``SELECTIONS_WITH_MESSAGE_IDS`` 而不是写死 ``"keyword-relevant"``：
        # 写死的那一版对**任何新加的、也带清单的** selection 都是静默跳过 ——
        # 那正是「判据在、但它不为这条跑」的样子（段式选取加进来时实测过一次）。
        if entry.get("selection") not in SELECTIONS_WITH_MESSAGE_IDS:
            continue
        declared = entry.get("message_ids")
        if declared is None:
            continue
        path = REPO_ROOT / entry["snapshot_path"]
        actual = [record["message_id"] for record in _jsonl(path)]
        if list(declared) != actual:
            offenders.append(
                f"{entry['id']}: manifest 列了 {len(declared)} 条，"
                f"文件里有 {len(actual)} 条；"
                f"{'顺序或内容不一致' if len(declared) == len(actual) else '数量不一致'}"
            )
        if entry.get("snapshot_message_count") != len(actual):
            offenders.append(
                f"{entry['id']}: snapshot_message_count="
                f"{entry.get('snapshot_message_count')} 与实际 {len(actual)} 不符"
            )
        if entry.get("upstream_message_count") is not None:
            assert entry["upstream_message_count"] >= len(actual), (
                f"{entry['id']}: 选中的行比上游总数还多，说明上游计数是错的"
            )
    assert not offenders, "\n  ".join(offenders)


def test_selections_with_message_ids_declare_their_criterion(manifest):
    """带清单的选取必须写出**判据**，不能只留一份清单。

    清单回答「选了哪几条」，判据回答「凭什么选这些」。只有清单的话，下一个人
    能核对「文件与清单一致」，却无法回答「漏没漏」—— 而后者才是这份快照能不能被
    信任的关键。判据为空（``[]`` 或缺键）正是 ``none-matched`` 那种「查过、没有」
    的写法，把带清单的条目写成那样会让两种语义在文件里长得一样。
    """
    offenders = [
        str(entry.get("id"))
        for entry in _declared_entries(manifest)
        if entry.get("selection") in SELECTIONS_WITH_MESSAGE_IDS
        and not entry.get("selection_criteria")
    ]
    assert not offenders, (
        "以下条目按关键词/时段选了行，却没说判据是什么：\n  " + "\n  ".join(offenders)
    )


def test_complete_selections_declare_no_filter(manifest):
    """``complete-*`` 不许带筛选清单 —— 带了就说明它不是完整复制。"""
    offenders = [
        entry["id"]
        for entry in _declared_entries(manifest)
        if str(entry.get("selection", "")).startswith("complete") and entry.get("message_ids")
    ]
    assert not offenders, (
        "以下条目声明为完整复制，却列了 message_ids —— 二者只能有一个是真的：\n  "
        + "\n  ".join(offenders)
    )


def test_complete_chat_line_count_matches_upstream_count(manifest):
    """完整复制的群聊：文件行数必须等于上游当时的总条数。"""
    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        if entry.get("selection") != "complete-chat":
            continue
        actual = len(_jsonl(REPO_ROOT / entry["snapshot_path"]))
        if actual != entry.get("upstream_message_count"):
            offenders.append(
                f"{entry['id']}: 快照 {actual} 行 vs 上游记录 {entry.get('upstream_message_count')} 条"
            )
    assert not offenders, "\n  ".join(offenders)


def test_a_none_matched_selection_is_zero_bytes_and_explains_itself(manifest):
    """零命中的那一份必须是**真零字节**，且必须写明理由。

    这一条守的是一个诚实的结果：``none-matched`` 意味着「查过了，没有」。
    它很容易被后来的人「修好」—— 要么塞一条合成的说明记录（破坏了「每一行都是原文」），
    要么干脆删掉这个目录（于是「查过没有」变成「没查过」）。两者都是把结论丢掉了。
    """
    empty = [e for e in _declared_entries(manifest) if e.get("selection") == "none-matched"]
    assert empty, "没有任何 none-matched 条目 —— 如果扫描结论变了，请改 manifest 而不是删掉这条测试"
    for entry in empty:
        path = REPO_ROOT / entry["snapshot_path"]
        assert path.read_bytes() == b"", (
            f"{entry['id']}: 声明 none-matched 却不是零字节 —— "
            "零命中的快照里不该有任何行（哪怕是一行说明，也会破坏「每一行都是原文」）"
        )
        assert not entry.get("message_ids"), f"{entry['id']}: none-matched 却列了 message_ids"
        note = str(entry.get("selection_note", ""))
        assert len(note) >= 80, (
            f"{entry['id']}: selection_note 太短（{len(note)} 字）。"
            "「为什么一条都没选」必须写清楚，否则下一个人只能重查一遍"
        )


# --- 3. 凭据 ---------------------------------------------------------------


def test_no_credential_shaped_string_in_sources(manifest):
    """快照里不得出现凭据。**只管值，不管变量名。**

    原文里出现 ``DEEPSEEK_API_KEY`` 这样的**名字**是正常的（那是在讨论配置）；
    跟着值的才是事故。上游 archive 已经做过脱敏，本条是第二道闸 ——
    因为快照是复制出来的，复制这条路径绕过了上游的脱敏检查点。
    """
    offenders: list[str] = []
    for path in sorted(SOURCES_DIR.rglob("*")):
        if not path.is_file() or path.name in _NON_SNAPSHOT_FILES:
            continue
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".pdf"}:
            continue  # 二进制材料：由上游脱敏负责，本测试只做文本关口
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in _CREDENTIAL_SHAPES:
            for match in re.finditer(pattern, text):
                # 只记位置，不记值 —— 报告里也不写
                line = text[: match.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line}: 疑似{label}"
                    "（值不写入报告）"
                )
    assert not offenders, (
        "sources/ 里出现疑似凭据：\n  " + "\n  ".join(offenders)
        + "\n凭据永不进仓（CLAUDE.md）。若这是脱敏漏掉的一处，去 upstream-records 侧补脱敏后重新快照。"
    )


# --- 4. 扫描范围 -----------------------------------------------------------

# 「``sources/`` 不在 portability 扫描范围内」这条边界断言放在
# ``tests/test_portability.py::test_evidence_tree_is_outside_the_scanned_roots``。
# 放那里而不是这里，是因为 ``SOURCE_ROOTS`` / ``EXTRA_ROOTS`` 定义在那个文件里 ——
# 想改扫描范围的人打开的就是那个文件，边界应当在他要改的那一行旁边。
# 从测试模块里 import 另一个测试模块也可以做到，但那会让这条断言依赖导入机制，
# 而它要守的是一个常量取值。


# --- 5. 上游对照（可选）----------------------------------------------------


def test_upstream_checks_are_the_only_optional_ones():
    """把「哪些检查是可选」钉住。

    上游对照是增强项，上游不在时显式 skip；但**哪些**检查属于增强项不能是隐含的。
    少掉一条上游检查会在 diff 里显形 —— 这是这条断言的全部作用。
    """
    optional = {
        name
        for name, value in globals().items()
        if name.startswith("test_upstream_") and callable(value)
    }
    expected = {
        "test_upstream_blob_hashes_match",
        "test_upstream_verbatim_subset",
        "test_upstream_decoded_locators_point_at_real_files",
        "test_upstream_entry_pins_are_reachable_from_the_remote",
        # 本条自己也在 test_upstream_ 前缀下：它断言的是这份集合，所以必须入列。
        "test_upstream_checks_are_the_only_optional_ones",
    }
    assert optional == expected, (
        f"以上游对照为名的测试集合变了：多出 {optional - expected}，少了 {expected - optional}。"
        "少一条就少一份证明；多一条要先说清它是不是可选的。"
    )


def _entry_upstream_path(entry: dict) -> Path | None:
    """把 manifest 条目折成上游文件路径；非文件型条目返回 None。"""
    upstream = entry.get("upstream_path")
    if not upstream:
        return None
    return Path(upstream)


def test_upstream_blob_hashes_match(upstream_root, manifest):
    """快照必须等于**声明的那个 commit 上**那个 blob 的内容。

    ``upstream_blob_sha`` 是 git object 的 sha（``blob <len>\\0<content>`` 的哈希），
    与文件的 sha256 不是一回事：前者证明「是记录里那一版」，后者证明「文件没被动过」。
    两个都要：一个锚住来源，一个锚住此刻。

    读对象库而不是读工作树 —— 后者会因为别人未提交的 WIP 转红，而报文说的是
    「上游被重新同步过」（见模块 docstring §2.19）。三种失败分得开，因为处置不同：
    声明的 commit 不在检出里（``_upstream_commit`` 判）、路径在这个 commit 上不存在
    （manifest 的 ``upstream_path`` 抄错了）、路径解析出的 blob 不是声明那个
    （上游在那个 commit 之后被重写过，按 ``sources/README.md`` §8 **追加**新条目）。

    commit **逐条目解析**（``_entry_commit``），不在循环外解析一次：一个条目一条 pin
    是 §8 第 2 条的形态，循环外用一次会让后来追加的条目永远和第一条的上游比。
    """
    if upstream_root is None:
        pytest.skip(_NO_UPSTREAM)

    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        upstream = _entry_upstream_path(entry)
        if upstream is None:
            continue
        commit = _upstream_commit(upstream_root, manifest, entry)
        declared = str(entry.get("upstream_blob_sha", ""))
        if not _FULL_SHA_RE.match(declared):
            offenders.append(f"{entry['id']}: upstream_blob_sha 不是 40 位 sha")
            continue
        actual = _blob_at_commit(upstream_root, commit, upstream.as_posix())
        if actual is None:
            offenders.append(f"{entry['id']}: 这个 commit 上没有 {upstream}")
            continue
        if actual != declared:
            offenders.append(
                f"{entry['id']}: 上游 blob 已变（声明 {declared[:12]}… 实际 {actual[:12]}…）"
            )
            continue
        # 上面比的是 git 自己说的那个哈希；这一条比的是**读回来的字节**。
        # 它是对**读取器**的断言，不是对上游的：读错对象、把二进制当文本读、
        # 读串了行，都落在这里而不是落在上面那一句。
        content = _blob_bytes(upstream_root, declared)
        if content is None or hashlib.sha1(  # noqa: S324 - git 用 sha1，这里在复算 git 的算法
            b"blob %d\0" % len(content) + content
        ).hexdigest() != declared:
            offenders.append(f"{entry['id']}: 读回来的字节不是被点名的那个对象（读取器的问题）")
    assert not offenders, (
        "上游 blob 与快照声明不符：\n  " + "\n  ".join(offenders)
        + "\n按 sources/README.md §8 追加新条目，不要改写旧的。"
    )


def test_upstream_verbatim_subset(upstream_root, manifest):
    """每一行快照都必须是上游对应文件的**逐字节一行**。

    这是「选择复制」这个词的全部含义：我们删掉了一些行，但没有改写任何一行。

    读的仍然是**对象库里的那个 blob**（按 ``upstream_blob_sha`` 点名），所以理由与
    上一条相同。而这里**不解析 commit、也不比哈希**：那条路径（声明落后于上游、
    路径在同 commit 上不存在）由 ``test_upstream_blob_hashes_match`` 报，这条只管
    「快照里的行是不是原文」。两者红的原因不同，报文就不该在这里各说一遍。
    """
    if upstream_root is None:
        pytest.skip(_NO_UPSTREAM)

    offenders: list[str] = []
    for entry in _declared_entries(manifest):
        upstream = _entry_upstream_path(entry)
        if upstream is None or entry.get("type") not in {"feishu-p2p-chat", "feishu-group-chat"}:
            continue
        declared = str(entry.get("upstream_blob_sha", ""))
        if not _FULL_SHA_RE.match(declared):
            continue
        up_bytes = _blob_bytes(upstream_root, declared)
        if up_bytes is None:
            continue
        up_lines = {line for line in up_bytes.split(b"\n") if line.strip()}
        snap_bytes = (REPO_ROOT / entry["snapshot_path"]).read_bytes()
        for lineno, line in enumerate(
            (ln for ln in snap_bytes.split(b"\n") if ln.strip()), 1
        ):
            if line not in up_lines:
                offenders.append(f"{entry['id']}:{lineno} 不是上游任何一行的原文")
    assert not offenders, (
        "快照行被改写过（不再是上游的逐字节副本）:\n  " + "\n  ".join(offenders[:10])
    )


def test_upstream_decoded_locators_point_at_real_files(upstream_root, manifest):
    """manifest 里那些**解析后**的定位符必须真的指到文件。

    ``upstream_path`` 是给人读的字符串；如果它写错了（少一级目录、chat_id 抄错一位），
    上面两条检查会撞在「这个 commit 上没有它」上，读起来像是上游缺文件，而不是我们
    抄错了。这条把两件事分开：先证明路径本身在这个 commit 上可达。

    「可达」是**这个 commit 上的可达**，与上一条同一个理由；「这个 commit」也是
    **逐条目**解析出来的那个（``_entry_commit``），所以报文里带上 commit 的前 12 位 ——
    一条 pin 写错时，读的人要能一眼看出它问的是哪一版，而不是去猜顶层那个值。

    另一处必须一起看的是时间方向：路径现在不在、但在这个 commit 上在 —— 那是上游
    后来删了它，与本仓库抄错一位是两件事；反过来，路径现在在、但在这个 commit 上不在，
    说明 manifest 记的 provenance 落后于上游。读工作树会把这两个方向都答反。
    """
    if upstream_root is None:
        pytest.skip(_NO_UPSTREAM)

    unreachable: list[str] = []
    for entry in _declared_entries(manifest):
        path = _entry_upstream_path(entry)
        if path is None:
            continue
        commit = _upstream_commit(upstream_root, manifest, entry)
        if _blob_at_commit(upstream_root, commit, path.as_posix()) is None:
            unreachable.append(f"{entry['id']}: {path}（commit {commit[:12]}）")
    assert not unreachable, (
        "manifest 的 upstream_path 在它自己声明的 commit 上指不到文件：\n  "
        + "\n  ".join(unreachable)
    )


def test_upstream_entry_pins_are_reachable_from_the_remote(upstream_root, manifest):
    """每条 pin 必须在**上游远端**可达 —— 本机读得到，不等于别人 checkout 得到。

    上面那三条都读**本机检出里的对象库**，所以它们对一个只活在本地 ref 上的提交
    照常绿。``manifest.yaml`` 头部早把这件事记成一条已知缺陷（顶层 ``b81eb776…``：
    records 的历史 2026-09-22 被重写过，对象还在、远端早没了）—— 但它此前**只写在
    散文里**，没有任何东西会响。

    症状是实测出来的，而且比「会红」更糟（变异对照：把一条 pin 退回旧值，另三条
    上游对照 **1 failed, 7 passed** —— 红的是本条，那三条照常绿）：
    **在拥有那个对象的机器上，它永远绿**；只有在别人的检出上（对象取不到）才变成
    ``_upstream_commit`` 的「声明的 commit 不在检出里」—— 一句关于**配置**的报文。
    也就是说，这个形态在唯一能修它机器上没有任何信号。

    确实又发生过一次，是这条判据的来历：4 条 member-c 条目的 pin 曾指向
    ``e9ce8b7…`` —— records 2026-09-23 做发布面投影时改写掉的提交，本机可读
    （对象 + 一个 ``refs/preserve/`` ref）、远端不可达。pin 已随之迁到承载同样字节的
    ``e6c8c81c…``（本仓引用的四个路径在新旧提交的树里逐字节相同，实测）。

    豁免恰好一个值：manifest 顶层那个历史回退值。它是**被记录过的**缺陷，改写它等于
    把「当时读的是哪一版」删掉；豁免在这里写成一次显式比较，于是「除它以外还有别的
    不可达 pin」仍然会红。
    """
    if upstream_root is None:
        pytest.skip(_NO_UPSTREAM)

    fallback = str(manifest.get("source_repository_commit", ""))
    unreachable: list[str] = []
    for entry in _declared_entries(manifest):
        # 用 _upstream_commit 而不是 _entry_commit：它一并判「格式对不对」与
        # 「本机读不读得到」，两种缺失各有各的报文。走到这里说明这两件都过了，
        # 剩下要问的只有「远端有没有」。
        commit = _upstream_commit(upstream_root, manifest, entry)
        verdict = _reachable_from_remote(upstream_root, commit)
        if verdict is None:
            pytest.fail(
                f"{upstream_root} 里问不出「{commit[:12]} 是否在 origin/main 上」："
                "这个检出的 origin/main 镜像不可用。这不是「pin 不可达」——"
                "读不出来与读出来不相等是两个诊断。"
            )
        if verdict or commit == fallback:
            continue
        unreachable.append(f"{entry['id']}: {commit[:12]}")
    assert not unreachable, (
        "这些 pin 在本机可读、但在**上游远端**不可达 —— 别人 clone 了 records 也取不到"
        "这些字节，provenance 指不到东西（而在本机它本来一点信号都没有）：\n  "
        + "\n  ".join(unreachable)
    )


# --- 6. 上游对照背后的机制本身 ---------------------------------------------
#
# 上面那四条判据要跑得起来，靠三件**不在它们里面**的事：谁决定用哪个检出、
# **每个条目用哪个 commit**、以及字节从哪儿读。这三件事各有自己的取舍，各有自己的
# 名字，所以各有自己的判据 ——「没有执行器就只是自觉」在这里的具体形态是：
# 机制写错了，上面四条判据会**照常绿**（读到的是同一个东西，或者根本没跑），
# 而那时没有任何东西会响。
#
# 中间那件（逐条目的 commit）是多条目共用一份 manifest 之后才成为一件事的：
# 只有一个 pin 的时候，「用哪个 commit」不需要回答。
#
# 这一节的判据**不是可选的**：它们只用 ``tmp_path`` 与纯函数，不读任何真实 records。
# 名字刻意不带 ``test_upstream_`` 前缀 —— 那个前缀在
# ``test_upstream_checks_are_the_only_optional_ones`` 里被断言等于「可选那一组」。


def _fake_workspace(base: Path, *, information: bool) -> Path:
    """造一个「工作区 + 检出」的形状，返回其中的仓库根（也就是 ``start``）。

    造得出来是因为 ``_resolve_upstream_root`` 只看两件事：起点往上数的目录名，
    以及 ``records/information/`` 在不在。于是这一节的断言**不依赖本机的
    records 在哪**，也就不会在另一台机器上因为旁边恰好有个 records 而变绿。
    """
    checkout = base / "workspace" / "worktrees" / "marketops" / "mvp"
    checkout.mkdir(parents=True)
    records = base / "workspace" / "records"
    (records / "information" if information else records).mkdir(parents=True, exist_ok=True)
    return checkout


def test_the_discovery_finds_a_records_checkout_carrying_information(tmp_path: Path) -> None:
    """正对照：没有变量时上溯真的找得到。**没有这条，下面两条可以是空言。**

    一个「永远返回 None」的实现会让「不读 ``MARKETOPS_RECORDS_ROOT``」与
    「不回退」两条判据**同时通过** —— 它们断言的都是「不是那个」，而 ``None``
    不是任何东西。
    """
    start = _fake_workspace(tmp_path, information=True)

    assert _resolve_upstream_root({}, start) == tmp_path / "workspace" / "records"


def test_a_records_directory_without_information_is_not_the_upstream(tmp_path: Path) -> None:
    """诱饵：``records/`` 在、``records/information/`` 不在 ⇒ 上溯必须越过去。

    探测判据是 ``information/`` 而不是「名字叫 records」：上游对照要的是那六条来源
    所在的路径，一个同名但装别的东西的目录不是它。断言写成**不等于**而不是
    ``is None``，因为之上还可能真有别人的 records —— 那是环境，不是这条判据的事。
    """
    start = _fake_workspace(tmp_path, information=False)

    assert _resolve_upstream_root({}, start) != tmp_path / "workspace" / "records"


def test_the_invariant_half_variable_does_not_decide_the_enhanced_half(tmp_path: Path) -> None:
    """**债表第 5 条的机制本身。**

    ``MARKETOPS_RECORDS_ROOT`` 是自足不变量那半的输入（那边找不到就 FAIL），
    上游对照不读它。第二个断言是这条判据的**分辨力**所在：它指向一个**真的存在
    但是另一个**目录 —— 一个「先看 RECORDS_ROOT」的实现会返回那个，于是转红；
    而只断言「设与不设都返回同一个值」的写法对一个恰好把两者缝回去的实现是绿的
    （那个实现在两种调用下都返回它先找到的那个）。
    """
    start = _fake_workspace(tmp_path, information=True)
    discovered = tmp_path / "workspace" / "records"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert _resolve_upstream_root({"MARKETOPS_RECORDS_ROOT": "/tmp/nonexistent-records"}, start) == (
        discovered
    ), "把 RECORDS_ROOT 指向一个不存在的路径，上游对照不该跟着一起消失（这就是债表第 5 条）"
    assert _resolve_upstream_root({"MARKETOPS_RECORDS_ROOT": str(elsewhere)}, start) == discovered, (
        "上游对照读了 MARKETOPS_RECORDS_ROOT —— 两个决定又缝回一个变量了"
    )
    assert _resolve_upstream_root({UPSTREAM_ROOT_VAR: str(elsewhere)}, start) == elsewhere


def test_an_explicit_upstream_root_does_not_fall_back_to_auto_discovery(tmp_path: Path) -> None:
    """点名了一个不存在的路径 ⇒ ``None``，**不要**退化成旁边那个检出。

    退化看起来是「尽力而为」，代价是读的人无法从他给的那个值反推跑的是哪个检出：
    他把 ``/typo/records`` 敲错一位，得到的是旁边那个 —— 而输出里没有任何一处
    提到这件事。让它在夹具那里 FAIL 也不从这里回退，正是为了这个。
    """
    start = _fake_workspace(tmp_path, information=True)

    assert _resolve_upstream_root({UPSTREAM_ROOT_VAR: str(tmp_path / "typo")}, start) is None


def test_the_reader_reads_the_object_store_not_the_working_tree(tmp_path: Path) -> None:
    """**正对照**：工作树被改过之后，读到的仍然是那个 commit 里的字节。

    这条判据在改动**之前**的实现上会红：旧实现 ``read_bytes()`` 读磁盘文件。
    段末那一条断言（读回来的**不**等于工作树现在的字节）是分辨力本身 —— 只写
    「等于提交时的内容」的话，一个「碰巧两者都一样」的环境会让它绿而什么都没证明。

    用 ``tmp_path`` 里现造的仓，不碰真实 records：这里要证明的是**读取器读的是哪个
    对象**，而那件事与六条来源的内容无关。
    """
    repo = tmp_path / "records"
    target = repo / "information" / "x.jsonl"
    target.parent.mkdir(parents=True)
    committed = b'{"a": 1}\n'
    target.write_bytes(committed)

    def git(*args: str) -> bytes:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=120, check=True
        )
        return done.stdout

    git("init", "-q")
    identity = ("-c", "user.email=t@example.invalid", "-c", "user.name=T", "-c", "commit.gpgsign=false")
    git(*identity, "add", "information/x.jsonl")
    git(*identity, "commit", "-q", "-m", "snapshot")
    commit = git("rev-parse", "HEAD").decode().strip()
    declared = git("rev-parse", f"{commit}:information/x.jsonl").decode().strip()

    # 别人未提交的 WIP —— 本仓规范说这棵树可以一直长这样（不得 reset / clean）。
    target.write_bytes(b'{"a": 2}\n')

    assert _commit_exists(repo, commit) is True
    assert _blob_at_commit(repo, commit, "information/x.jsonl") == declared
    assert _blob_bytes(repo, declared) == committed, (
        "读的是工作树 —— 别人的未提交改动会让上游对照红在一件与快照无关的事上"
    )
    assert _blob_bytes(repo, declared) != target.read_bytes()


def test_the_reader_reads_the_declared_commit_not_whatever_head_is(tmp_path: Path) -> None:
    """上游对照读的是 manifest 说的那个 commit，**不是 HEAD**。

    本机的 records 恰好停在声明的 commit 上，于是「读 ``<commit>``」与「读 HEAD」
    在这里给出同一个答案 —— 一个把入参写成 ``HEAD`` 的实现**今天无法被分辨**。
    分辨它的办法是造出两者不同的那一刻，而不是等它真的发生：上游一旦往前进一格，
    读 HEAD 的实现会报「blob 已变」，而事实是「我们还没跟上去」，处置完全不同。

    顺带把反方向也钉住：commit 在前、路径在后 —— 所以「这个 commit 上有没有这个
    路径」问的不是「现在有没有这个文件」。
    """
    repo = tmp_path / "records"
    path = repo / "information" / "x.jsonl"
    path.parent.mkdir(parents=True)

    def git(*args: str) -> bytes:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=120, check=True
        )
        return done.stdout

    def commit(message: str, content: bytes) -> str:
        path.write_bytes(content)
        git("add", "information/x.jsonl")
        git("-c", "user.email=t@example.invalid", "-c", "user.name=T",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        return git("rev-parse", "HEAD").decode().strip()

    git("init", "-q")
    snapshot = commit("snapshot", b'{"a": 1}\n')
    blob_at_snapshot = _blob_at_commit(repo, snapshot, "information/x.jsonl")
    head = commit("later", b'{"a": 2}\n')

    assert head != snapshot, "两次提交必须是两个 revision，否则这条判据什么都没证明"
    assert _blob_at_commit(repo, snapshot, "information/x.jsonl") == blob_at_snapshot
    assert _blob_at_commit(repo, head, "information/x.jsonl") != blob_at_snapshot
    assert _blob_bytes(repo, blob_at_snapshot) == b'{"a": 1}\n', (
        "按声明的 commit 读到了后来的那一版 —— manifest 的 provenance 会被读成「已过期」"
    )
    assert _blob_at_commit(repo, snapshot, "information/gone.jsonl") is None


def test_the_entry_pin_overrides_the_manifests_top_level_commit(tmp_path: Path) -> None:
    """条目自己的 ``source_repository_commit`` 压过顶层那个 —— 且**必须**由它压过。

    本机的 records 恰好是「顶层那条 pin 对所有条目都还成立」（后来的条目都追加在
    同一条上游之后），于是「逐条目解析」与「循环外解析一次」在今天给出同一个答案 ——
    一个只解析顶层、然后循环复用的实现**无法被分辨**。分辨它要造出两者不同的那一刻：
    两个 commit、两条路径上的 blob 不同，一条条目 pin 在旧的那个上、另一条 pin 在
    新的那个上。

    分辨力在最后一段断言上：按条目 pin 读到的必须是**它自己那一版**。一个复用顶层的
    实现会在第二条上读到 commit1 的字节 —— 若第二条的 ``upstream_blob_sha`` 恰好写的
    也是旧值，它会**静默放过**一条其实指不到自己那一版的条目；若写的是新值，它会报
    「blob 已变」，而按 §8 去追加一条新条目是**错的处置**（真实处置是补上那条 pin）。
    """
    repo = tmp_path / "records"
    path = repo / "information" / "x.jsonl"
    path.parent.mkdir(parents=True)

    def git(*args: str) -> bytes:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=120, check=True
        )
        return done.stdout

    def commit(message: str, content: bytes) -> str:
        path.write_bytes(content)
        git("add", "information/x.jsonl")
        git("-c", "user.email=t@example.invalid", "-c", "user.name=T",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        return git("rev-parse", "HEAD").decode().strip()

    git("init", "-q")
    first = commit("first", b'{"a": 1}\n')
    second = commit("second", b'{"a": 2}\n')
    blob_first = _blob_at_commit(repo, first, "information/x.jsonl")
    blob_second = _blob_at_commit(repo, second, "information/x.jsonl")

    assert second != first, "两次提交必须是两个 revision，否则这条判据什么都没证明"
    assert blob_first != blob_second, (
        "两个 commit 上这个路径的 blob 必须不同 —— 否则「读的是哪一版」不可分辨"
    )

    manifest = {"source_repository_commit": first}
    fallback = {"id": "第一条快照"}
    pinned = {"id": "后追加的条目", "source_repository_commit": second}

    assert _upstream_commit(repo, manifest, fallback) == first, "没有 pin 时回退到顶层"
    assert _upstream_commit(repo, manifest, pinned) == second, (
        "条目自己的 pin 没有被采用 —— 逐条目解析是假的，报文会把「pin 没写」说成「blob 已变」"
    )
    assert _blob_at_commit(repo, _upstream_commit(repo, manifest, pinned), "information/x.jsonl") == (
        blob_second
    ), "按条目 pin 读到的不是它自己那一版的字节"


def test_the_probes_answer_no_when_the_thing_is_not_there(tmp_path: Path) -> None:
    """两个探针在「没有」的时候说「没有」，不抛也不猜。

    它们各自守着夹具的一个分支：``_commit_exists`` 判「声明的 commit 不在这个检出里」
    （FAIL 而不是 skip），``_is_git_checkout`` 判「解开的目录副本不是检出」。两个分支
    在本机**都走不到**（records 是检出、且在正确的 commit 上），所以只能这样造出来测。

    写成 ``is False`` 而不是 ``not ...``：后半个写法对一个**返回 None** 的实现是绿的，
    而那个实现会让上面两条分支同时失效。
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _is_git_checkout(repo) is False, "一个普通目录不是 git 检出"
    assert _commit_exists(repo, "0" * 40) is False

    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, timeout=120)
    assert _is_git_checkout(repo) is True
    assert _commit_exists(repo, "0" * 40) is False, "刚 init 的空仓里没有这个 commit"
    assert _blob_at_commit(repo, "0" * 40, "information/x.jsonl") is None
