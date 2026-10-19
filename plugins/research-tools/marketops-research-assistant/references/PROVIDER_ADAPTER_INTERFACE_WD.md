# W-D provider adapter 接口（给 Integrator / QA 的稳定面）

**面向**：W-INTEGRATOR（接线）、W-QA（独立复核）。**不**是给宿主 Agent 的文档。
**状态**：`CODED` + `OFFLINE_TESTED`。**没有**任何 `UPSTREAM_SCHEMA_VERIFIED` —— 未连接过
`mcp.serpapi.com`，未向 Serper 发过请求，引擎数量不是我们数出来的。

两个模块：`scripts/research_adapter_serpapi.py`（官方 MCP）、`scripts/research_adapter_serper.py`（REST）。
两者都实现契约 §6 的 `ProviderAdapter`：`capabilities()` / `probe_schema()` / `execute(req)` / `cancel(ref)`。
另有**模块级** `capabilities()`，供建 Registry 时不构造实例就能读。

## 1. 能力表（建 `CapabilityEntry` 用）

| capability_id | provider | transport | tier | effect_class | allowed_params |
|---|---|---|---|---|---|
| `serpapi.engine_catalog` | serpapi | `official_mcp` | T2 | READ_ONLY | （无） |
| `serpapi.engine_schema` | serpapi | `official_mcp` | T2 | READ_ONLY | `engine`, `refresh` |
| `serpapi.search` | serpapi | `official_mcp` | T2 | BILLABLE_READ | `engine`, `query`, `country`, `language`, `limit` |
| `serper.search` | serper | `rest` | T2 | BILLABLE_READ | `query`, `country`, `language`, `limit`, `page`, `mode` |

`describe_capabilities()` 直接产出上表（外加 `availability: ENABLED`、`readiness: CODED`）。

**两条资源读取为什么也是 T2**：它们是远端、需要凭据的调用。**每次是否计费我们没有读到过**，而
「没读到」不等于「免费」，所以不降到更便宜的档。`effect_class` 保持 `READ_ONLY`——那一格记的是调用
*做了什么*，写 `BILLABLE_READ` 等于声称一笔没人读过的费用。

**`country` / `language` 是必填**：它们是 canonical operation key 的两个轴。`serpapi.search` 的
`engine` 有默认 `google_light`。

## 2. `execute()` 返回什么

返回一个 frozen dataclass（`SerpApiResult` / `SerperResult`），字段**恰好**是 §3.4 的十个；
`to_adapter_result_fields(result)` 摊成可用 `AdapterResult(**payload)` 构造的映射。多出来的信息
走 `payload`，不加第十一个键（多一个键就是集成时的 `TypeError`）。

`outcome` 取值（§2）：

| outcome | 含义 | 网关会做什么 |
|---|---|---|
| `OK` | 成功 | settle |
| `BLOCKED` | **本地拒绝，未触达上游** | settle（无常生费用） |
| `FAILED` | 上游明确失败（拿到了响应） | settle |
| `UNKNOWN` | **派发之后超时/没有响应对象**，费用与效果均未知 | **hold（`mark_unknown`）** |

**`UNKNOWN` 与 `FAILED` 的区分是承重的**：超时必须是 `UNKNOWN`，否则网关会把一笔没有答案的费用
「结算」掉。`payload["charge_state"]` 为 `UNKNOWN` 时同上。
这一条我最初写反了（把超时记成 `FAILED`），是对着**真实 Gateway** 跑一致性测试时被纠正的。

## 3. 拒绝词表

- `caveats[0]`：当 `outcome == BLOCKED` 时是 **§5.3 闭集成员**（网关只读这一格）。
- `payload["reason"]`：**精确的本地码**。同一件事翻成闭集码之后，只剩这一格能区分
  「没有绑定会话」与「引擎不在索引里」。
- `payload["contract_reason_code"]`：显式写出映射结果，便于复核。

本地码 → 闭集码的映射在 `CONTRACT_ANALOGUE`，每一行都写明契约里哪一条说的就是这件事。
**派发之后的码刻意没有映射**（`POST_DISPATCH_CODES` ∩ `CONTRACT_ANALOGUE` = ∅）：给一次真的发生过的
失败配一个政策码，就是把它记成一次没出门的调用。

新增一个闭集码要走 §8 的 CONTRACT-CHANGE-REQUEST。

