# MarketOps 产品环验收：2026-09-24 构建

这一份是**构建专属**的收据，回答一个问题，而且只回答这一个：

> 这个 MarketOps MCP 今天能不能从 Scene 出发，经来源、母池、责任主体、证据、增量
> 维护，一路跑到受控的外发准备与销售交接；跑到哪里为止，哪里还不行，证据是什么。

## 这份文件不是什么

- **不是**对 `docs/MVP_ACCEPTANCE_REPORT.md` 的修订。那一份是冻结记录，它上面每条
  FAIL 都是当时那个构建的真实读数。**本文件不改它的任何一行，也不把 FAIL 改成 PASS。**
- **不是** `docs/MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md` 的续篇。那一份是
  `168d69b` 的收据（A–E 逐项）。本文件是**另一个构建**（`e31f4e0`）上另一次测量的读数，
  被测对象从「研究链」扩到了「产品环」。两份收据并列存在，谁也不覆盖谁。
- **不是**部署证书。本文件的每一条 PASS 只在**这一台机器、这一次测量**上成立，并且
  每一条都附一个可以自己重跑的读数。

## 时间口径

测量发生在 **2026-09-23T20:21–20:33 UTC**，即 **2026-09-24 04:21–04:33 本地
（UTC+8）**。文件按本地日期命名，内文时间戳一律取 UTC（与库、账本、备份名一致），
以免「哪个先」在跨时区时读反。

---

## A. Build

| 项 | 值 |
|---|---|
| 仓库 | `ProfessorZhi/marketops` |
| 检出 | `/home/operator/workspace/worktrees/marketops/impl-20260923`（worktree，非 canonical checkout） |
| 分支 | `marketops/product-loop-20260923` |
| **被测构建（git SHA）** | `e31f4e0743214240e6473b57e7eb8f4fd44d4862` |
| **测量时工作树是否脏** | **否**。`git status --porcelain` 输出为空；`--check` 报 `"dirty": false`（读数见下） |
| 交付 SHA（`delivery_sha`） | **见本文件末尾「交付」一节** —— 本文件自身是交付的一部分，所以它记不下自己的 SHA。那一节写的是推送后由 `git ls-remote` 实测的值 |
| Python | 3.12.3 |
| 解释器 | `/home/operator/toolchains/marketops-mvp/bin/python` |
| mcp / httpx / pydantic | `2.2.0` / `0.28.1` / `2.13.5` |
| crawl4ai / playwright | `0.9.3` / `1.63.0` |
| laya / typesafe_sdk（Jev） | `0.3.4` / `0.7.1` |
| ruff / pytest | `0.15.22`（CI 只跑 `ruff check`，从不跑 `ruff format`）/ `9.1.1` |
| 传输 | Streamable HTTP，绑定 `http://127.0.0.1:8791/mcp`（字面量环回） |
| 认证 | bearer 令牌，`TokenRing` 格式 3；audience + expires_at 在传输层，roles + workspaces 在 `RoleGuard` |

`python -m marketops.interfaces.mcp --check`（在**本部署的环境变量下**执行）：

```json
{"ok": true,
 "build": {"source_tree": ".../impl-20260923",
           "revision": "e31f4e0743214240e6473b57e7eb8f4fd44d4862",
           "dirty": false, "errors": {}},
 "mcp": {"host": "127.0.0.1", "port": 8791, "auth_configured": true,
         "resource": "http://127.0.0.1:8791/mcp",
         "workspace": "uk-acme-natural-grass",
         "principals": ["claude-control", "claude-worker-1", "claude-worker-2"],
         "roles": ["controller", "researcher"]},
 "blocker": ""}
```

> **一处必须点名的读法**：`--check` 报的是**它自己那个进程的环境**，不是**跑着的那个
> 服务**的环境。上面这次调用在装载了 vault 凭据的 shell 里跑，所以 `get_capabilities`
> 里 serper / jev / crawl4ai / quay 都是 `configured: true`；同一条命令在**没装凭据的
> shell** 里会报 `serper_enabled: false`、`jev_enabled: false`，而那**不是**部署的
> 状态。把两处读数当成同一件事，会把「这个 shell 没带钥匙」读成「这个部署没有能力」。

---

## B. §8E 的三个问题，分开回答

`docs/MCP_SERVICE.md` §8E 要求这三个问题**分开**回答，且不得有一个绿灯盖住缺掉的
模型/浏览器路径。下面三节没有共同的结论句。

### B1. 研究执行了吗？——**执行了**

| 读数 | 值 | 来源 |
|---|---|---|
| 真实 Quay 驱动的派发 | 有，`driver start --kind worker` 起 anchor，selector 选任务，worker 走 launcher | `$R/quay/.quay/anchor.log` |
| 真实 worker 数（本轮） | 4 次租约、3 次结算 | `job_attempt` / `job_settlement` |
| **两个 worker 重叠的时间窗** | **116.0 秒**（见 §D） | `job_attempt` 的区间相交 |
| 真实 Serper 调用 | 有（`search_web` 返回真实 hits + `mirrors/serper/<scope>/<hash16>.json`） | 8791 服务日志 |
| 真实页面抓取 | 有（142 条 `fetch_document` 证据，`http` 117 / `browser` 25） | `evidence` 表 `payload.tier` |
| 真实 crawl4ai 初始化 | 有（日志中 `Crawl4AI 0.9.3` 多次 `[INIT]`） | 8791 服务日志 |
| 研究结论提交 | 有，3 件工作单元各自提交 candidates / observations / gaps | `job_settlement.payload` |

### B2. 证据 / 业务输出有效吗？——**证据有效；业务输出的有效性有边界，边界不在环里**

