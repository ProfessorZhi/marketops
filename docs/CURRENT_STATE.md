# CURRENT_STATE — MarketOps MVP 起点盘点

```yaml
status:     Phase A 交付物 1/2（§1 要求的「先盘点」）
revision:   origin/main @ 9a4fe4e（分支 marketops/mvp）
method:     读实现 + 读测试 + 读 git history，不按目录名推断
```

> 本文是 **projection**，不是 authority（`docs/SOURCES.md` §4）。
> 业务口径以 `sources/` 冻结快照为准，架构以实际代码为准。
> 每一个判断都带 `file:line`，可以被复核。**没有一条来自目录名猜测。**

---

## 0. 先纠正一个错误基线（我自己记错过）

| | |
|---|---|
| 我先前记录 | 「361 passed / 1 failed」 |
| **自足基线（正确）** | **1536 passed + 3 documented skips / 1539 collected，零失败**（2026-09-22 §2.19 之后） |
| 本机为何曾多一条失败 | `test_sources_snapshot.py::test_upstream_blob_hashes_match` 读的是**磁盘上隔壁 records 检出的工作树字节**，不读 pin |

**2026-09-22 更新（§2.19）**：这一条**是**代码缺陷，而且已经修掉。当时记的
「这不是代码缺陷，是测试的一个真实局限」只对了一半 —— 它确实是「读磁盘而不读 pin」，
但那不是一个可以就这么记下来的局限：它让这条判据在**别人未提交的 WIP** 上转红，
而 records 的工作树按本仓规范**可以一直长那样**。更要紧的是同一片里发现的另一半：
那三条上游对照按本仓规范调用**从来没跑过**（一个环境变量兼两个决定），所以
上面这个「局限」从来没人撞见 —— 只有裸跑套件时才撞见。

修法：上游对照有自己的变量 `MARKETOPS_UPSTREAM_RECORDS_ROOT`，三条判据改读**对象库**
（`rev-parse <commit>:<path>` + `cat-file blob`）。现在规范调用下它们**真的跑**，
`3 skipped` 全是 `test_business_sources.py` 的 dossier 交叉核对。

机制（实测，仍然成立）：

- 声明的 `upstream_blob_sha` = `85ac620f…`，在 `sources/manifest.yaml:102`
- `git rev-parse b81eb776:<path>` = `85ac620f…` —— **声明的值在该 pin 上是对的**
- 隔壁 records 检出的工作树比 pin **新且未提交**（`member-d-private` 那条）

**不得**去改 manifest（那会破坏该 pin 上正确的 provenance），也**不得**去动 records
的工作树（那是别人的 WIP）。六条条目的声明实测**全部**等于
`rev-parse <commit>:<upstream_path>`。

---

## 1. 仓库是什么

`ProfessorZhi/marketops` —— 把一份**英国天然草坪划线漆**的销售 SOP
（三人群通道里的 docx）编译成**可执行、可审计的公开信息研究管线**。

它现在**不是**一个服务，是一个**库 + 一个 CLI**：

| 入口 | 位置 | 能做什么 |
|---|---|---|
| `marketops` console script | `pyproject.toml:32` → `services/worker/cli.py:main` | `check-config` / `check-deps` / `export-contracts` / `run-demo --dry-run` |
| `python -m marketops.schema` | `marketops/schema.py:85` | 从模型重生成 `contracts/*.schema.json` |

`services/api/` 与 `services/feishu_bot/` **各只有 1 行 docstring**，是刻意留空的位置。

---

## 2. 每个模块真正在做什么（实现程度）

图例：**真实** = 有生产导入者且逻辑完整；**契约-only** = 类型与校验齐全但调用点恒失败；
**死代码** = 零生产导入者。

### 2.1 顶层

| 模块 | 行 | 做什么 | 程度 |
|---|---|---|---|
| `enums.py` | 157 | 22 个 `str,Enum` + 4 个 `frozenset` 不变量（`BINDING_ELIGIBLE_SOURCES:69` 等） | 真实，21 处置入 |
| `facts.py` | 235 | 事实词表 + 子句方言解析（`fact` / `!fact` / `fact==v` / `fact in [..]`） | 真实 |
| `util.py` | 89 | `slugify` / `check_slug_safe` / `sha256_text` / `sha256_file` / `normalise_email` | 真实 |
| `errors.py` | 101 | 9 个异常 | **6 个在用**；`FetchError:22` `GradingError:46` **无 raise 无 catch** |
| `config.py` | 337 | **唯一读 `os.environ` 的模块**（`_raw:36`）；`research_only`/`send_authorized` 是**常量属性**，无 env 通路（`_RESEARCH_ONLY:28`） | 真实 |
| `profiles.py` | 920 | 三份 YAML → dataclass，装载期 fail-closed 校验 | 真实，**全仓最重** |
| `schema.py` | 89 | 10 个模型 → `contracts/*.schema.json`（**单向：代码 → 契约**） | 真实 |

