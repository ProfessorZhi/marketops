# Search Benchmark — SearXNG 实测，Serper/TinyFish 受阻

状态：**数据集 12 例就绪；SearXNG 有 1 轮有效数据；Serper/TinyFish 未配置**
最后更新：2026-11-18 17:52 +08:00

---

## 1. 结论先行

| provider | 本轮状态 | 能否作为生产搜索源 |
|---|---|---|
| SearXNG（自建） | 可用，但有硬限流 | **不能**，见 §5 |
| Serper | `not_configured`（无 key） | 未测 |
| TinyFish | `not_configured`（无 key；契约已按 LiteLLM 实现写入 adapter） | 未测 |
| DeepSeek Search | `not_configured` | 未测 |
| SerpAPI (Facebook) | `not_configured` | 未测 |

三个必须分开说的话：

1. SearXNG **能跑出真实、正确的搜索结果**——OpenAI→openai.com、荷兰政府→
   government.nl、Maersk→facebook.com/Maersk/，10/10 查询有结果。
2. SearXNG **不能承接批量查询**——连续 12 例就会把 google cse 打到 429，
   然后该引擎整小时不可用。
3. 所以本轮唯一可信的质量数字来自**一轮** 12 例，不是稳定测量。

## 2. 数据集

`benchmark/datasets/local-runtime/search/cases.jsonl` — 12 例，12/12 通过
`benchmark/schemas/search-case.schema.json`（Draft 2020-12，`additionalProperties: false`）。

| case_id | 类型 | query 要点 | 金标粒度 |
|---|---|---|---|
| SRCH-DE-WEB-001 | official_website_discovery | Siemens AG | 域名 siemens.com |
| SRCH-NL-WEB-002 | official_website_discovery | ASML | 域名 asml.com |
| SRCH-DK-WEB-003 | official_website_discovery | Maersk | 域名 maersk.com |
| SRCH-CH-WEB-004 | official_website_discovery | Nestlé（含 é） | 域名 nestle.com |
| SRCH-SE-WEB-005 | official_website_discovery | Ericsson | 域名 ericsson.com |
| SRCH-DE-WEB-006 | official_website_discovery | Robert Bosch GmbH | 域名 bosch.com |
| SRCH-DE-SOC-007 | social_media_discovery | Siemens LinkedIn | 页面级 |
| SRCH-DK-SOC-008 | social_media_discovery | Maersk LinkedIn | 页面级（**disputed**） |
| SRCH-DE-CON-009 | contact_discovery | Siemens 联系方式 | 域名 |
| SRCH-NL-CON-010 | contact_discovery | ASML 总部联系方式 | 域名 |
| SRCH-DE-EXACT-011 | exact_search | `"Siemens AG"`（带引号） | 域名 |
| SRCH-NL-LONG-012 | long_tail_search | ASML EUV 长句 | 域名 |

金标来自公开常识，不是任何 provider 的返回结果。全部 `draft`，一条 `disputed`：

> **SRCH-DK-SOC-008**：SearXNG 返回的首条是 `linkedin.com/company/maersk-group`，
> 与金标 `.../company/maersk` 不一致。LinkedIn 对未登录请求一律 403，无法核验
> 哪个 slug 正确。**没有为了让 provider 得分去改金标**，标 disputed 待人工核验。

## 3. SearXNG 部署与上游引擎

完整记录见 `services/searxng-local/README.md`。要点：

- 源码 `b867c56`，Python 3.12，进程 RSS ~108 MB，监听 `127.0.0.1:8888`。
- `settings.yml` 有 **353 个 engine 定义**，实际产出结果的是 **2 个**：
  google cse（10/10）、duckduckgo（8/10，2 次 CAPTCHA）。
- brave 10/10 超时且拖慢查询，已禁用：均值延迟 3.16 s → **0.86 s**，
  峰值 8.02 s → 1.58 s。
- 没有 Docker 依赖。

## 4. 唯一一轮有效质量数据

`runs/search-bench-20261118T173935.jsonl`（12 例，进程刚启动、上游未被限流）：

| 指标 | 值 |
|---|---|
| precision@5 | **0.65** |
| precision@10 | 0.5583 |
| recall@5 / @10 | 0.9167 / 0.9167 |
| domain_hit_rate | 1.0 |
| duplicate_rate | 0.0639 |
| latency p50 | 0.825 s |
| cost | $0 |

逐例：

