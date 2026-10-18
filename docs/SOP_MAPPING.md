# SOP 映射 —— 人工流程 → 自动化能力

```yaml
status:   SOP 已取得并编译进 profiles/；本次收敛把「已装载」与「已接线」逐条核准
sop:      三人群通道内的 SOP docx（原文在 records，见 docs/SOURCES.md §2）
compiled: profiles/uk-acme-natural-grass/{profile,grading,role-matrix}.yaml
```

---

## ⚠️ 阅读须知

本文不是 SOP 正文，也**不复制** SOP 正文。正文只有一份，在 records 里。

本文做的是 REQ → 模块 的映射：SOP 声明了什么，本仓库哪个模块承载它，
以及**当前还差什么**。前者是投影，后者是缺口 —— 两者都要写清楚，
因为一份只说「已映射」的文档会让人以为能力已经具备。

一句话：

```text
「SOP 说了什么？」       → profiles/<id>/*.yaml（编译产物）+ records（原文）
「哪些已经能跑？」       → 本文 §3 的「现状」列
```

---

## 1. 通用阶段的自动化状态

不论具体 SOP 如何，公开信息研究都包含这几个阶段。

| # | 阶段 | 人工做法 | 本仓库能力 | 状态 |
|---|---|---|---|---|
| 1 | 确定目标 | 从名单里挑账号 | `AccountRecord` + 三个队列 | ✅ 已实现 |
| 2 | 拟定检索 | 想几个关键词丢进 Google | `application/queries.py::build_queries`（**已知账号**）/ `application/openweb.py`（**还没有账号**时） | ⚠️ `query_templates` 与 `venue_types` 有展开器了，但**还没接进流水线**（那一圈 runner 属于 Phase 4 的 harness，本轮不执行）；`regions` 仍无消费者，且它该是「在范围内的国家」还是「检索式里的地区词」**未定**（见 `profiles/…/profile.yaml` 里那段注释）。站内检索那一族不在本仓 |
| 3 | 筛候选链接 | 看标题/摘要挑几条 | `SearchResult` | ✅ 已实现 |
| 4 | 打开页面 | 点进去看 | `HttpxFetcher` + `Crawl4AIBrowserFetcher` | ✅ 已实现 |
| 5 | 找联系方式 | 人眼在页面/PDF 里找 | `domain/extract.py` literal 抽取 | ✅ 已实现 |
| 6 | 判断归属 | "这个邮箱是谁的？" | `domain/bind.py` 保守归属 | ⚠️ 冲突时**主动放弃**，不猜 |
| 7 | 分两级判定 | 按 SOP 打 A/B/C/D 并判账号去留 | `application/grading.py::grade_route` + `grade_account` | ✅ **已接线**（两条轴分开，见 §4） |
| 8 | 记录归档 | 填表 | `SqliteEvidenceStore`（主存）+ `LocalEvidenceStore`（导出/审计）+ Bitable 契约 | ✅ 本地已实现，**飞书未接线** |
| 9 | 触达 | 发邮件 | —— | ⛔ **本阶段明确不做**（`send_authorized=false` 是模型层校验） |

第 7 步曾是最大的空缺：`grade_route` 与 `grade_account` 现在都能跑，
**账号准入**里那部分事实仍然没有生产者 —— 见 §4.1，它现在是一个被显式记录的距离，
而不是一次静默的失败。

---

## 2. 人工做法 vs 系统做法的关键差异

| 环节 | 人 | 系统 | 差异是否有意 |
|---|---|---|---|
| 判定"这个邮箱能不能用" | 凭经验，会脑补 | 只认字面证据 | ✅ 有意。脑补不可审计 |
| 遇到反爬 | 换浏览器 / 找同事要 | 标记受阻，换来源 | ✅ 有意。见 `ARCHITECTURE.md` §3.3 |
| 多个邮箱并存 | 挑最像的那个 | 标记冲突，留空 | ✅ 有意。宁可空，不可错 |
| 评级 | 综合直觉 | 只按配置规则 | ✅ 有意。规则必须可复现 |
| 有岗位但没邮箱 | 照样记下这个人 | 记为**路线机会**，不记成 B | ✅ 有意。见 §4.2 |
| 处理摘录里的邮箱 | 常常直接采信 | **拒绝**，必须抓原页 | ✅ 有意。见 `PRODUCT_SPEC.md` §3.1 |
| 评分 | 不会显式打分 | `scoring` 只用于排序 | ✅ 有意。**定级是规则的，不是分数的** |

**这些差异都是刻意的**：系统的价值在于**可审计**，而不是模拟人的判断力。
一个会"合理猜测"的系统在校验时无法区分"猜对了"和"运气好"。