### 2.2 六层能力

| 层 | 模块 | 做什么 | 程度 |
|---|---|---|---|
| 检索 | `search/base.py` | `SearchQuery`/`SearchHit`/`SearchResult`/抽象 `SearchProvider` | 契约 |
| | `search/serper.py` | 真实 POST `https://google.serper.dev/search:23`，原始响应落盘 | **真实** |
| | `search/queries.py` | `build_queries` —— 只用 5 条通用模板，**`bundle` 参数收下后不用**（`:8-11,37`） | 真实但**未接线** |
| | `search/fake.py` | `FakeSearchProvider` | **死代码：零导入者，连测试都不用** |
| 抓取 | `acquisition/base.py` | `FetchRequest`/`FetchResult`（15 字段）/抽象 `Fetcher` | 契约 |
| | `acquisition/classify.py` | 纯函数：块分类（WAF→403→429→401→paywall→login **顺序敏感**） | 真实，升级判定的唯一入口 |
| | `acquisition/escalation.py` | `decide` 纯决策，5 条优先级规则 | 真实 |
| | `acquisition/http.py` | `HttpxFetcher`，内容寻址落盘 | **真实，但只有 `cli.py:117` 一个导入者** |
| | `acquisition/crawl4ai_fetcher.py` | `Crawl4AIBrowserFetcher`，`asyncio.run` 包裹 | 真实，**可选依赖**（`try/except:29`） |
| 文档 | `documents/router.py` | primary → fallback 有序尝试 | 真实 |
| | `documents/text_pdf.py` | HTML 块解析 + pypdf（空白页比 ≥0.7 判扫描件抛错） | 真实 |
| | `documents/mineru_adapter.py` | **`_run_mineru:100` 恒抛 `ParserUnavailableError`** | **契约-only** |
| 抽取绑定 | `entities/extract.py` | 纯正则：邮箱/混淆邮箱（**只登记不解码**）/具名角色（**子串匹配**） | 真实 |
| | `entities/bind.py` | **全项目最重要模块**：四条不变量 + `_classify_email`（**词元精确匹配**）+ 记录块层级 P1–P4 | 真实 |
| 判定 | `grading/engine.py` | `grade_route` / `grade_account` —— **两条互不决定的轴** | 真实 |
| 存储 | `evidence/store.py` | 抽象 `EvidenceStore` + `LocalEvidenceStore`（JSONL）+ `InMemoryEvidenceStore` | 真实 |
| 编排 | `orchestration/pipeline.py` | 唯一的组装点，全部依赖经 `PipelineDeps:62` 注入 | 真实 |
| | `orchestration/metrics.py` | `AccountMetrics`(23 字段) + `RunReport` | 真实 |
| 集成 | `integrations/feishu/*` | 两张字段映射表 + 客户端骨架 | **接口-only：`ADAPTER_STATE="interface_only":25`，唯一导入者是测试** |

---

## 3. 重复与死材料

| 对象 | 判定 | 证据 |
|---|---|---|
| `marketops/search/fake.py` | **死代码** | 零导入者；测试自建 `test_pipeline.py:65 RecordingSearch` |
| `marketops/integrations/feishu/` 整包 | **接口-only** | 唯一导入者 `tests/test_feishu_adapter.py:9`；`client.py:85` 的 `wired` 分支是 `raise NotImplementedError` |
| `VenueAsset` / `ResponsibleEntity` / `AccountVenueRelation` | **契约层死模型** | 只在 `schema.py` + `tests/test_models_invariants.py` 出现；**零生产者** |
| `ResearchRun` | **从未被构造** | 只在 `schema.py:36`、`evidence/__init__.py:11` 出现 → **run 级元数据不落盘** |
| `ParseAttempt`（`documents/base.py:44`） | 导出未用 | 无构造、无消费 |
| `build_run_record`（`pipeline.py:302`） | 零调用 | —— |
| `FetchError` / `GradingError` / `AccessBlockedError` | 死异常类 | 无 raise 无 catch；`AccessBlockedError` 只活在 `acquisition/base.py:75` 的 docstring 里 |

