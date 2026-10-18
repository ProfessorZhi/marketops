# MarketOps 产品规格

> 2026-09-21 scope clarification: **MarketOps is the full product**, from candidate-source discovery through account/contact intelligence and, in a separately authorized future phase, outreach and reply handling. The current repository implementation is only the research-engine milestone of that product.

## 0. Product scope vs. current milestone

### Target product

```text
Scene/Profile
→ continuous Source Discovery
→ Asset Pool
→ Account resolution / Account Pool
→ evidence-backed account/contact research
→ freshness / change maintenance
→ candidate outreach manifest
→ explicit authorization
→ staged outreach
→ reply / bounce / unsubscribe monitoring
→ sales handoff and account update
```

### Current implementation milestone

The code currently concentrates on:

```text
Known Account
→ Search / Fetch / Parse
→ Evidence
→ ContactRoute / RouteOpportunity
→ A/B/C/D/X + Account status
```

The target architecture must not be read as a claim that SourceProvider, Jev, MCP service, Postgres, OpenViking or authorized outreach are already implemented.

### Product interfaces

The target product is a **durable service**, exposed through REST/MCP to MUC/Hosting, Claude Code/DSH and other approved clients. A “MarketOps Agent” is a client/persona on top of that service; it is not the sole keeper of state.

See `docs/ARCHITECTURE.md`, `docs/MCP_SERVICE.md` and `docs/DEPLOYMENT.md`.

---

## Existing research-engine specification

```yaml
status:   业务规则已由 SOP 提供（编译进 profiles/）；工程侧缺口已逐条列出
principle: 已知的写清楚，未知的明确标出来，**不编造**
```

---

## ⚠️ 先读这一段

**业务规则已取得并编译进 `profiles/uk-acme-natural-grass/`**：等级定义、
角色矩阵、排除项、搜索面都在那里，取代了此前那份 `status: DRAFT` /
`business_rules: UNKNOWN` 的骨架。

本文里凡是标着缺口的地方，成因都在**工程侧**，不在业务侧 —— 这个区别很重要，
它们的处置相反：业务缺口要去找人问，工程缺口要写代码。

**上一版这里写着「最重要的一处工程缺口在评级：SOP 的条件是账号级的，而引擎的
判定主语是单个联系人」。那个主语分歧已经解决 —— 解决方式不是把两者对齐，
而是承认它们是两条轴，并且分开。** 于是现在有两个函数，谁也不决定谁：

* `grade_route`：一条联系路线（Account + Person/Department + Role/Business
  Function + literal Email + Evidence）属于 A/B/C/D/X 哪一级；
* `grade_account`：一个账号（组织）值不值得研究 —— SOP L188 的状态机
  （eligible / hold / irrelevant / duplicate …）。

「组织对、人不对」是最常见的情形之一。等级挂在账号上就表达不出它。见 §4。

仍然存在、但属于另一类的缺口：账号准入规则引用了一批**场地资产研究事实**
（SOP 原文写的是 `account.attributes.*` / `account.pages.*`，引擎侧是扁平名字），
本仓还没有生产者 —— 它们的载体是发现期的候选项，账号行上没有。引擎因此取
`None`（**未确认**），把引用它们的规则逐条记进 `unchecked`，并据此**不排除**
任何账号 —— 没有证据就丢弃账号，比留着它危险得多。

**「还缺什么」有两份清单，答的不是同一件事**：`DeclaredGrading.ungradeable`
（`marketops/domain/profile.py`，装载器在 `marketops/adapters/profile_loader.py`
里算出并填进该字段）答**装载期**的「引擎不认识哪个事实名」——今天为空；
`AccountOutcome.unchecked` 答**运行期**的「引擎认识、今天还没有值」——今天非空。

本文中仍标 **UNKNOWN** 的项，是真的还没人回答的。见 §6。

---

## 1. 问题

已知的市场开拓流程依赖人工检索公开信息，判断某个账号（组织）对应到
可联系的**人名 / 职位 / 邮箱**，并按重要程度分级。

SOP 已提供**产品**上下文（目标账号是谁、产出如何分级、角色如何归类）。
**业务**背景（谁在做、为何做、成效如何衡量）仍未获得 —— 见 §6.2。

---

## 2. 本阶段交付的能力

**一条研究流水线**：给定已知账号 → 产出**带证据的**候选联系路线。

关键限定词：

