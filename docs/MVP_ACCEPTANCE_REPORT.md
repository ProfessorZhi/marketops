# MarketOps Research MVP —— 验收报告

**这是一份记录，不是一份规格。** 它记的是**在下面这几个 revision 上真的发生过什么**，
包括**没通过的部分**。它不描述「应该是什么样」——那是 `PRODUCT_SPEC.md` 与
`ARCHITECTURE.md` 的事。

按 `docs/README.md` §1 的分类，它属于 **B 类：冻结的盘点**，与 `CURRENT_STATE.md` 同类，
理由相同：它的每一格都绑着具体的时间、job id、行号与 revision，
**去「更新」它会让一份可复核的记录变成一份无法复核的叙述**。要今天的形态，读代码。

## 0. 怎么读这份

这一份里其实记着**三轮**研究，它们不是同一次验收。**读之前先分清楚**（这是 §12 的要求）：

| 轮 | job_id | `input_ref` / `idempotency_key` | 是什么 |
|---|---|---|---|
| 历史 | `1b1b7f8209c19491e1a26c34ad2207a8` | `kent-2026-09-21` / `kent-slice-001` | **历史残留。不属于任何一次验收。** 5 件，其中一件的租约至今停在 `leased` |
| 验收 A（cap=1） | `11ed121a8a9357139aa92ccc177c072a` | `acceptance-2026-09-23` / `accept-cap1-2026-09-23` | 第一次验收：**cap=1**，1 件（`kent-004`） |
| 验收 B（cap=2） | `373428064a88ecf78f248588f2e458a2` | `acceptance-2026-09-23-cap2` / `accept-cap2-2026-09-23` | **本报告的主角。** 3 件（`kent-005/006/007`） |

**本报告的每一格数字，除非明写「历史」或「验收 A」，都来自验收 B。**
本文件的 revision 头 —— **两个 revision 要分开读**：

```text
验收 B 跑在:      origin/main @ 9ef5ba449997b7911194dc7acf9f02e47654d5cc
本报告写于/部署于: origin/main @ cf4c7a6af33253f1542672dc993d662057654711
§C 末尾的补记:     其后一次 doc-only 提交（工件在 ARTIFACTS_ROOT 下，按路径取，不按 revision）
验收 B 的运行窗口: 2026-09-23T02:34:02Z .. 02:43:47Z
数据根:           /home/operator/artifacts/marketops-runtime
Quay 工作区:      /home/operator/artifacts/marketops-mvp-quay
```

第一行的意思：**验收 B 观察到的行为，是 `9ef5ba4` 那棵树的行为。**
第二行只是本文件自己的提交 —— 它不改变上面任何一个读数。
两个都写出来，是因为把它们合成一个会让 §L 那处不一致看起来像是被抹掉了。
第四行是**事后补的一条**，它不改变任何验收读数（零供应商成本，只跑派发器自己的函数），
但它也不该借用上面那两个 revision 的日期，所以单列。

验收 B **没有通过**，而且没有通过的地方是硬判据（§4 并发形状、§10 判定落盘）。
C / F / H 三节是 FAIL。**这份报告的价值有一半在那三节里**：
一个只记 PASS 的验收报告，和没有验收报告，对下一个人的区别是零。

---

## A. 最终 Job

```text
job_id            373428064a88ecf78f248588f2e458a2
scene_id          uk-acme-natural-grass
input_kind        known_accounts
input_ref         acceptance-2026-09-23-cap2        ← 新的，不复用历史
idempotency_key   accept-cap2-2026-09-23            ← 新的，不复用历史
request_sha256    26e80eea556e7177bc98d0d432552bc8280ffc7e234604d4f2d057af8fce85e3
created_at        2026-09-23T02:34:02.062750+00:00
```

`research_job.state` 至今是 `queued`，而其 3 件工作单元**全部已终态**。
`queued` 在这里**不是**「还在排队」的意思，是本仓的一个缺口 —— 见 §N.1。
`get_job` 的 `progress` 给出的才是真实进度：

```json
{"work_items": 3, "dispatching": 0, "stopped": 3, "not_started": 0}
```

`not_started` 从**租约**算而不是从时间算（那一格自己的注就是这么写的）——
所以它答的是「还没派出租约」，而这正是这里想要的答案。

---

## B. 三个 Account

全部来自既有的 source-backed 合格账号池，**没有一个是为了这次验收造的**。
三个都带既往公开来源证据，都不继承历史 A/B/C/D 结果。

| # | `subject_ref` | `work_item_id` | Quay task | `mapped_at` |
|---|---|---|---|---|
| 0 | `kent-005` | `70f3353e0692db0f99c7816f449c9405` | `MOPS-37342806-70f3353e` | 02:34:03.200646Z |
| 1 | `kent-006` | `8bfc5723683cd51e8f8c6f58ee9eb1d4` | `MOPS-37342806-8bfc5723` | 02:34:04.290665Z |
| 2 | `kent-007` | `a24321d385c292023e839e7ac04f2c53` | `MOPS-37342806-a24321d3` | 02:34:05.371695Z |

SubjectSnapshot 在 Job 准入时冻结：三件工作单元的 `work_item_snapshot` 行
`pinned_at` 都是 `2026-09-23T02:34:02.087417+00:00`（= Job 创建同一刻），
`scene_version` 都是 `cc9798f6a507a5fa991e2556c0f8d625ee45d7e330832b4461ec613b0ba25c07`。**已冻结，且冻结在同一个 scene 版本上。**

