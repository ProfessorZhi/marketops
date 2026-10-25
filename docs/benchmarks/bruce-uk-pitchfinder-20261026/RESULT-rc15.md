# 结果：`marketops-research-assistant` 0.5.11-rc15 在本题上的实测

同一道题（`TASK.md`）、同一批输入（344 账户 / 301 机构）、同一套判据
（`BASELINE.md` §8），由 rc15 完整自主跑一遍。本文只写结论与证据口径；
逐账户明细在东京本机，路径见 §11。文中不含任何第三方姓名、邮箱、电话。

---

## 0. 一句话结论

**主指标过了，精度和覆盖率没过。** 每百机构 A+B **4.32**（基线 1.99，门坎 4.0），
但 A/B 精度 **46%**（门坎 90%）、覆盖 **88.7%**（门坎 90%），两项不及格，
按 §5 的五条判据 **不可交付**。

主指标的领先有一半是口径造成的：13 条 A/B 里有 6 条的岗位是学校
Business Manager / Bursar（财务口径，Bruce §4/§7 明确排除）。
把这 6 条按 Bruce 的口径剔掉，A+B 每百机构回落到 **1.99**，与基线持平，
精度升到 85.7%（仍未过 90%）。

---

## 1. 版本冻结与运行环境

| 项 | 值 |
|---|---|
| 插件 | `marketops-research-assistant` **0.5.11-rc15**（`~/.claude/plugins/cache/marketops/...`） |
| 解释器 | `/data/home/vince/toolchains/marketops-plugin-runtime/bin/python` |
| 数据根 | `artifacts/marketops/uk-pitchfinder-rc15-20261026/data`（**新建**，与其它 Item 隔离） |
| 同意 | `MONEY` / `RED_LINE` 默认红线，**无总额上限**；TinyFish Agent 按安装时设置 |
| 场景 | `shipped:uk-natural-grass`（简报 `brief-scene-uk-natural-grass` → `brief-source-gate-off`） |
| Run | `uk-pitchfinder-rc15-20261026r2`（池 `uk-pitchfinder-20261026r2`，368 目标） |
| 代码改动 | **零**。全程未改插件代码；发现的 bug 只记录（§9） |

时间均为北京时间（UTC+8）。

---

## 2. 方法：插件实际做了什么

1. **入池**：`task_input.csv` 的 344 账户按 `name+website` 归一成 **302 个目标**
   （42 个账户与同站账户合并成同一 target_key），另加一轮"官网公开名"补充源
   **66 个目标**（同一批官方 URL，仅把清单里的机构名换成站点自己印的名字）。
   368 个目标，`pool_targets` 可逐条对账。
2. **抓取**：`harvest_batch`，并发 8、单次 40 秒上限、每目标 6–8 页
   （联系页 / 教职员目录 / 场地与 lettings 页）。共 crawl4ai 渲染 1,062 页。
3. **抽取 + 分级**：字面邮箱抽取 → typesafe_jev 判角色（1,893 次）→
   `research_grading.grade_routes` 按场景规则定 A/B/C/D/X。
4. **方法阶梯**：`OWN_SITE` 已 `exhausted`；`OPERATORS` / `CLUBS_ASSOCIATIONS` /
   `FEDERATION_LISTS` 记为 `not_applicable`——本题分母是 Bruce 给的固定账户清单，
   引入清单外运营方会让"逐账户对齐"失效。
5. **交付**：`marketops_handoff(mode=final)` 产出
   `Research_Result.xlsx` + 8 张 CSV + `MANIFEST.json`（sha256 已固定）。

运行中插件自身触发过一次 **`SOURCE_YIELD_STOP`**（36 个目标后 0 个 A/B，
阈值 5%/10%），把剩下 263 个目标整源 park。本题一账户一 URL，没有"换来源"的余地，
必须以 `source_gate: false` 重开简报才继续——这是 §9 的 bug 3。

