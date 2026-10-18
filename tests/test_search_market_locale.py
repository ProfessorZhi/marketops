"""一次检索必须说出它问的是**哪个市场** —— 而这句话要真的被发出去、被记下来。

## 这条判据守的三件静默失败

在接线之前，``SearchQuery`` 里没有市场这一格，于是三件事同时成立，而回执上
**逐字同形**：

1. **``gl``/``hl`` 一个都没发。** 问的是哪个国家的索引由 provider 那一侧的缺省
   决定 —— 那是一个我们没写下来、也无法从报告里读出来的值。一次英国检索按美国
   索引跑，两次的回执一模一样。
2. **幂等键不含市场**（``search:{text}``）。同一条检索式在两个市场里会被算成
   **同一次预留**：第二次读成 ``SKIPPED``（「付过账了」），而它从未跑过。
3. **镜像名不含市场**（``sha256_text(text)[:16]``），且 ``if not path.exists()``
   —— 第二个市场的原始响应**不落盘**，复现能力少一半，而现象只是「响应变多了」
   的反面。

第 2、3 条由下面第五组钉住：市场进的是 ``SearchQuery.identity``，而幂等键与镜像
文件名**都取那一个值** —— 两处各写一份推导，就会出现「账上算两次、盘上只留一份」
这种半对的状态，而两边的判据各自都绿。第一组钉的是第 1 条所依赖的东西：市场那
两格从哪来、必须是什么形状、被发到哪里去。

## 为什么是**两个格**而不是一个

``country`` 与 ``language`` 是两个轴（SOP 的「国家 ≠ 语言」）：英国法语、法国
英语都真实存在。把它们压成一格（``en-GB`` 这种写法就是压缩）之后，「法国、英文」
这句话在类型上表达不出来 —— 而它会被读成一次正常的检索。判据在 ``domain/util.py``，
本文件下半部分钉的是「那个判据真的拦在构造点上」。
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace

import httpx
import pytest

from marketops.adapters.profile_loader import load_profile
from marketops.adapters.serper import SerperSearchProvider
from marketops.application.billed_search import idempotency_key
from marketops.application.queries import (
    MarketLocale,
    build_queries,
    market_locale,
    market_query,
)
from marketops.config import Settings
from marketops.domain.errors import ConfigError
from marketops.ports.search import SearchQuery
from tests.conftest import REPO_ROOT
from tests.test_profiles import _mutated_settings

#: 市场取值。``GB``/``en`` 与真实 profile 的声明同形（大写两位、主语言子标签）。
MARKET = {"country": "GB", "language": "en"}


def _query(**overrides) -> SearchQuery:
    fields = {"text": "cricket club devon", "scope": "probe-001", **MARKET}
    fields.update(overrides)
    return SearchQuery(**fields)


def _captured(settings: Settings, handler=None) -> tuple[SerperSearchProvider, list[dict]]:
    """带 mock 传输的 provider，交回它**实际发出去**的请求体列表。"""
    sent: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"organic": []})

    keyed = replace(settings, serper_api_key="test-key-not-a-real-one")
    provider = SerperSearchProvider(
        keyed, client=httpx.Client(transport=httpx.MockTransport(handler or responder))
    )
    return provider, sent


def _echoing(settings: Settings) -> tuple[SerperSearchProvider, list[dict]]:
    """把请求体**回声**进响应体的 mock provider。

    与 ``_captured`` 分开，因为第五组要断的是「两个文件的内容各是各的」——
    一个恒回 ``{"organic": []}`` 的 responder 会让两个文件逐字相同，那一条断言就
    测不到东西（而它看起来是绿的）。
    """
    sent: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        sent.append(payload)
        return httpx.Response(200, json={"echo": payload})

    keyed = replace(settings, serper_api_key="test-key-not-a-real-one")
    provider = SerperSearchProvider(
        keyed, client=httpx.Client(transport=httpx.MockTransport(responder))
    )
    return provider, sent


# ----------------------------------------------------------------------
# 一、两格真的发出来了，而且是声明里那两格
# ----------------------------------------------------------------------
def test_the_request_carries_both_market_fields(settings) -> None:
    """请求体**逐字**是这四个键。

    用整字典相等而不是「``gl`` 在里面」：多一个键、少一个键都是这一片要挡的事
    （少一个键正是接线之前的状态，而它当时看起来一切正常）。
    """
    provider, sent = _captured(settings)

    provider.search(_query(text="cricket club devon"), limit=10)

    assert sent == [
        {"q": "cricket club devon", "num": 10, "gl": "gb", "hl": "en"}
    ]


def test_country_and_language_are_two_axes_in_the_request(settings) -> None:
    """英国法语与法国英语**发出两个不同的请求**。

    这一条是「两格是一个还是两个」的全部判据：把它们压成一格的实现在这里只能发出
    一个请求体（两次调用逐字相同），而那时「法国、英文」这句话已经在类型上不存在了。
    """
    provider, sent = _captured(settings)

    provider.search(_query(country="GB", language="fr"))
    provider.search(_query(country="FR", language="en"))

    assert sent[0]["gl"] == "gb" and sent[0]["hl"] == "fr"
    assert sent[1]["gl"] == "fr" and sent[1]["hl"] == "en"
    assert sent[0] != sent[1]


def test_the_country_is_lowercased_at_the_adapter_not_in_the_type(settings) -> None:
    """``gl`` 的写法转换住在适配器里：``SearchQuery.country`` 仍是 ``GB``。

    这一条是上面那条的正控 —— 一个把 ``SearchQuery.country`` 直接小写化的实现也会
    让请求体全绿，而它改的是**本仓那一格的取值**：国家那一格进幂等键与镜像文件名，
    改它等于把两条账并成一条。
    """
    provider, sent = _captured(settings)
    query = _query(country="GB")

    provider.search(query)

    assert query.country == "GB", "本仓那一格被适配器改写了"
    assert sent[0]["gl"] == "gb"


# ----------------------------------------------------------------------
# 二、构造不出来就是失败关闭（类型层）
# ----------------------------------------------------------------------
def test_a_query_cannot_be_built_without_a_market() -> None:
    """缺一格 ⇒ ``TypeError``。**没有缺省值，所以没有「忘了给」这种状态。**

    可选参数在这里等于一个开关：给值的一方按英国问、忘了给的一方按美国问，而两边
    的回执逐字同形。所以这两格是必填的 —— 构造不出这条查询，就是失败关闭点。
    """
    for missing in ("country", "language"):
        fields = {"text": "x", "scope": "probe-001", **MARKET}
        fields.pop(missing)
        with pytest.raises(TypeError):
            SearchQuery(**fields)


def test_a_lowercase_country_is_rejected_not_normalised() -> None:
    """``gb`` 被拒，**不是**被改写成 ``GB``。

    拒绝而不是规范化：这一格进幂等键与镜像文件名，一个替调用方改写的实现会在盘上
    留下一份账，而调用方以为自己写的那个写法被记下了。
    """
    with pytest.raises(ValueError, match="两位大写 ISO-3166"):
        _query(country="gb")


def test_a_locale_style_language_is_rejected() -> None:
    """``en-GB`` 被拒 —— 地区部分不属于语言那一格。"""
    with pytest.raises(ValueError, match="主语言子标签"):
        _query(language="en-GB")


def test_market_locale_holds_the_same_two_rules() -> None:
    """同一个值类型单独构造时也守着同样两条（它是这两格的载体）。"""
    assert MarketLocale(country="GB", language="en").country == "GB"
    with pytest.raises(ValueError, match="两位大写 ISO-3166"):
        MarketLocale(country="gb", language="en")
    with pytest.raises(ValueError, match="主语言子标签"):
        MarketLocale(country="GB", language="EN")


# ----------------------------------------------------------------------
# 三、市场从声明里读，不从参数里收
# ----------------------------------------------------------------------
def test_the_real_profile_yields_its_declared_market(bundle) -> None:
    """真实 profile 的声明 → 一次检索问的市场。

    ``en-GB`` 这样的完整标签只取**主语言子标签**：地区部分归 ``country`` 那一格，
    而国家那一格的值是它自己声明的（不是从 locale 里拆出来的）。
    """
    locale = market_locale(bundle)

    assert (locale.country, locale.language) == ("GB", "en")
    assert bundle.profile is not None and bundle.profile.country == "GB"

    query = market_query(bundle, text="cricket club devon", scope="probe-001")
    assert (query.country, query.language) == ("GB", "en")


def test_market_query_takes_no_market_from_the_caller() -> None:
    """签名里没有市场那两格 —— 调用方**给不出**一个与声明不同的市场。

    这一条盯的是「多一个参数就能覆盖声明」这种写法：它可以有一份看起来很合理的
    实现（缺省用声明），而只要它在，调用点就能静默地按另一个市场问。
    """
    parameters = inspect.signature(market_query).parameters

    assert set(parameters) == {"bundle", "text", "scope", "purpose"}
    assert "country" not in parameters and "language" not in parameters
    assert parameters["text"].kind is inspect.Parameter.KEYWORD_ONLY


def test_build_queries_has_no_default_bundle() -> None:
    """``bundle`` 没有缺省值。

    ``bundle=None`` 的缺省形状正好是「这次检索不用说出它问的是哪个市场」——
    也就是让一次按英国问、一次按美国问的回执逐字同形。缺省在，那条路就一直开着。
    """
    parameter = inspect.signature(build_queries).parameters["bundle"]

    assert parameter.default is inspect.Parameter.empty


def test_a_bundle_without_a_declaration_block_fails_closed(bundle) -> None:
    """没有声明块 ⇒ ``ConfigError``，回执点名缺的是什么。

    ``ProfileBundle.profile`` 可以是 ``None``（手搓的 bundle 就长这样），而那时
    「这一次问的是哪个市场」没有出处 —— 用 provider 的缺省顶上，会让一次按 A 市场
    的检索静默地按 B 市场跑。
    """
    with pytest.raises(ConfigError, match="没有声明块"):
        market_locale(replace(bundle, profile=None))


def test_a_missing_locales_declaration_fails_closed(repo_root, tmp_path) -> None:
    """``search.locales`` 不写 ⇒ 装载成功，**第一次检索**停在语言那一格。

    与装载层那几条负控配成一对：这一条证的是「装载层放它过去」之后失败真的会发生，
    而且发生在说得清那件事的地方。
    """
    probe = _mutated_settings(tmp_path, repo_root, "locales_absent")

    loaded = load_profile(probe)
    assert loaded.profile is not None and loaded.profile.search_locales == ()

    with pytest.raises(ConfigError, match="search.locales"):
        market_locale(loaded)


def test_an_empty_declared_country_fails_closed(repo_root, tmp_path) -> None:
    """``country: ""`` ⇒ 装载成功，第一次检索停在国家那一格。"""
    probe = _mutated_settings(tmp_path, repo_root, "country_empty")

    loaded = load_profile(probe)

    with pytest.raises(ConfigError, match="country 是空的"):
        market_locale(loaded)


def test_a_country_that_is_not_an_iso_code_fails_closed(repo_root, tmp_path) -> None:
    """``country: gb`` ⇒ 装载成功，第一次检索**点名 profile.country** 报错。

    回执点的是 profile 那一格而不是 ``SearchQuery.country``：写坏它的人在
    profile.yaml 里，指错地方会让人去改代码。
    """
    probe = _mutated_settings(tmp_path, repo_root, "country_not_iso")

    loaded = load_profile(probe)

    with pytest.raises(ConfigError, match="profile.country"):
        market_locale(loaded)


def test_two_declared_languages_fail_closed_at_use(repo_root, tmp_path) -> None:
    """两种语言 ⇒ **装载成功**（那是一份诚实的声明），第一次检索停下并说明为什么。

    取第一条是静默丢掉一条已声明的东西；按两种语言展开会让一条检索式变成 N 次
    计费调用 —— 那要连额度预留与账本一起改。两件事都不该在这里悄悄做掉。
    """
    probe = _mutated_settings(tmp_path, repo_root, "locales_two_languages")

    loaded = load_profile(probe)
    assert loaded.profile is not None
    assert loaded.profile.search_locales == ("en", "fr")

    with pytest.raises(ConfigError, match="多种语言"):
        market_locale(loaded)


# ----------------------------------------------------------------------
# 五、身份：同一条检索式在两个市场里是**两件事**
# ----------------------------------------------------------------------
def test_two_markets_are_two_idempotency_keys() -> None:
    """同 text、不同市场 ⇒ **两条**幂等键。

    幂等键答的是「这是不是同一次意图」。市场不同就不是：一次问英国索引、一次问
    法国索引，返回的是两批页面。合成一条键之后，第二次会被读成 ``SKIPPED``
    （「付过账了」）—— 而它从未跑过，报告上看起来只是「这一次很快」。
    """
    assert idempotency_key(_query(country="GB", language="en")) != idempotency_key(
        _query(country="FR", language="fr")
    )
    assert idempotency_key(_query(country="GB", language="fr")) != idempotency_key(
        _query(country="GB", language="en")
    )


def test_the_same_text_and_market_is_still_one_key() -> None:
    """**正控**：同 text、同市场 ⇒ 同一条键。

    没有这一条，一个「每次都算出一条新键」的实现会让上面那条以最糟的方式变绿 ——
    它让每一条检索式都重新计费，而判据说的是「市场被算进去了」。
    """
    assert idempotency_key(_query()) == idempotency_key(_query())


def test_the_identity_is_injective_over_the_three_fields() -> None:
    """身份是**单射**的 —— 没有两条不同的检索式算出同一个身份。

    现状下这条是绿的（分隔符是 ``/``，而两格的字符集都不含它）。它的作用是把
    「为什么能用 ``/`` 拼」这个理由从 ``SearchQuery.identity`` 的 docstring 里搬进
    执行器：一个把三格**直接拼起来**的实现（``GB`` + ``en`` + 文本）在这里会撞上
    —— ``("GB", "enx", "")`` 与 ``("GB", "en", "x")`` 得到同一个串，而两格的字符集
    **各自都允许**这两种取值（三字母语言标签合法，检索式可以为空）。
    """
    cases = [
        ("GB", "en", "x"),
        ("GB", "enx", ""),
        ("GB", "en", ""),
        ("GB", "en", "/x"),
        ("FR", "en", "x"),
    ]
    identities = [
        SearchQuery(
            text=text, scope="probe-001", country=country, language=language
        ).identity
        for country, language, text in cases
    ]

    assert len(set(identities)) == len(cases), identities


def test_two_markets_land_in_two_mirror_files(settings) -> None:
    """两个市场 ⇒ 两个镜像文件，**两个都真的落盘、内容各是各的**。

    内容寻址的代价是「同一份内容只留一份」，而两个市场的原始响应**是两份不同的
    内容**。合成一个文件之后，第二个市场的那份响应不落盘，而现象只是「文件数比
    预期少一个」—— 它不会报错，只会让复现能力少一半。
    """
    provider, _ = _echoing(settings)
    first = provider.search(_query(country="GB", language="en"))
    second = provider.search(_query(country="FR", language="fr"))

    assert first.raw_response_path is not None
    assert second.raw_response_path is not None
    assert first.raw_response_path != second.raw_response_path
    assert first.raw_response_path.is_file() and second.raw_response_path.is_file()

    one = json.loads(first.raw_response_path.read_text(encoding="utf-8"))
    two = json.loads(second.raw_response_path.read_text(encoding="utf-8"))
    assert one["echo"]["gl"] == "gb" and two["echo"]["gl"] == "fr", (
        "两个文件的内容逐字相同 —— 那说明其中一个写的是另一次调用的响应"
    )


def test_the_mirror_name_is_a_pure_function_of_the_query(settings, tmp_path) -> None:
    """**正控**：文件名**只由这条检索式**决定 —— 与时间、响应体、发出次序无关。

    与 ``tests/test_mirror_names_do_not_carry_the_query.py`` 第 2 条同一条纪律：
    「路径不含检索词」的实现可以是不留档，也可以每次换一个随机名 —— 那两件事都会
    让那条判据以更糟的方式为真。这一条钉住「算得回来」：换个运行目录、换个
    provider 实例、换一份响应，同一条检索式仍落到同一个相对路径。
    """
    provider, _ = _echoing(settings)
    here = provider.search(_query(text="cricket club devon"))

    elsewhere = replace(settings, data_root=tmp_path / "another-runtime")
    elsewhere.ensure_data_dirs()
    other, _ = _echoing(elsewhere)
    there = other.search(_query(text="cricket club devon"))

    assert here.raw_response_path is not None and there.raw_response_path is not None
    assert here.raw_response_path.relative_to(
        settings.data_root
    ) == there.raw_response_path.relative_to(elsewhere.data_root)


def test_the_key_and_the_mirror_name_agree_on_what_is_the_same_query(settings) -> None:
    """两处推导是**同一个谓词**的两种写法：键相同 ⟺ 落点相同，逐对成立。

    幂等键住在 ``application/``，镜像名住在 ``adapters/``。两处各自回答「这是不是
    同一件事」—— 各写一份的话，一处掺进市场、另一处不掺，就会出现「账上算两次、
    盘上只留一份」这种半对的状态，而两边的判据各自都绿。

    断言写成**逐对等价**而不是「三条互不相同」：后者在「两处都漏掉市场」的实现下
    也成立（三条文本本来就不同），而那正是要挡的那件事。
    """
    provider, _ = _echoing(settings)
    cases = [
        _query(country="GB", language="en"),
        _query(country="FR", language="fr"),
        _query(country="GB", language="fr"),
        _query(country="GB", language="en"),  # 与第 0 条同市同文
    ]
    locations = []
    for query in cases:
        result = provider.search(query)
        assert result.raw_response_path is not None
        locations.append(result.raw_response_path)

    for i, left in enumerate(cases):
        for j, right in enumerate(cases):
            assert (idempotency_key(left) == idempotency_key(right)) is (
                locations[i] == locations[j]
            ), f"第 {i} 条与第 {j} 条在键与落点上不一致"

    assert len(set(locations)) == 3, "四条里有一条该与另一条同落点，实测没有"


# ----------------------------------------------------------------------
# 六、只有一条构造路（按 AST 扫，不按自觉）
# ----------------------------------------------------------------------
def _search_query_call_sites() -> dict[str, int]:
    """扫 ``marketops/`` 下每一处 ``SearchQuery(...)`` 调用点 → 相对路径计数。"""
    sites: dict[str, int] = {}
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "SearchQuery":
                found += 1
        if found:
            sites[str(path.relative_to(REPO_ROOT))] = found
    return sites


def test_only_the_query_module_constructs_a_search_query() -> None:
    """``SearchQuery`` 只从 ``application/queries.py`` 里构造。

    这两格是必填的，所以「在别处构造」也能过 —— 但那时填进去的是什么就只有那个
    调用点知道，而它决定的是幂等键、镜像文件名与 provider 的国家/语言参数。这条
    判据把「市场只能从声明里来」从自觉变成一条会红的规则。

    **正控**：那一处必须真的扫得到（计数为空说明这条判据在扫空气，而扫空气的实现
    对任何新调用点都会答绿）。
    """
    sites = _search_query_call_sites()

    assert sites, "一处都没扫到 —— 这条判据现在扫的是空气"
    assert set(sites) == {"marketops/application/queries.py"}
    # 本模块里那两条快路各构造一次（``market_query`` 与 ``build_queries`` 经它）。
    assert sites["marketops/application/queries.py"] == 1


def test_the_ast_scan_is_looking_for_the_right_name() -> None:
    """扫的东西与 ``markets`` 里那个类同名 —— 改名那天这条判据不能静默失效。

    一条按名字扫 AST 的判据在类被改名之后会**恒绿**（扫不到任何东西），而它长得与
    「全仓只有一处构造点」逐字相同。正控：拿一个真的会有命中的源码串喂给它。
    """
    source = "from x import SearchQuery\nq = SearchQuery(text='x')\n"
    tree = ast.parse(source)
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == SearchQuery.__name__
    ]

    assert len(hits) == 1
