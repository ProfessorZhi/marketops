"""SerperSearchProvider —— ``SearchProvider`` 的第一个实现。

Serper 是 SaaS API，不 clone 仓库、不引入源码。
它在这里只被用来做 URL discovery；返回的 snippet **不进入**证据链。

原始响应一律落盘到 ``settings.mirrors_dir/serper/<scope>/``，文件名是
``SearchQuery.identity``（国家 / 语言 / 检索式）的 sha256 前 16 位 —— 与
``mirrors/pages/`` 同一套内容寻址约定。同一条检索式重复发出不覆盖、不重复写，
且跨平台同名；**两个市场是同一条检索式的两件事**，所以是两份留档（见
``_persist_raw``）。

**为什么检索词不能住在文件名里。** 这里曾经写作
``f"{slugify(account_id, 40)}--{slugify(query.text)}.json"``，扁平放在一个目录里。
那样一来**列一次目录就等于读到别人搜过什么**：一次 ``ls``/``find`` 会把同组织
其它运行的检索词逐字摆出来。2026-09-20 真实发生过 —— 一个研究代理为了确认台账
落在哪而 ``find`` 了运行目录，看到了同一账号家族上一次运行的 8 个文件名，
其中一条与它自己那一发逐字同形。它如实报备了，并承认自己无法证明那不是泄漏。

被毁掉的不是保密，是**可比性**：当「第二个人会不会独立想到同一个 query」正是
被测量的量时，一份顺手可读的检索词清单让这个测量失去意义。

还有一处同源缺陷一并收口：``slugify("中文查询甲")`` 与 ``slugify("中文查询乙")``
都得到 fallback ``"unnamed"``，于是**每一条**非 ASCII query 落进同一个文件 ——
一个由字符集决定的静默合并，而它看起来只是「响应变多了」。内容寻址对这件事
天然免疫。

检索词仍在**文件内容**里（复现能力不受影响），但要读它得打开文件 ——
那是一次能被说出来的动作，而不是一次顺手的目录列表。

## 失败也一样：说清量到了什么，而不是猜一个根因

这里曾经这样报错：

    401/403 通常是 key 失效或额度耗尽。

一句话里三个没有出处的东西：401 与 403 合成一句（而两条的处置不同）、两个从未被
观测的根因、以及一个不是统计的「通常」。设计 §2 点名的正是这一句，并给出处置：
「**不从代码注释推测供应商所有计费语义**」。

所以失败不再折成一句话，而是走 ``domain/access.py`` **已有的**观测/诊断两步：
:meth:`SerperSearchProvider._refuse_response` 与 ``_refuse_transport`` 各建立一个
:class:`~marketops.domain.access.Observation`，交给同一个 ``diagnose`` 判成
:class:`~marketops.domain.access.AccessDiagnosis`，两个对象都挂在
:class:`~marketops.domain.errors.SearchError` 上。检索面与抓取面因此共用同一张
表（设计 §4）—— 403 的结论是 ``ACCESS_DENIED_UNKNOWN``（「根因不明」，而且它**不
是** IP 封禁），挑战页是 ``WAF_CHALLENGE``（正文不得进入业务证据），429 带
``Retry-After`` 时那个秒数被读出来。

判据在 ``tests/test_search_failure_diagnosis.py``。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx

from ..config import Settings
from ..domain.access import (
    AccessDiagnosis,
    ActionTaken,
    Observation,
    SuspectedCause,
    diagnose,
    safe_response_headers,
)
from ..domain.errors import ConfigError, SearchError
from ..domain.util import FILENAME_ID_MAX_LENGTH, sha256_text, slugify
from ..ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult

SERPER_ENDPOINT = "https://google.serper.dev/search"


class SerperSearchProvider(SearchProvider):
    """Serper 检索实现。

    Args:
        settings: 提供 API key、超时、mirrors 目录。
        client: 测试时注入。生产路径下由本类自行构造并持有连接池。
    """

    name = "serper"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not settings.serper_api_key:
            raise ConfigError(
                "SERPER_API_KEY 未设置。Serper 只负责 URL discovery。\n"
                "没有它时：测试请注入自己的 SearchProvider 替身；真跑请把 key 放进本机 "
                "secret 区（不要写进 repo、.env.example、日志或聊天）。"
            )
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=settings.http_timeout_seconds,
            # **显式**写下来，不吃 httpx 的缺省。今天那个缺省恰好是不跟随，而
            # 「恰好」在某次升级或某次参数整理里会变成它的反面 —— 那时一次跳到
            # 一个没人判过的地址会被读成一次正常检索，且报告上完全正常。
            follow_redirects=False,
            headers={
                "X-API-KEY": settings.serper_api_key,
                "Content-Type": "application/json",
                "User-Agent": settings.user_agent,
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _payload(query: SearchQuery, *, limit: int) -> dict[str, object]:
        """检索请求体。**厂商词汇只住这一个函数里。**

        ``gl`` / ``hl`` 是 Serper 的字段名（国家 / 语言），所以这两个名字属于实现，
        不属于领域层 —— ``ports/search.py`` 那一侧叫 ``country`` / ``language``，
        而且刻意**不是**同一个概念：``gl=gb`` 问的是英国索引，``hl=en`` 问的是英文
        页面，两者是两个轴（见 ``domain/util.py::check_market_language``）。

        **``gl`` 要小写、``hl`` 只收主语言子标签**，所以两格各过一次转换，而不是
        直接透传：``SearchQuery`` 校验的是「大写两位国家码」「小写主语言子标签」这套
        本仓的写法，vendor 那一侧要的是另一个写法。转换写在这里，是因为这里的格式
        要求是 vendor 的。

        **没有对着真端点量过 ``gl``/``hl`` 的效果。** 本机没有做过一次真实的
        跨市场对照（同一检索式在两个国家下返回不同结果），所以这里只保证「这两格
        被发出去了、发的是声明里那个值」；「provider 是否真的按它换了索引」是一件
        还没被观测过的事。凭这一点不得声称任何地理覆盖。
        """
        return {
            "q": query.text,
            "num": max(1, min(limit, 100)),
            "gl": query.country.lower(),
            "hl": query.language,
        }

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        payload = self._payload(query, limit=limit)
        try:
            response = self._client.post(SERPER_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            raise self._refuse_transport(query, exc) from exc

        # **不调 ``raise_for_status``**：它替我们判成败的规则与我们要的差一格 ——
        # httpx 只在 3xx **带 Location** 时才抛，一个不带 Location 的 302 会落到
        # 下面 ``json()`` 那一行，读成「非 JSON」。自己判 ``is_success`` 就没有这条缝。
        if not response.is_success:
            raise self._refuse_response(query, response)

        try:
            data = response.json()
        except ValueError as exc:
            # 状态码成功、正文不是 JSON：这**也不是**一次检索结果。与上面走同一格 ——
            # 在那里 ``Observation.body_usable`` 是缺省的 ``False``，于是 ``diagnose``
            # 判 ``CONTENT_UNUSABLE``。少了这一格，一次坏的响应会变成「这次没有结果」。
            raise self._refuse_response(query, response) from exc

        raw_path = self._persist_raw(query, data)
        return SearchResult(
            query=query,
            hits=self._to_hits(data, query=query, limit=limit),
            raw_response_path=raw_path,
            api_calls=1,
        )

    # ------------------------------------------------------------------
    def _refuse_response(self, query: SearchQuery, response: httpx.Response) -> SearchError:
        """一次**不是检索结果**的响应 → 一次失败。

        两种情形都走这里：状态码不成功（4xx / 5xx / 3xx），以及状态码成功但正文
        不是 JSON。``Observation.body_usable`` 留缺省 ``False`` —— 能走到这一行
        就说明这次响应不是一份可用的检索结果，而这是**判定**不是默认：

        它盖不掉更强的结论。``diagnose`` 里挑战、401、403、429、5xx、404/410 的
        判定都在正文可用性**之前**，所以一个 403 不会因为「正文不是 JSON」被读成
        「内容问题」；正文可用性那一格只管得到 2xx，而 2xx 走到这里只有一种原因
        （解析不出 JSON）。
        """
        headers = safe_response_headers(response.headers)
        observation = Observation(
            observed_status=response.status_code,
            headers=headers,
            # **有界**：这份正文跟着异常对象停在内存里直到那次运行结束，而一个坏掉
            # 的响应不该把整轮研究的内存吃掉。界复用 ``settings.http_max_bytes``
            # （抓取面用它界页面正文），不另立一个 —— 两处共用同一个界，就不会出现
            # 「抓取按 8 MiB、检索按另一个数」这种没人解释得清的差。
            text=_bounded_text(response, self._settings.http_max_bytes),
            url=SERPER_ENDPOINT,
            final_url=SERPER_ENDPOINT,
        )
        diagnosis = diagnose(observation)
        if 300 <= response.status_code < 400:
            diagnosis = _redirect_diagnosis(
                diagnosis, response.status_code, headers.get("location", "")
            )
        return _failed(query, observation, diagnosis)

    # ------------------------------------------------------------------
    def _refuse_transport(self, query: SearchQuery, exc: httpx.HTTPError) -> SearchError:
        """一次**没有响应对象**的失败：连接、超时、协议层。

        ``observed_status`` 因此留空，而不是折成一个数。「没问到状态码」与「问到了
        一个数」是两件事，而 ``status_code or 200`` 那一类写法会把它读成后者。
        """
        observation = Observation(
            url=SERPER_ENDPOINT,
            final_url=SERPER_ENDPOINT,
            transport_error=str(exc),
            transport_error_kind=type(exc).__name__,
        )
        return _failed(query, observation, diagnose(observation))

    # ------------------------------------------------------------------
    def _persist_raw(self, query: SearchQuery, data: object) -> Path:
        """原始响应留档。这是复现能力的基础，不做可选。

        **路径里不含 query 文本**，理由见模块 docstring：文件名的可读性会让一次
        目录列表变成一次设计泄漏。query 本身在文件内容里，要读它得打开文件。

        **散列的是 ``query.identity``，不是 ``query.text``。** 两个市场的原始响应
        是两份不同的内容，所以它们是两份留档；按文本散列会把它们合成一个文件，
        而 ``if not path.exists()`` 让**第二个市场的那份响应不落盘** —— 现象只是
        「文件数比预期少一个」，它不报错，只让复现能力少一半。散列进市场之后，
        ``identity`` 相同 ⟺ 落点相同，与幂等键（``application/billed_search.py``）
        是同一个谓词。

        目录名用 ``FILENAME_ID_MAX_LENGTH`` 而不是一个本地字面量：``scope``
        已经被 ``SearchQuery`` 校验成规范形式，所以 ``slugify`` 在这里是恒等
        函数 —— 两处共用同一个界，就不会出现「校验按 60、落盘按 40」那种
        「校验通过的值在落盘时被截断」。
        """
        target_dir = (
            self._settings.mirrors_dir
            / "serper"
            / slugify(query.scope, max_length=FILENAME_ID_MAX_LENGTH)
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{sha256_text(query.identity)[:16]}.json"
        if not path.exists():  # 内容寻址：同 query 重复发出不覆盖、不重复写
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _to_hits(data: object, *, query: SearchQuery, limit: int) -> tuple[SearchHit, ...]:
        if not isinstance(data, dict):
            return ()
        organic = data.get("organic") or []
        hits: list[SearchHit] = []
        for index, item in enumerate(organic, start=1):
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or "").strip()
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=str(item.get("title") or "").strip(),
                    snippet=str(item.get("snippet") or "").strip(),
                    rank=index,
                    query=query.text,
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


def _bounded_text(response: httpx.Response, limit: int) -> str:
    """响应正文的前 ``limit`` 字节，解码成文本。

    ``errors="replace"``：截断可能切在一个多字节字符中间 —— 那一个字符不认识就是
    了，不该让解码本身抛出去，把一次「检索失败」变成一次别的失败。

    固定按 UTF-8 解（JSON 的默认编码就是它），不跟 ``content-type`` 里的 charset
    走：这份文本只有两个用处 —— ``diagnose`` 在里面找几个 ASCII 指纹，以及让人
    事后读一眼。跟随一个由响应头决定、我们没验过的编码，只会让它更不可预测。
    """
    return response.content[:limit].decode("utf-8", errors="replace")


def _redirect_diagnosis(diagnosis: AccessDiagnosis, status: int, location: str) -> AccessDiagnosis:
    """把 ``diagnose`` 给 3xx 的那一格，改成**检索面**的读法。

    ``domain/access.py`` 对 3xx 的结论是 ``NONE`` / ``PROCEEDED``，理由写在那一格
    里：「一次重定向不是一次内容失败……跟不跟由下一跳自己的地址判据决定」。那句话
    对抓取面成立 —— ``adapters/http.py`` 逐跳自己跟，每一跳都过地址判据。

    检索面**没有下一跳**：本适配器只问一个固定端点，且不跟随任何跳转（跳到别处就是
    一次没人判过的出网）。于是同一件事在这里的意思变了：它是一次**没有结果的失败**。
    而「它为什么要重定向」我们没有任何证据，所以根因填 ``UNKNOWN`` —— 那个成员的
    docstring 逐字写着它的含义是「有异常，但没有任何证据把它归到下面任何一类」。
    处置是生成一条待复核项：一个固定 SaaS 端点在跳转，是有人该看一眼的事。

    只改这三格（根因 / 处置 / 人话），不另造一个 ``AccessDiagnosis``：``replace``
    是 ``domain/access_governance.py`` 已经在用的写法（``_with_action``）。多出来的
    那个地址来自 ``safe_response_headers`` 的白名单 —— 它本来就是给人看的东西。
    """
    return replace(
        diagnosis,
        cause=SuspectedCause.UNKNOWN,
        action=ActionTaken.REVIEW_REQUESTED,
        detail=(
            f"HTTP {status}：这一跳是重定向"
            + (f"到 {location!r}" if location else "（响应里没有 Location）")
            + "；本适配器不跟随未经地址判据的跳转，这一次检索因此没有结果"
        ),
    )


def _failed(
    query: SearchQuery, observation: Observation, diagnosis: AccessDiagnosis
) -> SearchError:
    """把一次失败的交换收成一个异常。**观测与判断各占一个字段，不折成一句话。**

    那句话本身只放两样东西：这是哪一条检索式（``identity`` 含市场，所以并发多市场
    时读得出是哪一条），以及 ``diagnose`` 写好的那半句处置说明。

    **正文与响应头都不进这句话。** 报告里那一行是 ``str(receipt.error)``
    （``adapters/mcp_server/research.py`` 把它拼进 ``errors``），所以一个 8 MiB 的
    响应正文若混进来会跟着报告落到盘上。设计 §3 的「原始敏感 headers/cookies/key/IP
    不出报告」是同一条边界，只是这一次要挡的是体积与页面内容。
    """
    return SearchError(
        f"Serper 检索未完成（{query.identity}）：{diagnosis.detail}",
        observation=observation,
        diagnosis=diagnosis,
    )


__all__ = ["SerperSearchProvider", "SERPER_ENDPOINT"]
