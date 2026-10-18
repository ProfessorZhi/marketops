"""决策题面：编译、闭集对齐，以及**题面里不许有匹配规则**。

``ports.decisions`` 要求题面由服务端持有，本文件测的是那个「服务端」这一侧唯一的
构造点（``application/decision_specs``）。三组判据，每一组都对应一处**会静默出错**
的地方：

1. **闭集对齐。** 一道题的选项必须逐字等于 profile 在那个维度上的全部取值。不等时
   必须红 —— 少一个描述，后端会答出一个闭集外的取值；多一个描述，是一个永远不会
   被选中的选项。**两者在记录上都看不出来。**
2. **题面是内容寻址的。** 题面改一个字（包括换掉选项的顺序），``version`` 必须跟着
   变。不变的那一次，两条记录会被读成「同一件事的两次观测」，而分歧会被归因到后端。
3. **描述里没有匹配规则。** 见下面 ``_foreign_terms`` / ``_glob_leaks`` 两段。

## 这一族判据里最要紧的一条是对齐的**执行器**

``role-matrix`` 里新增一个角色组、它的 ``business_function`` 不在题面里 ——
那时后端只能在闭集外作答，或者更糟：照旧返回闭集内的某一个，于是**这一类岗位
全部被判成了别的东西**。这条不是靠自觉守的：``test_a_new_business_function_...``
把那种改法造出来，然后要求编译失败。
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from marketops.application.decision_specs import (
    QUESTION_IDS,
    closed_choices,
    compile_spec,
    compile_specs,
)
from marketops.domain.enums import (
    BusinessFunction,
    DecisionTask,
    QuestionKind,
    VenueRelation,
)
from marketops.domain.errors import ConfigError
from marketops.domain.profile import (
    DeclaredDecisions,
    DeclaredTaskSpec,
    ProfileBundle,
)
from marketops.ports.decisions import DecisionCase, DecisionSpec

#: §9 Experiment 3 逐字点名的三族冻结样本。写死在这里，而不是从
#: ``DecisionTask`` 抄 —— 抄一遍的话，枚举少一个成员这条判据也跟着少一条，
#: 而那正是它要发现的事。**判据的锚必须是外部事实，不是被判对象的回声。**
_SECTION_9_TASKS = ("page_triage", "responsibility_relation", "role_function")


def _task(name: str) -> DecisionTask:
    return DecisionTask(name)


def _spec(bundle: ProfileBundle, name: str) -> DecisionSpec:
    return compile_spec(bundle, _task(name))


def _options(spec: DecisionSpec) -> dict[str, str]:
    question = spec.questions[0]
    return {option.key: option.description for option in question.options}


# --- 题面里不许有匹配规则 ---------------------------------------------------


def _normalized(text: str) -> str:
    """大小写、空白与标点归一化，两边各补一个空格。

    **标点必须也归一化**：role-matrix 的 ``normalization`` 就是那么写的
    （``strip_punctuation``），而一句 "…and also accounts." 里的 ``accounts``
    在补空格的写法下会漏掉 —— 句号把它粘在了词上。一个会漏掉句末词的扫描器，
    漏掉的恰好是「描述里列举词表」那种写法的最后一项。
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() + " "


def _options_by_function(bundle: ProfileBundle) -> dict[str, set[str]]:
    """``business_function`` → 归到它名下的 match 词。"""
    grouped: dict[str, set[str]] = {}
    for term, function in bundle.business_functions.items():
        grouped.setdefault(function.value, set()).add(term)
    return grouped


def _foreign_terms(label: str, own: set[str], all_terms: set[str]) -> list[str]:
    """``label`` 里出现的、**不属于 ``own``** 的 match 词。

    这是这一族里真正可判的那条线。一个选项的描述当然可以用它**自己**的名字来
    定义自己 —— ``customer_service`` 那一项说 "customer service"，那不是抄规则，
    那是在给出这个口径。而一个选项的描述里出现**别人**的词，就是在把一条错的
    映射当成定义交给后端：看到那个词的后端会去选另一个选项，而那次判定在记录上
    与一条正确的判定没有区别。
    """
    haystack = _normalized(label)
    return sorted(
        term
        for term in all_terms - own
        if term and _normalized(term) in haystack
    )


