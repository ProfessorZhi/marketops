"""§6 的「reconcile dispatch task mapping」：把本地 work item 与外部任务之间那条线维持住。

§6 逐字：**「Store admission and dispatch intent transactionally, reconcile 执行器
task mapping, use attempt/lease protection …」**。前半句由 ``JobStore`` 守
（``admit`` / ``compile_work_items``），后半句是一个**跨系统**的动作，所以它有
自己的那一份契约（``ports/dispatch.py``）和自己的那一段顺序 —— 就是本模块。

## 本模块发明的唯一一样东西：**关联约定**

本地与外部**互不相识**：``JobStore`` 不知道 label 是什么，外部执行器不知道
「作业」和「工作单元」是什么。所以「外面那个任务是我的哪一件」这件事没有现成的
答案，它是我们要发明并**一直维持**的一条约定。它住在这里而不是协议那一侧，因为
它不是任何一方的契约 —— 端口不该知道我们用了几个 label、叫什么名字。

    ``link_labels(job_id, work_item_id)`` → ``("marketops-job:<job>",
    "marketops-item:<item>")``

**为什么是两个 label 而不是一个。** 一个 label 认不出一件事：作业 id 太粗（一个
作业下有许多单元），单元 id 太细到会重（同一个 subject 在两个作业里编译出来的
``work_item_id`` 不同，但一个「按 subject 命名」的 label 会撞）。查询侧是
**AND-join**（``ports/dispatch.py`` 逐字），所以两个都要给，少一个就是一次
「查到的东西比该查到的多」。

**为什么这个函数只有一个。** ``find`` 与 ``create`` 各算一遍这组 label，漂移的
那一天会造出一个**查不回来**的任务：``create`` 按一组 label 写下去，而 ``find``
按另一组去问，于是「已经有了吗」的答案永远是「没有」，每一次重试都新建一个
任务 —— §8B 逐字禁止的 duplicate expensive work，而两边都会正常跑完。
一个生产者，两处消费，不是风格问题。

## 两个入口，**一条**顺序

``dispatch(work_item_id)`` 派一件，``dispatch_job(job_id)`` 把一个作业当下该派的活
按 ``ordinal`` 逐件走完。后者是前者的循环加上「停在哪一件、为什么停」—— 它不另走
一条顺序、不另有一套拒绝（连拒绝的那句话都是同一个函数给的）。所以下面这张表对两个
入口都成立。

## 顺序，以及每一步各自挡的是哪一件事

| 步 | 动作 | 挡住的事 |
|---|---|---|
| 1 | 读 work item / 读 job | 一个指向不存在记录的 id 被静默放过 |
| 2 | **已有 ``execution_ref`` 就原样返回** | 见下 —— 这是本模块最承重的一步 |
| 3 | 作业不放行派发 → 拒 | 派发了一个已经被 pause / cancel 的作业的活 |
| 4 | ``find(labels)`` | 同一件事被交出去两次 |
| 5 | 恰好一条 → **认领** | 见下 |
| 6 | 零条 → ``create`` | —— |
| 7 | ``map_execution`` | 本地记不住，于是下一次重派 |

第 2 步到第 3 步之间**没有**「work item 已停止就拒」这一格，而那是刻意的、
不是漏的：**它到不了**。停止（``WorkItem.stop_reason`` 非空）只有一个写者
（``JobStore.settle``），``settle`` 只收活租约，租约要求先有 ``ExecutionRef``，
而映射是**写一次**的（没有解除映射的入口）—— 所以「已停止」蕴含「已映射」，
第 2 步总是先返回。把它写成一个分支，会得到一条**没有任何输入能走到的判据**：
它与一条恒真的健康旗是同一类东西，读代码的人会以为那里有一道防线。
``tests/test_dispatch_ordering.py`` 有一条判据钉着这个蕴含关系本身。

### 第 2 步：本地映射是「派发过没有」的**权威**

已经映射过的就不再问外部一句。反过来（每次都按 label 重查一遍）看起来更稳，
实际更贵：外部那个任务被谁删掉之后，重查会得到零条，于是**静默地再建一个**。
一次人为的删除于是变成一次重复的昂贵工作，而本地那张映射从始至终都是对的。
把本地映射当权威的代价是「外部被删了我们不会立刻发现」—— 那是第 5 步真正
要挡的那件事的反面，两害相权，账在本地。

``ALREADY_MAPPED`` 在 ``PAUSED`` 的作业上也照样返回：交回一个**已经写下的**事实
不是一次新的派发，没有任何东西被送出去。§6 那句「Pause normally prevents new
dispatch」管的是新的。

### 第 6 步：认领（adopt），以及它为什么是必需的

``find`` 查到恰好一条而本地没有映射 —— 这不是异常，它是**唯一**能从「``create``
成功之后、``map_execution`` 之前进程死了」里恢复过来的路径。那次崩溃留下一个
外面有、本地没有的任务；按 label 认领回来是它唯一能被找回的方式。所以第 6 步
不是容错，它是那条崩溃路径的**正常出口**（``DispatchOutcome.ADOPTED``）。

查到**两条以上则拒绝**，不挑一条。挑一条等于把「同一件事被派发了两遍」的证据
扔掉 —— 而那是 §8B 最贵的一条失败，报告上只会显示「工作多了一倍」。这是要
**停下报告**的事实，不是要自动收敛的噪声。

## 第 3 步那道本地预检只是**避免孤儿**，不替代存储层的检查

``map_execution`` 自己也会拒（作业不放行派发时抛 ``JobStateError``）——
但那时外部任务**已经建出来了**，于是留下一个外面有、本地没有的孤儿。所以这一
步在**碰外部之前**先做，理由就是「不要留下孤儿」。

它**不**替代存储层那一次检查，TOCTOU 仍然在：本层读到「放行」与
``map_execution`` 真正写入之间，控制端可能已经把作业 pause 掉了。那一次的处置
是让 ``JobStateError`` 原样上抛 —— **不要**在这里捕获它去补一个「回滚」，
端口上刻意没有 ``delete``：收回外面的一件事需要一个写明理由的动词，而那个动词
今天不在这条路径上。那一层的残留也不是白留的，它就是第 5 步认领得回来的那种
孤儿。

## 初始外部状态是**写死的**，不是一个参数

``INITIAL_EXTERNAL_STATUS`` 是一个模块常量，没有构造函数参数、没有 per-call
参数。理由与「把一件事做成没有的入口优于有入口 + 一条注意事项」同源：给它一个
带缺省值的参数，就会有两个部署用两个值，而「同一批 work item 在两个部署里落在
两种外部状态上」是一件没人会去比对的事。

## 取值是 ``"ready"``，而这件事是**量出来的**，不是选出来的

第一版这里写的是 ``"todo"``，并把它称作「外部闭集里的入口态」。**那是一句错的
话**，而且是这条边界上最贵的一类错话：它读起来完全合理（``todo`` 确实是闭集
里的第一个词），但它让整条链路**静默地跑不起来**。

2026-09-22 在 0.11.0（插件缓存 ``外部执行器的 0.11.0``，``--version`` 报
``0.11.0-dev``）上量到的是：

* worker-driver 每一轮扫的是 ``status: ready`` 的任务。**``todo`` 的任务它从来
  不看一眼** —— 不是报错，不是跳过并记录，是那一行**不在候选集里**。
* 于是 ``todo`` 的下场与「外部执行器根本没接线」逐字同形：看板上有一行，谁也没
  动过它，而两边都不报错。本仓的第一批 5 个 MOPS 任务（``marketops-mvp``）
  就停在这一格上。

而 ``ready`` 是**受支持**的建任务取值：`task create --status <status>`
的 usage 行里有它，provider 侧的 ``default_task_status`` 也收 ``todo|ready``
（外部执行器源码 的校验函数与取值点）。所以这不是绕过什么 ——
是走到对面本来就开着的那个门上。

**为什么本仓的初始状态就该是 ``ready``，而不是先 ``todo`` 再补一次 promote。**
因为「这件事该不该被做」这个决定在本仓**已经做完了**：``dispatch`` 发生在
``compile_work_items`` 与受理之后，工作项、场景、guidance 版本全都定了。外部那套
``todo → ready`` 的 promotion 判据（``## Contract`` / ``## AC`` checkbox 存在）
问的是「这个任务写清楚了没有」，而本仓的回答在派发那一刻就已经是「清楚了」——
只是它写在我们的表里，不在外部的正文里。补一次 promote 等于为了让对面的门打开
而去伪造一份它的正文，而那正是 §2.9 记过的那种「声明与实现不一致」。

**失效条件，写明。** 外部换掉那个「扫哪个状态」的取值那天，改的是这一行 ——
而改了之后没有一条本仓的判据会红。这是这条边界上少数几个**必须由人看着**的点
之一；写在这里是为了让它是一个被看见的点，而不是一个等着下一次静默停摆的假设。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from ..domain.enums import StopReason
from ..domain.errors import DispatchError, ExternalStateError, JobStateError
from ..domain.job import ExecutionRef, ResearchJob, WorkItem
from ..domain.util import check_canonical_id
from ..ports.dispatch import Dispatcher, ExecutionUnit, ExternalTask
from ..ports.jobs import JobStore

__all__ = [
    "EXTERNAL_STATUSES",
    "INITIAL_EXTERNAL_STATUS",
    "STOP_REASON_EXTERNAL_STATUS",
    "BoardConvergence",
    "BoardObservation",
    "BoardRead",
    "BoardReadKind",
    "BoardSync",
    "DispatchOutcome",
    "DispatchReceipt",
    "DispatchRound",
    "WorkDispatch",
    "board_target",
    "external_task_id",
    "link_labels",
    "work_item_id_from_labels",
]

#: 外部任务建出来时的初始状态。**一个值，一处写** —— 理由见模块 docstring。
#:
#: ``"ready"`` 而不是 ``"todo"``：外部执行器只扫 ``ready``，``todo`` 的任务
#: 永远不会被派发，且**不报错**。理由与实测依据见模块 docstring 同名小节。
INITIAL_EXTERNAL_STATUS = "ready"

#: 外部看板认识的**全部**状态。闭集，从**实现**那侧读的（`task list
#: --help`` 的 ``--status`` 过滤值，0.11.0 实测），理由同
#: ``INITIAL_EXTERNAL_STATUS``。
#:
#: **为什么本仓要抄一份。** 回传写下去的是一个自由字符串，外部不校验它 ——
#: 一个拼错的状态名会被原样存下来，看板上出现一个谁也不认识的状态，而两边都不
#: 报错。抄一份在这里，是为了让「本仓写出去的值在不在那个闭集里」成为一条**可以
#: 被执行的**判据（``tests/test_board_sync.py``），而不是一句自觉。
EXTERNAL_STATUSES = frozenset({"todo", "ready", "done", "needs-human", "superseded"})

#: 结算结局 → 外部状态。**键是 ``StopReason`` 枚举**，所以这是闭集上的映射：
#: 少一个键是静态可见的（判据钉的是集合相等），而不是等到某一次真实结算才炸 ——
#: 那时产出已经落盘，那一次回传则永远地丢了。
#:
#: **分界线是「还欠不欠人一个动作」，不是「成没成功」。** ``no_useful_next_action``
#: 与 ``goal_met`` 同归 ``done``：它说的是「该做的做完了，没有下一步」，不是「我
#: 卡住了」。把它归进待人工，会让一次性研究最常见的诚实终点永久亮在
#: ``needs-human`` 里 —— 而那盏灯亮久了就没人看了。
STOP_REASON_EXTERNAL_STATUS: Mapping[StopReason, str] = {
    StopReason.GOAL_MET: "done",
    StopReason.NO_USEFUL_NEXT_ACTION: "done",
    StopReason.BUDGET_EXHAUSTED: "needs-human",
    StopReason.BLOCKED: "needs-human",
    StopReason.AMBIGUOUS_HOLD: "needs-human",
    StopReason.MANUAL_PAUSE: "needs-human",
}


def board_target(item: WorkItem) -> str | None:
    """这一件工作单元的看板目标状态 —— 从**已落库的结局**读，不从调用方的参数读。

    ## 为什么目标必须是一个可以重放的值

    ``WorkItem`` 的 docstring 写着：``stop_reason`` 非空 ⟺ 有一条 ``Settlement``，
    两半在同一个写事务里。所以「这件工作最后是怎么结束的」这件事**在库里**。

    而结算与回传之间隔着一次外呼，进程可以死在中间 —— 2026-09-22 那次真实运行
    就死在了中间：worker 退出时外面那个任务还是 ``ready``，看板上的结局是**之后**
    才写下去的，而外部那套按退出那一刻的状态判了一次 ``exited-not-landed``。
    死过一次之后再来的调用方，**手上没有那个理由**：它只知道「这件该收尾了」。

    于是「回传可以重放」的前提是**目标可重放**。让目标来自一个参数，等于让
    「上一次要收敛到哪」只活在一个可能已经死掉的进程的内存里 —— 而看板停在
    ``ready`` 的后果不是一句报错：外部那套按 ``ready`` 认「还没做」，它会**再派
    一次**这件事。

    ## 它不接受「调用方比我更清楚」

    这不是一个可以覆盖的默认值。``_sync_board`` 的调用点确实刚把那个理由交给了
    ``settle``，看上去它更清楚 —— 但那条路径之外的每一次到达都更不清楚，而
    参数化之后两者在代码里长得一样。一个**只有**一个来源的值不会与它自己分叉。

    Returns:
        目标状态；``None`` = 这一件还没有结算，因此没有目标。
    """
    if item.stop_reason is None:
        return None
    return STOP_REASON_EXTERNAL_STATUS[item.stop_reason]

#: 两个 label 前缀。它们是**约定**，不是任何一方的契约：外部系统只认得「一组
#: 字符串」，本仓的 ``JobStore`` 连 label 这个词都没有。跟着 ``link_labels``
#: 住在这里，是为了让「谁发明的」和「谁能改」是同一个答案。
_JOB_LABEL_PREFIX = "marketops-job"
_ITEM_LABEL_PREFIX = "marketops-item"

#: 一次查询返回多条时，错误消息里最多点几个 id。**不是截断**（消息里会写明总数
#: 与「还有 N 条」）—— 上限存在的理由是下面这一条：``find`` 是唯一可能**一次拿回
#: 一整张表**的调用（列表查询的过滤旗标有一种退 0 的静默失效法，见 §2.9 的实测），
#: 而一条把几百个 id 拼进去的错误消息会把真正有用的那句话埋掉。
_MAX_IDS_IN_MESSAGE = 5

#: 外部任务 id 的前缀。与下面两个常量一起，是「本地的一对 id → 外部的那个名字」
#: 这条映射的**全部**内容。跟着 :func:`external_task_id` 住，理由与两个 label 前缀
#: 一样：让「谁发明的」与「谁能改」是同一个答案。
_TASK_PREFIX = "MOPS"

#: 从每个 id 里取多少位拼进外部 id。两个 id 各取一段而不是再散一次：这样一个人
#: 拿着外部 id 能直接认出它是哪个作业下的哪个单元，不必回来查。
_ID_SLICE = 8


def external_task_id(job_id: str, unit_id: str) -> str:
    """由本地的一对 id **确定性**地派生外部任务 id —— 这是 ``create`` 幂等的全部机制。

    两个并发的派发者算出同一个字符串，于是第二个的 create 是原地对齐而不是新建。
    换成随机 id（或带时间戳的 id）就会把「同一件事被交出去两次」重新放回来，而
    那时两边都会正常跑完。

    **两个输入都先过 ``check_canonical_id``**，不假设它们已经是规范形式：这个函数
    的返回值会被当作外部的**任务 id** 用，而对面拒 ``/`` 与空字节（M9）。在这里
    校验而不是靠「调用方一定传的是 hash」，是因为调用方能构造 ``ExecutionUnit``。

    ## 为什么不再散一次

    把两个 id 各取一段拼起来就够，**不再过一遍哈希**：再散一次只会让这个 id 变得
    不可读，而不增加任何东西 —— 它要防的「两个单元撞进同一个外部 id」由 ``create``
    的回读判据回答（撞上时那一组 label 已经属于先到的那个，回读会查到 0 条并抛）。
    一个为了「更保险」而加的第二层哈希，会让上面那条真正的判据看起来像多余的。

    ## 为什么它住在这里，以及为什么参数是两个 id 而不是一个 ``ExecutionUnit``

    住在这里是因为**正文必须写出这个名字**（见 :data:`_TASK_BODY` 的 ``## Touches``
    那一节：派发器按「这一件声明了哪些文件」判断两件工作能不能同时跑，而这一件在
    外部那棵树里**唯一**会变的文件就是它自己的任务文件）。写正文的那一层因此必须
    知道这条命名规则 —— 而让它从适配器 import 是不可能的（``application`` 不依赖
    ``adapters``，那是这条分层的方向）。所以这条规则搬到了「两层都能读、而且只有
    一份」的地方：执行器适配器 的 ``create`` 与这里的 :func:`_unit`
    调的是**同一个函数**。两份实现的那天，正文里写的那个文件名会与外面真正建出来的
    那个分家，而两边各自都正常。

    参数是 ``(job_id, unit_id)`` 而不是 ``ExecutionUnit``：写正文时手上只有
    ``ResearchJob`` 与 ``WorkItem``，还没有单元可传 —— 为了拿一个 id 先去构造一个
    正文还没渲染出来的 ``ExecutionUnit``，是把顺序倒过来迁就签名。
    """
    check_canonical_id(job_id, "job_id")
    check_canonical_id(unit_id, "unit_id")
    return f"{_TASK_PREFIX}-{job_id[:_ID_SLICE]}-{unit_id[:_ID_SLICE]}"


#: 外部那条任务正文里写的**全部**内容。它是这件事对外说的那一面，读者是外部那张
#: 看板前的人（以及别的 agent）—— 那个人手里**没有**本仓，所以三个身份要写进去，
#: 否则他为了知道这是哪件事得回来查一次。
#:
#: ## 它**不是**指引，而且这一点是本条常量的全部理由
#:
#: §2.14 H3a 逐字：「指引走 ``get_assignment``，**不**走 执行器的 task 正文」。
#: 所以这里一个市场词都没有、一条操作步骤都没有 —— 它只说「谁提供指引、谁收口」，
#: 并且**指名**那一个工具。抄一份指引进来的代价不是多几行：两份指引会各自漂移，
#: 而漂移的那一份（正文）**没有任何执行器**，读它的人却会照着做。
#:
#: ## 它也不长得像一件软件开发任务，但它**必须**带一节
#:
#: 正文里**只有一节** ``## Touches``，而且那一节里只有一条：这一件自己的任务文件。
#: 两半各有各的理由，都不是排版偏好：
#:
#: * **必须有那一节。** 派发器按它判断两件工作能不能同时跑。实测（2026-09-23，
#:   执行器 0.11.0 的 ``concurrent-batch-scheduler``，喂的就是本模板渲染出来的
#:   正文）：没有这一节时它报 ``ill-declared touches: conservative: side A declares
#:   no/empty ## Touches → serialize``，批宽 **0** —— 于是 ``cap=2`` 会静默退化成
#:   一次一件，而每一轮的报告都正常。补上自身声明之后同一个执行器报
#:   ``BATCH (2-wide, concurrent)``。声明的那一条是**真的**：这一件在外部那棵树里
#:   唯一会变的文件就是它自己的任务文件（结算时由市场侧回写这一行）。
#: * **只许有那一节。** 外部那套机械闸门按小节名认形状（``## Finding`` / ``## AC``
#:   / ``## Plan`` / ``## Contract`` …），几个小节一凑，这一行就会被别人的看板流程
#:   当成一件**代码工单**接走。它不是工单：它由市场侧创建、由市场侧收口，而
#:   「谁晋升它」也因此是市场侧（外部那套自动晋升要求四件套，本仓的条目按设计就
#:   不具备）。原来那条判据写的是「一个小节都没有」，它比这条理由**更宽**；现在
#:   收窄成「只许有派发器要读的那一节」，执行器在
#:   ``tests/test_dispatch_ordering.py`` 的 ``_ALLOWED_BODY_SECTIONS``。
#:
#: 三个身份是 ``str.format`` 的字段，值来自 ``ResearchJob`` / ``WorkItem``（都是
#: 运行时数据，不是常量）—— 正文里**不许**出现任何具体的市场词，由
#: ``tests/test_no_hardcoded_market_knowledge.py`` 对 ``marketops/`` 的 AST 扫描执行。
#:
#: 那一节里的路径由 :func:`external_task_id` 算出来（同一个函数也是适配器建任务时
#: 用的那一个），所以它是**字面路径、不含通配符**：通配符要按当下盘上的文件展开，
#: 而两个任务的展开集合必然相交 —— 一条看着非空的声明会让判据退回串行。
_TASK_BODY = """\
MarketOps research entry — this is not a software task.

