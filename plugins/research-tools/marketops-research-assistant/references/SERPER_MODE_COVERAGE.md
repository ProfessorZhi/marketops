# Serper 覆盖口径：已声明 ≠ 已核实

**这份文档回答一个问题：`marketops_search(provider="serper", mode=...)` 的九个 `mode`
里，哪些是我们**核实过**的？答案是：**一个都没有。** 九个都是「已声明」，各自带出处与
核实等级。声明与核实是两件事，把前者的数量报成后者的数量，正是本项目被审计到的缺口。

程序侧的唯一权威是 `scripts/serper_modes.py`；本文只是它的可读版本，**不是**第二份判据。
`marketops_search_capabilities()` 返回的 `serper_coverage` 块直接来自那里。

## 1. 2026-10-17 实际读到了什么

| 来源 | 结果 |
| --- | --- |
| `https://serper.dev/` | **读到了**。列出十个纵向标签（Search / Images / News / Maps / Places / Videos / Shopping / Scholar / Patents / Autocomplete）与样例响应键（`organic` / `images` / `news` / `places` / `videos` / `shopping`）。**没有端点路径、没有单价、没有分页参数。** |
| `https://docs.serper.dev/` | **没有解析**（DNS `ENOTFOUND`）。本机没有读到 Serper 的官方 API 参考。 |
| 端点路径（`/search`、`/places`、`/scholar` ……）、`num` 规则、`page` 语义 | 只从**第三方**产物读到：客户端 SDK、一个 MCP toolkit、一个 n8n 节点、一份社区 OpenAPI 文件。 |

**没有向 Serper 发出任何请求。** 未核实的不是「调用会不会成功」，而是「这些路径是否
真的是厂商提供的分类」——而一次付费调用买不到后一个问题的答案。

「读到了官网首页的标签」与「读到了官网的 API 参考」不是同一格；把两者并成一格，就是
把 UI 文案当成路由表。所以下面的 `evidence` 逐条写清它站的是哪一种。

## 2. 十一个已声明分类，十二个已声明路径

| mode | 实际端点 | 结果键 | 可调度 | 核实等级 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `web` | `/search` | `organic` | ✅ | `SECONDARY_SOURCES_ONLY` | 唯一所有来源都一致的分类 |
| `maps` | `/places` | `places` | ✅ | `SECONDARY_SOURCES_ONLY` | **不发往 `/maps`**；第三方来源称 `/maps` 第 2 页起需要 `ll`，本适配器从不发 `ll` |
| `images` | `/images` | `images` | ✅ | `SECONDARY_SOURCES_ONLY` | 第三方称 `num` 只接受 10 或 100，且 `num>10` 使**单价翻倍** |
| `news` | `/news` | `news` | ✅ | `SECONDARY_SOURCES_ONLY` | 第三方称此处可用时间范围过滤 |
| `videos` | `/videos` | `videos` | ✅ | `SECONDARY_SOURCES_ONLY` | |
| `shopping` | `/shopping` | `shopping` | ✅ | `SECONDARY_SOURCES_ONLY` | 第三方称端点自行固定结果条数 |
| `scholar` | `/scholar` | `organic` | ✅ | `SECONDARY_SOURCES_ONLY` | 第三方称 `num` 被忽略 |
| `patents` | `/patents` | `organic` | ✅ | `SECONDARY_SOURCES_ONLY` | |
| `autocomplete` | `/autocomplete` | `suggestions` | ✅ | `SECONDARY_SOURCES_ONLY` | 结果键**没有**出处，是适配器历史上就在解析的字段 |
| `lens` | `/lens` | `organic` | ✅ | `SECONDARY_SOURCES_ONLY` | 官网首页**没有** Lens 标签，出处只有第三方；翻页未取到出处，故 `page>1` 拒绝 |
| `reviews` | `/reviews` | `reviews` | ❌ | `SECONDARY_SOURCES_ONLY` | **已声明但本适配器服务不了**：端点要 `placeId` 且按 cursor（`nextPageToken`）翻页，本适配器两者都不发；`assert_dispatchable("reviews")` → `SERPER_MODE_REQUIRES_UNSENT_PARAM`。它留在目录里可见，而不是静默消失 |

**十二个已声明路径** = 上表十一个 + `/maps`（`UNROUTED_DECLARED_PATHS`）。`/maps` 被声明但**没有**
mode 指向它：把一个 mode 映到一条新路径是一次**活的请求形状变更**，而第三方来源对「`/maps` 与
`/places` 是一个端点还是两个」说法不一，所以本适配器保持它已经发出去的路径，只把这个歧义记下来。

