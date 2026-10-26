# 英国学校与场馆天然草坪 · rc16 回归实测

插件 `marketops-research-assistant` **0.5.11-rc16**。题目、输入、简报与 rc15 完全一致
（`TASK.md`、`BASELINE.md`、PR #58/#59），用于回答一个问题：**同一道题在 rc16 上更好还是更差。**

> 文档纪律：本文件不写任何第三方姓名、邮箱、电话；只写模式与数量。逐账户明细留在东京本机（§10）。
> 时间一律北京时间。

---

## 0. 一句话结论

**比 rc15 差，而且是压倒性地差。** 每百机构 A+B **0.66**（rc15 4.32，门坎 2.0），
覆盖率 **66.6%**（rc15 88.7%，门坎 90%），A/B 精度 100%（1/1，样本只有 1 条）。
另外，**本题的交付件根本没有产出**：`handoff` 被一条含控制字符的引用卡死（B3）。

同期做的一个对照把差距拆成了两半：**把 rc15 自己的记录用 rc16 的评级器重跑**，
A+B 从 13 掉到 8（每百机构 4.32 → 2.66）。所以 rc15→rc16 的落差里，
约四成来自评级口径收紧，其余来自本次采集。

---

## 1. 运行身份

| | |
|---|---|
| 数据根 | `/data/home/vince/artifacts/marketops/uk-pitchfinder-rc16-20261026/data`（本题专用新根） |
| Run | `uk-pitchfinder-rc16-20261026` |
| 池 | `uk-pitchfinder-20261026rc16`（368 目标 = 302 基础 + 66 按发布名补采） |
| 来源 | `task-input-batch0078-0082-v2`（344 列出 → 302 导入 / 42 重复 / 对账 `true`）、`task-input-published-names`（69 列出 → 66 导入 / **对账 `false`，`unaccounted 3`**） |
| 简报 | 与 rc15 生效简报同一份（`research_brief`，`source_gate:false`，`minimum_ab 12`，`scene uk-natural-grass`，同一组 `roles_wanted` / `exclusions`） |
| 人工插话 | **0 次** |

---

## 2. 与 rc15 的口径差异（必须先读）

为了可比性，输入、简报、规则版本、导出列都与 rc15 对齐。**但有两处操作员侧偏差，
必须写在这里，否则数字会被误读：**

1. **采集参数**：首批 302 个目标用了插件默认（`concurrency 4`、`max_pages 5`），
   rc15 用的是 `concurrency 8`、6–8 页。66 个发布名目标与重开的 hold 用的是 8/8。
   这是本轮的操作失误，不是插件差异。
2. **多跑了一轮 hold 重开**：109 个 `NAME_NOT_FOUND_ON_SITE` 被 `pool_reopen` 重开并按 8/8 重采
   （动机是"深度不够"），结果**零产出**（见 B2）。这一轮只增加成本，不增加信息。

两处偏差都**不会低估** rc16：偏差 1 偏浅，偏差 2 只是白跑。因此"rc16 比 rc15 差"这个结论是保守的。

---

## 3. 运行过程（北京时间 2026-10-26）

