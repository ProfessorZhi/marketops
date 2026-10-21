# MarketOps Research Assistant v0.4 — 代码审查与发布状态（2026-10-16）

**判定：可进入本地离线测试与 Codex/WorkBuddy MCP 验收，但不是即装即用的正式 Member-A ZIP。** 本次只修改 private GitHub MarketOps PR #64 中 plugins/research-tools 目录；没有调用收费 API、没有部署东京设备或修改主项目业务数据库。用户计划是先完成本地 E2E，再提供经验证的压缩包。

## 已有的实现（不是实测 PASS）

| 模块 | 当前代码/文档 | 本次能确认的状态 |
| --- | --- | --- |
| Member-A 业务知识 | 8 业务 Skills、BUSINESS_PLAYBOOK、MEMBER_A_SKILL_ANALYSIS、QUALITY_GATES | 基于 Member-A Session 衍生审计整理；不得自动改 SOP |
| 五 Provider 能力 | research_mcp.py、dual_search_mcp.py、research_provider_extensions.py | 一个本地 stdio MCP：SerpApi/Serper 搜索、Crawl4AI local/Cloud、TinyFish Search/Fetch、Jev Choice 源码路径已入库 |
| Skills 与安装 | 总计16 Skills、根/兼容清单、setup_clients.py、build_client_bundles.py、MEMBER_A_INSTALL | Codex Marketplace/WorkBuddy 单MCP+Skills 客户机安装候选；尚未实机验证 |
| 研究数据 | research_lite_store.py | 本地 SQLite Run/候选实体，衍生材料与SHA；非 MarketOps 正式 Market Graph |
| 经验 | research_event_journal.py、experience_pack.py | 已接入操作事件自动保存，marketops_handoff 联合输出候选 CSV 与经验初稿；不是自动批准经验 |
| 审核工具 | preflight.py、模拟 unittest、RELEASE_GATE | 检查和测试代码已写入；本轮**未在客户/隔离运行器执行完整测试** |

## 已修复的重要问题

1. **SerpApi 与 Serper** 不是同一家公司：API、密钥和账单严格隔离，不自动切换或重复消费。
2. **Codex 与 WorkBuddy MCP 配置**：两份 Codex 清单指向唯一 marketops-research 服务；MCP JSON 采用实际加载器支持的 mcpServers；WorkBuddy 单Server、Token表单使用目标机器的解释器和源码路径。
3. **Codex 插件缓存 cwd**：安装脚本不把 cwd 固定为缓存插件根之外的位置，防止客户端安全校验拒绝启动。
4. **跨 Run 串资料风险**：artifact_id、record_id 必须属于同一 Run；先校验归属，再写入资料，重复或矛盾 ID 拒绝。
5. **经验丢事件风险**：已接入工具的失败、阻断、费用/凭据前置条件和部分交付写入观察日志；未知根因不能自动声明已验证。
6. **包中混入客户资料风险**：安装器过滤真实 SQLite、搜索回执、artifacts、exports、experience、.env 和常见凭据；客户数据根不得在 Git 工作树内。
7. **错误晋升风险**：Lite 的 venue/organization/person/contact_route 都是候选，不能设置 verified、ready、approved 或 send_authorized。

## 阻止给 Member-A 发布正式 ZIP 的剩余问题

**B1 完整离线测试未执行。** 运行 `python scripts/preflight.py`、`python -m unittest discover -s tests -v`、`python -m compileall -q scripts tests`，并记录实际日志/环境。GitHub 文件审核不是 Python 测试 PASS。

**B2 五家真实API与两客户端 E2E 未执行。** Codex/WorkBuddy 真实插件导入、MCP tools/list、SerpApi/Serper、Crawl4AI local/Cloud、TinyFish、Jev 使用客户自有 Key 与已批准试用额度逐个验收；不可把 installed 和 authenticated 混为一谈。

**B3 完整商业研究未闭环。** Lite Store 只有候选，正式 A/B/C/D 联系路线需要主 MarketOps 可信业务规则/证据/人类复核/权限模型。英国 AcmeCorp 已知 named Procurement 评级缺口保留 RULES_UNKNOWN，不可由模型猜测。

**B4 Excel XLSX 未实机生成。** 当前是可信候选CSV和Excel业务Skill；只有实际客户端拥有表格引擎、输出并核对XLSX，才能把格式写成正式交付。

**B5 供应商高级能力和多人服务未接。** TinyFish复杂浏览器Agent走独立官方MCP；Jev Noul/Score尚无Lite MCP工具；并发多设备需要一个认证服务端，不能直接共享SQLite；自动CC调度和外联是另一个产品。

