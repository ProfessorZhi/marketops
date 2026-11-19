# Research Core 数据集覆盖

数字直接从 `cases.jsonl` 与 `capture-manifest.json` 统计，未手工调整。

## 1. Synthetic Benchmark — 175 cases / 9 阶段

| stage | cases | development | validation | holdout |
|---|---|---|---|---|
| brief | 3 | 3 | 0 | 0 |
| search | 47 | 28 | 1 | 18 |
| fetch | 33 | 30 | 3 | 0 |
| extraction | 30 | 20 | 0 | 10 |
| qualification | 23 | 14 | 9 | 0 |
| identity | 15 | 10 | 5 | 0 |
| evidence | 15 | 10 | 5 | 0 |
| channel | 6 | 4 | 2 | 0 |
| e2e | 3 | 3 | 0 | 0 |
| **合计** | **175** | 122 | 25 | 28 |

### 1.1 Search — 47 cases / 21 类意图

| intent | n | intent | n |
|---|---|---|---|
| official_website | 5 | exact_company | 3 |
| contact_page | 4 | executive | 3 |
| long_tail | 4 | multilingual | 3 |
| youtube_channel | 3 | country_limited | 2 |
| disambiguation | 2 | identity_link | 2 |
| competitor | 2 | official_source_priority | 2 |
| parent_subsidiary | 2 | team_page | 2 |
| youtube_creator | 2 | product_catalogue | 1 |
| pdf_document | 1 | news_article | 1 |
| job_posting | 1 | social_profile | 1 |
| local_business | 1 | | |

任务要求 ≥20 类意图、≥40 cases，实测 21 类 / 47 条。

### 1.2 Fetch — 33 cases / 25 种页面类型

| page_kind | n | page_kind | n |
|---|---|---|---|
| team_page | 5 | static_html | 3 |
| about_page | 2 | contact_page | 2 |
| binary_garbage | 1 | cookie_consent | 1 |
| duplicate_content | 1 | empty_spa | 1 |
| founder_biography | 1 | generic_contact | 1 |
| http_error | 1 | invalid_content | 1 |
| javascript_rendered | 1 | lazy_loading | 1 |
| meta_refresh | 1 | missing_contact | 1 |
| not_found | 1 | org_structure | 1 |
| pagination | 1 | pdf_link | 1 |
| pdf_text | 1 | redirect_chain | 1 |
| role_based_contact | 1 | stale_content | 1 |
| wrong_language | 1 | | |

任务要求 ≥30 cases、≥25 种页面，实测 33 / 25。

### 1.3 Extraction — 30 cases

| 项 | 数量 |
|---|---|
| gold 公司 | 30 |
| gold 人物 | 36 |
| gold 邮箱候选 | 22 |
| 明确"不应出现个人邮箱"的负例 | 16 |

任务要求 ≥30 公司、≥20 人物、≥20 邮箱候选、≥10 负例，均满足。

### 1.4 Qualification — 23 cases / 10 种状态

| status | n | 回答的问题 |
|---|---|---|
| weak | 6 | 同国家但行业只有弱关联 |
| not_qualified | 5 | 行业与国家都不符合 |
| strong | 3 | 行业、国家、规模都对上 |
| excluded | 3 | 命中 ICP 明确排除项 |
| partial | 1 | 行业对、国家不对 |
| insufficient_evidence | 1 | 没有任何被引页面能证明行业或规模 |
| duplicate | 1 | 同域名已在本批出现过 |
| out_of_scope | 1 | 记录本身是目录，不是公司 |
| unverified_identity | 1 | 域名只有占位页，无法确认是不是这家公司 |
| needs_review | 1 | 被引页面互相矛盾，规则不能替人裁决 |

最后三种不是"分数低"，而是"这个问题规则回答不了"。为了让 gold 可复核，这三条
的 `prospect.evidence_notes` 记录了引文原文，测试会验证每句引文确实出现在对应
corpus 页面上；它们同时被标为不进入 `search_index.json`，所以搜索基准的既有期望
一个字节都没有变。

### 1.5 其余阶段

- Identity：15 cases，覆盖同域、同名不同域、母公司/子公司、误合并负例。
- Evidence：15 cases，14 条快照存在且 SHA256 可复核，1 条**故意**记录不存在的
  快照并把 gold 标为 `snapshot_exists: false`。
- Channel：6 cases，3 组 brief × 2 种排序（含一条名次有争议的用例，报告里作为已知
  baseline gap 保留）。
- E2E：3 cases，覆盖 3 份 Product Brief 的完整 Research Dossier。

## 2. Public Evidence Benchmark — 56 snapshots

| 项 | 实测 |
|---|---|
| 冻结快照 | 56 |
| 记录为缺口 | 44 |
| 覆盖公司（网站） | 33 |
| 覆盖创作者（YouTube 频道） | 17 |
| 国家 | 11（AT CH DE ES FI FR GB JP NL SE US） |
| 语言 | 5（de en es fr nl） |
| fetch cases | 56 |
| evidence cases | 56 |

任务要求 20 家真实公司、10 个真实 YouTube 频道、≥3 国家、≥3 语言、约 60 份可追溯
快照。实测 33 / 17 / 11 / 5 / 56。

### 2.1 按国家

| 国家 | snapshots |
|---|---|
| DE | 21 |
| FR | 5 |
| US | 5 |
| NL | 4 |
| SE | 4 |
| JP | 4 |
| CH | 4 |
| ES | 4 |
| GB | 3 |
| FI | 1 |
| AT | 1 |

### 2.2 缺口（44 条，未绕过）

| 原因 | n | 含义 |
|---|---|---|
| `disallowed_by_robots` | 34 | `robots.txt` 明确禁止，尊重并跳过 |
| `http_404` | 9 | 目标路径不存在（多为猜的 about 页） |
| `forbidden` | 3 | 反爬直接 403，不绕过 |

逐条 URL 见 `benchmark/datasets/research-core/public-evidence/GAPS.md`。

### 2.3 为什么只有 56 而不是 60

候选池扩到 50+ 家真实公司、19 个频道后，能通过 `robots.txt` 且返回 200 HTML 的只有
56 个页面。差的 4 个没有用合成页补——补了就破坏了"public evidence 必须是真实公开页"
的前提。数字上的缺口，比一条假快照更有价值。

### 2.4 一条真实的反例留在数据里

`RC-FECH-PUB-034`（SKF 首页）是一个 Angular 应用外壳：冻结下来的 19 KB HTML 去掉
脚本后只剩三个字符 `SKF`。它的 gold 是 `expected_outcome: no_content`、
`requires_js: true`，参考适配器也如实返回空文本。这条用例专门用来证明：
**"HTTP 200 + 非空 HTML"不等于"抓到了可读内容"**。
