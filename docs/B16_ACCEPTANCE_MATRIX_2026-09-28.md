# 上下文编译验收矩阵 · 逐行对账（2026-09-28）

这份文件把 `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B16 的 **7 条关闭条件**逐行落成
可核实的证据指针：每一行给出机制、生产者、消费者、正证据、负证据、标注（fixture /
纯函数 / live）、状态，以及**缺口归谁**。

它**不改**计划文件，也不改 `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md`。那份矩阵是
`convergence_start_sha` 的快照，它 §2 末尾逐字写着：

> **这一页不是活读数，今天也没有别的地方是。** B16 的活读数会落在它自己的验收矩阵上
> （按计划 §B16 的 7 条关闭条件逐行对账，形状照
> `docs/ACQUISITION_ACCEPTANCE_MATRIX_2026-09-28.md`），而那份文件**在本片里还没有写**。

这一份就是那份文件。两份各自完整：它记的是「当时量到什么」，这一份记的是「今天这一棵
树上，哪一条真的被守住了」—— 把前者的那句「还没有写」改掉，等于把一份在记录观测时点
的文件改写成事后追认。

## 状态词表

| 状态 | 含义 |
|---|---|
| `DEMONSTRATED` | 有生产者、有消费者，正负证据都在本仓测试里，且**读得回** |
| `PARTIAL` | 某几格有判据，另几格没有；缺口逐条写在该行的「缺口」栏里 |
| `NOT_BUILT` | 全仓没有实现（不是「没测」——是「没有那个东西」） |
| `OUT_OF_SCOPE_HERE` | 属于别的切片（B17/B01…），本矩阵只登记归属 |

**这份矩阵不宣称任何 live 结果。** 下面每一条证据都是 fixture 或纯函数判据，零网络
调用，零 provider 调用。§18 的 provider 版本与现场验证不在这份文件里。

**不给完成百分比。** 7 行里 `DEMONSTRATED` 几行、`PARTIAL` 几行是**行数**，不是进度；
每一行的判词各自带着自己的证据。

## 0. 时点与基线读数

| 格 | 值 |
|---|---|
| 计划权威 | `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B16（7 条关闭条件，本文件逐行对号） |
| 本文件读数时点 | `25397c68e226f2bd09adc6d75638058d504ad806`（B16-b 3/3，初版） |
| 后续复核 | 条件 2 那一节在 `06c3afd`（B16-C1）上读过、在 B16-C2 上复核并改写；它的缺口第 3 条在切片 D 上关闭（那一节的缺口栏随之改写）；条件 5 那一节在切片 E 上复核并改写（前半的判词从「这个动作在盘上不存在」改成「排序没有运行期自由度」，后半补三条执行器，缺口从三条收成一条）。其余各条仍是初版读数，七行判词未变 |
| 全量套件 | `5736 passed, 4 skipped`（初版）→ `5765`（B16-C1）→ `5775 passed, 4 skipped`（B16-C2）→ `5778 passed, 4 skipped`（切片 D，+3 = 第 9 节三条）→ `5783 passed, 4 skipped`（切片 E，+5 = 第 10 节五条） |
| `ruff check .` | 干净（未跑 `ruff format`）；B16-C1 / B16-C2 / 切片 D / 切片 E 各复核一次，仍干净 |
| `python -m marketops.schema` | 重生成后工作树**无 diff**（初版那一轮）；B16-C1/C2/D/E 不新增契约，未重跑 |
| B16 落地序列 | `ec7c45d`（a · 输入面）→ `e96cfca`（b 1/3）→ `4282ada`（b 2/3）→ `25397c6`（b 3/3）→ `1bbca7c`（本文件）→ `edbe4bd`（c2a）→ `385a0d6`（c2b）→ `06c3afd`（C1）→ `3c768a5`（C2）→ `5f024a7`（D）→ 切片 E（本片） |
| 七行判词 | 1 `DEMONSTRATED` · 2 `PARTIAL` · 3 `DEMONSTRATED` · 4 `DEMONSTRATED` · 5 `PARTIAL` · 6 `PARTIAL` · 7 `PARTIAL` |
| 缺口去向 | 条件 2 的第 1/2/3 条（C2 与切片 D 已关）、第 4 条（这条链只从 tests 可达），与条件 1（MCP 格）、条件 5 两半的运行期那一面、条件 6、条件 7 的缺口指回**同一处空宿主** —— `RunSpec` 今天在 `marketops/` 与 `tests/` 下零命中 —— **全部归 B17**；条件 5 缺口第 2/3 条（排序面的形状、拆 Scene 的入口闭集）已在切片 E 上关掉 |

那两行是**行数**，不是进度（见下面「不给完成百分比」）。写在这里是因为七行的判词有一个共同
形状：**要求运行期的那一半今天没有宿主** —— 而 B16 能自己关的两处（条件 2，与条件 5 里不依赖
运行期的那两格）在切片 C2 / D / E 上已经关完，剩下的缺口没有一条是 B16 关得掉的。

`§B16` 开头对「输入」的要求（Member-A 明确给出的 objective、GeoScope、对象/场景描述、
可选产品用途、输出需求、预算与硬边界；**历史模型猜测不能自动扩大范围**）落在
`marketops/domain/research_context.py`，它的读数在 `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md`
§2 的注里已经记过：那两个名字存在了，但「都还没有生产消费者」—— **那一半今天已经不成立**，
消费者是 `marketops/application/research_strategy.py`（四个模块）与
`marketops/application/plan_preview.py`（`preview_plan`）。

---

## 逐条对账

### 关闭条件 1 · GB/FR/DE + 至少一个多语地区 fixture，同一自然语言任务稳定编译可复现 plan digest；preview 零 provider 调用

**机制。** 两条要求分属两半：

* **前半（四个市场 / 可复现 digest）** —— `research_compiler(bundle).compile(...)` 把一份
  声明编译成 `EffectiveResearchPlan`，`digest` 是**内容摘要**而不是路径或时间戳
  （`marketops/domain/research_plan.py`，逐格进摘要的字段清单在 `PlanField` 那一张表里）。
  四个市场是同一份**任务**（同一个 `TASK` 常量：entity/asset/organization classes、一个
  DECLARED 场景候选、objective、evidence_requirements）配上四份不同的声明：
  `MARKETS = {"gb": ("GB", ("en-GB",)), "fr": ("FR", ("fr-FR",)), "de": ("DE", ("de-DE",)),
  "ch": ("CH", ("de-CH", "fr-CH", "it-CH"))}`。多语地区取 CH，**三条语言全在且按声明序**。
* **后半（preview 零 provider 调用）** —— `marketops/application/plan_preview.py` 的
  `preview_plan(bundle, context)`：**两个参数，没有缺省**，且这一层**拿不到端口**
  （不是「拿了没用」，是构造上拿不到）。零调用因此不是一句承诺，是这一层能做什么的
  全部内容 —— 判据是两条 AST 扫描，不是自觉。

**生产者**：`research_compiler` / `StrategyCompiler.compile`（`marketops/domain/research_plan.py`）、
`preview_plan`（`marketops/application/plan_preview.py`）。
**消费者**：`PlanPreview.as_dict()` —— 今天只有测试读它；**MCP 面今天不存在**，见下面的缺口。
**存储/事务**：无（这一层不落盘，这是它的定义里的一句话）。

