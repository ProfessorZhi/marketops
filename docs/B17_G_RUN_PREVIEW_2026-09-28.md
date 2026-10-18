# B17-g · Run preview：把「编之前先看一眼」接到**一次跑**上

这一份是切片 g 的落地记录，而它从**动手之前**写起：先侦察、再定拆法、最后才改代码。
`docs/B17_DESIGN_SLICES_2026-09-28.md` 逐字声明过「冻结之后这一份不再长」（`:1150`、
`:1220`），所以 e3 之后的每一片各开一个新文件 —— e3 / e4 / e5 / f 都是这么落的，g 照旧。

## 一、这一片判的是哪句话

权威是执行计划 §B17 正文那句，逐字：

> Run preview 必须先报告：已有事实复用多少、预计补哪些缺口、计划可能调用哪些 provider、
> 预算/stop、哪些歧义会进入 Review。Preview 本身零 provider 调用。

（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:253`。同一件事在 §445 的启动流程里再出现
一次，逐字：「→ preview ResearchContext / EffectiveResearchPlan / 已有池子复用 /
预算 / stop」，而在 `:102` 那条启动用例里是「启动用例先 preview」。）

**两处差量，先登记，不顺手统一：**

1. **这一句不在 B17 的七条关闭条件里。** 七条关闭条件是 `:231-237` 与 `:262-268` 那两组
   编号句，而这一句住在**正文**里。设计表的切片行把它记成「关 1 的后半」，而条件 1 的
   后半句逐字是「重复提交同一 idempotency key 不重复池子或成本」—— 那半句今天由 a/c
   两片（幂等主键 + 双向对账）和 d 片（增量）承担，本片**不认领**它。所以本片落的是一条
   **正文要求**，不是一个关闭条件的编号。
2. **验收矩阵里此前没有这一条的任何登记**：`command grep -n preview
   docs/B17_ACCEPTANCE_MATRIX_2026-09-28.md` 零命中 —— 那份矩阵的七行判词只覆盖七条关闭
   条件。本片的补记第一次把它写进矩阵。

## 二、侦察：§253 那五项，今天各住在哪

| §253 那一项 | 今天的载体 | 受理**之前**读得到吗 |
|---|---|---|
| 已有事实复用多少 | `application/run_increment.py` 的 `RunIncrement.counts[REUSE]` / `cells` | ✗ 要一个**已经受理**的 `Run`（`plan_increment(run=...)`），而 `get_run_increment` 还要求那一行没结束 |
| 预计补哪些缺口 | 同上的 `works`（`NEW` / `MISSING` / `EXPIRED` 三档） | ✗ 同上 |
| 计划可能调用哪些 provider | `application/plan_preview.py` 的 `sources`（计划开出来的来源方法）+ `NOT_REPORTED` 里那条「方法 → provider 没有映射」 | △ 在**计划层**，而且只答一半 |
| 预算 / stop | `RunSpec.budget` / `window` / `cutoff` + `ports/runs.py::admission_refusal` / `window_refusal` | ✗ 只在 `admit_run` 内部被判，判完就写；今天能读到它只有两条路：受理被拒时那句 `ToolError`，或者受理之后重读那一行 |
| 哪些歧义会进入 Review | `plan_preview` 的 `held` / `recorded` | △ 计划层有；受理面把它变成 `ToolError`（HOLD）或回执的 `recorded` 一格 |

一句话：五项里**两项**（复用 / 缺口）库层已经有完整判据，但**只在受理之后**读得到；
**一项**（provider）只答一半；**一项**（预算/stop）今天只有拒绝措辞；**一项**（歧义）
已经报得出来，只是以拒绝的形式。

还有一个更容易漏掉的读数：**受理面判「预算/stop」的次序本身是一条信息，而它今天没有
任何一处讲给调用方听**。`RunStore.admit` 的 docstring 逐字钉着三步 ——

1. `admission_refusal`（写事务**之外**、纯函数，只读 `spec` 与 `at`）；
2. **重放检查**（`get(rid)` 命中就返回那一行，**不再问窗口**）；
3. `window_refusal`（写事务**之内**，读这条链的累计）。

第 2 步与第 3 步的次序意味着：**一次重发不会被链的阈值拦下**（那一条路上根本没人问它），
所以「这条链已经到 stop 了」与「这次提交会被拒」不是同一句话。今天没有一处把这件事说出来。

## 三、四个设计决定

### 决定 1：复用/缺口**不另写一份判据**，把 `run_increment` 那一段抽出来共用

`plan_increment` 的判据链是 `aspect_states`（钟）→ `census_for`（盘，只在 `CURRENT` 上问），
而它自己逐字写着「它是**读法**，不是第二处判定」（那是那条链的第三次应用）。preview 要的
是**同一份格子结论**，缺的只有三样：`run_id`、`selector`，以及「这一轮结束了吗」那个前提。

→ 抽出 `increment_cells(...)`（只收 `workspace` / `plan` / `observations` / `evidence_store`
/ `at` / `signals` / `subjects`），`plan_increment` 与 preview 各自调它。抽取之后
`plan_increment` 的行为**逐格不变** —— d 片那 33 条判据是它的守门人，抽取那一次提交里
它们一条都不许改。

**不做的事**：不新造一套四格判定，不把 `Run` 拆开传进去，也不给 preview 一个「假装受理过」
的 `Run`。最后一条是硬的：`domain/run.py::run_id` 逐字写着「服务端一旦能自己造一个键，
它就能造出第二个 Run」，而 preview 手上**没有**键。

### 决定 2：预算/stop **不重判**，按受理面同样的次序问同样那几个函数

preview 报的就是 `admit` 那几步的读数，而 `StopVerdict` 是一个**五成员的闭集**：

| 成员 | 什么条件下 | 报告说的是 |
|---|---|---|
| `NOT_DUE` | `due_refusal(spec, at=at, ...)` 非空 | 「这一批主体上没有到期的」——**只有 `watch` 走得到这一步**，而它排在**最前** |
| `REQUEST_REFUSED` | 上面为空，而 `admission_refusal(spec, at=at)` 非空 | 「这一份请求今天走不通」——链**没有被问** |
| `REPLAY` | 上面为空，而 `get(run_id)` 命中一行 | 「重发：拿回同一行；链的阈值这一步不被问」 |
| `CHAIN_REFUSED` | 上面三样都空，而 `window_refusal(...)` 非空 | 「现在提交会被拒」 |
| `OPEN` | 四条都空 | 「按当下的盘，这一份请求受理得下来」 |

**第四档与第五档是动手时多出来的两格，登记在这里。** 计划里写的是「按受理面同样的次序
问同样三个函数」，实测的受理面次序是**四步**：到期核验（`due_refusal`，watch 独有）在
`store.admit` **之前**、也在 `admission_refusal` **之前**。少报第一档的后果具体：一份
`watch` 请求在报告上会把「这一批主体今天没有到期的」读成「这一份请求走不通」—— 前者是
**到点了再来**，后者是**去改请求**，处置相反。次序也不是优先级声明，是位置：
`tests/test_run_preview.py::test_not_due_speaks_before_the_request_itself_is_refused` 用一份
**两样都不成立**的规格（`watch` 判不了到期，而截止也过了）判读到的是 `NOT_DUE`。

`spent` 读不到时 `window_refusal` 自己会说「读不到账本读数 ⇒ 拒」，preview **照抄那一句**，
不改写成「预算还够」—— 那正是那个函数第四条的存在理由。

一份 `StopReadout` 的不变量由 `__post_init__` 挡住（只有直接构造才违反得成，
`tests/test_plan_preview.py` 对 `PlanPreview` 用的是同一条做法），分两组：**次序组**是
「前面一步说了话 ⇒ 后面的读数缺席」（`due_refusal` 有值 ⇒ 后面四格全 `None`；
`request_refusal` 非空 ⇒ 后面三格全 `None`；`existing` 命中 ⇒ 链的读数与累计花费全
`None`）；**主语组**是「一句话有没有主语」（`window is None` ⇒ 链的三格全 `None` —— 没有
窗口的规格不属于任何一条链，「累计」这句话对它没有主语；`chain_refusal` 非空 ⇒ 链的读数
在；`spent` 在 ⇒ 链的读数在；链的窗口摘要必须与规格的相等）。最后还有一条**「缺席要说得
出来」**：`window` 在场、链的读数缺席、而前四步**一步都没说话** ⇒ 抛 —— 那一份读数会在
报告上长成 `OPEN`，而 `OPEN` 是一句「受理得下来」。逐条写在 `StopReadout` 的类文档里，
第 6 节把它们**逐条**构造一遍（一个没人能违反的守卫与一个从不响的守卫，在报告上长得一样）。

**不做的事**：不给一个「这次会不会是重发」的布尔。受理回执**刻意不下那个判断**（
`runs.py::_receipt` 逐字：「这一层刻意不替调用方下这个判断」），理由是多出来的那个布尔会在
下一次有人改受理语义时变成一处没人维护的口径。preview 给的是 `run_id`（内容寻址的一个
读数）与那一行**此刻在不在盘上**（`RunStore.get` 的 `None` 语义），判断留给读的人。

### 决定 3：一份规格，两个面，**共用同一段前奏**

preview 与 submit 必须描述**同一轮**。两处各拼一次 `RunSpec` 的那天，漂移的形态是
「preview 说这一轮是 X，提交下去的是 Y」——而两边都跑得过、都自洽、都通得过各自的判据。

→ 把 `submit_run` 的前奏（`_plan_context` → 生效版本判 → `_declaration` → `preview_plan`
→ `_window` / `_mode` → `_subject_source` / `_subjects`）抽成一处共用助手，返回一份冻结的
小结构；受理面与 preview 面各自调它。这一条有判据：**同一份入参下，两个面拼出来的
`spec_fingerprint` 逐字相等**，而且那个 `at` 是**同一个值**（一次调用里只有一个时刻，
preview 报的 `at` 就是它据以判 `cutoff` 的那个时刻）。

### 决定 4：零 provider 调用仍然是**构造**，但这一层的构造与 `plan_preview` 不同款

`plan_preview` 拿不到端口 —— 它的签名只收声明与上下文。preview **必须**读三样本地存储
（维护账、证据盘点、Run 记录）与（有窗口时）预算账本：少了它们，「复用了多少」答不出来。
所以这一层的构造是：**签名只收存储与已经读出来的值**，不收任何花钱的端口，并且**不读时钟**
（`at` 由外面给，与 `admission_refusal` 同款）。

判据照 `tests/test_plan_preview.py` 那一族的 AST 量具，但**白名单要明写三样**：
`ports.runs` / `ports.store` / `ports.budget`（它们是本地存储），而**明禁** `ports.search` /
`ports.acquisition` 那一族（它们花钱）。更强的半条：preview 的**类型注解**里不得出现任何
检索/抓取端口 —— 它拿不到，所以「不花一次检索」不是自觉。

## 四、拆法：**一片，一个提交**

不拆。理由是一条结构性的：preview 的五个块互相依赖 —— 复用与缺口要**计划编出来了**才答得
出（`HOLD` 时它们逐条进 `not_reported`），而预算那一块要 `RunSpec`（它要那两份），
`RunSpec` 又要计划与上下文。切一半的产物是一个**没有完整答案的读数面**，而它在报告上与
一个完整的读数面长得一样 —— 正是本片要防的那件事。

另一条理由是同一条纪律的另一面：库层的 preview 面若单列一个「库层片」先落，它就是一个
**零生产调用点**的模块（§6 点名不算完成的形状），而受理面若单列一个「MCP 片」先落，
它就是一个没有判据可依的接线。两半必须同一次落地。

## 五、这一片**不**做什么

* **不改 `plan_preview.NOT_REPORTED`。** 它是**计划层**的报告，那五条在计划层今天仍然成立
  （那一层只有声明与上下文两样输入，读不到池子）。Run preview 报告里把它**原样带着**
  —— 见决定 1 与下面那一节，带的方式是逐字引用，不是重抄。
  里面那句 reason 有一处**借词**，动手时逐条核过，登记在这里而不改它：

  > 「（「合格数上限」「预算用尽」「候选枯竭」是三个不同的结局），不在
  > `EffectiveResearchPlan` 的十七格里。」（`application/plan_preview.py:432`）

  这三例取自 **discovery 那一侧**的停止理由词表 —— 在那里它们是**一次跑结束时的读数**
  （`stops`，见 `application/discovery_run.py:221`、`application/metrics.py:42`、
  `interfaces/cli.py:440`）。而这句话的主语是**授权链**上的阈值（条件 4 明写的是
  「对象数/累计预算/截止时间」，本片的 `_HOLD_REASONS` 里也是这三条）。逐条对下来：
  **「预算用尽」** 与「累计预算」是同一种东西；**「合格数上限」** 是 discovery 侧「**我们**
  按自己的上限停的」（`tests/test_discovery_run.py:462` 逐字：「上限是我们按自己的决定停的；
  候选枯竭是网络里没有再给出东西」），它与「对象数」不是同一个数（一个是**合格产出数**，
  一个是**这一条链收下的对象数**）；**「候选枯竭」** 在网络那一侧，与那三条阈值都不是一回事。
  而条件 4 的第三条阈值「**截止时间**」在三例里**一个都没有**。

  → 那三例**举不出**条件 4 的阈值表，所以本片里凡是要说阈值的地方一律写那三条（`run_preview.py`
  `_HOLD_REASONS` 那一处），而**这句 reason 一个字不改**：它在计划层仍然成立（计划层真的
  十七格里没有 stop 条件），改它会把一处登记在册的差量抹成「看起来本来就对」。
* **不给「重发」那个布尔**（决定 2）。
* **不新增 `PreviewOutcome` 成员。** 越界（`ConfigError`）照 `plan_preview` 的老路**抛**，
  由 MCP 面译成 `ToolError` —— 把治理问题渲染成「请人回答一次」的错，`plan_preview` 的
  模块 docstring 已经拒过一次（逐字：「两个都读成『这一次没能编出计划』的话，『等一个人
  回答』与『等一次治理变更』在报告上同形」）。
* **不动 `submit_run` 的回执形状。** `_receipt` 那句「这里**没有**一份完整的 preview 报告
  （复用多少、补哪些缺口）：那是切片 g 的产物」要**改写** —— 那一份不是回执的一格，它是
  一个**面**（`preview_run`）。回执答的仍然是「这一行被写下来了吗」。
* **不修 `TOOL_CATALOG` 那几处「十五条」。** 那是一个**已经登记过的**旧账（
  `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md:227` 实测：目录已经不是十五条），本片只按规矩
  往目录里加一条，不去动那些数字 —— 顺手改会让一个已经在别处登记过的缺口在这里显出
  「被修好了」的样子。

## 六、`not_reported` 怎么带：**减法**，不是重抄，也不是并集

这一节的取舍值得单独写，因为它在报告上最难看出来。

Run preview 的 `not_reported` = `plan_preview.NOT_REPORTED` **减去**本层答得出来的那**三条**
（「已有事实复用多少」「预计补哪些缺口」「停在哪（stop 条件）」）。两条留下来的**逐字**是
计划层那两条（引用同一份常量里的同一个对象，不重写一个字），于是「同一句话的第二份实现」
在结构上写不出来。

**第三条是动手时多出来的，登记在这里。** 计划里写的是「减去那两条」，实测能减的是三条：
本层**确实**报得出「停在哪」—— 那正是决定 2 那一块（五档 `verdict` + 信封 + 截止 + 链走到
哪了）。把它留在 `not_reported` 里，同一份报告会一边说「我报不出停在哪」、一边把它报在
`budget_and_stop` 那一格里，而读的人分不出该信哪一句。减法的过滤器因此是
`_ANSWERED_WHEN_READY`，与 `_HOLD_REASONS` 的**键集**逐字相同（那一条相等本身有判据）。

`READY` 时它是**两条**；`HOLD` 时它是**五条** —— 计划这一次没编出来，那三条就没人答得了，
它们**加回来**，`reason` 换成本次的那一句（「计划这一次没编出来（HOLD），所以没有一份计划
去对着池子读」）。这正是 `NotReported.reason` 的定义（「这一层答不上来的一项，**以及为什么**」）。

两条判据把这件事钉住：

1. `READY`：条目名逐字等于 `[条二]`，且每一条的 `reason` 与常量里那一条**相等**（不是相等
   的文本，是同一句话）。
2. `HOLD`：条目名逐字等于五条，而多出来的三条各自点名 `HOLD`。

再加**两条对照**：过滤器用的那三个名字必须与常量里那三条逐字相同，且 `_HOLD_REASONS` 的
键集必须与过滤器逐字相同 —— 否则计划层改一个措辞之后，这份报告会**静默地把一条答不上来的
东西滤掉**（它看起来只是短了一条），或者在那一条上加回来的那一刻 `KeyError`。

## 七、落地记录：动手之后，盘上与这一份设计**不一样**的地方

这一节是这一份自己的差量台账。写法与前几片一致：**只登记指得到地方的东西**，
凡不是本会话复跑出来的，逐条标来源，不写成「我量过」。

### 7.1 五处差量

**（一）`ConfigError` 的异常链在 `due_refusal` 这一步断掉 —— 没有判据钉它**

这一片把 `watch` 的到期判定改成「返回字符串而不抛」（`:337` 那条 docstring 写着理由：
预览面要一句话、受理面要一次抛）。代价具体：

* `marketops/application/run_admission.py:317-320` —— `except ConfigError as exc:` /
  `return str(exc)`：`ConfigError` **在摊成字符串这一步就被丢掉了**；
* 同文件 `:363` 的 `_require_due` 拿到的只是一个 `str`，于是 `raise RunSpecError(reason)`
  **写不出 `from exc`** —— 异常对象已经不在作用域里；
* 类型换对了、措辞一个字没丢（`tests/test_run_admission.py:620` 那条逐字钉 `RunSpecError`
  与 `"paused"` / `"不接受新的派发"`），**丢的是 `__cause__`**。

它**没有判据**：实测 `tests/` 与 `marketops/` 两棵树下，`__cause__` 只在
`marketops/domain/errors.py:64` 的散文里出现一次（那一处讲的正是「刻意不叫 `cause`」），
**没有一条测试读它**。这条性质今天只体现在「有人去看 traceback 时看得见什么」。

反面对照就在同一个切片里：工具层那几处是**带走因**的 ——
`marketops/adapters/mcp_server/runs.py:1079-1080` 的 `except ConfigError as exc:` /
`raise ToolError(str(exc)) from exc`（这一处在 HEAD 上就已经是这个形状，那时它在
`:691-692`），以及 `preview_run` 的两个 `except` 各自 `raise _translate(exc) from exc`
（`:494` / `:509`）。同一个切片里，一条路上链在、一条路上链断 —— 差别就是
**判定有没有先被摊成字符串**。

**（二）§三 决定 2：正文里有两个数，数的是两件事**

计划那一侧写的是「按受理面同样的次序问**同样三个函数**」。落地是：

* **次序：3 步 → 4 步。** `due_refusal` 插在**最前**，且**在 `store.admit` 之前**、
  在 `admission_refusal` 之前（正文 `:83-84` 逐字登记）；
* **成员：`StopVerdict` 是五成员的闭集**（正文 `:72` 逐字）：`NOT_DUE` /
  `REQUEST_REFUSED` / `REPLAY` / `CHAIN_REFUSED` / `OPEN`。

正文 `:82` 那一句（「第四档与第五档是动手时多出来的两格」）与紧随其后的 `:84`
（解释**少报第一档**的后果：watch 的「这一批主体上没到期」会被读成「这一份请求走不通」，
两者处置相反）**数的不是同一件事**。本记录**不改正文那一句**，只记下该以哪一句为准：
读次序看 `:72` 的成员表与 `:83` 的「四步」，`:82` 的序数不要当编号用。

判据是现成的：`tests/test_run_preview.py:742` 用一份**两样都不成立**的规格
（watch 判不了到期、而截止也过了）判读到 `NOT_DUE` —— 次序是**位置**，不是优先级声明。

**（三）§六：由两改三 —— 已就地登记，此处只留索引**

正文 `:188` 已经写了（计划写「减去那两条」、实测能减三条，第三条是「停在哪」，
它正是决定 2 那一块）。此处不重述，只把它的**判据**指出来：过滤器的三个名字必须与
`plan_preview.NOT_REPORTED` 里那三条逐字相同，且 `_ANSWERED_WHEN_READY` 与
`_HOLD_REASONS` 的**键集**逐字相同（`tests/test_run_preview.py:597` 与 `:641` 两侧各一条）。

**（四）`works_of` / `counts_of`：由属性内联改为共用函数**

`RunIncrement` 的 `works` / `counts` 两个属性在 HEAD 上各自内联一段判定；这一片把它们抽成
`marketops/application/run_increment.py:288` 的 `works_of(cells)` 与 `:301` 的
`counts_of(cells)`，两个属性改成转调（`:277` / `:285`），预览侧直接 import
（`marketops/application/run_preview.py:152` / `:154`）并在 `:480` / `:486` 用。
理由与「复用/缺口不另写一份判据」同一条（§三 决定 1），而这是它**跨文件**的那一半：
`cells` 的判定只有一处，两个面各读各的格子。

**（五）跨面判据的落点：由 `tests/test_run_preview.py` 改到 `tests/test_mcp_runs.py`**

§三 决定 3 那条「两个面拼出来的 `spec_fingerprint` 逐字相等」，**在库层看不出来** ——
`tests/test_run_preview.py` 钉的是同一个生产者造的那份读数对不对，而「这条请求在
`submit_run` 与 `preview_run` 上会不会错开」要**两处工具都真的调过**才看得见。
所以它落在 `tests/test_mcp_runs.py` 的第 12 节（`:3816` 起），三条：

1. `:3830` —— **先 preview 再 submit**，所以它同时判到「preview 一行都不写」：
   读到的 `verdict` 是 `open`、`existing` 是 `None`，提交之后 `_rows` 才从 0 变成 1；
2. `:3878` —— 受理之后同一个键 ⇒ `replay`。这是库层那条「重放排在链的阈值之前」
   在**服务面**上的另一半；
3. `:3930` —— 两个面在**同一本**账上数出同样的格子。

`tests/test_mcp_runs.py:48` 那一行（模块 docstring 的小节名）写着这条落点为什么在这里。

### 7.2 本会话新出现的五处

**（六）`StopReadout` 那段说明里不再写条数**

`marketops/application/run_preview.py:223` 今天逐字是「## 不变量：只有直接构造才违反得成
（这里不写条数 —— 它是**随下面这段文字而变**的）」。这是自指数字纪律的直接应用：
那个数随它下面那段文字而变，写进那段文字里就会在下一次改动时变成一句假话。
同一份文件 `:908` 仍写「`StopReadout` 的**三条次序不变量**」—— 那三条是**次序组**那三条
（`:230-231` 逐条列出），与整段不变量的总数不是同一个数。

**（七）计划层 reason 的借词**

正文 §五已经逐条写了（`marketops/application/plan_preview.py:432` 那句 reason 借了
discovery 侧的停止理由词表，而那三例举不出条件 4 的阈值表）。此处只留索引与**处置**：
那句 reason **一个字不改**。

**（八）`marketops/application/__init__.py` 的模块表补了 `run_preview` 一行**

这一条是**全量套件抓出来的**，不是读出来的：`tests/test_application_module_table.py`
的 `test_every_module_under_application_has_a_row` 红了（`missing: ['run_preview']`）。
修法是加一行 —— 落点今天实测在 `:58`，是这一片新增的 `+` 行。
顺带记一次动手事故：加行时我用了替换而不是插入，**误删了 `outreach` 那一行**，
复现那条判据的 walker 得到 `missing: ['outreach']` 之后补回。

**（九）目录里新加的 `preview_run` 条目，把产品环那条判据一起带上了**

本片按规矩往 `marketops/security/catalog.py:721` 加了一条 `preview_run`
（`phase="J"`、`_CONTROL`）。而 `tests/test_mcp_product_loop.py:1185` 那一句要求
**目录里产品环那一半每一条都有载荷**（`assert set(payloads) == product_loop_half`，
那一半由 `TOOL_CATALOG` 按 `phase == "J"` 与 `Role.CONTROLLER` 现算）。
⇒ 「加一条目录条目」与「加一格载荷」是**同一件事**：分开做，套件会红在
`tests/test_mcp_product_loop.py:1002` 那条判据上。载荷落在 `:1128`，共 25 行。

**（十）变异控制：9/9 被杀，三处异常**

`g/MUTATION-G.py` 对 9 处判据各设一条变异，**9/9 `exit == 1` 且红名单非空，且每条
「期望的那一条」都在红名单里**（这正是「判据在判事」那个必要条件的下一问）。
三处异常都不是判据失效，逐条记下来，因为它们会在下一片重演：

* **M3 ——「次序不可观测」。** 变异的落点是 `StopReadout` 的次序守卫，而那个次序在盘上
  由**另一处**先说话：`marketops/application/run_preview.py:890` 的 `_stop_readout`
  里那五个提前 `return StopReadout(...)`（`:937` / `:951` / `:967` / `:981` / `:1000`）。
  守卫被改坏之后读数**没变**，因为前面那一处已经先返回了。
  ⇒ 「这条守卫在判事」与「这个次序在判事」是两句话。
* **M4 ——「那条判据自算滤波器因而瞎」。** `tests/test_run_preview.py:597` 在**判据里
  重算了一遍滤波器**（`kept = tuple(item for item in NOT_REPORTED
  if item.item not in _ANSWERED_WHEN_READY)`），于是它与被它判的那段实现**读的是同一个
  常量**：常量对了它就对，实现换成别的读法它照样绿。它的名字（"drops exactly the three
  answered items"）与它实际断言的东西（留下来的那**两条**）也对不上。
* **M5 —— 量具的编码问题。** 用 `pytest -q` 的 id 去核对红名单时，非 ASCII 的 id 会被
  pytest **转义成 `\uXXXX`**，量具按中文子串去找一律找不到 ⇒ 看起来像「那一条没红」。
  真正的开关是 pytest 的 ini 选项
  `-o disable_test_id_escaping_and_forfeit_all_rights_to_community_support=True`
  （它关掉的是**转义**；那个长名字是 pytest 自己起的），与 `-o addopts=""` 一起传。

### 7.3 g 在 `run_admission.py` 的改名级联

这一片把两个私有函数改成公共名，并**新起了一个同名的私有包装**：

| HEAD | 今天 | 说明 |
|---|---|---|
| `_require_due`（`:228`，只抛不返回） | `due_refusal`（`:228`，返回字符串） | 判定那半，两个调用方共用 |
| —— | `_require_due`（`:337`，**新起**） | 只把类型换成 `RunSpecError` |
| `_spent_of`（`:311`） | `spent_of`（`:366`） | 公共名 |
| `_spec_of`（`:380`） | `spec_of`（`:435`） | 公共名 |

后果：**f 那一份落地记录与冻结的设计件里凡写旧名的地方，今天都不再成立** ——
`docs/B17_F_WATCH_MAINTENANCE_RUN_2026-09-28.md:75`、
`docs/B17_DESIGN_SLICES_2026-09-28.md:764` / `:1080` / `:1099` 仍写 `_spent_of` / `_spec_of`。
冻结件**只登记不改**（它逐字声明过「冻结之后这一份不再长」）。

### 7.4 指针核对：本片**修好 0 条、造出 0 条**

第 9 条那一步的读数，完整推导与三栏表在 `g/reconcile-after-E.txt`，这里只留结论：

| 读数 | 哪张表 | 量哪棵树 | 值 |
|---|---|---|---|
| `g/miss-f.txt` | **f 时代**那张表（`g/CHECK.py.before-g-bump`） | f 那棵树（= HEAD `cb402ba`） | **44** |
| `g/miss-now.txt` | **f 时代**那张表 | **今天**的工作树 | **98** |
| 两代都红（`comm -12`） | —— | —— | **44** |
| 本片修好（`comm -23`） | —— | —— | **0** |
| 本片造出（`comm -13`） | —— | —— | **0** |

`98 − 44 = 54` = 本片挪走的条目数，与 `g/reconcile-moved.txt` 里那 54 个 ★ 同值。
补号表 54 条 54/54 落上（`g/bump-checks.py`），矩阵侧 23 行（`g/bump-matrix.py`），
补记 E 自己新引的 8 条（`g/append-E-rows.py`）；核对表 **224 → 232** 条，复跑后 MISS
**仍恰好 44 且与 f 那棵树逐条同集合**。⇒ 本片在这一维上修好 0 条、造出 0 条，
44 条既有漂移一条没动；而那 44 条里**没有一条能靠位移修好**（两次独立实测）。
判据升级带来的登记翻转与「表行保留而文档侧可补」的差**差为零**这件事，已经在矩阵补记 E
的第六节里量化，此处不重述。既有漂移里有一类值得单独点出来 —— **生在红里**的行：
核对表第 `:72` / `:73` 行（`("marketops/ports/runs.py", 242, ("WATCH",))` 与
`(..., 250, ("bounded_auto",))`）点的那两处，**两棵树都不含**必需串（实测都是标准库散文），
它们从被写进表的那一天起就没绿过。本片的自证对这两条是**反过来**做的：必须**找不到**。

### 7.5 三处读数与一条纪律

* **重复键实测 5 个**，不是先行笔记里记的 3 个，且 224 / 224 / 232 三份表都验过：
  `marketops/application/contact_routes.py:599`、`marketops/domain/maintenance.py:328`、
  `marketops/domain/maintenance.py:612`、`marketops/domain/run_delta.py:69`、
  `marketops/ports/runs.py:273`（各 ×2）。归并成「(文件, 行号) → 串的集合」之后判绿判红
  都不变，所以它只影响「这张表有多少行」这一个读数。
* **`TOOL_CATALOG` 实测 59 → 60**（`git show HEAD` 那棵树 59 条，今天 60 条）：
  本片按规矩加的那一条就是 `preview_run`。那几处 docstring 仍写「十五条」的旧账
  **不在本片修**（正文 §五逐字登记过理由）。**但新加的这一条自己带了一处不齐**：
  它的文字写着「按受理面同样的次序问**同样三个函数**」，同一句里又说「读数收成**五档**」
  —— 与 7.1（二）是同一个数不清，来源同一处。登记，不改。
* **`tests/test_mcp_product_loop.py` 也在本片的改动清单里**（`:1117` 起新增 25 行，
  见 7.2（九））。它不是 preview 的判据住在这里，是**可及性** —— 那一族文件的主题。

**一条纪律，本片第二次咬人**（第一次在补记 E 的收据里）：**仪器读现表会让 HEAD 绿红判据
整片失效。** `g/repoint-matrix.py` 必须读 `g/CHECK.py.before-g-bump`（f 时代那张表）：
读现表的话，本片已补过号的那 54 条会坐在**新**行号上，拿文档里的**旧**号去查一律
「无核对行」，`HEAD绿` / `HEAD红` 这两支整个不可达。实测过一次 —— 修之前 `matrix:212` 的
`runs.py:196` 显示「无核对行」，而它在 f 那棵树上是绿的。

另一条同族：**「逐字同一行」是必要不充分。** `matrix:78` 那个反例里，
`tests/test_mcp_runs.py:401` 被原样搬走、逐字同文，但那一句的主语
（`_rows(settings) == 1`）根本不在那一行上。所以补号要两道都过：**逐字同文**（非平凡行）
**且主语印证**；而判据升级之后，**有核对行的以表为准**（f 那棵树绿而今天红 ⇒ 本片挪走的，
补号；两代都红 ⇒ 本片之前就不指那一行，登记不改）。

### 7.6 收据

* **全量套件**：`PYTHONPATH=. /home/operator/toolchains/marketops-mvp/bin/python -m pytest
  -o addopts="" -q -rf` ⇒ `6280 passed, 4 skipped in 419.50s`，退出码 `0`
  （写这一节之前取的读数）。
* **lint**：`/home/operator/.local/bin/ruff check .` ⇒ `All checks passed!`，退出码 `0`。
  **没有跑 `ruff format`** —— 本仓从不跑它。
* **变异控制**：9/9 被杀（`g/MUTATION-G-OUT.txt`，2026-09-28 21:16），三处异常见 7.2（十）。
* **本节引的每一个地址，都是写盘脚本 `g/append-G-section.py` 在落笔之前逐条实测过的**：
  今天侧的按工作树读、历史侧的按 `git show HEAD:<path>` 读，另有两条按**反过来**的办法验
  （必须找不到，见 7.4）。任何一条不符，脚本拒绝写盘 —— 这一节不是靠我数准行号凑出来的。
  同一份脚本里还钉着自证 1 / 4 / 5：锚点逐字唯一、结果以原文为前缀（旧字节一个不动）、
  `## 七、` 恰好一次。