**为什么是这三个而不是 `kent-003` / `kent-004`**：先看预算台账。`kent-003`（9 次）
与 `kent-004`（14 次）已经用过历史 paid usage；`kent-005/006/007` 在本次之前**零付费查询**。
本轮的账在 §D。

---

## C. Loop Pulse 饱和 —— **FAIL**

要求（§4）：Quay worker cap = 2、Job 3 件，且必须**真的截到两个时刻**都 `In flight 2 / 2`。

**实测峰值 `in_flight = 1`。第二个 worker 一次也没有被派出去。**

证据是驱动器**自己写的** `.quay/worker-round.jsonl`（验收 B 的那一段，`round` 从 1 重新计）：

```text
ts                     round  in_flight  pool  stop_reason
2026-09-23T02:34:40.780Z   7        1       3   filtered-empty (all dispatchable candidates filtered by predicates)
2026-09-23T02:35:13.590Z   9        1       3   同上一行
2026-09-23T02:36:43.592Z  12        1       2   同上一行        ← 第 1 件终态，池 3→2
2026-09-23T02:37:59.055Z  14        1       2   同上一行
2026-09-23T02:40:59.853Z  20        1       1   同上一行        ← 第 2 件终态，池 2→1
2026-09-23T02:43:47.971Z  26        1       0   pool-empty (no dispatchable candidate in the ready pool)
2026-09-23T02:43:51.048Z  27        0       0   pool-empty …
```

读法：**池子里一直有活（`pool` 3 → 2 → 1），但在飞恒为 1，
`stop_reason` 恒是「全部可派候选被谓词过滤掉了」。** 这不是「活干完了所以没并发」——
是**派发器主动拒绝了第二个 worker**，而且是每一轮都拒绝。

### 根因（有源码一级的证据，不是推测）

Quay 0.11.0 的 `worker-driver.js`：

```js
// 55596   applyTaskFilters(shuffled, makeFilterContext(rootDir, {inFlight: inFlightTasks(), …}))
// 55600   else if (afterTaskFilters.length === 0) waitReason = "filtered-empty (all dispatchable candidates filtered by predicates)"

// 37861   function applyTaskFilters(candidateIds, ctx, names) {
//           …
//           return candidateIds.filter((id) => preds.every((p) => p(id)));

// 32709   var touchesDisjoint = { name: "touchesDisjoint", predicate: (ctx) => {
//           if (ctx.inFlight.length === 0) return () => true;      // ← 没人在飞时，对所有人放行
//           …
//           return (id) => inFlight.every((ifp) => checkTouchesPair(readTouches(id), ifp, expand).disjoint);

// 32735   function checkTouchesPair(parsedA, parsedB, expand) {
//           for (const [p, who] of [[parsedA, "A"], [parsedB, "B"]]) {
//             if (!p.hasSection || p.globs.length === 0) {
//               return { disjoint: false, … reason: `conservative: side ${who} declares no/empty ## Touches → serialize` };
```

三行合起来就是全部机制：

1. `inFlight.length === 0` → 谓词是恒真的，**所以第一个 worker 永远派得出去**。
2. 一旦有 1 个在飞，其余每个候选都要与它 `checkTouchesPair(...).disjoint`。
3. `checkTouchesPair` 的**第一条**分支：任一侧「没有 `## Touches` 节，或节是空的」
   → `disjoint: false`，理由是 `conservative: … → serialize`。

而验收 B 的三件任务**都没有 `## Touches` 节**。这一条是**量出来的**，不是推的：

```console
$ grep -c '^## ' <quay-root>/tasks/MOPS-37342806-70f3353e.md
0
```

对照本仓 `marketops/application/dispatch.py` 的 `_TASK_BODY`：

```console
$ <检查 dispatch.py 的模板>   ## Touches 在其中: True      ← 今天（9ef5ba4 之后）
```

**所以它们是 `9ef5ba4` 之前的那一版派发器写下的。** 时间线也对得上：
任务写在 02:34:03Z，`9ef5ba4` 提交于 02:55:13Z（= 10:55 本地）。

### 这个失败不是「配置写错了」

`plugin/scripts/drivers.yml` 的 `worker.cap` **确实是 2**：

```yaml
worker: { cap: 2, interval_ms: 10000, reconcile_interval_secs: 30 }
```

`cap` 这一格没坏。坏的是**它的另一半**：cap 只决定「最多几个」，
**下面还有一层谓词决定「这一轮能不能派」**。只改 cap 而不让任务带 `## Touches`，
得到的正是本节的读数 —— 一个**看起来饱和、实际串行**的池子。

**修已经提交、已经在跑，但还没有被任何一次真实派发检验过。**
`9ef5ba4`（`fix(dispatch)`: 正文补上派发器要读的那一节）之后的 MCP 加载的正是这一版
（见 §L），但它此后**没有写过任何任务** —— 所以 fix 的状态是
**「已装载、未验证」**，不是「已修复」。

### 补记：那条 fix 的**输出**已经过了派发器自己的谓词（零供应商成本）

