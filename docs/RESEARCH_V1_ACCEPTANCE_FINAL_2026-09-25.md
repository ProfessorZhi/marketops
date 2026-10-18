# Research v1 最终验收：2026-09-25

这一份回答一个问题，而且只回答这一个：

> **第一层 Research v1 是否已达到「真实日常可用」？**

**否。** `NOT_READY_FOR_REAL_USE`。

比 2026-09-24 那一份进了一大截：**九条承重断点里八条已经在真实链路上打通并留下活实证**
——研究件的主语换成了 canonical 账号 id、四格资产事实有了生产者、责任关系由真实
Research 路径产出且被 Jev 消费、路线由应用用例生成、未来日期不再打断整轮研究、
维护的 Run1 → Run2 闭环跑通、模板族与写作策略有了产品入口、草稿可自述来历。
**但 §14 逐字要求 `Draft count > 0`，而本轮真实链路上的草稿数是 0。**

理由不是「草稿生成器坏了」。理由是一条**在每一个阶段都生效的闸门**，它的注释与
docstring 都写着它只在**首次接触**上生效：`KEY_CONTACT_GRADES = {A, B}`。本语料
的 22 条路线全部是 D/X —— 于是这条闸门把 22 条全部跳掉，一条草稿都没有机会被写。
而 D/X 的全部成因，是**第二个真实断点**（SOP L121 那条域接缝）在本语料上把
`official_business_entry` 一级的候选全部拒收。下面每一条都是本次测量出来的读数。

## 这份文件不是什么

- **不是** `docs/RESEARCH_V1_ACCEPTANCE_2026-09-24.md` 的修订。那一份是 `d1e9d73`
  上的测量，本文件是 `e919d98` 上的另一次测量，两者相隔 10 个提交。两份并列，
  谁也不覆盖谁，旧文件一个字节都没改。
- **不是** `docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md` 的修订，也**不是**
  `docs/MVP_ACCEPTANCE_REPORT.md` 的修订。后者是冻结记录：里面每一条 FAIL 仍然是
  FAIL，本文件不改它任何一行。
- **不是**质量评级。本文件**不给合格率**，理由见 §G。
- **不是**部署证书。生产没有上线。
- **不是**「上面那份的补充」。上面那份的五条真实阻塞里有三条本轮真的关掉了，
  另外两条仍然开着，并且本轮**新查出两条**。全部列在 §H。

## 时间口径

测量窗口 **2026-09-25T13:47–14:52 UTC** = **2026-09-25 21:47–22:52 本地（UTC+8）**。
内文时间戳一律取 UTC（与库、账本、工件名一致）。文件按本地日期命名。

---

## A. 被测构建与装置

| 项 | 值 |
|---|---|
| 仓库 | `ProfessorZhi/marketops` |
| 检出 | `/home/operator/workspace/worktrees/marketops/impl-20260923`（worktree，非 canonical checkout） |
| 分支 | `marketops/product-loop-20260923` |
| **被测构建（git SHA）** | `e919d989583235ceb30cd50bb41b3bee25c324ff` |
| 测量时工作树是否脏 | **否**。`git status --porcelain` 输出为空 |
| 基线 | `6ad5c6303ce211976ba373135e196dceb5c87318`（PR #6 分支当时的远端头） |
| Python | 3.12.3 |
| 解释器 | `/home/operator/toolchains/marketops-mvp/bin/python` |
| httpx / pydantic | `0.28.1` / `2.13.5` |
| 传输 | Streamable HTTP，绑定 `http://127.0.0.1:8797/mcp` |
| 认证 | bearer 令牌，`TokenRing` 格式 3，受众 `http://127.0.0.1:8797/mcp` |
| 辅助判定后端 | `MARKETOPS_DECISION_PROVIDER=jev`（Laya 未启用，`DEFERRED`） |
| 检索提供方 | Serper（键从 vault 读入，未落盘、未打印） |

**装置是隔离的。** 数据根 `artifacts/marketops-acc-final/run2`、令牌环
`.../vault/marketops-mcp-tokens.json`、Quay 工作区 `.../quay`、端口 **8797** ——
四样都是本次验收现铸的，与 8787 上那个部署没有共用一件。**8787 / 8796 / 11000 /
11001 上跑着的服务不是本装置起的，本装置一个都没碰。**