def _glob_leaks(label: str, globs: tuple[str, ...]) -> list[str]:
    """``label`` 里逐字出现的 URL glob，外加任何 ``*``。

    ``*`` 单独判，是因为本 profile 的 glob 全都带它 —— 于是这条判据今天能抓住
    的只有「整条 glob 被抄了进去」这一种。写下 ``*`` 那一半是为了它**以后**还有
    分辨力：一份没有通配符的 glob（``contact``）被改进来时，前一半会抓不住，
    而那时候把规则抄进题面的写法看起来仍然完全正常。
    """
    leaks = [glob for glob in globs if glob and glob in label]
    if "*" in label:
        leaks.append("*")
    return leaks


def test_a_description_never_defines_a_different_option(bundle: ProfileBundle) -> None:
    """一条描述只准定义它自己那一项。

    正对照在下面两处：词表非空（否则这条判据是空言），以及扫描器确实会红。
    """
    grouped = _options_by_function(bundle)
    all_terms = {term for terms in grouped.values() for term in terms}
    assert all_terms, "前提：这份 profile 的角色词表非空，否则这条判据扫的是空集"

    offenders: dict[str, list[str]] = {}
    for task in DecisionTask:
        for key, label in _options(_spec(bundle, task.value)).items():
            foreign = _foreign_terms(label, grouped.get(key, set()), all_terms)
            if foreign:
                offenders[f"{task.value}.{key}"] = foreign
    assert not offenders, (
        f"这些选项的描述里出现了**别的**选项的 match 词：{offenders}\n"
        "  描述是给后端读的定义。定义里出现别人的词，等于把一条错的映射当定义交出去："
        "看到那个词的后端会去选另一个选项，而那条判定在记录上与一条正确的判定无异。"
    )


def test_the_foreign_term_scanner_actually_fires(bundle: ProfileBundle) -> None:
    """扫描器的正对照 —— 它得**真的会红**。

    ``_foreign_terms`` 是上面那条判据的全部执行器。一个恒返回空列表的实现会让
    那条判据永远绿，而它绿的原因与题面无关。
    """
    grouped = _options_by_function(bundle)
    all_terms = {term for terms in grouped.values() for term in terms}
    # 挑一个**属于别的口径**的词，塞进 grounds 的描述里。
    foreign_word = sorted(grouped["finance"])[0]
    caught = _foreign_terms(
        f"Grounds people, and also {foreign_word}.",
        grouped.get("grounds", set()),
        all_terms,
    )
    assert caught, f"扫描器没有抓住故意塞进去的 {foreign_word!r} —— 它是空转的"

    # 另一面：用自己的词不算违规，否则这条判据会逼着题面不用任何业务词。
    own = sorted(grouped["finance"])[0]
    assert not _foreign_terms(f"Concerns {own}.", grouped["finance"], all_terms)


def test_a_description_never_restates_the_url_glob_table(bundle: ProfileBundle) -> None:
    """页面档位的三句说的是那条性质，不是 ``page_discovery_hints`` 的 glob 表。"""
    assert bundle.profile is not None
    globs = tuple(
        glob
        for group in bundle.profile.page_discovery_hints.values()
        for glob in group
    )
    assert globs, "前提：这份 profile 声明了页面优先级表"
    assert any("*" in glob for glob in globs), (
        "前提：glob 带通配符 —— 否则「描述里没有 *」这条判据是空转的"
    )

    spec = _spec(bundle, "page_triage")
    leaks = {key: _glob_leaks(label, globs) for key, label in _options(spec).items()}
    assert not any(leaks.values()), (
        f"page_triage 的描述里出现了 URL glob 或通配符：{leaks}\n"
        "  给出 glob 表等于让后端做一次精确匹配 —— 而那件事 "
        "application/sitemap_frontier.py 已经在做。这条题要量的是它做不到的那部分。"
    )

    # 正对照：把 glob 逐字抄进一句描述，扫描器必须抓住它。
    # （它同时还会报出 ``*/`` 与 ``*`` —— 那两条也是这份表里的 glob，而 ``*/``
    # 恰好是 ``*/contact*`` 的子串。判据要的是「抓住了」，不是「只抓住一条」。）
    assert globs[0] in _glob_leaks(f"Pages like {globs[0]} matter.", globs)
    assert _glob_leaks("Contact and staff pages.", globs) == []


