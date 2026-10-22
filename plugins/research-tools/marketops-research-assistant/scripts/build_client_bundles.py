#!/usr/bin/env python3
"""Generate a single WorkBuddy skill-only trial connector from canonical MarketOps Skills."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#193552"/><path d="M12 18h40v8H12zm0 12h32v8H12zm0 12h24v8H12z" fill="#75D3BA"/><circle cx="49" cy="46" r="7" fill="#F6BE65"/></svg>\n"""
META = {
    "name": "MarketOps Research Assistant",
    "name_zh": "MarketOps 半自动市场调研",
    "name_en": "MarketOps Research Assistant",
    "description": "Member-A-grounded business Skills, provider routing and candidate research experience packs.",
    "description_zh": "通用市场调研业务Skills、五Provider可选能力、证据库与可审计调用回执。",
    "description_en": "Research business workflow, five-provider routing and bounded evidence-first local MCP.",
    "source": "marketops-research-assistant-lite",
    "type": "skill-only",
    "version": "0.5.11-rc3",
    "minWorkbuddyVersion": "4.24.0",
    "examples_zh": [
        "调查指定地区的场地和运营组织，先列官方来源，将待核实项标为HOLD。",
        "根据已有公司名单，寻找相关岗位和有原始出处的公开工作邮箱。",
        "检查调研交付，并把卡点与成功方法归纳成待验证的经验包。"
    ],
    "examples_en": [
        "Research venues and operating organizations in the chosen market with source-based review.",
        "Research decision-maker roles from official public pages without guessing emails.",
        "Audit the research handoff and produce an evidence-linked candidate retrospective."
    ],
}


