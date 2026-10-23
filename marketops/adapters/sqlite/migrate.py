"""v1 → v5：先给三张表里每一行补上血统（**只补证得出来的那些**），再把三张维护表
的 ``aspect`` 从五个面换成七个面（**只改认得出来的那些**），然后把三张配置表的主键
从 ``(id)`` 换成 ``(id, revision)``，最后把 ``contact_route`` 从一张**事件流**拆成
「不可变历史 + 当前投影」。

    python -m marketops.adapters.sqlite.migrate [--db PATH] [--profile NAME]
                                               [--inputs-dir PATH] [--no-archive]

## 四步，一次运行

| 步 | 改什么 | 判据 |
|---|---|---|
| v1 → v2 | 给 ``evidence`` / ``contact_route`` / ``route_opportunity`` 的每一行补血统（``workspace``） | ``application/lineage.py``：**证得出来**吗 |
| v2 → v3 | 把 ``observation`` / ``change_signal`` / ``maintenance_claim`` 的 ``aspect`` 换成新词表 | 本模块：这个值**认得出来**吗 |
| v3 → v4 | 把 ``outreach_family`` / ``outreach_writing_policy`` / ``outreach_claim`` 重建为 ``(id, revision)`` 主键，每一行回填 ``revision = 1`` | 本模块：这一行**读得出来**吗 |
| v4 → v5 | 把 ``contact_route`` 的每一行搬成 ``route_observation`` 里的一条观察，按规范键折出 ``contact_route`` 的当前投影 | 本模块：这一行**读得出来**吗 + 折得出来吗 |
| v5 → v6 | 重建 ``research_job``，让 ``input_kind`` 那一格的 CHECK 认得 ``cohort`` | 本模块：这张表认得的**词**是不是代码这一份 |

五步的失败方向不同，而这是它们唯一值得分开说的差别：第一步的东西要**被证明**（证明
来自部署自己的输入，见下），第二步的东西只要**被认出来**（词在不在旧词表里），第三
第四步的东西要**读得出来**（重建之后那一行还得过得了它自己的模型），第五步的东西只要
**说得一样**（盘上那一格上的词与代码那一份是不是同一批）。认不出来时**都是整份拒绝**，
都不是跳过。

五步还差一处：**第三、四、五步都改形状**。前两步是 UPDATE，后三步是重建（SQLite 改不了
主键，也改不了 CHECK 约束），所以它们多守一条 —— 建回来的那张表必须与代码认定的形状
逐字一致，而那一份形状只有一处（``db.CONFIG_TABLE_DDL`` / ``db.ROUTE_TABLE_DDL`` /
``db.JOB_TABLE_DDL``）。理由写在 ``_apply_revisions`` 那里。

第五步还有一件别步没有的事：它要 **DROP** 一张被别人引用着的表（``work_item.job_id``
指向 ``research_job``），而 SQLite 的外键强制只能在**事务之外**关。于是整个迁移不再是
「事务里什么都不用管」的一个环境 —— 那一步真的要做时，外键在这一整个事务期间是关的，
对账因此从「SQLite 会拦」变成「这一步自己算」。见 ``_plan_job_kind`` 与
``_require_job_rows_kept``。

一个库里五步都要跑时，它们共用**一次备份**、**一个事务**、**一个版本号**。分成几次
运行没有意义：没有任何一个中间状态（v2 的血统补齐了、词表还没换）是有人想要的。

## 五步：preflight → backup → migrate → verify → start

§15(c) 要求的形状是这五步，两头不在本模块，只有中间三步在这里：

| 步 | 在哪 | 做什么 |
|---|---|---|
| preflight | ``db.ensure_schema``（服务的只读探问走 ``adapters/sqlite/readiness.py``） | **只读**：这个库的版本与代码期望的那个一致吗。不一致时服务**起不来**，并直接说出「schema 是 v1、代码期望 v5、跑这条命令」—— 而不是把一次版本落后表现成「工具坏了」 |
| backup | 本模块 | 在线备份 + 回读校验版本；写不完就删掉半截文件并中止 |
| migrate | 本模块 | 一切判断都在任何一次写入之前做完，写入是一个事务 |
| verify | 本模块 ``_verify_migrated`` | **写完从盘上读回来**核六格（版本 / 绑定 / 逐行的章 / 旧词表 / 回执的键 / 路线拆表对账） |
| start | 服务自己的启动路径 | 前面都过了才起来 —— 本模块跑完就退出，它不启动任何东西 |

verify 与 preflight 是**同一个问题的两端**，不是同一句话写两遍：preflight 读的是
「这一次运行之前」的库，verify 读的是「这一次运行之后」的库，中间隔着一次提交。
少了后一半，「已经迁完了」与「迁完了、而且是那个样子」在报告里逐字相同。

## 第一步在修什么

``evidence`` / ``contact_route`` / ``route_opportunity`` 的 v1 行只有 ``account_id``，
没有 Scene 这一维。补上 ``workspace`` 这件事**不能**由「缺省 = 当前部署」完成：
那样一来，一个从别的部署复制过来的库、一个被两个 Scene 共用的库、一个拿错了
``MARKETOPS_DATA_ROOT`` 的进程，都会把自己的名字写到别人的事实上，且不留痕迹。

所以这里没有「回填」这个动作，只有**盖章**：每一行都要先被证明属于本部署，才写进
去；证不出来的行**整份拒绝并点名**（``domain/evidence.py`` 的
``Evidence.workspace`` 那句「不要静默给一个值」的落地）。

证明从哪来、每一档证明强在哪，写在 ``application/lineage.py`` 的模块 docstring 里
（那一份是**判据**，本文档是**执行**）。

## 可信来源是**部署配置**，不是这一行的内容

``workspace`` 的取值只有一个来源：调用方给的 ``profile``（``main()`` 里取自
``Settings.profile`` —— 与 ``RoleGuard`` / ``_require_scene`` 用的是同一个值）。
每一行的内容里**没有**任何一格能推它出来，这正是这一格缺了这么久的原因。

## 第二步在修什么（v2 → v3）

``Aspect`` 从五个面变成七个（§11 逐字要求的那七个）。存量的 ``aspect`` 那一格因此
分成两类，而**两类各自的处置只有一个是对的**：

* **换名字**（``source_supply`` → ``source_coverage``、``responsibility`` →
  ``account_relation``）：这是**同一格**。改名字不改身份，所以三张表一律就地改写。
* **被拆开**（``asset_fact`` → ``asset_identity`` + ``asset_attribute``、
  ``role_email`` → ``contact_person_role`` + ``email_evidence``）：粗面在新词表里
  **没有对应的那一格**，于是处置变成一个**有方向**的问题，而三张表的答案不同 ——
  逐表的理由写在 ``SPLIT_DISPOSITION`` 那里，挨着它自己。

回执那一侧另有一条**只有它**才有的约束：``maintenance_claim.dedupe_key`` 里编着
aspect 的值（``domain.maintenance.due_dedupe_key``），所以改名不能只改那一列 ——
键要按**同一个函数**重算，而行里存着的旧键**必须先被验证过一次**。存量的键与它自己
那几列算出来的键不一致时，那一行不是它自己说的那个东西，整份拒绝。

## 第四步在修什么（v4 → v5）

``contact_route`` 一直是一张**追加式**的表：一轮研究插一行，读的人拿到历来写过的全部
行。那是「事件流」的形状，而它的两个后果都不是想要的：

* 「这个账号现在有几条路线、各是哪一级」要读的人自己在几十行里按时间挑 —— 于是一个
  落后三步的 worker 提交的一行会静默改掉上面那个答案；
* 「我们看到过什么」与「现在算什么」用同一行表达，于是**没有任何一处**同时留得下
  两者：改写当前读法就是覆盖掉它上一次的样子。

v5 把它拆成两张表，而旧表的每一行**都是**一次观察 —— 那是它的本来面目，只是它当时
没有名字。所以这一步不是一次翻译，是一次**认领**：给每一行补上 ``canonical_key`` /
``observed_at`` / ``observation_id`` 三格，全部搬进 ``route_observation``（**一行不
丢**），再按规范键折出 ``contact_route`` 的当前投影。

**折叠是有损的**：同一个规范键下有 N 行观察时，投影只有一行。这一点必须在对账里对得
上 —— ``观察行数守恒``、``投影行数 ≤ 观察行数且每个键恰好一行``。历史那一侧因此是这
一步唯一不可替代的产物：折掉的那些读法只在那里。

``observed_at`` 是这一步唯一一处**要挑**的取值：旧行没有它。两档见
``_plan_routes`` 的 docstring，而报告里要按档分开数 —— 第二档是退让，不是同一个值。

## 第五步在修什么（v5 → v6）

``research_job.input_kind`` 那一格的 CHECK 是 ``JobInputKind`` 那份词表的落库形态，而
v5 写下的那一份只列了三个成员。第四个（``cohort``，按冻结的那一份队列提交研究）加进
枚举时**这个词没有跟着进库**，于是 ``admit`` 的那句 INSERT 撞在 CHECK 上，报出来的是一句
``sqlite3.IntegrityError: CHECK constraint failed`` —— 读起来像存储坏了，而事实是
「这张表不认识这一版代码的枚举」。这不是「少了一个可选值」：按 cohort 提交研究在这张表
上的每一次尝试都会失败。

前四步动的是**列**与**表是什么**，这一步动的是**约束**，而它是三者里最难改的一种：
``ALTER TABLE`` 没有改 CHECK 这一项。所以处置是「按新形状建一张 → 搬行 → 丢旧的 →
改名」四步，与第三、四步那套「改名搬开」不同 —— 而**必须**不同的理由是一句会静默出错
的话：改名搬开时 SQLite 会把引用它的那一句（``work_item.job_id`` 的 ``REFERENCES``）
一起改写成指搬开的那张表，于是删掉搬开的那张之后，外键指向一张不存在的表，而盘上
一切正常（``ensure_schema`` 不看外键指向哪）。四步的顺序与理由写在 ``_apply_job_kind``。

判据是**这张表认得的词**，不是它的建表语句逐不逐字相同 —— 后者在这个环境下永远收敛
不了：SQLite 存下来的语句会被它自己改（``ALTER TABLE ... RENAME`` 之后那一句变成
``CREATE TABLE "research_job"``，``IF NOT EXISTS`` 也不见了），于是「文本不同 ⇒ 重建」
会让这条命令每跑一次都重建一遍。读得出词与读不出词的两档见 ``_plan_job_kind``。

这一步还带出两件别步没有的东西：

* **外键强制要在事务之外关**（``PRAGMA foreign_keys`` 在事务里是空操作）。关掉的后果
  是「SQLite 不会替你拦下搬漏的行」，所以这一步自己算：提交**之前**逐行对账（计划里
  记下的每一个 ``job_id`` 都还在、引用它的每一行的那一格都还指得到），见
  ``_require_job_rows_kept``。只在第五步真的要做时才关 —— 另外四步仍然在外键强制下跑。
* **引用者那份名单是问出来的，不是写下来的**（``_foreign_keys_to``）：今天只有
  ``work_item`` 引用 ``research_job``，而写死它的话，下一个引用者出现时这一格会
  **悄悄少一件事**，核出来的结论还是「都指得到」。

## 备份在哪、叫什么名、失败了怎么办

* **位置**：与库**同一个目录**（``<db>.v1-<UTC 时间戳>.bak``）。同一目录是为了
  「把库挪走时备份跟着走」，也让备份与它备份的东西在同一个文件系统上（一次写完
  一次 rename，不会跨设备）。
* **命名**：``marketops.db.v1-20260924T101500Z.bak``。``v1`` 写的是**这次运行从哪
  个版本起步**（于是 1 → 3 的运行留下的是 ``.v1-``，而 2 → 3 的是 ``.v2-``），
  时间戳是 UTC（``%Y%m%dT%H%M%SZ``）—— 本机的账单与日志都以 UTC 为准，备份名混用
  本地时区会让「哪个先」在跨时区时读错。
* **存档那一侧的备份**（``state/evidence/*.jsonl`` 等）带的是 ``.lineage-`` 而不是
  版本号：JSONL 存档**不带版本**，它的版本是库的。写一个 ``v2`` 进去只会造出一份
  关于别的东西的声明。
* **怎么做的**：``sqlite3.Connection.backup``（在线备份 API），不是 ``cp``。WAL 模式下
  复制文件会漏掉 ``-wal`` 里尚未 checkpoint 的页，而那正是最近写进去的那些行。
* **失败处置**：备份写不完就**删掉半截文件并中止**，一个字节的数据都不改。备份写完
  之后**验一次**（打开它、读它的 ``schema_version`` 必须是迁移前那个值）—— 一个打不开
  的备份不是备份。**同名备份已存在时不覆盖**：先处置那一份，因为「今天的备份」与
  「上一次迁移的备份」混起来之后，回滚会回到一个没人知道是哪一刻的状态。
* **回滚**：把备份覆盖回原路径即可（库是自包含的一个文件；``-wal`` / ``-shm`` 需要
  一起删掉，否则旧的 WAL 会被当成新库的日志重放）。

## 原子性：库是**一次事务**，存档是**一个文件一次替换**

* 库：算完所有行、确认没有一行证不出来、也没有一个词认不出来之后，一个
  ``BEGIN IMMEDIATE`` 事务里改完两步的每一行 + 版本 + 绑定，然后提交。中途任何异常
  都回滚，库回到起步时那个版本。
* JSONL 存档（``state/evidence`` 等）：每个文件先备份、再写临时文件、最后
  ``os.replace`` 原子替换。**跨文件没有原子性** —— 一次中途失败会留下一部分文件已迁移、
  一部分没有，而异常消息点名的是停在哪一个。重跑即可：已经迁移过的文件按「这一格
  已经在，且等于本部署」跳过，不会重复盖。

## 重跑与「已经是这一版的库」

已经迁完的库不会被重新盖章：那次运行只做两件事 —— 核对 ``schema_meta`` 里的绑定
与请求的 profile 是否一致（不一致就拒绝：这个库属于别人），以及把存档那一侧补完。
所以一次在存档阶段失败的迁移，重跑一次就能收敛。

## 它**不**做的事

* 不建库。库不存在 → 拒绝（「迁移」不是「初始化」；建一个空的 v5 库由第一次写入
  自己完成）。
* 不改任何 ``account_id``，不改任何一行的**主语**。第一步只在 payload 里加一格，
  第二步只改 ``aspect`` 那一格（外加由它派生的 ``dedupe_key``），第三步只给每一行
  补上 ``revision = 1`` 那一格并把表按新主键重建，第四步只给每一行补上
  ``canonical_key`` / ``observed_at`` / ``observation_id`` 三格，第五步只换
  ``research_job`` 的那一处 CHECK 并原样搬行 —— 五步都不给任何一行换主语。
* **第四步不改任何一条路线的等级、证据或主语**。折出来的那一条当前读法与旧表里
  最新那条读法**逐位相同**（``fold_observation`` 取 ``(observed_at,
  observation_id)`` 最大的那一条），差别只在它现在带着「这是第几次改写」和
  「上一次是什么」这两格账。
* **第二步会删掉少数几行**：被拆开的那两格在 ``observation`` 与 ``maintenance_claim``
  里留下的行，新词表里没有它们对应的那一格（理由在 ``SPLIT_DISPOSITION`` 那里）。
  删了多少行、为什么，逐表进报告 —— 而它们逐字都在备份里，回滚就能拿回来。
* 不**盖章** ``research_job`` / ``work_item`` 等表：它们本来就带 ``scene_id``
  （作业的 Scene 是受理那一刻冻住的），缺这一格的只有第一步那三张。第五步会重建
  ``research_job``，但它改的是那张表的**约束**，不是它哪一行说的是谁 —— 那些行原样
  搬过去，``scene_id`` 一个字都不动。
* 不自动跑。``ensure_schema`` 拒绝旧版本的库并**点名这条命令**，但不会替你执行它。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...application.lineage import InputProof, load_input_proof
from ...domain.errors import MarketOpsError, StoreError
from ...domain.maintenance import due_dedupe_key
from ...domain.util import utc_now
from .db import (
    CONFIG_TABLE_BY_NAME,
    CONFIG_TABLE_DDL,
    JOB_KEY_COLUMNS,
    JOB_TABLE_BY_NAME,
    LINEAGE_TABLES,
    ROUTE_TABLE_BY_NAME,
    ROUTE_TABLE_DDL,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    WORKSPACE_META_KEY,
    connect,
    replay_ddl,
    set_meta,
    split_ddl_statements,
    write_transaction,
)
from .payload import dump_payload, load_payload

#: 备份名里的时间戳格式。UTC —— 本机的账单与日志都以 UTC 为准。
BACKUP_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

#: 迁移会改的三张表里，「一行的引用」怎么取。
#:
#: evidence 的行**自己就是**一条证据（``evidence_id``）；路线与机会挂的是
#: ``evidence_ids`` 一串。这一格喂给 ``InputProof.source_for``，所以它在
#: 「哪些行能被证明」这件事上是承重的 —— 取错了只会让更多行落到「证不出来」，
#: 不会让任何一行被错误地证明（判据是集合成员，不是相等）。
_REF_KEY = "evidence_id"
_REFS_KEY = "evidence_ids"


@dataclass(frozen=True)
class ArchiveFile:
    """JSONL 存档里被迁移（或核对）过的一个文件。"""

    path: Path
    rows: int
    backup: Path | None
    unchanged: bool


@dataclass(frozen=True)
class MigrationReport:
    """一次迁移干了什么。**这些字段都是给人读的**，所以每一句都要能对上下面的数字。"""

    db_path: Path
    workspace: str
    from_version: int
    to_version: int
    already_current: bool
    backup: Path | None
    rows: dict[str, int] = field(default_factory=dict)
    proof_counts: dict[str, int] = field(default_factory=dict)
    #: 第二步（v2 → v3）改了什么。``None`` = 这一次运行没走到那一步（库已经是 v3）。
    aspects: AspectRewrite | None = None
    #: 第三步（v3 → v4）重建了什么。``None`` = 这一次没有可重建的表。
    revisions: RevisionRebuild | None = None
    #: 第四步（v4 → v5）把 ``contact_route`` 拆成了什么。``None`` = 这一次运行没收
    #: 到这一步的动作（与「拆了、但一行都没有」是两句不同的话）。
    routes: RouteSplit | None = None
    #: 第五步（v5 → v6）把 ``research_job`` 重建成了什么。``None`` 与上面同义。
    #: **它总是有一个值**（哪怕这一步没做）—— 「这张表认得的词与代码一致」是一句有
    #: 内容的话，而 ``None`` 只说得出「这一次没走到那一步」，两者不该长得一样。
    jobs: JobKindWidening | None = None
    #: 第六步（v6 → v7）把 ``execution_ref`` 的映射列改名成了什么。**总是有一个值**，
    #: 理由与 ``jobs`` 那一格逐字相同。
    refs: ExecutionRefRename | None = None
    archive: tuple[ArchiveFile, ...] = ()
    proof: InputProof | None = None
    #: §15(c) 的回读核对读数。**由那一步自己算出来**（数了哪些格、各多少行），
    #: 不是另写的一句声明 —— 每一格都过了才会走到这里（不过就是一条异常）。
    verified: str = ""

    def render(self) -> str:
        lines = [
            f"状态库：{self.db_path}",
            f"工作区（Scene）：{self.workspace}",
        ]
        if self.already_current:
            lines.append(
                f"版本已经是 {self.to_version}（{SCHEMA_VERSION_KEY}={self.to_version}）"
                "—— 没有重新盖章，只核对了绑定、并补完存档那一侧。"
            )
        else:
            lines.append(f"版本：{self.from_version} → {self.to_version}")
            if self.backup is not None:
                lines.append(f"迁移前备份：{self.backup}")
        if self.rows:
            total = sum(self.rows.values())
            lines.append(f"盖章 {total} 行：" + "、".join(
                f"{table} {count} 行" for table, count in sorted(self.rows.items())
            ))
        if self.proof_counts:
            lines.append(
                "证明来源："
                + "、".join(
                    f"{source} {count} 行"
                    for source, count in sorted(self.proof_counts.items())
                )
            )
        if self.aspects is not None:
            lines.append(self.aspects.describe())
        if self.revisions is not None:
            lines.append(self.revisions.describe())
        if self.routes is not None:
            lines.append(self.routes.describe())
        if self.jobs is not None:
            lines.append(self.jobs.describe())
        if self.refs is not None:
            lines.append(self.refs.describe())
        if self.verified:
            lines.append(self.verified)
        if self.proof is not None:
            lines.append(self.proof.describe())
        for item in self.archive:
            if item.unchanged:
                lines.append(f"存档 {item.path}：{item.rows} 行，已经带血统，未改动")
            else:
                lines.append(
                    f"存档 {item.path}：{item.rows} 行已补血统"
                    + (f"（迁移前备份：{item.backup}）" if item.backup else "")
                )
        return "\n".join(lines)


def migrate(
    db_path: Path,
    *,
    profile: str,
    inputs_dir: Path,
    archive_dirs: tuple[Path, ...] = (),
    now: datetime | None = None,
) -> MigrationReport:
    """把 ``db_path`` 从它**现在那个版本**迁到 ``SCHEMA_VERSION``，并（可选地）迁移
    JSONL 存档。

    一个 v1 的库五步都跑（补血统 + 换词表 + 重建配置表 + 拆路线表 + 重建研究请求表），
    一个 v2 的库跑后四步，v3 跑后三步，v4 跑后两步，v5 只跑第五步 —— 但无论跑几步，
    都是**一次备份、一个事务、一个版本号**。没有任何一个中间状态（血统补齐了、词表还没
    换）是有人想要的，所以这条命令不提供「只迁到 v2」这个选项。

    Args:
        db_path: 状态库。**必须已经存在** —— 不存在时拒绝，不建库。
        profile: 本部署的工作区（``Settings.profile``）。**必填、无默认值**：它就是要
            盖上去的那个值，一个「缺省 = 当前配置」的参数会让「我没说清哪个工作区」
            变成一次静默的盖章。
        inputs_dir: 本部署自己的输入目录（证明来源，见 ``application/lineage.py``）。
        archive_dirs: JSONL 存档目录（``Settings.evidence_dir`` / ``routes_dir`` /
            ``opportunities_dir``）。**空的元组是合法的**：一个部署可能只用 SQLite。
        now: 注入的时刻，只用于备份文件名（重放可测）。

    Raises:
        StoreError: 库不存在、版本比代码新、作业记录属于另一个 Scene、绑定属于另一个
            工作区、**有任何一行证不出血统**、**有一个 aspect 词认不出来**、有一行
            过不了它自己的模型（配置表那一侧）、有一行读不出它自己说的那条路线
            （路线那一侧）、**``research_job.input_kind`` 那一格的约束读不出来**、
            或者有一行的 ``input_kind`` 不在这一版代码的词表里（研究请求那一侧）——
            都逐行点名，且都在任何一次写入之前。
    """
    if not db_path.exists():
        raise StoreError(
            f"没有这个状态库：{db_path}\n"
            "迁移**不建库** —— 建一个空的 v5 库由第一次写入自己完成（写入时的工作区"
            "绑定取自那一行自己的 workspace），与「给历史行盖章」是两件事。"
        )

    stamp = (now or utc_now()).astimezone(timezone.utc).strftime(BACKUP_STAMP_FORMAT)
    # 证明表**两个分支都要读**：库已经是 v2 而存档里还有没盖章的行是可能的
    # （两次落盘是两个时刻，中间可能失败），而「把存档那一侧补完」正是那次重跑的
    # 用途。手上没有证明表时，那些行只能被拒绝 —— 而拒绝是**对的答案**，
    # 前提是它说得出「我手上什么都没有」。
    proof = load_input_proof(inputs_dir)
    with connect(db_path) as conn:
        replay_ddl(conn)
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
        current = None if version is None else int(version["value"])
        if current == SCHEMA_VERSION:
            _require_binding_matches(conn, profile=profile, db_path=db_path)
            archive = _migrate_archive(
                archive_dirs, profile=profile, proof=proof, stamp=stamp, write=True
            )
            return MigrationReport(
                db_path=db_path,
                workspace=profile,
                from_version=SCHEMA_VERSION,
                to_version=SCHEMA_VERSION,
                already_current=True,
                backup=None,
                archive=archive,
                proof=proof,
                # 这一格**不空着**：空着读起来会像「也核过、没问题」，而这一次根本没
                # 写库 —— 没有可回读的东西。§15(c) 的回读是「写完读回来」，把它写成
                # 一句「不适用」比留白诚实。
                verified=(
                    "回读核对：本次没有写库（版本已经是 "
                    f"{SCHEMA_VERSION}），所以没有可回读的东西 —— 上面那两格"
                    "（绑定、存档）就是这一次的全部动作。"
                ),
            )
        if current is None or current < 1 or current > SCHEMA_VERSION:
            raise StoreError(
                f"状态库 {db_path} 的 schema 版本是 {current!r}，这条迁移只认 1 … "
                f"{SCHEMA_VERSION - 1} → {SCHEMA_VERSION}。\n"
                "比代码新的库不该被这条命令打开（它不认识那些形状），版本为空的库也不该"
                "被盖章（它里面是什么还没有人说过）。"
            )

        # 这个库自己的记录说它服务的是谁 —— 两个版本都过这一道（它只在**矛盾**时拒绝）。
        _require_jobs_belong_to(conn, profile=profile, db_path=db_path)
        if current != 1:
            # v1 的库没有绑定（绑定正是第一步要盖上去的东西），所以只在 v2 起才核对。
            # 换词表这一步本身与工作区无关，但它改的仍然是**别人的行**：一个 v2 的库被
            # 这一版代码改过之后，它自己那个构建就读不出来了。
            _require_binding_matches(conn, profile=profile, db_path=db_path)

        # **每一步的预检都在任何一次写入之前跑完。** 一步失败不该留下另半步 —— 而「迁移过
        # 一半」在报告里与「迁完了」长得一样。
        stamped: dict[str, list[_StampedRow]] | None = None
        if current == 1:
            stamped = _plan_db(conn, profile=profile, proof=proof, db_path=db_path)
        rewrite = _plan_aspects(conn, db_path=db_path)
        rebuild = _plan_revisions(conn, db_path=db_path)
        split = _plan_routes(conn, db_path=db_path, stamped=stamped)
        jobs = _plan_job_kind(conn, db_path=db_path)
        refs = _plan_execution_ref(conn)
        # 存档的预检：与库那一侧同一个理由 —— 在任何一次写入之前就知道每一处都证得出来。
        # 它的报告被丢掉（那一份里的 backup 还是空的），真正进报告的是下面那次 write=True。
        _migrate_archive(
            archive_dirs, profile=profile, proof=proof, stamp=stamp, write=False
        )
        backup = _backup_database(db_path, stamp=stamp, from_version=current)
        # 第五步要 **DROP** 一张被别人引用着的表（``work_item.job_id`` →
        # ``research_job``），而 SQLite 的外键强制**只能在事务之外**关：``PRAGMA
        # foreign_keys`` 在事务里是空操作（文档逐字如此，实测亦然）。所以它在这里、
        # 在 ``BEGIN IMMEDIATE`` 之前。
        #
        # 只在这一步真的要做时才关：另外四步用的是「改名搬开」那套（不删任何被别人
        # 引用的表），本来就不需要这一手，而关掉它会让那四步的写入少一层 SQLite 替我们
        # 做的检查。关掉的那一段时间里，那层检查由第五步自己补上 —— 提交之前逐行对账，
        # 见 ``_require_job_rows_kept``。
        guard_foreign_keys = not jobs.empty
        if guard_foreign_keys:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with write_transaction(conn):
                if stamped is not None:
                    _apply_db(conn, stamped)
                    set_meta(conn, WORKSPACE_META_KEY, _single_workspace(stamped))
                _apply_aspects(conn, rewrite)
                _apply_revisions(conn, rebuild)
                _apply_routes(conn, split)
                _apply_job_kind(conn, jobs)
                _require_job_rows_kept(conn, jobs, db_path=db_path)
                _apply_execution_ref(conn, refs)
                set_meta(conn, SCHEMA_VERSION_KEY, str(SCHEMA_VERSION))
        except sqlite3.Error as exc:
            # 存储层报错（主键撞车、约束、磁盘）**不能长成一句 Python 回溯**：它看起来
            # 像代码坏了，而事实是「这一次没改成，库还在原处」。整个写入在一个事务里，
            # 所以这句话是准的。
            raise StoreError(
                f"写入这一步失败了：{exc}\n"
                f"库没有被改（诸步在同一个事务里，已经回滚），还是 v{current} 那个样子。"
                f"迁移前的备份在 {backup}。\n"
                "整份中止是刻意的：一处写不进去时，报告里「迁移完成」之后就没有第二个人"
                "会回头看剩下的那些行了。"
            ) from exc
        finally:
            # 关掉的东西一定要还回来，而 ``finally`` 是唯一一处**无论提交、回滚还是
            # 别的异常**都走到的地方：一个外键强制一直关着的连接，在下一个用它的人
            # 手里是一句静默的「约束不生效」。连的是同一个库的同一个连接（``migrate``
            # 用它跑完整个流程），所以这不是可选的收尾。
            if guard_foreign_keys:
                conn.execute("PRAGMA foreign_keys = ON")
    # §15(c)：**写完之后回读一遍**。放在这里、在存档那一侧之前 —— 库是下一个进程
    # 要打开的东西，也是 ``ensure_schema`` 会拒的东西，所以它先被核对；存档那一侧
    # 一个文件一次原子替换，核对不过时不该已经被动过。
    verified = _verify_migrated(
        db_path,
        profile=profile,
        stamped=stamped,
        rebuild=rebuild,
        split=split,
        jobs=jobs,
        refs=refs,
        backup=backup,
    )
    archive = _migrate_archive(
        archive_dirs, profile=profile, proof=proof, stamp=stamp, write=True
    )
    return MigrationReport(
        db_path=db_path,
        workspace=profile,
        from_version=current,
        to_version=SCHEMA_VERSION,
        already_current=False,
        backup=backup,
        rows={} if stamped is None else {table: len(rows) for table, rows in stamped.items()},
        proof_counts={} if stamped is None else _count_sources(stamped),
        aspects=rewrite,
        revisions=rebuild,
        routes=split,
        jobs=jobs,
        refs=refs,
        archive=archive,
        proof=proof,
        verified=verified,
    )


def _verify_migrated(
    db_path: Path,
    *,
    profile: str,
    stamped: dict[str, list[_StampedRow]] | None,
    rebuild: RevisionRebuild,
    split: RouteSplit,
    jobs: JobKindWidening,
    refs: ExecutionRefRename,
    backup: Path,
) -> str:
    """写完之后的**回读**：盘上那个库真是我们打算留下的那个吗（§15(c)）。

    预检答的是「能不能迁」，写入答的是「迁了」，而这两句都不等于「盘上现在是那个
    样子」。中间隔着一次 commit，以及三处**按 ``rowid`` 落地**的 UPDATE —— 计划按
    rowid 说「改这一行」，而 rowid 是这次运行看见的那个库里的地址。所以这里换一条
    **新连接**从盘上读回来（下一个进程打开的正是它），核七格：

    * 版本那一格 = 代码写下去的那个值（``ensure_schema`` / 只读探问会拒的就是它）；
    * 工作区绑定 = 本次请求的那个 —— 只在这一次真的写过它时查（v2 → v3 那次只是
      核对过绑定，回读一遍是同一句话再说一次，不是一条新判据）；
    * **逐行的章**：计划里要盖的那些行，一行一行读回来，``payload.workspace`` 必须是
      本部署。这是 ``_apply_db`` 唯一一次被人读回来核对 —— 否则它对这条命令的贡献
      只有「计划是对的」。第四步换过主键的那一张表（``contact_route``）是例外：它的
      ``seq`` 已经不存在了，逐行找回来不可能，于是换成整表读回来核一遍 —— 那一格在
      报告里**单独一个数**，见下面那个分支；
    * **旧词表没有残留**：三张表里一个 v2 的词都不许剩下（换名字的没改全、``drop``
      的没删掉，都长这样）；
    * **回执的键与它自己那几列仍然自洽**：``due_dedupe_key`` 是那一列的唯一算法，
      而这次写入改过它 —— 混行（键来自另一行）在这里是最后一处能被拦住的地方；
    * **重建过的那三张表是新的形状，而且一行不少**：``revision`` 那一列在、搬开的那张
      临时表不在、行数与计划里的对得上。重建是这一步唯一一处**会丢行**的动作
      （改名 → 建回来 → 搬行 → 删旧的，少搬一步就丢），而丢了行之后紧接着的那次写入
      照样成功 —— 直到有人去查一族模板；
    * **路线拆表的业务键对账**：观察行数与计划里的对得上（那一侧**一行都不能丢**，
      折掉的读法只在它那里）、投影的键集与观察的键集相同（于是「每个键恰好一行」
      与「投影 ≤ 观察」一起成立，主键管着前者的后半句）、搬开的旧表不在、
      建表语句里声明的每一个索引都真的落在盘上（不查这一格的话，
      ``_drop_indexes_of`` 那一处失误的表现是「迁移成功，之后每次按账号取路线全表
      扫」，而没有任何一处说过话）；
    * **研究请求那一张表的词换了、行一行不少**：盘上那句建表语句认得的词与代码这一份
      相同、搬行用的那张临时表不在、``research_job`` 与**引用它的每一张表**的行数与
      计划里的对得上且引用都还指得到。这一格与第四步那一格是两个方向：那边查的是
      「旧行有没有全部变成观察」，这边查的是「换掉一张被别人引用着的表之后，那些引用
      还成不成立」—— 而它在盘上**不会报错**（``ensure_schema`` 只看表在不在）。

    失败时**不删库、不回滚**：库已经提交了，把它改回去是运维的动作（备份就在旁边，
    路径写在消息里），而一个被进程悄悄改回去一半的库比一个核对不过的库更难承认。
    这一步只回答「能不能启动」，所以它停下来、把话说满 —— 名字、路径、回滚怎么做。

    Args:
        db_path: 刚写完的状态库。
        profile: 本次盖的那个工作区。
        stamped: 第一步的计划。``None`` = 这一次没走第一步（库本来就是 v2）。
        rebuild: 第三步的计划。
        split: 第四步的计划。
        jobs: 第五步的计划。**没有默认值** —— 与 ``migrate(profile=...)`` 同一条理由：
            一个「缺省 = 这一步没做」的参数会让「我忘了传」与「这一步不用做」在报告里
            长得一样，而这两句话在这一格上的差别正是「那张表认不认得 cohort」。
        backup: 迁移前的备份，失败消息里要报出来。

    Returns:
        给人读的一行**读数**（每格都带数字），进 ``MigrationReport``。它是这一步自己
        算出来的，不是另写的一句声明 —— 一份没核对过的回执与一份核对过且一致的回执
        必须是两份读得出差别的东西（§15(b) 那一课的同一句）。

    Raises:
        StoreError: 上面任何一格不成立。
    """
    problems: list[str] = []
    checked: dict[str, int] = {}
    #: 重建过的那三张表各自读回来多少行。**与 ``checked`` 分开**：那个数的是「第一章
    #: 盖过几行」，这个数的是「第二章搬过来几行」—— 一个 dict 装两件事，报告上那一行
    #: 就会把两处的数字加起来，而加起来之后没有一个数字还对得上。
    rebuilt_rows: dict[str, int] = {}
    #: 路线那两张表各自读回来多少行。**与上面两个分开**，同一个理由 —— 一个 dict
    #: 装两件事，报告上那一行就会把两处的数字加起来。
    route_rows: dict[str, int] = {}
    #: 换过主键之后读回来多少行「章还在」。见下面那个分支：它是**另一条**判据，
    #: 所以它的数不能并进 ``checked``（那个数的是「计划里那一行找回来了几行」）。
    rekeyed: dict[str, int] = {}
    #: 第五步那一张表与**引用它的每一张表**读回来各多少行。**与上面三个分开**，同一条
    #: 理由：一个 dict 装两件事，报告上那一行就会把两处的数字加起来。
    job_rows: dict[str, int] = {}
    #: 第六步那一张表读回来多少行。**单独一个数**（同一条理由）。
    ref_rows: dict[str, int] = {}
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
        found = None if row is None else str(row["value"])
        if found != str(SCHEMA_VERSION):
            problems.append(
                f"版本那一格是 {found!r}，而这一步写下去的是 {SCHEMA_VERSION!r}"
            )

        if stamped is not None:
            bound = conn.execute(
                "SELECT value FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,)
            ).fetchone()
            value = None if bound is None else str(bound["value"])
            if value != profile:
                problems.append(
                    f"工作区绑定读回来是 {value!r}，而本次盖的是 {profile!r}"
                )
            for table, rows in stamped.items():
                key_column = _key_column(table)
                present = _columns_of(conn, table)
                if key_column not in present:
                    # 这张表在第四步里**换过主键**（``seq`` → 规范键）：计划里那几个
                    # 键在盘上已经不存在了，逐行找回来不可能。换成「这一张表上的每一
                    # 行都得说本部署」。
                    #
                    # 它**比逐行匹配弱**，弱在这里：它不再证明「计划里那一行」就是盘
                    # 上这一行。行数对不对由第四步自己的业务键对账回答（观察行数守
                    # 恒、投影键集与观察键集相同），而这一格只回答「章还在不在」。
                    for found in conn.execute(
                        f"SELECT canonical_key AS key, payload FROM {table}"  # noqa: S608
                    ).fetchall():
                        where = f"{table}[{found['key']}]"
                        try:
                            payload = load_payload(str(found["payload"]), where)
                        except StoreError:
                            problems.append(f"{where}：读回来那条 payload 解不开")
                            continue
                        rekeyed[table] = rekeyed.get(table, 0) + 1
                        if payload.get("workspace") != profile:
                            problems.append(
                                f"{where}：章读回来是 {payload.get('workspace')!r}，"
                                f"而本次盖的是 {profile!r}"
                            )
                    continue
                read = {
                    str(row["key"]): row["payload"]
                    for row in conn.execute(
                        f"SELECT {key_column} AS key, payload FROM {table}"  # noqa: S608
                    ).fetchall()
                }
                for planned in rows:
                    # 「已经盖过」的那些行这次没有被写（``_apply_db`` 跳过它们），
                    # 所以回读它们核对的还是**迁移前**那个读数 —— 那是预检说过的话。
                    if planned.source == "already-stamped":
                        continue
                    where = f"{table}[{planned.key}]"
                    stored = read.get(planned.key)
                    if stored is None:
                        problems.append(f"{where}：计划里要盖章的那一行读不回来了")
                        continue
                    try:
                        payload = load_payload(stored, where)
                    except StoreError:
                        problems.append(f"{where}：读回来那条 payload 解不开")
                        continue
                    checked[table] = checked.get(table, 0) + 1
                    if payload.get("workspace") != profile:
                        problems.append(
                            f"{where}：章读回来是 {payload.get('workspace')!r}，"
                            f"而本次盖的是 {profile!r}"
                        )

        old_words = sorted({*ASPECT_RENAMES, *ASPECT_SPLITS})
        placeholders = ", ".join("?" for _ in old_words)
        survivors = 0
        for table in _ASPECT_TABLES:
            rows = conn.execute(
                f"SELECT aspect, COUNT(*) AS n FROM {table}"
                f" WHERE aspect IN ({placeholders}) GROUP BY aspect",
                old_words,
            ).fetchall()
            survivors += sum(int(row["n"]) for row in rows)
            if rows:
                problems.append(
                    f"{table}：v2 的词还在（"
                    + "、".join(f"{row['aspect']} {row['n']} 行" for row in rows)
                    + "）"
                )

        claims = conn.execute(
            "SELECT rowid AS rid, dedupe_key, attempt, scene_id, scene_version,"
            " subject_kind, subject_id, aspect, period FROM maintenance_claim"
        ).fetchall()
        for claim in claims:
            computed = due_dedupe_key(
                scene_id=str(claim["scene_id"]),
                scene_version=int(claim["scene_version"]),
                subject_kind=str(claim["subject_kind"]),
                subject_id=str(claim["subject_id"]),
                aspect=str(claim["aspect"]),
                period=str(claim["period"]),
            )
            if computed != str(claim["dedupe_key"]):
                problems.append(
                    f"maintenance_claim[rowid={claim['rid']}]：存着的键与它自己那几列"
                    f"算出来的 {computed} 不一致"
                )

        for name, key_column, _ in CONFIG_TABLE_DDL:
            columns = _columns_of(conn, name)
            if "revision" not in columns:
                problems.append(
                    f"{name}：``revision`` 那一列读不回来 —— 这张表的形状还是 v3 的"
                    "（主键只有 id），而代码认的是 (id, 版)"
                )
                continue
            parked = f"{_PARKED_PREFIX}{name}"
            if _columns_of(conn, parked):
                problems.append(f"{name}：搬开的旧表 {parked} 还在，没有被删掉")
            found = int(conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"])  # noqa: S608
            planned = sum(1 for row in rebuild.rows if row.table == name)
            if name in rebuild.rebuilt and found != planned:
                problems.append(
                    f"{name}：计划里要搬 {planned} 行，盘上是 {found} 行"
                )
            if name in rebuild.rebuilt:
                rebuilt_rows[name] = found
            # 每一行搬过去之后仍然说得清自己是谁：键与版本都在，且 payload 认得出。
            for row in conn.execute(
                f"SELECT {key_column} AS key, revision AS rev, payload FROM {name}"  # noqa: S608
            ).fetchall():
                where = f"{name}[{row['key']}]"
                loaded = load_payload(str(row["payload"]), where)
                if loaded.get("revision") != int(row["rev"]):
                    problems.append(
                        f"{where}：列里的版本是 {row['rev']}，而 payload 里那一格是"
                        f"{loaded.get('revision')!r}"
                    )

        if split.rebuilt:
            parked = f"{_PARKED_V5_PREFIX}contact_route"
            if _columns_of(conn, parked):
                problems.append(f"contact_route：搬开的旧表 {parked} 还在，没有被删掉")
            # 旧行守恒：每一条旧行要么成了一条观察，要么与前面某一行并成了同一条。
            # 少了这一条，「搬了几行」与「写了几条」两个数各说各的也核不出来 ——
            # 而丢行的样子恰恰是这两个数悄悄不等。
            if len(split.observations) + split.repeated != split.legacy_rows:
                problems.append(
                    f"contact_route：旧表的 {split.legacy_rows} 行对不上 —— 计划里 "
                    f"{len(split.observations)} 条观察 + {split.repeated} 行重复，"
                    "两者应当正好等于旧行数（少的那几行没有被搬到任何地方）"
                )
            for table, planned in (
                ("route_observation", len(split.observations)),
                ("contact_route", len(split.projections)),
            ):
                found = int(
                    conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
                )
                if found != planned:
                    problems.append(
                        f"{table}：计划里是 {planned} 行，盘上是 {found} 行"
                    )
                route_rows[table] = found
            planned_keys = {row["canonical_key"] for row in split.observations}
            on_disk_keys = {
                str(row["canonical_key"])
                for row in conn.execute("SELECT canonical_key FROM contact_route").fetchall()  # noqa: S608
            }
            if on_disk_keys != planned_keys:
                problems.append(
                    "contact_route：投影的键集与那一批观察的键集对不上 —— 观察 "
                    f"{len(planned_keys)} 个键、投影 {len(on_disk_keys)} 个键"
                    + (
                        "（差集：" + "、".join(sorted(on_disk_keys ^ planned_keys)) + "）"
                        if on_disk_keys ^ planned_keys
                        else ""
                    )
                )
            # 索引那一格：**只有重建的表**要查 —— 另两张是 replay_ddl 建的，
            # 而它建的时候那句 IF NOT EXISTS 不会撞上任何东西。
            for name, _key, ddl in ROUTE_TABLE_DDL:
                if name != "contact_route":
                    continue
                attached = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
                        (name,),
                    ).fetchall()
                }
                missing = [
                    index for index in _declared_index_names(ddl) if index not in attached
                ]
                if missing:
                    problems.append(
                        f"{name}：建表语句声明的索引没落在盘上（"
                        + "、".join(missing)
                        + "）—— 重建时搬开的旧索引还占着那些名字"
                    )

        if jobs.rebuilt:
            if _columns_of(conn, _V6_NEW_JOB_TABLE):
                problems.append(
                    f"{_V6_NEW_JOB_TABLE}：搬行用的那张表还在，没有被改名成 {_JOB_TABLE}"
                )
            admitted = _admitted_kinds(_table_sql(conn, _JOB_TABLE))
            if admitted != jobs.admitted_declared:
                problems.append(
                    f"{_JOB_TABLE}.input_kind：盘上那一格认得的词是"
                    f"（{'、'.join(admitted) if admitted is not None else '读不出来'}），"
                    f"而这一步写下去的是（{'、'.join(jobs.admitted_declared)}）"
                )
            # 与写入那一步**同一处算式**（``_job_rows_check``）：行数、引用者的每一个键
            # 都还指得到。提交之前已经核过一次，而这一次是**从盘上新开的连接**读回来
            # 的 —— 中间隔着一次提交，以及一次改名（改名是这一步唯一一处会让盘上的东西
            # 与内存里说的不同的话的动作）。
            counted, found = _job_rows_check(conn, expected=jobs.keys)
            problems.extend(found)
            job_rows.update(counted)

        if refs.present:
            # 改名这一步核两件事：新名字在、旧名字不在（后者由「除开那两列固定的
            # 只剩一列」这句话承担），以及行数没变 —— 改名不该动任何一行。
            columns = _columns_of(conn, _EXECUTION_REF_TABLE)
            if _EXECUTION_REF_COLUMN not in columns:
                problems.append(
                    f"{_EXECUTION_REF_TABLE}：映射列读回来不叫 {_EXECUTION_REF_COLUMN}"
                    f"（盘上的列是 {sorted(columns)!r}）"
                )
            strays = sorted(
                set(columns) - _EXECUTION_REF_FIXED_COLUMNS - {_EXECUTION_REF_COLUMN}
            )
            if strays:
                problems.append(
                    f"{_EXECUTION_REF_TABLE}：除开映射列还有多余的列 {strays!r}"
                )
            found_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM {_EXECUTION_REF_TABLE}"  # noqa: S608
                ).fetchone()["n"]
            )
            if refs.renamed and found_rows != refs.rows:
                problems.append(
                    f"{_EXECUTION_REF_TABLE}：改名之前是 {refs.rows} 行，盘上是 "
                    f"{found_rows} 行 —— 改名不该动任何一行"
                )
            ref_rows[_EXECUTION_REF_TABLE] = found_rows

    if problems:
        raise StoreError(
            f"{db_path} 迁完之后**回读核对不过** —— 报告里「迁移完成」，而盘上不是"
            "那个样子：\n"
            + "\n".join(f"  - {item}" for item in problems)
            + "\n\n"
            "库**已经改过了**（写入那一步提交了），所以这不是「什么都没发生」："
            "别在这个库上继续跑。存档（JSONL）那一侧还没动 —— 这一步在它之前。\n"
            f"回滚：把备份 {backup} 覆盖回原路径即可（库是自包含的一个文件；"
            "``-wal`` / ``-shm`` 要一起删掉，否则旧的 WAL 会被当成新库的日志重放）。\n"
            "回滚之后请把上面这一整段留住：版本那一格、逐行的章、旧词表、回执的键"
            "分别对应四处不同的写入 —— 哪一处不对说明的是哪一处坏了。"
        )

    stamps = "、".join(f"{table} {count} 行" for table, count in sorted(checked.items()))
    rebuilt = "、".join(
        f"{table} {count} 行" for table, count in sorted(rebuilt_rows.items())
    )
    split_rows = "、".join(f"{table} {count} 行" for table, count in sorted(route_rows.items()))
    rekeyed_rows = "、".join(f"{table} {count} 行" for table, count in sorted(rekeyed.items()))
    job_rows_reading = "、".join(
        f"{table} {count} 行" for table, count in sorted(job_rows.items())
    )
    ref_reading = "、".join(
        f"{table} {count} 行" for table, count in sorted(ref_rows.items())
    )
    return (
        f"回读核对（从盘上新开一条连接读回来）：版本 {SCHEMA_VERSION}"
        + ("、工作区绑定" if stamped is not None else "")
        + (f"、逐行的章 {stamps}" if stamped is not None else "")
        + (f"、换过主键之后章还在的 {rekeyed_rows}" if rekeyed else "")
        + f"、旧词表残留 {survivors} 处、回执的键 {len(claims)} 行自洽"
        + (f"、重建的配置表 {rebuilt}" if rebuilt_rows else "")
        + (
            f"、路线拆表（观察 + 投影 {split_rows}，旧行 {split.legacy_rows} 行、"
            f"并掉 {split.repeated} 行、键集一致、索引都在）"
            if route_rows
            else ""
        )
        + (
            f"、研究请求的输入种类（{job_rows_reading}，词与代码一致、引用都指得到）"
            if job_rows
            else ""
        )
        + (
            f"、派发映射的列名（{ref_reading}，列名是 {_EXECUTION_REF_COLUMN}）"
            if ref_rows
            else ""
        )
        + " —— 都对上了。"
    )


# --- 库那一侧 -----------------------------------------------------------------


@dataclass(frozen=True)
class _StampedRow:
    """一行被盖了章的历史行。``where`` 是报错与统计用的定位串。"""

    table: str
    key: str
    workspace: str
    source: str
    payload: dict


def _plan_db(
    conn: sqlite3.Connection, *, profile: str, proof: InputProof, db_path: Path
) -> dict[str, list[_StampedRow]]:
    """读出三张表的每一行，算出它该盖什么、靠什么证明。**一行都不写。**

    证不出来的行在这里就**整份拒绝**（在备份之前、在任何写入之前）—— 半信半疑地
    盖一部分比不盖更坏：报告里「迁移完成」之后，剩下那些行就没有第二个人会回头看。
    """
    stamped: dict[str, list[_StampedRow]] = {}
    refused: list[str] = []
    for table in LINEAGE_TABLES:
        key_column = _key_column(table)
        rows = conn.execute(
            f"SELECT {key_column} AS key, payload FROM {table} ORDER BY {key_column}"
        ).fetchall()
        planned: list[_StampedRow] = []
        for row in rows:
            key = str(row["key"])
            payload = load_payload(row["payload"], f"{table}[{key}]")
            existing = payload.get("workspace")
            if existing is not None:
                if existing != profile:
                    refused.append(
                        f"  - {table}[{key}]：已经写着 workspace={existing!r}，"
                        f"而本次要盖的是 {profile!r}"
                    )
                    continue
                planned.append(
                    _StampedRow(table, key, profile, "already-stamped", payload)
                )
                continue
            account_id = str(payload.get("account_id") or "")
            source = proof.source_for(
                account_id=account_id, evidence_ids=_refs_for(payload)
            )
            if source is None:
                refused.append(
                    f"  - {table}[{key}]：account_id={account_id or '(缺这一格)'}"
                    f"，引用={list(_refs_for(payload)) or '（无）'}"
                )
                continue
            payload["workspace"] = profile
            planned.append(_StampedRow(table, key, profile, source, payload))
        stamped[table] = planned
    if refused:
        raise StoreError(
            f"{db_path} 里有 {len(refused)} 行**证不出血统**，整份拒绝（一行都没有改）：\n"
            + "\n".join(refused)
            + "\n\n"
            "这些行的主语不在本部署自己的输入里 —— 也就是说，没有证据说明本部署研究过"
            "它们。它们可能来自另一个部署，也可能来自一份已经被换掉的输入（清单被"
            "重生成、工件被删）。**不猜**：一个猜出来的血统比空着更坏，因为空着看得见，"
            "猜出来的会被人当成一条事实读。\n\n"
            "两条路：把能证明它们的输入放回 inputs_dir（清单 / 回执 / 发现工件），"
            "或者把这几行连同它们的库一起移开。\n\n"
            f"本次手上的证明表：\n{proof.describe()}"
        )
    return stamped


def _apply_db(conn: sqlite3.Connection, stamped: dict[str, list[_StampedRow]]) -> None:
    """把第一步的章盖上去。**自己不开事务** —— 调用方决定它和谁在同一个事务里。"""
    for table, rows in stamped.items():
        key_column = _key_column(table)
        for row in rows:
            if row.source == "already-stamped":
                continue
            conn.execute(
                f"UPDATE {table} SET payload = ? WHERE {key_column} = ?",
                (dump_payload(row.payload), row.key),
            )


def _single_workspace(stamped: dict[str, list[_StampedRow]]) -> str:
    """这次盖章用的那个工作区。**恰好一个** —— 多个意味着上游判据漏了。"""
    values = {row.workspace for rows in stamped.values() for row in rows}
    if len(values) != 1:
        raise StoreError(
            f"这次迁移手上的工作区有 {len(values)} 个：{sorted(values)}。\n"
            "一次迁移只盖一个工作区（绑定是**逐库**的），所以这是一个内部矛盾，"
            "不是一条可以挑一个继续跑的输入。"
        )
    return values.pop()


def _require_binding_matches(
    conn: sqlite3.Connection, *, profile: str, db_path: Path
) -> None:
    """库已经有绑定了（v2 起）：那个工作区必须就是本次请求的那个。"""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,)
    ).fetchone()
    bound = None if row is None else str(row["value"])
    if bound == profile:
        return
    raise StoreError(
        f"{db_path} 绑在工作区 {bound!r} 上，而本次请求的是 {profile!r}。\n"
        "一个状态库只装一个工作区的产出：把别人的库当自己的来迁移（或迁移完再当成"
        "自己的来读）会让两批行在同一个 ``account_id`` 下混起来，而报告上看起来只是"
        "「证据更多了」。\n"
        "要用另一个工作区，换一个库。"
    )


def _require_jobs_belong_to(
    conn: sqlite3.Connection, *, profile: str, db_path: Path
) -> None:
    """库自己的作业记录是**逐库**的旁证：它服务过的 Scene 必须就是本次请求的那个。

    一个库可以没有任何作业（发现那一轮不建作业），所以这里**只拒绝矛盾**，不要求
    存在：作业记录的用途是「这个库是谁在用」这一句话，而它说不是本部署时，这个库的
    每一行都不该被本部署盖章。
    """
    rows = conn.execute("SELECT DISTINCT scene_id FROM research_job").fetchall()
    scenes = sorted({str(row["scene_id"]) for row in rows})
    if not scenes:
        return
    if scenes == [profile]:
        return
    raise StoreError(
        f"{db_path} 的作业记录说这个库服务的是 {scenes}，而本次请求的工作区是 "
        f"{profile!r}。\n"
        "作业的 scene_id 是受理那一刻冻住的，所以它是这个库**自己**关于「我在给谁用」"
        "的记录 —— 它说不是本部署时，本部署没有立场给这个库里的任何一行盖血统章。\n"
        "要么用那个 Scene 来迁移，要么把这个库移开。"
    )


def _key_column(table: str) -> str:
    """一行在这个表里的主键列名。表名只在这两个之间，且都不来自调用方。"""
    if table == "evidence":
        return "evidence_id"
    return "seq"


def _refs_for(payload: dict) -> tuple[str, ...]:
    """一行 payload 里指着的证据 id。"""
    single = payload.get(_REF_KEY)
    if isinstance(single, str) and single:
        return (single,)
    many = payload.get(_REFS_KEY)
    if isinstance(many, list):
        return tuple(item for item in many if isinstance(item, str) and item)
    return ()


def _count_sources(stamped: dict[str, list[_StampedRow]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rows in stamped.values():
        for row in rows:
            if row.source == "already-stamped":
                continue
            counts[row.source] = counts.get(row.source, 0) + 1
    return counts


def _backup_database(db_path: Path, *, stamp: str, from_version: int) -> Path:
    """迁移前备份。**失败即中止**，一个字节都不改。"""
    target = db_path.with_name(f"{db_path.name}.v{from_version}-{stamp}.bak")
    if target.exists():
        raise StoreError(
            f"备份名已经被占用：{target}\n"
            "不覆盖既有备份是刻意的 —— 「今天的备份」与「上一次迁移的备份」混起来之后，"
            "回滚会回到一个没人知道是哪一刻的状态。先把它移走或改名，再跑一次。"
        )
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    except sqlite3.Error as exc:
        dest.close()
        source.close()
        target.unlink(missing_ok=True)
        raise StoreError(
            f"迁移前备份失败（{db_path} → {target}）：{exc}。\n"
            "没有写任何东西，库还是迁移前那个状态。先解决备份为什么写不出来 —— "
            "在一个没有备份的路上继续，等于把「回滚」这个选项丢掉。"
        ) from exc
    dest.close()
    source.close()
    _verify_backup(target, expected_version=from_version)
    return target


def _verify_backup(path: Path, *, expected_version: int) -> None:
    """打开备份，确认它真的是**迁移前**那个库。

    一个打不开的备份不是备份，而一个版本已经变了（或压根没有版本）的备份是
    **别的东西**。两种都在这里当场失败 —— 发现它们的时刻离写入越近，回滚越便宜。
    """
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        path.unlink(missing_ok=True)
        raise StoreError(
            f"备份写出来了但打不开：{path}（{exc}）。已删除半截文件。"
        ) from exc
    if row is None or int(row[0]) != expected_version:
        path.unlink(missing_ok=True)
        raise StoreError(
            f"备份的 schema 版本不是 {expected_version}（{path}）。已删除它。\n"
            "备份必须逐字是**迁移前**那个库 —— 它存在的唯一理由就是能回滚到那一刻。"
        )


# --- 词表那一侧（v2 → v3） -----------------------------------------------------


#: v2 里那些**只是换了名字**的面：同一格，同一身份，只是词变了。
ASPECT_RENAMES: dict[str, str] = {
    "source_supply": "source_coverage",
    "responsibility": "account_relation",
}

#: v2 里那些**被拆开**的面：粗面在新词表里没有对应的那一格，只有它的两个后继。
ASPECT_SPLITS: dict[str, tuple[str, str]] = {
    "asset_fact": ("asset_identity", "asset_attribute"),
    "role_email": ("contact_person_role", "email_evidence"),
}

#: 三张表各自怎么处置一格**被拆开**的行。
#:
#: 判据只有一条：这一行在新词表里还成不成立，而它取决于这一行说的是「我看过」还是
#: 「它可能变了」—— 前者是一个**声明**（会推迟下一次复核），后者是一个**待办**
#: （会把下一次复核提前）。两者错的方向不同，所以处置也不同。
SPLIT_DISPOSITION: dict[str, str] = {
    # 「我核过这一格」。粗面的一次观测**不能**归给任何一半：拆开的理由正是「两半以
    # 不同速率过期」，而归给一半就是替另一半宣布「刚核过」—— 一次假观测会把真正的
    # 复核推迟整整一个周期（默认 180 天）。删掉它**没有丢事实**：它记的是「当时有人
    # 看了 asset_fact」，而那个东西在新词表里不存在了。
    "observation": "drop",
    # 「这一格可能变了，去看一眼」。粗面变了**就是**两半都可能变了，所以拆成两条。
    # 方向是**多花一次调用**，而不是少看一眼 —— 反过来只留一半，另一半会带着旧值继续
    # 被用下去，而报告上它看起来是「刚核过」。
    "change_signal": "split",
    # 「这一格这一周期有人在跑 / 跑过了」。它既是租约也是去重记录，而它的
    # ``dedupe_key`` 里**编着 aspect 的值**（:func:`~marketops.domain.maintenance.due_dedupe_key`）
    # —— 拆成两条等于给两半各伪造一条租约，而那一半可能从来没有被谁认领过，于是它
    # 这一周期**不会被做**。删掉丢的是一条不再成立的历史统计（那一格失败过几次），
    # 不丢任何一格的工作。
    "maintenance_claim": "drop",
}

#: 有 ``aspect`` 那一格的表，与**它要说出的那件事**。顺序就是处理顺序。
_ASPECT_TABLES: tuple[str, ...] = ("observation", "change_signal", "maintenance_claim")


def _rewrite_of(old: str) -> tuple[str, ...] | None:
    """旧面在新词表里的归属。``None`` = 这个词**不在** v2 的词表里。

    被拆开的面返回两个后继；换名字的返回一个。这里只回答「它变成了什么」，**不**回答
    「那一行怎么处置」—— 后者是逐表的判断（:data:`SPLIT_DISPOSITION`），而把两者混在
    一个返回值里会让「拆开的观测该删」这条理由住在一个与表无关的地方。
    """
    if old in ASPECT_RENAMES:
        return (ASPECT_RENAMES[old],)
    return ASPECT_SPLITS.get(old)


@dataclass(frozen=True)
class _AspectAction:
    """一行要怎么改。**计划里已经算好了全部结果**，写入时不再做任何判断。"""

    table: str
    rowid: int
    where: str
    old: str
    kind: str
    #: 目标面。``rewrite`` 一个，``split`` 两个（第一个给原行，第二个给复制出来的那行）。
    to: tuple[str, ...] = ()
    #: 改写后的 payload（两张带 payload 的表才有）。
    payload: str | None = None
    #: ``split`` 复制出来的那一行的 payload —— 它只与 ``payload`` 差 ``aspect`` 那一格。
    payload_2: str | None = None
    #: 回执：按新面重算出来的键。**不重算的处置是错的** —— 键里编着 aspect 的值。
    dedupe_key: str | None = None


@dataclass(frozen=True)
class AspectRewrite:
    """v2 → v3 的**计划**：改哪些行、删哪些行、拆哪几行。**一行都没动过。**"""

    actions: tuple[_AspectAction, ...] = ()
    renamed: dict[str, int] = field(default_factory=dict)
    split: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.actions

    def describe(self) -> str:
        """给人读的一段。**「删了几行」必须在这里说出来** —— 一个只说「换名字多少行」的
        报告会让删除变成一件没人知道发生过的事。"""
        if self.empty:
            return "词表：三张表里没有旧词，一行都没改。"
        parts: list[str] = []
        for label, counts in (("换名字", self.renamed), ("拆成两行", self.split), ("删除", self.dropped)):
            if counts:
                parts.append(
                    label + " " + "、".join(f"{table} {n} 行" for table, n in sorted(counts.items()))
                )
        return (
            "词表（v2 → v3）："
            + "；".join(parts)
            + "。删除的那些行在新词表里没有对应的那一格（逐表理由见 SPLIT_DISPOSITION）"
            "—— 它们逐字都在备份里。"
        )


def _plan_aspects(conn: sqlite3.Connection, *, db_path: Path) -> AspectRewrite:
    """读出三张表的每一行，算出它该改成什么、删掉还是拆开。**一行都不写。**

    认不出来的词在这里就**整份拒绝**（在备份之前、在任何写入之前）—— 与第一步同一个
    理由：迁移到一半的库比没迁的库更坏，因为「迁移过」这句话之后没有人会再核对它。

    三处**自洽性**判据都在这里做，而不是等写入时炸在某个模型校验上：

    * 两张带 payload 的表：列里的 ``aspect`` 与 payload 里的**必须一致**。读路径只认
      payload（``SqliteMaintenanceStore`` 从 payload 重建对象），所以一份不一致的行在
      今天读得出来、却与它自己的索引列说着两件事。
    * 回执：存着的 ``dedupe_key`` 必须等于**它自己那几列**按同一个函数算出来的键。
      那一列是 ``sha256`` 摘要，验不了就是「这一行不是它自己说的那个格子」。
    * 重算出来的键在新表里**不许撞车**（按 ``(键, attempt)`` 比 —— 同一个格子换了几次
      尝试是正常的，那是两行同一个键）。
    """
    actions: list[_AspectAction] = []
    renamed: dict[str, int] = {}
    split: dict[str, int] = {}
    dropped: dict[str, int] = {}
    refused: list[str] = []
    taken: dict[tuple[str, int], str] = {}

    for name in _ASPECT_TABLES:
        if name == "maintenance_claim":
            rows = conn.execute(
                "SELECT rowid AS rid, dedupe_key, attempt, scene_id, scene_version,"
                " subject_kind, subject_id, aspect, period FROM maintenance_claim"
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT rowid AS rid, aspect, payload FROM {name}"
            ).fetchall()
        for row in rows:
            old = str(row["aspect"])
            targets = _rewrite_of(old)
            if name == "maintenance_claim":
                where = f"{name}[dedupe_key={row['dedupe_key']}#{row['attempt']}]"
            else:
                where = f"{name}[rowid={row['rid']}]"
            if targets is None:
                refused.append(
                    f"  - {where}：不认识的 aspect 取值 {old!r}（v2 的词表："
                    f"{sorted({*ASPECT_RENAMES, *ASPECT_SPLITS})}）"
                )
                continue

            payload: dict | None = None
            if name != "maintenance_claim":
                payload = load_payload(row["payload"], where)
                stated = payload.get("aspect")
                if stated != old:
                    refused.append(
                        f"  - {where}：列里写的是 aspect={old!r}，payload 里写的是 "
                        f"{stated!r} —— 这一行不是它自己说的那个东西"
                    )
                    continue

            if name == "maintenance_claim":
                computed = due_dedupe_key(
                    scene_id=str(row["scene_id"]),
                    scene_version=int(row["scene_version"]),
                    subject_kind=str(row["subject_kind"]),
                    subject_id=str(row["subject_id"]),
                    aspect=old,
                    period=str(row["period"]),
                )
                if computed != str(row["dedupe_key"]):
                    refused.append(
                        f"  - {where}：存着的键与它自己那几列算出来的 {computed} 不一致"
                        " —— 这一行不是它自己说的那个格子"
                    )
                    continue

            rid = int(row["rid"])
            if len(targets) > 1 and SPLIT_DISPOSITION[name] == "drop":
                dropped[name] = dropped.get(name, 0) + 1
                actions.append(_AspectAction(name, rid, where, old, "drop", targets))
            elif len(targets) > 1:  # 拆成两条：原行拿第一半，复制出来的那条拿第二半
                assert payload is not None  # split 只发生在带 payload 的表
                first, second = targets
                split[name] = split.get(name, 0) + 2
                actions.append(
                    _AspectAction(
                        name,
                        rid,
                        where,
                        old,
                        "split",
                        targets,
                        dump_payload(dict(payload, aspect=first)),
                        dump_payload(dict(payload, aspect=second)),
                    )
                )
            elif name == "maintenance_claim":
                new_key = due_dedupe_key(
                    scene_id=str(row["scene_id"]),
                    scene_version=int(row["scene_version"]),
                    subject_kind=str(row["subject_kind"]),
                    subject_id=str(row["subject_id"]),
                    aspect=targets[0],
                    period=str(row["period"]),
                )
                # 同一个格子换了几次尝试是**正常**的（两行同一个键、不同 attempt），所以
                # 撞车要按 ``(键, attempt)`` 看：那个组合相同才是两个格子挤进了一行。
                occupant = taken.setdefault((new_key, int(row["attempt"])), where)
                if occupant != where:
                    refused.append(
                        f"  - {where}：重算出来的键 {new_key}（attempt={row['attempt']}）"
                        f"已经属于 {occupant}"
                    )
                    continue
                renamed[name] = renamed.get(name, 0) + 1
                actions.append(
                    _AspectAction(name, rid, where, old, "rewrite", targets, None, None, new_key)
                )
            else:
                assert payload is not None  # 只有回执那张表没有 payload
                renamed[name] = renamed.get(name, 0) + 1
                actions.append(
                    _AspectAction(
                        name,
                        rid,
                        where,
                        old,
                        "rewrite",
                        targets,
                        dump_payload(dict(payload, aspect=targets[0])),
                    )
                )

    if refused:
        raise StoreError(
            f"{db_path} 里有 {len(refused)} 行**认不出来**，整份拒绝（一行都没有改）：\n"
            + "\n".join(refused)
            + "\n\n"
            "这些行的 aspect 既不在 v2 的词表里，也不是新词表里的词 —— 迁移不知道该把它"
            "们改成什么，而**猜一个面比空着更坏**：空着看得见，猜出来的会被人当成一条"
            "事实读（「这一格刚核过」）。\n\n"
            "两条路：把这一行是什么弄清楚（它可能是手工写进去的，或者来自一个有别的"
            "词表的部署），或者把这个库移开。"
        )
    return AspectRewrite(tuple(actions), renamed, split, dropped)


def _apply_aspects(conn: sqlite3.Connection, plan: AspectRewrite) -> None:
    """按计划改 / 删 / 拆。**自己不开事务** —— 调用方决定它和谁在同一个事务里。"""
    for action in plan.actions:
        if action.kind == "drop":
            _one_row(
                conn.execute(
                    f"DELETE FROM {action.table} WHERE rowid = ? AND aspect = ?",
                    (action.rowid, action.old),
                ),
                action.where,
                "删",
            )
        elif action.kind == "rewrite":
            if action.table == "maintenance_claim":
                _one_row(
                    conn.execute(
                        "UPDATE maintenance_claim SET aspect = ?, dedupe_key = ?"
                        " WHERE rowid = ? AND aspect = ?",
                        (action.to[0], action.dedupe_key, action.rowid, action.old),
                    ),
                    action.where,
                    "改",
                )
            else:
                _one_row(
                    conn.execute(
                        f"UPDATE {action.table} SET aspect = ?, payload = ?"
                        " WHERE rowid = ? AND aspect = ?",
                        (action.to[0], action.payload, action.rowid, action.old),
                    ),
                    action.where,
                    "改",
                )
        else:  # split：原行改成第一半，另插一行第二半
            _one_row(
                conn.execute(
                    "UPDATE change_signal SET aspect = ?, payload = ?"
                    " WHERE rowid = ? AND aspect = ?",
                    (action.to[0], action.payload, action.rowid, action.old),
                ),
                action.where,
                "改",
            )
            # 这一句里七个列名是**故意逐个写下来的**：它们全是 NOT NULL，于是将来给这张表
            # 加一列时，漏在这里的那一列会当场报错（NOT NULL constraint failed），而不是
            # 静默填一个缺省值。这个复制走 ``WHERE rowid = ? AND aspect = ?``（第二半的值），
            # 所以它读到的正是上一句刚改好的那一行。
            _one_row(
                conn.execute(
                    "INSERT INTO change_signal "
                    "(workspace, subject_kind, subject_id, aspect, signalled_at, reason, payload) "
                    "SELECT workspace, subject_kind, subject_id, ?, signalled_at, reason, ? "
                    "FROM change_signal WHERE rowid = ? AND aspect = ?",
                    (action.to[1], action.payload_2, action.rowid, action.to[0]),
                ),
                action.where,
                "复制",
            )


def _one_row(cursor: sqlite3.Cursor, where: str, verb: str) -> None:
    """恰好动了一行，否则中止（整个写入在一个事务里，于是库回到起步时那个版本）。

    计划按 ``rowid`` 落地，而 ``rowid`` 是这次运行**看得见**的那个库里的地址。它在写入
    时不再指那一行（别的进程动过这个库、或者有人手工改过）时只有两种可能：什么都没改
    （静默漏掉一行），或者改错了行。两种都不接受，而两种的表现都是 ``rowcount`` 不是 1。
    """
    if cursor.rowcount != 1:
        raise StoreError(
            f"{where}：计划里要{verb}的那一行，写入时不是恰好一行"
            f"（rowcount={cursor.rowcount}）。\n"
            "计划与写入之间这个库被别的进程动过，或者有人用别的方式改过它。"
            "库没有被改（整个写入在一个事务里，已经回滚）—— 先弄清楚它在被谁用，再重跑。"
        )


# --- 第三步：三张配置表的主键（v3 → v4）----------------------------------------
#
# 这一步与前两步**在种类上不同**：前两步改的是值（补血统、换词表），这一步改的是
# **形状** —— 主键从 ``(id)`` 变成 ``(id, revision)``，并多一列 ``revision``。
# 所以它不能用 UPDATE：SQLite 改不了主键，只能「把旧表改名搬开 → 按同一个形状建回来
# → 搬行 → 删掉旧的」。而正因为是重建，它比其他两步**多一条**要守的东西：建回来的
# 那张表必须与代码认定的形状**逐字一致**，否则「迁移成功」之后每一次写入都会失败。
# 那一份形状因此只有一处（``db.CONFIG_TABLE_DDL``），这里读它，不照抄。

#: v3 的形状里每个 id 最多一行，于是那一行就是**第一版**。
FIRST_REVISION = 1

#: 重建时旧表被搬去的名字。**加下划线前缀**：它只在这个事务里存在，而一个与业务表
#: 同名的临时表会让「这次迁移没走完」在下一次运行里看起来像一张真表。
_PARKED_PREFIX = "_pre_v4_"


def _config_models() -> dict[str, type]:
    """表名 → 它装的那个模型。**在这里导入而不是在模块头上**：理由与 ``migrate()``
    里 ``load_input_proof`` 那处相同 —— 这一步用不着领域模型，前两步也用不着，
    而模块头导入会把「迁移」与「外向链」绑在一起。"""
    from ...domain.outreach import ApprovedClaim, TemplateFamily, WritingPolicy

    return {
        "outreach_family": TemplateFamily,
        "outreach_writing_policy": WritingPolicy,
        "outreach_claim": ApprovedClaim,
    }


def _columns_of(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    """这张表此刻在盘上的列，按序。空元组 = 这张表不存在。"""
    return tuple(
        str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    )


@dataclass(frozen=True)
class _RebuiltRow:
    """一行搬过去之后的样子。**计划里已经算好了全部结果**，写入时不再做任何判断。"""

    table: str
    where: str
    values: dict[str, Any]


@dataclass(frozen=True)
class RevisionRebuild:
    """v3 → v4 的**计划**：哪几张表要重建、每一行搬过去之后长什么样。**一行都没动过。**"""

    #: 要重建的那几张表的表名，按 ``CONFIG_TABLE_DDL`` 的顺序。
    rebuilt: tuple[str, ...] = ()
    rows: tuple[_RebuiltRow, ...] = ()
    moved: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.rebuilt

    def describe(self) -> str:
        """给人读的一段。**搬了几行必须说出来** —— 一次重建最坏的结果是丢行，
        而「重建了 outreach_family」这句话里没有数字可以核。"""
        if self.empty:
            return "配置表的主键：三张表都已经是 (id, 版) 的形状，一行都没有重建。"
        parts = "、".join(
            f"{name} {self.moved.get(name, 0)} 行" for name in self.rebuilt
        )
        return (
            f"配置表的主键（v3 → v4）：重建了 {parts}，每一行按 ``revision = "
            f"{FIRST_REVISION}`` 回填 —— v3 的形状里每个 id 最多一行，那一行就是"
            "第一版。旧表（``(id)`` 主键）已经不在了。"
        )


def _plan_revisions(conn: sqlite3.Connection, *, db_path: Path) -> RevisionRebuild:
    """算出三张配置表要怎么重建、每一行搬过去长什么样。**一行都不写。**

    两条判据都在这里做完，理由与另外两步逐字相同：迁移到一半的库比没迁的库更坏，
    因为「迁移过」这句话之后没有人会再核对它。

    * **这一行在新形状里还成不成立**：payload 补上 ``revision`` 之后必须过得了它
      自己的领域模型。过不了就整份拒绝并点名 —— 一行搬过去而读不出来，失败会推迟
      到服务启动之后某一次读，那时它长得像「库坏了」。
    * **已经认过形状的那张表仍然自洽**：表里若已经有 ``revision`` 那一列（一个被手工
      改过、或者由别的部署写过的库），列里的值与 payload 里那一格必须一致。读路径
      只认 payload，一份不一致的行今天读得出来、却与它自己的索引列说着两件事。
    """
    models = _config_models()
    rebuilt: list[str] = []
    rows: list[_RebuiltRow] = []
    moved: dict[str, int] = {}
    refused: list[str] = []

    for name, key_column, _ in CONFIG_TABLE_DDL:
        columns = _columns_of(conn, name)
        if not columns:
            # ``replay_ddl`` 刚跑过，三张表一定在。真的不在时是别的问题（权限、
            # 磁盘），让它在下一次建表上炸出来比在这里猜一句好。
            continue
        if "revision" in columns:
            for row in conn.execute(
                f"SELECT {key_column} AS key, revision AS rev, payload FROM {name}"  # noqa: S608
            ).fetchall():
                loaded = load_payload(str(row["payload"]), f"{name}/{row['key']}")
                if loaded.get("revision") != int(row["rev"]):
                    refused.append(
                        f"{name}[{row['key']}]：列里的版本是 {row['rev']}，"
                        f"而 payload 里那一格是 {loaded.get('revision')!r}"
                    )
            continue
        rebuilt.append(name)
        for row in conn.execute(
            f"SELECT {', '.join(columns)} FROM {name}"  # noqa: S608
        ).fetchall():
            values: dict[str, Any] = {column: row[column] for column in columns}
            key = str(values[key_column])
            where = f"{name}[{key}]"
            loaded = load_payload(str(values["payload"]), where)
            if "revision" in loaded:
                refused.append(
                    f"{where}：这张表还没有 ``revision`` 那一列，而 payload 里已经有了"
                )
                continue
            try:
                model = models[name].model_validate({**loaded, "revision": FIRST_REVISION})
            except ValidationError as exc:
                refused.append(f"{where}：{exc}")
                continue
            # 用**模型再吐一遍**的结果当搬过去的那一份，而不是手工往 dict 里塞一格：
            # 原样搬会让库里的字节由 v3 那一版序列化器决定，而写入路径记的是
            # ``dump_payload(model.model_dump(mode="json"))`` —— 两者差一个空格就是
            # 「同一次发布重放一次会报错」。
            values["payload"] = dump_payload(model.model_dump(mode="json"))
            values["revision"] = FIRST_REVISION
            rows.append(_RebuiltRow(table=name, where=where, values=values))
            moved[name] = moved.get(name, 0) + 1

    if refused:
        raise StoreError(
            f"{db_path} 里有几行搬不过去（v3 → v4 要让每个 id 能存多版）：\n"
            + "\n".join(f"  - {item}" for item in refused)
            + "\n\n"
            "**整份拒绝，一行都没写。** 这些行读不出它自己说的那个对象，而重建之后"
            "它们会被原样搬进一张新形状的表里 —— 那时失败长在某一次读上，看起来像"
            "「库坏了」。请先把它们弄清楚（它们可能是手工写进去的，或者来自一个有别的"
            "模型的部署），或者把这个库移开。"
        )
    return RevisionRebuild(tuple(rebuilt), tuple(rows), moved)


def _apply_revisions(conn: sqlite3.Connection, plan: RevisionRebuild) -> None:
    """按计划重建。**自己不开事务** —— 调用方决定它和谁在同一个事务里。

    四步，一句都不能少也不能换顺序：

    1. 旧表改名搬开（``ALTER TABLE ... RENAME TO``）；
    2. 按**代码认定的形状**建回来 —— 建表语句读 ``db.CONFIG_TABLE_DDL``，不在这里
       照抄。照抄的话，两边分家的表现是「迁移建出一张没有主键的表」，而那是一句
       **不会报错**的错误；
    3. 把计划里那些行搬进去；
    4. 删掉搬开的那张。

    用 ``execute`` 而不是 ``executescript``：后者在 Python 的 ``sqlite3`` 里会**先
    隐式提交**当前事务，于是第 2 步与另外三步不在同一个事务里 —— 而整个迁移的全部
    保证都建在「它们在一个事务里」这句话上。
    """
    for name in plan.rebuilt:
        parked = f"{_PARKED_PREFIX}{name}"
        conn.execute(f"ALTER TABLE {name} RENAME TO {parked}")  # noqa: S608
        conn.execute(CONFIG_TABLE_BY_NAME[name])
        rows = [row for row in plan.rows if row.table == name]
        if rows:
            columns = list(rows[0].values)
            conn.executemany(
                f"INSERT INTO {name} ({', '.join(columns)})"  # noqa: S608
                f" VALUES ({', '.join('?' for _ in columns)})",
                [tuple(row.values[column] for column in columns) for row in rows],
            )
        conn.execute(f"DROP TABLE {parked}")  # noqa: S608


# --- 路线那一侧（v4 → v5）-----------------------------------------------------


#: v5 重建 ``contact_route`` 时旧表被搬去的名字。**与 v4 那批分开**：两批表在不同的
#: 步骤里被搬开，共用一个前缀会让「这是上一步留下的」与「这是这一步留下的」在报告里
#: 读成同一句话。
_PARKED_V5_PREFIX = "_pre_v5_"

#: 观察时刻的取值来源。报告里按这两档**分开数**：第二档是退让，不是同一个值。
OBSERVED_FROM_FIRST_SEEN = "first_seen_at"
OBSERVED_FROM_STORED_AT = "stored_at"

#: 建表语句里 ``CREATE INDEX IF NOT EXISTS <名>`` 的那个名字。
_INDEX_NAME_PATTERN = re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)


def _declared_index_names(ddl: str) -> tuple[str, ...]:
    """这段建表语句声明了哪几个索引。

    **从 DDL 里读**，不另抄一份名字表：抄一份的话，DDL 里多加一个索引时回读核对不会
    知道 —— 而「少一个索引」正是第四步会出的那种不出声的错（见
    :func:`_drop_indexes_of`）。
    """
    return tuple(_INDEX_NAME_PATTERN.findall(ddl))


def _drop_indexes_of(conn: sqlite3.Connection, table: str) -> None:
    """把 ``table`` 上附着的索引全删掉。

    重建要把新形状的**同名**索引建回来（``ROUTE_TABLE_BY_NAME`` 里那两句 ``CREATE
    INDEX IF NOT EXISTS``），而索引名是**库级唯一**的：搬开的那张表还把旧索引挂在
    原名下，于是新表那一句 ``IF NOT EXISTS`` 会**静默跳过** —— 新表建出来了、索引
    没有，之后每一次按账号取路线变成全表扫，而没有任何一处会说出来。

    名字**从 ``sqlite_master`` 读**，不在这里照抄：照抄的话，旧库上多一个或少一个
    索引都会让「打算删的那几个」与「实际附着的那几个」分成两份。
    ``sqlite_autoindex_*`` 是主键/唯一约束自己带的，删不掉也不需要删（它跟着表走）。
    """
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", (table,)
    ).fetchall():
        name = str(row["name"])
        if name.startswith("sqlite_autoindex"):
            continue
        conn.execute(f"DROP INDEX {name}")  # noqa: S608


@dataclass(frozen=True)
class RouteSplit:
    """v4 → v5 的**计划**：``contact_route`` 从「事件流」拆成「历史 + 当前」。

    旧表的每一行**都是**一次观察 —— 只是它当时没有名字，于是被读成了「这个账号的
    路线」。所以搬过去不是一次翻译，是一次**认领**：给它补上 ``canonical_key`` /
    ``observed_at`` / ``observation_id`` 三格，再按规范键折叠。

    折叠是**有损**的（同一个键下 N 行观察只剩一行读法），而这一点必须在对账里对得
    上：观察行数守恒、投影行数 ≤ 观察行数且每个键恰好一行。
    """

    rebuilt: bool = False
    #: 旧表里读到的行数。**与下面两个数不是一回事**：重复行会被并成一条观察（见
    #: ``repeated``），而报告里「搬了几行」若用 ``observations`` 说，重复的那几行就
    #: 在一次对账里凭空消失了。
    legacy_rows: int = 0
    #: 要搬进 ``route_observation`` 的那些行 —— 已经是**新形状的字典**，写入时不再做
    #: 任何判断（与 :class:`_RebuiltRow` 同一条纪律）。
    observations: tuple[dict[str, Any], ...] = ()
    #: 按规范键折出来的当前读法，同样已经是字典。
    projections: tuple[dict[str, Any], ...] = ()
    #: 旧表里有几行给出的是**同一条观察**（``observation_id`` 一样）。旧形状里一行是
    #: 一次写入，而新形状里观察的身份**由内容定** —— 两行内容与观察时刻都一样时，它们
    #: 在今天的模型里就是一条，而 ``record_route`` 对同一条观察重放的回答也是「什么都
    #: 不做」。所以这里并掉，并把并掉的条数**数出来**（§19：更少的真实结果可以，多出来
    #: 的错误结果不行 —— 而一个没说出口的合并正是后者）。
    repeated: int = 0
    #: ``observed_at`` 取自哪一格，各多少行。键是 :data:`OBSERVED_FROM_FIRST_SEEN`
    #: 与 :data:`OBSERVED_FROM_STORED_AT`。
    observed_from: dict[str, int] = field(default_factory=dict)
    #: 这一步**没有**重建时的那句话。非空当且仅当 ``rebuilt`` 为假 —— 一个空串会
    #: 让「表已经是新形状」与「库根本没这张表」在报告里读成同一件事。
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.rebuilt

    def describe(self) -> str:
        if not self.rebuilt:
            return self.note
        first = self.observed_from.get(OBSERVED_FROM_FIRST_SEEN, 0)
        stored = self.observed_from.get(OBSERVED_FROM_STORED_AT, 0)
        return (
            f"路线表的拆表（v4 → v5）：旧 ``contact_route`` 的 {self.legacy_rows} 行"
            f"搬成 ``route_observation`` 的 {len(self.observations)} 条观察"
            f"（其中 {first} 条的观察时刻取自路线自己的 ``first_seen_at``，{stored} 条"
            "退回该行的 ``stored_at`` —— 旧形状里没有第三处可查）"
            + (
                f"，另有 {self.repeated} 行与前面的行给出的是**同一条**观察"
                "（内容与观察时刻都一样，而新形状里观察的身份由内容定 —— 它们并成了"
                "一条，不是丢了一条）"
                if self.repeated
                else ""
            )
            + f"，并按规范键折成 {len(self.projections)} 行当前读法。折叠是**有损**的："
            "同一个键下的多条观察只留一行读法，而每一条都还在历史里。旧表（主键 "
            "``seq``）已经不在了。"
        )


def _plan_routes(
    conn: sqlite3.Connection,
    *,
    db_path: Path,
    stamped: dict[str, list[_StampedRow]] | None = None,
) -> RouteSplit:
    """算出 ``contact_route`` 要怎么拆、每一行搬过去长什么样。**一行都不写。**

    判据与另外三步逐字相同：这一行在新形状里还成不成立 —— 过不了它自己的领域模型
    就整份拒绝并点名。一行搬过去而读不出来，失败会推迟到服务启动之后某一次读，那时
    它长得像「库坏了」。

    ## 为什么读的是**第一步计划里**那一行，而不是盘上那一行

    ``workspace`` 是第一步盖上去的章，而这一步与另外三步一样跑在**任何一次写入之前**
    —— 所以对一个 v1 的库，盘上的 ``contact_route`` 行里根本没有这一格，照盘上的读
    会以 ``workspace / Field required`` 整份拒绝，而那是**预检顺序**造出来的拒绝，
    不是这些行有问题。

    要盖的章只有一个来源：第一步的计划。所以 ``stamped`` 从那里传进来 —— 两处因此
    由构造共用同一条判据。「第四步自己挑一个工作区」在结构上做不到：第一步证不出来的
    行在那里就整份拒绝了，走不到这里；能走到这里的每一行，它的章都是被证明过的那一个。

    ``stamped is None`` 是 v2 起的库（那时第一步早就在某一次迁移里跑过了，payload 里
    ``workspace`` 已经在盘上），读盘即可。

    ## ``observed_at`` 从哪来 —— 两档，而且要分开报

    新表里 ``observed_at`` 是「**我们看到那一页**的时刻」。旧行没有这一格，能查的只有
    两处，而它们的强度不同：

    * ``ContactRoute.first_seen_at`` —— 写路径当场记下的「第一次看到这条路线所依据的
      材料」的时刻。**这是这一格真正想要的取值**，两处都在时用它。
    * 旧行的 ``stored_at`` —— 落库的墙钟。它总是偏晚（材料先抓到、再绑定、再落库），
      但它是**真实发生过的时刻**，不是补一个现在。

    两档都取不到时拒绝那一行 —— 新表这一格非空，而「补一个现在」会让一条几年前的
    观察在报告里看起来是刚看到的。

    ## 折叠按 ``(observed_at, observation_id)`` 升序做

    ``fold_observation`` 的第三档是「否则折进来」，所以按升序折一遍得到的当前读法，
    与那一批观察无论以什么顺序到达时收敛到的那个**逐位相同**。``revision`` 因此等于
    这一组观察的条数 —— 那是「这个存储把每一条都改写了一次」这件事本身，而旧库的到达
    顺序已经无从还原（``seq`` 是插入顺序，不是观察顺序）。

    ## 两行给出同一条观察时，并成一条 —— 但数出来

    旧形状里一行是一次**写入**，新形状里观察的身份**由内容定**（``observation_id_for``
    的输入里没有 ``seq``）。所以两行只要 ``route`` 逐位相同、导出的 ``observed_at``
    又落在同一档，它们就是同一条观察 —— 而这不是一句可以被忽略的巧合：同一个账号被
    反复绑定而路线没变过时，旧库里的行逐字就是这样的。

    并掉是**唯一**正确的处置，理由不是「省一行」：另两条路都更坏。拒绝会让一个完全
    正常的库迁不动；而给其中一行另选一个 ``observed_at``（例如退回 ``stored_at``）
    等于凭空造出一个观察顺序 —— 折叠那一层会照它排，而排错位是静默的。并掉之后
    ``RouteSplit.repeated`` 把条数记下来，报告里说得出「N 行并成了 M 条」；一个没说
    出口的合并才是这里真正的风险（§19）。
    """
    # 与 ``_config_models`` 同一个理由：这一步用不着领域模型，而模块头导入会把
    # 「迁移」与「路线判定」绑在一起。
    from ...domain.entities import ContactRoute
    from ...domain.route_records import (
        RouteObservation,
        canonical_route_key,
        fold_observation,
        observation_id_for,
    )

    columns = _columns_of(conn, "contact_route")
    if not columns:
        # ``replay_ddl`` 刚跑过，这张表一定在。真的不在时是别的问题，而这一步没有
        # 可拆的东西 —— 说出来，别让它读成「拆过了、零行」。
        return RouteSplit(note="路线表：这个库没有 ``contact_route`` 这张表，没有可拆的东西。")

    refused: list[str] = []
    if "canonical_key" in columns:
        # 已经是 v5 的形状。与 ``_plan_revisions`` 对「已经认过形状的那张表」同一个
        # 理由：读路径只认 payload，一份列与 payload 说着两件事的行今天读得出来，
        # 却与它自己的主键说着两件事。
        for row in conn.execute(
            "SELECT canonical_key AS key, payload FROM contact_route"  # noqa: S608
        ).fetchall():
            where = f"contact_route[{row['key']}]"
            loaded = load_payload(str(row["payload"]), where)
            if loaded.get("canonical_key") != str(row["key"]):
                refused.append(
                    f"{where}：列里的规范键与 payload 里那一格"
                    f"（{loaded.get('canonical_key')!r}）不是同一个"
                )
        if refused:
            raise StoreError(
                f"{db_path} 的 ``contact_route`` 已经是 v5 的形状，但有几行不自洽：\n"
                + "\n".join(f"  - {item}" for item in refused)
                + "\n\n**一行都没写。** 请先把它们弄清楚，或者把这个库移开。"
            )
        return RouteSplit(
            note=(
                "路线表：``contact_route`` 已经是 v5 的形状（主键是规范键），"
                "一行都没有重建。"
            )
        )
    if "seq" not in columns:
        raise StoreError(
            f"{db_path} 的 ``contact_route`` 既不认得出 v5 的形状（没有 "
            "``canonical_key`` 列），也不认得出 v4 的形状（没有 ``seq`` 列）。\n"
            "**一行都没写。** 这张表不是这份代码认得的那两版里任何一版造出来的；"
            "请先弄清楚它是谁写的，或者把这个库移开。"
        )

    # 第一步的计划按 ``seq`` 给出这一行**将要**长成的样子（见 docstring）。它是这里
    # 唯一被证明过的工作区来源。
    stamped_routes: dict[str, dict] = {}
    if stamped is not None:
        stamped_routes = {item.key: item.payload for item in stamped.get("contact_route", ())}

    legacy = conn.execute(
        "SELECT seq, account_id, payload, stored_at FROM contact_route ORDER BY seq"  # noqa: S608
    ).fetchall()
    observations: list[RouteObservation] = []
    observed_from: dict[str, int] = {}
    #: 已经认领过的观察身份。见下面那一处 ``repeated``。
    seen: set[str] = set()
    repeated = 0
    for row in legacy:
        where = f"contact_route[seq={row['seq']}]"
        loaded = stamped_routes.get(str(row["seq"]))
        if loaded is None:
            loaded = load_payload(str(row["payload"]), where)
        try:
            route = ContactRoute.model_validate(loaded)
        except ValidationError as exc:
            refused.append(f"{where}：{exc}")
            continue
        if route.account_id != str(row["account_id"]):
            refused.append(
                f"{where}：列里的账号是 {row['account_id']!r}，而 payload 里那一格是"
                f" {route.account_id!r} —— 两者必须是同一个（读路径只认 payload，"
                "于是这一行今天读得出来，却与它自己的索引列说着两件事）"
            )
            continue
        observed_at = route.first_seen_at
        source = OBSERVED_FROM_FIRST_SEEN
        if observed_at is None:
            source = OBSERVED_FROM_STORED_AT
            try:
                observed_at = datetime.fromisoformat(str(row["stored_at"]))
            except ValueError as exc:
                refused.append(f"{where}：``stored_at`` 不是时刻（{exc}）")
                continue
        key = canonical_route_key(
            workspace=route.workspace, account_id=route.account_id, email=route.email
        )
        try:
            candidate = RouteObservation(
                observation_id=observation_id_for(
                    workspace=route.workspace,
                    account_id=route.account_id,
                    canonical_key=key,
                    observed_at=observed_at,
                    route=route,
                ),
                canonical_key=key,
                workspace=route.workspace,
                account_id=route.account_id,
                observed_at=observed_at,
                route=route,
            )
        except ValidationError as exc:
            # 两处都取到时刻、但那一格不带时区（或者 route 与它自己的键对不上）时落
            # 在这里。**不替它补时区**：一个从墙钟字符串补上去的 UTC 会让这条观察的
            # 「谁更新」与真实顺序无关地排错位，而排错位是**静默**的 —— 折叠那一层
            # 只会照它以为的顺序挑最大的那一条。
            refused.append(f"{where}（观察时刻取自 {source}）：{exc}")
            continue
        if candidate.observation_id in seen:
            # 这一行与前面某一行给出的是**同一条观察**：内容与观察时刻都一样（旧形状
            # 里能查到的时刻只有那两处，而两行都落在同一档）。并掉它 —— 新形状里观察
            # 的身份由内容定，而 ``record_route`` 对同一条观察重放的回答正是「什么都
            # 不做」。不并的话，用户拿到的是一个**主键撞车**的 sqlite3 错误，包在
            # 「写入这一步失败了」里 —— 那句话说的是磁盘或约束，读起来像运维问题，
            # 而真原因是这一步自己重了两条。
            repeated += 1
            continue
        seen.add(candidate.observation_id)
        observed_from[source] = observed_from.get(source, 0) + 1
        observations.append(candidate)

    if refused:
        raise StoreError(
            f"{db_path} 里有几行搬不过去（v4 → v5 要把 ``contact_route`` 拆成"
            "不可变历史 + 当前投影）：\n"
            + "\n".join(f"  - {item}" for item in refused)
            + "\n\n"
            "**整份拒绝，一行都没写。** 这些行读不出它自己说的那条路线，而重建之后"
            "它们会被原样搬进新形状的表里 —— 那时失败长在某一次读上，看起来像"
            "「库坏了」。请先把它们弄清楚（它们可能是手工写进去的，或者来自一个有"
            "别的模型的部署），或者把这个库移开。"
        )

    by_key: dict[str, list[RouteObservation]] = {}
    for observation in observations:
        by_key.setdefault(observation.canonical_key, []).append(observation)

    # 迁移写下去的 ``stored_at``：**这一次迁移**落库的时刻。旧行自己的 ``stored_at``
    # 不是它 —— 那一格在上面的退让档里已经被当成了 ``observed_at``，两处用同一个值
    # 会让「落库时刻」变成一句关于过去的话。
    at = utc_now().isoformat()
    observation_rows: list[dict[str, Any]] = []
    for observation in observations:
        observation_rows.append(
            {
                "observation_id": observation.observation_id,
                "canonical_key": observation.canonical_key,
                "account_id": observation.account_id,
                "workspace": observation.workspace,
                "observed_at": observation.observed_at.isoformat(),
                "payload": dump_payload(observation.model_dump(mode="json")),
                "stored_at": at,
            }
        )

    projection_rows: list[dict[str, Any]] = []
    for key in sorted(by_key):
        current = None
        for observation in sorted(
            by_key[key], key=lambda item: (item.observed_at, item.observation_id)
        ):
            # ``at=observation.observed_at`` 与 ``EvidenceStore.record_route`` 逐字
            # 相同 —— 折叠是「关于那一刻的一次改写」，不是关于迁移这一趟的墙钟。
            current = fold_observation(current, observation, at=observation.observed_at).projection
        if current is None:  # pragma: no cover - by_key 里的每个列表都非空
            continue
        projection_rows.append(
            {
                "canonical_key": current.canonical_key,
                "account_id": current.account_id,
                "workspace": current.workspace,
                "payload": dump_payload(current.model_dump(mode="json")),
                "stored_at": at,
            }
        )

    return RouteSplit(
        rebuilt=True,
        legacy_rows=len(legacy),
        observations=tuple(observation_rows),
        projections=tuple(projection_rows),
        repeated=repeated,
        observed_from=observed_from,
    )


def _apply_routes(conn: sqlite3.Connection, plan: RouteSplit) -> None:
    """按计划拆表。**自己不开事务** —— 调用方决定它和谁在同一个事务里。

    五步，顺序不能换：

    1. 旧表改名搬开（``ALTER TABLE ... RENAME TO``）；
    2. **删掉搬开那张表上的索引** —— 理由写在 ``_drop_indexes_of`` 里，这是这一步
       唯一一处「不做也不会报错、但结构变了」的动作；
    3. 按**代码认定的形状**建回来 —— 语句读 ``db.ROUTE_TABLE_BY_NAME``，不在这里照抄。
       照抄的话，两边分家的表现是「迁移建出一张没有主键的表」，而那是一句**不会报错**
       的错误；
    4. 把观察搬进 ``route_observation``、把折出来的当前读法搬进 ``contact_route``；
    5. 删掉搬开的那张。

    ``route_observation`` / ``route_rejection`` **不重建**：它们在 ``replay_ddl`` 那一
    步已经是新形状（一个 v4 的库里它们根本不存在，于是那一步刚把空表建出来），没有
    旧行要搬。只有 ``contact_route`` 是「同名不同形」。

    用 ``execute`` 而不是 ``executescript``：后者在 Python 的 ``sqlite3`` 里会**先
    隐式提交**当前事务（见 ``_apply_revisions``），而整个迁移的全部保证都建在「它们
    在一个事务里」这句话上。建表语句因此要切开（``split_ddl_statements``）。
    """
    if not plan.rebuilt:
        return
    parked = f"{_PARKED_V5_PREFIX}contact_route"
    conn.execute(f"ALTER TABLE contact_route RENAME TO {parked}")  # noqa: S608
    _drop_indexes_of(conn, parked)
    for statement in split_ddl_statements(ROUTE_TABLE_BY_NAME["contact_route"]):
        conn.execute(statement)
    for table, rows in (
        ("route_observation", plan.observations),
        ("contact_route", plan.projections),
    ):
        if not rows:
            continue
        columns = list(rows[0])
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(columns)})"  # noqa: S608
            f" VALUES ({', '.join('?' for _ in columns)})",
            [tuple(row[column] for column in columns) for row in rows],
        )
    conn.execute(f"DROP TABLE {parked}")  # noqa: S608


# --- 研究请求那一侧（v5 → v6）---------------------------------------------------


#: 第五步只有这一张表。与 ``CONFIG_TABLE_DDL`` / ``ROUTE_TABLE_DDL`` 放在一起读：
#: 那两族各自是一组表，这里是一张 —— 而「这一族有几张表」在任何一处都只有一个答案。
_JOB_TABLE = "research_job"

#: v5 → v6 重建 ``research_job`` 时，**先建出来的那一张**叫什么。
#:
#: 名字里带版本号是故意的：它不是一个会长期存在的表（四步之内就被改名成
#: ``research_job``），而一个听起来像正经表的名字会让人以为它是。它与
#: ``_PARKED_*_PREFIX`` 那一族方向相反 —— 那边的名字说的是「旧表被搬到了这里」，
#: 这里说的是「新表先建在这里」。两件事共用一个命名，报告里「这一步留下的东西还在
#: 不在」就会被读成另一件事。
_V6_NEW_JOB_TABLE = "research_job_v6"

#: ``input_kind TEXT NOT NULL CHECK (input_kind IN ('a', 'b'))`` 里那一份词表。
#:
#: **只认这一种写法**（``IN`` 后面一对括号、里面是若干个引号括起来的词）。认不出来时
#: :func:`_admitted_kinds` 返回 ``None``，而调用方**拒绝**而不是跳过：一张读不出自己
#: 约束的表，重建它就是把它的约束**换成**这里这一份，而换之前没有任何一处证明过它在
#: 盘上原本说的是什么。
_INPUT_KIND_CHECK = re.compile(
    r"input_kind\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*input_kind\s+IN\s*\(\s*([^)]*?)\s*\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _admitted_kinds(ddl: str) -> tuple[str, ...] | None:
    """这段建表语句里 ``input_kind`` 那一格认得的词，按写下来的顺序。``None`` = 认不出来。

    判据是**词**而不是那句建表语句本身，理由写在模块 docstring 里：SQLite 会自己改写
    它存下来的那句话（``IF NOT EXISTS`` 不见了、表名被引号包起来），逐字比的话这条命令
    每跑一次都重建一遍 —— 一个永远收敛不了的判据与一个不存在的判据，在报告里长得一样。
    """
    found = _INPUT_KIND_CHECK.search(ddl)
    if found is None:
        return None
    return tuple(part.strip().strip("'\"") for part in found.group(1).split(","))


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    """这张表此刻在盘上的建表语句（SQLite 自己存下来的那一句）。空串 = 这张表不在。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    return "" if row is None or row["sql"] is None else str(row["sql"])


