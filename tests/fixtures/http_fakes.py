"""一组假的 ``HttpFetcher``：按一张 URL 表回答，**分类交给真规则**。

这里的替身不是「照我印象里的响应长什么样」编的：``FakeHttp`` 把
``(status, content_type, body)`` 交给真的 ``assess_http_response``，落盘规则也照
``adapters/http.py`` 那一条（只有 ``OK`` / ``EMPTY_SHELL`` 才有镜像）。这样
「200 的 WAF 页会被判成 BLOCKED」这类判据测的是**真实规则**，而不是替身作者的
印象 —— 一个按印象分类的替身会让判据与实现一起漂移，而漂移的方向永远是
「两边都错得一样」。

没在表里的 URL 一律答 404，**不是**答 200：一个安静的 200 会让「这个替身忘了给
这一页摆响应」在判据里长得像一个成功的抓取。这个默认值抓到过一次真的缺口 ——
``site_crawl`` 的替身一开始漏了裸种子的响应，而全部 22 条判据仍然绿。

## 为什么它住在 ``fixtures/`` 而不是某一个测试文件里

它现在有三个消费者：``tests/test_sitemap_frontier.py``（前沿自己）、
``tests/test_site_crawl.py``（拿前沿做编排的那一层）与
``tests/test_mcp_research_tools.py``（服务面上那条 ``crawl_site``）。抄第二份的
代价不是多几行，而是**两份替身会分叉**：一份修了「404 该不该落盘」，另一份没修，
于是两个文件的判据开始对同一件事给出不同答案，而两边都是绿的。

``FakeClock`` 与 ``SlowHttp`` 是跟着同一个理由搬过来的：墙钟那一条上限要有判据，
而判据不能真的等 —— 一个要 sleep 三秒的用例最后会变成一条没人跑的用例。这两个
替身因此属于「凡是测墙钟的地方都要用的东西」，而不是某一个文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from marketops.adapters.classify import assess_http_response
from marketops.domain.enums import FetchOutcome, FetchTier
from marketops.domain.util import sha256_text
from marketops.ports.acquisition import FetchRequest, FetchResult, HttpFetcher

from .site_content import page_html, sitemap_xml

__all__ = ["FakeClock", "FakeHttp", "Reply", "SlowHttp", "site_replies"]


@dataclass(frozen=True)
class Reply:
    """一次假的响应。``transport`` 非空表示传输层就结束了（超时 / 连接错误）。"""

    body: str = ""
    status: int = 200
    content_type: str = "text/plain"
    transport: FetchOutcome | None = None


class FakeHttp(HttpFetcher):
    """按 URL 表回答的抓取器。记录它被问过什么 —— 那才是安全的判据。"""

    def __init__(self, directory: Path, replies: Mapping[str, Reply]) -> None:
        self.directory = directory
        self.replies = dict(replies)
        self.asked: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.asked.append(request.url)
        reply = self.replies.get(request.url, Reply(status=404))
        if reply.transport is not None:
            return FetchResult(
                request=request,
                outcome=reply.transport,
                tier=FetchTier.HTTP,
                final_url=request.url,
                error="transport",
                reason=f"fake:{reply.transport.value}",
            )
        outcome, block_reason = assess_http_response(
            status_code=reply.status,
            content_type=reply.content_type,
            text=reply.body,
        )
        path = None
        if outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL):
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{sha256_text(request.url)[:16]}.txt"
            path.write_text(reply.body, encoding="utf-8")
        return FetchResult(
            request=request,
            outcome=outcome,
            tier=FetchTier.HTTP,
            final_url=request.url,
            status_code=reply.status,
            content_type=reply.content_type,
            body_path=path,
            body_sha256=sha256_text(reply.body) if path else "",
            body_bytes=len(reply.body.encode("utf-8")),
            block_reason=block_reason,
            reason=f"fake:{reply.status}",
        )


class FakeClock:
    """可注入的时钟。**只在有人让它走的时候走。**

    真实部署用的是 ``time.monotonic``，而一个判据不能真的等三秒。这个替身让
    「墙钟用尽」变成一个可以精确摆出来的状态，而不是一次需要等的实验。
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SlowHttp(FakeHttp):
    """每取**一页**让时钟走 ``per_page`` 秒。

    ``free`` 是这一次实验里**不计时**的那些 URL —— 前沿自己那两次读（robots 与
    站点地图）通常是它。缺省是空元组，也就是每一次读都计时；哪些读算免费由调用
    方显式写出来，因为那正是被测的那件事（``site_crawl`` 的墙钟里含不含前沿的
    读，是一条单独问的判据）。
    """

    def __init__(
        self,
        directory: Path,
        replies: Mapping[str, Reply],
        *,
        clock: FakeClock,
        per_page: float,
        free: tuple[str, ...] = (),
    ) -> None:
        super().__init__(directory, replies)
        self._clock = clock
        self._per_page = per_page
        self._free = free

    def fetch(self, request: FetchRequest) -> FetchResult:
        result = super().fetch(request)
        if request.url not in self._free:
            self._clock.advance(self._per_page)
        return result


def site_replies(
    site: str,
    pages: Sequence[str],
    *,
    robots_lines: str = "",
    links: Sequence[str] = (),
) -> dict[str, Reply]:
    """一个形状正常的站：robots 放行、站点地图列出 ``pages``、每一页都有正文。

    ``site`` **本身**也要摆一条响应。``{site}`` 与 ``{site}/`` 是两条不同的 URL
    （前沿刻意不做规范化），而站点地图几乎总会列出后者；少了前者，第一条回执
    会是一个 404 —— 那是替身的缺口，不是被编排的性质。这个默认值抓到过一次真
    的缺口，记在 ``FakeHttp`` 的说明里。

    ``robots_lines`` 追加在 ``User-agent: *`` 之后，用来摆 ``Disallow`` 那一类
    实验；缺省是一个只声明站点地图、什么也不禁的 robots。

    ``links`` 摆在**每一页**上（含站点首页）。逐页链接是一条要看的判据，而它要
    的站必须有一个真实的形状：链接写在页面上，抓取器取回字节，解析器从字节里
    抽出来 —— 摆在这里的那一份走的正是这条路。
    """

    origin = site.rstrip("/")
    return {
        f"{origin}/robots.txt": Reply(
            f"Sitemap: {origin}/sitemap.xml\nUser-agent: *\n{robots_lines}"
        ),
        f"{origin}/sitemap.xml": Reply(sitemap_xml(*pages), content_type="application/xml"),
        origin: Reply(page_html("home", links=links), content_type="text/html"),
        **{
            url: Reply(page_html(url, links=links), content_type="text/html")
            for url in pages
        },
    }
