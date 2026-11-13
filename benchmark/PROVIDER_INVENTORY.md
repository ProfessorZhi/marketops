# Provider Inventory（Phase 0）

本文回答一个问题：**在被测量的每一个能力上，代码里到底有什么，它有没有被调用，
有没有被验证过。**

判定依据只有仓库里的代码与测试，不是 README、不是设计文档、不是我的记忆。每一条结论
都给出可复核的路径。凡是"文档说有、代码里找不到"的，一律记为 **DOCUMENTED only**。

> 术语约定：**核心**（`marketops/`）与 **插件**（`plugins/research-tools/marketops-research-assistant/`）
> 是两套独立实现，各自有 provider。它们不共享代码，只共享配置文件的形状。基准必须分别
> 标注属于哪一套，否则"Serper 的延迟"会变成两个不同实现的两份数字。

---

## 1. 五个状态的判据

| 状态 | 判据 | 反例 |
|---|---|---|
| **DOCUMENTED** | 文档/注释/设计稿里描述了这个能力 | 只在 README 出现的 SerpApi 适配器 |
| **IMPLEMENTED** | 代码存在且自身逻辑完整，有单元测试 | 存在但从未被装配的 `GuardedHttpClient` |
| **WIRED** | 有真实调用路径：CLI、MCP 工具、应用层用例中的一个 | 只有测试调用的适配器 |
| **TESTED** | 离线测试覆盖其行为（不含网络） | 只在人工 smoke 里跑过一次 |
| **LIVE-VERIFIED** | 对真实端点发出过真实请求，并记录了结果 | 只读过文档的 `gl`/`hl` 语义 |

**这五格不是等级，是四个不同的问题。** 一个 provider 可以 IMPLEMENTED + TESTED 却
NOT WIRED（功能存在但没人用）；也可以 WIRED + LIVE-VERIFIED 却没有 TESTED（线上在跑，
但没有回归判据）。基准评分只对 WIRED 的 provider 有意义 —— 给一个没接线的适配器排名，
测的是我的想象力。

`LIVE-VERIFIED` 在本文里**只表示"凭据与链路通"**，不表示"质量已量过"。一次 200 回包
证明不了检索质量，正如下面 Serper 那一格会看到的。

---

## 2. 总览

| Provider | 层 | 能力 | DOC | IMPL | WIRED | TESTED | LIVE |
|---|---|---|---|---|---|---|---|
| Serper | 核心 | search | ✅ | ✅ | ✅ | ✅ | ⚠️ 仅链路 |
| Serper | 插件 | search / maps | ✅ | ✅ | ✅ | 🟡 | ⚠️ 仅链路 |
| SerpApi | 插件 | search / maps | ✅ | ✅ | ✅ | 🟡 | ⚠️ 仅额度 |
| DeepSeek | 插件 | search / fetch | ✅ | ✅ | ✅ | 🟡 | ⚠️ 仅链路 |
| TinyFish | 插件 | search / fetch | ✅ | ✅ | ✅ | 🟡 | ⚠️ 仅链路 |
| TinyFish Agent | 插件 | browse（付费） | ✅ | ✅ | 🟡 仅 opt-in | ❌ | ❌ |
| Crawl4AI | 核心 | fetch | ✅ | ✅ | ✅ | ✅ | ❌ |
| Crawl4AI | 插件 | fetch | ✅ | ✅ | ✅ | 🟡 | ❌ |
| HTTP（httpx） | 核心 | fetch | ✅ | ✅ | ✅ | ✅ | ❌ |
| HTTP（httpx） | 插件 | fetch | ✅ | ✅ | ✅ | 🟡 | ❌ |
| download | 插件 | 数据文件 | ✅ | ✅ | ✅ | 🟡 | ❌ |
| PDF（text_pdf / pypdf） | 核心 | parse | ✅ | ✅ | ✅ | ✅ | ❌ |
| Facebook 公开页 | 插件 | fetch | ✅ | ✅ | ✅ | 🟡 | ❌ |
| MinerU | 核心 | parse（兜底） | ✅ | ✅ | ⛔ 恒不可用 | ✅ | ❌ |
| GuardedHttpClient | 核心 | 受控取回 | ✅ | ✅ | ❌ 未装配 | ✅ | ❌ |
| Jev | 核心 | 判定 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Jev（grade） | 插件 | 评级 | ✅ | ✅ | ✅ | 🟡 | ❌ |
| Laya | 核心 | 判定 | ✅ | ✅ | 🟡 | ✅ | ❌ |