# --- 闭集对齐 ---------------------------------------------------------------


def test_the_section_9_samples_all_have_a_spec(bundle: ProfileBundle) -> None:
    """§9 点名的三族都要有题面 —— 少一族意味着那一族没有任何生产者。"""
    compiled = sorted(spec.task.value for spec in compile_specs(bundle).values())
    assert compiled == sorted(_SECTION_9_TASKS), (
        f"profile 声明了 {compiled}，而 §9 Experiment 3 点名的是 "
        f"{sorted(_SECTION_9_TASKS)}。\n"
        "  少一族不是「以后再说」：那一族的样本会被冻结下来，却没有任何代码能"
        "把它们编译成一道题，而报告上看起来像「还没跑」。"
    )


def _declared_open_set(bundle: ProfileBundle, task: DecisionTask) -> tuple[str, ...]:
    """闭集**按声明本身**数一遍，字段来源与 ``closed_choices`` 不同。

    这个函数是上面那条判据的关键：如果期望值也从 ``closed_choices`` 取，那条判据
    就问「实现自己觉得答案是什么」——两边一起动，永远相等。F1 里已经栽过一次同样
    的跟头（laya 的替身回显了我们自己的常量，于是「英文走本地 checkpoint」那条判据
    变成一句自洽的废话）。

    所以这里走**另一条路**：page 档位读 ``page_discovery_hints`` 的声明键序，
    business function 读 role-matrix 的**角色组声明顺序**（不是编译后的词表，
    那条路是 ``bundle.business_functions``）。
    """
    if task is DecisionTask.PAGE_TRIAGE:
        assert bundle.profile is not None
        return tuple(bundle.profile.page_discovery_hints)
    if task is DecisionTask.RESPONSIBILITY_RELATION:
        # 五个**可判**的关系。``unresolved`` 不在其中 —— 它是关系行的状态，不是
        # 一页公开文字能判成的类别（理由写在 ``application/decision_specs`` 的
        # 模块 docstring 里）。这里用**排除法**写，实现那一侧用的是白名单：
        # 两种写法都写对才相等。
        return tuple(
            member.value
            for member in VenueRelation
            if member is not VenueRelation.UNRESOLVED
        )
    assert bundle.role_matrix is not None
    return tuple(
        dict.fromkeys(
            group.business_function.value
            for group in (*bundle.role_matrix.groups, *bundle.role_matrix.excluded_groups)
        )
    )


@pytest.mark.parametrize("name", _SECTION_9_TASKS)
def test_option_keys_are_exactly_the_profiles_closed_set(
    bundle: ProfileBundle, name: str
) -> None:
    """选项 key 逐字等于闭集 —— 不多、不少、不换顺序。

    期望值走 ``_declared_open_set``（声明那一侧），实际值走出被测的函数。
    """
    task = _task(name)
    expected = _declared_open_set(bundle, task)
    assert expected, f"前提：{name} 在这份 profile 上的声明非空"
    assert tuple(_options(_spec(bundle, name))) == expected
    # 同一条事实的另一半：被测函数自己也得同意「闭集是这些」。
    assert closed_choices(bundle, task) == expected


def test_role_function_closes_over_the_role_matrix_not_the_enum(
    bundle: ProfileBundle,
) -> None:
    """ROLE_FUNCTION 的闭集是**这份** role-matrix 用到的口径，不是枚举全体。"""
    closed = set(closed_choices(bundle, DecisionTask.ROLE_FUNCTION))
    used = {function.value for function in bundle.business_functions.values()}
    assert closed == used

    unused = {member.value for member in BusinessFunction} - used
    assert unused, (
        "前提：枚举里确实有这份 role-matrix 没有触达的口径 —— 否则「用 values() "
        "而不是枚举全体」这件事无从分辨"
    )
    assert not (closed & unused), (
        f"闭集里出现了这份 profile 没有的角色组所定义的口径：{sorted(closed & unused)}。\n"
        "  多出来的取值让后端有机会选一个这个 Scene 根本不认识的口径，"
        "而它看起来仍是一条合法的回答。"
    )


