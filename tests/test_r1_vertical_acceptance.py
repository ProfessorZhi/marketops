"""§G1 的纵向验收：从**零手工 roster** 起，**一个** MCP 会话走完九个阶段。

## 这一条证的是 §G1 的那两句

> 顺序：Scene → discovery/import → 准入/identity → cohort → Quay research →
> Jev（确有用途）→ 唯一路线/拒收 → 草稿/合法 hold。**全程通过一个 MCP，从零手工
> roster 开始。**

| 阶段 | 在哪一条上被判 |
|---|---|
| Scene（立项并激活） | :func:`test_the_whole_chain_runs_from_a_clean_root_on_one_mcp` |
| discovery / import（上游工件 → 回执 → 池子） | 同上 |
| 准入 / identity（身份线索不足者整份拒绝） | :func:`test_a_cohort_member_without_identity_clues_refuses_the_whole_batch` |
| cohort（系统冻结成员名单，不落 YAML） | :func:`test_the_whole_chain_runs_from_a_clean_root_on_one_mcp` |
| Quay research（**把名单交给研究**） | 同上 —— 这一步在 G1 之前**没有入口**，见下 |
| Jev（确有用途，且不被强加给每个任务） | :func:`test_the_jevs_step_hands_over_the_specs_and_forces_no_call` |
| 唯一路线 / 拒收 | :func:`test_the_whole_chain_runs_from_a_clean_root_on_one_mcp` |
| 草稿 / 合法 hold | 同上（草稿）；:func:`test_a_route_that_was_never_judged_is_review_only_and_leaves_named`（hold） |
| 八条「必须失败」的对照 | :data:`CONTROLS` + :func:`test_every_control_names_a_real_executor` |

## 这一轮的量具是什么（**必须写明**）

链上没有任何一次真实网络调用：检索与抓取是**夹具**，人提供的是**测试种子**（一份
上游导出、几页材料），而 Account 本身**不是**手拼的 —— 它是 ``promote_receipt``
从回执折出来的。§G1 逐字：「人提供测试种子不等于生产路径允许人工拼 Account」。

所以这份文件证明的是**这条链的结构真的接得上**（每一步真的走 MCP、真的落盘、真的
按业务键对得上），**不是**「一次 live run 通过了」。真实付费那一段（Serper / Jev 的
授权预算）在本机没有额度，按 §G1 末尾那句只阻塞 paid 验收；它在这份报告里记
``BLOCKED``，不拿假后端冒充。

## 为什么这一趟必须**新写**一个文件，而不是接着三段现成的链跑

仓里已经有三段纵向链，各自全绿：``test_mcp_discovery_chain``（发现 → 受理）、
``test_mcp_contact_routes``（受理 → 领活 → 结算 → 路线）、``test_mcp_cohorts``
（冻结 → 维护 → 起草）。**它们之间的接缝是用夹具补上的** —— 冻结那一段的路线由
``_seed_routes`` 直接写库，结算那一段根本不经过名单。于是「三段各自绿」与「接缝
接得上」是两件可以同时成立的事，而 G1 要的正是后者。

所以这里每一次调用都从**工具**进、从**工具**出，中间不写 YAML、不手工拼 Account；
**并且这个部署是真正的组合根**（``interfaces/mcp/__main__._compose``），不是这一份
文件自己拼的一张处理器表 —— 后者能过而生产起不来，是这一族测试最贵的那种假绿。

## 这一趟顺带修好的一处断链

``submit_research`` 的 ``input_kind`` 在此之前只能三选一，**没有一条能把一份冻好的
名单送去研究**。于是 §B12 的实现逐字要求的「维护/草稿/**研究**接受 Scene selector
或 durable cohort 引用」只有前两条兑现，而 §G1 的顺序里 cohort 后面紧跟着 Quay
research。这不是一条可以登记后结束的缺口：它在本仓代码范围内，所以那条枚举成员
（``JobInputKind.COHORT``）与那两格接线在这一轮补上了，而**补完之后这条链才第一次
走得通** —— 这一份文件就是它的执行器。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.discovery_run import candidate_id_for
from marketops.application.evidence_ingest import document_evidence
from marketops.application.promotion import canonical_account_id_for
from marketops.config import Settings
from marketops.domain.entities import ContactRoute
from marketops.domain.enums import Grade, GradeReason
from marketops.domain.util import sha256_text
from marketops.interfaces.composition import default_asset_store
from marketops.interfaces.mcp.__main__ import _compose
from marketops.security.tokens import mint
from tests import test_mcp_contact_routes as contact_routes
from tests.mcp_harness import drive, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence, make_observation

#: 本部署的 Scene（``conftest.DEFAULT_PROFILE``）。``require_scene`` 只认它。
PROFILE = "uk-acme-natural-grass"

#: 这个部署的发送箱。一个表达不出「要发信的部署」的实例走不到 hold / review-only
#: 那两档的正常路径上。
SENDBOX = "outreach@marketops.example"

#: 一次成立的立项。与 ``tests/test_mcp_cohorts.py`` 的 ``BASE_CREATE`` 同形 ——
#: 它是**测试输入**，真实那套住在 ``profiles/`` 里。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: 一份上游交进来的导出。两行，两个场地、两家组织 —— 一行的话「名单上有几个人」
#: 与「折进去了几个人」是同一个数，而这一条要的正是它们**可以**不同。
#:
#: ``source_identity`` 写 ``site:<主机>`` 这个形状：``promote_receipt`` 只把**发现
#: 那一轮写下的**形状折成账号，换个形状它会折成 ``asset_only``（场地存在、账号
#: 拿不出依据）。
ARTIFACT_LINES: tuple[str, ...] = (
    '{"source_identity": "site:example-village.example",'
    ' "name": "Example Village Hall", "location": "Devon",'
    ' "official_domain": "example-village.example"}',
    '{"source_identity": "site:example-sports.example",'
    ' "name": "Example Sports Club", "location": "Devon",'
    ' "official_domain": "example-sports.example"}',
)

CAMPAIGN = "campaign-uk-grass-2026q4"
WAVE = "wave-2026-09-27-0001"
POLICY = "policy-uk-grass-stage-1"
FAMILY = "family-uk-grass-stage-1"
LOCALE = "en-GB"


def _ok(outcome, index: int = -1) -> dict[str, Any]:
    """上一次调用的回执，**按错误通道先判**：``is_error`` 为真时把正文原样带出来。

    取 ``index=-1``（最后一次）是本文件里的常态：每一步都只用它自己的回执，
    而把序数写死会让一次插入的调用静默地改变后面那几句的读数。
    """
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = -1) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


# ----------------------------------------------------------------------
# 部署：真正的组合根
# ----------------------------------------------------------------------
class Deployment:
    """一个**真的**服务实例：``_compose`` 装配出来的那一份，外加一枚令牌。

    ## 为什么是 ``_compose`` 而不是自己拼一张处理器表

    ``drive_with_settings`` 让每个测试自己列接线清单 —— 那是**装置**，不是**部署**：
    它把「组合根漏传了一个工厂」这一类缺陷挡在测试之外，而那正是生产上唯一会出事
    的那一类。这里走 ``_compose``：它与 ``python -m marketops.interfaces.mcp`` 起的
    服务是同一份装配，所以这一趟走通的链，生产上那个进程也走得通。

    ## 一枚令牌、两个角色

    ``controller`` 与 ``researcher`` 在同一枚令牌上。§G1 要求的是「全程通过**一个**
    MCP」—— 一个服务实例，不是一枚钥匙；而角色是**能力**边界，不是会话边界：
    受理（控制侧）与领活/结算（研究侧）在同一趟里都要发生，各铸一枚令牌的形态会让
    「这一趟是谁做的」这个问题有两个答案。

    环里**没有** ``authorizer``，这不是遗漏：``security/tokens.py`` 拒绝同一主体同时
    持 controller 与 authorizer，而 §G1 那条链止于草稿/合法 hold —— ``send_wave``
    不在这九步里。

    ## 执行器：一个**真的子进程**，但不是一个真的 Quay

    §6 那句 "The controller cannot substitute an outer-shell worker launch for
    Quay dispatch" 由 ``lease`` 反过来守：没有 ``ExecutionRef`` 就没有租约，于是
    ``get_assignment`` 直接拒。所以这一条链**必须**真的走过一次派发，否则它在
    「领活」那一格上红成一个与被测行为无关的理由 —— 第一版就是这样：``settings``
    里三样执行器配置都没有，``default_dispatcher`` 返回 ``None``，工作单元编译出来了
    却一件也没交出去。

    这里给的是 ``tests/fake_quay_cli.py``：**真子进程、真 argv、真 stderr、真退出
    码**，照着实测到的 Quay 怪癖失败（见那个文件的模块文档）。它不是 in-process
    替身 —— 换掉的是一个**外部进程**，而那个进程不在本仓、也不在 CI 上。真正那套
    Quay（`quay.js` + 一个含 ``.quay/config.yml`` 的 store）属于 live 验收那一轮，
    这里用的是同一份 ``default_dispatcher`` 映射，只是把那个可执行文件换成了它。
    """

    def __init__(self, settings: Settings, tmp_path: Path) -> None:
        roles = ["controller", "researcher"]
        token, digest = mint("claude-g1", roles)
        ring_path = tmp_path / "tokens.json"
        ring_path.write_text(
            json.dumps(ring_payload(ring_entry(digest, "claude-g1", roles))),
            encoding="utf-8",
        )
        quay_root = tmp_path / "quay"
        (quay_root / ".quay").mkdir(parents=True)
        (quay_root / ".quay" / "config.yml").write_text(
            "provider: native\n", encoding="utf-8"
        )
        composed = _compose(
            replace(
                settings,
                mcp_tokens_path=ring_path,
                outbound_from_address=SENDBOX,
                quay_cli_path=Path(__file__).with_name("fake_quay_cli.py"),
                quay_node_path=Path(sys.executable),
                quay_root=quay_root,
            )
        )
        assert composed.blocker == "", composed.blocker
        self._server = composed.server
        self._token = token

    def run(self, *calls: tuple[str, dict[str, Any]]):
        outcome = drive(
            server=self._server, token=self._token, calls=calls, want_tool_list=False
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome


@pytest.fixture
def deployed(settings: Settings, tmp_path: Path) -> Deployment:
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"
    return Deployment(settings, tmp_path)


# ----------------------------------------------------------------------
# 输入的生产者（夹具，不是被测对象）
# ----------------------------------------------------------------------
def _write_artifact(settings: Settings) -> None:
    """把上游那份导出落进 ``inputs_dir``。**工具收名字，不收路径。**

    它不是一份账号清单：``.jsonl`` 里的行是上游对象（场地句柄 + 名称 + 地点），
    折成账号是 ``promote_receipt`` 的事。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "motherpool.jsonl").write_text(
        "\n".join(ARTIFACT_LINES) + "\n", encoding="utf-8", newline="\n"
    )


