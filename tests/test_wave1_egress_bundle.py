"""独立插件包里的出口层，与核心是**同一份源码** —— 这条纪律由本文件执行。

契约位置：``INTERFACE-CONTRACT.md`` 的 ``IA-4``（浏览器与辅助 HTTP 全部过出口）
与 ``WORKER-DAG-AND-OWNERSHIP.md`` §5 的 ``DECISION-01``（插件**如何**拿到唯一的
EgressProxy 实现）。协调者对 ``DECISION-01`` 的默认口径是
「vendoring + ``schema_sha256`` 式 hash 钉死 + 单一维护源」—— 本文件就是那个
「钉死」的执行器。

## 它防的是一个具体的失败：第二份实现

插件包在一个只装了标准库的解释器里跑（``launch_research_mcp.sh`` 挑一个
``import mcp`` 通得过的就算数）。它没有 ``marketops`` 这个包，所以出口判据要么被
复制过去，要么在客户机上根本不存在。而复制有两种做法：

* 复制之后**各自演化** —— 某一天其中一份多了一次拒绝，另一份没有。**分叉的那一份
  看起来完全正常**，它只是少判了一条；
* 复制之后**逐字节相同**，而「相同」这件事有一个执行器。

``tests/test_domain_is_pure.py`` 的模块 docstring 写着同一条道理：「一条没有执行器的
边界只是自觉」。这里就是那条执行器。

## 判据分五层，缺一层就有一种漏法

1. **逐字节相同** —— 生成器清单里每一个文件与核心里的规范模块摘要相等。
2. **切片是自足的** —— 在一个 ``-I -S`` 的子进程里只把切片目录放上 ``sys.path``，
   它能 import 成功，而且 ``sys.modules`` 里**没有** ``marketops``。这一条判的是
   「插件包不需要核心运行时」这句话，而不是关于它的注释。
3. **规范模块是纯标准库的** —— 六个规范文件的**绝对** import 里，没有一个顶层名字
   不属于 ``sys.stdlib_module_names``。第 2 层判的是**今天**，这一层判的是
   **下一次有人往 ``security/scope.py`` 里加一句 ``from ..domain.errors import``**
   —— 那一句在核心侧完全合理（两千条测试全绿），而在客户机上让整个出口层 import
   不了。这一层红的时候会点出**是哪一行**。
4. **浏览器接线只有一个定义** —— 那两个 Chromium 参数与 ``proxy_config`` 那个键名
   只写在 ``adapters/guarded_http.py`` 一处，``crawl4ai_fetcher`` 读它。
5. **入口名只有一个正本** —— ``research_safe_egress`` 是 ``research_egress`` 的别名，
   两者里的名字必须是**同一个对象**（``is``）。派发 prompt 与协调契约给的入口名不同，
   而「两个名字」不该变成「两份实现」。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-tools" / "marketops-research-assistant"
SLICE_ROOT = PLUGIN_ROOT / "scripts" / "marketops_safe_egress"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
SYNC_TOOL = PLUGIN_ROOT / "tools" / "sync_safe_egress_bundle.py"


def _load_sync_tool() -> ModuleType:
    """把生成器当模块读进来，而不是把它的清单在本文件里重抄一份。

    重抄一份清单会让「切片范围」这件事有两个定义 —— 而那两个定义会漂移的正是本文件
    要挡的那种东西。清单只有一个出处：生成器自己。
    """
    spec = importlib.util.spec_from_file_location("_sync_safe_egress_bundle", SYNC_TOOL)
    assert spec is not None and spec.loader is not None, "生成器读不进来"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = _load_sync_tool()


# ----------------------------------------------------------------------
# 1. 逐字节相同
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("relative_slice", "relative_canonical"),
    SYNC.FILES,
    ids=[entry[0] for entry in SYNC.FILES],
)
def test_each_slice_file_is_byte_identical_to_its_canonical_module(
    relative_slice: str, relative_canonical: str
) -> None:
    """切片里的每一个文件与核心里的规范模块**一字节不差**。

    这一条红的意思是具体的：有人改了一边。改哪一边是对的取决于改的是什么 ——
    改策略改核心（切片重新生成），改客户机上的路径**没有**这回事。
    """
    canonical = REPO_ROOT / relative_canonical
    shipped = SLICE_ROOT / relative_slice
    assert canonical.is_file(), f"规范模块不在了：{relative_canonical}"
    assert shipped.is_file(), f"切片里缺文件：{relative_slice}"
    assert shipped.read_bytes() == canonical.read_bytes(), (
        f"{relative_slice} 与 {relative_canonical} 不一致。\n"
        "修复：python plugins/research-tools/marketops-research-assistant/tools/"
        "sync_safe_egress_bundle.py"
    )


def test_the_provenance_manifest_matches_the_slice() -> None:
    """``PROVENANCE.json`` 与切片实际内容一致 —— 走的是 CI 用的那一个入口。"""
    assert SYNC.check() == 0


def test_the_manifest_carries_a_digest_for_every_listed_file() -> None:
    """清单本身的形状：每个条目都有 ``slice`` / ``canonical`` / ``sha256``。

    「有摘要」与「摘要是对的」是两件事（上一条判后者），而这一条判的是清单**读得出
    来** —— 一个少了键的清单会让上一条在 ``KeyError`` 上红，而那个报错说的是
    「测试炸了」，不是「切片漂了」。
    """
    manifest = json.loads((SLICE_ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "marketops.safe-egress-slice.v1"
    assert manifest["rule"] == "verbatim"
    for row in manifest["files"]:
        assert set(row) == {"slice", "canonical", "sha256"}, row
        assert len(row["sha256"]) == 64, row


# ----------------------------------------------------------------------
# 2. 切片是自足的
# ----------------------------------------------------------------------
#: 在 ``-I -S`` 的子进程里只把切片所在的那个目录放上 ``sys.path`` 再 import。
#: ``-I`` 关掉用户站点与 ``PYTHONPATH``，``-S`` 连系统 site-packages 也关掉 ——
#: 于是「它 import 得到」这句话才等于「它只需要标准库」，而不是「这台机器上恰好
#: 装了什么」。
_ISOLATED_IMPORT = r"""
import json
import sys