def test_responsibility_relation_closes_over_the_domain_enum(
    bundle: ProfileBundle,
) -> None:
    """责任关系与 Scene 无关 —— 换一个产品场景，还是那五种（SOP L27）。

    而枚举里**有**第六个成员 ``unresolved``，它不在这道题里。这不是漏掉：
    它说的是「一条已经存下来的关系行知道有关系、但关系还没读出来」，是一行数据的
    **状态**，不是一页公开文字能被判成的**类别**。本题的五个类别互斥且穷尽 ——
    「这一页没说是哪种关系」的正确答案是后端弃权（``DecisionOutcome.ABSTAINED``），
    而多给一个出口，同一天两个后端就会给出两种看起来都正常的记录。
    """
    closed = closed_choices(bundle, DecisionTask.RESPONSIBILITY_RELATION)
    assert closed == tuple(
        member.value for member in VenueRelation if member is not VenueRelation.UNRESOLVED
    )
    assert VenueRelation.UNRESOLVED.value not in closed
    assert VenueRelation.UNRESOLVED.value in {member.value for member in VenueRelation}, (
        "前提：枚举里确实有 ``unresolved`` —— 它不在了的话，上面那条判据"
        "测的就不是「排除」这件事，而是一个空条件"
    )


def test_a_new_business_function_without_a_description_fails_to_compile(
    bundle: ProfileBundle,
) -> None:
    """**对齐的执行器。**

    role-matrix 里长出一个新的 ``business_function`` 而题面没跟上 —— 这是这条
    判据存在的全部理由，所以它必须由「加一个角色组」那种改法来触发，不是由
    「monkeypatch 掉返回值」来触发。
    """
    grown = {**bundle.business_functions, "estates lead": BusinessFunction.ESTATES}
    poisoned = dataclasses.replace(bundle, business_functions=grown)
    assert BusinessFunction.ESTATES.value not in {
        function.value for function in bundle.business_functions.values()
    }, "前提：这份 role-matrix 今天确实没有触达 estates"

    with pytest.raises(ConfigError) as caught:
        compile_spec(poisoned, DecisionTask.ROLE_FUNCTION)
    message = str(caught.value)
    assert BusinessFunction.ESTATES.value in message, (
        f"报错没说清差在哪一个取值上：{message}"
    )
    assert "闭集里没有声明描述的" in message


def test_a_description_for_an_option_that_does_not_exist_fails_to_compile(
    bundle: ProfileBundle,
) -> None:
    """反方向：声明了一个闭集里没有的选项。

    它比缺一条描述更安静 —— 一个永远不会被选中的选项不会让任何一次判定出错，
    只会让这道题的题面比它声称的宽。
    """
    assert bundle.decisions is not None
    role_key = DecisionTask.ROLE_FUNCTION
    current = bundle.decisions.tasks[role_key]
    widened = dataclasses.replace(
        bundle,
        decisions=DeclaredDecisions(
            tasks={
                **bundle.decisions.tasks,
                role_key: DeclaredTaskSpec(
                    instructions=current.instructions,
                    labels={**current.labels, "estates": "Estates and property."},
                ),
            }
        ),
    )
    with pytest.raises(ConfigError) as caught:
        compile_spec(widened, role_key)
    assert "声明了但闭集里没有的" in str(caught.value)
    assert "estates" in str(caught.value)


def test_an_empty_closed_set_fails_closed(bundle: ProfileBundle) -> None:
    """闭集为空时拒绝，而不是编一道没有选项的题。

    两种空法都要走到：profile 那一侧没有页面档位，role-matrix 那一侧一个角色组
    都没有。它们走的是同一个拒绝点，而理由不同。
    """
    with pytest.raises(ConfigError, match="没有可用的取值"):
        compile_spec(
            dataclasses.replace(bundle, profile=None), DecisionTask.PAGE_TRIAGE
        )
    with pytest.raises(ConfigError, match="没有可用的取值"):
        compile_spec(
            dataclasses.replace(bundle, business_functions={}),
            DecisionTask.ROLE_FUNCTION,
        )


