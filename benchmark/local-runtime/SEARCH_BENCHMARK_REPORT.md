# Search Benchmark — 数据集与阻塞说明

状态：**数据集就绪（12 例，draft）；Benchmark 未运行（0 个 provider 可用）**
最后更新：2026-11-18 17:40 +08:00

---

## 1. 结论先行

Search Provider 统一 Adapter 已完成，12 例数据集已通过严格 schema 校验。
**本次没有产生任何一条 provider 结果**，因为本地不存在任何一个搜索 provider 的
凭据或实例。运行记录如实写的是 `not_configured`，不是 0 分，也不是空结果列表。

一个未配置的 provider 如果返回 `[]`，precision 会算成 1.0、recall 算成 0，
表格看起来「很准」；这是假数据。`search_bench.py` 因此让 `NotConfigured`
直接冒泡到行记录，聚合时单独计数。

## 2. 数据集

`benchmark/datasets/local-runtime/search/cases.jsonl` — 12 例，
`schema_version = marketops.benchmark.case.v1`，12/12 通过
`benchmark/schemas/search-case.schema.json`（Draft 2020-12，`additionalProperties: false`）。

| case_id | 类型 | query 要点 | 金标粒度 |
|---|---|---|---|
| SRCH-DE-WEB-001 | official_website_discovery | Siemens AG | 域名 siemens.com |
| SRCH-NL-WEB-002 | official_website_discovery | ASML | 域名 asml.com |
| SRCH-DK-WEB-003 | official_website_discovery | Maersk | 域名 maersk.com |
| SRCH-CH-WEB-004 | official_website_discovery | Nestlé（含 é） | 域名 nestle.com |
| SRCH-SE-WEB-005 | official_website_discovery | Ericsson | 域名 ericsson.com |
| SRCH-DE-WEB-006 | official_website_discovery | Robert Bosch GmbH | 域名 bosch.com |
| SRCH-DE-SOC-007 | social_media_discovery | Siemens LinkedIn | **页面级** linkedin.com/company/siemens |
| SRCH-DK-SOC-008 | social_media_discovery | Maersk LinkedIn | **页面级** linkedin.com/company/maersk |
| SRCH-DE-CON-009 | contact_discovery | Siemens 联系方式 | 域名 |
| SRCH-NL-CON-010 | contact_discovery | ASML 总部联系方式 | 域名 |
| SRCH-DE-EXACT-011 | exact_search | `"Siemens AG"`（带引号） | 域名 |
| SRCH-NL-LONG-012 | long_tail_search | ASML EUV 长句 | 域名 |

### 金标怎么来的，以及它有多弱

金标来自公开常识（公司与其官网域名的对应关系），**不是任何 provider 的返回
结果**。从 provider 输出复制金标会让该 provider 天然拿满分。

四个必须说清楚的局限：

1. **全部 `label_status: draft`，没有一条 human_verified。** 按 schema 约定，
   draft 只能用于跑通 Runner，不能进正式记分卡。
2. **多数是域名级金标。** 搜索很少返回完全相同的路径，按 URL 精确匹配会让所有
   provider 都拿 0；所以除社交页外，命中官方域名的任意页面都算相关。这放宽了
   precision，且对每个 provider 同样放宽。
3. **社交页必须页面级。** `linkedin.com/company/siemens` 若按域名放宽，任何
   公司的 LinkedIn 页都会算命中。`search_bench.py` 因此只对「路径为空的金标
   URL」启用域名级匹配。
4. **`forbidden_urls` 全部为空。** 本轮没有已知的坏结果（过期页、爬虫农场、
   错误实体）。留空是诚实的，但也意味着 forbidden 指标这一轮没有区分力。

另有一个结构性限制：contact 类用例的 `expected_fields` 写了 email/phone，但
**搜索层只能证明「找到了官方域」**，抽不到具体邮箱。真正的联系方式抽取属于
Fetch 层，把它算进 search 分数会串层。

## 3. 为什么没跑起来

| provider | 需要什么 | 本地现状 |
|---|---|---|
| Serper | `SERPER_API_KEY` | 无 |
| SearXNG | `SEARXNG_BASE_URL`（自建实例） | 无实例 |
| TinyFish | `TINYFISH_API_KEY` + 已验证的端点契约 | 无 key，端点未核实 |
| DeepSeek Search | `DEEPSEEK_SEARCH_API_KEY` | 无（且与 DeepSeek LLM 不是同一接口） |
| SerpAPI (Facebook) | `SERPAPI_API_KEY` | 无 |

本机只有 LLMLAB_HOST / SSH 凭据，没有任何搜索服务的 key。按约束**不得使用
其他用户的 API 凭据**，也不得超免费额度，所以本轮到此为止。

TinyFish 和 DeepSeek Search 即使给了 key 也仍会 `not_configured`：它们的请求
契约在本仓库里没有可依据的定义，猜一个端点会产出看起来像测量结果、实际是编的
数字。

## 4. 运行记录

```
runs/search-bench-20261118T173637.jsonl          60 行（5 provider × 12 用例）
runs/search-bench-20261118T173637.summary.json   聚合：每 provider ran=0, not_configured=12
```

五个 provider 的 `precision_at_5`、`recall_at_10`、`latency_p50_s`、`cost_usd`
全部为 `null`。这不是失败，是没有测量。

## 5. 解除阻塞需要什么

最小可用组合：

1. **Serper** — 免费额度 2500 次，注册即得 key，覆盖 Google 主索引，是与现有
   生产链路最可比的一个。
2. **自建 SearXNG** — 本地 Docker 或 Python 部署，零 API 成本，作为「免费互补
   搜索」策略的候选。它不依赖外部 key，是当前唯一能在本机闭环的一条路。
3. TinyFish / DeepSeek Search — 需要先把请求契约写进本仓库（端点、鉴权头、
   响应结构），否则 adapter 只能是 `NotConfigured`。

拿到 key 后直接：

```bash
export SERPER_API_KEY=...
python -m benchmark.local_runtime.search_bench --providers serper
```

不需要改数据集，也不需要改 adapter。

## 6. 本轮不覆盖

- 任何 provider 的实际质量、延迟、成本。
- 真实查询分布（12 例全是「找官方站」型，没有 directory、政府开放数据、
  PDF 发现、时效性查询）。
- 多语言查询（全部 en，只有 Nestlé 带非 ASCII 字符）。
- 结果摘要与正文抽取的差异（只评 URL 相关性）。
