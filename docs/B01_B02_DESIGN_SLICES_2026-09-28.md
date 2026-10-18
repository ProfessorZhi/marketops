# B01 + B02 · 产品/Offer 可选上下文、启动与 EffectiveScene —— 切片划分（2026-09-28）

权威：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` 的 `:96-104`（B01，八条关闭条件）、
`:106-116`（B02，八条），其中 `:110` 是编译要求那一段；现状判词 `:25`（B01 `NOT BUILT`）、
`:26`（B02 `PARTIAL`）；顺序与完成判据 `:503`（「每项必须有生产者、存储/事务、实际调用者、
MCP读取/控制、故障回执及关闭测试；只有类名/字段/接口的无消费者实现不算完成」）。
语义规格是 `docs/PRODUCT_LINE_AND_SCENE_MODEL.md`：§2 名词边界、§4 启动顺序、
§5 Scene 切分与冻结绑定、§6 资料分工与迁移、§8 共享事实/按 Scene 经营。

这份文件是**计划**。它与「落地记录」（B17 那种逐片新开的文件）分开：下面每一片落地时
各写一份落地记录，本文只维护切片表与差量。

## 一、侦察（本 worktree，2026-09-28，逐条实测）

### 1.1 已经在盘上、这一轮要**复用**的

| 面 | 落点 | 它已经守住了什么 |
|---|---|---|
| 一版规则与「批准」这个动词 | `domain/scene.py:113 SceneVersion`、`:320 SceneRecord`、`application/scenes.py:194 activate` | 版本化 + CAS 写入；`activate` 的 docstring 逐字写着「**这就是「批准」这个动作**」 |
| 「改哪一格要重抓/重评什么」 | `domain/scene.py:625 scene_impact` + `:541 ImpactScope`（9 成员）+ `:573 SCENE_IMPACT_RULES` | 规则表已存在，缺格是**故意的**（`budget`/`refresh` 不在表里） |
| 策略编译（B16 落的） | `application/research_strategy.py`（`ScenarioProfile:663` 等模块）+ 每轮的 `EffectiveResearchPlan` | 场景层三格（先看哪类页面 / 找哪几种业务口径 / 先后）从**声明**读，不推 |
| 计划预览 | `application/plan_preview.py:239 preview_plan`、`:399 NOT_REPORTED`（5 项，逐条起于 `:400`/`:409`/`:417`/`:427`/`:437`） | 零 provider 调用是**构造**（签名拿不到端口），不是自律 |
| 预览 → 持久受理 | `application/run_preview.py`（MCP `preview_run`）、`application/run_admission.py:101 admit_run` | B17 已把这条链做成三层里的两层 |
| 预算 | `ports/budget.py` 两阶段账本、`domain/run.py:150 AuthorizationWindow` | 预留/结算；链级累计在 `RunSpec.window` |
| 跨工作区复用 | `application/reuse.py:168 ReuseLedger`、`:127 SHARED_PREFIX`、`domain/evidence.py::Evidence.workspace` | 「复用范围**止于工作区边界**」有执行器 |
| 名单不越 Scene | `application/cohorts.py:324-327` | 拿别个 Scene 的 `cohort_id` 来读会**抛**，不是静默取最近的 |
| 角色排除表 | `domain/profile.py:212 excluded_groups`、`adapters/profile_loader.py:900` | 排除表存在，且 `research_strategy.py:673` 逐字规定**只读 `groups` 不读 `excluded_groups`** |

### 1.2 实测零命中的（`marketops/` 下 `.py` 文件命中数 = 0）

`ProductVersion`、`OfferVersion`、`ProductLine`、`ProductVersionSource`、`EffectiveScene`、
`GeoScope`、`SalesMotion`、`SourceArtifact`、`ProductFit`、`LaunchRecord` —— 各 **0 个文件**。
`security/catalog.py` 的 **60** 个工具里没有任何 product / offer / launch / workstream 命名的
工具（实测 `command grep -inc` 的命中全是注释在解释它引的是哪份文档）。
`adapters/sqlite/db.py` 的 **47** 条 `CREATE TABLE` 里没有产品、Offer、EffectiveScene 任何一张。

### 1.3 三个「有名字、没有对象」的落点（本轮要正面撞上）

1. **`SceneVersion.product: str`（`domain/scene.py:133`）与 `TemplateFamily.product: str`
   （`domain/outreach.py:628`）** —— 两根裸字符串，没有实体、没有版本、没有外键。
   实测全仓共 **9** 处 `product: str`（`command grep -rn "product: str" --include=*.py marketops/`）：
   域层两处（上面两根）＋ MCP 面七处（`adapters/mcp_server/outreach.py:732`、`:2130`、
   `product_loop.py:556`、`:755`、`:867`、`:4942`、`:4976`）。这一轮**不一次改九处**：
   先让域层那两根有东西可指，MCP 面那七处随各自的片一起走（它们今天收的是调用方递来的串）。
2. **`ResearchContext.product_use: str`（`domain/research_context.py:314`）** —— 它**进指纹**
   （`:787`/`:802`），却没有声明来源。
3. **`StrategyModuleId.PRODUCT_OVERLAY` / `WORKSTREAM_OVERRIDE`（`domain/research_plan.py:95-97`）**
   —— 同一个文件 `:113-114` 逐字承认：「两个可选模块（Product overlay / Workstream override）
   今天**没有声明来源**：profile 里没有 product overlay 块，全仓也没有 Workstream 这个概念」。

`Workstream` 在 `marketops/*.py` 里共 9 处命中，**全是注释**；其中 `domain/run.py:34-36` 逐字：
「``Workstream`` 在本仓**不存在**……这一格今天由 ``workspace`` 承载」。

### 1.4 一处**侦察错报的纠正**（记下来，免得下一个人再报一次）

自动化侦察报「`ScenarioProfile` 零命中」。**实测它存在**：`application/research_strategy.py:663`
`class ScenarioProfile(_Declared)`，是 B16 落的一个**策略模块**。

它与 B02 规格 §2 表里的「ApplicationScenario / Profile」**不是同一个东西**，这个边界必须写清：

* B16 那个 `ScenarioProfile` 是**策略模块**：从 profile 声明里读三格，产出这一轮计划的几格。
* B02 规格要的是**场景参照物**：Scene 的冻结绑定里那个 `scenario_profile_ref`（§5 逐字），
  它是一个**被引用的版本**，不是一次编译动作。

**所以 B02 不许去重造 B16 已经落了的那三格。** 它要做的是给 `SceneVersion.scenario` 那根裸串
一个可解析的参照物，并把解析结果与出处一起冻进 EffectiveScene。

### 1.5 三条既有约束（实测读出来的，不是猜的）

**(1) `SceneVersion.frozen_sha256` 落库，且读回时重算比对。**

`domain/scene.py:231` 是计算属性，但 `adapters/sqlite/db.py:584` 有一列 `frozen_sha256 TEXT NOT NULL`，
而 `adapters/sqlite/scenes.py:167-175` 在**读回时重算并与库里的值比**，不等就抛。
摘要串的第一个元素是一段**算法标签字面量** `"scene-version"`（`domain/scene.py:241`）。

> **结论：往 `SceneVersion` 加一个绑定字段并让它进摘要，会让库里所有已存的版本读不出来。**
> 这不是一个可以在实现时顺手决定的事，它是 B02-a 那一片必须正面处理的第一件事。

**(2) 「每个绑定字段都要动摘要」已经有执行器。**

`tests/test_domain_scene.py:254 test_every_bound_field_moves_the_frozen_digest` 遍历
`BOUND_FIELDS` 逐个改值断言摘要必变。新加一个绑定字段而忘了加进摘要，这条会说得出话；
但**先要有人把字段加进 `BOUND_FIELDS`** —— 它是一张手维护的表，不是从模型导出来的。
（同一条纪律的另一处：`tests/test_outreach_contract.py:961`。）

**(3) 本轮加的产品/Offer 类型**不落 `contracts/`**。**

`contracts/` 下 15 个文件实测不含 `ApprovedClaim` / `TemplateFamily` / `WritingPolicy` /
`SceneVersion`（`command grep -rl` 零命中）。所以给 `ApprovedClaim` 增作用域（§6 逐字
「复用当前 ApprovedClaim 并增作用域」）**不动任何已提交契约** —— 这一条是实测出来的边界，
不是假设。

### 1.6 一处命名上的旧账（本轮会被读成矛盾，先登记）

`adapters/mcp_server/outreach.py:646` 的 docstring 逐字自称是「给 TemplateFamily /
WritingPolicy / ApprovedProductClaims 一个**产品入口**」，而 `put_family` / `put_writing_policy` /
`put_claim` 三个工具**已经在 60 个工具的目录里、已经接线**。它写进去的是 family / policy / claim，
**没有 Product**。

「产品入口」这个名字是对的（它服务的确实是产品上下文），今天缺的是**被引用的对象**。
B01 不重命名这三个工具、不改它们的语义；它做的是让 `TemplateFamily.product` 与
`ApprovedClaim` 有东西可指。

## 二、B01 与 B02 的交界在哪

两条关闭条件读起来像两件事，实际是**一个链的两半**，交界只有一条：

* **B01 管「批准前」**：产品资料怎么登记、缺什么怎么报、启动前预览什么、谁批准、批准之后
  **受理成什么**（一条可回读的记录 + 实际队列状态），以及**重复提交不重建、不重置费用**。
* **B02 管「批准后怎么解释一份 Scene」**：把 Scene 的每一格格子解析成**确切的版本引用**，
  编译成不可变的 EffectiveScene，并让「同一份事实、各 Scene 各算一份评价」在盘上成立。

交界那一格是 **`OfferVersion` 的 ref**：B01 造出「一个被批准的产品版本/组合方案」，
B02 把它**解析进 Scene 并冻进 EffectiveScene**。两边都不许在这一格上自造值 —— B01 造的是
可被批准的对象，B02 造的是「哪一版被这一版 Scene 绑住了」这条事实。

**一个贯穿两边的硬要求**（`:503` 逐字「只有类名/字段/接口的无消费者实现不算完成」）：
每一片都要能在 MCP 面上走通，或者明确写出「这一片的消费者是下一片」并**不得**在那之前
把这一格算作完成。

## 三、切片表

每片一个提交，每片自带判据与故障回执。**判据先红**（先写下会让它红的东西，再改实现）。

| 片 | 内容 | 关掉哪一条 |
|---|---|---|
| b01-a | **产品目录的形状**：`SourceArtifactRef`（原始资料引用：字节出处 + 版本 + 摘要）、`ProductVersion`（能力 / 限制 / 区域变体 / 未知项 / 出处 / 负责人 / 状态）、`OfferVersion`（产品版本组合 / 用途 / 可用市场 / 允许 claims / 价格可缺省而**不可编造**）、`ProductLine`（可选分组，**零行为**）；`ApprovedClaim` 增作用域（产品 / 市场 / 语言 / 有效期） | 1 的前提 |
| b01-b | **登记面**：`ProductCatalog` 端口 + SQLite/内存两实现 + 三个 MCP 工具（`put_product_version` / `put_offer_version` / `put_product_line`）。**「登记不产生网络调用」做成构造而不是自律**（AST 白名单：登记面拿不到 `ports.search` / `ports.acquisition`） | 4 |
| b01-c | **缺什么要说得具体**：`ProductReadiness`（`missing` / `invalid` / `awaiting_approval` 三张清单），按 §10 逐字「返回结构化 missing/invalid/awaiting_approval，不让模型读一段 traceback 猜」 | 8 |
| b01-d | **启动预览** `launch_preview`：研究目标 / GeoScope / Scenario / **可选** Product/Offer / policy / 预算与周期 / 下一步动作 / 待批缺项。零外部调用同样是构造 | 5 |
| b01-e | **批准与持久受理**：`LaunchRecord`（版本化）+ 幂等三件事 —— 重复提交回**同一个** launch、不重建、不重置费用；返回排队状态与「何时会再醒来」（`:124`「不能只把一个表字段改 active」） | 6 / 7 |
| b01-f | **纯市场研究**：不带 Product 也能启动，并把「因此哪些能力不可用」**说出来**（不是默默少做，是明确限制） | 2 |
| b01-g | **一个获批产品版本被两个 Scene 共用**，且两个 Scene 的草稿**回链同一份资料版本** | 1 |
| b01-h | **claim 撤回**：受影响的未发草稿转 `hold`、无关任务继续；撤回是 scoped 操作且原子 | 3 |
| b02-a | **参照物与迁移**：`GeoScope`（含排除区）与 `SalesMotion` 的类型；`SceneVersion` 的 `region`/`product`/`scenario` 三根裸串接上可解析 ref。**迁移必须处理 §1.5(1) 那条摘要约束**（拟：ref 为空时摘要逐字节不变，非空时换算法标签并在同一片里给出「旧形态摘要不变」的实测判据）；§6 逐字「旧 `product: str` 迁移走**可核对 alias 映射**，歧义进入 review/draft，**不凭相似名字绑定**」 | B02 的前提 |
| b02-b | **`EffectiveScene` 编译**：`SceneVersion`（声明）→ EffectiveScene（解析后的不可变值），含 source / provenance / units / locale / owner / hash，**每一格的出处与规则优先级可解释**，冲突要能逐条点名而不是取一个赢家 | `:110` 的整段 |
| b02-c | **事实一份、评价各一份**：同一组织在两个不同 Offer 的 Scene 里，事实只有一份、两个 Scene 各有一份评价 | B02 关闭 1 |
| b02-d | **边界三条**：地区子集 Cohort 不越出 Scene 的 GeoScope；组合 Offer 可在同 Scene 内冻结、未获批组合不自动出现；终端与渠道的角色排除**不同且正确** | B02 关闭 2 / 3 / 4 |
| b02-e | **改产品版本优先重评、不全量重抓**：接 `scene_impact` 的 `must-reassess` / `must-refetch` 两侧，并给出「没有缺的事实就不抓」的判据 | B02 关闭 5 |
| b02-f | **暂停隔离**：暂停一个 Scene/Workstream 不影响无关 Scene，**且源端共享限额仍有效**（后者是跨 Scene 的那一半，落在 `ReuseLedger` 的 permission range 上） | B02 关闭 6 |

**本期不排片的关闭条款（登记，不假装）**：B02 那节末尾两句是**约束**而不是功能 ——
「若维护 ProductLine 分组或扩大国家范围，必须有来源/批准」与「默认值不可来自历史会话随口
建议」；它们的执行器是 b01-a 的批准字段与 b02-b 的出处要求，不再单独开片。

## 四、这份设计件不做的判定

* **不做通用 PIM**（`:534` 逐字「不要做通用PIM」）。产品目录止于「能被批准、能被引用、
  能被查缺」三件事：能力/限制/区域变体/未知项/出处各一格，不做 BOM、不做价格引擎。
* **不改 `ApprovedClaim` 已发语义**：增作用域是**加字段**，不是给旧 claim 补一个猜出来的作用域。
  旧记录的作用域为空 = 「全工作区有效」这个旧读数保持可读。
* **不重造 B16 的三格**（见 §1.4）。
* **不在这一轮打开 `bounded_auto` / `watch`**：B17-e/f 已经落了它们的形状，
  B01/B02 只负责让**启动**这件事有产品上下文可选。
* **不动 44 条既有的指针漂移**（那是「矩阵正文的指针维护」这件独立的事）。
