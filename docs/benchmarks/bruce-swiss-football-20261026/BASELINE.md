# 基线：瑞士户外天然草足球场（CH-PE-001 … CH-PE-027）

Bruce 在同一批输入上的实际产出。每个数字后面都写了它从哪来。
逐账户明细在东京本机，见 §7；本文不含任何第三方姓名、邮箱、电话。

---

## 1. 总览

| 指标 | CH-PE 全族（交付部分） |
|---|---:|
| 输入账户 | **44** |
| 官网域名（去重） | **31** |
| 交付行数 | **66** |
| A（关键决策/推动） | **16** |
| B（场地/草坪/设施一线） | **26** |
| C（官方业务入口） | **24** |
| D（仅审计，不计入有效邮箱） | **0** |
| A+B 联系人数 | **42** |
| A+B 机构数（至少一条 A 或 B 的账户） | **27** |
| **每百机构 A+B** | **95.5** |
| 具名联系人数（`Contact Name` 非空） | **44**（其中 A/B 42 条全部具名） |
| 覆盖率（有任一交付行的账户 ÷ 44） | **100%**（见 §2.6 的口径警告） |
| 未找到 | **0**（同上） |
| 语言分布 | de 60 行、fr 6 行 |
| 耗时 | 见 §3 |
| 交付性质 | `Research-only = TRUE`、`Send Authorized = FALSE`，66/66 |

读法（Bruce 自己的口径，见 §4）：**A/B 是有效邮箱，C 是官方业务入口，D 只作审计**。
本族 66 行里一条 D 都没有——和英国题正好相反：英国是 D 档兜底 325/376，
瑞士是 A/B 撑起 42/66，剩下 24 条是有场地职能证据的官方部门口。

## 2. 每个数字怎么来的

### 2.1 交付表本体

Bruce 的最终交付是一张飞书表格 **「瑞士户外足球场_最终研究有效邮箱_20260904」**
（`UVoisOUxkhgV6BtVwy8cMYdFn3g`，工作表「研究有效邮箱」，`1bMrkc`，`A1:S67`）。

- 67 行 = 1 行表头 + **66 行数据**；19 列：
  `Batch ID, Account ID, Account Name, Account Type, City, Email, Contact Name,
  Role / Department, Role Tier, Language, Original URL, Final URL, Evidence Excerpt,
  Local Mirror Path, Extraction Method, Email Evidence Status, Account / Role Relevance,
  Research-only, Send Authorized`
- 该链接出现在语料里 Bruce 的线程 `01a06cac-7fc9-76b3-8631-137a04d35012`
  （2026-09-04 21:43 用户贴「新开发信清单」）与 2026-09-08 的收单回执中。
- 交付表里 66 行的分层：A 16 / B 26 / C 24 / D 0 —— 与任务书给的
  「Bruce 的交付是 66 条，其中 A16 / B26 / C24」逐档相等。

### 2.2 逐批行数

| 批次 | 账户 | 行 | A | B | C | A+B |
|---|---:|---:|---:|---:|---:|---:|
| CH-PE-001 | 2 | 2 | 0 | 2 | 0 | 2 |
| CH-PE-002 | 8 | 10 | 3 | 0 | 7 | 3 |
| CH-PE-003 | 5 | 8 | 3 | 1 | 4 | 4 |
| CH-PE-005 | 2 | 2 | 0 | 1 | 1 | 1 |
| CH-PE-009 | 1 | 1 | 0 | 0 | 1 | 0 |
| CH-PE-012 | 3 | 3 | 1 | 0 | 2 | 1 |
| CH-PE-013 | 3 | 5 | 2 | 0 | 3 | 2 |
| CH-PE-016 | 1 | 2 | 0 | 0 | 2 | 0 |
| CH-PE-017 | 2 | 2 | 0 | 0 | 2 | 0 |
| CH-PE-018 | 4 | 5 | 2 | 3 | 0 | 5 |
| CH-PE-019 | 5 | 10 | 2 | 6 | 2 | 8 |
| CH-PE-020 | 1 | 1 | 0 | 1 | 0 | 1 |
| CH-PE-021 | 1 | 1 | 1 | 0 | 0 | 1 |
| CH-PE-022 | 1 | 3 | 0 | 3 | 0 | 3 |
| CH-PE-026 | 3 | 4 | 2 | 2 | 0 | 4 |
| CH-PE-027 | 2 | 7 | 0 | 7 | 0 | 7 |
| **合计** | **44** | **66** | **16** | **26** | **24** | **42** |