**有效的那一半**：142 条证据每一条都带 `source_url` / `content_sha256` / `fetched_at` /
`http_status` / `tier` / `literal_value`，且 `source_kind` 全部是 `fetched_html`
（**没有一条来自 SERP snippet** —— `BINDING_ELIGIBLE_SOURCES` 把 snippet 挡在门外，
这一轮实测该判据是活的）。三条结算各自的 `candidates[].evidence_refs` 都指向真实
evidence id，`gaps` 也逐条写了出来（例：「主题页面上没有任何一处点名场地」）。

**有边界的那一半，值得单独记**：本轮 3 件工作单元的被试主体来自**冻结的输入名册**
（`inputs/devon-probe-20260923.jsonl`），而那份名册里**本身就含非英国主体**
（University of Arizona 的体育部门、Arizona Cardinals）。也就是说，工作单元忠实地
研究了它被交给的东西，**scope 漂移发生在名册里，不在环里**。这条要写下来，因为它
很容易被读成「产品环抓错了地方」——不是，是输入就没筛干净。下一轮若要把发现链接上
这一环，名册的来源判据要先立起来。

**不写进本文件的那一半**：真实人名、职务、邮箱、电话。它们存在于
`$R/run/state/marketops.db` 的 `evidence.payload` 与 `job_settlement.payload` 里，
**只留在 artifacts 下，不进 Git**（SOP 与全局策略都要求如此）。本文件只按
`work_item_id` / `evidence_id` 引用它们。

### B3. 每个必需集成通过了吗？——**六个里有五个 PASS，一个 FAIL，两个 BLOCKED**

见 §E。**没有一个共同的绿灯**：`laya` 这一格是 `FAIL`（未接线），`mineru` 与
`feishu` 是 `BLOCKED`（缺依赖 / 缺授权），而 `serper` / `http` / `crawl4ai` / `quay` /
`jev` 五格各自有本轮的真实读数。§8E 那句「必须没有单一绿旗盖住缺失的模型路径」在
`get_capabilities` 的形状上就是成立的：它逐项报 `configured` / `runtime_ready` /
`wired` 三格，且 `last_live_verification` **恒为 `null`**（见 §E 末尾的读法）。

---

## C. 本轮是怎么跑的：隔离装置

§2 要求「只管理自己创建且归属明确的测试进程/服务；不杀其他人的服务，不做宽匹配
`pkill`」。8787 上那个服务**不是本脚本起的**，本轮全程没有碰它。隔离装置在
`/home/operator/artifacts/marketops-e6-33/`：

| 隔离面 | 值 | 为什么必须隔离 |
|---|---|---|
| 数据根 | `$R/run`（`state/marketops.db` + `inputs/` + `mirrors/` + `workers/`） | 与 8787 的 `artifacts/marketops-runtime` 完全分开 |
| 令牌环 | `$R/vault/marketops-mcp-tokens.json`（格式 3，3 个 principal） | 不轮换、不覆盖生产 vault 的令牌（§2 明令） |
| 端口 | **8791** | 8787 是别人的服务 |
| Quay 工作区 | `$R/quay`（含 `tasks/`、`.quay/`、`.claude/`、`.mcp.json` 的副本） | 不动共享的 `artifacts/marketops-mvp-quay` |
| 运行根 | `$R/run/workers` | 上一轮泄漏进活运行根的两个 scratch 目录已**移出**（不是删除），留在 `$R/leaked-into-live-root/` |

**工作区的三处改动**（都是「让它指向我这一份」的必要改动，逐条列出来）：

1. `bin/marketops-research-worker`：`VAULT_DIR`、`PYTHONPATH`、`MARKETOPS_WORKER_ROOT`
   三行改指 `$R`。**这是最关键的一处** —— 前两轮失败的直接原因就是这两行没改，
   于是 worker 读的是**生产 vault 里已被 9-23 的格式 3 环取代的旧令牌**，它的
   `claude` 委托方因此完全没有 `marketops` 工具面。
2. `.quay/profiles.yml` 的两条 `launcher:` 行改指 `$R/quay/bin/…`。**这一处与前一处是
   两个不同的东西**：改了 launcher 脚本但没改 profiles，等于没改（第一轮就是这样，
   anchor.log 里写着 `.../artifacts/marketops-mvp-quay/.quay/identities/worker-1.lock`）。
   本轮已用 Quay **自己的** `workerArgvForTaskAsync` 复核：`argv[0]` 与
   `--mcp-config` 的键都解析成期望值（`["marketops"]`）。
3. `.mcp.json` 与 `.claude/launch.settings.json`：URL 改 8791，`deny` 三条 `Read(//…)`
   前缀改指 `$R`。

---

## D. §8B 明文要求的那一条：两个 worker **重叠**

§8B 逐字：「Demonstrate at least two independent workers overlapping and no duplicate
expensive work.」本轮的读数：

```
claude-worker-1  39776e281315  state=settled  leased=2026-09-23T20:26:35.644493+00:00 settled=2026-09-23T20:29:33.181311+00:00
claude-worker-2  a2ce22fb7d1b  state=settled  leased=2026-09-23T20:27:37.178118+00:00 settled=2026-09-23T20:29:46.171951+00:00
```

两条 **`[leased_at, settled_at)` 相交 116.0 秒**，且两条都**已结算**（不是拿「还在跑」
当重叠）。取证脚本在 `$R/forensics/overlap-3.3.txt`，判据不是「派发了几个任务」——
那是任务数——而是区间相交，并同时记录那一刻两个 `research_worker` 进程都在。

**「没有重复的昂贵工作」**：两件工作单元 id 不同（`39776e281315…` 与 `a2ce22fb7d1b…`），
两条结算各自挂各自的 `evidence_refs`，**没有一个 evidence id 同时出现在两份结算里**。
每件工作单元一个 `attempt_id`，无重复 attempt。