**`run2` 这个名字是一次刻意的重跑。** §14 要求「从干净隔离数据根、在**最新 HEAD**
上重新运行」：投影层改过之后，旧那一轮（`run`）的回执不再是这一版代码的回执。
`run` 留着当历史，不作为本文件的读数来源。

---

## B. CI

PR #6 在基线 `6ad5c63` 上：

| job | 结论 |
|---|---|
| `test (3.11)` | `success` |
| `test (3.12)` | `success` |

**本文件自身的 CI 读数**（被测构建推上去之后才有，本地绿灯不能代替它）：

| 项 | 值 |
|---|---|
| 被推送的 revision | `bfebb1f08d1d2333cc41b103647f45e463bfcb79` |
| run（push 事件） | `36151558798` |
| run（pull_request 事件） | `36151564804` |
| `test (3.11)` | `success` |
| `test (3.12)` | `success` |

**这条读数属于 `bfebb1f`，不属于任何别的 revision。** 记录它本身又产生一个新的
提交，而新提交的 CI 读数不在这张表里 —— 这是引用 revision 时固有的性质，
不是一处遗漏：要判某个 revision 的 CI，去读**那个 revision 的** run。

**干净克隆 `.[dev]`（本机实测，不是 CI 的副产物）：**

```
git clone --quiet /home/operator/workspace/worktrees/marketops/impl-20260923 <检出>
uv venv --python /usr/bin/python3.12 <检出之外的空目录>
uv pip install --python <venv>/bin/python -e ".[dev]"
python -m pytest -o addopts="" -q -rf     →  4346 passed, 15 skipped in 219.01s   rc=0
python -m ruff check .                    →  All checks passed!
python -m marketops.schema; git status --porcelain contracts/  →  空
```

那 15 条 skip 全部是**可选**的上游对照与可选 SDK（`test_sources_snapshot.py` 5 条、
`test_business_sources.py` 3 条、`test_decision_providers.py` 4 条、
`test_evidence_store_contract.py` 3 条）。工作树里跑出 `4358 passed / 3 skipped`，
差的那 12 条正是本机旁边**真的有** `records` 检出时才会跑的上游对照 —— 干净克隆上
它们按设计 skip，不是少跑了什么。

---

## C. 真实使用验收（§14 的读数）

**一句话**：整条链从 Scene 走到草稿，走完了；**在草稿这一步产出 0**。

### C.1 逐项

| 环节 | 读数 | 判定 |
|---|---|---|
| Scene | `uk-acme-natural-grass` v1，`state: active`，`frozen_sha256 3fc32c05aeb108aa`，`exclusions` 九条，`region ["GB"]`，`source_plan` 三条 | 真 |
| Discovery | `run_id acc-final2-devon-20260925-20260925T134817Z`，`source_method_id search.engine.v1`，`raw=25 resolved=20 merged=0 excluded=5 pending=0 failed=0` | 真（25 个原始候选） |
| Asset | `venue_asset` **20 行** | 真 |
| Canonical Account | `market_account` **20 行**，全部 `acct-<hex16>` | 真 |
| 责任关系 | `account_venue_relation` **13 行**，**10 条**能被证明由一条 Jev 决策消费 | 真 |
| Claude / Quay | 20 件工作单元全部走 Quay driver → Claude Code worker，`quay_exit_code` 20/20 为 `0` | 真 |
| Evidence | `evidence` **1230 行**（按**字面块**计），全部 `fetched_html`、全部带 `literal_value`；去重到来源页是 **284 份内容 / 270 个 URL** | 真 |
| ContactRoute | `contact_route` **22 行**，`D 18 / X 4`，与 `route_opportunity` 263 行并存 | 真 |
| Maintenance Run1 | due 24 → claimed 24 / done 24 / failed 0 | 真 |
| Maintenance Run2 | due 16 → claimed 16 / done 16 / failed 0 | 真 |
| Observation / change | `change_signal` 20 行，`observation` 36 行 | 真 |
| TemplateFamily | `family-uk-natural-grass-first-touch` rev 1 | 真 |
| **Draft** | **`drafts: []`，`planned: 0`，`skipped: 22`** | **0 —— 这一条不成立** |

### C.2 三方身份一致（§14 逐字要求的那条）

§14 要求证明 `research work_item.account_id == canonical market_account.account_id`，
且不存在并行的 `row-*` Account 空间。四条读数：

