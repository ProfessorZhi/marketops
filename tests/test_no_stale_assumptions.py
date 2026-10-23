"""过时的部署假设、别的机器上的路径、以及「必须旁边摆着另一个仓库才能跑」。

这三件事看起来是三类问题，其实是同一件：**仓库里写着一条关于环境的断言，
而那条断言已经不是当前的事实**。它们的共同症状是不会有任何东西报错 ——
一条写死的旧路径和一条写死的当前路径长得一模一样。

因此这里的判据都不是「这个字符串好不好看」，而是「它是否让下一个人
把某个环境的特性当成仓库的特性」。

关于 ``sources/`` 的例外：那是**逐字节冻结的原始材料**，里面的路径属于被引用的
证据（SOP 正文、聊天原文），不是本仓库的假设。改写证据正是它存在要防止的事，
所以扫描必须跳过它 —— 并且这件事本身被断言，见 ``test_sources_is_excluded_and_declared``。

关于本文件自身：它**没有**豁免。写检测规则不等于可以使用被检测的形态 ——
下面所有的模式都是用形状描述的，不含任何一条真实路径。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from marketops.config import REPO_ROOT

#: 已经过时的部署假设。它们**曾经是事实**（当时的开发机就是那样），
#: 收敛之后它们只是陈旧 —— 而陈旧的部署说明比没有说明更坏：
#: 它会被照着做。
STALE_DEPLOYMENT_ASSUMPTIONS: tuple[tuple[str, str], ...] = (
    # lookbehind 是必需的：没有它，``https://`` 会被当成盘符后面跟一个斜杠。
    # 这条与 ``tests/test_portability.py`` 的 ``windows-drive-letter`` 是同一条规则 ——
    # 那边管代码，这边管文字（README、docs/、.gitignore）。
    (r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", "盘符绝对路径 —— 开发机时代的路径形态"),
    (r"marketops-demo", "早期 demo 目录名，已不再是本项目的部署形态"),
    (r"(?i)windows[\s-]only", "「只能在 Windows 上跑」的假设"),
    (r"(?i)linux (?:server )?(?:is )?unavailable", "「没有可用的 Linux 服务器」的假设"),
    (r"(?i)linux port(?:ing)? (?:todo|pending|later)", "「Linux 移植待办」的假设"),
)

#: 别的节点上的根目录。部署形态由部署配置给出，不写死任何一台机器的路径。
#:
#: 模式用形状写（``home`` 或 ``opt`` 下面的一级目录名），因此本文件里
#: 不含任何一条**真实**路径 —— 一条检测规则自己带着一个真路径，
#: 正是它要防的那种事。
FOREIGN_MACHINE_ROOTS: tuple[tuple[str, str], ...] = (
    (r"/(?:home|opt)/(?:upstream|applications)\b", "另一台节点的 HOME / 数据根"),
)

#: 已经不存在的标识符。文档提到一个函数名时，读者会去 grep 它 ——
#: 而 grep 不到和「这个函数在别处」在阅读体验上是一样的：都是找不到。
#: 这条规则管的是**名字已经消失**这一类，比路径更容易悄悄留下来：
#: 路径会在搬家时被发现，函数名只会在有人真的去查的时候才发现。
REMOVED_IDENTIFIERS: tuple[tuple[str, str], ...] = (
    (r"\bgrade_contact\b", "已不存在的函数名（定级现在分成 grade_route / grade_account）"),
)

#: 全部规则。分三组只是为了让人能读懂「为什么这条是过时的」，
#: 求值时它们是一件事：这行字还在描述一个已经不成立的世界。
_ALL_RULES: tuple[tuple[str, str], ...] = (
    *STALE_DEPLOYMENT_ASSUMPTIONS,
    *FOREIGN_MACHINE_ROOTS,
    *REMOVED_IDENTIFIERS,
)

#: 扫描范围：被维护的文本文件。**二进制与冻结材料不在其中。**
SCANNED_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".jsonl", ".txt", ".cfg", ".ini", ".example"}
)
SCANNED_BARE_NAMES: frozenset[str] = frozenset({".env.example", ".gitattributes", ".gitignore"})

#: 不扫的目录。
SKIPPED_DIRECTORIES: frozenset[str] = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "sources", "artifacts"}
)

#: 冻结快照。**必须与 SKIPPED_DIRECTORIES 一致**，由下面的测试断言。
FROZEN_EVIDENCE_ROOT = "sources"

#: 规则载体：这些文件**必须**写下它要禁止的形状，否则规则无从写起。
#: 它们整份豁免。
#:
#: 这个豁免是有代价的，而且代价要说清楚：它是**按文件**豁免的，不是按行 ——
#: 因此哪天有人在这两个文件里写进一条真的过时路径，扫描不会响。
#: 之所以接受：这两个文件里出现这些字样本来就是常态（描述、样本、注释），
#: 逐行豁免的判据会复杂到没人愿意维护，而一个没人愿意维护的判据
#: 最后总是被整个删掉。取而代之的是下面两条约束 —— 豁免必须**列出来**、
#: 必须**仍然需要**（见 ``test_rule_carriers_still_need_their_exemption``）。
#: 与 ``tests/test_portability.py`` 的 ``SELF_EXEMPT`` 是同一个机制。
RULE_CARRIER_FILES: frozenset[str] = frozenset(
    {
        "tests/test_no_stale_assumptions.py",
        "tests/test_portability.py",
        # 插件树整棵并入后带进来的两个载体：一个是配置文档里的 Windows 示例路径，
        # 一个是 win32 分支的路径断言本身。它们必须写下盘符路径 —— 否则「Windows
        # 上配置放在哪」既没法举例也没法测，删掉它等于删掉这项支持。
        "plugins/research-tools/marketops-research-assistant/references/mcp.windows.example.json",
        "plugins/research-tools/marketops-research-assistant/tests/test_install_config.py",
        # Native Windows containment/case-folding regression requires real drive paths.
        "plugins/research-tools/marketops-research-assistant/tests/test_install_windows_config_contract.py",
    }
)

#: 载体数量上限。拓宽约束必须是有意识的决定，不是顺手加一行。
MAX_RULE_CARRIER_FILES = 5  # 2 个本仓库规则 + 3 个插件树的 Windows 载体


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
            continue
        if path.relative_to(REPO_ROOT).as_posix() in RULE_CARRIER_FILES:
            continue
        if path.name in SCANNED_BARE_NAMES or path.suffix in SCANNED_SUFFIXES:
            files.append(path)
    return files


def _scan_file(path: Path) -> list[tuple[str, int, str, str]]:
    """返回 (相对路径, 行号, 说明, 该行)。"""
    relative = path.relative_to(REPO_ROOT).as_posix()
    findings: list[tuple[str, int, str, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, description in _ALL_RULES:
            if re.search(pattern, line):
                findings.append((relative, lineno, description, line.strip()))
    return findings


# --- 扫描必须先证明自己真的在扫 ---------------------------------------------


def test_the_scan_reaches_the_files_it_claims_to() -> None:
    """防止扫描静默失效 —— 范围写坏时，它会让下面所有断言变成空话。"""
    files = _iter_scanned_files()
    assert len(files) >= 30, f"只扫到 {len(files)} 个文件，扫描范围可能已被改坏"

    names = {path.name for path in files}
    assert {"README.md", "pyproject.toml", "config.py", "grading.yaml"} <= names


def test_the_patterns_are_not_empty() -> None:
    assert STALE_DEPLOYMENT_ASSUMPTIONS, "没有任何过时假设规则 —— 那这条约束等于不存在"
    assert FOREIGN_MACHINE_ROOTS
    assert REMOVED_IDENTIFIERS
    for pattern, description in _ALL_RULES:
        assert description.strip(), f"规则 {pattern!r} 没有说明它为什么是过时的"


def test_sources_is_excluded_and_declared() -> None:
    """``sources/`` 不参与扫描，而且这件事被显式断言。

    反方向也守：如果哪天 ``sources/`` 没了，这条会先响 —— 否则这条边界
    就成了对空集成立的空话。
    """
    assert (REPO_ROOT / FROZEN_EVIDENCE_ROOT).is_dir(), (
        "sources/ 不存在 —— 这条边界断言会假通过"
    )
    assert FROZEN_EVIDENCE_ROOT in SKIPPED_DIRECTORIES, (
        "sources/ 被纳入扫描。那是逐字节冻结的原始材料，里面的路径是被引用的证据，"
        "不是本仓库的假设 —— 见 sources/README.md 与本文件模块文档。"
    )


def test_rule_carriers_still_need_their_exemption() -> None:
    """豁免名单必须**仍然需要** —— 每一条都要真的会命中。

    这是 ``tests/test_portability.py`` 的 ``test_allowlist_matches_reality``
    的同一条道理：一条不再需要的豁免不是一个无害的遗留，它是一块
    未来违规可以躲进去的地方，而且是一块已经没人再看的地方。

    所以判据不是「这个文件在名单上」，而是「这个文件去掉豁免后确实会产生发现」。
    """
    assert len(RULE_CARRIER_FILES) <= MAX_RULE_CARRIER_FILES, (
        f"规则载体已增至 {len(RULE_CARRIER_FILES)} 个 —— 豁免面被拓宽了，"
        "拓宽必须是有意识的决定。"
    )

    unused: list[str] = []
    for relative in sorted(RULE_CARRIER_FILES):
        path = REPO_ROOT / relative
        assert path.is_file(), f"豁免名单里的 {relative} 不存在 —— 这条豁免已失去对象"
        if not _scan_file(path):
            unused.append(relative)

    assert not unused, (
        "这些文件被豁免却没有命中任何规则 —— 豁免已不再需要，请把它们从 "
        f"RULE_CARRIER_FILES 里删掉：{unused}"
    )


# --- 主断言 -----------------------------------------------------------------


def test_no_stale_deployment_assumptions() -> None:
    """仓库里不得留下过时的部署假设、别的机器的路径、或已消失的名字。

    代码里的路径类字符串由 ``tests/test_portability.py`` 用更细的规则管；
    这里管的是**文字**：README、docs/、deploy/、.env.example、.gitignore。
    三种规则都要管，因为它们会导致同一个动作：「照着一份不再成立的说明去做」。
    """
    findings = [item for path in _iter_scanned_files() for item in _scan_file(path)]

    assert not findings, (
        "发现过时的部署假设或别的机器上的路径：\n  "
        + "\n  ".join(
            f"{relative}:{lineno}  [{description}]  {line}"
            for relative, lineno, description, line in findings
        )
        + "\n\n这不是文字问题：照着它做的人会把某个环境的特性当成仓库的特性。"
    )


# --- 「必须旁边摆着另一个仓库才能跑」 ---------------------------------------


def _test_functions(path: Path) -> list[ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _argument_names(node: ast.FunctionDef) -> set[str]:
    args = node.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    return names - {"self", "cls"}


#: 需要**上游 checkout**才能给出结论的 fixture 名。它们出现的测试必须自带
#: skip 路径 —— 否则一个普通 CI 会因为旁边没有另一个仓库而变红。
UPSTREAM_FIXTURES: frozenset[str] = frozenset({"upstream_root", "records_root", "cards"})


def test_every_test_needing_the_upstream_checkout_can_skip() -> None:
    """用了上游 fixture 的测试，函数体里必须有一个 ``pytest.skip``。

    这条守的是 §18 的验收标准：本仓库的 CI 必须能独立跑完，
    **不允许**出现「因为没有隔壁 upstream-records 检出 → 默认测试失败」。
    这类失败模式最坏的地方不是它红，而是它把真实的失败埋在一堆本来就红的用例里。

    用 AST 而不是正则：函数参数表是结构，「这个测试要不要外部检出」这件事
    可以由参数名回答；正则只能回答「文件里有没有出现过 pytest.skip」，
    那样一个模块里只要有一处 skip，整份文件就都算合格。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        for node in _test_functions(path):
            needed = _argument_names(node) & UPSTREAM_FIXTURES
            if not needed:
                continue
            body = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            if "pytest.skip" not in body and "skipif" not in body:
                offenders.append(f"{relative}::{node.name} 依赖 {sorted(needed)} 却没有 skip")

    assert not offenders, (
        "这些测试需要上游检出，却没有跳过路径 —— 它们会在独立 CI 上默认失败：\n  "
        + "\n  ".join(offenders)
    )


