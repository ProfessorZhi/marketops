# MarketOps 实施验收：E1–E5 在当前构建上的复验（2026-09-23）

这一份是**构建专属**的收据，不是对结论的重述。它回答一个问题，而且只回答这一个：

> 这个 MarketOps MCP 今天能不能从母池 / 发现开始，一路跑到 evidence-backed 的
> Account / contact 研究；跑到哪里为止，哪里还不行，证据是什么。

历史那一份 `docs/MVP_ACCEPTANCE_REPORT.md` 是一份**冻结记录**，它上面每一条 FAIL 都
是当时那个构建的真实读数。**本文件不修改它，也不把它的 FAIL 改成 PASS。** 本文件
只做一件事：在**新的 build** 上重新测一遍那些判据，把新的读数写在这里，并逐条注明
哪些达成了、哪些没有、没达成的是哪一格。

凡是标 PASS 的行，后面都跟着一个可以自己重跑的读数；凡是没跑的，写 `NOT_RUN` 而不
写「应该没问题」；凡是缺凭据、缺服务、缺授权的，写 `BLOCKED` 并写出解封条件。

---

## A. Build

| 项 | 值 |
|---|---|
| 仓库 | `ProfessorZhi/marketops` |
| 检出 | `/home/operator/workspace/worktrees/marketops/impl-20260923`（worktree，非 canonical checkout） |
| 分支 | `main` |
| **git SHA（被测构建）** | `168d69be24ffbad611d8245a637e8edaaad3a973` |
| **git SHA（本文件所在提交）** | `9aaba89646dbce865d34efa7aa7a81193202c1c7` —— 它与被测构建之间**只有 `docs/` 两个提交**（`git diff --name-only 168d69b..9aaba89 \| grep -v '^docs/'` 为空）。所以下文所有读数取自 `168d69b`，而不是取自写这份文件时的 HEAD：**收据与被测构建是两个提交，这件事本身就说出来** |
| **工作树是否脏** | **否**（`git status --porcelain` 输出为空；`--check` 报 `dirty: false`） |
| 相对 `origin/main` | `origin/main = 9347b56`；被测构建 **ahead 11 / behind 0**；本文件提交后 ahead 14；**全程未推送**。⚠️ 本行原先写的是「ahead 16 / 18」，那是**错的**：当时是从上一轮的交接摘要里抄的，没有重新数。2026-09-23 复核用 `git rev-list --count origin/main..<sha>`，`168d69b` 是 **11**、`55304b6` 是 **14**。改在这里而不是悄悄改掉，因为「一个数字是从摘要抄的还是自己量的」正是这份收据要能回答的问题 |
| Python | 3.12.3 |
| 解释器 | `/home/operator/toolchains/marketops-mvp/bin/python` |
| **MCP SDK** | `mcp==2.2.0`（运行时依赖钉在 `>=2.2,<3`） |
| httpx | 0.28.1（HTTP 抓取） |
| pydantic | 2.13.5 |
| PyYAML | 6.0.3 |
| pypdf | 6.19.0 |
| jsonschema | 4.26.0（契约导出/校验） |
| crawl4ai | 0.9.3（有界浏览器） |
| playwright | 1.63.0（crawl4ai 的浏览器后端） |
| laya | 0.3.4（本地判定后端） |
| typesafe_sdk | 0.7.1（Jev 判定后端） |
| ruff | 0.15.22（CI 只跑 `ruff check`，从不跑 `ruff format`） |
| pytest | 9.1.1 |
| **协议版本（协商结果）** | `2025-11-25`（实测 `initialize` 响应的 `protocolVersion`；SDK 自己的 `LATEST_PROTOCOL_VERSION` 是 `2026-07-28`，服务端不升到它） |
| server info | `{"name": "marketops", "version": "0.1.0"}` |
| **传输** | Streamable HTTP，绑定 `http://127.0.0.1:8787/mcp`（字面量环回；`loopback_transport_security`） |
| **认证模式** | 受控、带外预置的 bearer 令牌（`TokenRing` **格式 3**）。**不发布任何授权服务器元数据**：`AuthSettings.resource_server_url` 显式 `None`。每条授权带五格并被判：principal / roles / audience / workspaces / expires_at |
| 判据分两层 | audience + expires_at 在传输层（`adapters/mcp_server/auth.py`）；roles + workspaces 在 `RoleGuard` 中间件（`adapters/mcp_server/guard.py`） |
| 可选依赖缺失 | `mineru` 未安装（`ModuleNotFoundError: No module named 'mineru'`）—— 可选，缺它时复杂 PDF 走降级路径 |

`--check` 在本次构建上的裁决（`python -m marketops.interfaces.mcp --check`）：

```json
{"ok": true,
 "build": {"source_tree": ".../impl-20260923", "revision": "168d69be...", "dirty": false},
 "mcp": {"host": "127.0.0.1", "port": 8787, "auth_configured": true,
         "resource": "http://127.0.0.1:8787/mcp",
         "workspace": "uk-acme-natural-grass",
         "audiences": ["http://127.0.0.1:8787/mcp"],
         "workspaces": ["uk-acme-natural-grass"],
         "principals": ["claude-control", "claude-worker-1", "claude-worker-2"],
         "roles": ["controller", "researcher"]},
 "blocker": "",
 "settings": {"research_only": true, "send_authorized": false, ...}}
```

`exit = 0`。**`research_only = true` 与 `send_authorized = false` 是从进程的 settings 里
读出来的，不是文档里写的。**

---

## B. 架构完成度

状态只有四种：**PASS / FAIL / BLOCKED / NOT_RUN**。「机制做完了但活体读数没取到」按
`NOT_RUN` 记，不按 `PASS` 记 —— 那是本次收口反复用到的同一条纪律。

### B.1 E1 · 母池读完之后每一行去了哪里（SourceReceipt）

**PASS** —— 而且是在**一份真的由发现跑出来的池子**上测的，不是 fixture。

本轮实跑：

```
python -m marketops.interfaces.cli read-motherpool \
  --artifact devon-probe-20260923 --source-method search.engine.v1
```

读数（`artifacts/marketops-closeout/e6-live-head/motherpool-read.out`）：

