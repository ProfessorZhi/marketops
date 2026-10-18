# Research v1 验收：2026-09-24

这一份回答一个问题，而且只回答这一个：

> **第一层 Research v1 是否真的可使用？**

**否。** `NOT_READY_FOR_REAL_USE`。理由不是它跑不起来 —— 它跑起来了，而且链上
大多数环节今天是真的。理由是：**验收判据要求的那条端到端路径走不完**，而走不完
的地方恰好都在「产品自己生产」这一侧，不在「操作者替它做决定」这一侧。下面每一条
都是本次测量出来的读数，不是判断。

## 这份文件不是什么

- **不是** `docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md` 的修订。那一份是 `e31f4e0`
  上的构建收据，本文件是 `d1e9d73` 上的另一次测量。两份并列，谁也不覆盖谁。
- **不是**对 `docs/MVP_ACCEPTANCE_REPORT.md` 的修订。那一份是冻结记录，本文件
  **不改它的任何一行**，也不把里面任何一条 FAIL 读成 PASS。
- **不是**质量评级。本文件**不给合格率**：这一轮没有独立的人工标注者，所以凡是要
  人给标签的指标一律记 `UNAVAILABLE`，不折算成百分比。
- **不是**部署证书。**生产没有上线**，两个阻塞还挂着（见 `docs/DEPLOYMENT.md`
  的 `### Production rollout: BLOCKED`）。

## 时间口径

测量发生在 **2026-09-24T10:47–11:10 UTC**，即 **2026-09-24 18:47–19:10 本地
（UTC+8）**。文件按本地日期命名。内文时间戳取 UTC（与库、账本、备份名一致），
唯一用本地时间的是下面 `run-demo` 那一段墙钟 —— 它逐字取自当时的终端记录。

---

## A. 被测构建与装置

| 项 | 值 |
|---|---|
| 仓库 | `ProfessorZhi/marketops` |
| 检出 | `/home/operator/workspace/worktrees/marketops/impl-20260923`（worktree，非 canonical checkout） |
| 分支 | `marketops/product-loop-20260923` |
| **被测构建（git SHA）** | `d1e9d73e75cf22251b6db512979bbbbfe4350ce6` |
| 测量时工作树是否脏 | **否**。`git status --porcelain` 输出为空 |
| Python | 3.12.3 |
| 解释器 | `/home/operator/toolchains/marketops-mvp/bin/python` |
| httpx / pydantic | `0.28.1` / `2.13.5` |
| 传输 | Streamable HTTP，绑定 `http://127.0.0.1:8796/mcp` |
| 认证 | bearer 令牌，`TokenRing` 格式 3，受众 `http://127.0.0.1:8796/mcp` |
| 辅助判定后端 | `MARKETOPS_DECISION_PROVIDER=jev`（Laya 未启用） |
| 检索提供方 | Serper（键从 vault 读入，未落盘、未打印） |

**装置是隔离的。** 数据根 `/home/operator/artifacts/marketops-acc-16/run`、令牌环
`.../vault/marketops-mcp-tokens.json`、Quay 工作区 `.../quay`、端口 8796 —— 四样都
是本次验收现铸的，与 8787 上那个部署没有共用一件。`8787` / `11000` / `11001` 上
跑着的服务不是本装置起的，本装置没碰它们。

**装置本身出过一处故障，已修，读数保留。** Quay 的 `driver start` 一度报
`Error: spawn node ENOENT`。成因是本装置的工作区比生产那份**深一层**，于是
`.quay/config.yml` 里 `providers.native.path`（`../quay-cli-build/quay/packages/quay-native`，
**相对工作区根**）解析不到。修法是为本装置补一条符号链接，不是改代码。

---

## B. CI

在被测构建上跑的三件事，**全部在本机实测**：

| 判据 | 读数 | 退出码 |
|---|---|---|
| 全量测试（Python 3.12.3） | `4183 passed, 3 skipped in 183.03s (0:03:03)` | 0 |
| `ruff check .` | `All checks passed!` | 0 |
| 契约同步 | `python -m marketops.schema` 之后 `git status --porcelain contracts/` **为空** | 0 |

那 3 条 skip 不是被跳过的判据，是**结构上构造不出来**的那一侧：`tests/test_evidence_store_contract.py:541`
说的是内存实现的一行就是一个模型实例，而模型上那一格必填。跳过理由逐字记在
pytest 的 short summary 里。