**正证据**：
* `tests/test_plan_preview.py::test_four_markets_compile_the_same_task_into_four_plans` ——
  四个市场各自 READY，且 `plan.locales == locales`（多语那一份**一条不少、按声明序**）。
* `tests/test_plan_preview.py::test_the_same_market_and_the_same_task_compile_to_the_same_plan` ——
  同一份声明 + 同一份任务两次预览，比的是 **`as_dict()` 逐字相等**而不只是 `digest`：
  一份摘要相同、报告不同的实现会让「可复现」停在摘要那一格上。
* `tests/test_research_strategy.py::test_two_compiles_of_one_declaration_are_identical`、
  `::test_a_revision_is_a_content_digest_not_a_path` —— 模块级摘要同样是内容摘要。
* `tests/test_research_strategy.py::test_the_real_profile_fills_the_eight_writable_cells` ——
  真 profile 能填满可写的八格（不是只有 fixture 能）。

**负证据**：
* `tests/test_plan_preview.py::test_a_different_market_is_a_different_plan` —— 四个市场**两两
  digest 不同**。少了这一条，「两次编译可复现」会在一份**忽略了市场**的实现上照样成立
  （它每次都很稳定地编出同一份东西）。
* `tests/test_plan_preview.py::test_the_multilingual_market_keeps_all_of_its_locales` ——
  单拎出来是因为它有两个不同的失败形状：少留几条（收窄过头）与顺序变了。
* `tests/test_plan_preview.py::test_the_layer_takes_exactly_a_declaration_and_a_context` ——
  签名**恰好**那两样。多一个参数（哪怕带缺省）就红。
* `tests/test_plan_preview.py::test_the_layer_imports_exactly_what_it_needs` —— import 白名单
  **精确相等**（十个），`ports.*` 与 `adapters.*` 进不来。
* `tests/test_plan_preview.py::test_the_layer_calls_no_io_and_no_clock` —— 两张调用表（内建
  与属性）分开查，`open` / `read_text` / `time` 两类都挡住。
* `tests/test_plan_preview.py::test_the_layer_catches_nothing` —— 模块里**一个 `ast.Try`
  都没有**，所以这一层不可能靠「接住再降级」把一次失败伪装成一次正常的空转。

**标注**：fixture（四个市场各 `copytree` 一份 profile 到 `tmp_path`）+ 纯函数（两条 AST
扫描读的是源码文本，零执行）。**零 provider 调用**这一点由构造保证，不由 fixture 保证。

**状态**：`DEMONSTRATED`。

**缺口（一条，归属明确）**：**MCP 读取/控制这一格今天不存在。**
`PlanPreview.as_dict()` 就是将来那个面要交出去的 JSON，所以将来那一步是**投影**而不是
第二份实现；今天不接的理由是 `ResearchContext` 在测试之外**没有生产者** —— 接一个只读
工具就得发明 context 的线上形状，而那是 §B17 的 `RunSpec` 的事（计划 §B17 逐字把
`ResearchContext ref` 与 `EffectiveResearchPlan ref` 列在最小 `RunSpec` 里）。先发明形状
再回头改，等于把一次投影做成两次实现。**归 B17。**

---

### 关闭条件 2 · 同一 Workstream 的学校/高尔夫/维护商等不同 context 得到不同 source/page/role 策略，但共享事实/工件仍复用

**机制（前半 · 入口在 C2 之后开着）。** `ScenarioProfile`
（`marketops/application/research_strategy.py:663`）从声明里读 `page_discovery_hints` 与
`role-matrix`，产出 `PAGE_ARCHETYPES` / `ROLE_FUNCTIONS` / `ROLE_PRIORITIES` 三格；
`GeoLocaleSourceProfile`（`:490`）产出 `LOCALES` / `QUERY_FAMILIES` / `SOURCE_METHODS`。
这四格按场景收窄的入口是 `_scenario_choice`（`:212`）：它从 `context` 里那**一条可信
读法**（`effective_scenario`，`domain/research_context.py`）取 `scenario-matrix.yaml`
上对应的块。四个模块的 `cells` **都收下**这一次的 `_ScenarioChoice`，其中两个真的按块
收窄（`GeoLocaleSourceProfile` 的 `SOURCE_METHODS` 与 `ScenarioProfile` 的三格），另两个
（`ObjectivePolicy` / `AccessStrategy`）**收下而不读** —— 那两格今天没有按场景分的声明
依据，收下是为了让「这一次是哪个场景」在四个模块里是**同一个输入**，而不是让它们各自
去问一遍请求（判据见下面的「负证据」）。

**这几格在 C2 之前是「声明里唯一的那一套」。** 那时换一套 context 只改
`EffectiveResearchPlan.fingerprint_digest`（`ambiguities_of`、
`fingerprint_of`），**一格策略都不动**。`geo` 那一格也不随 context 动：它来自
`envelope.geo`（`research_plan.py:492`），context 只被拿去做越界检查。今天随 context
动的有四路：`candidate_locales` → `LOCALES`（**收窄**声明，不是另给一套）、
`jurisdiction` → `JURISDICTION`、`product_use` → `PRODUCT_USE` / `PRODUCT_CLAIMS`，
以及**场景读法 → `SOURCE_METHODS` / `PAGE_ARCHETYPES` / `ROLE_FUNCTIONS` /
`ROLE_PRIORITIES` 四格**（C2 新增的第四路）。

一条边界要按**行为**说而不是按字段名说：`ResearchContext.entity_classes` /
`asset_classes` / `organization_classes` / `scenario_candidates` / `objective` 这五个
字段名在 `research_strategy.py` 与 `research_plan.py` 里仍**一次都不出现**，但这不等于
「这两层与 scenario 无关」—— 第四路走的是 `effective_scenario`，而它读的正是
`scenario_candidates`。**`entity_classes` 那一侧今天确实仍无入口**：这四格随场景
**读法**动，不随 `entity_classes` 动。

**机制（后半 · 共享事实/工件）。** 共享的边界是 `workspace` 而不是场景：
`marketops/ports/pool.py`（场地资产/关系/账号/冻结名单四个 ABC）、
`application/{pool,accounts,relations,motherpool}.py`、`domain/evidence.py:124-145` 的 store
按工作区划界、`application/reuse.py:156` 的 `permission_range` 同样是工作区。
盘上**不存在**一个叫 `shared_pool` 的模块或表；「共享池」这个词的代码落点只有
`plan_preview.py` 与 `research_context.py` 的散文。

**上面这一段在切片 D 之前只是散文。** D 之后它有三条执行器，都在
`tests/test_research_strategy.py` 第 9 节：
`::test_the_plan_chain_cannot_reach_a_pool_or_an_evidence_store`（计划链够不到池 / 证据 /
复用）、`::test_the_request_carries_no_cell_for_a_pool_or_a_workspace`（请求面上没有一格
能说「用哪口池 / 哪个工作区」）、`::test_the_pool_and_the_evidence_layers_do_not_know_about_the_plan`
（反方向也够不到）。三条一起说的是**分家没有入口**；「仍按工作区共享」那一半的判据在
`tests/test_page_reuse.py`（见缺口第 3 条 —— 两半合起来才是这一条要求）。

**正证据（单点判据，每一格各自等于声明里的什么）**：
* `tests/test_research_strategy.py::test_locales_come_from_the_declared_search_locales`
* `::test_query_families_are_the_declared_templates_verbatim`
* `::test_source_methods_are_the_routes_the_declaration_opens`
* `::test_page_archetypes_are_the_declared_hints`
* `::test_role_functions_are_the_declared_business_functions`
* `::test_role_priorities_are_the_declared_class_order`