def _job_ddl_as(table: str) -> str:
    """``research_job`` 的建表语句，表名换成 ``table``。**形状一个字不动。**

    句子里另外两处 ``research_job`` 在注释里（换不换都不影响 SQLite 读到的东西），真正
    那个名字是 ``CREATE TABLE IF NOT EXISTS`` 后面那一个 —— 它必须换，因为搬行用的那张
    是**新名字**，理由见 :func:`_apply_job_kind`。

    形状从 ``JOB_TABLE_BY_NAME`` 取，不在这里照抄：照抄的话，两边分家的表现是「迁移把
    CHECK 又搬回了旧词表」—— 重建照样成功，只是 cohort 仍然写不进去，而那正是这一步
    要修的东西。
    """
    return JOB_TABLE_BY_NAME[_JOB_TABLE].replace(_JOB_TABLE, table)


def _foreign_keys_to(conn: sqlite3.Connection, table: str) -> tuple[tuple[str, str], ...]:
    """哪些表的哪一列引用着 ``table``：``(表名, 列名)``。**问出来，不是写下来。**

    写一份在这儿的代价不是「多写几行」：``work_item.job_id`` 是今天**唯一**一处引用
    ``research_job`` 的，于是那份名单会在下一个引用者出现时**悄悄少一个** —— 而它正是
    第五步对账要用的那一格（「引用它的每一行都还指得到」），少一个的表现是「核过了、
    没问题」，恰好是这一格最不该得出的那种结论。
    """
    found: list[tuple[str, str]] = []
    names = [
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    ]
    for name in names:
        if name == table:
            continue
        for fk in conn.execute(f"PRAGMA foreign_key_list({name})").fetchall():  # noqa: S608
            if str(fk["table"]) == table:
                found.append((name, str(fk["from"])))
    return tuple(found)