---

## 3. 插件分级 → Bruce 分级 的映射表

rc15 的导出只有 10 列（`渠道ID/组织ID/联系人或部门/职能/渠道类型/公开字面值/
来源字面校验/研究评级/业务状态/资料引用`），TASK §6 要 18 列。
东京本机的转换脚本 `tools/convert_to_bruce.py` 按下表补齐并落成 18 列 CSV。

| 插件（`研究评级` / 判定理由） | Bruce `role_priority` | Bruce `evidence_tier` | `qualification_status` | 计入 |
|---|---|---|---|---|
| `A` + `NAME_ROLE_EMAIL_ONE_BLOCK`（decision 类） | `A_key_role` | `strict_named_role_email` | `effective_research_email` | 是 |
| `B` + `ROLE_BOUND_NO_NAME` / `NAME_ROLE_EMAIL_ONE_BLOCK`（operational 类） | `B_related_operator` | `role_bound_public_email` | `effective_research_email` | 是 |
| `C` + `ENTRY_ROLE` / `ROLE_NOT_IN_SCENE` / `NO_ROLE` / `DEPARTMENT_INBOX_NEXT_TO_NAME` | `C_official_business_entry` | `observed_business_route` | `official_business_entry` | 是（研究资产） |
| `D` + `GENERIC_INBOX` / `SHARED_INBOX_SEVERAL_PEOPLE` | `D_official_general_research_entry` | `official_general_research_route` | `official_general_research_entry` | 是（研究资产） |
| `X` + `ROLE_EXCLUDED` | — | — | `excluded` | 否（19 条） |
| `RULES_UNKNOWN`（全部是非 email 渠道） | — | — | — | 否（356 条电话，Bruce 契约无电话列） |

派生列（插件不产出，按定义补）：

| 列 | 取值规则 |
|---|---|
| `decision_function` | operational 类 → `operational_owner`；decision 类 → `economic_buyer`；C/D → `routing_fallback` |
| `contact_name` / `contact_title` | 取 `grade_routes` 的 `person_name` / `role_title` |
| `department` | A/B → `Grounds and facilities`；C → `Official business entry`；D → `Official general business entry` |
| `role_relevance` | 由等级 + 角色类生成的固定句式 |
| `evidence_text` | `claims[].quote` 中含该字面值的那一段原文 |
| `mirror_path` | `artifacts.relative_path` 的绝对路径（内容寻址 `.bin`） |
| `product_profile` / `research_only` / `send_authorized` | 常量：`FJD PaintMaster Pro \| UK outdoor natural grass` / `TRUE` / `FALSE` |

> 标签差异：本批 Bruce 的 C 类在基线里写作 `C_relevant_official_business_entry`，
> 与 TASK §5.2 的 `C_official_business_entry` 是两种写法（`BASELINE.md` §8.4），
> 对比时合并计为 C。

---

## 4. 指标

### 4.1 分批次

机构数按官网域名去重（我方 299，基线按机构名 301，差 2 家共用域名）。

| 指标 | batch0078 | batch0079 | batch0080 | batch0081 | batch0082 | **合计** |
|---|---:|---:|---:|---:|---:|---:|
| 账户数 | 69 | 81 | 59 | 65 | 70 | **344** |
| 机构数（域名） | 62 | 70 | 50 | 55 | 63 | **299** |
| 有路线的账户 | 61 | 73 | 55 | 56 | 60 | **305** |
| 覆盖率 | 88.4% | 90.1% | 93.2% | 86.2% | 85.7% | **88.7%** |
| 交付路线行数 | 238 | 251 | 161 | 108 | 88 | **846** |
| A+B 机构 | 1 | 5 | 2 | 1 | 2 | **11** |
| A+B 联系人数 | 1 | 7 | 2 | 1 | 2 | **13** |
| 每百机构 A+B | 1.6 | 10.0 | 4.0 | 1.8 | 3.2 | **4.32** |
| C | 177 | 173 | 107 | 50 | 34 | **474** |
| D | 60 | 70 | 51 | 57 | 51 | **248** |
| 未找到账户 | 8 | 8 | 4 | 9 | 10 | **39** |
| `contact_name` 非空行 | 83 | 114 | 41 | 19 | 9 | **266** |

