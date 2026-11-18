# A6000 本地运行时实验室 —— 交接文档

> **本仓库是 public。** 本文只写代码、路径、方法和结论，不写内网地址、不写凭据、
> 不贴原始请求/响应。
>
> **同步状态**：本文描述的是「Windows 侧已确认的事实 + 尚未从原始机器取回的 artifacts」。
> 哪些已提交、哪些仍缺失，以同目录的 `SYNC_MANIFEST.json` 为准 —— 它有机器可读的
> `missing_artifacts` 字段。

---

## 1. 项目目标

把 MarketOps 的 provider 评测从「一张印象」变成「一次可复现的测量」，并在 A6000 上
建一个**本地运行时实验室**：不依赖外部 API 就能跑的抓取/解析能力（Crawl4AI Local、
MinerU Local GPU），以及一个能对外部 provider 做受控对比的 runner。

要回答的问题按三层拆开（定义见 `../STANDARD.md`）：

| 层 | 问题 | 现在的状态 |
|---|---|---|
| A | 单个 provider 在单一能力上有多好？ | 框架完成，**金标仍是 `draft`** |
| B | 组合/消融下谁真正贡献了结果？ | 框架完成，**未执行** |
| C | 装进业务链路端到端能不能用？ | 只有 10 条 `draft` 用例，**未执行** |

**这不是一份选型结论。** Phase 2 已经跑过 180 次真实调用（见 §4），但金标没升级，
所以任何「谁更好」的陈述都不成立。

---