1. `work_item.subject_ref` 20 件**全部**是 `acct-*`；`market_account` 20 行**全部**是
   `acct-*`；两者是同一个集合。
2. `market_account` 里**没有任何**非 `acct-` 前缀的行。
3. `evidence.account_id` 里**没有任何** `row-*`。**不存在并行的 `row-*` Account 空间。**
4. **路线↔证据归属不符 0 条**：每一条路线的 `evidence_ids` 逐条解得开，且解出来的
   证据行的 `account_id` 就是那条路线的 `account_id`。

**有一处并行的句柄空间，它不是账号空间。** `evidence` 的 1230 行里，**1180 行**挂在
`acct-*` 下、**50 行**挂在 `site-*` 下（**25 个**场馆句柄）。`site-*` 是**发现期的场馆
句柄**，`acct-*` 是**账号**；两者不互相冒名，路线与结算引用的**全部**是 `acct-*`。
它们是同一条链上两种句柄，不是两套账号。**20 份来源页的内容同时出现在两岸**
（`site-*` 侧 40 行、`acct-*` 侧 46 行）—— 这是句柄并行的真实现象，不是重复计数错误。

**「1230 行」是字面块数，不是来源页数。** 证据的粒度是**一页里的一个字面块**：
`https://client22.example.org/staff-directory` 一页、一次抓取（`fetched_at` 只有一个值）、
一份内容（`content_sha256` 只有一个值）产出 **254 行**，每行一个不同的 `literal_value`
（页头、每个邮箱各一行）。全库去重后：**771 个**不同的 `literal_value`、
**284 份**不同的页面内容、**270 个**不同的 `source_url`。三个数都对，回答的不是
同一个问题 —— 报数时必须说清报的是哪一个。

### C.3 真实跨账户负例（活的）

以 `claude-worker-1` 对 UMSL 的**已结算**工作单元
`b85e405047f17595c814b936c4a2a32c` / attempt `830536b1cc16e09c07fe482aeb1bced0`
调 `submit_findings`，引用 **Example Village** 的证据 `3b0630b6a4ca26a6b5e34dc5864becd5`：

```
is_error = True
「这些证据引用解得开，但不是这一件工作的……整份产出被拒绝」
```

事后 UMSL **仍只有原有路线，一行都没写**。这是 §14 要的那个「真实的被拒绝的跨账户
负例」，不是在应用层或只读探针里造出来的。

### C.4 Claude / Quay：走的是外部执行器，读数完整

20 件工作单元，**20 个互不相同的 Quay task_id**：

| 格 | 读数 |
|---|---|
| `quay_exit_code` | `0` × 20 |
| `quay_final_state` | `exited-not-landed` × 20 |
| `external.code_landing` | `not-applicable` × 20 |
| `quay_wall_clock_ms` | 最小 94 280 / 最大 344 118 / 合计 **72.6 分钟** |
| `board_sync.synced` | `true` × 20 |
| `quay_failure_reason` | 18 条 `worktree state unreadable (git worktree list failed)`、2 条 `task status=needs-human (not done)` |

**最后那一行是 §9 最锋利的一条证据。** 18 件工作单元的**外部**观察是「worktree 读不到」，
而产品的权威终态是：

```
research_terminals.authority      = marketops.settlement
authoritative_source              = marketops.settlement  × 20
external_matches_authoritative    = true
authoritative: done 18 / needs-human 2
chain.settlement.accepted         = true × 20
```

**即：Quay 读不到那件工作的 worktree，而研究仍然被判为完成 —— 因为完成的判据是
「一份被受理的 MarketOps settlement」，不是「git 能读到一个 worktree」。** 这正是 §9
逐字要求的口径，而且是活的。

### C.5 Run1 → Run2：到期依据是变更信号真的把那格提前了

维护这一格最容易被做假的地方是「让它到期」。本轮逐条读：

**`change_signal` 20 行，`aspect` 全部是 `source_coverage`，写入时刻 13:57:44 → 14:38:02。**
每一条的理由逐字点名了这一轮为那个账号新落了**几条**来源（例：「这一轮研究为这个账号
新落了 25 条来源（fetched_html×25）——来源供给不再是上一次盘点时的样子，该重看一眼」）。
写入时刻**跟着研究结算走**，不是一次性批量造的。

