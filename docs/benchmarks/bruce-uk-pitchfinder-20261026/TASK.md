# 英国天然草坪运动场／学校：公开联系人研究（Bruce 基线题）

这是一道从 Bruce（bruce.xu）真实工作记录里反向出的题。出题只规定**范围**与**判据**，
不规定做法：怎么找、找几轮、用什么工具，留给答题方。

| | |
|---|---|
| 族 | 英国｜天然草坪运动场／学校（UK Pitchfinder） |
| 子任务 | 5 个连续批次（`batch0078`–`batch0082`） |
| 规模 | 344 个账户／301 家机构 |
| 输入 | 每账户一条：机构名 + 官方站点 URL |
| 交付 | 逐账户的联系路线表（18 列，见 §6） |
| 基线 | 见同目录 `BASELINE.md` |
| 语料 | `marketops-session` 仓库 `corpus/codex`（Bruce 的 Codex 会话导出） |

---

## 1. 任务

给定五个批次共 **344 个账户**（**301 家机构**，同一机构可能有多个账户），
为每个账户在**公开网页**上找到可用的联系路线，按 Bruce 的分级契约打标，
并让每一条路线都带可复核的证据（引用原文 + 来源页 + 本地镜像路径）。

一个账户允许产出 0 到 N 条路线；同一机构的不同账户分开计。

## 2. 机构类型

- 英国中小学、独立学校、学院（state / independent / academy / special school）
- 使用天然草坪球场的学校与体育场馆运营方（含少量俱乐部、市属场馆运营页）
- 少量机构页会落在体育俱乐部、市议会场馆目录、场地方官网上

题面不预筛"有没有天然草坪"：给定输入里已经混有少量草坪待证实、室内/人工场页面，
答题方需要自己判断并排除（见 §7）。

## 3. 输入

东京本机文件（不入 Git）：

```text
/data/home/vince/artifacts/marketops/benchmarks/uk-pitchfinder/task_input.csv
```

列：`batch, account_id, organization, official_url`。
`account_id` 是 Bruce 的账户编号（`SR-xxxx` / `SR2-xxxx-xx` 两种形态），
`official_url` 是 Bruce 当年为这个账户定位到的官方页面。

> 为什么给 URL：Bruce 这套流水线的这个阶段，输入本来就是**已定位到官方页面的账户**
> （每批约 200 个页面已镜像）。给一个机构名让模型自己找官网是另一道题，
> 会把"找站"和"找人"混在一起，基线就没法逐账户对比。

## 4. 想要的岗位（roles_wanted）

Bruce 的 `decision_function` 枚举（照抄）：

| 值 | 含义 |
|---|---|
| `economic_buyer` | 能拍板预算的人 |
| `technical_decider` | 技术选型/评估的人 |
| `operational_owner` | 实际运营这套场地的人 |
| `procurement_gatekeeper` | 采购闸门 |
| `internal_champion` | 内部推动者（demo champion） |
| `routing_fallback` | 只能作为转达入口的官方业务口 |

本族的目标角色是**场地与设施口径**：Grounds / Estates / Facilities / Site / Operations
的负责人或团队口；其次是能可靠转达到这些岗位的官方业务入口。
教学、招生、财务、IT、人事等无关口一律排除。

## 5. 分级契约（照抄 Bruce，`fjd-email-role-qualification`）

### 5.1 证据层级 `evidence_tier`

| 值 | 判据 |
|---|---|
| `strict_named_role_email` | 同一官方记录里，**具名 + 岗位/部门 + 字面邮箱**三者绑定 |
| `role_bound_public_email` | 岗位/部门与字面邮箱在同一官方记录里显式绑定；不要求可安全抽取的姓名 |
| `observed_business_route` | 相关官方部门或业务信箱，字面存在且与场景相关，但不是具名直达路线 |
| `official_general_research_route` | 官方通用研究入口（Bruce 在本族实际使用的下限档） |
| `inferred_candidate` | 猜测模式、无绑定、非字面地址 → **不计入交付** |

### 5.2 角色优先级 `role_priority`

| 值 | 判据 |
|---|---|
| `A_key_role` | 经济买家、运营所有人、技术评估人、采购闸门、demo champion，且职责直接对口 |
| `B_related_operator` | 相关的设施、维护、场地、项目、设备、车队、现场负责人，能影响或执行用例 |
| `C_official_business_entry` | 相关官方部门入口，仅作路由兜底 |
| `D_official_general_research_entry` | 官方通用入口，只作研究资产，不构成触达路线 |
| `excluded` | 无关、无证据、推断、私人、畸形、no-reply、场景不符 |

A/B 是"可触达路线"，C/D 是"研究资产"，这条区分是本族全部回执都在强调的口径。

### 5.3 资格状态 `qualification_status`

`effective_research_email` / `official_business_entry` / `official_general_research_entry` /
`excluded` / `manual_review`

### 5.4 两条硬约束

- `research_only = TRUE`，`send_authorized = FALSE`：本族**全部**交付都是研究用途，
  Bruce 从未在这个族里直接发信。
- C/D 不得被描述为"A/B 可触达覆盖"。Bruce 的复盘里专门纠过这个口径：
  "58.33/100 是 C/D 研究资产除以镜像页，不能解读为 A/B 可触达覆盖"。