> **这一条为什么之前一直不成立**：2026-09-22/23 的每一轮 `job_attempt` 都是
> 「worker-1 租 → worker-1 结算 → worker-2 租」，**严格顺序**，从未相交。重叠要能
> 发生，需要 **Quay 工作区**里的 `plugin/scripts/drivers.yml`（相对工作区根解析，
不是仓库里的文件）声明 `kinds.worker.cap >= 2`，也需要 ready
> 池里同时有 ≥ 2 个可派发的任务 —— 本轮前者是 2，后者池里有 25 个从未尝试的
> `MOPS-30c2bc41-*`。**cap 与研究身份数是一对**：cap 超过身份数时，第 cap 个 worker
> 会以退出码 2 响亮地退出，而不是共享身份。

---

## D2. 结算结果回传 Quay 看板：本轮实测收敛

`plugin/scripts/drivers.yml` 的 `reconcile_interval_secs: 30` 那段注释声称：worker
正常 `submit_findings` 之后，`board_sync` 必须**在 worker 退出前**把 Quay task 收敛掉。
本轮把它当判据来读（**file mtime** 对 **settlement 时刻**，本地时间 UTC+8）：

| Quay task | 结算时刻（UTC） | 看板翻成 `done` 的时刻（本地） | 差 |
|---|---|---|---|
| `MOPS-30c2bc41-39776e28` | 20:29:33.181 | 04:29:33 | < 1 秒 |
| `MOPS-30c2bc41-a2ce22fb` | 20:29:46.172 | 04:29:46 | < 1 秒 |
| `MOPS-30c2bc41-0feebeb3` | 20:32:19.571 | 04:32:20 | < 1 秒 |

三次结算对应三次 `ready → done`，池子分布从 `done: 4 / ready: 25` 变成
`done: 7 / ready: 21`。**不是靠 30 秒的兜底轮**收敛的 —— 差额远小于 `reconcile_interval_secs`。

**反面同样有读数**：第一件工作单元（被 schema v1 挡住的那一件，`MOPS-30c2bc41-0251ffd9`）
**没有**被写成 `done`，它落在 **`needs-human`**。这比「收敛成功」更能说明这条路的判据
是活的：一个**诚实报告 blocked** 的 worker，其任务不会被读成一次成功。

> 对本轮之前的状态：5 个任务恒为 `todo`、`transition` 端口零调用点 —— 那是这条链
> **没接线**时的读数。现在它是通的，并且**两种结局各有实测**（收敛成 `done`，
> 或诚实阻断成 `needs-human`）。

---

## E. §8C 六组件逐条

逐个从**运行中的服务**用 `get_capabilities` 读回（不用文档里的说法）：

| # | 组件 | configured | runtime_ready | wired | 本轮实测 | 判 |
|---|---|---|---|---|---|---|
| 1 | **Serper** | ✅ | ✅ | ✅ | 真实检索：`search_web` 返回真实 hits 并落 `mirrors/serper/<scope>/<hash16>.json`；额度从**冻结 profile** 读（`ceiling: 20`），不从调用方参数读 | **PASS** |
| 2 | **Crawl4AI 渲染** | ✅ | ✅ | ✅ | 25 条 `tier=browser` 证据；日志中 `Crawl4AI 0.9.3` `[INIT]` | **PASS** |
| 3 | **Crawl4AI 有界多页遍历** | ✅ | ✅ | ✅ | 同上（`crawl_site` 与 `fetch_document` 共用同一个升级入口） | **PASS** |
| 4 | **Jev 活体推理** | ✅ | ✅ | ✅ | **8 条 `decision_record`，`provider` 全部是 `jev`**，`model=jev-1.13.0`、`checkpoint=jev-latest`、`mode=assist`；经 `get_decision` 逐键读回 | **PASS** |
| 5 | **Laya 真实 checkpoint 推理** | ✅ | ✅ | ❌ | **未接线**：`get_capabilities` 逐字回 `本部署的辅助后端是 'jev'，decide 只咨询它。` | **FAIL** |
| 6 | **Quay 派发 + Claude Code 有界执行** | ✅ | ✅ | ✅ | anchor + selector + 4 次租约；`run.json` 记录 `mcp.strict=true`、`mcp.servers=["marketops"]`、`loaded_marketops.revision` | **PASS** |

另有两格不在 §8C 的六项里，但同表可读：

| 组件 | 状态 | 说明 |
|---|---|---|
| `mineru` | `runtime_ready: false` | `ModuleNotFoundError: No module named 'mineru'` —— 可选依赖，缺它时复杂/扫描件 PDF 走降级路径。**NOT_RUN**，不折算成 PASS |
| `feishu` | `outbound_capable: false` | 见 §F |

### 读 `get_capabilities` 时必须知道的一条

`last_live_verification` **恒为 `null`，包括本轮刚真跑过的四项**。那不是「没跑过」，
是**这一格没有记录源**：本构建没有任何一处记录过活体验收。服务自己的 `note` 里给了
理由，值得逐字引用：

> 一个恒绿的健康旗比没有旗更危险，它会让一次从未验证过的接线看起来是已验证的。

所以本轮的活体读数**不在能力表里**，就在这份文件里（§B1、§D、§B4）。两处都要读，
只读一处会得出相反结论。

---

## F. Jev / Laya，与外发链的隔离验收

### Jev —— 真跑，经**产品路径**

| 项 | 读数 |
|---|---|
| `decision_record` 行数 | 0 → **8**（本轮之前那张表是空的） |
| `provider` | `jev`（8/8） |
| 题面 | `page_triage` / `role_function` / `responsibility_relation` 三类都出现过 |
| 落盘时刻 | 2026-09-23T20:27:56 – 20:31:xx UTC |
| 读回 | `get_decision(record_id=…)` 逐键返回，含 `evidence_refs`、`input_sha256`、`latency_ms`、`outcome: answered` |
| **与前一轮的差别** | 上一次真实 Jev 调用是一个**独立脚本**（`artifacts/marketops-closeout/e6-live-head/jev-real.py`），`decision_record` 表当时是 **0 行** —— 也就是说它没有经过产品路径。本轮这 8 条是 worker 在派发的运行里经 `decide` 工具产生的，**落在同一张表上、可按 id 读回** |

