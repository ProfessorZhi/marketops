# MarketOps Search & Fetch Benchmark Standard v1 —— 实施报告

分支：`feat/provider-benchmark-v1`（工作区只有新增文件，未改动任何已跟踪文件）
本阶段的交付物**不是结论，是量具**：一套能长期复用、供应商无关、可离线复现的评测框架。
**没有任何一次收费 API 调用**，没有任何 provider 排名，没有改动生产逻辑或配置。

---

## 0. 一句话结论

标准、数据结构、Runner 骨架与离线验证已建立并通过测试。**仍缺的一样东西是人确认的金标** ——
pilot 的 62 条用例全部是 `draft`，所以在它升级为 `human_verified` 之前，这套框架能算出数字，
但那些数字**不能用于任何选型决策**（`README.md`「这个目录现在还不能做什么」）。

---

## 1. 已完成内容

### 1.1 Phase 0 · 审计

`benchmark/PROVIDER_INVENTORY.md`。判据只有仓库里的代码与测试，README 只用于对照。
结论按 DOCUMENTED / IMPLEMENTED / WIRED / TESTED / LIVE-VERIFIED 五格记录，逐条给出路径。

**核心结论**：接线的 provider 不少，但**没有任何一个在「检索质量 / 抓取质量」的意义上被量过**。
`LIVE-VERIFIED` 一栏里所有的 ⚠️ 都只表示「凭据与链路通」，一次 200 回包证明不了检索质量。

审计还产出了 10 条「不写进标准就一定会被算错」的事实（`PROVIDER_INVENTORY.md` §3.16），
例如：TinyFish 丢弃 `n`、DeepSeek 丢弃 `country`、maps 打 deepseek/tinyfish 会静默记一次成功、
插件缓存命中不记 ledger、MinerU 恒不可用。这些不是缺陷报告，是**评分口径的前提**。

### 1.2 Phase 1 · 三层标准

`benchmark/STANDARD.md`（398 行）：A 单 provider / B 组合与消融 / C 端到端。
三层**分别报告**，不存在把搜索结果质量与页面提取质量混在一起的指标。

### 1.3 Phase 2 · 数据集

`benchmark/datasets/pilot/`，JSONL，一行一例：

| 层 | 条数 | 市场 | split（development / validation / holdout） |
|---|---|---|---|
| search | 24 | FR 12 / GB 7 / US 5 | 11 / 7 / 6 |
| fetch | 28 | FR 10 / GB 9 / US 9 | 13 / 9 / 6 |
| e2e | 10 | FR 4 / GB 3 / US 3 | 4 / 3 / 3 |

search 覆盖 9 类任务（官方站/联系方式/名录/本地场馆/政府开放数据/PDF/社媒/长尾/时效），
fetch 覆盖 10 类（静态/名录页/JS 渲染/Cookie 弹层/PDF 文字层/重定向/断链/分页/
反爬挑战/空页）。**全部 62 条 `label_status = draft`。**

### 1.4 Phase 3 · 指标

`benchmark/runner/metrics.py`（738 行，纯函数、无 I/O、无网络）：

- Search：`precision_at_k`、`recall_at_k`、`reciprocal_rank`、`ndcg_at_k`、`unique_recall`、
  `official_source_rate`、`domain_diversity`、`duplicate_rate`、`market_language_accuracy`、
  `freshness_pass_rate`、`percentile`（p50/p95）、`cost_per_result`。
- Fetch：`fetch_case_scores`（HTTP 成功 ≠ 内容成功）、`aggregate_fetch`
  （useful content / shell-page false success / JS / PDF / email、phone 的 P-R / 链接与
  mailto 保留 / p50-p95 / 每次成功读取的成本）。
- E2E：`e2e_scores`、`aggregate_e2e`（实体 P-R、已验证联系人 P-R、归属、证据完整性、
  假阳性率、重复实体率、任务完成率、每个已验证实体/联系人的成本、端到端延迟）。
- 组合层：`marginal_contribution`、`ablation` / `ablate_cases`、`pareto_front`、`error_histogram`。

### 1.5 Phase 4 · Judge

`benchmark/runner/judge.py`（383 行）+ `JUDGE_CONTRACT.md` + `JUDGE_CALIBRATION.md`：

- 盲化由 `blind_labels(providers, salt)` 做确定性洗牌，标签是 `candidate_a…`，
  provider → 标签的映射**单独封存**（`write_sealed_mapping`），不进 payload。
