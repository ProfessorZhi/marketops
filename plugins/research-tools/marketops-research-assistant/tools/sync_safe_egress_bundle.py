#!/usr/bin/env python3
"""把核心的出口判据**逐字节**复制成插件包里可以独立 import 的那一份。

## 为什么要复制，而不是「让插件 import 核心」

独立发行的插件包在一个**只装了标准库**的解释器里跑（``launch_research_mcp.sh``
把一个 ``import mcp`` 通得过的解释器挑出来，仅此而已）。它没有 ``marketops``
这个包，而 ``pip install marketops`` 会把整棵核心树带进来 —— 那是几百个模块、
十几个第三方依赖，只为了四步判据与一个本机代理。**发行面不该按实现面裁剪。**

而另一条路（把判据在插件里重写一遍）更糟：两份实现会在某一天分叉，而分叉的那一份
看起来完全正常 —— 它只是少了一次拒绝。

所以这里走的是第三条：**同一份源码，逐字节复制**。

## 它为什么能逐字节复制

``marketops/{ports/dns.py, security/network.py, security/scope.py,
adapters/{resolution,egress_proxy,guarded_http}.py}`` 这六个文件只用标准库，
且它们之间的相对 import（``..ports.dns`` / ``..security.network`` / ``.resolution``）
在一个**内部结构相同**的包里解析结果完全一样。于是复制不需要改写任何一行：
目标目录镜像源目录的层级，相对 import 自己就对了。

## 这个工具判什么、不判什么

* 它写：那六个文件的副本，以及 ``PROVENANCE.json``（每个文件的 sha256）。
* 它**不**写：``__init__.py``。四层 ``__init__.py`` 是**手写**的 —— 核心那两个
  装着整个包的导言（讲的是 ``marketops`` 这棵树的取向），照抄进客户机的包里是
  一篇指错地方的文档。判据因此落在**实现文件**上：**策略住在哪几个文件里，
  哪几个文件就被钉死**。
* ``--check`` 只读，一字节不一致就退 1 并把差异打出来。CI 与
  ``tests/test_wave1_egress_bundle.py`` 都用这一条。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parents[2]
SLICE = HERE / "scripts" / "marketops_safe_egress"

#: ``(切片内相对路径, 仓库内相对路径)``。**顺序即报告顺序**，改这里等于改判据的
#: 范围 —— 加一个文件意味着「它也是策略的一部分」，减一个意味着「它不再是」。
FILES: tuple[tuple[str, str], ...] = (
    ("ports/dns.py", "marketops/ports/dns.py"),
    ("security/network.py", "marketops/security/network.py"),
    ("security/scope.py", "marketops/security/scope.py"),
    ("adapters/resolution.py", "marketops/adapters/resolution.py"),
    ("adapters/egress_proxy.py", "marketops/adapters/egress_proxy.py"),
    ("adapters/guarded_http.py", "marketops/adapters/guarded_http.py"),
)

#: 手写的 ``__init__.py`` 是切片的一部分，但**不是**副本。它们在这里被列出来，
#: 只为了让「切片里还有什么」这件事有一个可核对的清单 —— 一个没被列出的
#: ``.py`` 文件意味着有人在切片里塞了一份**没有出处**的代码。
MIRRORS: tuple[str, ...] = (
    "__init__.py",
    "ports/__init__.py",
    "security/__init__.py",
    "adapters/__init__.py",
)

SCHEMA = "marketops.safe-egress-slice.v1"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for relative_slice, relative_canonical in FILES:
        source = REPO_ROOT / relative_canonical
        if not source.is_file():
            raise SystemExit(f"CANONICAL_MISSING: {relative_canonical}（{source}）")
        rows.append(
            {
                "slice": relative_slice,
                "canonical": relative_canonical,
                "sha256": _digest(source),
            }
        )
    return rows


def _provenance(rows: list[dict[str, str]]) -> bytes:
    document = {
        "schema": SCHEMA,
        "rule": "verbatim",
        "generated_by": "plugins/research-tools/marketops-research-assistant/tools/"
        "sync_safe_egress_bundle.py",
        "hand_written_mirrors": list(MIRRORS),
        "files": rows,
        "notice": (
            "These files are byte-identical copies of the canonical modules named in "
            "'canonical'; they are not edited here. Regenerate with this tool after any "
            "change to a canonical module. The __init__.py files are hand-written and "
            "deliberately not copies."
        ),
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check() -> int:
    """只读。一字节不一致、缺文件或多文件都退 1。"""
    rows = _plan()
    problems: list[str] = []
    for row in rows:
        target = SLICE / row["slice"]
        if not target.is_file():
            problems.append(f"MISSING: {row['slice']}")
            continue
        actual = _digest(target)
        if actual != row["sha256"]:
            problems.append(
                f"DRIFT: {row['slice']} 与 {row['canonical']} 不一致"
                f"（切片 {actual[:12]}… vs 规范 {row['sha256'][:12]}…）"
            )
    manifest = SLICE / "PROVENANCE.json"
    expected = _provenance(rows)
    if not manifest.is_file():
        problems.append("MISSING: PROVENANCE.json")
    elif manifest.read_bytes() != expected:
        problems.append("DRIFT: PROVENANCE.json 与当前的规范摘要不一致")
    for extra in sorted(_slice_python_files()):
        if extra not in [row["slice"] for row in rows] and extra not in MIRRORS:
            problems.append(f"UNTRACKED: {extra} 在切片里但没有出处")
    if problems:
        print(f"SAFE_EGRESS_SLICE_DRIFT ({len(problems)})")
        for problem in problems:
            print(f"  - {problem}")
        print("修复：python plugins/research-tools/marketops-research-assistant/tools/"
              "sync_safe_egress_bundle.py")
        return 1
    print(f"SAFE_EGRESS_SLICE_OK files={len(rows)} mirrors={len(MIRRORS)}")
    return 0


def _slice_python_files() -> list[str]:
    if not SLICE.is_dir():
        return []
    return [
        path.relative_to(SLICE).as_posix()
        for path in SLICE.rglob("*.py")
        if path.is_file()
    ]


def sync() -> int:
    """写入副本与 ``PROVENANCE.json``。**只写它自己的那几个目标。**"""
    rows = _plan()
    for row in rows:
        target = SLICE / row["slice"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / row["canonical"]).read_bytes())
        print(f"wrote {row['slice']} <- {row['canonical']} ({row['sha256'][:12]}…)")
    SLICE.mkdir(parents=True, exist_ok=True)
    (SLICE / "PROVENANCE.json").write_bytes(_provenance(rows))
    print(f"wrote PROVENANCE.json ({len(rows)} files)")
    missing = [name for name in MIRRORS if not (SLICE / name).is_file()]
    if missing:
        print("注意：以下是手写的，不由本工具生成，当前缺失：" + ", ".join(missing))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="只读校验；有 drift 时退 1")
    args = parser.parse_args(argv)
    try:
        return check() if args.check else sync()
    except SystemExit as exc:  # _plan() 的 CANONICAL_MISSING
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