## 6. 交付格式

一张 CSV，列名照抄 Bruce 的 `qualified_email_routes.csv`：

```text
account_id,account_name,product_profile,email,contact_name,contact_title,department,
evidence_tier,role_priority,decision_function,role_relevance,source_url,mirror_path,
evidence_text,qualification_rationale,qualification_status,research_only,send_authorized
```

- `product_profile`：统一填 `FJD PaintMaster Pro | UK outdoor natural grass`
- `evidence_text`：页面上的可见原文片段（含邮箱出现的那一块上下文），不是转述
- `mirror_path`：答题方自己保存的页面副本路径
- 无邮箱的账户可以不出现，但要在回答里给出"未找到"计数

## 7. 排除规则

| 排除项 | 理由 |
|---|---|
| 高尔夫 / Footgolf 场地与俱乐部 | 产品场景不符；Bruce 每批回执都单独报排除数 |
| 人工草 / 室内场馆页 | 同上；质量阶段每批排除 8–12 页 |
| `no-reply@`、`donotreply@`、明显的私人邮箱 | 不可触达 |
| 猜测构造的地址（拼 `first.last@`） | 非字面观察 |
| 跨页拼接、页脚串联、卡片外的姓名 | 证据不支持 |
| 教学、招生、财务、IT、人事等功能口 | 与场地/设施无关 |

## 8. 评分口径

对每个子任务和总计，按下列字段打分：

1. 机构数（去重机构名）
2. A+B 机构数（至少一条 A 或 B 的机构）
3. A+B 联系人数
4. 每百机构 A+B（= A+B 联系人数 ÷ 机构数 × 100）
5. C 数、D 数
6. 未找到数（该账户没有任何合格路线）
7. 具名联系人数（`contact_name` 非空）
8. 耗时
9. 人工介入次数（人给下的指令、纠偏、放行）

主指标是 **每百机构 A+B**：本族 Bruce 的交付里 D 档几乎兜住了所有账户，
单看"有没有邮箱"区分不出能力差异。

## 9. 数据边界

- 机构清单（`task_input.csv`）与逐机构基线明细（`baseline_rows.csv`）**只放东京本机**，
  路径见 §3 与 `BASELINE.md §7`。
- TASK.md 与 BASELINE.md 不含任何第三方姓名、邮箱、电话。
- 语料里可能出现凭据和个人信息，一律不得带出东京节点、不得进 Git。

## 10. 出题依据

### 10.1 为什么是这一族

把 Bruce 的记录按国家 / 品类 / 机构类型分组后，同时满足
"有完整最终交付 + 3–5 次同类任务可合并到 200–800 家机构 + 能公开复现 + 逐机构可衡量 + 迭代最成熟"
的只有这一族。对比见下：

| 族 | 时间跨度 | 同类任务 | 最终交付 | 规模 | 逐机构可衡量 | 结论 |
|---|---|---|---|---|---|---|
| **英国 天然草坪运动场/学校（Pitchfinder）** | 2026-09-03 → 09-06 | 111 批 + 1 次历史重评 | 飞书主表 6,859 行，111/111 批可追溯 | 5,596 家 / 6,292 账户 | 是（账户 + 官网 + 邮箱 + 分级） | **选中** |
| 英国 PaintMaster Pro v2/v3（控制面） | 2026-09-06 → 09-08 | 20 账户 Validation + 16 批 50 页 | ROI 回执 | 20 账户 | 是，但太小 | 落选：规模不足 |
| 美国 非高尔夫户外运动场 | 2026-09-17 → 09-21 | 2 批 × 25 账户 | B002_v1 发布包 | 50 账户 | 是（A1/B7/C4/D3） | 落选：只有 2 批 |
| 法国 天然草运动场（割草/划线） | 2026-09-16 → 09-17 | 68 批 × 100 机构 | 网页候选包 | 约 25,700 家母池 | 否：只到"页候选"，无邮箱交付 | 落选：交付不完整 |
| 美国 PL01 停车场 | 2026-08-31 → 09-03 | wave 009/014/015/017/018 | 638 条唯一邮箱 | 53 家机构 | 是，但机构数太少 | 落选：510 条只覆盖 53 家 |
| 瑞士 户外足球场 | 2026-09-04 | 115 批 | 66 条（A16/B26/C24） | 66 家 | 是 | 落选：规模不足 |

### 10.2 为什么是这 5 批

- **同一天、同一流水线、同一口径**：`batch0078`–`batch0082` 在 2026-09-05（北京时间）
  连续跑完，每批都是"约 200 个官方页面 → 字面邮箱提取 → 角色分级 → 飞书入库"。
- **规模落在区间内**：344 账户 / 301 机构。
- **可交叉校验**：这 5 批在三个线程里都留有回执（质量线程、提取线程、收单线程），
  回执里的"新增 N 条"与主表逐批行数**逐批相等**（77/88/63/72/76）。
- **代表性**：这 5 批覆盖了本族的典型形态——大量学校官网、少量俱乐部与场馆运营页、
  每批 8–12 页被排除（室内/人工场）、D 档兜底。

### 10.3 基线在哪里

`BASELINE.md`；逐机构明细在东京本机 `baseline_rows.csv`（376 行）。