「已装载、未验证」缺的那一半可以**不花钱**补上：拿派发器**自己那几段函数**跑一遍，
看新正文与旧正文各自得到什么判词。这不是我对它的复述 ——
`extractTouchesSection` / `parseTouchEntries` / `parseTouches` / `normalizePath` /
`isOverbroadDeclaration` / `globToRegExp` / `matchGlob` / `expandDeclaredTouches` /
`walkFiles` / `filesDisjoint` / `checkTouchesPair` 是**按名字从
`scripts/dist/worker-driver.js` 里抠出来**（带传递闭包），在真工作区
`/home/operator/artifacts/marketops-mvp-quay` 上执行的；正文由**本仓的**
`_TASK_BODY` 渲染，不手抄。

工件在 `artifacts/marketops-closeout/closeout-2026-09-23/touches/`
（`render-bodies.py` → `bodies.json` → `verify-touches-gate.mjs` → `verify-touches-gate.txt`）。
**20 条断言，0 条 FAIL**，其中承重的四条：

| 断言 | 判词 |
|---|---|
| 新正文：有 `## Touches` 节 / 解析出一条 glob / 不算过宽 | PASS |
| 新正文：两件**不同**任务互判 `disjoint`（这才叫可并发） | `disjoint file-sets` |
| 反面：同一件与**自己**比 → 不 disjoint（判据不是恒真） | `overlapping file-sets` |
| 反面：**旧正文**与另一件 serialize | `conservative: side A declares no/empty ## Touches → serialize` |

最后一行是关键：那串理由**与本节上面 `worker-round.jsonl` 里的 `stop_reason`
是同一个字符串**（`filtered-empty`）—— 所以那个失败是**因果**，不是巧合；
换成新正文，这一条判词就不再出现。

顺带更正我自己在这份记录里的一个预期（**是判据错，不是代码错**）：
`expandDeclaredTouches` 对**字面量**路径**不查文件系统**，直接 `normalizePath` 收进集合
（只有带 `*`/`?` 的声明才走 `expandGlobs`）。两个后果都钉进了断言：修**不依赖任务文件
已经写下去**（声明先于产物也成立）；而「展开为空」那条分支只能由**拼错的 glob** 触发，
且那条 glob 还得先过 `isOverbroadDeclaration`（`tasks/DOES-NOT-EXIST-*.md` 会被「过宽」
先挡掉，`tasks/<幽灵>/*.md` 才到得了）。

**这条补记证明的是「修的输出过得了谓词」，不是「并发真的跑到了 2」。**
后者需要两个 worker 真的同时在飞，价格见 §O。

---

## D. 供应商用量 —— 含**超支披露**

### 本轮（验收 B）实际

| Account | `search` spent | reserved | ceiling（产品强制） | §6 声明的上限 |
|---|---|---|---|---|
| `kent-005` | 2 | 0 | 20 | ≤2 |
| `kent-006` | 7 | 1 | 20 | ≤2 |
| `kent-007` | 12 | 1 | 20 | ≤2 |
| **合计** | **21** | **2** | — | **≤6** |

**21 次已付费 + 2 次已预留 = 23，对 §6 声明的「每 Account ≤2、总计 ≤6」。
超出总量上限 3.5 倍，单 Account 最多超出 7 倍（`kent-007` 12 次）。**

两件事必须分开说，否则这一段会被读成「预算机制坏了」：

- **产品侧的强制是好的。** `get_job` 的 `usage` 逐 Account 报出
  `ceiling 20 / spent / reserved / remaining`，`budget_ceiling` 的来源是
  `profile:uk-acme-natural-grass:search.max_queries`。工具真的在拦。
- **被越过的是本 prompt 声明的那个数。** §6 的「≤2 / ≤6」**在这套产品里表达不出来** ——
  上限只从 profile 读，没有一条路能把「这一轮用 2」写进去。所以「≤2」是一句
  **没有执行器的声明**。这正是本仓最贵的那类错误（`docs/` 里反复出现的那句：
  一条没有执行器的声明就是散文）。

**历史对照（验收 A，cap=1）**：`kent-004` 用了 **14 spent + 2 reserved = 16**，
同样声明 ≤2 —— **超出 14 次**。两轮同样的偏差、同样的机制，
所以这不是一次手滑，是**同一处缺口被撞了两次**。

### 其余供应商

| 项 | 本轮实际 | 说明 |
|---|---|---|
| **Jev** | **0** | 没有任何一次判定调用。与 §F 的 FAIL 是同一件事的两个面 |
| **Crawl4AI** | **0 条落盘证据** | 没有一条 `evidence` 行的 `tier` 是 `browser`。全部 38 条的 `tier` 都是 `http`、`source_kind` 都是 `fetched_html` |
| **Claude 研究 worker 会话** | **3**（对 §6 的 ≤3，已用满） | `job_attempt` 恰好 3 行，`holder` 全是 `claude-worker-1` |

Crawl4AI 要说得更准一点：`kent-006` 的 `gaps` 里写着 staff-list 的三份 PDF
「returned evidence.status=unparsed with no bytes stored **on both http and browser tiers**」
—— 也就是说 **browser tier 真的被走到过**，只是它既没落盘字节、也没产生证据行。
所以准确的说法是：**浏览器档被尝试过并失败，没有留下任何持久证据**；
不是「从未尝试」。这两句在报告里不能混。

### 全部调用的纪律

每一发都是 **先 reserve 再 call、call 完 settle**，都有一条 durable 台账行
（`budget_reservation`，本轮 23 行）。金额这一栏：本轮所有行都是 `units: 1` 的
**次数配额**，没有任何一行声称了金额 —— 所以本报告**不写 $0**，也不写任何金额。

---

## E. Evidence

**本轮共 38 条**（`evidence.account_id` 在 `kent-005/006/007` 上）：