- 七个维度、证据片段必须**逐字**出现在 payload 里（取前 120 字符比对）、必须有 confidence、
  允许 abstain 且 abstain 必须写理由。
- 校准 `CalibrationReport` 输出 agreement / FPR / FNR / MAE，按维度分开；
  判定分级：decided < 10 → `insufficient_sample`，agreement < 0.80 或 FPR > 0.20 →
  `uncalibrated`，否则 `calibrated`。

**注意**：`judge.py` 只构造 payload、校验 verdict、算校准，**它自己不调用任何模型**，因此
仍在离线闭包内。真正的调用方是 Phase 2 的采集器。

### 1.6 Phase 5–7 · 公平协议、互补性、长期因素

写进 `STANDARD.md` §4–§5：

- 同 query / 国家 / 语言 / top-k / 时间窗；记录实际发送的地区参数与原始响应；
  冷/热缓存分表；易波动任务 ≥3 次重复；不制造人为限流；
  参数不被支持记 `n/a` 而不是失败；结构性必败类别（扫描件、登录墙）单列。
- 「成功」以**是否发生 HTTP 请求**为准，不以 ledger 为准（deepseek/tinyfish 的 maps 分支
  返回 `[]` 却记一次成功）。
- 角色判定六档（Primary / Secondary / Specialist / Fallback / Not Recommended /
  Insufficient Evidence）+ Pareto 前沿 + 边际贡献，**不产出全局第一名的排行榜**。

### 1.7 Phase 8 · 15 项交付物

| # | 交付物 | 落点 |
|---|---|---|
| 1 | Benchmark Standard 文档 | `benchmark/STANDARD.md` |
| 2 | Provider Inventory | `benchmark/PROVIDER_INVENTORY.md` |
| 3 | Search Case Schema | `benchmark/schemas/search-case.schema.json` |
| 4 | Fetch Case Schema | `benchmark/schemas/fetch-case.schema.json` |
| 5 | End-to-End Case Schema | `benchmark/schemas/e2e-case.schema.json` |
| 6 | 初始 Pilot Dataset | `benchmark/datasets/pilot/{search,fetch,e2e}/pilot.jsonl` |
| 7 | Ground Truth 标注规范 | `benchmark/GROUND_TRUTH_GUIDE.md` |
| 8 | Jev Judge Contract | `benchmark/JUDGE_CONTRACT.md` + `schemas/judge-verdict.schema.json` |
| 9 | Judge Calibration 规范 | `benchmark/JUDGE_CALIBRATION.md` |
| 10 | Provider Runner 架构 | `benchmark/runner/` + `benchmark/EXECUTION_GUIDE.md` |
| 11 | Offline Test Suite | `tests/test_benchmark_*.py`（6 个模块，111 例） |
| 12 | Scorecard Schema | `benchmark/schemas/scorecard.schema.json` |
| 13 | JSON / Markdown 报告模板 | `benchmark/templates/report.template.md`、`scorecard.example.json` |
| 14 | Benchmark Execution Guide | `benchmark/EXECUTION_GUIDE.md` |
| 15 | Phase 2 Live Testing Plan | `benchmark/PHASE2_LIVE_PLAN.md`（**未执行**） |

Runner 可独立运行，不依赖生产任务调度：

```bash
python -m benchmark.runner.cli validate --dataset benchmark/datasets/pilot
python -m benchmark.runner.cli score --dataset benchmark/datasets/pilot \
    --recorded <recorded 目录> --out out/run.json
python -m benchmark.runner.cli report --scorecard out/run.json --out out/run.md
python -m benchmark.runner.cli calibrate --verdicts v.json --labels l.json
```

---

## 2. 文件变更

**全部是新增，没有任何已跟踪文件被修改。**

新增（`git status` 原样）：

```
?? benchmark/
?? tests/test_benchmark_cases.py
?? tests/test_benchmark_cli.py
?? tests/test_benchmark_is_offline.py
?? tests/test_benchmark_judge.py
?? tests/test_benchmark_metrics.py
?? tests/test_benchmark_normalize.py
```

`benchmark/` 内部：