| 项 | 值 |
|---|---|
| `receipt_id` | `receipt-57bf400076f21b22` |
| `version` | `v1` |
| `source_method_id` | `search.engine.v1` |
| `artifact_ref` | `devon-probe-20260923` |
| `artifact_sha256` | `0417165166c18fd3c544175412562dfa101e5ed546c6723a585c293d9c488f08` |
| `raw_row_count` | 25 |
| 处置汇总 | `raw=25 resolved=25 merged=0 excluded=0 pending=0 failed=0` |

每一行都带 `row_id` / `ordinal` / `disposition` / `source_identity` / `reason` /
`merged_into` / `exclusion_ids` / `resolved_name` / `resolved_location` /
`official_domain`。**六个数加起来等于 `raw_row_count`，所以「有一行没去向」这件事在
算术上就被排除了。**

失败关闭也测到了：同一命令喂 `--artifact kent-2026-09-21`（那是一份 `known_accounts`
的**受理输入**，形状不对）时，退出码 **2**，理由说清是形状问题而不是内容问题：

> kent-2026-09-21.yaml 是一个对象，而它既不是一份列表，也没有 rows 那一栏。……
> 「一个对象就是一行」也说得通 —— 但那时一份**单行**的导出与一份**读错了**的导出长得
> 一样，而这两种的处置相反。

### B.2 E2 · discovery_scope 有生产者

**PASS** —— 本轮做了一次**真实的**发现运行，不是 dry-run。

dry-run 先校准计划规模（零网络、零写盘）：

```
python -m marketops.interfaces.cli run-discovery --scope devon-probe --region Devon --dry-run
```

```json
{"templates_declared": 4, "queries_planned": 542, "frozen_region": "Devon",
 "from_template": {"{role} {venue_type} {region}": 512,
                   "{venue_type} {region} contact": 10,
                   "{venue_type} {region} staff": 10,
                   "{venue_type} {region} grounds team": 10},
 "truncated": ["{role} {venue_type} {region}"], "skipped": [],
 "source_method": "search.engine.v1", "source_family": "public_search"}
```

dry-run 的旁白本身就是判据的一部分：它说清「计划里的 542 条发不完，额度是 20，
后 522 条发不出去；停下来时是**额度用尽**（budget_exhausted），不是**候选枯竭**」。

真跑：

```
python -m marketops.interfaces.cli run-discovery --scope devon-probe-20260923 --region Devon
```

读数（`inputs/devon-probe-20260923.jsonl`、`inputs/receipt-57bf400076f21b22.receipt.json`、
`benchmarks/devon-probe-20260923-20260923T102648Z.json`）：

| 项 | 值 |
|---|---|
| 检索命中 | 200 条 → **82 台主机**（同主机重复 118 条、折不出主机 0 条） |
| 读出名字的主机 | **25**（进池子上限 `max_candidates = 25`） |
| 抓取结果分布 | `{ok: 25, blocked: 8, timeout: 3, error: 3, not_found: 1, not_fetched: 42}` |
| 落成证据 | **50 行** `Evidence` |
| **额度** | `search`：ceiling **20**、**spent 20**、reserved 0、refunded 0 |
| **停止条件** | `["budget_exhausted", "cap_reached"]`（两条都记下来了） |
| 候选处置 | `discovered 25`；`by_reason: {unresolved_facts: 25}` |

三件必须分开的事，回执里分开了：

* **额度用尽 vs 候选枯竭**：`stops` 里两条都在，旁白另说「还有 521 条检索式**没有发
  出去**……「没搜」与「搜了没结果」不是一件事」。
* **「达到上限」不是「候选枯竭」**：旁白逐字写「有 42 台站点没有去取……这是一次
  「达到上限」的停止，不是「候选枯竭」」。
* **「未确认」不是「空」**：旁白点名 `organisation_kind` / `venue_environment` /
  `surface_class` 三格**是空的而不是未确认**，「空值在规则方言里读作「记了，是空」——
  它会在报告里看起来像一条结论」。

额度那一格是 E2 的关键判据：**20 来自冻结的 profile（`search.max_queries`），不是
调用方的参数**，而 `search_web` 的额度断言在同一件事的另一侧
（`tests/test_mcp_research_tools.py::test_the_query_ceiling_is_the_profile_not_the_caller`）。

### B.3 E3 · MCP authentication interoperability

**PASS**。方式是**不声称**做不到的那一部分，而不是把它做成假的。

* 传输模式：受控的带外预置令牌。五格（principal / roles / audience / workspaces /
  expires_at）全部落地到环上并被判。
* **没有虚构的授权服务器**：`resource_server_url = None` ⇒ SDK 不注册任何 RFC 9728
  保护资源路由，401 的 `WWW-Authenticate` 里没有 `resource_metadata=` 指针。两个方向
  都有判据：路由表 + 401 响应头（`tests/test_mcp_auth_interop.py`）。
* 本次实测：**无令牌 → 拒绝**。用 SDK 客户端对真的在监听的进程发 `initialize`，回
  `MCPError: Server returned an error response`；服务端日志是 `POST /mcp HTTP/1.1" 401 Unauthorized`。
* **受众那一格是真的在判**：令牌按 `audience=http://127.0.0.1:8787/mcp` 铸出后，把它
  送到一个绑定在 **8789** 的同一份 HEAD 进程上，四条调用**全部 401** —— 服务端按绑定
  地址推出 `resource = http://127.0.0.1:8789/mcp`，与环里的受众不符。这条不是设计
  说明，是实测（过程记录见 `artifacts/marketops-closeout/e6-live-head/server.log`）。
* 诊断面：`--check` 同时报 `mcp.resource`（本进程认什么）与 `mcp.audiences`（环里写着
  什么）—— 因为「受众错配」与「令牌写错」在协议上是**同一个 401**。

未做也没有声称：完整的 OAuth 发现 / 授权 / 受众往返。那需要一个真实的、被批准的授权
服务器，本构建没有，也没有伪造一个。`NOT_RUN` 且不打算在本阶段做。

### B.4 E4 · 历史 FAIL 在当前构建上复验

`docs/MVP_ACCEPTANCE_REPORT.md` 里的两条 FAIL 是 §C（Loop Pulse 饱和）与 §H（并发）。
本轮逐条复验如下。**总评 FAIL** —— 因为四条子判据里有一条的**字面判据**没有达成，下面
单独说明是哪一条。

