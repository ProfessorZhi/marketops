# P0 环境审计 — Research Core 本地实验室 v2

审计时间：2026-11-19 10:05 +08:00
审计方式：只读。未启动、停止、重启任何服务，未下载任何镜像或模型。
结论先行：**基线完好，可以开始改造；但现有 harness 只覆盖 Search / Fetch / E2E
三层，Research Core 的其余七段（Brief、Extraction、Qualification、Identity、
Evidence、Channel、Dossier）没有任何用例、schema 或 runner。**

---

## 1. Git

| 项 | 实测值 |
|---|---|
| 分支 | `feat/a6000-local-runtime-lab` |
| HEAD | `abac0901bf4ca6a73cb6d8c48d501a676b33bc5c` |
| 远端同名分支 | 同为 `abac090`（本地/远端一致） |
| 工作区 | 干净，`git status --porcelain` 0 行 |
| 远端 | `https://github.com/ProfessorZhi/marketops.git` |

## 2. 服务状态

| 服务 | 端口 | 状态 | 依据 |
|---|---|---|---|
| MinerU 4.2.3 | 127.0.0.1:18000 | 运行中 | `/api/v1/health` → 401（要求 bearer，即进程在） |
| Crawl4AI Docker `mo-crawl4ai-local` | 11235 | 运行中 healthy | `/health` → `{"status":"ok",...,"version":"0.9.5"}` |
| SearXNG | 127.0.0.1:8888 | 运行中 | `GET /` → 200 |
| fixture server（local-runtime） | 127.0.0.1:18080 | 运行中 | pid 2370210 `fixtures/serve.py` |
| 静态文件服务 | 127.0.0.1:18081 | 运行中 | pid 2241480 `http.server` |
| PDF fixture 服务 | 127.0.0.1:18082 | 监听 | serve.py 的 `/pdf/link/*` 指向此处 |
| Caddy（公共入口） | 8081 | 运行中，**与本研究无关，未触碰** | |

**本轮不会重启以上任何一个。** 新 fixture 路由追加到 `serve.py` 后需要的是
**新端口上的新进程**（见 §7），而不是替换正在服务旧用例的那个进程。

## 3. 资源

| 项 | 实测 |
|---|---|
| GPU | RTX A6000，已用 6,639 MiB / 49,140 MiB |
| ├ MinerU | 752 MiB + 2,523 MiB = 3,275 MiB（两个 worker 进程） |
| ├ isaac venv（他人实验） | 2,235 MiB — **不动** |
| └ 图形栈 | 约 560 MiB |
| CPU | 20 核 |
| 内存 | 30 GiB 总，已用 16 GiB，可用 13 GiB |
| Swap | 7 GiB 中已用 6 GiB — **偏紧，本轮避免大规模并发编译/测试** |
| 磁盘 `/` | 468 G，可用 206 G |
| 磁盘 `/data` | 1.9 T，可用 1.6 T |
| 仓库体积 | 390 MB（含 15 份文档 blob，已被 .gitignore 排除） |

## 4. Python 环境

| 解释器 | 版本 | jsonschema |
|---|---|---|
| `python3` | 3.14.4 | 4.19.2 |
| `.venv/bin/python` | 3.14.4 | 4.26.0 |

`pyproject.toml` 的 `[tool.pytest.ini_options]` 设 `pythonpath = ["."]`、
`testpaths = ["tests"]`、`addopts = "-q"`。新增测试放 `tests/`，不需要安装包。

## 5. 现有基线复核（实测，非转述）

```
benchmark.runner.cli validate --dataset benchmark/datasets/pilot
  → ok: 24 search, 28 fetch, 10 e2e  (checksum 28157ca3b4a9ac31)

pytest tests/test_benchmark_local_runtime_manifest.py -o addopts= -q
  → 9 passed

pytest tests/test_benchmark_*.py -o addopts= -q
  → 120 passed
```

三项与交接记录一致。校验和 `28157ca3b4a9ac31` 未变。

## 6. 现有资产盘点（避免重复建设）

### 6.1 已有 schema（`benchmark/schemas/`）

| 文件 | 覆盖 | 新 harness 能否直接复用 |
|---|---|---|
| `search-case.schema.json` | 检索式 + 域名级金标 | 可复用为 search 段的 **payload**，但缺 `provider-agnostic` 之外的研究字段（intent、excluded_domains、expected_entities 粒度不足） |
| `fetch-case.schema.json` | 单 URL + 页面性质金标 | 可复用，缺 contacts/people 结构化金标 |
| `e2e-case.schema.json` | 业务目标 + 金标实体/联系人 | 可复用，缺 qualification / dossier 判据 |
| `judge-verdict.schema.json`、`scorecard.schema.json` | LLM judge 与记分卡 | 本轮不接 LLM judge，保留不动 |