## 2. 当前 GitHub 基线

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/ProfessorZhi/marketops.git`（**public**） |
| 默认分支 | `main` |
| 基线分支 | `feat/provider-benchmark-v1` |
| 基线 commit | `61fa275054f3ab589ed67c5471c342ee97178eb6` |
| 对应 PR | **#111**（`OPEN`，目标 `main`，**未合并**） |
| 本分支 | `feat/a6000-local-runtime-lab`（从上面的基线 commit 切出） |

**PR #111 是基线，不是要改的对象。** 本分支的约束：

- 不修改 `feat/provider-benchmark-v1`；
- 不 force push；
- 不合并 PR #111、不改 `main`。

> PR #111 的 `mergeStateStatus` 是 `BLOCKED`（保护规则/审查未满足），
> `mergeable` 是 `MERGEABLE`。这两个字段含义不同，不要互相代替。

---

## 3. 已有测试集规模

数据集在 `../datasets/pilot/`，**全部 62 条都是 `draft`**，无一条 `human_verified`。

| 层 | 文件 | 条数 |
|---|---|---|
| search | `datasets/pilot/search/pilot.jsonl` | **24** |
| fetch | `datasets/pilot/fetch/pilot.jsonl` | **28** |
| e2e | `datasets/pilot/e2e/pilot.jsonl` | **10** |
| 合计 | | **62** |

按 `split` 分布：

| split | search | fetch | e2e |
|---|---|---|---|
| `development` | 11 | 13 | 4 |
| `validation` | 7 | 9 | 3 |
| `holdout` | 6 | 6 | 3 |

校验和（`validate` 命令输出，覆盖全部用例的规范化内容）：

```
ok: 24 search, 28 fetch, 10 e2e  (checksum 28157ca3b4a9ac31)
```

每个文件的 SHA256 记在 `SYNC_MANIFEST.json` 的 `datasets.hashes` 里。

**已知的结构性缺口**（不是数字问题，是覆盖问题）：

- fetch 的 `difficulty` **全部是 `easy`**，没有 `medium`/`hard`；
- `pdf_scanned`（扫描件）**没有任何适配器能处理** —— MinerU 恒不可用（见 §8）；
- Facebook 公开页、懒加载、联系方式专页这几类没有用例；
- e2e 的 10 条里没有「跨来源验证 / 归属判断」的对抗样例。

---

## 4. 已有真实 Pilot 结果

**2026-11-18 在 A6000 上执行。**

| 项 | 值 |
|---|---|
| Search 调用 | **96** |
| Fetch 调用 | **84** |
| e2e 调用 | **0（未执行）** |
| 合计 | **180** |
| 总成本 | **$0.4230** |
| 金标状态 | 62 条**全部 `draft`** |

### 4.1 四条观察（线索，不是结论）

1. **SerpApi：Google 重定向 URL 包装。** 一部分返回值是 `google.com/url?...` 形式的
   跳转包装，不是目标页地址。这是**必须在计分前处理的规范化缺口**；在解包之前，
   URL 层面的指标量的是包装层，不是结果。
2. **TinyFish：fetch 恢复表现好。** 在失败/受限用例上有实际价值。样本量与难度分布
   都不足以支撑「可替代核心抓取器」。
3. **Serper：重要的低成本 Search 候选。** 值得进下一轮重点验证。单价低 ≠ 单位有效
   结果成本低 —— 后者需要 `human_verified` 金标才算得出来。
4. **DeepSeek Search：证据不足。** 有效样本量不够，任何方向（包括「不好」）都不成立。

### 4.2 为什么不能升级为结论

- **金标是 `draft`。** `../STANDARD.md` §5 的门槛是「每能力每市场 ≥ 30 条
  `human_verified`」。本轮是在 `../PHASE2_LIVE_PLAN.md` §1 第 1 条前置条件**不成立**
  的情况下执行的 —— 这是已知偏离，必须写在报告里，不能悄悄补上。
- **e2e 未执行。** 零件分数加总不等于装配效果，`../STANDARD.md` §2 明确禁止
  `overall_score`。
- **样本有结构性缺口**（见 §3 末尾）。

### 4.3 原始 artifacts 的位置

运行机（A6000）上：

```
/data/home/vince/artifacts/skills/marketops-benchmark/2026-11-18/recorded-pilot/
```

包含 8 个文件：

```
LIVE_SEARCH_REPORT.md
LIVE_FETCH_REPORT.md
PROVIDER_COMPLEMENTARITY.md
RUN_MANIFEST.json
scorecard.json
scorecard.md
scorecard.ablation.md
quota_snapshot.json          # 第 7 个
serpapi_url_resolution.json  # 第 8 个
```

**本次同步时这些文件不在 Windows 侧，因此没有进入仓库。**
它们被列在 `SYNC_MANIFEST.json` 的 `missing_artifacts` 里，状态是**待从原始机器取得**。

> 不要把「仓库里没有」读成「没跑过」，也不要把「跑过」读成「已复核」。
> 在 artifacts 取回并复核之前，§4.1 的四条只是线索。

---

## 5. 目前未解决的问题

按「挡住什么」排序：

1. **金标未升级（挡一切排名）。** 62 条全 `draft`。需要双人复核走完
   `../GROUND_TRUTH_GUIDE.md` 的流程，先升 `development` 子集。
2. **live 采集器未实现（挡「复算」）。** runner 目前**只有 replay 路径**；
   `--recorded` 目录要靠采集器产出，而采集器不存在（要做的事列在
   `../PHASE2_LIVE_PLAN.md` §3）。**没有采集器就没有原始字节保证，那批调用的钱
   不可复算。**
3. **SerpApi 重定向解包缺失。** 见 §4.1 第 1 条。解包逻辑要有测试，且要能区分
   「包装层 URL」与「真实目标 URL」。
4. **fetch 难度分布退化。** 全 `easy`，量不到 provider 在困难页上的差异。
5. **e2e 层零证据。** 10 条 `draft`，未执行。
6. **judge 未接真实模型。** 只实现了 payload 构造、verdict 校验、校准统计。
   `../JUDGE_CALIBRATION.md` 的校准必须在人工样本上先跑一次，且 judge 实例
   **必须与生成金标的实例不同**。
7. **本机依赖漂移（Windows 侧）。** 本机 `mcp` 是 1.26.0（提供 `fastmcp`），
   仓库声明 `mcp>=2.2,<3`（提供 `mcp.server.mcpserver`），41 个既有测试在收集阶段
   报错。**这不是「通过」**，验收时要么修版本，要么显式用
   `--continue-on-collection-errors` 并说明口径。
8. **Windows 本机 16 GB 内存天花板。** 跑浏览器/GPU 相关验证要放到 A6000 上，
   不要在本机硬扛。

---

## 6. A6000 后续工作

按依赖顺序，前一项不做，后一项的产出不可信：

| # | 工作 | 完成判据 |
|---|---|---|
| 1 | 取回 §4.3 的 8 个 artifacts 并复核 | 落进仓库（脱敏后）或明确记录不可得 |
| 2 | 装好 `.[browser]` 并跑通 live 出口测试 | `tests/test_browser_egress_live.py` 两条用例真的跑（不是 skip），见 §7 |
| 3 | 实现 live 采集器 | 能产出 `--recorded` 目录，含 `manifest.json` 与 `_meta` |
| 4 | 用采集器重跑 §3 的 `development` 子集 | 原始字节可复算，成本可对账 |
| 5 | 人工金标升级 `development` 子集 | `validate` 的 `label_status` 计数里出现 `human_verified` |
| 6 | judge 校准 | 与人类标签的一致率达到 `../JUDGE_CALIBRATION.md` 的等级要求 |
| 7 | 部署 MinerU（GPU） | `mineru_fallback_ready()` 为真，见 §8 |
| 8 | 执行 e2e 层 | 有 C 层证据，且仍然**没有** `overall_score` |

**不要跳步。** 尤其不要把第 4 步的结果直接当第 5 步的替代 —— 采集器量的是
provider 输出，金标量的是「正确答案」，两者不能互相顶替。

---

## 7. Crawl4AI Local 部署要求

代码位置：`marketops/adapters/crawl4ai_fetcher.py`（`Crawl4AIBrowserFetcher`）。

### 7.1 依赖

- 可选组：`pyproject.toml` 的 `browser = ["crawl4ai>=0.6"]`
  → `pip install -e ".[browser]"`；
- 文档记录的验证版本：`crawl4ai 0.9.3` + `playwright 1.63.0`（见
  `docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`）。版本漂移时必须重跑 live 测试；
- **Chromium 必须真的下载**。`PLAYWRIGHT_BROWSERS_PATH` 应钉到**安装目录内**的
  `browsers/`；不显式设置时会落到平台默认缓存（`$HOME/.cache/ms-playwright`），
  此时运行期会如实报 `source: platform_default`。

### 7.2 出口是承重配置，不能省

浏览器这一层**没有**第二个出口。`_browser_config()` 必须把
`BrowserConfig(proxy_config={"server": proxy.url})` 指到
`marketops/adapters/egress_proxy.py` 起的本机正向代理。三条要点：

- **用 `proxy_config`，不用 `proxy`。** 该版本 `BrowserConfig.proxy` 已 deprecated，
  且在 `browser_manager.py` 的一条路径上被忽略 —— 照抄字段名会得到「配了但没接上」
  的部署，而它看起来完全正常。
- **`--proxy-bypass-list=<-loopback>`**：关掉 Chromium 对回环地址的隐式绕过。
  不加这一格，页面可以直接连本机服务，而那条路在判据里**没有任何痕迹**。
- **`--disable-quic`**：UDP/HTTP-3 那条路的**防御**，本机未实测（没有可构造的
  HTTP/3 目标）。写完它不要顺手宣称 HTTP/3 已被挡住。

`headless` 与 `user_agent` 必须与浏览器**一起**构造，不能分两处拼 —— 否则
「这次抓取用的是哪个 UA」会在某条路径上悄悄不同。

### 7.3 验收

```bash
python -m pytest tests/test_browser_egress_live.py -v
```

两条用例是**真的**开浏览器 + 真的起本机服务：一条验「页面连不到本机服务」
（本机服务上应当**一次连接都没有**），一条验「渲染后的页面拿到的是渲染结果，
不是占位符」。它们带 `crawl4ai_available()` 的 skipif —— **skip 不算通过**，
A6000 上必须是 `2 passed`。

### 7.4 这一层没有宣称做到的事

- **不做 TLS 中间人**：https 是隧道，出口看得见「连了哪个 host」，看不见隧道里的 URL；
- **落点检查只是检测**（`_fetch_async` 里渲染后的 `check_target(final_url)`）：
  它挡的是「不把落点不合规的渲染写成一条来路清白的研究证据」，不是「那一跳没发生过」；
- **不做指纹/反检测**，匿名浏览不等于不被识别（见 `tests/test_no_evasion.py`）。

---

## 8. MinerU Local GPU 部署要求

代码位置：`marketops/adapters/mineru_adapter.py`。

### 8.1 今天的真实状态

- 可选组：`mineru = ["mineru>=2.0"]` → `pip install -e ".[mineru]"`；
- **`_CALL_POINT_CALIBRATED = False`**，`_run_mineru()` **无条件**抛
  `ParserUnavailableError`；
- 所以 `mineru_available()`（问「装了没」）可能在装完之后翻真，而
  `mineru_fallback_ready()`（问「能不能用」）仍然是假。`default_router()` 问的是
  后者 —— **这是刻意的**：写一个看起来能跑、实际把 PDF 解析成垃圾的调用点，
  比明确不接它更坏。

### 8.2 部署要求

| 项 | 要求 |
|---|---|
| GPU | 需要。A6000 48G 上跑，不要在 Windows 本机 |
| 权重 | 数 GB，**不得落在 `$HOME` 下**（宿主策略禁止在 HOME 建大缓存）—— 必须落在 `DATA_ROOT` 之内 |
| 中间产物 | `MinerUDocumentParser(output_dir=...)` 必须由配置注入（通常在 `settings.mirrors_dir` 之下），**不得写死** |
| 调用点 | 按**当时版本**的 MinerU API 实现 `_run_mineru()`；MinerU 的 Python API 小版本间变动较大，不要照抄旧签名 |
| 校准 | 用 `tests/fixtures/` 里的**真实**复杂 PDF 样本验证产出的 markdown 对不对 |
| 翻开关 | 只有真实样本验证通过后，才把 `_CALL_POINT_CALIBRATED` 改成 `True` |
| 链接抽取 | `extracts_links = False` 保持到「有第一份真的 markdown 可以对着写」为止。在那之前从印象出发写的抽取规则会产出一批没人验证过的地址，而它们会一路走进证据 |

### 8.3 验收

```bash
python -m pytest tests/test_mineru_fallback.py -v
```

该测试直接改 `_mineru_do_parse` 与 `_CALL_POINT_CALIBRATED` 两个模块级事实，
不去碰真依赖。**它通过只证明契约正确，不证明 MinerU 装好了。** 部署验收还要：

1. `get_capabilities` 里 `mineru.runtime_ready` 翻真（装了）；
2. `mineru.fallback_ready` 也翻真（能用）；
3. 拿一份真实扫描件 PDF 跑一次，人工看产出的 markdown。

第 3 条是唯一能证明「接上了」的证据。

---

## 9. 迭代测试集的规则

1. **数据集与 provider 无关。** 用例里不出现某个 provider 的字段名。provider 之间的
   差异只在 `normalize.py` 这一层收口 —— **它是唯一的接缝**，别处不得出现
   `if provider == ...`。
2. **只给 WIRED 的实现打分。** 存在但没接线的代码（`GuardedHttpClient`）、恒不可用的
   兜底（MinerU）、没有入口的机制（`run_shadow`）进 `../PROVIDER_INVENTORY.md`，
   不进排行榜。
3. **算不出来一律 `null`，不记 0。** `n/a`（没观测到）与 `0`（观测到是零）在业务上
   是相反的结论。
4. **不引入 `overall_score`。** 加权需要价值判断，而价值判断一旦写进公式就不可见了。
   三层分开报。
5. **评分只走 replay。** `score` 读 `--recorded` 目录，不现场调 provider。
   recorded 布局见 `../EXECUTION_GUIDE.md` §3：
   `<recorded>/<provider>/<case_id>.json` + `manifest.json`
   （`run_id`、`started_at`、`marketops_version`、`git_commit`、
   `providers.*.region_params_sent`、`api_quota_used`）。
6. **加用例的流程**：写进 `datasets/pilot/<layer>/pilot.jsonl` → 过
   `schemas/<layer>-case.schema.json`（JSON Schema 2020-12）→ 跑 `validate` →
   把新的 `checksum` 与文件 SHA256 更新进 `SYNC_MANIFEST.json`。
7. **`holdout` 不参与迭代。** 调参、改 prompt、选 provider 只看 `development`；
   `validation` 用于确认；`holdout` 只用于最终报告，看多了就废了。
8. **金标状态是事实，不是标签。** `label_status` 从 `draft` 升到 `human_verified`
   必须走 `../GROUND_TRUTH_GUIDE.md` 的双人复核，不允许为了「让数字好看」跳级。
9. **阈值不许事后改。** `../STANDARD.md` 里的门槛在执行前定死；要改就新开一轮，
   并在报告里同时给出旧口径的结果。
10. **成本口径唯一。** 可比的只有 `cost_per_100_relevant_results_usd`，且它需要
    `human_verified` 金标。单价不是成本。

---

## 10. 如何运行现有 Benchmark

全部命令在仓库根目录执行，**不需要任何凭据**（离线）。

### 10.1 环境

- Python **≥ 3.11**（`pyproject.toml` 的 `requires-python`）；
- runner 是纯 Python，**无第三方依赖**。

### 10.2 校验数据集

```bash
python -m benchmark.runner.cli validate --dataset benchmark/datasets/pilot
```

期望输出：

```
ok: 24 search, 28 fetch, 10 e2e  (checksum 28157ca3b4a9ac31)
```

`checksum` 变了就是数据集变了 —— 要么是你要的，要么是意外，先弄清再提交。

### 10.3 用已记录的 provider 输出算分

```bash
python -m benchmark.runner.cli score \
    --dataset benchmark/datasets/pilot \
    --recorded <recorded 目录> \
    --out out/run.json
