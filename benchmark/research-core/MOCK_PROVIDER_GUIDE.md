# Mock Provider 指南

实验室里没有任何真实 API。七个 Mock Provider 的作用是：**让 Provider 的返回形状与
失败方式，和真实 API 一模一样地被测到**。它们的成绩与真实 Provider 永远分开记录。

---

## 1. 七个 Provider

| Provider | stage | 顶层信封 | 条目字段 |
|---|---|---|---|
| `mock_serper` | search | `organic[]` | `title`、`link`、`snippet`、`position` |
| `mock_tinyfish_search` | search | `results[]` | `title`、`url`、`description` |
| `mock_deepseek_search` | search | `data[]` | `title`、`url`、`content` |
| `mock_youtube_data` | search | `items[]` | `snippet.title`、`id.channelId` |
| `mock_google_maps_places` | search | `places[]` | `displayName.text`、`websiteUri` |
| `mock_tinyfish_fetch` | fetch | `{url, content_type, text, links}` | `links` 里的 `mailto:` 即邮箱 |
| `mock_deepseek_fetch` | fetch | `{final_url, content, mailto}` | `mailto[]` |

每个 Provider 只负责"用**自己的**信封返回"，归一化由 adapter 完成——这与真实接入
时的分工一致，所以换成一个 live adapter 只更换取数层，不改 metrics。

Google Maps 只有 API Contract + Mock Fixture（任务要求），没有真实调用。

## 2. 十种模式

| mode | 触发 | 期望 status |
|---|---|---|
| `normal` | 正常结果 | `ok` |
| `empty` | 成功但零结果 | `empty` |
| `timeout` | `AdapterTimeout` | `timeout` |
| `rate_limited` | `HTTPError(429)` | `failed` |
| `server_error` | `HTTPError(500)` | `failed` |
| `duplicate` | 重复条目 | `ok`（由 metrics 去重后计分） |
| `bad_format` | 合法 JSON，但列表字段变成对象/字符串 | `invalid_output` |
| `missing_field` | 合法 JSON，缺必需键 | `invalid_output` |
| `invalid_json` | 根本不是 JSON | `failed` |
| `multilingual` | 非英语/德语/荷兰语结果 | `ok` |

三种"坏返回"必须分开，这是契约测试的核心：

- `bad_format` 与 `missing_field` 是**返回了但不符合契约** → `invalid_output`；
- `invalid_json` 是**传输出错** → `failed`；
- 三者都**不得**塌缩成 `empty`。`empty` 只表示"Provider 成功地返回了空"。

## 3. 契约矩阵

```bash
make research-contract       # 或 python -m benchmark.research_core.mocks.contract
```

5 个 search Provider + 2 个 fetch Provider，各 10 种模式，共 **70 项**。每项断言
runner 记录的状态等于 `EXPECTED[mode]`，全部通过才返回 0。

`tests/test_research_core_benchmark.py` 另外验证：

- 矩阵条目数 = (7) × 10；
- `by_mode` 映射里 `empty` 只来自 `empty`，`invalid_output` 只来自两种坏格式，
  `failed` 只来自 429/500/非 JSON——它们是四种不同的答案。

## 4. 本地 fixture 服务

| 服务 | 端口 | 内容 |
|---|---|---|
| `mocks/web.py` | `127.0.0.1:18090` | 40 家可检索合成公司 × 5 页 + 3 条 qualification 边界记录 × 5 页 = 215 份文档，外加只含 40 家的 `search_index.json` |
| `mocks/public_web.py` | `127.0.0.1:18091` | 56 份冻结的真实公开页（已脱敏） |

适配器构造时按需启动：先探测端口，已经在监听就复用。`18080`（local-runtime 既有
fixture）从不被修改。

18090 上的特殊路由，用来测 Fetch 的真实坑：

| 路由 | 行为 |
|---|---|
| `/healthz` | `ok` |
| `/redirect/...` | 302 链，最终落在 about 页 |
| `/cookie-gate/...` | 无 Cookie 时返回同意页，带 Cookie 才返回正文 |
| `/meta-refresh/...` | `<meta http-equiv="refresh">` 跳转到真实页 |
| `/pdf/...` | 有文本层的 PDF |
| `/js-rendered/...` | JS 注入联系人块（最小 DOM 模拟，不是浏览器） |
| `/not-found` | 410 |
| `/binary` | `image/png` 字节 |

`MockFetchAdapter` 对非文本 content-type 直接返回空文本而不是把二进制当 HTML 解码：
`HTTP 200` 不等于"读到了内容"。

## 5. 三条不可越过的线

1. **不混分**：Mock 结果只出现在 `kind="mock"` 的 manifest 里。真实 Provider 必须
   有独立的 run_id、provider 名与成绩单；报告里分开成节。
2. **不冒充**：`mock_qualification_rules` 等参考实现只读输入（及同批更早的输入），
   **从不读 gold**。重复检测靠"同 domain 在更早的 case 出现过"，不是查答案。
3. **不编造**：抽取只报告快照里可见的字符串；没有个人邮箱就写没有；MX/SMTP 一律
   记 `not_attempted`，不虚报验证成功。