| Account | 条数 | 取回的主机（去重） |
|---|---|---|
| `kent-005` | 6 | `www.client18.example.org` |
| `kent-006` | 14 | `www.client39.example.org` (10) · 第三方 (4)：`get-information-schools.service.gov.uk` · `www.client20.example.org` · `www.client16.example.org` · `www.client14.example.org` |
| `kent-007` | 18 | `client08.example.org` (8) · `www.client20.example.org` (5) · `client23.example.org` (3) · `www.find-tender.service.gov.uk` · `www.client19.example.org` |

样本 id（按 `evidence_id` 可逐条读回）：

```text
kent-005  af48135882152cde2360624301cbd51b   6ee68e0b6a4ac4430a9468cdeb397b4e
          7c9219f15f4a8f38c4e95f8c075ac6a8   958d319c9bca701edf5f885316157b14
          c79bfda0fc810b8d2e1a253156c6d1e1   efa46f97ad3fc608241e6c1a61e6c1f6
kent-006  2dda96eefe9f20061ea688adb3bd1ea3   8129f19128ec22f613f1464de392919f
kent-007  b91376a95b6aa3e21125cdac8234ca73   f6fdcb3e79272307bce7a4db166e08cf
          ff642d32294a3df946ea0122a372cbe6
```

§7 要求的那条链**是真的**。一条真实证据行（`kent-005` 的第一条）逐字：

```json
{"account_id": "kent-005",
 "content_sha256": "4269e0bf1d32c3e198ac521ca97b5f9832ee96a4d2223ab73aaa0d75f0ab5633",
 "evidence_id": "af48135882152cde2360624301cbd51b",
 "fetched_at": "2026-09-23T02:35:02.148009Z",
 "http_status": "200",
 "literal_value": "Example School Three - Contact Us\n\nSkip to content &darr;\n…",
 "page_locator": "whole-document",
 "parser": "html",
 "source_kind": "fetched_html",
 "source_url": "https://www.client18.example.org/contact-us",
 "tier": "http"}
```

`source_url` + `content_sha256` + `fetched_at` + `literal_value` + `page_locator`
**一样不缺**，而且 locator 是精确的、值是原文的。

**一条观察**（不是判词）：证据的归属字段是 `account_id`，不是来源域名。
`kent-006` 的 14 条里有 4 条取自与该账号无域名关系的第三方站
（其中 `www.client14.example.org` 从域名上看不出与该校有关 —— 本报告**只报域名，
不推断那家站是做什么的**；要判断它为什么被取回，得去读那条 `evidence` 行的 `literal_value`）。
按本仓的模型这是**允许的**（证据证明的是关于该账号的主张，页面的所属者不是限定条件），
但它意味着「一个账号的证据」这句话**不能**被读成「都来自该账号自己的站」。

---

## F. Decisions —— **FAIL**

`decision_record` 表**自建表以来一行也没有过**（`select count(*) === 0`）。

`kent-?` 三方这次的语义判定是**研究员在会话里做的**，结论写进了 `job_settlement.payload`
的 `candidates[].statement` —— 有 `evidence_refs` 指回真实证据 id，形态是对的，
**但它不是一条 durable `decision_record`**：没有 `record_id`、没有 `provider`、
没有 `case_id`、没有 `spec`，也没有模型/版本/概率。

**根因不是「能力没建」。** `211348e`（`feat(decisions)`: 判定记录落盘）已经建好了
那条路 —— 它落在 `marketops/ports/decisions.py` 与对应的 SQLite store 上，有测试。
根因是**没有任何一个 MCP 面把 `DecisionSpec` 的 id 交到研究员手里**：
`get_assignment` 给的是 guidance 路径与摘要、工具与预算，
**没有一个字段说「这次判定该用哪份 spec、该拿哪个 case_id 交回来」**。

于是这一格的状态是 **「可达但无人指路」**（reachable but undirected），
不是「未实现」。这两句在派活时的含义完全不同：前者要改的是一份**回执**，
后者要改的是一套**存储**。

§10 要求的三件事（真实 fetch 之上的 Jev SUCCESS、durable 行、
`decide receipt → DB 行 → 服务重启 / 新客户端 → 同一个 decision_id 读回`）
**一件也没有可验的对象**，因为第一件就没发生。

---

## G. Outcomes

| Account | `stop_reason` | 回传看板的终态（`board_sync.target_status`） | settlement `recorded_at` |
|---|---|---|---|
| `kent-005` | `goal_met` | `done` | 02:36:37.438181Z |
| `kent-006` | `goal_met` | `done` | 02:40:27.575878Z |
| `kent-007` | `goal_met` | `done` | 02:43:40.491143Z |

第三列**不是**「外部那套的终态」。同一个 `get_job` 回执里 `research_terminals.items[].external`
写的是 `quay_final_state: "exited-not-landed"` —— 三个都是。**两个事实、两个名字**，
所以这一列的标题就写成它的来源；把它们合成一个「Quay 终态」会让下面那条错配看起来不存在。

三件**全部终态**，没有一件停在 `needs_human`
（`select count(*) from work_item where stop_reason='needs_human'` = 0）。

**三件的产出形状不同，而且这是好的**：

```text
kent-005  candidates 6   evidence_refs 11   gaps 4
kent-006  candidates 0   evidence_refs  0   gaps 5   ← 诚实的空结果
kent-007  candidates 12  evidence_refs 13   gaps 7
```