### 4.2 与基线对照

| 指标 | rc15 | Bruce | 判据 | 结果 |
|---|---:|---:|---|---|
| **每百机构 A+B** | **4.32** | 1.99 | ≥ 4.0 | ✅ |
| A+B 机构 | 11 | 6 | — | — |
| A+B 联系人数 | 13 | 6 | — | — |
| A/B 精度（逐条开页复核） | **46.2%** | 100%（6/6 字面在页） | ≥ 90% | ❌ |
| 覆盖（344 账户） | **88.7%** | 100% | ≥ 90% | ❌ |
| 具名联系人 | 266 行有值 / 其中**可用 6** | 0 | > 0 | ✅ |
| C | 474 | 45 | — | — |
| D | 248 | 325 | — | — |
| 未找到 | 39 | 0 | — | — |
| 交付路线行数 | 846（735 个机构×地址对） | 376 | — | — |

口径说明：

- 我方行数是**逐账户**的（同一机构多个账户各得一行，与 Bruce 的 376 行/344 账户同口径）；
  去重到"机构×地址"是 735 对。
- "未找到"= 该账户一条**邮箱**路线都没有（只有电话的不算——Bruce 契约没有电话列）。
- "具名联系人"照抄 TASK §8 的定义（`contact_name` 非空）。266 行里有 260 行的姓名是
  `NAME_FROM_EMAIL_QUOTE` 从原文猜出来的，含把岗位词当人名的情况（§9 bug 6），
  人工复核后真正"人名 + 岗位 + 地址三者同块"的只有 **6** 条。

---

## 5. 四类账户（每侧 10 条；只写机构类型与原因）

分类口径：按 `account_id` 逐账户取"最好等级"，比较是否属 A/B。

### 5.1 我方有 A/B、Bruce 没有（12 个账户，列 10）

| # | 机构类型 | 我方依据 | Bruce 在该账户上的档位 |
|---|---|---|---|
| 1 | 学院（academy） | 教职员页卡片内"姓名+Premises Manager+mailto"同块 | D（通用信箱） |
| 2 | 学院（academy） | 教职员页"姓名+Site Manager+mailto"同块 | D |
| 3 | 中学（secondary） | 领导团队页"姓名+Business Manager+mailto"同块 | C |
| 4 | 小学 | 联系页"Contact for Site Manager : 地址"同句（无姓名） | D |
| 5 | 小学 | 页脚区"岗位 - 姓名 + 地址"相邻但**不同行** | D |
| 6 | 小学 | 教职员卡片"姓名+Site Manager+mailto"同块 | D |
| 7 | 小学（教会办） | lettings 页"人名+School Bursar+mailto"同块 | D |
| 8 | 社区体育场地协会 | 场地页"联系 Groundsman 预订球场"+mailto | C |
| 9 | 小学 | 领导团队页"姓名（地址）"，岗位在**上一行** | D |
| 10 | 特殊/社区学校 | staff 卡片"姓名+Site Facilities Manager+mailto"同块 | D |

其中 1、2、4、6、8、10 复核为真（6 条）；3、5、7、9 属 §9 的绑定/口径问题。
剩下 2 个账户与上表同因，未单列。

### 5.2 Bruce 有 A/B、我方没有（4 个账户，全部列出）

