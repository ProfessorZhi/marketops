"""``marketops/application/openweb.py`` 的行为测试 —— 纯函数，**不联网**。

这一族存在的理由只有一个：臂 B 的第一条检索式必须在**还不知道任何域名**的时候
就能发出去。因此这里盯的失败都不是会崩的失败，而是**不会报错**的失败：

1. 顺序不确定 —— 同一次实验跑两遍拿到不同的检索序列，报告里只表现为「净新增
   有点不一样」。这条要求比「跑两次一样」更强：**与词表的声明顺序无关**，
   否则一次词表重排就会悄悄把预算花到别的取值上。
2. ``{region}`` 没绑在冻结值上 —— 两条臂各自选地理范围，比较的就不再是发现能力。
3. 占位符闭集被放宽 —— ``{domain}`` 在开放网络模板里变成**合法**写法，
   账号事实被静默注入发现期检索式。
4. 空词表返回空序列 —— 「零条查询」在报告里与「没有候选」长得一模一样。

探针 profile 刻意用 ``probe ground`` 这类不存在的场馆类型：真实词表住
``profiles/``，这里验的是展开器行为，不是那份业务内容
（``tests/test_no_hardcoded_market_knowledge.py`` 管的是另一半）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from marketops.adapters.profile_loader import load_profile
from marketops.application.openweb import (
    MAX_EXPANSIONS_PER_TEMPLATE,
    OPEN_WEB_PLACEHOLDERS,
    build_open_web_queries,
)
from marketops.config import REPO_ROOT, Settings
from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.domain.sources import SOURCE_METHODS, resolve_source_method

PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"

#: 冻结值。真实实验里它是 manifest 的 ``geographic_scope``，两条臂传**同一个**。
DEVON = "Devon"

#: 真实 profile 的 ``search.regions`` 里躺着的那份**国家词表**。它是本文件最重要
#: 的一条诱饵：展开器读它一次，两条臂就会各自从这里挑地理范围。
#:
#: 这份词表**在盘上活着**（不是历史遗留）：``search`` 里原本还有一行
#: ``regions: [GB]``，那个死键已在 ``95c102e`` 里删掉，留下的是这四个构成国名。
#: 所以「不读它」这件事在真实 profile 上就能验，探针 profile 那条只是它的因果版本。
DECOY_REGION_WORDS = ["England", "Scotland", "Wales", "Northern Ireland"]

PROBE_TEMPLATES = [
    "{venue_type} {region} contact",
    "{venue_type} {region} team",
    "{role} {venue_type} {region}",
]
PROBE_VENUE_TYPES = ["probe ground", "probe club", "probe pitch"]


def _search(**overrides: object) -> dict:
    """一份探针 ``search`` 段。缺省值刻意与探针模板配套。"""
    section: dict = {
        "locales": ["en-GB"],
        "query_templates": list(PROBE_TEMPLATES),
        "venue_types": list(PROBE_VENUE_TYPES),
    }
    section.update(overrides)
    return section


def _bundle_with_search(tmp_path: Path, search: dict, name: str = DEFAULT_PROFILE) -> ProfileBundle:
    """复制真实 profile、只替换 ``search`` 段，再走**真实装载器**读回来。

    走真实装载器是有意的：一个只在手搭的假 bundle 上成立的展开器，接线到流水线
    上会碎 —— 而碎的那一次是在花了额度之后。
    """
    profiles_dir = tmp_path / "profiles"
    target = profiles_dir / DEFAULT_PROFILE
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, target)

    profile_yaml = target / "profile.yaml"
    document = yaml.safe_load(profile_yaml.read_text(encoding="utf-8"))
    document["search"] = search
    profile_yaml.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    settings = Settings(data_root=tmp_path / "runtime", profiles_dir=profiles_dir, profile=name)
    return load_profile(settings)


def _venue_type_in(text: str, declared: list[str]) -> str:
    """这条检索式里出现的是哪个场馆类型。

    只用在探针词表上（``probe ground`` / ``probe club`` / ``probe pitch``，
    互不为子串）：真实词表里有 ``sports ground`` 与 ``university sports ground``
    这种包含关系，用文本包含判类型本身就不唯一。
    """
    hits = [term for term in declared if term in text]
    assert len(hits) == 1, f"{text!r} 里的场馆类型不唯一：{hits}"
    return hits[0]


# --- 真实 profile：不需要账号就能展开 ----------------------------------------


def test_the_real_profile_expands_without_any_account(bundle: ProfileBundle) -> None:
    """真实 profile + 冻结值：产得出检索式，且没有一条带花括号出门。

    这条是本次提交的**存在理由** —— 在此之前，本仓唯一生成检索式的地方
    （``application/queries.py::build_queries``）要求一个已存在的 ``AccountRecord``。
    """
    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert plan.queries, "真实 profile 一条开放网络检索式都产不出来"
    assert plan.templates_declared == len(
        bundle.profile.raw["search"]["query_templates"]
    ), "声明的模板数与读到的模板数不一致"
    for query in plan.queries:
        assert "{" not in query.text and "}" not in query.text, f"未渲染的占位符：{query.text!r}"
        assert DEVON in query.text, f"没有绑上冻结值：{query.text!r}"


def test_two_runs_of_the_same_declaration_are_identical(bundle: ProfileBundle) -> None:
    """同一个 profile + 同一个冻结值，跑两次逐条相同。"""
    first = build_open_web_queries(bundle, frozen_region=DEVON)
    second = build_open_web_queries(bundle, frozen_region=DEVON)

    assert [q.text for q in first.queries] == [q.text for q in second.queries]
    assert [q.template for q in first.queries] == [q.template for q in second.queries]


def test_the_sequence_does_not_depend_on_the_vocabulary_insertion_order(
    tmp_path: Path,
) -> None:
    """打乱词表再展开，结果逐条相同 —— 这一条才是「确定性」真正的含义。

    只验「跑两次一样」是不够的：Python 的 ``dict``/``set`` 遍历顺序稳定，
    而``list`` 的声明顺序会被人重排。把声明顺序当语义，一次重排就会把预算花到
    别的取值上，而报告里什么都不会变红。
    """
    declared = build_open_web_queries(
        _bundle_with_search(tmp_path / "declared", _search()), frozen_region=DEVON
    )
    shuffled = build_open_web_queries(
        _bundle_with_search(
            tmp_path / "shuffled",
            _search(venue_types=list(reversed(PROBE_VENUE_TYPES))),
        ),
        frozen_region=DEVON,
    )
    rotated = build_open_web_queries(
        _bundle_with_search(
            tmp_path / "rotated",
            _search(venue_types=PROBE_VENUE_TYPES[1:] + PROBE_VENUE_TYPES[:1]),
        ),
        frozen_region=DEVON,
    )

    assert [q.text for q in shuffled.queries] == [q.text for q in declared.queries]
    assert [q.text for q in rotated.queries] == [q.text for q in declared.queries]


# --- {region} 只绑冻结值 ------------------------------------------------------


def test_every_query_carries_the_frozen_value_and_no_declared_country_word(
    bundle: ProfileBundle,
) -> None:
    """传入 ``Devon`` ⇒ 每条检索式里是 ``Devon``，不是四个构成国名里的任何一个。

    那四个名字**就躺在同一个 ``search`` 映射下的 ``regions`` 键里**。展开器读它，
    两条臂就会各自从这里挑地理范围，而用户规格要求两支臂绑**同一个**冻结值。
    """
    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    for query in plan.queries:
        assert DEVON in query.text
        for decoy in DECOY_REGION_WORDS:
            assert decoy not in query.text, f"检索式里出现了声明过的国家名：{query.text!r}"
    assert plan.frozen_region == DEVON


def test_a_declared_region_word_list_is_never_read(tmp_path: Path) -> None:
    """``search.regions`` 就算声明着，展开器也不读它。

    真实 profile 上那条（``test_every_query_carries_the_frozen_value_...``）说的是
    「产出的检索式里没有它」；这条是它的**因果**版本：把那份词表写成探针词表，
    验证「不读」而不是「恰好没落进序列」。
    """
    bundle = _bundle_with_search(
        tmp_path, _search(regions=list(DECOY_REGION_WORDS))
    )
    assert bundle.profile.raw["search"]["regions"] == DECOY_REGION_WORDS, (
        "探针 profile 没有真的声明 regions —— 这条测试会假通过"
    )

    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert plan.queries
    for query in plan.queries:
        assert DEVON in query.text
        for decoy in DECOY_REGION_WORDS:
            assert decoy not in query.text, f"展开器读了 search.regions：{query.text!r}"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "{domain}", "de von}", None, ["Devon"], 7],
)
def test_the_frozen_region_must_be_one_usable_value(value: object, bundle: ProfileBundle) -> None:
    """冻结值缺失/空白/带花括号/不是一个字符串 ⇒ 抛错，不悄悄降级成空。

    带花括号的值会被原样拼进检索式 —— 那是一个看起来正常的检索式里凭空多出来的
    占位符形状，而它不经任何校验。
    """
    with pytest.raises(ConfigError):
        build_open_web_queries(bundle, frozen_region=value)  # type: ignore[arg-type]


# --- 占位符闭集 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "placeholder",
    ["domain", "organisation", "country", "venue_name", "responsible_entity", "location"],
)
def test_account_side_placeholders_are_refused_in_open_web_templates(
    placeholder: str, tmp_path: Path
) -> None:
    """账号侧占位符在开放网络模板里**不被接受** —— 这是注入，不是配置疏漏。

    这一族在还没有账号的时候就要发出检索式。一个与「站内检索」族共用的校验器会
    让 ``{domain}`` 在这里变成合法写法，于是账号事实被塞进发现期检索式，
    检索式看起来完全正常、报告里没有任何东西变红。
    """
    bundle = _bundle_with_search(
        tmp_path, _search(query_templates=[f"{{{placeholder}}} {{venue_type}} {{region}}"])
    )
    with pytest.raises(ConfigError) as caught:
        build_open_web_queries(bundle, frozen_region=DEVON)

    message = str(caught.value)
    assert placeholder in message
    # **说的是「账号侧」而不是「不认识」。** 两条分支都会拒，所以只断言「抛了」
    # 证明不了这一条还在：把账号侧那条分支删掉，这些模板会落进「本族不认识的
    # 占位符」—— 一样红，而报错把一次注入说成了一个笔误。
    assert "账号侧" in message, f"账号侧占位符被当成了未知占位符：{message}"


def test_the_open_web_placeholder_set_is_its_own_closed_set() -> None:
    """闭集是本族自己的：账号事实一个都不在里面。"""
    assert {"venue_type", "region"} <= OPEN_WEB_PLACEHOLDERS
    assert not ({"domain", "organisation", "country"} & OPEN_WEB_PLACEHOLDERS)


@pytest.mark.parametrize(
    "template",
    [
        "{venue_typ} {region}",  # 拼错
        "{venue_type} {region} {Grounded}",  # 大小写不对
        "{venue_type} {region",  # 括号不闭合
        "probe ground Devon",  # 没有占位符 —— 它是一个常量，不是模板
    ],
)
def test_a_template_that_cannot_be_validated_fails_closed(template: str, tmp_path: Path) -> None:
    """未知/畸形/无占位符的模板 ⇒ 抛错，**不是**被留下字面量照发。

    一条带花括号的检索式不会报错：它只会返回一堆无关结果，然后把失败伪装成
    「Google 上没有」。
    """
    bundle = _bundle_with_search(tmp_path, _search(query_templates=[template]))
    with pytest.raises(ConfigError):
        build_open_web_queries(bundle, frozen_region=DEVON)


# --- 空词表失败关闭 -----------------------------------------------------------


def test_an_empty_template_list_fails_closed(tmp_path: Path) -> None:
    """模板表为空 ⇒ 抛错，不返回空序列。

    「零条检索式」在报告里与「没有候选」长得一模一样，而它们要做的事完全不同
    （改 profile / 改结论）。
    """
    with pytest.raises(ConfigError):
        build_open_web_queries(
            _bundle_with_search(tmp_path, _search(query_templates=[])), frozen_region=DEVON
        )


def test_a_missing_template_key_fails_closed(tmp_path: Path) -> None:
    """连 ``query_templates`` 这个键都没有 ⇒ 同样抛错（这一族没有通用回退）。"""
    search = _search()
    del search["query_templates"]
    with pytest.raises(ConfigError):
        build_open_web_queries(_bundle_with_search(tmp_path, search), frozen_region=DEVON)


def test_an_empty_vocabulary_fails_closed_instead_of_returning_nothing(tmp_path: Path) -> None:
    """唯一能取值的维度是空词表 ⇒ 抛错，不返回空序列。"""
    bundle = _bundle_with_search(
        tmp_path,
        _search(query_templates=["{venue_type} {region}"], venue_types=[]),
    )
    with pytest.raises(ConfigError):
        build_open_web_queries(bundle, frozen_region=DEVON)


def test_a_template_with_no_vocabulary_is_skipped_and_recorded(tmp_path: Path) -> None:
    """**渲染不出的模板：跳过并记录**，不是照发。

    照发一条 ``{venue_type} Devon contact`` 不会报错，它只会浪费一次预算并把
    「我们其实没搜」记成「搜了没结果」。而静默丢是不行的：「跑了 1 条」与
    「想跑 2 条但缺 1 个词表」在报告里必须是两句话。
    """
    bundle = _bundle_with_search(
        tmp_path,
        _search(query_templates=["{venue_type} {region}", "{region} contact"], venue_types=[]),
    )
    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert [q.text for q in plan.queries] == [f"{DEVON} contact"]
    assert plan.templates_declared == 2
    assert len(plan.skipped) == 1
    skipped = plan.skipped[0]
    assert skipped.template == "{venue_type} {region}"
    assert skipped.reason == "no_vocabulary"
    assert skipped.missing == ("venue_type",)
    assert "{" not in plan.queries[0].text


# --- 轮转 ---------------------------------------------------------------------


def test_round_robin_gives_every_template_a_first_query_before_any_second(
    bundle: ProfileBundle,
) -> None:
    """模板为外层：**任何模板都不会在别的模板之前贡献第二条**。

    逐模板耗尽会让词表最长的那个模板吃掉整个预算，而「我们其实搜了 4 种线索」
    就成了假话。
    """
    declared = list(bundle.profile.raw["search"]["query_templates"])
    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert [q.template for q in plan.queries[: len(declared)]] == declared
    assert plan.queries[len(declared)].template == declared[0]


def test_the_venue_type_dimension_is_the_inner_one(tmp_path: Path) -> None:
    """``venue_types`` 为内层：一轮换一个取值，轮内几条共用一个场馆类型。"""
    declared = list(PROBE_VENUE_TYPES)
    plan = build_open_web_queries(
        _bundle_with_search(tmp_path, _search()), frozen_region=DEVON
    )
    width = len(PROBE_TEMPLATES)

    rounds = [plan.queries[index : index + width] for index in range(0, 3 * width, width)]
    per_round = [[_venue_type_in(query.text, declared) for query in chunk] for chunk in rounds]

    for chunk in per_round:
        assert len(set(chunk)) == 1, f"同一轮里出现了多个场馆类型：{chunk}"
    assert len({chunk[0] for chunk in per_round}) == len(per_round), (
        f"相邻轮次用了同一个场馆类型：{per_round}"
    )


def test_the_plan_records_which_templates_hit_the_expansion_bound(tmp_path: Path) -> None:
    """展开上限是**记录**下来的，不是静默截断。

    ``{role}`` 有 52 个取值，场馆类型再多一点，一条模板的笛卡尔积就会过界 ——
    过界的那部分没有出现在序列里，而「没出现」必须说出来。
    """
    many = [f"probe {index:02d}" for index in range(12)]
    template = "{role} {venue_type} {region}"
    bundle = _bundle_with_search(tmp_path, _search(query_templates=[template], venue_types=many))

    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert len(plan.queries) == MAX_EXPANSIONS_PER_TEMPLATE
    assert plan.truncated == (template,)



# 这里**没有**一条 `assert MAX_EXPANSIONS_PER_TEMPLATE == 512`。512 是内存护栏，
# 不是外部标准（对照：配对键的 253 是 DNS 的界，所以那条值得钉）。钉住它只会让
# 每一次合理的调参都要动测试，而那种红灯教会人的是「改测试」。
# 它真正需要被钉住的是**行为**：截断必须被记录 —— 见上面那条。


# --- 来源注册表 ---------------------------------------------------------------


def test_the_plan_names_a_source_method_from_the_registry(bundle: ProfileBundle) -> None:
    """展开器知道自己属于哪一个发现方法，且它是注册表里的**那个成员**。

    开放网络检索扩的是池子（``public_search``）。下游拿这一栏去建候选时不必抄一个
    id —— ``marketops/domain/candidate.py::new_candidate`` 收的就是这个成员。
    """
    plan = build_open_web_queries(bundle, frozen_region=DEVON)

    assert SOURCE_METHODS[plan.source_method.id] is plan.source_method
    assert plan.source_method.family == "public_search"
    assert resolve_source_method(plan.source_method.id) is plan.source_method