**SerpApi 关键本地码**：`SERPAPI_RESOURCE_NOT_ADVERTISED`（列过但没读 → 预派发 →
`CAPABILITY_NOT_AVAILABLE`）、`SERPAPI_RESOURCE_UNAVAILABLE` / `RESOURCE_EMPTY` /
`SCHEMA_TOO_LARGE` / `ENGINE_SCHEMA_UNREADABLE`（读过但没拿到可用内容 → 后派发 → `FAILED`）、
`SERPAPI_AMBIGUOUS_TIMEOUT`（→ `UNKNOWN`）。

**Serper 关键本地码**：`SERPER_MODE_REQUIRES_UNSENT_PARAM`（`reviews` 要 `placeId`+cursor，本
adapter 不发）、`SERPER_MODE_PAGINATION_UNVERIFIED`、`SERPER_AMBIGUOUS_TIMEOUT`。

## 4. 一次逻辑操作一次派发

`search_canonical.DispatchGuard`：**进程内、按实例**。同一 canonical key 第二次 `execute` →
`BLOCKED` + `DUPLICATE_OPERATION`（§5.3 码）。它**不是**账本——durable 记录是 `UsageLedger` 的
`op_signature`；它补的是账本单独关不上的一格：**超时之后的重发**。

**它只在请求真的会上网时才占名额**：`serpapi.engine_schema` 命中缓存时不占（IL-5，缓存命中不产生
上游调用），否则「第二次读缓存」会被误判成重复操作。

key 的两个来源（都在 `search_canonical`）：

```python
operation_signature(canonical_arguments(...))     # 搜索：provider/mode/query/country/language/limit/page/engine/account
resource_operation_key(provider=, capability_id=, params=)   # 资源读取：capability + 标量参数
```

两者都**不含 transport**：同一查询走 REST 与走 MCP 是**一次**花费机会。`account` 是值不是通配。

## 5. 计价（Serper）

`serper_modes.credit_estimate(mode, num=...)` 给出**这一次请求**的 credit 倍数：

```json
{"credits": null, "multiplier": 2, "base": "ONE_QUERY",
 "applied_rules": ["images_num_gt_10_doubles_credit"],
 "source_id": "serper.credit_rules", "version": "unverified-2026-10-17",
 "priced_at": "2026-10-17", "observed_at": "2026-10-17",
 "unit_price_usd": null, "unit_price_verified": false,
 "credit_basis": "UNKNOWN_ACCOUNT_DEPENDENT"}
```

`credits` 恒为 `null`、`unit_price_usd` 恒为 `null`：**本账户的单价从来没被读过**。把它写成 `0`
会让下游读成「这次免费」。`multiplier` 是规则算出来的、可判的那一半。回执里带
`payload["credit_estimate"]`。

## 6. 兼容性测试（I / QA 直接跑）

```bash
cd plugins/research-tools/marketops-research-assistant
PY=/home/operator/toolchains/marketops-research-plugin-e2e/bin/python
$PY -m unittest tests.test_wave1_serp_adapter_contract \
                  tests.test_wave1_serp_offline_upstreams \
                  tests.test_wave1_serp_robustness
```

**对着真实 Gateway 跑**（我本人跑过的方式；只读借用集成树，不修改）：

```bash
PYTHONPATH=<integration tree>/scripts $PY -m unittest <上面三个文件>
```

那会启用 `GatewayConformanceTests`（含「先预留再派发」「被拒的调用要结算」「超时要挂起」
三条断言）。没有 `PYTHONPATH` 时这类会**显式 skip**，不会假装跑过。

**给 I 的三条硬约束**（任一被打破，上面的用例会红）：
1. 我的 `capabilities()` 与 Registry 的 `capability_id` 必须一致（网关会交叉核对，
   不一致是 `ADAPTER_DOES_NOT_ADVERTISE_CAPABILITY`）。
2. `BLOCKED` 的 `caveats[0]` 必须是 §5.3 成员——网关对闭集外的码抛 `ContractViolation`，
   而它拒得对。
3. adapter **不做** tier/预算判定（IA-2）；有一条用例 grep 本模块源码，禁止出现
   `MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS` / `MARKETOPS_SEARCH_MAX_CALLS` /
   `*_ALLOW_NETWORK`。

## 7. 还没做、需要别人做的

- **真实会话**：`SerpApiAdapter(session=...)` 的 session 由持有官方 MCP 连接的一方注入
  （`research_official_mcp.py` 属 W-C 区段）。本 adapter 不自己开连接。
