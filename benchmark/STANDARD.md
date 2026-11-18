# MarketOps Search & Fetch Benchmark Standard v1

**状态**：v1，Phase 1 交付。**未做任何真实收费调用。** 本文定义的每一个指标都可以在
离线状态下由已记录的数据算出；本文不包含任何 provider 的排名。

---

## 0. 这份标准解决什么

"哪个搜索 API 更好"这个问题在没有标准时是答不了的，因为大家各自在比不同的东西：

- 比价格，却不比"每 100 条相关结果多少钱"；
- 比延迟，却把缓存命中混进冷启动；
- 比条数，而某个引擎根本不接受 `num`；
- 比准确率，用的却是同一个模型生成的标签，于是模型的自洽被当成事实。

本标准把"更好"拆成三层，每层给出可复算的公式、可拒绝的判据，以及**明确不参与评分
的东西**。它的目标不是得出一个总冠军，而是让任何人在拿到同一份记录后算出同一张表，
并且知道这张表回答不了什么。

### 0.1 三条不可协商的原则

1. **可复现**：任何分数必须能由"记录的原始响应 + 数据集 + 版本号"重新算出。凡是需要
   现场再调一次 API 才能得出的结论，不进评分表。
2. **可拒绝**：每个指标都要说明它在什么情况下不该被采信（样本量、退化、必败类别）。
   一个没有拒绝条件的高分是噪声。
3. **不合并语义与确定量**：确定性指标（precision、延迟、成本）与语义判断（相关性、
   证据质量）**分块报告，永不加权成一个总分**。理由见 §7。

### 0.2 不在本标准的范围内

- 真实外联、回复监控、销售交接（`research_only` 边界之外）。
- 任何一家的定价谈判、合规结论、采购决策。
- "哪个 provider 最好"的结论。本标准只定义怎么量，不预设谁该赢。

---

## 1. 被测对象

### 1.1 三个层

| 层 | 被测单位 | 问题 | 判据来源 |
|---|---|---|---|
| **A. Provider 层** | 单个 provider 的单个调用 | 这家在这一类任务上召回/精度/成本如何？ | 确定性指标 |
| **B. Ensemble 层** | 若干 provider 的组合与消融 | 组合比最好单家多拿到什么？去掉谁会让组合变差？ | 集合运算（并集/差集） |
| **C. 端到端层** | 一条完整业务链路（检索 → 抓取 → 抽取 → 验证） | 交付物对不对、有没有证据、每条要花多少钱？ | 确定性指标 + 抽样人工 |

三层**不能互相替代**：A 层赢的 provider 在 C 层可能因为抓取失败而交不出东西；
B 层的并集数字**天然高于**任何单家，把它和单家并列比较是错的（见 §4.3）。

### 1.2 被测能力的清单

| 能力 | 层 | 必测臂 |
|---|---|---|
| 网页检索 | A / B | serper、serpapi、deepseek、tinyfish |
| 页面抓取 | A / C | http（零成本基线）、crawl4ai、deepseek、tinyfish、download |
| 文档解析 | A / C | text_pdf（pypdf）、HTML 解析；MinerU 仅作缺口记录 |
| 端到端链路 | C | 至少 3 种组合，见 §4.4 |

**必测臂的定义**：零成本基线（`http`、`crawl4ai`）必须在每一层出现。一个付费 provider
如果打不过纯 httpx，那它买的不是能力。把基线省掉，任何"付费 provider 更好"的结论都
没有分母。

---

## 2. 数据集的规范

### 2.1 三类用例

| 类型 | 单位 | schema |
|---|---|---|
| search | 一条检索式 + 金标 URL 集合 | `benchmark/schemas/search-case.schema.json` |
| fetch | 一个 URL + 期望的页面性质 | `benchmark/schemas/fetch-case.schema.json` |
| e2e | 一句业务目标 + 金标实体/联系人 | `benchmark/schemas/e2e-case.schema.json` |

schema 是 **JSON Schema 2020-12**，可被任何标准校验器验证；runner 在没有 `jsonschema`
时退化为结构检查（`models._structural_check`），两者的必填/枚举/类型判据一致。

### 2.2 分层（split）

| split | 用途 | 规则 |
|---|---|---|
| `development` | 调参数、调 prompt、试 provider | 可以反复看 |
| `validation` | 选型决策 | 只看汇总，不看逐条 |
| `holdout` | 最终结论 | **在写结论之前不许打开逐条结果** |

