"""领域层的纯助手：字符串规范化与内容寻址。

本模块**没有副作用**，也不碰文件系统。它曾经不是这样的 —— 之前它还住着
``sha256_file``（要 ``pathlib``，要打开文件）。那一个 I/O 函数的存在，就是
「领域层能不能被无磁盘地测试」这个问题的全部答案：不能。拆出去之后，
``tests/test_domain_is_pure.py`` 才能要求领域层**只依赖自己**，而不是
「除了 util」。

``sha256_file`` 现在住 ``marketops/adapters/hashing.py`` —— 读文件是实现，
不是领域知识。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

#: 只保留跨平台安全的文件名字符（不含任何路径分隔符）。
_UNSAFE_FILENAME_CHARS = re.compile(r"[^a-z0-9._-]+")


#: **一切会被拼成文件名的 id 的长度上限。只在这里定义一次。**
#:
#: 它同时是「id 是不是规范形式」这条判据的参数，与 :func:`slugify` 是天生一对 ——
#: 判据的全部内容就是 ``slugify(value, max_length=FILENAME_ID_MAX_LENGTH) == value``。
#: 两个数各写一遍必然漂移，而漂移的那一次会让**校验通过的 id 在落盘时被截断**：
#: 校验看起来在保护一个它其实没保护的东西。
#:
#: 这个数不是从某份规格推出来的，是从两侧夹出来的：一侧是实际会出现的 id 形状
#: （账号 id、run id、场地资产 id，都远短于 60），另一侧是文件系统的 255 字节
#: 上限，``<id>.jsonl`` 加上后缀仍要留足余量。夹在中间的一段都行，所以重点不在
#: 「为什么是 60」，而在它**只有一个定义处**。
#:
#: 名字里没有 ``ACCOUNT`` 是刻意的：这个仓库里第一处落盘是账号证据
#: （``<account_id>.jsonl``，界曾经是 60），第二处是检索镜像的目录名（界曾经是
#: 40），第三处是 run 报告的
#: ``<run_id>.json``（界曾经是 80）。三个界、同一条判据 —— 前两个差 20，
#: 于是「按 40 截断落盘、按 60 读」这件事在纸面上看不出来。名字按**用途**取，
#: 三个消费者就都会来用同一个。
FILENAME_ID_MAX_LENGTH = 60


def slugify(value: str, *, max_length: int = 80, fallback: str = "unnamed") -> str:
    """把任意字符串转成跨平台安全的文件名片段。

    Windows 与 Linux 得到**同一结果**：先做 ASCII 折叠，再只保留
    ``[a-z0-9._-]``。任何 ``/`` ``\\`` ``:`` ``?`` ``*`` ``"`` ``<`` ``>`` ``|``
    都会被替换掉，因此结果不可能被当作路径分隔符解释。

    ``max_length`` 是**截断**，而截断不是一一映射 —— 用它的每一处都必须同时回答
    「两个不同的输入落到同一个名字上时会发生什么」。id 类字段的答案是
    :func:`check_canonical_id`：拒绝，不截断。
    """
    folded = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    slug = _UNSAFE_FILENAME_CHARS.sub("-", folded.lower()).strip("-._")
    slug = slug[:max_length].strip("-._")
    return slug or fallback


#: 会被文件系统或 slugify 误解释的字符。id 会被当作文件名片段用。
_UNSAFE_ID_CHARS = '/\\:*?"<>|'


def check_slug_safe(value: str, field: str) -> str:
    """id 类字段的公共校验：非空、无首尾空白、不含路径分隔符与保留字符。

    放在 util 而不是各模型里各写一遍，是因为账号 id、场地资产 id、责任实体 id
    都会被 ``slugify`` 变成文件名 —— 三处各写一份必然有两处先漂移。

    **这条比 :func:`check_canonical_id` 松，两者不是替代关系。** 它拦的是「这个值
    会让文件名变成路径或非法名字」；后者拦的是「这个值与它落盘用的名字不是一一
    对应」。一个含空格的 id（``My Account 001``）过得了这一条，过不了那一条 ——
    而证据是按 ``slugify(account_id)`` 定位的，所以放它过去 = 读到另一个账号的存档。
    """
    if not value or value != value.strip():
        raise ValueError(f"{field} 不能为空或首尾带空白")
    if any(ch in value for ch in _UNSAFE_ID_CHARS):
        raise ValueError(f"{field} 不得含路径分隔符或保留字符（会被用作文件名）")
    return value


def check_reference(value: str, field: str) -> str:
    """**引用**类字段的公共校验：非空、无首尾空白、不含控制字符。

    与 :func:`check_slug_safe` **刻意不同**，而且不是更松的那种不同：这两条拦的是
    两件事。

    * ``check_slug_safe`` 拦的是「这个值会让文件名变成路径或非法名字」，所以它禁
      掉 ``:`` ``/`` ``*`` 这些字符。用它去校验一个**引用**是错的：
      ``account:acme`` / ``view:v1`` / ``workspace/run`` 都是本仓真实存在的引用
      形状，而它们一个都不是文件名。把这条判据套上去，第一个要写
      ``account:acme`` 的人会拿到一句「会被用作文件名」——那句话对他做的事没有
      任何解释力，而他接下来会做的下一件事是换个名字或者把校验删掉。
    * 这条拦的是「这个值不能当一个引用字段」，具体的规则只有一条承重的：
      **不得含 U+001F**（:data:`SEP`）。它是内容寻址 id 的分段分隔符
      （``job_id`` / ``binding_id`` 那一族），引用里出现它会让
      ``("a" + SEP + "b", "c")`` 与 ``("a", "b" + SEP + "c")`` 拼成同一个 id ——
      两个不同的对象获得同一个 id，而 id 决定的是这次写入落在哪一行上。

    其余控制字符一并不收：它们在任何一种回执里都读不出来，而一个读不出来的引用
    无法被人工核对。
    """
    if not value or value != value.strip():
        raise ValueError(f"{field} 不能为空或首尾带空白（收到 {value!r}）")
    bad = [ch for ch in value if ord(ch) < 0x20 or ord(ch) == 0x7F]
    if bad:
        raise ValueError(
            f"{field} 含控制字符（{sorted({ord(ch) for ch in bad})}）：{value!r}。"
            f"U+001F 是内容寻址 id 的分段分隔符，引用里出现它会让两个不同的对象"
            f"拼出同一个 id —— 而 id 决定这次写入落在哪一行上。"
        )
    return value


def check_canonical_id(
    value: str, field: str, *, max_length: int = FILENAME_ID_MAX_LENGTH
) -> str:
    """id 必须是 ``slugify`` 的**不动点**：不满足就抛，**不截断**。

    这是「id → 文件名」这条映射的全部判据。它成立时，``slugify`` 是一个恒等
    函数，于是「候选 id 的集合」与「文件名的集合」是同一个集合 —— 没有两个 id
    能落进同一个文件，也没有 id 会在落盘时被悄悄改写。

    **为什么是拒绝而不是规范化。** 一个会替调用方把 ``My Account 001`` 改写成
    ``my-account-001`` 的实现，读起来更友好，但它把一次错误变成了两个不同的 id
    静默合并：台账行数不变、报告上只显示「证据更多了」。而这个仓库里
    ``AccountRecord.account_id`` 早就有一条更松的校验（slug-safe），所以改成
    「落盘时规范化」会让同一个 id 在两个模型里获得两种命运。

    ``max_length`` 与 :func:`slugify` 是同一个参数、同一个缺省值 —— 判据与落盘
    共用它，见 :data:`FILENAME_ID_MAX_LENGTH`。
    """
    if not value or value != value.strip():
        # 交给同一个函数报错，免得两条判据对「空值」给出两种说法。
        return check_slug_safe(value, field)
    canonical = slugify(value, max_length=max_length)
    if canonical != value:
        raise ValueError(
            f"{field} 不是规范形式：{value!r} 会被规范成 {canonical!r}。"
            "它会被当作文件名用，而需要规范化的 id 会与另一个 id 落到同一个文件上 "
            "—— 两个不同的账号静默合并，报告上只会显示「证据更多了」。"
            f"请改用 {canonical!r}（或另一个不需要改写的 id）。"
        )
    return value


#: 国家：ISO-3166 alpha-2，**大写**。
_MARKET_COUNTRY = re.compile(r"[A-Z]{2}")

#: 语言：BCP-47 的**主语言子标签**，小写，两个或三个字母。
#:
#: 刻意**不收** ``en-GB`` 这种带地区子标签的写法 —— 地区部分是 ``country`` 那一格
#: 的事。收下它，两个轴就合成一格了（见 :func:`check_market_language`）。
_MARKET_LANGUAGE = re.compile(r"[a-z]{2,3}")


def check_market_country(value: str, field: str) -> str:
    """问的是**哪个国家的索引**。大写两位 ISO-3166。

    **为什么是拒绝而不是 ``upper()``。** 这一格进幂等键与检索镜像的文件名（见
    ``application/billed_search.py`` 与 ``adapters/serper.py``），所以 ``gb`` 与
    ``GB`` 是两个不同的值、两条不同的账 —— 归一化的实现在盘上留下一份账，
    而调用方以为自己写的那个写法被记下了。与 :func:`check_canonical_id` 同一条
    理由：拒绝，不替调用方改写。
    """
    if not _MARKET_COUNTRY.fullmatch(value):
        raise ValueError(
            f"{field} 必须是两位大写 ISO-3166 国家码（收到 {value!r}）。"
            "它问的是**哪个国家的索引**，会进幂等键与镜像文件名 —— "
            "小写写法不是它的等价物，是另一个值、另一条账。"
        )
    return value


def check_market_language(value: str, field: str) -> str:
    """问的是**哪种语言**。只收主语言子标签（``en``），不收 ``en-GB``。

    **国家 ≠ 语言**是一条被写进类型的规则，不是一个提醒。``en-GB`` 里的地区部分
    属于 :func:`check_market_country` 那一格：收下 ``en-GB`` 就等于把「英国 =
    英语」这句话焊进这一格，于是「法国、英文」「英国、波兰语」这两种真实存在的
    组合在类型上表达不出来 —— 而它们在报告上会显示成一个正常的检索。
    """
    if not _MARKET_LANGUAGE.fullmatch(value):
        raise ValueError(
            f"{field} 必须是主语言子标签（两位或三位小写字母，收到 {value!r}）。"
            "地区部分不属于这一格 —— 它属于 country 那一格。"
            "两个轴合成一格之后，「法国、英文」这样的组合就没有表达方式了。"
        )
    return value


def primary_language_subtag(locale: str) -> str:
    """从一条 BCP-47 语言标签里取**主语言子标签**：``en-GB`` → ``en``。

    只取第一段，**不推断**：``zh-Hant-TW`` 得到 ``zh``。这一层不做「这个语言在
    那个国家的首选写法是什么」这类判断 —— 那是业务规则，住在 profile 的声明里，
    而这里只是把一条已经声明过的标签拆开。
    """
    return locale.split("-")[0].lower()


def sha256_text(value: str) -> str:
    """文本的 sha256。统一用 UTF-8 编码，避免平台默认编码差异。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """字节的 sha256。

    **与 :func:`sha256_text` 不是可以互相替代的两个入口：** 散一份**文件**用它，
    散一份**在内存里的文本**用上面那个。区别不是风格 —— 一份带 BOM 或 CRLF 的
    工件，散文本与散字节给出两个不同的值，而「读的是哪一份字节」这个问题要的
    是后一个。取前一个会让「同名文件内容换过」这件事在报告上看起来没发生过。
    """
    return hashlib.sha256(value).hexdigest()