**没有两份真实实现。** 重复是「契约有、实现无」而非「实现有两份」。
唯一形态相近的是 `FakeSearchProvider` 与测试内自建的两套 fake —— 但它们都不进生产路径。

---

## 4. Provider 集成状态

| Provider | 功能？ | Mock？ | 缺 key 时 |
|---|---|---|---|
| **Serper** | **是**，真实 POST + `raise_for_status:58` + 原始响应落盘 `:87` | 无 | `ConfigError:37`；`cli.py:109` 直接 `return 2` |
| **Crawl4AI** | **是**（作为 fetcher），每次全新匿名 context，`cache_mode="BYPASS"` | 无 | `available=False`，`fetch` 抛 `ParserUnavailableError:69` |
| **pypdf** | **是**，扫描件按空白页比 0.7 判负抛 `ParseError:116` | 无 | —— |
| **MinerU** | **否，恒抛** | —— | `mineru_available()` 可能为真但 `_run_mineru` 恒抛（**潜在地雷 #10**） |
| **Feishu** | **否**，接口 + 字段映射 | —— | 未配置 → `feishu_not_configured`；已配置 → `adapter_not_wired` |
| **Jev / Laya** | **不存在** | —— | 全仓无 LLM/决策 provider SDK，无 `openai`/`anthropic` 字样 |

---

## 5. `tests/` 到底在保护什么（§1 的关键问题）

19 个 `test_*.py`，5808 行，310 个测试函数。**分三类**：

### (i) 真正保护业务规则的 —— 不许动，改坏了就是产品语义被改坏

| 文件 | 保护的不变量 |
|---|---|
| `test_bind.py` | SERP snippet 永不产出路线/机会/证据；无 literal 邮箱不是路线；**排除类别优先于入口性质**（否则 `info@` 会把财务洗白） |
| `test_grading.py` | **两轴互不决定**（`grade_route` vs `grade_account`，`:331-357`）；规则求不了值 → `X` + 原因码，**不猜** |
| `test_pipeline.py` | 端到端（零网络、全 fake、读真实 profile）；**每条路线的每个 `evidence_id` 必须落盘** |
| `test_profiles.py` | A/B/C/D 条件逐条断言 + **18 个 mutation 必须 `ConfigError`**（:539-638） |
| `test_extract.py` | 只做字面抽取：不猜邮箱、**不解码混淆邮箱**、无标签姓名不猜 |
| `test_classify.py` | 7 个 `FetchOutcome` 分支各有正反例；`WAF_CHALLENGE` **优先于** `LOGIN_REQUIRED` |
| `test_cohorts.py` | Demo 队列 10/10/10；出厂夹具为空 |

### (ii) 治理层 —— 重构时最容易误伤

| 文件 | 扫什么 | 陷阱 |
|---|---|---|
| `test_no_evasion.py` | 14 个禁用模块 + 16 条禁用行为（**大小写无关行子串**） | 裸词 `stealth`/`undetected`/`persistent_context`/`user_data_dir`；**docstring 不豁免**，只有 `#` 行豁免 |
| `test_no_hardcoded_market_knowledge.py` | `marketops/**.py` 的 16 个市场词 | **标识符先 `_`→空格再子串匹配** → `venue_manager` / `natural_grass_verified` / `muga_like` 都会红 |
| `test_no_stale_assumptions.py` | 整仓 `.md`/`.py`/`.yaml`… | 纯行正则，**docstring/注释不豁免**；禁用：Windows 盘符前缀、两台旧机器的 home/opt 根、已拆分的旧函数名、旧 demo 名（**连本文档都不能写出这四种字面形态** —— 写的时候踩过一次） |
| `test_business_sources.py` | `source_ref` 合法性 | `docs/` 里的 `MKT-\d{4,}` 必须在 `docs/SOURCES.md` §6 索引过 |
| `test_portability.py` | 机器绝对路径 | 只扫 `marketops`/`services`/`tests` 的 `.py`；扫不到 `.gitignore:35` 的 Windows 残留 |
| `test_contracts_sync.py` | 契约与模型一致（15 个用例） | 改任何 pydantic 字段 → 必须重生成并提交 JSON |
| `test_sources_snapshot.py` | 快照与 manifest 自洽 + **3 条可选上游对照** | 自足 9 条**永远跑**；上游 3 条读**对象库**（`rev-parse <commit>:<path>` + `cat-file blob`），规范调用下也跑，缺检出时 skip（§2.19） |

