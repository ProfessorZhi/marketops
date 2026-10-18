"""母池的读入：把上游交进来的一份导出读成一份 :class:`SourceReceipt`。

它填的是 §2 里 ``JobInputKind.SOURCE_RECEIPT`` 那个洞 —— 在那之前这个种类
**没有生产者**，而 ``marketops/application/inputs.py`` 的 ``_NO_PRODUCER``
把这件事写成了一条阻塞理由。现在它有了：**回执是一份文件**
（``inputs_dir/<receipt_id>.receipt.json``），于是它带着一个名字、一个内容摘要
与一份内容重新进入受理。

形状与不变量在 ``marketops/domain/receipt.py``（本模块只**读、判、写**，
不重新定义任何一种处置）。

# 这里要解决的是什么

Member-C 的原话（``sources/member-c-private`` pos30）：「纯 osm 只有代码 id，
很少能跑到具体场地名称，**所以还需要在 osm 基础上补齐**」。

也就是说：母池的常态是**一份不完整的导出**。要求它先被人工整理成完整 Account
才能进系统，等于把「补齐」这一步挡在门外 —— 而补齐正是要在这里发生的事。
所以本模块的目标不是「把池子读干净」，是**给每一行一个说得出来的去向**：

* 有真名字的 → ``resolved``（这一行现在可以去研究）
* 只有标识、没有名字的 → ``pending``（**还缺身份补全**，而这个缺是一个记录）
* 同一个上游对象出现了两次 → 后一条 ``merged`` 到前一条
* 按 profile 已声明的排除项命中的 → ``excluded``
* 那一行读不出来 → ``failed``（带得到底哪里读不出来）

五种加起来**必须等于原始行数**，由 :class:`SourceReceipt` 的序号判据执行。

# 只在「上游自己的标识」上归并，**不在名字上**归并

两个没有标识、名字与地点都一样的行**不合并不**。「同名同地点是同一个东西」
是一个**判断**，而这一层拿不出证据支持它 —— 英国一个村子里两个不同单位共用
一个名字和同一个邮编是常见的。

代价是同一块场地可能进来两行，于是下游多做一次研究。但另一个方向的代价更大：
两条**本来不同**的行被并成一条，那条丢掉的行在报告里只剩下一个 ``merged``，
而「它到底是不是同一个」这个问题再也没有答案了。

这与 ``marketops/application/pairing.py::keys_with_multiple_organisations``
是同一条纪律：**看起来像的，报告，不解决。** 配对键（canonical host）才是
「同一个单位」被判断的地方，而那一步有证据。

# 一个叫 ``id`` 的列**不是**来源标识

这条值得单独写，因为它是这里最容易做错的一步。一份导出里有一个叫 ``id`` 的
列，它可能是主键，也可能是行号，还可能是**类别码** —— 五十行共享同一个
``id: "golf"`` 是完全正常的。拿它当归并键，那五十行会合成一行，
而报告上只显示 ``merged=49``：一条看起来像「去重做得很干净」的读数。

所以来源标识只从两种地方取：

1. **显式命名的列**（``source_identity`` / ``source_id`` / ``identifier`` /
   ``registry_id``）—— 上游自己说这是它的标识；
2. **「对象类型 + 编号」那一对**（``type`` 与 ``id`` 同时存在，且 ``type``
   取的是那套按对象类型编号的取值）—— 拼成 ``<type>:<id>``。

一个裸 ``id`` 列什么都不构成。它仍然逐字留在 ``attributes`` 里，所以什么都没丢：
丢的只是「拿它当归并键」这个动作。

# 排除项是**输入**，不是引擎的词表

``marketops/`` 整包被 ``tests/test_no_hardcoded_market_knowledge.py`` 管着 ——
引擎里不许有一个市场词。所以「排除」这件事只能由两个东西相遇发生：**来源行说**
它命中了哪几项，**profile 声明**过哪几项。

于是 ``excluded`` 的 ``exclusion_ids`` 必须逐条落在调用方传进来的
``declared_exclusion_ids`` 里。**落不进去就整份拒绝（``ConfigError``），
不降级、不跳过**：

* 把它当 ``excluded`` 收下，等于让输入**发明**一条 profile 从没声明过的规则 ——
  而这条规则会静默地从池子里删行，报告上只显示 ``excluded=1``。
* 把它记成这一行 ``failed``，则会把一次**导出与 profile 版本不匹配**渲染成
  「这一行读坏了」。三百行全部失败的读数看起来像数据烂，而真正要修的是词表 ——
  两种处置相反，报告必须分得开。

# 读出来的东西原样留着，不在这里二次加工

每一行的**全部**列逐字进 ``attributes``（连嵌套的一层也展开后留着），
原始工件本身留在 ``inputs_dir`` 下、由回执的 ``artifact_ref`` + ``artifact_sha256``
指着。所以「补齐」那一步将来要回头看的原始列，都在。

**解析不出来的 ``official_domain`` 只是让这一格为空**，不降级这一行：一个
列值不能解析成主机名（``https://a.co.uk`` 之外的怪形状）是一列的问题，
而这一行有一个真名字，它就是一个可研究的主体。原值在 ``attributes`` 里，
比对得出来。判据是名字，不是那一列。
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from ..domain.enums import DomainVerdict
from ..domain.errors import ConfigError
from ..domain.receipt import (
    RECEIPT_VERSION,
    Disposition,
    RawCandidateRow,
    RowDisposition,
    SourceReceipt,
    dispositions_digest,
    identity_tail,
    receipt_id_for,
    row_id_for,
    verify_receipt_id,
)
from ..domain.util import check_canonical_id, sha256_bytes, utc_now

#: 工件的后缀，按**优先级**排列。``find_artifact`` 逐个试；同时命中两个以上是
#: 一次歧义（见那里），不是一次「随便挑一个」。
ARTIFACT_SUFFIXES: tuple[str, ...] = (".jsonl", ".csv", ".yaml", ".yml", ".json")

#: 回执文件的后缀。文件名是 ``<receipt_id>.receipt.json`` —— id 与文件名的主干
#: 是同一个名字，所以「盘上这份回执是哪一个 id」这个问题不靠读内容回答。
RECEIPT_SUFFIX = ".receipt.json"

#: 读文本时用的编码。**``utf-8-sig`` 不是 ``utf-8``**：Excel 与不少导出工具会带 BOM，
#: 而 BOM 会让 CSV 的第一个表头变成 ``"﻿name"`` —— 于是 ``name`` 那一列
#: **认不出来**，整池行全部落进 ``pending``，而报告上看不出发生过什么。
#: 对不带 BOM 的文件，两者逐字节相同。
_TEXT_ENCODING = "utf-8-sig"

#: 显式命名的来源标识列。顺序即优先级。
IDENTITY_KEYS: tuple[str, ...] = (
    "source_identity",
    "source_id",
    "identifier",
    "registry_id",
)

#: 「对象类型 + 编号」那一对里的类型列。取值限定在**按对象类型编号的那套词汇**里 ——
#: 一个表的 ``type`` 列写着 ``club`` 时不该被拼成一个标识。
TYPE_KEYS: tuple[str, ...] = ("type", "osm_type", "object_type")
ID_KEYS: tuple[str, ...] = ("id", "osm_id", "object_id")
OBJECT_TYPES: frozenset[str] = frozenset({"node", "way", "relation", "area"})

#: 名称列的别名。**第一个非空且「像一个名字」的**胜出（判据见 :func:`_usable_name`）。
NAME_KEYS: tuple[str, ...] = (
    "name",
    "venue_name",
    "organisation",
    "organization",
    "site_name",
    "club_name",
    "title",
)

#: 地点列的别名。它与名字一起决定这一行**看起来**是哪个单位 —— 但**不参与归并**
#: （见模块 docstring）。它进 ``resolved_location``，是给人读与给下游参考的一格。
LOCATION_KEYS: tuple[str, ...] = (
    "location",
    "venue_location",
    "town",
    "city",
    "county",
    "region",
    "area",
    "locality",
    "place",
    "address",
)

#: **转述**的域名列的别名。取第一个能过 ``canonical_host`` 的。
#:
#: 这些列说的都是「**别人**说这一行的官网是哪一个」：登记册、导出、上游清单。
#: 「我们自己在哪儿读到的这一行」是另一格（:data:`DISCOVERED_HOST_KEYS`），
#: 两者**都读进来**、分别落格 —— 合成一格的那天，「我们看到的」与「别人说的」
#: 在下游长得一样，而下落相反（审计里那条「SERP 抓回来的主机被读成『这是它的官网』」）。
DOMAIN_KEYS: tuple[str, ...] = (
    "official_domain",
    "domain",
    "website",
    "web",
    "homepage",
    "url",
)


#: 排除项列的别名。值可以是列表，也可以是一段用 ``,`` ``;`` ``|`` 分开的文本。
EXCLUSION_KEYS: tuple[str, ...] = ("exclusions_hit", "exclusions", "excluded")

#: 资产事实的列名（§2）。**这一份是精确的、没有别名**：它们与
#: ``marketops/domain/facts.py`` 的事实名逐字同名，而别名的每一次出现都是一次
#: 「这一列到底是不是那个事实」的判断 —— 判错的那一次不会有症状，
#: 只会让某一格永远是空的。
ASSET_FACT_KEYS: tuple[str, ...] = (
    "organisation_kind",
    "venue_environment",
    "surface_class",
    "country",
)


@dataclass(frozen=True)
class _ReadRecord:
    """工件里的一行：要么是一个对象，要么是**读不出来的原因**。

    两种都占一个序号。丢掉读不出来的那一行会让整份回执的序号不连续，
    而那正是「每一原始行都有一个处置」这句话要防的事。
    """

    payload: Mapping[str, object] | None
    #: 读不出来时空，否则一句话说清哪里读不出来（含原始行的文本）。
    error: str = ""
    #: 这一行的原始文本 —— 读不出来时它是身份的**唯一**来源（见 :func:`row_id_for`）。
    text: str = ""


@dataclass(frozen=True)
class _Candidate:
    """一行读出来之后、还没判定去向的中间态。

    它**不落盘**：回执里只有处置，原始行在工件本身里（由 ``artifact_ref`` +
    ``artifact_sha256`` 指着）。所以这一层不需要把它序列化，也就不需要为
    「序列化出来的形状」再定一次契约。
    """

    row: RawCandidateRow
    name: str
    location: str
    domain: str
    #: **我们在哪台主机上读到了这一行** —— 一次观察。空 = 这一份工件没有这一栏
    #: （母池那种形状的导出就没有），不是「没有主机」。
    discovered_host: str = ""
    #: 我们在**哪一页**读到的（整条 URL）。与上一格成对：只有主机名而没有 URL 的行，
    #: 事后答不出「你在哪儿看到的」。
    discovered_url: str = ""
    #: **我们证得的**官网集合与它的判定（``domain/domain_attribution.py`` 的产出）。
    #: 这三格是 §4.2 那条链上**唯一**的账号域名权威；上面的 ``domain`` 只是转述。
    verified_official_domains: tuple[str, ...] = ()
    domain_verdict: DomainVerdict = DomainVerdict.UNKNOWN
    domain_evidence_ids: tuple[str, ...] = ()
    domain_reason: str = ""
    exclusions: tuple[str, ...] = ()
    #: **资产事实**（§2）。与 ``exclusions`` 同一条纪律：它们是这一行上的**列**，
    #: 不是这一层判出来的东西 —— 取值是谁写进工件的，就由谁负责（发现期那一侧是
    #: ``application/classification.py``，而它的取值词表住 profile）。
    #: 本层只做一件事：把它们原样搬到 ``RowDisposition`` 上，让场地资产那一行
    #: 拿得到。空串 = 这一份工件没有这一栏（母池那种形状的导出就没有）。
    organisation_kind: str = ""
    venue_environment: str = ""
    surface_class: str = ""
    country: str = ""
    #: 逐字留存的那一整行（喂给 ``attributes`` 也在里面）。
    attributes: Mapping[str, str] = field(default_factory=dict)

    @property
    def row_id(self) -> str:
        return self.row.row_id

    @property
    def ordinal(self) -> int:
        return self.row.ordinal

    @property
    def source_identity(self) -> str:
        return self.row.source_identity


# --- 找工件 -------------------------------------------------------------------


def find_artifact(inputs_dir: Path, artifact_ref: str) -> Path:
    """按**名字**找原始工件；``inputs_dir`` 下、后缀逐个试。

    与 ``inputs.py::known_account_dossiers`` 同一条纪律：收一个**名字**，
    不收一个路径。收路径等于把「读哪个文件」交给调用方 —— 一次跨机器部署下，
    那就是一条越权读盘的接口。

    Raises:
        ConfigError: 一个都没有，或**同时命中两个以上**。后者是一次歧义：
            同一个名字下既有 ``.csv`` 又有 ``.yaml`` 时，「读的是哪一份」没有
            答案，而随便挑一个的后果是报告上出现一份**没人选的**输入的读数。
    """
    checked = check_canonical_id(artifact_ref, "artifact_ref")
    found = [
        candidate
        for suffix in ARTIFACT_SUFFIXES
        if (candidate := inputs_dir / f"{checked}{suffix}").is_file()
    ]
    if not found:
        tried = "、".join(f"{checked}{suffix}" for suffix in ARTIFACT_SUFFIXES)
        raise ConfigError(
            f"在 {inputs_dir} 下找不到工件 {checked!r}；试过：{tried}。\n"
            "母池的工件也放在 inputs_dir 下，并且用**名字**引用 —— 与其它的输入"
            "清单同一条约定（一个收路径的入口等于让调用方决定服务去读哪一份文件）。"
        )
    if len(found) > 1:
        raise ConfigError(
            f"工件名 {checked!r} 在 {inputs_dir} 下命中了不止一份："
            f"{[path.name for path in found]}。\n"
            "同时存在两份时「读的是哪一份」没有答案，而随便挑一份的后果是"
            "报告上出现一份**没人选过的**输入的读数。请只留一份。"
        )
    return found[0]


# --- 读工件 -------------------------------------------------------------------


def read_artifact(path: Path) -> list[_ReadRecord]:
    """把一份工件读成一串 :class:`_ReadRecord`，**按文件顺序**。

    顺序有意义：它决定 ``ordinal``，因而决定 ``row_id``，因而决定
    ``receipt_id``。重排同一个工件会被读成另一批（与 ``known_account_dossiers``
    对清单顺序的态度一致）。

    一行读不出来只在 ``.jsonl`` 上是**一行**的问题（那种格式本来就是一行一条），
    在整份文档格式上它是**整个文件**读不出来 —— 后者抛 ``ConfigError``：
    一份坏掉的 YAML 不是「三百行里有一行不行」，它是一份读不出来的文件。
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _read_jsonl(path)
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in (".json", ".yaml", ".yml"):
        return _read_document(path, suffix)
    raise ConfigError(
        f"不认识的后缀 {suffix!r}（{path.name}）；能读的是 {list(ARTIFACT_SUFFIXES)}。"
    )


