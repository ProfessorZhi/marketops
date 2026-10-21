# MarketOps

MarketOps 是完整的市场运营产品：持续维护场地/资产母池，核实责任实体，研究有证据且有时效性的联系人和公开邮箱，未来在独立授权下完成个性化外联、回复监控和销售交接。

## 开发入门

以下命令在一个全新 clone + 隔离 venv 中实测通过（Linux，Python 3.12）。venv 建在仓库之外，因为 `tests/test_no_stale_assumptions.py` 会扫描整个仓库树。`MARKETOPS_DATA_ROOT` 必须是仓库之外的显式运行目录。

### Clone

```bash
git clone https://github.com/ProfessorZhi/marketops.git
cd marketops
```

### 装依赖

```bash
python3.12 -m venv ~/.venvs/marketops
. ~/.venvs/marketops/bin/activate
python -m pip install -e ".[dev]" pytest-timeout
```

### 跑测试

```bash
CUDA_VISIBLE_DEVICES="" nice -n 10 python -m pytest -q --timeout=300
```

根套件不再排除快照、排序/折叠、slugify 或邮箱归一化测试。公开快照分别校验
脱敏文件的 `public_snapshot_sha256` 与原始历史 pin 的格式，未改写原始 pin。
排序与折叠断言遵循存储接口的既定契约。

根套件包含 Linux 专用的 `fcntl` 身份锁模块，全量结果以 GitHub Linux CI 为准。
Windows 安装流程由 `research-plugin-e2e` 的 Windows job 验证；Windows 上直接跑完整
离线测试还会遇到既有夹具对 HOME、文件权限、短路径别名和 SQLite 句柄清理的假设。

插件测试单独跑：

```bash
cd plugins/research-tools/marketops-research-assistant
python -m pip install -r requirements-lite.txt
CUDA_VISIBLE_DEVICES="" nice -n 10 python -m unittest discover -s tests -v
```

完整插件套件的当前数量与结果见 `research-plugin-e2e` 的 `offline-3.12` 日志。
`requirements-lite.txt` 里没有 pytest，用的是 `unittest discover`，但 `mcp` 与 `typesafe-sdk` 必须装上，否则相关用例走的是「SDK 未安装」的阻断分支。

### 本地启动

CLI 与 MCP 服务面：

```bash
export MARKETOPS_DATA_ROOT=/tmp/marketops-dev
mkdir -p "$MARKETOPS_DATA_ROOT"
marketops --help
marketops-controller --help
python -m marketops.interfaces.mcp --check
python -m marketops.interfaces.mcp
```

实测：两个 `--help` 均 rc=0；`--check` 在未配置 `MARKETOPS_MCP_TOKENS` 时 **exit 1 属预期**——服务面能启动，但没有令牌环就“谁都不许”，blocker 会明确写出缺少哪一项；`python -m marketops.interfaces.mcp` 绑定 `127.0.0.1:8787/mcp`（Streamable HTTP）。

## 当前目标：六项技术全部接入的 MCP 研究 MVP

**Serper、Crawl4AI、Jev、Laya、Quay、Claude Code 全部属于 MVP 必做、必实测范围。** 对外提供一个 MarketOps MCP 业务服务，而不是要求用户分别接六套工具。

```text
Claude Code 测试客户端（后续换成现有 Hosting/Hermes 入口）
        ↓
MarketOps MCP + 持久化业务台账
        ↓
Quay 实际派发 → Claude Code 研究 worker
        ↓
Serper 搜索 | HTTP/PDF + Crawl4AI 获取 | Jev + Laya 决策
        ↓
证据验证、时效、去重、账户档案、报告
```

全部接入不等于每个账户必须串行调用全部技术：静态正文可走 HTTP，浏览器能力按需使用；Jev/Laya 是统一契约下的两个真实后端，基于同一批证据做对比，再按配置参与研究。缺凭据、模型或真实调用证据时，可以报告降级结果，但不能宣布完整 MVP 验收通过。