def test_upstream_fixtures_do_resolve_to_none_without_the_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    """把「上游不在」这件事真的做一遍，而不是只读代码。

    两个 fixture 的失败语义刻意相反（一个 skip、一个 fail 在「上游在但空」上），
    但**都要**在检出不存在时返回 None。只断言其中一个，会让另一个的
    「找不到就炸」在很久以后才被发现。

    **上游那半的「不存在」要用它自己的变量造。** 它此前用
    ``MARKETOPS_RECORDS_ROOT`` 造 —— 那个变量属于**自足不变量**那半（见
    ``tests/test_business_sources.py``），而让它同时决定上游对照的有无，正好是那
    三条检查从来没跑过、并且会在别人未提交的 WIP 上转红的原因（§2.19）。
    所以这里两个变量都设，且**同一条断言现在顺带钉住另一个决定**：点名的路径不是
    目录时返回 ``None`` 而**不**退化成上溯找到的那个检出 —— 退化了的话，这一条
    在本机恒绿（旁边就有 records），而人给的那个值从此推不出跑的是哪个检出。
    """
    monkeypatch.setenv("MARKETOPS_RECORDS_ROOT", str(tmp_path / "not-a-checkout"))
    monkeypatch.setenv("MARKETOPS_UPSTREAM_RECORDS_ROOT", str(tmp_path / "not-a-checkout"))

    from tests.test_business_sources import records_root
    from tests.test_sources_snapshot import upstream_root

    assert records_root.__wrapped__() is None
    assert upstream_root.__wrapped__() is None