**没有做影子对比**（§0）：`decide` 只以 `assist` 调用 Jev，影子是另一条路径且
没有 MCP 入口。

### Laya / Docker / ServerOS —— **DEFERRED**

按 §0，缺席是**推迟**，不是失败：本轮**没有**下载权重、没有改 ServerOS、没有做影子或
双模型对比。能力表里 laya 那一格的 `wired: false` 是**这一格的读数**，不是一次尝试
失败。要把它变成 PASS，需要的是一件独立授权的事，不是这里的补测。

### 外发 / 邮件链 —— 代码就位，**隔离验收通过，真实验收 BLOCKED**

§6 要求测试模式在**三个层面**阻止真实出站。三层各自的落点与执行器：

| 层 | 落点 | 执行器（判据真的会红的那个） |
|---|---|---|
| 收件人允许列表 | `security/egress.py::check_recipient` + `adapters/guarded_mail.py::GuardedMailTransport` | `tests/test_guarded_mail.py`：所有判据的对照面是**一个记账的假传输** —— 放行的要求它真的被调到，被拒的要求它**一次都没有**被调到。两个方向的错法各有判据（该拒的放过去 / 不该拒的换个说法拒掉） |
| 网络出口 | `security/network.py` 的 URL 判据 + 逐跳重定向检查 | `tests/test_security_egress.py` 等 |
| 凭据 | 部署配置里 `outbound_missing` 逐项点名；缺一项就构造不出外发路径 | `get_capabilities` 的 `outbound_capable: false` |

**运行中服务的实测读数**（这是「真实部署验收为什么阻塞」的答案，不是借口）：

```json
{"send_authorized": false,
 "outbound_capable": false,
 "outbound_missing": ["smtp_host","smtp_port","smtp_credentials",
                      "smtp_security","outbound_from_address","outbound_allowlist"]}
```

也就是说：**六个必需配置一个都没有**，外发路径在这一部署里**构造不出来**。
按 §6「缺少生产凭据只会阻塞真实部署验收，不得只留下 `NotImplementedError` 或
`interface_only`」—— 这里既不是 `NotImplementedError` 也不是 `interface_only`：
`adapters/smtp_mail.py` 是真的实现（STARTTLS 不宣告时**抛而不退回明文**、
`timeout` 显式不给 `None`），它只是在这个部署里**没有可用的凭据**而被关在门外。
判定：**BLOCKED**，解封条件是那六项配置由获授权的一方提供。

**授权门**：默认停在 `prepared_pending_authorization`；授权必须来自独立授权主体或
受管理的授权记录（`send_authorized=true`、模型写「我批准」、普通 controller 自行
授予角色，都不产生授权）。本轮**没有**做任何真实投递，也**没有**解除 `research_only`。

### 飞书 —— **BLOCKED**

`feishu_write_authorized: false`。写入路径存在（且读回 row id / version / count，
不匹配就算失败），但只在隔离测试里跑在传输替身上；**没有任何生产调用方把它接到
流水线里**，所以本地研究输出仍是唯一交付的输出。按 §9，这一条写成 BLOCKED 而不是
PASS。

---

## G. 本轮定位并修复的三个阻塞

三个都不是「理论上可能」，都是这一轮**实际卡住**的，且各有可重跑的判据。

### G1. **部署的启动器读的是已被取代的 vault，因此一个工具都拿不到**（已定性，未修）

`artifacts/marketops-mvp-quay/bin/marketops-research-worker` 的身份池遍历
`$VAULT_DIR/marketops-mcp-token-worker-<k>.txt`。那两个文件是 9-22 铸的，而 9-23 的
格式 3 环发布时**替换**了它们 —— 环里没有它们的摘要，服务因此一律回 401。
后果不是「权限不够」，而是**委托方完全没有 `marketops` 工具面**：前两轮 worker 的
最终报告逐字写着「marketops server 未被挂载或其进程未起来」。

**未修，且刻意不修**：修它要覆盖生产 vault 里的既有消费者令牌，而 §2 说「未获批准不
轮换已有生产消费者令牌」。本轮的处置是**另建一份 vault**（`$R/vault`），并实测
`$R/vault/marketops-mcp-token-worker-{1,2}.txt` 在 8791 上通过握手、拿到完整 8 个
研究工具。**部署的那个启动器今天仍然不能自己认证** —— 这是一条真实的、未关闭的缺口。

### G2. **状态库停在 schema v1，代码期望 v2**（本轮已修，在我的隔离根内）

worker 的原始报错逐字：`检索额度不可用：StoreError: 状态库 schema 版本是 1，代码期望 2`。
凡是读写该库的工具**全部失败**（`get_assignment` / `submit_findings` / `decide` /
`search_web` / `fetch_document` / `get_evidence`）。两个 worker 都**拒绝自己跑迁移**，
理由写得很对：「它改的是多个并发 Agent 共用的 run 级状态库，迁移不自动跑是刻意的」。

我的处置（在**自己的**数据根副本上，不是生产库）：
`python -m marketops.adapters.sqlite.migrate --db $R/run/state/marketops.db`，退出码 0，
`版本：1 → 2`，**盖章 100 行**（`evidence` 100 / `contact_route` 0 / `route_opportunity` 0），
证明来源 `artifact-evidence-id 50 行 + roster-subject 50 行`，**没有一行证不出来而被拒**。
迁移自带的备份落在 `run/state/marketops.db.v1-20260923T202504Z.bak`；我另存了一份
`$R/forensics/marketops.db.pre-migrate.bak`。

迁移的核心主张是**盖章而不是回填**：每一行的 `workspace` 都要先被证明属于本部署，
证不出来的整份拒绝并点名。所以它有一个可独立核对的读数 —— **迁移后全库 142 条证据
的 `workspace` 是否都等于本部署的 Scene**：

```
证据总数 142
workspace 分布: {'uk-acme-natural-grass': 142}
缺字段: 无        字段为空: 无
```

