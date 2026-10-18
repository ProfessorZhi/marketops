"""SearchProvider 抽象。

**职责边界**：Search 只做 URL discovery。它不产生 Contact，也不产生可用作
Contact 依据的证据。

即便 SERP snippet 里出现了一个格式完全正确的邮箱，那也只是「某个页面里可能
有邮箱」的线索 —— 它可能来自缓存、聚合站、目录站，甚至竞品站点。要成为结果，
必须：抓取真实公开页面 → 在镜像中发现 literal email → 保存 evidence。
这条规则在 ``domain.bind`` 里以类型层面强制执行，而不是靠流程自觉。

实现（Serper）在 ``marketops/adapters/serper.py``。本模块**不得**出现任何厂商名。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..domain.util import (
    check_canonical_id,
    check_market_country,
    check_market_language,
)


@dataclass(frozen=True)
class SearchQuery:
    """一次检索请求。``purpose`` 用于事后统计「哪些 SERP query 真正产出了东西」。

    ``scope`` 必须**已经是**文件名（``check_canonical_id``）：原始响应镜像按它
    建目录。这条校验在**发请求之前**发生 —— 一次注定写不进去的检索不该先花掉钱。
    判据不在这里重复实现，见 ``domain/util.py``。

    ## 这一格为什么不叫 ``account_id``

    它叫 ``scope`` 是因为它答的是「这一行记在谁的账上」，而这个答案**不总是**
    一个账号：联系人研究那一轮是账号 id，而 §2 的 ``discovery_scope`` 那一轮
    是**这一轮运行的名字**（``application/discovery_run.py`` 的
    ``DiscoveryScope.scope``）—— 发现期要做的恰好是「找出账号」，所以那时候
    还没有账号可以记在名下。

    改名的理由不是整洁，是那一格原来的名字在**其中一个调用点上说了假话**：
    ``SearchQuery(account_id=scope.scope, …)`` 读起来像「这次检索是关于账号
    ``discovery-v1-arm-b`` 的」，而那个字符串是一个运行名。同名两义的东西迟早
    会被当成只有一义来用。先例与完整论证在同一棵树的
    ``marketops/v1-serper-discovery`` 分支的 ``cb9dac1`` 上。

    ## 两个市场格为什么是**必填**，而不是可选

    一次检索必须说出它问的是**哪个国家的索引**、**哪种语言**。这两件事都不是
    检索式的一部分 —— 同一条检索式在两个市场里是两个不同的动作，而缺省值
    （provider 自己那一侧默认的国家）会让「我们问的是美国索引」这件事在报告上
    完全看不见，且**看起来一切正常**。

    可选参数在这里等于一个开关：给值的一方按英国问、忘了给的一方按美国问，
    而两边的回执逐字同形。所以它是两个必填字段：给不出市场，就**构造不出**
    这条查询（调用方的失败关闭点在 ``application/queries.py::market_query``，
    它从 bundle 读，读不到就抛 ``ConfigError`` 点名缺哪一个声明）。

    两格是**两个轴**，不是一个：「法国、英文」与「英国、法语」都真实存在，
    而 ``en-GB`` 这种写法把二者压成一格（判据在 ``domain/util.py`` 的
    ``check_market_language``：地区部分属于 ``country`` 那一格）。
    """

    text: str
    scope: str
    #: 哪个国家的索引（两位大写 ISO-3166）。见 :func:`~marketops.domain.util.check_market_country`。
    country: str
    #: 哪种语言（主语言子标签）。见 :func:`~marketops.domain.util.check_market_language`。
    language: str
    purpose: str = "general"

    def __post_init__(self) -> None:
        check_canonical_id(self.scope, "SearchQuery.scope")
        check_market_country(self.country, "SearchQuery.country")
        check_market_language(self.language, "SearchQuery.language")

    @property
    def identity(self) -> str:
        """这条检索式的**身份**：``<国家>/<语言>/<检索式>``。

        两处各自回答「这是不是同一次动作」—— 幂等键（``application/billed_search.py``
        的 :func:`~marketops.application.billed_search.idempotency_key`）与镜像文件名
        （``adapters/serper.py``）—— 都取这一个值。两处各写一份的话，一处掺进市场、
        另一处不掺，就会出现「账上算两次、盘上只留一份」这种半对的状态，而两边的
        判据各自都绿。

        **市场在前，文本在后**，中间用一个 ``/`` 分隔：市场那两格的字符集分别是
        两位大写字母与两位/三位小写字母（``domain/util.py`` 的两个校验器），
        **都不含 ``/``**，所以这个形状是单射 —— 没有两条不同的检索式算出同一个
        身份。检索式文本本身可以含 ``/``（``site:`` 限定里就有），但它在最后，
        不影响前面的切分。

        ``scope`` **不进身份**：它已经是账本与镜像目录的**另一条轴**（键的作用域
        与目录名），再进一次会让「同一条检索式」有两个名字。身份说的是「问的是
        什么」，不是「替谁问的」。
        """
        return f"{self.country}/{self.language}/{self.text}"


@dataclass(frozen=True)
class SearchHit:
    """一条 organic 结果。``snippet`` 只用于**人工线索**，不可作证据。"""

    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    query: str = ""


@dataclass(frozen=True)
class SearchResult:
    """一次检索的完整结果。

    ``raw_response_path`` 指向落盘的原始响应（在 ``settings.mirrors_dir`` 下），
    使任何一次判定都能事后复现 —— 这是 benchmark 可审计的前提。
    """

    query: SearchQuery
    hits: tuple[SearchHit, ...] = ()
    raw_response_path: Path | None = None
    api_calls: int = 0
    from_cache: bool = False


class SearchProvider(ABC):
    """URL discovery 的统一接口。

    实现者只被允许做「查询 + 落盘原始响应 + 返回 URL 列表」。
    """

    #: 用于日志与 benchmark 的稳定标识。
    name: str = "abstract"

    @abstractmethod
    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        """执行一次检索。

        Raises:
            SearchError: 这一次没有拿到检索结果。异常上带着**观测**（状态码 /
                传输错误 —— 读不出来就是 ``None``，不折成一个数）与**诊断**
                （``domain/access.py`` 的闭集根因 + 处置），所以调用方不必去解析
                那句话；见 :class:`~marketops.domain.errors.SearchError`。

                刻意**不**在这里列「配额」之类的成因：那是供应商的计费语义，不该
                由一个状态码推出来（设计 §2）。实现方要说的是它**观测到了什么**。
            ConfigError: provider 所需凭据缺失。
        """


__all__ = ["SearchQuery", "SearchHit", "SearchResult", "SearchProvider"]
