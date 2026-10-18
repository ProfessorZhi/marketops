"""页面镜像的**位置约定**与**读回**：判据。

## 这个文件在钉什么

``Evidence.content_sha256`` 的说明写着它「指向镜像文件的具体字节」，
``ParsedDocument.sha256`` 写着它「让证据可以回溯到具体的镜像文件」——
而在这之前，**没有任何代码能从一行证据走回那个文件**。约定存在（两个适配器
各写了一遍），读回它需要写第三遍，而三遍里只要有一遍漂了，读到的就是别人的
字节：不报错、不空手，只是拿错材料。

所以这里钉两件事，分开的两件：

1. **约定只有一个定义处** —— 两个写入方与读回方说的是同一棵目录树、同一个
   文件名。判据形状是「用真实写入方落盘，用读回方取回来」。
2. **名字负责定位，字节负责校验** —— 文件名是 URL 的散列前缀，**不是**内容的。
   名字对上而字节不符是「这个 URL 的镜像被换过」，名字对不上是「这台机器没有
   那次抓取的字节」。两种故障的处置相反，而只按名字读文件的实现会把它们合成
   一种，且合成之后仍然报「材料拿到了」。

## 与 ``tests/test_mcp_control_tools.py`` 的分工

那边判的是 ``get_evidence`` 工具**对外**说什么；这边判的是它底下那条读回路径
本身。工具那一层的判据要能红在措辞上，这一层要能红在字节上 —— 一份把两个都
塞进去的判据，红了之后看不出是「读错了文件」还是「回执写错了话」。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.adapters.crawl4ai_fetcher import Crawl4AIBrowserFetcher
from marketops.adapters.hashing import sha256_file
from marketops.adapters.http import HttpxFetcher
from marketops.adapters.page_mirror import (
    account_page_dir,
    as_text,
    copy_page,
    page_path,
    page_prefix,
    page_version_path,
    read_stored_page,
)
from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.application.evidence_ingest import document_evidence
from marketops.config import Settings
from marketops.ports.acquisition import FetchRequest

URL = "https://example.co.uk/contact"
OTHER_URL = "https://example.co.uk/about"
BODY = "<html><body>grounds@example.co.uk</body></html>"
ACCOUNT = "account-001"
#: 复制这一半的两个主体（``tests/test_page_reuse.py`` 里的两个名字是给台账用的，
#: 这里只关心字节落进哪一棵目录）。
DONOR = "account-002"
RECEIVER = "account-003"


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _read(
    settings: Settings,
    *,
    account_id: str = ACCOUNT,
    url: str = URL,
    digest: str,
    max_bytes: int = 4096,
):
    return read_stored_page(
        settings,
        account_id=account_id,
        source_url=url,
        content_sha256=digest,
        max_bytes=max_bytes,
    )


# --- 约定：名字按 URL 取 -------------------------------------------------------


def test_the_name_is_the_url_hash_not_the_content_hash(settings: Settings) -> None:
    """同一份字节挂在两个 URL 下是**两个**文件；同一个 URL 抓两次是**一个**。

    这条曾经写反过：``http.py`` 的模块 docstring 说文件名含**内容** sha256，
    还带一句推论「不同 URL 内容相同不会产生重复文件」—— 按 URL 命名时那句是反的。

    写下来不是为了争一个措辞：**读回方按 URL 前缀找文件**，所以命名规则一旦
    真的是内容的散列，读回就只能全目录扫哈希。命名规则与读回代价是一件事。
    """
    first = _write(page_path(settings, scope="a", url=URL, extension=".html"), BODY)
    second = _write(page_path(settings, scope="a", url=OTHER_URL, extension=".html"), BODY)

    assert first != second, "同一份字节挂在两个 URL 下应当是两份文件"
    assert page_path(settings, scope="a", url=URL, extension=".html") == first
    assert page_prefix(URL) != page_prefix(OTHER_URL)


def test_two_accounts_do_not_share_a_directory(settings: Settings) -> None:
    """账号那一层是分片的：抹平它会让两个账号的同名页面混成一棵。"""
    assert account_page_dir(settings, "a") != account_page_dir(settings, "b")


def test_the_two_real_writers_land_where_the_reader_looks(settings: Settings) -> None:
    """**两个真实写入方**落盘的位置，就是约定算出来的位置。

    断言的是 ``_persist`` —— 约定的实现本身。判据不重抄一遍目录布局：重抄一份的
    判据会在约定改动时和实现一起漂，而它仍然是绿的。
    """
    request = FetchRequest(url=URL, scope=ACCOUNT)
    plain = HttpxFetcher(settings)._persist(
        request, BODY.encode("utf-8"), content_type="text/html", url=URL
    )
    rendered = Crawl4AIBrowserFetcher(settings)._persist(request, BODY, url=URL)

    assert plain == page_path(settings, scope=ACCOUNT, url=URL, extension=".html")
    assert rendered == page_path(
        settings, scope=ACCOUNT, url=URL, extension=".rendered.html"
    )


def test_neither_writer_overwrites_bytes_already_on_disk(settings: Settings) -> None:
    """同一个 URL 抓第二次**不覆盖**已经落盘的字节 —— 两个 tier 都是。

    名字是 URL 的散列，所以第二次抓取会算回**同一个文件名**。覆盖它，上一条
    引用这个 URL 的证据就指向了被换掉的字节：读回时报的「散列不符」是**真的**
    （字节确实变了），而它本来是一条可以避免的证伪信号 —— 一条本来该指向
    「这次抓取的内容变了」的信号，被我们自己的写入行为制造了出来。

    浏览器那一侧此前是无条件 ``write_text``，与直连那一侧不一致；这条判据是
    两边对齐的执行器。
    """
    request = FetchRequest(url=URL, scope=ACCOUNT)
    plain = HttpxFetcher(settings)._persist(
        request, BODY.encode("utf-8"), content_type="text/html", url=URL
    )
    rendered = Crawl4AIBrowserFetcher(settings)._persist(request, BODY, url=URL)
    original = plain.read_bytes()

    HttpxFetcher(settings)._persist(
        request, b"<html><body>second</body></html>", content_type="text/html", url=URL
    )
    Crawl4AIBrowserFetcher(settings)._persist(request, "<html><body>second</body></html>", url=URL)

    assert plain.read_bytes() == original
    assert rendered.read_text(encoding="utf-8") == BODY


def test_the_second_version_lands_under_its_own_name_and_the_old_reference_still_reads(
    settings: Settings,
) -> None:
    """同一个 URL 换了内容：新字节落在**另一个文件**上，旧证据**仍然读得回去**。

    上一条只断言「旧文件没被改」—— 它把 ``_persist`` 的返回值丢掉了，于是这一片
    真正的承重处**没有任何判据**：新字节去了哪里、它叫什么、用旧证据的
    ``content_sha256`` 还读不读得回旧字节。这一条把那个返回值接上，并把读回方
    也接进来。

    ## 为什么「旧引用不变」必须是端到端串起来的

    ``Evidence.content_sha256`` 指的是**那一次抓取**的字节。第二次抓取换了内容之后，
    旧证据行一个字节都没有变，而它还能不能读出材料，取决于写入侧有没有把旧文件
    留在原地 —— 那是**写入侧的行为**，靠读回侧的判据（``test_a_page_whose_bytes_changed_is_not_returned``
    用的是被覆盖的同一个文件）是测不出来的。

    ## 名字只钉**承重的那一段**

    ``read_stored_page`` 按 :func:`page_prefix` 找候选、再按字节挑，所以名字里
    承重的是那一段前缀。后缀怎么拼是写入侧的实现细节，判据不重抄一遍 ——
    重抄的判据会在约定改动时和实现一起漂，而它仍然是绿的。
    """
    request = FetchRequest(url=URL, scope=ACCOUNT)
    first = HttpxFetcher(settings)._persist(
        request, BODY.encode("utf-8"), content_type="text/html", url=URL
    )
    old_digest = sha256_file(first)
    changed = "<html><body>bookings@example.co.uk</body></html>"

    second = HttpxFetcher(settings)._persist(
        request, changed.encode("utf-8"), content_type="text/html", url=URL
    )

    assert second != first, (
        "换了内容却写回同一个文件 —— 那正是上一条判据挡的覆盖，"
        "而这一次它连痕迹都不留"
    )
    assert second.read_bytes() == changed.encode("utf-8"), (
        "新字节必须落在新文件里：``FetchResult.body_sha256`` 按新文本算过，"
        "读回方按它去前缀候选里挑，挑错一份就把一次真实的变更读成「什么都没发生」"
    )
    assert second.name.startswith(page_prefix(URL)), (
        "新文件仍要在读回方会去找的那个前缀下 —— 换一个后缀可以，"
        "换掉前缀等于这份字节谁都找不回来"
    )

    # 写入侧的行为：旧文件**逐字节没变**（上一条判据的正面那一半）。
    assert first.read_bytes() == BODY.encode("utf-8")

    # 读回侧接上：两份证据各自读得回自己的那一份字节。
    old_page, old_reason = _read(settings, digest=old_digest)
    assert old_reason == "", old_reason
    assert old_page is not None and old_page.path == first
    assert old_page.head == BODY.encode("utf-8")

    new_page, new_reason = _read(settings, digest=sha256_file(second))
    assert new_reason == "", new_reason
    assert new_page is not None and new_page.path == second
    assert new_page.head == changed.encode("utf-8")


# --- 第三类写入方：复制到另一个主体的目录 -------------------------------------


def test_a_mirror_file_that_does_not_belong_to_this_url_is_not_copied(
    settings: Settings,
) -> None:
    """源文件名与这个 URL 配不上就**抛**，不复制。

    复制方是唯一一个「字节从别处来」的写入方，所以它比另外两个多一层责任：另外两个
    的字节是自己刚取回来的，名字也是自己刚算出来的，对不上是写错了代码；复制方的源
    路径是别人给的，对不上意味着**要复制的不是这个 URL 的材料**。

    放它过去的后果不是「多一个文件」：读回方按前缀在前缀候选里挑，而落点会带着这个
    URL 的前缀 —— 于是一条关于**这个** URL 的证据读回了另一个页面的字节，且散列校验
    通过（它就是那些字节的真散列）。这条判据钉的是「宁可拒绝也不复制一份不属于它的
    材料」，所以顺带断言接收方的目录**没有**被建出来。
    """
    source = _write(
        page_path(settings, scope=DONOR, url=OTHER_URL, extension=".html"), BODY
    )

    with pytest.raises(ValueError, match="不属于这个 URL"):
        copy_page(settings, source=source, scope=RECEIVER, url=URL)

    assert not account_page_dir(settings, RECEIVER).exists(), (
        "抛之前不该落下任何东西 —— 一次「先写后校验」的复制会留下一个孤儿文件，"
        "而它的名字带着这个 URL 的前缀"
    )


def test_a_file_whose_name_is_only_the_prefix_is_refused(settings: Settings) -> None:
    """前缀之后什么都没有的文件**不是**本模块算出来的名字，照样拒绝。

    第二道守卫与第一道不是同一件事：前缀对上只说明「它属于这个 URL」，不说明「它按
    本模块的约定命名」。自己拼一个扩展名的实现（比如一律 ``.html``）会在这里放过去，
    而它的代价落在读回侧 —— 两版页面挤回一个名字，正是 212 行挡下的那件事换个地方
    重演，且这一次的触发条件是「有人往目录里放了一个不带扩展名的文件」。
    """
    nameless = _write(account_page_dir(settings, DONOR) / page_prefix(URL), BODY)

    with pytest.raises(ValueError, match="没有扩展名"):
        copy_page(settings, source=nameless, scope=RECEIVER, url=URL)


def test_the_copy_lands_under_the_same_name_in_the_other_subjects_directory(
    settings: Settings,
) -> None:
    """复制**只换目录**，名字整段带走 —— 含版本段。

    名字里除了扩展名还可能有版本段：同一个 URL 的第二版叫
    ``<prefix16>.<sha16>.html``（:func:`page_version_path`）。所以复制方不自己挑
    扩展名，它把 :func:`page_prefix` 之后的**整段**留着。取其中一段自己拼回去的实现
    （或者干脆硬编 ``.html``）在下面这一条的第二步就会露出来：带版本段的那一份会被
    落到另一个名字上，而那个名字在那棵目录里没有对应的版本。

    落点读得回去是第三条断言：接收方的目录是**按主体分**的，所以「同名」指的是
    它自己那棵树下的同名文件。
    """
    changed = BODY + "<!-- second -->"
    request = FetchRequest(url=URL, scope=DONOR)
    first = HttpxFetcher(settings)._persist(
        request, BODY.encode("utf-8"), content_type="text/html", url=URL
    )
    versioned = HttpxFetcher(settings)._persist(
        request, changed.encode("utf-8"), content_type="text/html", url=URL
    )
    assert versioned != first, "第二版应当落在另一个名字上（否则这条判据测的是同一份）"
    assert versioned == page_version_path(
        settings, scope=DONOR, url=URL, bytes_sha256=sha256_file(versioned), extension=".html"
    )

    landed = copy_page(settings, source=versioned, scope=RECEIVER, url=URL)

    assert landed.parent == account_page_dir(settings, RECEIVER)
    assert landed.name == versioned.name, (
        "名字整段带走 —— 只剩扩展名的话，版本段会在接收方那一侧消失"
    )
    assert landed.read_bytes() == versioned.read_bytes()

    page, reason = _read(
        settings, account_id=RECEIVER, digest=sha256_file(versioned)
    )
    assert reason == "", reason
    assert page is not None and page.path == landed
    assert page.head == changed.encode("utf-8")


# --- 读回：按字节校验 ---------------------------------------------------------


def test_a_stored_page_reads_back_and_its_digest_is_of_the_file_bytes(
    settings: Settings,
) -> None:
    """写进去的字节，用**它自己的** sha256 取回来，逐字节相同。

    这里同时钉住散列的口径：``Evidence.content_sha256`` 来自
    ``ParsedDocument.sha256``，而那是 ``sha256_file`` —— **文件字节**的散列，
    不是解码后文本的（``FetchResult.body_sha256`` 才是文本口径的那个）。
    两个口径混用会让每一次读回都报「字节不符」，而那是把一次口径错误说成
    「材料被改写」。
    """
    path = _write(page_path(settings, scope=ACCOUNT, url=URL, extension=".html"), BODY)

    page, reason = _read(settings, digest=sha256_file(path))

    assert reason == ""
    assert page is not None
    assert page.path == path
    assert page.head == BODY.encode("utf-8")
    assert page.byte_length == len(BODY.encode("utf-8"))
    assert page.truncated is False


def test_a_page_whose_bytes_changed_is_not_returned(settings: Settings) -> None:
    """前缀相符、**字节不符**的文件不是这条证据的材料。

    这是本文件里最重要的那条。文件名的前缀是 URL 的散列，所以同一个 URL 上
    换过的内容、被截断的重写、被别的东西覆盖的同名文件，**名字全都对**。
    只按名字读文件的实现会把它们当成那次抓取的字节交出去 —— 而回执上
    「材料拿到了」与真的拿到了完全同形。
    """
    path = _write(page_path(settings, scope=ACCOUNT, url=URL, extension=".html"), BODY)
    stale = sha256_file(path)
    _write(path, "<html><body>someone.else@example.co.uk</body></html>")

    page, reason = _read(settings, digest=stale)

    assert page is None
    assert "散列" in reason, reason


def test_the_hash_decides_between_two_files_sharing_one_prefix(settings: Settings) -> None:
    """同 URL 的两个 tier 落在**同一个前缀**下，由字节决定读哪一个。

    ``pages/<account>/<hash16>.html``（直连）与 ``<hash16>.rendered.html``
    （浏览器）共享前缀 —— 这不是疏漏，是「同一次抓取的两个 tier」。所以读回
    必须在候选里按字节挑，不能在名字上做文章。
    """
    target = page_path(settings, scope=ACCOUNT, url=URL, extension=".html")
    _write(target, BODY)
    _write(target.with_suffix(".rendered.html"), "<html><body>rendered</body></html>")

    page, reason = _read(settings, digest=sha256_file(target))

    assert reason == ""
    assert page is not None
    assert page.path == target


# --- 两种「没有」必须分开 -----------------------------------------------------


def test_a_missing_directory_and_a_missing_digest_are_different_reasons(
    settings: Settings,
) -> None:
    """「这台机器没有这个账号的镜像」与「有这个 URL 的镜像、但字节不是那一次」。

    两句话指向两个方向：前者的处置是「换一台机器 / 换个部署」，后者是「这个 URL
    的内容在两次抓取之间变过，或者文件被换过」。合成一句话会让第一种故障被读成
    第二种，而第二种是**证据链的证伪信号** —— 把它误报成「材料不在」会把一条
    该被追查的事故注销掉。
    """
    _, no_dir = _read(settings, digest="deadbeef")

    _write(page_path(settings, scope=ACCOUNT, url=URL, extension=".html"), BODY)
    _, no_match = _read(settings, digest="deadbeef")

    assert no_dir and no_match
    assert no_dir != no_match, "两种「没有」被说成了同一句话"


def test_a_url_with_no_mirror_at_all_says_which_url(settings: Settings) -> None:
    """理由里要有可追查的东西 —— 一句「没找到」查不下去。"""
    _write(page_path(settings, scope=ACCOUNT, url=URL, extension=".html"), BODY)

    _, reason = _read(settings, url=OTHER_URL, digest="deadbeef")

    assert page_prefix(OTHER_URL) in reason, reason


# --- 有界 ---------------------------------------------------------------------


def test_the_read_is_bounded_but_the_length_is_the_true_one(settings: Settings) -> None:
    """回传有界，``byte_length`` 是**真实**大小，截断被标出来。

    只回传有界的头、并把头的大小当成产物大小，会让一份被截断的材料看起来
    正好就是这么长 —— 而「这个产物是不是完整的」正是读者要判断的事。
    """
    body = "x" * 5000
    path = _write(page_path(settings, scope=ACCOUNT, url=URL, extension=".txt"), body)

    page, reason = _read(settings, digest=sha256_file(path), max_bytes=64)

    assert reason == ""
    assert page is not None
    assert page.byte_length == 5000, "报的是头的大小，不是产物的大小"
    assert len(page.head) == 64
    assert page.truncated is True


def test_a_binary_artifact_is_not_decoded_as_text(settings: Settings) -> None:
    """非 UTF-8 的产物（PDF 那些）不当文本回传，也不猜编码。

    猜错的代价不是乱码，是一次**看起来成功了**的读取：调用方拿到一段替换字符，
    而没有任何字段说它坏了。
    """
    path = page_path(settings, scope=ACCOUNT, url=URL, extension=".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\n\xff\xfe\x00\x01binary\x80\x81")

    page, reason = _read(settings, digest=sha256_file(path))

    assert reason == ""
    assert page is not None
    assert as_text(page.head) is None
    assert as_text(b"plain ascii") == "plain ascii"


# --- 一条证据走回它的字节 -----------------------------------------------------


def test_an_evidence_row_locates_its_own_bytes(settings: Settings) -> None:
    """从**一行 Evidence** 出发能走回镜像字节 —— 这是这一整套的用途。

    上面每一条判据都自己算 digest 再读，所以它们证明的是「给定 digest 能读到」。
    这一条证明的是**证据行本身就够**：``account_id`` + ``source_url`` +
    ``content_sha256`` 三格齐了，材料就取得到。
    """
    written = HttpxFetcher(settings)._persist(
        FetchRequest(url=URL, scope=ACCOUNT),
        BODY.encode("utf-8"),
        content_type="text/html",
        url=URL,
    )
    document = HtmlDocumentParser().parse(written, content_type="text/html")
    evidence = document_evidence(
        # 血统取**部署配置**（``settings.profile``）—— 与 MCP 那一侧
        # （``marketops/adapters/mcp_server/research.py``）将来要传的是同一个值，
        # 也是 ``RoleGuard`` 判权的那个值。
        workspace=settings.profile,
        account_id=ACCOUNT,
        source_url=URL,
        document=document,
        fetched_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert evidence is not None

    page, reason = read_stored_page(
        settings,
        account_id=evidence.account_id,
        source_url=evidence.source_url,
        content_sha256=evidence.content_sha256,
        max_bytes=4096,
    )

    assert reason == ""
    assert page is not None
    assert page.head == BODY.encode("utf-8")
    assert "grounds@example.co.uk" in as_text(page.head)