**这一半的判据在 C2 之后范围变清楚了**：
`tests/test_research_strategy.py::test_only_the_locale_cell_moves_with_the_context` 断言
`wide.locales != narrow.locales` 且**其余七格逐字相同** —— 它用的是**没有**写
`scenario-matrix.yaml` 的声明，于是三根轴那一侧走 `scenario_matrix is None` 那条分支
（四格按整份声明写）。它钉住的是「在这些声明上，收窄只发生在语言那一格」，**不是**
「三根轴没有入口」—— 后者是 C2 关掉的那一半，由下面的判据顶住。

**负证据（关掉条件 2 的那一条）**：
`tests/test_research_strategy.py::test_two_readings_of_one_declaration_differ_only_on_the_three_axes`
—— **同一份声明**、两个读法（`k12` / `golf_course`），断言那四格两组不同、其余格逐字
相同、`modules` 逐字相同（版本不随场景分家）、`digest` 不同。同一节里还有两条各管一头：
`::test_a_scenario_block_narrows_the_four_cells_and_not_the_version`（块进格子与理由、
不进版本）、`::test_a_scenario_with_no_block_keeps_the_whole_declaration`（矩阵里没有
那个场景的块 ⇒ 八格回到基线，理由里写明按整份声明写）。这两条都是 C2 新增的。
`tests/test_plan_preview.py::test_a_different_market_is_a_different_plan` 比的是
**四份不同 profile 声明**（四个国家），不是同一声明下的两个 context —— 它不是这一条。

**标注**：fixture（`tmp_path` 上的声明块）+ 纯函数；另一条是 AST 扫描（四条读记录各
收一次场景、全文只有一个选择点）。第 9 节那三条也是 AST 与字段表 —— 结构判据，不摆行为
判据：后半的行为面要一次 Run 才能观察，而 Run 面属 B17。

**状态**：`PARTIAL`。机制这一半在 C2 之后是开着的（入口 + 选择 + 判据都有了），仍是
`PARTIAL` 的原因有两处，都不是机制：**内容** —— 真实 profile
（`profiles/uk-acme-natural-grass/`）的三份必需声明之外**没有**第四份
`scenario-matrix.yaml`（`adapters/profile_loader.py` 逐字：它是「三份必需文件之外的
第一份**可选**声明」），所以在那一份声明上两个场景今天编出的仍是同一份计划 ——
这一点由 C2 的探针执行：同一个公开口子（`research_compiler(...).compile(...)`）在
`06c3afd` 与 B16-C2 两棵树上跑同一份真实 profile，两份 JSON **逐字节相同**
（各 9267 字节）；**宿主** —— 这条链今天只从 tests 可达（缺口第 4 条，归 B17）。

**缺口（四条；前三条归 B16 自己，第四条归 B17）**：
1. ~~声明里没有「按场景分块」的入口~~ —— **C2 已关**（入口在 c2a/c2b，选择与判据在 C2）。
   一处与本节原措辞的分歧要写在明处：本节原来要求「选不到就**抛**，不回落默认块」，
   而落地的裁定是**说得清但没有那个场景的块 = 按整份声明写 + 在理由里写明**。理由是
   这一层的两个失败不同：**说不清**（零条或两条以上可信读法）是**错误**，照抛
   （`::test_an_unclear_scenario_is_not_resolved_by_the_block`）；**说得清而这个场景
   没人写块**是**声明还没写**，把它变成编译失败会让「加这一份文件之前」的每一份声明
   当场编不出来 —— 真 profile 今天正是这种状态。
2. ~~跨 context 的对比判据~~ —— **C2 已关**，见上面的「负证据」。落地的那一条比本节
   原措辞更强：不只「允许其余格相同」，而是断言其余格**逐字相同**、`modules` 逐字相同
   （否则一个「每个场景都重编一遍」的实现也能靠「不同」过关）。注意它由 fixture 声明
   执行 —— 真实 profile 上这条今天仍是同一份计划（见上面的「状态」）。
3. ~~后半要一条钉住「池与证据仍按工作区共享、不随场景分家」的判据~~ —— **切片 D 已关**。
   落地的三条都在 `tests/test_research_strategy.py` 第 9 节，**三条都是结构判据**（这一半
   的行为面要一次 Run 才能观察，而 Run 面属 B17）：计划链三个模块按 AST 够不到池 / 证据 /
   复用（import、标识符、字符串三条路，每条各带反控）；`ResearchContext` 的十二格与
   `ResearchEnvelope` 的四格**逐字成闭集**（`extra="forbid"` 那一半也钉住 —— 请求面上
   没有一格能说「这一次用哪口池」「这份事实属于哪个工作区」）；反方向扫 `ports/` 全部与
   证据 / 复用的那几个模块，禁词表零命中。**「仍按工作区共享」那一半的判据仍在
   `tests/test_page_reuse.py::test_two_subjects_on_one_url_pay_once_a_tick`**（同一个工作区
   里的两个**主体**只付一次取回）—— D 钉的是它的另一半：**分家在这条链上没有入口**，
   两半合起来才是这一条要求。
   一处由 D 的判据自己撞出来的读数：`plan_preview.py` 的 `NOT_REPORTED` 里逐字写着
   「「池子里已经有什么、这一次复用了多少」要读共享池才知道，而这一层只有声明与上下文
   两样输入。持有它的是 §B17 的 Run preview 与 `application/reuse.py` 的 `ReuseDecision`」
   —— 也就是这一层**自己声明了够不到**。那条判据第一次跑就撞上这句话：它当时按**类名**查
   字符串，把「点名持有者」误判成「够到」，于是改成只查模块路径。点名不是接线。
4. **这条链今天只从 tests 可达**（这一条与 D 无关，是原第 3 条的后半）：`preview_plan` /
   `research_compiler` / `StrategyCompiler` / `build_plan` 在 `marketops/` 下除自身外零
   调用者，`adapters/` 与 `interfaces/` 里 `ResearchContext` / `EffectiveResearchPlan` /
   `plan_preview` 零命中。**归 B17** —— 真实调用者要随 Run 面一起出现，而那不是判据能
   造出来的东西。在那之前，这一行的判词只能是 `PARTIAL`。

**这一行的外部要求行**：
* `docs/ACQUISITION_AND_PROVIDER_DESIGN.md:87` 逐字「``scenario/entity class`` 决定什么责任
  关系、网页类型、岗位词和证据最值得找」—— 「网页类型」与「岗位词」对应的正是计划上的
  `PAGE_ARCHETYPES` 与 `ROLE_FUNCTIONS` / `ROLE_PRIORITIES`。C2 之后这三格随 **scenario
  读法**动（真实 profile 没写那一份声明，所以那份声明上今天仍然没有差异）；`entity
  class` 那一侧**仍没有入口** —— 这一句里的两个词今天只兑现了前一个。
* `docs/ACQUISITION_AND_PROVIDER_DESIGN.md:96` 逐字「若歧义仅影响低风险 query/page 排序，
  可在已批准 Workstream envelope 内使用保守计划」—— 这一句同时是条件 5 前半的授权边界，
  两条条件共用它。

---

### 关闭条件 3 · Country 与 language 分离：多语国家不会被强塞单一 locale；翻译不覆盖原文 title/evidence

