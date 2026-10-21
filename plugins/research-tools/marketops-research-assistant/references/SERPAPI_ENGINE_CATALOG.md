# SerpApi 引擎目录：按资源惰性读取，按哈希钉住

SerpApi 官方 MCP（`https://mcp.serpapi.com/mcp`）不把引擎写在 `search` 工具的 schema
里。它把引擎作为 **MCP resources** 发布：

- 索引：`serpapi://engines`
- 单引擎参数 schema：`serpapi://engines/<engine>`

这两条 URI 就是 `scripts/serpapi_engine_catalog.py` 依赖的全部契约，**来源是厂商自己的
MCP 仓库 README**，不是推断出来的。连接、`tools/list` 与读资源都不需要 API Key；真正
的搜索才需要。

程序侧权威是 `scripts/serpapi_engine_catalog.py`；本文是它的可读版本。

## 1. 为什么不把引擎一次性读进来

产品简报点名的失败形状是「不加区分地暴露厂商的每一个方法，或者干脆把高级方法藏起来」。
把上百个引擎 schema 摊平进一次工具返回，正好是前半句：它把上下文花在这次研究问题根本
不会碰到的引擎上，并且把一大段**厂商撰写的文本**交给宿主 Agent——而收敛评审把上游工具
描述归为**不可信输入**，不得直接变成对宿主的指令。

所以契约是反过来的：

1. 读一次索引，拿到**引擎名列表**（便宜，可进上下文）；
2. 只有调用方点名某个引擎时，才 `read_resource("serpapi://engines/<engine>")` 读它的
   schema；
3. `EngineCatalog.summary()` 返回计数与新鲜度，**永远不含任何 schema 正文**——这是允许
   进入 Agent 上下文的那一种形状。

测试用一个假 MCP session 钉死了这一点：索引里有 118 个引擎时，`discover_index()` 只发生
**1 次** `read_resource`，0 次单引擎读取。

## 2. 为什么缓存要按哈希钉住

缓存的用途是让同一轮里的第二次调用不必再问厂商。一个「磁盘上是什么就信什么」的缓存，
于是多了一条路径：另一个进程、另一个 Agent 或一个人改了那个文件，它就变成了「厂商的
schema」。

每条缓存项存下它被读到那一刻的精确内容的 sha256；读回时重算。对不上是
`SERPAPI_ENGINE_CACHE_INTEGRITY`，不是静默返回。缓存文件本身无法解析时是
`SERPAPI_ENGINE_CACHE_UNREADABLE`，**不是**「目录里没有引擎」——这两种状态导向不同的
动作（重读厂商 vs. 相信一个空目录），把它们并成一格，就是一次空答案被读成一份完整答案。

缓存位于客户数据根：`<MARKETOPS_RESEARCH_DATA_ROOT>/state/serpapi-engine-cache.json`，
与搜索台账同一个根、同一套目录约定。

## 3. 显式不声称的东西

- **不声称目录是最新的**。每条记 `observed_at`，`is_stale()` 按 TTL 报告，过期时
  `freshness_note` 写 `INDEX_STALE_FALLBACK_TO_LAST_OBSERVED`——过期可读,但不许装新。
- **不声称这个账户有权跑某个引擎**。读 schema 不是一次搜索，也不证明账户能执行它。
- **不碰费用**。`build-engines` 生成的 schema 里可能有引导字段，但这里没有价格判断。

## 4. 接线状态（LIVE-UNVERIFIED）

本模块提供的是**契约与缓存**，不是一次连上厂商的调用：

| 能力 | 状态 |
| --- | --- |
| 索引/单引擎解析、缓存、哈希校验、TTL | `CODED` + `OFFLINE-TESTED`（假 session） |
| 对 `mcp.serpapi.com` 的真实 `resources/list` + `resources/read` | **未验证**：本机没有连过厂商 |
| 引擎数量、单引擎 schema 形状 | **未验证**：118 这个数是任务书给的量级，不是我们数出来的 |
| 某引擎在本账户是否可执行、花多少 | **未验证**，且读 schema 也证明不了 |

把资源读取接进实时 MCP session 的那一步属于宿主 MCP 会话的持有者（内嵌官方 MCP 客户端的
`research_official_mcp.py`），因此**不在本模块内**——本模块只提供
`discover_index(session, catalog=...)` 与 `discover_schema(session, engine, catalog=...)`
这两个接收 session 的入口，供持有会话的一方调用。

## 5. 谁在消费这个目录（Wave-2 起）

`scripts/research_adapter_serpapi.py` 是 §6 的 `ProviderAdapter`，它把上面三条契约包成三个
capability：

| capability_id | 做什么 | tier |
| --- | --- | --- |
| `serpapi.engine_catalog` | 读一次 `serpapi://engines`，记下引擎名、出处与观测时间 | T2 |
| `serpapi.engine_schema` | 读**一个** `serpapi://engines/<engine>`，按 sha256 钉进缓存 | T2 |
| `serpapi.search` | 走 `search(params, mode='compact')`；引擎**必须**先在本目录里解析出来 | T2 |

两条资源读取标 T2 而不是更低的档，理由要写清楚：它们是远端的、需要凭据的调用。**每次调用是否计费
我们没有读到过**——而「没读到」不等于「免费」。收敛评审明说远端只读调用不因此变成本地或免费，所以在
读到本账户的价格之前，它们和搜索一起待在同一个开关后面，而不是被降到一个更便宜的档上。
`effect_class` 仍写 `READ_ONLY`：这一格记的是这次调用**做了什么**，把 `BILLABLE_READ` 写上去就又是
在声称一笔没人读过的费用。

**`serpapi.search` 要求那个引擎的 schema 已经读过。** 这不是顺手加的门槛：只有读过 schema 才谈得上
「这个引擎存在、它的必填参数我们看过」。所以解析用 `resolve_engine()` 的 `READY`；而
`serpapi.engine_schema` **不用**它——对那个 capability 来说「schema 还没缓存」正是它要处理的正常情况，
用 `READY` 当门槛会让唯一能填缓存的 capability 在缓存填满前一直拒绝运行。

**`serpapi.rest_search` 声明为 `DISABLED`**，留在目录里可见。它是通往同一家付费供应商的第二条路，
默认安装不该有它；产品简报要求它活到官方 MCP 契约验证完成再做退役决定，所以它没被删，只是默认关闭。

## 6. 与旧 REST 适配器的关系

`marketops_search(provider="serpapi", ...)` 走的是 `serpapi.com/search.json` 上的
**兼容 REST 表**，只有九个常见 Google 类型（见 `dual_search_mcp.SERPAPI_REST_ENGINES`）。
它**不是** SerpApi 的引擎目录，也不能被读成「SerpApi 覆盖」。按收敛评审 P2-02：在真实
MCP 契约验证成功之前，这条 REST 路径保持**不参与自动路由**，但**不删除**。

相关：[嵌入式官方 MCP](EMBEDDED_OFFICIAL_MCP.md)、[官方 Skill/MCP 登记](OFFICIAL_SKILLS_MCP_REGISTRY.md)。