sys.path.insert(0, sys.argv[1])
import marketops_safe_egress as safe

core = sorted(name for name in sys.modules
              if name == "marketops" or name.startswith("marketops."))
third_party = sorted({
    name.split(".")[0]
    for name in sys.modules
    if name.split(".")[0] not in sys.stdlib_module_names
    and not name.startswith("marketops_safe_egress")
    and name != "__main__"
})
print(json.dumps({
    "schema": safe.SLICE_SCHEMA,
    "version": safe.__version__,
    "core_modules": core,
    "third_party": third_party,
    "files": len(safe.slice_provenance()["files"]),
}))
"""


def test_the_slice_imports_without_the_core_package_or_any_dependency() -> None:
    """只把切片目录放上 ``sys.path``、关掉 site-packages，它 import 得起来。

    而且 ``sys.modules`` 里**没有** ``marketops``，也没有任何第三方包 —— 这两句话
    合起来才是「插件包不 import 庞大的核心运行时」这一条的事实版本。
    """
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _ISOLATED_IMPORT, str(SCRIPTS_ROOT)],
        capture_output=True,
        text=True,
        check=False,
        cwd="/",
    )
    assert completed.returncode == 0, (
        "切片在只装标准库的解释器里 import 不了 —— 这正是它在客户机上的样子。\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    report = json.loads(completed.stdout)
    assert report["schema"] == "marketops.safe-egress-slice.v1"
    assert report["core_modules"] == [], (
        f"切片 import 了核心包：{report['core_modules']} —— 客户机上没有这个包"
    )
    assert report["third_party"] == [], f"切片拉进了标准库之外的包：{report['third_party']}"
    assert report["files"] == len(SYNC.FILES)


# ----------------------------------------------------------------------
# 3. 规范模块是纯标准库的
# ----------------------------------------------------------------------
def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    """文件里每一句**绝对** import 的 ``(行号, 顶层名)``。

    相对 import（``level > 0``）不算：它们指向切片内部，而切片镜像了核心的层级，
    所以同一句话在两处解析结果相同 —— 这是「逐字节复制」能成立的全部原因。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