**GitHub Actions**（`b222fb1`，`https://github.com/ProfessorZhi/marketops/actions/runs/35991832516`）：

| job | 结论 |
|---|---|
| `test (3.11)` | `success` |
| `test (3.12)` | `success` |

**干净克隆 `.[dev]`**（本机实测，不是 CI 的副产物）：

```
git clone --branch marketops/product-loop-20260923 https://github.com/ProfessorZhi/marketops.git
uv venv --python /usr/bin/python3.12 <检出之外的空目录>
uv pip install -e ".[dev]"        # mcp / httpx 0.28.1 / pydantic 2.13.5 都在
python -m pytest -o addopts="" -q -rs   →  4171 passed, 15 skipped in 164.04s
python -m ruff check .                  →  All checks passed!
python -m marketops.schema; git status --porcelain contracts/  →  空
```

那 15 条 skip 全部是 `tests/test_sources_snapshot.py` 的上游对照（「没有可用的
`upstream-records` 检出」）—— 这是**刻意**的：本仓必须能在没有上游检出的地方跑完
（CI 里有一条断言 `test ! -d ../upstream-records` 专门钉住这件事）。所以干净克隆的
`4171 passed` 不是比工作树的 `4183 passed` 少跑了什么，是那 13 条上游对照在这里
按设计 skip。

### 一处测量装置造成的假红，与它暴露的真问题

第一次干净克隆时我把 venv 建在**检出里面**（`repo/.venv`），套件转红一条：

```
tests/test_no_stale_assumptions.py:203
  .venv/lib/python3.12/site-packages/_pytest/_py/path.py:644  [盘符绝对路径 ……]
  .venv/lib/python3.12/site-packages/mcp/cli/claude.py:136     [盘符绝对路径 ……]
  …（共 37 条，全部来自第三方 site-packages）
```

**这一条是我造成的**：venv 是我的装置，第三方包的注释不是本仓的文字。把 venv 移到
检出之外，同一份字节转绿。CI 不撞它，因为 CI 装进 runner 自己的 python，检出里
没有 `.venv`。

**但它底下有一个真问题。** `.gitignore:19` 忽略 `.venv/`，而
`tests/test_no_stale_assumptions.py:102` 的 `_iter_scanned_files` **不查 git 的忽略
规则**，只跳过 `SKIPPED_DIRECTORIES` 里那几个名字。所以「在检出里建一个标准名字的
虚拟环境」—— 一件完全正常的开发动作 —— 会让这条断言对第三方包的注释报错。
扫的应该是一份**受版本控制的内容**，不是**坐在那儿的一切**。本文件记下它，不修它：
它不在 Research v1 的判据上。

---

## C. 真实使用验收（§16 的读数）

### C.1 逐项

| §16 判据 | 读数 | 结论 |
|---|---|---|
| Scene 生命周期真的持久化 | `get_maintenance_plan` 报 `state=active`、`ticking=true`、`accepts_new_work=true`、`effective_version=1`；7 条 refresh 规则全部 `calibrated: false` | **真** |
| 来源发现可重复 | run `acc16-devon-20260924-20260924T104957Z`：计划 542 条检索式、发出 20 条、1 条 `budget_exhausted`、521 条 `not_attempted`；200 命中 → 84 台主机 → 25 进池（`cap 25, dropped 0`） | **真** |
| SourceReceipt 形式化 | `receipt-09b9a327e4c518c0`，工件 `acc16-devon-20260924`，sha256 已落盘 | **真** |
| Asset 有真生产者与存储 | `venue_asset` **25 行**。但 `venue_type` / `surface` / `address` 全空、`evidence_count: 0`、`domain.verdict: "unknown"`，`name` 是**页面标题**（"Home"、"Booking and payment for your local sports facility \| Pitchbooking"） | **存储真，场地身份不真** |
| Account 有真生产者与存储 | 见 C.2 —— **两个 id 空间，交集为 0** | **否** |
| 责任关系形式化 | 契约在（`VenueAsset` / `ResponsibleEntity` / `AccountVenueRelation`），`account_venue_relation` **0 行** —— 全仓没有生产者 | **否（零实例）** |
| 联系方式研究真的走 Quay + Claude | 见 C.3：4 次尝试 / 4 件结算，`goal_met` ×4，证据 644 行，两个真 Claude worker 并发 | **真** |
| Jev 真的走产品路径 | `get_capabilities` 活体读数：`jev` `runtime_ready: true`、`laya` `runtime_ready: false`（blocker 逐字：「本部署的辅助后端是 'jev'，decide 只咨询它」）。而 `decision_record` **0 行** —— 本批次没有一件工作去问过判定 | **接线真，未被走过** |
| ContactRoute / RouteOpportunity 可审计 | `contact_route` **4 行**（`grade` D×3 + X×1，X 那条 `has_conflict: true`）、`route_opportunity` **248 行**；4 条路线的 `research_only: true` / `send_authorized: false` 逐条成立 | **真（4 条样本）** |
| 新鲜度 / 到期 / 刷新真的生效 | 每条路线的 `freshness.currentness: "unknown"`、`reason: "no_time_anchor"`、`policy_calibrated: false`、`policy_version: "uncalibrated-v1"` | **机制真，值得出的答案是「判不了」** |
| 第二轮维护证明的是增量而不是重跑 | 第 1 轮 `due_count 25 / claimed 25 / done 25`；第 2 轮 `due_count 25 / claimed 0 / done 0 / not_claimed 25` | **真** |
| Draft 有证据根据、可追溯 | **没有产出**。`draft_outreach` 需要一个模板族，而 `put_family` / `TemplateFamily(` 全仓没有产品写入点；且没有一条 route 落在被研究的那些主体上 | **否** |