| # | 子判据（历史 FAIL 的原文判据） | 状态 | 证据 |
|---|---|---|---|
| 4a | 3 个独立工作单元、worker 上限 2，**真的观察到 `in_flight >= 2`** | **FAIL** | 见下 |
| 4b | 任务隔离：A 读不到 B 的指派、交不了 B 的结果；过期租约/旧 attempt 覆盖不了新 attempt | **PASS** | 见下 |
| 4c | 判定持久化：DecisionRecord 真的落库、重放不重复计费、`get_decision(record_id)` 逐字读回、Jev 与 Laya 分开记、unavailable/abstained 是正常显式状态 | **PASS** | 见下 |
| 4d | 作业状态：不再出现「所有工作单元都终态但作业永远 queued」 | **PASS** | 见下 |

#### 4a —— **FAIL**，根因已修，但字面判据没取到

历史 §C / §H 的根因是：**派发器认为两件工作不能同时跑**。Quay 0.11.0 的
`concurrent-batch-scheduler` 对「声明了 `## Touches` 与没声明」这件事是保守的 ——
`touchesDisjoint` → `checkTouchesPair`，只要有一侧没有 `## Touches` 就 `serialize`。
历史那三条任务是 `9ef5ba4` 之前写下的，正文里没有那一节，于是 cap=2 的验收一件都并行
不起来（峰值 `in_flight = 1`）。

这个根因**已经修掉**，而且本轮在**真实任务正文**上重新量过：

* 当前 HEAD 派发出去的 25 条真实任务（`job_id 30c2bc4174db7406225258b7156fa48b`，
  见 B.5）喂给同一份 `plugin/scripts/concurrent-batch-scheduler.ts`：

  ```
  BATCH (25-wide, concurrent): MOPS-30c2bc41-0251ffd9, MOPS-30c2bc41-059b0663, …（25 条全进）
  ```

* 阴性对照（同一执行器、`9ef5ba4` 之前那种没有 `## Touches` 的正文）：

  ```
  BATCH (0-wide, concurrent): (none)
     deferred: … ill-declared touches: conservative: side A declares no/empty ## Touches → serialize
  ```

  对照记录在 `artifacts/marketops-closeout/e4-concurrency/negative-control.txt`。

**但这仍然不是 §H 的字面判据。** 字面判据要在**一次真实的 worker 运行里观察到
`in_flight >= 2`** —— 两个 worker 真的同时持有租约。本轮没有跑那样一次运行（没有起
两个真的 Claude Code worker 会话去领活），所以**这一条按 FAIL 记**。上面那两条读数证明
的是「正文形状不再是障碍」，不是「两个 worker 真的重叠过」。

**算完成的解封条件**：起 2 个 worker 会话对着同一个作业领活，在 `get_job` 的
`progress.dispatching` 里读到 `>= 2`（或在 `job_attempt` 表里读到两条 `leased` 且
`leased_at` 窗口重叠），并把这个读数连同 `job_id` 记下来。

#### 4b —— **PASS**

四条判据各有具名测试，全部在本轮的全量套件里通过：

| 判据 | 测试 |
|---|---|
| A 读不到 B 的指派 | `tests/test_mcp_assignment_tools.py::test_another_worker_cannot_read_the_assignment` |
| A 交不了 B 的结果 | `tests/test_mcp_assignment_tools.py::test_another_worker_cannot_settle_the_lease` |
| attempt id 张冠李戴被拒 | `tests/test_mcp_assignment_tools.py::test_an_attempt_id_from_another_work_item_is_refused` |
| 过期租约不能事后结算 | `tests/test_mcp_assignment_tools.py::test_a_stale_attempt_cannot_be_settled_after_it_was_released` |
| 同一租约结算两次而结论不同 → 拒 | `tests/test_mcp_assignment_tools.py::test_settling_twice_with_different_findings_is_refused` |
| 跨 worker 抢租约被拒 | `tests/test_job_contract.py::test_another_holder_cannot_take_a_leased_work_item` |

此外 E5c 补上的一条（本轮新增）：**结算那条写路径也判对象** ——
`tests/test_mcp_workspace.py::test_findings_for_another_scene_cannot_be_submitted`。它做过
变异验证：把那行 `_require_scene` 删掉，这条测试当场红，而回执里带着 `content_sha256`
—— 也就是说**修之前，一份别的 Scene 的租约真的会被结算进盘**（变异与还原记录在
`artifacts/marketops-closeout/e5-mutation/`，还原后 sha256 逐字节一致）。

#### 4c —— **PASS**

| 判据 | 测试 |
|---|---|
| 记录活得比服务实例长、按 id 逐字读回 | `tests/test_decision_store.py::test_a_decision_outlives_the_service_instance_and_reads_back_by_id` |
| 重放同一次调用不重复调后端（不重复计费） | `tests/test_decision_store.py::test_replaying_the_same_invocation_does_not_call_the_backend_twice` |
| 同一次 case 收两次费会被报出来而不是被合并 | `tests/test_decision_store.py::test_a_second_payment_for_the_same_case_is_reported_and_not_merged` |
| Jev 与 Laya 各自独立成行、各自计费 | `tests/test_decision_store.py::test_the_same_case_asked_of_two_backends_is_two_records_and_two_payments` |
| 判定不改任何**已核实**的业务事实 | `tests/test_decision_store.py::test_a_decision_changes_no_verified_business_fact` |
| `abstained` 与 `unavailable` 是两种不同的失败 | `tests/test_mcp_decision_tools.py::test_abstained_and_unavailable_are_two_different_failures` |
| 未知 work item 在调后端**之前**就被拒 | `tests/test_decision_store.py::test_an_unknown_work_item_is_refused_before_the_backend_is_called` |
| 未接线的三个入口是三种**不同**的说法 | `tests/test_mcp_decision_tools.py::test_the_three_ways_of_having_no_call_path_are_three_sentences` |

历史 §F 的根因是**「题面 id 从来没有交到研究员手里」**（`decision_record` 恒零行）。
本轮修在 `3666b70`：`get_assignment` 现在把 `spec_id` 一并发下去，并且发下去的那一组
选项与 `decide` 强制的那一组是**同一组**（
`tests/test_mcp_assignment_tools.py::test_the_assignment_hands_over_the_spec_ids_the_decision_tool_wants`
与 `::test_the_handed_over_options_are_the_ones_the_decision_tool_enforces`）。

#### 4d —— **PASS**