@dataclass(frozen=True)
class JobKindWidening:
    """v5 → v6 的**计划**：``research_job`` 要不要重建。**一行都没动过。**

    ``present=False`` 与 ``rebuilt=False`` 是两句不同的话：前者是「这张表不在盘上」
    （``replay_ddl`` 会按这一版代码的形状建它），后者是「它在，而且认得的词与代码
    一致」。报告里都读作「一行都没有重建」，但理由不同 —— 而理由正是运维要看的。
    """

    table: str = _JOB_TABLE
    present: bool = False
    rebuilt: bool = False
    #: 计划里要原样搬过去的那几行（键值）。**重建唯一会丢的东西就是行**，所以这个
    #: 集合在提交之前与回读之后各被核一次。
    keys: frozenset[str] = frozenset()
    #: 盘上那张表此刻认得的词。``present=False`` 时是空元组。
    admitted_on_disk: tuple[str, ...] = ()
    #: 这一版代码的建表语句认得的词。
    admitted_declared: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.rebuilt

    @property
    def rows(self) -> int:
        return len(self.keys)

    def describe(self) -> str:
        """给人读的一段。**搬了几行必须说出来** —— 一次重建最坏的结果是丢行，而
        「重建了 research_job」这句话里没有数字可以核。"""
        declared = "、".join(self.admitted_declared)
        if not self.present:
            return (
                "研究请求的输入种类（v5 → v6）：这张表不在盘上（``replay_ddl`` 会按这一版"
                f"代码的形状建它：{declared}），没有可重建的东西。"
            )
        if not self.rebuilt:
            return (
                "研究请求的输入种类（v5 → v6）：盘上那张表认得的词与这一版代码的一致"
                f"（{declared}），一行都没有重建。"
            )
        return (
            f"研究请求的输入种类（v5 → v6）：重建了 {self.table} {self.rows} 行 —— "
            f"``input_kind`` 那一格原来认得"
            f"（{'、'.join(self.admitted_on_disk)}），现在认得（{declared}）。"
            "旧表与搬行用的那张临时表都不在了。"
        )