### C.2 Account：两个 id 空间，交集为 0

这是本轮最重的一条读数，而且它不是判断，是两个数字：

```
market_account 表的 account_id           25 个   （slug 形，如 client15.example.org）
研究作业 25 个工作单元的主体 account_id   25 个   （row-<16 位十六进制>，如 row-e97e74867653bfa0）
两者的交集                                 0 个
```

- `row-*` 这一侧**是产品自己生产的**：`submit_research(input_kind="discovery_scope",
  input_ref="acc16-devon-20260924")` 让产品去读那份来源回执，取 `resolved` 的行，
  逐行造出主体（`marketops/application/inputs.py:278`），`account_id` 用的是那一行的
  `RowDisposition.row_id`。回执里 25 行 → 25 个工作单元，`work_item_snapshot` 25 行，
  一行为证。
- 那个 slug 形的 25 行**是操作者写的**：一份手写的账号清单
  （`run/inputs/acc16-devon-20260924.yaml`）经 `remember_accounts` 进池。
- 产品**没有**从回执主体到账号池的桥。`market_account` 的唯一写入者是
  `remember_accounts`，而它是调用方交行的工具 —— 交什么写什么。

后果是具体的，不是抽象的：一个真 Claude worker 研究了 `row-e97e74867653bfa0`
（域名 `client15.example.org`），在该 id 下落了 13 行证据；而账号池里那同一家
`client15.example.org` 的 9 行证据是另一条路写的。**两条读数谁也走不到谁** ——
`get_account("client15.example.org")` 的 `evidence_count` 是 0，而它的证据就在库里，
只是挂在另一个键下。`get_account("row-e97e74867653bfa0")` 不是一个账号。

同一域名下的两个身份，正是 SOP 那句「不能把来源标识当账号身份」要挡的形状；
`inputs.py:278` 的注释专门论证过 `row_id` 不是来源标识。注释是对的，缺的是
**从它到账号身份的那一步**，而那一步今天不存在。

### C.3 真实 Claude 研究员：走 Quay，读数完整

装置：Quay `0.11.0-dev` 的 `driver`（`driver start --kind worker --root <本装置工作区>`），
`cap: 2`，`interval_ms: 10000`。令牌环是本次现铸的格式 3 环，研究员身份 2 个。

| 读数 | 值 |
|---|---|
| 派发轮次 | `worker-round.jsonl` 11 轮 |
| 尝试 / 结算 | `job_attempt` 4 行 / `job_settlement` 4 行 |
| 停止理由 | 4 件全部 `goal_met` |
| 墙钟（按件） | 99.2 s、120.6 s，另两件同量级 |
| 证据行增量 | 597 → 644（本轮新增 47 行落在 `row-*` 主体上） |
| 结算产出 | 逐条 `observations` / `candidates` / `gaps`，每条 `statement` 中文书写、逐条挂 `evidence_refs` |