142 = 迁移盖章的 100 条 + 迁移之后新写的 42 条，**一条都没有落到别的 Scene 上**。
若迁移是「缺省 = 当前部署」的回填，这个读数不会有区别；区别在**证不出来的那些行**
被拒绝了，而本轮恰好没有这样的行（`proof_counts` 显示 100 条全部由
`artifact-evidence-id 50 + roster-subject 50` 证明）。换句话说：**这一轮验证了
「盖章」的正面，没有验证「拒绝」的那一面** —— 后者的执行器在
`tests/` 里，不在这次运行里。

> 这一条值得记进部署文档：**v1 的库在任何工具可用之前先要一次人工迁移**，而迁移是
> 刻意不自动的。一个照着 `docs/DEPLOYMENT.md` 起来的部署，如果数据根是从别处拷来的，
> 会以「所有工具都报一句没有 message 的 `Error executing tool`」的形式失败 —— 那个
> 症状与「服务没起来」几乎同形。唯一能读出真原因的是 `search_web`（它的错误文本不被
> 通用包装吞掉），这一点由 worker 自己发现并写进了报告。

### G3. 实时启动器的 `PYTHONPATH` 与服务器**不是同一个 revision**（本轮绕开，未修）

生产启动器把 `PYTHONPATH` 指向 `worktrees/marketops/mvp`（`3744262`），而 8787 上的
服务跑的是另一个 revision —— 两者相差 47 个提交。也就是说：**此前所有「真实 worker」
运行，worker 侧加载的 `marketops` 与服务器侧的并不是同一份代码。**

本轮的绕开方式是把它指到 `impl-20260923 @ e31f4e0`，于是 `run.json` 的
`loaded_marketops.revision` 与服务端一致。台账里那一格现在是可核对的读数，不再是一
个碰巧站着的 revision。

---

## H. 未完成与缺口（按模块 / 根因 / 完成条件）

| # | 模块 | 根因 | 完成条件 |
|---|---|---|---|
| 1 | 部署启动器的身份池 | vault 里的 worker 令牌已被格式 3 环取代（G1） | 获授权的一方轮换那两个生产消费者令牌，或把启动器改成从环里取 —— **本轮无权做** |
| 2 | 真实外发 / 邮件 | 六项 SMTP 配置一项都没有（§F） | 由获授权的一方提供 `smtp_host/port/credentials/security` + `outbound_from_address` + `outbound_allowlist`；随后按 §6 走准备→授权→投递 |
| 3 | 飞书写入 | `feishu_write_authorized=false`，且无生产调用方接线 | 授权 + 把写路径接进流水线 + 写后读回校验 |
| 4 | `laya` | 本部署的辅助后端被指定为 `jev` | 一件独立授权的事；本轮按 §0 是 **DEFERRED** |
| 5 | `mineru` | 模块未安装 | 装依赖；影响面是复杂/扫描件 PDF |
| 6 | 输入名册的 scope 判据 | 名册本身含非英国主体（§B2） | 发现链接上这一环之前，先给名册立来源判据 |
| 7 | `OutreachManifest` / `WaveAuthorization` 都**不带 `scene_version`** | 摘要**确实是逐项枚举**的（`wave_id`/`campaign_id`/`workspace`/`scene_id`/两个窗口/`valid_until`/`allowed_count`，加每条草稿的正文摘要、收件人、发送箱、阶段、证据 id），**但节奏不在被散列的内容里**：`waits` / `prior_touches` / `max_per_recipient` / `max_per_account` 由**派发那一刻读到的生效版本**决定（`adapters/mcp_server/outreach.py::_rules_and_scene` → `application/outreach.py::OutreachRules.from_declaration`），而这两个模型上都没有钉住版本的那一格 | 让清单与授权记下派发时用的 `scene_version`（或把节奏计入摘要）。否则一次 `add_scene_version` 改节奏之后，同一份**摘要没变**的清单会按新节奏发出去 —— 授权还在，发法变了 |
| 8 | 回信规则分类器 | **从不**产生 `HUMAN_REPLY` | 要么补上会产生它的规则，要么明说这一档只能由人工置入 |
| 9 | `WRITER_ID` | 没有任何东西固定它的内容 | 固定它，否则「哪一版的写作者产出这封信」不可回溯 |
| 10 | `SalesHandoff` | **没有 owner 字段**，而它的目录摘要声称含「负责人与状态」 | 补字段，或改摘要 —— 现在两者不符 |
| 11 | §3.4 的 `value=null` / `status=unavailable` / `reason` 三元组 | **不作为统一形态存在**。**已部分落地**：本轮修掉两处具体的「未知被写成 0」（§H1 发现 5），另有一族**不同**的做法是刻意的 —— `application/metrics.py` 与 `application/shadow.py` 只出**计数与分母**，不出比率（`shadow.py` 的模块 docstring 逐字论证「三个分母各不相同，一个 `accuracy: float` 会把它们全部藏起来」），`SearchBudget` 那一族把「预算用尽」/「上一轮已付过账而跳过」/「候选枯竭」拆成三个字段 | 要么立起这个形态，要么把「统一形态」这句话从文档里撤掉 —— 现在盘上是「几处具体的未知被正确处理」+「一族刻意的计数不比率」，**不是**一个统一形态 |

下表第 12–16 行是 **§H1 那次独立审查**带出来的，我自己逐条复核过机制（复核方式写在 §H1）。
它们没有被修，不是被驳回了：每一条都是**新的写路径**或**判据次序**的决定，属于进 PR 时
该由人讨论的内容，而不是一次收尾里该顺手改掉的。

