"""业务来源治理的不变量 —— ``docs/SOURCES.md`` §7 的可执行版本。

这个仓库已经在用测试守两条同类规矩（``test_no_hardcoded_market_knowledge.py`` /
``test_evidence_literal_only.py``）。本文件守第三条：**业务需求只能从 approved 通道推导**。

它防的不是笔误，是漂移 —— 一条需求在半年后被「顺手」挂到一个 GitHub issue 或一篇论文上，
当时没人觉得有问题，事后没人分得清那条需求到底是谁要的。

判据是精确的：

- ``source_refs`` 必须落在 approved 通道内，或使用能承载它们的 typed locator scheme
- 技术依赖名不得出现在 ``source_refs``
- ``source_refs`` 不得指向本仓库（一份投影不能是自己的来源）
- fragment 必须真实存在（坏锚点会失败，不会静默通过）
- ``docs/`` 里出现的 ``MKT-xxxx`` 必须在来源索引里被声明

## 默认与可选：一条判据，两个见证

**默认（不需要任何外部 checkout）** 用本仓库的冻结快照作见证：
通道集合来自 ``sources/manifest.yaml``，而每一条通道都有一份冻结材料
（``tests/test_sources_snapshot.py`` 保证那是对的）。这让 canonical repo 的普通 CI
能独立跑完这套治理 —— 一个必须旁边摆着另一个仓库才能验证自己的仓库，不算独立可理解。

**可选（需要 ``upstream-records`` checkout）** 再拿上游权威对一次：Item 卡片存在吗、
``source_refs`` 真指到上游文件吗、``MKT-xxxx`` 真立项了吗。
上游不在时这些 **skip 并写明原因** —— 但这不是「查不了所以放过」：

- 默认那半已经把**本仓库内部的声明一致性**查完了，不是空跑；
- skip 会带着理由出现在报告里，不会被读成 passed。

原来这里写的是「定位不到就 fail，不 skip」。那条原则没有作废 —— 它现在的适用对象是
**默认**那半：它不会因为环境缺什么而静默变成通过。对上游交叉核对这半，让一个
普通 CI 因为旁边没有另一个仓库而变红，只会训练人忽略红色。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
SOURCES_DOC = DOCS_DIR / "SOURCES.md"
MANIFEST_PATH = REPO_ROOT / "sources" / "manifest.yaml"

#: 上游核对跑不了时的 skip 理由。写了怎么把它打开，而不只是「跳过了」。
_NO_UPSTREAM = (
    "没有找到含 projects/marketops/items/ 的 records 检出，上游交叉核对跳过（这是可选检查）。"
    "探测顺序：MARKETOPS_RECORDS_ROOT → workspace 下 records/ → worktrees/records/*/。"
    "dossier 可能还在 records 的未合并分支上（本机是 marketops/intake），"
    "那种情况下把 MARKETOPS_RECORDS_ROOT 指向那个分支的检出面。"
    "本仓库内部的声明一致性由默认那半覆盖，不是空跑（docs/SOURCES.md §7）。"
)


def _load_approved_channels() -> list[dict]:
    """从 ``sources/manifest.yaml`` 读 approved 通道。

    **通道集合的定义点在那份 manifest 里，不在这里。** 加一条通道 = 改 manifest
    并给它一份快照；本文件与 ``docs/SOURCES.md`` 都从它读。两份定义会漂移，
    而漂移的那份是没人看的那份。

    这里刻意直接 `assert` 而不是 `pytest.fail`：读不出通道集合意味着**整套治理失去
    判据**，那时任何一条断言都没有意义，与其逐个报错不如在收集阶段就断掉。
    """
    assert MANIFEST_PATH.is_file(), (
        f"{MANIFEST_PATH} 不存在。approved 通道集合定义在 sources/manifest.yaml —— "
        "它没了，业务来源治理就没了判据（见 sources/README.md §2）。"
    )
    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    channels = (document or {}).get("approved_channels")
    assert isinstance(channels, list) and channels, "sources/manifest.yaml 没有 approved_channels"
    for channel in channels:
        for field in ("id", "label", "chat_id", "channel_root"):
            assert channel.get(field), f"approved 通道 {channel.get('id')} 缺 {field}"
    return channels


_APPROVED_CHANNELS = _load_approved_channels()

#: approved 业务来源通道的 records 相对目录。**条数不写在这里** —— 它是
#: ``approved_channels`` 的派生量，写死一个数字就要在每次加通道时同步改一遍，
#: 而漏改的那一次不会红（它只是一句注释）。都是飞书镜像归档。
APPROVED_SOURCE_ROOTS = tuple(str(channel["channel_root"]) for channel in _APPROVED_CHANNELS)

#: typed locator 的 scheme 全表，抄自 ``src/serveros/context/resolver.py::_TYPED_SCHEMES``。
#: 刻意复制而不是 import：本仓库不得依赖 serveros。两边不一致时，
#: ``_resolve_source_ref`` 才是权威 —— 这段只是把它的词汇表搬过来判合法性。
ALL_LOCATOR_SCHEMES = ("conversation", "feishu", "github", "http", "https")

#: 上表里哪些能承载**业务**来源。全部通道都在飞书里，所以只有这两个。
#: ``github`` / ``http`` / ``https`` 在 resolver 里合法，在本项目里不是业务来源。
APPROVED_LOCATOR_SCHEMES = ("feishu", "conversation")

#: 技术来源的名字。它们可以出现在 docs/ARCHITECTURE.md 与 ADR，不得出现在 source_refs。
TECHNICAL_SOURCE_MARKERS = (
    "crawl4ai",
    "mineru",
    "playwright",
    "serper",
    "scispace",
    "arxiv",
    "github.com",
    "stackoverflow",
    "huggingface",
    "langgraph",
    "pypdf",
    "beautifulsoup",
    "bs4",
    "lxml",
)

#: records 相对前缀，抄自 ``resolver._RECORDS_RELATIVE_PREFIXES``。
#: 其它相对路径按 resolver 的规则锚到 Item 自身目录 —— 在这里等于指向 records 内部，
#: 所以本项目一律只用这两个前缀。
RECORDS_RELATIVE_PREFIXES = ("projects/", "information/")

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):")
_MKT_ID_RE = re.compile(r"\bMKT-[0-9]{4,}\b")


# --- 上游 records 定位（只被可选那半用到）---------------------------------


def _records_candidates() -> list[Path]:
    """候选 records 检出，按可信度降序。

    1. ``MARKETOPS_RECORDS_ROOT`` —— 显式指定永远最优先；
    2. 从本仓库根上溯，workspace root 下的 ``records/``；
    3. 同一层下 ``worktrees/records/*/``。

    为什么要退到 worktree：dossier 是交付物，它先落在 records 的一个**分支**上
    （本机是 ``marketops/intake``），只有那个分支的检出面才有 ``projects/marketops/``；
    主 ``records/`` 检出在合并前看不到它。只看主检出会落到「records 在、dossier 不在」
    这个中间态，而它既不是「上游没有」也不是「上游有问题」。

    探测是**只读**的：只 stat，不写、不动任何 worktree。
    """
    override = os.environ.get("MARKETOPS_RECORDS_ROOT")
    if override:
        return [Path(override).expanduser().resolve()]
    candidates: list[Path] = []
    for ancestor in REPO_ROOT.parents:
        primary = ancestor / "records"
        if not ((primary / "projects").is_dir() and (ancestor / "src").is_dir()):
            continue
        candidates.append(primary)
        candidates.extend(sorted((ancestor / "worktrees" / "records").glob("*/")))
        break
    return candidates