def _plan_job_kind(conn: sqlite3.Connection, *, db_path: Path) -> JobKindWidening:
    """算出 ``research_job`` 要不要重建、要搬哪几行。**一行都不写。**

    判据是**这张表认得的词**（理由见模块 docstring 与 :func:`_admitted_kinds`）。四个
    答案，而第四个是拒绝：

    * 这张表不在盘上 —— ``replay_ddl`` 刚按这一版的形状建了它，没有可修的东西；
    * 认得的词与代码这一份**一样** —— 没有可修的东西（**重跑走的就是这一条**：一次
      迁完的库再跑一次，这一步读回来的是同一批词）；
    * 不一样 —— 要重建，这一步要修的就是这个形状；
    * **读不出来**（那一格上没有 CHECK，或者 CHECK 不是 ``IN (...)`` 的形状）—— 整份
      拒绝并点名。猜一个「大概是想说这几个」比不修更坏：重建会把那张表的约束换成这里
      这一份，而换之前没有任何一处证明过它在盘上原本说的是什么。

    还有一条只在这一步存在的判据：**每一行都得过得去新的那一份**。行的 ``input_kind``
    若不在代码这一份词表里，搬过去会撞在 CHECK 上 —— 而那是一个在事务中间才炸出来的
    失败（外键强制此时已经关掉了，SQLite 替我们拦不住东西）。所以它在计划里先算完，
    一行一行点名，理由与另外几步逐字相同：迁移到一半的库比没迁的库更坏。
    """
    declared = _admitted_kinds(JOB_TABLE_BY_NAME[_JOB_TABLE])
    if declared is None:
        raise StoreError(
            f"这一版代码里 {_JOB_TABLE} 的建表语句读不出 ``input_kind`` 那一格认得的词。\n"
            "建表语句是这一格词表的唯一一份（``db.JOB_TABLE_DDL``），而它读不出来时，"
            "没有任何一处说得清「重建之后这张表该认得哪些词」。这是代码的问题，不是库的"
            "问题：先把那一句写回 ``CHECK (input_kind IN (...))`` 的形状。"
        )
    if not _columns_of(conn, _JOB_TABLE):
        return JobKindWidening(present=False, admitted_declared=declared)
    on_disk = _admitted_kinds(_table_sql(conn, _JOB_TABLE))
    if on_disk is None:
        raise StoreError(
            f"{db_path} 里 {_JOB_TABLE}.input_kind 那一格读不出它认得的词 —— 它的建表"
            "语句里没有 ``CHECK (input_kind IN (...))`` 这个形状的东西。\n"
            "**整份拒绝，一行都没写。** 不猜它本来该认得哪些词：重建会把那一格的约束"
            "换成代码这一份，而换之前没有任何一处证明过它在盘上原本说的是什么。\n"
            "它可能被手工改过，也可能来自一个有别的约束的部署 —— 请先弄清楚是哪一个。"
        )
    if on_disk == declared:
        return JobKindWidening(
            present=True, admitted_on_disk=on_disk, admitted_declared=declared
        )

    key_column = JOB_KEY_COLUMNS[_JOB_TABLE]
    allowed = set(declared)
    refused: list[str] = []
    keys: set[str] = set()
    for row in conn.execute(
        f"SELECT {key_column} AS key, input_kind FROM {_JOB_TABLE}"  # noqa: S608
    ).fetchall():
        value = str(row["input_kind"])
        if value not in allowed:
            refused.append(
                f"{_JOB_TABLE}[{row['key']}]：input_kind 是 {value!r}，"
                f"而这一版代码认得的只有 {'、'.join(declared)}"
            )
            continue
        keys.add(str(row["key"]))
    if refused:
        raise StoreError(
            f"{db_path} 里有几行搬不过去（v5 → v6 要换掉 {_JOB_TABLE}.input_kind 那一格的"
            "约束）：\n"
            + "\n".join(f"  - {item}" for item in refused)
            + "\n\n"
            "**整份拒绝，一行都没写。** 搬过去的那一句会撞在新约束上 —— 而那时报出来的"
            "是一句 CHECK 撞车，看起来像存储坏了。请先把它们弄清楚（它们可能是手工写进去"
            "的，或者来自一个约束更宽的部署），或者把这个库移开。"
        )
    return JobKindWidening(
        present=True,
        rebuilt=True,
        keys=frozenset(keys),
        admitted_on_disk=on_disk,
        admitted_declared=declared,
    )