最后一行值得展开：SOP 同时声明了离散的 `grades` 与连续的 `scoring`。把阈值调一调
比讨论「什么算 A」容易得多，而后者才是这个产品要沉淀的东西 —— 所以分数**不参与
定级**，它在 profile 里被显式标注为"仅用于排序与人工复核"。

---

## 3. SOP 声明 → 模块映射（⚠️ 已声明 ≠ 已能跑）

> 下表左列是 SOP 声明的形状，右列是本仓库的承载点与**当前是否真的能跑**。
> 「已声明」指的是规则进了 `profiles/`；**不等于**引擎能求值它。

| SOP 声明的部分 | 落在哪 | 现状 |
|---|---|---|
| target account 的形状（场景 / 场地 / 地域） | `profile.yaml` → `target_accounts` | ⚠️ 已装载；供查询生成（已接线）与相关性判断（未接线） |
| 搜索模板与场馆词表 | `profile.yaml` → `search` | ⚠️ 已装载，**无消费者** |
| 排除项 | `profile.yaml` → `exclusions` | ✅ 已装载；判定在 `grading.yaml → x_rules`（见下） |
| 账号准入 A/B/C/D… 的判定（`x_rules`，带 `status`） | `grading.yaml` → `x_rules` | ⚠️ **装载期已接线、运行期缺证据** —— 六条规则**全部**编译出了可求值的子句（`ungradeable` 为空），但五条引用的是**候选事实**，而账号行上还没有那些字段，取值为 `None`（未确认）⇒ 落 `unchecked`。今天能真的判的只有 `out_of_region` |
| 路线等级 A/B/C/D/X 的判定（`requires`） | `grading.yaml` → `grades` | ✅ **已接线** —— `grade_route` 按 A→B→C→D 求值，未匹配 → X + `rules_unknown` |
| 连续分与惩罚项 | `grading.yaml` → `scoring` | ⚠️ 已装载，**无消费者**（且不参与定级） |
| 页面证据联动规则 | `grading.yaml` → `evidence_rules` | ⚠️ 已装载，**无消费者** |
| 角色矩阵（7 个正向组 + 7 条 `excluded_roles`） | `role-matrix.yaml` → `role_groups` / `excluded_roles` | ✅ **已接线** —— `RouteVocabulary.matchable_role_terms`（正向 ∪ 排除）进 `domain/extract.py` |
| 记录块绑定语义 P1–P4 | `grading.yaml` → `page_priorities` | ✅ **已接线**（`P1` 的「同一 record block 内」自 2026-09-24 起真的被计算）—— `domain/bind.py` 产出 `block_priority`，且具名与岗位**先过一遍条目窗口**（`extract.in_the_same_entry`，行距 ≤ `ENTRY_WINDOW_BLOCKS`）才可能进 `P1` / `P2`；不落窗口的只在页面上出现**不算归属**，落 `entry_elsewhere` 拒绝理由 |
| URL 先抓哪些页 | `profile.yaml` → `page_discovery_hints` | ⚠️ 已装载，**无消费者**（扩展名与路径提示） |
| **同一个工作区里不同场景先看什么**（source / page / role 三轴分块） | `scenario-matrix.yaml` → `blocks`（**可选文件**，`marketops/scenario-matrix.v1`） | ✅ 已装载、装载期对账（块里的名字必须来自这份声明、必须保持声明的次序），**并且有消费者** —— B16-C2 起 `application/research_strategy.py::_scenario_choice` 按 `context` 里那一条可信读法选块、四个模块的 `cells` 按块收窄（`SOURCE_METHODS` / `PAGE_ARCHETYPES` / `ROLE_FUNCTIONS` / `ROLE_PRIORITIES` 四格真的收窄，另两格**收下而不读**）；说得清但没有那个场景的块 ⇒ 按整份声明写并在理由里写明，说不清 ⇒ 抛。真实 profile 今天**没有**这份文件：SOP 说过「责任实体是谁」（L27）与多类批量来源（L80），**没有按形态分支的 page / role 策略**，所以分块的内容留给 SOP 所有者裁决 |
| 邮箱 local part 业务词表 | `grading.yaml` → `business_entry_locals` / `generic_mailbox_locals` | ✅ **已接线** —— 决定 `entry_kind`（C 的业务入口 vs D 的泛化官方） |
| 归一化与优先级 | `role-matrix.yaml` → `normalization` / `priority` | ⚠️ 已装载，**无消费者**（抽取只做大小写不敏感的子串匹配） |
| 合规边界 | `profile.yaml` → `compliance` | ✅ 已装载并有断言守着（`tests/test_profiles.py::test_compliance_flags_stay_closed`） |