* **研究**（research），不是触达（outreach）；
* **候选**（candidate），不是已确认对象；
* **带证据**（evidence-backed），每个非 X 结论都能指回原文。

产出物是"可审计的线索"，交给人类判断。系统不做联系决策。

产出在**两个维度**上而不是一个：账号（组织）走状态机，路线（怎么联系上人）
走等级。一条路线不是"某个账号等级下的一个联系人" —— 它自己带
角色 / 业务职能 / 字面邮箱 / 证据，见 §4 与 `docs/DATA_MODEL.md`。

---

## 3. 三条不可协商的产品规则

这三条不是实现细节，是**产品定义的一部分**。

### 3.1 邮箱必须来自真实公开页面

搜索引擎摘录里的邮箱**不能**成为联系人。
必须实际抓取该公开页面 / PDF，在其镜像中找到字面邮箱，并保存证据。

**产品理由**：摘录可能是缓存的、过时的、甚至拼接自其他站点。
基于它产生的联系人无法审计 —— 出问题时查不到源头。

### 3.2 明确受阻即换来源，不绕

遇到 WAF / challenge / 需登录 / 付费墙 → 标记受阻，**改走其他公开来源**。

**产品理由**：绕过手段（换代理、模拟指纹、绕验证码）会让系统在
被明确拒绝后继续获取数据。这个边界一旦模糊，后续所有"能不能抓"的
判断都会失守。而且这类手段的维护成本会持续上升。

### 3.3 规则取不到可求值的形式时拒绝判定

A/B/C/D 的语义**完全来自 profile 配置**。配置里没有可求值的规则 → 返回 X + 理由。

**产品理由**：默认值会静默传播。如果规则缺失时默认给 C，
那么"规则没写完"和"确实很差"在数据上无法区分，而前者会污染所有统计。

**没有兜底级。** A/B/C/D 四条都是正面定义，SOP 里没有"其余都算 D"这种东西，
所以 profile **不得**声明 `fallback_grade`（装载层显式拒绝它回来）。没有任何一级
匹配时，结果是 `X` + `rules_unknown` —— 一个明说的未判定，不是一个看起来
像结论的默认值。

拒绝的同时必须说清是哪一种缺，因为两者处置相反：业务没给 → 去找人；
引擎读不懂 → 去写代码。这个区分由 `bundle.unknowns` 的措辞承载，
并原样进入判定结果的 `detail`，可被审计。

---

## 4. 两条轴：路线等级与账号状态

这两件事必须分开，因为它们回答的是两个不同的问题：

| 轴 | 问题 | 取值 | 求值函数 |
|---|---|---|---|
| 路线等级 | 这一条联系路线有多好？ | `A` / `B` / `C` / `D` / `X` | `grade_route` |
| 账号状态 | 这个组织值不值得研究？ | `discovered` / `eligible` / `hold` / `irrelevant` / `duplicate` / `researched` / `archived` | `grade_account` |

**两者互不决定。** 「组织对、人不对」——账号 `eligible`、路线 `X` ——是最常见的
情形之一；反过来也有。任何一侧的结论都不蕴含另一侧。

### 4.1 路线等级 A / B / C / D / X

定义来自 SOP，**权威副本在 `profiles/uk-acme-natural-grass/grading.yaml`**，
不在本文 —— 本文只做概述（见 `docs/SOURCES.md` §4）。

| 等级 | 大意 | 声明在哪 |
|---|---|---|
| A | 关键决策或 demo 推进职能 + 真实公开业务邮箱 + 强绑定 | `grading.yaml → grades.A`（SOP L125） |
| B | 关键一线运营 / 维护角色 + 真实公开邮箱 + 强绑定 | `grading.yaml → grades.B`（SOP L126） |
| C | 与目标账号绑定，且 grounds / facilities / venue / bookings / operations 业务语境的官方入口 | `grading.yaml → grades.C`（SOP L127） |
| D | 与目标账号绑定的公开真实泛化官方邮箱 | `grading.yaml → grades.D`（SOP L128） |
| X | 未判定 / 排除类别 | `grading.yaml → grades.X` + 短路 reason（SOP L129/L131） |

求值顺序：短路条件（排除类别 / 证据冲突 / 无合格证据）→ 按 A→B→C→D 取第一个
`requires` 全部满足的级 → 都不满足则 `X` + `rules_unknown`。
**这个顺序是 SOP 声明的一部分**，不是引擎内建的业务知识。