`kent-006` 交回的是 `candidates: []` 加五条 `gaps`。其中一条逐字：

> One search query (`"client01.example.org" staff email head of school`) failed at the provider
> (Serper HTTP 400); it was not retried and **that failure is not evidence of absence**.

这是一句失败关闭的话，而且是**自己说出来的**。另有一条重复了同一纪律：
staff-list 三份 PDF 在 http 与 browser 两档都是 `unparsed`、零字节，所以
「no named Site Manager / Facilities Manager / Business Manager could be evidenced」
—— **查不到就说查不到，没有补猜**。

**一条必须记下的错配**：三个 worker 各自的 `.quay/worker-outcome.jsonl` 行都写着
`final_state: "exited-not-landed"`、`failure_reason: "worktree state unreadable (git worktree list failed)"`。
这与「三件都 `goal_met`」不矛盾，但**看起来矛盾** ——
`exited-not-landed` 问的是「有没有代码落地」，而这三件根本不是软件任务（任务正文第一句就写着
`this is not a software task`）。本仓在 §1 已经处置了这个形状：
`get_job` 的 `research_terminals.note` 明说外部那条判据「问的是软件工程任务的问题」，
**不一致时以 `authoritative`（本仓的 `marketops.settlement`）为准**。
所以这条错配**已被预期并被读法覆盖**，但它会继续出现在每一轮研究件的 `.quay/` 日志里 ——
看 `worker-outcome.jsonl` 的人需要先读到这一句。

### `board_sync`（回传看板）

三条链都完整，且 `changed: false`：

```json
{"task_id": "MOPS-37342806-70f3353e", "target_status": "done",
 "observed_status": "done", "synced": true, "changed": false, "error": null}
```

`synced: true` 是「回传成功」，`changed: false` 是「写下去时目标已经是那个值」——
**两个都不是同一个事实**，所以分开报，也正是这一格不写成一个布尔的原因。

---

## H. 并发 —— **FAIL**

| 判据 | 要求 | 实测 |
|---|---|---|
| 峰值 `in_flight` | 2 | **1** |
| `cap` | 2 | 2 ✔ |
| 截到两次 `In flight 2 / 2` | 必须 | **一次也没有** |

机制见 §C。**§8 的并发隔离因此不可取证** —— 隔离的每一条（A 读不到 B 的 assignment、
B 引不到 A 的证据、租约不串、台账不串、同一 work item 同一时刻只有一个持有者）
都需要**两个 worker 真的同时在飞**才有可验对象。本轮 `job_attempt` 的三行 `holder`
**全是 `claude-worker-1`**：启动器一次也没能占到第二个身份，
因为它一次也没有拿到第二个派发。

`_require_holder` 与身份锁（`bin/marketops-research-worker` 的 flock 槽位）在本轮**在线**，
且这与「本轮验不了」是两件事：机制在场，**场景没发生**。

**一条正面的推论**：正因为 `in_flight` 恒为 1，
本轮**没有出现**任何「同一 work item 被判给两个人」的痕迹 ——
3 行 attempt、3 个不同的 `work_item_id`、每个 `ordinal: 0`，没有任何一件有第二个 attempt。
**但这不能替代 §8 的证书**，它只是「没有发生」而不是「被证明了不会发生」。

---

## I. 重复工作 = 0

判据是**同一查询 + 同一 idempotency key 不被付两次**。实测：

```text
budget_reservation 全表 50 行，distinct idempotency_key = 50，重复键 0
本轮          23 行，distinct = 23，重复键 0
```

三件的 scope 彼此独立（`uk-acme-natural-grass:kent-00{5,6,7}`），
没有任何一个查询字符串跨 Account 复用。**0 重复付费。**

---

## J. 人工 drain = false

**没有为「防止重派」做过任何 drain。** 证据：

- 驱动器**自己**走到 `pool-empty`（round 26 → 27，02:43:47Z → 02:43:51Z，`in_flight` 1 → 0），
  此后一路 `round 28…129` 全是 `pool-empty`，**没有一件被重新放回 ready**。
- 期间 `retryCapNotExhausted` 的 `needsHuman` 集合空（无 `needs_human` 任务），
  没有任何一件进入退避 / 人工队列。
- `quay driver stop --kind worker` 是在**全部终态之后**才执行的（§15 的收尾），
  它是生命周期收尾，不是紧急制动。它写下的是
  `{"round":129,"action":"idle","in_flight":0,"pool":null,"stop_reason":null}`。

所以 §9 的判据成立：**终态是链子自己走到的**，
`submit_findings → settlement → board_sync → 空槽自动补位 → pool-empty`。

---

## K. 重启持久性 —— **PASS**

MCP 在验收 B **之后**重启过（下面 §L 的指纹即证据），重启后**新起一个客户端进程**
（不是任何 Claude 会话的内存）读回：

```json
{"research_terminals": {"reported": true, "authority": "marketops.settlement",
  "items": [{"work_item_id": "70f3353e…", "authoritative": "done", "complete": true,
    "chain": {"work_item":       {"subject_ref": "kent-005", "stop_reason": "goal_met"},
              "attempt":         {"holder": "claude-worker-1", "state": "settled",
                                  "settled_at": "2026-09-23T02:36:37.438181+00:00"},
              "settlement":      {"accepted": true, "stop_reason": "settled",
                                  "content_sha256": "fc76f7cb8b787e9396cd100151e767b142f96cdbc954d63be9ea3cb2f7dd1bf9"},
              "board_sync":      {"task_id": "MOPS-37342806-70f3353e", "synced": true, "changed": false}}}]}}
```

