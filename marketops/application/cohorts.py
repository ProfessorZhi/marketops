"""账号清单这一族的两个意思，以及它们各自的读法。

## 这个模块名字下的两件事

* **Demo 队列构成**（本文件上半部分）：§10 要求第一轮准备 10 + 10 + 10 共 30 个
  账号，:class:`CohortPlan` / :func:`load_accounts` / :func:`validate_plan` 是**一份
  实验输入清单**的 schema 与校验。它读 YAML，**不内置任何账号数据** —— 凭空的示例
  账号会被误当成真实结果，那比没有清单更糟。
* **冻结成员名单**（下半部分，§B12）：:func:`freeze_from_pool` / :func:`current_cohort`
  / :func:`cohort_dossiers`。它读**账号池**，产出一份内容寻址的
  :class:`~marketops.domain.cohort.AccountCohort`，让维护、起草与研究三条链不必再
  依赖 operator 手写的 ``inputs/<name>.yaml``。

两件事共用一个模块名不是巧合，而是它们真的同族：都回答「这一批要处理哪些账号」。
不同的是**那句话由谁来说** —— 上半部分是 operator 写在文件里的，下半部分是系统从
自己的池子里读出来的。名字上的这一格就是本仓对「人工桥」这件事的处置：
:class:`CohortPlan` 留着（显式导入仍然兼容），而下游路径不必再经过它。

**两半不互相引用。** ``CohortPlan`` 不知道池子存在，冻结名单也不用
``Cohort``（``domain/enums.py`` 那个三成员闭集）—— 那是 §10 实验的分类，
与「这个市场现在有哪些账号」是两句话。

## 下半部分为什么住在**应用层**

``domain/cohort.py`` 里是一个纯函数（池子里的行 + 一条规则 → 一份冻结名单），
它不读盘也不写盘。把「读池子、冻结、写进去」串起来的那一趟住在这里，与
``application/accounts.py`` / ``application/relations.py`` 同一个理由：
**顺序与失败处置是应用层的事，判定是域层的事。**
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from ..domain.account import MarketAccount
from ..domain.cohort import (
    AccountCohort,
    CohortSelection,
    freeze_cohort,
)
from ..domain.enums import Cohort
from ..domain.errors import ConfigError
from ..domain.evidence import AccountRecord
from ..domain.subject import SubjectDossier
from ..domain.util import sha256_text
from ..ports.pool import AccountCohortStore, AccountPoolStore

ACCOUNTS_FILE = "accounts.yaml"


@dataclass(frozen=True)
class CohortPlan:
    """期望的队列构成。"""

    known_success: int = 10
    accessible_no_core_email: int = 10
    previously_blocked: int = 10

    @property
    def total(self) -> int:
        return self.known_success + self.accessible_no_core_email + self.previously_blocked

    def expected_for(self, cohort: Cohort) -> int:
        return {
            Cohort.KNOWN_SUCCESS: self.known_success,
            Cohort.ACCESSIBLE_NO_CORE_EMAIL: self.accessible_no_core_email,
            Cohort.PREVIOUSLY_BLOCKED: self.previously_blocked,
        }[cohort]


DEFAULT_PLAN = CohortPlan()


def load_accounts(path: Path) -> list[AccountRecord]:
    """从 YAML 装载账号清单。

    文件形状::

        accounts:
          - account_id: example-001
            organisation: Example Ltd
            cohort: known_success
            domain: example.co.uk      # 可选
            notes: ""                  # 可选，勿写 secret
    """
    if not path.exists():
        raise ConfigError(
            f"账号清单不存在：{path}\n"
            f"请按 tests/fixtures/{ACCOUNTS_FILE} 的形状提供 30 个真实账号。"
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"账号清单不是合法 YAML：{path}：{exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise ConfigError(f"账号清单顶层必须形如 {{accounts: [...]}}：{path}")

    accounts: list[AccountRecord] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["accounts"], start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"账号清单第 {index} 项不是映射")
        try:
            account = AccountRecord.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic 校验错误转成配置错误
            raise ConfigError(f"账号清单第 {index} 项非法：{exc}") from exc
        if account.account_id in seen:
            raise ConfigError(f"账号清单存在重复 account_id：{account.account_id}")
        seen.add(account.account_id)
        accounts.append(account)

    return accounts


def validate_plan(
    accounts: list[AccountRecord], plan: CohortPlan = DEFAULT_PLAN
) -> list[str]:
    """检查队列构成是否符合计划。返回问题清单，空列表表示通过。"""
    problems: list[str] = []
    counts: dict[Cohort, int] = {cohort: 0 for cohort in Cohort}
    for account in accounts:
        counts[account.cohort] += 1

    for cohort, count in counts.items():
        expected = plan.expected_for(cohort)
        if count != expected:
            problems.append(
                f"{cohort.value}: 期望 {expected} 个，实际 {count} 个"
            )

    if len(accounts) != plan.total:
        problems.append(f"总数期望 {plan.total} 个，实际 {len(accounts)} 个")

    return problems


# ======================================================================
# 冻结成员名单（§B12）：池子 → 一份内容寻址的读数
#
# 分段线不是排版：上面那一半是**一份实验输入清单**的装载器，下面这一半是它要
# 替掉的那条人工桥。两半共用模块名，理由写在模块 docstring 里。
# ======================================================================

#: 一份冻结名单在 ``SubjectDossier.source_ref`` 里的写法。
#:
#: 走 ``cohort:`` 前缀而不是一个裸 id，与 ``receipt_path`` 那一族同一条：
#: ``source_ref`` 是「这一行从哪来」的唯一一处说法，而一个裸的 ``coh-…``
#: 在回执里读起来像一个账号 id（两者都是 ``<三字母>-<十六位十六进制>``）。
COHORT_REF_PREFIX = "cohort:"


def freeze_from_pool(
    pool: AccountPoolStore,
    cohorts: AccountCohortStore,
    *,
    workspace: str,
    scene_id: str,
    selection: CohortSelection,
    at: datetime,
) -> AccountCohort:
    """这个 Scene 的池子 → 一份冻下来的成员名单，**并且写进去**。

    这一趟是「选 cohort」这个动作的全部：读一遍池子、按一条规则冻结、落盘。它
    **不派发任何工作**，也不花任何钱 —— 冻结是一份读数，不是一次运行。

    ## 为什么 ``workspace`` 是参数，而不是从池子里读

    池子（``AccountPoolStore.list_accounts``）**没有**一个「我是谁」的读：它给的是
    行，每一行自己带 ``workspace``。所以它答不了「这个池子属于哪个 Scene」——
    而这个问题必须由调用方回答，因为答案在 **Scene 记录**上（``record.workspace``）。

    ## 但「池子绑在另一个工作区上」必须**当场拒**

    这一条是承重的，而且它不是理论上的：库是绑工作区的
    （``adapters/sqlite/db.py::bind_workspace``），所以一个属于工作区 B 的 Scene
    在一个绑在 A 上的库上问「我的池子有什么」时，得到的是 **A 的那些行** ——
    没有任何一处会报错。照单全收的后果是：一份标着 B 的名单里装着 A 的账号，
    而它会被派出去研究。所以下面逐行核一次 ``account.workspace``，不匹配就整份拒。

    池子是空的（新库、还没绑过）时没有行可核，这时不拒：那一份名单是**空的**，
    而「空的」在下面 :func:`cohort_dossiers` 那一侧是一条说得出口的阻塞，
    不是一次静默的成功。

    Raises:
        ConfigError: 池子里有行不属于 ``workspace``。**整份拒，一行都不冻** ——
            冻结一份混了两个工作区的名单，比不冻更坏：它会落盘、会内容寻址、
            会被当成一次合法的读数回读。
    """
    accounts = pool.list_accounts()
    foreign = sorted({row.workspace for row in accounts} - {workspace})
    if foreign:
        raise ConfigError(
            f"这个池子里的行属于 {foreign}，而本次要冻的是 {workspace!r} 的名单。\n"
            "库是绑工作区的：一个属于别的 Scene 的池子在这一问上**不会报错**，它只会把"
            "另一个 Scene 的行交出来。照单收下的后果是一份标着本 Scene、装着别人账号的"
            "名单 —— 而它会内容寻址落盘、被当成一次合法读数回读，然后被派出去研究。\n"
            "正确做法：这个 Scene 用自己的库（或先确认调用方拿的是哪一个库）。"
        )
    return cohorts.put_cohort(
        freeze_cohort(
            accounts,
            workspace=workspace,
            scene_id=scene_id,
            selection=selection,
            at=at,
        )
    )


def current_cohort(cohorts: AccountCohortStore, *, scene_id: str) -> AccountCohort | None:
    """这个 Scene **最近**冻的那一份，或 ``None``（还没冻过）。

    ## 为什么这条读住在应用层

    ``AccountCohortStore`` 上**没有** ``latest_cohort``，而且不该有
    （``ports/pool.py`` 的模块 docstring 逐字写了理由）：排序键只能有一处定义，
    写成存储上的一个方法会让「哪一份是当前的」有第二个定义处。所以那处定义留在
    :meth:`AccountCohortStore.list_cohorts` 里（``(created_at, cohort_id)``，早的
    在前），而这里只做两件事：按 ``scene_id`` 过滤、取最后一个。

    ``None`` 与「有一份空名单」是两件事：前者是「还没选过」，后者是「选过，
    而池子里当时没有可用的账号」。调用点要把它们分开报 —— 合成一句会让一次
    「还没选 cohort」读成「这个市场没有账号」。
    """
    for cohort in reversed(cohorts.list_cohorts()):
        if cohort.scene_id == scene_id:
            return cohort
    return None


def _member_sha256(cohort_id: str, account: MarketAccount) -> str:
    """这一行主体的 ``input_sha256``：**（哪一份名单，池子里那一行的当前字节）**。

    两格都进原像，与 ``inputs._input_row_sha256`` 的 ``(input_ref, row)`` 逐字同形
    —— 同一个账号出现在两份名单里是两行输入，而「这一行被改过没有」问的正是这两
    格合起来的那一份字节。

    序列化用 ``model_dump(mode="json")`` + ``sort_keys`` + 紧凑分隔符，理由与
    ``findings_sha256`` 相同：默认输出带空格，同一份记录在不同 Python 版本上可能
    给出不同空白 —— 那时同一个内容会算出两个散列，一次重发被读成一次冲突。
    """
    payload = {"cohort_id": cohort_id, "account": account.model_dump(mode="json")}
    return sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


@dataclass(frozen=True)
class CohortReading:
    """一份冻住的成员名单，**连同它自己的时刻**，以及从它编译出来的主体。

    ## 为什么「时刻」要跟着主体一起回来

    一个只拿到主体序列的读者，要回答「我这一批读的是**哪一份**」时只剩一条路：
    再往存储里读一遍名单。而那是**第二次读** —— 两次读之间库可以被换过（本文件与
    ``adapters/mcp_server/product_loop.py`` 都逐字写着这一条）。于是两份回执各自
    自洽，读的却是两份不同的名单，而这件事在报告上看不出来。

    §B25 要求所有 session 读到**同一个** ``as_of``，所以那个时刻必须与主体序列
    **同源**：谁读出了这份名单，谁就把它的 ``created_at`` 一起交出去。这就是这一
    型存在的全部理由 —— 它不是一个方便，它是一条判据的载体。
    """

    cohort: AccountCohort
    dossiers: tuple[SubjectDossier, ...]

    @property
    def as_of(self) -> datetime:
        """这份读数**自己的**时刻：「我们第一次把它冻下来」那一刻。

        ``AccountCohort.created_at`` 只往早走（``put_cohort`` 保留更早的那一个），
        所以同一个 ``cohort_id`` 在任何时刻、任何 session 读回来都是这一个值 ——
        它够格当 ``as_of`` 靠的正是这条性质，而不是「它看起来像个时刻」。

        **它不回答「这一次读发生在什么时候」**：那是另一格，另一个名字（读侧各自
        取自己的时钟）。把两者合成一格，会让新鲜度判定拿一个冻住的时刻当基准。
        """
        return self.cohort.created_at


def read_cohort(
    pool: AccountPoolStore,
    cohorts: AccountCohortStore,
    *,
    scene_id: str,
    cohort_ref: str,
) -> CohortReading:
    """一份冻结名单 → **它自己**（带时刻）与一串主体身份，**按名单里的成员顺序**。

    Args:
        pool: 账号池。**必须读**：名单冻的是「这一批是谁」，而身份线索（域名、
            国家）不在名单上 —— 见下。
        cohorts: 冻结名单的存储。
        scene_id: ``cohort_ref`` 为空时按它取**当前那一份**。
        cohort_ref: 一份名单的 id，或 ``""``＝这个 Scene 最近冻的那一份。

    ## ``source_ref`` 是 ``cohort:<id>``，不是池子里的行

    回执要能回答「这个名字是谁说的」。名单那一侧回答了「这一批是谁选的、按什么
    规则选的、谁被排除和为什么」（:func:`~marketops.domain.cohort.freeze_cohort`），
    所以来源引用指的是它。指池子里的行是答错了问题：池子只说得清「这一行现在
    是什么」，说不清「它为什么在这一批里」。

    ## 身份线索从**池子**现读，不从名单里读

    ``CohortMember`` 只有 ``account_id`` / ``organisation`` / ``status`` 三格，
    没有域名也没有国家。这是刻意的分工：名单冻的是**成员与顺序**（那两格决定
    ``work_item_id``，所以必须冻住），而身份线索是**当下这件事需要的最好的线索**
    （一个刚被补上的官网域名，不该因为名单是昨天冻的就用不上）。

    推论要写下来：**同一个 ``cohort_id`` 在两次编译里可能给出两份不同的
    ``work_item_snapshot``** —— 池子里那一行被改过的时候。这不是 bug，
    ``input_sha256`` 存在的理由正是让这一点**可见**（它变了，所以那一次是新输入，
    而不是一次静默的重编译）。

    ``domain_verdict`` 一律 ``unknown``：池子那一行记的是**有哪些主机**，不记
    「那是谁证的」。把它读成 ``confirmed`` 会让绑定那一层开始拒跨域邮箱
    （``domain/bind.py`` 只把 ``confirmed`` 读成权威）—— 那是一次**没有依据的**
    业务判定，而它的表现是一封本该发出的信被拦下。所以这里退回缺省，
    宁可让这一条走「条目内证据」那条路。

    Raises:
        ConfigError: 名单读不出来（没冻过 / id 不存在）、名单是空的、或者名单里
            有账号在池子里查不到。**整份拒，不跳过那一行** —— 与
            ``known_account_dossiers`` 那条路径同一个位置、同一条理由：
            跳过会让一份十二行的名单编译出十一个工作单元，而报告里那个「11」
            与「名单里本来就只有 11 个」看起来一样。
    """
    cohort = (
        current_cohort(cohorts, scene_id=scene_id)
        if not cohort_ref
        else cohorts.get_cohort(cohort_ref)
    )
    if cohort is None:
        if not cohort_ref:
            raise ConfigError(
                f"场景 {scene_id!r} 还没有冻过任何成员名单。\n"
                "维护、起草与研究这三条链的主体从前来自 operator 手写的 "
                "``inputs/<name>.yaml``；现在它们可以先从池子里读一份冻下来"
                "（``freeze_cohort``），而这一句说的正是「还没有读过」。\n"
                "先 freeze_cohort 一次、再拿它的 cohort_id 来（或留空表示"
                "「用最近那一份」）；报里那个空的主体序列不是「这个市场没有账号」。"
            )
        raise ConfigError(
            f"读不到成员名单 {cohort_ref!r}。\n"
            "id 是内容寻址的（``coh-`` + 十六位），所以一个抄错一个字符的 id 与一个"
            "从来没存在过的 id 在这里长得一样 —— 本层说不出是哪一种。\n"
            "用 list_cohorts 列出这个库里真的有的那些。"
        )
    if cohort.scene_id != scene_id:
        raise ConfigError(
            f"成员名单 {cohort.cohort_id!r} 属于场景 {cohort.scene_id!r}，"
            f"而本次问的是 {scene_id!r}。\n"
            "名单是**逐 Scene** 的：同一份池子在两个 Scene 下选出来的成员可以不同"
            "（池子里的行本身就逐 Scene），所以一份名单跨 Scene 复用等于把另一个"
            "市场的主体拿来这里研究。"
        )

    members: list[SubjectDossier] = []
    missing: list[str] = []
    for member in cohort.members:
        account = pool.get_account(member.account_id)
        if account is None:
            missing.append(member.account_id)
            continue
        members.append(
            SubjectDossier(
                account_id=account.account_id,
                organisation=account.organisation,
                domain=account.domains[0] if account.domains else "",
                country=account.country,
                source_ref=f"{COHORT_REF_PREFIX}{cohort.cohort_id}",
                input_sha256=_member_sha256(cohort.cohort_id, account),
                domains=tuple(account.domains),
            )
        )

    if missing:
        raise ConfigError(
            f"成员名单 {cohort.cohort_id!r} 里有 {len(missing)} 个账号在池子里查不到："
            f"{sorted(missing)}。\n"
            "这一份名单是从**这个**池子里读出来的，所以查不到只有两种可能：库被换过"
            "（或读的是另一个工作区的库），或者有人在名单冻好之后把那些行取走了。\n"
            "两种情况下「这个主体现在是什么」都没有答案 —— 拿一个 id 去检索而不带"
            "身份线索，正是 worker 把预算花在同名物上的那个形状。所以整份拒，"
            "一个 work item 都不建。"
        )
    if not members:
        raise ConfigError(
            f"成员名单 {cohort.cohort_id!r} 里一个成员都没有。\n"
            f"它是冻好的、读得回来的一份读数，而它的排除栏里有 "
            f"{len(cohort.exclusions)} 条原因码 —— 逐条读得出「为什么一个都没进」。\n"
            "这一句必须与「还没冻过名单」分开：那个是「还没选」，这个是「选过、"
            "结果为空」。一个没有任何 work item 的作业在报告里长得像『已经做完』"
            "—— 它其实什么都没跑。"
        )
    return CohortReading(cohort=cohort, dossiers=tuple(members))


def cohort_dossiers(
    pool: AccountPoolStore,
    cohorts: AccountCohortStore,
    *,
    scene_id: str,
    cohort_ref: str,
) -> tuple[SubjectDossier, ...]:
    """一份冻结名单 → 一串主体身份。**只有主体，不带时刻。**

    这一支留给「只要主体」的调用方（``application/inputs.py`` 之外还有测试与
    语言之外的读者）。要那一格 ``as_of`` 就用 :func:`read_cohort` —— 它把名单
    自己一起交出来，理由见 :class:`CohortReading`：时刻与主体必须同源，否则
    「我读的是哪一份」要再读一遍存储才能答，而那第二遍可以与第一遍分家。
    """
    return read_cohort(
        pool, cohorts, scene_id=scene_id, cohort_ref=cohort_ref
    ).dossiers


__all__ = [
    "ACCOUNTS_FILE",
    "COHORT_REF_PREFIX",
    "CohortPlan",
    "CohortReading",
    "DEFAULT_PLAN",
    "cohort_dossiers",
    "current_cohort",
    "freeze_from_pool",
    "load_accounts",
    "read_cohort",
    "validate_plan",
]