@pytest.fixture(scope="session")
def records_root() -> Path | None:
    """第一个**含 ``projects/marketops/items/``** 的候选检出，没有则 None。

    判据是 ``items/`` **目录存在**，不是「里面有卡片」—— 两个状态要分开：

    * ``items/`` 目录不存在 → 上游这个检出里就没有这份 dossier（可能在别的分支上）。
      返回 None，调用方 skip：这是上游的中间态，不是本仓库的失败。
    * ``items/`` 目录在、却一张 card.yaml 都没有 → **上游被人掏空了**。
      返回这个 root，``_require`` 让测试 FAILED。空跑通过比失败更坏。

    None **不是** failure：本文件的判据在默认那半已经由 ``sources/`` 冻结快照承担，
    上游在这里的角色是交叉核对。让一个普通 CI 因为旁边没有另一个仓库而变红，
    只会训练人忽略红色。
    """
    for candidate in _records_candidates():
        if (candidate / "projects" / "marketops" / "items").is_dir():
            return candidate
    return None


@pytest.fixture(scope="session")
def cards(records_root: Path | None) -> list[tuple[Path, dict]] | None:
    """返回 [(card.yaml 路径, 内容)]。

    ``None`` 的含义被收窄成**只有一种**：records 整个不可用（上游不在）。
    records 在、但 ``projects/marketops/items/`` 缺失或没有卡片时返回**空列表** ——
    两种情形的处置不同（一个要去找 checkout，一个要去看需求落没落），返回同一个
    ``None`` 会让下游拿不到区分它们的判据，然后报出一个与原因无关的错误。
    """
    if records_root is None:
        return None
    items_dir = records_root / "projects" / "marketops" / "items"
    if not items_dir.is_dir():
        return []
    found: list[tuple[Path, dict]] = []
    for path in sorted(items_dir.glob("*/card.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{path} 不是 mapping"
        found.append((path, document))
    return found


def _require(root: Path, cards_list: list | None = None) -> None:
    """上游在、但需求没落盘时的第一句。**FAILED，不 skip。**

    这与「上游不在」是两件事：上游不在是环境缺东西（skip 并写明怎么打开）；
    上游在、``projects/marketops/items/`` 却空着，是**上游本身缺了该有的东西**，
    接下来的断言会空跑通过 —— 空跑通过比失败更坏，所以这里失败。
    """
    if cards_list is not None and not cards_list:
        items_dir = root / "projects" / "marketops" / "items"
        pytest.fail(
            f"{items_dir} 下没有任何 card.yaml。\n"
            "  需求还没落到 records，或落到了另一个 checkout —— 两种情况都会让下面的\n"
            "  断言空跑通过，所以这里失败。",
            pytrace=False,
        )


# --- 判据：一条 source_ref 是否合法 ----------------------------------------


def _scheme(ref: str) -> str | None:
    match = _SCHEME_RE.match(ref)
    return match.group(1) if match else None


def _locator_path(roots_parent: Path, roots_label: str, ref: str, item_dir: Path) -> Path:
    """按 resolver 的规则把一条路径型 ref 折成绝对路径。"""
    path, _, _ = ref.partition("#")
    if path.startswith("/"):
        return Path(path)
    if path.startswith(RECORDS_RELATIVE_PREFIXES):
        return roots_parent / roots_label / path
    return item_dir / path


def _fragment_present(target: Path, fragment: str) -> bool:
    """fragment 必须是目标文件里一行**标题**的一部分。

    只认标题行，不认正文里偶然出现的同样几个字 —— 否则锚点会在标题被改名之后
    继续「通过」，因为这几个字还在段落里。
    """
    text = target.read_text(encoding="utf-8", errors="replace")
    return any(fragment in line for line in text.splitlines() if line.lstrip().startswith("#"))


def check_ref(ref: str, *, parent: Path, label: str, item_dir: Path) -> str | None:
    """检查一条 source_ref。合法返回 None，违规返回原因。"""
    if not isinstance(ref, str) or not ref.strip():
        return f"不是非空字符串: {ref!r}"

    lowered = ref.casefold()
    for marker in TECHNICAL_SOURCE_MARKERS:
        if marker in lowered:
            return (
                f"技术来源不得作为业务来源: {marker!r}。"
                "技术证据只进 docs/ARCHITECTURE.md 与 ADR（docs/SOURCES.md §5）"
            )

    scheme = _scheme(ref)
    if scheme is not None:
        if scheme not in ALL_LOCATOR_SCHEMES:
            return f"未知的 locator scheme: {scheme!r}"
        if scheme not in APPROVED_LOCATOR_SCHEMES:
            return (
                f"scheme {scheme!r} 不是 approved 业务来源通道 "
                f"（approved: {APPROVED_LOCATOR_SCHEMES}）"
            )
        return None

    target = _locator_path(parent, label, ref, item_dir)

    if REPO_ROOT == target or REPO_ROOT in target.parents:
        return f"指向本仓库，投影不能是自己的来源（NO_SELF_REFERENCE）: {target}"

    allowed = tuple(parent / label / root for root in APPROVED_SOURCE_ROOTS)
    if not any(target == base or base in target.parents for base in allowed):
        return (
            "不在 approved 通道内，或没有使用 records 相对前缀 "
            f"({RECORDS_RELATIVE_PREFIXES}): {ref!r} → {target}"
        )

    if not target.is_file():
        return f"目标不存在: {target}"

    _, separator, fragment = ref.partition("#")
    if separator and not _fragment_present(target, fragment):
        return f"fragment 不是目标文件里的标题: {fragment!r} in {target}"

    return None


# --- 对照：扫描器本身必须能抓到违规 ----------------------------------------


@pytest.fixture
def fake_records(tmp_path: Path) -> tuple[Path, str, Path]:
    """一个最小的假 records，用来给扫描器做阳性/阴性对照。"""
    parent, label = tmp_path, "records"
    approved = parent / label / APPROVED_SOURCE_ROOTS[2] / "oc_fake" / "links" / "sop"
    approved.mkdir(parents=True)
    (approved / "content.md").write_text("# 标题\n\n## 硬性边界\n\n正文。\n", encoding="utf-8")
    item_dir = tmp_path / "items" / "MKT-0001"
    item_dir.mkdir(parents=True)
    roots_parent = parent / label / "projects" / "marketops" / "items" / "MKT-0001"
    roots_parent.mkdir(parents=True)
    return parent, label, item_dir


_SOP_REF = APPROVED_SOURCE_ROOTS[2] + "oc_fake/links/sop/content.md#硬性边界"


def test_scanner_accepts_an_approved_ref(fake_records):
    parent, label, item_dir = fake_records
    assert check_ref(_SOP_REF, parent=parent, label=label, item_dir=item_dir) is None


def test_scanner_accepts_a_channel_typed_locator(fake_records):
    parent, label, item_dir = fake_records
    ref = "feishu://docx/TOKEN140674"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir) is None


def test_scanner_rejects_a_technical_source(fake_records):
    parent, label, item_dir = fake_records
    # 触发的是 ``github.com`` 这个通用技术来源标记，不是某个具体项目的名字。
    # 刻意**不**写成 ``https://github.com/unclecode/crawl4ai/...``：那样会连带命中
    # ``tests/test_portability.py`` 的 ``thirdparty-repo-abs-path`` 规则，而那条规则的
    # allowlist 有刻意的 3 条上限。要探针去撞一个无关的预算，是把两件事搅在一起 ——
    # crawl4ai 作为技术来源已在 ``TECHNICAL_SOURCE_MARKERS`` 里，不需要靠这里证明。
    ref = "https://github.com/example/readme/blob/main/README.md"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir), (
        "扫描器没有拦下技术来源，扫描结果不可信"
    )