结算产出**不是**走过场。举例，`row-c4b5a4057be2e143`（client12.example.org）
那一份的 `gaps` 逐字写着「站内没有任何 grounds / facilities / maintenance 职能的
联系人」，并说明「页面只显示它在第三方场地办赛……没有一句涉及所有权、运营、维护或
预订权归属」；`row-6d4063c7c4164fb6`（example.co.uk）那一份把
Wix 模板残留（`info@example.co.uk` / `123-456-7890`）**明确判为不得采信**。这是研究
该有的样子。

**引用的存在性校验是真的、但它的范围是全库，不分账号。**
`submit_findings` 在结算前调 `require_evidence_refs`（`marketops/application/evidence_refs.py:98`），
逐条 `store.get_evidence(ref) is None` 地查，查不到就**整份拒绝**。所以一条编造的
引用过不去。但校验问的是「这个库里有没有这一行」，不是「这一行是不是关于这个账号的」
—— 所以 C.2 那条错位，**这道闸门拦不住**。

**Quay 那一侧的判词不是关于研究质量的。** 两件已结算的研究工作 `exit_code: 0`，
而 driver 把两件都判成：

```json
{"task": "MOPS-8f8b7a94-feb008fe", "exit_code": 0,
 "final_state": "exited-not-landed",
 "failure_reason": "worktree state unreadable (git worktree list failed)",
 "wall_clock_ms": 99204}
```

随后还记了一次有界重试：

```json
{"task": "MOPS-8f8b7a94-feb008fe", "kind": "count-and-retry",
 "verdict": "insufficient-data-fallback",
 "reason": "first unattributable suite red for this task — one bounded retry allowed"}
```

一件研究任务被判了「**套件红了**」。研究侧既没有 git worktree，也没有套件 ——
driver 把它的**软件**扇入机制（合并 develop → 类型检查 → scoped 门 → 套件 → ff）
套在了研究工作上，于是每一件都会得到同一个「红」。这不是研究失败，是**两种工作
单元共用了一个 driver**。本轮到此为止，未继续追。

### C.4 有界性与花费

| 项 | 值 |
|---|---|
| 检索额度 | `usage: [{line: "search", ceiling: 20, spent: 20, reserved: 0, refunded: 0}]` —— 上限来自 profile 的声明，不是环境变量 |
| 金额成本 | **`UNAVAILABLE`** —— 本仓没有价目表，而「不知道价钱」不得写成 0 |
| 抓取额度 | 这一轮抓取**不受额度账本约束**（MVP 只扣检索）。有界性来自三个结构上的界：检索式条数、每条检索式的 `limit`、进池子的 `max_candidates` |
| 停止原因 | `["budget_exhausted", "cap_reached"]` —— 「预算用尽」与「达到上限」分别记着，没有合成一句 |
| 没搜 / 没取 | 521 条检索式没发出（额度用尽）、41 台主机没去取（池子在上限处满了）。这两件事与「搜了没结果 / 取过没读到」在报告里**是分开的** |

### C.5 墙钟与一次中止

| 段 | 墙钟 |
|---|---|
| 来源发现（含 25 次抓取） | ≈ 85 s（其中检索窗口 28.0 s） |
| `run-demo`（CLI，25 账号） | 本地 18:54:41 → 19:03:14 = **8 分 33 秒**，然后中止 |

中止的原因是一页**未来日期**：

```
pydantic_core.ValidationError: 1 validation error for TemporalAssertion
  source_published_at (2026-09-28T00:00:00+00:00) 晚于 observed_at (2026-09-24T11:03:10.174302+00:00)
  —— 不可能在它发布之前就观测到它。
```

出处：`client07.example.org`，证据 `d24db91d5eb22f20d1e088892d5c0e9d`。
判据本身是对的（`TemporalAssertion` 拒收未来发布日是对的），但它以
**未捕获异常**的形式打死整轮：`pipeline.py:349` → `freshness.py:214` →
`freshness.py:159`。后果是 25 个账号跑到中途全停、**没有报告落盘**，而盘上留下
半途状态（248 条 `route_opportunity`、4 条 `contact_route`）。一条坏日期能端掉
一整轮，这一条要单独修。

### C.6 CLI 的 `run-demo` 是路线的**唯一**生产者

全仓 grep 的结论：`ContactRoute` / `RouteOpportunity` 只在
`marketops/domain/bind.py:550` / `:706` 构造，唯一可达路径是
`pipeline.py` ← `ResearchPipeline` ← `interfaces/cli.py:238`（`run-demo`）。
**MCP 面上没有任何工具产出一条 route**；`get_account` / `list_accounts` 只读它们。

