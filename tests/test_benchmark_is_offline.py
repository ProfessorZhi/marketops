"""基准 Runner 必须**拿不到**网络能力，而不是「我们不调用」。

``benchmark/runner/__init__.py`` 写着「can be imported, tested and run offline …
never touches network by default」。一句话的「默认不碰」在第一万次修改里失效的
方式是**静默**的：某天为了「顺手把录像也抓下来」import 了 ``httpx``，行为上仍然
正确，只是离线套件从此会联网、会超时、会在断网机器上失败，而没有一条判据会响。

所以这一条判的是**闭包**：``benchmark.runner`` 里的任一模块，直接或间接，都到不了
任何网络能力。判的是「拿不到」，不是「拿到的时候表现如何」。

复用 ``tests/test_registration_is_offline.py`` 的判据本体（``_network_reach``），
而不是另写一份 —— 一份已经带正/反对照的判据比第二份只测 ``ast`` 的判据更可信。

这一条**判不到**什么：用不在清单里的名字联网（``os.system``、``ctypes``）。所有
白名单判据都有这个上限，写在这里而不是留给读者自己发现。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.test_adapters_hold_the_vendors import _imported_modules, _module_name
from tests.test_registration_is_offline import _network_reach

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = REPO_ROOT / "benchmark" / "runner"

#: 必须逐个点名的 runner 模块。
#:
#: 不用 ``glob`` 当清单：那样删掉一个文件，清单就跟着缩小，判据于是永远全绿。
#: ``test_every_runner_module_is_named_here`` 把「盘上多了一个文件」变成一次必须
#: 做的决定，而不是一次静默的漏判。
BENCHMARK_SURFACE: tuple[str, ...] = (
    "benchmark/runner/__init__.py",
    "benchmark/runner/cli.py",
    "benchmark/runner/judge.py",
    "benchmark/runner/metrics.py",
    "benchmark/runner/models.py",
    "benchmark/runner/normalize.py",
    "benchmark/runner/report.py",
)


def _import_time_modules(tree: ast.AST, *, package: str) -> set[str]:
    """这份源码**加载时**会执行的 import 解析出的仓库内/顶层名字。

    函数体里的 import 不算：``models.validate_case`` 里的
    ``try: import jsonschema except ImportError`` 正是「可选依赖」的实现方式，
    也是 ``no third-party packages`` 那句承诺能被兑现的原因。把它当违规判，
    会逼着人删掉那个 fallback —— 那才是真的破坏了承诺。
    """
    names: set[str] = set()
    stack = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mini = ast.Module(body=[node], type_ignores=[])
            names |= _imported_modules(mini, package=package)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        else:
            stack.extend(ast.iter_child_nodes(node))
    return names


def _runner_sources() -> tuple[dict[str, str], set[str]]:
    sources: dict[str, str] = {}
    packages: set[str] = set()
    for rel in BENCHMARK_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        sources[name] = path.read_text(encoding="utf-8")
        if path.name == "__init__.py":
            packages.add(name)
    return sources, packages


def test_the_runner_surface_is_on_disk() -> None:
    """清单里的每一项都必须真的在盘上。

    一个不存在的模块当然不 import 任何东西 —— 路径写错会让下面的判据**静默**
    变成恒真。
    """
    missing = [rel for rel in BENCHMARK_SURFACE if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"runner 清单点名的模块不在盘上：{missing}"


def test_every_runner_module_is_named_here() -> None:
    """新增一个模块必须显式加进清单，不能靠 glob 自动被覆盖。"""
    on_disk = {
        path.resolve().relative_to(REPO_ROOT).as_posix()
        for path in sorted(RUNNER_DIR.glob("*.py"))
    }
    named = set(BENCHMARK_SURFACE)
    assert on_disk == named, (
        f"runner 目录与清单不一致。多出来的：{sorted(on_disk - named)}；"
        f"清单里已经没有的：{sorted(named - on_disk)}。"
    )


def test_the_runner_never_reaches_the_network() -> None:
    """本体：runner 的闭包到不了任何网络能力。"""
    sources, packages = _runner_sources()
    start = set(sources)
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "benchmark runner 能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
    )


def test_the_runner_does_not_import_the_production_package() -> None:
    """基准是独立工具，不是生产调度器的一部分。

    这条判的是「不依赖」而不只是「不联网」：一个 ``from marketops.application import
    x`` 会让基准跟着生产代码一起漂，而漂的方式是**评分口径**变了、没人发现。
    """
    sources, _ = _runner_sources()
    offenders: dict[str, list[str]] = {}
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)
        package = name if name.endswith("__init__") else name.rpartition(".")[0]
        imported = _imported_modules(tree, package=package)
        hits = sorted(n for n in imported if n == "marketops" or n.startswith("marketops."))
        if hits:
            offenders[name] = hits
    assert not offenders, f"runner 依赖了生产包：{offenders}"


def test_the_runner_loads_nothing_outside_the_standard_library() -> None:
    """``models.py`` 承诺「bare Python 3.10+ with no third-party packages」。

    判的是**加载时**：一个模块级的 ``import jsonschema`` 会打破那句承诺与离线
    可移植性；函数里的可选 import（``validate_case`` 的 fallback）不算，见
    ``_import_time_modules``。
    """
    allowed = set(sys.stdlib_module_names) | {"benchmark"}
    sources, _ = _runner_sources()
    offenders: dict[str, list[str]] = {}
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)
        package = name if name.endswith("__init__") else name.rpartition(".")[0]
        roots = {n.split(".")[0] for n in _import_time_modules(tree, package=package)}
        extra = sorted(r for r in roots if r and r not in allowed)
        if extra:
            offenders[name] = extra
    assert not offenders, f"runner 加载时引入了标准库以外的模块：{offenders}"


# --------------------------------------------------------------------------------------
# 判据自证
# --------------------------------------------------------------------------------------


def test_the_scanner_catches_a_direct_violation() -> None:
    chains = _network_reach(
        {"benchmark.runner.cli": "import httpx\n"},
        packages=set(BENCHMARK_SURFACE),
        start=("benchmark.runner.cli",),
    )
    assert chains == [("benchmark.runner.cli", "httpx")], f"直接违规没抓到：{chains}"


def test_the_scanner_catches_an_indirect_violation() -> None:
    """真正的形态是间接的：cli → 一个 Helper → socket。只判直接 import 会漏。"""
    chains = _network_reach(
        {
            "benchmark.runner.cli": "from .helper import read\n",
            "benchmark.runner.helper": "import socket\n",
        },
        packages=set(BENCHMARK_SURFACE),
        start=("benchmark.runner.cli",),
    )
    assert chains == [
        ("benchmark.runner.cli", "benchmark.runner.helper", "socket")
    ], f"间接违规没抓到：{chains}"


def test_the_scanner_accepts_a_clean_module() -> None:
    """反转对照：干净的那一份不许报红，否则「见谁都报」也能过上面两条。"""
    chains = _network_reach(
        {"benchmark.runner.cli": "import json\nfrom pathlib import Path\n"},
        packages=set(BENCHMARK_SURFACE),
        start=("benchmark.runner.cli",),
    )
    assert chains == [], f"干净的模块被误判：{chains}"


def test_the_check_can_actually_turn_red() -> None:
    """自证：把真闭包里的一个模块换成联网版本，本体判据必须红。

    这不是重复的正对照 —— 它证明的是 ``_network_reach`` 吃**真模块名**时仍然
    会响，而不是只在 fabricated 名字上灵。
    """
    sources, packages = _runner_sources()
    poisoned = dict(sources)
    poisoned["benchmark.runner.models"] = "import httpx\n"
    chains = _network_reach(poisoned, packages=packages, start=set(poisoned))
    assert chains, "真闭包被投毒后判据仍然是绿的 —— 它没有在看这份源码"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
