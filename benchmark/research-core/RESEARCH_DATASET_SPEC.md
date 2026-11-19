# Research Core 数据集规范

两个数据集，一套 case 契约：

| 数据集 | 根目录 | `dataset_version` | `origin` | 是否联网 |
|---|---|---|---|---|
| Synthetic Benchmark | `benchmark/datasets/research-core/synthetic` | `research-core-1.0.0` | `synthetic` | 否 |
| Public Evidence Benchmark | `benchmark/datasets/research-core/public-evidence` | `research-core-public-1.0.0` | `public_real_world` | 只在冻结快照时 |

两者**永不混算**：`origin`、`dataset_version` 与 `dataset_checksum` 都不同，报告里分开
成两节。

---

## 1. 文件布局

```
<dataset-root>/
  <stage>/cases.jsonl        # 一行一个 case，UTF-8，无 BOM
```

`<stage>` ∈ `brief | search | fetch | extraction | qualification | identity |
evidence | channel | e2e`。一个数据集只放它拥有的 stage：synthetic 九段齐全，
public-evidence 只有 `fetch/` 与 `evidence/`。**目录存在但为空**算失败；**目录不存在**
按"该数据集没有这一阶段"跳过，并在输出里显示。

## 2. Case 信封

每个 case 的公共字段（全部 required）：

| 字段 | 说明 |
|---|---|
| `case_id` | 稳定标识，命中各 stage 的正则（见下） |
| `schema_version` | 固定 `marketops.research.case.v1` |
| `dataset_version` | `^research-core(-[a-z]+)?-\d+\.\d+\.\d+$` |
| `stage` | 与所在目录一致，schema 用 `if/then` 分派 |
| `split` | `development / validation / holdout` |
| `origin` | `synthetic / public_real_world / bruce_derived` |
| `market` | ISO 风格大写国家码，或 `multi` |
| `language` | 两字母小写语言码 |
| `input` / `gold` | 由 stage 分支约束形状 |
| `label_status` | `draft / human_verified / disputed` |
| `provenance` | `source_ref`、`captured_at`、`content_sha256`、`notes` |
| `notes` | 人读备注 |

`case_id` 形如 `RC-<STAGE>-<QUALIFIER>-<NNN>`，例如 `RC-SRCH-CAT-042`、
`RC-FECH-PUB-001`、`RC-EVID-OK-003`。同一文件内必须唯一。

## 3. 各阶段 input / gold

| stage | input 必填 | gold 必填 |
|---|---|---|
| brief | `raw_brief{product,description,offer,markets,languages,goal}` | `icp_attributes, personas, pain_points, channels, queries, exclusions, forbidden_claims` |
| search | `query, intent, country_code, language_code, top_k`（可选 `expected_result_kind`） | `expected_domains, expected_urls, expected_entities, excluded_domains, source_refs` |
| fetch | `url, timeout_seconds, expected_outcome`（可选 `final_url_expected`） | `page_kind, expected_text_markers, expected_emails, expected_persons, expected_links, min_text_chars`（可选 `requires_js`、`forbidden_strings`） |
| extraction | `snapshot_path, source_url, company` | `companies, people, emails, expect_no_personal_email`（可选 `forbidden_emails`、`missing_evidence`） |
| qualification | `prospect, icp`（prospect 可选 `record_type`、`domain_status`、`conflicts`、`evidence_notes`） | `qualification_status, icp_match, relevance, reason, supporting_evidence, missing_evidence` |
| identity | `records` | `clusters, relations` |
| evidence | `evidence{evidence_id,source_url,source_type,retrieved_at,snapshot_path,sha256,extracted_fields,entity_refs,upstream_refs}` | `snapshot_exists, sha256_matches, fields_traceable, entity_ref, stale_after_days, contradicts` |
| channel | `brief_ref, channels` | `ranked_channels, acceptable_set, rejected` |
| e2e | `brief_ref, max_prospects, corpus` | `expected_prospects, expected_gaps, forbidden_claims` |

## 4. 三种身份，每次运行都记录

| 身份 | 计算方式 | 作用 |
|---|---|---|
| `case_sha256` | case 的规范化 JSON（sorted keys、无空白）的 SHA256 | 内容寻址；改一个字就换一个身份 |
| `dataset_checksum` | 数据集目录下文件名 + 字节的 SHA256 | 两次跑分能否比较，先看它 |
| `dataset_version` | case 里声明的语义版本 | 人读的代数 |

三者都写进 run manifest；报告里每个数字都能追到 `run_id` + `dataset_checksum`。

## 5. 确定性

`build_datasets.py` 与 `build_public_evidence.py` 都是纯函数式生成器：

- synthetic 由固定种子和固定语料生成，**重跑字节完全一致**；
  `tests/test_research_datasets.py::test_rebuild_is_byte_identical` 逐 stage 比对。
- public-evidence 的字节来自已冻结快照；重跑不联网、不重写快照，只在有新增目标时
  追加。`retrieved_at` 保持首次抓取时间。
- 语料 `corpus/MANIFEST.json` 的 SHA256 固定为
  `0ecfa5b03b84d2b723f0bc1308687d28bafde516f3c1649f0e631b8ee858da81…`。

## 6. Gold Label 规则

1. **gold 必须能从冻结字节读出来**。fetch 的 `expected_text_markers`、evidence 的
   `fields_traceable` 都要在快照可见文本里存在，否则就是错的 gold，不是"难"。
2. **不允许为了跑通而改 gold 或放宽判据**。本轮修过的每一处 gold 都写明了依据
   （页面实际字符串、输入实际内容），而不是把阈值调低。
3. **没有验证就写 `not_attempted`**。证据用例里 `snapshot_exists: false` 的记录，
   `fields_traceable` 必须为空、`sha256_matches` 必须为 false。
4. **规则回答不了的问题要有独立状态**。`out_of_scope`（不是公司）、
   `unverified_identity`（域名无法确认身份）、`needs_review`（被引页面互相矛盾）
   都不是"低分"，而是"这件事不该由一条规则下结论"。它们的 `icp_match` 一律为
   false：未知不等于匹配。
5. **`label_status` 默认 `draft`**。本轮 175 + 112 条全部是 `draft`：由脚本从冻结
   字节生成，未经人工逐条复核。报告不把它说成 `human_verified`。
6. **缺口不补数**。抓不到的目标进 `GAPS.md` 与 `capture-manifest.json` 的 `gaps`，
   不造一条合成样本冒充。

## 7. 语料约定

- 合成页面一律用保留域 `.example`，永远不会解析到真实公司。
- 公开快照写入前做脱敏：邮箱改成 `contact@<slug>.example`，电话改
  `+00 000 000 000`；原始字节的 SHA256 保留在 manifest 的 `original_sha256`，
  保证"抓到了什么"仍可追溯。
- 不保存任何非公开个人信息；不保存 LinkedIn / Instagram 内容。