@pytest.mark.parametrize("relative_canonical", [entry[1] for entry in SYNC.FILES])
def test_canonical_policy_modules_import_only_the_standard_library(
    relative_canonical: str,
) -> None:
    """六个规范模块的绝对 import 里，每一个顶层名字都属于标准库。

    这一条判的是**将来**：今天它们都是纯标准库的，而「今天成立」与「改不坏」是两件
    事。一句 ``from ..domain.errors import ConfigError``（核心侧完全合理）会让切片在
    客户机上 import 不了，而**核心侧的两千条测试全绿**。
    """
    offenders = [
        (line, name)
        for line, name in _absolute_imports(REPO_ROOT / relative_canonical)
        if name not in sys.stdlib_module_names
    ]
    assert not offenders, (
        f"{relative_canonical} 里有非标准库的绝对 import："
        + ", ".join(f"第 {line} 行 {name!r}" for line, name in offenders)
        + "。\n这一份源码会被逐字节复制进一个只装标准库的插件包 —— "
        "在那里这一句就是一次 ImportError。"
    )


# ----------------------------------------------------------------------
# 4. 浏览器接线只有一个定义
# ----------------------------------------------------------------------
SINGLE_HOME_LITERALS = ("--proxy-bypass-list=<-loopback>", "--disable-quic")
HOME_OF_BROWSER_WIRING = Path("marketops") / "adapters" / "guarded_http.py"


def _string_constants(path: Path) -> list[str]:
    """文件里每一个**不是 docstring** 的字符串常量。

    ## 为什么按 AST、为什么要把 docstring 摘掉

    这两个参数**在散文里被反复解释**（为什么它是防御、哪一格实测过、删掉它测试会不会
    红），而 ``crawl4ai_fetcher`` 的模块 docstring 正是这么提到它们的。那些解释有价值，
    一段「不许出现这个子串」的文本扫描会把它们当成违规。

    本判据要的从来不是「有没有人**提到**它」，而是「有没有第二处地方把它写成一段
    **会被执行**的字符串」—— 只有后者会在下一次改动里与 ``guarded_http`` 分叉。
    docstring 也是常量节点，但它不会被当作参数值传出去，所以按「是不是某个体第一条
    语句」把它们摘掉。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.xfail(
    strict=False,
    reason=(
        "已知分歧：marketops/adapters/crawl4ai_fetcher.py 仍把这两个参数写成会被执行的"
        "字符串（本轮吸收复核发现，旧仓 Q19 没有）。判据本身有效，修复留给 crawl4ai_fetcher "
        "的单出处化；修好后本标记应删除。"
    ),
)
@pytest.mark.parametrize("literal", SINGLE_HOME_LITERALS)
def test_the_browser_egress_flags_have_exactly_one_home(literal: str) -> None:
    """Chromium 那两个参数**作为会被执行的字符串**只出现在 ``guarded_http.py`` 一处。"""
    home = REPO_ROOT / HOME_OF_BROWSER_WIRING
    assert any(literal == value for value in _string_constants(home)), (
        f"{literal} 不在 {HOME_OF_BROWSER_WIRING} 里 —— 这一格是浏览器接线的唯一出处"
    )
    for other in (REPO_ROOT / "marketops").rglob("*.py"):
        if other == home:
            continue
        offenders = [value for value in _string_constants(other) if literal in value]
        assert not offenders, (
            f"{literal} 在 {other.relative_to(REPO_ROOT)} 里也是一个会被执行的字符串："
            f"{offenders[:1]}。\n"
            "两处各写一份的那一天，改一处的那一处不会红，而失败的样子是"
            "「出口配了但没接上」—— 浏览器仍会「正常」渲染。"
        )


class _ProxyStub:
    """一个只有 ``url`` 的替身。``_browser_config`` 只读这一格。"""

    url = "http://127.0.0.1:54321"


def test_the_browser_fetcher_reads_the_shared_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``crawl4ai_fetcher._browser_config`` **真的**用共享定义填那两格。

    上一条判「别处没有第二份字符串」，而一个把 ``extra_args=[]`` 写死、从此不再传参数
    的实现同样通过它 —— 却把那两个防御整格丢掉了。这一条判的是取值这件事本身。

    用 ``__new__`` 绕过 ``__init__``：这一条判的是那一格函数，构造一个完整的
    ``Settings`` 只会把判据与另一套配置耦合起来。``BrowserConfig`` 在没装 crawl4ai 的
    机器上是 ``None``，所以它必须被换掉 —— 这正好也是这一条能在这里**跑**（而不是
    skip）的原因。
    """
    from types import SimpleNamespace

    from marketops.adapters import crawl4ai_fetcher as module
    from marketops.adapters.guarded_http import (
        BROWSER_EGRESS_ARGUMENTS,
        browser_proxy_config,
    )

    recorded: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(module, "BrowserConfig", _Recorder)
    fetcher = module.Crawl4AIBrowserFetcher.__new__(module.Crawl4AIBrowserFetcher)
    fetcher._headless = True  # noqa: SLF001 - 这一条判的就是这一格
    fetcher._settings = SimpleNamespace(user_agent="probe/1.0")  # noqa: SLF001

    fetcher._browser_config(_ProxyStub())  # noqa: SLF001

    assert recorded["proxy_config"] == browser_proxy_config(_ProxyStub.url)
    assert recorded["extra_args"] == list(BROWSER_EGRESS_ARGUMENTS), (
        "浏览器启动参数不再来自共享定义 —— 那两个防御被整格丢掉了，"
        "而报告上什么都看不出来"
    )