- **账本接线**：`op_signature` 由 W-B 的 `UsageLedger` 拥有。本模块只提供键与拒绝，
  不写账。B 的价目表是 credit-only 时，按美元判定的闸门是惰性的（另有记录）。
- **live list/tools**：仍然 gated 在 Stage 0 B0 与已验证鉴权上。本分支**不请求也不提供 Key**。

## 8. Wave-4 增补：配额计量、schema drift、reviewed=false

### 8.1 Serper 的 credit 计量进得了 campaign floor

`serper_modes.price_fields(mode, num=...)` 直接产出 B 的 `Price` kwargs：

```python
{"provider": "serper", "capability": "serper.search",
 "unit_micros": 0, "quota_unit": "credits", "quota_per_call": 2,
 "source": "serper.credit_rules", "version": "unverified-2026-10-17",
 "observed_at": "2026-10-17T00:00:00+00:00",
 "free_tier": False, "verified": False}
```

三件事合起来才让 floor 真的动：

1. `unit_micros=0` + `free_tier=False` 是 B 账本对「**没有已核实的美元价**」的编码
   ——**不是免费**。B 的 `_usd_price_is_known()` 对它返回 `False`。
2. `quota_per_call` 是**倍数**（`num>10` 的 images = 2 credits，其余 1）。我们已知的是
   **比例**，不是每 credit 多少钱——所以 `credits`/`unit_price_usd` 仍是 `null`。
3. 因此以 `meter="credits"` 冻结的 B0 能算出 floor（20%）并随调用下降，**在美元价为 0 或未知时
   照样动**。用例：`CampaignBaseline(units=100, meter="credits")` → `floor_units=20`、
   `spendable_units=80`；一次翻倍调用扣 2。

`observed_at` 是带时区的完整时间戳（B 的 `Price` 拒收裸日期）。我们的读数是**日精度**，
所以编码在当天零点，并用 `PRICING_PRECISION = DATE_PRECISION_ENCODED_AT_MIDNIGHT_UTC`
标注——不假装知道那个小时。

**模块不 import B 的 `Price`**：类型归账本，在这里 import 会让一个 vendored 副本成为价格权威。
它只产出字段名，由 owner 构造。有 ledger 时用例会真构造一次（`PYTHONPATH` 指向集成树即启用）。

### 8.2 Schema drift 在 T2 失败关闭

- `catalog.schema_verdict(engine, observed_text)` → `MATCH` / `DRIFT` / `ABSENT` / `UNVERIFIED`
  （无 pin = 没比较过 = `UNVERIFIED`；解析不了 = `ABSENT`，与「比过了但不一致」不是同一句话）。
- `discover_schema(refresh=True)` 读到不一致时：**记 drift，不覆盖已接受的 pin**，返回
  `schema_verdict="DRIFT"`。让新正文变成下一次校验的基线，等于让 drift 把自己洗白。
- **`serpapi.search` 在派发前**检查 `catalog.drifted(engine)` → `BLOCKED`
  `CAPABILITY_SCHEMA_DRIFT`（§5.3 码）。本 adapter 三个能力全是 T2，所以没有「带 caveat 继续」的档。
- `engine_schema` 的 refresh 撞到 drift → 也是 `BLOCKED`，`payload` 带
  `accepted_sha256` / `observed_sha256` 两个值供人复核。
- drift 落盘（`state` 文件），重载后仍在；只有 `catalog.clear_drift(engine)` 能清——**那是一次
  明确的接受动作**，不是超时自愈。

### 8.3 reviewed 默认 false

`serpapi_engine_catalog.REVIEWED_ENGINE_RESOURCES = False` 是常量，不是推断。
厂商 README 只说明 URI 存在，不说明这台机器的账号服务端真的提供它、形状也一致。
`probe_schema()["resources_reviewed"]`、`describe_capabilities()[*]["resources_reviewed"]`、
`catalog.summary()["reviewed"]` 三处都报 `False`，并带 `RESOURCE_REVIEW_NOTE`。
**没有任何路径会写 `True`**，直到一次单独授权的只读握手真的看过。

### 8.4 值与路由仍然分开

`mode` 与 `engine` 是**取值**，永远不是路由。`mode="/search"`、`engine="https://…"`、
`engine="../engines"` 一律拒绝（`UNSUPPORTED_SERPER_MODE` / `CAPABILITY_PARAM_NOT_ALLOWED`），
不会因为长得像路径就被拼进 URL 或 URI。

## 9. Wave-5（CD-03）：两个 transport 共用的 search 身份

