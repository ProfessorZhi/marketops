# Research Core Benchmark 指南

这套 harness 的目的不是"跑出一个好看的分数"，而是让任何 Provider 在同一份数据、
同一套判据、同一份记录下被比较，并且比较结果事后可复核。

---

## 1. 四个角色

| 角色 | 代码 | 职责 |
|---|---|---|
| Dataset | `benchmark/datasets/research-core/**` | 固定输入与 gold，版本化 + 校验和 |
| Adapter | `benchmark/research_core/adapters/` | 把某个 Provider 的一次调用变成 stage 输出 |
| Runner | `benchmark/research_core/runner.py` | 逐 case 执行、分类状态、写三份产物 |
| Metrics | `benchmark/research_core/metrics.py` | 只在 `ok` / `empty` 上打分 |

## 2. 运行

```bash
# 一阶段一次
.venv/bin/python -m benchmark.research_core.cli run --stage search --provider mock_serper --repeat 3

# 九阶段参考套件
.venv/bin/python -m benchmark.research_core.cli suite --repeat 3

# Public Evidence 两阶段
.venv/bin/python -m benchmark.research_core.cli \
  --dataset-root benchmark/datasets/research-core/public-evidence \
  suite --stages fetch evidence --tag=-public --repeat 3
```

`suite` 会跑数据集里**存在**的 stage；不存在的跳过并打印，不会假装跑过。

## 3. 六个状态，一个都不能混

| status | 含义 | 是否计分 |
|---|---|---|
| `ok` | Provider 正常返回了内容 | ✅ |
| `empty` | Provider 正常返回，但结果是空的（可能是正确答案） | ✅ |
| `failed` | 传输/HTTP/解析错误 | ❌ |
| `not_configured` | 没有凭据或未配置，**根本没到 Provider** | ❌ |
| `timeout` | 超时 | ❌ |
| `invalid_output` | 返回了，但形状不符合契约 | ❌ |
| `skipped` | 主动跳过 | ❌ |

映射是类型驱动的，写在 `runner._ERROR_STATUS`：

```
NotConfigured     -> not_configured
AdapterTimeout    -> timeout
ContractViolation -> invalid_output
AdapterError 其他  -> failed
```

规则只有一句：**失败不是空结果，空结果也不是失败**。`not_configured` 的 latency 记
为 `null`，因为它没有真的发出请求。

## 4. Adapter 契约

一个 adapter 必须提供：

```python
class AdapterInfo:
    name: str; stage: str; kind: str   # "mock" 或将来的 "live"
    model: str | None
    requires: tuple[str, ...]
    notes: str

adapter.availability() -> (usable: bool, reason: str)
adapter.run(case: dict) -> dict      # 形状见 contracts.CONTRACTS[stage]
```

`run()` 可以抛异常，但不能返回 metrics 读不懂的东西：`contracts.violations()`
先检查结构（缺列表、列表项不是对象、字段类型错），不合格直接记 `invalid_output`。

### 新增一个 Provider

1. 实现上述接口，`AdapterInfo.kind="live"`；
2. 在 `adapters/` 注册进 `AdapterRegistry`；
3. `validate` + `run --provider <name>` 先跑 1 次；
4. 它的成绩单会出现在自己的 manifest 里，**不会**并入 reference suite。

## 5. 每次运行的产物

```
benchmark/research-core/runs/
  <run_id>-<provider>.jsonl            每 case 每 repeat 一行
  <run_id>-<provider>.manifest.json    数据集/配置/计数/指标/环境
  suite-summary.json                   九阶段参考套件索引
  suite-summary-public.json            Public Evidence 索引
```

一行结果必含：`run_id, case_id, stage, provider, model, configuration,
started_at, latency_s, status, output, gold, evaluation, error, cost_usd,
provenance`。`provenance` 里带 `dataset_version`、`dataset_checksum`、
`case_sha256`、主机与 Python 版本。

manifest 必含：`configuration.config_sha256`、`datasets[].checksum`、
`counts`（六个状态 + `cases` + `scored`）、`metrics`、`repeat`、
`environment`。

**什么进 Git**：manifest、`suite-summary*.json` 与报告进；`*.jsonl` 逐 case 输出
（含整页文本，单次可达 19 MB）不进——它由冻结数据集原样重跑即可再生，`.gitignore`
已按路径忽略。manifest 里的 `results_path` 指向本地那份文件。

## 6. 指标

| stage | 指标 |
|---|---|
| brief | `overall_coverage`、分项 `coverage{icp_attributes,personas,pain_points,channels,queries,exclusions}`、`forbidden_claims_leaked` |
| search | `precision_at_5/10`、`recall_at_10`、`mrr`、`domain_hit_rate`、`unique_discovery_rate`、`excluded_hits`、`duplicate_rate`、`correct_shape` |
| fetch | `text_length`、`marker_recall`、`email_recall`、`person_recall`、`link_recall`、`extraction_completeness`、`false_success`、`correct_no_content`、`forbidden_hit`、`final_url_ok` |
| extraction | `company_recall`、`person_recall`、`person_precision`、`person_hallucinations`、`email_recall`、`email_kind_accuracy`、`invented_personal_emails`、`decision_maker_accuracy`、`gap_honesty` |
| qualification | `status_correct`、`icp_match_correct`、`relevance_error`、`grounded`、`ungrounded_citations`、`missing_evidence_recall` |
| identity | `pair_precision`、`pair_recall`、`pair_f1`、`relation_correct`、`relation_false_positives`、`relation_misses` |
| evidence | `snapshot_exists_correct`、`sha256_correct`、`fields_traceable_recall`、`entity_correct`、`stale_detected`、`contradictions_found` |
| channel | `top1_correct`、`top3_acceptable`、`ndcg`、`rejected_selected`、`order_match` |
| e2e | `prospect_recall`、`status_accuracy`、`person_presence_accuracy`、`email_presence_accuracy`、`evidence_coverage`、`fabricated_claims`、`forbidden_claims_seen`、`gap_honesty`、`dossier_complete` |

聚合规则（`metrics.aggregate`）：

- 普通数值取平均；
- 布尔按命中率（`sum(true)/n`）；
- 计数类（`results`、`relation_misses`、`fabricated_claims` 等）求和；
- 嵌套 dict（brief 的 `coverage`）按子键分别平均；
- 值为 `None` 的指标（例如内容页上的 `correct_no_content`）**不参与**，不当 0。

## 7. 可复现性的四道校验

1. **重复运行**：`--repeat 3` 后 `repeat_identical` 必须为 `true`，否则写进 manifest
   而不是被平均掉。
2. **数据集校验和**：同一数据集两次运行 `dataset_checksum` 必须一致。
3. **快照 SHA256**：evidence 阶段逐条重算，`sha256_correct` 可直接复核。
4. **重建一致**：`build_datasets.py` 重跑必须字节一致（有测试守着）。

## 8. 已知基线缺口

报告里 `KNOWN_BASELINE_GAPS` 记录了三个刻意不追平 1.0 的指标：

- search 的 `precision_at_5/10`：词法排序会返回 10 条而多数 query 只对应 1–2 家，
  低精度是结构性的，看 recall / domain_hit_rate / MRR。
- identity 的 `relation_misses=1`：一条用例要求 `branch_of`，基线在没有页面明确
  陈述的情况下不编造公司关系，因此报 0 而不是猜。
- channel 的 `order_match=2/6`：gold 排序含编辑判断，两处名次本身可争议。

把它们写下来，比为了好看去改 gold 更诚实。