| # | 模块 | 根因 | 完成条件 |
|---|---|---|---|
| 12 | 路线的状态在发送链上**从不前进** | `plan_wave` 的白名单 `INTAKE_STATUSES = {OBSERVED, QUEUED_CANDIDATE}` 声称「已经进过发送链的路线不再进批次」，而全仓**唯一**的生产写入点是发现期的 `application/pipeline.py:342` —— 发送链一次都不写 `ContactRoute.status` | 给发送链加上「这一条进到哪一段了」的写入，否则那条白名单在生产上拦不到人；现在唯一的刹车是 `max_per_recipient`（缺省 4） |
| 13 | `OutreachRules.max_per_account` **无执行器** | 有声明、有构造期校验、有回执投影，但全仓没有一处读它做判定（没有 `attempts_by_account` 这类查询，跨批次也不计数）；批内按 `seen_accounts` 去重，等于写死了「一批一条」 | 要么实现它，要么把它从「生效中的规则」那一格撤下来 —— 现在报告上它是一格生效的规则 |
| 14 | 「字面替代联系人」这条判据**到不了人眼前** | `_decide` 第 2 步（`if failure:`）排在**第 5 步（替代联系人）之前**，而生产接线用的分类器对「请找我的同事 x@y」这类正文必然弃权 ⇒ `hold_reason` 永远是弃权文本，`_alternate_contact` 那句话的**唯一**落点写不出来。**安全性未破**（处置仍是 `MANUAL_REVIEW`、仍不换人），丢的是「为什么该人看」这一格 | 把这条事实挂到弃权分支的理由上（或调整次序），并让判据用**生产分类器**跑那类正文 —— 现有两条测试注入的是生产分类器对这段正文**永远不会返回**的值 |
| 15 | `MessageState.BOUNCED` **没有写入者**；`stop_followup_for` **没有生产调用者** | 前者让 `check_prior_stages` 里「前序由退信确认」那一支成为死代码；后者的 docstring 自称是 §7「先保守暂停、再分类」的落地，而那条要求实际只由 `stopped_by_reply` 在计划/投递期间接兜住 | 补写入者 / 接线，或改掉那两句 docstring。同一族还有一句**已过期**的：`domain/enums.py:143` 的「`send_authorized` 恒为 False，因此 `SENT_STAGE_1` 及其之后的取值不可能出现」 |
| 16 | 业务判据住在 MCP 处理器里 | `adapters/mcp_server/outreach.py::_checked_facts` 逐条查库核对「这条事实引用的证据存在、且 `account_id` 就是它声称的那个账号」；应用层的 `generate_drafts` 只接受裸三元组、只判「引用的是不是本次给过的东西」 | 把这条判据移到 application 层。**今天是潜伏的**（全仓只有一个调用方），但第二个入口（CLI、worker、另一个工具面）一出现，就会带进一份没有核过归属的事实 |

---

## H1. §10 独立审查：逐项处置

§10 要求「完成后，在独立上下文中做一次代码/负面审查」。审查是在**另一个上下文**里起的
只读会话：不写文件、不起服务、不发网络请求、不写库，自己跑了一遍完整套件
（它跑的那次是 `3492 passed, 3 skipped / 133 s`，即**修之前**的树），另有若干次纯内存
探针把结论从「读出来像」变成「跑出来是」。

它交了 6 条发现，外加一份「查了、没找到问题的几类」与 5 条「我不确定的地方」。
**这 6 条我逐条自己复核过机制**，复核方式写在下表的「处置」列里 —— 没有一条是照抄结论。
一条被记录下来的发现如果没被复核过，它与一条没被发现的缺口在文件上长得一样。

| # | 严重度 | 位置 | 发现 | 处置 |
|---|---|---|---|---|
| 1 | **高** | `application/correspondence.py::_decide` 第 1 步 + `_link_by_reference`、`adapters/mail_capture_source.py` | 一封投递状态通知（DSN）的发件人是 `MAILER-DAEMON@…`、且不带 `In-Reply-To`，**两条关联路都走不通**；`_decide` 第 1 步又先于处置表，而 `_suppression` 取的收件人是 `plan.attempt.recipient` ⇒ §7 的「硬退信 → 按 SOP 抑制并保留证据」**在唯一的生产来源上一条都不产生**，那个死地址在下一批里照常被写进计划 | **已修**（`c1f5b10`）。复核方式：逐行读 `_link` / `_decide` / `_suppression`，并实测「修之前一封 5.1.1 落进 `manual_review`、`is_suppressed=False`、同一个人被重新计划」；修之后**真的走一遍**（发一封 → DSN 落进捕获目录 → 读回 → `ingest`），抑制写在**被退地址**上、且**没有**写在那台机器身上。新增 7 条判据，含软退不抑制 / 陌生地址不抑制 / 说不出退的是哪一封不抑制三条反向对照 |
| 2 | 中高 | `application/outreach.py:363,437` | 路线状态在发送链上**从不前进**，于是 `INTAKE_STATUSES` 那条「已经进过发送链的不再进批次」的白名单**在生产上拦不到人** | **记录为缺口**（#12）。复核方式：`command grep -rn put_route` —— 生产调用点只有一个（`application/pipeline.py:342`，发现期） |
| 3 | 中 | `application/outreach.py:119-127,383` | `OutreachRules.max_per_account` **没有执行器**：只被读、被校验、被投影、被报告，从不与任何东西比较 | **记录为缺口**（#13）。复核方式：把它的全部出现点读一遍 —— 判定只有 `max_per_recipient`（`:386,1414`）与 `max_per_wave`（`:389`）两处 |
| 4 | 中 | `application/correspondence.py:525` vs `:558` | 判定次序把「分类器弃权」排在「正文里给了替代联系人」之前，而生产分类器对这类正文**必然弃权** ⇒ `_alternate_contact` 那句话一次都到不了人眼前。**安全性未破**：处置仍是 `MANUAL_REVIEW`、仍不换人 | **记录为缺口**（#14）。复核方式：读 `_decide` 的 1–5 步次序与 `_kind_of` 的弃权分支 |
| 5 | 低 | `adapters/mcp_server/research.py:471,474`、`adapters/crawl4ai_fetcher.py:113` | 两处「未知被写成 0」 | **已修**（`59138a8`）。复核方式：两处都读过原文 —— 账本无行时 `spent`/`reserved` 写 `0` 而同格的 `ceiling`/`remaining` 写 `None`；状态码 `int(getattr(result, "status_code", 0) or 0)` 把「没有状态码」压成 `0`，而端口声明的是 `int \| None` |
| 6 | 低（今天潜伏） | `adapters/mcp_server/outreach.py:249-317` | 一条**业务判据**（这条事实引用的证据存在、且属于它声称的账号）住在 MCP 处理器里，而 §8 要求「处理器做协议映射，业务逻辑在 application 层」 | **记录为缺口**（#16）。复核方式：读 `_checked_facts` 与 `generate_drafts` 的入参形状 —— 后者只收裸三元组 |