三个 split 的用例**互不重叠**（`models.Dataset.duplicate_ids()` 会报重复），且
**同一实体不出现在两个 split 里**。否则"holdout 上表现好"只是记住了 development。

### 2.3 金标（ground truth）

- 金标**必须由人确认**（`label_status: "human_verified"`）。`draft` 的用例可以参与
  开发，但**不得进入结论表**。
- 金标**不得由参与打分的同一个模型生成**。理由：那个模型的自洽会被当作事实，
  且误差在"生成"与"打分"两侧同向，无法被任何校准发现。
- 联系方式（邮箱/电话/地址）只有在**打开过印着它的那个页面**之后才能进金标，并且要
  记 `evidence_url`。没有证据地址的联系人不是金标，是猜测。

详见 [`GROUND_TRUTH_GUIDE.md`](GROUND_TRUTH_GUIDE.md)。

### 2.4 市场

| 市场 | 优先级 | 说明 |
|---|---|---|
| FR | 最高 | 法语、法域、`.fr` 为主，本地目录/开放数据是主战场 |
| GB | 高 | 英语、英域、registry 类目录（Companies House / Charity Commission） |
| US | 中 | 英语、联邦 + 州 + 市三级开放数据 |

**跨市场比较必须同一条检索式**（`SearchQuery.identity = country/language/text`），
因为 URL 集合随市场变化，"法国找得到、美国找不到"可能只是那句法语在美国索引里没有
对应页面。三国都有的任务，每个市场各算各的，不做跨国平均（否则法语用例的权重会淹没
英语用例）。

### 2.5 pilot 规模

24 search / 28 fetch / 10 e2e，FR 12/10/4 · GB 7/9/3 · US 5/9/3。
全部为 `draft`，等人类确认后才升级。规模只够**建立方法与判据**，不够支撑 provider 排名
—— 这一点写在这里，是为了防止有人拿 pilot 的数字当结论。

---

## 3. 指标定义

所有指标在 `benchmark/runner/metrics.py` 里是可单独调用、无 I/O 的纯函数，并有离线测试。
**算不出来的一律记 `null`，不记 0。** `null` 与 0 的区别是"没观测到"和"观测到是零"。

### 3.1 URL 归一化（先于一切指标）

```
scheme 强制 https，host 去 www.，去尾部 /，去跟踪参数，去 fragment
```

不归一化时，`http://www.x.com/a/` 与 `https://x.com/a` 会被当成两个结果，于是
precision 偏低、unique recall 偏高，两个方向的偏差同时出现且互相掩盖。

### 3.2 Search 层指标

记 $G$ 为金标相关 URL 集合（相关性 ≥ 1），$R_k$ 为前 $k$ 条结果里命中 $G$ 的集合。

| 指标 | 公式 | 拒绝条件 |
|---|---|---|
| `precision_at_k` | $\lvert R_k \rvert / k$ | 返回条数 < k 时**分母仍为 k**（缺条就是缺条） |
| `recall_at_k` | $\lvert R_k \rvert / \lvert G \rvert$ | $\lvert G \rvert = 0$ → `null` |
| `mrr` | $1 / \text{第一个命中位置}$，未命中为 0 | 无金标 → `null` |
| `ndcg_at_k` | $\sum (2^{g_i}-1)/\log_2(i+1)$ 归一化 | 无金标 → `null` |
| `unique_recall` | 只有该 provider 找到的相关 URL / 全部相关 URL | 单臂运行时 = 0，无意义 |
| `official_source_rate` | 命中结果里属于官方域的比例 | 用例未标 `is_official` → `null` |
| `domain_diversity` | 前 k 条里不同主域数 / k | — |
| `duplicate_rate` | 归一化后重复 URL 数 / 返回条数 | — |
| `market_language_accuracy` | 结果页面语言符合市场的比例 | 无语言判据 → `null` |
| `freshness_pass_rate` | 满足 `max_age_days` 的比例 | 无时间戳 → `null` |
| `latency_p50 / p95` | 最近秩百分位 | 样本 < 5 时 p95 不采信 |
| `cost_per_100_relevant_usd` | 总成本 / 命中相关数 × 100 | 命中数为 0 → `null` |

三点必须写清楚：

1. **`precision_at_k` 的分母是 $k$，不是返回条数。** 否则一个只返回 3 条却全对的引擎
   会得到 1.0，而返回 10 条对了 6 条的得到 0.6 —— 前者是"没答完"，不是"更准"。
