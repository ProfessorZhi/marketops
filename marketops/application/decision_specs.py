"""决策题面的**生产者**：把 profile 声明的题面编译成 ``DecisionSpec``。

``ports.decisions`` 要求题面由**服务端**持有，而本模块是那个「服务端」这一侧唯一
的构造点。调用方拿到的是编译好的 spec，没有「自己写一道题」的入口 —— 少一个入口
就少一处可以把题面改成「后端一定答得上来」的地方。

## 三条 task 各自的**闭集**在哪

一道题的选项不是随便列几个词，它是**这个 profile 在一个维度上的全部取值**。
三个 task 的闭集来源各不相同，而这是它们最值得写下来的差异：

==============================  ==============================================
``PAGE_TRIAGE``                 ``profile.page_discovery_hints`` 的档位 ——
                                「先抓哪些页面」的分级，声明在 profile.yaml。
``RESPONSIBILITY_RELATION``     ``VenueRelation`` 里**可判**的五个成员（SOP L27
                                逐字：拥有 / 运营 / 维护 / 预约控制 / 采购）。
                                它**与 Scene 无关** —— 换一个产品场景，责任关系
                                还是这五种，所以闭集在 domain 层而不是 profile 里。
                                第六个成员 ``unresolved`` **不在这道题里**，理由见
                                下一节。
``ROLE_FUNCTION``               该 profile 的 role-matrix **实际用到的**
                                ``business_function``。换一份 role-matrix 就换
                                一批取值，所以它只能从 ``ProfileBundle`` 上读。
==============================  ==============================================

## ``unresolved`` 为什么不做成一个选项

``VenueRelation`` 有六个成员，而这道题只出五个。剩下的那个不是「漏了」：

``unresolved`` 说的是**一条已经存下来的关系行**「知道两者有关系、但关系还没读出来」，
它是一行数据的**状态**，不是一页公开文字能被判成的一个**类别**。放进题面会有两个
后果，第二个更糟：

1. 后端多了一个「我不想答」的出口，而引擎**已经有**一个：``DecisionOutcome.ABSTAINED``
   （"后端能跑，但对这一份输入拒绝作答"）。两个出口说同一件事，就会有一处先漂移 ——
   而漂移的那一天，同一份输入在两个后端上会得到「弃权」与「答了 unresolved」两种记录，
   报告上看起来都像正常回答。
2. 一个页面上写着「由 X 维护」，后端答 ``unresolved`` 是**答错了**，而它看起来是一个
   合法选项、一条正常记录。题目给的五个类别是**互斥且穷尽**的那种「这一页在说什么」
   —— 少一个成员，回答就必须是其中之一或弃权。

这条有执行器：``tests/test_decision_specs.py`` 断言题面恰好五个选项、``unresolved``
不在其中。

## 对齐是一条**执行器**，不是一次检查

profile 为每道题声明了每个选项给后端的那句话（``decisions`` 块）。本模块在编译时
断言「声明的 key 集 == 闭集」—— 而这条断言的作用**不是**防手滑。

role-matrix 里加一个 ``business_function: estates`` 的角色组，而 ``decisions``
块里没有 ``estates`` 这一项 —— 后端就只能在闭集外作答，或者更糟：照旧返回闭集内的
一个取值，于是**每条 estates 岗位都被判成了别的东西**，而记录上看不出来。装载期
失败把这件事变成一次立刻可见的红。

## 题面里不得出现匹配规则

labels 里刻意没有 role-matrix 的 ``match`` 词，也没有 ``page_discovery_hints``
的 URL glob。理由不是洁癖：这道题要量的是「后端能不能做出规则做不到的判断」，
把规则抄进题面等于让它复述规则，而**规则做不到的那部分恰好是这一族样本的全部
意义**。这条有执行器（``tests/test_decision_specs.py``）。

## 版本是内容寻址的

``DecisionSpec.version`` 由题面自己算出来（题面文本 + 选项 key 与描述，按题面
顺序）。改一个字 —— 包括改选项的**顺序** —— 版本就变。这不是洁癖：``version``
进 ``DecisionCase.input_sha256``，而两条有着同一个版本的记录会被读成「同一件事的
两次观测」。题面改过而版本没变，那次不一致就会被归因到后端头上。
"""