它另外点名、我核实后并入缺口表的两条：**`MessageState.BOUNCED` 没有写入者**、
**`stop_followup_for` 没有生产调用者**（连同 `domain/enums.py:143` 一句已过期的陈述）——
合成 #15。

**它说它查过、没找到问题的几类**，写下来是因为「没找到」与「没查」在收据上不同：
授权闸门能否被绕过（它逐段读了从 `draft_outreach`/`prepare_wave` 到 `transport.send` 的整条路，
并核了 `_authorize_wave` 的 `authorized_by` 取自 `_require_principal()`、工具签名里没有可填
「谁批的」的那一格、`_AUTHORIZE` 是全目录唯一一条不含 controller 的工具）；测试模式的
**三层隔离都是代码而不是声明**；危险操作的工具面隔离（研究角色那一集合是从 `TOOL_CATALOG`
**算出来**的）；分层与「没有单一的 `execute(action, payload)`」；以及十处强声明 docstring 的抽样
（其中「时刻不是参数」「`approved_count`/`expires_at` 无缺省」「唯一一条不是 controller 的工具」
「`in_reply_to` 原样带过来、不在来源层翻译」逐条为真）。

**它自己标了 5 处不确定**，我按它列的顺序给了结论：第 1 条（DSN 在真实世界里会不会带
`In-Reply-To`）**不影响结论** —— 它自己就写了理由：`_link_by_reference` 查的是
`attempts_by_recipient(item.sender)`，而 DSN 的 `sender` 不是被退的那个收件人，这条路
结构上就接不上；本轮我加的第二条路走的是 `Final-Recipient` 那一格，与
`In-Reply-To` 在不在无关。第 2 条（`spent: 0` 是否可能是有意的）**以「已修」结案**：
同一 dict 里 `ceiling`/`remaining` 在同一情形下是 `None`，四格不一致本身就是判据。
第 3 条（#12 的量级）**按机制记录**：它证明的是「路线状态无人写、过滤器拦不到」，
不是「有人已经这么用过」。第 4、5 条是它自陈的阅读范围（`interfaces/cli.py` 没通读、
`ruff check` 没跑）与 #16 的「潜伏」定性 —— 前者不影响本文件的任何读数（本文件的读数
不引 `interfaces/cli.py`），`ruff check` 由 §H2 那一行自己跑，通过。

---

## H2. 测试与 CI（完整命令，通过 / 失败 / 跳过 / 待处理）

**两个 SHA，必须分开读**（下表三行是在**被测构建之后**那两个提交上跑的，因为 §H1 的
修复落在它们里面）：

- `e31f4e0743214240e6473b57e7eb8f4fd44d4862` —— **被测构建**：§B/§D/§F 里那些**活体**
  读数（真派发、真检索、真 Jev、真重叠、真结算回传）测的是它。
- `59138a82df4b919ef438ed8f9a989638cb0ded5d` —— **门禁树**：下表这三行测的是它，工作树里
  除本文件（未跟踪）外干净。它比被测构建多两个提交：`c1f5b10`（硬退信关联路）、
  `59138a8`（两处「未知被写成 0」）。

| 命令（在 `59138a8` 上） | 结果 |
|---|---|
| `/home/operator/toolchains/marketops-mvp/bin/python -m pytest -o addopts="" -q -rs` | **3499 passed, 3 skipped in 130.61s (0:02:10)**，退出码 0 |
| `/home/operator/.local/bin/ruff check .` | `All checks passed!`，退出码 0（CI 只跑这一条，从不跑 `ruff format`） |
| `python -m marketops.schema` 后 `git diff --exit-code contracts/` | **无差异** —— 重生成的契约与 committed 的逐字节一致 |

`3499 = 3492 + 7`：§H1 发现 1 的修复新增 7 条判据，**没有一条原有判据被删或放宽**
（改动前的树上是 3492 passed，改动后在测的是 3499 passed，两次都 0 失败）。

**跳过的那 3 条**（不是失败，也不折算成通过）：

```
SKIPPED [3] tests/test_evidence_store_contract.py:541:
  内存实现的一行就是一个模型实例，而模型上这一格必填 —— 缺这一格的行在这个实现上
  构造不出来（那一侧由 test_models_invariants.py 守）
```

**待处理**：无。本构建上没有任何 `xfail` / `pending` / 被跳过的判据被当作通过；
上表三行的退出码都是 0。

### 交付 ref 的独立复核（§12 的最后一条）

上表是在**工作树**里跑的。§12 还要求「在新的临时目录中检出**远端交付 ref**，并按文档运行
无凭据测试」。这一条单独跑过，读数与上表**不一样**，不一样的每一处都有解释：

```
检出目录   /home/operator/artifacts/marketops-e6-33/delivery-verify-6146d31
           （新目录，git init + fetch --depth 1 origin <分支> + checkout FETCH_HEAD）
检出的 SHA  6146d315a053a754c958cf05aadb539d0d78561b  ← 与 ls-remote 的 tip 逐字相等
环境        env 里 MARKETOPS_* 计数 = 0（不装载任何 vault 凭据）
pytest      3492 passed, 10 skipped in 133.51s (0:02:13)，退出码 0
ruff        All checks passed!，退出码 0
schema      python -m marketops.schema 后 git diff --exit-code contracts/ 无差异
```

