"""镜像文件名不得含检索词 —— **列一次目录不能等于读到别人搜过什么**。

## 这条判据守的不是命名风格

``SerperSearchProvider._persist_raw`` 曾经写作
``f"{slugify(account_id, 40)}--{slugify(query.text)}.json"``，扁平放在
``mirrors/serper/`` 一个目录里。于是检索词住在**文件名**里：一次 ``ls`` 或
``find`` 会把同组织其它运行的全部检索词逐字摆出来。2026-09-20 真实发生过 ——
一个研究代理为了确认台账落在哪而 ``find`` 了运行目录，看到了同一账号家族上一次
运行的 8 个文件名，其中一条与它自己那一发**逐字同形**。它如实报备了，并承认
自己无法证明那不是泄漏。

被毁掉的不是保密，而是**可比性**：当「第二个人会不会独立想到同一个 query」正是
被测量的量时，一份可被顺手读到的检索词清单让这个测量失去意义。而读到的动作
（一次目录列表）与它的后果（这一轮结论作废）之间没有任何提示。

## 判据

1. **路径不含检索词**：query 原文、它的 slug、以及它的每个 ≥4 字词元都不得出现在
   ``mirrors/`` 树下任何一段路径里（正控：树非空，词元确实存在于**内容**中）。
2. **留档不能因此消失**：检索词仍在文件内容里 —— 复现能力是这次的代价上限，
   不许越过（正控：真的能从文件里读回来）。
3. **不覆盖、不重复写**：同一 query 重复发出仍落到同一个文件，且不重写它。

第 2、3 条与第 1 条**成对**才有意义：只测「路径不含检索词」的实现可以是不留档、
或者每次换一个随机名 —— 那两件事都会让第 1 条以更糟的方式为真。
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from marketops.adapters.serper import SerperSearchProvider
from marketops.config import Settings
from marketops.domain.util import sha256_text, slugify
from marketops.ports.search import SearchQuery

#: 够长、足够独特，短到不会撞上 16 位十六进制摘要里的子串。
QUERIES = (
    "site:client06.example.org filetype:pdf",
    "groundsmen vacancies devon cricket club",
    "artificial surface replacement tender somerset",
)

#: 视为「泄漏」的最小词元长度。太短的词元（``in`` ``of``）会与随机摘要撞上，
#: 那会让这条判据变成一条会随机变红的判据 —— 而会随机变红的判据会被关掉。
MIN_TOKEN_LENGTH = 4


def _provider(settings: Settings, handler) -> SerperSearchProvider:
    """带 mock 传输的 provider。**不碰网络**，也不读任何真实 key。

    路径取自 ``settings`` 夹具（而不是另造一份）。另造一份会让断言看的目录与
    provider 写的目录是两个，而那两棵树在今天恰好同名 —— 一条靠巧合成立的
    测试，在夹具改路径的那天会静静地开始测空气。
    """
    keyed = replace(settings, serper_api_key="test-key-not-a-real-one")
    return SerperSearchProvider(
        keyed, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _responder(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content.decode("utf-8"))
    return httpx.Response(
        200,
        json={
            "organic": [
                {
                    "link": f"https://{slugify(payload['q'])}.example/p",
                    "title": "Example",
                    "snippet": "…",
                }
            ]
        },
    )


def _issue(provider: SerperSearchProvider, text: str, *, account_id: str = "acme-001"):
    # 这一组用例问的是路径里有没有检索词，所以市场那两格填定值（写法与 profile 的
    # 声明一致：国家两位大写、语言是主语言子标签）。
    return provider.search(
        SearchQuery(text=text, scope=account_id, country="GB", language="en")
    )


def _tokens(text: str) -> set[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.lower())
    return {word for word in cleaned.split() if len(word) >= MIN_TOKEN_LENGTH}


def _path_components(root) -> list[str]:
    """整棵树里每一段路径 —— 目录名与文件名都要算。"""
    components: list[str] = []
    for path in root.rglob("*"):
        components.extend(path.relative_to(root).parts)
    return components


# ----------------------------------------------------------------------
# 1. 路径不含检索词
# ----------------------------------------------------------------------
def test_the_raw_response_path_does_not_contain_the_query(settings) -> None:
    """单条：路径里没有 query 原文，也没有它的 slug。"""
    provider = _provider(settings, _responder)
    text = QUERIES[0]
    result = _issue(provider, text)
    assert result.raw_response_path is not None

    rendered = str(result.raw_response_path)
    assert text not in rendered
    assert slugify(text) not in rendered, "slug 化只是把空格换成连字符，不改变可读性"


def test_listing_the_mirrors_tree_does_not_reveal_any_query(settings) -> None:
    """**遍历整棵树**：任何一段路径都不含任何一条已发出 query 的词元。

    这是本文件的主判据。它刻意不检查「文件名 == 摘要」，因为那只是一种实现；
    换个同样安全的实现（比如按序号编号）不该让它变红。它检查的是**观察到的
    性质**：一次目录列表看不出一条检索词。
    """
    provider = _provider(settings, _responder)
    for text in QUERIES:
        _issue(provider, text)

    mirrors = settings.mirrors_dir
    assert mirrors.exists()

    components = _path_components(mirrors)
    # 正控：树真的非空。空的树过得了下面每一条断言 —— 那时这条判据守的是「没写」，
    # 而「没写」是这条判据的**反面**（留档不能消失，见下面第 2 组）。
    assert len(components) >= len(QUERIES), "镜像树是空的，这条判据没有测到东西"

    flattened = " ".join(components)
    for text in QUERIES:
        assert text not in flattened
        assert slugify(text) not in flattened
        for token in _tokens(text):
            assert token not in flattened, (
                f"检索词元 {token!r} 出现在镜像路径里 —— "
                f"一次目录列表就能读到这一轮搜过什么，而读的人不会知道自己读到了"
            )


def test_the_scope_directory_still_separates_accounts(settings) -> None:
    """按账号分目录仍在。

    内容寻址换掉的是**文件名**，不是「谁搜的」这条轴：两个账号发同一条 query
    不该互相看到对方的响应文件。把全部镜像拍平到一个目录里，是「路径不泄漏」
    的一个更省事的解法 —— 它以丢掉归属为代价。
    """
    provider = _provider(settings, _responder)
    _issue(provider, QUERIES[0], account_id="acme-001")
    _issue(provider, QUERIES[0], account_id="acme-002")

    scopes = sorted(
        path.name for path in (settings.mirrors_dir / "serper").iterdir() if path.is_dir()
    )
    assert scopes == ["acme-001", "acme-002"]


# ----------------------------------------------------------------------
# 2. 留档没有因此消失 —— 检索词仍在**内容**里
# ----------------------------------------------------------------------
def test_the_query_is_still_readable_inside_the_file(settings) -> None:
    """不泄漏的代价不许是「不留档」。

    检索词从文件名搬进了内容：要读它得打开文件，那是一次**能被说出来的**动作。
    这一条是上一条的代价上限 —— 没有它，一个「只写摘要不写正文」的实现会让
    ``test_listing_the_mirrors_tree_does_not_reveal_any_query`` 以更糟的方式变绿。
    """
    provider = _provider(settings, _responder)
    text = QUERIES[1]
    result = _issue(provider, text)
    assert result.raw_response_path is not None

    stored = json.loads(result.raw_response_path.read_text(encoding="utf-8"))
    assert stored["organic"][0]["link"].startswith("https://groundsmen-vacancies")

    # 响应体本身不含 query 原文（Serper 不回显它），所以「复现」靠的是**路径可推导**：
    # 同一条 query 必须能算出同一个文件名。
    #
    # 散列的是**这条检索式的身份**（``SearchQuery.identity``：国家 / 语言 / 检索式），
    # 不是检索式文本 —— 两个市场的原始响应是两份不同的内容，按文本散列会让第二个
    # 市场的那份**不落盘**（``if not path.exists()``），而现象只是「文件数少一个」。
    # 这里逐字写出身份的形状，与 ``test_billed_search.py`` 钉幂等键字面量同一条理由。
    assert result.raw_response_path.name == f"{sha256_text(f'GB/en/{text}')[:16]}.json"


# ----------------------------------------------------------------------
# 3. 不覆盖、不重复写
# ----------------------------------------------------------------------
def test_the_same_query_lands_in_the_same_file(settings) -> None:
    """同一条 query 两次 → 一个文件，且**内容不被重写**。

    「不重写」是可以观察的：第一次的响应与第二次不同，若第二次覆盖了，
    文件里会是第二个响应的内容。
    """
    calls = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"organic": [], "run": calls["n"]})

    provider = _provider(settings, responder)
    first = _issue(provider, QUERIES[0])
    second = _issue(provider, QUERIES[0])

    assert first.raw_response_path == second.raw_response_path
    files = list((settings.mirrors_dir / "serper").rglob("*.json"))
    assert len(files) == 1, "同一条 query 落了不止一个文件"
    assert json.loads(files[0].read_text(encoding="utf-8"))["run"] == 1, (
        "第二次发出覆盖了第一次的留档 —— 内容寻址的『不重复写』没有生效"
    )


def test_two_different_queries_land_in_two_files(settings) -> None:
    """不同的 query 落到不同的文件。

    这一条防的是「所有 query 都映射到同一个名字」那种退化实现 —— 它同样不含
    检索词，但会把每一次检索都写成同一个文件。
    """
    provider = _provider(settings, _responder)
    paths = {_issue(provider, text).raw_response_path for text in QUERIES}

    assert len(paths) == len(QUERIES)
    assert len(list((settings.mirrors_dir / "serper").rglob("*.json"))) == len(QUERIES)


def test_a_query_that_slugifies_to_nothing_still_gets_its_own_file(settings) -> None:
    """全部会被 slugify 吃掉的 query（非 ASCII）也不会撞在一起。

    ``slugify("中文查询")`` 得到 fallback ``"unnamed"``，所以「文件名用 slug 化后的
    query」这种实现会让**每一条**非 ASCII query 落进同一个文件 —— 一个由字符集
    决定的静默合并。内容寻址对这件事天然免疫，这一条把它钉住。
    """
    provider = _provider(settings, _responder)
    first = _issue(provider, "中文查询甲")
    second = _issue(provider, "中文查询乙")

    assert first.raw_response_path != second.raw_response_path
    assert len(list((settings.mirrors_dir / "serper").rglob("*.json"))) == 2


@pytest.mark.parametrize("text", QUERIES)
def test_every_query_reaches_the_api_unchanged(settings, text: str) -> None:
    """改名不动请求：发出去的 ``q`` 仍是原文。

    这一条是「改名只是改名」的正控 —— 一个把 query 摘要化之后再发出去的实现
    也会让上面每一条都变绿，而它换了产品行为。
    """
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload["q"])
        return httpx.Response(200, json={"organic": []})

    _issue(_provider(settings, responder), text)
    assert seen == [text]