修法不是给作业加一个 `done`，而是**说清 `state` 这一格本来就不是进度**（`9b5a74f`），
并另外报出进度与终态。活体回执（本轮真跑出来的作业）：

```json
"state": "queued",
"state_note": "state 是**作业**的状态，不是进度：queued = 受理了且没有阻塞在它的输入上；
  paused / cancelled = 控制端按下的；blocked = 输入产不出工作单元，理由在 reason 里。
  **这里没有 done / settled，是刻意的** —— 「这个作业做完了吗」由工作单元回答
  （看 progress 与 research_terminals），不由作业状态回答。所以一个**全部工作单元都已结算**
  的作业，state 仍然是 queued，而那是正确的读数，不是一格停了。",
"progress": {"work_items": 25, "dispatching": 0, "stopped": 0, "not_started": 25,
             "note": "not_started 是「还没派出租约」，不是「没在跑慢」—— 两者的区别只有租约能回答，所以它从租约算，不从时间算。"},
"research_terminals": {"reported": true, "authority": "marketops.settlement", …},
"usage": {"reported": true, "accounts": [{"configured": false, "reason": "no_ceiling"}, …]}
```

对应的判据是 `tests/test_mcp_assignment_tools.py::test_a_fully_settled_job_still_reads_queued_and_says_why`
—— 它钉住的正是历史那一条「全部终态而作业永远 queued」现在**有了一个说得出口的答案**。

**这里要记一笔诚实的话**：需求原文说的是「建立明确的 **aggregate state projection**」。
本轮给的是 `state_note` + `progress` + `research_terminals` + `usage` 四块，**没有**把作业
状态做成一个新的枚举成员或单独一列聚合态。功能上「不把研究结果状态与执行状态并成一个
字段」这一条满足了；形式上「一个显式的聚合状态枚举」没有做。见 §E.7。

另一条同样属于这一节、且是本轮**真的踩到**的：`results` 那一格在还没有任何结算时
**不报 0**：

```json
"results": {"available": false, "blocker": "还没有任何 work item 结算，所以这里**不报 0 条产出**。
  0 会被读成「跑过了、什么都没找到」，而真实情况是还没有人结算过 —— 两者的处置不同：一个要等，
  一个要去看这次跑得对不对。"}
```

这是「零分母 ⇒ unavailable，不是 0%」这条纪律在活体回执上的样子。

### B.5 端到端：从发现一直到派发出去的作业（本节是这次收口的主要读数）

上一节的四条是**分项**复验。这一条是**同一条链真的跑通**：活体 MCP 服务面、真实 TCP、
真实 bearer 令牌，从一份真实检索池一路到 25 个真的交出去的 Quay 任务。
`artifacts/marketops-closeout/e6-live-head/closure.json` 是原始回执。

```
run-discovery（真跑，花掉 20 次检索）
  → inputs/devon-probe-20260923.jsonl                      （母池工件）
  → read-motherpool → receipt-57bf400076f21b22            （SourceReceipt）
  → submit_research(input_kind=source_receipt, input_ref=receipt-57bf400076f21b22)
  → job_id 30c2bc4174db7406225258b7156fa48b
  → 25 个 work item → 25 个 Quay 任务（MOPS-30c2bc41-…）
```

`submit_research` 的回执（活体）：

```json
{"job_id": "30c2bc4174db7406225258b7156fa48b",
 "scene_id": "uk-acme-natural-grass",
 "input_kind": "source_receipt",
 "input_ref": "receipt-57bf400076f21b22",
 "state": "queued", "reason": "",
 "work_items": {"count": 25, "blocker": ""},
 "dispatch": {"executor_configured": true,
              "counts": {"already_mapped": 0, "adopted": 0, "created": 25},
              "handed_out": 25, "stopped_at": "", "blocker": "",
              "board_convergence": {"ran": true, "considered": 0, "pushed": 0,
                                    "pending": 0, "unmapped": 0, "unsettled": 25}}}
```

`board_convergence` 里有一条不变量被写进了回执：`considered + unmapped + unsettled`
恒等于件数，而 `handed_out` **本来就不该等于它**。

盘上的对应物（`MARKETOPS_QUAY_ROOT=/home/operator/artifacts/marketops-mvp-quay`）：

* `tasks/MOPS-30c2bc41-*.md` 新增 **25 个文件**（该 store 从 9 涨到 34）；
* 每一条 `status: ready`，带 `labels: [marketops-job:…, marketops-item:…]`；
* 正文**只**有一个小节 `## Touches`，条目是它自己那一个文件路径；其余部分只说
  「这是哪一件事」，并明确**不复述**任何工具与预算 —— 指引走 `get_assignment`。

### B.6 工具面：9 / 8 与角色隔离（活体）

同一个活的 HEAD 进程，用不同 principal 的令牌握手后 `tools/list`：

| 令牌 | 角色 | 可见工具数 | 名字 |
|---|---|---|---|
| `claude-control` | controller | **9** | `control_job`, `get_account`, `get_capabilities`, `get_decision`, `get_evidence`, `get_job`, `get_report`, `get_scene`, `submit_research` |
| `claude-worker-1` | researcher | **8** | `crawl_site`, `decide`, `fetch_document`, `get_assignment`, `get_decision`, `get_evidence`, `search_web`, `submit_findings` |

**15 个唯一工具名，与冻结时的名单逐字一致；两侧不重叠。** 而且隔离不止在列表上：用
controller 令牌调 `search_web`，回执是 `Unknown tool: search_web` —— 研究员工具对控制面
**不存在**，不是「存在但拒绝」。

### B.7 质量评测 harness（§5 可复现评测）

**FAIL（不在本构建里）** —— 而且是**本文件里最要紧的一条诚实读数**。

| 项 | 值 |
|---|---|
| `experiments/` 在 HEAD 上 | **不存在**（`git ls-tree -d HEAD` 里没有） |
| 它在哪 | 分支 `marketops/v1-serper-discovery`，tip `cb9dac171d49b2a5687bc50c741b1e88cdda94f6` |
| merge-base | `817f0031d6dae97d9ef9c44dbd53dc3c699b2e1c` |
| 分叉 | `branch...HEAD` = **22 / 129** |

所以「可复现评测 harness」这一条的现状是：**它在另一条分支上，没有进本构建。** 本轮
没有把它合过来（合它是一次独立的、有自己判据的收敛动作，不该塞进 E1–E5 的收口里）。

