"""``pyproject.toml`` 的依赖表与代码真的对得上 —— 两个方向都由本测试执行。

## 这个洞的形状：一条只在**文档**里的 pin

``mcp`` SDK 用了整片服务面（``marketops/adapters/mcp_server/`` 的四个模块），
而它**不在** ``pyproject.toml`` 的任何依赖或 extra 里。版本上限 ``>=2.2,<3``
写在 ``docs/MVP_MIGRATION_PLAN.md`` §4 的 D1 里 —— 一条**没有执行器**的约定。

它的症状是具体的：一次干净安装之后 ``packages = ["marketops"]`` 会把
``marketops/adapters/mcp_server/`` 一起装出去，而那个包 import 不了。
**而本仓自己的测试环境里 mcp 是装着的**，所以整套测试全绿 —— 这正是这条判据
存在的理由：一个只在别人机器上现形的失败，等于没有失败。

## 反方向的第二个实例：一个声明了没人用的依赖

``beautifulsoup4`` 声明在 ``dependencies`` 里，而全仓没有一处 import 它 ——
正文抽取走的是 ``pypdf`` 与正则（``adapters/text_pdf.py`` 自己这么写着）。
它同时出现在 ``interfaces/cli.py`` 的能力探针里，于是一份**文档 + 一张探针表**
合起来让「HTML 是 BeautifulSoup 解析的」这句话看起来有依据。两个方向都要判，
因为这两种错各自的后果不同：少一个依赖会**当场坏**，多一个依赖会让下一个人
按它去理解系统，而后者不会坏，只会一直错。

## 为什么映射表是手写的

发行包名与顶层 import 名不是一回事（``PyYAML`` → ``yaml``、``typesafe-sdk`` →
``typesafe_sdk``）。这件事本来可以问 ``importlib.metadata.packages_distributions()``
—— 而它只认识**装上的**那些，本仓四个能力型 extra（``crawl4ai`` / ``mineru`` /
``laya`` / ``typesafe-sdk``）**刻意没有安装**（它们是按需启用的重依赖），
所以那条路只能答一半，而答一半的清单会让这张表看起来是被校验过的。

手写的那张表因此自己也要被守：它必须与 ``pyproject.toml`` 声明的那一组**恰好
相等**。多一个键（声明被删了、表没跟上）与少一个键（新依赖加上了、表没跟上）
都会红 —— 人维护的清单与代码对照，漏掉的那一个不是「没问题」，是「不受管」
（同 ``tests/test_ids_are_filenames.py`` 里那张 ``CARRIERS`` 清单）。

## 扫描范围为什么与 ``test_adapters_hold_the_vendors.py`` 一样

``marketops/``：那是会被装出去、会被运行的东西。``tests/``
不在范围内，因为它的依赖是 ``dev`` 那一组（见下），而分开判反而更严 ——
``dev`` 里的东西出现在 ``marketops/`` 里是**一条独立的判据**（runtime 里混进
pytest 不是「一个未声明的依赖」，是这个包依赖了测试工具）。
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

from marketops.config import REPO_ROOT

#: 发行包名 → 它提供的顶层 import 名。见模块 docstring 里「为什么是手写的」。
#:
#: 这张表的键必须与 ``pyproject.toml`` 声明的那一组**恰好相等**，由
#: ``test_the_table_and_the_manifest_declare_the_same_distributions`` 守。
IMPORT_NAMES = {
    # 运行依赖
    "httpx": "httpx",
    "mcp": "mcp",
    "pypdf": "pypdf",
    "pydantic": "pydantic",
    "PyYAML": "yaml",
    # 能力型 extra（按需启用的重依赖，本机刻意没装）
    "crawl4ai": "crawl4ai",
    "mineru": "mineru",
    "laya": "laya",
    "typesafe-sdk": "typesafe_sdk",
}

#: **不是**运行能力的 extra：工具链。
#:
#: 它不是「本仓的一项能力」，而是「开发这个仓要装的东西」—— 所以它的成员走的是
#: **反向**判据：名字出现在 ``marketops/`` 的 import 里才是错的。一个 runtime 模块
#: import pytest，读起来像「测试顺带被带进去了」，实际后果是一次运行期
#: ``ModuleNotFoundError``，而它只在别人机器上现形。
TOOLING_EXTRAS = {"dev"}

#: 工具链发行名 → 顶层 import 名。与 ``IMPORT_NAMES`` 分开，因为两个方向都分开了：
#: 主表问「声明了有没有人用」，这张表问「没人该用的有没有被用」。
#:
#: 它们不许交叉（``test_the_two_tables_do_not_overlap_and_the_tooling_one_is_complete``）：
#: 一个发行包不能既是本仓的一项能力、又是纯工具 —— 那意味着两条判据会对它给出
#: 相反的判决，而两条都会绿。
TOOLING_IMPORT_NAMES = {
    "pytest": "pytest",
    "jsonschema": "jsonschema",
    "ruff": "ruff",
}

#: 会被装出去、会被运行的代码。与 ``test_adapters_hold_the_vendors.py`` 同一范围。
#:
#: ``services/`` 曾在这里 —— 当时的理由是「顶层还有一个平级的、会被运行的包」。
#: 那个包（``services/worker/``）在 B7a 搬进了 ``marketops/interfaces/``，
#: 剩下的两个占位目录本轮也删了（见 ``docs/NOT_BUILT.md``），所以这条范围里
#: **只剩一个**成员。仍然写成元组而不是换成单个字符串：换掉形状会让「再出现一个
#: 会被装出去的顶层包」这件事只能靠改类型来表达，而那正是最该被看见的一次改动。
SCANNED_ROOTS = ("marketops",)

#: 扫描范围的下限。判据范围写坏时，下面每一条都会变成对空集成立的空话 ——
#: 同 ``tests/test_no_stale_assumptions.py::test_the_scan_reaches_the_files_it_claims_to``。
MIN_SCANNED_MODULES = 40
MIN_THIRD_PARTY_NAMES = 6


def _manifest() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _distribution(requirement: str) -> str:
    """``"mcp>=2.2,<3"`` → ``"mcp"``。

    切在第一个不是包名字符的位置：PEP 508 的包名只含字母、数字、``-``、``_``、``.``
    （大小写不敏感）。**不**用 ``re.split(r"[<>=!~]")`` —— 那样 ``typesafe-sdk``
    要靠 ``-`` 不在分隔符里才活得下来，是一条隐形的巧合。
    """
    name = requirement
    for index, char in enumerate(requirement):
        if char not in "-_.!" and not char.isalnum():
            name = requirement[:index]
            break
    return name.strip()


def _declared() -> tuple[dict[str, str], dict[str, list[str]]]:
    """``(运行依赖, extra → 它的成员)``，名字按 ``pyproject`` 里的写法原样。"""
    project = _manifest()["project"]
    runtime = {_distribution(item): item for item in project["dependencies"]}
    extras = {
        name: [_distribution(item) for item in members]
        for name, members in project.get("optional-dependencies", {}).items()
    }
    return runtime, extras


def _imported() -> tuple[set[str], int]:
    """``marketops/`` 里 import 到的**第三方顶层**模块名。

    相对 import 跳过（``level > 0`` 是本仓自己的模块）。``ast.walk`` 会走到函数体
    与 ``try`` 块里 —— 这正是要的：``crawl4ai`` 那一族就是**惰性** import 的，
    只扫模块顶层会把四个能力型 extra 全部读成「没被用」。
    """
    stdlib = set(sys.stdlib_module_names)
    found: set[str] = set()
    modules = 0
    for root in SCANNED_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            modules += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    third_party = {
        name for name in found if name not in stdlib and name != "marketops"
    }
    return third_party, modules


def _unused(
    runtime: dict[str, str],
    extras: dict[str, list[str]],
    imported: set[str],
    table: dict[str, str],
    tooling: set[str],
) -> list[str]:
    """声明了、却没有任何一处 import 的依赖（人读的清单，带它声明在哪）。

    与 ``_undeclared`` 同样抽成函数，理由也一样：**正对照要能证明它会红**。
    表与 extra 集合都是参数，所以对照可以用合成的输入，不受这张表的实际内容影响
    （这个教训是变异 M3 给的，见 ``test_the_undeclared_criterion_actually_discriminates``）。
    """
    unused: list[str] = []
    for name in runtime:
        if table[name] not in imported:
            unused.append(f"{name}（声明在 dependencies 里）")
    for extra, members in extras.items():
        if extra in tooling:
            continue
        for name in members:
            if table[name] not in imported:
                unused.append(f"{name}（声明在 {extra} 里）")
    return unused


def _undeclared(
    imported: set[str], table: dict[str, str], tooling: dict[str, str]
) -> set[str]:
    """被 import 了、却没有任何一条声明提供它的顶层名。

    工具链的成员**在这里被减掉**，它们的错由下面那条泄漏判据单独报：两件事的修法
    相反（一个是「声明它」，一个是「把它从运行代码里删掉」），在这里一并报出会给出
    一个错的修法。只减不报的话那条泄漏判据就永远走不到 —— 被这条先吃掉，
    而一条没有输入能走到的判据与一条恒真的健康旗是同一类东西。

    抽成函数是**为了让正对照能证明它真的会红**
    （``test_the_undeclared_criterion_actually_discriminates``，按名字点，不按第几条 ——
    序数会在下一次插判据时悄悄失效）。一条只对着真实输入跑过一次的判据，
    「它会不会红」是没被证明过的。
    """
    return imported - set(table.values()) - set(tooling.values())


# ----------------------------------------------------------------------
# 一、这张表自己不许漂
# ----------------------------------------------------------------------
def test_the_table_and_the_manifest_declare_the_same_distributions() -> None:
    """``IMPORT_NAMES`` 的键 == ``dependencies`` ∪ 能力型 extra。

    两边的差集都报，理由同 ``tests/test_ids_are_filenames.py``：人维护的清单漏掉
    的那一个不是「没问题」，是「不受管」。少一个键时，下面那条「有没有 import」
    的判据会对这个依赖**无语**；多一个键时，一条已经删掉的声明还在这张表里，
    而它会继续为「这件事被检查过」作证。
    """
    runtime, extras = _declared()
    assert TOOLING_EXTRAS <= set(extras), (
        f"TOOLING_EXTRAS 里的 {TOOLING_EXTRAS - set(extras)} 不在 pyproject 的 extra 里 —— "
        "这条豁免指向了一个不存在的 extra，于是它什么都没豁免"
    )
    capable = set(runtime) | {
        name for members in (v for k, v in extras.items() if k not in TOOLING_EXTRAS)
        for name in members
    }

    missing = capable - set(IMPORT_NAMES)
    stale = set(IMPORT_NAMES) - capable
    assert not missing, (
        f"这些依赖声明了但没有顶层 import 名的映射：{sorted(missing)}。\n"
        "加上它（``pyproject`` 里的发行名 → 顶层 import 名），否则下面那条"
        "「谁 import 了没声明的模块」的判据对它一个字都说不出。"
    )
    assert not stale, (
        f"这些映射没有对应的声明：{sorted(stale)}。\n"
        "声明被删了而这张表没跟上 —— 它会继续为「这个依赖被检查过」作证。"
    )


def test_the_two_tables_do_not_overlap_and_the_tooling_one_is_complete() -> None:
    """工具链表与主表**互补且不交叉**，且它的键 == 工具链 extra 的全部成员。

    互补：一个发行包落在哪张表决定它受哪条判据管辖（「有没有人用」还是「没人该用」）。
    交叉时两条判据都会对它说一句相反的话，而两条都会绿 —— 一张同时被要求「用」和
    「不用」的清单什么也没守。

    完整：漏一个成员，那条「工具链不该被 import」的判据对它一个字都说不出 ——
    而它会继续显示为「工具链这一块查过了」。
    """
    _, extras = _declared()

    overlap = set(IMPORT_NAMES) & set(TOOLING_IMPORT_NAMES)
    assert not overlap, f"这两个发行包同时落在两张表里：{sorted(overlap)}"

    tooling = {name for e in TOOLING_EXTRAS for name in extras.get(e, [])}
    missing = tooling - set(TOOLING_IMPORT_NAMES)
    stale = set(TOOLING_IMPORT_NAMES) - tooling
    assert not missing, (
        f"这些工具链依赖没有顶层 import 名的映射：{sorted(missing)}。\n"
        "补上它，否则「工具链不该出现在 marketops/ 的 import 里」这条判据对它无效。"
    )
    assert not stale, f"这些映射没有对应的工具链声明：{sorted(stale)}"


# ----------------------------------------------------------------------
# 二、声明了却没被 import
# ----------------------------------------------------------------------
def test_every_declared_dependency_is_imported_somewhere() -> None:
    """``beautifulsoup4`` 在这里红 —— 而它在盘上站了很久。

    它的后果不是一次崩溃：一张声明表是**读**的人对系统的第一份描述，而
    「HTML 由 BeautifulSoup 解析」这句话在有这张表的时候看起来是有依据的
    （``interfaces/cli.py`` 那条能力探针当时也在为它作证）。
    """
    runtime, extras = _declared()
    imported, _ = _imported()

    unused = _unused(runtime, extras, imported, IMPORT_NAMES, TOOLING_EXTRAS)
    assert not unused, (
        f"这些依赖声明了但没有任何一处 import：{unused}。\n"
        "要么删掉声明，要么它是真的在用而扫描范围漏了 —— 两种都要有人看一眼，"
        "因为一张声明了不用的依赖会让人按它去理解系统。"
    )


def test_the_unused_criterion_actually_discriminates() -> None:
    """``_unused`` 报得出「声明了没人用」，也**不**报 ``dev`` 里的东西。

    前半条防的是一个恒返回空表的实现（它与一条真的在守事的判据长得一模一样）；
    后半条防的是**豁免被写成了沉默** —— ``dev`` 的成员全都「声明了没人 import」，
    少了那一格跳过，pytest / ruff / jsonschema 会一起被报成债，而正确的动作是
    把那格豁免删掉、不是去 import 它们。

    输入全是合成的，所以这条判据不会被 `pyproject` 的正常改动碰到 ——
    它证明的是**函数会不会红**。
    """
    table = {"boto3": "boto3", "pytest": "pytest", "crawl4ai": "crawl4ai"}

    assert _unused({"boto3": "boto3>=1.34"}, {}, set(), table, set()) == [
        "boto3（声明在 dependencies 里）"
    ]
    assert _unused({}, {"browser": ["crawl4ai"]}, set(), table, set()) == [
        "crawl4ai（声明在 browser 里）"
    ]
    # 而 ``dev`` 那一格必须被跳过 —— pytest 在 table 里、也确实没被 import，
    # 报出来才是错的。
    assert _unused({}, {"dev": ["pytest"]}, set(), table, TOOLING_EXTRAS) == []
    assert _unused({}, {"dev": ["pytest"]}, set(), table, set()) == ["pytest（声明在 dev 里）"]


# ----------------------------------------------------------------------
# 二之二、**报告别人的那张表**也不许替不存在的东西作证
# ----------------------------------------------------------------------
def test_the_dependency_probe_only_probes_declared_dependencies() -> None:
    """``check-deps`` 报告的名字，每一个都必须真的是一条被声明的依赖。

    这条判据守的是一个**具体发生过的**假证人：``interfaces/cli.py`` 那行
    ``("beautifulsoup4", *_probe("bs4"))`` 与 ``pyproject`` 里那条声明互相印证 ——
    而正文抽取从没用过 BeautifulSoup。两个证人，一句假话。

    它不属于上面任何一条：那条「声明了没人用」的判据只管**已经声明**的，
    而「import 了没声明」的判据扫的是 **import 语句** —— 探针名是两个字符串字面量，
    两条都看不见它。所以这一格必须有它自己的判据，否则同一个形状的假证人随时可以
    再生（往探针表里加一个谁也没声明的名字，全绿）。
    """
    from marketops.interfaces.cli import DEPENDENCY_PROBES

    assert DEPENDENCY_PROBES, "探针表是空的 —— 下面这条判据会变成对空集成立的空话"
    probed = {module for module, _ in DEPENDENCY_PROBES}
    declared = set(IMPORT_NAMES.values())
    undeclared = sorted(probed - declared)
    assert not undeclared, (
        f"``check-deps`` 探了这些没有被声明的模块：{undeclared}。\n"
        "它会在报告里显示成一条正经的依赖行 —— 而那正是 beautifulsoup4 当年"
        "在那张表上站着的方式。要么声明它，要么把它从探针表里去掉。"
    )


# ----------------------------------------------------------------------
# 三、import 了却没声明
# ----------------------------------------------------------------------
def test_every_third_party_import_is_declared() -> None:
    """``mcp`` 在这里红 —— 而它是本仓对外接口的 SDK。

    它的症状只在**干净安装**上现形：开发机上装着，所以整套测试全绿。

    与上面那条的分工要看清：这条读的是**映射表**（``import ⊆ 表``），而表与清单的
    相等是上面那条的事。两条合起来才是 ``import ⊆ 声明``。所以把一条依赖从
    ``pyproject`` 里删掉时，红的是**上面那条**（表里多了一个键），这条反而不会响 ——
    这不是漏洞，是两条判据各报各的错，而「表不再可信」才是要先修的那件事。
    """
    runtime, extras = _declared()
    imported, _ = _imported()
    table = IMPORT_NAMES

    undeclared = _undeclared(imported, table, TOOLING_IMPORT_NAMES)
    assert not undeclared, (
        f"这些模块被 import 了，却没有任何一条声明提供它们：{sorted(undeclared)}。\n"
        "声明它们（``dependencies`` 或一个能力型 extra），并给这张表加一行 —— "
        "否则一次干净安装会在 import 时坏，而本仓的测试环境里它们是装着的。"
    )
    # 反方向不该成立的豁免：运行依赖里混进工具链。
    tooling = {name for e in TOOLING_EXTRAS for name in extras.get(e, [])}
    leaked = sorted(
        TOOLING_IMPORT_NAMES[name] for name in tooling if TOOLING_IMPORT_NAMES[name] in imported
    )
    assert not leaked, (
        f"工具链依赖出现在 {SCANNED_ROOTS} 的 import 里：{leaked}。\n"
        "``dev`` 那一组装的是开发这个仓要用的东西，运行期 import 它意味着"
        "一次只在别人机器上现形的 ModuleNotFoundError。"
    )

    # 空声明表会让上面两条判据都变成对空集成立的空话。
    assert runtime, "dependencies 是空的 —— 上面两条判据都会变成对空集成立的空话"


# ----------------------------------------------------------------------
# 四、正对照：这些判据真的会红
# ----------------------------------------------------------------------
def test_the_undeclared_criterion_actually_discriminates() -> None:
    """给 ``_undeclared`` 塞一个没人声明过的模块，它必须报出来。

    没有这一条，一个把 ``_undeclared`` 写成 ``return set()`` 的实现（或者一个
    真实集合恰好永远为空的判据）与一条真的在守事的判据**长得一模一样**。
    同 ``tests/test_job_contract.py`` 里那四条 ``*_actually_discriminates``。
    """
    runtime, _ = _declared()
    imported, _ = _imported()

    # 探针名刻意是一个**不可能是真实发行包**的字符串，理由是它唯一要防的事：
    # 这条正对照证明的是**函数会不会红**，不是**表里有什么**。一个写成 ``"boto3"``
    # 的探针会因为有人真的把 boto3 加进映射表而转红 —— 那时它报的是一个与它无关的
    # 事实，而修法（换探针名）看起来又像在绕过判据。（这一版最初就写的是 ``boto3``，
    # 变异 M3 顺手把它打红了。）
    probe = "not-a-real-distribution"
    assert probe not in IMPORT_NAMES.values()

    assert _undeclared(imported, IMPORT_NAMES, TOOLING_IMPORT_NAMES) == set(), (
        "真实输入上不该有未声明的模块"
    )
    assert _undeclared(imported | {probe}, IMPORT_NAMES, TOOLING_IMPORT_NAMES) == {probe}
    # 而工具链的名字**不该**在这里被报 —— 它有一条自己的判据，两条的修法相反。
    # 少了这一行，一个把工具链也一并报出的实现会给出错的修法提示，而那条泄漏判据
    # 会变成走不到的死代码。
    assert _undeclared(imported | {"pytest"}, IMPORT_NAMES, TOOLING_IMPORT_NAMES) == set()

    # 而「声明了没人用」那一侧的正对照：把一个真的在用的集合拿掉一个成员。
    assert runtime and (IMPORT_NAMES[next(iter(runtime))] in imported)


def test_the_scan_range_is_not_empty() -> None:
    """扫描范围写坏时，上面每一条都变成空话 —— 而它们仍然是绿的。

    与 ``tests/test_no_stale_assumptions.py`` 里那条同一个道理：一条对空集成立的
    边界与没有这条边界是一件事。
    """
    imported, modules = _imported()

    # 范围跟着 manifest 走，不跟着写这行字的人的记性走：往 wheel 里加一个包而不加
    # 扫描范围，那几行判据会对它一个字都说不出 —— 而它们仍然全绿。
    wheel = set(_manifest()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])
    outside = sorted(wheel - set(SCANNED_ROOTS))
    assert not outside, (
        f"这些包会被装出去、却不在扫描范围里：{outside}。\n"
        f"把 {SCANNED_ROOTS} 补上 —— 否则新包里的 import 不受任何一条判据管辖。"
    )

    assert modules >= MIN_SCANNED_MODULES, f"只扫到 {modules} 个模块，范围可能已被改坏"
    assert len(imported) >= MIN_THIRD_PARTY_NAMES, (
        f"只扫到 {len(imported)} 个第三方顶层模块：{sorted(imported)}"
    )
    # 两个方向都要求这些名字真的被扫到 —— 否则扫描器可能恰好漏掉了某一类 import。
    assert {"httpx", "mcp", "pydantic", "yaml"} <= imported


def test_the_manifest_is_the_one_we_think_it_is() -> None:
    """读到的 ``pyproject.toml`` 是这个仓的，不是一个找到了的近邻。

    ``REPO_ROOT`` 指错位置时，上面每一条都会对着另一份清单作答 —— 而那份清单
    可能恰好是自洽的。
    """
    project = _manifest()["project"]
    assert project["name"] == "marketops"
    assert (REPO_ROOT / "marketops" / "config.py").is_file()
    assert Path(REPO_ROOT).name, "REPO_ROOT 应当是一个真实路径"