job        {job_id}
work item  {work_item_id}
scene      {scene_id}
subject    {subject_ref}

The working agent gets its guidance, its research tools and its budget from the
``marketops`` MCP tool ``get_assignment`` with the work item id above; this entry
deliberately restates none of that. Findings and evidence stay on the market side, and
the market side writes this entry back when the run settles.

## Touches

- tasks/{task_id}.md
"""


def link_labels(job_id: str, work_item_id: str) -> tuple[str, ...]:
    """一次派发的**关联身份**：一个作业 id label 与一个工作单元 id label。

    返回**两个**，且顺序固定（作业在前）。调用方不得自己拿一个去查 —— 查询侧是
    AND-join，少给一个不是「查得宽一点」，是查**别人**的东西。

    Raises:
        ValueError: 任何一个 id 为空。空 id 拼出来的 label 是
            ``"marketops-job:"``：它非空，所以会被读写两面接受，然后**每一条**
            空 job id 的记录都会落进同一个桶。拒绝一个空输入比事后分辨
            「哪些行是那一桶的」便宜得多。
    """
    if not job_id or not work_item_id:
        raise ValueError(
            "link_labels 需要非空的 job_id 与 work_item_id"
            f"（收到 {job_id!r} / {work_item_id!r}）。空 id 拼出的 label 会被"
            "读写两面都接受，于是所有空 id 的记录共用一个桶。"
        )
    return (f"{_JOB_LABEL_PREFIX}:{job_id}", f"{_ITEM_LABEL_PREFIX}:{work_item_id}")


def work_item_id_from_labels(labels: Sequence[str]) -> str:
    """从一组 label 里读回 ``link_labels`` 写下的那个**工作单元** id。

    入站方向的第三个消费者：``marketops-research-worker`` 收到一条 派发任务时手上
    只有任务的 label，而它要回答「这件工作单元是谁」。执行器的 ``-p`` 里那句身份
    只有 task id、没有 work item id，所以这件事在这条路上**没有第二个来源** ——
    读错就是去领另一件工作，而两次调用各自的日志都正常。

    只读 item 那一半，不要求 job 那一半也在：两个 label 是一起写的，缺一个只说明
    这条任务不是这条路写出来的，而「这条任务该不该由我们派发」是调用方的问题。

    Raises:
        ValueError: 恰好一个 item label 都没有、有**两个不同的**、或者那一个的尾巴
            是空的 / 带首尾空白。四种都拒绝，不回落 —— 挑一条或补一个都是猜。
    """
    found: set[str] = set()
    for label in labels:
        if not label.startswith(f"{_ITEM_LABEL_PREFIX}:"):
            continue
        tail = label[len(_ITEM_LABEL_PREFIX) + 1 :]
        if not tail or tail != tail.strip():
            raise ValueError(
                f"label {label!r} 的 id 部分是空的或带首尾空白 —— 它不是「没有 label」，"
                "是一个残缺的 label：前者说明有人写坏了，后者说明这条任务不该由我们"
                "派发，两件事不该报成同一件。"
            )
        found.add(tail)
    if not found:
        raise ValueError(
            f"{len(list(labels))} 个 label 里没有 {_ITEM_LABEL_PREFIX}:… —— 读不出这件"
            f"工作单元是谁。labels={list(labels)!r}"
        )
    if len(found) > 1:
        raise ValueError(
            f"有 {len(found)} 个不同的工作单元 label（{sorted(found)!r}）—— 挑一个是猜，"
            "而这条任务是一件事。"
        )
    return found.pop()


@dataclass(frozen=True)
class BoardSync:
    """一次结算之后回传外部看板的结果。**六个字段恒在，形状固定。**

    ## ``synced`` 与 ``changed`` 为什么是两个字段

    它们回答两个不同的问题：``synced`` 是「看板现在对不对」，``changed`` 是
    「这一次真的动了吗」。**重发恰好落在 ``changed=False, synced=True``** ——
    那不是失败，是「上一次已经写好了」。合成一个布尔，重发就会长得像一次失败，
    而失败该有的处置（去读 ``error``）在那里没有东西可读。

    ## 失败是字段，不是异常

    本地结算与外部回传不可能原子，而**结算已经发生了**。把回传失败抛成异常，会
    让调用方以为产出没落盘 —— 于是它重发，拿到 ``replayed: true``，而看板永远
    没被同步过。「说错了但做完了」这个中间态比一条明确的失败更坏，所以这里只报。

    ## ``observed_status`` 是**我们读到的**那一个

    不是外面此刻真实的那一个 —— 两者在读与写之间可能分叉。分叉时 CAS 会拒，
    而外面真实的值出现在 ``error`` 里（那段报文是外部报回来的，本层不改写它）。
    所以「冲突了」这件事必须读 ``error``，不能只看 ``observed_status``。
    """

    #: 外面那个任务的 id。没有映射（从没派发过）时是 ``None`` —— 与 ``""``
    #: 的区别是「没有这件事」与「这件事的 id 是空的」，而只有前者可能出现。
    task_id: str | None
    #: 这一次要把看板收敛到哪。**失败时也照报** —— 它是这次意图的一部分，
    #: 而「本来要去哪」正是排查一个失败回传时要看的第一样东西。
    #:
    #: ``None`` **当且仅当**这一件还没有结算过：目标从已落库的结局读出来
    #: （:func:`board_target`），没有结局就没有目标。它不是 ``""`` —— 空串是
    #: 「要收敛到一个名字为空的状态」，而外部闭集里没有那个词（同 ``task_id``
    #: 那一段：``None`` 与 ``""`` 的区别是「没有这件事」与「这件事是空的」）。
    target_status: str | None
    #: 回传**前**读到的状态。没读到（读本身失败了）是 ``None``。
    observed_status: str | None
    #: 看板现在对不对。
    synced: bool
    #: 这一次真的动过看板没有。
    changed: bool
    #: 没同步的原因。``synced`` 为真时恒为 ``None``。
    error: str | None


@dataclass(frozen=True)
class BoardConvergence:
    """一个作业上**已经结算的那些件**的看板收尾：逐件的结果，加三个计数。

    ## 它与 ``DispatchRound`` 是两个数字，而它们本来就不该相等

    ``DispatchRound`` 数的是「这一轮**交出去**几件」，``BoardConvergence`` 数的是
    「这一轮把**几件的看板**推到位」。同一个作业可以先派 5 件、只结算 1 件，所以
    这两个数字合成的那个数字是有歧义的：一次结算看起来像一次派发。

    ## ``unmapped`` 与 ``unsettled`` 为什么各自要有位置

    已经结算、但外部没有映射的件**不是「还没同步」** —— 外面从来没有过这件事，
    没有看板可收敛。把两件并成一格（一律叫「待同步」），会让一个「派发之前就停掉
    的作业」永远报着待同步，而一个永远亮着的数字与一盏永远亮着的灯一样：亮久了
    就没人看了（同 ``needs-human`` 那一段）。所以三者分开，且它们的和**恒等于**
    这个作业的件数 —— 分母在，读者才能自己判断 0 的分量。
    """

    job_id: str
    #: 逐件的结果，按 ``work_items`` 的 ``ordinal`` 顺序。**只含已结算且已映射的
    #: 件** —— 它们是有看板可收敛的那一些。
    syncs: tuple[BoardSync, ...]
    #: 已结算但外部没有映射的件数。**不是失败**，是本仓从来没把这件事交出去。
    unmapped: int = 0
    #: 还没结算的件数（编译出来了、还没有人做完）。
    unsettled: int = 0

    @property
    def considered(self) -> int:
        """这一趟**读了外部**的件数。"""
        return len(self.syncs)

    @property
    def pushed(self) -> int:
        """这一趟真的动了看板的件数。**不是「到达目标状态的件数」** —— 后者还要
        加上那些读到就已经对了的（它们不该算进「这一趟做了什么」）。"""
        return sum(1 for sync in self.syncs if sync.changed)

    @property
    def pending(self) -> int:
        """还没到位的件数。原因在各自的 ``BoardSync.error`` 里 —— 不是一句话，
        因为逐件的原因可能不同（一件是执行器起不来，另一件是外面那条被删了）。"""
        return sum(1 for sync in self.syncs if not sync.synced)


class BoardReadKind(str, Enum):
    """一次板面读取的**种类**。闭集四员，而它们分开的理由是**处置不同**。

    读不到东西有四种，四种都不报错：

    * ``OK`` —— 读到了，``observed_status`` 是外面此刻的状态。
    * ``MISSING`` —— 按 label 一条都查不到。映射还在本地，外面那一行没了。
      处置是**去看谁删的**，不是重建（重建 = 一次重复的昂贵工作）。
    * ``DUPLICATE`` —— 查到不止一条。这是「同一件事被交出去两次」的证据
      （§8B），处置是**停下报告**，不是挑一条。
    * ``UNREADABLE`` —— 这次读取**本身**没跑成。前三种都说明「外面是什么样」，
      这一种说明「我们不知道外面是什么样」—— 把它并进 ``MISSING`` 会让一次
      执行器故障长得像一次人为删除。

    前三种是**外面的**结论，第四种是**我们这一侧**的结论。这个分界线就是这张
    闭集存在的理由：``读不出来`` 与 ``读出来不相等`` 是两个诊断。
    """

    OK = "ok"
    MISSING = "missing"
    DUPLICATE = "duplicate"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class BoardRead:
    """**只读**地读回一件的板面状态 —— 不写任何东西。

    ``error`` 非空**当且仅当** ``kind`` 不是 ``OK``，且那时它是给人读的那一段
    （与 :class:`BoardSync` 的 ``error`` 同源、同一处产生）。``kind`` 是机器读的
    那一半：上面四种处置里只有 ``OK`` 一种能往下走。
    """

    task_id: str
    kind: BoardReadKind
    observed_status: str | None
    error: str | None


@dataclass(frozen=True)
class BoardObservation:
    """一个作业上**每一件已映射的件**的板面读数。**一件都没碰外面。**

    ## 它与 ``BoardConvergence`` 不重合，而那不是疏漏

    ``BoardConvergence`` 只含「已结算且已映射」的件：给一件还在跑的活下一个结论
    是抢在它前面写结局。读数（本类型）含**全部已映射**的件，因为「外面比本地靠前」
    那类漂移（本地还没结算、外面已经 ``done``）恰恰在 ``BoardConvergence`` 的
    视野之外 —— 它被算进 ``unsettled`` 那个计数里，看不出是哪一件。

    两个类型的字段都叫 ``syncs`` / ``rows`` 而不是同一个名字，是为了让
    「我手上这份能不能写」在类型上就看得出来：``BoardConvergence`` 来自一次
    真的收敛，``BoardObservation`` 来自一次只读。
    """

    job_id: str
    #: 逐件（``WorkItem``, 读数），按 ``work_items`` 的 ``ordinal`` 顺序。
    rows: tuple[tuple[WorkItem, BoardRead], ...]

    @property
    def unreadable(self) -> int:
        """读不成的件数（``UNREADABLE``）。与 ``MISSING`` 分开计数 —— 见
        ``BoardReadKind``：一个是外面的结论，一个是我们这一侧的结论。"""
        return sum(1 for _, read in self.rows if read.kind is BoardReadKind.UNREADABLE)

    @property
    def missing(self) -> int:
        """外面查不到的件数。"""
        return sum(1 for _, read in self.rows if read.kind is BoardReadKind.MISSING)

    @property
    def duplicated(self) -> int:
        """外面查到不止一条的件数。"""
        return sum(1 for _, read in self.rows if read.kind is BoardReadKind.DUPLICATE)


class DispatchOutcome(str, Enum):
    """这一次派发**做了什么**。闭集三员，三者的区别是可观测的。

    分成三个而不是一个 ``created: bool``，是因为它们各自要说的话不同：

    * ``ALREADY_MAPPED`` —— 外部一次都没被碰过（连读都没有）。它是唯一一个
      ``DispatchReceipt.external is None`` 的取值。
    * ``ADOPTED`` —— 外部**本来就有**这件事（上一次派发死在 ``create`` 与
      ``map_execution`` 之间，或者有人手工建了它），这一次把它认领了回来。
    * ``CREATED`` —— 这一次建出来的。它同时是「这一次的账单」的记号。

    ``ADOPTED`` 与 ``CREATED`` 分开，是因为运维处置相反：前者要去问「上一次
    为什么没记住」，后者什么都不用问。合成一个 ``ok``，那件要查的事就沉了。
    """

    ALREADY_MAPPED = "already_mapped"
    ADOPTED = "adopted"
    CREATED = "created"


@dataclass(frozen=True)
class DispatchReceipt:
    """一次派发的回执。**本地的那一半与外部的另一半分开放在两个字段里。**

    ``ref`` 是本地那张映射 —— 写下去的那一行，或早就存在的那一行。
    ``external`` 是**外部实际报回来的那一份**（``ExternalTask``），不是我们
    送出去的那一份：``ADOPTED`` 时它的 ``labels`` 可能是我们要的那组的**超集**
    （别人也在跟这件事），而那正是回执里值得看的东西。

    ``external is None`` **当且仅当**这一次没有碰过外部（``ALREADY_MAPPED``）。
    把它写成 ``""`` / ``()`` 而不是 ``None``，会让「没读」与「读到了空」长得
    一样 —— 而这两件事的处置相反（前者什么都不用做，后者说明对面出问题了）。
    """

    work_item_id: str
    job_id: str
    outcome: DispatchOutcome
    ref: ExecutionRef
    external: ExternalTask | None


@dataclass(frozen=True)
class DispatchRound:
    """一次**作业级**派发做了什么：每一件的回执、停在哪一件上、为什么停。

    ``blocker`` 非空而 ``receipts`` 为空**有两种**成因，回执里必须能分开 ——
    一件都没派是因为作业不放行派发，还是因为作业手里一件工作都没有？后者是
    ``blocker`` 为空、``receipts`` 为空，而它**不是**一次拒绝，是一件可做的事
    都不存在。（经由工具进来的作业拿不到这一格：``compile_work_items`` 拒绝空
    清单，所以「有作业、没工作单元」只在有人直接读存储时出现。）
    """

    job_id: str
    receipts: tuple[DispatchReceipt, ...]
    #: 停在哪一件 work item 上。空串 = 没有中途停下。
    stopped_at: str = ""
    #: 为什么一件都没派、或为什么停下。空串 = 没有理由要报。
    blocker: str = ""

    @property
    def by_outcome(self) -> dict[DispatchOutcome, int]:
        """三个取值各自的件数，**三个键恒在**。

        这里与「一个恒为 0 的桶」那条例外不同：三个桶的和恒等于 ``len(receipts)``，
        所以 0 是一个**有位置的**数字 —— 它说的是「这一轮里没有一件是这样办的」，
        而少一个键会让「没有认领过」与「这一版还没记录认领」长得一样。
        """
        counts = {outcome: 0 for outcome in DispatchOutcome}
        for receipt in self.receipts:
            counts[receipt.outcome] += 1
        return counts

    @property
    def handed_out(self) -> int:
        """真正**碰过外部**的件数：``CREATED`` + ``ADOPTED``。

        ``ALREADY_MAPPED`` 不算，它连读都没读（``DispatchReceipt.external is None``）。
        这个数字是「这一轮交出去几件事」，而它**不是**「这个作业一共有几件事」——
        后者问 ``JobStore.work_items``。两个数字混用，会让一次重发看起来像一次派发。
        """
        counts = self.by_outcome
        return counts[DispatchOutcome.CREATED] + counts[DispatchOutcome.ADOPTED]


def _dispatch_refused(job: ResearchJob) -> str:
    """作业不放行派发时的那一句话。**一处写**，两个调用点都报它。

    单件派发把它当异常消息抛，作业级派发把它当 ``DispatchRound.blocker`` 返回 ——
    同一件事在两处各写一句，漂移的那天调用方会以为它们是两种不同的拒绝。
    """
    return (
        f"作业 {job.job_id} 现在不放行派发（状态 {job.state.value}）。"
        "§6 逐字：Pause normally prevents new dispatch and lets accepted "
        "in-flight results settle —— 恢复它是 ``control_job(action=resume)``，"
        "不是换一条派发路径。"
    )


class WorkDispatch:
    """把 §6 的「派发一件工作」按上面那张表走完。

    Args:
        jobs: 持久作业记录。本层读它、也写它（``map_execution``），但**不**知道
            它背后是什么（SQLite 还是内存）—— 那是组合根的事。
        dispatcher: 外部执行器的契约（``ports/dispatch.py``）。本层不 import 任何
            具体实现：谁来当执行器由组合根回答，而组合根是本仓唯一有权点名厂商
            adapter 的地方（``tests/test_adapters_hold_the_vendors.py``）。

    依赖是注入的、且是**端口**：本模块因此可以在没有外部执行器、没有磁盘的
    情况下被完整测到（``tests/test_dispatch_ordering.py`` 就是一个记账的替身）。
    """

    def __init__(self, *, jobs: JobStore, dispatcher: Dispatcher) -> None:
        self._jobs = jobs
        self._dispatcher = dispatcher

    # ------------------------------------------------------------------
    def dispatch(self, work_item_id: str) -> DispatchReceipt:
        """把一件 work item 送到外部，并记下那条线。顺序见模块 docstring。

        Raises:
            JobStateError: work item / 作业不存在，或作业不放行派发。**两种分开
                说**，因为处置不同（改 id / 先 ``control_job`` 恢复），而合成
                一句「派发被拒」会让后者的处置变成一个猜谜。
            ExternalStateError: 外部带着同一组 label 的任务**不止一条**。本层不
                挑一条来认领 —— 挑一条等于把「同一件事被派发了两遍」的证据扔掉。
            DispatchError: 执行器没能给出一份可读的答案（起不来、超时、输出读不懂）。
                **它不是** ``JobStateError``：前者是「这次调用不许」，后者是
                「用来得出结论的那个东西不工作了」。
            ValueError: 由 ``link_labels`` 与实现侧的形状判据抛出（空 id、
                写不下去的 label 等）。
        """
        item = self._jobs.get_work_item(work_item_id)
        if item is None:
            raise JobStateError(
                f"没有这个 work item：{work_item_id!r}。派发的主语必须是一条已经"
                "编译出来的工作单元；工作单元由 ``compile_work_items`` 产生。"
            )
        job = self._jobs.get(item.job_id)
        if job is None:
            # 外键在的话到不了这里。到了说明这份存储里有一条悬空的 work item，
            # 而派发一件「不知道属于谁」的工作意味着连它该不该派发都判不了。
            raise JobStateError(
                f"work item {item.work_item_id} 指向的作业 {item.job_id} 不在 —— "
                "连这个作业放不放行派发都判不了。"
            )

        existing = self._jobs.execution_ref(item.work_item_id)
        if existing is not None:
            return DispatchReceipt(
                work_item_id=item.work_item_id,
                job_id=job.job_id,
                outcome=DispatchOutcome.ALREADY_MAPPED,
                ref=existing,
                external=None,
            )

        # **本地**预检，在碰外部之前。它只是避免孤儿，TOCTOU 仍在 —— 见模块
        # docstring，那里写明了这一层为什么不去补一个「回滚」。
        #
        # 这里**没有**「work item 已停止就拒」那一格：它到不了（已停止蕴含已映射，
        # 上一步已经返回了）。理由写在模块 docstring 的顺序表下面。
        if not job.admits_dispatch:
            raise JobStateError(_dispatch_refused(job))

        labels = link_labels(job.job_id, item.work_item_id)
        found = self._dispatcher.find(labels=labels)
        if len(found) > 1:
            raise ExternalStateError(_duplicate_tasks_message(item.work_item_id, found, labels))

        if found:
            task = found[0]
            outcome = DispatchOutcome.ADOPTED
        else:
            task = self._dispatcher.create(
                self._unit(job, item), labels=labels, status=INITIAL_EXTERNAL_STATUS
            )
            outcome = DispatchOutcome.CREATED

        ref = self._jobs.map_execution(item.work_item_id, dispatch_task_id=task.external_id)
        return DispatchReceipt(
            work_item_id=item.work_item_id,
            job_id=job.job_id,
            outcome=outcome,
            ref=ref,
            external=task,
        )

    # ------------------------------------------------------------------
    def dispatch_job(self, job_id: str) -> DispatchRound:
        """把一个作业**当下该派的活**按 ``ordinal`` 逐件派出去（§6 的入口）。

        没有这一条，``dispatch`` 在整份生产代码里**零调用**：单件派发是一个
        正确的、自己就能测的动作，但没有任何东西会去**遍历**一个作业的工作单元
        并把它们交出去。于是「受理了」与「外面有任务在跑」之间没有桥。

        ## 已经在外面有映射的件：原样返回，不重派

        上一轮的 ``ALREADY_MAPPED`` 走的就是 ``dispatch`` 的第 2 步。所以这个
        方法**幂等**：重发同一个作业不会造出第二个任务（§8B 逐字禁止
        duplicate expensive work），而重发这件事本身是正常的 —— 受理、resume、
        断线重连之后的重试都会走到这里。这也是「派发过没有」的权威在本地映射
        那一条纪律在这里的样子。

        ## 途中失败就停下，不跳过它继续

        三种失败（外部带着同一组 label 的任务不止一条、执行器起不来/答非所问、
        作业在循环中被 pause 掉）指向同一个判断：**现在这条路径给出的答案不可信**。
        继续往下派的代价是真的把活交出去 —— 所以停在第一件上，``stopped_at`` 与
        ``blocker`` 说清停在哪、为什么。已经派出去的那几件**不会被收回**：端口上
        没有 ``delete``，收回外面的一件事需要一个写明理由的动词，而它不在这条
        路径上（同 ``dispatch`` 的 TOCTOU 那一段）。

        ## 作业不放行派发时一件都不碰

        这一格在碰外部**之前**返回，``receipts`` 为空、外部一次都没被读过。
        它不是异常：调用方要的是「这一次派发做了什么」的如实回答，而一次拒绝
        就是那个回答的一种。§6 那句「Pause normally prevents new dispatch」在这里
        第一次真的有东西可以阻止。

        Raises:
            JobStateError: 作业不存在。**只有这一种** —— 其余拒绝都写在返回值里，
                因为它们的处置是「停下报告」，不是「这次调用不合法」。
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobStateError(
                f"没有这个作业：{job_id!r}。派发的主语必须是一个已经受理的作业；"
                "作业由 ``JobStore.admit`` 产生。"
            )
        if not job.admits_dispatch:
            return DispatchRound(job_id=job_id, receipts=(), blocker=_dispatch_refused(job))

        receipts: list[DispatchReceipt] = []
        for item in self._jobs.work_items(job_id):
            try:
                receipts.append(self.dispatch(item.work_item_id))
            except (JobStateError, ExternalStateError, DispatchError) as exc:
                return DispatchRound(
                    job_id=job_id,
                    receipts=tuple(receipts),
                    stopped_at=item.work_item_id,
                    blocker=str(exc),
                )
        return DispatchRound(job_id=job_id, receipts=tuple(receipts))

    # ------------------------------------------------------------------
    def reconcile(self, work_item_id: str, *, dry_run: bool = False) -> BoardSync:
        """把一次结算的结局回传到外部看板 —— **收敛**，不是迁移。

        ## 它是 ``dispatch`` 的镜像，而两条纪律是相反的

        ``dispatch`` 拿**本地**映射当「派发过没有」的权威，一次都不问外部；
        ``reconcile`` 反过来，它必须先读外部。这不是不一致：派发问的是「要不要把
        这件事交出去」，答案在本地；回传问的是「外面现在是什么」，只有外面知道。
        同一个映射在两条路径上因此有两副面孔 —— 前者拿它当**结论**，后者拿它当
        **地址**（去读，不是去下判断）。

        ## 为什么是收敛

        结算是一次只发生一次的事件，回传是它之后的一次外呼，两者不可能原子，所以
        这条路一定会被走到第二次。一次**迁移**式的回传在第二次会撞 CAS 冲突，而
        冲突报文说的是「有别人先写了」—— **一条凭空造出来的并发**。收敛式的回传
        第二次读到「已经是目标状态」，一次都不发。

        ## 失败一律回报，不抛

        下面没有 ``Raises`` 段，而那是这条路径的要害：本地结算已经落库，回传失败
        不改变那个事实。抛出去会让调用方以为产出没落盘。可预期的失败只有两类，都
        来自外部，都进 ``BoardSync.error``：``DispatchError``（执行器起不来 / 答非
        所问）与 ``ExternalStateError``（CAS 拒了 —— 冲突，或那个 id 外面没有了）。

        其余的异常照常上抛：``link_labels`` 的 ``ValueError``（空 id）到不了
        （``get_work_item`` 先返回 ``None``），而编程错误不该被吞成一次「同步失败」。

        Args:
            work_item_id: 要回传的那件工作单元。**没派发过不是错误**，是一件还没
                交出去的事的正常状态，回报在 ``error`` 里说清是哪个原因。**没结算
                过也不是异常**：那时没有目标状态可读，``target_status`` 是 ``None``
                （见 :func:`board_target`）。
            dry_run: 只读外面、不写外面。**同一个实现**，减去那一次 ``transition``
                —— 判据是「该不该同步」的那一段逐字相同，所以 dry-run 说「会变」
                与 apply 真的变是同一件事。见 :meth:`_converge`。
        """
        item = self._jobs.get_work_item(work_item_id)
        if item is None:
            return BoardSync(
                task_id=None,
                target_status=None,
                observed_status=None,
                synced=False,
                changed=False,
                error=(
                    f"没有这个 work item：{work_item_id!r}，因此没有可回传的看板。"
                    "回传的主语是一条已经派发过的工作单元。"
                ),
            )

        target = board_target(item)
        if target is None:
            return BoardSync(
                task_id=None,
                target_status=None,
                observed_status=None,
                synced=False,
                changed=False,
                error=(
                    f"work item {work_item_id} 还没有结算（``stop_reason`` 是空的），"
                    "因此没有可回传的结局。\n"
                    "看板的目标状态从 ``settle`` 落库的那条理由读出来，而这个函数"
                    "**不接受**由调用方补一个 —— 那样「要收敛到哪」就只活在一个进程"
                    "的内存里，而进程死在结算与回传之间正是这条路径最常见的到达方式"
                    "（2026-09-22 那次运行就是这样停下的）。"
                ),
            )

        ref = self._jobs.execution_ref(work_item_id)
        if ref is None:
            return BoardSync(
                task_id=None,
                target_status=target,
                observed_status=None,
                synced=False,
                changed=False,
                error=(
                    f"work item {work_item_id} 没有外部映射（没派发过），"
                    "外部没有可同步的东西。"
                ),
            )

        return self._converge(ref, item.job_id, target, dry_run=dry_run)

    # ------------------------------------------------------------------
    def converge_settled(self, job_id: str) -> BoardConvergence:
        """把这个作业上**已经结算**的每一件推到位 —— 派发轮次之后的那一趟。

        ## 它修的是哪一种失败

        结算与回传之间隔着一次外呼。那一次外呼失败、或进程死在中间之后，看板上
        那一行仍然是 ``ready``。外部那套的任务驱动按 ``ready`` 认「还没做」——
        于是它会**再派一次**这件事：一个已经做完的工作，外面看起来还没开工。

        重复派发本身**不会**重复花钱，而这靠的是另一道闸门而不是这一趟：已停止的
        工作单元不再出租租约（``JobStore.lease``），而 ``get_assignment`` 在任何
        一次检索、任何一次模型调用之前就撞在那上面。这一趟修的是剩下那半件事 ——
        看板一直是错的那个数字，以及一遍遍真实的 worker 启动。

        ## 为什么是「再读一遍」而不是「记住上次没成」

        目标状态从库里读（:func:`board_target`），收敛判据从外部读（``_converge``
        先 ``find`` 再决定发不发 CAS）。所以这一趟**不需要**任何「上次同步失败了」
        的本地记录：它问的是「现在对不对」，而那个问题的答案只有外面知道。代价是
        每一趟对每一件已映射的件各读一次外部 —— 那是「如实回答看板对不对」这笔账
        本身，省不掉。把「已同步」记在本地可以省掉这些读，代价是本地那一格**会说
        谎**：外面那一行被谁删掉之后，本地记着「已同步」，而实际什么都没有。

        ## 它碰哪些件、不碰哪些件

        碰的是**已结算且已映射**的件。不碰没结算的（外面还在做，替它下一个结论就是
        抢在它前面写结局），也不碰没映射的（外面从来没有过这件事）。三类各自计数，
        见 ``BoardConvergence``。

        ## 这一趟什么时候跑

        由调用方在派发轮次之后调（``tools.py::_dispatch_block``），所以触发点是
        **下一次控制动作**：受理、resume。本构建里没有任何计时器会去跑它 —— 这句话
        写在这里是因为「看板还是没变」最容易被读成「这一趟没生效」，而真实的原因
        常常是这一趟**还没被谁叫醒过**。

        ## 它**不**是运维对账的入口，而那是刻意的

        它挂在一个会派发的调用点上，所以「只把历史对账干净」这件事从这里表达不出来
        —— 要收敛就得同时开始新的付费工作。那个入口在
        ``application/reconcile.py``，它逐件走 :meth:`reconcile`（**同一个**
        ``_converge``，所以两处看到的是同一份判据），并且**一次都不派发**。

        本方法因此**没有** ``dry_run``：它的调用者已经决定要写，而多一个用不上的
        模式参数会造出一条**没有任何输入能走到的判据** —— 与「一盏恒绿的灯」同类。

        Raises:
            JobStateError: 作业不存在。**只有这一种** —— 逐件的失败进
                ``BoardSync.error``，理由同 ``reconcile``（结算已经发生，回传的
                失败不改变那个事实）。
        """
        if self._jobs.get(job_id) is None:
            raise JobStateError(
                f"没有这个作业：{job_id!r}。看板收尾的主语是一个已经受理的作业；"
                "作业由 ``JobStore.admit`` 产生。"
            )

        syncs: list[BoardSync] = []
        unmapped = 0
        unsettled = 0
        for item in self._jobs.work_items(job_id):
            target = board_target(item)
            if target is None:
                unsettled += 1
                continue
            ref = self._jobs.execution_ref(item.work_item_id)
            if ref is None:
                unmapped += 1
                continue
            syncs.append(self._converge(ref, item.job_id, target))
        return BoardConvergence(
            job_id=job_id,
            syncs=tuple(syncs),
            unmapped=unmapped,
            unsettled=unsettled,
        )

    # ------------------------------------------------------------------
    def _converge(
        self, ref: ExecutionRef, job_id: str, target: str, *, dry_run: bool = False
    ) -> BoardSync:
        """读一次当下状态，不等就发一次 CAS —— ``reconcile`` 的下半截。

        单独一个方法不是为了复用（只有一个调用点），是为了让 ``reconcile`` 那段
        docstring 说的事与这里做的事分得开：上面判「该不该做、做到哪」，这里只做。

        ## ``dry_run`` 为什么是一个参数，而不是第二个函数

        因为「该不该同步」这条判据只许有一份。写成第二个函数，dry-run 会在某一天
        与 apply 分家，而分家的表现是**运维看到一份与实际执行不同的计划** —— 那
        比没有 dry-run 坏：一个看起来能预演、实际在撒谎的工具。

        所以这里只有一处分支，就在那次 ``transition`` 上。其余逐字相同：同样读
        外面、同样比 ``observed == target``、同样那一串诊断。于是「dry-run 说会变」
        与「apply 真的变」不是两件事。

        ``dry_run`` 下 ``BoardSync.synced/observed_status`` 仍然说的是**外面此刻
        的真实状态**（不是预测），``changed`` 说的是「差一个写」。这三个字段的
        语义因此不随模式漂移。
        """
        task_id = ref.dispatch_task_id
        read = self._observe(ref, job_id)
        if read.error is not None:
            return BoardSync(
                task_id=task_id,
                target_status=target,
                observed_status=read.observed_status,
                synced=False,
                changed=False,
                error=read.error,
            )
        if read.observed_status == target:
            return BoardSync(
                task_id=task_id,
                target_status=target,
                observed_status=read.observed_status,
                synced=True,
                changed=False,
                error=None,
            )
        if not dry_run:
            try:
                self._dispatcher.transition(
                    task_id, expected=read.observed_status, status=target
                )
            except (DispatchError, ExternalStateError) as exc:
                return BoardSync(
                    task_id=task_id,
                    target_status=target,
                    observed_status=read.observed_status,
                    synced=False,
                    changed=False,
                    error=str(exc),
                )
        #: ``synced=not dry_run``：dry-run 下外面**没有**被改，所以「外面现在是不是
        #: 目标状态」的诚实答案是「不是」。`changed=True` 说的是**差一个写**。
        #: 三个字段合起来的读法：`changed and not synced and error is None` =
        #: 「dry-run 说这里要写一次」—— 这是 dry-run 下唯一一种到达这里的形状。
        return BoardSync(
            task_id=task_id,
            target_status=target,
            observed_status=read.observed_status,
            synced=not dry_run,
            changed=True,
            error=None,
        )

    # ------------------------------------------------------------------
    def _observe(self, ref: ExecutionRef, job_id: str) -> BoardRead:
        """按 label 从外面读回这一件 —— **读取这一侧唯一的一份实现**。

        ## 为什么把它从 ``_converge`` 里提出来

        因为运维需要一个**不做写**的板面读数（``application/reconcile.py``），而
        那个读数必须与写路径看到的**逐字相同**。让它自己再 ``find`` 一次，等于把
        「查到了几条、查到了什么、查不到是什么意思」这三条判据复制一份 —— 复制
        出来的那一份迟早会与这一份分家，而分家的表现是**运维看到一份与实际执行
        不同的板面**。那是比没有这个入口更坏的事：一个看起来能预演、实际在撒谎的
        工具。

        所以写路径（``_converge``）与只读路径（:meth:`observe_board`）都走这里。

        Returns:
            :class:`BoardRead`。``error`` 非空表示**读这一侧**已经有结论了
            （读不成 / 查不到 / 查到不止一条），三种各有自己的 ``kind``；
            ``error`` 为空时 ``observed_status`` 是外面**此刻**的状态。
        """
        labels = link_labels(job_id, ref.work_item_id)
        try:
            found = self._dispatcher.find(labels=labels)
        except (DispatchError, ExternalStateError) as exc:
            return BoardRead(
                task_id=ref.dispatch_task_id,
                kind=BoardReadKind.UNREADABLE,
                observed_status=None,
                error=str(exc),
            )
        if len(found) > 1:
            return BoardRead(
                task_id=ref.dispatch_task_id,
                kind=BoardReadKind.DUPLICATE,
                observed_status=None,
                error=_duplicate_tasks_message(ref.work_item_id, found, labels),
            )
        if not found:
            return BoardRead(
                task_id=ref.dispatch_task_id,
                kind=BoardReadKind.MISSING,
                observed_status=None,
                error=(
                    f"本地记着 work item → {ref.dispatch_task_id} 这条映射，但外面按它的"
                    f" label（{', '.join(labels)}）一条都查不到。\n"
                    "本地映射因此**仍然**是权威（同 ``dispatch`` 第 2 步）：这件事"
                    "派发过，是外面那一行被谁删了。本层不重建它 —— 重建等于让一次"
                    "人为的删除变成一次重复的昂贵工作（§8B 逐字禁止 "
                    "duplicate expensive work）。"
                ),
            )
        return BoardRead(
            task_id=ref.dispatch_task_id,
            kind=BoardReadKind.OK,
            observed_status=found[0].status,
            error=None,
        )

    # ------------------------------------------------------------------
    def observe_board(self, job_id: str) -> BoardObservation:
        """**只读**地把这个作业上每一件已映射的看板状态读回来。

        与 :meth:`converge_settled` 的两处不同，都是刻意的：

        1. 它**一件都不碰**外面（不 ``transition``），所以它对「外面是不是对的」
           这件事只有一个意见，没有动作。
        2. 它**不收窄到已结算的件**。``converge_settled`` 只碰「已结算且已映射」
           的，因为给一件还在跑的活下一个结论是抢在它前面写结局。而只读的读数
           恰恰要看全部：**一件本地还没结算、外面却已经 ``done`` 或
           ``needs-human`` 的活，正是「外面比本地靠前」那类漂移**，它在
           ``converge_settled`` 的视野里根本不存在（那一类被算进 ``unsettled``
           的一个计数里，看不出是哪一件）。

        它不抛：逐件的失败进 ``BoardRead.error``（理由同 ``converge_settled`` ——
        外面读不成的失败不改变本地任何事实）。作业不存在抛 ``JobStateError``，
        与那两个入口一致。

        Raises:
            JobStateError: 作业不存在。
        """
        if self._jobs.get(job_id) is None:
            raise JobStateError(
                f"没有这个作业：{job_id!r}。看板读数的主语是一个已经受理的作业；"
                "作业由 ``JobStore.admit`` 产生。"
            )
        rows: list[tuple[WorkItem, BoardRead]] = []
        for item in self._jobs.work_items(job_id):
            ref = self._jobs.execution_ref(item.work_item_id)
            if ref is None:
                continue
            rows.append((item, self._observe(ref, job_id)))
        return BoardObservation(job_id=job_id, rows=tuple(rows))

    # ------------------------------------------------------------------
    @staticmethod
    def _unit(job: ResearchJob, item: WorkItem) -> ExecutionUnit:
        """本地的一对记录 → 一件要交出去的事。

        标题是 ``"<scene>: <subject>"``：外部那张表里**只有标题是给人读的**，
        而 ``work_item_id`` / ``job_id`` 都是 128 位十六进制。一个只写 id 的标题
        等于让人为了知道「这是哪件事」去查一次本仓。

        标题**不是身份**：身份是 ``external_id``，它由实现从 ``unit`` 的 id 派生。
        所以标题写错不会造出第二个任务，只会让那张表里的一行读起来不对 —— 代价
        是可见的，不是静默的。（反面是外部的 ``create`` 是个 upsert，它会把已有
        任务的标题**改掉**，见 M5；所以「拿标题去认一件事」在这条边界上是错的，
        认事情靠 label。）

        正文由 ``_TASK_BODY`` 渲染（见那条常量的 docstring：它说的是什么、以及
        为什么指引一个字都不在里面），三个身份分别填进去。

        正文里那一节 ``## Touches`` 写的是 ``tasks/<外部 id>.md``，那个 id 由
        :func:`external_task_id` 算出来 —— **就是适配器建任务时用的同一个函数**。
        所以正文里那个文件名与外面真的建出来的那个不可能分家；换句话说，那一条
        声明是**算出来的**，不是抄来的，也不是一个可以忘记同步的副本。
        """
        if not job.scene_id or not item.subject_ref:
            raise ValueError(
                f"派发需要非空的 scene_id 与 subject_ref（收到 {job.scene_id!r} / "
                f"{item.subject_ref!r}）。拼出来的标题会是一句读起来正常、"
                "但认不出是谁的话。"
            )
        return ExecutionUnit(
            unit_id=item.work_item_id,
            job_id=job.job_id,
            title=f"{job.scene_id}: {item.subject_ref}",
            body=_TASK_BODY.format(
                scene_id=job.scene_id,
                job_id=job.job_id,
                work_item_id=item.work_item_id,
                subject_ref=item.subject_ref,
                task_id=external_task_id(job.job_id, item.work_item_id),
            ),
        )