#: 内容寻址 id 的**分段分隔符**（ASCII 单元分隔符，U+001F）。
#:
#: 不用 ``:`` 或 ``/``：分段本身就可能含它们（scope 是 ``<profile>:<run>`` 这类
#: 形状），于是 ``("a:b", "c")`` 与 ``("a", "b:c")`` 会拼成同一个字符串 —— 两件
#: 不同的东西获得同一个 id，而 id 决定的是「幂等键指向哪一条记录」。
#:
#: 它同时是一个**控制字符**，不可能出现在用户输入里，所以拼接天然无歧义 ——
#: 这条性质是「不转义也不会有歧义」的依据，不是风格偏好。
#:
#: 放在这里而不是各模块各定义一份：``domain/budget.py`` 的 ``reservation_id``
#: 与 ``domain/job.py`` 的 ``job_id`` / ``work_item_id`` / ``attempt_id`` 是同一条
#: 推导规则的四次应用，分隔符是它共同的那一个输入。
SEP = "\x1f"


def utc_now() -> datetime:
    """带时区的当前时间。全项目禁止 naive datetime。"""
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    """把一个带时区的时刻规范到 UTC。**同一个瞬间，不同写法。**

    为什么每个时刻字段的校验器都要过一遍这里：本仓把时刻写进库里是**格式化成
    ISO-8601 字符串**存（``adapters/sqlite`` 各表），而字符串的大小顺序只在
    偏移量相同时才等于瞬间的先后。``2026-09-23T09:00:00+08:00`` 与
    ``2026-09-23T02:00:00+00:00`` 是同一个瞬间，前者按字符串排却在**后面** ——
    于是「取最近一次观测」会取到一个更早的，而没有任何一处会红。

    只要求「带时区」是不够的：它挡住了 naive，却没挡住偏移量。规范化的位置选在
    校验器而不是存储层，是为了让**所有**实现（SQLite、JSONL、内存替身）拿到同一个
    值 —— 放在某一处存储里，契约测试就会在两个实现之间读出不同的时刻。
    """
    if value.tzinfo is None:
        raise ValueError("naive datetime 不得进入本系统：见 utc_now 的说明")
    return value.astimezone(timezone.utc)


def normalise_email(value: str) -> str:
    """规范化邮箱用于**比较**。

    只做两件不改变语义的事：去空白、域名小写。
    本地部分保持原样 —— 它在 RFC 5321 下是大小写敏感的，随意 lower()
    会造成错误归并（这也是「false attribution」的一种来源）。
    """
    candidate = value.strip().strip(".,;:()<>[]\"'")
    if "@" not in candidate:
        return candidate
    local, _, domain = candidate.rpartition("@")
    return f"{local}@{domain.lower()}"


def is_plausible_email(value: str) -> bool:
    """最基本的形态校验。**不做** SMTP 验证、**不做**猜测补全。"""
    candidate = normalise_email(value)
    local, sep, domain = candidate.partition("@")
    if not sep or not local or "." not in domain:
        return False
    if candidate.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")):
        return False
    return True


__all__ = [
    "slugify",
    "FILENAME_ID_MAX_LENGTH",
    "check_canonical_id",
    "check_reference",
    "check_slug_safe",
    "sha256_text",
    "sha256_bytes",
    "SEP",
    "utc_now",
    "to_utc",
    "normalise_email",
    "is_plausible_email",
]