2. **`unique_recall` 在单臂运行时恒为 0**，它只在 B 层有意义（"这家独有贡献"）。
3. **`cost_per_100_relevant_usd` 是唯一可以直接横向比的成本口径。** 每千次调用价格
   不可比，因为各家的"相关"产出率不同。

### 3.3 Fetch 层指标

| 指标 | 定义 | 拒绝条件 |
|---|---|---|
| `http_success_rate` | `ok=true` 的比例 | — |
| `useful_content_rate` | `ok` 且**非壳页** | — |
| `shell_page_false_success_rate` | `ok=true` 但被判为壳页的比例 | **这是最危险的一格** |
| `text_coverage` | 抽出正文字符数 / 金标 `min_text_chars`（封顶 1.0） | 金标未给 → `null` |
| `content_completeness` | 金标 `expected_text_markers` 命中的比例 | 无 marker → `null` |
| `structured_extraction_accuracy` | 结构化字段（地址/电话/名称）对上的比例 | 无结构化金标 → `null` |
| `email_precision / recall` | 基于金标 `expected_emails` 的集合 P/R | **两侧都为空 → `null`，不是 1.0** |
| `phone_precision / recall` | 同上（7–15 位数字） | 同上 |
| `contact_person_accuracy` | 人名校验通过率 | 无金标 `expected_persons` → `null` |
| `link_retention` | 抽出链接 ∩ 金标 `expected_links` / 金标链接数 | 无金标链接 → `null` |
| `mailto_retention` | mailto 链接被保留的比例 | 页面无 mailto → `null` |
| `js_coverage` | `requires_js` 用例的通过率 | 该类用例数为 0 → `null` |
| `pdf_coverage` | PDF 用例的通过率 | 同上 |
| `latency_p50 / p95` | 最近秩百分位 | — |
| `cost_per_successful_page_usd` | 总成本 / 有效页数 | 有效页为 0 → `null` |

**壳页检测**是 fetch 层真正的分水岭。一个只拿到 `<div id="root"></div>` 的 reader
`ok=true`、状态码 200、文本非空，所有朴素指标都判它成功。判据：

- 正文（去标签、去脚本/样式/导航）字符数低于用例的 `min_text_chars`；
- 或命中了金标 `forbidden_strings`（登录墙文案）；
- 或页面的可交互骨架比正文占比更高（SPA 空壳）。

`shell_page_false_success_rate` 必须单独报告，**不能折进成功率**。它的意义是：
付了钱买回来一个空壳，比直接失败更坏，因为下游会把它当证据。

### 3.4 端到端（E2E）指标

| 指标 | 定义 |
|---|---|
| `entity_precision` | 交付实体里正确的 / 交付实体总数 |
| `entity_recall` | 正确交付的 / 金标实体总数 |
| `verified_contact_precision` | 有 `evidence_url` 且页面真的印着它的 / 交付联系人数 |
| `verified_contact_recall` | 同上 / 金标联系人数（金标为空 → `null`） |
| `correct_entity_attribution` | 联系人被挂到正确实体上的比例 |
| `source_attribution_accuracy` | 交付的来源 URL 真的支持该结论的比例 |
| `evidence_completeness` | 每个结论都带可打开证据的比例 |
| `false_positive_rate` | 交付了但金标里没有、且无法人工确认的 / 交付总数 |
| `duplicate_entity_rate` | 同一实体重复出现的比例 |
| `task_completion_rate` | 达到用例 `acceptance` 的比例 |
| `cost_per_verified_entity_usd` | 总成本 / 已验证实体数 |
| `cost_per_verified_contact_usd` | 总成本 / 已验证联系人数 |

**实体匹配顺序**（`metrics.e2e_scores`）：`entity_id` → `official_url` 归一化相等 →
名称归一化相等。三者都不中即不匹配。**联系人必须带 `evidence_url` 才计入**——
没有证据的联系人在 precision 里算错，而不是算"待核"。待核是一种状态，不是一种正确。

---

## 4. 公平的测试协议

### 4.1 同条件

| 条件 | 规则 |
|---|---|
| 检索式 | 逐字相同（含标点、大小写） |
| 国家 / 语言 | 同一对 `country`/`language`，记录实际发送的参数 |
| top-k | 同一 $k$；**引擎不支持 $k$ 时记 `n/a`，不记失败** |
| 时间窗 | 同一轮内完成；跨天必须分批并记录批次 |
| 账号 | 每个 provider 固定账号，记录额度快照 |
| 客户端 | 同一 MarketOps 版本、同一 Python、同一网络出口 |