**机制（前半 · 两格来源分开，三处一起挡）。**

* **声明层**：`DeclaredProfile.country`（`marketops/domain/profile.py:349`）与
  `search_locales`（`:375`）是两个独立字段，`envelope_of`
  （`domain/research_context.py:424-427`）分别把它们落进 `ResearchEnvelope.geo` 与
  `.locales`，中间**没有任何互相推导**。
* **类型层**：`domain/util.py::check_market_language` 只收**主语言子标签**（`en`），
  明确拒收 `en-GB`，理由逐字是「收下 ``en-GB`` 就等于把「英国 = 英语」这句话焊进这一格，
  于是「法国、英文」「英国、波兰语」这两种真实存在的组合在类型上表达不出来」；
  `primary_language_subtag` 从标签里只取第一段，逐字写着「**不推断**」。
* **计划层**：语言格的唯一写者是 `_locales_cell`
  （`application/research_strategy.py:275-320`）：值 = 声明里的 locales 与
  `context.candidate_locales` 的**交集**，按**声明序**去重，**从不读取 country/geo**
  （`PlanField.GEO` 与 `JURISDICTION` 是 `HARD_SCOPE_FIELDS`，`LOCALES` 在
  `MODULE_WRITABLE_FIELDS` 里，两条路不交叉）。
* **检索层**：`application/queries.py::market_locale` 在 `search.locales` 声明多种语言时
  **抛 `ConfigError`，不取第一条** —— 多语国家不会被静默压成单一语言。

**「没有 country→language 表」是可核的读数**：`command grep -rnE "\{\s*\"[A-Z]{2}\"\s*:"
marketops/ --include=*.py` **零命中** —— 全仓没有任何以国家码为键的表，`domain/` 下也没有
语言表。全仓唯一「语言标签 → 内容」的表是写信骨架
（`adapters/deterministic_writer.py:227` `_SKELETONS`），那是信件措辞、不是 locale 推导，
且表外的 locale 一律 hold。

**正证据**：
* `tests/test_plan_preview.py::test_the_multilingual_market_keeps_all_of_its_locales` ——
  多语地区（CH）三条语言**全在且按声明序**（`("de-CH", "fr-CH", "it-CH")`）。
* `tests/test_plan_preview.py::test_four_markets_compile_the_same_task_into_four_plans` ——
  四个市场逐一 `plan.locales == locales`。
* `tests/test_research_strategy.py::test_geo_and_locales_move_independently` —— 只改
  country 得到 `("FR", ("en-GB",))`、只改 locales 得到 `("GB", ("de-DE",))`：两格来源
  不是同一块声明。
* `tests/test_search_market_locale.py::test_country_and_language_are_two_axes_in_the_request`
  —— GB+fr 与 FR+en 发出**两个不同的请求体**。

**负证据**：
* `tests/test_search_market_locale.py::test_two_declared_languages_fail_closed_at_use` ——
  声明了两种语言时使用点**失败关闭**，不取第一条。
* `tests/test_research_strategy.py::test_a_request_for_an_undeclared_locale_is_refused`
  （请求里点名一个声明里没有的语言 ⇒ `ConfigError`，报文含 `de-DE`）、
  `::test_a_locale_outside_the_envelope_is_refused`（声明两种语言而
  `envelope.locales=()` ⇒ 抛，报文点名 `envelope.locales`：空 envelope **不是**
  「不限语言」）、`::test_only_the_locale_cell_moves_with_the_context`（收窄语言时其余
  七格逐字不变）。

**机制（后半 · 翻译不覆盖原文）。** 这一句成立的方式是**构造**：全仓**没有翻译器** ——
`command grep -rniE "translate_|translator|machine.translat|localize\(|译文|localiz|localis"
marketops/ tests/` 零命中，而 `translat` 的其余命中全是「把一种内部表示译成另一种」的
内部词汇（`ports/dispatch.py`「本仓不翻译它」、`application/research_terminal.py`
「**不翻译**它们：两套语义不是同一个」）。所以这不是「有翻译能力而选择不覆盖」，是那条
路不存在。原文由三层钉住：

* **类型层**：`domain/evidence.py:150` 的 `literal_value`，校验器 `:216-223` 只要求非空
  且无首尾空白，**不做任何改写**。`Evidence` 上**没有** `title` 字段 —— 标题是一条独立
  证据行的 `literal_value`。
* **写入层**：`application/evidence_ingest.py:143` 正文 `literal = document.text.strip()`、
  `:234` 标题 `title = document.title.strip()`。**唯一加工是截断**，且截断不静默（正文的
  截断事实写进 `:181-186` 的 `page_locator`，标题走 `MAX_TITLE_CHARS`）。换主语那一趟
  逐格照抄：`application/promotion.py:656` / `:662`，docstring 逐字「观测本身一格都没变」。
* **存储层**：同 id 不同内容**拒而不覆盖** —— `adapters/jsonl/store.py:347-359`
  `_reject_conflicting_payload`、`adapters/sqlite/evidence_store.py:123-128`。
* 判据：`tests/test_evidence_ingest.py::test_the_literal_is_actually_in_the_source_text`
  （`literal_value in document.text`）、`::test_the_title_row_carries_the_name_the_material_wrote_for_itself`
  （`literal_value == document.title`）、`::test_the_title_is_not_necessarily_inside_the_body_text`。

**标注**：fixture + 纯函数。

**状态**：`DEMONSTRATED`。

**缺口 / 这一行没做到的（三条，都记读数不给建议）**：

1. **多语样本只有 CH 一份。** 「少留几条」与「顺序变了」两个失败形状由
   `test_the_multilingual_market_keeps_all_of_its_locales` 抓到，但样本数不因这条判据
   变多。这是样本面的事实，不是缺判据 —— 本条的三处构造（类型层拒收、使用点抛错、
   计划层保序）各自独立，「多语被压成单一语言」要同时绕过三处才成。
2. **`literal_value` 不恒等于完整原文。** 截断会缩短它（正文与标题各一处）；`literal_value`
   是「原文里的一段」，不是「原文」。这一句在本行里写出来，因为「原文不被覆盖」很容易被
   读成「原文被完整保存」。
3. **「页面级语言检测」这一支没有生产者。** `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:225`
   那一句（「页面级语言检测只影响查询/解析/翻译策略；原始 title/evidence 保留原文」）的
   **后半**就是本行上半关住的；**前半**在仓内没有实现 —— `command grep -rn
   "detect_language\|page_language\|content_language" marketops/ --include=*.py` 只命中
   `adapters/laya.py:339`（设备决策的语言检测，不是页面）。它属于 §B16「动态识别规则」
   第 4 条，不是这 7 条关闭条件之一；本行登记它的读数，因为条件 3 引的那句话与它同源。
   **归 B16 自己。**

**这一行的外部要求行**（要求写在哪，便于对照）：
`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:233`（本行）、`:225`；
`docs/ACQUISITION_AND_PROVIDER_DESIGN.md:91`（「原始职位、页面和证据始终保留原文；
翻译/规范化是派生层……不能把翻译后的 title 冒充来源原文」）；
`docs/DATA_MODEL.md:97`（英文本：「Country and language remain separate fields;
translation/normalization never overwrites literal source text.」）。

---

### 关闭条件 4 · 模糊输入影响 hard scope 时明确 hold/review；不能由模型置信度越过