这 16 批是 `CH-PE-001`…`CH-PE-027` 里**留下交付行**的那 16 个。
其余 11 批在 Bruce 本机跑过，但语料与飞书里都没有逐账户结果，故不入基线（`TASK.md §10.2`）。

### 2.3 账户数、域名数与机构数

- 账户数 = 66 行的 `Account ID` 去重 = **44**。
- 官网域名 = 44 个账户 `Original URL` 去重 = **31**：6 组同域多账户
  （St. Gallen 5、Basel 4、`staatskalender.bs.ch` 5、Morges 2、Uster 2、Bern 2）。
- 机构数取**账户数 44**，不取域名 31：本族的账户是按"一块场地 / 一个体育设施"建的，
  同一市政站下的 5 个 St. Gallen 账户是 5 个不同的研究对象。
  两个口径都写出来，避免和英国题（344 账户 / 301 机构）的口径混淆：

  | 分母 | Bruce 每百 A+B |
  |---|---:|
  | 44 账户（本文主口径） | **95.5** |
  | 31 官网域名 | 135.5 |

### 2.4 A+B 机构数

42 条 A/B 落在 **27** 个账户上（有的账户 2–5 条），其余 17 个账户只有 C。
每百机构 A+B = 42 ÷ 44 × 100 = **95.5**。

### 2.5 为什么这族的 A/B 密度这么高

三条原因，都不是"运气好"：

1. **账户本身挑过**：Bruce 在 09-04 的批次里优先多场地俱乐部、体育公园、市政体育设施
   （任务书「批次规则」），这类站点有 `Vorstand`（理事会）、`Platzwart` 名单页。
2. **德语区把岗位写在网页上**：`Platzwart`、`Materialwart`、`Leiter Sportanlage`
   是俱乐部/市政站点上的标准栏目，往往一人一岗一字面邮箱。
3. **两条补充通道**：66 行里 10 行来自 PDF（球场预订/设施手册），2 行来自
   HTML 实体解码与 Cloudflare 包裹页——只扫 HTML 的 `mailto:` 会直接漏掉这一档。

### 2.6 覆盖率与"未找到"的口径警告

这 44 个账户是 Bruce **交付成功**的那一批。他实际跑过 27 批、母池 2,893 个卫星候选，
但**没有留下**"尝试过但零路线"的账户清单。因此：

- 基线覆盖率 = 100%、未找到 = 0，这两个数是**构造出来的**，不是能力差距。
- 拿它们和答题方比没有意义；有意义的是**同一批 44 个账户上**的
  A/B 密度、具名人数、C/D 结构与精度。
- 反过来说，答题方在这 44 个账户上必须几乎条条有产出，才能逼近 95.5 的密度——
  这是本题最硬的一道闸，判定时按 `TASK.md §8` 的主指标读。

### 2.7 具名

- `Contact Name` 非空 = **44 条**：A 16/16、B 26/26、C 2/24。
- 42 条 A/B 全部具名，且名字与岗位来自同一页面同一块（`Evidence Excerpt` 可复核）。
- 这和英国那道题形成对照：英国 376 行里具名 0 条。

## 3. 耗时

时间戳取自语料与 Bruce 本机文件时间，北京时间（UTC+8）：

| 阶段 | 起 | 止 | 时长 | 出处 |
|---|---|---|---|---|
| 母池建库（瑞士卫星候选 2,893） | 2026-09-03 18:22 | 2026-09-04 04:29 | — | 母池 `config.created_at`（UTC 09-03 10:22） |
| 主调研线程（交付指令 → 停研出表） | 2026-09-04 12:28 | 2026-09-04 21:43 | **9 小时 15 分** | 线程 `01a066c5` 首尾消息 |
| 交付文件最后写入 | — | 2026-09-04 16:24 | — | `effective_public_email_routes.csv` 的 Windows 文件时间 |
| 交付表日期 | — | 2026-09-04 | — | 表名 `_20260904` |

**逐批耗时不可还原**：Bruce 的批次时间戳只在批次回执里，
本族 16 批的回执散在三个线程里，没有逐批对齐的时间对（英国那 5 批是有的）。
所以本文只给三个端到端锚点，不给"每批几分钟"。

**成本不可测**：这一族跑在 Codex 会话里，语料没有 token / 金额账本。

## 4. 人工介入

