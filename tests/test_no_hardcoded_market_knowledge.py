"""核心系统不得包含任何具体市场的知识。

这是 ``docs/SOURCES.md`` §3「业务需求只允许从 approved 通道推导」在**代码层**的
可执行版本 —— 同一纪律的另一面：

    if "Grounds Manager" in title:   # ← 禁止

一个扫描整个 ``marketops/`` 包的测试，比一句写在文档里的约定更可靠 ——
文档不会在 CI 里失败。

扫描走 AST 而不是正则，因为核心代码的 **docstring 里会引用反面例子**
（「禁止这样写」的那一行本身包含被禁的字符串）。用纯文本扫描会把这些说明
判成违规，然后测试就只好被放宽 —— 那是把测试改成迎合现实，不是改现实。

因此本测试的判据是精确的：

- **docstring 与注释豁免**（它们是说明，不是行为）
- **字符串字面量不豁免**（``"Grounds Manager"`` 出现在代码里就是硬编码）
- **标识符不豁免**（变量名/函数名/属性名）

与 ``tests/test_profiles.py`` 的分工：那一条用极小词表（3 个）扫同一个包并
**整文件**跳过 ``config.py``；本文件用完整的 16 个词表、并额外扫标识符与
``"字面量" in title`` 的 AST 形状，**只豁免一个指针字面量**。两条都在，
是因为它们防的是不同程度的漂移：一条防「engine 长出业务」，一条防
「engine 长出这个市场的业务」。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = REPO_ROOT / "marketops"

#: 出现在首个 profile 里的市场知识。这些词**只允许**出现在 profiles/ 与 docs/。
FORBIDDEN_IN_CORE = (
    # 角色
    "grounds manager",
    "groundskeeper",
    "groundsman",
    "sports turf manager",
    "pitch manager",
    "venue manager",
    "facilities manager",
    "head of grounds",
    # 市场 / 地域
    "acme",
    "natural grass",
    "cricket",
    "rugby",
    "football club",
    "united kingdom",
    # 排除项
    "footgolf",
    "artificial grass",
    "muga",
)

#: 唯一允许出现在通用 engine 里的业务串：默认 profile id。
#:
#: 它是**配置指针**，不是业务规则 —— 它说明去 ``profiles/`` 下读哪个目录，
#: 不声明那个目录里有什么。上面 16 个词描述的是**世界**，这一个描述的是**地址**。
#:
#: 豁免的粒度是**这一个字面量**，不是一个文件：``config.py`` 里出现任何别的
#: 业务词、或任何业务标识符，仍然失败。而且它不得出现在 ``config.py`` 之外 ——
#: 那由 ``test_pointer_literal_appears_only_in_the_pointer_module`` 守住。
POINTER_LITERAL = "uk-acme-natural-grass"
POINTER_MODULE = "marketops/config.py"


def _python_sources() -> list[Path]:
    return sorted(CORE_PACKAGE.rglob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集 docstring 对应的 Constant 节点 id。

    docstring 的定义：Module / ClassDef / FunctionDef / AsyncFunctionDef 的
    第一个语句且为字符串常量。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _label(path: Path, root: Path) -> str:
    """相对 root 的路径；root 之外（阳性对照的探针）就报绝对路径。

    探针文件刻意写在 ``tmp_path`` 而不是 ``marketops/`` 里 —— 这个包是源码树，
    测试不该往里写东西（中断会留下残骸，并发跑会撞车）。
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _scan(path: Path, *, root: Path = REPO_ROOT) -> list[str]:
    """返回该文件里出现的违规。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    exempt = _docstring_nodes(tree)
    rel = _label(path, root)
    offenders: list[str] = []

    for node in ast.walk(tree):
        # 1) 非 docstring 的字符串字面量
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in exempt:
                continue
            if rel == POINTER_MODULE and node.value == POINTER_LITERAL:
                continue
            lowered = node.value.casefold()
            for needle in FORBIDDEN_IN_CORE:
                if needle in lowered:
                    offenders.append(f"{rel}:{node.lineno}: 字符串字面量含 {needle!r}")

        # 2) 标识符（名字 / 属性 / 参数名 / 定义名）
        elif isinstance(node, ast.Name):
            _check_identifier(node.id, rel, node.lineno, offenders)
        elif isinstance(node, ast.Attribute):
            _check_identifier(node.attr, rel, node.lineno, offenders)
        elif isinstance(node, ast.arg):
            _check_identifier(node.arg, rel, node.lineno, offenders)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _check_identifier(node.name, rel, node.lineno, offenders)

    return offenders


def _check_identifier(name: str, rel: str, lineno: int, offenders: list[str]) -> None:
    lowered = name.casefold().replace("_", " ")
    for needle in FORBIDDEN_IN_CORE:
        if needle in lowered:
            offenders.append(f"{rel}:{lineno}: 标识符含 {needle!r}: {name!r}")


# --- 对照：扫描器本身必须能抓违规、且不误判 ----------------------------------


def test_core_package_has_sources():
    assert _python_sources(), "marketops/ 下没有 .py 文件，扫描会假通过"


def test_scanner_actually_catches_a_violation(tmp_path: Path):
    """阳性对照 —— 扫描器本身必须能抓到违规。

    没有这个测试，「扫描通过」可能是扫描器坏了。
    """
    probe = tmp_path / "probe.py"
    probe.write_text('def f():\n    return "Grounds Manager"\n', encoding="utf-8")
    assert _scan(probe, root=tmp_path), "扫描器未能抓到明显的违规，扫描结果不可信"


def test_scanner_exempts_docstrings(tmp_path: Path):
    """阴性对照 —— docstring 里引用反面例子不算违规。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        'def f():\n    """不要写 if "Grounds Manager" in title。"""\n    return 1\n',
        encoding="utf-8",
    )
    assert _scan(probe, root=tmp_path) == [], "docstring 里的说明被误判为违规"


