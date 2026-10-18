"""契约导出：把领域模型变成 ``contracts/*.json``。

契约**从代码导出**，不手写。手写的契约一定会和代码漂移，
而 ``tests/test_contracts_sync.py`` 会在漂移时失败 —— 这比「记得同步文档」可靠。

导出方式（**规范命令**，也是那条测试失败信息里印出来的那一条）：
    python -m marketops.schema
``python -m marketops.interfaces.cli export-contracts`` 转调本模块的
``write_contracts``，两条命令做的事逐字节相同。

## 它为什么留在顶层，而不是搬进 ``interfaces/``（B7b）

§2 的目标树里顶层只有 ``config/`` 与 ``security/`` —— 本模块**不在**那份名单里。
B7 给它的结论是**留在顶层**，理由是它不属于任何一层：

* 它不是**入口**。``interfaces/`` 是 MarketOps 的运行时表面 —— 外面的人按一个名字
  叫这一栈东西。本模块的消费者是**构建期**：开发者与 CI，在提交之前跑一次。
  把构建工具放进入口层，``interfaces/`` 就同时意味着「产品表面」与「构建步骤」，
  而这两件事的唯一共同点是"可以被调用"。
* 它不是**领域**、**端口**、**应用**或**适配器** —— 它对这四层都只是**读**。
  它读 ``domain/`` 的模型与 ``config.REPO_ROOT``，写出一个目录。判据
  （``adapters/__init__.py`` 开头：「把它 import 进来会不会需要磁盘、网络或一个
  进程？」）对 import 的答案是"不需要" —— 只有调用 ``write_contracts()`` 时才写盘，
  且写到一个显式给出的路径。所以它连 adapter 都不是。
* ``CONTRACT_MODELS`` 这张**权威登记表**被 ``tests/test_contracts_sync.py`` 当作
  判据来源读。让测试从入口层读权威，是把层级读反了。

这是一处**有意保留的偏离**，不是遗漏：``config.py`` 同理（目标树里是 ``config/``
一个包，落地是一个模块）。两处都写在这里，免得下一个人以为是没人管。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import REPO_ROOT
from .domain.entities import (
    AccountVenueRelation,
    ContactRoute,
    Person,
    ResponsibleEntity,
    Role,
    RouteOpportunity,
    VenueAsset,
)
from .domain.account import MarketAccount
from .domain.candidate import AccountCandidate
from .domain.cohort import AccountCohort
from .domain.evidence import AccountRecord, Evidence, ResearchRun
from .domain.fetch_attempt import FetchAttempt
from .domain.receipt import SourceReceipt

#: 契约文件名 → 模型。文件名即契约名，被 services / 飞书 / 未来 API 引用。
#:
#: ``contact-candidate`` 已改名为 ``contact-route``：契约名会被下游
#: （飞书主表、未来 API）引用，所以改名就是一次**破坏性变更**，
#: 不能悄悄留个旧名字当别名 —— 留着它就等于同时存在两个名字，而下游只会用其中一个。
CONTRACT_MODELS = {
    # 研究输入与依据
    "account-record": AccountRecord,
    # 发现期的候选账号 —— **不是**「字段不全的 account-record」：
    # ``Cohort`` 是闭集而 ``account-record`` 的 ``required`` 含 ``cohort``，
    # 发现出来的账号没有也不可能有诚实的 cohort，所以它是另一个类型。
    "account-candidate": AccountCandidate,
    # 市场里的一行账号（§6）—— 这是账号这一族的**第三个**名词，与上面两个都不同：
    # 它是**写进池子的一行**（有血统、有证据、有新鲜度、有本 Scene 的准入判定），
    # 不是一份输入清单里的行、也不是一条待物化的候选。三者各自存在，因为
    # ``cohort`` 只属于清单那一侧，而候选那一侧连 ``account_id`` 都还没有。
    "market-account": MarketAccount,
    # 一份**冻结的成员名单**（§B12）—— 账号这一族的第四个名词，与上面三个都不同：
    # 它不说「有哪些账号」，它说**某一次读池子读到了谁、按什么规则读的、谁没被读进来
    # 以及为什么**。它必须是对下游可见的一型，因为「这一批研究的是谁」是下游
    # （飞书主表、未来 API）按 id 回查的东西，而回查到的那一份要读得懂。
    "account-cohort": AccountCohort,
    "evidence": Evidence,
    "research-run": ResearchRun,
    # 一次访问的**实测记录**（采集设计 §3）。它是本仓第一批「观测」对象之一，
    # 与 evidence 是两个层次：evidence 说的是「我们据此认为业务事实是什么」，
    # 它说的是「那个 URL 那天到底发生了什么」。前者可以没有（没抓到就没有证据），
    # 后者**每一次尝试都有**，包括失败的那些 —— 一份只记成功的采集账回答不了
    # 「为什么这个来源一直没产出」。
    "fetch-attempt": FetchAttempt,
    # 母池入口：一次「把上游母池读进来」之后，**每一行去了哪里**。
    # 它不是第三张 ``account-record``：``AccountRecord`` 的 ``cohort`` 是闭集且必填，
    # 而回执记的是**发现期/母池期**的行，那一层不产生账号（见 ``domain/receipt.py``）。
    "source-receipt": SourceReceipt,
    # 领域模型：场地资产、责任实体与它们的关系（SOP L78）
    "venue-asset": VenueAsset,
    "responsible-entity": ResponsibleEntity,
    "account-venue-relation": AccountVenueRelation,
    "person": Person,
    "role": Role,
    # 产出
    "contact-route": ContactRoute,
    "route-opportunity": RouteOpportunity,
}

CONTRACTS_DIR = REPO_ROOT / "contracts"


def build_contracts() -> dict[str, dict]:
    """生成全部契约（内存中）。"""
    return {
        name: model.model_json_schema(mode="serialization")
        for name, model in CONTRACT_MODELS.items()
    }


def contract_path(name: str) -> Path:
    return CONTRACTS_DIR / f"{name}.schema.json"


def write_contracts(*, directory: Path | None = None) -> list[Path]:
    """写盘。返回写入的文件列表。"""
    target = directory or CONTRACTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in build_contracts().items():
        path = target / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",  # 强制 LF：Windows 生成的文件在 Linux 上不应显示为全文件改动
        )
        written.append(path)
    return written


def main() -> int:
    for path in write_contracts():
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CONTRACT_MODELS", "CONTRACTS_DIR", "build_contracts", "write_contracts", "main"]