四条值得单独写出来的设计：

* **A 与 B 都要求字面公开邮箱。** 有具名角色、却没有邮箱时，产出物是
  **未解决的路线机会**（`RouteOpportunity`，记录块层级 P3），**不是** B。
  把它记成 B 等于声称我们有一条可联系的路线 —— 那是造假的一种，
  而且它会让"拿到路线的比例"看起来比实际高。
* **B 不是失败。** 一线运营角色配上真实公开邮箱是正常且扎实的输出。
  旧规则把 B 读成"有具名角色但没有邮箱"，那是把机会当成了路线。
* **D 不是"信息不足"。** D 是一个正面定义：与目标账号绑定的泛化官方邮箱
  （`info@` / `enquiries@`）。"信息不足 / 抓不到"按 SOP L119 走
  blocked / no_route，**不伪装成一个等级**（§3.3）。
* **财务不是采购角色。** `finance` / `finance director` 属 X（SOP L129）。
  `admissions` / HR / teaching / 一般行政 / 一般客服同样不进有效路线；
  bookings 只有在明确绑定 venue / facility / sports ground / operations 时才进 C。
  这一条在 profile 里是**装载期检查**，不只是运行期判断 —— 见 §4.3。

### 4.2 账号状态（SOP L188）

`eligible` / `hold` / `irrelevant` / `duplicate` 由 `grading.yaml → x_rules`
（每条带一个 `status`）判定；`discovered` / `researched` / `archived` 是流程状态，
不由规则产生。

**`x_rules` 不是 X 级的定义** —— 它是账号准入规则。旧 profile 把两者混在一起，
于是"账号不合格"和"路线是 X"成了同一件事；两个概念共用一个名字之后，
"这个账号该丢"和"这条路线不能联系"就只能同进同出。

当前状态：6 条账号规则**全部**有求值子句，其中能真的判决的只有一条
（`country not in [GB]` → `irrelevant`，因为 `country` 是 `AccountRecord`
自己的字段）；其余五条引用的是**候选事实**（场地环境 / 组织形态 / 表面类别 /
抓取统计），它们的载体是发现期的候选项，账号行上没有 —— 引擎手上是 `None`，
把这些规则逐条记进 `unchecked`，并据此**不排除**账号 —— 没有证据就丢弃账号，
比留着它危险得多。

**「还缺什么」有两份清单**：`DeclaredGrading.ungradeable` 答装载期的「引擎不认识
哪个事实名」（今天为空），`AccountOutcome.unchecked` 答运行期的「引擎认识、
今天还没有值」（今天非空）。`docs/SOP_MAPPING.md` §4 记了完整缺口。

### 4.3 已知的规则缺口（写出来，而不是替 SOP 补一条）

`grading.yaml → known_gaps` 记着一条：SOP L79 把 Procurement 列为优先角色，
但 L125–L128 四条正面定义里没有哪一条容得下「强绑定的具名采购角色 + 字面邮箱」，
于是它落到 `X` + `rules_unknown`。

这是**刻意留着**的：塞进 A 会让 A 覆盖几乎所有正面角色、失去分辨力；
塞进 C 则要用"具名角色"去满足"官方入口"的定义。正确修法是回 SOP 确认采购归口
属于哪一级，然后重编 profile —— 不是在这里猜。

---

## 5. 已知的数据边界

| 项 | 值 |
|---|---|
| 运行模式 | `research_only = true` |
| 发送授权 | `send_authorized = false` |
| 数据落点 | `MARKETOPS_DATA_ROOT`，**无默认值**：未设置则启动即失败（`marketops/config.py::load_settings`）。运行数据永不进 Git |
| 是否进 Git | 否 —— 运行数据、镜像、证据全部在仓库外 |
| 采集性质 | 匿名公开浏览 |

`research_only` / `send_authorized` 是**模型层校验**，不是配置项：
它们不是环境变量，也无法被任何入参打开。

---

## 6. 待确认清单

本清单现在分两部分，因为 SOP 已经把其中一批问题答完了。
**答完的那些不再留在"待确认"里** —— 让已答的问题与没答的问题混在一张表上，
下一次读它的人会重新去问一遍已经问过的事。

### 6.1 已由 SOP 回答