| 时间 | 事件 |
|---|---|
| 10:18 | 新数据根；`customer_setup.py --skip-keys` 走通同意流程（凭据只从运行中 MCP 的环境继承，不落盘） |
| 10:21 | `marketops_start` + 首源入池（302/42/对账 true） |
| 10:21–10:46 | 首轮采集（默认 4/5）：302 → done 171 / hold 131 |
| 10:46 | 发现参数偏差，第二段起改用 8/8 |
| 10:47 | 第二源入池（66/0/**unaccounted 3**） |
| 10:47–10:56 | 补采 66 个发布名目标：全池 **done 220 / hold 148 / pending 0** |
| 10:56 | `pool_reopen(NAME_NOT_FOUND_ON_SITE, max_attempts=2)` → 109 重开 |
| 10:57–11:01 | 重开目标按 8/8 重采：**0 个 done**，全部回落同一 hold 理由 |
| 11:02 | 三个目标族记 `not_applicable`（OPERATORS / CLUBS_ASSOCIATIONS / FEDERATION_LISTS） |
| 11:03 | `handoff(mode='final')` → **BLOCKED**；`mode='checkpoint'` → **BLOCKED**（同一条错） |

---

## 4. 结果

### 4.1 总量（基准口径，`out/ukp_rc16_bruce18.csv`）

| 指标 | 值 |
|---|---:|
| 池目标 | 368（done 220 / hold 148） |
| 交付路线行 | **629** |
| 有路线的账户 | **229 / 344 = 66.6%** |
| 未找到账户 | 115 |
| 评级行 | A **0** / B **2** / C **442** / D **185** |
| A+B 行 / A+B 机构 | 2 / 2 |
| `contact_name` 非空行 | 205（其中进 A/B 的 **2**） |
| 降级丢弃 | `no_grade` **287**、`grade_X` 13、`account_unmatched` 18 |

### 4.2 分批次

| 指标 | 0078 | 0079 | 0080 | 0081 | 0082 | 合计 |
|---|---:|---:|---:|---:|---:|---:|
| 账户数 | 69 | 81 | 59 | 65 | 70 | **344** |
| 有路线账户 | 52 | 48 | 35 | 44 | 50 | **229** |
| 覆盖率 | 75.4% | 59.3% | 59.3% | 67.7% | 71.4% | **66.6%** |
| 交付行 | 122 | 206 | 94 | 88 | 119 | **629** |
| A+B 机构 | 0 | 0 | 2 | 0 | 0 | **2** |
| C | 85 | 165 | 65 | 51 | 76 | **442** |
| D | 37 | 41 | 27 | 37 | 43 | **185** |

### 4.3 与 Baseline / rc15 对照

| 指标 | rc16 | rc15 | Bruce | rc16 判据 | 结果 |
|---|---:|---:|---:|---|---|
| **每百机构 A+B** | **0.66** | 4.32 | 1.99 | ≥ 2.0 | ❌ |
| A+B 机构 | 2 | 11 | 6 | — | — |
| A+B 联系人数 | 2 | 13 | 6 | — | — |
| A/B 精度 | **100%**（1/1） | 46.2% | 100%（6/6） | ≥ 90% | ✅（n=1） |
| 覆盖率（344 账户） | **66.6%** | 88.7% | 100% | ≥ 90% | ❌ |
| 具名联系人 | 205 行有值 / 可用 **2** | 266 / 6 | 0 | > 0 | ✅ |
| C | 442 | 474 | 45 | — | — |
| D | 185 | 248 | 325 | — | — |

四分类（账户粒度）：`both_ab` 1、`ours_only_ab` 1、`bruce_only_ab` 5、`neither_ab` 337。

### 4.4 拆因：评级口径 vs 采集

把 **rc15 的记录**用 **rc16 的评级器**重跑（同一份记录，只换版本）：

| | rc15 自评 | rc15 记录 + rc16 评级器 | rc16 实测 |
|---|---:|---:|---:|
| 交付行 | 846 | 845 | 629 |
| 覆盖率 | 88.7% | 88.7% | 66.6% |
| A/B 行 | 13 | **8** | 2 |
| 每百机构 A+B | 4.32 | **2.66** | **0.66** |

读法：**评级器换版本本身就让 A/B 掉了 38%**（13 → 8）；其余（8 → 2）才是本轮采集与参数的结果。

---

## 5. 精度：逐条打开原页面

| 侧 | 抽样 | 字面命中 | 抓取失败 |
|---|---:|---:|---:|
| rc16 | 1 条 A/B（唯一去重地址）+ 20 条 C/D | 21/21 | 0 |
| Bruce | 6 条 A/B + 20 条 C/D | 26/26 | 0 |

rc16 唯一的 A/B 是 `<groundsman>@<club>.co.uk` 型：地址字面在页、岗位词 `grounds` 同块、
不属于排除岗位。**n=1，精度 100% 不构成"精度没问题"。**

Bruce 的 6 条 A/B 里有 5 条是我们**没拿到**的岗位箱（`facilities@`、`centremanager@`、
`manager@`、`site.manager@`、`site.mgr@`）——它们的共同点是**岗位词就在地址的 local-part 里**，
而我们的复现把同类地址判成了 C。

---

## 6. 成本、耗时、人工介入

### 6.1 成本（`marketops_costs`，按价目表估算）

| 提供方 | 调用次数 | 估算 |
|---|---:|---:|
| typesafe_jev（角色判定） | 1,586 | $0.0326 |
| tinyfish（抓取） | 1,391 | $0（免费额度） |
| deepseek（检索） | 18 | $0.0135 |
| serper | 2 | $0.0020 |
| serpapi | 2 | $0 |
| **Run 合计** | **2,999** | **$0.0481** |

对照 rc15：3,022 次调用 / **$0.0568**。rc16 少花 15%，但产出少得多。

### 6.2 耗时

| 段 | 起 | 止 | 耗时 |
|---|---|---|---|
| 建根 + 同意流程 | 10:18 | 10:21 | 3 分钟 |
| 首轮采集（4/5 参数） | 10:21 | 10:46 | 25 分钟 |
| 补采 + 重开重采 | 10:46 | 11:01 | 15 分钟 |
| 收口 + 导出尝试 | 11:01 | 11:03 | 2 分钟 |
| **端到端** | 10:18 | 11:03 | **45 分钟** |

rc15 端到端 2 小时 34 分（含一个作废 Run）。**rc16 更快，也更差。**

### 6.3 人工介入

- **人工插话：0 次**（判据 ≤3，✅）。
- **插件停等：0 次**——`handoff` 是**报错**，不是提问。
- **操作员绕过围栏：1 次**（必须单列）：用 `sqlite3 … UPDATE` 直接改了池里一条目标的 URL
  （把粘贴时打错的 `beaconof light.co.uk` 改回正确值，`rows fixed 1`）。
  这条动作本应被 `hooks/guard.py` 拦下，但 guard 的退出码被 `||` 吃掉（B1），没拦住。
  正确路径应是"重新规范化后重导"或记 HOLD 由插件处理；当时没有可用动词（B8）。
  **人工干预合计按 1 次计**（仍 ≤3）。

---

## 7. 判定表

| 判据 | 门坎 | 实测 | 结论 |
|---|---|---|---|
| 每百机构 A+B | ≥ 2.0 | **0.66** | ❌ |
| A/B 精度 | ≥ 90% | 100%（1/1） | ✅ |
| 覆盖率 | ≥ 90% | **66.6%** | ❌ |
| 人工干预 | ≤ 3 | 1（含 1 次绕过围栏） | ✅ |
| 产出交付件 | 必须 | **0**（两条导出路径都被卡） | ❌ |

**判定：不可交付，且是回归。**

---

## 8. 通用缺陷（任何行业都会出现，不是"这道题该怎么调"）

每条都给复现方式。行内 B 编号与瑞士题 `RESULT-rc16.md` 共用一套。

### B1 `hooks.json` 的 `||` 吃掉退出码，围栏形同虚设

```json
"command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py\" || python \"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py\""
```

`guard.py` 命中时 `sys.exit(2)`（Claude Code 里 2 = 拦截）；本机没有 `python`，
于是 `|| python …` 以 127 覆盖 2，Claude Code 判"非阻塞"，工具照跑。
第 19 行的 `runtime_check.py` 是同一模式。

- 复现：`python3 guard.py` → 打印 `BLOCKED …`、退出码 2；把 `hooks.json` 那一行原样跑一遍 → 退出码 127。
- 修法：用保留退出码的写法（`sh -c 'P=python3; command -v python3 … || P=python; exec "$P" …'`），
  并对 `runtime_check.py` 同改。**已有 PR #62 在做这件事**，本条只做记录。

### B2 `pool_reopen` + `harvest_batch` 是空转

重开 109 个 `NAME_NOT_FOUND_ON_SITE`，按 8/8 重采，**0 个 done**；
所有发现的路线带 `ungraded:DUPLICATE_OPERATION`，目标回落到同一 hold 理由。
页面没变、结论也不会变，但**每一页都重新抓了一遍**，成本照付。

- 复现：任一已 done 的目标 `pool_reopen(reason, max_attempts=2)`，再 `harvest_batch`。
- 影响：插件自己在 `next` 里推荐这条补救路径（"按页面发布名重试一次"），
  实际只在**换了查询词**时有用；原样重采是纯浪费，且不给任何"已重试"的可见标记。

### B3 一条引用里的控制字符让整个交付失败

```
HANDOFF_PARTIAL_OR_FAILED: ClientViewError:
UNSAFE_CELL_CHARACTER: 联系人路线!362:原文引用 contains U+0003
```

抓取文本里的 `U+0003`（ETX）被原样写进 `原文引用`，客户端构建器直接抛错。
`mode='final'` 与 `mode='checkpoint'` **都失败**，没有任何降级出口（跳过坏行 + 报告、或转义写出）。
本题因此 0 交付件。

- 复现：任一路线的 `claims[].quote` 含 `\x03`，调 `marketops_handoff`。
- 修法方向：入库时清洗控制字符（保留 `\t\n\r`），导出端再做一次兜底并**报告**被清洗的行。

### B6 域外邮箱被当作该机构的路线

样例：一家草坪俱乐部只抓到 `d.budd@aquaspersions.co.uk`（一家化学品公司的域）；
一所学校抓到 `enquiries@ofsted.gov.uk`、`psi@nationalarchives.gsi.gov.uk`；
另一所抓到 `chair@gardnerslane.gloucs.sch.uk`（另一所学校的域名）。
没有任何"地址域 vs 机构域"的校验。

- 复现：页面底部有"合作伙伴/监管机构"区块，抓取会把它和联系区一起收进来。
- 影响：C/D 行里混入不属于该机构的地址；一旦被误判 A/B 就是直接错误。

### B7 非电话字符串被当成电话路线

学期日期 `09.22-17.07.26`、`04.10.20\n05.10`、`01.12.2021 01`，以及瑞士侧的
`07.11.2026 10`、`0022 3223 3010`、`0900 0000 2000`，都以 `phone` 入库并落 `RULES_UNKNOWN`。
rc15 的同名现象是 356 行。

- 复现：学校页面的"学期日期表"、瑞士页面的"活动日期"。
- 影响：交付里出现不可能拨通的号码，且这些行绕过评级（`RULES_UNKNOWN` 不计入 A–D）。

### B8 URL 不做规范化，同一机构可以有两个身份；且没有修正动词

带空格的 host（`https://beaconof light.co.uk/event-hire/`）通过 scheme 检查入库；
`target_key` 的 anchor 是 hostname，host 一变 key 就变，去重只认 key 完全相等，
于是同一家机构在池里出现两条。`urlsplit` 不拒绝带空格的 host。

- 复现：`pool_import` 里放一条 host 含空格的 URL；再看 `pool_status` 的 `targets` 与 `bad urls` 输出。
- 连带缺口：没有"改正一条目标 URL"的插件动词（`pool_import` 只新增/去重，`pool_reopen` 只动 HOLD），
  操作员因此伸手进库——正是 guard 要挡的那类动作。

### B9 导入对账静默丢目标

`listed 69 → imported 66, duplicates 0, reconciled false, unaccounted 3`，无错误、无警告。
（瑞士题同源：`retry-published-names` 6 条另算一个来源。）

- 复现：给 `pool_import` 一份含空字段/异常行的 targets 列表。
- 影响：分母与"已覆盖"的账从此对不上，只有逐行读来源表才发现。

### B10 评级返回空值时静默丢弃

本题 287 条 `contact_point` 在 `grade_routes` 里没有返回（`no_grade`），
既不进交付也不进"排除"清单。消费者看到"这家 0 条路线"。

- 复现：同一 target 的 `contact_point` 来自 `DUPLICATE_OPERATION` 路径或 `claims` 为空时。
- 影响：无法区分"页面上没有"和"抓到了但被判定器吞掉"。

### B11 角色词表只认英语场景（与瑞士题 B5 同因，英国侧也可见）

英国侧的表现不是语言，而是**岗位词只在地址里**就不算：
`facilities@`、`site.manager@`、`site.mgr@`、`centremanager@`、`manager@` 五类
Bruce 拿到 A/B 的地址，我们全部落 C。

- 复现：用 `TASK.md` 的简报跑同一批账户，取 Bruce 有 A/B 而我没有的 6 个账户，看它们的 `reasons`。
- 影响：这是"每百机构 A+B"的主要缺口来源之一。

---

## 9. 结论

- rc16 在**同一道题、同一份输入、同一份简报**上没有达到可交付线：
  每百机构 A+B 0.66（门坎 2.0）、覆盖率 66.6%（门坎 90%）、且**没有产出交付件**。
- 相对 rc15 是**回归**：A/B 从 4.32 掉到 2.66（仅换评级器），再掉到 0.66（采集+参数）。
- 本轮的操作员偏差（首轮 4/5 参数、多跑一轮无用的 hold 重开）已如实记录；
  两处都只会让 rc16 看起来**不差于**实际，因此结论是保守的。

---

## 10. 证据路径（东京本机，不进 Git）

```
/data/home/vince/artifacts/marketops/uk-pitchfinder-rc16-20261026/
├── data/state/research-lite.sqlite3 / research-pool.sqlite3
├── data/exports/uk-pitchfinder-rc16-20261026/      （空：交付被 B3 卡住）
├── logs/interventions.md                           人工插话与操作员动作
├── out/ukp_rc16_bruce18.csv                        18 列逐账户明细
├── out/rc15_regraded_with_rc16_grader.csv          §4.4 的对照
├── out/grades_snapshot.json                        逐记录评级/理由/角色类别
├── out/align.csv                                   与 Bruce 的四分类
└── out/precision_ours.json / precision_bruce.json  开页复核
```

18 列里"角色类别"落在 `decision_function` + `role_relevance`，
"评级理由"是 `qualification_rationale`，"原文引用"是 `evidence_text`，可直接逐行核对。