MCP 是服务接口；Skill 是研究方法；插件是配置/Skill 的分发包装；Quay/Claude 是研究执行组合。不要再增加通用 Agent 平台、第二套调度器或默认外部记忆系统。Hosting 不改源码，先用 Claude Code 验证同一个 MCP 服务。

本仓库当前已有的 CLI 研究引擎与上面要实现的完整 MCP MVP 是两个交付阶段。**以下文档是设计和验收依据，不表示所有服务已经实现或部署。**

Current development deployment: a host-native MarketOps service/MCP and one shared
CPU Laya endpoint also used by ServerOS. Serper uses the existing adapter and protected
key binding; Jev remains a remote comparison/decision adapter. Docker packaging comes
later. See `docs/DEVELOPMENT_INFRASTRUCTURE.md` for the current boundary.

See:

- `docs/ARCHITECTURE.md` — 完整业务架构、六项必需集成、证据/时效/预算边界。
- `docs/MCP_SERVICE.md` — 控制/研究工具、真实调用凭证及完整 MVP 验收。
- `docs/DEPLOYMENT.md` — 小型部署、浏览器隔离、Laya 常驻推理与服务生命周期。
- `docs/DATA_MODEL.md`、`docs/SOP_MAPPING.md` — 既有业务契约和 SOP 映射。
- `docs/REQUIREMENTS_DISCOVERY.md` 等 — 需求发现历史；旧的候选/可选技术描述不改变本轮六项必做范围。

当前不发邮件、不提交外部表单、不启用 follow-up；`research_only=true`、`send_authorized=false`。真实外联和生产 Hosting 配置另行验收与授权。

---

## 已有实现：known-account 联系路线研究引擎

给定一批已知组织，在公开可得的页面和文档中寻找 Name / Role / Email 的字面证据，绑定为联系路线，按 profile 规则定级，并保留证据以便审计与复现。

产出在两条独立的轴上：

```text
Account：discovered / eligible / hold / irrelevant / duplicate …
ContactRoute：A / B / C / D / X
```

一条路线 = 账户 + 人/部门 + 岗位/业务职能 + 字面公开邮箱 + 证据。没有邮箱时是 RouteOpportunity 或 ContactSignal，不能把电话/表单当 D。业务规则以 `sources/` 中冻结 SOP 和 `profiles/` 为准，不以历史试跑中临时使用的标签为准。

```text
Known Account
    → Serper URL discovery
    → HTTP Fetch
    → 必要的 Crawl4AI Browser Fetch
    → HTML / PDF parsing
    → literal extraction / evidence binding
    → grade_route + grade_account
    → Evidence Store
```

既有关键约束包括：搜索摘要不能成为路线证据；已取得可用正文时不必启动浏览器；明确受阻不绕过；没有字面邮箱不能成为有效邮箱路线；研究权限不能打开真实发送。语义归属仍需来源证据，不能把 hash 或字段检查视为绝对正确性证明。

## 现有 CLI 快速开始

以下是已有研究引擎入口，不是尚待实现的 MCP 服务启动命令。安装前遵守目标机器的目录、网络与虚拟环境策略。

```bash
export MARKETOPS_DATA_ROOT=<运行目录>
python -m pip install -e ".[dev]"
python -m marketops.interfaces.cli check-config
python -m marketops.interfaces.cli check-deps
python -m pytest
python -m marketops.interfaces.cli run-demo --accounts <清单.yaml> --dry-run
python -m marketops.interfaces.cli run-demo --accounts <清单.yaml>
```

现有 `.[browser]` 是 Python 浏览器适配依赖入口；它不等于已部署、验证过的 MVP Crawl4AI 服务。MVP 的新入口、依赖锁定与部署文件须随实现提交并实测，不在文档里编造可执行命令。

## 目录