def _apply_job_kind(conn: sqlite3.Connection, plan: JobKindWidening) -> None:
    """按计划重建那一张表。**自己不开事务**（同 ``_apply_revisions``）。

    四步，而顺序与 ``_apply_revisions`` **不同** —— 那不是风格差异，是那一套在这里会
    **静默出错**：

    1. 那边第一步是「旧表改名搬开」，这里不能那么做。``ALTER TABLE ... RENAME`` 会把
       引用它的那一句（``work_item.job_id`` 的 ``REFERENCES research_job``）一起改写成
       指着搬开的那张表；接着第 4 步删掉搬开的那张之后，外键指向一张**不存在**的表，
       而盘上一切正常 —— ``ensure_schema`` 只看表在不在，不看外键指向哪。
    2. 所以这里是「按新形状建一张（新名字）→ 搬行 → 丢掉旧表 → 把新的改名过来」。
       改名那一步 SQLite 会把引用改回这个名字，正是我们要的那句。
    3. 搬行按**列名**搬，而列名从刚建出来的那张读（它才是代码认定形状的落地）——
       旧表多一列或少一列时这会是一句 ``no such column``，一句**会报错**的错，
       而不是悄悄搬少几格。
    4. 丢弃旧表这一步要求外键强制已经关掉，而那件事只能在事务之外做（见 ``migrate()``）。
       这一步因此带出一条别步没有的义务：搬漏的行要**自己**算出来，见
       :func:`_require_job_rows_kept`。

    每一步都用 ``execute`` 而不是 ``executescript``：后者在 Python 的 ``sqlite3`` 里会
    **先隐式提交**当前事务 —— 而整个迁移的全部保证都建在「它们在一个事务里」这句话上。
    """
    if plan.empty:
        return
    if _columns_of(conn, _V6_NEW_JOB_TABLE):
        raise StoreError(
            f"搬行用的那张表 {_V6_NEW_JOB_TABLE} 已经在这个库上了，而它不是这个库该有的"
            "东西。\n"
            "**整份拒绝，一行都没写。** 用它搬行会把一张来路不明的表的形状当成新形状，"
            "而那正是这一步要换掉的那一格。请先弄清楚它是谁留下的（这个名字只有这一步"
            "会用，而这一步在同一个事务里建它、改掉它、不再留下它）。"
        )
    conn.execute(_job_ddl_as(_V6_NEW_JOB_TABLE))
    columns = _columns_of(conn, _V6_NEW_JOB_TABLE)
    joined = ", ".join(columns)
    conn.execute(
        f"INSERT INTO {_V6_NEW_JOB_TABLE} ({joined})"  # noqa: S608
        f" SELECT {joined} FROM {_JOB_TABLE}"
    )
    conn.execute(f"DROP TABLE {_JOB_TABLE}")  # noqa: S608
    conn.execute(f"ALTER TABLE {_V6_NEW_JOB_TABLE} RENAME TO {_JOB_TABLE}")  # noqa: S608