**机制。** 三张表与一次拒绝回答（B16-b 1/3 立的）：

* `ContextDimension` 十格；**硬的那八格**是 `GEO` / `JURISDICTION` / `ENTITY_CLASSES` /
  `ASSET_CLASSES` / `ORGANIZATION_CLASSES` / `SCENARIO` / `OBJECTIVE` / `PRODUCT_USE`
  （`marketops/domain/research_context.py::_HARD_AMBIGUITY_DIMENSIONS`），软的两格是
  `LOCALES` / `EVIDENCE_REQUIREMENTS`。
* `ambiguities_of(context)` 产出「客户端声明的 ∪ 推出来的」；`ambiguity_is_hard(dimension)`
  只查那两张表。
* `preview_plan` 在**编之前**先问这张表：`held` 非空就**根本不进编译器**，返回
  `PreviewOutcome.HOLD` 并把硬的那几条**逐条点名**。不是接住 `ValueError`，是问同一张表。
* 「不能由模型置信度越过」由 **provenance** 挡，不由 confidence 挡：
  `_CREDIBLE_PROVENANCE = {DECLARED, POOL_FACT}`，`MODEL_GUESS` 不在里面；
  `ambiguities_of` 对 SCENARIO 的判据是「**可信**候选的读法去重之后不是恰好一条」。
  `ScenarioCandidate.confidence` 的字段注释逐字写着它「只记录，不参与『要不要停下来问人』
  的判定……硬那一半读都不读它」。

**生产者**：`ambiguities_of` / `ambiguity_is_hard`（`marketops/domain/research_context.py`）、
`preview_plan`（`marketops/application/plan_preview.py`）。
**消费者**：`PlanPreview.held`（报告里的那一栏）与 `StrategyCompiler.compile` 的硬门
（`fingerprint_of` 抛 `ValueError`）—— 两个读者读同一张表。

**正证据**：
* `tests/test_plan_preview.py::test_a_hard_ambiguity_holds_instead_of_reporting_a_plan` ——
  **8 个参数化用例**，六条推导（geo / 三个类别 / scenario / objective）+ 两条**声明**
  （JURISDICTION / PRODUCT_USE），每条都断言 HOLD 且**逐条点名**。
* `tests/test_plan_preview.py::test_the_two_readers_of_one_table_agree` —— 从编译器抛出的
  消息里正则读回那几条维度（`^\s+- \[([a-z_]+)\]`），与这一层的 `held` **逐条比**；
  8 个用例同样是「硬的那八维覆盖满」。
* `tests/test_plan_preview.py::test_a_preview_that_contradicts_itself_cannot_be_written_by_hand` ——
  三条 `__post_init__` 守卫：计划缺席与 HOLD 必须同步、HOLD 必须点得出名、READY 必须
  有来源。自相矛盾的预览**写不出来**。
* `tests/test_research_context.py::test_a_high_confidence_guess_does_not_settle_the_scenario`
  （`confidence=1.0` 的 `MODEL_GUESS` 仍然定不了场景）、
  `::test_two_credible_readings_are_an_ambiguity_and_confidence_is_not_an_input`、
  `::test_the_credible_provenance_set_has_no_room_for_a_guess`。

**负证据**：
* `tests/test_plan_preview.py::test_a_hold_never_reaches_the_compiler` —— 把 `research_compiler`
  monkeypatch 成一个**炸弹**，硬歧义那条路依然 HOLD。这条证明「先问表」而不是「接住异常」：
  接住异常的实现会**碰到**炸弹。
* `tests/test_plan_preview.py::test_a_request_outside_the_envelope_is_refused_not_held` ——
  越界（`geo=FR` 落在 `GB` 的批准范围外）**不**落成 HOLD，仍是 `ConfigError`。治理问题
  与业务歧义是两件事，用一次「问人」把它们混成一件会让越界看起来像等人回答。
* `tests/test_research_context.py::test_an_ambiguous_record_carries_no_confidence_of_its_own` ——
  `"confidence" not in Ambiguity.model_fields`：「这条歧义本身有多确定」在这个形状里
  写不出来。
* `tests/test_plan_preview.py::test_the_hard_half_of_the_table_is_the_eight_dimensions` +
  `::test_the_hand_written_cases_cover_every_hard_dimension` —— 硬表就是那八维，且手工
  用例**覆盖到每一个硬维度**（少一维就有一条硬维度没被任何用例走到）。

**标注**：fixture（profile 声明块落 `tmp_path`）+ 纯函数。

**状态**：`DEMONSTRATED`。

**缺口**：无。

**这一行的外部要求行**：`docs/ACQUISITION_AND_PROVIDER_DESIGN.md:98` 逐字
「**模型置信度不能扩大授权。**」—— 本行的三条结构判据（
`test_a_high_confidence_guess_does_not_settle_the_scenario`、
`test_two_credible_readings_are_an_ambiguity_and_confidence_is_not_an_input`、
`test_an_ambiguous_record_carries_no_confidence_of_its_own`）就是这一句话的执行器：
挡住猜测的是 **provenance**，而 `confidence` 连一格都没有。

---

### 关闭条件 5 · Source yield 变化只能改批准范围内排序；低样本不会自动拆 Scene 或改变客户资格

**机制（前半 · 「运行中按四个量调排序」这个动作在盘上不存在）。**

而这不是缺口，是一句更硬的话：**排序没有运行期自由度**。编译只做**准入收窄** —— 计划上
`source_methods` 那一格的值只能是 `domain/sources.py` 注册表顺序的**子序列**，构造这一格的
地方全仓只有一处（`_source_methods_cell`），它的输入只有声明与这一次选中的块。所以「按产出
把某条路提前」没有落点：它一写出来就打乱注册表顺序，而切片 E 的两条判据数的正是这个顺序与
这个签名。于是「批准范围内排序」这句话今天的答案是 —— **排序不在批准范围内，因为它没有
运行期自由度**。

* **四个量各有读数，但没有一个通到排序**：
  * `access` —— `domain/access_governance.py::AccessGovernor`（`record()`:457 生产、`admit()`:365
    消费、`snapshot()`:571 产出 `OriginReport`:281）。**`snapshot()` 在 `marketops/` 下零
    消费者** —— 诊断面收得住，但没有一格读它去改顺序。
  * `duplicate` —— `application/reuse.py::ReuseLedger`（`decision()`:212、`reuse()`:277），
    消费者在 `application/refresh.py`（:1101、:1149）。它答的是「这一轮还取不取」，
    不是「顺序改不改」。
  * `yield` —— **`marketops/` 下没有这个量**。最接近的是 `candidate.source_method_id`
    （`domain/candidate.py:173` 那一格；`:167` 的注释写明它必填）：每个来源有身份，
    但**没有任何地方按来源聚合产出**。
  * `cost` —— `command grep -rnE "\bcost\b" marketops/ --include=*.py` **零命中**（中文「成本」
    只出现在 adapter 注释里讲外部计量）。
* **排序是编译期定死的**：`EffectiveResearchPlan.source_methods`（`domain/research_plan.py:587`）
  由 `_source_methods_cell`（`application/research_strategy.py:336`）算定，顺序逐字取自
  注册表的**声明顺序**（`:355-357` 的注释：同一份声明两次编译要逐字相同）；计划是
  `frozen=True` 的值对象（`domain/base.py:26`），唯一构造点 `build_plan`
  （`research_plan.py:705`），唯一调用者 `StrategyCompiler.compile`（`:932`），唯一消费者
  `plan_preview`（**只读**）。**全仓不存在任何在运行中改一条已编译计划排序的代码路径。**