`✅` 有证据 · `🟡` 部分（见该 provider 的说明） · `❌` 没有 · `⚠️` 有但不足以支撑质量结论

**这张表最重要的信息不是对钩数量，是最后一列。** 整个仓库里，没有一个 provider 在
"检索质量 / 抓取质量"这个意义上被量过。Phase 1 的全部工作就是从零建立这套测量。

---

## 3. 逐项说明

### 3.1 Serper —— 核心适配器

| | |
|---|---|
| 实现 | `marketops/adapters/serper.py`，`SerperSearchProvider` |
| 端口 | `SearchProvider.search(query, limit) -> SearchResult` |
| 端点 | `https://google.serper.dev/search` |
| payload | **只有** `q` / `num` / `gl` / `hl` |

**它是 `SearchProvider` 的第一个实现，也是唯一"接进应用层"的核心检索适配器。**

- **DOCUMENTED**：模块 docstring 很长，其中有一段直接是实测缺口的自白 ——
  "没有对着真端点量过 `gl`/`hl` 的效果"。这句话本身就是一条 inventory 事实：
  **参数语义未经线上验证**。
- **IMPLEMENTED**：`_payload()` 是纯函数，`gl` 小写、`hl` 只取主语言子标签，两格
  各过一次转换而不是共用一个字符串。
- **WIRED**：经 `interfaces/composition.py` 装配成 `SearchProvider`。
- **TESTED**：payload 形状、状态码到 `AccessDiagnosis` 的映射都有离线判据。
- **LIVE-VERIFIED：仅链路。** 用真实 key 发出过真实请求并核对过额度账；但**没有**
  就 `gl`/`hl` 是否真的改变结果做过对照实验。

基准必须量的事：`gl`/`hl` 是否真的生效（同一 query 三国比较）、`num` 上限外的截断、
以及检索式里的 `site:` / `filetype:` 是否被支持（`SearchQuery.purpose` 根本不会到达
Serper —— 见 3.16）。

### 3.2 Serper / SerpApi / DeepSeek / TinyFish —— 插件四个检索引擎

| | |
|---|---|
| 实现 | `plugins/.../scripts/mo_providers.py`，`SEARCH_ENGINES = ("serper", "serpapi", "deepseek", "tinyfish")` |
| 路由 | `pick()` 按权重哈希把 provider 分成"现在打"和"压着"；答案弱（< 3 条）才把压着的叫起来 |
| 缓存 | `mo_library`，键 `[engine, query, country, lang, n, maps(, page, near)]`，TTL 14 天 |

**四个引擎的参数并不对称，这是基准里最容易被误判成"能力差"的东西。** 逐条对着代码：

| 参数 | serper | serpapi | deepseek | tinyfish |
|---|---|---|---|---|
| `n`（条数） | 发送 | 发送（maps 不发送） | 夹到 1–50 | **丢弃** |
| `country` | `gl` 小写 | `gl` 小写 | **丢弃** | `location` 大写 |
| `lang` | `hl` | `hl` | 折进 query 做 `lang:xx`（128 字符内才加） | `language` |
| `purpose`（意图） | **丢弃** | **丢弃** | `ori_question` | `purpose` |
| `page` | `page` | `num*(page-1)` / maps `20*(page-1)` | 忽略 | `page-1`，上限 9 |
| `maps` | 打 `/places` | 打 `engine=google_maps` | **返回 `[]`，不发请求** | **返回 `[]`，不发请求** |

两处必须写进标准的后果：

1. **TinyFish 根本不接受 `n`。** 给它 `n=25` 与 `n=10` 是同一个请求。以"返回条数"作为
   成本或质量指标时，这一格必须标注为"引擎不支持"，不能记成"只返回了 10 条"。
