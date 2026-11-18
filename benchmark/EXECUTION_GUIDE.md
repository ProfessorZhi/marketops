# 执行指南

怎么跑、怎么扩、怎么在离线状态下证明它没坏。

---

## 1. 总体结构

```
benchmark/
  STANDARD.md                 标准（量什么、怎么量、什么时候不采信）
  PROVIDER_INVENTORY.md       代码里到底有什么（Phase 0）
  GROUND_TRUTH_GUIDE.md       金标怎么产生
  JUDGE_CONTRACT.md           judge 的输入输出契约
  JUDGE_CALIBRATION.md        校准与等级
  EXECUTION_GUIDE.md          本文件
  PHASE2_LIVE_PLAN.md         真实调用的计划（2026-11-18 已执行 search/fetch 部分）
  schemas/                    4 份 JSON Schema 2020-12
  datasets/pilot/             62 条 draft 用例
  templates/                  报告与计分卡模板
  runner/                     纯 Python，无第三方依赖
    __init__.py               版本常量
    metrics.py                确定性指标（纯函数，无 I/O）
    models.py                 数据集加载/校验、recorded 回放
    normalize.py              各厂商响应 → 统一结果
    judge.py                  盲化 payload、verdict 校验、校准
    report.py                 计分卡组装与 Markdown 渲染
    cli.py                    validate / score / report / calibrate
```

**依赖方向是单向的**：`cli → report → judge → metrics`，`models`、`normalize` 只被
`cli` 使用。`metrics.py` 不 import 任何其它模块，它不知道文件系统存在。

---

## 2. 离线能做什么

| 命令 | 作用 | 需要网络 |
|---|---|---|
| `validate` | 校验数据集是否符合 schema，报出重复 case_id 与校验和 | ❌ |
| `score` | 读 recorded 目录，算出 A/B/C 三层计分卡 + Markdown + 消融表 | ❌ |
| `report` | 把已有计分卡 JSON 重新渲染成 Markdown | ❌ |
| `calibrate` | judge verdicts 与人工标签比对，输出校准报告 | ❌ |

```bash
python -m benchmark.runner.cli validate --dataset benchmark/datasets/pilot
python -m benchmark.runner.cli score --dataset benchmark/datasets/pilot \
    --recorded <recorded 目录> --out out/run.json
python -m benchmark.runner.cli report --scorecard out/run.json --out out/run.md
python -m benchmark.runner.cli calibrate --verdicts out/verdicts.json --labels out/human.json
```

四条命令都**不读凭据、不发请求**。`score` 的输出在给定同一份 recorded 目录时是幂等的
（键排序、LF、只读时钟字段例外）。

---

## 3. recorded 目录的布局

```
<recorded>/
  manifest.json
  serper/SRCH-FR-OFFICIALWEBSITE-001.json
  serpapi/SRCH-FR-OFFICIALWEBSITE-001.json
  crawl4ai/FECH-FR-STATIC-001.json
  ...
```

**一个 provider 一个子目录，一个用例一个文件，文件名就是 `case_id`。** 目录名即
provider 名 —— `score` 不去别处找"有哪些 provider"，它列目录。这有一个直接好处：
**加一个 provider 不需要改代码**，把目录放进去就行。

`manifest.json`：

```json
{
  "run_id": "pilot-live-2026-12-01",
  "started_at": "2026-12-01T09:00:00Z",
  "marketops_version": "1.0.0",
  "git_commit": "abc1234",
  "providers": {
    "serper": {"version": "google.serper.dev", "endpoint": "search",
               "region_params_sent": {"gl": "fr", "hl": "fr"}, "api_quota_used": 24}
  }
}
```

`providers.*.region_params_sent` **必须记录实际发出的参数**，不是想发的参数 ——
`PROVIDER_INVENTORY.md` §3.2 里那些"丢弃 `n` / 丢弃 `country`"的不对称，只有靠这一格
才能在报告里被发现。

### 3.1 三种响应形状

**search**（归一化形式，手写 fixture 用它就不用模仿厂商信封）：

```json
{"results": [{"rank": 1, "title": "...", "url": "...", "snippet": "..."}],
 "_meta": {"latency_ms": 412, "cost_usd": 0.001, "from_cache": false}}
```

厂商原始信封也接受，由 `normalize.search_rows()` 认：

| provider | 认的键 |
|---|---|
| serper | `organic` → `places` |
| serpapi | `organic_results` → `local_results` |
| deepseek / tinyfish | `results` |

认不出来就 **抛错**，不返回空列表。理由：一个解析失败的空列表和一个"真的没找到"在
计分卡上长得一模一样。

**fetch**：

```json
{"ok": true, "url": "...", "status": 200, "text": "...", "html": "...",
 "links": [], "emails": [], "phones": [], "persons": [["name", "role"]],
 "error_code": "", "_meta": {"latency_ms": 2100, "cost_usd": 0.0}}
```

**e2e**：

```json
{"entities": [{"entity_id": "OM", "name": "Olympique de Marseille",
               "official_url": "https://www.om.fr/",
               "contacts": [{"kind": "email", "value": "...",
                             "evidence_url": "https://...", "person": "", "role": ""}]}],
 "_meta": {"latency_ms": 180000, "cost_usd": 0.42}}
```

