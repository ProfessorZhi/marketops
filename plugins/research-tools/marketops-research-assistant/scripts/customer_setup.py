#!/usr/bin/env python3
"""Interactive customer setup after installation; secrets stay on this machine."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def usage_snapshot(root):
    """Read existing project totals; never create a new budget or reset usage."""
    import research_usage_ledger as usage
    if not usage.default_path(root).exists():
        return {"calls_used": 0, "unknown_operations": 0, "reserved_operations": 0,
                "actual_spend_micros": None, "money_status": "UNKNOWN_NO_LEDGER"}
    ledger = usage.for_root(root)
    project = usage.project_for_root(root)
    totals = ledger.usage("project", project, window="total")
    report = ledger.report(project)
    calls = totals[0].calls_used if totals else sum(
        1 for op in ledger.operations(project=project)
        if op.counts_against_caps and op.charge_state != "NO_CHARGE_PROVEN")
    money = report["money"]
    return {"calls_used": calls,
            "unknown_operations": report["by_charge_state"].get("UNKNOWN", 0),
            "reserved_operations": report["by_charge_state"].get("RESERVED", 0),
            "actual_spend_micros": money["actual_micros"] if money["basis"] == "VERIFIED_ACTUAL" else None,
            "money_status": money["basis_code"] if money["basis"] == "VERIFIED_ACTUAL" else "UNKNOWN_" + money["basis_code"]}


def configure_research(root, *, providers, domains, max_calls, task):
    import research_runtime_setup as runtime
    previous = runtime.config(root)
    if previous:
        snapshot = usage_snapshot(root)
        print(f"沿用研究数据目录：{root}")
        print(f"原累计请求上限：{previous.get('max_calls', 'UNKNOWN')}；新累计总上限：{max_calls}")
        print(f"累计已用/占用请求：{snapshot['calls_used']}；预约未结算：{snapshot['reserved_operations']}；UNKNOWN 操作：{snapshot['unknown_operations']}")
        actual = snapshot["actual_spend_micros"]
        print(f"实际货币费用（micro-USD）：{actual if actual is not None else 'UNKNOWN'}；依据：{snapshot['money_status']}。")
        print("续授权保留所有历史 Run、材料、经验和已消耗额度；新数字是总上限，不是额外充值。")
        if input("确认上述范围和累计总上限，输入 AMEND；其他输入取消: ").strip() != "AMEND":
            print("未更改研究授权。")
            return False
    runtime.configure(root, providers=providers, domains=domains, max_calls=max_calls,
                      task=task, confirm=True, amend=bool(previous))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-keys", action="store_true", help="keep existing environment/keyring credentials")
    args = parser.parse_args(argv)
    import research_config
    from provider_onboarding import format_text, report
    from research_credentials import configure_keys, load_into_environment
    try:
        print("MarketOps 本地首次设置：四个服务账户；Crawl4AI 本地不需要账户。")
        print(format_text(report(client="codex")))
        stored = research_config.load()
        if stored:
            print("保留已有研究数据与经验文件配置。")
        else:
            root = input("研究数据保存目录（绝对路径）: ").strip()
            experience = input("经验汇总文件（研究目录之外的绝对路径，文件名 Research_Experience.md）: ").strip()
            root_path, exp_path = Path(root), Path(experience)
            if not root_path.is_absolute() or not exp_path.is_absolute():
                raise ValueError("PATH_NOT_ABSOLUTE")
            install_root = HERE.parents[2]
            if (install_root / "INSTALL_STATE.json").is_file():
                if any(p.resolve().is_relative_to(install_root.resolve()) for p in (root_path, exp_path)):
                    raise ValueError("DATA_MUST_BE_OUTSIDE_INSTALL")
            research_config.configure(root, experience)
        if not args.skip_keys:
            print(json.dumps(configure_keys(), ensure_ascii=False))
        load_into_environment()
        root = research_config.data_root()
        if input("现在配置一次小范围真实调研的联网额度？[y/N]: ").strip().lower() == "y":
            task = input("此次调研的目标和范围: ").strip()
            providers = input("本次服务（逗号分隔：serper,serpapi,tinyfish,typesafe_jev,crawl4ai）: ").strip().split(",")
            providers = [x.strip() for x in providers if x.strip()]
            if not task or not providers or any(x not in {"serper", "serpapi", "tinyfish", "typesafe_jev", "crawl4ai"} for x in providers):
                raise ValueError("INVALID_RESEARCH_SCOPE")
            domains = input("允许抓取的公开网站域名（逗号分隔）: ").strip()
            value = input("本项目允许的请求次数上限（1-500）: ").strip()
            if not value.isdigit() or not 1 <= int(value) <= 500 or not domains:
                raise ValueError("INVALID_FINITE_SETUP_LIMIT")
            helper = HERE / "research_runtime_setup.py"
            if not helper.is_file():
                raise ValueError("RUNTIME_SETUP_HELPER_MISSING")
            if not configure_research(root, providers=providers, domains=domains.split(","),
                                      max_calls=int(value), task=task):
                return 0
            for provider in providers:
                if provider in {"tinyfish", "serpapi"}:
                    probe = subprocess.run([sys.executable, str(helper), "bootstrap", "--data-root", str(root),
                                            "--provider", provider])
                    if probe.returncode:
                        print(f"{provider} 连接检查未通过；保留配置，其他服务可分别检查。")
            subprocess.run([sys.executable, str(helper), "status", "--data-root", str(root)], check=True)
        print("已保存本地配置。打开安装目录 START_HERE.md，完成宿主导入并在新会话检查 MarketOps 状态。")
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
        # Backend and provider exceptions can contain credentials or requests.
        code = str(exc).split(":", 1)[0] if isinstance(exc, ValueError) else "LOCAL_SETUP_STEP_FAILED"
        if not re.fullmatch(r"[A-Z_]+", code):
            code = "LOCAL_SETUP_STEP_FAILED"
        print(json.dumps({"status": "SETUP_INCOMPLETE", "reason_type": type(exc).__name__,
                          "code": code,
                          "next": "rerun customer_setup.py; saved data and keys are retained"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