协调者 `CONTRACT-DECISION-DEDUP-IDENTITY.md` 的 CD-03 指出：REST 路径把
`{"query": "serpapi|search|pool|gb"}` 交给账本，MCP 路径交 `{"action": "serpapi_mcp_search",
"signature": "sig-0"}`。**这两种形状在任何实现下都不会同 digest**，所以 IL-2 不可达 ——
缺口在 adapter → ledger 的边界，不在账本公式。本 Wave 提供那一层。

### 9.1 两个可导入函数（`search_canonical`，稳定面）

```python
search_operation_args(*, query, capability="search", country=None, language=None,
                      mode=None, engine=None, limit=None, page=None, extra=None) -> dict
#   -> {"capability": "search", "query": <规范化>, "params": {<稳定轴，键已排序>}}

search_route_identity(*, provider, account_ref, query, capability="search",
                      units=1, site="", **operation_args) -> dict
#   -> {"provider", "account_ref", "capability", "units", "site", "args"}
```

`search_route_identity` 返回的**正是 B 的 `route_shape(request, args)` 吃的那些值**，
但它返回映射、不返回摘要：摘要归账本，这里再算一个就是第二个答案。

### 9.2 给 I 的接线（`dual_search_mcp._reserve` 与 `research_provider_extensions._reserve`）

两处都改成：

```python
args = search_canonical.search_operation_args(
    query=q, capability="search",
    mode=mode,                      # Serper 侧：分类
    engine=engine,                  # SerpApi 侧：引擎（没有就省略）
    country=country, language=language, limit=num, page=page)
# 然后 OperationRequest(..., args=args)
```

**同一个 provider 的两条路径必须传同一组值。** 只要两边都从 `search_operation_args` 出，
transport 不同不会分叉（CD-02a）；query / country / language / limit / page / mode / engine
任一不同则必然分叉（CD-02b，防止退化成「这个项目碰过这个 provider」）。

### 9.3 硬规则（函数会强制执行，不是文档约定）

| 规则 | 行为 |
|---|---|
| `action` / `signature` / `transport` / `run_id` / `adapter` / `tool` / `endpoint` / `url` / `api_key` / `key` | `extra` 里出现即 `IDENTITY_FIELD_FORBIDDEN` —— **拒绝，不是静默丢弃** |
| 非标量（dict/list） | `IDENTITY_VALUE_NOT_SCALAR` |
| 省略 `limit` / `page` | 物化为 10 / 1，因此「省略」与「显式默认」是**一个**操作，不是两个 |
| query | NFC + 空白折叠 + `casefold()` |
| `country` / `language` | 归一成 `GB` / `en` |
| 厂商没有的轴 | **省略**，不补默认值（Serper 无 `engine`，SerpApi 无 `mode`） |

### 9.4 一处刻意的不对称

**身份大小写不敏感；线上 query 大小写保留。** `canonical_arguments()`（决定发出去什么）
只折叠空白，因为那是用户写的字、是厂商收到的字符串；`search_operation_args()`（决定是不是
同一笔）做 casefold，因为 CD-03 要求「大小写先归一」。前者是请求，后者是拒绝重复 ——
**拒的是重复，不是拼写**。

### 9.5 测试

```bash
PY=/home/operator/toolchains/marketops-research-plugin-e2e/bin/python
$PY -m unittest tests.test_wave1_serp_identity_contract          # 21 条，9 条在无账本时 skip
PYTHONPATH=<integration>/scripts $PY -m unittest tests.test_wave1_serp_identity_contract  # 21 条，0 skip
```

对着 **B 的真账本**（`UsageLedger` + `CapabilityRegistry` + `PricingCatalog`，临时 sqlite）验证：

- 同一操作走 `rest_https` 与 `official_mcp` → `route_shape` **相等**，第二次 `reserve`
  → `DUPLICATE_OPERATION_SIGNATURE`；
- 不同 query / engine / country / limit / page → `route_shape` 不同，第二条**放行**；
- `mark_unknown` 之后：**同**一操作仍被拒（持有敞口），**不同** query 正常放行；
- 把一行改成 `route_shape=''`、`origin='legacy'` 后，新的同一操作**放行**（CD-05），
  因为真实 shape 是 64-hex，永远不等于迁移占位符。

**没有网络调用**：MCP 侧是注入的 fake session，REST 侧是注入的 transport，账本是本地 sqlite。

## 10. Wave-6：有界只读 discovery 契约（给 B / I）

