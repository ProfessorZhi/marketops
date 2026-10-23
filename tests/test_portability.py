"""Windows → Linux 可迁移性的**硬约束**。

需求 §4 要求核心代码禁止写死盘符、反斜杠路径、用户名、本机 Python 位置、
第三方 repo 绝对路径。本文件把这条要求变成每次 ``pytest`` 都会执行的红线，
而不是一份需要人记得遵守的文档。

设计取舍：

* 扫描的是**源码文件**（``marketops/`` 下的 ``.py``）。
  ``docs/`` 与 ``README`` 刻意不扫 —— 它们描述的是**部署形态**
  （容器里挂哪个目录、env 怎么传），而不是代码假设；把这些文字也纳入扫描，
  只会逼人用同义词把同一件事再说一遍，而扫描器看到的是一份更含糊的文档。
  文字侧的约束另有一条更直接的检查，见 ``tests/test_no_stale_assumptions.py``。
* allowlist 必须**逐条带理由**，且有数量上限。放宽约束的代价必须可见。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from marketops.config import REPO_ROOT, Settings
from marketops.domain.util import slugify

#: 会被运行的代码。``services/`` 曾在这里，随后 B7a 搬走了它唯一有实现的包，
#: 本轮又删掉了剩下的两个占位目录 —— 于是范围里只剩一个成员。与
#: ``tests/test_declared_dependencies.py`` 的 ``SCANNED_ROOTS`` 是同一件事，
#: 两处一起改，否则「代码在哪」会有两个答案。
SOURCE_ROOTS: tuple[str, ...] = ("marketops",)

#: 本文件自身豁免 —— 它必须包含这些模式才能检测它们。
SELF_EXEMPT: frozenset[str] = frozenset({"tests/test_portability.py"})

#: 额外纳入扫描（排除 SELF_EXEMPT）的目录。
EXTRA_ROOTS: tuple[str, ...] = ("tests",)


class Rule:
    __slots__ = ("rule_id", "pattern", "description")

    def __init__(self, rule_id: str, pattern: str, description: str) -> None:
        self.rule_id = rule_id
        self.pattern = re.compile(pattern)
        self.description = description


RULES: tuple[Rule, ...] = (
    Rule(
        # 前面的 lookbehind 是必需的：没有它，``https://`` 里的 ``s:/``
        # 会被当成本地盘符 —— 这个假阳性会让规则的告警失去可信度。
        "windows-drive-letter",
        r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]",
        "Windows 盘符字面量（如 D:/  C:\\）",
    ),
    Rule(
        # 要求**两个**反斜杠，且被路径成分包住。
        #
        # 为什么是两个：源码里的 ``\\`` 才等于字符串里的一个真反斜杠；
        # 单个 ``\`` 后面跟字母一律是转义序列（``\n`` ``\t`` ``\d`` ``\.``），
        # 不是路径。早期版本单看 ``\\\\`` 会命中 ``'/\\:*?"<>|'`` 这类字符集，
        # 只看 ``\`` 又会命中满屏的 ``\n`` —— 两种情况都会让人不再相信这条告警。
        #
        # 已知局限：raw 字符串里的**单**反斜杠相对路径（``r"data\mirrors"``）
        # 检测不到 —— 它与 ``r"\d+"`` 这类正则字面量无法区分。
        # 绝对路径由 windows-drive-letter / windows-user-path 两条兜住。
        "backslash-path-literal",
        r"\w\\{2,}\w|[^A-Za-z0-9_]\\{2,}[A-Za-z]",
        "字符串里的反斜杠路径分隔符（应使用 pathlib）",
    ),
    Rule(
        "os-sep-hardcoded",
        r"\bos\.sep\b",
        "os.sep 硬编码分隔符（应使用 pathlib）",
    ),
    Rule(
        "windows-user-path",
        r"(?i)\busers[\\/][A-Za-z0-9._-]+",
        "硬编码 Windows 用户名路径",
    ),
    Rule(
        "posix-user-home-path",
        r"/(?:home|Users)/[A-Za-z0-9._-]+/",
        "硬编码用户 home 路径",
    ),
    Rule(
        "hardcoded-deploy-root",
        r"/(?:opt|srv|var|mnt)/[A-Za-z]",
        "硬编码 Linux 部署根路径",
    ),
    Rule(
        "thirdparty-repo-abs-path",
        r"[\"'][^\"'\n]*[\\/](?:crawl4ai|MinerU)[\\/][^\"'\n]*[\"']",
        "第三方仓库绝对路径（应通过包或配置引用）",
    ),
)

class Allowance:
    """一条豁免：理由 + 它在盘上**恰好**命中几处。

    ``hits`` 这个数字是这条豁免**有界**的原因。没有它，豁免的粒度是
    ``(文件, 规则)`` —— 一个豁免等于该文件里这条规则**永远**不再被检查，
    而未来的新违规正好躲进这个已经没人看的条目里。有了它，同一文件里多出
    一处同规则的命中就会让 ``test_allowlist_matches_reality`` 变红，必须有人
    去看一眼再决定是改写代码还是把数字加一。
    """

    __slots__ = ("reason", "hits")

    def __init__(self, reason: str, hits: int) -> None:
        self.reason = reason
        self.hits = hits


#: allowlist：(相对路径, rule_id) → Allowance。
#: 理由必须说明**为什么这里写死是安全的**，而不是「测试需要」。
#:
#: 收敛时删掉了一条：``marketops/config.py`` 的 ``hardcoded-deploy-root``。
#: 那条豁免是因为当时的 ConfigError 提示里举了一条 Linux 部署路径当例子 ——
#: 提示改成「该值应当来自部署配置」之后，豁免本身就失去了对象。
#: ``test_allowlist_matches_reality`` 正是为此存在的：留着一条不再需要的豁免，
#: 会让未来的新违规躲进一个已经没人看的条目里。
ALLOWLIST: dict[tuple[str, str], Allowance] = {
    (
        "tests/test_models_invariants.py",
        "backslash-path-literal",
    ): Allowance(
        reason=(
            "该测试断言含反斜杠的 account_id 会被拒绝。样本本身必须是真的反斜杠，"
            "无法改写成别的形式而不失去测试意义。"
        ),
        hits=1,
    ),
    (
        "tests/test_research_worker.py",
        "backslash-path-literal",
    ): Allowance(
        reason=(
            "路径成分校验的样本表：这两个参数化用例断言 ``a\\b`` 会被拒绝，"
            "样本本身必须是真的反斜杠，换成别的写法就不再验证那一条。"
        ),
        hits=3,
    ),
}

#: allowlist 条目数上限。拓宽约束必须是有意识的决定，不是顺手加一行。
MAX_ALLOWLIST_ENTRIES = 3

#: 每条理由的最短长度，避免写成 "test" / "todo" 这种占位。
MIN_REASON_LENGTH = 20


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root_name in (*SOURCE_ROOTS, *EXTRA_ROOTS):
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if path.relative_to(REPO_ROOT).as_posix() in SELF_EXEMPT:
                continue
            files.append(path)
    return files


def _scan(path: Path) -> list[tuple[str, int, str]]:
    """返回该文件的违规项：(rule_id, 行号, 该行内容)。"""
    violations: list[tuple[str, int, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line):
                violations.append((rule.rule_id, lineno, line.strip()))
    return violations


def test_source_files_are_discovered() -> None:
    """防止扫描静默失效 —— 如果路径改了导致扫不到文件，这条会先失败。"""
    files = _iter_source_files()
    assert len(files) >= 15, f"只发现 {len(files)} 个源文件，扫描范围可能已被改坏"
    names = {path.name for path in files}
    assert "config.py" in names
    assert "cli.py" in names


def test_evidence_tree_is_outside_the_scanned_roots() -> None:
    """``sources/`` 是**故意**不在扫描范围内的，而且这件事被断言。

    证据是逐字节原文。原文里出现另一台机器的路径（Member-A 讨论 Codex 安装位置时就会写
    ``C:\\...``）不是本仓库的路径假设，而是被引用的材料本身。把 ``sources/`` 纳入扫描，
    会逼着后来的人去改写证据 —— 而「不改写证据」正是那份快照存在的全部理由。

    所以这里守的是一条**边界**。谁要把 ``sources`` 加进 ``SOURCE_ROOTS`` / ``EXTRA_ROOTS``，
    必须先读到这段；反方向也守：如果哪天 ``sources/`` 没了，下面的断言会先响，
    否则这条边界就成了对空集成立的空话。
    """
    assert (REPO_ROOT / "sources").is_dir(), "sources/ 不存在 —— 这条边界断言会假通过"
    scoped = {*SOURCE_ROOTS, *EXTRA_ROOTS}
    assert "sources" not in scoped, (
        "sources/ 被纳入了 portability 扫描。证据是原文，可以被引用但不可以被改写 —— "
        "见 sources/README.md §3 与本测试的 docstring。"
    )


def test_no_hardcoded_platform_paths_in_source() -> None:
    """源码中不得出现任何平台特定路径字面量（allowlist 除外）。"""
    failures: list[str] = []

    for path in _iter_source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        for rule_id, lineno, line in _scan(path):
            if (relative, rule_id) in ALLOWLIST:
                continue
            rule = next(item for item in RULES if item.rule_id == rule_id)
            failures.append(f"{relative}:{lineno}  [{rule_id}] {rule.description}\n    {line}")

    assert not failures, (
        "发现平台特定路径字面量：\n"
        + "\n".join(failures)
        + "\n\n如果确实必要，加进 ALLOWLIST 并写明理由；不要为了过测试而放宽规则。"
    )


def test_allowlist_is_deliberate() -> None:
    """allowlist 必须小、必须带真实理由 —— 防止它被当成万能挡板。"""
    assert len(ALLOWLIST) <= MAX_ALLOWLIST_ENTRIES, (
        f"allowlist 已有 {len(ALLOWLIST)} 条（上限 {MAX_ALLOWLIST_ENTRIES}）。"
        "扩大 allowlist 意味着放宽可迁移性约束，应当先修代码。"
    )
    for key, allowance in ALLOWLIST.items():
        reason = allowance.reason.strip()
        assert len(reason) >= MIN_REASON_LENGTH, f"allowlist 条目 {key} 的理由过于简短"
        assert allowance.hits >= 1, f"allowlist 条目 {key} 的 hits 必须 >= 1"
    for path_str, rule_id in ALLOWLIST:
        assert (REPO_ROOT / path_str).exists(), f"allowlist 指向了不存在的文件：{path_str}"
        assert rule_id in {rule.rule_id for rule in RULES}, f"allowlist 引用了未知规则：{rule_id}"


def test_allowlist_matches_reality() -> None:
    """豁免必须**逐处对得上**：不多（不需要了）也不少（被新违规撑大了）。

    两个方向都要守，因为它们掩盖的是不同的东西：

    * 命中**少于** ``hits`` —— 这条豁免已经不需要了。留着它，未来的新违规会躲进
      一个没人再看第二眼的条目里。
    * 命中**多于** ``hits`` —— 有人往同一个文件里又塞了一处同规则的路径字面量，
      而豁免把它一起放过了。这正是「豁免的粒度是 (文件, 规则)」的代价，
      ``hits`` 就是给这个代价上的封顶。

    两个方向都不许自动修正：看见这条红的人要回答的是「这处路径该不该在源码里」，
    而那个问题只有人答得了。
    """
    drift: list[str] = []
    for (path_str, rule_id), allowance in ALLOWLIST.items():
        found = sum(1 for rule, _, _ in _scan(REPO_ROOT / path_str) if rule == rule_id)
        if found == allowance.hits:
            continue
        if found == 0:
            drift.append(f"{path_str} [{rule_id}] 已不再命中任何代码，应删除（hits={allowance.hits}）")
        else:
            drift.append(
                f"{path_str} [{rule_id}] 实命中 {found} 处，豁免写的是 {allowance.hits} 处"
            )
    assert not drift, (
        "allowlist 与盘上的实际情况对不上：\n"
        + "\n".join(drift)
        + "\n\n改代码，还是改数字，由看了那几行的人决定；不要为了让这条变绿而顺手加数字。"
    )


def test_settings_accepts_posix_data_root(tmp_path: Path) -> None:
    """Settings 必须能接受 POSIX 形态的运行目录 —— 它不该对盘符有任何假设。"""
    posix_like = tmp_path / "marketops-runtime"
    resolved = Settings(data_root=posix_like, profiles_dir=REPO_ROOT / "profiles", profile="x")

    assert resolved.state_dir == posix_like / "state"
    assert resolved.mirrors_dir == posix_like / "mirrors"
    assert resolved.evidence_dir == posix_like / "state" / "evidence"
    resolved.ensure_data_dirs()
    assert resolved.benchmarks_dir.is_dir()


def test_slugify_is_platform_independent() -> None:
    """同一输入在 Windows 与 Linux 必须得到同一文件名。"""
    assert slugify("AcmeCorp / Natural Grass Ltd.") == "acmecorp-natural-grass-ltd"
    assert "/" not in slugify("a/b")
    assert "\\" not in slugify("a\\b")
    assert ":" not in slugify("C:something")
    assert slugify("", fallback="x") == "x"


def test_contract_files_use_lf(repo_root: Path) -> None:
    """契约文件强制 LF —— 否则 Windows 生成、Linux 检出会显示为全文件改动。"""
    contracts = repo_root / "contracts"
    if not contracts.is_dir():
        pytest.skip("contracts 尚未生成")
    for path in contracts.glob("*.json"):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} 含 CRLF"


def test_gitattributes_pins_lf_for_source(repo_root: Path) -> None:
    """.gitattributes 必须强制 LF 入库，否则跨平台会产生整文件级别的假 diff。"""
    attributes = repo_root / ".gitattributes"
    assert attributes.exists()
    content = attributes.read_text(encoding="utf-8")
    assert "text=auto" in content
    assert "eol=lf" in content


def test_no_absolute_paths_in_env_example_keys(repo_root: Path) -> None:
    """.env.example 只放变量名 —— 值必须为空（这里顺带守住「样例文件里没有真 secret」）。"""
    lines = (repo_root / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = [
        line for line in lines if "=" in line and not line.lstrip().startswith("#")
    ]
    assert assignments, ".env.example 应当包含变量名"
    for line in assignments:
        key, _, value = line.partition("=")
        if key.strip() == "MARKETOPS_PROFILE":
            continue  # 默认 profile 名不是 secret，且写出来有用
        assert value.strip() == "", f".env.example 中 {key.strip()} 应当留空，实际有值"