| 文件 | 行数 | 作用 |
|---|---|---|
| `README.md` | 73 | 入口与「现在还不能做什么」 |
| `STANDARD.md` | 398 | 标准正文 |
| `PROVIDER_INVENTORY.md` | 291 | Phase 0 审计 |
| `GROUND_TRUTH_GUIDE.md` | 164 | 标注规范 |
| `JUDGE_CONTRACT.md` | 174 | judge 输入/输出/禁令 |
| `JUDGE_CALIBRATION.md` | 146 | 校准 |
| `EXECUTION_GUIDE.md` | 231 | 执行与扩展 |
| `PHASE2_LIVE_PLAN.md` | 162 | 真实调用计划（未执行） |
| `IMPLEMENTATION_REPORT.md` | 本文件 | 实施报告 |
| `runner/__init__.py` | 16 | 版本常量 `marketops.benchmark.v1` |
| `runner/models.py` | 310 | 用例加载/校验、`RecordedRun` 录像、`label_status` 计数 |
| `runner/normalize.py` | 134 | 厂商响应 → 标准形状的唯一接缝 |
| `runner/metrics.py` | 738 | 全部确定性指标，无 I/O |
| `runner/judge.py` | 383 | 盲化 payload / verdict 校验 / 校准 |
| `runner/report.py` | 454 | 计分卡装配 + Markdown + 消融表 |
| `runner/cli.py` | 385 | `validate` / `score` / `report` / `calibrate` |
| `schemas/*.json` | 5 个 | 2020-12（scorecard 341 行） |
| `templates/*` | 2 个 | 报告模板与计分卡示例 |

**未修改**：`marketops/` 下任何文件、`plugins/` 下任何文件、`pyproject.toml`、任何配置文件。

---

## 3. 测试结果

```
tests/test_benchmark_cases.py      15 passed
tests/test_benchmark_cli.py        17 passed
tests/test_benchmark_is_offline.py  9 passed
tests/test_benchmark_judge.py      31 passed
tests/test_benchmark_metrics.py    28 passed
tests/test_benchmark_normalize.py  11 passed
------------------------------------------------
111 passed
```

`python -m ruff check .`（仓库根，含既有代码）：**All checks passed!**

### 3.1 测试覆盖了什么（而不只是「跑过了」）

- **契约**：计分卡必须过 `scorecard.schema.json`；用例必须过各自 schema；JSONL 坏行报行号。
- **产物语义**：录了响应的 provider 恰好成一条 arm，目录里没匹配任何用例的**不**成 arm
  （否则「没测」会在表里变成「测了、0 分」）。
- **离线是能力，不是承诺**：`test_benchmark_is_offline.py` 对 7 个 runner 模块做 import 闭包，
  断言到不了任何网络能力、不 import `marketops`、加载时只用标准库。带**正对照、反对照、
  真源码投毒**三种自证 —— 见红、见绿的判据都验过，所以「见谁都报」或「见谁都不报」都过不了。
- **判据能翻红**：每一条关键断言都配了「把它弄坏必须红」的对照。例如缓存/报表的
  `test_the_check_can_actually_turn_red`、CLI 的「坏文件必须 exit 2」。

### 3.2 测试过程中发现并修掉的 5 个真实缺陷

这些不是为了让测试变绿而改的测试，是 runner 本身的 bug：

1. **`_structural_check` 拒掉全部合法用例。** 它拿包级 `SCHEMA_VERSION`
   （`marketops.benchmark.v1`）去比用例的 `schema_version`（`…case.v1`），两个不是一回事。
   没装 `jsonschema` 的机器上**每一条**合法用例都被拒。删掉那条早检查，交给通用的 `const`。
2. **`FetchArm.metrics()` / `E2EArm.metrics()` 永久为 `null`。** 它们对 per-case 的原始信号
   （`ok`、`shell_page`、`requires_js`）按比率键求均值，于是
   `useful_content_rate`、`shell_page_false_success_rate`、`js_coverage`、`pdf_coverage`、
   `task_completion_rate` 恒为 `n/a` —— **一张显示不出「付钱买空壳」的计分卡等于没有**。
   改为委托 `aggregate_fetch` / `aggregate_e2e`。
3. **空数据集假绿。** 路径打错时 `load_dataset` 返回空 `Dataset`，`validate` 打印
   「ok: 0 search, 0 fetch, 0 e2e」并以 0 退出。改为目录不存在、没有用例文件都抛错。
4. **计分卡不记录金标状态。** 用 `draft` 数据集算出的分数与用人工确认过的数据集算出的
   分数**在产物上无法区分** —— 而这套框架的全部纪律就是把 pilot 保持在 draft 直到人确认。
   已在 `dataset.label_status` 里按状态计数，schema 设为 required，Markdown 顶部显式告警
   「非 human_verified 的数字不可用于选型」。schema 的 required 有翻红对照测试。