### (iii) 与当前仓库状态耦合的脚手架（状态一变就红）

`test_pipeline.py:509` 断言真实 profile 的排除规则**当下仍求不了值**；
`test_feishu_adapter.py:102` 断言 `ADAPTER_STATE == "interface_only"`；
`test_config.py:148` 断言 `repr(settings)` **确实**泄露 secret（固定一个已知缺陷）。

**没有典型的历史实验残留**，但有**弱断言**（重构时别当保证）：
`test_bind.py:111` / `:120`、`test_escalation.py:32/55/66`、`test_store.py:186`（`pytest.raises(Exception)` 未指定类型）。

---

## 6. 归档与运行数据（§18 的问题：有没有不该进仓的东西）

**结论：基本干净。** 全仓**没有** `artifacts/`、`runtime/`、`browser-profile/`、`model-cache/`、`*.db`。
唯一进仓的「非代码材料」是 `sources/`（372 KB），它**受 manifest 治理且有哈希锚定**，是刻意的。

`sources/` 实测：**0 个邮箱地址**、无 `password`/`Bearer`/真实 key
（`sk-` 的 3 处命中是文件名 `task-inventory` 的子串）。
`sources/member-d-private/marketops-messages.jsonl` **0 字节** —— 这是**查过的结果，不是遗漏**
（`sources/README.md` §9：零字节比写一条「我检查过了」的合成记录更诚实）。

运行数据全部在 `MARKETOPS_DATA_ROOT` 之下（`config.py:124-166`），**不在仓内**。

**唯一残留**：`.gitignore:35` 注释里的 `%LOCALAPPDATA%\FengjiangKnowledge\marketops\`（Windows 主机路径），
`test_portability.py:27` 只扫 `.py` 所以扫不到。

---

## 7. 持久化现状（**与需求冲突最严重的一处**）

**没有任何数据库**（`sqlite3` 全仓 **0 处**）。全部是 JSONL 追加 + 一次性 `write_text`。

| 落点 | 代码 | 写入方式 |
|---|---|---|
| 证据 | `store.py:76,103` | 追加 |
| 路线 / 路线机会 | `store.py:84,92` | 追加（**两个独立集合**） |
| 页面镜像 | `http.py:118-121` | `if not exists` 才写（**检查与写非原子**） |
| 渲染镜像 | `crawl4ai_fetcher.py:139-143` | **无条件覆盖** |
| SERP 原始响应 | `serper.py:84-87` | **无条件覆盖** |
| run 指标 | `cli.py:145-148` | 一次性 `write_text` |

**事务 / 锁 / fsync：全无。** 无 `fcntl`、无 `flock`、无 `threading.Lock`、无 `os.replace`。

`store.py:106-119` 的幂等检查是「**读全文件 + 再追加**」，无锁：

```python
existing = {row.get(key) for row in LocalEvidenceStore._read(path) if isinstance(row, dict)}
if payload.get(key) in existing:
    return