| # | 问题 | 答案落在哪 |
|---|---|---|
| 2 | "账号"在业务上的确切定义 | `profile.yaml → target_accounts`（场景 + 场地 + 环境 + 归属） |
| 6 | UK 与 AcmeCorp 的关系 | `country: GB` + `product.name: AcmeCorp Pro`（地区与产品是两个字段） |
| 7 | "Natural Grass" 在 profile id 中的作用 | 它是**目标账号的场地属性**（`target_accounts.attributes.surface`），不是产品线 |
| 8 | 该市场下"有效联系人"的标准 | `grading.yaml` 的 A/B 条件 + `role-matrix.yaml` 的 `class` / `business_function` |
| 9 | A / B / C / D 的定义与判定依据 | `grading.yaml → grades`（见本文 §4） |
| 10 | 角色矩阵：哪些职位算核心/次要/忽略 | `role-matrix.yaml → role_groups`（每组带 `class`：decision/influencer/operational/gatekeeper）+ `excluded_roles` |
| 11 | 是否"必须同时具备姓名和邮箱"才算数 | **A 与 B 都要求字面公开邮箱**（SOP L125/L126）。只有具名角色、拿不到邮箱时，产出的是 `RouteOpportunity`（记录块 P3），不是 B —— 见 §4.1 |
| 12 | 现有人工 SOP 的完整步骤 | SOP 正文在 records（`docs/SOURCES.md` §2），映射见 `docs/SOP_MAPPING.md` |
| 18 | 采集行为的产品边界 | `profile.yaml → compliance`（仅公开页面；不登录、不绕验证码、不突破访问控制） |

### 6.2 仍待确认

以下每一条都需要人来回答，不是靠代码推导。

#### 业务背景

1. MarketOps 服务的**具体业务目标与最终成功指标**是什么？
   （SOP 说清了目标账号与产出分级，但没有说清"做成了"如何衡量）
3. 三类队列（known_success / accessible_no_core_email / previously_blocked）
   的划分标准由谁维护、多久更新一次？

#### Member-A

4. Member-A 是谁 / 是什么系统 / 是什么角色？与本 pipeline 的关系？
5. Member-A 侧的输入输出格式与交付节奏？

#### SOP 的落地细节

13. SOP 各步骤中，哪些必须由人判断、哪些可以自动化？
14. SOP 里的验收/复核环节由谁执行？

#### 集成

15. 飞书 Bitable 的目标表结构、字段映射与写入权限（Sandbox 应用未申请）。
16. 结果交付给谁、以什么形式、什么频率？
17. 是否存在其他下游系统需要对接？

#### 合规

19. 联系人数据的保存期限与删除要求？
20. 目标市场（英国）在**法律层面**对公开信息采集与联系人处理有哪些限制？
    （§6.1 的第 18 项是产品自我约束，不是法律意见 —— 两者不能互相替代）

---

## 7. 下一步

**阻塞项（工程侧）**：

1. §4.2 的场地资产事实生产者（SOP 原文的 `account.attributes.*` / `account.pages.*`，
   引擎侧的扁平名字见 `marketops/domain/facts.py` 的 `CANDIDATE_FACT_NAMES`）——
   没有它们，SOP L99 的账号准入规则里那五条永远只能记在 `unchecked` 里。
   注意**它不阻塞路线定级**：两条轴已经分开，`grade_route` 现在就能对已给出的
   A/B/C/D 条件求值；
2. §4.3 的采购归口 —— 一件要回 SOP 确认的业务问题，不是代码问题；
3. §3 的 30 个真实账号 —— 否则无法启动首次真实运行。

**已关闭**：判定主语的对齐（账号 vs 联系人）。曾按"把两者对齐"处理，
实际正确的处置是承认它们是两条轴并分开，见 §4。

**非阻塞项（可并行）**：

* **开放网络那一族模板的展开器已经落地**（`application/openweb.py`，三个键里
  `query_templates` / `venue_types`），**缺的是把它接进一轮发现** —— 那一圈 runner
  属于 Phase 4 的 harness。**接线的地方不是 `application/queries.py::build_queries`**：
  两族模板刻意**不共用**校验器，否则 `{domain}` 会变成开放网络模板里的合法占位符，
  也就是允许把账号事实注入发现期查询 —— 一次看起来完全正常的静默查询注入。
  `regions` 仍然没有消费者，且它该是「在范围内的国家」还是「检索式里的地区词」
  **未定**（见 `profiles/uk-acme-natural-grass/profile.yaml` 里那段注释）。
* 飞书 Sandbox 应用申请（见 `docs/NOT_BUILT.md` §2）。