**B6 安全策略需实机审查。** URL允许域不等于完全OS/网络隔离，浏览器仍需防内网请求与DNS重绑定。SerpApi原生REST URL中的Key可能出现在代理/请求审计日志；账户硬限额、浏览器和工作区权限由客户设置。

## 发包前最低动作

首先在用户批准的测试机只执行静态与mock测试；随后对五家各做获准的最小真实调用，检查同一个Run的来源发现→材料取证→候选实体→回读对账→人工裁决→CSV/真实XLSX→经验草稿。再在Codex和WorkBuddy各测试解包与安装，执行 references/RELEASE_GATE.md 的所有适用条目，记录产品版本、Python版本、Commit SHA、Provider响应ID、费用及失败。

通过后才从锁定的Git Commit生成不含任何客户数据和Key的源码ZIP、记录SHA256和回滚步骤。**今天不提前生成声称可交付的压缩包。**


## 2026-10-16 additional implementation and test evidence — no false E2E green

**New code in PR #64, 2026-10-16:** `scripts/_xlsx.py` is a provenance-marked copy of the existing tested integration-branch OOXML serializer; `scripts/research_delivery.py` projects one Lite SQLite Run into a real **9-sheet** candidate workbook, per-sheet CSV files and SHA-256 manifest. It refuses an empty research Run, wrong-run source references, corrupted source material bytes and overwritten delivery ID. It checks public emails' literal occurrence in saved material, but **does not** promote the contact routes to approved A/B/C/D. `research_mcp.py` now exposes `marketops_export_xlsx`. The 17th Skill is `marketops-research-acceptance`.

**Tests authored:** `tests/test_research_delivery.py` checks SQLite→XLSX/CSV readback, actual source bytes tampering, unsupported email HOLD and immutable repeated delivery. `tests/test_research_live_e2e.py` is an opt-in actual read-only public HTTPS source test against the official City of Lincoln Council page; it checks source content and produces a real candidate research workbook. The dedicated GitHub Actions workflow `.github/workflows/research-plugin-e2e.yml` runs offline and live-source jobs independently.

**Test verdict:** As of this update, GitHub Actions jobs returned `failure` with **no visible step summaries**, and downloading job logs returned `404 BlobNotFound`.

**CI failure cause, now CONFIRMED (2026-10-16, Tokyo acceptance run):** it is account billing, not Python and not the workflow. The check-run annotation on run `38833245882` (job `116756514780`) reads verbatim:

> The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings

Every job in both workflows shows `runner_name: ""`, `runner_group_name: ""`, `steps: []` and a 3-second `started_at` → `completed_at` gap: no runner was ever assigned, so no step and no log ever existed — which is exactly why the log download is `404 BlobNotFound`. `main` fails identically (latest `ci` run on `main` is `36585763573`, also `failure`), so this is repository/account-wide and blocks every branch. **The red X on this PR carries no information about this PR's Python code.**

**Independent acceptance run on an authorized runner (Tokyo `device:tokyo-shared-dev-01`, worktree `worktrees/marketops/research-e2e-acceptance-20261016`, branch base `7491730`):** the offline suite was executed for real and is green after two stale assertions were fixed (see the commit that updates `tests/test_dual_search_mcp.py` and `tests/test_setup_clients.py` from 16 to 17 Skills). `python -m compileall -q scripts tests` → rc 0; `python -m unittest discover -s tests -v` → 68 tests, `OK (skipped=1)`, where the single skip is the explicit opt-in live test; `python scripts/preflight.py` → rc 0, `STATIC_PASS_NOT_RUNTIME_VERIFIED`. The opt-in live test and a separate 4-source acceptance run against real `www.lincoln.gov.uk` HTTPS pages produced a real 9-sheet XLSX whose per-file SHA-256 matched `MANIFEST.json`. This is **L2 evidence only**.

This branch remains **Draft, NOT RELEASE READY**. No real Serper/SerpApi, Crawl4AI, TinyFish, Jev billable requests, Codex/WorkBuddy client handshake or formal SOP-graded research were executed by this PR.

**Separate demonstrator, not plugin E2E:** A manually researched three-source public-sector sample was rendered as a nine-sheet workbook with `artifact_tool` in the current ChatGPT execution environment. It contains four organization candidates, three venue candidates, five public work contact candidates and five review issues. Formula-based summary counts, XLSX ZIP inspection and worksheet re-import were observed. The source materials in that demonstration are **web-retrieved text extracts**, not the original HTTP response bytes; it must not be claimed as verifying the plugin's artifact hash functionality, external Provider integration or real Agent flow.

**Next blocked gate:** Run the exact plugin test suite on an authorized Python runner with logs, and then use a customer-approved small-scope live Provider trial with proper credentials/SOP and reviewer. An approved human business verdict is still required before route promotion. Never treat the separate demonstration workbook as a formal client research release.