在它那条分支上，零分母的处理是返回 `None` 并在渲染时写成 `—（不可求值）`，**不是**
字面量 `unavailable`（`marketops/experiments` 在该分支上对 `unavailable` 零命中）。
这与本仓在别处（活体 `results.available = false` + `blocker`，见 B.4d）用的口径**不是
同一个写法**。见 §E.3。

### B.8 Scene / profile 演进

**PASS（未破坏）** —— 本轮对 `profiles/uk-acme-natural-grass` **一处都没有改**，
而冻结那一份在本轮的全量套件里仍然是绿的（`tests/test_profiles.py`、
`tests/test_profiles_schema.py`，以及所有以 bundle fixture 为入口的测试）。

活体侧面：`--check` 报 `workspace = uk-acme-natural-grass`、`audiences` 与
`workspaces` 各一格，而**「一个部署只服务一个 Scene」是明写的拒绝**（B.9）。

演进面本身（`docs/SCENE_EVOLUTION.md` 描述的那条路）本轮没有推进：没有新增 profile
版本，也没有改 Scene 的装载路径。`NOT_RUN`。

### B.9 E5 · 对象级工作区 / 授权

**PASS**。这一条本轮做得最深，因为它有一条会红的边界要守。

设计形状（逐字对应需求里的 `传输身份 → 主体 → 工作区/上下文 → 对象授权`）：

```
传输身份（audience + expires_at，auth.py）
  → 主体（摘要命中环 → principal，tokens.py）
  → 工作区（claims.workspaces ∩ 本部署的 Scene，guard.py 中间件）
  → 对象授权（这一行作业 / 这一条 work item 属于哪个 Scene，tools.py 的 _require_scene）
```

工作区取值 = Scene 的 profile id。`Principal.workspaces` 缺省是**空集**（失败关闭）；
`AccessToken.claims` 里那一格叫 `workspaces`，不叫 scopes。

四条**活体负向**读数：

1. **别的 Scene 的调用被拒**（活体，真实 TCP）：

   ```
   submit_research(scene_id="some-other-scene", …)
   → Error executing tool submit_research: 没有名为 'some-other-scene' 的 Scene。
     本部署配置的 Scene 是 'uk-acme-natural-grass'（MARKETOPS_PROFILE）。
     一个部署只服务一个 Scene —— 证据、预算与凭据都属于它；静默退回默认值会让调用方
     以为读的是另一个场景的规则。
   ```

2. **无令牌被拒**：`MCPError: Server returned an error response`，服务端日志 401（B.3）。
3. **受众不符被拒**：同一份 HEAD 进程绑在 8789 时四条调用全 401（B.3）。
4. **跨 Scene 的租约结算被拒**：`test_findings_for_another_scene_cannot_be_submitted`，
   且做过变异验证（4b 末段）。

`_require_scene(settings, scene_id)` 现在是**七个调用点**的单一收口（定义 + `_scene` /
`_submit_research` / `_job` / `_control_job` / `_assignment` / `_attempt_for`），其中
`_attempt_for` 是本轮补上的那条**写**路径。它的顺序是有意的：**Scene 先于枚举尝试**
—— 枚举回执里会带每条 attempt id，而 attempt id 是内容推导的摘要，跨场景泄漏它就是
泄漏另一份作业的运行记录指纹。

本地测试面上的判据：`tests/test_mcp_workspace.py` 共 17 条，含
`test_a_token_for_another_workspace_is_refused_on_every_tool`、
`test_the_refusal_happens_in_the_application_not_at_the_transport`、
`test_the_tool_list_is_not_filtered_by_workspace`、
`test_a_role_failure_is_decided_before_the_workspace`。

**运维侧的前置条件（真的踩到了）**：格式 3 意味着任何既有部署**必须先重铸令牌环**。
本机 vault 里那一份 `marketops-mcp-tokens.json` 在 2026-09-23 时是**格式 1**，于是

```
python -m marketops.interfaces.mcp --check
→ exit 2，blocker: 令牌环格式版本是 1，本构建只认 3：…/marketops-mcp-tokens.json
```

本轮按代码自己给出的出路，把三个主体按**格式 3** 重铸到**另一个文件**
（`/home/operator/.secrets/vault/marketops-mcp-tokens-v3.json`，0600，每个主体
`--audience http://127.0.0.1:8787/mcp --workspace uk-acme-natural-grass --expires-in 30d`），
`--check` 随即 `ok: true`、`exit 0`。**旧文件原地保留，没有删** —— 它是既有授权的记录。
切换是一次运维动作（把 `MARKETOPS_MCP_TOKENS` 指到新文件并重启），不是代码路径。

### B.10 飞书（Feishu）状态

**BLOCKED** —— 状态是诚实的，能力是没有的。

* `marketops/adapters/feishu/client.py` 的 `ADAPTER_STATE == "interface_only"`，由
  `tests/test_feishu_adapter.py:101` 钉住（「接线没做完但状态先改了」会当场红）；
  该文件里唯一的 `NotImplementedError` 在 `ADAPTER_STATE != "wired"` 时不可达。
* 活体 `get_capabilities` 的 `integrations` 里有 **7** 项：`serper` / `http` /
  `crawl4ai` / `mineru` / `quay` / `jev` / `laya`。**没有 `feishu`** —— 配置了凭据也不
  等于接线，而回执没有把它算进来。
* 解封条件：一个**被授权**的 sink，且它必须在报告交付成功之前回读写入的行 id、版本与
  条数。本阶段（研究期，`send_authorized = false`）不做。
* 双向飞书这件事本身（入站读取 / 出站投影）本轮 `NOT_RUN`，也不在 E1–E5 范围内。

### B.11 活体集成总表（从活体 `get_capabilities` 原文读出）

| 集成 | kind | configured | runtime_ready | wired | phase | blocker |
|---|---|---|---|---|---|---|
| serper | search | true | true | true | D | — |
| http | acquisition | true | true | true | D | — |
| crawl4ai | acquisition | true | true | true | E | — |
| mineru | document_parse | true | **false** | true | D | `ModuleNotFoundError: No module named 'mineru'` |
| quay | dispatch | true | true | true | G | — |
| jev | decision | true | true | true | F | — |
| laya | decision | true | true | **false** | F | 「本部署的辅助后端是 'jev'，decide 只咨询它。」 |