| # | 机构类型 | Bruce 的依据 | 我方落到 |
|---|---|---|---|
| 1 | 社区足球俱乐部 | 委员会页上"Facilities"这一**表头**与该地址同表 | C（同页拿到地址，但岗位在表头，被判"不在邮箱块内"） |
| 2 | 矿工福利俱乐部 | 预订页"Venue Manager"与地址同块 | C（同页只认到 `bookings` 这类入口词 → entry） |
| 3 | 体育俱乐部 | 站点根页"Venue Manager" | C（我方读了 contact 页，岗位词未与地址同块） |
| 4 | 小学 | contact-us 页"Operations" | D（只抓到根页通用信箱） |

这 4 条是**角色绑定过严**造成的漏判：地址都拿到了，岗位在表头/相邻单元格时不认。
它们是本轮唯一"别人有我没有"的 A/B 缺口。

### 5.3 两侧都没有 A/B

326 个账户。两侧都以通用信箱（D）或入口（C）兜底，与基线"学校官网找得到邮箱、
找不到人"的结论一致。

---

## 6. 精度：逐条打开原页面

样本：我方 **全部 13 条 A/B**（不足 30，全查）、我方随机 20 条 C/D、
Bruce 全部 6 条 A/B、Bruce 随机 20 条 C/D（随机种子 20261026）。共 59 条。

**第一检（字面存在）**：59/59 全部在页面上找到该字面值——
我方 A/B 13/13、我方 C/D 20/20、Bruce A/B 6/6、Bruce C/D 20/20。
没有一条是猜出来的地址；有 4 条只能从 `mailto:` 属性里读到（可见链接文字是另一个地址），
单看页面文本会误判"不存在"。

**其余三检（只对 A/B 有意义）**：

| 检查 | Bruce（6 条） | rc15（13 条） |
|---|---|---|
| 1 字面在页 | 6/6 | 13/13 |
| 2 姓名/岗位/地址**同一记录块** | 6/6 | **10/13**（3 条跨行/跨卡片套用） |
| 3 岗位在场地与设施口径内 | 6/6 | **6/13**（6 条是 Business Manager / Bursar / Finance） |
| 4 未落入 §7 排除项 | 6/6 | **7/13** |
| **判为可用** | **6/6 = 100%** | **6/13 = 46.2%** |

我方 13 条的逐条判定（机构类型 + 原因，无姓名/邮箱）：

| # | 机构类型 | 页面实际写的岗位 | 判定 | 原因 |
|---|---|---|---|---|
| 1 | 学院 | Premises Manager | ✅ | 同块绑定 |
| 2 | 学院 | Site Manager | ✅ | 同块绑定 |
| 3 | 中学 | Business Manager | ❌ | 财务/人事口径，§4 排除 |
| 4 | 小学 | Site Manager（无姓名） | ✅ | 同句绑定 |
| 5 | 小学 | 特殊教育协调（SENCO） | ❌ | 岗位来自上一行另一个人 |
| 6 | 小学 | Site Manager | ✅ | 同块绑定 |
| 7 | 小学 | School Business Manager | ❌ | 财务口径 |
| 8 | 小学 | Receptionist | ❌ | 岗位来自相邻卡片 |
| 9 | 小学（教会办） | School Bursar | ❌ | 财务口径；可见链接文字写的是另一个地址 |
| 10 | 社区体育场地协会 | Groundsman（预订球场） | ✅ | 同句绑定，正是本题用例 |
| 11 | 小学 | 该行无岗位 | ❌ | 继承了上一行 Business Manager |
| 12 | 社区学校 | Site Facilities Manager | ✅ | 同块绑定 |
| 13 | 小学 | Finance and Business Manager | ❌ | 明文财务口径 |

细到每条的原文块与判定在东京本机
`out/precision_ab_verdicts.json`、`out/precision_ours.json`、`out/precision_bruce.json`。

**结论**：地址抽取是干净的（59/59），错在**岗位归类**与**绑定**：
一半的 A/B 是"财务口径被当成运营口径"，另有三条是把别的行的岗位套到这个地址上。

---

## 7. 成本、耗时、人工介入