def make_zip(folder: Path, output_zip: Path) -> None:
    """Fixed timestamps/order, preserved modes, no symlink dereference.

    Three properties, each of which has been wrong here before:

    * **Determinism** — sorted walk and a fixed timestamp, so the same tree gives
      the same bytes and the published hash means something.
    * **Mode** — the ZIP records the source mode. Flattening every member to 0644
      strips the executable bit from `launch_research_mcp.py` (100755 upstream);
      `unzip` honours this field, so dropping it ships a launcher a host cannot
      exec even though the build tree had one. Group/other *write* bits are
      dropped first: they are the one thing a checkout's umask varies, and
      leaving them in would make the published hash depend on who built it.
    * **Containment** — a symlink is refused, never followed. Following one would
      read a file from outside the package and ship its bytes under a name that
      looks internal, which no later audit would flag.

    `os.walk(followlinks=False)` rather than `rglob`: on some versions `rglob`
    descends *into* a symlinked directory, and then no per-file `is_symlink()`
    check ever sees the link.
    """
    with zipfile.ZipFile(output_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs.sort()
            root_path = Path(root)
            for name in sorted(dirs):
                if (root_path / name).is_symlink():
                    raise ValueError(
                        f"SYMLINK_IN_PACKAGE: {(root_path / name).relative_to(folder).as_posix()} "
                        "— a package may not carry links out of itself")
            for name in sorted(files):
                filename = root_path / name
                if filename.is_symlink():
                    raise ValueError(
                        f"SYMLINK_IN_PACKAGE: {filename.relative_to(folder).as_posix()} "
                        "— a package may not carry links out of itself")
                if not filename.is_file():
                    continue
                arcname = f"{folder.name}/{filename.relative_to(folder).as_posix()}"
                info = zipfile.ZipInfo(arcname, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = stat.S_IMODE(filename.stat().st_mode) & ~0o022
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, filename.read_bytes())


def _verify_egress_slice(slice_root: Path) -> None:
    """Verify the managed-egress slice against its own PROVENANCE.json.

    Two failures are possible and they mean different things, so they get different
    codes. A listed file whose digest moved is EDITING: somebody changed the copy
    instead of the core. A python file the manifest never heard of is UNTRACKED:
    policy was added to the shipping slice without a canonical source, which is the
    "second copy" this whole arrangement exists to prevent. Both raise rather than
    warn — a warning here would be discovered by a customer, not by us.

    This check needs no core checkout and no network, so it runs on the machine that
    is most likely to hold a hand-edited copy.
    """
    manifest = json.loads((slice_root / "PROVENANCE.json").read_text(encoding="utf-8"))
    listed = set()
    for row in manifest["files"]:
        listed.add(row["slice"])
        target = slice_root / row["slice"]
        if not target.is_file():
            raise FileNotFoundError(f"SAFE_EGRESS_SLICE_INCOMPLETE: {row['slice']}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != row["sha256"]:
            raise ValueError(
                f"SAFE_EGRESS_SLICE_EDITED: {row['slice']} 的摘要与 PROVENANCE.json "
                f"不一致（{actual[:12]}… vs {row['sha256'][:12]}…）。"
                "切片里的代码不得手改 —— 改核心，再用 "
                "tools/sync_safe_egress_bundle.py 重新生成。"
            )
    mirrors = set(manifest["hand_written_mirrors"])
    for path in slice_root.rglob("*.py"):
        relative = path.relative_to(slice_root).as_posix()
        if relative not in listed and relative not in mirrors:
            raise ValueError(
                f"SAFE_EGRESS_SLICE_UNTRACKED: {relative} 在切片里但没有出处 —— "
                "一份没有规范来源的策略就是这件事要防的第二份实现。"
            )


def build(output: Path, *, with_search_mcp: bool = False,
          with_research_mcp: bool = False, browser_cache: Path | None = None,
          host_install_root: Path | None = None) -> dict:
    if with_search_mcp and with_research_mcp:
        raise ValueError("Select exactly one MCP mode")
    output = output.expanduser().resolve()
    if output.is_relative_to(HERE):
        raise ValueError("Build output must be outside this plugin source directory")
    if browser_cache is not None:
        browser_cache = browser_cache.expanduser().resolve()
        if browser_cache.is_relative_to(HERE):
            raise ValueError("BROWSER_CACHE_MUST_BE_OUTSIDE_PLUGIN_CHECKOUT")
    workbuddy = output / "workbuddy"
    target = workbuddy / "marketops-research-assistant"
    package = workbuddy / f"marketops-research-assistant-v{META['version']}.zip"
    if target.exists() or package.exists():
        raise FileExistsError(f"Will not overwrite an existing connector/package: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    metadata = dict(META)
    if with_search_mcp or with_research_mcp:
        metadata.update({
            "name": "MarketOps Research Assistant + Research MCP" if with_research_mcp else "MarketOps Research Assistant + Search MCP",
            "name_zh": "MarketOps 半自动调研 MCP" if with_research_mcp else "MarketOps 调研与双搜索 MCP",
            "source": "marketops-research-assistant" if with_research_mcp else "marketops-research-assistant-dual-search",
            "type": "mcp",
            "auth_mode": "token",
            "description": "One local Research MCP with five optional provider adapters, guided onboarding, typed Jev judgments and network off by default." if with_research_mcp else "One local Search MCP for SerpApi or Serper; paid network calls default off.",
            "description_zh": "统一调研 MCP + 6 项 Skills；五家 Provider 任选，联网默认关闭。" if with_research_mcp else "本地双搜索 MCP + 6 项 Skills，联网默认关闭。",
        })
    (target / "connector-meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "icon.svg").write_text(ICON, encoding="utf-8")
    for part in ("skills", "references"):
        shutil.copytree(HERE / part, target / part)
    (target / "scripts").mkdir()
    # Every module and launcher, minus an explicit never-ship set.
    #
    # This used to be a hand-maintained list, and it drifted: seventeen modules
    # the server imports — the whole adapter layer, research_wiring, research_grants
    # — were absent from the connector, so the shipped package could not even
    # import its own entry. A list that has to be updated whenever a module is
    # added will be forgotten; a list that has to be updated when a module must
    # NOT ship is read every time someone adds one.
    CONNECTOR_SCRIPT_EXCLUSIONS = frozenset({
        # QA tooling that drives the Claude CLI; not part of the managed product.
        "cc_host_probe.sh",
    })
    for source in sorted((HERE / "scripts").glob("*")):
        if source.suffix not in (".py", ".sh") or source.name in CONNECTOR_SCRIPT_EXCLUSIONS:
            continue
        shutil.copy2(source, target / "scripts" / source.name)
    # The managed-egress slice is a **package**, not a flat script, and it is copied
    # whole: the canonical modules sit at the same relative depth here as they do in
    # `marketops/`, so their relative imports resolve unchanged and the copies stay
    # byte-identical (see tools/sync_safe_egress_bundle.py). Before copying, verify
    # the slice against its own PROVENANCE.json — this catches a hand-edited copy on
    # a machine that has no core checkout, which is exactly the machine most likely
    # to have one.
    _verify_egress_slice(HERE / "scripts" / "marketops_safe_egress")
    shutil.copytree(HERE / "scripts" / "marketops_safe_egress",
                    target / "scripts" / "marketops_safe_egress",
                    # Never ship `__pycache__`: a `.pyc` is keyed to the interpreter
                    # and source mtime that produced it, so a bundle built on one
                    # machine would carry bytecode that another machine's Python
                    # ignores or, worse, one this machine's stale cache would shadow.
                    # It also makes the fixed-timestamp ZIP non-reproducible.
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(HERE / "README.md", target / "README.md")
    # This entry belongs to the generated connector, whose interpreter and
    # script paths are bound to this machine. The source installer's entry
    # instead tells another machine how to bootstrap a fresh installation.
    start_here = (
        "# MarketOps — WorkBuddy 本机连接器\n\n"
        "此目录由安装器在当前电脑生成。请保留安装目录和其中的 Python 环境，"
        "在 WorkBuddy 连接器管理中添加/导入这个目录（或同目录旁生成的 ZIP），启用后新建会话。\n\n"
        "MCP 配置绑定当前电脑的路径；换电脑请下载 portable 源码安装包，"
        "在目标电脑重新运行安装入口。此连接器不是跨电脑的一键安装源码包。\n\n"
        "账号、Key 与额度见[四账户启动指南](references/FOUR_ACCOUNT_START.md)。"
        "已在系统凭据库保存四个 Key 时，WorkBuddy 的密码字段可留空。"
        "首次数据设置和宿主步骤见[客户快速开始](references/CUSTOMER_QUICKSTART.md)，"
        "本机精确配置命令也在安装根目录的 START_HERE.md 中。\n\n"
        "新会话先让 MarketOps 检查数据路径、凭据和已授权额度，再建立 Research Brief，"
        "完成一小批公开研究、保存证据并导出 Excel。导入或工具可见不能代替真实任务验收。\n"
        if with_research_mcp or with_search_mcp else
        "# MarketOps — WorkBuddy Skills 包\n\n"
        "此包只包含 Skills，不包含可启动的研究 MCP 配置。完整安装请使用 portable 源码安装包，"
        "按[客户快速开始](references/CUSTOMER_QUICKSTART.md)在目标电脑运行安装器，"
        "再导入该电脑生成的 Research MCP 连接器。\n"
    )
    (target / "START_HERE.md").write_text(start_here, encoding="utf-8")
    for requirements_file in HERE.glob("requirements*.txt"):
        shutil.copy2(requirements_file, target / requirements_file.name)
    # Every bundle (including skill-only archives) contains account signup URLs,
    # secure optional official MCP connection templates, and the read-only guide.
    # The CLI is stdlib-only and never logs into or calls any provider.
    if not (target / "references" / "PROVIDER_CONNECTIONS.json").is_file():
        raise FileNotFoundError("PROVIDER_CONNECTION_CATALOG_MISSING")
    if not (target / "references" / "CUSTOMER_QUICKSTART.md").is_file():
        raise FileNotFoundError("CUSTOMER_QUICKSTART_MISSING")
    if with_search_mcp or with_research_mcp:
        # This is a *machine-bound* preview: the checked-out source and Python
        # interpreter must remain on the importing client's machine. Never copy
        # the generated ZIP to a different machine without rebuilding locally.
        variables = [
            "SERPAPI_API_KEY", "SERPER_API_KEY", "MARKETOPS_RESEARCH_DATA_ROOT",
            "MARKETOPS_RESEARCH_CONFIG",
            "MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_SEARCH_MAX_CALLS",
        ]
        env = {name: "${" + name + "}" for name in variables}
        if browser_cache is not None:
            # Bind the machine's private browser cache, never rely on HOME.
            # This value is an installation path, not a credential.
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
        server_name = "marketops-research" if with_research_mcp else "marketops-dual-search"
        # The generated connector loads the single managed entrypoint
        # `research_mcp.py`, the same module the installer's client configs, the
        # three host manifests and the source-tree configs point at. The legacy
        # dual-search connector predates it and is unchanged.
        program = "research_mcp.py" if with_research_mcp else "dual_search_mcp.py"
        if with_research_mcp:
            variables.extend(["CRAWL4AI_API_KEY", "TINYFISH_API_KEY",
                              "TINYFISH_MCP_ACCESS_TOKEN", "MARKETOPS_TINYFISH_ACCOUNT_ALIAS",
                              "TYPESAFE_API_KEY",
                              "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID",
                              "MARKETOPS_RESEARCH_ALLOW_NETWORK",
                              "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
                              "MARKETOPS_FETCH_ALLOWED_DOMAINS"])
            env.update({name: "$" + "{" + name + "}" for name in variables})
        search_config = {"mcpServers": {server_name: {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(target / "scripts" / program)],
            "cwd": str(target),
            "env": env,
            "timeout": 30000,
        }}}
        (target / "mcp.json").write_text(
            json.dumps(search_config, indent=2) + "\n", encoding="utf-8")
        # The unified Research MCP is configured in-product at first use, so the data
        # root is an advanced override here, not a required onboarding field. Never
        # inject a default directory: a blank value must stay unconfigured.
        data_root_field = (
            ("MARKETOPS_RESEARCH_DATA_ROOT",
             "Optional advanced override: absolute research data directory "
             "(leave empty to use the first-use configuration)",
             "text", False, "") if with_research_mcp else
            ("MARKETOPS_RESEARCH_DATA_ROOT", "Private absolute data directory", "text", True, ""))
        field_data = [
            ("SERPAPI_API_KEY", "SerpApi key: register at https://serpapi.com/dashboard (optional)", "password", False, ""),
            ("SERPER_API_KEY", "Serper key: register at https://serper.dev/ (optional)", "password", False, ""),
            data_root_field,
            ("MARKETOPS_RESEARCH_CONFIG", "Optional: path to a different first-use config JSON", "text", False, ""),
            ("MARKETOPS_SEARCH_ALLOW_NETWORK", "Enable billable search: enter 1, otherwise 0", "text", True, "0"),
            ("MARKETOPS_SEARCH_MAX_CALLS", "Cumulative reserved call limit", "text", True, "0"),
        ]
        if with_research_mcp:
            field_data.extend([
                ("CRAWL4AI_API_KEY", "Optional Crawl4AI Cloud Key; local mode needs no Key", "password", False, ""),
                ("TINYFISH_API_KEY", "TinyFish account key: embedded official MCP or REST; OAuth token is optional alternative", "password", False, ""),
                ("TINYFISH_MCP_ACCESS_TOKEN", "Optional TinyFish OAuth access token; set from your protected credential store, never paste here from chat", "password", False, ""),
                ("MARKETOPS_TINYFISH_ACCOUNT_ALIAS", "TinyFish OS-keyring OAuth account alias; empty means default", "text", False, "default"),
                ("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", "Explicit opt-in to capped PAID TinyFish Agent (1/0); stays 0 otherwise", "text", True, "0"),
                ("TYPESAFE_API_KEY", "TypeSafe Jev key: https://console.typesafe.ai/ (optional)", "password", False, ""),
                ("MARKETOPS_RESEARCH_ALLOW_NETWORK", "Enable non-search provider calls: 1/0", "text", True, "0"),
                ("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "Cumulative other provider attempts cap", "text", True, "0"),
                ("MARKETOPS_FETCH_ALLOWED_DOMAINS", "Comma separated approved public website domains", "text", False, ""),
            ])
        schema = {
            "title": "MarketOps Research MCP" if with_research_mcp else "MarketOps Dual Search (local stdio)",
            "description": "Built-in TinyFish and SerpApi official MCP clients; provider keys/OAuth and paid wallet authorization must be configured by the user.",
            "docUrl": "https://github.com/ProfessorZhi/marketops",
            "fields": [
                {"key": key, "label": label, "type": datatype, "required": required, "defaultValue": default}
                for key, label, datatype, required, default in field_data
            ],
        }
        (target / "token-schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    # The default skill-only bundle has no MCP; explicitly request search for tool mode.
    if host_install_root is not None:
        # The archive is what WorkBuddy imports. Rewrite its generated files
        # before zipping so both the folder and ZIP use the stable current path.
        import release_layout
        release_layout.rebase_host_files(host_install_root, target)
    make_zip(target, package)
    machine_bound = with_search_mcp or with_research_mcp
    if machine_bound:
        # The generated mcp.json names this build host's interpreter, because a
        # host config takes one `command` and there is no token that names "a
        # Python with the SDK" on every OS. Saying so in data — outside the
        # connector directory, so no host import ever sees an extra key — is the
        # difference between a labelled preview and a package that looks portable
        # until somebody copies it to another machine.
        (workbuddy / "MACHINE_BOUND_PREVIEW.json").write_text(
            json.dumps({
                "marker": "MACHINE_BOUND_PREVIEW",
                "why": "the generated mcp.json binds this build host's interpreter "
                       "path and this build host's absolute package path",
                "regenerate_on_target": "run setup_clients.py on the target machine; "
                                        "do not copy this connector between hosts",
                "portable": False,
                "built_by": "scripts/build_client_bundles.py",
            }, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "BUILT_STATIC_NOT_CLIENT_TESTED",
        "directory": str(target), "zip": str(package),
        "zip_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "skills": len(list((target / "skills").glob("*/SKILL.md"))),
        "mcp_servers_bundled": 1 if (with_search_mcp or with_research_mcp) else 0,
        "machine_bound_local_mcp": with_search_mcp or with_research_mcp,
        "provider_onboarding_script_bundled": True,
        "official_mcp_connection_templates_bundled": True,
        "all_vendor_sdks_physically_bundled": False,
        "runtime_setup_required_on_target_machine": True,
        "browser_cache_configured": browser_cache is not None,
        "machine_bound": machine_bound,
        "machine_bound_marker": str(workbuddy / "MACHINE_BOUND_PREVIEW.json")
                                 if machine_bound else None,
    }


def build_source(output: Path, qa_receipt: Path | None = None) -> dict:
    """Portable customer input, unlike an installed WorkBuddy connector.

    Contains no venv, browser binary, credentials or machine-specific host
    configuration. The customer runs the included platform installer locally.
    QA is a separately produced artifact: never regenerate or manufacture it
    here. Only a supplied receipt matching the copied bytes makes a release.
    """
    from setup_clients import source_files, source_digest
    output = output.expanduser().resolve()
    if output.is_relative_to(HERE):
        raise ValueError("SOURCE_ARCHIVE_OUTPUT_MUST_BE_OUTSIDE_PLUGIN")
    target = output / "marketops-research-assistant"
    archive = output / f"marketops-research-assistant-v{META['version']}-source.zip"
    if target.exists() or archive.exists():
        raise FileExistsError("SOURCE_ARCHIVE_DESTINATION_EXISTS")
    if qa_receipt is not None:
        supplied = qa_receipt.expanduser()
        if supplied.is_symlink():
            raise ValueError("BUILD_QA_RECEIPT_MUST_BE_EXTERNAL_FILE")
        qa_receipt = supplied.resolve()
        if qa_receipt.is_relative_to(HERE):
            raise ValueError("BUILD_QA_RECEIPT_MUST_BE_EXTERNAL_FILE")
        if not qa_receipt.is_file():
            raise FileNotFoundError("BUILD_QA_RECEIPT_MISSING")
    _verify_egress_slice(HERE / "scripts" / "marketops_safe_egress")
    target.mkdir(parents=True)
    for source in source_files():
        if source.relative_to(HERE).as_posix() == "references/BUILD_QA.json":
            # A stale generated receipt in a developer checkout must not turn
            # a no-receipt preview into a misleading accepted package.
            continue
        copied = target / source.relative_to(HERE)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
    qa_verified = False
    qa_sha256 = None
    qa_attestation = {"verified": False, "reason": "NO_QA_RECEIPT"}
    if qa_receipt is not None:
        receipt_copy = target / "references" / "BUILD_QA.json"
        shutil.copy2(qa_receipt, receipt_copy)
        from research_runtime_setup import verify_build_receipt, verify_independent_qa
        if not verify_build_receipt(target)["ok"]:
            raise ValueError("BUILD_QA_STALE_OR_INVALID")
        # A receipt that matches these bytes is still self-generated unless an
        # external signer attests it. Only that attestation may set qa_verified.
        qa_attestation = verify_independent_qa(target)
        qa_verified = bool(qa_attestation.get("verified"))
        qa_sha256 = hashlib.sha256(receipt_copy.read_bytes()).hexdigest()
    manifest = {"schema": "marketops.source-package.v1", "version": META["version"],
                "source_sha256": source_digest(), "portable": True,
                "qa_verified": qa_verified, "qa_receipt_sha256": qa_sha256,
                "qa_attestation": qa_attestation,
                "files": {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(target.rglob("*")) if p.is_file()}}
    (target / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    make_zip(target, archive)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + ".sha256")).write_text(f"{sha}  {archive.name}\n", encoding="ascii")
    return {"status": "SOURCE_PACKAGE_BUILT" if qa_verified else "SOURCE_PACKAGE_PREVIEW_BUILT",
            "portable": True, "directory": str(target),
            "qa_verified": qa_verified, "qa_receipt_sha256": qa_sha256,
            "qa_attestation": qa_attestation,
            "zip": str(archive), "zip_sha256": sha,
            "source_sha256": manifest["source_sha256"],
            "supported_runtime": "Python 3.12; Windows/Linux x64",
            "customer_entrypoints": ["Install.cmd", "install.sh"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="explicit user-owned destination outside source")
    parser.add_argument("--source-package", action="store_true", help="build the portable source installer ZIP")
    parser.add_argument("--qa-receipt", type=Path,
                        help="external real BUILD_QA receipt; must match the copied source before release ZIP creation")
    parser.add_argument("--with-search-mcp", action="store_true", help="legacy dual-search local MCP")
    parser.add_argument("--with-research-mcp", action="store_true", help="one local MCP for all 5 provider integrations (beta)")
    parser.add_argument("--browser-cache", type=Path,
                        help="optional machine-local Chromium cache path from explicit installer")
    parser.add_argument("--host-install-root", type=Path,
                        help="versioned install root whose host paths use current")
    args = parser.parse_args()
    if args.source_package and (args.with_search_mcp or args.with_research_mcp
                                or args.browser_cache or args.host_install_root):
        parser.error("--source-package cannot bind this machine's MCP or browser paths")
    if args.qa_receipt and not args.source_package:
        parser.error("--qa-receipt requires --source-package")
    try:
        result = build_source(args.output, qa_receipt=args.qa_receipt) if args.source_package else build(args.output, with_search_mcp=args.with_search_mcp,
                               with_research_mcp=args.with_research_mcp,
                               browser_cache=args.browser_cache,
                               host_install_root=args.host_install_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(status=2, message=f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