def test_scanner_rejects_a_non_approved_channel(fake_records):
    parent, label, item_dir = fake_records
    ref = "information/raw/feishu/chats/groups/ai-support/oc_x/manifest.json"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir), (
        "扫描器放过了非 approved 的群归档"
    )


def test_scanner_rejects_a_self_reference(fake_records):
    parent, label, item_dir = fake_records
    ref = str(REPO_ROOT / "docs" / "PRODUCT_SPEC.md")
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir), (
        "扫描器放过了指向本仓库的自引用"
    )


def test_scanner_rejects_a_missing_target(fake_records):
    parent, label, item_dir = fake_records
    ref = APPROVED_SOURCE_ROOTS[2] + "oc_typo/links/sop/content.md"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir)


def test_scanner_rejects_a_broken_fragment(fake_records):
    """锚点指向一个不存在的标题 —— 这正是踩过的那类坏锚点。"""
    parent, label, item_dir = fake_records
    ref = APPROVED_SOURCE_ROOTS[2] + "oc_fake/links/sop/content.md#已被改名的标题"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir)


def test_scanner_ignores_the_fragment_text_in_body(fake_records):
    """标题被改名、同样的字留在正文里 —— 仍然算坏锚点。"""
    parent, label, item_dir = fake_records
    target = parent / label / APPROVED_SOURCE_ROOTS[2] / "oc_fake/links/sop/content.md"
    target.write_text(
        "# 标题\n\n正文里提到硬性边界这两个字，但它已经不是标题。\n", encoding="utf-8"
    )
    ref = APPROVED_SOURCE_ROOTS[2] + "oc_fake/links/sop/content.md#硬性边界"
    assert check_ref(ref, parent=parent, label=label, item_dir=item_dir)