from __future__ import annotations

import json

from ..domain.enums import DecisionTask, QuestionKind, VenueRelation
from ..domain.errors import ConfigError
from ..domain.profile import DeclaredTaskSpec, ProfileBundle
from ..domain.util import sha256_text
from ..ports.decisions import DecisionOption, DecisionQuestion, DecisionSpec

__all__ = [
    "CLASSIFIABLE_RELATIONS",
    "QUESTION_IDS",
    "compile_spec",
    "compile_specs",
    "closed_choices",
]

#: 责任关系这道题**出得出来**的那五个取值。**只在这里定义一次** ——
#: ``closed_choices`` 与它自己的那条测试都读它，各写一份的话，
#: 「题面恰好五个」这条断言会在改了一处之后继续绿。
CLASSIFIABLE_RELATIONS: tuple[VenueRelation, ...] = (
    VenueRelation.OWNS,
    VenueRelation.OPERATES,
    VenueRelation.MAINTAINS,
    VenueRelation.BOOKING_CONTROL,
    VenueRelation.PROCURES,
)

#: 每个 task 的 ``question_id``。**结构，不是内容** —— 所以它住在这里，不住
#: profile：它随 task 而定，换成哪一份 profile 都是同一个名字。回执按它对齐，
#: 比较报告也按它对齐，一个会跟着 profile 漂移的名字会让跨 profile 的对比失准。
QUESTION_IDS: dict[DecisionTask, str] = {
    DecisionTask.PAGE_TRIAGE: "band",
    DecisionTask.RESPONSIBILITY_RELATION: "relation",
    DecisionTask.ROLE_FUNCTION: "function",
}

#: ``version`` 取摘要的前多少位。与 ``ports.decisions`` 的 ``record_id`` 同为 32：
#: 它不是安全边界，是一份能印在报告上的短标注，而两处取不同长度只会让人以为
#: 其中一个更严。
_VERSION_LENGTH = 32


def closed_choices(bundle: ProfileBundle, task: DecisionTask) -> tuple[str, ...]:
    """这个 profile 在这个 task 上的**全部合法取值**，按稳定顺序。

    顺序是题面的一部分（它进 ``as_questions`` 也进后端的渲染），所以它必须是
    **确定的**：声明顺序，而不是集合的迭代顺序。同一个 bundle 编译两次得到逐字
    相同的题面 —— 这条由 ``test_compiling_twice_is_byte_identical`` 守住。
    """
    if task is DecisionTask.PAGE_TRIAGE:
        declared = bundle.profile.page_discovery_hints if bundle.profile else {}
        return tuple(declared)
    if task is DecisionTask.RESPONSIBILITY_RELATION:
        # 只出**可判**的那五个，见模块 docstring 的「``unresolved`` 为什么不做成
        # 一个选项」：它是关系行的状态，不是一页文字能判成的类别，而「我不想答」
        # 已经由 ``DecisionOutcome.ABSTAINED`` 表达。
        return tuple(item.value for item in CLASSIFIABLE_RELATIONS)
    # ROLE_FUNCTION：**该 profile 实际用到的**口径，保序去重。用
    # ``values()`` 而不是枚举全体是关键 —— 枚举里有 15 个成员，而这份
    # role-matrix 只触达其中一部分，多出来的取值会让后端有机会选一个这个
    # Scene 根本不认识的口径，而它看起来仍是一条合法的回答。
    return tuple(dict.fromkeys(item.value for item in bundle.business_functions.values()))