2. **maps + deepseek/tinyfish 会静默记一次成功。** `_deepseek`/`_tinyfish` 在
   `maps=True` 时直接 `return []`，外层 `search()` 不以异常区分，于是
   `mo_budget.record(..., "search", True, ...)` 照样记一条 ok。**任何以 ledger 统计
   成功率的基准都会把这算成"成功但零结果"** —— 零结果就是它们的真实行为，但那不是一次
   调用。评分器必须以"有没有发生 HTTP 请求"为准，不能以 ledger 为准。

- **TESTED：部分。** 各函数的 payload 形状有测试；`pick()` 的确定性有测试；
  **没有任何测试断言"deepseek 的 maps 分支不发请求"**，这条目前只有代码事实。
- **LIVE-VERIFIED：仅额度/链路。** SerpApi 两个账号已确认 0/250（额度用尽，置旁），
  第三个 250/250；Serper 额度账已核对。**没有一次调用是为了量质量而发出的。**

### 3.3 DeepSeek —— 插件里的检索 + 抓取

- **端点**：`<base_url>/v1/tools/search` 与 `/v1/tools/fetch`（`mo_config.base_url("deepseek")`）。
- **计费**：`PRICES[("deepseek","search"|"fetch")] = 0.00075` USD/次（`mo_budget.py`）。
- **它的结果自带 `rerank_score`**，`_deepseek` 在返回前按它排序
  （`sorted(rows, key=-score)`）。这条很重要：**它的第 1 名是厂商自己排的，不是我的
  合并顺序**。基准记录原始顺序时必须记这份厂商顺序，不能记合并后的顺序。

### 3.4 TinyFish —— 插件里的检索 + 抓取 + 付费 Agent

- **检索/抓取**：`PRICES` 记为 `0.0`（免费额度），`RATE.wait("tinyfish:search")` 做
  全机限速（默认 30 次/分钟）。
- **付费 Agent（browse）**：`PRICES[("tinyfish","agent")] = 0.30` 是**列表估值**，而工具
  自己的契约是**按步计费（约 0.016/步，单次上限 40 步）**。两者不是一回事，
  成本表里必须标注口径，不能直接相加。
- **状态**：DOC + IMPL + WIRED（仅在 `paid_agent=True` 时），**TESTED ❌，LIVE ❌**。
  它的输出形状是异步任务 + 轮询，基准需要单独一条"每个 URL 只提交一次、结果落成 page"
  的记录路径，目前没有。

### 3.5 Crawl4AI —— 核心与插件各一份

- 核心：`marketops/adapters/crawl4ai_fetcher.py`，`Crawl4AIBrowserFetcher`，
  `CacheMode.BYPASS`（永不读缓存，每次都真渲染）。
- 插件：`READERS` 里的 `crawl4ai`，`PRICES` 记 `0.0`（本机浏览器，成本是 CPU 不是钱）。
- **两套实现的差别就是基准要量的东西**：核心版保留渲染后 HTML（供证据链），插件版
  更关心正文与联系人。
- **LIVE-VERIFIED ❌**：仓库内没有任何一份"这个页面浏览器读出来是什么"的留档。

### 3.6 HTTP（httpx）—— 核心 `HttpxFetcher` 与插件 `http`

- 核心：`marketops/adapters/http.py`。
- 插件：`_READ` 里的 `http`，**`PRICES` 里没有它的条目**（`_cost` 落到 0）。
- 它是"零成本基线"：任何付费 reader 如果打不过纯 httpx，就没有理由付钱。基准必须
  把它作为**必测臂**，而不是可选臂。

### 3.7 download —— 插件里的数据文件读取

- `READERS="download"`，CSV/JSON/XLSX/PDF 导出走它。`PRICES` 无条目（0）。
- 它的价值不在"读得好"，在**"别的 reader 根本做不了"**：一个 XLSX 导出的开放数据，
  四个 reader 都会返回一个壳页。这类用例在 fetch 层要能单独标出来，否则会被算进
  "抓取失败率"，把 provider 的能力差异抹平。

### 3.8 PDF —— 核心 `text_pdf.py`（pypdf）