# --- 不变量：默认（只需本仓库）--------------------------------------------


def _mkt_index_ids() -> set[str]:
    """``docs/SOURCES.md`` §6 需求索引里声明的 Item 编号。

    索引表是**本仓库内**对「有哪些需求」的声明。它是权威 records 的索引，
    不是权威本身 —— 但「本仓库只引用自己声明过的编号」这件事不需要上游就能查。
    """
    text = SOURCES_DOC.read_text(encoding="utf-8")
    return set(_MKT_ID_RE.findall(text))


def test_docs_only_cite_ids_declared_in_the_source_index():
    """文档里引用的需求编号必须在本仓库的索引里被声明过。

    这是 ``test_docs_only_cite_ids_that_exist_in_records`` 的**默认版**：上游不在时
    仍然能查出「文档凭空引了一个编号」—— 那种引用读起来像已确认的需求。

    能查出的是**一半**：编号在索引里、但上游其实没有这个 Item 的情形查不出。
    那一半需要上游，见下面那条可选测试。两半都写，是因为只留可选那半会让默认 CI 空跑。
    """
    declared = _mkt_index_ids()
    assert declared, (
        f"{SOURCES_DOC.relative_to(REPO_ROOT)} §6 里一个 MKT-xxxx 都没有 —— "
        "要么索引被删了，要么格式变了；两种情况都会让本测试假通过"
    )
    offenders: list[str] = []
    for path in sorted([*DOCS_DIR.glob("*.md"), *DOCS_DIR.glob("*/*.md"), *(REPO_ROOT.glob("*.md"))]):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for item_id in _MKT_ID_RE.findall(line):
                if item_id not in declared:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: 引用了索引里没有的 {item_id}"
                    )
    assert not offenders, (
        "\n  ".join(offenders)
        + "\n要么在 docs/SOURCES.md §6 里补上这条索引，要么把引用删掉 —— "
        "不要为了过测试往索引里塞一个还没立项的编号。"
    )


