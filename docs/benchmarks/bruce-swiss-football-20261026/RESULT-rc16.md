# 瑞士户外天然草足球场 · rc16 实测结果

插件 `marketops-research-assistant` **0.5.11-rc16**（本机 `/data/home/vince/.claude/plugins/cache/marketops/marketops-research-assistant/0.5.11-rc16/`）。
题目见同目录 `TASK.md`，基线见 `BASELINE.md`。本文只写结论与证据口径；
**逐账户/逐机构明细留在东京本机**，不进 Git（见 §10）。

> 文档纪律：本文件不写任何第三方姓名、邮箱、电话；只写模式（如 `groundsman@<club>.co.uk`）与数量。

---

## 0. 一句话结论

**三项主判据过了一项。** 每百机构 A+B **4.5**（Bruce 61.4）、覆盖率 **54.5%**（门坎 90%）、
A/B 精度 100%（2/2，但样本只有 2 条）。**判定：不可交付。**

原因不在"没找到"，而在**找到的东西进不了 A/B**：44 个账户有 24 个拿到了路线，
但德语岗位名（Platzwart / Platzmäher / Klubhauswart / Leiter Infrastruktur）
在 rc16 的场景词表里不成立，绝大多数被判 C。

---

## 1. 运行身份

| | |
|---|---|
| 数据根 | `/data/home/vince/artifacts/marketops/swiss-pitchfinder-rc16-20261026/data`（本题专用，新根） |
| Run | `swiss-pitchfinder-rc16-20261026` |
| 池 | `swiss-pitchfinder-20261026r2`（44 目标；首个未绑定 Run 的池 `swiss-pitchfinder-20261026` 留作证据，未使用） |
| 规则版本 | `bruce-swiss-effective-email-20260904` |
| 交付导出 | `data/exports/swiss-pitchfinder-rc16-20261026/swiss-handoff-final-1/`（`handoff(mode='final')` 成功） |
| 人工插话 | **0 次**（插件未向人提过任何问题） |

---

## 2. 输入与简报

44 个账户来自 `benchmarks/swiss-football/task_input.csv`（Bruce 语料中 16 个交付批次、
27 个城镇、31 个官网域名，含 St. Gallen 5 账户同站、Basel 4 账户同站、
`staatskalender.bs.ch` 5 账户同站的跨账户同域情形）。

简报一次性由 `marketops_start` 写入：`roles_wanted` 为场地/草坪/设施口径的德语与英语岗位，
`exclusions` 为教学、招生、财务、IT、人事、赞助、赛事编排等功能口与私人邮箱；
`minimum_targets=44`。**未做任何针对本题的特别调整**——没有改规则、没有加白名单、
没有替插件挑选账户。

---

## 3. 运行过程（北京时间 2026-10-26）

| 时间 | 事件 |
|---|---|
| 09:56 | 建数据根；`research_config` / `runtime_setup` / `spending.configure`（MONEY、RED_LINE 0.90、`public_web_fetch=true`、`paid_agent=false`） |
| 09:56 | `marketops_start`（简报 + `minimum_targets=44`） |
| 09:57 | 首池导入 44 目标——**未带 `run_id`**，池存在但未绑定 Run，`next` 反复要求 `IMPORT_NEXT_TARGET_FAMILY` |
| 09:59 | 新建 `…r2` 池，带 `run_id` 重导（44 导入 / 0 重复 / 对账 `true`） |
| 10:00–10:03 | 第一轮 own-site 采集：44 → **10 done / 32 hold**（`NAME_NOT_FOUND_ON_SITE` 23、`ROUTES_NOT_ADMITTED` 5、`SITE_UNREACHABLE` 4） |
| 10:04–10:05 | 输入侧修正：6 个账户按站点发布名重导（`retry-published-names-20261026`）并补采 |
| 10:07–10:12 | people 阶段：4 个 `account-researcher` 子代理并行处理 16 家俱乐部；市政/场地类 6 家由主线程处理 |
| 10:14 | 最后一次写入 |
| 10:16 | `handoff(mode='final')` → `swiss-handoff-final-1`（成功） |

> 子代理阶段的实际收获：它们在页面上**确实**找到了具名设施岗位（例如
> 「Leiter Infrastruktur + 个人邮箱」「Platzchef + 两个电话」「Materialwart + 邮箱」），
> 但这些记录进入交付时被重新评级，见 §4 与 §9 的 B4/B5。

---

## 4. 结果

### 4.1 插件交付口径（`本轮说明.csv`，即客户表实际内容）

| 指标 | 值 |
|---|---:|
| 实际组织候选数 | 16 |
| 实际联系渠道候选数 | 63 |
| 评级分布 | A **0** / B **2** / C 39 / D 15 / X 4 |
| A+B 去重人数 | **2** |
| 有 A+B 的组织数 | **1** |
| 具名关键岗位（无公开邮箱） | `contact-pack/named_people_no_email.csv` **0 行**（而运行内确实存在此类记录） |
| 准入评估 / 通过 / 排除 | 171 / 79 / 92 |
| 主要准入排除原因 | `ORGANIZATION_NOT_ADMITTED` 56、`ORGANIZATION_PROVENANCE_MISMATCH` 28、`SOURCE_TYPE_NOT_ADMISSIBLE:provider_extracted` 26、`EMAIL_NOT_IN_PRIMARY_MATERIAL` 16、`CONTACT_VALUE_NOT_IN_EXCERPT` 17 |
| 每 100 目标的 A+B 人数（插件自报） | 4.0（分母写的是 `finished pool targets 50`） |