* **「批准范围」是 `ResearchEnvelope`**（`domain/research_context.py:358`，四格 `geo` /
  `jurisdictions` / `locales` / `exclusions`）：越界有判据（`constraints_out_of_envelope`:446
  → `compile` 在 `:910-922` 抛 `ConfigError`）。**四格里没有一格是排序** —— 所以「排序只在
  批准范围内」这句话今天没有承接面，因为排序没有运行期自由度。

**机制（后半 · 两句各自怎么成立，成立方式不同）。**

* **「低样本不会自动拆 Scene」——结构上成立**：拆/并 Scene 的三个入口全部是 controller
  角色**人工**调用的 MCP 工具（`create_scene` / `add_scene_version` / `control_scene`，
  `adapters/mcp_server/product_loop.py:4929` / `:4962` / `:4993`），唯一的自动化闸门
  `_activation_gate`（`:991`）**只拒绝、不创建**。`SceneSplitCandidate` 在 `marketops/`
  下**零命中**（只在两份文档的散文里）。**这一句是读数，不是判据**（零命中只说明「今天还
  没有」，见下条）。
* **三处执行器（切片 E，`tests/test_research_strategy.py` 第 10 节）**：入口闭集是**派生**出来的
  —— `_scene_record_writers` 按函数体内有没有 `put_record` / `put_version` 派生，新写的写函数
  自动进射程 —— 派生出的六个写函数的调用者只允许出现在 `adapters/mcp_server/product_loop.py`
  （`test_only_the_mcp_tools_can_write_a_scene_record`）；**生效**那一步（`activate`）逐处必须带
  `by=_require_principal()`（`test_the_step_that_makes_a_version_effective_records_who_did_it`，
  它刻意不把 `_add_scene_version` 算进来：写一版不生效不改变正在跑的规则）；而 `_activation_gate`
  只拒绝、不创建 —— 它体内既没有写调用、也不调用任何写函数
  （`test_the_activation_gate_refuses_and_never_creates`）。
* **「不会改变客户资格」——类型闭集上成立**：资格判定链上「样本量」只以三态进入，
  `application/qualify.py::_any`（`:127`）逐字
  `return None if count is None else count > 0`（docstring 说明这一行为什么**不能**折成
  布尔：「把 ``None`` 折成 ``False`` 会把「还没查」读成「查了，是 0」，而后者是一个结论」）；
  事实键集是闭集，`application/grading.py::_require_the_qualification_key_set`（`:340`）
  把它变成硬失败。**1 页与 100 页给出同一个判定** —— 要让样本量影响资格，必须先往那个
  闭集里加一个事实名。

  > **订正（2026-09-29，切片 L 交回后；上句原先只有「而那一步会红」一句，那是一个预测）。**
  > 「那一步」实测下来是**两步，两步的答案不同**：
  >
  > * **只加事实名、不同步加投影** ⇒ `_require_the_qualification_key_set` 那条硬失败红。
  > * **词表与两条投影一起长**（「真的让样本量影响资格」要走的完整一步）⇒ 上面那三条
  >   正证据**全部保持绿**：它们量的是「两条路交给求值循环的键集相同、且等于那个闭集」，
  >   而两边一起长时那个等式**仍然成立**。接住这一步的是切片 L 新写的
  >   `tests/test_qualification_ignores_sample_size.py::test_a_count_field_magnitude_never_reaches_the_fact_dictionary`
  >   —— 它按 `AccountCandidate` 字段**注解的形状**（不是名单）派生计数集，判「量级进不了
  >   事实字典」。
  >
  > 所以「而那一步会红」**在一个读法下曾经是假的**：那条约束真正的强度不在既有那三条
  > 正证据上，而在一条当时还不存在的判据上。这是本矩阵里唯一一处「预测被实测改掉」的
  > 地方，记在这里是因为它是「判一条约束的强弱，唯一有效的量具是『违反它的世界，哪条
  > 判据会红』」那句话的实例 —— **违反它的世界已经走过一遍，而当时没有判据会红。**

**正证据**：
* `tests/test_research_plan.py::test_the_plan_is_only_constructed_by_its_own_builder` ——
  AST 扫全仓，别处构造计划即红（排序没有第二个写者）。
* `tests/test_grading.py::test_account_facts_keys_equal_the_qualification_vocabulary` ——
  `set(account_facts(...)) == set(ACCOUNT_QUALIFICATION_FACT_NAMES)`。
* `tests/test_discovery_qualify.py::test_both_paths_call_the_one_shared_loop` —— 两条路
  交给求值循环的键集**相同**且等于那个闭集。

切片 E 新加的五条（`tests/test_research_strategy.py` 第 10 节，每条的 docstring 里写着它
数的是什么、以及为什么不是另一件事）：

* `test_the_source_order_is_the_registry_order_narrowed_never_reordered` —— 声明全宽 +
  场景块收窄，读回来的值必须是注册表顺序的**子序列**；反控里有一条把反过来排的序列喂给
  谓词，证明这个谓词本身会判事。它与第 2 节的收窄判据、第 8 节的块判据有重叠，增量是
  **谓词化**：将来有人把排序写进这一格，第一条会红，而那两条照样绿。
* `test_the_source_cell_has_one_writer_and_it_takes_only_the_declaration_and_the_block` ——
  全仓只有一处构造这一格，且它的签名逐字只有 `reading` 与 `scenario`。
* `test_only_the_mcp_tools_can_write_a_scene_record` / `test_the_step_that_makes_a_version_effective_records_who_did_it`
  / `test_the_activation_gate_refuses_and_never_creates` —— 后半的人工入口那三条。

**负证据**：
* `tests/test_research_plan.py::test_an_out_of_envelope_request_is_refused_and_not_narrowed`
  + `tests/test_plan_preview.py::test_a_request_outside_the_envelope_is_refused_not_held` ——
  越界是**拒绝**而不是**收窄**。「收窄」正是「悄悄改掉批准范围」的形状，这两条把它挡住。

**标注**：纯函数 + 结构判据（AST）。

**状态**：`PARTIAL`。

**缺口（一条，归 B17）**：两半各要一次 Run 才谈得上 —— 前半要「运行中真的有来源产出可比」，
后半要「长期观察证明 source strategy / 责任模型 / 治理边界持续不同」，而后半要读的那份观察
今天没有生产者（`SceneSplitCandidate` 零命中不是「忘了写类」，是它要读的那份观察还没有生产
者）。

B16 自己关得掉的两处已在切片 E 关掉，从缺口里移出去了：

* 原第 2 条 **`ResearchEnvelope` 没有「排序」这一维** —— 处置不是「加一维」，而是把这句话
  问对：**排序没有运行期自由度**，编译只做准入收窄，所以没有「哪些重排算在范围内」要回答。
  要让排序进批准范围，得先给它自由度，那一刻判据红。
* 原第 3 条 **「不自动拆 Scene」只有读数** —— §6 的纪律是「只有类名/字段/接口的无消费者实现
  不算完成」，它的反面同样成立：**一个只靠「那条路还不存在」成立的约束，不算被守住**。现在
  有三条执行器（见上面的机制段）。判据刻意**不做**「`SceneSplitCandidate` 零命中」那种断言：
  那是「今天还没有」，把它写成判据等于把「还没写」当成「守住了」。