### 4.2 记录原始响应

每次调用落一份**原样响应**（不改字段名、不删字段、不重排），另有 runner 侧的归一化
结果。归一化是**派生数据**，可以重算；原始响应才是证据。

记录的元数据至少包含：`latency_ms`、`http_status`、`cost_usd`、`from_cache`、
`cache_age_s`、实际发送的参数、引擎返回的条数。

### 4.3 缓存：冷/热必须分表

| | 冷 | 热 |
|---|---|---|
| 定义 | 该 `identity` 在本轮首次出现 | 本轮内已出现过 |
| 用途 | 延迟、成本、成功率 | 只用于复现，**不进延迟统计** |

插件层命中缓存时**不记 ledger**（`PROVIDER_INVENTORY.md` 3.15），所以：
- 用 ledger 算成本 → 偏低；
- 用一次到位的延迟算 p95 → 把 0 ms 的缓存命中混进真实调用。

标准要求：**每个指标旁边标注它是在冷样本还是全样本上算的。**

### 4.4 重复与波动

- 检索结果对同一 query 在短时间内基本稳定，**≥ 3 次重复**只对易变任务强制：
  时效性检索（`freshness_search`）、社交主页、新闻类抓取。
- 重复 ≥ 3 次时报告**极差**，不只报均值。一个 3 次结果完全不同的 provider，
  均值是它最不重要的一面。

### 4.5 速率限制：不制造人为失败

- 遇到 429：等待并重试，记录等待时长，**不算该次失败**。
- 重试上限 3 次，仍失败记 `rate_limited`，**单列一类失败**，不混入 `error`。
- 同一 provider 的所有用例共享同一个限速器，避免并发把它打成超时。
- 因限速导致的超时要能从记录里区分出来（`error_code = "rate_limited"`）。

### 4.6 Ensemble 的消融

B 层回答的不是"谁最好"，是"组合的每一部分值不值"：

```
ensemble_recall      = 所有臂的结果并集在 k 内的召回
recall_without_X     = 去掉 X 之后的并集召回
recall_drop          = ensemble_recall - recall_without_X
```

`recall_drop ≈ 0` 意味着 X 的工作被覆盖了。**这只说明冗余，不说明 X 差**——
每调用一次都是有成本的，冗余就是可以省下的钱。

**B 层的数字不能与 A 层并列。** 并集天然 ≥ 任何单家，把 0.82 和 0.61 放在同一列
比较是错的。报告里必须分表，且 B 层表头写明"union across N providers"。

### 4.7 必败类别

以下类别在当前代码上是结构性必败，**单独报告**，不混进总体成功率：

| 类别 | 原因 |
|---|---|
| `pdf_scanned` | 无 OCR 适配器，MinerU 恒不可用 |
| 登录墙 / 登录后页面 | 期望 `no_content`，返回登录页 = 假成功 |
| 需要交互才能展开的列表 | 无自动化点击路径（TinyFish Agent 另计，按步付费） |

把它们混进分母，会让"PDF 抓取率 40%"这种数字既不能改进也无法解释。

---

## 5. 角色判定（不是排名）

每个 provider 在每个能力上得到一个**角色**，而不是一个名次。角色由三个事实决定：
绝对水平、相对覆盖、独有贡献。

| 角色 | 判据 | 使用建议 |
|---|---|---|
| **Primary** | 该类别下 `useful_content_rate`（或 recall）最高，且成本可接受 | 默认先用它 |
| **Secondary** | 与 Primary 差距在噪声内，或在不同子类上互有胜负 | 并行或按子类路由 |
| **Specialist** | 总体一般，但在某一子类上明显最好（如 PDF、maps、开放数据） | 只在该子类调用 |
| **Fallback** | 只有在 Primary 失败/额度耗尽时才值得调用 | 兜底 |
| **Not Recommended** | 在该类别上低于零成本基线，或 `shell_page_false_success_rate` 高 | 不调用 |
| **Insufficient Evidence** | 样本 < 30，或该类别为必败类别 | **不下结论** |

**"无结论"是合法结论。** pilot 规模（24 条 search、28 条 fetch）在任何 provider 上都
不足以跨过 `Insufficient Evidence` 的门槛 —— 这是设计而非遗憾。要拿角色，先扩样本。

### 5.1 帕累托前沿