`progress` 3/3、`usage` 逐 Account 的 ceiling/spent/reserved 也一并读回。
**Job / WorkItems / Evidence / DecisionRecords（0 行，也是可读的事实）/ Settlements /
execution refs / 报告 —— 全部在重启后仍在，且是新客户端读到的。**

---

## L. 最终版本 —— **三个面收敛；第四个面是一条历史记录**

**下面这张表是「今天」的读数。** 验收 B 当时的那张表与之只差一处：
三个面当时是 `9ef5ba449997b7911194dc7acf9f02e47654d5cc`（`cf4c7a6` 那时还不存在），
worker 那一面同样是 `58dcc45`。**所以下面这一节里的因果，当时和现在都成立。**

| 面 | revision | 一致？ |
|---|---|---|
| worktree HEAD | `cf4c7a6af33253f1542672dc993d662057654711` | — |
| `origin/main` | `cf4c7a6af33253f1542672dc993d662057654711` | ✔ |
| MarketOps MCP（加载时指纹） | `cf4c7a6af33253f1542672dc993d662057654711`（`dirty: false`，`read_at` `2026-09-23T03:12:42Z`） | ✔ |
| Research worker（每次派发的 `run.json`） | **`58dcc45008f1c04cd868a2d8a647c537a96502a6`** | **✘ 差一个提交** |

三个面已收敛到 `cf4c7a6`：`9ef5ba4` 之后**只多了一个 docs 提交**（本文件本身），
所以**验收 B 观察到的行为仍然完全属于 `9ef5ba4`**。MCP 按已有生命周期重启过一次
（`TERM` 而非 `-9`，退出码干净、端口释放后再起），
指纹是**回执里读出来的**，不是「进程启动时间晚于提交」那种替代品：

```text
pre  loaded: 9ef5ba4…  read_at 2026-09-23T02:57:01.837155+00:00
post loaded: cf4c7a6…  read_at 2026-09-23T03:12:42.652650+00:00
source_tree 相同: True | /home/operator/workspace/worktrees/marketops/mvp
重启前后读回相等: True   ← 逐件终态 / board_sync / usage / progress 全部逐字相同
```

**第四面不是不一致的证据，是一条历史的记录。** worker 的指纹是**每次派发那一刻**读的，
三次派发分别在 02:34:38Z / 02:37:54Z / 02:40:57Z，而 `9ef5ba4` 提交于 02:55:13Z ——
三次都发生在那个提交之前，所以它们加载的是当时的 HEAD。三个 `run.json` 的
`loaded_marketops` 一节逐字（三份只在这一格上相同，其余字段各不相同）：

```text
/home/operator/workspace/worktrees/marketops/mvp @ 58dcc45008f1c04cd868a2d8a647c537a96502a6（未提交改动：无）
```

`58dcc45` 是 `9ef5ba4` 的**直接父提交**（`git merge-base 58dcc45 HEAD` = `58dcc45`，
`58dcc45..HEAD` 恰好一个提交，就是 `9ef5ba4`）。所以两者的差**只有那一处**：
`## Touches` 派发修 —— 而那一处正是 §C 里本轮 FAIL 的**根因所在的文件**。

**这条不一致是时间造成的，不是部署错的**，但它有一个必须写下来的后果：

> **验收 B 的 3 件研究，是在「没有 `## Touches` 修」的代码上跑的；
> 而那个修恰好是让 cap=2 有可能饱和的那一个。
> 于是验收 B 在两个意义上都是「修之前的读数」。**

这不是失败，是**因果**。但它意味着：`9ef5ba4` 之后**还没有任何真实派发被观察过**，
所以它的状态是**已装载、未验证**（见 §C 末）。

---

## M. 本轮产生的代码与提交

三个提交，都是小步、都带测试、都在 `origin/main` 上：

| 提交 | 是什么 |
|---|---|
| `51c4273` | `fix(terminal)`: 研究件的终态以本仓为准，外部那条落地判据标 N/A（§1） |
| `211348e` | `feat(decisions)`: 判定记录落盘 —— 一条付过费的判定不许只活在那次调用的返回值里（§2） |
| `9ef5ba4` | `fix(dispatch)`: 正文补上派发器要读的那一节（`## Touches`），并把命名规则搬到两层都读得到的地方（§4） |

验证（提交前跑、全绿；命令逐字）：

```console
$ MARKETOPS_RECORDS_ROOT=/tmp/nonexistent-records \
  /home/operator/toolchains/marketops-mvp/bin/python -m pytest -o addopts="" -q
$ /home/operator/.local/bin/ruff check .          # 从不跑 ruff format
$ /home/operator/toolchains/marketops-mvp/bin/python -m marketops.schema   # 与 committed 逐字一致
```

`origin/main` 是**纯 fast-forward**，无 force、无覆盖外部并发更新。秘密扫描：本报告与三个提交
均不含凭据（凭据只在 `/home/operator/.secrets/vault/`）。

---

## N. 未收口的观察（都是**观察**，不是判词）

**N.1 `research_job.state` 恒为 `queued`。**
三个 Job 全是 `queued`，包括已完全结算的那个。`state` 因此**不承重**——
真实进度在 `get_job.progress`（它从租约算）。这一条要么补上流转，要么在某处说明它不承重；
**今天它是一格看起来像进度、其实不动的字段**，而那种字段比没有字段更坏。