def _hosts() -> tuple[str, ...]:
    """工件里那两行的主机名。从**行自己**读出来，不在这里再抄一遍。"""
    return tuple(
        json.loads(line)["source_identity"].split(":", 1)[1] for line in ARTIFACT_LINES
    )


def _domain_of(settings: Settings, account_id: str) -> str:
    """这个账号**那块场地上写着**的官网域。研究者要从哪台主机取那一页，就是它。

    ## 为什么不是 ``MarketAccount.domains``

    第一版这里读的是池子里 ``MarketAccount.domains``，而它是**空的** —— 那不是库坏了：
    ``promote_receipt`` 有意不写域名/地址/别名（它的模块文档逐字写着理由：回执上的
    ``official_domain`` 是**那块场地**的属性，而 ``MarketAccount.domains`` 是**这个
    组织**的属性，前者当后者用正是 §3 禁止的那一类「从形状推责任」）。所以「池子里
    那一行没有域」是这条链的正常形态，把它读成前提就会让这条夹具红在一个与被测行为
    无关的理由上 —— 而它真正该做的是**换一个地方问**。

    ## 为什么是场地资产

    账号 id 是 ``sha256``，不可逆；``acct-…`` 是哪块场地这个问题，池子里只有一处答得
    出来：资产池那一行。而「账号 ← 场地」这条边的方向是生产代码定的
    （``promotion.canonical_account_id_for``，纯函数，与折号时用的那一个逐字同一个），
    所以这里**用那个函数反查**，不自己拼一遍摘要 —— 拼出来的那个永远匹配不上，而报告
    上只会看到「这个夹具自己坏了」。

    取 ``claimed_official_domain``（**上游说的**那一句）而不是
    ``verified_official_domain``：这份工件是母池形状的，它只带 ``official_domain``
    一列、不带判定那四列，所以**证得的那一格必须是空的** —— 这一条断言在这里，是为了
    让「哪天这份工件换成了发现期的形状」在这条夹具上立刻可见，而不是让等级悄悄跟着变。
    """
    wanted = {
        canonical_account_id_for(asset.venue_asset_id): asset
        for asset in default_asset_store(settings)().list_assets()
    }
    asset = wanted.get(account_id)
    assert asset is not None, (
        f"资产池里没有折成 {account_id!r} 的那块场地 —— 这个夹具自己坏了"
        f"（池子里有 {sorted(wanted)!r}）"
    )
    assert not asset.verified_official_domain, (
        f"{asset.venue_asset_id!r} 有证得的官网域 {asset.verified_official_domain!r} ——"
        " 而这份母池形状的工件不带判定那四列。夹具的前提变了，别默默接着跑。"
    )
    assert asset.claimed_official_domain, (
        f"{asset.venue_asset_id!r} 那一行没有 official_domain，而这两页材料要有地方取 ——"
        " 夹具自己坏了"
    )
    return asset.claimed_official_domain


def _seed_discovery_evidence(settings: Settings) -> None:
    """发现那一轮取回的那几条证据。**上游产出的既有事实，不是这一层的动作。**

    ``promote_receipt`` 只在**有证据**时才写账号，而发现期的证据挂在**候选**身份
    上 —— 这里用**生产的**那个函数（``candidate_id_for``）把它挂回去，不自己拼一个
    「看起来像」的 id：拼出来的那个永远不会被读到，而报告上只会看到池子是空的。

    ``field=None``：这一条证据说的是「这一页被取回来看过」，不是「这个组织的联系
    方式是 X」。发现期的证据不得满足 ``account.contacts.*`` 那一族事实。

    ## 指纹与解析器名要**是真的**，不是两格待填

    ``content_sha256`` 与 ``parser`` 在这一行上不是装饰：``application/refresh.py``
    的 ``_page_targets`` 把**带指纹的可绑定来源**当成可复核的页面，而复核那一趟
    拿回来的字节要算出**同一个摘要**才算「没变」、``parser`` 落在
    ``router.parser_names`` 里才谈得上复用。所以：

    * 落一个占位串（``make_evidence`` 的缺省是 ``"abc"``）的后果不是「少了一个数」
      —— 是这一页**每一轮都被判成 changed**，而报告上看起来像「这一页一直在变」；
    * ``parser`` 留空的后果是这一页**永远过不了复用那一条** —— 每一轮都真的出去
      取一次，而它长得像「复核跑得比较勤」。

    生产那一侧两个字段都是有的（``evidence_ingest.document_evidence`` 全部取自
    解析结果），所以这里也照那一份给：摘要来自 :func:`_home_page` 的**同一份字节**，
    解析器名是本部署注册的那一个。
    """
    store = SqliteEvidenceStore(settings.database_path)
    for host in _hosts():
        store.put_evidence(
            make_evidence(
                evidence_id=f"evidence-{host}",
                account_id=candidate_id_for(host),
                source_url=f"https://{host}/",
                field=None,
                content_sha256=sha256_text(_home_page(host)),
                parser="html",
            )
        )


def _file_evidence(settings: Settings, account_id: str, *, text: str, url: str) -> str:
    """一页材料按**真的那一趟**落成一行文档级证据，返回它的 id。

    形状借 ``test_mcp_contact_routes`` 那一份实现（下划线开头，跨模块 import 一个
    私有名字在这里是**较小的**那件坏事）：这一格的定义是「真实抓取那一侧落下的一行
    长什么样」，两个文件各写一份的话，``field`` / ``source_kind`` / ``workspace``
    里任意一格写错都会让某一条红在一个与被测行为无关的理由上 —— 或者更坏：绿在
    一个不存在的形状上。本仓已有的先例是 ``tests/test_mcp_contact_routes.py`` 直接
    ``from tests import test_mcp_discovery_chain as chain``。

    ## 取回时刻由**这里**给，不照抄那一份写死的 2026-09-24

    两处的时刻必须是**同一对**：材料的 ``fetched_at`` 是路由那一行 ``first_seen_at``
    的来路（``domain/evidence.py::moments_from_evidence`` 取最早那一次），而页面自己
    声明的发布时刻必须**不晚于**它 —— 晚了就是 ``invalid_source_time``，一条关于
    那一页的坏消息，与这一条链要证的事无关。所以两个时刻一起按运行当天算，
    见 :data:`_FETCHED_AT` / :data:`_PUBLISHED_ON`。
    """
    evidence = document_evidence(
        workspace=settings.profile,
        account_id=account_id,
        source_url=url,
        document=contact_routes._document(text, url=url),
        fetched_at=_FETCHED_AT,
        http_status=200,
        tier="http",
    )
    assert evidence is not None, "这一页没有可引用的正文 —— 夹具自己坏了"
    SqliteEvidenceStore(settings.database_path).put_evidence(evidence)
    return evidence.evidence_id


#: 名单里那个**只有一个内部 id、没有名字**的成员。它是 §1 那次 ``kent-003`` 事故
#: 的形状：上游那一份清单在名称那一格填的是它自己的记录键。
IDENTITYLESS = "kent-003"