- **DOC + IMPL + WIRED + TESTED**：`TextPdfParser`，有 `ParsedDocument` 契约测试。
- **扫描件（无文字层）没有任何适配器能处理。** MinerU 是唯一的兜底，而它恒不可用（3.9）。
  所以 `pdf_scanned` 这一类 fetch 用例，**当前整套系统是必败的** —— 基准要把它单列，
  不能混进 PDF 总体成功率。
- **LIVE ❌**：仓库里没有一份真实 PDF 的解析留档。pilot 数据集里放了一条有真实文字层的
  公开 PDF（`FECH-FR-PDFTEXT-008`），就是为了在 Phase 2 首次建立这条基线。

### 3.9 MinerU —— 恒不可用的兜底

| | |
|---|---|
| 实现 | `marketops/adapters/mineru_adapter.py` |
| 关键事实 | `_CALL_POINT_CALIBRATED = False`（模块级常量） |
| 后果 | `available()` 恒为假；`parse()` 在未标定时直接放弃 |

- **DOC + IMPL + TESTED**（测试通过 monkeypatch 把它打开来验分支） + **WIRED**（装配在
  文档路由里） + **永远不生效**。
- 这不是 bug，是一个**刻意的诚实标记**：调用点没有对着真实 MinerU 校准过，于是它
  宣称不可用，而不是在运行时给一个没验证过的结果。
- 基准的含义：**任何"PDF 兜底率"指标在当前代码上恒为 0，且这个 0 是有意为之。**

### 3.10 Facebook 公开页 —— 插件 `_READ` 里的 `facebook`

- `PRICES` 无条目。它的存在是为了"公开主页能读、登录墙要识别成 no_content"。
- **TESTED 🟡 / LIVE ❌。** pilot 数据集里放了两条登录墙用例（`FECH-GB-LOGIN-018/019`），
  期望 `no_content` 并列出禁止出现的登录文案 —— 一个把登录页当成功读取的 reader 会在
  这两条上被打成假成功。

### 3.11 GuardedHttpClient —— 存在、被测试、没有接线

| | |
|---|---|
| 实现 | `marketops/adapters/guarded_http.py`，`GuardedHttpClient` |
| 测试 | `tests/test_wave1_egress_guarded_http.py`（对抗判据），`tests/test_wave1_egress_approved_url.py` |
| 装配 | **没有任何生产模块 import 它** |

`grep` 全仓的结果：除了它自己的文件、它的两个测试、以及 `egress_proxy.py` 里三处
docstring 提到它，**没有任何一处 import**。

- **DOC + IMPL + TESTED，NOT WIRED。**
- 基准里的处理：**不把它列为 fetch 的一个臂。** 一个没有调用路径的实现没有延迟、没有
  成功率、没有覆盖率 —— 给它打分等于给设计稿打分。它在 inventory 里出现，是为了让
  "为什么 fetch 只有三个臂"这件事有据可查。

### 3.12 Jev —— 核心判定与插件评级

- 核心：`marketops/adapters/jev.py`，`JevDecisionProvider`，走 `typesafe_sdk` +
  `TYPESAFE_API_KEY`。它是**决策**适配器（`DecisionProvider`），不是检索也不是抓取。
- 插件：`PRICES[("jev","grade")] = 0.0005`，用于给邮箱候选评级。
- **对基准的意义：它是 Phase 4 的 judge 候选，但同时也是"业务链路本身"的一部分。**
  标准里那条"打分用的 Jev 不能与生成标签的 Jev 是同一个"，正落在这里：同一个模型既
  产出候选又给自己打分，指标会系统性偏高。

### 3.13 Laya —— 第二个决策适配器

- `marketops/adapters/laya.py`。**DOC + IMPL + TESTED，WIRED 🟡**（装配存在，但不在
  Phase 1 的测量面上）。
- 它和 Jev 的关系是"同一个端口的两个实现"，基准若要比较判定器，必须先固定输入样本
  （`run_shadow` 做的正是这件事，见 3.15）。

### 3.14 `run_shadow` —— 有实现，没有入口