- 主调研线程 `01a066c5`（2026-09-04 12:28–21:43）去重后 **≥28 条**人工消息
  （同一句常被重复落盘两次，已合并）。典型动作：
  - 纠 ROI：「roi 太低，需要短时间足够多有效邮箱」（04:37 UTC）
  - 纠命名：「瑞士球场命名规则，比如 FC、FV、TUS 这类」（05:38）
  - 改口径：「不需要强绑定姓名，角色+工作邮箱明确即可」（07:52）
  - 改节奏：「每次按 20 页面下载为基础」（08:21）
  - 收工：「停止调研，最终有效邮箱飞书表格输出」（13:43 UTC / 21:43 CST）
- 对照：英国那 5 批在收单线程里人工消息 **0 条**——那是一个全自动流水线段。
  本族是"边跑边定口径"的探索阶段，人工介入不可比。

## 5. 主要来源与方法

- **来源形态**：俱乐部官网的 `Vorstand` / `Kontakt` / `Ausrüstung` 页，
  市政站点的 `Sportanlagen` / `Reservationen` / `Infrastruktur` 页，
  市属园艺部门的 `Leitung` 页，以及球场预订 PDF 与市政员工目录（`staatskalender`）。
- **方法链**（Bruce 的六段）：母池 → 页面镜像 → 字面邮箱提取 → 场景确认 → 角色分层 → 飞书入库。
- **全部 research-only**：66 行 `Research-only = TRUE`、`Send Authorized = FALSE`，
  本族没有做过任何发信或客户动作。
- **提取方式分布**（66 行）：
  `literal_mailto_from_local_html` 54、`literal_text_from_local_pdf_pypdf` 7、
  `literal_text_from_local_pdf_pdfplumber` 3、`literal_html_entity_decoded_from_local_mirror` 1、
  `literal_email_decoded_from_local_cloudflare_html` 1。
- **证据状态**：`OFFICIAL_PUBLISHED` 61、`OFFICIAL_PUBLISHED_ARCHIVAL` 4、
  `OFFICIAL_PUBLISHED_PUBLIC_BUSINESS_FREE_MAIL` 1（后者是一个俱乐部的 `gmx.ch` 私人域工作邮箱）。

## 6. 与同族其他部分的关系

- 母池表 `switzerland_natural_grass_outdoor_football_pitches_master.csv`（2,185 家）
  与场景池里的「Switzerland Natural-Grass Football Contacts（331）」
  **不是**本题的基线：前者是候选池，后者是含 D 档与审计行的更宽口径。
  本题只取 09-04 的「最终研究有效邮箱」66 行，因为只有这一版是
  「A/B/C 分层 + 逐行证据 + 镜像路径」齐全的最终交付。
- 09-08 之后这批邮箱被用于一次群发准备（`switzerland_football_bulk_9ewan_20260904`）。
  那是发信动作，不在研究基线里，也不在本题范围内。

## 7. 逐账户明细（东京本机，不入 Git）

```text
/data/home/vince/artifacts/marketops/benchmarks/swiss-football/baseline_rows.csv  # 66 行，Bruce 的逐账户结果（19 列，原样）
/data/home/vince/artifacts/marketops/benchmarks/swiss-football/task_input.csv     # 44 行，题目输入的账户清单
/data/home/vince/artifacts/marketops/benchmarks/swiss-football/baseline_raw.csv   # 飞书导出的带 [row=N] 前缀原文，留作回读校验
```

`task_input.csv` 的列：`batch, account_id, organization, official_url, city, account_type, language, bruce_rows, bruce_tiers`。
后两列只用于出题对账。

这三个文件含第三方邮箱与机构名，**不得进 Git、不得带出东京节点**。

## 8. 已知缺口

1. **没有"尝试过但零结果"的分母**：见 §2.6。覆盖率与未找到都是构造值。
2. **只有 16/27 批**：另 11 批在 Bruce 本机，语料与飞书都没有逐账户结果。
3. **逐批耗时缺失**：见 §3。
4. **同域多账户的归属只在 66 行里可验**：St. Gallen / Basel 的 5+4 个账户，
   交付行里能看出谁用汇总信箱、谁用专用信箱；但"为什么这样分"没有文字回执，
   只有 `Evidence Excerpt` 与 `Original URL` 能反推。
5. **法语区样本少**：66 行里 fr 只有 6 行（Morges / Delémont），
   法语口径的统计意义弱于德语区，判定时按语言分层看。