#: 上游那份**成员目录页**的正文，``kent-003`` 只是其中的一行。
#:
#: 它是一个模块常量而不是函数里的一个字面量，因为**这份字节有两个用处**：这里落下的
#: 证据要解得开（``remember_accounts`` 逐条核），而 R2 那一份验收会在同一趟里把这一页
#: **预置进抓取器** —— 那个账号名下唯一的页面目标就是它，复核那一趟会真的去取。
#: 两处各写一份的漂移形态是「那一页取回来读起来像变了」，而它长得像**来源变化**。
DIRECTORY_PAGE = (
    "<html><head><title>Member directory</title></head><body>\n"
    f"  <p>{IDENTITYLESS} — grounds, Devon</p>\n"
    "</body></html>\n"
)


def _remember_a_name_that_is_only_an_id(
    deployment: Deployment, settings: Settings
) -> str:
    """把「名称那一格填的是内部 id」这一行**从工具**送进池子，返回它的 account_id。

    ## 为什么这一行不能靠改池子里已经有的那一行来造

    第一版这里读一行已入库的账号、``model_copy(update={"organisation": ""})``
    再写回去；它撞在两个校验器上（名字那一格、以及被它带出来的 ``aliases`` 那一格）。

    那不是库坏了，**也不该顺手放宽**：``model_copy`` 绕开校验器，所以它造出来的是
    一行生产路径产不出来的数据，拿它当被测行为的输入，测的是「夹具能不能骗过校验器」。
    而空名字在这一层本来就写不进去 —— ``organisation`` 必填且非空
    （``domain/account.py::_organisation_is_non_empty``），``promote_receipt`` 也不
    产它。改池子里那一行还有第三处不对：``merge_accounts`` 的第 5 条是「主语第一次
    记下的为准」，所以**名字一旦记下就抹不掉**，这个动作在合并规则下根本不成立。

    ## 真正可达、而且就是被记下来的那一种

    ``identity_refusal`` 唯一的**内容**判据是 ``organisation != account_id``，
    它挡的正是 §1 报上来的那个情形：清单里把内部 id 填进了名称那一格。所以这一行
    走的就是那条入口 —— ``remember_accounts``，operator/agent 交一批账号的 MCP 工具。
    它是池子的**第二个**写者（另一个是 ``promote_receipt``），而这一条判据必须对
    两个写者一视同仁：整份拒绝是受理那一侧读出来的，与那一行从哪进来的无关。

    ## 证据

    这一行要带一条解得开的证据（``remember_accounts`` 逐条核），所以这里先落一页
    **那份清单本身**：上游的成员目录页，条目上写的正是那个内部键。用真的那一趟
    （``_file_evidence`` → ``document_evidence``）落，不手拼 ``Evidence`` —— 手拼的
    那一行在库里读起来与真的一模一样，而它证明不了这一步真的走得通。
    """
    evidence_id = _file_evidence(
        settings,
        IDENTITYLESS,
        text=DIRECTORY_PAGE,
        url=f"https://{_hosts()[0]}/directory",
    )
    outcome = _ok(
        deployment.run(
            (
                "remember_accounts",
                {
                    "accounts_rows": [
                        {
                            "account_id": IDENTITYLESS,
                            # 名称那一格就是它自己的内部 id —— 这一行**过得了**
                            # ``MarketAccount`` 的全部校验器，所以它是一个真实的
                            # 输入，不是一行被硬造出来的数据。
                            "organisation": IDENTITYLESS,
                            "evidence_ids": [evidence_id],
                        }
                    ]
                },
            )
        )
    )
    assert outcome["by_disposition"]["written"] == 1, (
        f"这一行该正常写进池子 —— 它要证的是**受理那一侧**拒得掉，"
        f"不是这一行写不进去：{outcome!r}"
    )
    return IDENTITYLESS


#: 这一趟「取回这几页」的时刻，以及那几页自己声明的发布时刻。**按运行当天算。**
#:
#: ## 为什么不写死
#:
#: 这一条链走的是**真时钟**：MCP 面上没有 ``now`` 注入口（``produce_routes`` 取
#: ``utc_now()``），而时效那一格判的是「来源发布在 ``current_within_days`` 以内」
#: （本 profile 是 180 天）。所以一个写死的发布日期会让这一条在半年后自己转红 ——
#: 那时红的原因与这一条要证的事没有关系，而它长得像「链路坏了」。
#:
#: 反过来，只把发布时刻写成「今天」也不行：它必须**不晚于**取回时刻，否则
#: ``invalid_source_time``。两个值因此是一对，一起算。
_FETCHED_AT = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
_PUBLISHED_ON = (_FETCHED_AT - timedelta(days=30)).date().isoformat()


def _contact_page(domain: str) -> str:
    """一页**有具名角色、有本组织域下真实邮箱、且声明了发布时刻**的材料 ⇒ 一条 B 级路线。

    域名由调用方按**这个账号自己的官网域**给。写一个别处的域会让这一条测成
    「域名不符」—— 那是一条**等级**的差别，不是「绑没绑出来」的差别。

    那一行 ``Published:`` **不是装饰**：没有它，这条路线落盘时 ``freshness`` 是
    ``unknown（no_time_anchor）``，而 ``review_reasons`` 会把「当前性不够」读成
    review-only（§8 逐字：当前性不够时不得称 outreach-ready）。也就是说
    「这一稿能不能发」这一格**整个**挂在它上面 —— 而真实世界里一页联系人材料
    本来就常带发布日期（``domain/extract.py::page_publication_time`` 就是为它写的）。
    """
    return (
        "<html><head><title>Example Club — Contact</title></head><body>\n"
        "  <h1>Example Club</h1>\n"
        f"  <p>Published: {_PUBLISHED_ON}</p>\n"
        "  <p>Grounds Manager: Jane Doe</p>\n"
        f"  <p>Email: jane.doe@{domain}</p>\n"
        "</body></html>\n"
    )


def _unbound_page(domain: str) -> str:
    """一页**有邮箱、但没说这是谁**的材料 ⇒ 一条 ``unbound_email`` 拒收回执。

    那一档的处置是「回那一页找那张卡片」，与「这一页一个人都没有」不是一件事，
    所以它在回执上要落成**拒收**而不是零条路线。
    """
    return (
        "<html><head><title>Example Club — Contact</title></head><body>\n"
        "  <h1>Example Club</h1>\n"
        f"  <p>Reach us at sales@{domain}</p>\n"
        "</body></html>\n"
    )


def _home_page(domain: str) -> str:
    """这块场地**站根**那一页的正文 —— 发现那一轮取回来的就是它。

    它是一个独立的生产者，而不是「随便一段像页面的字」：``_seed_discovery_evidence``
    落下的那一行要带一个**真指纹**，而指纹只有在有人拿得出**同一份字节**时才比得上
    —— 复核那一趟会把这一页重取回来、再算一次摘要。这里和 ``_contact_page`` /
    ``_unbound_page`` 一样是**同一个产地**：写一份「意思差不多」的正文，会让每一次
    复核都读成 ``changed``，而它在报告上长得像**来源变了**。
    """
    return (
        "<html><head><title>Example Club — Home</title></head><body>\n"
        "  <h1>Example Club</h1>\n"
        f"  <p>We look after the grounds at {domain}.</p>\n"
        "</body></html>\n"
    )


def _seed_unjudged_route(settings: Settings, account_id: str) -> None:
    """给这个账号落一条**绑定与时效都没判过**的路线。

    它**不是**一条生产产不出来的行：路线是这一层只**消费**的输入（生产者在研究
    侧），而 ``review_reasons`` 逐字把那两格读成「从来没有人判过」——
    ``RouteBinding.NONE`` 与 ``freshness is None``。§8 要的正是这一档不许自称
    outreach-ready，所以这一条是**合法 hold** 那一侧的正控。

    证据与路线一起落：一条没有证据的路线在 ``plan_drafting`` 里被直接跳过，而那是
    另一条判据（「这条路线没有证据」）的事 —— 用它来造这一档会让这一条红在一个
    与被测行为无关的理由上。

    ## 指纹与解析器名照 :func:`_contact_page` 那一份给

    与 :func:`_seed_discovery_evidence` 同一条理由，而且这一行更近：它的 URL 就是
    ``/contact``，是页面复核那一趟**真的会去取**的那一页。落一个占位摘要会让这一页
    每一轮都被读成 ``changed``（并且每次都新落一行证据 —— 「世界变了」这个数跟着
    「我们看得勤不勤」一起涨）；``parser`` 留空则让它**永远过不了复用那一条**，每一轮
    都出去取一次。两个字段生产那一侧都是真的，所以这里也照那一份给，而且用
    **同一个产地**的正文 —— 有人要重放这一页时不必再猜它当时是什么。
    """
    evidence_id = f"evidence-unjudged-{account_id}"
    domain = _domain_of(settings, account_id)
    store = SqliteEvidenceStore(settings.database_path)
    store.put_evidence(
        make_evidence(
            evidence_id=evidence_id,
            account_id=account_id,
            content_sha256=sha256_text(_contact_page(domain)),
            parser="html",
            source_url=f"https://{domain}/contact",
            field=None,
        )
    )
    store.record_route(
        make_observation(
            account_id,
            route=ContactRoute(
                workspace=PROFILE,
                account_id=account_id,
                organisation=f"Example {account_id}",
                email=f"grounds@{domain}",
                evidence_ids=[evidence_id],
                grade=Grade.B,
                grade_reason=GradeReason.RULE_MATCHED,
            ),
        )
    )