**一次真实的失效值得记在这里。** 上一版这一行写着「角色词表已接线」，
而实际接线的是**正向组**：排除词表从未进过匹配器，因此
`RoleClass.EXCLUDED` 在生产中不可达，SOP L129 的财务排除规则每次运行都安静地不生效。
它之所以没被测出来，是因为只测"正向角色被认出来"就够绿了。
修法是把两个词表并成一个显式命名的 `matchable_role_terms`，并在装载层拒绝
同一个词出现在两个类别里 —— 一份只在文档里写着"已接线"的接线，与没有接线
在输出上完全一样。

**第二处失效，与上面同一族，值得并排记着。** 上一版这一行写「✅ **已接线**」，
读起来是「P1–P4 的四条语义都算了」。实际接线的是 `block_priority` 这**一格字段**：
`_block_priority` 只看这一页上有没有具名、有没有岗位，而 SOP L105 给 `P1` 的原文是
「相关人 / 岗位 / 邮箱在同一 record block 内 —— 归属可以确认」。于是只要整页有一个
姓名与一个邮箱，判据就产出 `P1` + `STRONG`，**页面上那个姓名印在哪里、这三个字段
是不是同一张卡片，从来没有一处算过**。它比上面那次更难看出来：上面是词表少了一半，
这次字段有、取值有、等级词也对，只有那句「同块」是空的。

「SOP 里的一句限定词」与「一格枚举取值」不是同一件东西，接线要接在前者上。
修法是把归属从「页面上同时出现了」改成「与这条邮箱落在同一张卡片的条目窗口里」
（`extract.in_the_same_entry`），并给拒绝路径单独一句
`REASON_ENTRY_ELSEWHERE` —— 它必须与「这一页什么都没有」分开数，因为处置不同：
前者是**我们读到了但没归属上**，后者是**这一页没有可归属的东西**。

残留（如实记下，未修）：窗口锚在**邮箱**上，姓名与岗位各自距邮箱不超过一条，
因此两者彼此最远可以差两条。真正要的是同一块，这里取的是一条更松、但可计算的线；
把它收紧需要块切分，而块切分依赖源文件排版（见 `extract.block_ordinal` 的注释）。

---

## 4. 缺口的成因（不是「业务没给」）

上一版本文档把这一节写成「待业务方确认」。**那个前提已经不成立**：SOP 已取得，
A/B/C/D 的定义、角色矩阵、排除项都在 `profiles/` 里。

剩下的缺口是**工程侧**的。

### 4.1 账号准入引用的事实没有生产者

SOP L99 的账号准入规则写的是**账号级**事实。其中一部分本仓已经在生产
（`country` 就在 `AccountRecord` 上），其余**没有任何代码在计算**。

六条 `x_rules` 今天**全部**编译出了可求值的子句 —— 装载期不再有「规则点名了引擎
不认识的事实」这种欠账（`DeclaredGrading.ungradeable` 的判据，它现在是**空**的）。
缺的**不是名字，是值**：五条规则引用的是**候选事实**，它们的载体是发现期的候选行
（`marketops/domain/candidate.py`），账号行上没有那些字段，于是引擎手上是 `None`
（**未确认**），规则不决定、不命中、落 `AccountOutcome.unchecked`。

| 事实（引擎侧名字） | 谁该生产它 | 现状 |
|---|---|---|
| `country` | 账号自身的字段 | ✅ **有生产者**，今天真的判得了 |
| `exclusions_hit` | 命中 `profile.exclusions` 的排除项 | 候选行有字段，投影已写（`application/qualify.py`）；**值要等发现期填** |
| `venue_environment` / `organisation_kind` | 场地/场景属性抽取 | 同上 |
| `surface_class` / `surface_evidence_confirmed` | 表面类别 + 场地证据核验 | 同上 |
| `any_page_fetched` / `any_page_blocked` | 抓取统计 | 同上（候选行上是两个计数，投影折成「有没有」） |

**投影与生产者不是一回事。** 投影（`marketops/application/qualify.py::candidate_facts`）
已经有了，它保证「候选行上的字段 → 引擎事实名」这一段是字面的、键集恒等于词表。
缺的是**候选行自己的值**：本轮还没有任何代码把抓取结果填进 `AccountCandidate`，
所以 `None` 仍然是常态。投影接上之后，`unchecked` 从「这条路没接线」变成
「这一行还没有证据」—— 缺口的位置移了，但**没有关闭**。