| | Run1 | Run2 |
|---|---|---|
| 计划时刻（UTC） | `14:02:55` | `14:40:55` |
| `due_count` | **24** | **16** |
| 到期理由 | `never_observed` × 20 + `change_signalled` × 4 | `change_signalled` × 16 |
| `refreshable_aspects` | `["source_coverage"]` | `["source_coverage"]` |
| 截断 | cap 30 / kept 24 / dropped 0 | cap 200 / kept 16 / dropped 0 |
| 执行 | claimed 24 / **done 24 / failed 0 / not_claimed 0** | claimed 16 / **done 16 / failed 0 / not_claimed 0** |

`maintenance_claim` 40 行**全部** `state=done`，`claimed_at` 两组：
`14:03` × 24、`14:41` × 16。**两组之和 40 = Run1 + Run2，每一格只做过一次** ——
`dedupe_key` 把场景版本、主体、aspect、周期都编进去，重复 tick 会落回同一格。

`source_deltas` 逐格 `{new_source_gaps: 0, new_venues: null, unmeasured: ["new_venues"],
measured: false}`：`new_venues` 恒为 `null`（未测），**不是 0**（测了没有）——
这一版读本地记录、不联网，而真实场地新增只可能从一次新检索里出现。

**前后对照：**

| | 研究前 | Run1 后 | Run2 后 |
|---|---|---|---|
| `freshness.by_status` | `{current: 20, due: 0, never_observed: 120}` | `{current: 20, due: 0, never_observed: 120}` | `{current: 20, due: 0, never_observed: 120}` |
| `due_count` | 0 | 0 | 0 |
| `ticks` | — | — | `{claims: 40, done: 40, by_state: {done: 40}, done_by_aspect: {source_coverage: 40}}` |

`cells` 140 = 20 账号 × 7 个 aspect；`out_of_scope` 120 是本次部署声明的刷新面之外的
那些格，**在认领之前**就被滤掉（不是「没做」，是「不是我的」）。

**一处必须写下来的计数错位（它不是缺陷，但会被读成缺陷）：**
`observation` 只有 **36 行**，而 `maintenance_claim` 是 **40 行**。差的 4 条来自 Run1：
4 个账号的 `source_coverage` 同时有 `never_observed` 与 `change_signalled` **两格**
（周期不同），于是它们被认领两次；而 `observation` 的行键是 `(subject, aspect)` ——
一个 `(账号, source_coverage)` 只有一行。所以 `observation` 36 = 20（Run1 去重后）
+ 16（Run2）。**「40 格做过」与「36 行观测」都是对的读数，它们回答的不是同一个问题。**

### C.6 Jev 真的被下游消费了

`decision_record` **57 行**，全部 `provider=jev` / `model=jev-1.13.0` /
`outcome=answered` / `mode=assist`。

「被消费」不是按数量判的。判据是**能追到具体那一条**：

`account_venue_relation` 13 行里，**10 行**满足三件事同时成立 ——

1. 关系的 `evidence_ids` 与某条决策的 `evidence_refs` **集合完全相同**（且非空）；
2. 该决策选中的答案**就是**那条关系的值；
3. 那条决策带 `record_id` 与 `case_id`。

例（逐字取自审计读数）：

| account_id | relation | 决策 `record_id` | `case_id` | 置信 |
|---|---|---|---|---|
| `acct-7c28d05d887e3256` | `booking_control` | `b1ef23464c08c4160f2298406cf46552` | `…:responsibility_relation#f87f38f3accb455b` | 0.42 |
| `acct-aa1fcb74ffa2f891` | `operates` | `f833842d6b4a19644b1ab64e8abf26a4` | `…:responsibility_relation#d0d072414912f7fb` | 0.20 |
| `acct-e3bf1ae79a8e419b` | `operates` | `d6255610d2b4b7c0d697c11a11337fcb` | `…:responsibility_relation#4c3b9b97ee883c47` | 0.78 |
| `acct-32d253deeca77858` | `owns` | `dfb1c2abe604ad571fd83b49a76968ee` | `…:responsibility_relation#2a9faf60292a880d` | 0.48 |
| …（另有 6 条同类） | | | | |

**`decision_record.work_item_id` 全部为 `NULL`** —— 所以这条判据只能走**证据集合 +
答案取值**，不能走外键。这一点如实写下来：判据更弱，而它是这一版**唯一**能成立的那条。