def _summarize(tasks: tuple[ExternalTask, ...]) -> str:
    """错误消息里的若干条外部任务。**带总数**，所以它不假装自己列全了。"""
    shown = ", ".join(sorted(task.external_id for task in tasks)[:_MAX_IDS_IN_MESSAGE])
    rest = len(tasks) - _MAX_IDS_IN_MESSAGE
    return f"{shown}{f'，另有 {rest} 条' if rest > 0 else ''}"


def _duplicate_tasks_message(
    work_item_id: str, found: tuple[ExternalTask, ...], labels: tuple[str, ...]
) -> str:
    """同一组 label 下不止一条任务时的那一句话。**一处写**，两个入口都报它。

    ``dispatch`` 把它抛成 ``ExternalStateError``，``reconcile`` 把它放进
    ``BoardSync.error``。同一件事在两处各写一句，漂移的那天读的人会以为这是两种
    不同的发现 —— 而它们的处置是同一个：**停下报告**，不是自动收敛。

    两个入口都会撞上这一格，因为读的都是同一组 label（``link_labels`` 是唯一
    生产者）。派发时撞上说明这件事被交出去过两遍；回传时撞上说明它被交出去过
    两遍**并且**我们已经结算了其中一遍 —— 后者更坏，那笔账已经付了。
    """
    return (
        f"work item {work_item_id} 在外部有 {len(found)} 个任务带着同一组 "
        f"label（{', '.join(labels)}）—— 本层不挑一条来认领。\n"
        f"它们是：{_summarize(found)}\n"
        "挑一条等于把「同一件事被派发了两遍」这个事实扔掉（§8B 逐字禁止 "
        "duplicate expensive work），而两边都会正常跑完、账单付两遍。"
        "处置是**停下报告**：先看清哪一条是真的，再决定交出去的那一条怎么收回。"
    )
