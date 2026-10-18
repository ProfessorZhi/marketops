"""``marketops-plugin/`` 下的文本不得携带任何权威事实。

承重点（AGENT-BRIEF 里 E 那一条）：**无外部副作用 —— Skill 不保存 customer
facts / database / budget truth / credentials**。权威条款是
``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` 第 404 行那句「客户端文件、聊天
memory、Skill 都不是权威」。

一句话写在文档里不会在 CI 里失败，所以这一条把它变成可执行的扫描。

## 判据的形状

两条，别的方式都判不动：

1. **凭据形态的串**：私钥块、厂商前缀密钥、JWT、``password = …`` 这类赋值形态、
   凭据库位置。这一类是「拿到了就能用」的东西，出现在 Skill 里就是泄漏。
2. **本地介质的名字**：``.csv`` / ``.sqlite`` / ``本地缓存`` / ``local ledger``
   这一类。它们本身不是秘密，但一个 Skill 提到它们只有两种原因 ——
   要么要求把服务端事实写进去再读回来（那就是第二个真源），要么在讲为什么
   不能这么干。**判据只盯介质，不盯动词**：``不要写到本地`` 里的介质名一样会被
   抓到，于是禁止句本身也会被打红。

   这不是缺陷，是刻意的：一份需要靠「读懂上下文里的否定」才能判绿的扫描器，
   它的绿说明不了任何事。所以本包的正文里**一个介质名都不出现**。

   判据自己当然满是这些串（规则表与探针就在这个文件里），但它在 ``tests/``
   下，而扫描范围是 ``marketops-plugin/`` —— 判据不在自己的射程内，靠的是
   位置而不是豁免。``SELF_EXEMPT`` 只是同一件事的第二道闩，不是给正文开的口子。

## 为什么还有正向的那一半

一个「什么都没扫到」的扫描器和一个「扫到的东西都干净」的扫描器给出同一份
报告。所以除了违规为零，本文件还要求：扫描真的走到了它声称的那些文件、
每个 reference 都真的引用了服务端的工具、每个被引用的名字都真的在目录里、
``SKILL.md`` 真的说了以 ``tools/list`` 为准。少了这一半，绿是假绿。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.research import research_handlers
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.document_router import DocumentRouter
from marketops.config import Settings
from marketops.ports.search import SearchProvider, SearchQuery, SearchResult
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.fixtures.http_fakes import FakeHttp
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "marketops-plugin"

#: 判据自己的文件 —— 规则的反面例子与探针必然含违规串。
#: 这是**按文件**豁免的唯一一处，且只覆盖这一个文件。
SELF_EXEMPT = {Path(__file__).resolve()}

#: 要被扫描的文本类型。``.json`` 在列 —— 客户端投影里有 status.json 与 manifest。
SCANNED_SUFFIXES = (".md", ".toml", ".json", ".txt")

#: 权威事实的正文：只在这些文件里要求「不自指」。
CANONICAL = ("SKILL.md",) + tuple(
    f"references/{name}"
    for name in (
        "access-diagnostics.md",
        "build-market.md",
        "follow-up-work.md",
        "monitor-and-refresh.md",
        "prepare-draft.md",
        "research-contact.md",
    )
)

#: 生成物镜像：逐字节与服务端正文相同。同内容两处放是刻意的（插件要求
#: ``skills/<name>/``），所以用恒等判据钉住，而不是靠人记得重新生成。
MIRROR_PREFIX = "skills/marketops/"


# --- 规则 1：凭据形态 --------------------------------------------------------

CREDENTIAL_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI 形态的密钥", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHub 令牌", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub 细粒度令牌", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("Slack 令牌", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("AWS 访问键", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    ),
    (
        "赋值形态的凭据",
        # ``\b`` 是承重的：``bearer_token_env_var`` 里 ``token`` 后面是 ``_``，
        # 那是词字符，所以这里不匹配 —— 命名一个环境变量不是携带一个凭据。
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token"
            r"|client[_-]?secret|private[_-]?key|bearer[_-]?token)\b\s*[:=]\s*"
            r"[\"']?[^\s\"'`]{6,}"
        ),
    ),
    (
        "内联的 Bearer 值",
        # 值那一半要求像值：``Bear${VAR}`` 与 ``Bearer <placeholder>`` 都不匹配。
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=+/]{16,}"),
    ),
    ("凭据库位置", re.compile(r"(?:^|[\s\"'(])\.secrets/")),
    ("凭据库位置", re.compile(r"/vault/")),
    (
        "凭据文件名",
        re.compile(r"(?i)\b(?:id_rsa|id_ed25519|auth\.json|credentials\.json|\.netrc)\b"),
    ),
)

# --- 规则 2：本地介质 --------------------------------------------------------

LOCAL_MEDIUM: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "本地表文件",
        re.compile(r"(?i)\.(?:csv|tsv|sqlite|sqlite3|db|parquet|jsonl|xlsx|xls)\b"),
    ),
    (
        "本地文件/缓存/副本/账本",
        re.compile(r"(?i)\blocal\s+(?:file|cache|copy|ledger|database|snapshot)\b"),
    ),
    (
        "本地文件/缓存/副本/账本",
        re.compile(r"本地\s*(?:文件|缓存|副本|账本|数据库|快照)"),
    ),
    ("落盘", re.compile(r"落\s*盘")),
    ("写到本地", re.compile(r"(?:写|存|落)\s*(?:到|入|进)\s*本地")),
)

# --- 规则 3：预算不许是一个 Skill 维护的数 -----------------------------------

BUDGET_WORDS = ("预算", "额度", "配额", "余额", "budget", "quota", "balance")

# --- 规则 4：不许自指工具条数（只对正文） ------------------------------------

TOOL_COUNT_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("中文工具条数", re.compile(r"\d+\s*个\s*(?:MCP\s*)?工具")),
    ("英文工具条数", re.compile(r"(?i)\b\d+\s+(?:MCP\s+)?tools?\b")),
    ("一共/共有 + 数字", re.compile(r"(?:一共|共有|总计)\s*\d+")),
    # 条数不必挨着 ``tools`` 这个词：``there are 86`` / ``serves 86`` 是同一种
    # 断言，只是换了个动词。没有这一条，把 ``37 tools`` 改写成
    # ``there are 37`` 就能从判据下面走过去。
    (
        "以数量动词断言条数",
        re.compile(r"(?i)\b(?:there are|serves?|exposes?|provides?|offers?|returns?)\s+\d+"),
    ),
)

#: ``tools/list`` 必须出现在 ``SKILL.md`` 里：条数以服务端为准，而不是写死。
TOOLS_LIST_MARKER = "tools/list"

#: 行首的列表序号。判「预算与数字同现」前要先把它摘掉 —— 否则 ``2. …budget…``
#: 里的那个 ``2`` 会被读成一个 Skill 自己维护的数，而它是排版。
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


# --- 扫描器 ------------------------------------------------------------------


def _scan_text(text: str) -> list[str]:
    """返回一段文本里的违规，带行号。"""
    offenders: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pattern in CREDENTIAL_SHAPES:
            if pattern.search(line):
                offenders.append(f"{lineno}: 凭据形态（{label}）")
        for label, pattern in LOCAL_MEDIUM:
            if pattern.search(line):
                offenders.append(f"{lineno}: 本地介质（{label}）")
        body = _LIST_MARKER.sub("", line)
        if any(word in body for word in BUDGET_WORDS) and re.search(r"\d", body):
            offenders.append(f"{lineno}: 预算/额度与数字同现（Skill 自己维护了一个数）")
    return offenders


def _scan_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return [f"{rel}:{hit}" for hit in _scan_text(path.read_text(encoding="utf-8"))]


def _scanned_files() -> list[Path]:
    return sorted(
        path
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and path.suffix in SCANNED_SUFFIXES and path.resolve() not in SELF_EXEMPT
    )


# --- 对照：扫描器本身必须能抓、且不误判 --------------------------------------


def test_scanner_actually_catches_a_violation():
    """阳性对照 —— 抓不到违规的扫描器，它的绿是假的。"""
    probe = "\n".join(
        [
            "把结果写到本地文件 accounts.csv 里，下次直接读。",
            'api_key = "abcdef123456"',
            "Bearer abcdefghijklmnopqrstuvwxyz",
            "剩余预算 40000 元。",
        ]
    )
    hits = _scan_text(probe)
    assert len(hits) >= 4, f"扫描器漏掉了明显的违规，结果不可信：{hits}"


def test_scanner_is_not_fooled_by_a_prohibition_shaped_line():
    """阴性对照 —— **禁止句一样会红**，而且这是刻意的。

    如果这条判据要靠读懂否定来判断，那它就成了一条可以被措辞绕过的判据。
    本包的正文里因此一个介质名都不出现；这条测试把那个取舍钉下来，免得
    后来的人「顺手」加一条豁免，把判据放宽成措辞检查。
    """
    probe = "不要写到本地缓存里，也不要使用 local file。"
    assert _scan_text(probe), "禁止句没有被打红 —— 判据已经退化成了措辞检查"


def test_scanner_leaves_clean_prose_alone():
    """阴性对照的另一半：没有违规的正常句子必须零命中。"""
    probe = "\n".join(
        [
            "Read the as-of moment from `mcp:get_report` and present it as given.",
            'bearer_token_env_var = "MARKETOPS_MCP_TOKEN"',
            "The tool surface is whatever the server lists: read `tools/list`.",
        ]
    )
    assert _scan_text(probe) == [], f"正常句子被误判：{_scan_text(probe)}"


# --- 扫描必须真的走到它声称的那些文件 ----------------------------------------


def test_plugin_root_exists_and_has_text():
    assert PLUGIN_ROOT.is_dir(), f"{PLUGIN_ROOT} 不存在 —— 扫描会假通过"
    files = _scanned_files()
    assert len(files) >= 12, f"只扫到 {len(files)} 个文件，扫描范围可能已失效：{files}"


def test_every_canonical_file_is_present():
    missing = [rel for rel in CANONICAL if not (PLUGIN_ROOT / rel).is_file()]
    assert not missing, f"正文文件缺失，正向判据会假通过：{missing}"


# --- 不变量 1：没有凭据、没有本地介质、没有 Skill 自己维护的数 ----------------


def test_no_credential_no_local_medium_no_self_maintained_budget():
    offenders: list[str] = []
    for path in _scanned_files():
        offenders.extend(_scan_file(path))

    assert not offenders, (
        "marketops-plugin/ 下出现了凭据形态、本地介质，或一个由 Skill 自己维护的数字"
        "（Skill 不许携带权威事实）：\n  " + "\n  ".join(offenders)
    )


# --- 不变量 2：每个业务事实都指得出一次服务端调用 ----------------------------


def _mcp_citations(text: str) -> set[str]:
    return set(re.findall(r"mcp:([a-z_]+)", text))


def _canonical_texts() -> dict[str, str]:
    return {rel: (PLUGIN_ROOT / rel).read_text(encoding="utf-8") for rel in CANONICAL}


def test_every_reference_cites_the_server():
    """每个 reference 都必须至少引用一次服务端工具。

    一个不引用任何工具的 reference 就是一份客户端自己攒的知识 —— 正是本片
    要防的东西，而它在「违规为零」那一条里是看不见的。
    """
    missing: list[str] = []
    for rel, text in _canonical_texts().items():
        if rel == "SKILL.md":
            continue
        if not _mcp_citations(text):
            missing.append(rel)
    assert not missing, f"这些 reference 没有引用任何服务端工具：{missing}"


def test_every_cited_tool_exists_in_the_catalog():
    """被引用的名字必须真的在目录里。

    引用一个不存在的工具，读起来和引用一个存在的工具完全一样 —— 直到有人
    照着它去调。这一条把「编制出来的工具名」变成一次失败。
    """
    unknown: list[str] = []
    for rel, text in _canonical_texts().items():
        for name in sorted(_mcp_citations(text)):
            if name not in TOOL_CATALOG:
                unknown.append(f"{rel}: mcp:{name}")
    assert not unknown, f"引用了目录里没有的工具：{unknown}"


def test_the_canonical_set_cites_a_broad_surface():
    """引用的**去重后**数量有个下限。

    下限不是目标，是「扫描没扫到东西」的探测器：一个把整个正文换成一句
    「见服务端」的改动会让上面两条都通过。
    """
    names: set[str] = set()
    for text in _canonical_texts().values():
        names |= _mcp_citations(text)
    assert len(names) >= 25, f"正文只引用了 {len(names)} 个不同的工具，覆盖可能已失效：{sorted(names)}"


# --- 不变量 3：不自指 ---------------------------------------------------------


def test_skill_defers_the_tool_surface_to_the_server():
    """``SKILL.md`` 必须说以 ``tools/list`` 为准，且正文不许写工具条数。

    条数写在文件里，下一次有工具落地它就错了，而一个错的条数比没有条数更糟。
    """
    skill = (PLUGIN_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert TOOLS_LIST_MARKER in skill, f"SKILL.md 没有提到 {TOOLS_LIST_MARKER}，条数就没有服务端可依"


def test_canonical_files_state_no_tool_count():
    offenders: list[str] = []
    for rel, text in _canonical_texts().items():
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in TOOL_COUNT_SHAPES:
                if pattern.search(line):
                    offenders.append(f"{rel}:{lineno}: {label}: {line.strip()}")
    assert not offenders, "正文里写了工具条数（应当以 tools/list 为准）：\n  " + "\n  ".join(offenders)


def test_skill_states_the_authority_clause():
    """权威条款必须被正文引用，而不是只活在设计文档里。"""
    skill = (PLUGIN_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "not authoritative" in skill, "SKILL.md 没有引用权威条款（客户端文件/Skill 不是权威）"
    assert "MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:404" in skill, (
        "SKILL.md 没有指出权威条款的出处，读者无法自己核对"
    )


# --- 不变量 4：生成物镜像与正文逐字节相同 ------------------------------------


def test_generated_skill_mirror_is_byte_identical():
    """``skills/marketops/`` 是生成物，必须与正文逐字节相同。

    插件要求 ``skills/<name>/`` 这一层，所以同样的内容在包里有两处。两份文本
    会漂 —— 除非有一条判据在漂的时候失败。生成方式见 ``client-projections/codex/RECEIPT.md``：
    把 ``SKILL.md`` 与 ``references/`` 复制进 ``skills/marketops/``。
    """
    drifted: list[str] = []
    for rel in CANONICAL:
        source = PLUGIN_ROOT / rel
        mirror = PLUGIN_ROOT / MIRROR_PREFIX / rel
        if not mirror.is_file():
            drifted.append(f"{rel}: 镜像缺失")
            continue
        if source.read_bytes() != mirror.read_bytes():
            drifted.append(f"{rel}: 镜像与正文不同")
    assert not drifted, "生成物镜像已漂移，重新生成：\n  " + "\n  ".join(drifted)


# --- 不变量 5：客户端投影逐客户端诚实记录 ------------------------------------


def test_every_projection_has_a_status_with_a_reason():
    """每个客户端投影的每一格都要有状态；未验证的必须写出理由或解除条件。

    「验证过」必须指向收据；「被外部挡住」必须写出解除条件。空格子读起来像
    通过，所以空格子本身就是失败。
    """
    problems: list[str] = []
    for name in ("codex", "workbuddy", "muc-hosting"):
        path = PLUGIN_ROOT / "client-projections" / name / "status.json"
        if not path.is_file():
            problems.append(f"{name}: status.json 缺失")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        fields = doc.get("fields")
        if not isinstance(fields, dict) or not fields:
            problems.append(f"{name}: 没有 fields")
            continue
        for field, cell in fields.items():
            if not isinstance(cell, dict) or not cell.get("state"):
                problems.append(f"{name}.{field}: 没有 state")
                continue
            state = cell["state"]
            if state == "verified" and not cell.get("evidence"):
                problems.append(f"{name}.{field}: 说 verified 却没有 evidence")
            if state == "blocked_external" and not (cell.get("unblock") or doc.get("unblock")):
                problems.append(f"{name}.{field}: blocked_external 却没有 unblock")
            if state == "not_verified" and not cell.get("reason"):
                problems.append(f"{name}.{field}: not_verified 却没有 reason")
    assert not problems, "客户端投影的诚实记录有缺口：\n  " + "\n  ".join(problems)


def test_verified_projections_point_at_a_receipt():
    """凡是有任何一格写了 verified 的投影，必须有一份收据文件在。"""
    problems: list[str] = []
    for name in ("codex", "workbuddy", "muc-hosting"):
        directory = PLUGIN_ROOT / "client-projections" / name
        status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        verified = [
            field
            for field, cell in (status.get("fields") or {}).items()
            if isinstance(cell, dict) and cell.get("state") == "verified"
        ]
        if verified and not (directory / "RECEIPT.md").is_file():
            problems.append(f"{name}: 有 {len(verified)} 格 verified 却没有 RECEIPT.md")
    assert not problems, "\n  ".join(problems)


# --- 不变量 6：为 get_report 点名的字段必须真的读得到 -------------------------
#
# 这一条与上面的 ``test_every_cited_tool_exists_in_the_catalog`` 是同一件事的
# 两半：那一条管**工具名**，这一条管**字段名**。引用一个不存在的工具，与引用一个
# 存在的工具读起来完全一样 —— 引用一个不存在的字段也一样 —— 直到有人照着它去读。
#
# 而这一条还多一层。这个包此前那句「Read the as-of moment and the business keys
# from ``mcp:get_report``」**一个字段都没点**：它既指不出读哪里，也就无法被证伪。
# 一份读不到的承诺与一份读得到的承诺，在散文里长得一模一样。所以判据是两半：
# 名字必须被点出来（否则这条判据没有主语，任何实现都能让它变绿），点出来的名字
# 必须真的从 ``get_report`` 读得到（否则客户端照着读会扑空）。

#: 反引号里的标识符。``mcp:get_report`` 这类引用带 ``:``，落不进下面那条形状。
_BACKTICKED = re.compile(r"`([^`]+)`")

#: 一个字段名的形状。``tools/list`` 带 ``/``，``mcp:*`` 带 ``:`` —— 都不是字段名。
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: 这一条判据自己那一轮的运行名。它会被拼成文件名（``benchmarks/<run_id>.json``），
#: 所以它必须是**规范形式**（``slugify`` 不变），不是随便一个字符串。
REPORT_SCOPE = "skill-field-probe"
REPORT_REGION = "Devon"


def _blocks(text: str) -> list[list[str]]:
    """按空行把 markdown 切成段。

    **以段为单位而不是以行**：一条 bullet 折行之后，字段名可能落在第二行上，
    而那一行不会出现工具名。按行扫会静默漏掉它，而漏掉的后果是——文档把名字
    写在折行处时，这条判据悄悄变绿。
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _fields_named_for(text: str, tool: str) -> set[str]:
    """``text`` 里为 ``tool`` 点名的字段名。"""
    named: set[str] = set()
    for block in _blocks(text):
        if not any(f"mcp:{tool}" in line for line in block):
            continue
        named |= {
            token
            for line in block
            for token in _BACKTICKED.findall(line)
            if _FIELD_NAME.match(token)
        }
    return named