### 4.2 基准口径（按 Bruce 的账户粒度展开，`out/swiss_rc16_bruce19.csv`）

| 指标 | 值 |
|---|---:|
| 交付路线行 | 164 |
| 有路线的账户 | **24 / 44 = 54.5%** |
| 评级行 | A 0 / B 2 / C 147 / D 15 |
| A+B 行 / A+B 机构 | 2 / 2 |
| `contact_name` 非空行 | 89 |
| 降级丢弃 | `grade_X` 4、`no_grade` 4、`account_unmatched` 4 |
| 未找到账户 | **20 / 44** |

> 两个口径的分母不同：交付口径按"组织"（16 家），基准口径按"账户"（44 个，跨账户同域会继承）。
> 二者都保留，因为 Bruce 的判据是账户粒度。

---

## 5. 与 Bruce 基线对照

| 指标 | rc16 | Bruce | 判据 | 结果 |
|---|---:|---:|---|---|
| **每百机构 A+B** | **4.5**（2/44） | **61.4**（27/44） | ≥ Bruce | ❌ |
| A+B 机构 | 2 | 27 | — | — |
| A+B 联系人数 | 2 | 42（A 16 + B 26 行） | — | — |
| 覆盖率（44 账户） | **54.5%** | 100%（44/44 有行） | ≥ 90% | ❌ |
| A/B 精度（开页复核） | 100%（2/2） | 100%（31/42 可机检；其余 11 条源自 PDF） | ≥ 90% 且 ≥ Bruce | ✅（n=2） |
| 具名联系人 | 89 行有值 / 其中 A+B **2** | 42 行 | > 0 | ✅ |
| C | 147 | 24 | — | — |
| D | 15 | 0 | — | — |
| 未找到 | 20 账户 | 0 账户 | — | — |

四分类（账户粒度）：`both_ab` 2、`ours_only_ab` 0、`bruce_only_ab` **25**、`neither_ab` 17。

**读法**：Bruce 在 27 个账户上拿到 A/B，我们只在他也拿到的那 2 个账户上重合。
25 个"他有我没有"里，多数不是"我们没找到那个邮箱"（不少邮箱在我们的 C 行里），
而是**同一个邮箱在我们这里被评成了 C**。这是判定口径问题，不是采集问题。

---

## 6. 精度：逐条打开原页面

四条检查（字面在页 / 岗位词同块 / 姓名-岗位-地址绑定 / 不属于排除岗位），
只从页面本身取证据，不用搜索片段。

| 侧 | 抽样 | 字面命中 | 抓取失败 |
|---|---:|---:|---:|
| rc16 | 2 条 A/B + 10 条 C/D | 12/12（其中 1 条现场抓取报 `ValueError`，本地镜像命中） | 1 |
| Bruce | 42 条 A/B | 31/42 可机检命中；11 条来自 PDF（`Adressliste.pdf`、`fcmg-adressen.pdf`），文本检查器不读 PDF，非"不成立" | 1（HTTPError） |

两条 A/B 的实际情形：
1. `<club-hauswart>@<club>.ch` —— 页面把「Clubhauswirt + 人名 + 邮箱」印在同一块，评级理由 `SAME_ADDRESS_FOR_SEVERAL_ROLES` 被误触发（见 B4）。
2. `<materialwart>@gmail.com` —— 页面同块有姓名与岗位，但落成 `LISTED_ON_ANOTHER_ORGANISATIONS_PAGE; NAME_ROLE_EMAIL_ONE_BLOCK`，地址是个人 gmail。

**精度 100% 但 n=2，不能当作"精度没问题"的证据。**

---

## 7. 成本、耗时、人工介入

### 7.1 成本

交付口径的账本没有写进客户端（`收费 Provider 额度与价格: UNKNOWN_NOT_IN_LITE_STORE`）。
运行内可读的调用量：4 个子代理共 18 次 provider 操作（15 次 `research_step` + 2 次 Serper + 1 次 `site_harvest`），
主线程另有 own-site 采集与 6 个目标的补采。**本项按未定价处理**，不打换算成美元。

### 7.2 耗时

| 段 | 起 | 止 | 耗时 |
|---|---|---|---|
| 建根 + 简报 + 两次入池 | 09:56 | 10:00 | 4 分钟 |
| 采集（own-site + 补采） | 10:00 | 10:07 | 7 分钟 |
| people 阶段（4 子代理 + 主线程） | 10:07 | 10:14 | 7 分钟 |
| 导出 | 10:16 | 10:16 | <1 分钟 |
| **端到端** | 09:56 | 10:16 | **20 分钟** |