```

`<recorded 目录>` 的布局见 `../EXECUTION_GUIDE.md` §3。
**没有这个目录时这条命令跑不了** —— 而它正是 §5 第 2 条要补的采集器的产出。

### 10.4 渲染报告

```bash
python -m benchmark.runner.cli report --input out/run.json
```

### 10.5 校准 judge

```bash
python -m benchmark.runner.cli calibrate --help
```

### 10.6 跑离线测试

```bash
python -m pytest tests/test_benchmark_*.py -q
```

期望 **111 passed**。如果本机的 `mcp` 版本漂移（§5 第 7 条），收集阶段会报错 ——
那**不是**通过，按 §5 第 7 条处理。

### 10.7 看版本

```bash
python -m benchmark.runner.cli --version
```

---

## 附：本文与其他文档的关系

| 想知道 | 看 |
|---|---|
| 指标怎么定义、什么算公平 | `../STANDARD.md` |
| 代码里到底有什么、哪些没接线 | `../PROVIDER_INVENTORY.md` |
| 金标怎么产生、谁能确认 | `../GROUND_TRUTH_GUIDE.md` |
| judge 能做什么、不能做什么 | `../JUDGE_CONTRACT.md`、`../JUDGE_CALIBRATION.md` |
| 怎么跑、怎么加 provider | `../EXECUTION_GUIDE.md` |
| 真实调用要多少、什么条件停 | `../PHASE2_LIVE_PLAN.md` |
| 实施过程与已知偏离 | `../IMPLEMENTATION_REPORT.md`（§10 是 Phase 2 补记） |
| 本次同步的清单与缺口 | `SYNC_MANIFEST.json` |