**拒收的分配也没有被填成绿灯。** 关系 13 条、被消费 10 条，差的 3 条**没有被解释成
「Jev 判了别的」**：它们的证据集合与任何一条决策都对不上，于是它们在这条判据下
就是**不可判定**，不折算。

### C.7 Draft：0，而且理由是唯一的

```
routes_considered: 22
plan: {"planned": 0, "skipped": 22, "skipped_reasons": {"等级不是关键联系人": 22}}
drafts: []
receipts: []
send_authorized: false
```

**`Draft count = 0`。** 唯一记录在案的理由是**全部 22 条路线都不是关键联系人（A/B）**。
`send_authorized: false` 没有被削弱，`research_only: true` 逐条成立。

---

## D. MCP 用户链路（§11）

**32 个工具**对 controller 可见，工具名由产品自己的目录给出（不是本文件抄的）：

```
add_scene_version, control_job, control_scene, create_scene, draft_outreach,
get_account, get_capabilities, get_decision, get_evidence, get_job,
get_maintenance_plan, get_ops_report, get_report, get_scene, get_wave,
import_motherpool, list_account_pool, list_accounts, list_assets, list_handoffs,
list_responsibilities, list_scenes, prepare_wave, promote_receipt, put_claim,
put_family, put_writing_policy, remember_accounts, remember_responsibility,
run_discovery, run_maintenance, submit_research
```

**19 个命名步骤在干净客户端上走得通**（`run_discovery` 是提交 `fb3fd0a` 补上的那一步：
在此之前，链上第 3 步在 MCP 面上**没有工具**，只能从 CLI 走）。

**「只通过一个 MarketOps MCP」这一条要分两句说，第二句是限制：**

- **Discovery 到 Draft，全部走 MCP** —— 场景、发现、受理、晋升、研究、证据、路线、
  维护、模板族、写作策略、草稿，没有一步需要 CLI、开发者脚本、手工 SQLite 或 SSH。
- **但维护与起草这两条链的「主体名单」不是 MCP 产生的。** `get_maintenance_plan`
  与 `draft_outreach` 的主体只从 `inputs/<name>.yaml` 来。
  **本轮的 `acc-final2-devon-20260925.yaml` 是人手抄的**（文件头逐字写着
  「产品链上没有『账号池 → 这份名单』的生产者」）。
  这是本轮**真实阻塞项第 4 条**，不是一句说明。

产品自己的文档措辞也照抄如下，不改：`draft_outreach` 回答里逐字写着
「起草完成不是允许发送。这一批要能出去，还要 `prepare_wave` 冻清单、再由一个
`authorize` 角色的主体登记一份授权。」

---

## E. 分层：eligible / excluded / hold-unknown（§10）

上一轮的问题是「25/25 unresolved」—— 三种结局一条都做不出来。本轮**三种都有了**，
但要注意它们量在**两层**上，混着读会得出错的结论。

### E.1 发现期准入（回执那一层）

```
by_status   {discovered: 20, irrelevant: 5}
by_reason   {unresolved_facts: 20, rule_fired: 5}
```

**5 条 `excluded` 逐条带已声明的排除项 id**（这是 UK 天然草那份冻结 SOP 的硬排除，
**有效**）：

| row_id | disposition | 命中 |
|---|---|---|
| `row-e97e74867653bfa0` | `excluded` | `three_g` |
| `row-76a5c411cb311be8` | `excluded` | `indoor` |
| `row-1ad53f632cb2fc03` | `excluded` | `indoor` |
| `row-ed5cab53487a4750` | `excluded` | `indoor` |
| `row-cbbbf94e678d6ef9` | `excluded` | `three_g` |

**20 条 `discovered` 的理由逐条是 `unresolved_facts`** —— 也就是**hold**，不是 eligible。
**发现期准入的 eligible 是 0。** 这不是缺陷本身，是 §2 那四格事实（`organisation_kind`
/ `venue_environment` / `surface_class` / `country`）在本语料的真实页面上**读不出确定值**
的直接后果：

| 轴 | 20 条 resolved 行上的读数 |
|---|---|
| `organisation_kind` | `school` 3 / `university` 2 / **`unknown` 15** |
| `venue_environment` | `outdoor` 8 / **`unknown` 12** |
| `surface_class` | **`unknown` 20** |
| `country` | `gb` 8 / **`unknown` 12** |