这正是 §16 那句「如果 Research v1 任何一个关键步骤仍然需要开发者手工改数据库 /
复制 CSV / 替 Agent 做判断，就不能给 `READY_FOR_REAL_USE`」所指的形状：
今天要拿到 route，操作者必须在 shell 里跑一条 CLI 动词，而不能在 MCP 面上完成。

---

## D. MCP 走查：从干净客户端连上，八步走到哪里

| # | §16 要求的那一步 | 结果 | 工具 |
|---|---|---|---|
| 1 | 建 Scene | ✅ | `create_scene` + `add_scene_version` |
| 2 | 跑研究 | ✅ | `submit_research`（作业主体由产品从来源回执派生） |
| 3 | 查资产 | ✅ | `list_assets` / `get_asset` |
| 4 | 查 Account | ⚠️ **半通** | `list_accounts` / `get_account` 只认 `market_account` 那个空间；刚跑完研究的 25 个 `row-*` 主体不在其中 |
| 5 | 查联系人 | ⚠️ 同 4 | 4 条路线全部落在手写清单那一侧 |
| 6 | 查证据 | ⚠️ **半通** | `get_evidence` 按 id 读得到；**不能**从账号走过去 |
| 7 | 跑维护 | ✅（有一处缺陷） | `run_maintenance` / `get_maintenance_plan` —— **默认参数会崩**（见 E.2） |
| 8 | 生成 Draft | ❌ | 无模板族生产者，且没有 route 落在被研究的主体上 |

第 4、5、6 步合起来就是 §16 那句「用户不得需要 SSH、手改 SQLite、搬 CSV」的反面：
**今天不需要 SSH，也没有人需要手改 SQLite** —— 但要「查 Account」，操作者得先
自己造出账号行（手写清单 + 派生脚本），因为产品那 25 个主体进不了池子。
**这一步是替产品做了判断**，不是操作便利性问题。

**危险工具面**：默认部署的线上目录里**没有**任何发送 / 回复工具。控制器令牌下
可见 28 个工具，研究员令牌下恰好 8 个（`crawl_site` / `decide` / `fetch_document` /
`get_assignment` / `get_decision` / `get_evidence` / `search_web` / `submit_findings`），
没有 `send` / `reply` / 任何外发动词。`research_only: true` / `send_authorized: false`
在能力表与**每一条** route 上逐条成立。

---

## E. 操作者侧：这一轮仍然要人做的地方

按 §16 的口径逐条列，**不评价它们该不该由人做**，只记它们今天确实是人在做：

### E.1 五处人做而不是产品做的事

1. **账号行由人手写**：25 行清单是操作者按 discovery 回执逐行判断后写下的 YAML
   （`run/inputs/acc16-devon-20260924.yaml`，头部注释逐字写明「这是一次人工判断的
   书面记录，不是产品的产物」，`cohort` 是操作者的判断）。
2. **账号 id 由脚本派生**：`derive-accounts.py` —— 该脚本自己的 docstring 写着
   「**这不是产品里的东西**……产品链上没有「回执/池子 → AccountClaim」的派生者」。
3. **路线只能从 CLI 拿**：见 C.6。
4. **维护动词要手填 `name`**：见 E.2。
5. **Draft 的模板族无处可写**：`put_family` 全仓没有产品写入点。

### E.2 本轮实测到的产品缺陷（五条）

1. **两个 id 空间无桥**（C.2）。最重的一条。
2. **`get_maintenance_plan` / `run_maintenance` 默认参数崩**：
   `name=""` → `check_canonical_id("")` 抛裸 `ValueError`，而调用点只包了
   `ConfigError`（`product_loop.py:2927` 一带），于是它以 `UnexpectedToolError`
   的形式出去，**消息是空的**。读回执的人看不出这是「参数缺了」还是「工具坏了」。
3. **一页未来日期打死整轮**（C.5）。
4. **`record_observation` 零调用点**、`put_signal` 只出现在测试里
   → `observation` / `change_signal` 两张表**没有产品生产者**（本次读数：各 0 行）。
5. **`submit_findings` 的引用校验不分账号**（C.3 末）—— 全库存在性而非账号归属。

---

## F. 人工抽查