def _publish(deployment: Deployment) -> None:
    """两发**发布**：先写作策略，再模板族（§7：用户不得直接写数据库）。

    夹具直接写库会省掉两次调用，而它正是 §7 要修的那件事：一个干净客户端发布不了
    话术时，每一次 ``draft_outreach`` 的前提都是夹具替它造好的。
    """
    _ok(
        deployment.run(
            (
                "put_writing_policy",
                {
                    "policy_id": POLICY,
                    "revision": 1,
                    "generic_wording_id": "wording-generic-1",
                },
            )
        )
    )
    _ok(
        deployment.run(
            (
                "put_family",
                {
                    "family_id": FAMILY,
                    "revision": 1,
                    "stage": "stage-1",
                    "role_class": "grounds_manager",
                    "locale": LOCALE,
                    "product": "natural-grass",
                    "writing_policy_id": POLICY,
                },
            )
        )
    )


def _drafting_payload(cohort_id: str) -> dict[str, Any]:
    """一次起草的参数。**两处调用同一份** —— 分两处写会让「同一批」变成两句话。"""
    return {
        "wave_id": WAVE,
        "campaign_id": CAMPAIGN,
        "campaign_name": "UK 天然草 首次接触",
        "stage": "stage-1",
        "locale": LOCALE,
        "cohort": cohort_id,
    }


def _preparing_payload(cohort_id: str) -> dict[str, Any]:
    return {
        "wave_id": WAVE,
        "campaign_id": CAMPAIGN,
        "stage": "stage-1",
        "window_start": "2026-09-01T00:00:00+00:00",
        "window_end": "2026-12-01T00:00:00+00:00",
        "valid_until": "2026-12-31T00:00:00+00:00",
        "cohort": cohort_id,
    }