**N.2 `work_item_guidance` 缺了本轮的 3 件。**
该表只有 5 行，全是历史那 5 件（`pinned_at` 2026-09-22T07:11:43Z）。
验收 A 的 `kent-004` 与验收 B 的三件**都没有 guidance 行**，
而它们各自的 `snapshot` 行是齐的。这不只是台账少一行 ——
`kent-007` 的 `gaps` 里**自己报了**这件事，逐字：

> Guidance content was not readable through the exposed MCP surface: `get_assignment` returned
> the pinned guidance as **file paths plus sha256 digests** … with `content_from=snapshot`

也就是说：**研究员拿不到 guidance 的正文，只拿到路径与摘要**，
而这一格（§7 要求的「ScenePack/guidance snapshot」）在准入时没有被钉住。
本轮没有因此失败（三件仍 `goal_met`），但这是一处**声明与实现的错位**。

**N.3 `job_settlement.payload` 不携带供给侧的判决。**
`get_job` 的 `research_terminals` 报 `settlement.stop_reason = "settled"` 与
一个 `content_sha256`，而 `job_settlement` 表里除了 `work_item_id / attempt_id / payload /
recorded_at` 没有别的列 —— 判决是靠 `payload` 的 `content_sha256` 定身的。
这不是错，但它意味着**「为什么这件算完成」必须去读 payload 才知道**，
而 payload 的形状没有契约。下游若有第二个消费者，这里是第一个会漂的地方。

**N.4 历史残留。**
`1b1b7f82` 那一轮留下一条 `state: 'leased'` 的 attempt
（`869bf1a78152d69bbee874f4f73af94a`，`claude-worker-2`，自 2026-09-22T07:16:41Z 起），
以及 5 行 `work_item_guidance` / 5 行 snapshot。**它们不属于验收 B，也没有混进验收 B**
（验收 B 的 3 件全部 `goal_met`、全部独立结算）。
这一条写在这里是为了 §12：**将来读这份报告的人要能一眼分清哪一行是历史。**

---

## O. 如果要拿到 §4 的证书，重跑的价格

**本轮没有拿到 §4 / §8 的证书，而在不额外算清代价之前不应该自己再跑一遍。**
把它写下来，是因为它是下一个决定要用的数：

- **必须**：新 job（新 `input_ref` + 新 `idempotency_key`）、**新 3 个 Account**、
  新的 Quay task id。而复用的 Account 会污染「本轮 Provider 用量」这一格。
- **Serper**：3 个 Account × 与本轮同量级 = **21–23 次**。本轮已经对 §6 的 ≤6 超支，
  所以下一轮**要么先把「每轮上限」变成产品里能表达的东西，要么把预算声明改成一个
  产品真的能执行的值**（今天 `budget_ceiling` 只从
  `profile:…:search.max_queries` 取，值是 20）。
- **Claude 研究 worker 会话**：3 次（§6 的 ≤3，本轮已用满）。
- **Jev**：本轮 0 次，所以 `decision_record` 若要一并取证，需要**先补上那条指路**（§F），
  否则再跑一轮仍然是 0 行。
- **墙钟**：本轮 3 件串行用了 **9 分 45 秒**（02:34:02Z → 02:43:47Z）。
  并发真的到 2 的话，同量级的 3 件大约是一半；**这不构成省钱的论据**。

**结论：一轮能同时证明 §4 与 §8 的重跑，价格约等于本轮（21–23 次 Serper + 3 个会话），
而且它只有在 `9ef5ba4` 已被验证（task 真的带上 `## Touches`）之后才有意义。**
在那之前重跑，最可能的结果是**再得到一次 `in_flight = 1`**。

---

## P. 补记二：§5 那个三态在看板上的实况，与四个面的最终读数

§C 的补记证明的是「那条 fix 的输出过得了谓词」。这一节记两件**主体里没有落点**的事。
两件都不改变主体任何一格读数，所以它们只补记，不改写。

### P.1 三态：驱动器给得出来，看板那一格给不出来

**驱动器自己给得出来。** `worker-round.jsonl` 里验收 B 之后共 123 轮：
122 轮 `action: "stop"`、**1 轮 `action: "idle"`**，而那一轮恰好是最后一轮：

```text
round 128  03:05:34.653Z  action stop  in_flight 0  pool 0     stop_reason "pool-empty (no dispatchable candidate in the ready pool)"
round 129  03:05:44.664Z  action idle  in_flight 0  pool null  stop_reason null   ← 最后一轮
```

四个数因此**都取得到**：`idle` 来自第 129 轮、`0` 来自 `in_flight`、
`pool-empty` 来自第 128 轮的 `pool: 0` 与那条 `stop_reason`、`cap 2` 来自 `drivers.yml`。
第 129 轮那两个 `null` 是**刻意的缺值**，不是 0：不再评估池子了，所以「池子为空」这时
不是某个取值而是「没测」—— 缺值不该被写成取值。

**但看板那张卡给不出来。** 同一瞬间、同一个响应里，两张卡各说各的：

```text
liveCard  Loop pulse   running       In flight 0 / cap 2
mgrCard   worker       Not running   Last record 15m ago
```

`driver status --kind worker` 同时报 `alive=0 running=0 pid=none`，最后一轮在 15 分钟前。

