"""MarketOps CLI —— 本阶段唯一可运行的入口，也是**组合根**。

    python -m marketops.interfaces.cli check-config   # 配置与 profile 自查，不联网
    python -m marketops.interfaces.cli check-deps     # 可选依赖可用性
    python -m marketops.interfaces.cli export-contracts
    python -m marketops.interfaces.cli mint-token \
        --principal P --role R --audience http://127.0.0.1:8787/mcp \
        --expires-in 30d --out <仓库外的路径>
    python -m marketops.interfaces.cli run-demo --accounts <path> [--dry-run]
    python -m marketops.interfaces.cli trial preflight [--json]

设计约束：
* **不打印任何 secret**。所有输出走 ``Settings.as_public_dict()`` 或显式投影。
  唯一的例外是 ``mint-token`` 打印出**刚铸出来**的那个令牌 —— 那是它存在的理由，
  而盘上只写摘要（见 ``marketops/security/tokens.py``）。它把令牌单独放在 stdout，
  说明文字走 stderr，所以 ``TOKEN=$(...)`` 拿到的是干净的一行。
* 缺配置时给出可操作的下一步，而不是堆栈。
* ``--dry-run`` 不发起任何网络请求。

``export-contracts`` 是 ``marketops.schema`` 的转调，不是第二个实现：契约导出的
规范命令是 ``python -m marketops.schema``（``tests/test_contracts_sync.py`` 的失败
信息里印的就是它）。这条子命令存在的唯一理由是"人已经在本仓库 CLI 里了"。

它从 ``services/worker/cli.py`` 搬来（见 ``marketops/interfaces/__init__.py``）。
因此本文件里出现 ``SerperSearchProvider`` / ``HttpxFetcher`` 这类具体类型是**对的**
—— 组合根就是那个回答「谁来给」的地方，其余每一层只认端口。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from marketops import RESEARCH_ONLY, SEND_AUTHORIZED, __version__
from marketops.config import REPO_ROOT, load_settings
from marketops.domain.errors import ConfigError, MarketOpsError
from marketops.application.cohorts import load_accounts, validate_plan
from marketops.adapters.profile_loader import load_profile


def _cmd_check_config(args: argparse.Namespace) -> int:
    settings = load_settings()
    print("== MarketOps 配置 ==")
    print(f"version          : {__version__}")
    print(f"research_only    : {RESEARCH_ONLY}")
    print(f"send_authorized  : {SEND_AUTHORIZED}")
    print(json.dumps(settings.as_public_dict(), ensure_ascii=False, indent=2))

    bundle = load_profile(settings)
    print("\n== Profile ==")
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2))
    if bundle.has_unknown_rules:
        print(
            "\n注意：本 profile 含 UNKNOWN 规则，grading engine 会拒绝判定并返回 X。"
            "这是设计行为，不是故障 —— 见 docs/SOP_MAPPING.md。"
        )
    return 0


#: 依赖探针：**要 ``__import__`` 的顶层名**，配上人读的标签。
#:
#: 它是**数据**而不是写在 ``rows`` 里的一串字面量，因为这张表曾经替一个不存在的东西
#: 作过证：``("beautifulsoup4", *_probe("bs4"))`` 在这一行上站了很久，而当时
#: ``pyproject`` 也声明着它、正文抽取却从没用过 —— 两个证人互相印证，没有一处会响。
#: 拆成常量之后，``tests/test_declared_dependencies.py`` 能**执行**一条判据：这里探的
#: 每一个名字都必须真的是一条被声明的依赖。缺了它，这一类假证人随时可以再生 ——
#: 另外两条判据都看不见它（一条只管**已声明**的有没有人用，另一条只扫 import 语句）。
DEPENDENCY_PROBES: tuple[tuple[str, str], ...] = (
    ("httpx", "httpx (HTTP fetch)"),
    ("mcp", "mcp (服务面 SDK)"),
    ("pypdf", "pypdf (text PDF)"),
    ("pydantic", "pydantic"),
    ("yaml", "PyYAML"),
)


def _cmd_check_deps(args: argparse.Namespace) -> int:
    """报告可选依赖。缺重依赖不算失败（MinerU 按需启用）。"""
    from marketops.adapters.crawl4ai_fetcher import (
        crawl4ai_available,
        crawl4ai_unavailable_reason,
    )
    from marketops.adapters.mineru_adapter import mineru_available, mineru_unavailable_reason

    rows = [(label, *_probe(module)) for module, label in DEPENDENCY_PROBES] + [
        ("crawl4ai (browser, 可选)", crawl4ai_available(), crawl4ai_unavailable_reason()),
        ("mineru (复杂 PDF fallback, 可选)", mineru_available(), mineru_unavailable_reason()),
    ]
    print("== 依赖可用性 ==")
    for name, available, detail in rows:
        mark = "OK  " if available else "MISS"
        suffix = "" if available else f"  ({detail[:90]})"
        print(f"[{mark}] {name}{suffix}")

    print(
        "\n提示：crawl4ai / mineru 为可选依赖。缺失时 pipeline 仍可运行，"
        "只是对应能力不可用 —— 见 docs/ARCHITECTURE.md 的降级策略。"
    )
    return 0


def _cmd_export_contracts(args: argparse.Namespace) -> int:
    from marketops.schema import write_contracts

    for path in write_contracts():
        print(f"wrote {path}")
    return 0


def _cmd_mint_token(args: argparse.Namespace) -> int:
    """铸一个 MCP 令牌，只打印一次。

    输出契约值得写下来：**stdout 只有令牌本身（一行）**，其余一律进 stderr。
    这样 ``TOKEN=$(marketops mint-token ...)`` 拿到的是干净的东西，而人看到的
    那几句说明不影响它。一份会往 stdout 混说明字的实现，会让第一次这么写的人
    把一个带解释的字符串交给调用方，而那次失败看起来像认证配错了。

    令牌环的路径**必须显式给出**，没有缺省值：缺省值只有在「我知道这个部署把
    凭据放在哪」时才成立，而本仓不知道 —— 见 ``check_credential_path``。

    ``--audience`` / ``--workspace`` / ``--expires-in`` 同样是必填的，理由与
    ``--out`` 一样：它们的缺省值只能是本命令**猜**出来的一个部署事实。缺省受众
    会猜成「本机」，而那正好是反向代理部署里错的那一个；缺省工作区会猜成「哪个
    Scene 都能动」，那时这条令牌在任何部署上都被放行；缺省有效期会猜成一个没人
    评审过的数字，而它是一次安全策略。三样都写进环文件，所以猜错的那一刻就固化了。

    受众、工作区与有效期的取值规则不在本文件里：前者是
    ``security.tokens.normalize_resource``（与 SDK 同一套归一化），中者是
    ``security.workspace.check_workspace_name``（与环装载器同一个函数），后者是
    ``parse_duration``（含那个写明了理由的上界）。
    """
    from marketops.security.catalog import Role
    from marketops.security.tokens import (
        add_token_to_ring,
        check_credential_path,
        parse_duration,
    )

    roles: list[Role] = []
    for name in args.role:
        try:
            roles.append(Role(name))
        except ValueError as exc:
            raise ConfigError(
                f"角色名 {name!r} 不认识。已知角色：{sorted(role.value for role in Role)}"
            ) from exc

    lifetime = parse_duration(args.expires_in)
    target = check_credential_path(Path(args.out), repo_root=REPO_ROOT)
    token, summary = add_token_to_ring(
        target,
        principal=args.principal,
        roles=roles,
        audience=args.audience,
        workspaces=args.workspace,
        expires_in_seconds=lifetime,
        rotate=args.rotate,
    )
    # 令牌单独一行、前面什么都不加：``$( )`` 只吃掉**尾部**换行，一个前导空行会
    # 变成令牌的一部分，而那个错误要到调用方拿它去认证时才会显形。
    print(token)
    print(summary, file=sys.stderr)
    print(f"用法：把它作为 Bearer 令牌交给 {args.principal!r} 这一侧的调用方。", file=sys.stderr)
    # 受众与工作区两格各要再说一句：它们是仅有的两处能让「铸给 A 的令牌拿去连 B」
    # 当场被发现的提示，而两处都不会在拒绝里说清理由（401 没有放理由的位置，
    # 工作区那句不报出本部署的 Scene）。
    print(
        "提示：受众必须在服务端等于 MARKETOPS_MCP_RESOURCE（未设置时按绑定地址推导）。"
        "对不上时那个令牌一律 401，而 401 看起来和写错令牌一样 —— "
        "用 ``python -m marketops.interfaces.mcp --check`` 看两边的值。",
        file=sys.stderr,
    )
    print(
        "提示：工作区要写成服务端 MARKETOPS_PROFILE 的那个值（Scene 的 profile id）。"
        "写错了不是 401 —— 令牌认得过、每一条业务工具都被拒 —— 所以这一格在这里"
        "就要对方答得出来：它准动的是哪个 Scene。",
        file=sys.stderr,
    )
    return 0


def _cmd_run_demo(args: argparse.Namespace) -> int:
    settings = load_settings()
    bundle = load_profile(settings)
    accounts = load_accounts(Path(args.accounts))

    problems = validate_plan(accounts)
    print(f"== 账号清单 ==\n文件：{args.accounts}\n数量：{len(accounts)}")
    for problem in problems:
        print(f"  [!] {problem}")
    if not problems:
        print("  队列构成符合 10/10/10 计划")

    if args.limit:
        accounts = accounts[: args.limit]
        print(f"  （--limit 生效，本次只跑前 {len(accounts)} 个）")

    if args.dry_run:
        print(
            "\n== DRY RUN ==\n"
            "未发起任何网络请求。已完成：配置装载、profile 装载、清单校验。\n"
            f"将会执行：{len(accounts)} 个账号 × 每人 {len(bundle.role_terms) or 'UNKNOWN'} 个角色词表"
        )
        return 0

    if not settings.serper_enabled:
        print(
            "\nSERPER_API_KEY 未设置，无法真实检索。\n"
            "  * 只想验证工程链路：加 --dry-run\n"
            "  * 想真跑：把 key 放进本机 secret 区后重试"
        )
        return 2

    from marketops.interfaces.composition import (
        default_acquisition,
        default_maintenance_store,
        default_router,
    )
    from marketops.adapters.serper import SerperSearchProvider
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
    from marketops.application.pipeline import PipelineDeps, ResearchPipeline

    settings.ensure_data_dirs()

    deps = PipelineDeps(
        settings=settings,
        bundle=bundle,
        search=SerperSearchProvider(settings),
        # 「HTTP 成功就不开浏览器」这条规则不在这里，也不在 pipeline 里 ——
        # 它在入口自己身上（§10）。这里只回答「这个部署接不接浏览器」。
        acquisition=default_acquisition(settings, allow_browser=not args.no_browser),
        store=SqliteEvidenceStore(settings.database_path),
        router=default_router(mineru_output_dir=settings.mirrors_dir / "mineru"),
        # 检索额度的账本与证据库共用同一个库文件 —— 它们是同一份状态的两个面
        # （一次运行花了多少、产出了什么），分开两个文件只会让「备份/搬迁」多一个
        # 容易漏的步骤。上限不在这里设定：它来自冻结的 profile，由
        # ``RunBudget.for_account`` 逐账号播种（§8：调用方不得静默扩大上限）。
        budget=SqliteBudgetLedger(settings.database_path),
        # 维护账（§6）。pipeline 在这一趟**真的**落下新证据行时要给维护账记一条
        # 变更信号，所以它必须有一本账可写 —— 与 ``budget`` 同一条：可省略的那
        # 一天，「忘了注入」与「这一趟真的没有新来源」在任何报告上都同形。
        # 工厂调用一次：``default_maintenance_store`` 返回的是惰性工厂（理由写在
        # 它自己的 docstring 上），而 pipeline 要的是存储本身。
        maintenance=default_maintenance_store(settings)(),
    )
    report = ResearchPipeline(deps).run(accounts)

    out = settings.benchmarks_dir / f"{report.run_id}.json"
    out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n指标已写入 {out}")
    return 0


def _cmd_read_motherpool(args: argparse.Namespace) -> int:
    """读一份母池工件 → 一份回执，落在 ``inputs_dir`` 下。**只读与判定，不联网。**

    ## 它为什么是一个 CLI 动词而不是一个 MCP 工具

    与 ``reconcile`` 逐字同一条理由：MCP 那一面是给**研究员与控制端**的
    （``submit_research`` / ``get_assignment`` / ``search_web``），而这一条是给
    **运维**的 —— 它把上游交进来的一份导出读成一份回执。把它挂在服务面上会让
    「谁能往 ``inputs_dir`` 里写东西」这个问题多出一个主体，而它对研究这件事
    没有任何用处。写完之后的下一句是 ``submit_research`` 的 ``input_ref``：
    **打印出来的那个回执 id 就是它**，那一步才走 MCP。

    ## 退出码

    ``0`` = 跑完了，回执已落盘 —— **含「一行都不可研究」**。那是一个如实的读数
    （回执里每一行都有处置、``blocker`` 说着缺什么），不是这次调用失败。
    ``2`` = 参数或配置不成立（工件不存在或有歧义、名字不是规范形式、
    命中了未声明的排除项、来源方法不在闭集里）。

    一个把「整池都是 pending」也退成非零的实现，会让自动化在**有东西要人看**时
    以为**这个工具坏了**，而两种处置相反 —— 与 ``reconcile`` 对 NEEDS_OPERATOR
    的态度逐字一致。
    """
    from marketops.application.motherpool import read_motherpool, write_receipt
    from marketops.domain.util import check_canonical_id

    settings = load_settings()
    settings.ensure_data_dirs()

    # 名字规则单独在这里过一遍，**只为把它接到退出码 2 上**。
    # ``check_canonical_id`` 抛的是 ``ValueError``：在 ``subjects_for`` 那一侧
    # 那是「调用方给错了」（异常那条通道，由 MCP 层转成一次错误响应），而在 CLI
    # 这一侧同一个事实就叫「这一次调用不成立」—— 也就是 2。
    #
    # 不把整个调用包进 ``except ValueError``：那会连 ``SourceReceipt`` 的
    # ``ValidationError`` 一起吞掉，而那种失败的意思是**读入这一步自己出了问题**
    # （它本该保证每一行都有处置），处置与「路径/名字写错了」相反。
    try:
        check_canonical_id(args.artifact, "artifact")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    receipt = read_motherpool(
        settings.inputs_dir,
        args.artifact,
        source_method_id=args.source_method,
        batch_id=args.batch,
        declared_exclusion_ids=tuple(args.exclusion),
        profile_id=args.profile,
        scope=args.scope,
        cutoff=args.cutoff,
        cursor=args.cursor,
    )
    path = write_receipt(settings.inputs_dir, receipt)

    print(json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print(f"\n回执已落盘：{path}", file=sys.stderr)
    print(f"读数：{receipt.counts_line}", file=sys.stderr)
    if receipt.blocker:
        # 大声说出来。「受理得成功、只是没有活干」与「每一行都缺一次身份补全」
        # 是两件事，而它们都表现为「没有 resolved 行」。
        print(f"\n== 这一份回执**不能**直接拿去受理研究 ==\n{receipt.blocker}", file=sys.stderr)
    else:
        print(
            f"下一步：submit_research(input_kind=source_receipt, "
            f"input_ref={receipt.receipt_id!r})",
            file=sys.stderr,
        )
    return 0


def _cmd_run_discovery(args: argparse.Namespace) -> int:
    """跑一轮**开放网络发现**：发查询、取页面、把结果落成一份可以重新喂进受理的池子。

    ## 它为什么是一个 CLI 动词而不是一个 MCP 工具

    与 ``read-motherpool`` 逐字同一条理由，而且更强：这一条会**真的花钱**
    （检索额度从冻结的 profile 里扣）。MCP 那一面是给研究员与控制端的，而
    「谁能让服务去花预算」这个问题在那一面多一个主体就多一处要解释的地方。
    用户面向的服务面入口是 ``submit_research(input_kind=discovery_scope)``：
    那一侧收的是**已经跑出来的范围**，不是「去跑一轮」。跑这一轮的人是运维，
    做的是一次有额度、有上限、有理由的作业。

    ## ``--region`` 为什么必填、而且没有缺省值

    发现期的检索式里有一格是地区（``{region}``），而**冻结规格的装载器在本工作树里
    不存在** —— 给它一个缺省值（比如 ``GB``）就等于替这一次运行做了一个它没做过的
    决定，而那个决定会进工件、进回执、进每一个下游读数，且不留痕。所以它由操作者
    显式给出，并且被记成这一次运行的**冻结**值（工件的 ``discovery_region`` 一格）。

    ## 退出码

    ``0`` = 跑完了，读数已落盘 —— **含「一行都没产出」**。那是如实的读数
    （``stops`` 会说出停下来时是什么状态），不是这次调用失败。与
    ``read-motherpool`` 对「整池都是 pending」的态度逐字一致。
    ``2`` = 参数或配置不成立，**而且都在花掉第一次检索之前**：范围名不是规范形式、
    地区为空、profile 没有声明 ``search.max_queries``、工件名已被占用、
    检索式一条都展开不出来、``SERPER_API_KEY`` 未设置。
    """
    from marketops.application.discovery_run import (
        DiscoveryScope,
        DiscoveryStop,
        materialize_pool,
        run_discovery,
    )
    from marketops.domain.util import check_canonical_id

    settings = load_settings()
    bundle = load_profile(settings)

    # 名字与参数在这里过一遍，**只为把它们接到退出码 2 上**（与 ``read-motherpool``
    # 逐字同一条理由）。``DiscoveryScope`` 抛 ``ValueError``，而那在 MCP 那一侧是
    # 「调用方给错了」；在 CLI 这一侧同一个事实就叫「这一次调用不成立」。
    try:
        check_canonical_id(args.scope, "scope")
        scope = DiscoveryScope(
            scope=args.scope,
            region=args.region,
            limit=args.limit,
            max_candidates=args.max_candidates,
            fetch_pages=not args.no_fetch,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not scope.region.strip():
        raise ConfigError(
            "--region 不能是空白。\n"
            "它是这一次运行**冻结**的地区值（绑给检索式里的 {region}），会逐字进工件与"
            "回执。本工作树里没有冻结规格的装载器，所以它只能由操作者给出 —— "
            "编一个缺省值等于替这一次运行做一个它没做过的决定。"
        )

    # 工件名与额度上限都在**发第一发查询之前**问一遍。两件事都可能在跑到一半时
    # 才被发现（名字被占用、profile 没声明额度），而那时候钱已经花掉了。
    artifact = settings.inputs_dir / f"{scope.artifact_ref}.jsonl"
    if artifact.exists():
        raise ConfigError(
            f"工件 {artifact.name} 已经存在（{artifact}）。\n"
            "重跑同一轮要换一个范围名 —— 范围名同时是工件名、检索镜像与页面镜像的"
            "子目录名、以及额度记在谁名下那一段（见 marketops/application/budget.py）。"
        )
    from marketops.application.budget import declared_search_ceiling
    from marketops.domain.errors import BudgetNotConfiguredError

    try:
        ceiling = declared_search_ceiling(bundle)
    except BudgetNotConfiguredError as exc:
        # 「这个部署没有声明检索上限」是**配置**不成立，不是运行失败 —— 它要落在
        # 退出码 2 那一档（与 ``run-discovery`` 的其余前置条件同一档）。不转的话
        # 它会以 ``BudgetError`` 落进「其余 MarketOpsError」那一档（退出码 1），
        # 而那一个码的含义是「跑起来了、但运行本身失败了」—— 恰恰相反。
        raise ConfigError(str(exc)) from exc

    wants_network = not args.dry_run
    if wants_network and not settings.serper_enabled:
        print(
            "\nSERPER_API_KEY 未设置，无法真实检索。\n"
            "  * 只看这一轮会发什么：加 --dry-run\n"
            "  * 想真跑：把 key 放进本机 secret 区后重试",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        # 检索式是**纯函数**产出的，所以这一条不联网也能给出这一次运行会发什么。
        # 它不是「跑了一半」：一份计划与一次运行是两件事，而把前者印成后者
        # （比如报「0 条命中」）会让「还没跑」看起来像「跑了、什么都没有」。
        from marketops.application.openweb import build_open_web_queries

        plan = build_open_web_queries(bundle, frozen_region=scope.region)
        print(json.dumps(plan.summary(), ensure_ascii=False, indent=2))
        print("\n== DRY RUN ==\n未发起任何网络请求、未写入任何工件。已完成：配置装载、profile 装载、检索式展开。")
        print(f"将会执行：{len(plan.queries)} 条检索式（每条最多 {scope.limit} 条结果）")
        print(
            f"上限：检索 {ceiling} 次（来自 profile 的 search.max_queries）、"
            f"进池子的候选最多 {scope.max_candidates} 个"
        )
        if len(plan.queries) > ceiling:
            # 展开器与额度是两件事：展开器把词表的**乘积**列出来，额度决定这一轮
            # 真正发得出去几条。差值不是错误，但它必须被说出来 —— 否则「计划了 542
            # 条」会被读成「会发 542 条」，而实际停下来时是
            # ``DiscoveryStop.BUDGET_EXHAUSTED``（「额度用尽」），不是「候选枯竭」。
            print(
                f"注意：计划里的 {len(plan.queries)} 条**发不完** —— 额度是 {ceiling} 次，"
                f"后 {len(plan.queries) - ceiling} 条发不出去。\n"
                f"  停下来时是「额度用尽」（{DiscoveryStop.BUDGET_EXHAUSTED.value}），"
                "不是「候选枯竭」；两者在报告里是不同的读数。\n"
                "  计划是按模板 × 场地类型的乘积展开的，它列出的是**词表覆盖**，"
                "不是这一轮真的会发出去的东西。"
            )
        print(f"工件名：{artifact.name}", file=sys.stderr)
        for query in plan.queries:
            print(f"  [{query.template}] {query.text}", file=sys.stderr)
        return 0

    from marketops.adapters.serper import SerperSearchProvider
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
    from marketops.interfaces.composition import default_acquisition, default_router

    settings.ensure_data_dirs()
    run = run_discovery(
        bundle=bundle,
        scope=scope,
        search=SerperSearchProvider(settings),
        acquisition=default_acquisition(settings, allow_browser=not args.no_browser),
        store=SqliteEvidenceStore(settings.database_path),
        router=default_router(mineru_output_dir=settings.mirrors_dir / "mineru"),
        ledger=SqliteBudgetLedger(settings.database_path),
        inputs_dir=settings.inputs_dir,
    )
    report = run.as_dict()
    out = settings.benchmarks_dir / f"{run.run_id}.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n读数已写入 {out}", file=sys.stderr)

    pool = materialize_pool(settings.inputs_dir, run)
    if pool is None:
        # **不写空工件**：一份空工件会被读成「跑了、一行都没成」，而它其实什么都没
        # 声明 —— 一份空工件连 ``read_motherpool`` 都会拒绝（「一份空工件不是一次
        # 成功的母池读入」）。为什么空，答案在 stops 里，所以这里把它印出来。
        print(
            "\n== 这一轮一行都没有产出，**没有**写任何工件 ==\n"
            f"停下来时的状态：{[stop.value for stop in run.stops]}\n"
            f"取回读数：命中 {len(run.hits)} 条、涉及主机 {len(run.sites)} 台、"
            f"抓取结果分布 {run.fetch_counts()}、落成证据 {len(run.evidence_ids)} 行。\n"
            "「跑了、没搜到」与「跑了、一行都没成」是两件事，而两份读数都在上面那份"
            "报告里 —— 不要把它读成一次成功的母池读入。",
            file=sys.stderr,
        )
        return 0

    print(f"\n池子已落盘：{pool.artifact_path}", file=sys.stderr)
    print(f"回执已落盘：{pool.receipt_path}", file=sys.stderr)
    hits = run.hit_counts()
    named = sum(1 for site in run.sites if site.named)
    print(
        f"取回读数：命中 {len(run.hits)} 条（新主机 {hits.get('site', 0)} 台、"
        f"同主机重复 {hits.get('same_site', 0)} 条、"
        f"折不出主机 {hits.get('unusable_url', 0)} 条）；"
        f"涉及主机 {len(run.sites)} 台、其中读出名字的 {named} 台；"
        f"抓取结果分布 {run.fetch_counts()}；落成证据 {len(run.evidence_ids)} 行。",
        file=sys.stderr,
    )
    # 这一句是为一个具体的误读写的：``resolved=N`` 说的是**池子里的行**，
    # 不是「发现了 N 个单位」。没读到名字的主机不在池子里（它们不是待补全的
    # 对象，是一次失败的抓取 —— 而按 ``source_identity`` 把它们写进池子，
    # 下游的身份补全就会拿一台主机名去花检索额度）。不上这一句，一次「取了 60 页、
    # 只读出 4 个名字」的运行会与一次「只发现 4 个」在屏幕上同形。
    print(
        f"读数：{pool.receipt.counts_line}\n"
        "      （回执说的是**池子里的行**；没读出名字的主机不在这里，它们在上面那份"
        "报告里各自的 reason 一栏）",
        file=sys.stderr,
    )
    if pool.receipt.blocker:
        print(f"\n== 这一份回执**不能**直接拿去受理研究 ==\n{pool.receipt.blocker}", file=sys.stderr)
    else:
        print(
            f"下一步：submit_research(input_kind=discovery_scope, "
            f"input_ref={scope.artifact_ref!r})",
            file=sys.stderr,
        )
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    """把历史投影对账干净 —— **默认 dry-run，且一次都不派发**。

    ## 它为什么是一个 CLI 动词而不是一个 MCP 工具

    因为 MCP 那一面是给**研究员**的（``get_assignment`` / ``search_web`` /
    ``submit_findings``），而这条路径是给**运维**的：它读的是外部看板、写的是
    外部看板，不产生任何研究内容。把它挂在服务面上会让「谁能在生产上改外部
    看板」这个问题多出**一个主体**（任何一个持研究员令牌的进程），而它对研究
    这件事没有任何用处。

    ## 退出码

    ``0`` = 跑完了（**含「有 NEEDS_OPERATOR」**）—— 那条处置是给人看的，不是
    这次调用失败。``2`` = 参数或配置不成立（作业不存在、``--class`` 不认识）。
    一个把 NEEDS_OPERATOR 也退成非零的实现，会让自动化在「有东西要人看」时
    以为「这个工具坏了」，而两种处置相反。
    """
    from marketops.application.reconcile import ALL_CLASSES, ReconcileClass, reconcile_job
    from marketops.interfaces.composition import default_dispatcher

    # 参数校验走在装载部署之前：一个拼错的 ``--class`` 是**这一次调用**不成立，
    # 而 ``load_settings()`` 失败说的是**这个部署**没配好。两件事的处置不同，
    # 而先装载的那一版会让「类别写错了」在没配好的机器上报成「没有数据目录」。
    wanted: list[ReconcileClass] = []
    for name in args.reconcile_class:
        try:
            wanted.append(ReconcileClass(name))
        except ValueError as exc:
            known = ", ".join(cls.value for cls in ALL_CLASSES)
            raise ConfigError(f"不认识的类别 {name!r}；可用的是 {known}。") from exc

    settings = load_settings()
    dispatcher = default_dispatcher(settings)

    from marketops.adapters.sqlite.jobs import SqliteJobStore

    settings.ensure_data_dirs()
    report = reconcile_job(
        jobs=SqliteJobStore(settings.database_path),
        dispatcher=dispatcher,
        job_id=args.job_id,
        classes=tuple(wanted),
        dry_run=not args.apply,
    )

    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.apply:
        print(f"\n== APPLY：真的写下去 {len(report.writes)} 次 ==", file=sys.stderr)
    else:
        print(
            f"\n== DRY RUN：一次写都没产生；判据说要写 {len(report.planned_writes)} 次"
            "（加 --apply 才会真的写）==",
            file=sys.stderr,
        )
    if report.needs_operator:
        print(
            f"== 需要人看一眼的有 {len(report.needs_operator)} 项 ==",
            file=sys.stderr,
        )
    return 0


def _probe(module: str) -> tuple[bool, str]:
    try:
        __import__(module)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _cmd_trial_preflight(args: argparse.Namespace) -> int:
    """试运行自检（§26）。**不发起任何真实付费请求。**

    判据一条都不在这个函数里：它住在组合根（``interfaces/mcp/__main__.py`` 的
    ``trial_preflight``），因为七行里有四行读的是 ``_compose`` 的产物 ——
    另一处再装配一遍的形态是「自检说接线了、真实例是 stub」，两份都长得正常。
    这一层只做两件事：转调，以及把判词翻成一个退出码。

    退出码只有 0 与 1：四值判词在 stdout 上，**不往退出码里再编四个数字** ——
    两个地方各说一遍同一件事，分歧时读的人不知道该信哪一个。1 与 ``main`` 里
    「运行失败」共用，也是这个道理：这一层要说的是「没到 READY」，而具体是哪一种
    没到，报告里写着。
    """
    from marketops.interfaces.mcp.__main__ import PREFLIGHT_READY, trial_preflight

    report = trial_preflight()
    if args.json:
        print(json.dumps(report.payload(), ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return 0 if report.verdict == PREFLIGHT_READY else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marketops", description="MarketOps research pipeline")
    parser.add_argument("--version", action="version", version=f"marketops {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-config", help="打印配置与 profile 摘要（不含 secret）").set_defaults(
        func=_cmd_check_config
    )
    sub.add_parser("check-deps", help="报告依赖可用性").set_defaults(func=_cmd_check_deps)
    sub.add_parser("export-contracts", help="从模型导出 contracts/*.json").set_defaults(
        func=_cmd_export_contracts
    )

    token = sub.add_parser(
        "mint-token", help="铸一个 MCP 令牌（只打印一次，落盘的只有摘要）"
    )
    token.add_argument("--principal", required=True, help="令牌属于谁（进入审计记录的那个名字）")
    token.add_argument(
        "--role",
        action="append",
        required=True,
        help="授予的角色，可重复（controller / researcher）",
    )
    token.add_argument("--out", required=True, help="令牌环文件路径，必须在仓库之外")
    token.add_argument(
        "--audience",
        required=True,
        help="这条令牌发给哪个资源（RFC 8707 指示符，如 http://127.0.0.1:8787/mcp）；"
        "必须与服务端认定的资源一致，否则一律 401",
    )
    token.add_argument(
        "--workspace",
        action="append",
        required=True,
        # 例子刻意不写某一个真的 profile id：默认 profile 指针只应出现在
        # config.py（``test_profile_pointer_is_confined_to_config``），而一句
        # 举例说明会顺手把一个市场名写进核心代码 —— 那条判据会拦下它。
        help="这条令牌准在哪个 Scene 里动手，可重复；取值是 Scene 的 profile id"
        "（服务端 MARKETOPS_PROFILE 的那一个）。与 --audience 是两格：受众答"
        "「发给哪个服务」，工作区答「在那个服务里准动哪个 Scene 的对象」",
    )
    token.add_argument(
        "--expires-in",
        required=True,
        help="有效期，整数加单位：d / h / m / s（如 30d、12h）；上界 365 天，不接受 never",
    )
    token.add_argument("--rotate", action="store_true", help="该主体已在环里时替换它的令牌")
    token.set_defaults(func=_cmd_mint_token)

    rec = sub.add_parser(
        "reconcile",
        help="把外部投影与本地结账对账（默认 dry-run；**一次都不派发**）",
    )
    rec.add_argument("--job-id", required=True, help="要对账的作业 id")
    rec.add_argument(
        "--class",
        dest="reconcile_class",
        action="append",
        default=[],
        help="要对账的类别，可重复（board / lease / mapping）；不给就是三类都做",
    )
    rec.add_argument(
        "--apply",
        action="store_true",
        help="真的写外部看板。不给就是 dry-run，判据照跑但一次写都不产生",
    )
    rec.set_defaults(func=_cmd_reconcile)

    pool = sub.add_parser(
        "read-motherpool",
        help="把一份母池工件读成回执（只读与判定，不联网）",
    )
    pool.add_argument(
        "--artifact",
        required=True,
        help="工件的**名字**（不含后缀），放在 data_root/inputs 下；不收路径",
    )
    pool.add_argument(
        "--source-method",
        default="import.register.v1",
        help="这一批是怎么来的（闭集，见 marketops/domain/sources.py）",
    )
    pool.add_argument("--batch", default="", help="上游对这一批的称呼（可空）")
    pool.add_argument(
        "--exclusion",
        action="append",
        default=[],
        help="这份 profile **已声明**的排除项 id，可重复；来源行命中的必须在这里",
    )
    pool.add_argument("--profile", default="", help="读的时候哪一份 profile 的排除声明在生效")
    pool.add_argument("--scope", default="", help="范围标签，原样记进回执（可空）")
    pool.add_argument("--cutoff", default="", help="上游的截止时间，原样记进回执（可空）")
    pool.add_argument("--cursor", default="", help="上游的游标，原样记进回执（可空）")
    pool.set_defaults(func=_cmd_read_motherpool)

    run = sub.add_parser("run-demo", help="运行第一轮 Demo")
    run.add_argument("--accounts", required=True, help="账号清单 YAML 路径")
    run.add_argument("--dry-run", action="store_true", help="只校验，不联网")
    run.add_argument("--limit", type=int, default=0, help="只跑前 N 个账号（调试用）")
    run.add_argument("--no-browser", action="store_true", help="禁用 browser 升级")
    run.set_defaults(func=_cmd_run_demo)

    disc = sub.add_parser(
        "run-discovery",
        help="跑一轮开放网络发现，把结果落成可以喂进受理的母池工件（会花检索额度）",
    )
    disc.add_argument("--scope", required=True, help="范围名（规范形式；同时是工件名与额度作用域）")
    disc.add_argument(
        "--region",
        required=True,
        help="这一次运行**冻结**的地区值，绑给检索式里的 {region}；没有缺省值",
    )
    disc.add_argument("--limit", type=int, default=10, help="每条检索式最多取几条结果")
    disc.add_argument(
        "--max-candidates",
        type=int,
        default=25,
        help="进池子的候选上限；到顶就停，并且停下来这件事会被记下来",
    )
    disc.add_argument("--no-fetch", action="store_true", help="只发查询，不去取页面（没有证据）")
    disc.add_argument("--no-browser", action="store_true", help="禁用 browser 升级")
    disc.add_argument(
        "--dry-run",
        action="store_true",
        help="只展开检索式并打印上限，一次网络请求都不发、一个工件都不写",
    )
    disc.set_defaults(func=_cmd_run_discovery)

    # ``trial preflight`` 是**两层**：``trial`` 是那一族问题（「能不能开一次真实
    # 试运行」），``preflight`` 是其中的第一个动词。做成两层而不是
    # ``trial-preflight`` 一个名字，是因为 §26 那句话列的是**动词**，而这一族下面
    # 还会有别的动词（例如真的把一次试运行开起来）—— 一个连字符名字会在第二个
    # 动词出现时逼出一次改名，而改名会把已经写进文档与脚本的那一行弄坏。
    trial = sub.add_parser("trial", help="试运行相关的动词（下面每一个都不发起真实付费请求）")
    trial_sub = trial.add_subparsers(dest="trial_command", required=True)
    pre = trial_sub.add_parser(
        "preflight",
        help="回答「这个部署能不能开一次真实试运行」，判词四值，退出码 0=READY / 1=其余",
    )
    pre.add_argument(
        "--json",
        action="store_true",
        help="把整份报告打成 JSON（七行 + 一个判词），供脚本读；不给就是人读的那一份",
    )
    pre.set_defaults(func=_cmd_trial_preflight)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except MarketOpsError as exc:
        print(f"运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main", "build_parser", "DEPENDENCY_PROBES"]