def test_sources_doc_declares_every_approved_channel():
    """声明与代码不能漂移：manifest 里加一条通道就必须改 docs/SOURCES.md。

    这条不依赖 records —— 它是本仓库内部的声明一致性。
    """
    text = SOURCES_DOC.read_text(encoding="utf-8")
    missing = [root for root in APPROVED_SOURCE_ROOTS if root not in text]
    assert not missing, "docs/SOURCES.md 没有声明这些 approved 通道：\n  " + "\n  ".join(missing)


def test_scan_scope_and_channel_set_agree():
    """``check_ref`` 的作用域必须就是 approved 通道的集合，不能宽也不能窄。

    作用域是从 ``APPROVED_SOURCE_ROOTS`` 现场算的（``check_ref`` 里那行 ``allowed``），
    所以这条测的是**它算对了**：每条通道都真的落进作用域，而作用域之外的目录不落进去。
    """
    assert len(APPROVED_SOURCE_ROOTS) == len(set(APPROVED_SOURCE_ROOTS)), "通道有重复"
    parent, label = Path("/nonexistent-record-root"), "records"
    allowed = tuple(parent / label / root for root in APPROVED_SOURCE_ROOTS)
    for root in APPROVED_SOURCE_ROOTS:
        probe = parent / label / root / "oc_probe" / "messages.jsonl"
        assert any(probe == base or base in probe.parents for base in allowed), (
            f"通道 {root} 没有被 check_ref 的作用域覆盖"
        )
    for outside in ("information/raw/feishu/chats/groups/ai-support/", "docs/"):
        probe = parent / label / outside / "x.md"
        assert not any(probe == base or base in probe.parents for base in allowed), (
            f"{outside} 被误判进了作用域"
        )