### 7.1 成本（`marketops_costs`，按价目表估算）

| 提供方 | 调用次数 | 估算 |
|---|---:|---:|
| crawl4ai（本地渲染） | 1,062 | $0 |
| typesafe_jev（角色判定） | 1,893 | $0.0383 |
| deepseek（检索） | 22 | $0.0165 |
| tinyfish | 41 | $0 |
| serper | 2 | $0.0020 |
| serpapi | 2 | $0 |
| **Run 合计** | 3,022 | **$0.0568** |

加上被放弃的首个 Run（$0.0097，输入侧名单带内部编号前缀导致身份门全灭），
本轮总计 **$0.0665**。免费额度余量：serper 2,498/2,500，serpapi 248/250。

**Bruce 那一侧没有可比的金额**——语料里只有批次回执，没有账单。

### 7.2 耗时

| 段 | 起 | 止 | 耗时 |
|---|---|---|---|
| 建根 + 首池（作废） | 00:12 | 01:33 | 1 小时 21 分 |
| 正式 Run r2：入池 → 抓完 | 01:58 | 02:46 | **48 分钟** |
| 端到端（任务下达 → 抓完） | 00:12 | 02:46 | **2 小时 34 分** |
| 转换 + 指标 + 精度复核 | 02:20 | 02:55 | 35 分钟（与抓取尾部重叠） |
| Bruce 端到端 | 16:49 | 23:06 | **6 小时 17 分** |

### 7.3 人工介入

- **人工插话：1 次**——即任务书本身（00:12）。之后全程无人工消息；
  会话续跑指令（01:52）不构成对运行的干预。
- **插件侧停等：1 次**——`handoff(mode=final)` 被拒并返回
  `CHECKPOINT_RESEARCH_NOT_FINISHED`（详见 §9 bug 8）。它没有向人提问，
  只是要求"继续研究"，由运行者判定为固定分母下不适用后继续测量。
- **操作员动作 5 次**（新建数据根、补 scene、弃首池、重开闸门、转换/复核），
  全部记录在 `logs/interventions.md`，不计入人工插话。
- Bruce：当天四条流水线线程共 **43 条**人工消息；本 5 批收单线程内 **0 条**。

---

## 8. 结论

### 8.1 五条可交付判据

| 判据 | 门坎 | 实测 | 结论 |
|---|---|---|---|
| 每百机构 A+B | ≥ 4.0 | **4.32** | ✅ |
| A/B 精度 | ≥ 90% | **46.2%** | ❌ |
| 覆盖率（344 账户） | ≥ 90% | **88.7%** | ❌ |
| 具名联系人数 | > 0 | 6（可用的 A/B 具名块） | ✅ |
| 人工介入 | ≤ 3 | 1 | ✅ |

**判定：不可交付。** 差距在两项：精度差 43.8 个百分点，覆盖差 1.3 个百分点。

### 8.2 差距的成因

1. **精度**：场景 `uk-natural-grass` 把 `business manager` 列为 operational、
   把 `bursar` 列为 decision，而本题 §4/§7 明确排除财务口径。
   13 条 A/B 里 6 条落在这里（46%→85.7% 的差距全靠它们）。
   剩下 3 条是**跨行/跨卡片绑定**：岗位在上一行或相邻卡片时被错误套用。
2. **覆盖**：39 个账户没有邮箱路线，其中 **22 个**只因身份门
   （要求机构名出现在抽取文本里）拒绝了一个**正确**的官网——页面的
   `<title>`/`og:site_name` 明明就是机构名；**8 个**是抓取层对
   urllib 返回 200 的站点报 `SITE_UNREACHABLE`（重试仍失败）；
  剩下 9 个是只有电话、或路线准入失败。
   把这 22 个账户修好，覆盖就是 95.1%。
3. **主指标为什么还赢了**：赢在"每机构能给出多个具名/岗位地址"，
   而基线几乎全部靠 D 档兜底。但这部分优势被上面的口径问题扣掉后归零
   （剔除财务岗后 1.99，与基线持平）。

