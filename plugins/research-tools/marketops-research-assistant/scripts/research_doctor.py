#!/usr/bin/env python3
"""One pass over this computer: what would stop a run, and which fixes are safe.

The field failures this exists for are all the same shape — the plugin is fine
and the *machine* is not, but nothing says which part. Today's example: the MCP
server runs on whatever ``MARKETOPS_RESEARCH_PYTHON`` names, that interpreter had
no ``crawl4ai``, and the only symptom was a page read falling through to a paid
route and stopping at HOLD. ``marketops_crawl4ai_check`` answers "is the local
reader working"; it cannot answer "is it working *in the interpreter the server
actually uses*", because it is itself running inside that interpreter.

So the checks here are deliberately about the boundary:

* the interpreter the server will use, probed **from outside** it (``find_spec``,
  so nothing imported by the probe can become the module under test);
* the keys, by **name only** — a doctor that prints a key is a leak, not a check;
* the data root and config, which decide whether a run can write anything at all;
* the local reader's cached verdict, since "installed" is not "working".

Repairs stay inside what cannot lose a customer's data: create the directories a
configured root names, drop a stale crawl cache pointer, and — only when the
command line says ``--install`` — install the missing packages into the target
interpreter. Nothing here deletes, overwrites a config value the user chose, or
touches credentials.

Exit codes: ``0`` nothing blocked, ``20`` at least one BLOCKER remains.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Finding", "doctor", "repair", "main"]

PLUGIN = Path(__file__).resolve().parents[1]
INTERPRETER_ENV = "MARKETOPS_RESEARCH_PYTHON"

#: The packages the MCP server needs, in the order a missing one hurts most.
SERVER_MODULES = ("mcp", "crawl4ai", "playwright")

#: Key variable per provider. Names only, and only to answer "set or not".
KEY_VARIABLES = {
    "serper": "SERPER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "tinyfish": "TINYFISH_API_KEY",
    "jev": "TYPESAFE_API_KEY",
}

OK, WARN, BLOCKER = "OK", "WARN", "BLOCKER"


@dataclass
class Finding:
    """One line of the report: what was checked, what it said, what to do."""

    check: str
    state: str
    detail: str
    fix: str = ""
    repair: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "check": self.check,
            "state": self.state,
            "detail": self.detail,
            "fix": self.fix,
            "repair": self.repair,
        }


@dataclass
class Report:
    interpreter: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, *findings: Finding) -> None:
        self.findings.extend(findings)

    def payload(self) -> dict[str, object]:
        blockers = [f.check for f in self.findings if f.state == BLOCKER]
        warnings = [f.check for f in self.findings if f.state == WARN]
        return {
            "interpreter": self.interpreter,
            "state": "BLOCKED" if blockers else ("DEGRADED" if warnings else "OK"),
            "blockers": blockers,
            "warnings": warnings,
            "findings": [f.payload() for f in self.findings],
        }


def configured_interpreter(env: dict[str, str] | None = None) -> str:
    """The interpreter the MCP server will run on.

    ``MARKETOPS_RESEARCH_PYTHON`` when set (same rule as the launcher), otherwise
    this process's own interpreter: the doctor and the server are started by the
    same host, so "not configured" means "whatever started me".
    """
    named = ((env if env is not None else os.environ).get(INTERPRETER_ENV) or "").strip()
    return named or sys.executable


def module_available(python: str, module: str, *, timeout: float = 30.0) -> bool | None:
    """Is ``module`` importable by ``python``? ``None`` when the probe could not run.

    Probing from the outside is the whole point: importing ``crawl4ai`` inside this
    process would answer a different question (what *this* interpreter has) and
    would drag the SDK's global state into the doctor.
    """
    code = (
        "import importlib.util, sys;"
        f"sys.exit(0 if importlib.util.find_spec({module!r}) is not None else 1)"
    )
    try:
        done = subprocess.run(
            [python, "-I", "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


def interpreter_states(python: str) -> dict[str, bool | None]:
    """Probe the configured interpreter once; every finding below reads this."""
    return {module: module_available(python, module) for module in SERVER_MODULES}


def install_prefix(python: str) -> list[str]:
    """The argv that installs packages into ``python``; empty when nothing can.

    Not every interpreter can install its own packages. A ``uv``-created venv
    ships without ``pip``, so ``python -m pip install`` there is not a command
    that fails — it is a command that does not exist, and printing it sends the
    user in a circle. Measured on the machine this was written on: the
    configured interpreter had no ``pip`` at all.
    """
    if module_available(python, "pip") is not False:
        return [python, "-m", "pip", "install"]
    uv = shutil.which("uv")
    return [uv, "pip", "install", "--python", python] if uv else []


def install_advice(python: str, packages: list[str]) -> str:
    """The exact command to run, or why no command can be named here."""
    prefix = install_prefix(python)
    if prefix:
        return f"`{shlex.join([*prefix, *packages])}`"
    return (f"这个解释器里既没有 pip 也没有可用的 uv，装不了包 —— "
            f"先装 pip 或 uv，或把 {INTERPRETER_ENV} 换到带包管理的解释器")


def browser_advice(python: str) -> str:
    """Installing playwright is not enough; its browser is a second download."""
    return f"`{shlex.join([python, '-m', 'playwright', 'install', 'chromium'])}`"


def interpreter_findings(python: str,
                         states: dict[str, bool | None] | None = None) -> list[Finding]:
    """The interpreter must exist and carry ``mcp``; the reader packages are softer."""
    lines: list[Finding] = []
    if os.sep in python and not Path(python).exists():
        lines.append(Finding(
            "interpreter.exists", BLOCKER,
            f"{INTERPRETER_ENV} 指向 {python}，而那个文件不存在",
            f"把 {INTERPRETER_ENV} 指向一个真实存在的 Python（插件自带的 .venv 或系统解释器）",
        ))
        return lines

    if states is None:
        states = interpreter_states(python)
    if states["mcp"] is None:
        lines.append(Finding(
            "interpreter.probe", WARN, f"起不动 {python} 去查包（探针自己失败）",
            "确认这个解释器能执行：`\"<python>\" -c \"print(1)\"`",
        ))
        return lines

    # Whether a command can be named at all is part of the finding: an
    # interpreter with neither pip nor uv needs a different first step.
    can_install = bool(install_prefix(python)) if any(
        states.get(name) is False for name in SERVER_MODULES) else False

    if states["mcp"] is not True:
        lines.append(Finding(
            "interpreter.mcp", BLOCKER,
            f"{python} 里没有 mcp —— 这个解释器起不了 MarketOps 的研究服务",
            f"{install_advice(python, ['mcp'])}，或把 {INTERPRETER_ENV} 指向插件自带的 .venv",
            repair="install" if can_install else None,
        ))

    if states["crawl4ai"] is False:
        lines.append(Finding(
            "interpreter.crawl4ai", WARN,
            f"{python} 里没有 crawl4ai —— 免费的本地读页这一路用不了（其余路照跑，读页会降级）",
            f"{install_advice(python, ['crawl4ai'])}，装完还要 {browser_advice(python)}",
            repair="install" if can_install else None,
        ))
    if states["playwright"] is False:
        lines.append(Finding(
            "interpreter.playwright", WARN,
            f"{python} 里没有 playwright —— crawl4ai 的浏览器这一层装不上",
            f"{install_advice(python, ['playwright'])}，装完还要 {browser_advice(python)}",
            repair="install" if can_install else None,
        ))
    if states["crawl4ai"] is True and states["playwright"] is True:
        lines.append(Finding("interpreter.reader", OK,
                             f"{python} 同时有 crawl4ai 与 playwright（本地读页可用）"))
    return lines


def key_findings(env: dict[str, str] | None = None) -> list[Finding]:
    """Which provider keys are set — by name only, never by value."""
    source = env if env is not None else os.environ
    present = [name for name, var in KEY_VARIABLES.items()
               if (source.get(var) or "").strip()]
    absent = [name for name in KEY_VARIABLES if name not in present]
    lines = [Finding("keys.present", OK if present else WARN,
                     f"已设置：{', '.join(present) if present else '一个都没有'}",
                     "" if present else "没有搜索凭据，本机跑不了发现步骤")]
    if absent:
        lines.append(Finding(
            "keys.absent", OK if present else WARN,
            f"未设置：{', '.join(absent)}"
            "（DeepSeek/SerpApi/TinyFish/Jev 都是可选的；Serper 是默认发现路由）",
            f"按需安装后在启动宿主前设置 {', '.join(KEY_VARIABLES[n] for n in absent)}",
        ))
    return lines


def config_findings(env: dict[str, str] | None = None) -> list[Finding]:
    """A config file must exist before a run has anywhere to write."""
    import research_config

    view = research_config.status(env)
    path = view["config_file"]
    lines: list[Finding] = []
    if not view.get("configured"):
        lines.append(Finding(
            "config.data_root", BLOCKER,
            f"{path} 里没有可用的 data_root —— 一次运行没有地方写数据",
            "调用 marketops-setup（或 marketops_setup_configure）选定数据根与经验文件；"
            "本工具不替你编一个默认目录",
        ))
        return lines

    root = Path(view["data_root"])
    if root.exists() and os.access(root, os.W_OK):
        lines.append(Finding("config.data_root", OK, f"数据根可写：{root}"))
    elif root.exists():
        lines.append(Finding(
            "config.data_root", BLOCKER, f"数据根存在但不可写：{root}",
            f"修权限：`chmod u+w {root}`，或换一个可写目录重跑 setup",
        ))
    else:
        lines.append(Finding(
            "config.data_root", BLOCKER, f"数据根不存在：{root}",
            "创建它（doctor --repair 会建父目录），或换一个目录重跑 setup",
            repair="create_data_root",
        ))
    return lines


def reader_findings(data_root: str | None,
                    reader_available: bool | None = None) -> list[Finding]:
    """The local reader's cached verdict — cheap, and about *this* computer.

    ``reader_available`` is whether the **configured** interpreter can import
    crawl4ai at all. The cached verdict is written by whichever interpreter last
    ran a check, and the cache lives in the data root, so a "WORKING" line can
    describe a venv the server will never use. That is not a detail: it is the
    exact shape of the failure this file was written for — a machine that looks
    ready because another interpreter was ready.
    """
    if data_root is None:
        return []
    import research_crawl_doctor

    cached = research_crawl_doctor.cached(Path(data_root))
    if cached is None:
        return [Finding("crawl4ai.state", WARN,
                        "本机还没有做过 crawl4ai 检查（第一次读页时会自动做）",
                        "marketops_crawl4ai_check() 或 `research_crawl_doctor.py check`")]
    state = str(cached.get("status"))
    if reader_available is False:
        return [Finding(
            "crawl4ai.state", WARN,
            f"缓存里 crawl4ai 报 {state}，但那是**另一个**解释器写下的结论；"
            "服务实际用的这个解释器里没有 crawl4ai",
            "先修 interpreter.crawl4ai（换解释器或装包），再重跑一次 crawl4ai 检查",
            repair="crawl4ai",
        )]
    if state == "WORKING":
        verified = cached.get("page_read_verified") is True
        return [Finding("crawl4ai.state", OK if verified else WARN,
                        f"crawl4ai 报 {state}"
                        + ("（真读过一页）" if verified else "（浏览器起得来，还没读过真实页面）"),
                        "" if verified else "marketops_crawl4ai_check(run_id=<当前 run>)")]
    return [Finding("crawl4ai.state", WARN, f"crawl4ai 报 {state}",
                    "marketops_crawl4ai_check(repair=True)，或本目录的 Repair.cmd / repair.sh",
                    repair="crawl4ai")]


def doctor(env: dict[str, str] | None = None) -> Report:
    """Run every check. Pure reading: nothing here writes to disk."""
    python = configured_interpreter(env)
    report = Report(interpreter=python)
    states = interpreter_states(python)
    report.add(*interpreter_findings(python, states))
    report.add(*key_findings(env))
    try:
        config_lines = config_findings(env)
    except Exception as exc:  # noqa: BLE001 - a broken config must be a finding, not a crash
        config_lines = [Finding("config.read", BLOCKER, f"读不了配置：{exc}",
                                "检查 MARKETOPS_RESEARCH_CONFIG 指向的文件")]
    report.add(*config_lines)
    import research_config

    try:
        view = research_config.status(env)
        data_root = view.get("data_root") if view.get("configured") else None
    except Exception:  # noqa: BLE001 - already reported above
        data_root = None
    report.add(*reader_findings(data_root, reader_available=states.get("crawl4ai")))
    return report


def repair(report: Report, *, install: bool = False) -> list[str]:
    """Apply the safe repairs the findings asked for; return what was done.

    A repair that fails is reported as text and does not stop the others: one
    unrepairable line must not hide the four that could be fixed.
    """
    import research_config

    done: list[str] = []
    for finding in report.findings:
        if finding.repair is None:
            continue
        try:
            if finding.repair == "create_data_root":
                root = Path(research_config.data_root())
                root.mkdir(parents=True, exist_ok=True)
                done.append(f"已创建数据根：{root}")
            elif finding.repair == "crawl4ai":
                import research_crawl_doctor

                result = research_crawl_doctor.repair(Path(research_config.data_root()))
                done.append(f"crawl4ai 修复：{result.get('status', result)}")
            elif finding.repair == "install":
                missing = [m for m in SERVER_MODULES
                           if module_available(report.interpreter, m) is False]
                if not install:
                    done.append(
                        f"未安装（需要 --install 明确同意）：{', '.join(missing)}"
                        f" —— {install_advice(report.interpreter, missing)}"
                    )
                    continue
                done.extend(_pip_install(report.interpreter, missing))
        except Exception as exc:  # noqa: BLE001 - each repair stands alone
            done.append(f"{finding.repair} 修复失败：{exc}")
    return done


def _pip_install(python: str, modules: list[str]) -> list[str]:
    """Install into the **target** interpreter, never into the one running the doctor."""
    prefix = install_prefix(python)
    if not prefix:
        return [f"没装 {', '.join(modules)}：{python} 里既没有 pip 也没有 uv，这里装不了 —— "
                f"先把 {INTERPRETER_ENV} 换到一个能装包的解释器"]
    done: list[str] = []
    for module in modules:
        try:
            run = subprocess.run([*prefix, module],
                                 capture_output=True, text=True, timeout=1800, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            done.append(f"{module} 安装失败：{exc}")
            continue
        done.append(f"{module} 安装{'成功' if run.returncode == 0 else '失败'}"
                    f"（exit {run.returncode}）")
    if "playwright" in modules or "crawl4ai" in modules:
        done.append(f"包已装，浏览器还要单独跑：{browser_advice(python)}")
    return done


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true",
                        help="apply the safe repairs (creates directories, fixes the crawl cache)")
    parser.add_argument("--install", action="store_true",
                        help="with --repair, pip-install missing packages into the target interpreter")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = doctor()
    acted: list[str] = []
    if args.repair:
        acted = repair(report, install=args.install)
        report = doctor()
    body = report.payload()
    if acted:
        body["repaired"] = acted
    if args.json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        for finding in report.findings:
            marker = {OK: "ok  ", WARN: "warn", BLOCKER: "STOP"}[finding.state]
            print(f"[{marker}] {finding.check}: {finding.detail}")
            if finding.fix and finding.state != OK:
                print(f"         → {finding.fix}")
        for line in acted:
            print(f"[done] {line}")
    return 20 if body["blockers"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