5. **纯实体用例永远判不完成。** `acceptance.evidence_required` 在「没声称任何联系人」时
   拿到 `None`，`None or 0` 得 0，于是这类用例结构性必败。改为证据完整性为 `None` 时
   该规则**空满足**（vacuous），两个方向各钉一条测试。

---

## 4. 验收十问

**Q1. MarketOps 当前究竟接入了哪些 Search / Fetch Provider？**

两套独立实现（核心 `marketops/` 与插件 `plugins/research-tools/marketops-research-assistant/`，
不共享代码，只共享配置形状）。

- Search —— 核心：Serper（唯一接进应用层的）。插件：serper / serpapi / deepseek / tinyfish
  四个引擎（后者含 maps）。
- Fetch —— 核心：Crawl4AI、httpx。插件：crawl4ai / tinyfish / deepseek / http / download /
  facebook（公开主页）。另加付费 TinyFish Agent（browse）。
- 解析 —— 核心：`text_pdf`（pypdf）；MinerU 作 PDF 兜底（恒不可用）。
- 判定 —— Jev（核心与插件各一处）、Laya。
- 存在但没接线：`GuardedHttpClient`；有实现没入口：`run_shadow`。

**Q2. 哪些只是文档声明，哪些实际可以调用？**

没有一个是「只有文档」。但**有五类不能当能力用**，每条都有代码证据：

| 项 | 状态 | 含义 |
|---|---|---|
| `GuardedHttpClient` | IMPL + TESTED，**NOT WIRED** | 全仓无 import，不进评分表 |
| MinerU | WIRED，但 `_CALL_POINT_CALIBRATED = False` | `available()` 恒假，「PDF 兜底率」恒 0 且是刻意的 |
| TinyFish Agent | 仅 `paid_agent=True`，无测试无实测 | 按步计费，成本口径与列表估值不是一回事 |
| `run_shadow` | 有实现无 CLI/MCP 入口 | 唯一已有的 A/B 机制，但比的是决策器，不是 provider |
| 扫描件 PDF | **无任何适配器** | 这一类结构性必败，必须单列 |

**README 与代码的差异**：以代码为准，逐条记在 `PROVIDER_INVENTORY.md` §3.16（10 条）。

**Q3. 每个 Provider 应该如何公平测试？**

`STANDARD.md` §4。Search：同原始 query、同国家、同语言、同 top-k、同时间窗，
记录实际发送的地区参数与原始响应，**不允许某个 provider 用人工优化过的查询**。
Fetch：同一 URL 集合、同内容目标、同超时与重试规则，明确区分 HTML / Markdown / 结构化数据，
每个 reader 独立测。共性要求：冷/热缓存分表、记录全部失败与错误类型、记录真实费用与配额、
易波动 ≥3 次重复、不因并发过高制造限流失败、记录测试时点与 provider 版本。

**Q4. Jev 如何评价，如何验证 Jev 自己的判断可靠？**

评价：盲化（确定性洗牌 + `candidate_a…` 标签 + 封存映射）、七维度、每个判断带逐字证据与
confidence、证据不足可 abstain。禁止：因为文字流畅判正确、把没有来源的推断判为事实。

验证：**与人工标签独立校准**（`calibrate` 子命令）。输出 agreement / FPR / FNR / MAE，
按维度分层，abstention 计入样本但不计入 agreement。分级：decided < 10 → 样本不足；
agreement < 0.80 或 FPR > 0.20 → 未校准；否则已校准。**未校准的 judge 只能作为发现问题的
工具，不能作为计分依据。**

**Q5. 怎样建立 Ground Truth？**

`GROUND_TRUTH_GUIDE.md` 三条硬规则：人确认（模型不得生成 GT，更不得用同一个 Jev 生成再用它
评分）；联系地址必须带**印着它的那一页**；不确定就标 `disputed`，不猜。
流程 = 逐条标注 → 双人复核 → 分 development / validation / holdout（避免针对公开测试答案
过度优化）。当前 pilot 62 条全部 `draft`，**这是 Phase 2 的第一道前置条件**。

**Q6. 怎样测量不同 Provider 的互补性？**

`metrics.py` 的 `unique_recall`（别的臂都没找到的有效结果）、`marginal_contribution`
（加入某臂带来的增量）、`ablation` / `ablate_cases`（逐个移除后掉多少）、`pareto_front`
（质量/成本前沿）、`error_histogram`。组合比较不预设结论；`SPEC` 里点名的几组组合只是
待测对象，不是推荐名单。