### 7.3 人工介入

- **人工插话：0 次**（判据 ≤3，✅）。
- **插件停等：0 次**——本题 `handoff(mode='final')` 一次成功。
- **操作员动作：5 项**（新数据根、带 run_id 重入池、按发布名补 6 个账户、派子代理、
  一次路由写入重试），均记在 `artifacts/…/logs/interventions.md`。

---

## 8. 判定表

| 判据 | 门坎 | 实测 | 结论 |
|---|---|---|---|
| 精度 | ≥ Bruce 且 ≥ 90% | 100%（2/2） | ✅（样本过小） |
| 每百机构 A+B | ≥ Bruce（61.4） | **4.5** | ❌ |
| 覆盖率 | ≥ 90% | **54.5%** | ❌ |
| 人工干预 | ≤ 3 | 0 | ✅ |

**判定：不可交付。**

---

## 9. 差距原因（只写通用缺陷，不写本题该怎么调）

### B4 同一地址的评级取决于读到哪条重复记录

同一条路线在运行里有 3 条重复 `contact_point`，评级理由不同：
一条 `ENTRY_ROLE`，另外两条 `SAME_ADDRESS_FOR_SEVERAL_ROLES`。
`SAME_ADDRESS_FOR_SEVERAL_ROLES` 本是给"部门共用箱"的判据，却被个人邮箱触发——
因为同一地址被三条记录共享。

- 复现：同一页面同块找到「人名 + 岗位 + 邮箱」，重复写入 3 次（子代理重试会自然产生），
  再看 `grades_snapshot.json`：同一 `email` 三条记录的 `reasons` 不一致。
- 影响：任何一个按"地址"去重、取最好评级的消费者（含本仓库的转换脚本）都在赌记录顺序。

### B5 岗位词表只认英语，非英语场景整体塌到 C

`Leiter Infrastruktur`（基础设施负责人）被判 `ENTRY_ROLE`（"入口岗位"）；
`Platzmäher`（场地割草）、`Platzwart`（场地管理员）、`Klubhauswart`（会所管理员）、
`Kontakt`（场地联系栏）被判 `ROLE_NOT_IN_SCENE`。
场景 `scene_rules` 里给的德语词没有进入判定，`rules_source` 显示规则来自
`brief.scene_rules+roles_wanted`，但实际生效的是英语岗位词。

- 复现：德语页面把「Platzwart + 人名 + 邮箱」印在同一块，交给 `site_harvest`，
  结果是 `ROLE_NOT_IN_SCENE` → C。
- 影响：**任何非英语市场的 A/B 产出都会接近 0**，与页面质量无关。这是本题从
  61.4 掉到 4.5 的主因。

### B6 角色分类把管理岗判成"入口"

`Leiter Infrastruktur`（基础设施负责人 = 场地口的决策人）在两处都落 `role_class=entry`，
`decision_function` 随之写成 `routing_fallback`。英文语境下 `Head of Grounds` 不会这样判，
说明分类器对"`Leiter …` / `Leiterin …`"这类构词没有覆盖。

### B9 导入对账会静默丢目标

第二次导入 `listed 69 → imported 66`，`duplicates 0`，`reconciled: false`，`unaccounted 3`，
没有任何错误或警告——只有把来源表逐行读一遍才能发现少了 3 个。
（本题的 6 个发布名补采里也出现过同类，`reconciled` 字段是唯一线索。）

### B10 评级返回空值时静默丢弃

`grade_routes` 对 4 条 `contact_point` 返回空（`no_grade`），它们既不进交付也不进"排除"
清单。消费者看到的是"这家 0 条路线"，看不到"有 4 条被无声吃掉"。

### B12 交付导出无降级路径

本题导出成功；但同一 rc16 在英国题上因为一条引用里含控制字符导致
`handoff` 与 `checkpoint` 双双失败（见英国题 `RESULT-rc16.md` 的 B3）。
这里记的是同一件事的另一面：**没有"跳过坏行并报告"的降级出口**。

---

## 10. 证据路径（东京本机）

```
/data/home/vince/artifacts/marketops/swiss-pitchfinder-rc16-20261026/
├── data/state/research-lite.sqlite3        运行记录
├── data/state/research-pool.sqlite3        两个池（首个未绑定 Run）
├── data/exports/swiss-pitchfinder-rc16-20261026/
│   ├── swiss-handoff-final-1/              Research_Result.xlsx + Client_View.xlsx + 客户表 CSV
│   └── swiss-handoff-checkpoint-1/         中途快照
├── logs/brief_to_plugin.md                 交给插件的一段话简报
├── logs/interventions.md                   人工插话与操作员动作
├── out/swiss_rc16_bruce19.csv              基准口径逐账户明细（不进 Git）
├── out/grades_snapshot.json                逐记录评级、理由、岗位类别
├── out/align.csv                           与 Bruce 的四分类对照
└── out/precision_ours.json / precision_bruce.json   开页复核结果
```

语料与逐账户明细均为第三方信息，**只留本机，不进 Git**。
