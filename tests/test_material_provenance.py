"""``material`` 那一格：**这一行的正文是从哪来的** —— 生产者那一端的判据。

## 这一条判的是谁说的话

``docs/MCP_SERVICE.md`` §8.C.2 逐字把两种材料都列为合法输入（「a normal public JS
page」与「a clearly labeled supplemental controlled fixture」），紧接着一句是
「**Both kinds must be labeled.**」，再一句是「A controlled fixture proves integration
behavior only and **cannot count as market yield or real-site improvement**」。

于是这一格要回答的是一道**二选一**的题，而承重的地方不在于缺省取哪个成员，在于
**声明落在哪一个对象上**：

* 落在 ``FetchRequest`` 上 —— 那是错的，而且错得很安静。生产里构造请求的那一处
  会写死在 ``LIVE``，而**替换发生在它下面**：任何一条注入了 ``MockTransport``
  的判据走的是同一段业务代码、同一个构造点，于是它会把一份自造的页面记成现场
  覆盖。这正是这一格存在的理由要挡的那句话。
* 落在**抓取器实例**上（这里）—— 真出网的组合根写 ``LIVE``，其余什么都不写。

下面六条判据各钉一半，缺任何一条都会让上面那个错误设计重新变得可表达：

1. 组合根那两个实例是 ``LIVE``（两个 tier 各一条）；
2. 一个裸构造的实例落在缺省上（两个类各一条）；
3. **一次替换了网络的抓取**落下来的那一行是 ``SYNTHETIC`` —— 这一条是负向的那一半，
   它断言的是「没有任何人需要写什么」（直连与浏览器各一条）；
4. **一条声明过的抓取写出来就是 ``live``** —— 正向的那一半。少了它，一个把这一格
   写死成 ``SYNTHETIC`` 的实现会让上面每一条都绿，而一次现场覆盖都数不出来；
5. ``attempt_from`` 的 ``material`` **没有缺省**（第三个抓取器忘不了）；
6. 存储把这一格原样带回来（在 ``test_fetch_attempt_store_contract.py`` 的逐格表里，
   不在这里重复）。

## 为什么不断言「生产里每一行都是 live」

那是一条**关于运行的**主张，而这一份判据一次网络都不碰（本仓的套件是零网络的）。
能在这里断言的是**声明发生在哪里**，以及缺省朝哪一边倒 —— 覆盖率是多少要由一次
真的运行去数，那一页是 ``list_fetch_attempts`` 的 ``counts.by_material``。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from marketops.adapters.crawl4ai_fetcher import Crawl4AIBrowserFetcher
from marketops.adapters.fetch_attempts import attempt_from
from marketops.adapters.http import HttpxFetcher
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.config import Settings
from marketops.domain.fetch_attempt import MaterialProvenance
from marketops.interfaces.composition import default_acquisition, default_http
from marketops.ports.acquisition import Fetcher, FetchRequest
from marketops.ports.fetch_attempt import FetchAttemptStore

from .fixtures.http_transport import PAGE, make_fetcher, page

SCOPE = "account-001"


def ledger_factory(store: FetchAttemptStore) -> Callable[[], FetchAttemptStore]:
    """一张只交同一个实例的工厂 —— 就是 ``default_attempt_store`` 的形状。"""
    return lambda: store


# --- 1. 真出网的那两处把 ``LIVE`` 说出口 -------------------------------------
def test_the_direct_root_declares_live(settings: Settings) -> None:
    """§10 那唯一一个直连抓取器。

    ``default_http`` 是生产路径上**唯一**一处构造 ``HttpxFetcher`` 的地方（四次
    调用点都走 ``default_acquisition``，而它走这里），所以这一条覆盖的是全部。
    """
    assert default_http(settings).material is MaterialProvenance.LIVE, (
        "组合根没有声明这一次抓的是现场页面 —— 于是它落回缺省的 SYNTHETIC，"
        "而每一次真实抓取都会被记成自造材料（低报，但同样是一句假话）"
    )


def test_the_browser_root_declares_live(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """浏览器那一层的组合根，同一条声明。

    ``DocumentAcquisition`` 没有公开的 ``browser`` 访问器（它只在自己内部用那个
    字段），所以这里**从构造点观察**：把类换成一层记录器再调组合根。换的是模块
    属性而不是 ``composition`` 的全局 —— 那个类是在函数体内导入的，所以模块属性
    就是它真正查到的那一处。
    """
    import marketops.adapters.crawl4ai_fetcher as module

    built: list[Crawl4AIBrowserFetcher] = []
    real = module.Crawl4AIBrowserFetcher

    def recording(*args: object, **kwargs: object) -> Crawl4AIBrowserFetcher:
        instance = real(*args, **kwargs)  # type: ignore[arg-type]
        built.append(instance)
        return instance

    monkeypatch.setattr(module, "Crawl4AIBrowserFetcher", recording)
    default_acquisition(settings)

    assert [fetcher.material for fetcher in built] == [MaterialProvenance.LIVE], (
        "浏览器那一层没有被声明为 LIVE。它与直连那一层是两条路，"
        "只声明其中一条会让「升级到浏览器之后覆盖还算不算现场覆盖」没有答案"
    )


# --- 2. 别的构造点什么都不写 ------------------------------------------------
def test_a_directly_built_fetcher_is_synthetic(settings: Settings) -> None:
    """**负向的那一半。** 没有声明过就是「自造」，不是「现场」。

    两个类各判一次：它们各自持有一份声明，一份共同缺省是不会有的 —— 缺省写在
    端口上（``Fetcher.material``），而两个实现都从那里取。这一条钉的是「取了」，
    而不是某一个类的构造参数表长得对。
    """
    assert HttpxFetcher(settings).material is MaterialProvenance.SYNTHETIC
    assert Crawl4AIBrowserFetcher(settings).material is MaterialProvenance.SYNTHETIC, (
        "浏览器那一层的缺省不是收回声明的那一个 —— 一个注入 egress 的实例"
        "（本机靶子）会把自己记成现场覆盖"
    )


def test_every_implementation_restates_the_ports_default() -> None:
    """两个实现自己那一份缺省，必须与端口上写的那一个**是同一个值**。

    这一格有两份声明，因为它们表达的不是同一件事：端口上那一份是**约定**
    （也是下一个 ``Fetcher`` 实现不用自己写就能继承到底线的地方），而实现构造
    参数上那一份是因为 **Python 的形参缺省取不到类属性** —— 要让构造出来的实例
    落在端口那个值上，只能再写一遍。

    再写一遍的代价就是这一条判据：只改一处的那天，端口说 ``SYNTHETIC``、实现说
    ``LIVE``，而两条各自都「看起来是权威的」。
    """
    port = Fetcher.material
    assert port is MaterialProvenance.SYNTHETIC, (
        f"端口上的缺省是 {port.value!r} —— 这一格是为覆盖率设的，"
        "缺省取肯定声明那一端等于把每一份没人声明过的材料都记成现场覆盖"
    )
    for cls in (HttpxFetcher, Crawl4AIBrowserFetcher):
        restated = inspect.signature(cls.__init__).parameters["material"].default
        assert restated is port, (
            f"{cls.__name__}.__init__ 的缺省是 {restated!r}，而端口上写的是 "
            f"{port!r} —— 两处各写一份，而它们现在说的不是同一句话"
        )


# --- 3. 替换了网络的那一次，落下来就是自造 ----------------------------------
def test_a_replaced_network_lands_as_synthetic(settings: Settings) -> None:
    """**这一份里最承重的一条**：一次真的抓取，而网络被替掉了。

    这个用例里没有任何一处写过 ``synthetic`` —— 抓取器是 ``make_fetcher`` 造的
    （假传输 + 假 DNS），它不认识这一格。所以断言的是：**缺省值就是这一格里
    「我们自己造的材料」的那一个成员**，而不是「有人记得标一下」。

    反过来说，如果声明落在 ``FetchRequest`` 上，这个用例会是**绿的而错的**：
    它会拿到业务代码里那句 ``LIVE``，而这一行的正文来自本文件顶上那个常量。
    """
    ledger = SqliteFetchAttemptStore(settings.database_path)
    fetcher = make_fetcher(settings, {PAGE: page()}, attempts=ledger_factory(ledger))

    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    rows = ledger.list_attempts()
    assert len(rows) == 1, "一次 fetch 恰好一行账（理由见 test_http_fetch_attempts）"
    assert rows[0].material is MaterialProvenance.SYNTHETIC, (
        f"这一行的正文来自 {PAGE!r} 上我们自己登记的一份响应，而它被记成了"
        f" {rows[0].material.value!r}"
    )


def test_a_replaced_browser_lands_as_synthetic(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一条，落在**浏览器那一层**：库是假的、出口是假的、靶子是我们立的。

    两个 tier 各判一次而不是只判直连那一层：声明是**每个实例一份**的，而这两个
    实例来自两个不同的构造点 —— 只钉住一处的话，另一处把 ``LIVE`` 写进调用点
    （也就是那个被否掉的设计的一个残留）不会有任何一条判据红。

    ``_Library`` / ``_fetcher`` 从 ``test_browser_fetch_attempts`` 借：那一份的架子
    是「假的库 + 假的 DNS + 形状正确的出口」，正是「我们自己造的材料」这个局面，
    而重造一份的代价是两套架子会分叉。
    """
    from tests.test_browser_fetch_attempts import _FakeResult, _Library, _fetcher

    ledger = SqliteFetchAttemptStore(settings.database_path)
    _Library(result=_FakeResult()).install(monkeypatch)
    fetcher = _fetcher(settings, attempts=ledger_factory(ledger))

    fetcher.fetch(FetchRequest(url=PAGE, scope=SCOPE))

    rows = ledger.list_attempts()
    assert len(rows) == 1, "一次浏览器渲染恰好一行账（理由见 test_browser_fetch_attempts）"
    assert rows[0].material is MaterialProvenance.SYNTHETIC, (
        "这一页是假的 crawl4ai 库交回来的，而它被记成了 "
        f"{rows[0].material.value!r} —— 一次渲染回执上分不出这两件事"
    )