| case | P@5 | R@10 | 观察 |
|---|---|---|---|
| DE-WEB-001 Siemens | 1.0 | 1.0 | |
| NL-WEB-002 ASML | 1.0 | 1.0 | |
| DK-WEB-003 Maersk | 1.0 | 1.0 | |
| CH-WEB-004 Nestlé | 0.4 | 1.0 | 前 5 混入 nestleusa.com 等子公司站 |
| SE-WEB-005 Ericsson | 0.6 | 1.0 | |
| DE-WEB-006 Bosch | 0.4 | 1.0 | 混入 bosch-home.com 等事业部站 |
| DE-SOC-007 Siemens LinkedIn | 1.0 | 1.0 | |
| DK-SOC-008 Maersk LinkedIn | 0.0 | 0.0 | **金标 disputed**，见 §2 |
| DE-CON-009 Siemens 联系 | 0.8 | 1.0 | |
| NL-CON-010 ASML 联系 | 0.6 | 1.0 | |
| DE-EXACT-011 `"Siemens AG"` | 0.2 | 1.0 | 前 3 是维基/彭博/雅虎；官方站在第 4 |
| NL-LONG-012 ASML 长尾 | 0.8 | 1.0 | |

两个真实的质量弱点：

- **品牌查询会召回集团子公司域名**（nestleusa.com、bosch-home.com）。对
  「找集团官网」没问题，对「找某个业务单元」会把 precision 拉低。
- **精确匹配查询的官方站排名靠后**：`"Siemens AG"` 前 3 条是维基、彭博、
  雅虎财经。这是 DuckDuckGo/Google 的排序，SearXNG 原样透传。

## 5. 稳定性实验：连续请求会把上游打挂

| 轮次 | 配置 | 空结果行 | 上游错误 | P@5 |
|---|---|---|---|---|
| 173935 | brave 开，进程新启 | 0 / 12 | 无 | **0.65** |
| 174407 | brave 关，无间隔 | 4 / 12 | google cse 限流 | 0.4833 |
| 174455 | 继续无间隔 | **5 / 12** | `google cse: Suspended: too many requests` ×12 | 0.3167 |
| 174737 | 冷却 150 s + 6 s 间隔 | 3 / 12 | 仍 11/12 被暂停 | 0.45 |

根因（源码）：

```python
# searx/engines/google_cse.py:142
if error.get("code") == 429:
    raise SearxEngineTooManyRequestsException(message=...)
# searx/exceptions.py:102
"""By default, SearXNG stops sending requests to this engine for 1 hour."""
```

**一次 429 → 该引擎在进程内停用 3660 秒。** 这是上游真实限制，不是配置能绕过的。
`SUSPENDED_STATUS` 只在内存里，重启进程会清空状态，但上游额度不会恢复。

因此**不能**用后三轮的数字评估质量：它们测的是限流，不是搜索质量。

## 6. 与 Serper / TinyFish 的同查询对照

```bash
SEARXNG_BASE_URL=http://127.0.0.1:8888 \
python -m benchmark.local_runtime.search_bench --providers searxng serper tinyfish
```

同一份 `cases.jsonl`、同一组 query/country/language 发给三个 provider。
结果：SearXNG 12/12 运行；Serper、TinyFish 各 12/12 `not_configured`。
**没有任何 Serper/TinyFish 的质量、延迟或成本数据**，表中对应单元格为空。

`NotConfigured` 不会退化成空列表——空列表会被算成 precision 1.0，是假数据。

## 7. 解除阻塞

- **Serper**：注册送 2500 次，`export SERPER_API_KEY=...` 即可跑，adapter 已就绪，
  数据集和打分不需要改。
- **TinyFish**：契约已从 LiteLLM 的实现读出来并写进 adapter
  （`GET https://api.search.tinyfish.ai?query=&location=`，头 `X-API-Key`，
  结果字段 `title/url/snippet/position`，见
  `litellm/llms/tinyfish/search/transformation.py`）。**这只是读源码，
  不是实测**：没有 key，12/12 仍是 `not_configured`。
- **DeepSeek Search**：端点与鉴权仍未知，adapter 保持 `NotConfigured`。猜一个
  端点会产出看起来像测量、实际是编造的数字。

本轮未申请任何新 key，外部 API 支出 **$0**（预算上限 $1 未动用）。
- **SearXNG 若要继续用**：只能低频（例如每条查询间隔数十秒）、失败可重试，
  且必须有备用源。不能当作生产主搜索。

## 8. 本轮不覆盖

- Serper / TinyFish / DeepSeek Search 的任何真实指标。
- SearXNG 的长期稳定性（已知 1 小时级封禁，未测恢复曲线）。
- 真实查询分布：12 例全是「找官方站/联系方式/社交页」，没有政府开放数据、
  PDF 发现、时效性查询。
- 多语言仅 1 例（中文）。
- 结果摘要质量（只评 URL 相关性，不评 snippet 内容）。

## 9. 原始数据

```
runs/search-bench-20261118T173935.jsonl            12 例有效轮
runs/search-bench-20261118T174407.jsonl            限流轮（4 空）
runs/search-bench-20261118T174455.jsonl            限流轮（5 空，含上游原因）
runs/search-bench-20261118T174737.jsonl            冷却+限速轮（3 空）
runs/searxng-acceptance-control-20261118T174153.json   10 类查询（brave 开）
runs/searxng-acceptance-nobrave-20261118T174342.json   10 类查询（brave 关）
```