def _read_jsonl(path: Path) -> list[_ReadRecord]:
    records: list[_ReadRecord] = []
    for line in path.read_text(encoding=_TEXT_ENCODING).splitlines():
        if not line.strip():
            # 空行**不是**一行数据：它没有内容可处置，把它算进来会让
            # ``raw_row_count`` 变成一个包含排版空行的数，而那个数会被拿去
            # 当分母。所以空行跳过 —— 这是**跳过**，不是**丢掉**：它从来不是数据。
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append(_ReadRecord(payload=None, error=f"这一行不是合法 JSON：{exc}", text=line))
            continue
        if not isinstance(payload, Mapping):
            records.append(
                _ReadRecord(
                    payload=None,
                    error=f"这一行是 {type(payload).__name__}，不是一个对象",
                    text=line,
                )
            )
            continue
        records.append(_ReadRecord(payload=payload, text=line))
    return records


def _read_csv(path: Path) -> list[_ReadRecord]:
    """CSV 逐行读。**每一行都是一行数据**，包括读出来是空的那一行。

    与 ``.jsonl`` 的空行处理不同，理由在格式本身：CSV 里一条记录占一行，
    而分隔符已经保证了「行」这个单位；一个所有列都为空的 CSV 行是一条
    **内容为空的记录**，它有资格得到自己的处置。JSONL 的空行则连记录都不是。
    """
    with path.open("r", encoding=_TEXT_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            _ReadRecord(payload={str(key): value for key, value in row.items() if key is not None})
            for row in reader
        ]