def test_a_task_that_was_never_declared_fails_closed(bundle: ProfileBundle) -> None:
    """没声明的 task 不给占位题面。"""
    assert bundle.decisions is not None
    one_task = dataclasses.replace(
        bundle,
        decisions=DeclaredDecisions(
            tasks={DecisionTask.PAGE_TRIAGE: bundle.decisions.tasks[DecisionTask.PAGE_TRIAGE]}
        ),
    )
    assert sorted(spec.task.value for spec in compile_specs(one_task).values()) == [
        "page_triage"
    ]
    with pytest.raises(ConfigError) as caught:
        compile_spec(one_task, DecisionTask.ROLE_FUNCTION)
    assert "没有声明" in str(caught.value)
    assert "page_triage" in str(caught.value), "报错要列出已声明的那些"


def test_a_profile_without_a_decisions_block_fails_closed(bundle: ProfileBundle) -> None:
    """整块缺失时 ``compile_specs`` 返回空，而不是一份空的题面。"""
    without = dataclasses.replace(bundle, decisions=None)
    assert compile_specs(without) == {}
    with pytest.raises(ConfigError, match="没有 decisions 块"):
        compile_spec(without, DecisionTask.PAGE_TRIAGE)


# --- 版本是内容寻址的 -------------------------------------------------------


def test_compiling_twice_is_byte_identical(bundle: ProfileBundle) -> None:
    """正对照：不改任何东西时版本必须**一样**。

    没有这一条，下面每一条「改了所以要变」都可以由一个随机版本号满足。
    """
    first, second = compile_specs(bundle), compile_specs(bundle)
    assert {task: spec.version for task, spec in first.items()} == {
        task: spec.version for task, spec in second.items()
    }
    assert json.dumps(
        {task.value: spec.as_questions() for task, spec in first.items()}, sort_keys=True
    ) == json.dumps(
        {task.value: spec.as_questions() for task, spec in second.items()}, sort_keys=True
    )


@pytest.mark.parametrize("change", ["instructions", "label"])
def test_a_changed_spec_changes_its_version(
    bundle: ProfileBundle, change: str
) -> None:
    """题面改了，``version`` 必须跟着改 —— 它是「这次不一致是不是因为我改了题面」
    的唯一依据（``version`` 进 ``DecisionCase.input_sha256``）。
    """
    assert bundle.decisions is not None
    task = DecisionTask.ROLE_FUNCTION
    current = bundle.decisions.tasks[task]

    if change == "instructions":
        changed = DeclaredTaskSpec(
            instructions=current.instructions + " Answer in one word.",
            labels=dict(current.labels),
        )
    else:
        first = sorted(current.labels)[0]
        changed = DeclaredTaskSpec(
            instructions=current.instructions,
            labels={**current.labels, first: current.labels[first] + " (see above)"},
        )

    mutated = dataclasses.replace(
        bundle,
        decisions=DeclaredDecisions(tasks={**bundle.decisions.tasks, task: changed}),
    )
    before, after = compile_spec(bundle, task), compile_spec(mutated, task)
    assert after.version != before.version, f"改法 {change!r} 没有换掉版本号"
    assert after.spec_id == before.spec_id, (
        "spec_id 是身份，不该跟着内容走 —— 两者合一之后，"
        "「换了题面」与「换了一件事」在台账上没有区别"
    )