class _NoHits(SearchProvider):
    """一次什么都不回的检索。

    这一条判据的主语是**报告的形状**，不是检索：报告落在哪一个 ``run_id`` 上、
    里面有哪几格。所以替身刻意什么都不返回 —— 一次真的检索会让这一条判据的
    成本与失败理由都跑到别处去。
    """

    name = "no-hits"

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        return SearchResult(query=query, hits=(), api_calls=1)


def _controller_ring(tmp_path: Path) -> tuple[TokenRing, str]:
    """铸一个控制者令牌 —— ``run_discovery`` 与 ``get_report`` 都要它。"""
    token, digest = mint("skill-field-probe", [Role.CONTROLLER])
    path = tmp_path / "skill-probe-tokens.json"
    path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "skill-field-probe", [Role.CONTROLLER.value]))),
        encoding="utf-8",
    )
    return TokenRing.from_file(path), token


@pytest.fixture
def published_report(settings: Settings, tmp_path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """跑一轮真的发现、把报告真的写进 ``benchmarks/``、再用 ``get_report`` 读两遍。

    返回 ``(run_id, 第一次读, 第二次读)``。两遍读在**两次会话**里发生 —— 那是
    「两个客户端」在测试面上唯一诚实的替身：同一个进程里连读两次，中间没有任何
    东西来得及变，于是「两次一样」这件事几乎是免费成立的。

    ``run_discovery`` 走的是 MCP 上那条工具，不是 ``application`` 里的同名函数：
    报告落盘那两行在生产里**只**写在 ``adapters/mcp_server/research.py``（CLI 里
    另有一份同形的）。判据自己往 ``benchmarks/`` 造一份 JSON 的话，它测的就是
    「我造的那份读得出来」，而文档承诺的是「服务端产出的那份读得出来」。
    """
    ring, token = _controller_ring(tmp_path)

    def session(*calls: tuple[str, dict[str, Any]]) -> Any:
        handlers = research_handlers(
            settings=settings,
            search=_NoHits(),
            acquisition=DocumentAcquisition(
                http=FakeHttp(settings.mirrors_dir / "skill-field-probe", {})
            ),
            router=DocumentRouter([HtmlDocumentParser()]),
            ledger_factory=lambda: SqliteBudgetLedger(settings.database_path),
            evidence_store_factory=lambda: SqliteEvidenceStore(settings.database_path),
        )
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            research=handlers,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    published = session(
        ("run_discovery", {"scope": REPORT_SCOPE, "region": REPORT_REGION})
    ).call("run_discovery")
    assert not published.is_error, published.text
    run_id: str = published.as_json()["run_id"]

    def read() -> dict[str, Any]:
        result = session(("get_report", {"run_id": run_id})).call("get_report")
        assert not result.is_error, result.text
        payload = result.as_json()
        assert isinstance(payload, dict), payload
        return payload

    return run_id, read(), read()


def test_every_field_the_skill_names_for_get_report_is_readable(
    published_report: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    """``SKILL.md`` 为 ``mcp:get_report`` 点名的每一个字段都必须真的读得到。

    按文档去读，读不到就红。两条断言各自挡一种退化：

    * 名字一个都点不出来 —— 那句话又变回一句指不出读哪里的散文，这条判据
      也就没有主语了（任何实现都让它绿）。所以这一半本身是一条判据。
    * 点出来的名字读不到 —— 客户端照着文档去读会扑空，而扑空与「那一格是空的」
      在回执上长得一样。
    """
    _run_id, first, _second = published_report
    skill = (PLUGIN_ROOT / "SKILL.md").read_text(encoding="utf-8")

    named = _fields_named_for(skill, "get_report")
    assert named, (
        "SKILL.md 没有为 mcp:get_report 点出任何一个字段名 —— 一条点不出读哪里的"
        "承诺既没法照着读，也没法被证伪"
    )

    missing = sorted(name for name in named if name not in first)
    assert not missing, (
        f"SKILL.md 点名的字段读不到：{missing}（报告里实际有的是 {sorted(first)}）"
    )


def test_the_skill_field_check_catches_a_name_that_is_not_there(
    published_report: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    """正控：这条判据抓得住「点了一个读不到的名字」，两种写法各抓一次。

    两种写法都是真实出现过的形状，缺一条控制就少一格：

    * **散文里的词**（``the as-of moment``）—— 改之前 ``SKILL.md`` 就是这一种。
      它带连字符，不是一个字段名的形状，所以 ``_fields_named_for`` 一格都读不出来。
      抓住它的是上面那条判据的**第一半**（名字必须被点出来）——
      这也正是「零命中」与「没写」在散文里长得一样的地方。
    * **反引号里的字段名**（``as_of``）—— 一个照着改的人会写的形状。它读得出来，
      而报告里没有这一格，抓住它的是上面那条判据的**第二半**。

    ``as_of`` 在本仓的生产代码里一处都没有（``command grep -rn as_of marketops/``
    是 0 命中），所以这条控制的前提今天成立。
    """
    _run_id, first, _second = published_report

    prose = "* Read the as-of moment from `mcp:get_report` and present it as given."
    assert _fields_named_for(prose, "get_report") == set(), "散文里的词不该被读成字段名"

    named = "* Read `as_of` from `mcp:get_report` and present it as given."
    assert _fields_named_for(named, "get_report") == {"as_of"}, "反引号里的名字没被读出来"
    assert "as_of" not in first, "报告里居然有 as_of —— 这条控制的前提已经变了"


def test_two_clients_naming_the_same_run_id_agree_on_the_moment(
    published_report: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    """文档新写下的那一句：同一个 ``run_id`` 读回来的是同一份报告，两遍一样。

    「两遍一样」单独是空的 —— 一个每次都回当下的实现也满足它，只要两次够快。
    所以这里同时钉住**那个时刻是哪一刻**：``run_id`` 自己带着它（到秒），
    ``created_at`` 把同一个时刻写全。两格若各自是各自，同一份报告就有两个时刻，
    而那正是两个客户端会读到两个时刻的入口。
    """
    run_id, first, second = published_report

    assert first == second, "同一个 run_id 两次读回来的报告不一样"

    embedded = datetime.strptime(run_id.rsplit("-", 1)[-1].upper(), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )
    spelled = datetime.fromisoformat(first["created_at"]).astimezone(timezone.utc)
    assert embedded == spelled.replace(microsecond=0), (
        f"run_id 里的时刻是 {embedded.isoformat()}，created_at 是 {spelled.isoformat()} —— "
        "两格说的不是同一件事，同一份报告就有了两个时刻"
    )