最后一行不是缺陷，是**一个 `DecisionProvider` 端口、一个部署只接一个后端**这条边界的
样子：Laya 装好、就绪，但这个部署咨询的是 Jev。

同一份回执里还有 `research_only: true` 与 `send_authorized: false`，以及一条关于
`last_live_verification` 的顶层 note —— 「恒为 null……那不是数据缺失，是「从未发生」
—— 一个恒绿的健康旗比没有旗更危险」。

---

## C. Tests

| 项 | 值 |
|---|---|
| 命令 | `cd <worktree> && /home/operator/toolchains/marketops-mvp/bin/python -m pytest -o addopts="" -q -rs` |
| 结果 | **2473 passed**，0 failed，0 skipped，0 error，耗时 70.69s |
| lint | `/home/operator/.local/bin/ruff check .` → **All checks passed!**（ruff 0.15.22；CI 只跑这一条） |
| 契约重生成 | `python -m marketops.schema` 退出 0，`git status --porcelain contracts/` **空**（12 份契约与 committed 逐字节一致） |

失败原因：**无失败**，所以这一栏没有内容可写。`-rs` 打开的情况下 skipped 是 0 ——
包括那些会去对照 `sources/` 上游冻结快照的用例，在本机是**真的在跑**。

与判据直接相关的几条专项（都在上面那一轮里）：

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_mcp_workspace.py tests/test_mcp_assignment_tools.py tests/test_decision_store.py tests/test_mcp_auth_interop.py -q` | 全绿（E5 / E4b / E4c / E3 的承载文件） |
| `python -m pytest tests/test_dispatch_ordering.py -q` | 全绿（§C 那条正文形状判据的承载文件） |
| `python -m marketops.interfaces.mcp --check` | `ok: true`，exit 0（格式 3 环） |

**测试纪律**：本轮没有删掉或放松任何一条既有断言来求绿。唯一新增的「放宽」是把
`_ALLOWED_BODY_SECTIONS` 从「一个小节都不许有」收窄成「**只许有这一节**」，而它是在
同一条理由（多出来的每一节都会让这一行在外部闸门里像一份软件工单）下**更准**
的一条判据，不是放宽 —— 其反面（没有这一节）被阴性对照钉死。

---

## D. 真实集成

逐项，分开写。**缺凭据 ⇒ BLOCKED，绝不写 PASS。**

### D.1 Serper —— **PASS（真实调用）**

本轮的发现运行花掉了 **20 次**检索（`spent: 20 / ceiling: 20`），拿到 **200 条命中 /
82 台主机**，落盘在 `mirrors/serper/<scope>/<hash16>.json` 与
`inputs/devon-probe-20260923.jsonl`。额度上限来自冻结 profile 的 `search.max_queries`。

### D.2 Crawl4AI —— **PASS（真实浏览器抓取）**

同一次运行里 25 台主机抓取成功（`ok: 25`），另有 `blocked: 8` / `timeout: 3` /
`error: 3` / `not_found: 1` / `not_fetched: 42`。stderr 里有 crawl4ai 自己的
`[INIT] → [FETCH] → [SCRAPE] → [COMPLETE]` 逐页记录，例如
`https://client28.example.org/partners/CLIENTATHLETICTRACK | ✓ | ⏱: 2.26s`。

`blocked: 8` 这一档值得单独看：它们是**被拒的目标**（URL 判据 / 站点访问控制），记成
结果而不是崩溃，也不去绕。

### D.3 Jev —— **PASS（真实调用，有 answered 记录）**

凭据在 `/home/operator/.secrets/vault/typesafe.env`（键名 `TYPESAFE_API_KEY`，
长度 108；**值从未打印**）。一次真实判定，回执原文：

```
provider   = jev
model      = jev-1.13.0
checkpoint = jev-latest
route      = "jev-latest: 部署配置固定的模型，托管 API 没有本地路由"
outcome    = answered
answers    = ChoiceAnswer(question_id='band', choice='medium',
                           distribution=(('high', 0.0), ('medium', 0.86), ('low', 0.14)),
                           confidence=0.79)
latency_ms = 301
usage      = input_tokens 434, output_tokens 38, billed=True
mode       = assist
record_id  = f3ff3578120a49e302a76454659f6bfe
```

（`artifacts/marketops-closeout/e6-live-head/jev-record.json`。）

### D.4 Laya —— **PASS（真实调用），但本部署不接它**

同一天用**同一个 `DecisionProvider` 端口**对 Laya 发过一次真实判定（`provider=laya`，
`model=laya-rl-agent`，`outcome=answered`，分布 `high 0.3204 / medium 0.2517 / low 0.4279`，
`confidence 0.0214`，`latency_ms 16369`，`input_tokens 154`），checkpoint 指向
`/home/operator/caches/models/laya-multilingual/052592a15d198d9ad47da779604259b10b47b7aa`
（四个必需条目齐备）。

**但活体 `get_capabilities` 报 `laya.wired = false`**，因为本部署
`MARKETOPS_DECISION_PROVIDER=jev`。所以「Laya 真的能跑」与「本部署在用它」是两句话，
两句话都要说。这正是「Jev / Laya 在**一个**端口后面」这条设计的样子：
**一个部署只咨询一个后端，而且这件事是显式配的，不是静默回落的。**

### D.5 Quay —— **PASS（真实派发 + 真实调度器读数）**

* 本轮真实派发：`dispatch.created = 25`、`handed_out = 25`，盘上
  `tasks/MOPS-30c2bc41-*.md` 新增 25 个（store 9 → 34）。
* 真实调度器（Quay 0.11.0 的 `plugin/scripts/concurrent-batch-scheduler.ts`）对这 25 条
  **真实正文**报 `BATCH (25-wide, concurrent)`；阴性对照报 `BATCH (0-wide, concurrent)`。
* Quay CLI 路径：`/home/operator/artifacts/quay-cli-build/quay/packages/quay/bin/quay.js`
  （node `/home/operator/toolchains/node/current/bin/node`），root
  `/home/operator/artifacts/marketops-mvp-quay`。
* **没有**做：两个 worker 真的同时领活的那次运行（见 B.4a，这是 §E.1）。

### D.6 Claude Code / MCP —— 服务面 **PASS**，worker 会话 **NOT_RUN**

