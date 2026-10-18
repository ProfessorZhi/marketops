"""构造站点内容的小工具：站点地图与页面。

站点地图的方言多到不值得为每一种写分支，但**我们读的那一部分**（``<loc>``，含
``sitemapindex`` 的子图）在判据里要能被精确地摆出来。这三个函数只做这件事 ——
它们不替任何人决定「这份站点地图合不合法」「这一页算不算空壳」，那是被测代码的
判断（``application/sitemap_frontier.parse_sitemap``、
``adapters/classify.assess_http_response``）。

``page_html`` 是唯一一个需要**形状**而不只是**字节**的：一次 200 的 HTML 会被
``classify`` 分成 ``OK`` 还是 ``EMPTY_SHELL``，而后者会触发浏览器升级。所以这里
给的页面必须长成真实页面的样子 —— 见它自己的说明。
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["index_xml", "page_html", "sitemap_xml"]


def sitemap_xml(*urls: str) -> str:
    """一份 ``urlset``：每一条都是页面 URL。"""
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset>{body}</urlset>'


def index_xml(*children: str) -> str:
    """一份 ``sitemapindex``：每一条指向另一份站点地图文档。"""
    body = "".join(f"<sitemap><loc>{child}</loc></sitemap>" for child in children)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex>{body}</sitemapindex>'


def page_html(
    title: str, *, paragraphs: int = 2, links: Sequence[str] = ()
) -> str:
    """一页长得像真实页面的 HTML。

    ``assess_http_response`` 会把短小的 HTML 判成 ``EMPTY_SHELL``（两条判据，
    见 ``classify.looks_like_empty_shell``：正文不足 200 字符**且**带 SPA 挂载点，
    或者**一个承载内容的标签都没有**）。而 ``EMPTY_SHELL`` 会触发浏览器升级 ——
    也就是说，一个只有 ``<body>text</body>`` 的替身会让每一条「正常路径」的判据
    偷偷走在升级路径上，而回执读起来仍然像一页取回来的页面。所以替身里的页面
    有 ``<h1>`` 与 ``<p>``：形状对，分类才由**真实的那个函数**给出，而不是由
    替身希望的结论给出。

    ``links`` 是摆在页面上的 ``href``，**逐字**写进去（摆一个相对地址就得到一条
    相对地址，替身不替你解析它）。缺省为空元组时输出**逐字节不变** —— 这个函数
    在好几处承重的分类判据里被当作「一页正常页面」用，多一个空标签就会让那些
    判据测的是另一个字节串。
    """
    body = "".join(
        f"<p>{title}，第 {index} 段。</p>" for index in range(1, paragraphs + 1)
    )
    anchors = "".join(
        f'<a href="{href}">{title} 的链接 {index}</a>'
        for index, href in enumerate(links, 1)
    )
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1>{body}{anchors}</body></html>"
    )