SOP 原文用的是 `account.attributes.*` / `account.evidence.*` / `account.pages.*`
这类点分路径，引擎侧是**扁平**的名字，两边的对应关系由编译期决定、不是运行时映射
（见 `marketops/domain/facts.py` 的两张词表）。`account.contacts.*`
已不再是定级输入，见 §4.2。

**「哪些事实还没有值」由两处一起回答**：`DeclaredGrading.ungradeable` 答装载期的
**名字缺失**（现在是空），`AccountOutcome.unchecked` 答运行期的**值缺失**（非空，
点名了那五条规则）。空的那一份**不能**读成「账号侧已经对齐」——
`tests/test_profiles.py` 里那对测试（`test_every_account_rule_is_evaluable` /
`test_account_rule_facts_are_declared_at_load_time`）盯的就是这个区分。

**它不阻塞路线定级。** 这是本次收敛最重要的一处变化：账号侧缺事实，
曾经让 A/B/C/D 一起变成不可求值，因为两件事共用一个判定主语。分开之后，
`grade_route` 只需要路线自己的事实（literal 邮箱、绑定强度、角色类别、
入口性质、业务口径），这些都在生产。

### 4.2 判定主语：曾经的分歧，现在的两条轴（已关闭）

这是收敛过程中发现的**产品模型问题**，处置不是"对齐"，而是**承认它们不是一件事**：

* SOP L125–L129 的 A/B/C/D 定义在**一条联系路线**上 ——
  Account + Person/Department + Role/Business Function + literal 公开
  Email + Evidence；
* SOP L188 的 eligible / hold / irrelevant / duplicate 定义在**账号**上。

旧实现把两者压进**一个**函数：输入一个联系人，输出它的 A/B/C/D/X。于是：

1. `finance` 这类"角色类别"与"账号是否相关"共用一个判定入口；
2. 账号级条件（都需要账号级事实）一缺，联系人级等级也一起变成不可求值；
3. **B 被读成"有具名角色但没有邮箱"** —— 而 SOP L126 的 B 与 L125 的 A
   一样要求真实公开业务邮箱。把机会记成 B，等于声称有一条可联系的路线。

现在的结构是：

| 轴 | 函数 | 产出 | 事实来源 |
|---|---|---|---|
| 路线等级 | `grade_route` | `ContactRoute.grade` ∈ {A,B,C,D,X} | 路线自己的字段 + `page_priorities` + 词表 |
| 账号状态 | `grade_account` | `AccountStatus` | `AccountRecord` 自身字段 + `x_rules.requires` |

**两者互不决定**，因此「组织对、人不对」（账号 eligible、路线 X）可以被表达 ——
旧结构里这是最常见的真实情形之一，却表达不出来。

有具名角色、拿不到字面邮箱时，产出物是 `RouteOpportunity`：
它是一个模型类型，**没有 `email` 字段**，默认记录块层级 P3。
把「拿不到邮箱」做成一个类型而不是一个等级，是为了让"我们没有这条路"
在数据上不可能被读成"有这条路"。

### 4.3 一条仍然留着的规则缺口

`grading.yaml → known_gaps` 记着：SOP L79 把 Procurement 列为优先角色，
但 L125–L128 四条正面定义里没有哪一条容得下「强绑定的具名采购角色 + 字面邮箱」，
于是它落到 `X` + `rules_unknown`。

这是**刻意留着**的。正确修法是回 SOP 确认采购归口属于哪一级然后重编 profile；
在引擎里替 SOP 补一条规则，会让 profile 与原文分叉而没人发现。

---

## 5. 映射的维护方式

1. SOP 变更 → 先改 records 里的原文，再重新编译到 `profiles/<id>/`；
   **不要**只改 `profiles/`，那会让编译产物与原文分叉而没人发现。
2. 本文只描述映射与缺口。规则内容不写在这里（见 `docs/SOURCES.md` §4：
   本仓库的 `docs/` 是 projection，不是 authority）。
3. §3 表格里的「现状」列必须跟着实现变 —— 接线一条就改一条。
   让这一列停在"已声明"，是本文最容易发生、也最难发现的失效方式
   （§3 末尾那条已失效的接线就是例子）。
4. §4 的缺口清单会随事实生产者落地而变短。它的代码侧对应物**不是一个**：
   `ungradeable` 答「哪个事实名引擎还不认识」（今天空），`AccountOutcome.unchecked`
   答「哪个事实名引擎认识、今天还没有值」（今天非空）。两者会朝不同方向动 ——
   把一条散文规则改写成 `requires`，`ungradeable` 变短而 `unchecked` 变长。

`tests/test_profiles.py::test_business_rules_are_not_hardcoded_in_engine`
会持续保证：业务规则只存在于 profile 文件里，不会渗进引擎代码。