完整表格在 `/home/operator/artifacts/marketops-acc-16/readings/70-spot-check.md`。
**抽查人：本次验收的执行者（Claude Code 会话），不是独立的人工标注者。**
因此下面**只记可核对的事实**，**不给合格率**；质量类指标一律 `UNAVAILABLE`。

25 行逐行看过之后，可核对的事实有：

- **5 行是美国的同名实体**，靠人名 `Devon` 匹配到的（`client41.example.org`、
  `client36.example.org`、`client02.example.org`、`client13.example.org`、`client22.example.org`）。
  这是检索式 `{role} {venue_type} {region}` 里 `{region}=Devon` 被当成 **人名** 读的
  形状，不是地区范围错了。
- **4 行是英格兰其他郡的议会**（Pendle / Fareham / Dundee / Sheffield），在 Devon 之外。
- **5 行不是组织页**：`facebook.com`（一条帖子）、`client28.example.org`（SaaS 产品页）、
  `client40.example.org`、`digitalmarketplace`（采购平台）、`client35.example.org`（新闻）。
- 看起来确实落在 Devon 这一侧的：`client15.example.org`、`example.co.uk`、
  `example.co.uk`、`client10.example.org`、`client12.example.org`、`client29.example.org`、
  `client09.example.org`、`client11.example.org`、`client07.example.org`。
- **`organisation` 这一格大面积是页面标题**而不是组织名，最极端的一条是
  `facebook-com` 的「If you're looking for Technical Coaching, why not get in touch…」
  —— 一句正文被当成了组织名。

**产品自己已经把这些缺口写进了它的 `caveats`**，逐字：

> 这一轮产出的候选行上，有 1 条准入规则**没判**（`out_of_region`）。「没判」与
> 「核对过、不命中」不是一件事……缺的是那些事实的生产者，不是这一轮的零件。

> 另有 3 格是空的而**不是**未确认：`organisation_kind`、`venue_environment`、
> `surface_class`。发现期没有做过那三件事的分类，而空值在规则方言里读作「记了，是空」
> —— 它会在报告里看起来像一条结论。

`qualifications` 的读数与这两段一致：`by_status {discovered: 25}`、
`by_reason {unresolved_facts: 25}`、`unchecked_rules ["out_of_region"]`。
**25 个候选没有一个是合格的**，而且理由被产品自己说清楚了 —— 这是诚实，不是通行。

---

## G. 结论

**第一层 Research v1 是否真的可使用？否。**

分两句话说，因为「真的」与「可用」不是同一件事：

**链上大多数环节今天是真的。** Scene 生命周期真的持久化；来源发现有界、可重复、
停止原因逐条分开记；来源回执形式化；资产的存储与生产者是真的；维护第二轮证明的
确实是增量（`claimed 0`，不是重跑）；**真实的 Claude 研究员真的经 Quay 走完了
产品路径** —— 4 件全部 `goal_met`，产出的证据可逐条回读，产出的结论逐条挂引用；
路线等级可审计（D×3 + X×1），且 `research_only` / `send_authorized` 的边界在每一条
上都成立。这些都不是纸面判据，是本次测量出来的行数与回执。

**但它还不可真实日常使用**，因为 §16 那条端到端路径在三个地方走不完，而三处都在
「产品自己生产」这一侧：

1. **产品自己派生的 25 个研究主体，进不了账号池** —— 两个 id 空间交集为 0，
   研究了却查不到。
2. **路线的唯一生产者是 CLI 动词**，MCP 面上一条也产不出；而这条 CLI 在遇到一页
   未来日期时会以未捕获异常打死整轮且不留报告。
3. **Draft 走不到** —— 没有模板族写入点，也没有 route 落在被研究的主体上。

按 §16 那句原话：「如果 Research v1 任何一个关键步骤仍然需要开发者手工改数据库 /
复制 CSV / **替 Agent 做判断**，就不能给 `READY_FOR_REAL_USE`」—— 今天要拿到一份
可查的账号池，操作者必须先自己写一份清单、再跑一个派生脚本。**那正是「替产品做
判断」。** 所以判词是 `NOT_READY_FOR_REAL_USE`，并且它的意思很具体：**不是「这个
东西是坏的」，是「这条链还差三块，而这三块都在产品侧」。**

### 与上次验收的关系