def test_the_version_follows_the_closed_set_order_not_the_yaml_key_order(
    bundle: ProfileBundle,
) -> None:
    """两个方向都要判，因为它们是两件不同的事。

    * 把 ``decisions`` 块里的键**换一个书写顺序** —— 题面必须**逐字不变**。选项
      顺序由闭集决定，不是由作者在 YAML 里的排版决定；否则一次纯排版改动会让
      版本号变，而那次变化看起来像「题面改过」。
    * 把 role-matrix 的**角色组顺序**换一下 —— 题面**必须变**。闭集的顺序就是
      通道的顺序（后端的渲染按它排），而顺序确实是题面的一部分。
    """
    assert bundle.decisions is not None
    task = DecisionTask.ROLE_FUNCTION
    current = bundle.decisions.tasks[task]

    reordered_yaml = dataclasses.replace(
        bundle,
        decisions=DeclaredDecisions(
            tasks={
                task: DeclaredTaskSpec(
                    instructions=current.instructions,
                    labels=dict(reversed(list(current.labels.items()))),
                ),
                **{key: value for key, value in bundle.decisions.tasks.items() if key is not task},
            }
        ),
    )
    assert compile_spec(reordered_yaml, task).version == compile_spec(bundle, task).version, (
        "换一下 YAML 里的书写顺序就换掉了版本号 —— 选项顺序该由闭集决定，"
        "否则一次纯排版改动会在台账上读成「题面改过」"
    )

    reordered_matrix = dataclasses.replace(
        bundle,
        business_functions=dict(reversed(list(bundle.business_functions.items()))),
    )
    assert closed_choices(reordered_matrix, task) != closed_choices(bundle, task), (
        "前提：角色组的顺序确实决定闭集的顺序（一个角色组的词先出现，它的口径就先"
        "进闭集）。两者一样的话，下面那条判据什么也没测"
    )
    assert compile_spec(reordered_matrix, task).version != compile_spec(bundle, task).version, (
        "闭集的顺序换了而版本号没换 —— 而选项顺序是两个后端都会渲染进题面的东西"
    )


def test_two_tasks_never_share_an_id(bundle: ProfileBundle) -> None:
    """三条 task 的 ``spec_id`` 与 ``question_id`` 都不同，且 id 里带着 profile。"""
    specs = compile_specs(bundle)
    assert len({spec.spec_id for spec in specs.values()}) == len(specs)
    assert len({spec.questions[0].question_id for spec in specs.values()}) == len(specs)
    assert {task: spec.questions[0].question_id for task, spec in specs.items()} == {
        task: QUESTION_IDS[task] for task in specs
    }
    # 带上 profile_id 不是装饰：台账里要能回答「这条记录判的是**哪个场景**的哪一件
    # 事」。只写 task 的话，两个 profile 的 role_function 判定会同名 —— 而集群里
    # 同时存在几个 profile 目录是常态。
    assert all(spec.spec_id.startswith(f"{bundle.profile_id}:") for spec in specs.values())


def test_a_spec_is_usable_as_a_case(bundle: ProfileBundle) -> None:
    """编译出来的东西真的能当题面用 —— 端口的 ``__post_init__`` 逐条通过。

    这是这一族判据里唯一一条**不看实现**的：它让 ``DecisionSpec`` 与
    ``DecisionQuestion`` 自己去拒绝一个写坏了的题面。
    """
    for task, spec in compile_specs(bundle).items():
        question = spec.questions[0]
        assert question.kind is QuestionKind.CHOICE
        assert len(question.options) >= 2
        assert all(option.description.strip() for option in question.options)

        payload = spec.as_questions()
        assert set(payload) == {question.question_id}
        assert payload[question.question_id]["type"] == "choice"
        assert set(payload[question.question_id]["criteria"]) == set(  # type: ignore[arg-type]
            spec.choices_by_question[question.question_id]
        )

        case = DecisionCase(
            case_id=f"sample-{task.value}",
            spec=spec,
            state="A sample of frozen text.",
        )
        # 输入指纹随题面版本变，所以它对同一份题面是稳定的。
        assert case.input_sha256 == DecisionCase(
            case_id="another-id", spec=spec, state="A sample of frozen text."
        ).input_sha256


def test_a_spec_carries_no_backend_parameters(bundle: ProfileBundle) -> None:
    """题面里不许出现后端参数（checkpoint / 模型名 / 温度）。

    Spec 里出现它们，一个 Scene 的判定就绑死在一个后端上了 —— 而影子模式要的
    恰恰是**同一份题面**同时喂给两个后端。
    """
    forbidden = ("checkpoint", "temperature", "top_p", "max_tokens", "model_id")
    for spec in compile_specs(bundle).values():
        blob = json.dumps(spec.as_questions(), sort_keys=True).casefold()
        for word in forbidden:
            assert word not in blob, f"{spec.spec_id} 的题面里出现了后端参数 {word!r}"