* **服务面 PASS**：活的 HEAD 进程（`python -m marketops.interfaces.mcp`，HEAD 的
  `source_tree`）在 `127.0.0.1:8787` 上被一个真的 SDK 客户端（`mcp==2.2.0` 的
  `streamable_http_client` + `ClientSession`）通过 **TCP** 完成 `initialize`，
  协商协议版本 `2025-11-25`，拿到 9 / 8 两组工具，跑完 B.5 那条端到端链。
* **Claude Code 作为 worker 的会话 NOT_RUN**：本轮没有起一个真的 Claude Code worker
  去 `get_assignment` → 用工具 → `submit_findings`。最近一次这样的真实运行发生在
  2026-09-22，那时**没有** E1–E5 的代码。

  这里要点名一条禁令：**外层 Claude Code 进程不得冒充 Quay worker**。所以「没有跑」
  就写「没有跑」，不能拿本会话的 Claude Code 去顶替。

### D.7 关于「跑着的那个进程」

本轮开始时，`127.0.0.1:8787` 上跑着 pid 1504909（起于 11:20:27），而它加载的
`marketops` 来自 **`/home/operator/workspace/worktrees/marketops/mvp`**
（`PYTHONPATH` 写着，分支 `marketops/mvp`，tag `3744262`），`tools.py` mtime 10:10:46。
**它加载的不是这次要验收的代码**：`3744262` 是 HEAD 的祖先，中间隔着 **16 个提交**，
E1–E5 一个都不在里面。这正是本机已知的那个陷阱 —— **跑着的进程可能是旧的，而它的表现
是「回执少了一块」而不是报错**。

**这一节也要记一次我自己的操作事故**：我为了重启探针进程下了一条
`pkill -f "marketops.interfaces.mcp" -u member-b`，它比我的本意宽，把 8787 上那个进程
**一起杀掉了**。事后核查：当时 8787 上**零条已建立连接**，`~/.mcp.json` /
`~/.claude.json` / `~/.codex/config.toml` 与本仓任何 `.mcp.json` 里**都没有**指向 8787
的客户端配置，所以没有已配置的消费者被打断。随后把这个监听**按 HEAD 重建**了
（pid 2296349，`--check` `ok: true`），而不是把旧代码原样拉回来 —— 两者都是「恢复」，
但只有后者是本次要验收的那个构建。启动脚本留在
`artifacts/marketops-closeout/e6-live-head/start-8787.sh`。

---

## E. Known remaining gaps

只列**真实未完成**的。每一条写：缺什么 / 为什么 / 哪个模块 / 什么条件算完成。

### E.1 §H 的字面判据：一次真的观测到 `in_flight >= 2` 的运行

* **缺什么**：一次真实的多 worker 运行读数。
* **为什么**：历史 FAIL 的根因（正文缺 `## Touches` ⇒ 派发器保守地串行）已经修掉，
  而且本轮在 25 条**真实**任务正文上量到 `BATCH (25-wide, concurrent)`，阴性对照
  `BATCH (0-wide, concurrent)`。但「正文形状不再是障碍」不等于「两个 worker 真的重叠过」
  —— 后者需要一个真的双 worker 运行。
* **哪个模块**：`marketops/application/dispatch.py`（正文模板）、Quay worker 驱动器、
  `marketops/adapters/sqlite/jobs.py`（`job_attempt` 的租约行）。
* **什么条件算完成**：对同一个作业起 2 个 worker，在 `get_job` 的
  `progress.dispatching >= 2`，或 `job_attempt` 里两条 `state='leased'` 且
  `leased_at` 窗口重叠；把 `job_id` 与这个读数一并留档。

### E.2 质量评测 harness 不在本构建里

* **缺什么**：`experiments/`、`marketops/experiments/`、`services/benchmark/`。
* **为什么**：它们只存在于分支 `marketops/v1-serper-discovery`（tip `cb9dac1`，
  merge-base `817f003`，`branch...HEAD` = 22 / 129）。把那条分支收敛进来是一次独立的、
  有自己判据的动作，不该塞进 E1–E5 的收口。
* **哪个模块**：`marketops/experiments/`（比较层、闸门、规格装载）、`experiments/*.yaml`。
* **什么条件算完成**：那条分支的评测 harness 进 `main`，并且**零分母那条口径与本仓
  统一**——不是 `None` + `—（不可求值）`，而是字面量 `unavailable`（本仓在
  `marketops/experiments` 里对 `unavailable` 零命中）。

### E.3 「所有工作单元都终态」的聚合状态没有做成一格枚举

* **缺什么**：一个显式的 aggregate state projection（需求原文的措辞）。
* **为什么**：本轮给的是 `state_note` + `progress` + `research_terminals` + `usage`
  四块只读投影（见 B.4d），作业行上的 `state` 仍只有
  `queued / paused / cancelled / blocked`。功能上「不把研究结果状态与执行状态并成一个
  字段」满足了；形式上没有一个新的枚举成员。
* **哪个模块**：`marketops/domain/enums.py::JobState`、
  `marketops/adapters/mcp_server/tools.py::_job`。
* **什么条件算完成**：要么明确决定「不引入」并把这条从需求里划掉（现在的 `state_note`
  就是那个决定的一种表达），要么加一格**派生**的聚合态（由工作单元算，不落库），
  并在 `get_job` 的回执里与 `state` 并列。

### E.4 DecisionRecord 与 job store 之间没有引用完整性

* **缺什么**：`decision_record.work_item_id` 不被校验是否真的存在于 job store。
* **为什么**：端口层收的是 `work_item_id`，但那一层不读 job store，所以一条指向不存在
  工作单元的记录在库层面写得进去。今天没有活体会走到那条路（`decide` 之前的
  `get_assignment` 已经把 work item 判过一遍），但这是**执行顺序**在挡，不是**约束**在挡。
* **哪个模块**：`marketops/adapters/sqlite/decisions.py`、`marketops/adapters/sqlite/db.py`
  的 `decision_record` 表定义。
* **什么条件算完成**：写入路径拒绝未知 `work_item_id`（或加一条外键 / 一条具名测试），
  测试名类似 `test_a_record_cannot_be_stored_without_a_real_work_item` —— 那一侧已有
  半条，需要的是**存储层**的那半。

### E.5 Account 级知识对象没有 Scene 血统

* **缺什么**：`evidence` / `contact_route` / `route_opportunity` 三张表不带 `scene_id`。
* **为什么**：它们按 `account_id` 落，而 account 这一层今天没有 Scene 这一维。于是
  「这一条证据属于哪个 Scene」只能靠 `account_id` 的名字去推。