同时看**召回**与**每 100 条相关结果成本**：

- 一个 provider 若被另一个在两项上都不差且至少一项更好，则它被**支配**，不在前沿上；
- 前沿上的 provider 之间**没有优劣**，只有取舍。

`metrics.pareto_front()` 实现这条判定。它是"该不该付这个钱"的直接答案，比单一分数
有用，因为它保住了取舍。

### 5.2 长期因素（不进评分，进决策备忘）

以下八项**不折算成分数**，但必须在报告里逐项回答，因为它们决定"三个月后还能不能用"：

| 因素 | 要回答的问题 |
|---|---|
| 覆盖范围 | 只在 FR 好，还是三国都不差？新增市场要多久？ |
| 来源独立性 | 与现有 provider 是同一个上游索引吗？（同一上游 = 冗余） |
| 时效 | 索引延迟多久？能不能查"今天"？ |
| 可靠性 | 近 90 天失败率、置旁次数、额度耗尽频率 |
| 合规 | 抓取是否遵守 robots / ToS？数据出境有没有问题？ |
| 隐私 | 会把什么发给第三方？查询词本身算不算敏感？ |
| 可维护性 | 换账号/换端点要改几行？契约稳定性如何？ |
| 锁定 | 输出格式是不是独有？迁移成本多大？ |
| 经济 | 单价、阶梯、超额行为（静默降级还是报错） |

---

## 6. Judge（语义判定）的边界

相关性、证据质量、答案好坏无法完全机械化，需要模型判断。但模型判断**不是测量**，
除非它被校准过。

规则（完整契约见 [`JUDGE_CONTRACT.md`](JUDGE_CONTRACT.md)）：

1. **盲评**：judge 看到的是 `candidate_a/b/c`，不知道谁是谁；映射密封单独存。
2. **证据引用**：每个分数必须附一段**逐字出现在输入里**的引文。引文不在输入里 = 判定作废。
3. **可弃权**：证据不足时输出 `abstained: true` 并给理由，不猜。
4. **必须校准**：用人工标签算一致率；`agreement < 0.80` 或 `false_positive_rate > 0.20`
   或样本 < 10 → judge 结论**不得进入结论表**（`uncalibrated` / `insufficient_sample`）。
5. **不合并**：judge 分与确定性指标分块报告，不加权成一个总分。

第 5 条的理由：加权需要权重，权重需要价值判断，而价值判断一旦写进公式就不可见。
分块报告让读者自己看到"确定性指标说 A 好，语义判断说 B 好"，而不是被一个 0.73 分
掩盖了矛盾。

---

## 7. 报告的结构

一份报告必须包含，且顺序固定：

1. **运行元数据**：run_id、时间、数据集版本 + 校验和 + split、代码版本与 commit、
   Python 版本、provider 版本、操作者、模式（offline / live）
2. **A 层表**（每个能力一张），每张表都带：样本量、冷/热标注、`n/a` 的原因
3. **B 层表**（并集 + 消融），表头标注 union across N
4. **C 层表** + 抽样人工复核记录
5. **失败清单**（按 `error_code` 直方图），含 `rate_limited`、`shell_page`、必败类别
6. **角色与帕累托**，含 `Insufficient Evidence` 的显式列表
7. **judge 分块** + 校准等级
8. **长期因素备忘**
9. **这份报告回答不了什么**（必填）

第 9 项是强制的。一份不写自己边界的结果表会被当成全序排名使用。

模板：[`templates/report.template.md`](templates/report.template.md)，
结构示例：[`templates/scorecard.example.json`](templates/scorecard.example.json)。

---

## 8. 版本与可复现性

- 标准版本：`marketops.benchmark.v1`（`benchmark/runner/__init__.py::SCHEMA_VERSION`）
- 计分卡版本：`marketops.benchmark.scorecard.v1`
- judge 契约版本：`marketops.judge.v1`
- 数据集校验和：对所有 `.jsonl` 按文件名排序后的 `sha256(name + bytes)` 累积，
  写进计分卡。**数据集变了，校验和变了，旧分数自动失效。**

重算同一份计分卡的命令必须是幂等的：同一份 recorded 目录，跑两次得到逐字节相同的
JSON（键排序、LF、无时间戳漂移的字段除外）。

---

## 9. 变更记录

| 版本 | 变更 |
|---|---|
| v1 | 首版。三层结构、指标定义、公平协议、角色判定、judge 边界。无真实调用。 |