with path.open("a", ...) as handle:      # ← 检查与写之间可以插入另一个进程
```

而仓库**自己的**架构文档写着：

> `docs/MCP_SERVICE.md:79` — *"No unlocked read-modify-write JSON counters.
> The existing pilot demonstrated 83 counter units versus 106 fetch records under concurrency."*

**这是当前代码与自家架构要求的直接冲突**，且 Kent 那一轮的 83 vs 106 就是这个 bug 的实测后果。

---

## 8. MCP 代码：**零**

```bash
grep -rni "mcp|fastmcp|model context protocol" --include="*.py"   # → 0 命中
```

无 MCP server/client 代码、无 MCP 依赖、`pyproject.toml` 里没有 `mcp`。

MCP **只活在文档层**（10 个文件提及）。`docs/MCP_SERVICE.md:3` 自己写着：

> *"implementation proposal… The tools below are NOT deployed claims"*

`docs/MCP_SERVICE.md:37-69` 的 7 个 control tools + 5 个 worker tools 是**提案表格，不是实现**。

HEAD 的 commit message 是 `docs: complete MarketOps architecture and Claude-first MCP MVP` ——
这里的 "MCP MVP" 指**那份文档切片**，没有一行代码落地。

**所以 §6 的「MarketOps MCP 必须真的实现」是从零开始，不是接线。**

---

## 9. Quay 调研 POC 当前怎么接（2026-09-21 Kent 轮实测）

**结论：Quay 不需要 fork。它的扩展面是纯增量的。**

| 事实 | 证据 |
|---|---|
| `bin/quay` 是 **bash shim**，不是 node | 直接 `node bin/quay` 会 SyntaxError |
| 驱动在 `scripts/dist/*.js` | `worker-driver` / `promotion-driver` / `driver-anchor` / `driver-runtime` / `loop-complete-task` |
| 扩展点是**增量**的 | `probes/` / `gate-scripts/` / `agents/` / `skills/` / `workflows/` / `freshness-producers.json` / `invariant-ownership.md` |
| Kent 轮**零源码改动**跑通 | 只用 `.quay/config.yml` + `profiles.yml` launcher override |
| 落地判据 | `develop:tasks/<id>.md` status == `done` ∧ 无残留 `task/<id>` worktree |
| 调度器是**语义选择器** | `selectorReason` 靠推理产物存在性，**不强制 `depends_on`** |
| `cmdlineFingerprint` 记的是**启动器之前的 argv** | 读起来与真正跑的命令相反 |

Kent 轮结果：13 个任务 / 16 次派发（12 落地 + 4 `exited-not-landed`）/ 峰值并发 5（进程窗口）
vs 4（台账活动窗口）/ wall 27m03s。

---

## 10. 包与导入依赖图（实际测出来的）

**没有环。** 方向自上而下：

```
services/worker/cli.py
  └→ orchestration/pipeline.py          ← 唯一的组装点
       ├→ grading · entities · evidence · acquisition · documents · search
       └→ {enums, facts, util, errors, config}     ← 叶子
```

**两处反向感**（重构的真正理由）：

| 反向边 | 后果 |
|---|---|
| `evidence/store.py:19 → entities.models` | 存储层依赖实体模型 |
| **`grading/engine.py:53 → profiles.py:35,37 → config.py:14,36`** | **判定层直接 import 配置装载，后者 import `yaml` + 读 `os.environ`。所以「纯判定」只在本文件成立** |

另有一处：`search.serper` / `acquisition.http` / `acquisition.crawl4ai_fetcher`
的**唯一导入者是 `cli.py`** —— pipeline 只见接口。这是好结构，应保留。

---

## 11. 业务口径的当前状态（§4/§5 相关）

**A/B/C/D 的两轴模型已经在 main 上落地**（`docs/SOP_MAPPING.md` §4.2 记录）：

| 轴 | 函数 | 产出 |
|---|---|---|
| 路线等级 | `grade_route` | `ContactRoute.grade ∈ {A,B,C,D,X}` |
| 账号状态 | `grade_account` | `AccountStatus` |

**两者互不决定**。有具名角色但拿不到字面邮箱 → `RouteOpportunity`，**该类型没有 `email` 字段**
（`contracts/route-opportunity.schema.json` 实测 props=14，无 `email` 无 `grade`，
`block_priority` default `P3`）。**这是 §4 要求的正确语义，已经在了。**

**B 不是「有具名角色但没有邮箱」** —— 旧实现的错误读法已经修掉，B 与 A 一样要求真实公开业务邮箱。

**两个真实缺口**：

1. **账号准入引用的事实没有生产者**：`x_rules` 6 条**全部**编译出了可求值的
   `requires`（装载期没有欠账，`ungradeable` 为空），但其中 5 条引用的是
   **候选事实** —— 载体是发现期的候选行，账号行上还没有那些字段，引擎手上是
   `None`（未确认）→ 这 5 条落 `unchecked`。它们**不排除**任何账号
   （没有证据就丢弃账号，比留着它危险得多）。**两条清单不要读混**：
   `ungradeable` 答「引擎不认识哪个事实名」（空），`AccountOutcome.unchecked`
   答「引擎认识、今天还没有值」（非空）。候选行 → 事实名的**投影**今天已经写了
   （`marketops/application/qualify.py::candidate_facts`，与账号侧共用同一份求值
   循环），缺的是候选行自己的**值**：本轮还没有代码把抓取结果填进
   `AccountCandidate`。投影接上之后 `unchecked` 的含义从「这条路没接线」变成
   「这一行还没有证据」—— 缺口的位置移了，**没有关闭**。
   **但账号并非永远不被排除**：第 6 条 `out_of_region` 引用的是 `AccountRecord`
   自己的字段 `country`，一条声明了 `US` 的记录**真的**会被判成 `irrelevant` ——
   由 `tests/test_grading.py` 里那条真实 profile 的活体判据执行，它的正对照
   正是这一半。而一条**没写**国家的记录落 `unchecked`（缺省曾是 `"UK"`，
   那会让这条取反规则静默不触发，报告读成「在区域内」）。
2. **SOP L79 的 Procurement 归口缺口**：`grading.yaml:101 known_gaps` 记着
   「强绑定具名采购角色 + literal 邮箱」四条件都不满足 → 落到 `X + RULES_UNKNOWN`。
   **刻意留着**（在引擎里替 SOP 补规则会让 profile 与原文分叉）。

**§5 的 temporal/freshness 模型：完全不存在。** `Evidence` 有 `fetched_at`，
但**没有** `source_published_at` / `last_verified_at` / `currentness` / `supersedes` / `contradicted_by`。
一条 2021 年的 PDF 与一条 2026 年的页面在数据上没有区别 —— 这正是 §5 举的例子。

---

## 12. 十二处地雷（重构前必须知道）

| # | 位置 | 事实 |
|---|---|---|
| 1 | `acquisition/http.py:74-77` | `raw = response.content[:http_max_bytes]`（默认 8 MiB）后**把截断字节写进镜像**；`body_sha256` 是对截断文本算的；但 `Evidence` 不存 `reason` → **截断在证据层不可见** |
| 2 | ~~`pipeline.py` `_discover`~~ | **已闭。** 候选 URL 现在经 `application/caps.py::apply_cap` 切，被砍掉的条目数落进 `metrics.candidate_urls_dropped` —— 从「记切后值」变成「切后值 + 切掉的差」。原状：一行 `min(len(urls), cap)` 加一行切片，切掉多少一个字都没留下 |
| 3 | `documents/text_pdf.py:137` | `path.read_bytes()` 整份入内存，无上限 |
| 4 | `evidence/store.py:122-138` | 每次追加前**全量读**该账号 JSONL；任何一行坏 JSON 整个文件失败（`:134`，刻意） |
| 5 | `evidence/store.py:106-119`、`http.py:122`、`crawl4ai_fetcher.py:143`、`serper.py:87`、`cli.py:146` | **非原子写 / 无锁**（见 §7） |
| 6 | `pipeline.py:187-189` | 「SERP snippet 不产路线/机会/证据」这条**全项目最重要的不变量由三条 `assert` 承担** → `python -O` 下被剥离 |
| 7 | `.gitignore:35` | Windows 主机路径残留，`test_portability.py:27` 扫不到 |
| 8 | `grading/engine.py:53` | 域纯净的传递破口（见 §10） |
| 9 | `entities/bind.py:338-339` | `name_evidence if single and len(name_evidence)==1 else []` —— 文档里 1 个邮箱 + 2 个岗位 → **所有**岗位证据不参与绑定，路线落 `unbound_email`。**这个「必须恰好 1 个」的阈值没写进契约或文档** |
| 10 | `documents/mineru_adapter.py:34` vs `:100` | `mineru_available()` 可能为真而 `_run_mineru` **恒抛** |
| 11 | `pipeline.py:89-95`、`:302` | `ResearchRun` 从未被构造 → 运行级元数据只存在于 `cli.py:145` 打印的 benchmark JSON |
| 12 | `tests/fixtures/accounts.yaml:22` | `accounts: []` —— `run-demo` 无真实账号可跑（README 已列为已知缺口） |

**编号是身份，不是状态。** 已经修掉的条目**不删号**（别处的散文按号引用它），
改为划掉位置并写明用什么闭的。一张把已修项继续列为地雷的表，和一个漏报的地雷
一样坏：读者会去修一件已经不存在的事，而表格因此不再可信 —— 所以上面第 2 条
现在两件事都写了：现状是什么、原状是什么。

---

## 13. 一句话总结

**骨架完整且自洽，判定口径已经是对的；缺的是「服务化」与「运行时」这整整一层。**

具体地，四类缺口：

- **(a) 没有 MCP** —— §6 的全部要求要从零实现
- **(b) 没有数据库/原子预算** —— §8 的全部要求要从零实现，且**现有 JSONL 与自家架构文档明确冲突**
- **(c) 没有决策 provider** —— §11 的 Jev/Laya、§12 的 shadow/assist 模式完全不存在
- **(d) 没有 temporal/freshness** —— §5 的模型完全不存在

外加一个**结构性**问题：`grading → profiles → config(os.environ)` 这条传递链
使得「域纯净」只在单文件内成立，这正是 §2 的分层要求要修的东西。