- `marketops/application/shadow.py::run_shadow`：用冻结样本 + 多个 arm 跑影子对比，
  显式**没有** `mode` 形参（恒以 `DecisionMode.SHADOW` 调用）。
- **IMPLEMENTED + TESTED，NO CLI / MCP 入口**：全仓只有 `tests/test_shadow.py` 调用它。
- 与基准的关系：它是仓库里**唯一已有的"A/B 对比"机制**，但它比较的是**决策器**，
  不是检索/抓取 provider，且没有分数化输出。基准不改造它，只在文档里指明这条边界
  —— 否则读者会以为"影子对比已经在做了"。

### 3.15 检索缓存：两套，口径不同

| 层 | 实现 | 键 | 命中时 |
|---|---|---|---|
| 核心 Serper | 无缓存路径 | — | `SearchResult.from_cache` 恒为 `False` |
| 插件 | `mo_library.get_search` | `[engine, query, country, lang, n, maps(, page, near)]` | 直接返回，**不记 ledger** |

**这是基准公平性的一条硬约束**：插件层第二次问同一个问题会免费命中，ledger 里看不到
那条调用，延迟也接近 0。用 ledger 算"冷启动成本"会系统性偏低，用一次到位的延迟算
p95 会把缓存命中混进真实调用。标准里因此要求**每次测量记录 `from_cache` 与
`cache_age`，并在报告里分冷/热两张表**。

### 3.16 已知缺陷清单（会被误读成能力差异的东西）

按"如果不写在标准里，就一定会被算错"来收：

| # | 事实 | 会被误读成 |
|---|---|---|
| 1 | `SearchQuery.purpose` 不存在于核心端口，插件的 `purpose` 被两个 Google 引擎丢弃 | "Serper 不懂意图，所以查不准" |
| 2 | TinyFish 丢弃 `n` | "TinyFish 只能返回 10 条" |
| 3 | DeepSeek 丢弃 `country` | "DeepSeek 不会做地域检索" |
| 4 | maps + deepseek/tinyfish 返回 `[]` 但记一次成功 | ledger 成功率虚高 |
| 5 | 插件检索缓存命中不记 ledger | 成本/延迟偏低 |
| 6 | DeepSeek 结果按厂商 `rerank_score` 重排 | 与其它引擎的"原始顺序"不可直接比 |
| 7 | Core Serper 无缓存，插件层有缓存 | 同一个名字两个延迟分布 |
| 8 | MinerU 恒不可用 | PDF 兜底率恒 0 |
| 9 | `GuardedHttpClient` 未接线 | "有四个 fetch 臂"，实际三个 |
| 10 | 扫描件 PDF 无任何适配器 | "PDF 成功率低"（其实这一类必败） |

---

## 4. 对基准的直接影响

1. **被测集合要先判 WIRED。** 未接线的实现（`GuardedHttpClient`）、恒不可用的实现
   （MinerU）、无入口的实现（`run_shadow`）不进评分表，只进 inventory 与"缺口"一节。
2. **必须有无成本基线。** `http`（httpx）与 `crawl4ai` 在每一层都要各有一条臂，否则
   "付费 provider 值不值"这个问题无法回答。
3. **参数不支持要标 `n/a`，不能记失败。** 一个引擎不接受 `n`，它在这一格没有观测值。
4. **冷/热缓存分表。** 见 3.15。
5. **"成功"以 HTTP 请求为准，不以 ledger 为准。** 见 3.2 第 2 条。
6. **必败类别单列。** `pdf_scanned`、登录墙、登录后页面，在当前代码上是结构性必败，
   单独报告才有信息量。

---

## 5. 这张清单没有覆盖的

- **生产环境的 provider 配置**（哪个 key 在哪个槽位、额度还剩多少）：属于运行期状态，
  每 6 小时就可能变（`slots.json` 置旁），不写进仓库。基准记录的是**调用时的快照**，
  在 manifest 里，不在本文。
- **东京节点上的历史会话语料**：按本阶段禁令未访问。它可能含有"真实业务里 provider
  怎么用的"证据，但那属于 Phase 2 之后、需要单独授权的主题。
- **`marketops-session` 仓库**：只允许看工作模式，不看内容。本文未引用其任何数据。