def test_ci_workflow_runs_the_suite_without_another_repository() -> None:
    """CI 必须存在，且不得检出第二个仓库。

    「本仓库能独立跑」如果只写在文档里，它会在某次「顺手加个 checkout 拿点数据」
    的改动之后变成假话 —— 而且那次改动看起来完全合理。
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file(), "没有 CI workflow —— 独立可跑这件事没有被自动化验证"

    text = workflow.read_text(encoding="utf-8")
    assert "pytest" in text, "CI 没有跑测试套件"

    offenders = [line.strip() for line in text.splitlines() if re.match(r"\s*repository\s*:", line)]
    assert not offenders, (
        "CI 里 checkout 了另一个仓库 —— 本套件必须能在只有本仓库时跑完：\n  "
        + "\n  ".join(offenders)
    )


def test_ci_needs_no_credentials() -> None:
    """测试套件不需要任何 secret —— CI 里因此不得出现 secrets。

    这条与「缺凭据时不阻塞」是一对：那一半由代码保证（适配层返回
    ``feishu_not_configured``），这一半由 CI 定义保证。CI 里一旦出现
    ``secrets.*``，就会有人把「跑测试」和「配好凭据」绑在一起，
    而那正是本仓库要避免的耦合。
    """
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "secrets." not in text, "CI 引用了 secrets —— 测试套件应当是零凭据的"


def test_no_workflow_sends_anything() -> None:
    """本仓库是 research-only 且 ``send_authorized=false``：CI 里不得有发送步骤。

    模型层已经让 ``send_authorized=True`` 构造不出来（见 test_models_invariants），
    这里补上另一半：**没有任何一条流水线打算发送**。
    """
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "没有 workflow 文件可查 —— 这条断言会假通过"

    # 用词边界而不是子串：``send_authorized`` 里含 ``send``，而它恰恰是**安全开关**。
    # 一条会把安全开关报成违规的规则，会被下一个人直接删掉 —— 那时它连
    # 真正的发送步骤也拦不住了。
    forbidden = (
        (r"\bsend(?:mail|er)?\b", "发送动作"),
        (r"\bsmtp\b", "SMTP"),
        (r"mailto:", "mailto 链接"),
        (r"\bgraph\.microsoft\b", "Microsoft Graph"),
        (r"\b(?:sendgrid|mailgun|postmark|ses)\b", "邮件服务商"),
    )
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        hits = sorted(
            f"{description}（{pattern}）"
            for pattern, description in forbidden
            if re.search(pattern, text, flags=re.IGNORECASE)
        )
        assert not hits, f"{path.name} 里出现了发送相关的字样：{hits}"


@pytest.mark.parametrize("pattern", [p for p, _ in STALE_DEPLOYMENT_ASSUMPTIONS])
def test_the_stale_patterns_actually_match_something(pattern: str) -> None:
    """阳性对照 —— 每条规则都要能命中它声称能命中的形态。

    没有这一条，一条写错的正则会让主断言恒过，而恒过的断言与没有断言
    在输出上完全一样。
    """
    samples = {
        r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]": [
            "开在 " + "C" + ":" + "\\" + "projects 下面",
            "工作树在 " + "D" + ":" + "/" + "work 下面",
        ],
        r"marketops-demo": ["旧路径是 marketops-demo 那份"],
        r"(?i)windows[\s-]only": ["This tool is Windows-only."],
        r"(?i)linux (?:server )?(?:is )?unavailable": ["Linux server unavailable at the time."],
        r"(?i)linux port(?:ing)? (?:todo|pending|later)": ["Linux porting pending"],
    }
    for sample in samples[pattern]:
        assert re.search(pattern, sample), f"规则 {pattern!r} 认不出它自己的样本 {sample!r}"