def compile_spec(bundle: ProfileBundle, task: DecisionTask) -> DecisionSpec:
    """编译一个 task 的题面。

    Raises:
        ConfigError: 这个 profile 没声明该 task；闭集为空；或者声明的选项集与
            闭集不等（两种情况分别说清是多声明了还是少声明了 —— 一份「选项集
            不对」的报错如果不说清差在哪，读的人只能去把两份清单人工比一遍）。
    """
    declared = _declared_task(bundle, task)
    closed = closed_choices(bundle, task)
    if not closed:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 在 {task.value} 上没有可用的取值 —— "
            "一道没有选项的选择题会让每一次判定都答同一项，而报告上看起来很正常"
        )

    missing = [key for key in closed if key not in declared.labels]
    extra = [key for key in declared.labels if key not in closed]
    if missing or extra:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 的 decisions.{task.value} 与它自己的取值集"
            f"对不上。\n  闭集里没有声明描述的：{missing}\n  声明了但闭集里没有的：{extra}\n"
            "  闭集来自 profile 自己的声明（页面档位 / 责任关系 / role-matrix 的"
            "business_function）。少一条描述，判定就会答出一个闭集外的取值；多一条"
            "描述，是一个永远不会被选中的选项 —— 而两者在记录上都看不出来。"
        )

    question = DecisionQuestion(
        question_id=QUESTION_IDS[task],
        kind=QuestionKind.CHOICE,
        instructions=declared.instructions,
        # 按**闭集**的顺序取描述，不按声明的顺序：题面的选项顺序必须由闭集决定，
        # 否则 profile 里调整一下 YAML 的书写顺序就会换掉题面，而版本号会跟着变
        # —— 那次 change 看起来像「题面改过」，其实只是键的顺序换了。
        options=tuple(
            DecisionOption(key=key, description=declared.labels[key]) for key in closed
        ),
    )
    spec_id = _spec_id(bundle, task)
    return DecisionSpec(
        spec_id=spec_id,
        version=_version(spec_id, question),
        task=task,
        questions=(question,),
    )


def compile_specs(bundle: ProfileBundle) -> dict[DecisionTask, DecisionSpec]:
    """这个 profile 声明过的**全部** task，按它的声明顺序。

    没声明的 task **不出现在结果里** —— 调用方拿不到一个占位 spec。要问「某个 task
    能不能做」，答案在 ``bundle.decisions`` 里，而 ``compile_spec`` 对没声明的
    task 显式抛错。
    """
    declared = bundle.decisions
    if declared is None:
        return {}
    return {task: compile_spec(bundle, task) for task in declared.tasks}


# --- 内部 -------------------------------------------------------------------


def _declared_task(bundle: ProfileBundle, task: DecisionTask) -> DeclaredTaskSpec:
    declared = bundle.decisions
    if declared is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 的 profile.yaml 没有 decisions 块 —— "
            "题面是声明，不是从别处推出来的（见 domain/profile.py 的 DeclaredTaskSpec）"
        )
    spec = declared.tasks.get(task)
    if spec is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明 {task.value} 的题面。"
            f"已声明的：{sorted(item.value for item in declared.tasks)}"
        )
    return spec


def _spec_id(bundle: ProfileBundle, task: DecisionTask) -> str:
    """``<profile>:<task>`` —— 身份，不含内容。

    内容进 ``version``。两者分开，是因为它们回答不同的问题：``spec_id`` 说
    「这是哪一件判定」，``version`` 说「用的是哪一版题面」。
    """
    return f"{bundle.profile_id}:{task.value}"


def _version(spec_id: str, question: DecisionQuestion) -> str:
    """题面的内容指纹。

    序列化用 ``sort_keys=True`` 只定**映射内**的键序，题目与选项的**列表**顺序
    原样保留 —— 那不是噪音，它就是题面的一部分（后端按它渲染）。
    """
    payload = {
        "spec_id": spec_id,
        "question_id": question.question_id,
        "kind": question.kind.value,
        "instructions": question.instructions,
        "options": [[option.key, option.description] for option in question.options],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_text(canonical)[:_VERSION_LENGTH]
