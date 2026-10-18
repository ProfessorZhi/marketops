"""「不绕过、不反检测」的硬约束。

需求 §8 划定了边界：匿名公开浏览**允许** JS、cookie、localStorage、普通 session
state、cache；**禁止**登录目标站点、访问 private content、CAPTCHA 绕过、突破
access control。明确受阻时应当 ``blocked`` 并改走其他公开来源。

``docs/ARCHITECTURE.md`` 里写着「本代码库不提供任何登录 / 验证码求解 / 反检测接口
—— 不是『没实现』，是**不提供**」。本文件让那句话变成每次 pytest 都执行的事实。

## 为什么需要这条测试

crawl4ai 的依赖树里**确实**带着 ``patchright``（Playwright 的 stealth 分支）与
``playwright-stealth`` —— 它们会被装进 venv，我们无法通过不装来排除。于是
「我们没用它」必须由代码来保证，而不是靠自觉：

* 装了却没用的库，会在某次「先让它跑通」的修改里被顺手用上；
* 一旦用了，这个项目就从「采集公开信息」变成了「规避访问控制」。

## 检测什么

分两类，因为它们的失效方式不同：

1. **能力引入** —— import 了求解器 / stealth / 反检测库。
2. **行为引入** —— 代码里出现求解、轮换、伪装这类动作的调用。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketops.adapters import classify
from marketops.config import REPO_ROOT

SOURCE_ROOTS: tuple[str, ...] = ("marketops", "services", "tests")

#: 本文件自身豁免 —— 它必须写出这些名字才能检测它们。
SELF_EXEMPT: frozenset[str] = frozenset({"tests/test_no_evasion.py"})

#: 禁止 import 的包（按顶层模块名匹配）。
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "patchright",
        "playwright_stealth",
        "undetected_chromedriver",
        "selenium_stealth",
        "pyppeteer_stealth",
        "cloudscraper",
        "capsolver",
        "twocaptcha",
        "anticaptcha",
        "python_anticaptcha",
        "hcaptcha",
        "recaptcha_solver",
        "requests_random_user_agent",
        "fake_useragent",
    }
)

#: 禁止出现的行为标识（子串匹配，全部小写）。
#:
#: 刻意**不含**裸词 ``captcha`` —— 分类器需要认出验证码页面才能把它判成
#: ``BLOCKED``。禁止「识别」会逼着实现者删掉识别逻辑，那是反效果的：
#: 认不出来就只会变成「抓不到内容」，而不是「明确受阻」。禁的是「求解」。
#:
#: 同理不用裸词 ``bypass`` —— ``cache_mode="BYPASS"``（绕过缓存）与
#: 「绕过访问控制」是两回事。只禁明确指向访问控制的组合词。
FORBIDDEN_BEHAVIOURS: tuple[tuple[str, str], ...] = (
    ("solve_captcha", "验证码求解"),
    ("captcha_solver", "验证码求解器"),
    ("bypass_waf", "绕过 WAF"),
    ("waf_bypass", "绕过 WAF"),
    ("bypass_access", "绕过访问控制"),
    ("access_control_bypass", "绕过访问控制"),
    ("bypass_cloudflare", "绕过 Cloudflare 挑战"),
    ("bypass_challenge", "绕过挑战页"),
    ("stealth", "反检测伪装"),
    ("undetected", "反检测伪装"),
    ("rotate_proxy", "代理轮换（用于绕 WAF）"),
    ("proxy_rotation", "代理轮换（用于绕 WAF）"),
    ("user_agent_rotation", "UA 轮换（用于伪装）"),
    ("random_user_agent", "随机 UA（用于伪装）"),
    ("persistent_context", "持久化浏览器上下文（会带上登录态）"),
    ("user_data_dir", "复用浏览器用户目录（会带上登录态）"),
)


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SOURCE_ROOTS:
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


def _imported_modules(tree: ast.AST) -> set[str]:
    """收集所有 import 的顶层模块名（含 ``from x import y``）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0].lower())
    return names


def test_source_files_are_discovered() -> None:
    """防止扫描静默失效。"""
    files = _iter_source_files()
    assert len(files) >= 15, f"只发现 {len(files)} 个源文件，扫描范围可能已被改坏"
    relative = {path.relative_to(REPO_ROOT).as_posix() for path in files}
    assert "marketops/adapters/crawl4ai_fetcher.py" in relative
    assert not any(path.startswith("tests/test_no_evasion") for path in relative)


def test_no_evasion_libraries_are_imported() -> None:
    """不得 import 求解器 / stealth / 反检测库 —— 装了也不行。"""
    offenders: list[str] = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for module in sorted(_imported_modules(tree) & FORBIDDEN_MODULES):
            offenders.append(f"{relative} import {module}")

    assert not offenders, (
        "引入了规避 / 反检测能力：\n  "
        + "\n  ".join(offenders)
        + "\n\n明确受阻时的正确处置是记 blocked 并改走其他公开来源，不是绕过去。"
    )


def test_no_evasion_behaviour_in_source() -> None:
    """不得出现求解 / 轮换 / 持久化登录态这类行为。"""
    offenders: list[str] = []
    for path in _iter_source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8").lower()
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # 注释里提到某个词是在解释「为什么不做」，不是在做。
            if stripped.startswith("#"):
                continue
            for needle, reason in FORBIDDEN_BEHAVIOURS:
                if needle in line:
                    offenders.append(f"{relative}:{lineno} [{reason}] {stripped}")

    assert not offenders, (
        "源码里出现了规避行为：\n  "
        + "\n  ".join(offenders)
        + "\n\n如确有必要，先说明它为什么不是「绕过访问控制」，再改本测试。"
    )


# --- 反向约束：分类能力不能被删掉 ---------------------------------------------


def test_challenge_detection_is_still_present() -> None:
    """识别验证码 / WAF 的能力必须保留 —— 它是 ``BLOCKED`` 判定的依据。

    如果哪天有人为了让上面的测试通过而删掉分类器里的验证码标记，
    受阻页面就会被误当成「这个站点没有邮箱」，指标随之失真。
    """
    markers = " ".join(
        str(item).lower()
        for group in (
            classify.WAF_MARKERS,
            classify.LOGIN_MARKERS,
            classify.PAYWALL_MARKERS,
        )
        for item in group
    )
    assert "captcha" in markers, "WAF/登录标记里必须仍能认出 captcha"
    assert markers.strip(), "分类标记不得为空"


@pytest.mark.parametrize("module", ["patchright", "playwright_stealth"])
def test_stealth_packages_are_recognised_as_forbidden(module: str) -> None:
    """守卫本身有效性的自检：这两个包（crawl4ai 的传递依赖）必须被规则拦住。

    它们**可能没被安装**，本测试不 import 它们，只验证规则表认得它们。
    """
    assert module in FORBIDDEN_MODULES, f"{module} 是 crawl4ai 的传递依赖，必须被列为禁用"