现状不是「读不到厂商」，而是**仓库里没有写下一个安全的 discovery 被允许做什么**。
授权一旦到位，第一个跑它的人只能临场发挥。这一节就是那个契约，用**合成 in-memory session**
逐条钉住 —— 它证明的是契约的行为，不是厂商的事实。

### 10.1 入口

```python
serpapi_engine_catalog.discover_surface(session, *, catalog=None,
                                        expected_tool_count=None, resources=()) -> dict
SerpApiAdapter.probe_surface(*, resources=(), expected_tool_count=None) -> dict
```

`probe_surface()` 与 §6 的 `probe_schema()` **刻意分开**：后者不联网、不伪造快照；
前者是显式探测。两者都不是 LIVE 证据。

### 10.2 方法序列就是契约

```text
initialize → notifications/initialized → tools/list → resources/list → resources/read*
```

`tools/call` **不在** `DISCOVERY_METHODS` 里，也不会有任何代码路径产生它：
一次能调用工具的 discovery 就是一次换了名字的计费调用。测试里 `call_tool` 是一个陷阱，
被调到即断言失败。

### 10.3 描述符哈希覆盖整条描述符

```python
tool_descriptor(item) -> {name, description_sha256, input_schema_sha256,
                          annotations_sha256, descriptor_sha256}
surface_digest(list) -> str          # 顺序无关
```

`descriptor_sha256 = H(name + description + inputSchema + annotations)`。
**只哈希名字看不到 schema 改动**——而 schema 才是「这个工具允许被怎么调用」的定义。
同名的 `search`、换了 `inputSchema`，digest 必须变；这条有用例。

### 10.4 什么时候关闭

| 情形 | 码 |
|---|---|
| 读一个 `resources/list` 里没有的 URI | `SERPAPI_RESOURCE_NOT_ADVERTISED`（**不读**） |
| 正文超过 `DISCOVERY_MAX_BYTES`（= `MAX_SCHEMA_BYTES`） | `SERPAPI_SCHEMA_TOO_LARGE` |
| URI 不是 `serpapi://engines` / `serpapi://engines/<合法 engine>` | `SERPAPI_RESOURCE_URI_INVALID` |
| 工具描述里出现面向宿主的指令文本 | `SERPAPI_SURFACE_UNTRUSTED_INSTRUCTIONS` |
| 工具数量与 `expected_tool_count` 不符 | `SERPAPI_SURFACE_TOOL_COUNT_CHANGED` |
| session 无法发送 `notifications/initialized` | `SERPAPI_SESSION_CANNOT_NOTIFY_INITIALIZED` |

前五个是 `DISCOVERY_ERROR_CODES`（探测路径），**不在** `CATALOG_ERROR_CODES`（派发路径）里：
一次探测失败不是一次能力拒绝，两套词表分开声明、分别判死。

### 10.5 标签

`discover_surface()` 的报告恒为：

```json
{"reviewed": false, "live": false,
 "evidence": "SYNTHETIC_SURFACE_NOT_VENDOR_VERIFIED",
 "provenance": "NOT_LIVE_IN_MEMORY_OR_INJECTED_SESSION"}
```

`REVIEWED_ENGINE_RESOURCES` 仍是常量 `False`，**没有代码路径会写 `True`**。
把它变成 `True` 需要一个真实的官方 capture **加人工审批**，两者都不在本分支。

### 10.6 Serper：unknown 美元价的显式词

`credit_estimate()` 增加 `"usd_basis": "UNKNOWN_NOT_FREE"`。
`unit_micros=0` + `free_tier=False` 已经是 B 的 `Price` 编码；这个是同一句话的可读版本，
供回执与报告使用。`quota_per_call` 恒为 1 或 2，不会凭空出现第三个值。

### 10.7 需要外部授权才能做的事

| 要做的事 | 缺什么 |
|---|---|
| 对 `mcp.serpapi.com` 真做一次 bounded discovery | Stage 0 独立 QA 通过 + 真实 B0 采集 + 已验证鉴权 |
| 把 `REVIEWED_ENGINE_RESOURCES` 置 `True` | 上述 capture **加人工审批** |
| 把 Serper 的分类从 `SECONDARY_SOURCES_ONLY` 提升 | 厂商 API 参考快照，或一次授权的有界真实请求 |
| 未来任何计费派发 | B0 与定价快照已捕获；当前四家均为 `NOT_CAPTURED` ⇒ 一律不发 |