# ----------------------------------------------------------------------
# 那条链（两个人各走一遍，供几条判据共用）
# ----------------------------------------------------------------------
def _settle(
    deployment: Deployment,
    settings: Settings,
    *,
    account_id: str,
    work_item_id: str,
    pages: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """领活、把 ``pages`` 摆成材料、结算一次，返回整份回执。

    ``pages`` 是 ``(正文, URL)`` 的序列 —— 一次结算可以引**多份**材料，而两半一起
    交正是这一族判据的要点：一份**全成功**的材料上，拒收那条通道根本看不到。

    材料挂在 ``account_id`` 上而不是 ``work_item_id`` 上：证据是**账号**的，
    而结算那一层要核的正是「你引的这条证据是不是这个账号的」——引用另一个账号的
    证据会被整份拒绝（``tests/test_mcp_contact_routes.py`` 那一条）。把两个 id 混成
    一个，这一格就测不出东西来。

    领活与结算分两次调用，因为服务面就是这样：``get_assignment`` 给一个 attempt，
    ``submit_findings`` 凭它交活。合成一次的话，「这一趟是谁领的」就没有第二个
    读数可以核。
    """
    attempt_id = _ok(
        deployment.run(("get_assignment", {"work_item_id": work_item_id}))
    )["attempt"]["attempt_id"]
    observations = []
    for text, url in pages:
        evidence_id = _file_evidence(settings, account_id, text=text, url=url)
        observations.append(
            {
                # 一句里带上**这一份**材料的 URL。写死一句话会让两份材料交上去时
                # 撞在「同一个 statement 写两遍」那条判据上 —— 而那一条判据是对的：
                # 同一句话写两遍会直接变成一个虚高的条数。
                "statement": f"这一页写了这个俱乐部的联系人与场地职能：{url}",
                "evidence_refs": [evidence_id],
            }
        )
    return _ok(
        deployment.run(
            (
                "submit_findings",
                {
                    "work_item_id": work_item_id,
                    "attempt_id": attempt_id,
                    "stop_reason": "goal_met",
                    "observations": observations,
                },
            )
        )
    )


def _run_chain(
    deployment: Deployment, settings: Settings, *, second_route: str
) -> dict[str, Any]:
    """从**干净数据根**走到「结算完、路线落库」为止，返回各处读数。

    ``second_route`` 决定**第二个成员**的路线从哪来，而且只有那一个选择：

    * ``"settled"`` —— 也走一次真的结算（材料 → 绑定 → 定级 → 落库）。这是
      §G1 那条链本来的样子。
    * ``"unjudged"`` —— 落一条绑定与时效**都没判过**的路线
      （:func:`_seed_unjudged_route`）。它让「草稿 / 合法 hold」那一档在**同一份
      回执里**与正常草稿并列出现，从而证明回执把两者**分得开**，而不是合成一句
      「这一批没发出去」。

    两个选择都**不改**前面七个阶段：那些读数在两条路上一模一样，而这也是
    「hold 是路线那一侧的事、不是链条接不上」这句话的落地。
    """
    # --- 阶段 1：Scene ---------------------------------------------------
    scene = _ok(deployment.run(("create_scene", {**BASE_CREATE, "activate": True})))
    assert scene["scene"]["state"] == "active", f"立项没激活：{scene!r}"

    # --- 阶段 2：discovery / import --------------------------------------
    # 上游那份导出落盘，然后**从工具**读成回执、折进池子。全程没有 YAML 清单。
    _write_artifact(settings)
    _seed_discovery_evidence(settings)
    imported = _ok(deployment.run(("import_motherpool", {"artifact_ref": "motherpool"})))
    receipt_id = imported["receipt"]["receipt_id"]
    assert imported["receipt"]["resolved_row_count"] == len(ARTIFACT_LINES)

    promoted = _ok(deployment.run(("promote_receipt", {"receipt_id": receipt_id})))
    assert promoted["promotion"]["counts"]["promoted"] == len(ARTIFACT_LINES), (
        f"每一行都该折出一个账号。逐行去向：{promoted['rows_detail']!r}"
    )

    # --- 阶段 3：准入 / identity ------------------------------------------
    pool = _ok(deployment.run(("list_account_pool", {})))
    accounts = tuple(row["account_id"] for row in pool["accounts"])
    assert len(accounts) == len(ARTIFACT_LINES), f"池子里的账号：{pool!r}"
    assert not list(settings.inputs_dir.glob("*.yaml")), (
        "inputs_dir 里出现了 yaml —— 而正常使用要的正是「不依赖它」"
    )

    # 阶段 4 是**唯一**能写下一份影子清单的那一步（``product_loop.py::_freeze_cohort``），
    # 所以下面还要在它**之后**再数一次 —— 上面那一格只盖到阶段 3，盖不到这一步。
    # （这条是 Agent I 在 §4.1 报的：少这一格，本判据对「人工 YAML 是必需」这条对照
    # 只承载了一半，而 green 会被读成「另一半也盖住了」。MAIN 补。）
    assert not list(settings.inputs_dir.glob("*.yaml")), (
        "inputs_dir 里出现了 yaml —— 而正常使用要的正是「不依赖它」"
    )

    # --- 阶段 4：cohort --------------------------------------------------
    frozen = _ok(deployment.run(("freeze_cohort", {})))
    cohort_id = frozen["cohort_id"]
    assert {row["account_id"] for row in frozen["members"]} == set(accounts), (
        "名单上的成员不是池子里那两个 —— 冻结那一刻读的是哪一份，"
        f"回执与池子要答得一样：{frozen!r}"
    )
    assert not list(settings.inputs_dir.glob("*.yaml")), (
        "冻结之后 inputs_dir 里多出了 yaml —— 冻结是一份读数，"
        "它没有理由在盘上多写一份清单"
    )

    # --- 阶段 5：Quay research **把名单交出去** ---------------------------
    # 这一步在 G1 之前**没有入口**：``submit_research`` 的 input_kind 只能三选一，
    # 而三条路都不读名单。补上 ``cohort`` 之后它第一次走得通。
    submitted = _ok(
        deployment.run(
            (
                "submit_research",
                {
                    "scene_id": PROFILE,
                    "input_kind": "cohort",
                    "input_ref": cohort_id,
                    "idempotency_key": "g1-cohort-1",
                },
            )
        )
    )
    assert submitted["state"] == "queued", (
        f"作业被阻塞了：{submitted['reason']}\n"
        "（这一条链上最常见的成因是：名单读不出来。回执里 subjects 那一格说的是"
        "它读的是哪一份名单）"
    )
    assert submitted["work_items"]["count"] == len(accounts), (
        f"名单上有 {len(accounts)} 个成员，编译出来的工作单元却是"
        f"{submitted['work_items']['count']} 个：{submitted!r}"
    )

    # --- 阶段 6：领活 ----------------------------------------------------
    items = SqliteJobStore(settings.database_path).work_items(submitted["job_id"])
    by_subject = {item.subject_ref: item for item in items}
    assert set(by_subject) == set(accounts), (
        "工作单元的主语不是池子里那两个账号 —— 那会让检索按一个不存在的账号跑完"
        f"而 get_account 一条都找不到：{[i.subject_ref for i in items]!r}"
    )

    # --- 阶段 7：唯一路线 / 拒收 ------------------------------------------
    # 第一个人交**两份**材料：一份带具名角色与本组织域邮箱（绑得出路线），一份只有
    # 邮箱、没说这是谁（绑不出路线，只留一条拒收回执）。两种一起交，是因为「拒收」
    # 这条通道在一份**全成功**的材料上看不到 —— 而 G1 要的正是那一档也真的在。
    first, second = min(accounts), max(accounts)
    settled = _settle(
        deployment,
        settings,
        account_id=first,
        work_item_id=by_subject[first].work_item_id,
        pages=(
            (
                _contact_page(_domain_of(settings, first)),
                f"https://{_domain_of(settings, first)}/contact",
            ),
            (
                _unbound_page(_domain_of(settings, first)),
                f"https://{_domain_of(settings, first)}/about",
            ),
        ),
    )["routes"]
    assert settled["error"] is None, f"绑定没跑成：{settled['error']}"
    assert settled["routes"] == 1, (
        f"两份材料里只有一份绑得出路线：{settled}\n"
        "（``routes`` 为 ``null`` 说的是这一格没算过；为 0 说的是算过了、材料里"
        "一个人都没有 —— 两者的下一步不同）"
    )
    assert settled["by_grade"]["B"] == 1, (
        f"具名决策职能 + 本组织域下的真实邮箱应当判 B：{settled['by_grade']}\n"
        "（判成 X 多半意味着域名那一格没传下去 —— 而报告里那只是「多了几个 X」）"
    )
    assert settled["rejections"]["total"] == 1, (
        f"那一页只有邮箱、没说这是谁 —— 它该落一条拒收回执：{settled['rejections']!r}"
    )
    assert settled["rejections"]["by_code"]["unbound_email"] == 1, (
        f"拒的那一条该是「看不出它是谁的」：{settled['rejections']['by_code']!r}"
    )

    # --- 阶段 7 之二：第二个人的路线 --------------------------------------
    if second_route == "settled":
        second_settled = _settle(
            deployment,
            settings,
            account_id=second,
            work_item_id=by_subject[second].work_item_id,
            pages=(
                (
                    _contact_page(_domain_of(settings, second)),
                    f"https://{_domain_of(settings, second)}/contact",
                ),
            ),
        )["routes"]
        assert second_settled["routes"] == 1, f"第二个成员没绑出路线：{second_settled}"
    else:
        assert second_route == "unjudged", second_route
        _seed_unjudged_route(settings, second)

    store = SqliteEvidenceStore(settings.database_path)
    return {
        "accounts": accounts,
        "first": first,
        "second": second,
        "cohort_id": cohort_id,
        "settled": settled,
        "item_id": by_subject[second].work_item_id,
        "routes": {account_id: store.list_routes(account_id) for account_id in accounts},
    }


# ----------------------------------------------------------------------
# 一、那条链
# ----------------------------------------------------------------------
def test_the_whole_chain_runs_from_a_clean_root_on_one_mcp(
    deployed: Deployment, settings: Settings
) -> None:
    """零手工 roster → 九个阶段 → 两张草稿，而**同一个账号**贯穿每一处读数。

    ## 为什么每一步都要把主语再读一遍

    「没报错」在每一格上都有信息量，而它们加起来仍然证明不了这条链是**一条**：
    池子里的账号、名单上的成员、工作单元的主语、路线的账号、草稿的账号 —— 这五个
    值各自都可以是对的，而它们指向的可以是五个不同的东西。三个池子、两份名单、
    两个库都能让每一份报告自洽，这正是本仓反复处理的那类失败。所以每一处都从回执
    里把它取出来，与第 3 步记下的那两个比。
    """
    chain = _run_chain(deployed, settings, second_route="settled")
    accounts, first, second = chain["accounts"], chain["first"], chain["second"]

    # 结算那一步的主语**就是**池子里那一个账号，回执说的条数与表里的行数对得上。
    routes = chain["routes"]
    assert routes[first], "结算说绑出来了，库里却是空的"
    assert len(routes[first]) == chain["settled"]["routes"], (
        "回执说的条数与表里的行数对不上 —— 读的人会据此认为路线已经落库"
    )
    assert len(routes[second]) == 1
    for account_id in accounts:
        for row in routes[account_id]:
            assert row.account_id == account_id, (
                "路线挂到了另一个账号上 —— 而这一行在任何一份按账号读的报告里都正常"
            )

    # 而它在**服务面**上也读得回来：「绑出来了」若只是一句内部状态，这一格就是空的。
    view = _ok(deployed.run(("get_account", {"account_id": first})))
    assert view["route_count"] == len(routes[first])
    assert view["rejections"]["by_code"]["unbound_email"] == 1, (
        "拒收那一趟按**账号**要读得回来 —— 与结算回执上那一份是两句不同的话"
        f"（一趟 vs 历来）：{view['rejections']!r}"
    )

    # --- 阶段 8：草稿 ----------------------------------------------------
    _publish(deployed)
    drafted = _ok(
        deployed.run(("draft_outreach", _drafting_payload(chain["cohort_id"])))
    )
    assert drafted["subjects"]["kind"] == "cohort"
    assert drafted["subjects"]["cohort_id"] == chain["cohort_id"], (
        "起草读的不是刚冻的那一份名单 —— 两者都自称成功，而收件人是另一批人"
    )
    assert drafted["subjects"]["routes_considered"] == len(accounts), (
        "这一批该按名单上每一个成员各看一次路线"
    )
    assert drafted["plan"]["planned"] == len(accounts), (
        f"这一批的目标该是名单上那两个。计划：{drafted['plan']!r}"
    )
    # 两条路线都来自**真的那一趟**结算，所以两条都是正常草稿：绑定与时效都判过。
    assert drafted["business"]["counts"]["accepted"] == len(accounts), (
        "两条都是判过绑定与时效的路线，应当都落成正常草稿："
        f"{drafted['business']['counts']!r}\n"
        "（落到 review_only 多半意味着结算那一趟没把绑定/时效判下去 —— 而那正是"
        "§8 那句「准入、当前性或绑定不足时不称 outreach-ready」要挡的反面）"
    )
    assert drafted["send_authorized"] is False, "起草完成不是允许发送"

    # 最后一步：草稿上的账号**指得回**池子里那两个。到这一格为止，同一个账号从
    # 池子一路走到了草稿上，中间没有任何一步是人替它搭的桥。
    by_account = {row["account_id"]: row for row in drafted["drafts"]}
    assert set(by_account) == set(accounts), (
        f"草稿挂的账号不是名单上那两个：{drafted['drafts']!r}"
    )
    for row in by_account.values():
        assert row["state"] == "generated", f"这一稿该是正常草稿：{row!r}"


def test_a_cohort_member_without_identity_clues_refuses_the_whole_batch(
    deployed: Deployment, settings: Settings
) -> None:
    """名单里只要有一个**拿不出身份依据**的成员，整份拒绝，一件工作都不建。

    ## 为什么是「整份」而不是「跳过那一行」

    跳过会让一份两行的名单编译出**一个**工作单元，而报告里那个「1」与「名单里本来
    就只有 1 个」看起来完全一样 —— 于是「有一个人没被研究」这件事在任何一份读数里
    都不存在。§B12 逐字要求整份拒绝，§1 那次 ``kent-003`` 事故的成因正是身份在这一
    层被丢掉。

    这条判据的位置也钉住了：它必须在 ``compile_work_items`` **之前**成立，因为到了
    那里就已经有工作单元了，而工作单元的失败形态是「worker 领走它、然后花掉真金
    白银」。所以断言的是**作业状态**与**工作单元条数**，不是一次拒绝。

    ## 那一个成员是怎么进池子的

    走 ``remember_accounts`` 这条工具（见
    :func:`_remember_a_name_that_is_only_an_id`）：它送进来的一行**名称那一格填的是
    它自己的内部 id**，而这一行过得了 ``MarketAccount`` 的全部校验器 —— 它是真实的
    输入，不是一行被硬造出来的数据。第一版这里是对一行已入库的账号做
    ``model_copy``，那既绕开了校验器（于是「夹具能不能骗过校验器」变成了被测对象），
    又在 ``merge_accounts`` 的第 5 条下根本不成立（名字一旦记下就抹不掉）。
    """
    scene = _ok(deployed.run(("create_scene", {**BASE_CREATE, "activate": True})))
    assert scene["scene"]["state"] == "active"
    _write_artifact(settings)
    _seed_discovery_evidence(settings)
    receipt_id = _ok(deployed.run(("import_motherpool", {"artifact_ref": "motherpool"})))[
        "receipt"
    ]["receipt_id"]
    _ok(deployed.run(("promote_receipt", {"receipt_id": receipt_id})))

    # 名单上**一个**成员拿不出身份依据。它从**另一个写者**进来（工具，不是回执），
    # 所以这一条同时钉住了「判据读的是池子那一行，不是它从哪来的」。
    account_id = _remember_a_name_that_is_only_an_id(deployed, settings)

    pool = _ok(deployed.run(("list_account_pool", {})))
    accounts = tuple(row["account_id"] for row in pool["accounts"])
    assert account_id in accounts, (
        f"那个成员没进池子，于是这一条一个成员都拒不了：{accounts!r}"
    )
    cohort_id = _ok(deployed.run(("freeze_cohort", {})))["cohort_id"]

    submitted = _ok(
        deployed.run(
            (
                "submit_research",
                {
                    "scene_id": PROFILE,
                    "input_kind": "cohort",
                    "input_ref": cohort_id,
                    "idempotency_key": "g1-identity",
                },
            )
        )
    )
    assert submitted["state"] == "blocked", (
        "缺身份依据的成员被放过去了 —— 而它会让检索按一个搜不出东西的主体花钱："
        f"{submitted!r}"
    )
    assert submitted["work_items"]["count"] == 0, (
        "整份拒绝的意思是一件工作都不建。建出来的话，「有一个人没被研究」在任何"
        f"一份读数里都不存在：{submitted!r}"
    )
    assert account_id in submitted["reason"], (
        f"阻塞理由要点得出是哪一个成员：{submitted['reason']!r}"
    )


def test_a_route_that_was_never_judged_is_review_only_and_leaves_named(
    deployed: Deployment, settings: Settings
) -> None:
    """「草稿 / 合法 hold」：一批里既有正常草稿，也有**不该发**的那一档。

    §8 逐字：「准入、当前性或绑定不够时只给**明确标注的** review-only/held 状态，
    **不称 outreach-ready**」。§B13 把同一件事要求成一份回执的**档位**。

    判据分三处，缺一处这句话就空：

    * **它进了这一批**（``plan.planned`` 数到它）—— 那一档是**状态**，不是筛选。
      被筛掉的话，「这条路线不够」在报告里根本不存在，而人只会看到这一批小了一点。
    * **它的档位是 ``review_only``**，与绑出来那条 ``accepted`` **并列** —— 两者
      合成一句「这一批没发出去」的话，下一步就分不出该去核路线还是该去补输入。
    * **它进不了清单**（``prepare_wave`` 只放行 ``generated``），而且**被点名排除**
      并带上理由 —— 一个只写在备注里的标记挡不住任何人，而它会让人以为挡住了。
    """
    chain = _run_chain(deployed, settings, second_route="unjudged")
    _publish(deployed)

    drafted = _ok(
        deployed.run(("draft_outreach", _drafting_payload(chain["cohort_id"])))
    )
    assert drafted["plan"]["planned"] == len(chain["accounts"]), (
        "两条路线都该进这一批：``review_only`` 说的是这一稿**够不够发**，"
        f"不是它该不该被起草：{drafted['plan']!r}"
    )
    counts = drafted["business"]["counts"]
    assert counts["accepted"] == 1 and counts["review_only"] == 1, (
        f"一份回执要同时报出「写了能发」与「写了但不许发」：{counts!r}\n"
        "（六个档位是**一个划分**；合成一个「没发出去」之后，两边的下一步会指向"
        "相反的事：accepted 那条要等授权，review_only 那条要去核路线）"
    )

    by_account = {row["account_id"]: row for row in drafted["drafts"]}
    assert set(by_account) == set(chain["accounts"]), drafted["drafts"]
    assert by_account[chain["first"]]["state"] == "generated"
    held = by_account[chain["second"]]
    assert held["state"] == "review_only", (
        f"绑定与时效都没判过的那一条该落 ``review_only``：{held!r}"
    )

    # 理由要说出**是哪一格不够** —— 合成一个布尔的话，下一步做哪件事就没了依据。
    reasons = "；".join(
        row["reason"] for row in drafted["receipts"] if row["outcome"] == "review_only"
    )
    assert "绑定" in reasons and "当前性" in reasons, (
        f"两条理由各对应一个不同的下一步（去核邮箱归属 / 去重新验证）：{reasons!r}"
    )

    # 而它**真的**走不了：清单只放行 ``generated``，且排除要点名带理由。
    prepared = _ok(
        deployed.run(("prepare_wave", _preparing_payload(chain["cohort_id"])))
    )
    assert prepared["allowed_count"] == 1, (
        f"清单只该放行那一条正常草稿：{prepared['allowed_count']} / "
        f"{prepared['excluded']!r}"
    )
    excluded = {row["recipient"]: row["reason"] for row in prepared["excluded"]}
    assert excluded, "那一条被挡住了，而排除那一栏是空的 —— 它就成了静默丢弃"
    assert all(reason.strip() for reason in excluded.values()), excluded


def test_the_jevs_step_hands_over_the_specs_and_forces_no_call(
    deployed: Deployment, settings: Settings
) -> None:
    """Jev 那一步：**指得路**（确有用途）而**不强制每一次都问**（§B14）。

    §G1 的顺序里这一格写作「Jev（确有用途）」，而 §B14 的关闭条件逐字：「不强迫
    每任务调用 Jev」。两句话合起来是一个要求的两半，所以判据也有两半：

    * **指得路** —— 领活那一份回执要交出 ``decision_specs``。少了那一格，判定是
      「可达而无人指路」：``decide`` 收的是 ``spec_id``，而没有任何一处会把 id 交到
      研究员手里，于是台账恒零行，而那零行与「这份 profile 从来没声明过判定」在
      报告上分不开。
    * **不强制** —— 整条链跑完（受理 → 编译工作单元 → 领活 → 结算），台账上**一行
      都没有**。一次「每个任务都先问一次 Jev」的实现会在这里红，而它在别的每一格
      读数上都正常。

    台账那一格读的是**它的产物**（库里的行），不是「某一次调用没有发生」：一次被
    判过、而报告上说没判过的形状，才是这条判据真正要挡的。读表前先构造一次存储
    （``ensure_schema`` 幂等），于是「表还不存在」不会伪装成「0 行」。

    ## 为什么这一条用 ``second_route="unjudged"``

    ``decision_specs`` 是**领活那一刻**交出去的那一份，所以这一条需要一个**还没被
    领过的**工作单元。走 ``"settled"`` 的那条链把两个工作单元都结算掉了，第二次
    ``get_assignment`` 会被正确地拒（「已经停止（goal_met），不再出租」）—— 那一条
    拒绝是对的，它只是让这一条红在一个与被测行为无关的理由上。
    """
    chain = _run_chain(deployed, settings, second_route="unjudged")

    handed = _ok(
        deployed.run(("get_assignment", {"work_item_id": chain["item_id"]}))
    )["decision_specs"]
    assert handed, (
        "回执一个 ``spec_id`` 都没交出来 —— 那么 ``decide`` 的入口对研究员来说"
        "不存在，而「台账恒零行」与「没有声明过判定」在报告上分不开"
    )
    for entry in handed:
        assert entry["spec_id"], entry

    assert _decision_rows(settings) == 0, (
        "这一趟没有任何一次调用需要判定，而台账上落了行 —— 「不强迫每任务调用"
        "Jev」这句话就只剩下散文了"
    )


def _decision_rows(settings: Settings) -> int:
    """``decision_record`` 现在的行数。

    直接读表而不是走端口：``DecisionRecordStore`` 那一族接口只有「按 record_id 取」
    与「按 case 取」，没有列举 —— 而这一条问的正是「有没有人写过」。
    """
    from marketops.adapters.sqlite.decisions import SqliteDecisionRecordStore

    SqliteDecisionRecordStore(settings.database_path)
    conn = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT count(*) FROM decision_record").fetchone()[0])
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 二、八条「必须失败的对照」
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Control:
    """一条必须失败的对照：它是**什么**，以及**谁**在守着它。

    ``executors`` 里每一项是 ``相对本仓的路径::测试函数名``。引用而不是复述那些
    测试的断言 —— 复述会漂移，而漂移之后两边都读得通。
    """

    name: str
    criterion: str
    executors: tuple[str, ...]


#: §G1 末尾那八条，逐条落到**今天真的在守着它**的那些执行器上。
#:
#: 这张表自己也是可执行的（:func:`test_every_control_names_a_real_executor`）：
#: 一个被改名或删掉的测试会让它红。少了那一条执行器，这张表就只是一段会过期的散文
#: —— 而它正是「八条对照各自有执行器」这句话的唯一载体。
CONTROLS: tuple[Control, ...] = (
    Control(
        name="错国家被标 eligible",
        criterion=(
            "一个国家不在范围内的账号必须被判 irrelevant（规则**真的生效**），"
            "而不是因为那一格没被生产出来而落进 unchecked"
        ),
        executors=(
            "tests/test_grading.py::"
            "test_a_missing_country_leaves_out_of_region_unchecked_rather_than_firing",
            "tests/test_discovery_qualify.py::"
            "test_the_region_fact_has_a_candidate_side_producer",
        ),
    ),
    Control(
        name="目录别人的邮箱被绑定",
        criterion=(
            "同一页上属于另一家组织的邮箱不得归属到目标账号（归因层），"
            "而引用另一账号的证据要整份拒绝（引用层）"
        ),
        executors=(
            "tests/test_domain_attribution.py::"
            "test_another_organisations_email_on_the_same_page_is_rejected",
            "tests/test_mcp_contact_routes.py::"
            "test_evidence_belonging_to_another_account_refuses_the_whole_settlement",
        ),
    ),
    Control(
        name="人工 YAML 是必需",
        criterion=(
            "干净数据根上一个 operator 清单都没有时，发现 → 晋升 → 列账号 → 冻名单 "
            "→ 起草整条链照跑，且**不生成影子 YAML**"
        ),
        executors=(
            "tests/test_mcp_cohorts.py::test_a_clean_data_root_never_grows_a_yaml_list",
            "tests/test_r1_vertical_acceptance.py::"
            "test_the_whole_chain_runs_from_a_clean_root_on_one_mcp",
        ),
    ),
    Control(
        name="重复 route 计新增",
        criterion=(
            "同一次观察重放不产生第二行路线（投影不变、id 相同），重发同一份材料"
            "不写第二条变更信号"
        ),
        executors=(
            "tests/test_route_records.py::test_a_replayed_observation_changes_nothing",
            "tests/test_application_contact_routes.py::"
            "test_re_sending_the_same_material_writes_no_signal",
        ),
    ),
    # 这一条**只有一个**执行器，而那是 2026-09-29 改的：原先第二个是
    # ``tests/test_site_crawl.py::test_a_page_that_failed_is_still_a_receipt``。
    # 它判的是**取不到**（404 也是一条回执、且不落镜像），而本条的 criterion 逐字是
    # 「取回**成功**而正文为空」—— 两者不是同一件事。挂在这里会让「表上有两个执行器」
    # 被读成「这一条被两处守着」，而那正是这张表最会骗人的读法。
    #
    # 撤销之前的两次实测（MAIN），记在这里免得下一个人重新挂回去：
    #   * 变异**生产侧** ``adapters/http.py`` 的镜像条件（``OK``/``EMPTY_SHELL`` → 恒真）：
    #     那条判据**照旧绿** —— 它走的是 ``tests/fixtures/http_fakes.py`` 那个替身，
    #     根本不经过真适配器；
    #   * 变异**替身自己**那一行（``http_fakes.py`` 同一条件）：红在
    #     ``tests/test_site_crawl.py:354``。
    # 换句话说它钉的是替身重写的那份规则，不是生产守卫。**那条判据本身是好的、
    # 留在套件里。**
    #
    # 2026-09-29 补记：它守的那一句（「取不到就不落镜像」）**生产侧那一半**现在有人守了 ——
    # ``tests/test_http_fetch_attempts.py::test_a_not_found_page_is_not_mirrored_while_a_found_one_is``
    # 走真 ``HttpxFetcher`` + 真磁盘，并配一条反面（同一台抓取器取到的那一页**必须**落盘）。
    # 它**仍然不挂在这张表上**，理由没变：本条的 criterion 逐字是「取回**成功**而正文为空」，
    # 而它判的是 **404**。别因为「现在有判据了」就把它挂回来。
    # 同一处变异（生产侧镜像条件补上 ``NOT_FOUND``）的两次读数：上述替身判据**照旧绿**，
    # 新判据**红**在回执那一格（``result.body_path is None``）。
    Control(
        name="空正文声称镜像成功",
        criterion=(
            "取回成功而正文为空时，那一趟**不得**被当成一页内容：工具面要说得出它是"
            "空的（``unparsed``），且那一趟不落证据行"
        ),
        executors=(
            "tests/test_mcp_research_tools.py::"
            "test_an_empty_body_is_not_passed_off_as_a_mirrored_page",
        ),
    ),
    Control(
        name="X 被包装为关键联系人",
        criterion=(
            "等级 X 不产正常草稿，而且它给出的理由是**停它的那条规则**，不是一句"
            "笼统的「不适用」；只有关键联系人的等级进得了第一触达"
        ),
        executors=(
            "tests/test_outreach_contract.py::"
            "test_x_is_not_drafted_and_the_reason_names_the_rule_that_stopped_it",
            "tests/test_outreach_contract.py::"
            "test_only_key_contact_grades_enter_the_first_touch",
        ),
    ),
    Control(
        name="跨 workspace 证据",
        criterion=(
            "另一个 workspace 的行走不到（**拒绝**而不是返回空），写进去要拒绝"
        ),
        executors=(
            "tests/test_evidence_store_contract.py::"
            "test_a_row_of_another_workspace_is_rejected_not_skipped",
            "tests/test_evidence_store_contract.py::"
            "test_writing_an_object_of_another_workspace_is_refused",
        ),
    ),
    Control(
        name="单页坏时间使全批无回执",
        criterion=(
            "一页的时间戳坏掉时，那一页自己落一条结论（``invalid_source_time``），"
            "而**同一批其余各页照常**有回执 —— 坏页放在**中间**也不得带走整批"
        ),
        executors=(
            "tests/test_application_contact_routes.py::"
            "test_one_bad_page_does_not_kill_the_batch",
            "tests/test_application_contact_routes.py::"
            "test_a_malformed_material_is_rejected_without_taking_the_batch",
        ),
    ),
)


def test_every_control_names_a_real_executor() -> None:
    """这张表点名的每一个执行器都真的在盘上、真的是一个测试函数。

    它挡的是一件很具体的事：某一天有人把那条测试改了名或移走了，而这张表**照旧
    读得通** —— 「八条对照各自有执行器」于是变成一句在报告里成立、在仓里不成立的
    话。这与 ``test_every_input_kind_is_either_served_or_has_a_sentence`` 是同一条
    纪律：一句关于覆盖的话，要么有执行器，要么它会烂掉。

    **它不证明那些判据是对的**：「指向哪里」可执行，「说得准不准」只能靠人读。
    这条分界在本仓已经写过一次（``tests/test_job_inputs.py`` 里那一条）。
    """
    import importlib

    repo_root = Path(__file__).resolve().parents[1]
    assert len(CONTROLS) == 8, f"§G1 列的是八条对照，这里有 {len(CONTROLS)} 条"

    for control in CONTROLS:
        assert control.criterion.strip(), f"{control.name} 没有说清判据是什么"
        assert control.executors, f"{control.name} 一条执行器都没有"
        for reference in control.executors:
            path, _, func = reference.partition("::")
            assert func, f"{control.name} 的引用 {reference!r} 没点名测试函数"
            assert (repo_root / path).is_file(), f"{path} 不在盘上"
            module = importlib.import_module(path.removesuffix(".py").replace("/", "."))
            assert hasattr(module, func), (
                f"{control.name} 点名的 {reference!r} 不在 {path} 里 —— "
                "这张表已经在指一个不存在的东西了"
            )


# ----------------------------------------------------------------------
# 三、组合根真的接上了哪几条工具
# ----------------------------------------------------------------------
def test_the_status_page_is_wired_by_the_composition_root(
    deployed: Deployment, settings: Settings
) -> None:
    """§B21 那条续跑状态页在**真的组合根**上是接好的，而且它不建库。

    ## 为什么这一条只能住在这里

    ``tests/test_mcp_continuation.py`` 全篇自己拼处理器表（``continuation_handlers``
    直接构造后递给 ``drive_with_settings``）—— 那是**装置**，不是**部署**。它把
    「组合根漏传了那三个工厂」这一类缺陷挡在测试之外，而那一类缺陷在生产上的
    形态恰好是：这一条工具对**任何**调用都回「尚未接线」，而适配器那一层的七条
    判据全绿。这不是假设 —— ``_compose`` 里少写一个 ``registry=`` 就长成这样，
    而在这一条之前没有任何执行器看得见它。

    ## 判据为什么是 ``no_store`` 而不是「调得通」

    「调得通」在这一条上不成立：一个没接线的实例**也**调得通，它回的是那句
    ``unbacked_message``（那是 ``is_error=False`` 还是 ``True`` 取决于守卫的实现，
    而两边的报告都读得通）。分得开它们的是**回执本身**：没接线的实例答不出
    ``store_state``，而一个干净数据根上的真部署答的是 ``no_store`` —— 那是这一页
    在「还没有产物」这个状态下唯一诚实的答案。

    顺带钉住第二件事：``_compose`` 加这一次调用**一并不建库**。三个存储的构造都会
    建库，而组合根与处理器里各有一道闸挡着它；两道都写在别处，只有「跑一遍真部署」
    能把它们一起验掉。
    """
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"

    payload = _ok(deployed.run(("get_continuation_status", {})))

    assert payload["store_state"] == "no_store", (
        f"组合根没把这一条接好（回执答不出 store_state）：{payload}"
    )
    assert payload["status"] is None and payload["driver"] is None, payload
    assert not settings.database_path.is_file(), (
        "一次只读查询把库建出来了 —— 此后「还没有产物」与「这里本该有东西」在磁盘上"
        "再也分不开，而这一页存在的全部意义就是在什么都没发生时问一句为什么"
    )


def test_the_feedback_loop_is_wired_by_the_composition_root(deployed: Deployment) -> None:
    """§B23 那八条在**真的组合根**上是接好的，而且复核存储真的到了它们手上。

    ## 为什么这一条只能住在这里

    与上面那一条同一个理由，而它在 §B23 上更锋利：``tests/test_mcp_feedback.py``
    的部署走 ``drive_with_settings``（**装置**），而它交出去的 ``feedback`` 表是
    那一份文件自己 ``feedback_handlers(...)`` 拼的。于是「组合根有没有把这一族接上」
    在那份文件里**看不见** —— 症状是那八条在生产部署上落回 ``unbacked_message``
    （``is_error=True``，正文是「尚未接线」），而它全绿。接线之前那份文件甚至更远：
    它照 ``build_server`` 的尾巴手搓了一个服务实例。

    ## 三段判据，各挡一个具体的错法

    *(一) 这一族自己的词汇。* ``submit_feedback`` 的**第一格**是 ``admitted`` ——
    没接线的实例回的是那句「尚未接线」，而 ``_ok`` 按错误通道先判，所以它进不了
    下面任何一句。

    *(二) 复核存储真的到了它手上，而且**真的被读了**。* 这一族有一处引用别处的存储：
    一份补丁挂到哪件复核案上、那件案还在不在。给 ``feedback_handlers`` 的 ``review``
    是 ``None`` 时 ``propose_patch`` **明确不判第三件事**（端口 docstring 逐字：
    「拿不到那个存储时唯一诚实的做法是**不假装核过**」），于是件号写错也照收。
    所以这里拿一个**不存在的件号**去提，它必须被拒 —— 而拒词要说得出是**件不在盘上**，
    不是别的什么。这一格是 ``_compose`` 那两处「必须同源」在**行为**上的唯一读数：
    一个把 ``review`` 漏成 ``None`` 的部署在 (三) 里也是绿的（它报 ``unreadable``），
    只有在 (二) 上红。

    *(三) 不归案的补丁读成 ``not_attached``，不是 ``unreadable``。* 两档在报告上
    都像「还没批」，而处置相反：前者是「去挂一件案」，后者是「去接线」。这条同时钉住
    **读**那一侧（``list_policy_patches`` 读的是同一份存储），而不只是写那一侧。

    ## 断开这一段的一条记录（不求闭）

    这一族里**连读**都会建库：``default_feedback_store`` 的工厂体是
    ``SqliteFeedbackStore(...)``，而它的 ``__init__`` 会 ``ensure_schema``。所以与
    上面那一条相反，这里**不能**断言「跑完库不在盘上」。两者不是同一条纪律的两半：
    续跑状态页存在的全部意义就是在什么都没发生时问一句为什么，而这一族在干净根上
    答的 ``matched: 0`` 是一句关于**反馈**的实话。真要把这一格也做成读面，那是
    ``default_evidence_reader`` 那条路的第二个用户，属于它自己的片。
    """
    submitted = _ok(
        deployed.run(
            (
                "submit_feedback",
                {
                    "kind": "workstream_policy",
                    "idempotency_key": "b23-wired-1",
                    "literal_feedback": "这一批的来源计划配得太宽了",
                    "run_ref": "run-b23-wired",
                    "source_session": "sess-b23-wired",
                },
            )
        )
    )
    assert submitted["admitted"] is True, submitted
    source_ref = f"feedback:{submitted['feedback_id']}"

    def proposal(**overrides: Any) -> dict[str, Any]:
        """一份形状正常的提案。``changes`` 落在 ``source_plan`` 上 —— 它不在那五个
        受保护格里（在的话这一份连构造都构造不出来，而这里要测的是归案那一步）。"""
        arguments: dict[str, Any] = {
            "source_ref": source_ref,
            "changes": ["source_plan"],
            "target_version": 4,
            "monitoring": "下一轮 Run 的来源计划条数",
            "rollback": "回退到第 3 版",
            "risk": "low",
            "rationale": "三次同族观察",
        }
        arguments.update(overrides)
        return arguments

    refused = _refused(
        deployed.run(
            (
                "propose_policy_patch",
                proposal(review_case_id="case-0000000000000000"),
            )
        )
    )
    assert "不在盘上" in refused, (
        "一个不存在的复核案号被收下了 —— 组合根要么没把复核存储交给这一族，"
        f"要么那份校验根本没读到它。拒词是：{refused!r}"
    )
    assert "尚未接线" not in refused, "这一条工具本身没接线，测的就不是归案那一步"

    admitted = _ok(deployed.run(("propose_policy_patch", proposal())))
    assert admitted["standing"] == "not_attached", (
        f"没归案的补丁没读成 not_attached：{admitted}"
    )

    listed = _ok(deployed.run(("list_policy_patches", {})))
    assert listed["count"] == 1, listed
    assert listed["patches"][0]["standing"] == "not_attached", listed
    assert listed["patches"][0]["approved_by"] is None, listed


def test_the_view_and_watch_loops_are_wired_by_the_composition_root(
    deployed: Deployment, settings: Settings
) -> None:
    """§B24 那八条在**真的组合根**上是接好的，而且两族共用同一份视图存储。

    ## 为什么这一条只能住在这里

    与上面两条同一个理由，而它在 §B24 上多一层：``tests/test_mcp_views.py`` 全篇
    自己拼处理器表（``views_handlers`` / ``followups_handlers`` 直接构造后递给
    ``drive_with_settings``）—— 那是**装置**，不是**部署**。它把「组合根漏传了
    那两格」这一类缺陷挡在测试之外，而那一类缺陷在生产上的形态恰好是：这八条对
    **任何**调用都回「尚未接线」，而适配器那一层的一百多条判据全绿。

    ## 承重的是第三段，不是「调得通」

    「调得通」在这一族上不够：一个只接了 ``views`` 而没接 ``followups`` 的实例，
    前两段照样绿。分得开它们的是**两族之间那条链**：

        put_saved_view 存一版 → put_watch **订阅刚存的那一版** → list_watches 读回来

    ``put_watch`` 受理前要按 ``view_id`` 去视图存储里读当前版（``_apply_watches``
    的 ``views.current``），所以两族各指一份存储时这一步会**拒** —— 而那正是
    ``_list_watches`` 的 docstring 点名的那种伪装：「目标没了」是一条**要处置**的
    结论，不是一条接线事实。这一段红的时候，判词会指向视图存储被接了两处，
    而不是指向这八条工具里的任何一条。

    最后一段是同一个事实的**读**侧：``targets_missing`` 那一格由 ``list_watches``
    现读视图存储算出来，所以它非空意味着「订阅写进去的那一版，清单读不到」。

    ## 没有闭的一格（当次记下，不求闭）

    本片这一条**不能**像 §B21 那一条一样断言「跑完库不在盘上」：
    ``default_saved_view_store`` 的工厂体是 ``SqliteSavedViewStore(...)``，而它的
    ``__init__`` 会 ``ensure_schema`` —— 这一族**读也建库**。这不是本片新引入的：
    ``default_feedback_store`` 是同一形状（§B23 那条判据记的是同一格）。
    """
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"

    empty = _ok(deployed.run(("list_saved_views", {})))
    assert empty["count"] == 0, (
        f"干净数据根上视图清单不是空的 —— 组合根要么没接这一族，要么接的不是它：{empty}"
    )
    assert "尚未接线" not in json.dumps(empty), "这一条工具本身没接线，测的就不是视图那一族"

    # 一条 Watch 要绑在一个**在盘上的**场景上（``put_watch`` 的场景判据逐字：一个
    # 不存在的场景后面没有生效版本，也就没有要监控的 aspect）。所以先把场景立起来
    # —— 这一步是这一条的**前置**，不是它要测的东西：它红的时候说明的是
    # ``create_scene`` 那条链断了，而那条链有它自己的判据。
    standing = _ok(deployed.run(("create_scene", {**BASE_CREATE, "activate": True})))
    assert standing["scene"]["state"] == "active", standing

    saved = _ok(
        deployed.run(
            (
                "put_saved_view",
                {
                    "view_id": "view-b24-wired",
                    "name": "B24 接线判据用的那一版",
                    "query": {"filter": []},
                    "expected_revision": None,
                    "visibility": "shared",
                },
            )
        )
    )
    assert saved["revision_written"] == 1, saved

    # 承重的那一步：订阅**刚刚经另一族存下的**那一版。
    watched = _ok(
        deployed.run(
            (
                "put_watch",
                {
                    "watch_id": "watch-b24-wired",
                    "name": "每周看一次",
                    "ceilings": [{"line": "search", "units": 10}],
                    "notify_on": ["changed"],
                    "expected_revision": None,
                    "view_id": "view-b24-wired",
                },
            )
        )
    )
    assert watched["revision_written"] == 1, (
        "订阅没有受理 —— 两族各指一份视图存储时，put_watch 按 view_id 读不到那一版："
        f"{watched}"
    )

    listed = _ok(deployed.run(("list_watches", {})))
    assert listed["count"] == 1, listed
    assert listed["targets_missing"] == [], (
        "刚订阅的那一版被清单报成「目标不在了」—— 与上一条同一处接线，"
        f"而这一格是一条**要处置**的结论：{listed}"
    )
    assert "尚未接线" not in json.dumps(listed), "跟进那一族本身没接线"