---

## 4. 分数是怎么算出来的

`score` 的三段，各自独立：

```
_score_search  → 每 provider 一条 SearchArm
                 每用例算 11 个指标 → 跨用例取均值（null 不参与均值）
                 unique_found 用 _unique_total 重算（集合差，不是加总）
_score_fetch   → 每 reader 一条 FetchArm，15 个指标
_score_e2e     → 每 configuration 一条 E2EArm，10 个指标
```

**关键实现细节（改代码前必须知道）**：

| 细节 | 位置 | 为什么 |
|---|---|---|
| 均值跳过 `null` | `report._mean` | `null` 是"没观测到"，不能当 0 拉低均值 |
| `precision_at_k` 分母恒为 k | `metrics.precision_at_k` | 少返回条目是缺答，不是更准 |
| `unique_recall` 只在多臂时有意义 | `cli._unique_total` | 单臂时它恒为 0 |
| 空 results 但 body 存在 → 记 failure | `cli._score_search` | 区分"没调"与"调了没结果" |
| 延迟 p50/p95 用最近秩 | `metrics.percentile` | 小样本上插值会造出不存在的观测值 |

**计分卡的三个块永不合并**：`search` / `fetch` / `e2e` / `judge` 各自一段，
没有 `overall_score` 字段，永远不会有。

---

## 5. 增加一个 provider

1. **确认它在 inventory 里是 WIRED。** 没接线的实现不进基准（`PROVIDER_INVENTORY.md` §4）。
2. **跑一次真实调用并把原始响应存成 `<recorded>/<name>/<case_id>.json`。**
   如果它的信封不在上表里，在 `normalize.py` 加一个分支 —— 只加**一个**分支，
   不要在这里做评分逻辑。
3. **在 `tests/test_benchmark_normalize.py` 加一个 fixture**，用真实响应的**结构**
   （字段名与嵌套），不含真实数据。
4. 重跑 `validate` + `score`，确认新 provider 出现在表里且失败清单为空。

**不要在 `metrics.py` 里为新 provider 开特例。** 需要特例说明它和其它 provider 量的
不是同一件事 —— 那种情况应当换成两条不同的指标，并写进标准。

---

## 6. 增加一条用例

1. 挑 layer 目录，按对应 schema 写一行 JSONL。
2. `label_status` 先写 `draft`，`validation_timestamp` 写实际时间。
3. 跑 `validate`。
4. **不要**为了让某个 provider 好看而改金标。金标的合法性只由
   `GROUND_TRUTH_GUIDE.md` 决定。

JSONL 选择的原因：用例会被增删改好几个月，第 800 行的一个语法错误不该让前 799 行
一起失效。

---

## 7. 离线保证

`tests/test_benchmark_is_offline.py` 用 AST 检查 `benchmark.runner` 的**整个 import
闭包**，凡是出现 `httpx` / `requests` / `socket` / `urllib.request` / `aiohttp` 就红。
判据同 `tests/test_registration_is_offline.py`：不是"没人调用网络"，是"根本 import
不到网络模块"。

另有 `test_benchmark_cli.py` 用 `--recorded` 指向临时目录跑完整 `score`，全程无 socket。

**为什么用 import 闭包而不是禁网 guard**：禁网 guard 只能证明"这次没发出去"，
import 闭包能证明"这个包里不存在发出去的能力"。后者才是可以写进标准的性质。

---

## 8. 从离线切到 live 的唯一改动

```
recorded 目录由谁写出来
```

`score` 的代码一行不改。这就是为什么 `_meta.latency_ms` / `_meta.cost_usd` /
`from_cache` 放在响应里而不是记在 manifest 里 —— **每个用例有自己的延迟和成本**，
manifest 只有 run 级别的汇总。

live 采集器要做的事（Phase 2 实现，见 `PHASE2_LIVE_PLAN.md`）：

1. 读数据集，逐条调用 provider，**原样**落盘；
2. 记录 `_meta`：`latency_ms`、`cost_usd`、`from_cache`、`cache_age_s`、实际参数、
   `http_status`；
3. 遇 429 等待重试，记 `error_code="rate_limited"`，**不记失败**；
4. 冷/热分开：同一 identity 本轮首次出现 = 冷；
5. 每轮结束写 `manifest.json`，含额度快照。

---

## 9. 常见错误

| 现象 | 原因 | 处置 |
|---|---|---|
| `unrecognised search envelope` | 新 provider 的信封没被 `normalize` 认识 | 加分支 + 加 fixture |
| 某 arm 的 `cases` 为 0 | recorded 目录名与预期不符 / 文件没落盘 | 列目录确认 |
| `n/a` 满屏 | 金标为空（pilot 的 PDF/社交/长尾用例就是这样） | 正常。补金标前不要解读 |
| 计分卡里 `judge: none` | 没传 `--verdicts` | 正常。Phase 1 全程如此 |
| `checksum` 变了 | 数据集改了 | 旧分数失效，这是设计 |
| 两次 `score` 结果不一致 | 读到了不同的 recorded 目录 | 计分卡里记的是 checksum，不是路径，需自己核对 |