**Q7. 怎样评价 MarketOps 完整业务交付？**

C 层：Search → URL 发现 → Fetch → 抽取 → 实体归并 → 证据 → 交付，按最终交付物评分，
不是按中间 API。指标见 `aggregate_e2e`（实体 P-R、已验证联系人 P-R、归属、来源归属、
证据完整性、假阳性率、重复实体率、任务完成率、每个已验证实体/联系人的成本、端到端延迟）。
**分别报告，不加权成一个总分** —— 计分卡里没有 `overall_score` 字段，将来也不会有。

**Q8. 哪些指标决定工具是否值得采用？**

不能按平均分排序。决定性的是：① 只有它能做的任务（必败类别的覆盖，如 XLSX 导出、扫描件）；
② 独有有效结果与边际贡献；③ 单位有效结果的成本与 p95 延迟；④ 参数不支持时是否诚实（记不记
`n/a`）；⑤ 故障时的降级能力；⑥ 地域语言覆盖；⑦ 维护成本与锁定风险。最终落到六个角色标签，
并明确允许「不同任务用不同 provider」。

**Q9. 怎样控制测试费用？**

四道闸：① 本阶段**零**真实调用，全部走 `--recorded` 回放；② `score` 模式在计分卡里强制标注
`run.mode = "replay"`、`judge: none` 与 `dataset.label_status`，不可能把回放结果当成实测，
也不可能把 draft 金标算出来的分数当成结论；③ `PHASE2_LIVE_PLAN.md`
给出逐 provider 的调用条数与成本/额度估算，并写了停止条件（额度触顶、错误率超阈、
预算到点即停）；④ 成本口径分开：API 单价按 `PRICES`，TinyFish Agent 按**步数**，
自有浏览器（crawl4ai / httpx）成本是 CPU 不是钱，三者不直接相加。

**Q10. 怎样保证未来新增 Provider 也能用同一套标准？**

接缝只有一处：`runner/normalize.py` 里加一个 `provider` 分支，把厂商响应翻译成标准形状；
录像按 `<recorded>/<provider>/<case_id>.json` 原样落盘；**`metrics.py` 一行不用改**。
随后自动获得：同一批用例、同一批指标、同一套消融与计分卡。加 provider 的完整步骤在
`EXECUTION_GUIDE.md` §5；判据在 `test_benchmark_normalize.py`（未知 provider 必须报错，
不得静默返回空）与 `test_benchmark_cli.py`（录像目录 → arm 的一一对应）。

---

## 5. 尚未完成内容

**本阶段明确不做、也确实没做的：**

1. **没有人工金标。** 62 条全部 `draft`，`validation_timestamp` 是占位值。
   `STANDARD.md` §5 的 Insufficient Evidence 门槛是「每能力每市场 ≥30 条 human_verified」，
   pilot 远低于此。**任何数字现在都不能作决策依据。**
2. **没有真实调用，没有排名。** 没有一次收费 API、没有消耗生产配额。
3. **没有 live 采集器。** runner 只有 replay 路径；`--recorded` 的录像由 Phase 2 的采集器
   产生，采集器尚未实现（`PHASE2_LIVE_PLAN.md` §3 列了它要做什么）。
4. **judge 未接真实模型。** 只实现了 payload 构造、verdict 校验、校准统计。
5. **pilot 覆盖有缺口**（诚实列出）：fetch 缺 `pdf_scanned`（扫描件）、Facebook 公开页、
   懒加载、联系方式专页这几类；fetch 的 `difficulty` **全部是 `easy`**，没有 medium/hard；
   e2e 10 条场景里没有「跨来源验证 / 归属判断」的对抗样例。
6. **全量测试套件跑不通**，原因与本次工作无关：本机安装的 `mcp` 是 1.26.0（提供 `fastmcp`），
   仓库声明 `mcp>=2.2,<3`（提供 `mcp.server.mcpserver`），41 个既有测试在收集阶段
   `ModuleNotFoundError`。**我没有为此改动环境**，也没有把它算进本次结果。

---

## 6. 设计决策

1. **`benchmark/` 是独立顶层包，不进 wheel。** `pyproject.toml` 已声明
   `packages = ["marketops"]`，所以它天然被排除。基准必须能在生产代码大改之后仍然跑得起来。
2. **runner 不 import `marketops`。** 判据在 `test_the_runner_does_not_import_the_production_package`。
   耦合的方式很隐蔽：评分口径跟着生产代码漂，而没人会发现。