* **为什么现在还不致命**：一个部署只服务一个 Scene（B.9），所以「同一个库里有第二个
  Scene 的 account」这件事在生产里不成立 —— 它成立的那一刻，这条缺口就变成一条真的
  跨 Scene 读。
* **哪个模块**：`marketops/domain/evidence/models.py`、
  `marketops/adapters/sqlite/evidence_store.py`、`marketops/domain/routes.py`。
* **什么条件算完成**：**共享多工作区之前**——三张表里至少 evidence 带上可以回溯到 Scene
  的一格，并对「另一个 Scene 的 evidence 被读到」有拒绝测试。

### E.6 上游母池的**真实**导出在本机不存在

* **缺什么**：一份真实的、来自业务上游的母池导出。
* **为什么**：`data_root/inputs/` 下三份 YAML 都是**受理输入**（`known_accounts`），
  不是母池导出。`read-motherpool` 对它们的裁决是**拒绝**（形状不对，exit 2 —— 这正是
  失败关闭该有的样子）。本轮跑通的那条链，母池那一端是**由发现自己产出的**
  （`devon-probe-20260923.jsonl`），按 SOP L82 它属**补充证据，不是母池**，回执里也
  这么写着。
* **哪个模块**：`marketops/application/inputs.py`、`marketops/interfaces/cli.py::read-motherpool`。
* **什么条件算完成**：拿到一份真实上游导出，走 `read-motherpool` 得到一份六个数相加等于
  行数的回执，并把 SOP 那句「这一池来自检索 / 来自上游」在报告里区分开。

### E.7 `mineru` 未安装

* **缺什么**：`mineru` 模块。
* **为什么**：可选依赖，本机未装。
* **哪个模块**：`marketops/acquisition/pdf.py` 的复杂 PDF 降级路径。
* **什么条件算完成**：装上并让 `get_capabilities` 里 `mineru.runtime_ready` 翻真；
  或者明确决定本阶段不做重 OCR，把它从验收里划掉（后者更符合「本阶段不做重 OCR 平台」）。

### E.8 本机活体部署的两项运维前置

* **缺什么**：（a）活体进程指向 HEAD；（b）`MARKETOPS_MCP_TOKENS` 指向格式 3 的环。
* **为什么**：本轮开始时跑的是 `marketops/mvp@3744262`（16 个提交之前），而 vault 里
  的环是**格式 1** ⇒ HEAD 的 `--check` 退 2。两件都已经在本轮处理好：
  8787 上现在是 HEAD（pid 2296349），格式 3 的环铸在
  `/home/operator/.secrets/vault/marketops-mcp-tokens-v3.json`。
* **但这不是仓库里的状态**：任何**新**的部署（另一台机器、另一个 checkout）仍会撞上
  同一件事，而它是**刻意的**（格式不符 ⇒ 报错，不迁移）。
* **哪个模块**：部署配置（不是代码）。
* **什么条件算完成**：部署文档里写死这两步；本机则把旧环文件归档、把
  `MARKETOPS_MCP_TOKENS` 正式切到新文件（本轮**没有**切，为了不打断任何可能存在的
  48 小时内的消费者）。

### E.9 上游实跑里的两个**质量**问题（数据侧的诚实读数）

这两条不是「没做」，是「跑了、结果不好看」，所以必须留下：

* **社交聚合域名被读成 official domain**：真实回执里出现了
  `official_domain: facebook.com`、`client04.example.org`，以及
  `resolved_name` 是一段**散文**（`"If you're looking for Technical Coaching, why not get
  in touch…"`）。这是发现期的真实表现，不是解析器的崩溃。
  **模块**：`marketops/application/qualify.py` / 发现期的名字读取。
  **算完成**：`resolved_name` 要么是一个像名字的串，要么明确记成「读出来了但不是名字」；
  聚合域名不进 `official_domain`（或进了但带一个说得出的理由）。
* **3 格是空的而不是未确认**：`organisation_kind` / `venue_environment` /
  `surface_class` —— 回执自己点名了这件事（「空值在规则方言里读作「记了，是空」——它会
  在报告里看起来像一条结论」）。缺的是那三件事在发现期的**生产者**。
  **算完成**：三个事实有了生产者，或者它们从规则方言里被明确排除。

### E.10 §N.2 的指引正文

`MVP_MIGRATION_PLAN.md` §2.15 记着的那一节正文（外部条目里该不该带指引）在本轮
**没有重新读过**，`NOT_RUN`。

---

## 附：本文件用到的原始读数落在哪

全部在 `/home/operator/artifacts/marketops-closeout/`（在 `WORKSPACE_ROOT` 之外，
符合凭据与运行时产物的落点约定）：

| 路径 | 内容 |
|---|---|
| `e6-live-head/closure.json` | 活体端到端：负向 Scene、受理、`get_job` 的原始回执 |
| `e6-live-head/capabilities.json` | 活体 `get_capabilities` 原文（7 项集成 + build + note） |
| `e6-live-head/live-roundtrip.json` | 9 / 8 工具面、角色隔离、无令牌 401 |
| `e6-live-head/jev-record.json` | 真实 Jev `DecisionRecord` |
| `e6-live-head/discovery-dryrun.json` | 542 条计划的 dry-run 读数 |
| `e6-live-head/discovery-run.out` | 真实发现运行的完整回执（含 8 条 caveats） |
| `e6-live-head/motherpool-read.out` | 真实池子 → SourceReceipt |
| `e6-live-head/scheduler-real.txt` | 25 条真实正文的 `BATCH (25-wide, concurrent)` |
| `e6-live-head/server.log` | 绑 8789 时的 401 记录（受众不符那条判据的现场） |
| `e4-concurrency/negative-control.txt` | `BATCH (0-wide, concurrent)` 阴性对照 |
| `e5-mutation/` | `_require_scene` 的变异与逐字节还原 |

派发出去的作业与它的 25 个外部条目：
`job_id 30c2bc4174db7406225258b7156fa48b`，
`/home/operator/artifacts/marketops-mvp-quay/tasks/MOPS-30c2bc41-*.md`。

**这一次改动 `docs/MVP_ACCEPTANCE_REPORT.md` 了吗：没有。** 它一个字节都没动。