def _read_document(path: Path, suffix: str) -> list[_ReadRecord]:
    """``.json`` / ``.yaml`` / ``.yml``：整份文档是一个列表，或一个带 ``rows`` 的对象。

    只认这两种形状。「一个对象就是一行」也说得通，但那时「一份单行的导出」
    与「一份读错了的导出」长得一样 —— 而这两种的处置相反。
    """
    raw = path.read_text(encoding=_TEXT_ENCODING)
    try:
        document = json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path.name} 读不出来：{exc}") from exc

    if isinstance(document, Mapping):
        if "rows" not in document:
            raise ConfigError(
                f"{path.name} 是一个对象，而它既不是一份列表，也没有 rows 那一栏。\n"
                "只有这两种形状被接受：「整份文档是一串行」或「一整份文档是一个"
                "带 rows 的对象」。「一个对象就是一行」也说得通 —— 但那时一份"
                "**单行**的导出与一份**读错了**的导出长得一样，而这两种的处置相反。"
            )
        document = document["rows"]
    if not isinstance(document, Sequence) or isinstance(document, str):
        raise ConfigError(
            f"{path.name} 的顶上一层不是一串行，而是 {type(document).__name__}。"
        )

    records: list[_ReadRecord] = []
    for index, item in enumerate(document):
        text = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(item, Mapping):
            records.append(_ReadRecord(payload=item, text=text))
        else:
            records.append(
                _ReadRecord(
                    payload=None,
                    error=f"第 {index} 项不是对象，而是 {type(item).__name__}",
                    text=text,
                )
            )
    return records


# --- 一行 → 一个候选 -----------------------------------------------------------