def _job_rows_check(
    conn: sqlite3.Connection, *, expected: frozenset[str] | None = None
) -> tuple[dict[str, int], list[str]]:
    """``research_job`` 与**引用它的每一张表**各有多少行，以及哪里对不上。**只读。**

    两个调用方各自处置，而算式只有这一份：写入那一步在提交**之前**拿它当判据（对不上
    就整份拒绝，见 :func:`_require_job_rows_kept`），回读那一步在提交**之后**拿它当读数。
    各写一份的话，两份会在某次改了表名或加了引用者之后分家 —— 而分家的表现是「预检
    过了、回读也过了」，两句话各对着自己那一份查询说，其中一份问的已经不是这张表。

    ``expected`` 是计划里记下的那几个键。给了它时核**集合相等**（少了是丢行，多了是
    凭空多出来的行）—— 「重建过了」这句话在丢行的时候与一行没丢的时候长得一样，而
    这一格是唯一一个能在提交之前把它们分开的地方。
    """
    counts: dict[str, int] = {}
    problems: list[str] = []
    if not _columns_of(conn, _JOB_TABLE):
        problems.append(f"{_JOB_TABLE}：这一步之后这张表不在盘上")
        return counts, problems
    keys = {
        str(row[0])
        for row in conn.execute(
            f"SELECT {JOB_KEY_COLUMNS[_JOB_TABLE]} FROM {_JOB_TABLE}"  # noqa: S608
        ).fetchall()
    }
    counts[_JOB_TABLE] = len(keys)
    if expected is not None and keys != set(expected):
        missing = sorted(set(expected) - keys)
        added = sorted(keys - set(expected))
        problems.append(
            f"{_JOB_TABLE}：计划里是 {len(expected)} 行、盘上是 {len(keys)} 行"
            + (f"（少掉：{'、'.join(missing[:5])}{'…' if len(missing) > 5 else ''}）" if missing else "")
            + (f"（多出：{'、'.join(added[:5])}{'…' if len(added) > 5 else ''}）" if added else "")
        )
    for table, column in _foreign_keys_to(conn, _JOB_TABLE):
        values = [
            str(row[0])
            for row in conn.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
            ).fetchall()
        ]
        counts[table] = len(values)
        orphans = sorted(set(values) - keys)
        if orphans:
            problems.append(
                f"{table}.{column}：{len(orphans)} 个键在 {_JOB_TABLE} 里找不到父行（"
                + "、".join(orphans[:5])
                + ("…" if len(orphans) > 5 else "")
                + "）—— 引用它的那一行指向一张已经被换过的表，而这在盘上不报错"
            )
    return counts, problems