**`unknown` 是正式写法，不是空串** —— 它在规则方言里意味着「这一页没说」，于是那条
子句**不决定**，而不是**确定地不命中**。这正是 §2 要的那条承重不变量
（「未确认的事实不得决定任何子句」）。**读数难看，但它是真的读过之后的难看。**

### E.2 路线层（`contact_route`）

| 状态 | 数 | 定义（写下来，避免和上面那一层混读） |
|---|---|---|
| **eligible** | **4** | 研究过的账号里，**至少持有一条非 X 等级路线**的 |
| **excluded** | **5** | **回执期**被 SOP 硬排除的行（上面那张表） |
| **hold-unknown** | **16** | 研究过的账号里，**一条非 X 路线都没有**的 |

**20 个账号被研究过，其中 4 个产出了路线（22 条），16 个一条都没有。**

另有一处要如实标注：`market_account` 上**没有** `grade` 字段（20/20 为 `None`），
**账号本身没有 A/B/C/D/X 等级** —— 等级活在**路线**上。所以 E.2 的分层是**在路线层
算出来的**，不是账号自带的属性。把它读成「账号被分成三类」是错的读法。

---

## F. 本轮定位到的真实缺陷（只如实列出，本轮不追加开发）

### F.1 `Draft count = 0`：闸门在所有 stage 上生效，而它写的是「首次接触」

`marketops/application/outreach.py`：

```python
#: 取值来自 SOP v1 L125–L129：A/B 是「关键决策职能 + 真实公开业务邮箱 + 强绑定」，
#: C/D 是业务入口或通用入口。§6 逐字：「C/D 必须标为业务入口/通用入口，
#: **不得**与 A/B 混称关键联系人」。所以 C/D **不进**首次接触的批次
KEY_CONTACT_GRADES: frozenset[str] = frozenset({"A", "B"})
```

`plan_wave` 的 docstring 第 1 条逐字「**等级**：C/D 不进首次接触」。而第 371 行的
判据 `if route.grade.value not in KEY_CONTACT_GRADES` **不读 `stage`** ——
它对 `stage-1` 到 `stage-4` 一视同仁。本轮 `stage-1` 的 22 条路线全部落进这条闸门。

**这一条不是「等级定错了」**：本语料里根本没有 A/B/C 路线可定。见 F.2。

### F.2 SOP L121 那条域接缝：一把两头都真的闸门

**断点**：`application/inputs.py:344` 把 `official_domain`（**取回这一页的那台主机**）
变成 `BindingSubject.domain`，而 `domain/bind.py` 把它当成 L121
「同页出现的其他组织邮箱不得归属目标账户」的**权威**。产品自己在
`application/discovery_run.py:42` 逐字写着：

> `official_domain` 是**取回它的那台主机**，不是一次「这是它的官网」的判定。

**对照实验（`readings/91`，只读探针）**：同一批已结算主语、同一批页面，**只改
`domains`** 一处，其余输入逐位相同：

| 主语 | 路线 | 强绑定 |
|---|---|---|
| A 现实主语（域 = 取回主机） | **10** | **1** |
| B 空主语（域 = 未确认 ⇒ 该子句不决定） | **37** | **4** |

**这条接缝的代价（真实被拒收的自有地址）：**

| account_id | 锚点（取回主机） | 被拒收的自有地址 |
|---|---|---|
| `acct-8814ecbfb1d52298` | `facebook.com` | `info@example.co.uk` |
| `acct-45aa3dbed6bbada1` | `example.co.uk` | `facilities@example.co.uk`（`official_business_entry`） |
| `acct-5b9c6ec4982dc2ec` | `example.co.uk` | `treasurer@example.co.uk` |

**这条接缝的风险（拿掉锚点会把别人绑进来）：**

`acct-7c28d05d887e3256` 锚在 `example.co.uk`。拿掉锚点，会新绑上
`info@example.co.uk`、`info@example.co.uk`、`admin@example.co.uk`、
`admin@example.co.uk`、`info@example.co.uk` ——
正是 L121 要防的事。

**结论：不能拆闸门。正确的修法是给 `venue_asset.verified_official_domain` 一个真实
生产者。** 那个字段**已经存在**，而本轮 **20/20 全为空**、`domain_evidence_ids`
**20/20 全为空**、`domain_verdict` **20/20 是 `unknown`**；
`claimed_official_domain`（「上游说」）**20/20 有值**（`facebook.com` /
`client41.example.org` / `example.co.uk` …）。