---

## 9. bug 清单（只记录，未修）

复现均在 `artifacts/marketops/uk-pitchfinder-rc15-20261026` 数据根内可重放。

| # | 现象 | 影响 | 复现 |
|---|---|---|---|
| 1 | **身份门只查抽取文本**，不查 `<title>` / `og:site_name` / JSON-LD；机构名只出现在标题里就判 `NAME_NOT_FOUND_ON_SITE` 并整条 HOLD，无参数可绕过 | 86 个目标挂起 / **22 个账户丢失**（覆盖 −6.4 pp） | 78 条 `logs/hold_site_titles.json`；抽查三站 HTTP 200、标题即机构名。用"站点自己印的名字"另建源后，同一批 URL 有 **54/66 通过**，证明原名是对的、门是错的 |
| 2 | **抓取层假阴性**：8 个 URL 被 crawl4ai 判 `SITE_UNREACHABLE`，同机 `urllib` 取回 200 | 8 个账户（−2.3 pp） | 重开后再抓仍失败；`tools/site_titles.py` 逐条 200 |
| 3 | **来源闸门与固定分母冲突**：36 个目标 0 个 A/B 即 `SOURCE_YIELD_STOP`，整源 263 目标 park；`pool_reopen` 无效（下次认领会立刻再 park） | 必须以 `source_gate:false` 重开简报才能跑完；本族天性就是 2.0 A+B/100 | 首池 + r2 前 36 个目标；改简报后 `verdict: GATE_OFF` |
| 4 | **简报缺 `scene` 时静默降级**：所有 `roles_wanted` 条目都当 decision/A | 首版简报把"场地租用入口"也算 A | `brief-…`（无 scene）vs `brief-scene-uk-natural-grass` |
| 5 | **跨行/跨卡片岗位绑定**：岗位取到了上一行或相邻卡片的文本 | 3/13 条 A/B 误绑 | 三例：卡片写 Receptionist 却判 site manager；该行无岗位却继承上一行 Business Manager；SENCO 被判 business manager |
| 6 | **`NAME_FROM_EMAIL_QUOTE` 把岗位词当人名** | `contact_name` 出现 "Site Manager"、"Green Premises Manager"、"P. Beesley-Evans Business" 这类值；266 行具名只有 6 行可用 | `grades_snapshot.json` 的 `person_name` |
| 7 | **带编号前缀的机构名必然失败**：`name_variants` 按 `\|` 切分只取前半段 | 首池 20 目标 6 个 HOLD，整池作废 | 首池 `uk-pitchfinder-20261026`（已放弃） |
| 8 | **`handoff(mode=final)` 自相矛盾**：同一次返回里 `execution.goal_satisfied=true`、`pending_pool_targets=0`、`sources_reconciled=true`，却给 `CHECKPOINT_RESEARCH_NOT_FINISHED` + `finalization_refused`；`goal_assessment` 另按 org-people 报 284 pending | 固定分母任务永远拿不到 `is_final` | 02:47 的 handoff 返回 |
| 9 | **导出列不全**：`联系人路线.csv` 只有 10 列，缺 TASK §6 的 8 列（`contact_name/contact_title/department/decision_function/role_relevance/qualification_rationale/research_only/send_authorized` 等），也无 `product_profile` | 必须自己拼 18 列，插件给不出"可直接交付"的表 | 导出目录下 8 张 CSV 的表头 |
| 10 | **导出与记录不一致**：records 里 1,132 个 contact_point（772 邮箱），导出 1,124 行（768 邮箱）——4 条邮箱、4 条电话消失，去重口径未说明 | 交付行数与内部状态对不上 | `research-lite.sqlite3` vs `联系人路线.csv` |
| 11 | **畸形/第三方/私人地址进入候选**：空域名标签、第三方慈善机构信箱、个人 gmail、供应商信箱都被 stage 成 route | 靠人工/事后规则排除 | 3 条 `VENDOR_HTML_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED` + 若干 C/D |