三份 case schema 都是 `additionalProperties: false`，**不能往里加字段**——
加了会让 pilot 的 `validate` 失败。因此新数据集用**新目录、新 schema**，
不复用旧 JSONL 文件本身，只复用旧数据结构中的语义。

### 6.2 已有 runner（`benchmark/runner/`，2,420 行）

`models.py`（加载/校验/校验和/RecordedRun）、`metrics.py`（P@k、R@k、nDCG、
e2e 评分）、`normalize.py`、`report.py`、`cli.py`、`judge.py`。

**可直接复用的部分**：`iter_jsonl`、`dataset_checksum`、`_structural_check`
的无第三方依赖降级路径、`RecordedRun`（回放已录制响应）。

**不能直接复用的部分**：`load_dataset` 写死了 `search/fetch/e2e` 三层目录结构；
`metrics.py` 的 e2e 评分面向「实体/联系人」而非「研究档案」。新 harness 需要
8 个新 stage，因此新建 `benchmark/research_core/` 包，**从 `benchmark.runner.models`
导入工具函数，不复制它们**。

### 6.3 已有数据集

| 数据集 | 规模 | 状态 |
|---|---|---|
| `datasets/pilot/` | 62 条（24/28/10），全部 `draft` | 锁定，不动 |
| `datasets/local-runtime/search/` | 12 条 | 有效 1 轮数据 |
| `datasets/local-runtime/fetch/` | 12 条，指向 :18080 fixture | 有 Docker/Python 两轮 |
| `datasets/local-runtime/document/` | 15 条（12 PDF + 3 Office） | 有完整 JSONL |
| `datasets/local-runtime/e2e/` | **空目录** | 从未建立 |
| `datasets/research-core/` | **不存在** | 本轮新建 |

### 6.4 已有报告与原始数据一致性

`benchmark/local-runtime/runs/` 共 48 个文件。抽查
`strategy_eval.py` 依赖的三个输入（`fetch-bench-20261118T194107.jsonl`、
`document-bench-all.jsonl`、`ocr-real-20261118T194826.json`）均在盘上，
且 `strategy_eval` 可复算（见 `STRATEGY_COMPARISON.md` §3）。**未发现报告与
原始 JSONL 矛盾。**

## 7. 缺口（本轮要补的）

| 缺口 | 影响 |
|---|---|
| 无统一 case/result/manifest schema | 各 stage 结果无法比较、无法复现 |
| 无 run_id / dataset_version / checksum 记录 | 结果不可追溯 |
| Search 仅 12 例、无 intent/excluded/多语言覆盖 | 不能衡量研究质量 |
| Fetch 仅 12 例，fixture 路由 12 条 | 覆盖不到 team/contact/email-in-html/错误页等 |
| 无 Extraction / Qualification / Identity / Evidence / Channel 数据 | 研究链的后半段完全未测 |
| 无 Mock provider | 缺 key 时整条链不可测 |
| 无 E2E | 无法验证「Brief → Dossier」 |
| 无 Makefile 目标 | 运行方式靠记忆 |

### 新增 fixture 的进程策略

现有 `serve.py` 正在 18080 服务旧 12 用例。本轮**追加新路由是安全的**
（HTTP 服务按请求读代码？否——Python 进程已加载旧代码，追加不会影响运行中的进程），
但**不能重启它**去加载新路由，因为同一时刻可能有别的实验在用它。

方案：新 fixture 写入**新文件** `benchmark/datasets/research-core/fixtures/serve.py`，
在 **18090** 上另起进程。旧服务不动，旧 12 用例的字节与端口全部不变。

## 8. 本轮不变量（每次提交前复核）

1. `validate` 校验和 = `28157ca3b4a9ac31`；
2. `tests/test_benchmark_*.py` = 120 passed；
3. `tests/test_benchmark_local_runtime_manifest.py` = 9 passed；
4. MinerU / Crawl4AI / SearXNG 进程 PID 不变；
5. 不修改 `services/crawl4ai-local/.env`、不重启 Docker 容器；
6. 新增数据的 `origin` 只有三种取值，公开数据必须带可复核 URL 与 sha256。