**关于协调者的「13 端点」**：第 13 个是 `scrape.serper.dev`，**不同主机**（POST 到它的根）。本适配器
的出网白名单只有 `google.serper.dev`，所以它**不声明、不调度**——把另一个主机上的爬取端点算进
搜索分类，正是本 lane 要防的那种超额声明。要收 13 这个数，需要单独决定 scrape 是否属于本能力面。

**没有声明、因此不可调度的**：`scrape`、以及把 `/search` 当 mode 名用。从一个别人的客户端库里
抄来一个分类，等于把别人的覆盖范围当成我们的。

## 3. 分页：三种拒绝，三句不同的话

`page` 是**可选**参数。不传时请求体与改动前逐字节相同——这条有测试
（`test_default_requests_keep_their_pre_change_shape`），因为「默认行为没变」也是
一个会被后来的顺手改动打破的事实。

| 情形 | 结果 |
| --- | --- |
| 声明为 `PAGE_1_BASED` 的分类（`web`/`images`/`news`/`videos`/`shopping`/`scholar`/`patents`） | 允许 1..10；`page>1` 时请求体加入 `"page": n` |
| `autocomplete` | 声明为不分页；`page>1` → `SERPER_MODE_NOT_PAGINATED` |
| `maps` | 声明为「需要我们不发送的参数」；`page>1` → `SERPER_MODE_PAGINATION_UNVERIFIED` |
| `lens` | 翻页出处未取到 → `SERPER_MODE_PAGINATION_UNVERIFIED` |
| `reviews` | 不可调度 → 任何页都先撞 `SERPER_MODE_REQUIRES_UNSENT_PARAM` |
| 任何分类的 `page<1` 或超出声明的上界 | `SERPER_PAGE_INVALID` |

三种拒绝分开，是因为它们的处置不同：去改页号、去换分类、去补一个参数。折成一句
「不支持」会让调用方去查错的地方。页号校验发生在**预留之前**——被拒的一页不该花掉
一次付费额度。

**上界 10 本身是第三方口径**，不是厂商声明。

## 4. 计价：有出处、有版本、没有数字

Wave-1 验收清单要求「`num>10` 双倍 credit 规则**进入计价**」。它进入的是 `pricing_terms(mode)`
的**规则**，不是价格：

```json
{"source_id": "serper.credit_rules", "version": "unverified-2026-10-17",
 "priced_at": "2026-10-17",
 "rules": [{"rule": "images_num_gt_10_doubles_credit", "effect": "CREDIT_MULTIPLIER",
            "multiplier": 2, "applies_to": ["images"],
            "verification": "SECONDARY_SOURCES_ONLY", "evidence": "…", "observed_at": "2026-10-17"}],
 "unit_price_usd": null, "unit_price_verified": false,
 "credit_basis": "UNKNOWN_ACCOUNT_DEPENDENT"}
```

`source_id` / `version` / `priced_at` 三者齐备，对应契约 IL-6 的「缺一即拒绝预约」。
`unit_price_usd` 恒为 `None`、`unit_price_verified` 恒为 `false`：**一个 source id 不是价格**。
一条写进代码的价格会活得比它所描述的那份条款更久，而在本账户上「免费」与「每千次多少钱」
都还没有被读过。`multiplier` 是规则里的常数（第三方说 2），不是单价的替代品。

批量请求的倍数规则（`batched_request_costs_a_multiple_of_single_query`）同样进表，但
`multiplier` 为 `null`——出处只说了「是倍数」，没说几倍，这里不替它补一个数。

## 5. 怎样把这里的一格变成 `LIVE_VERIFIED`

需要一次**单独授权**的、有界的真实请求，并且留下该次请求的回执（请求体、响应摘要、
时间、费用状态）。参照架构收敛评审 P2-04：每供应商一次有界探针，前提是显式授权。
在此之前：

- `marketops_search_capabilities()["serper_coverage"]["live_verified_modes"]` 是 `0`；
  这个数是**算出来的**，不是写死的——真有回执的那天它会自己动。
- 任何「Serper 支持 N 类搜索」的说法都必须带上：声明九类、核实零类。

相关：[双搜索接入](DUAL_SEARCH_SETUP.md)、[Provider 路由](PROVIDER_ROUTING.md)、
[五家 Provider 申请与鉴权指南](PROVIDER_ONBOARDING.md)。