def _require_job_rows_kept(
    conn: sqlite3.Connection, plan: JobKindWidening, *, db_path: Path
) -> dict[str, int]:
    """提交**之前**的对账：每一行都还在，且引用它的每一行都还指得到。

    为什么必须有它，而不是靠回读：第五步是五步里唯一一处 **DROP 表**，而它需要的外键
    强制在事务之外被关掉了（见 ``migrate()``）—— 也就是说，搬漏一行时 **SQLite 不会拦**，
    事务照样提交，而下一个撞上它的地方是某一次按 ``job_id`` 取工作单元，那时它长得像
    「库坏了」。回读在提交**之后**，救不了已经提交的东西，所以这一格必须在事务里算完。
    """
    if plan.empty:
        return {}
    counts, problems = _job_rows_check(conn, expected=plan.keys)
    if not problems:
        return counts
    raise StoreError(
        f"{db_path} 的 {_JOB_TABLE} 重建之后对不上 —— 这一次搬到一半：\n"
        + "\n".join(f"  - {item}" for item in problems)
        + "\n\n"
        "**整份拒绝，一行都没写**（对账在提交之前，事务已经回滚）。\n"
        "这一步的外键强制在事务期间是关的，所以「搬漏了会被拦下来」这件事在这里不成立"
        "—— 它是这一步自己算的，而算出来不对时唯一诚实的处置就是一句都不写。"
        "库还是迁移前那个样子，备份在旁边（路径写在下面那条消息里）。"
    )