**「3499/3」与「3492/10」是同一批判据，总数都是 3502。** 差的 7 条是**按设计会跳过的可选
上游交叉核对**，它们的探测顺序是 `MARKETOPS_RECORDS_ROOT` → `workspace 下 records/` →
`worktrees/records/*/`：

| 位置 | 条数 | 为什么在这个目录里跳过 |
|---|---|---|
| `tests/test_business_sources.py:486,503,531` | 3 | 找不到含 `projects/marketops/items/` 的 records 检出 |
| `tests/test_sources_snapshot.py:743,789,830,870` | 4 | 找不到 `upstream-records` 检出 |
| `tests/test_evidence_store_contract.py:541` | 3 | 两处都跳过（内存实现构造不出缺那一格的行） |

工作树在 `workspace/` 下，所以前两组探测得到 `workspace/records/`；交付复核目录在
`artifacts/` 下，探测不到 —— 于是这 7 条**转成跳过**。这是**「这一台机器上这一份检出能
看到什么」的差别，不是代码的差别**：同一份字节在工作树里 3499 passed，在交付复核目录里
3492 passed，而 `3492 + 7 = 3499`、`10 - 7 = 3`。

> 写下来是因为一个只报「绿了」的收据在这里会给出错误的确定感：一个**没有** records 检出的
> 环境读到的绿灯里少 7 条判据，而那 7 条恰好是「本仓的声明与上游 records 是否对得上」。
> 它们被跳过时**逐条打印了理由与怎么让它跑起来**，不是静默消失。

---

## I. 复现命令

```bash
R=/home/operator/artifacts/marketops-e6-33
VENV=/home/operator/toolchains/marketops-mvp/bin/python
W=/home/operator/workspace/worktrees/marketops/impl-20260923

# 1. 起隔离服务（我自己的数据根、我自己的环、端口 8791）
"$R/start-8791.sh" &

# 2. 握手：这个令牌看不看得见那个面（无副作用）
"$VENV" "$R/probe/handshake.py" \
  --token-file "$R/vault/marketops-mcp-token-worker-1.txt" \
  --url http://127.0.0.1:8791/mcp

# 3. 实调一个工具（唯一会吐真错的入口）
"$VENV" "$R/probe/calltool.py" \
  --token-file "$R/vault/marketops-mcp-token-worker-1.txt" \
  --url http://127.0.0.1:8791/mcp --tool get_capabilities --args '{}'

# 4. 起驱动（cap=2 ⇒ 两个 worker 可以重叠）
QUAY=/home/operator/.claude/plugins/cache/quay/quay/0.11.0/vendor/quay/dist/quay.js
export PATH=/home/operator/toolchains/node/current/bin:$PATH
node "$QUAY" driver start  --kind worker --root "$R/quay"
node "$QUAY" driver drain  --kind worker --root "$R/quay"   # 停派发；在途的让它跑完

# 5. 读重叠取证
"$VENV" - "$R" <<'PY'   # 判据脚本全文见 $R/forensics/overlap-3.3.txt
PY

# 6. 全套测试与风格（CI 只跑 ruff check）
cd "$W"
"$VENV" -m pytest -o addopts="" -q -rs
/home/operator/.local/bin/ruff check .
"$VENV" -m marketops.schema && git diff --exit-code contracts/
```

---

## J. 与既有报告的关系

| 文件 | 状态 |
|---|---|
| `docs/MVP_ACCEPTANCE_REPORT.md` | **原样**，未改一字 |
| `docs/MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md` | **原样**，未改一字 |
| `docs/CURRENT_STATE.md` | **原样**，未改一字 |
| `sources/` 原始业务快照 | **原样**，未改一字 |
| 本文件 | 新增。`e31f4e0` 上「产品环」那一段的读数 |

---

## 交付

本文件住在交付分支上，所以它记不下**自己**的 SHA 与**它所在提交**的 SHA。三个 SHA
各有各的所指，分开写：

| 项 | 值 | 怎么来的 |
|---|---|---|
| `tested_code_sha` | `e31f4e0743214240e6473b57e7eb8f4fd44d4862` | §B/§D/§F 里那些**活体**读数（真派发、真检索、真 Jev、真重叠、真结算回传）测的就是它，且测量时工作树干净 |
| 门禁树 SHA | `59138a82df4b919ef438ed8f9a989638cb0ded5d` | §H2 那三行测试跑在它上面 |
| `delivery_sha` | `c7f8c4cd146089d7eb1d957ed5d799ba5dbfbde6` | **推送之后由 `git ls-remote` 实测**的远端 ref tip：`refs/heads/marketops/product-loop-20260923`。它包含完整的产品环代码、§H1 的两处修复与这份文件 |

**这一格为什么指向一个「不是最终 tip」的 SHA**：把上面那一行写进文件的动作本身要让
tip 前进一格（一次纯文档提交），所以「记下自己的 SHA」在这里是一个会自我指涉的动作。
填的因此是**实测到的那一次值**，而不是一个滚动的当前值 —— 一个滚动的当前值永远填不对，
而一个实测过的值可以被任何人用 `git ls-remote` 复核它确实存在、且是 HEAD 的祖先。

**这一格之后还有纯文档提交**（记交付 SHA 的那一次、记 §H2「交付 ref 的独立复核」的那一次），
它们只动本文件、不动代码与契约。**最终 tip 由 PR 正文给出**，并用 `git ls-remote` 复核过 ——
「最终 tip」是一个滚动的值，写进文件的那一刻它就已经不是最终的了。

**推送方式**：`git push origin marketops/product-loop-20260923`，fast-forward
（`b7a7e15..c7f8c4c`），**没有**用 `--force`，**没有**动 `main`
（`origin/main = 9347b566` 是本分支的祖先，本分支按 §1/§12 走「命名实现分支 + 对 main 的 PR」，
不直接推 main）。
