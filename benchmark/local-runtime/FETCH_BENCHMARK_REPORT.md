# Fetch Benchmark — 第一轮（本地 fixture，2 个 provider）

状态：**Benchmark Completed（fixture 数据集；真实公开站点尚未加入）**
最后更新：2026-11-18 17:40 +08:00

---

## 1. 参与对照的 provider

| provider | 版本 | 方式 | 本轮状态 |
|---|---|---|---|
| HTTP + Trafilatura | httpx + trafilatura | 直接请求 + 正文抽取 | 完成 |
| Crawl4AI Local | 0.9.5 | 本地 REST API，Chromium 渲染 | 完成 |
| Crawl4AI Cloud | — | — | 未执行（无 key） |
| TinyFish Free Fetch | — | — | 未执行（未接入） |
| DeepSeek Fetch | — | — | 未执行（未接入，历史对照） |

同一份 `fixtures.jsonl` 喂给每个 provider，URL 完全相同。没有「某 provider 只在
适合它的页面上跑」这种情况：JS 页面同样发给 HTTP 基线，PDF 同样发给浏览器。

## 2. 数据集

`benchmark/datasets/local-runtime/fetch/fixtures.jsonl` — 12 例，全部
`synthetic_fixture`，由 `fixtures/serve.py` 生成，12/12 通过
`fetch-case.schema.json` 校验。来源哈希记录在 `PROVENANCE.jsonl`（当前为
`serve.py` 本身的 SHA256）。

| 用例 | 类型 | 考验什么 |
|---|---|---|
| STATIC-001 | static_html | 基线：静态正文与联系方式 |
| JS-002 | js_rendered | 内容由 JS 注入 |
| LAZY-003 | lazy_loading | 定时器后填充 |
| PAGINATION-004 | pagination | 第 2 页与翻页链接 |
| REDIRECT-005 | redirect_chain | 两次 302 |
| COOKIE-006 | cookie_consent | Set-Cookie 后跳转 |
| SHELL-007 | empty SPA | **必须报告无内容**，`no_content` |
| PDF-008 | pdf_text | 二进制正文 |
| LONG-009 | 长页面 | 39.8 KB，联系方式在**末尾** |
| TEAMSTATIC-010 | 团队页 | 3 人 ×（姓名/职位/邮箱/电话） |
| OWNERSHIP-011 | 组织归属 | 子公司与母公司的关系陈述 |
| METAREFRESH-012 | HTML 跳转 | `<meta http-equiv="refresh">`，不是 302 |

`task_type` 受 schema 的封闭枚举约束，长页面/团队页/组织页无法新增取值，因此
映射到现有枚举（`static_html` / `contact_page` / `redirect_chain`），细粒度语义
记在 `gold_label.page_kind`。没有为了塞新用例去改动既有契约。

### 判据

- **Useful Content**：文本长度 ≥ `min_text_chars` 且 gold marker 命中率 ≥ 0.6。
- **假成功**：`no_content` 用例里返回了 ≥80 字符，或返回了 `forbidden_strings`
  中的字符串。后者是必需的——空壳页常常吐出几十字符的「enable JavaScript」，
  只看长度会把它判成内容。
- **Final URL**：仅对声明了 `final_url_expected` 的用例比较。
- **成本**：两者都是本地，API 成本为 0；没有把本地服务折算成 0 成本后与云服务
  直接比价，云 provider 接入后需按 §7 的口径重算。

## 3. 结果

| 指标 | HTTP+Trafilatura | Crawl4AI Local |
|---|---|---|
| Useful Content Rate | 0.6364 | **0.9091** |
| 文本完整性 | 0.5697 | **0.9091** |
| 联系方式完整性 | 0.6364 | **0.9091** |
| HTTP 成功率 | **1.0000** | 0.7500 |
| Final URL 准确率 | 0.6667 | **1.0000** |
| 空壳页假成功 | **1** | **0** |
| 错误 | 0 | 2 |
| p50 / p95 | **0.013 / 0.143 s** | 2.833 / 2.989 s |

两条结论都有代价，不能只读上半张表：

- Crawl4AI 多拿到的 3 个用例（JS、懒加载、meta refresh），代价是**每个请求
  +2.8 s 固定开销**。在 12 例上它多花 32 s。若真实流量里大部分页面是静态的，
  这个代价买不到相应收益。
- HTTP 基线的**假成功**是最危险的一格：它对空壳页返回 46 字符并「看起来成功」。
  下游若不做长度与禁用串检查，会把空壳当成有效页面继续抽取，产生空记录而不是
  错误记录——这类错误不会触发重试，只会悄悄污染数据。

## 4. 逐项

| 用例 | HTTP | Crawl4AI |
|---|---|---|
| STATIC-001 | 通过 309 字符 | 通过 430 字符 |
| JS-002 | 失败 17 字符 | **通过 154 字符** |
| LAZY-003 | 失败 21 字符 | **通过 162 字符** |
| PAGINATION-004 | 通过 409 | 通过 533 |
| REDIRECT-005 | 通过 309，final URL 对 | 通过 430，final URL 对 |
| COOKIE-006 | 通过 45 | 通过 65 |
| SHELL-007 | **假成功** 46 字符 | 报告失败（归因错误，见下） |
| PDF-008 | 失败 0 字符 | **异常** 0 字符 |
| LONG-009 | 通过 37820 | 通过 38237 |
| TEAMSTATIC-010 | 通过 421 | 通过 472 |
| OWNERSHIP-011 | 通过 307 | 通过 330 |
| METAREFRESH-012 | 失败 59 字符 | **通过 430，final URL 对** |

Crawl4AI 在 PDF 上抛异常而不是返回空，调用方必须自己分流。

## 5. 归因问题

Crawl4AI 把空壳页判为
`Blocked by anti-bot protection: Structural: minimal_text, no_content_elements, script_heavy_shell`。
**结论对、原因错**：那是页面自身没有内容，不是被反爬拦截。下游若按「被拦」重试
或切代理，会无效消耗配额。基准记录的是它的 success=false，这一点不影响本轮分数，
但影响它在生产链路里的可解释性。

## 6. 本轮不覆盖

- **真实公开站点**：12 例全部本地 fixture。它们能区分能力，不能代表真实站点
  的分布（真实站点有 CDN、HTTP/2、反爬、多语言）。
- **Crawl4AI Cloud / TinyFish / DeepSeek Fetch**：未接入。
- **并发与吞吐**：串行，并发 1。
- **长尾页面**（>1 MB HTML）、**登录墙**、**地区跳转**：未覆盖。
- **成本**：云 provider 未接入，无法做成本对照。

## 7. 后续接入条件

接入云 provider 时必须同时满足，否则结果不可比：

1. 同一份 `fixtures.jsonl`，URL 不变。
2. 记录实际计费（不是标价），并区分失败请求是否计费。
3. 失败/重试产出的页面单独计数，不能混入成功率。
4. 若某 provider 对空壳页返回内容，按假成功计入，不给「它是云服务」的豁免。

## 8. 原始数据

```
runs/fetch-bench-20261118T173023.jsonl          24 行（2 provider × 12 用例）
runs/fetch-bench-20261118T173023.summary.json   聚合指标
runs/crawl4ai-smoke-20261118T172958.json        Crawl4AI API smoke test
```