def test_scanner_exempts_only_the_pointer_literal(tmp_path: Path):
    """指针豁免的**边界** —— 同一文件里的其他业务词仍然失败。

    这条防的是把「豁免一个字面量」偷偷放宽成「豁免这个文件」。
    """
    probe = tmp_path / "config.py"
    probe.write_text(
        f'PROFILE = "{POINTER_LITERAL}"\nOTHER = "cricket club"\n', encoding="utf-8"
    )
    offenders = _scan(probe, root=tmp_path)
    # 探针路径不等于 POINTER_MODULE，所以连指针本身也应当被报出来 ——
    # 这同时证明了豁免是**按路径+字面量**精确匹配的，不是按字面量全局开洞。
    assert len(offenders) == 2, f"指针豁免的粒度不对：{offenders}"


# --- 不变量 ------------------------------------------------------------------


def test_no_market_knowledge_in_core():
    offenders: list[str] = []
    for path in _python_sources():
        offenders.extend(_scan(path))

    assert not offenders, (
        "核心代码里出现了具体市场的知识（应当只存在于 profiles/）：\n  " + "\n  ".join(offenders)
    )


def test_pointer_literal_appears_only_in_the_pointer_module():
    """默认 profile id 只在指针模块里出现，且必须真的出现。

    「必须出现」那一半同样重要：这个词一旦不在 ``config.py`` 里，说明扫描范围
    已经漂了（改了目录结构、改了包名），此时「没有违规」是假通过。
    """
    hits: list[str] = []
    for path in _python_sources():
        rel = _label(path, REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == POINTER_LITERAL:
                hits.append(f"{rel}:{node.lineno}")

    assert hits, f"找不到默认 profile 指针 {POINTER_LITERAL!r} —— 扫描范围可能已失效"
    stray = [hit for hit in hits if not hit.startswith(f"{POINTER_MODULE}:")]
    assert not stray, f"默认 profile id 只应出现在 {POINTER_MODULE}，实际还出现在：{stray}"


def test_core_does_not_dispatch_on_literal_titles():
    """没有把字符串字面量直接和 title 比较的分派。

    ``if "Grounds Manager" in title`` 的 AST 形状是
    ``Compare(op=In, left=Constant(str), comparators=[Name/Attribute])``。
    这里按形状抓，而不是按文本抓 —— 文本抓法会被变量名、注释、拼接绕过去。
    """
    offenders: list[str] = []
    titleish = {"title", "raw_title", "job_title", "role_title", "position"}

    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _label(path, REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                continue

            sides = [node.left, *node.comparators]
            has_literal = any(
                isinstance(side, ast.Constant) and isinstance(side.value, str) for side in sides
            )
            has_titleish = any(
                isinstance(side, ast.Name) and side.id in titleish for side in sides
            ) or any(isinstance(side, ast.Attribute) and side.attr in titleish for side in sides)
            if has_literal and has_titleish:
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, "核心代码里出现了对 title 的直接字面量判断：\n  " + "\n  ".join(offenders)