顺带：这条接缝**违反**那条承重不变量 —— 它让一个**未确认的事实**（取回主机 ≠ 本组织域）
决定了一条子句（拒收）。

**下一轮能成为 C 级的入口本轮全部死在这里。** 例：`acct-45aa3dbed6bbada1` 的
`facilities@example.co.uk` 是 `official_business_entry`（业务入口，C 级的定义），被 L121 杀掉；
`acct-5b9c6ec4982dc2ec` 的 `treasurer@example.co.uk` 与账号同组织名，同样被杀。

### F.3 `contact_route` 是逐页事件流，不是联系人集合

没有 `(account_id, email)` 的 upsert 与等级归并：

```
acct-7c28d05d887e3256 | info@example.co.uk  →  3 行
   X  conflicting_evidence  evidence 3b0630b6a4ca26a6b5e34dc5864becd5
   X  conflicting_evidence  evidence 344fb6e00da0780be9fe643c31ea47dc
   D  rule_matched          evidence 0dd1246a3e3a5cff68e60ace11cd90d1
```

三行证据不同、`has_conflict` 不同、`first_seen_at` 差 37 秒。别的账号同样有
4 行、8 行、11 行落在一个 `(账号, 邮箱)` 上的。**同一个联系人被写成了多个联系人。**
这与 §1 关掉的那条「三条身份」缺陷是同一种形状，只是这次在路线上。

### F.4 roster 没有产品生产者

维护与起草两条链的主体只从 operator 手写的 `inputs/<name>.yaml` 来
（`application/inputs.py::account_subjects`），**没有任何产品生产者写它**。
本轮那份 20 行名单是**人手照抄** `market_account` 抄出来的，文件头逐字写着这件事。
`account_subjects` 之外没有第二条入口。

⇒ §11 的维护链与起草链，在拿到这份文件之前**一步都走不动**。

### F.5 `RouteProduction.rejected` 被算出来又丢掉

绑定这一趟把 `foreign_domain` / `entry_elsewhere` / `unbound_email` 三类拒收算了出来，
**没有任何面报它们的分布**。§14 要的那个负例，只能从应用层或只读探针复现
（本文件 F.2 那张表就是这么来的）。**一个算出来又丢掉的理由，在报告上等于不存在。**

### F.6 其余如实记录项（都不是本轮阻塞）

1. **`region="Devon"` 的召回有相当比例落在美国大学田径名录上**：20 条
   `official_domain` 里 11 条是美国或非场馆站（`client41.example.org` / `client36.example.org` /
   `client26.example.org` / `worldathletics.org` / `instagram.com` / `youtube.com` …）。
   §10 的 `out_of_region` 规则**在 `unchecked_rules` 里** —— 它没有生产者，
   所以这些行**没有被判**，而不是被判为合规。
2. **`organisation` 是页面标题，不是组织名。** 例：
   `acct-c0002e5744d265da` 的 `organisation` 是
   `Devon Harris Speaking Fee &amp; Booking Agent Contact`；
   `acct-7c28d05d887e3256` 的是一整段带 `&laquo;` 的标题。这会让下游一切按组织名
   匹配或去重的地方静默失效。
3. **`surface_class` 20/20 是 `unknown`**（见 E.1），于是 §10 要求的
   `artificial_surface_only` 这条排除**没有任何一条数据能触发** —— 它也在
   `unchecked_rules` 里。
4. **`facts_without_a_producer: [has_notes, surface_evidence_confirmed]`** ——
   两个事实没有生产者。产品自己的准入读数里逐字列着。
5. **`unchecked_rules: [artificial_surface_only, out_of_region, venue_is_indoor,
   venue_is_muga]`** —— 四条规则没被判。「没判」与「判了通过」在这份读数里是
   **两件不同的事**，不能互相折算。
6. **`evidence` 的粒度是字面块，于是「1230」这个数字很容易被读成「抓了 1230 页」。**
   真实是 **270 个 URL / 284 份内容**，其中一份（`client22.example.org/staff-directory`）
   一个页面就贡献了 **254 行**。不影响归属正确性（C.2 已验），但任何按
   `evidence` 行数做的成本或规模推算都会因此偏大一个量级。

---

## G. 拿不到的那个量（§15）

```
precision  value=null  status=unavailable  reason=no_independent_human_labels
recall     value=null  status=unavailable  reason=no_independent_human_labels
```