**这一行的外部要求行**：`docs/ACQUISITION_AND_PROVIDER_DESIGN.md:97-99` 逐字
「`SceneFingerprint` 是派生标签，不是 Scene ID：同一 Workstream 可产生多个细粒度
fingerprint/Segment。系统只有在**长期观察证明** source strategy、责任模型和治理边界持续
不同后，才提出 `SceneSplitCandidate`；它本身不能创建新 Workstream。」

这一句把条件 5 后半的要求说全了，也说明了今天**有执行器的是哪一半**：**入口那一半已经关掉了**
—— 拆 / 并 Scene 的三个入口只能是 controller 角色人工调用的 MCP 工具，闭集是派生的，生效那
一步记主体，闸门只拒绝、不创建（切片 E 的三条判据各钉一条）。仍缺的是**它要读的那份观察**：
「长期观察」这个输入（跨 Run 的来源策略、责任模型、治理边界的对比）今天在盘上不存在 ——
`SceneSplitCandidate` 零命中不是「忘了写类」，是**它要读的那份观察还没有生产者**。所以今天的
答案是「**拆的入口只有人为的**」，而不是「有一条路在读观察、读不够就不提」：后者才需要那份
观察，而它是 §B16 动态识别规则第 3 条要的形状。

### 关闭条件 6 · `EffectiveResearchPlan` 随 WorkItem 冻结；中途 profile 更新不改正在跑的 attempt，新 run 才用新版并给 impact

**机制。** 这一条有两个主语，盘上一个有、一个没有。分界不是「做了一半」，是**哪一半有主语**。

* **「profile 版本随 WorkItem 冻结」——有，而且是硬门。** `WorkItem`（`domain/job.py:169`）落表
  `work_item`（`adapters/sqlite/db.py:378`）；冻结副本 `WorkItemSnapshot`（`domain/job.py:220`），
  四格 `work_item_id` / `scene_version` / `pinned_at` / `subject`（`:258-262`），落表
  `work_item_snapshot`（`db.py:435`）。**「不许中途改」是一个拒绝函数**：`repin_refusal`
  （`ports/jobs.py:98`），两个存储各自调用（`adapters/sqlite/jobs.py:374`、
  `adapters/memory.py:745`）。
* **「跑着的 attempt 用哪一版」——只有 profile 那一半。** `JobAttempt`（`domain/job.py:266`）
  挂 `work_item_id`，所以 attempt 经由 WorkItem 继承那份 pin；但它**不另存计划**。
* **「`EffectiveResearchPlan` 随 WorkItem 冻结」——不存在。** 计划是 `frozen=True` 的值对象
  （`domain/research_plan.py:542`），唯一构造点 `build_plan`（`:705`），唯一调用者
  `StrategyCompiler.compile`（`:932`）。**没有 store、没有表、没有摘要列**：`db.py` 全文
  `command grep -i plan` **零命中**（`SCHEMA_VERSION = 6`，`db.py:157`）；`plan_digest` /
  `plan_version` / `plan_id` / `plan_json` / `effective_plan` / `plan_ref` / `plan_hash`
  在 `marketops/` 与 `tests/` 下**全仓零命中**。

**生产者**：profile 版本的 pin —— `domain/job.py:258`。计划 —— **无**：`preview_plan`
（`application/plan_preview.py:239`）是唯一可达入口，而它在 `marketops/` 下除自身零调用者。
**消费者**：`WorkItemSnapshot` —— `get_assignment` 回执（`adapters/mcp_server/tools.py:3026-3044`）
报 `guidance.pinned` / `pinned_at` / `effective` / `pinned_is_effective` / `content_from` /
`files`。计划 —— **无**。
**存储/事务**：`work_item_snapshot` 表在（`db.py:435`）；计划没有表。

**正证据（九条，全部断的是 `scene_version` 与 `subject`，无一条提到计划）**：

* `tests/test_job_contract.py::test_the_compiled_batch_records_the_version_it_was_compiled_under`
  （`:366`）
* `::test_every_work_item_in_the_batch_carries_its_own_pin`（`:387`）
* `::test_recompiling_under_the_same_version_is_still_idempotent`（`:410`）
* `::test_recompiling_under_another_version_is_refused_and_pins_nothing_new`（`:424`）—— 这一条就是
  `repin_refusal` 的执行器
* `::test_a_batch_with_no_pin_is_reported_as_unpinned_not_guessed`（`:457`）
* `tests/test_mcp_assignment_tools.py::test_the_assignment_reports_the_pinned_version_and_the_effective_one`
  （`:352`）
* `::test_a_work_item_pinned_to_another_version_says_the_two_disagree`（`:381`）
* `tests/test_mcp_job_tools.py::test_a_row_edited_after_the_work_item_exists_is_refused_not_repinned`
  （`:597`）
* `tests/test_application_maintenance.py::test_the_effective_version_decides_the_cadence`（`:242`）——
  「新写一版但没批准，不改在跑这件事的节奏」

**负证据**：全仓**没有一条**判据的名字或正文提到 `EffectiveResearchPlan` / `preview_plan` /
计划摘要；上面九条一条都不例外。

**标注**：sqlite fixture + 内存实现 + MCP 回执（本地库与纯函数，零网络调用）。
**状态**：`PARTIAL`。

**缺口（两条，归属不同）**：
1. **计划那一半三格全空**（生产者 / 存储 / 消费者）。它与条件 1 的缺口栏、条件 5 缺口第 1 条
   是**同一个根因**：`EffectiveResearchPlan` 今天只在 `tests` 里存在。**归 B17。**
