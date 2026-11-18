# SearXNG Local — 部署与上游引擎实测

状态：**Running**（本机 127.0.0.1:8888）；质量数据仅一轮有效，见 §4。

| 项目 | 状态 | 依据 |
|---|---|---|
| 源码 | Cloned | `b867c56`，31 MB |
| Python venv | Installed | 3.12.15，依赖装齐 |
| 服务 | **Running** | pid 2221317，RSS ~108 MB，监听 127.0.0.1:8888 |
| 首页 | 200 | 7.4 ms |
| JSON API | 可用 | `/search?q=...&format=json` |
| 上游引擎 | **2 个可用 / 353 个定义** | §3 |
| 生产可用性 | **不满足** | 429 后整小时不可用，§5 |

---

## 1. 部署形态

```
源码   /data/home/fjd/marketops-lab/src/searxng     (b867c56)
venv   /data/home/fjd/marketops-lab/envs/searxng    (Python 3.12.15)
配置   .../src/searxng/settings.yml                 (含 secret_key，不入库)
启动   services/searxng-local/start_searxng.sh
验收   services/searxng-local/acceptance.py
```

没有用 Docker：镜像拉取在本机死锁（见 `CRAWL4AI_LOCAL_REPORT.md` §2），
SearXNG 从源码跑不需要镜像。没有改全局 Docker 配置。

配置要点：

- `search.formats: [html, json]` —— 默认只有 html，不加 json 就没有 API。
- `server.limiter: false`、`public_instance: false` —— 本机单用户，关闭公共实例保护。
- `general.contact_url` 未设，导致 wikipedia / wikidata / openstreetmap 三个引擎
  注册失败。这三个与 MarketOps 的企业发现任务无关，故未补。
- **brave 已置 `disabled: true`**：10/10 查询超时，且把每条查询拖到 8 s。

## 2. 启动与基础验收

```
$ ss -ltnp | grep 8888
LISTEN 127.0.0.1:8888  users:(("python",pid=2221317))
$ curl -o /dev/null -w '%{http_code} %{time_total}\n' http://127.0.0.1:8888/
200 0.007449
$ curl 'http://127.0.0.1:8888/search?q=official+website+of+OpenAI&format=json'
HTTP 200, 20 results, 5.25 s (首次查询含引擎冷启动)
top3: openai.com/ , openai.com/about/ , openai.com/news/
```

## 3. 上游引擎：353 个定义，真正工作的是 2 个

`settings.yml` 里有 **353 个 engine 条目**。它们是定义，不是可用的搜索能力。
10 条不同类型查询（`acceptance.py`）的实测：

| 上游引擎 | 产出结果的查询数 | 报错的查询数 | 错误 |
|---|---|---|---|
| google cse | **10 / 10** | 0（首轮） | — |
| duckduckgo | **8 / 10** | 2 | CAPTCHA |
| brave | 0 | **10 / 10** | timeout |

其余 350 个定义在本轮要么未启用、要么无输入需求（图片/地图/百科），要么
未参与 web 查询。**结论：SearXNG 在本机的实际搜索能力 = google cse + duckduckgo，
且 duckduckgo 会间歇 CAPTCHA。**

## 4. 10 类查询验收结果（brave 关闭后）

| 类型 | 查询 | HTTP | 结果数 | 引擎 | 耗时 |
|---|---|---|---|---|---|
| 企业官网 | official website of OpenAI | 200 | 23 | gse 20 / ddg 3 | 1.58 s |
| 企业负责人 | OpenAI CEO Sam Altman profile | 200 | 25 | gse 20 / ddg 5 | 0.77 s |
| 企业联系方式 | Siemens AG corporate contact email phone | 200 | 27 | gse 20 / ddg 7 | 0.94 s |
| YouTube Creator | MrBeast YouTube channel | 200 | 26 | gse 20 / ddg 6 | 0.66 s |
| Facebook Page | Maersk official Facebook page | 200 | 29 | gse 20 / ddg 9 | 0.76 s |
| 国家与地区 | government of the Netherlands official portal | 200 | 24 | gse 20 / ddg 4 | 0.70 s |
| 竞品发现 | alternatives to Salesforce CRM | 200 | 28 | gse 20 / ddg 8 | 0.78 s |
| 特定行业 | EUV lithography equipment manufacturers | 200 | 24 | gse 20 / ddg 4 | 0.74 s |
| 多语言 | 西门子 官方网站（zh/cn） | 200 | 20 | gse 20 | 0.48 s |
| 长尾 | ASML EUV … headquarters Veldhoven Netherlands | 200 | 20 | gse 20 | 1.17 s |

- **HTTP 状态码**：10/10 为 200，没有 429/403 直接返回给客户端（上游的 429 被
  SearXNG 吞掉并记为 unresponsive）。
- **结果数量**：20–29 条，没有 0 结果。
- **响应时间**：均值 **0.858 s**，最大 1.578 s。
- **CAPTCHA/限流事件**：2 次 DDG CAPTCHA，0 次 HTTP 级 429。

样本查询的 top3 是真实且正确的：OpenAI → openai.com；MrBeast → 其 YouTube 频道；
Maersk → facebook.com/Maersk/；荷兰政府 → government.nl；ASML → asml.com 的
Veldhoven 页面。

## 5. 致命限制：429 之后整小时不可用

同一批查询连续跑第二次时，**google cse 12/12 报 `Suspended: too many requests`**，
5/12 条查询变成 0 结果。冷却 150 s 后按 6 s 间隔重跑，仍 11/12 被暂停。

源码给出的原因：

```python
# searx/engines/google_cse.py:142
if error.get("code") == 429:
    raise SearxEngineTooManyRequestsException(...)
# searx/exceptions.py:102
"""By default, SearXNG stops sending requests to this engine for 1 hour."""
```

即：**google cse 收到一次 429，该引擎在进程内停用 3660 秒**。这是上游真实限制，
不是配置问题；调小 `suspended_times` 只会更频繁撞 429，加剧封禁。

`SUSPENDED_STATUS` 只存在内存里，重启进程可清空，但上游额度不会因此恢复。

**结论：SearXNG 不能作为唯一或主力搜索源。** 它适合：

- 低频、可重试的补充查询；
- 零 API 成本场景下的兜底；
- 不能承接批量 Prospect 发现（本研究 12 例连续调用就会打满）。

## 6. 与 Search Dataset 的对照

`benchmark/local_runtime/search_bench.py` 已接入 SearXNG：

```bash
export SEARXNG_BASE_URL=http://127.0.0.1:8888
python -m benchmark.local_runtime.search_bench --providers searxng serper tinyfish
```

SearXNG 在**进程刚启动、上游未被限流**时的唯一一轮有效数据（12 例）：

| 指标 | 值 |
|---|---|
| precision@5 | 0.65 |
| precision@10 | 0.5583 |
| recall@5 / @10 | 0.9167 / 0.9167 |
| domain_hit_rate | 1.0 |
| duplicate_rate | 0.0639 |
| latency p50 | 0.825 s |
| cost | $0 |

**这组数字只代表一轮，不能当作稳定质量。** 后续 3 轮全部被 429 污染，其中 2 轮
出现 3–5 条空结果。Serper 与 TinyFish 无 key，全程 `not_configured`。

## 7. 未完成 / 不能声称

- 没有验证 SearXNG 在持续流量下的稳定性——已知不稳定。
- 没有验证多实例或代理池——本轮不使用代理。
- 没有补 `contact_url`，因此 wikipedia/wikidata/OSM 不可用。
- 没有和 Serper / TinyFish 做真实质量对照——缺 key，只有 `not_configured`。