---

## 10. 下一轮修什么（按对本题的影响排序）

| 序 | 修什么 | 预期效果 | 对应 bug |
|---|---|---|---|
| 1 | 身份门接受 `<title>` / `og:site_name` / JSON-LD 里的机构名（或让简报声明"名称别名来自站点自身"） | 覆盖 88.7% → **95.1%**，直接过 90% 门坎 | 1 |
| 2 | 抓取层对 200-但-判定不可达的站点换 UA / 重试 / 浏览器兜底 | 再 +2.3 pp，覆盖到 **97.4%** | 2 |
| 3 | 岗位绑定要求"同一卡片/同一表格单元"，禁止跨卡片与跨行继承 | 修 3/13 误绑，精度 +23 pp | 5 |
| 4 | 场景 `uk-natural-grass` 去掉 `business manager` / `bursar`，或允许简报的 `exclusions` 覆盖场景 `roles` | 精度 46% → **85.7%**；但主指标回落到 1.99 | — |
| 5 | 补第 4 条后仍需再提精度：对 A/B 增加"岗位词必须与人名同块，且人名不得是岗位词"的硬校验 | 85.7% → 接近 100%（剩 1 条 Receptionist 误绑由第 3 条修） | 5, 6 |
| 6 | 单来源/固定分母场景默认关掉来源闸门（或按来源族历史 AB 率自适应阈值） | 少一次人工改简报；本族基线本身 2.0/100 | 3 |
| 7 | 导出口径补齐 TASK §6 十八列，并公开去重口径 | 交付表可直接用；行数可对账 | 9, 10 |
| 8 | `goal_assessment` 与 `execution` 块对齐；允许"固定分母 + 覆盖目标"的 final 语义 | 不再出现自相矛盾的 final 拒绝 | 8 |
| 9 | 简报缺 `scene` 时改为报错而不是静默降级 | 首轮就不会把入口当 A | 4 |
| 10 | 候选入池前过滤空域名/第三方域名/个人邮箱 | 少一批 `ROUTES_NOT_ADMITTED` | 11 |

注意第 3–5 与第 4 的顺序：**先把岗位口径调对，再谈精度**。若只按第 4 条剔除财务岗，
主指标立刻掉到基线水平——这不是退步，是把原来记在主指标里的虚高分还回去。

---

## 11. 东京本机明细（不入 Git）

```text
/data/home/vince/artifacts/marketops/uk-pitchfinder-rc15-20261026/
  out/ukp_rc15_bruce18.csv        # 846 行，TASK §6 的 18 列，逐账户
  out/align.csv                   # 344 账户：我方最好档 vs Bruce 最好档 + 四分类
  out/metrics.json                # 本文件 §4 的全部数字
  out/grades_snapshot.json        # 772 条邮箱路线的等级/理由/角色类/姓名/标题
  out/precision_ab_verdicts.json  # 13 条 A/B 的逐条判定
  out/precision_ours.json         # 我方 33 条抽样（含页面上下文片段）
  out/precision_bruce.json        # Bruce 26 条抽样
  logs/interventions.md           # 人工插话 + 操作员动作
  logs/hold_site_titles.json      # 78 条身份门 HOLD 的站点自报名
  tools/convert_to_bruce.py       # 18 列转换（只读复用插件 grader）
  tools/metrics.py                # 指标
  tools/precision_check.py        # 逐条开页复核
  data/exports/.../handoff-ukp-r2-20261026/   # 插件原始交付（xlsx + 8 CSV + MANIFEST）
```

这些文件含第三方邮箱与机构信息，**只在本机**；本文与同目录 `TASK.md`、
`BASELINE.md` 不含任何第三方姓名、邮箱、电话。