def test_a_declared_live_fetch_is_written_as_live(settings: Settings) -> None:
    """**正向的那一半，而这一半少了整套东西就是空转的。**

    上面那一条说的是「没人声明时落在自造」。如果只有它，一个把这一格写死成
    ``SYNTHETIC`` 的实现（``attempt_from`` 里那一行转抄被漏掉、或者被抄错的形态）
    会让每一条判据都绿 —— 而报告上每一行都是自造材料，一次现场覆盖都数不出来。

    所以这一条把 ``LIVE`` **从构造点一路追到盘上那一行**：声明 → ``self.material``
    → ``attempt_from`` 的参数 → ``FetchAttempt.material`` → SQLite 的 payload。
    中间任何一格断掉，``live`` 都到不了这里。用的是真的抓取路径（只换掉传输与
    DNS），所以顺带钉住「转抄发生在 ``fetch`` 那一处」。
    """
    ledger = SqliteFetchAttemptStore(settings.database_path)
    fetcher = make_fetcher(
        settings,
        {PAGE: page()},
        attempts=ledger_factory(ledger),
        # 就是组合根写的那一句，而它在**构造点**而不是抓取前 —— 声明是实例的属性，
        # 一个在构造之后才成立的声明不是这一格要表达的东西。
        material=MaterialProvenance.LIVE,
    )

    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.material is MaterialProvenance.LIVE, (
        "构造点声明了 LIVE，而账上那一行是 "
        f"{row.material.value!r} —— 这一格没有生产者的声明就不剩什么了"
    )


# --- 4. 转抄这一步没有缺省可走 ----------------------------------------------
def test_the_collector_will_not_guess_the_material() -> None:
    """``attempt_from`` 的 ``material`` 是**必填**的，与 ``method``／``settlement`` 同列。

    让它带缺省看起来是免费的（缺省就是那个安全的成员）。它不是：那样一个忘了
    转抄 ``self.material`` 的抓取器会把一次真出网的抓取安静地记成自造材料 ——
    而两条路径在盘上同形。这一格的整个用途就是让这两个方向分得开，所以
    「没人说过」必须在这里当场抛，而不是落成一个值。
    """
    parameter = inspect.signature(attempt_from).parameters["material"]
    assert parameter.default is inspect.Parameter.empty, (
        "material 有了缺省 —— 第三个抓取器可以忘掉自己那一声声明，"
        "而账上看不出来"
    )
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        "它是位置参数的话，两个抓取器的调用点可以靠位置对上，"
        "而那种「对上了」在下一次改签名时是静默错位"
    )