3. **离线是「拿不到网络能力」，不是「不调用」。** 用 AST import 闭包证明，而不是行为承诺。
   复用 `tests/test_registration_is_offline.py` 的 `_network_reach`，不另写一份更弱的。
4. **`null` 与 `0` 严格区分。** 算不出来是 `None`。「没观测到」与「观测到是零」在业务上相反，
   合成一个数就再也拆不开。
5. **确定性指标与语义判断分开，永不加权。** 没有 `overall_score`。加权需要价值判断，
   而价值判断写进公式就不可见了。
6. **只有 WIRED 的实现进入评分。** 没接线的代码没有延迟、成功率、覆盖率 —— 给它打分
   等于给设计稿打分。
7. **厂商响应原样存储。** 回放若存解析后的摘要，就永远抓不到解析器回归。
8. **schema 校验有两条路径**：装了 `jsonschema` 用 Draft202012Validator，没装用内置子集校验器。
   保证裸 Python 3.10+ 无第三方依赖也能跑离线套件 —— 代价是子集校验器必须自己钉死
   「它到底支持哪些关键字」，这一点写在 `_structural_check` 的 docstring 里。
9. **每个测试模块的断言都带「翻红对照」。** 判据只在一侧被验证过，就等于没有验证。

---

## 7. 风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | pilot 未确认就出数字 | 结论系统性错误 | 计分卡的 `dataset.label_status` 是 schema **required**；Markdown 顶部对非 `human_verified` 显式告警；README 明写「现在还不能排名」 |
| 2 | 样本量小（FR 12 条 search） | 统计不显著 | 报 `insufficient_sample`，不报显著性结论 |
| 3 | 环境漂移（`mcp` 1.26 vs `>=2.2`） | 全量测试无法收集 | 已定位、已记录；**未擅自改环境** |
| 4 | 缓存口径混淆（插件命中不记 ledger） | 成本与 p95 系统性偏低 | 冷/热分表 + 每次记录 `from_cache` / `cache_age` |
| 5 | ledger 成功口径不可信 | 成功率虚高 | 「有没有发生 HTTP 请求」才是判据 |
| 6 | TinyFish Agent 按步计费 | 成本被低估 | 成本表分口径，禁止与单价直接相加 |
| 7 | URL 归一化影响命中判定 | Precision/Recall 偏移 | 归一化规则单列（`normalize_url`），有独立测试 |
| 8 | judge 未校准即被使用 | 语义分数不可信 | 校准分级；未校准只作问题发现 |
| 9 | 真实调用触发限流/封禁 | 数据不可比、账号受损 | 协议禁止制造人为限流；Phase 2 设停止条件 |

---

## 8. 下一阶段真实 API 测试所需条件

前置（缺一不可）：

1. **执行授权与预算上限。** `PHASE2_LIVE_PLAN.md` §3 列了逐 provider 的调用条数，§4 是成本
   与额度估算。**没有明确授权就不发任何收费请求。**
2. **金标升级。** 62 条 `draft` → `human_verified`。这是 Phase 2 唯一无法自动化的工作，
   也是它的前置条件。建议先只升 development 集，用小批量校准采集器。
3. **采集器实现。** 覆盖 §3 的三种响应形状，原样落盘 + `manifest.json`（版本、地区参数、
   额度、时间戳、`from_cache`）。
4. **凭据卫生。** key 只在运行期从密钥中心取，不落仓库、不进日志、不打印。
5. **三件需要用户决定的事**（`PHASE2_LIVE_PLAN.md` §9）：授权范围与预算；serpapi 的 e2e 处置；
   金标由谁升。

技术前置：

6. 修好 `mcp` 版本漂移，或明确以 `--continue-on-collection-errors` 作为本机验收口径
   （**不是**把 41 个错误当通过）。
7. 选定**与生成金标不同的** judge 实例，并先在人工样本上跑一次校准。

---

## 9. 与禁令的对照

| 禁令 | 状态 |
|---|---|
| 不发起真实收费 API 测试、不消耗生产配额 | ✅ 零调用 |
| 不修改生产业务逻辑 / 生产配置 / 不部署 | ✅ `git status` 只有新增 |
| 不读取、复制或输出 API Key / Cookie / Token | ✅ 未读取任何凭据 |
| 不提交或推送 | ✅ 未 commit、未 push |
| 不访问东京 11GB 语料、不扫描其他设备 | ✅ |
| 不提前做收费 Provider 排名 | ✅ 框架无排名字段，pilot 全 `draft` |