**本轮没有独立的人工标注者**，所以凡是要人给标签的指标一律记 `UNAVAILABLE`，
**不折算成百分比，不写「约 95% 准确」**。上面这些是**确定性审计**的读数 —— 它们
回答「链上哪一格有值、值是什么、谁写的」，不回答「判断得多准」。两件事，不合并。

**一处零分母没有被写成 0**：`no_route` 是 16/20（研究过但没有产出任何可走路线），
而**「路线本身对不对」的分母是 0 个独立标签**，所以它不是 0%，是 `UNAVAILABLE`。

---

## H. Remaining blockers

**`READY_FOR_REAL_USE: NO`。真实阻塞项五条，按影响排序：**

1. **`Draft count = 0`（§14 逐字要求 `> 0`）。** `KEY_CONTACT_GRADES = {A,B}` 的闸门
   在**所有 stage** 上生效，而它的注释与 docstring 写的都是「首次接触」。本语料 22 条
   路线全是 D/X。
2. **SOP L121 那条域接缝（F.2）。** 它按一个**不是「本组织域」的值**拒收真实路线
   （A 10 → B 37），而把那个值拿掉又会把同页的其他组织绑进来。**它是第 1 条的成因**：
   能成为 C 级的 `official_business_entry` 全部死在它这里。
3. **`contact_route` 无 `(account_id, email)` upsert 与等级归并（F.3）。** 同一个联系人
   落成多行、等级与 `has_conflict` 各不相同。在这个修好之前，任何按路线做的计数
   （包括本文件的 22 与 `D 18 / X 4`）都在数**事件**，不是在数**联系人**。
4. **roster 没有产品生产者（F.4）。** 维护链与起草链在拿到一份人手抄的
   `inputs/<name>.yaml` 之前一步都走不动。
5. **`RouteProduction.rejected` 没有面（F.5）。** 三类拒收算出来又丢掉。

**这五条都不是** Laya / Docker / SMTP / Feishu / ServerOS —— 那些按本轮范围是
`DEFERRED`（缺席 = 推迟，不是失败），不计为 Research v1 的阻塞。

---

## I. Deferred（`DEFERRED`，不计为 FAIL）

Laya（`MARKETOPS_DECISION_PROVIDER=jev` 是当前的生产 DecisionProvider）、
ServerOS、Docker / Kubernetes、SMTP、Feishu、真实发送、跟进、回复自动化、CRM、UI、
Web 仪表盘、新调度平台、Postgres、Redis、Kafka、Celery。

---

## J. 交付

| 项 | 值 |
|---|---|
| **被测构建**（读数全部取自它） | `e919d989583235ceb30cd50bb41b3bee25c324ff` |
| **CI 读数所属的 revision** | `bfebb1f08d1d2333cc41b103647f45e463bfcb79`（= 被测构建 + 本文件） |
| 分支 | `marketops/product-loop-20260923` |
| PR | #6（`OPEN` / `MERGEABLE` / `CLEAN`，不自动合并到 `main`） |
| 基线 | `6ad5c6303ce211976ba373135e196dceb5c87318` |
| 远端 `main` 与本分支的关系 | `origin/main` = `9347b566a65f3c3081059432ea52dd3199260d9d`，**落后基线 77 个提交**（是基线的祖先）。调和结果是**没有需要并入的上游改动** |
| 全量套件（工作树） | `4358 passed, 3 skipped in 219.79s`，`rc=0` |
| 全量套件（干净克隆 `.[dev]`） | `4346 passed, 15 skipped in 219.01s`，`rc=0` |
| `ruff check .` | `All checks passed!`（工作树与干净克隆各一次） |
| 契约重生成 | 与 committed **逐字节一致**（`git status --porcelain contracts/` 为空） |
| 读数根 | `artifacts/marketops-acc-final/readings/`（`20`–`99`，含 `90` 审计、`91` 接缝对照、`92` 套件、`94` 终态） |
| 数据根 | `artifacts/marketops-acc-final/run2`（隔离；端口 8797） |

**本文件不改任何既有验收报告的任何一行，也不改 `docs/CURRENT_STATE.md`。**
`docs/RESEARCH_V1_ACCEPTANCE_2026-09-24.md`、`docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`、
`docs/MVP_ACCEPTANCE_REPORT.md` 与 `docs/MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md`
全部保持原样：旧 FAIL 仍然是旧 FAIL。