2. **「新 run 才用新版并给 impact」今天的主语是 Scene 版本，不是 Run。** impact 面确实在
   （`domain/scene.py:625 scene_impact`、`application/scenes.py:250 impact_between`、MCP 工具
   `get_scene_impact`（`security/catalog.py:700`）），但它算的是「两版 Scene 之间差什么」，
   **不是「新 run 与在跑的 run 差什么」** —— 后者要**一次 Run** 才有一对可比对象。**归 B17。**

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:243` 逐字

> 最小 RunSpec：`workspace/workstream, objective, selector/cohort, ResearchContext ref, EffectiveResearchPlan ref, execution_mode, budget envelope, cutoff, policy versions, idempotency key`。Run 内部 Job/WorkItem/Quay attempt 继续复用 PR #8 已有实现，不另造执行器。

以及 `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md:80`「Run 语义 … **5 个全零**」。`RunSpec` 在
`marketops/` 与 `tests/` 下 `command grep -rnE "\bRunSpec\b"` **零命中** —— 它今天是一条要求，
还不是一个类型。这正是条件 6 的缺口为什么归 B17：**要求里那个 `EffectiveResearchPlan ref`
是 RunSpec 的一格，RunSpec 不存在，那一格就没有宿主。**

---

### 关闭条件 7 · Serper/HTTP/Crawl4AI/Jev/Quay 调用能反查是谁因哪一个 plan 步骤被选择；没需要的 provider 不为「集成完整」而强制调用

**机制。** 这句话要三个格子都填上才算成立：**谁**、**因哪一个 plan 步骤**、**哪一次调用**。
今天第一个填上了（强度按 provider 分三档），第二个**零格**。

* **「谁」——分三档。**
  * Serper → **账号**：额度主体是 `RunBudget.for_scope`（`application/budget.py:86`）与
    `for_account`（`:163`）；幂等键 `search:<country>/<lang>/<text>`
    （`application/billed_search.py:46`）。
  * Jev（判定）→ **work item**：`DecisionRecord`（`ports/decisions.py:328`）带
    `case_id` / `spec_id`，存储层另加 `work_item_id` / `subject_ref`
    （`adapters/sqlite/decisions.py:120`）；调用点是 MCP `decide` 工具
    （`adapters/mcp_server/decisions.py:383`）。
  * HTTP / Crawl4AI → **只到 scope**。
* **「因哪一个 plan 步骤」——零格。** `FetchAttempt`（`domain/fetch_attempt.py:319`）的全部字段
  （到 `:384`）里**没有** plan step / source_method / decision 任何一格；而它自己的三格业务归属
  今天是空的、**结构上填不进来**：`adapters/fetch_attempts.py:223-226` 逐字说
  `run_id` / `work_item_id` / `target_ref` 今天**没有生产者**（「业务归属随 B17 的 Run 与
  WorkItem 到来」），`attempt_from`（`:254`）只从**请求**抄 `scope`，而请求侧也表达不出来 ——
  `FetchRequest`（`ports/acquisition.py:49` 起）只有 `url` / `scope` / `purpose` /
  `reservation_id`。
* **计划不驱动调用（这是这一条反面的承载点）。** `application/pipeline.py:235` 逐字
  `queries = build_queries(account, self._deps.bundle)` —— `build_queries`
  （`application/queries.py:152`）硬写三条再按需加 `site:`，**只吃 `bundle`、不吃
  `EffectiveResearchPlan`**。唯一读 `plan.source_methods`（`domain/research_plan.py:587`）的代码
  是 `plan_preview.py:286 _methods_of(plan)`，它只把 id 翻成「族 + 能回答什么」，**不驱动任何
  调用**，且它自己零调用者。
* **「不为集成完整而强制调用」今天为什么成立** —— 不是因为有一条判据，是因为**没有东西在
  按需分派**：Crawl4AI 只在 `decide_escalation` 为真时才调
  （`application/acquisition.py:121-137`，`_unavailable_reason`（`:158-169`）区分「没接线」与
  「不可用」）；Jev / Laya 只在 MCP `decide` 被调时才调；多臂影子那条
  `application/shadow.py:373 provider.decide(...)` 零生产调用者。

**生产者**：`FetchAttempt`（`domain/fetch_attempt.py:319`）；`SourceReceipt`
（`domain/receipt.py:846 source_method_id` 那一格）—— 唯一能指回「来源方法」的就是它，而它是
闭集注册表成员（`domain/sources.py:125`），调用点写的是**常量**
（`application/discovery_run.py:898 source_method_id=OPEN_WEB_SOURCE_METHOD.id`）：
**它是「哪一族来源」，不是「哪一步决策」，也 join 不到任何 plan 实例。**
**消费者**：`list_fetch_attempts`（`security/catalog.py:519`；实现
`adapters/mcp_server/fetch_attempt.py:133`），筛选只有 `scope` / `url` / `since` / `limit`
（`ports/fetch_attempt.py:95-101`）。
**存储/事务**：`fetch_attempt` 一族各表；搜索侧的账是 `budget_reservation`
（`adapters/sqlite/db.py:187`）。

**正证据**（今天能拿出来的最好一条，恰好也是缺口的形状）：
* `tests/test_mcp_fetch_attempts.py::test_an_empty_scope_is_a_filter_and_not_a_wildcard`
  （`:340`）与 `::test_the_url_filter_asks_about_the_address_we_requested`（`:372`）——
  **筛选面只有 `scope` 与 `url`**。整个查询面就是「谁」与「打了哪个地址」，
  **没有第三格可以问「为什么」**。

**负证据**：
* MCP 面 **55 个工具**（`security/catalog.py:231` 起）里**没有 plan / preview 工具**：唯一名字里
  带 plan 的是 `get_maintenance_plan`（`:482`，维修调度那一族，与 `EffectiveResearchPlan`
  无关），`preview_plan` 零 MCP 入口。
* 最接近的一条路 —— discovery-run 把「哪条检索式」带回来了：`OpenWebPlan`
  （`application/openweb.py:162`）在 `application/discovery_run.py:690` 构造，`:1436
  _query_readings` 逐条 zip 计划里的查询 × 查询回执，`:322 HitRecord` 记下命中来自哪条检索式。
  **但它三处都不是这一行要的东西**：主语是 `OpenWebPlan`（开放网络模板展开）而不是
  `EffectiveResearchPlan`；它是**内存里的报告，不是盘上的产物**（`discovery_run.py:459` 逐字）；
  而且**没有到抓取那一跳**（`FetchRequest` 不带 query 引用）。

**标注**：字段清单 + MCP 目录 + 端口签名（都是结构读数，零网络调用）。
**状态**：`PARTIAL`。

**缺口（两条，都归 B17）**：
1. **「因哪一个 plan 步骤」全仓未覆盖**：零字段、零连线、零工具。它要 join 的两端 —— Run 与
   plan 实例 —— 今天都不存在；命中的三格里最强的一档（Jev）也是**借了 work item 的幂等键**
   才够到「谁」。**归 B17。**
2. **「不强制调用」没有执行器。** 它今天靠「没有东西在按需分派」成立，而 §6 的反面同样成立 ——
   **一个只靠「那条路还不存在」成立的约束，不算被守住**（与条件 5 缺口第 3 条同一条纪律）。
   要成为一句可判的话，先得有一条「这次 Run 开了哪几条路」的读数，而那就是 RunSpec 的
   `EffectiveResearchPlan ref`。**归 B17。**

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:229-238` 第 7 条原文，
本行标题即其逐字。

**条件 5 / 6 / 7 的共同形状**（写在这里，因为三行的缺口指回同一处）：三行各有一半**要求
运行期**才谈得上 —— 条件 5 的「运行中调排序」与「长期观察之后才提拆 Scene 候选」、
条件 6 的「新 run 与在跑的 run 给 impact」、条件 7 的「因哪一个 plan 步骤」。三者在盘上是
**同一个空的宿主**：`RunSpec`（`MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:243`）今天在 `marketops/`
与 `tests/` 下零命中。B16 能自己关的**已经在切片 C2 / D / E 上关完**（条件 2，与条件 5 里
不依赖运行期的那两格：排序面的形状、拆 Scene 的入口闭集）；这里剩下的没有一条是 B16 关得
掉的。

---

## 这份矩阵不做的事

* **不宣称任何 live 结果。** 上面每一条都是 fixture 或纯函数判据，零网络调用、零
  provider 调用。「测试通过」不等于「现场 provider 已验证」（§18）。
* **不把 fixture 说成现场。** 每一行的「标注」栏逐条写明。
* **不把 7 条之外的东西算进来。** 别的验收面（B15 / B17 / B23 / B24 / B25）各有自己的
  矩阵，这一份只管 §B16。
* **不改两份上游文件。** 计划 §B16 记的是「要求什么」，
  `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md` 记的是「`convergence_start_sha` 上量到什么」；
  改前者等于把要求改成今天的实现，改后者等于把当时的读数改写成事后追认。
* **不给完成百分比。** 7 行里各状态的**行数**不是进度 —— 每一行的判词各自带着自己的
  证据，而一行的状态会因为一条判据落地而变，不会因为别的行变了而变。