#: ``execution_ref`` 的形状：映射的左边、右边、时刻。v7 要求右边那一列叫
#: ``dispatch_task_id``（理由在 ``db.SCHEMA_VERSION`` 上面那段）。
#:
#: 这一步**不写旧名字**：它认的是「这张表除开那两列固定的，还剩哪一列」，而剩下的
#: 那一列就是映射的右边。一个把旧名字抄进来的迁移，会在下一次改名时留下第二个要
#: 同步的地方；而这一步真正的主张是「这张表只许有一个映射列，它叫这个名字」。
_EXECUTION_REF_TABLE = "execution_ref"
_EXECUTION_REF_FIXED_COLUMNS = frozenset({"work_item_id", "mapped_at"})
_EXECUTION_REF_COLUMN = "dispatch_task_id"


@dataclass(frozen=True)
class ExecutionRefRename:
    """v6 → v7 的**计划**：``execution_ref`` 那一列要不要改名。**一行都没动过。**

    ``present=False`` 是「这张表不在盘上」（``replay_ddl`` 会按这一版代码的形状建它），
    ``renamed=False`` 而 ``present=True`` 是「它在，而且列名已经是对的」。报告里两种都
    读作「没有改名」，但理由不同 —— 而理由正是运维要看的。
    """

    table: str = _EXECUTION_REF_TABLE
    present: bool = False
    renamed: bool = False
    #: 改名之前查到的那一列叫什么。**只在 ``renamed`` 与「已经是对的」两档里有值**：
    #: 一个没在盘上的表没有列名可报。
    found_column: str = ""
    #: 这张表有几行。改名不动取值，这个数是回读时的对账分母。
    rows: int = 0

    @property
    def empty(self) -> bool:
        return not self.renamed

    def describe(self) -> str:
        if not self.present:
            return (
                f"派发映射的列名（v6 → v7）：{self.table} 不在盘上"
                "（``replay_ddl`` 会按这一版代码的形状建它），没有可改的名。"
            )
        if not self.renamed:
            return (
                f"派发映射的列名（v6 → v7）：{self.table} 的映射列已经叫 "
                f"{self.found_column}，一行都没有改。"
            )
        return (
            f"派发映射的列名（v6 → v7）：{self.table} 的映射列 "
            f"{self.found_column} 改名为 {_EXECUTION_REF_COLUMN}"
            f"（{self.rows} 行的取值逐字没动）。"
        )


def _plan_execution_ref(conn: sqlite3.Connection) -> ExecutionRefRename:
    """算出 ``execution_ref`` 的映射列要不要改名。**一行都不写。**

    判据是**这张表有几列、分别叫什么** —— 不是「旧名字在不在」。两种答案都不是拒绝，
    第三种是：

    * 这张表不在盘上 —— ``replay_ddl`` 刚按这一版的形状建了它；
    * 映射列已经叫新名字 —— 没有可修的东西（**重跑走的就是这一条**）；
    * 这张表除开 ``work_item_id`` / ``mapped_at`` 之外有**恰好一列** —— 那一列就是
      映射的右边，改成新名字；
    * 零列或多于一个 —— 拒绝。零列说明这张表不是它该有的样子（没有映射列，改名会
      把它改成一张没有右边的表）；多于一个说明有人加过列，而「哪一列是映射」在这
      一步里没有答案。

    Raises:
        StoreError: 上面第四种。
    """
    columns = _columns_of(conn, _EXECUTION_REF_TABLE)
    if not columns:
        return ExecutionRefRename()
    if _EXECUTION_REF_COLUMN in columns:
        return ExecutionRefRename(
            present=True, found_column=_EXECUTION_REF_COLUMN
        )
    extra = [name for name in columns if name not in _EXECUTION_REF_FIXED_COLUMNS]
    if len(extra) != 1:
        raise StoreError(
            f"{_EXECUTION_REF_TABLE} 的列是 {sorted(columns)!r}，而这一步只认"
            f"「{sorted(_EXECUTION_REF_FIXED_COLUMNS)!r} 加恰好一列映射」。\n"
            "**整份拒绝，一行都没写。** 零列时这张表没有映射的右边；多于一列时"
            "「哪一列是映射」没有答案 —— 猜一个会把取值搬进错的地方。"
        )
    rows = int(
        conn.execute(f"SELECT COUNT(*) FROM {_EXECUTION_REF_TABLE}").fetchone()[0]  # noqa: S608
    )
    return ExecutionRefRename(present=True, renamed=True, found_column=extra[0], rows=rows)


def _apply_execution_ref(conn: sqlite3.Connection, plan: ExecutionRefRename) -> None:
    """按计划改名。**自己不开事务**（同 ``_apply_revisions``）。

    一条 ``ALTER TABLE … RENAME COLUMN``：SQLite 会就地改列名并保留每一行的取值，
    所以这一步既不像 v5 → v6 那样重建表，也不需要在事务之外关外键。
    """
    if plan.empty:
        return
    conn.execute(
        f'ALTER TABLE {plan.table} RENAME COLUMN "{plan.found_column}" '  # noqa: S608
        f"TO {_EXECUTION_REF_COLUMN}"
    )


# --- JSONL 存档那一侧 ---------------------------------------------------------


def _migrate_archive(
    archive_dirs: tuple[Path, ...],
    *,
    profile: str,
    proof: InputProof,
    stamp: str,
    write: bool,
) -> tuple[ArchiveFile, ...]:
    """``state/evidence`` 等目录下的 JSONL 行也带血统。

    ## 为什么存档也要迁

    证据**不只存在于库里**：``adapters/jsonl/store.py`` 是导出与审计通道（人类可以
    直接 grep 的那一份），而一个没有血统的存档在跨部署阅读时与库里的行有同一个问题
    —— 甚至更严重，因为它会被交给第三方（「不需要任何工具就能读」正是它存在的理由）。

    ## 为什么用**同一份**证明表

    两个存储里的行是同一件事的两种落盘（同一批 ``put_*`` 调用）。给存档另找一套
    证明来源，就会有两条判据，而它们在某一行上不一致时没有一处会红。

    ``write=False`` 时只算不写（迁移用它在动手之前就知道**每一处**都证得出来）；
    ``write=True`` 时逐文件备份 + 原子替换。已经带血统的行不会重复盖：它是一个
    **核对**（值必须等于本部署，否则拒绝）。

    ``proof`` 从来不是 ``None``，所以它的类型不是可选的：迁移在打开库**之前**就把证明
    表读好了（``migrate`` 里那次 ``load_input_proof``），库已经是 v2 的那次重跑也带着
    它 —— 那一次正是「库好了而存档还有没盖章的行」的用途。手上没有证明表时，那些行
    只能被拒绝，而拒绝是对的答案。
    """
    reports: list[ArchiveFile] = []
    for directory in archive_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            rows = _read_jsonl(path)
            changed = _plan_archive_file(
                path, rows, profile=profile, proof=proof
            )
            if not write or not changed:
                reports.append(ArchiveFile(path, len(rows), None, not changed))
                continue
            backup = _backup_file(path, stamp=stamp)
            _write_jsonl(path, rows)
            reports.append(ArchiveFile(path, len(rows), backup, False))
    return tuple(reports)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise StoreError(
                    f"存档损坏：{path} 第 {lineno} 行不是合法 JSON（{exc}）。"
                    "人工修复后再继续 —— 静默跳过会污染后续所有统计。"
                ) from exc
            if not isinstance(loaded, dict):
                raise StoreError(f"存档里的第 {lineno} 行不是一个对象：{path}")
            rows.append(loaded)
    return rows


def _plan_archive_file(
    path: Path, rows: list[dict], *, profile: str, proof: InputProof
) -> bool:
    """这个文件的每一行该不该补血统。返回「有没有改动」。**不写盘。**"""
    pending: list[str] = []
    changed = False
    for lineno, row in enumerate(rows, start=1):
        existing = row.get("workspace")
        if existing is not None:
            if existing != profile:
                raise StoreError(
                    f"{path} 第 {lineno} 行写着 workspace={existing!r}，而本部署是 "
                    f"{profile!r}。\n"
                    "存档是**同一个库的另一种落盘**，所以两者必须说同一件事。这一行的"
                    "归属没有第二种答案，也不在这里改写它。"
                )
            continue
        account_id = str(row.get("account_id") or "")
        source = proof.source_for(
            account_id=account_id, evidence_ids=_refs_for(row)
        )
        if source is None:
            pending.append(
                f"  - 第 {lineno} 行：account_id={account_id or '(缺这一格)'}"
                f"，引用={list(_refs_for(row)) or '（无）'}"
            )
            continue
        row["workspace"] = profile
        changed = True
    if pending:
        raise StoreError(
            f"{path} 里有 {len(pending)} 行**证不出血统**，整份拒绝（一个文件都没有改）：\n"
            + "\n".join(pending)
            + "\n\n"
            "存档与库是同一批写入的两种落盘，所以判据相同：证不出来就不盖章。"
            "能证明它们的输入放回 inputs_dir 之后再跑一次。"
        )
    return changed


def _backup_file(path: Path, *, stamp: str) -> Path:
    """存档文件的备份名带 ``lineage`` 而不是版本号。

    JSONL 存档**不带版本**（它的版本是库的），所以往名字里写一个版本号是往一份文件上
    写一句关于**别的东西**的声明 —— 而备份名是人出事时第一个读的字符串。
    """
    target = path.with_name(f"{path.name}.lineage-{stamp}.bak")
    if target.exists():
        raise StoreError(
            f"备份名已经被占用：{target}\n"
            "不覆盖既有备份是刻意的 —— 先把它移走或改名，再跑一次。"
        )
    target.write_bytes(path.read_bytes())
    return target


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """整文件重写：写临时文件 + ``os.replace``（同目录，同一文件系统）。

    逐行就地改会留下「改了一半」的文件，而那个状态与「这份存档本来就只有一半」在
    报告里长得一样。行序、空行与编码都与 ``adapters/jsonl/store.py`` 的写入一致
    （``ensure_ascii=False``、一行一条、每行以换行符收尾），否则一次迁移会让整个
    存档的 diff 全是空白。
    """
    temp = path.with_name(f"{path.name}.migrating")
    mode = path.stat().st_mode
    temp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.chmod(temp, mode)
    os.replace(temp, path)


# --- 命令行 -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m marketops.adapters.sqlite.migrate``。

    三个取值默认都来自**部署配置**（``load_settings``）—— 也就是那个可信来源本身。
    ``--profile`` 可以覆盖，但**不会**因此变成「随便填一个」：它盖上去的每一个值都
    还要过下面那串证明。

    三个都给全（并且 ``--no-archive``）时**一个都不读配置** —— 那条路是「把库拷到
    另一台机器上迁完带回来」，而那台机器上没有 ``MARKETOPS_DATA_ROOT``。
    """
    from ...config import load_settings

    parser = argparse.ArgumentParser(
        prog="python -m marketops.adapters.sqlite.migrate",
        description=(
            "把状态库（与它的 JSONL 存档）迁到 v5：补齐血统、换 aspect 词表、把三张"
            "配置表重建为 (id, 版)，最后把 contact_route 从事件流拆成不可变历史"
            "（route_observation）加当前投影。"
        ),
    )
    parser.add_argument("--db", type=Path, default=None, help="状态库路径（默认取部署配置）")
    parser.add_argument("--profile", default=None, help="工作区（默认取部署配置）")
    parser.add_argument("--inputs-dir", type=Path, default=None, help="本部署的输入目录")
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="只迁库，不迁 JSONL 存档（默认两边都迁）",
    )
    args = parser.parse_args(argv)

    # 三个取值都给全、并且不迁存档时**不读部署配置**。理由是一条实测出来的路：
    # 「把这个库拷到另一台机器上迁完带回来」是这条命令的正经用法之一，而那台机器上
    # 没有 ``MARKETOPS_DATA_ROOT``（配置必须显式注入，没有缺省）—— 读它会把一次可控
    # 的救援变成一句「环境没配好」。取值不齐时才回落到配置：那时配置就是那个可信来源
    # 本身，而它给出的每一个值都还要过同一串逐行证明。
    if (
        args.db is not None
        and args.profile is not None
        and args.inputs_dir is not None
        and args.no_archive
    ):
        db_path, profile, inputs_dir = args.db, args.profile, args.inputs_dir
        archive_dirs: tuple[Path, ...] = ()
    else:
        settings = load_settings()
        db_path = args.db or settings.database_path
        profile = args.profile or settings.profile
        inputs_dir = args.inputs_dir or settings.inputs_dir
        archive_dirs = ()
        if not args.no_archive:
            archive_dirs = (
                settings.evidence_dir,
                settings.routes_dir,
                settings.opportunities_dir,
            )
    try:
        report = migrate(
            db_path,
            profile=profile,
            inputs_dir=inputs_dir,
            archive_dirs=archive_dirs,
        )
    except MarketOpsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":  # pragma: no cover - 由命令行进入
    raise SystemExit(main())


__all__ = [
    "ArchiveFile",
    "BACKUP_STAMP_FORMAT",
    "MigrationReport",
    "RouteSplit",
    "main",
    "migrate",
]