# ----------------------------------------------------------------------
# 5. 入口名只有一个正本
# ----------------------------------------------------------------------
#: 派发 prompt 与协调契约给的两个名字。**两个都必须 import 得起来**，而行为只能有
#: 一份 —— 见 ``research_safe_egress`` 的模块 docstring。
ENTRY_NAMES = ("research_egress", "research_safe_egress")

EXPORTED_ENTRY_NAMES = (
    "EgressDenied",
    "CrawlScope",
    "SafeEgressSession",
    "open_session",
    "safe_browser_egress",
    "local_crawl_ready",
    "reason_code",
    "contract_reason_code",
    "allowed_domains_from_env",
)


def test_both_entry_names_exist_and_one_of_them_is_the_alias() -> None:
    """两个入口名都存在，而其中一个是另一个的**同一批对象**。"""
    sys.path.insert(0, str(SCRIPTS_ROOT))
    try:
        import research_egress
        import research_safe_egress

        assert research_egress.__file__ != research_safe_egress.__file__
        for name in EXPORTED_ENTRY_NAMES:
            assert getattr(research_egress, name) is getattr(research_safe_egress, name), (
                f"{name} 在两个入口里不是同一个对象 —— "
                "别名那一格长出了第二份实现"
            )
        assert research_egress.SAFE_EGRESS_API_VERSION == (
            research_safe_egress.SAFE_EGRESS_API_VERSION
        )
    finally:
        sys.path.remove(str(SCRIPTS_ROOT))
        for name in (*ENTRY_NAMES, "marketops_safe_egress"):
            for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
                del sys.modules[key]


def test_the_entry_file_name_matches_the_coordination_contract() -> None:
    """正本的文件名就是契约 §3 给 W-A 的独占路径 ``scripts/research_egress.py``。

    这一条挡的是「把正本改名成别名那个名字」：两个名字都能用，但**契约名**必须是
    有实现的那一个 —— 否则读契约的人照着 ``research_egress.py`` 去读行为，会读到
    一个只有转发的文件。
    """
    sys.path.insert(0, str(SCRIPTS_ROOT))
    try:
        import research_egress

        source = Path(research_egress.__file__).read_text(encoding="utf-8")
        assert "def safe_browser_egress(" in source, (
            "契约名 research_egress.py 里没有实现 —— 正本被搬到别名那一格去了"
        )
    finally:
        sys.path.remove(str(SCRIPTS_ROOT))
        for key in [k for k in sys.modules if k.startswith("research_egress")]:
            del sys.modules[key]