机制在 Quay 0.11.0 的**厂商 dist** 里（`vendor/quay/dist/quay.js`，
sha256 `ba03efa008e655047e41aa5ba8cc2f982acdf0744de97bfda3a9a021399d0180`）。三行就够：

```js
// 36871   function workerDriverActive(root) {
// 36872     return fs17.existsSync(path19.join(root, WORKER_OUTCOME_REL))
//                || fs17.existsSync(path19.join(root, WORKER_ROUND_REL));
//           }                    // ← 名字问的是「在不在跑」，读的是「账本文件在不在」

// 37338   if (workerDriverActive(root)) { telemetryEmpty = false; status = "ok"; }
// 37375   if (telemetryEmpty) { …decideLiveState(activity)… }   // 三态只在这一边
// 37380   else if (status === "ok") { liveState = "running"; }   // ← 无条件
// 39423   const liveStateText = … live.liveState === "running" ? L.liveStateRunning : …
```

账本只追加、从不删除。所以一个项目**只要跑过一次 worker**，
`workerDriverActive` 就恒为真、`liveState` 就恒为 `running`、
`decideLiveState` 那条三态分支（`running-unwired` / `not-running`）就**永远到不了**。

这是本仓反复记为最贵的一类错，值得逐字写下来：**它不是「通过了」，是「够不着」。**
Driver 卡从存活性读，Loop pulse 卡从文件存在性读 —— 同一个事实两个名字，
于是同一屏上 `running` 与 `Not running` 并存，两句里有一句在说谎。

**结论分两半，不要合并**：

* §5 的「没有 work 时不许整块空白」**满足**：卡上有内容，`In flight 0 / cap 2` 是对的，
  三个终态的甘特条也在；
* §5 的「最终态是 `idle / 0 / 2 / pool-empty`」在**卡的措辞**上**不满足**：那个词到不了
  `idle`。正确四元组要按上面那样从 `worker-round.jsonl` 与 `drivers.yml` 读。

**这不在本仓的可修范围内**：`vendor/` 是 Quay 的发布产物，本仓不动 Quay 上游。
记在这里，是为了让下一个读看板的人先知道那一格为什么是绿的。

### P.2 四个面的最终读数

```text
worktree HEAD : aa29a295651b19b8c42cfedb5d82925aaea931a6
origin/main   : aa29a295651b19b8c42cfedb5d82925aaea931a6
MCP 装载      : aa29a295651b19b8c42cfedb5d82925aaea931a6  (dirty false, read_at 2026-09-23T03:20:27.728428+00:00)
worker(派发时) : 58dcc45008f1c04cd868a2d8a647c537a96502a6
```

三个面收敛；第四个面是**历史记录**，理由见 §L（worker 的指纹是**派发那一刻**读的，
不是今天读的，所以它按构造不跟着走）。§L 记的是它自己那一刻的读数，两处不冲突。

这一节写在 `aa29a29` 之上，随后还会有一次 doc-only 提交把 worktree 与 origin 推前一格，
而 MCP 装载的仍是 `aa29a29` —— **那一格差是文档提交造成的，不是代码漂移**：
本文档不参与任何运行时判据。§L 已经把这个读法立过一次（「两个 revision 要分开读」）。

**重启持久性又证了一次，这次是第三个进程世代**：同一个 `get_job` 回执在这一节那次重启
前后**逐字节相等**，也与 §K 那次 `final` 捕获逐字节相等 —— `job.state`、三件 `stop_reason`、
`results.totals`（`observations 32 / candidates 18 / gaps 16`）、三件 `board_sync`、
`usage`（2/0、7/1、12/1）全部一格未动。

---

## 附：本报告里每个数字的取法

| 节 | 来源 |
|---|---|
| A / B / G | `state/marketops.db` 的 `research_job` / `work_item` / `execution_ref` / `work_item_snapshot`；`get_job` 的 `progress` |
| C | `<quay-root>/.quay/worker-round.jsonl` 验收 B 段；`~/.claude/plugins/cache/quay/quay/0.11.0/scripts/dist/worker-driver.js` 行 32709 / 32735 / 37861 / 55596 / 55600；`grep -c '^## ' tasks/*.md`；`marketops/application/dispatch.py` 的 `_TASK_BODY` |
| D | `budget_reservation` / `budget_ceiling`；`evidence` 的 `tier` / `source_kind`；`job_attempt` 的 `holder` |
| E | `evidence` 逐行 `payload` |
| F | `select count(*) from decision_record` |
| H | `worker-round.jsonl` 的 `in_flight` 列；`job_attempt.holder` |
| I | `budget_reservation` 的 `idempotency_key` 去重计数 |
| K | 重启后**新进程** MCP 客户端的 `get_job` 读回 |
| L | `git rev-parse HEAD` / `origin/main`；`get_capabilities.build.revision`；`workers/**/run.json` 的 `loaded_marketops` |
| P.1 | `worker-round.jsonl` 第 128 / 129 轮的 `action` / `in_flight` / `pool` / `stop_reason`；`curl 127.0.0.1:11001/dashboard/cards` 的 `liveCard` 与 `mgrCard`（同一响应）；`quay driver status --kind worker`；`vendor/quay/dist/quay.js` 行 36871-36872 / 37338 / 37375 / 37380 / 39423，该文件 sha256 `ba03efa0…` |
| P.2 | 同 L，另加 `get_capabilities.build.read_at`（MCP 侧的时刻）；重启前后的 `get_job` 回执逐字节比对 |