def _as_text(value: object) -> str:
    """列值 → 文本。**逐字**，不做规范化。

    ``None`` → ``""``（上游那一格是空的）；布尔 → ``true``/``false``（Python 的
    ``True`` 在报告里读起来像代码，而上游写的是 JSON）；``2.0`` 这种整数值的
    浮点还原成 ``2``（导出工具常常把编号写成浮点，留着小数点会让同一个 id
    在两次导出之间变成两个 —— 一次静默的重复）。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def flatten(record: Mapping[str, object]) -> dict[str, object]:
    """把一行摊平成「列名 → 值」，**展开一层嵌套**。

    嵌套是常态（按对象类型编号的那些导出把全部业务列放在 ``tags`` 里），
    而 ``tags.name`` 与 ``name`` 必须同等地被认出来。

    展开的规则是确定的，且**不丢东西**：

    * 嵌套里的每一格同时以 ``<外层名>.<内层名>`` 与 ``<内层名>`` 出现；
    * 限定名（带点的那个）永远保留，所以两个不同的嵌套块里有同名格这件事
      在盘上看得见，而不是被谁覆盖；
    * 裸名那一格取**外层同名格优先**，外层没有时取先出现的那个嵌套格。
    """
    flat: dict[str, object] = {}
    for key, value in record.items():
        name = str(key)
        if isinstance(value, Mapping):
            for inner_key, inner_value in value.items():
                inner = str(inner_key)
                flat.setdefault(f"{name}.{inner}", inner_value)
                flat.setdefault(inner, inner_value)
        else:
            flat[name] = value
    return flat


def _first_text(flat: Mapping[str, object], keys: Iterable[str]) -> str:
    for key in keys:
        if key in flat:
            text = _as_text(flat[key]).strip()
            if text:
                return text
    return ""


def _as_canonical_host(value: str) -> str:
    """一个域名列 → canonical host，或者空串。

    **解析不出来只是让这一格为空**，不降级这一行：一个列值不能解析成主机名是一列
    的问题，而这一行有一个真名字，它就是一个可研究的主体。原值在 ``attributes`` 里，
    比对得出来。判据是名字，不是那一列。

    迟到 import：``pairing`` 是本模块的**同一个**应用层邻居，而它在 ``canonical_host``
    上有一整套写下来的规则（scheme、端口、``www.``、IDN、标签语法）。自己再写一份会
    立刻与它漂移。
    """
    text = value.strip()
    if not text:
        return ""
    from .pairing import canonical_host

    try:
        return canonical_host(text)
    except ValueError:
        return ""


def _as_url(value: object) -> str:
    """一条 URL 列 → 原样（去过首尾空白），或者空串。

    **没有 scheme 的不收**：``RowDisposition`` 那一格要求它是一条能重放的观察
    （「你在哪儿看到的」），而一个裸主机名答不了那个问题 —— 它答的是
    ``discovered_host`` 那一格的问题。收下它，观察那两格就会有一格是假的。
    """
    text = _as_text(value).strip()
    return text if "://" in text else ""


def _domain_facts(
    flat: Mapping[str, object], *, ordinal: int
) -> tuple[tuple[str, ...], DomainVerdict, tuple[str, ...], str]:
    """官网判定那四列 → ``(集合, 判定, 证据 id, 理由)``。

    ## 与 ``official_domain`` 那一列**相反**的处置

    域名那一格解析不出来只让它为空（见 :func:`_as_canonical_host`），因为那一格是
    **信息**。这四格不一样：它们是绑定那一层（SOP L121）**唯一**的账号域名权威，
    而「读不出来 ⇒ 当成 unknown」有一个具体的后果 —— 一条**证得过的**外来域拒信
    会静默消失，另一家组织的邮箱于是被归到目标账号上，而报告上那一行完全正常。

    所以这里**整份拒绝**，与「命中了未声明的排除项」同一条纪律、同一个理由：
    收下一个读不懂的判定值等于让输入发明一条本仓从没声明过的规则。而这几列今天
    只有一个写者（发现期的 ``_artifact_row``），读到认不出的值说明工件与读取器
    不是同一版 —— 那要修的是版本，不是这一行。
    """
    verdict_text = _as_text(flat.get("domain_verdict", "")).strip()
    verified = _as_list(flat.get("verified_official_domains", ""))
    evidence = _as_list(flat.get("domain_evidence_ids", ""))
    reason = _as_text(flat.get("domain_reason", "")).strip()
    if not verdict_text and not verified and not evidence:
        # 这一份工件没有官网这一栏（母池那种形状的导出就没有）。**缺席**，
        # 不是「判定为 unknown」—— 两者在回执上由 ``domain_reason`` 那一格分开。
        return (), DomainVerdict.UNKNOWN, (), ""
    if not verdict_text:
        raise ConfigError(
            f"第 {ordinal} 行有官网域名或域名证据，却没有 domain_verdict 这一列。\n"
            "一个没有判定的域名集合与「查过了、确实没有官风」在回执里长得一样，"
            "而两者的处置相反。补齐这一列（发现期的工件会写它），或者去掉另外两列。"
        )
    try:
        verdict = DomainVerdict(verdict_text)
    except ValueError as exc:
        known = [member.value for member in DomainVerdict]
        raise ConfigError(
            f"第 {ordinal} 行的 domain_verdict 是 {verdict_text!r}，不在 {known} 里。\n"
            "把它读成 unknown 会让一条**证得过的**外来域拒信静默消失 —— 另一家组织的"
            "邮箱于是被归到目标账号上，而报告上那一行完全正常。整份拒绝。"
        ) from exc

    # 「证得的域名」这是**权威**那一格，所以它逐项严格：写坏了一项就整份拒绝。
    # 与 :func:`_as_canonical_host` 对 ``official_domain`` 那一格（解析不出来就留空）
    # 相反，方向也不是同一条：**丢掉一项会把集合缩小**，而集合正是绑定那一层判
    # 「这个邮箱域算不算外来」的依据 —— 于是本账号自己官网上登着的邮箱会被拒成
    # foreign_domain，而报告上那一行看起来只是「域名少了一个」。
    hosts: list[str] = []
    for item in verified:
        host = _as_canonical_host(item)
        if not host:
            raise ConfigError(
                f"第 {ordinal} 行的 verified_official_domains 里有 {item!r}，它不是主机名。\n"
                "留着它没有意义，扔掉它则会让这一行的域名集合变小 —— 而集合是判"
                "「这个邮箱域算不算外来」的依据：小掉一个，本账号自己官网上登着的邮箱"
                "就会被拒成外来域，报告上看起来只是「域名少了一个」。整份拒绝。"
            )
        hosts.append(host)
    return tuple(hosts), verdict, evidence, reason


def _has_alpha(value: str) -> bool:
    """这份文本里有没有一个**字母**（含中日韩等文字，它们也是 ``isalpha``）。

    「一个名字至少要有一个字母」这条判据挡的是纯数字与纯符号的值。它不判
    「这个名字好不好」—— 那不归这一层管。它只回答「这一格能不能读成一个指称
    对象」：``12345`` 与 ``--`` 读不出来。
    """
    return any(char.isalpha() for char in value)


def usable_name(name: str, source_identity: str) -> str:
    """这一格名称能不能当指称对象用。能用返回它，不能用返回 ``""``。

    三种情况下这一格**不是**一个名字：

    1. 空的（上游没给）—— ``""`` 是缺席，不是「上游说它没有名字」；
    2. 一个字母都没有（``12345`` / ``--``）；
    3. 它**就是**那个来源标识，或标识的取值那一段（``node/12345`` 那一行的名称
       栏里写着 ``12345``）。以标识回填名称是导出的常态，而这样的名字在报告里
       读起来像一个指称对象 —— 它不是，它是同一个标识换了个写法。

    第 3 条是 2026-09-23 那次事故在母池这一层的对应物：worker 拿内部 slug
    ``kent-003`` 去检索，9 次查询全落在无关的同名物上。这里挡的是同一个东西的
    另一种形态，而且挡在更前面 —— 一行这样的行连 work item 都建不出来。
    """
    stripped = name.strip()
    if not stripped or not _has_alpha(stripped):
        return ""
    if source_identity:
        identity = source_identity.strip()
        lowered = stripped.lower()
        if lowered == identity.lower() or lowered == identity_tail(identity).lower():
            return ""
    return stripped


def _as_list(value: object) -> tuple[str, ...]:
    """排除项那一格的取值 → 一串 id。

    接受列表（导出里本来就是列表）与一段文本（``a, b`` / ``a;b`` / ``a|b``）。
    两边的空白都去掉，空项丢掉。**顺序保留**：它进 ``exclusion_ids``，
    而报告读的是那一格。
    """
    if isinstance(value, (list, tuple)):
        items = [_as_text(item).strip() for item in value]
    else:
        text = _as_text(value)
        items = [part.strip() for part in text.replace("|", ",").replace(";", ",").split(",")]
    return tuple(item for item in items if item)


def candidate_from(record: Mapping[str, object], *, ordinal: int) -> _Candidate:
    """一行摊平后的对象 → 一个 :class:`_Candidate`。

    这里做的全是**取格子**：来源标识、名称、地点、域名、排除项、官网判定，
    加上逐字留存的全部列。任何判定（能不能研究、要不要归并）都不在这里 ——
    那些要看到**整批**才做得对，而一个只能看到一行的函数做不出正确的批次判定。
    """
    flat = flatten(record)

    source_identity = _first_text(flat, IDENTITY_KEYS)
    if not source_identity:
        object_type = _first_text(flat, TYPE_KEYS).lower()
        object_id = _first_text(flat, ID_KEYS)
        if object_type in OBJECT_TYPES and object_id:
            source_identity = f"{object_type}:{object_id}"

    name = ""
    for key in NAME_KEYS:
        candidate = usable_name(_as_text(flat.get(key, "")), source_identity)
        if candidate:
            name = candidate
            break

    domain = _as_canonical_host(_first_text(flat, DOMAIN_KEYS))
    # 观察那两格**只认它们自己的列名**，不给别名、也不拿 ``url`` 兜底：
    # ``discovered_host`` 今天只有一个写者（发现期的工件，``_artifact_row``），
    # 而一份母池导出里的 ``url`` 说的是别的东西（「这一行的网址」）。
    # 给它一个兜底，会让每一份带 ``url`` 的导出都长出一条**我们从没做过的观察**，
    # 而 ``candidate_domain`` 那一格会因此被填上 —— 报告上看起来完全正常。
    discovered_host = _as_canonical_host(_as_text(flat.get("discovered_host", "")))
    # 观察那两格成对：只有 URL 而没有主机名的行读起来像一条写坏了的观察。
    discovered_url = _as_url(flat.get("discovered_url", "")) if discovered_host else ""
    verified, verdict, domain_evidence_ids, domain_reason = _domain_facts(flat, ordinal=ordinal)

    exclusions: tuple[str, ...] = ()
    for key in EXCLUSION_KEYS:
        if key in flat:
            exclusions = _as_list(flat[key])
            if exclusions:
                break

    return _Candidate(
        row=RawCandidateRow(
            row_id=row_id_for(
                ordinal=ordinal,
                source_identity=source_identity,
                payload=json.dumps(flat, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
            ordinal=ordinal,
            source_identity=source_identity,
            name=name,
            location=_first_text(flat, LOCATION_KEYS),
            attributes={key: _as_text(value) for key, value in flat.items()},
        ),
        name=name,
        location=_first_text(flat, LOCATION_KEYS),
        domain=domain,
        discovered_host=discovered_host,
        discovered_url=discovered_url,
        verified_official_domains=verified,
        domain_verdict=verdict,
        domain_evidence_ids=domain_evidence_ids,
        domain_reason=domain_reason,
        exclusions=exclusions,
        # 资产事实：逐字取列，**不做任何判定**（不认识任何一个取值）。
        # 一列写着 ``unknown`` 的行与一列压根没有的行在这一层分得开 ——
        # 前者是一句结论（「查过，没查出来」），后者是这一份工件没有这一栏。
        organisation_kind=_as_text(flat.get("organisation_kind", "")),
        venue_environment=_as_text(flat.get("venue_environment", "")),
        surface_class=_as_text(flat.get("surface_class", "")),
        country=_as_text(flat.get("country", "")),
    )


# --- 判去向 -------------------------------------------------------------------


def _pending_reason(candidate: _Candidate) -> str:
    """这一行**还缺什么**才能研究。理由要能被照着做，不能只说「不行」。"""
    if candidate.source_identity:
        return (
            f"这一行只有来源标识（{candidate.source_identity}），"
            f"名称那一格是空的或写的就是这个标识。"
            "它因此还进不了研究：拿标识去检索会把预算花在同名物上，"
            "而交回的结算与「这个主体真的没有公开信息」不可区分。"
            "缺的是**身份补全**这一步 —— 补齐一个人类可读的指称对象之后重读这一批。"
        )
    return (
        "这一行既没有来源标识、也没有可用的名称 —— 没有任何东西指向它。\n"
        "它需要先能被指出来（从上游补一个标识或一个名称），然后重读这一批。"
    )


def _undeclared(exclusions: Iterable[str], declared: frozenset[str]) -> list[str]:
    return sorted({item for item in exclusions if item not in declared})


def dispositions_from(
    candidates: Sequence[_Candidate],
    *,
    declared_exclusion_ids: frozenset[str],
    artifact_ref: str,
) -> tuple[RowDisposition, ...]:
    """整批候选 → 每条一个处置。**每一行的去向都在这里定下来。**

    Raises:
        ConfigError: 有行命中了 ``declared_exclusion_ids`` 之外的排除项 id。
            **整份拒绝**，理由见模块 docstring 的那一节。
    """
    undeclared: dict[str, list[int]] = {}
    for candidate in candidates:
        for item in _undeclared(candidate.exclusions, declared_exclusion_ids):
            undeclared.setdefault(item, []).append(candidate.ordinal)
    if undeclared:
        lines = "\n".join(
            f"  - {item!r}（第 {ordinals} 行）" for item, ordinals in sorted(undeclared.items())
        )
        declared = sorted(declared_exclusion_ids)
        raise ConfigError(
            f"工件 {artifact_ref!r} 里有行命中了未声明的排除项：\n{lines}\n"
            f"这一份 profile 声明过的只有 {declared}。\n\n"
            "整份拒绝，**不是**跳过那几行，也**不是**把它们记成读坏了：\n"
            "收下它们等于让输入发明一条 profile 从没声明过的规则 —— 而那条规则"
            "会静默地从池子里删行，报告上只显示 excluded=1。\n"
            "记成 failed 则会把一次**导出与 profile 版本不匹配**渲染成「这一行读坏了」，"
            "而三百行全失败看起来是数据烂、实际要修的是词表。两种处置相反，"
            "报告必须分得开。\n"
            "补齐词表（改 profile 的 exclusions）或改这一份导出，然后重读。"
        )

    # 先把排除项定下来：被排除的行**不参与归并**，也不能当归并的赢家 ——
    # 一条按规则排除掉的行不该因为「它是这一批里第一个」而成为另一行的归宿。
    excluded: dict[str, RowDisposition] = {}
    for candidate in candidates:
        if candidate.exclusions:
            excluded[candidate.row_id] = RowDisposition(
                row_id=candidate.row_id,
                ordinal=candidate.ordinal,
                disposition=Disposition.EXCLUDED,
                source_identity=candidate.source_identity,
                reason="命中已声明的排除项：" + "、".join(candidate.exclusions),
                exclusion_ids=candidate.exclusions,
            )

    # 按来源标识分组，组内**序号最小且可研究的那一个**是赢家。
    winners: dict[str, str] = {}
    for candidate in candidates:
        if candidate.row_id in excluded or not candidate.source_identity:
            continue
        if candidate.name and candidate.source_identity not in winners:
            winners[candidate.source_identity] = candidate.row_id

    dispositions: list[RowDisposition] = []
    for candidate in candidates:
        if candidate.row_id in excluded:
            dispositions.append(excluded[candidate.row_id])
            continue

        winner = winners.get(candidate.source_identity) if candidate.source_identity else None
        if winner is not None and winner != candidate.row_id:
            dispositions.append(
                RowDisposition(
                    row_id=candidate.row_id,
                    ordinal=candidate.ordinal,
                    disposition=Disposition.MERGED,
                    source_identity=candidate.source_identity,
                    reason=(
                        f"与来源标识 {candidate.source_identity!r} 的第一次出现是同一个"
                        f"上游对象 —— 它已经在第 {_ordinal_of(candidates, winner)} 行处置过了。"
                    ),
                    merged_into=winner,
                )
            )
            continue

        if candidate.name:
            dispositions.append(
                RowDisposition(
                    row_id=candidate.row_id,
                    ordinal=candidate.ordinal,
                    disposition=Disposition.RESOLVED,
                    source_identity=candidate.source_identity,
                    resolved_name=candidate.name,
                    resolved_location=candidate.location,
                    # 「别人说的域名」那一格。名字里带 ``claimed`` 不是啰嗦 ——
                    # 权威那一格在下面（``verified_official_domains`` 加
                    # ``domain_verdict``），而两者在一份只有名称与网址的导出里
                    # **可以同时为空**，那时这一行照样是 resolved。
                    claimed_official_domain=candidate.domain,
                    # 观察那两格（我们在哪一页看到的）与判定那三格（我们证得了什么）
                    # 一起走。它们成对出现：只写 ``domain_verdict`` 而不写证据，
                    # 下游会把它读成一句没有出处的断言。
                    discovered_host=candidate.discovered_host,
                    discovered_url=candidate.discovered_url,
                    verified_official_domain=(
                        candidate.verified_official_domains[0]
                        if len(candidate.verified_official_domains) == 1
                        else ""
                    ),
                    verified_official_domains=list(candidate.verified_official_domains),
                    domain_verdict=candidate.domain_verdict,
                    domain_evidence_ids=list(candidate.domain_evidence_ids),
                    domain_reason=candidate.domain_reason,
                    # 资产事实随这一行走到场地资产那一行。**只有 resolved 带它们**：
                    # 一个被排除或被挂起的行不会产生场地资产，而把「这块场地是人工草」
                    # 挂在一条 `pending`（还缺身份）的行上，读起来像是在说
                    # 「这块场地已经可以进池子了」—— 那正是本类要防的那句话。
                    organisation_kind=candidate.organisation_kind,
                    venue_environment=candidate.venue_environment,
                    surface_class=candidate.surface_class,
                    country=candidate.country,
                )
            )
            continue

        dispositions.append(
            RowDisposition(
                row_id=candidate.row_id,
                ordinal=candidate.ordinal,
                disposition=Disposition.PENDING,
                source_identity=candidate.source_identity,
                reason=_pending_reason(candidate),
            )
        )
    return tuple(dispositions)


def _ordinal_of(candidates: Sequence[_Candidate], row_id: str) -> int:
    for candidate in candidates:
        if candidate.row_id == row_id:
            return candidate.ordinal
    return -1  # pragma: no cover —— 赢家一定取自这批候选，找不到就是本模块的 bug


def _failed(candidates: Sequence[int], errors: Mapping[int, str]) -> list[RowDisposition]:
    """读不出来的那些行 —— 它们同样需要一个处置。

    它带着**原始行的文本**算出来的 ``row_id``：那一行没有对象可派生身份，
    内容就是它全部的身份。
    """
    dispositions: list[RowDisposition] = []
    for ordinal in candidates:
        text = errors[ordinal]
        dispositions.append(
            RowDisposition(
                row_id=row_id_for(ordinal=ordinal, source_identity="", payload=text),
                ordinal=ordinal,
                disposition=Disposition.FAILED,
                reason=errors[ordinal],
            )
        )
    return dispositions


# --- 入口 ---------------------------------------------------------------------


def read_motherpool(
    inputs_dir: Path,
    artifact_ref: str,
    *,
    source_method_id: str,
    batch_id: str = "",
    declared_exclusion_ids: Iterable[str] = (),
    profile_id: str = "",
    scope: str = "",
    cutoff: str = "",
    cursor: str = "",
    created_at: datetime | None = None,
) -> SourceReceipt:
    """读一份母池工件 → 一份 :class:`SourceReceipt`。**只读，不写盘**。

    写盘是 :func:`write_receipt` 的事，两件事分开：判定与落盘之间有一次
    所有权的交接，而把它们合成一步之后，「这份回执是从哪一份工件、在哪一份
    profile 下算出来的」就只能从盘上的结果反推。

    Args:
        inputs_dir: 工件所在的目录（``Settings.inputs_dir``）。
        artifact_ref: 工件的**名字**（不含后缀）。它必须已经是规范形式 ——
            这里拒绝，不替调用方规范化：规范化会把两个不同的名字悄悄合并成
            一份工件。名字会变成文件名，这一条是「id → 文件名」那条判据的
            又一处在用。
        source_method_id: 这一批是怎么来的。闭集校验（``domain/sources.py``）。
        batch_id: 上游对这一批的称呼。没有就留空 —— 空是**缺席**，而这里不替它
            编一个：编出来的那个会被读成「上游真的这么叫过」。
        declared_exclusion_ids: **这一份 profile 声明过**的排除项 id。见模块
            docstring 的那一节。
        profile_id: 读的时候哪一份 profile 的排除声明在生效。它不参与判定，
            它回答那个事后一定会被问的问题：「这份回执里的 excluded 是按哪一版
            声明下的？」
        created_at: 这一次判定的时刻。注入是为了让重放可测 —— 默认 :func:`utc_now`。

    Raises:
        ConfigError: 工件找不到或有歧义、整份文档读不出来、名字不是规范形式、
            或命中了未声明的排除项。
    """
    path = find_artifact(inputs_dir, artifact_ref)
    records = read_artifact(path)

    candidates: list[_Candidate] = []
    failures: dict[int, str] = {}
    for ordinal, record in enumerate(records):
        if record.payload is None:
            failures[ordinal] = record.error
            continue
        candidates.append(candidate_from(record.payload, ordinal=ordinal))

    if not records:
        raise ConfigError(
            f"工件 {path.name} 里一行都没有。\n"
            "一份空工件**不是**一次成功的母池读入：它没有产出任何行，所以什么都"
            "没跑。请确认导出本身非空，或换一份工件。"
        )

    dispositions = dispositions_from(
        candidates,
        declared_exclusion_ids=frozenset(declared_exclusion_ids),
        artifact_ref=artifact_ref,
    )
    if failures:
        # 读不出来的行按序号插回去，于是序号恰好是 0..n-1 的一个排列。
        dispositions = tuple(
            sorted(
                (*dispositions, *_failed(sorted(failures), failures)),
                key=lambda item: item.ordinal,
            )
        )

    # 工件摘要**只算一次**：它同时进 ``artifact_sha256`` 与 ``receipt_id``，
    # 而两次读盘之间文件可能已经换过 —— 那时这两个值会来自两份不同的字节，
    # 于是「这份回执指着哪一份工件」这个问题的答案是两个。
    artifact_sha256 = sha256_bytes(path.read_bytes())
    digest = dispositions_digest(dispositions)
    return SourceReceipt(
        receipt_id=receipt_id_for(
            source_method_id=source_method_id,
            batch_id=batch_id,
            artifact_sha256=artifact_sha256,
            dispositions_digest_value=digest,
        ),
        version=RECEIPT_VERSION,
        source_method_id=source_method_id,
        batch_id=batch_id,
        artifact_ref=check_canonical_id(artifact_ref, "artifact_ref"),
        artifact_sha256=artifact_sha256,
        created_at=created_at or utc_now(),
        scope=scope,
        cutoff=cutoff,
        cursor=cursor,
        profile_id=profile_id,
        raw_row_count=len(records),
        dispositions=dispositions,
    )


def receipt_path(inputs_dir: Path, receipt_id: str) -> Path:
    """回执在盘上的位置。**id 与文件名的主干是同一个名字。**"""
    return inputs_dir / f"{check_canonical_id(receipt_id, 'receipt_id')}{RECEIPT_SUFFIX}"


def write_receipt(inputs_dir: Path, receipt: SourceReceipt) -> Path:
    """把回执写到 ``inputs_dir`` 下，返回路径。

    两个守卫，都不是形式主义：

    * **先验自己的 id**（:func:`verify_receipt_id`）：一份内容与 id 对不上的回执
      落盘之后，「这份回执没被动过」这个问题就只剩信任了。写之前验一次，
      盘上就不可能出现那样的文件。
    * **盘上已有的那一份必须自己验得过**，而且 id 相同；否则拒绝、不覆盖。

    # 第二条为什么**不是**逐字节比较（这一条我本来写错了）

    ``created_at``（这一次判定的时刻）**刻意不在** ``receipt_id`` 的推导里 ——
    在里面的话，同一份工件在 10:00 与 10:05 读出来会是两份不同的回执，
    而「重放幂等」正是要挡这个。所以同一份工件读两次：**id 相同，字节不同**
    （只有 ``created_at`` 那一格不同）。

    于是「同一个 id 下的两份字节必须相同」这条判据会把一次**正常重放**读成一次
    「盘上那份被改过」，而它拦的是一个没有发生的事。真正要拦的是别的：
    盘上那份还是不是一份**真的**回执。

    所以判据是 :func:`verify_receipt_id` + id 相同。它成立 ⟹ 盘上那份的内容与它
    自称的 id 自洽，也就是没人改过它的任何一格**被 id 覆盖的**内容。

    **这条判据证明什么、不证明什么，说清楚：** 它证明盘上那份是一份自洽的、
    id 相符的回执，不证明它与这次要写的那一份逐字节相同 —— ``created_at`` 被改过
    的写法验得过（它是一个不进 id 的出处字段），而那次改动**不留痕**。
    这是这一层能做到的最强判据：``created_at`` 既然不进 id，就没有任何东西能把
    一次被改过的 ``created_at`` 与一次不同的读取时刻分开。

    重放的后果是：**第一次读的时刻留在盘上**，重放不重写那一格、也不新建任何东西。

    Raises:
        ConfigError: id 与内容对不上，或盘上同名文件不是一份自洽的、id 相符的回执。
    """
    if not verify_receipt_id(receipt):
        raise ConfigError(
            f"回执 {receipt.receipt_id} 的内容与它自己的 id 对不上，不落盘。\n"
            "id 由「来源方法 + 批次 + 工件摘要 + 处置摘要」派生，所以对不上只有两种"
            "可能：字段被改过，或这份回执不是按 :func:`read_motherpool` 算出来的。"
            "两种都不该写进盘 —— 落盘之后，这个问题就只剩信任了。"
        )
    path = receipt_path(inputs_dir, receipt.receipt_id)
    if path.exists() and _existing_receipt_agrees(path, receipt.receipt_id):
        # 重放：盘上那份仍然自洽、id 也对得上，什么都不做。**不新建任何东西** ——
        # 这条正是「重放幂等」在盘上的样子。
        return path
    if path.exists():
        raise ConfigError(
            f"{path} 已经存在，而它**不是**一份自洽的、id 为 {receipt.receipt_id} 的回执。\n"
            "路径是按回执 id 内容寻址的，所以同一个 id 下出现另一份内容只有一种解释："
            "盘上那份被写过。覆盖它会销毁那次改动的证据，而那正是要回答"
            "「谁改了什么」的东西。请把那文件移到别处（或另存一份）再重跑。"
        )
    payload = json.dumps(
        receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    inputs_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return path


def _existing_receipt_agrees(path: Path, receipt_id: str) -> bool:
    """盘上那份还在不在：它读得回来、id 相符、而且内容与那个 id 自洽。

    **任何一步不成立都返回 ``False``，不抛** —— 调用点要的是「能不能当作已存在」，
    而每一种不成立都通向同一句拒绝。把它写成抛异常会让「读不回来」与「被改过」
    两条路径各要一次 ``except``，而它们在盘上要做的处置是同一个。
    """
    try:
        payload = json.loads(path.read_text(encoding=_TEXT_ENCODING))
        if not isinstance(payload, Mapping):
            return False
        existing = SourceReceipt(**payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return existing.receipt_id == receipt_id and verify_receipt_id(existing)


def load_receipt(inputs_dir: Path, name: str) -> SourceReceipt:
    """按名字读回一份回执，并验它**自己**。

    验两件事，它们是两件事：

    1. **文件名与 ``receipt_id`` 一致** —— 这个目录里「有哪几份回执」这个问题的
       答案来自文件名，而内容里那个 id 是「这一份是谁」的答案。两者不一致时，
       按名字找与按内容找会给出两个不同的集合。
    2. :func:`verify_receipt_id` —— 内容与 id 自洽，即这份回执**没有**被手工改过
       （或者改了之后 id 也跟着改了，那是一次明确的、留痕的改写）。

    Raises:
        ConfigError: 文件不存在、读不出来、字段不认识（``extra="forbid"``）、
            或上面两条判据之一不成立。
    """
    checked = check_canonical_id(name, "input_ref")
    path = inputs_dir / f"{checked}{RECEIPT_SUFFIX}"
    if not path.is_file():
        raise ConfigError(
            f"在 {inputs_dir} 下找不到回执 {checked!r}（期望的文件是 {path.name}）。\n"
            "回执由母池读入那一步写出（``read-motherpool``），文件名就是它的回执 id。"
        )
    try:
        payload = json.loads(path.read_text(encoding=_TEXT_ENCODING))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path.name} 读不出来：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ConfigError(f"{path.name} 的顶上一层不是对象，而是 {type(payload).__name__}。")

    from pydantic import ValidationError

    try:
        receipt = SourceReceipt(**payload)
    except ValidationError as exc:
        raise ConfigError(f"{path.name} 不是一份能读懂的回执：{exc}") from exc

    if receipt.receipt_id != checked:
        raise ConfigError(
            f"{path.name} 里的 receipt_id 是 {receipt.receipt_id!r}，与文件名 {checked!r} 不一致。\n"
            "这个目录里「有哪几份回执」的答案来自文件名，「这一份是谁」的答案来自内容 ——"
            "两者不一致时，按名字找与按内容找会给出两个不同的集合。"
        )
    if not verify_receipt_id(receipt):
        raise ConfigError(
            f"{path.name} 的内容与它自己的 id 对不上 —— 它被改过。\n"
            "一份被改过的回执仍然是可读的，所以它必须在这里被拦下：它是"
            "「这一批行读成了什么」的**唯一**权威，而权威被改过之后，"
            "下游每一个读数都建立在一个没有出处的值上。"
        )
    return receipt


__all__ = [
    "ARTIFACT_SUFFIXES",
    "RECEIPT_SUFFIX",
    "candidate_from",
    "dispositions_from",
    "find_artifact",
    "flatten",
    "load_receipt",
    "read_artifact",
    "read_motherpool",
    "receipt_path",
    "usable_name",
    "write_receipt",
]