**这一节描述的是当前实际的布局。** 迁移已经走完（`docs/MVP_MIGRATION_PLAN.md`
的 B1–B7 与 Phase C–H 全部落地），所以这里不再是「一份追赶中的清单」。
`docs/MVP_MIGRATION_PLAN.md` §1 也画了一棵树，但那是 **Phase A 定的目标树** ——
拿它对照「还差什么」，不要拿它查文件在哪。想知道哪份文档是当前事实、哪份是被冻结的
盘点，读 `docs/README.md`。

```text
marketops/          Python 包 —— 唯一会被装出去的东西（pyproject 的 packages 只有它）
  domain/           纯领域：实体、值对象、规则。不碰网络 / 磁盘 / 厂商
  application/      用例编排：发现、准入定级、预算与上限、派发、证据、运行报告
  ports/            契约：search / acquisition / documents / store / budget / jobs / dispatch / decisions
  adapters/         实现：serper / http / crawl4ai / pypdf / mineru / jev / laya
                     / sqlite / jsonl / feishu / mcp_server（绑 MCP SDK 的那几个模块 ——
                     不是端口，是入口）
  interfaces/       入口与**组合根**：cli.py + composition.py
                     （唯一允许点名具体厂商 adapter 的两个落点）
  security/         权限与合规边界：tokens（主体从哪来）/ catalog（判什么）
                     / network（允许去哪、允许绑哪）
  config.py         唯一配置入口 —— 模块不是包，理由写在它的 docstring 里
  schema.py         contracts 的导出者 —— 留在顶层，理由同上
contracts/          由模型导出的 JSON Schema（**单向：代码 → 契约**，不可手写）
profiles/<scene>/   业务规则：profile / grading / role-matrix
sources/            业务原始材料的**逐字节冻结快照**（不扫、不改，只追加）
tests/              自动验证（unit / contract / integration 的拆分见
                     docs/MVP_MIGRATION_PLAN.md，尚未执行）
deploy/             部署说明：只有文档，没有部署产物
docs/               架构、契约、SOP、来源与验收 —— 索引在 docs/README.md
```

`services/` 已经不存在。它最后剩下的是 `api/` 与 `feishu_bot/` 两个**只有 README
的占位目录**，本轮按 `marketops/interfaces/__init__.py` 里写下的处置删掉了 ——
留下的推理搬进了 `docs/NOT_BUILT.md`（为什么现在不建，以及真要建时哪些约束已经被固定）。

在现有模块上补 MCP、事务台账、Quay 研究衔接、Jev/Laya 适配与 Crawl4AI 服务连接。不为每个功能新建微服务，不把第三方源码复制进业务仓库。

## 数据、凭据和跨平台

运行数据不进源码仓库。`MARKETOPS_DATA_ROOT` 等逻辑配置决定 state、logs、mirrors、benchmarks 等位置；凭据使用部署允许的 secret 机制。目标机器实际路径不写死，部署前读取其最新治理文件。

原始网页/PDF、渲染 DOM、解析文本和派生摘要分别标注来源与变换，不互相冒充。并发写入走服务端事务/受控入库，JSONL 用于审计和导出，而不是无锁预算状态。

路径遵循既有 `pathlib` 和显式配置方式；第三方包/模型/浏览器版本固定并记录；不要为了演示复制源码、凭据或大模型缓存到不合规目录。

## 实现状态与历史缺口

当前 `main` 的研究引擎、开发机上的实验 worktree 与目标 MCP MVP 不应混称同一个完成状态。上一轮只读核查中报告了未实现的 API、旧 canonical remote、试跑预算计数竞争、研究 gate 与 SOP 口径差异。它们是当时的事实，不是本 README 对生产的实时检查。

本轮实现前核实活跃分支与当前代码，保留旧证据，另做版本化纠正。MVP 不应要求用户凭空编造 Account 清单；支持已存在来源 receipt、已知账户或批准的公开发现范围。

Crawl4AI、Jev、Laya 已升级为本轮必需集成，而不是后续选做；MinerU 等重型解析器、生产飞书/Hosting 接线和真实外联仍在各自独立范围。完整验收以 `docs/MCP_SERVICE.md` 的逐项真实凭证为准。