def test_approved_channels_are_disjoint():
    """通道之间不能互相包含 —— 包含关系会让「从哪条通道来」失去意义。"""
    for index, root in enumerate(APPROVED_SOURCE_ROOTS):
        for other in APPROVED_SOURCE_ROOTS[index + 1 :]:
            assert not root.startswith(other) and not other.startswith(root), (
                f"通道重叠: {root} / {other}"
            )


# --- 上游交叉核对（可选：需要 upstream-records）---------------------------------


def test_upstream_checks_are_the_optional_half():
    """把「哪些检查是可选」钉住。

    选项性是**显式**的：少掉一条上游核对应当在 diff 里显形，而不是让人以为
    治理只剩默认那半。默认那半的清单由本文件其余测试承担。
    """
    optional = {
        name
        for name, value in globals().items()
        if name.startswith("test_upstream_") and callable(value)
    }
    expected = {
        "test_upstream_checks_are_the_optional_half",
        "test_upstream_every_card_declares_a_source",
        "test_upstream_every_source_ref_is_an_approved_business_source",
        "test_upstream_docs_ids_exist_as_items",
    }
    assert optional == expected, (
        f"可选的上游核对集合变了：多出 {optional - expected}，少了 {expected - optional}。"
        "少一条就少一份对上游的验证；多一条要先说清它是不是可选的。"
    )


def test_upstream_every_card_declares_a_source(records_root, cards):
    """没有 source 的需求，就是「我们觉得应该这样」。"""
    if records_root is None:
        pytest.skip(_NO_UPSTREAM)
    _require(records_root, cards)
    offenders = [
        str(path.relative_to(path.parents[2]))
        for path, card in cards
        if not card.get("source_refs")
    ]
    assert not offenders, "这些 Item 的 source_refs 是空的：\n  " + "\n  ".join(offenders)


def test_upstream_every_source_ref_is_an_approved_business_source(records_root, cards):
    """``source_refs`` 必须真指到上游文件，且落在 approved 通道内。

    比默认那半强的地方：默认只查**本仓库的声明**是否自洽；这条把每条 ref
    折成上游绝对路径，于是「路径写错了」「文件其实不存在」这两类错误才会露出来。
    """
    if records_root is None:
        pytest.skip(_NO_UPSTREAM)
    _require(records_root, cards)
    offenders: list[str] = []
    for path, card in cards:
        item_dir = path.parent
        for ref in card.get("source_refs") or []:
            reason = check_ref(
                ref,
                parent=records_root.parent,
                label=records_root.name,
                item_dir=item_dir,
            )
            if reason:
                offenders.append(f"{card.get('id')}: {reason}")
    assert not offenders, (
        "有 source_refs 落在了 approved 通道之外（docs/SOURCES.md §3）：\n  "
        + "\n  ".join(offenders)
    )


def test_upstream_docs_ids_exist_as_items(records_root, cards):
    """文档里引用的需求编号必须在上游真的立项了。

    默认那半只能证明「编号在本仓库索引里」。这条补上另一半：
    索引本身是不是在描述真实存在的 Item。索引写了一个不存在的编号，
    默认那半看不出来 —— 这条能。
    """
    if records_root is None:
        pytest.skip(_NO_UPSTREAM)
    _require(records_root, cards)
    known = {card.get("id") for _, card in cards}
    offenders: list[str] = []
    for path in sorted([*DOCS_DIR.glob("*.md"), *DOCS_DIR.glob("*/*.md"), *(REPO_ROOT.glob("*.md"))]):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for item_id in _MKT_ID_RE.findall(line):
                if item_id not in known:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: 引用了不存在的 {item_id}"
                    )
    assert not offenders, "\n  ".join(offenders)