`docs/MVP_ACCEPTANCE_REPORT.md` 里那些 FAIL **今天依然是 FAIL**，本文件不动它们。
相对 `docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`（`e31f4e0`）的位移是：维护的
增量性、真实 Claude 研究员经 Quay 的完整读数、以及路线等级的样本，是**新证成的**；
而账号池的 id 空间错位与 Draft 的不可达，是**这次才量准的**。

---

## H. Remaining blockers

按「挡住 `READY_FOR_REAL_USE` 的强度」排，不按大小排：

| # | 阻塞 | 位置 | 判据 |
|---|---|---|---|
| 1 | 来源回执主体 → 账号池，**没有桥** | `application/inputs.py:278` 造主体；`market_account` 唯一写入者是 `remember_accounts` | C.2 的两个数字 |
| 2 | 路线只有 CLI 一条生产者 | `domain/bind.py:550` / `:706` ← `pipeline.py` ← `cli.py:238` | C.6 |
| 3 | 一页未来日期打死整轮、不留报告 | `pipeline.py:349` → `freshness.py:214` / `:159` | C.5 的 traceback 原文 |
| 4 | Draft 无可写模板族 | `put_family` / `TemplateFamily(` 无产品写入点 | C.1 末行 |
| 5 | 责任关系契约有、实例零 | `account_venue_relation` 0 行 | C.1 |
| 6 | `get_maintenance_plan` / `run_maintenance` 默认参数崩，且错误消息为空 | `product_loop.py:2927` 一带只包 `ConfigError` | E.2 |
| 7 | `observation` / `change_signal` 无产品生产者 | `record_observation` 零调用点 | E.2 |
| 8 | 产品的 25 个候选**一个都不合格**（`unresolved_facts`），缺的是三格事实的生产者 | `organisation_kind` / `venue_environment` / `surface_class` | F. 与产品自己的 `caveats` |
| 9 | 研究与软件的扇入机制共用一个 driver，研究任务被判「套件红了」 | `worker-outcome.jsonl` / `worker-round.jsonl` | C.3 末 |

## I. Deferred（`DEFERRED / NOT REQUIRED`，不计为 Research v1 的 FAIL）

按 §17：这些**不被继续扩张**，相关代码不删，但在本发布门里**不是判据**。

| 项 | 状态 |
|---|---|
| Laya | 未启用。`get_capabilities` 报 `laya runtime_ready: false`，blocker 逐字：「checkpoint 目录 '' 缺……缺任何一样 laya 都会去 HuggingFace 取，而本 adapter 不做隐式下载」。**未下载任何权重** |
| 影子 / 双模型对比 | 未做。`decide` 只以 **assist** 调用后端，影子路径**没有 MCP 入口** |
| ServerOS | 未接入，未修改 |
| Docker | 未使用。本次验收在宿主机上直接跑，不依赖容器 |
| 生产 SMTP / 生产 Feishu / 自动发信 / 自动跟进 / 全自动回复 / 销售 CRM | 未触碰。默认部署的线上工具面里没有这些动词 |
| 生产上线 | **`BLOCKED`**，两个阻塞未解（构建回执缺、研究员令牌需重铸而重铸要授权）。见 `docs/DEPLOYMENT.md` |

---

## J. 交付

- 分支 `marketops/product-loop-20260923`，**不自动 merge main**。
- 本文件所属的推送把 §15 的七个提交（`8c5c519` … `d1e9d73`）与本次验收的文档
  一起送上去，远端从 `8094122` 走到 `b222fb1`。
- PR #6（`OPEN` / `MERGEABLE`，base `main`）已按本次读数更新。

```text
CI（b222fb1 · Python 3.11）: success   runs/35991832516
CI（b222fb1 · Python 3.12）: success   runs/35991832516
ruff（b222fb1 · 两处实测）:  All checks passed!
contracts sync（b222fb1）:   与 committed 一致（工作树与干净克隆各测一次）
干净克隆 .[dev]（b222fb1）:   4171 passed, 15 skipped（skip 全是上游对照，见 B）
```

- 交付 SHA（`delivery_sha`）：**本文件是这个交付的一部分，所以它记不下自己的 SHA。**
  推送后由 `git ls-remote` 实测的 tip 写在 PR #6 的更新里。上面那一行 CI 读数记的是
  `b222fb1`（本文件的**正文**），本文件落地后的 tip 由 PR #6 记 —— 两个 SHA 分得开，
  因为它们要回答的确实是两个问题：「判据在哪一份字节上证成了」与「远端现在是什么」。
