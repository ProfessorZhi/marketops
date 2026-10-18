"""SQLite 连接、PRAGMA 与**写事务**。

## 为什么这个模块只有三十行也要单独存在

因为「用 SQLite」本身不解决任何问题。§8 那一串要求（事务性准入、原子预算预留、
attempt/lease、陈旧 attempt 不得覆盖新版本）全部成立的前提是同一条：

    读到的值与据此写下的值，必须在**同一个写事务**里。

Python 里最容易写出来的形状恰好违反它 ——

```python
held = conn.execute("SELECT SUM(units) ...").fetchone()[0]   # 读
if held + units <= ceiling:                                   # 判
    conn.execute("INSERT INTO budget_reservation ...")        # 写
```

这三行读起来完全正确，在单线程测试里也永远正确。它错的地方是：两个进程可以
在第 2 行和第 3 行之间都读到同一个 ``held``，于是**两个都通过**，合计超支。
这个失败不产生任何异常、不留下任何日志，只在月末的对账里表现为「花超了」。

``BEGIN IMMEDIATE`` 就是在第 1 行之前拿写锁 —— 第二个事务在 ``BEGIN`` 处就
排队，读不到那个过期的值。它必须写在**事务开始**而不是第一次写之前
（``BEGIN DEFERRED`` 默认到第一句写才拿锁，那时读已经发生了）。

## PRAGMA 的选择都是刻意的

* ``journal_mode = WAL`` —— 读不阻塞写。没有它，一个长查询会把写锁按住在
  整个查询期间，而 worker 的超时预算很短。
* ``busy_timeout = 10000`` —— 并发写是**预期**行为（多 worker），不是异常。
  立刻抛 ``database is locked`` 会把「排队」误报成「失败」，而失败会退预算。
* ``synchronous = FULL`` —— 代价是每次提交一次 fsync。§8 要求「重启不丢工作」；
  在这个规模上，丢一次结算比慢一点贵得多。
* ``foreign_keys = ON`` —— SQLite 默认**关**。外键在默认配置下是不生效的
  声明，那种「以为有约束」比明说没有更危险。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ...domain.errors import EvidenceError, MarketOpsError, StoreError

#: 连接的**类型别名**，给同层的、自己不 import 厂商的模块用（``evidence_store`` /
#: ``ledger`` / ``jobs`` / ``pool`` / ``scenes`` 都是这一类）。
#:
#: 它们**故意不** ``import sqlite3``：厂商只在这一个文件里被点名，是
#: ``tests/test_adapters_hold_the_vendors.py`` 那条闭包判据守的东西，而那几个模块
#: 之所以被算进闭包而不是被判干净，靠的就是这件事。给一个连接参数写字面类型本来是
#: 无害的（``from __future__ import annotations`` 之下它甚至不求值），但它会把那个
#: 模块变成厂商的直接 import 方 —— 判据不会红，只是**悄悄少了一个见证链**，
#: 而那条判据用来证明自己是「闭包而非扫描」的正对照正是踩着这两个模块的。
#: 所以类型从这里取。
Connection = sqlite3.Connection

#: schema 版本。DDL 只增不改 —— 改列必须走一个新的迁移步骤，不能就地改，
#: 因为已经落盘的库不会重跑建表语句。
#:
#: ## v1 → v2 改的是**内容**，不是列
#:
#: v2 给 ``evidence`` / ``contact_route`` / ``route_opportunity`` 三张表的每一行补上
#: 血统（``payload`` 里的 ``workspace`` 一格）。三张表的**列**一个都没有动：payload
#: 是 JSON 文本，契约的演进与存储的演进是解耦的（见 ``evidence_store.py`` 的模块
#: docstring）。所以这一步没有新的 DDL 可重放 —— 它是一次**数据**迁移，而数据迁移
#: 不能由 ``IF NOT EXISTS`` 自愈，必须由人跑一次。
#:
#: 版本号仍然要进：一个 v1 的库被 v2 的代码打开时，那些行没有 ``workspace``，读出来
#: 会在 ``Evidence`` 的模型校验上炸成一句 pydantic 的 ``ValidationError`` —— 那是一个
#: 「看起来像库坏了」的失败，而事实是「这个库还没迁移」。这条版本判据把那句话换成
#: 一句说得清的拒绝（见 :func:`ensure_schema`）。
#:
#: ## v2 → v3 改的也**不是列**，是词表
#:
#: :class:`~marketops.domain.maintenance.Aspect` 从五个面变成七个（§11 逐字要求的那七
#: 个），于是 ``observation`` / ``change_signal`` / ``maintenance_claim`` 三张表里
#: ``aspect`` 那一格**存过的词**就有两种处置：``source_supply`` 与 ``responsibility``
#: 换名字，``asset_fact`` 与 ``role_email`` 拆成两个新面。
#:
#: DDL 一个字都没动 —— 但版本号照样要进，理由和 v1→v2 是同一条：**一个存着旧词的库被
#: 这一版代码打开时，失败长在模型校验上，而那句话看起来像「库坏了」**（
#: ``Aspect("asset_fact")``）。``IF NOT EXISTS`` 对**值**没有任何自愈能力，所以这一格
#: 必须由人跑一次，而版本判据是唯一能把那句话换成一句说得清的拒绝的地方。
#:
#: 拆分与改名各自的处置（删 / 复制 / 重算 ``dedupe_key``）写在 ``migrate.py`` 里，
#: 挨着它们的理由。
#:
#: ## v3 → v4 改的是**列**，这是头一次
#:
#: 三张配置表（``outreach_family`` / ``outreach_writing_policy`` / ``outreach_claim``）
#: 的主键从 ``(id)`` 变成 ``(id, revision)``，并且多一列 ``revision``。前两步改的都是
#: **值**（补血统、换词表），这一步动的是形状 —— 于是 ``IF NOT EXISTS`` 在这里帮不上
#: 忙：一个 v3 的库打开时那三张表已经存在，建表语句被整句跳过，而新的写入会撞在一句
#: ``ON CONFLICT clause does not match any PRIMARY KEY`` 上 —— 那句话看起来像代码坏了，
#: 事实是「这个库还没有迁移」。
#:
#: 为什么非改不可：§7 要求这三个对象**版本化**且**历史修订不可变**，§8 又要求草稿的
#: 可追溯性里写着 ``template_family/version`` 与 ``writing_policy/version``。一个 id
#: 底下只留一版（v3 的形状）做不到那两件事 —— 发第二版就是覆盖第一版，而一条已经生成
#: 过的草稿记的正是那个 id，于是它指向一份自己没见过的配置，盘上却一切正常。这与
#: ``outreach_draft`` 早就用 ``PRIMARY KEY (draft_id, draft_version)`` 是同一条判据。
#:
#: 三张表的**行**一行都不丢：v3 的每一行按 ``revision = 1`` 回填。那个默认值是唯一
#: 诚实的取值 —— v3 的形状里每个 id 最多一行，而那一行就是第一版。
#:
#: ## v4 → v5 是**拆表**，而且是把「事件流」拆成「历史 + 当前」
#:
#: 旧的 ``contact_route`` 是追加式的：一次研究插一行，读的人拿到历来写过的**全部**行。
#: 那意味着「这个账号现在的联系入口是什么」这个问题在库里**没有答案** —— 唯一的
#: 答案是让读的人按时间自己排一遍，而那件事没有任何执行器在保证，也没写在任何一个
#: 契约里。§6 逐字要求把这一层拆成 :class:`RouteObservation`（不变的历史）与
#: :class:`CanonicalContactRoute`（当前投影）。
#:
#: 于是这一步**动列**（第二条 ``IF NOT EXISTS`` 帮不上忙的形状，理由同 v3→v4）：
#:
#: * ``contact_route`` 的主键从 ``seq`` 换成 ``canonical_key``，多一列 ``workspace``；
#: * 新增 ``route_observation``（旧表那些行**全部**搬到这里，一行不丢）与
#:   ``route_rejection``（§6.3 要求持久化的那些拒绝）。
#:
#: 旧表的每一行都**是**一次观察 —— 那是它的本来面目，只是它当时没有名字。所以
#: 搬过去不是一次翻译，是一次认领。搬完之后投影从这些观察**折叠**出来（折叠规则在
#: ``domain/route_records.fold_observation``，迁移调用同一个函数，不重新实现一遍）。
#:
#: 折叠是**有损**的：同一个规范键下有 N 行观察时，投影只有一行。这不是数据丢失 ——
#: 丢失的是「N 行都还在投影里」这个**错误的读法**，而 N 份证据一份不少地留在
#: ``route_observation`` 里。这一点在迁移的业务键对账里必须对得上：观察行数守恒，
#: 投影行数 ≤ 观察行数且每个键恰好一行。
#:
#: ## v5 → v6 只改一处**约束**，而约束是「改形状」里最难的那一种
#:
#: ``research_job.input_kind`` 上的 ``CHECK`` 是 :class:`JobInputKind` 那份词表的落库
#: 形态，而 v5 写下的那一份只列了三个成员。第四个（``cohort``，按冻结的那一份队列
#: 提交研究）加进枚举时**这个词没有跟着进库**，于是 ``admit`` 的那句 INSERT 撞在
#: CHECK 上 —— 报出来的是一句 ``sqlite3.IntegrityError: CHECK constraint failed``，
#: 读起来像存储坏了，而事实是「这张表不认识这一版代码的枚举」。这不是少一个可选值：
#: 按 cohort 提交研究在这张表上的**每一次**尝试都会失败。
#:
#: 为什么它是一条真迁移而不是一次重放：``IF NOT EXISTS`` 对已存在的表整句跳过，所以
#: 一个 v5 的库打开之后，盘上那张表的 CHECK 还是旧的，而代码已经说自己是 v6 ——
#: 「同一版代码、两个形状」正是版本号存在的唯一理由。而 ``ALTER TABLE`` 没有改约束
#: 这一项，只能「按新形状建一张 → 搬行 → 丢旧的 → 改名」（形状在 ``JOB_TABLE_DDL``）。
#:
#: 这一步还多一条别的步骤没有的规矩：``work_item.job_id`` 引用着这张表，而 SQLite 的
#: 外键强制**必须在事务开始之前**关掉（``PRAGMA foreign_keys`` 在事务里是空操作），
#: 所以它不再与另外四步共用同一个「事务里什么都不用管」的环境。代价由两步补回来：
#: 提交之前逐行对账（每一行都还在），提交之后回读再核一遍。见 ``migrate.py`` 那一步。
#:
#: ## 迁移**不在这里**，也刻意不自动跑
#:
#: 补什么值是一个需要证据的决定（``application/lineage.py``），方向与旧库去留是人的
#: 决定。命令是 ``python -m marketops.adapters.sqlite.migrate``，它先备份再改，只给
#: **证得出来**的行盖章，且只改**认得出来**的词。
SCHEMA_VERSION = 6

#: ``schema_meta`` 里记「这个库属于哪个工作区」的键。
#:
#: 它是**部署绑定**，不是一行的血统：一个状态库同时装着多个部署的产出时，「按
#: account_id 取到的这一行是谁的」就没有答案。绑定的取值只有两个来源 —— 迁移从
#: ``Settings.profile`` 写进去（历史行），或这个库的第一行写入从**对象自己的
#: ``workspace``** 写进去（今天的写路径）。两者都拒绝猜。
WORKSPACE_META_KEY = "workspace"

_BUSY_TIMEOUT_MS = 10_000

_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 每个 (scope, line) 一行。上限是**服务端**事实，不是调用方参数：
-- §8「the caller cannot silently expand them」。source 记录它是谁定的。
CREATE TABLE IF NOT EXISTS budget_ceiling (
    scope  TEXT    NOT NULL,
    line   TEXT    NOT NULL,
    units  INTEGER NOT NULL CHECK (units >= 0),
    source TEXT    NOT NULL,
    PRIMARY KEY (scope, line)
);

-- 一条预留。reservation_id 是内容寻址的（scope+line+幂等键），所以幂等
-- 由主键本身保证，而不是靠「写入前先查一下」。
CREATE TABLE IF NOT EXISTS budget_reservation (
    reservation_id  TEXT    PRIMARY KEY,
    scope           TEXT    NOT NULL,
    line            TEXT    NOT NULL,
    units           INTEGER NOT NULL CHECK (units > 0),
    state           TEXT    NOT NULL CHECK (state IN ('reserved', 'spent', 'refunded')),
    idempotency_key TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    settled_at      TEXT,
    note            TEXT    NOT NULL DEFAULT '',
    UNIQUE (scope, line, idempotency_key)
);

CREATE INDEX IF NOT EXISTS budget_reservation_open
    ON budget_reservation (scope, line, state);

-- 研究产出。payload 是模型的 model_dump(mode='json')，列出来的只有需要被
-- 查询/排序的那几个字段 —— 把契约字段摊平成列，等于把契约钉死在这个存储上，
-- 而契约是 domain 的事（它有自己的 JSON Schema，见 marketops/schema.py）。
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    account_id  TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    stored_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_by_account ON evidence (account_id, stored_at);

-- 场地资产池（§4）。每一行是**一块场地**，不是一条证据，也不是一个账号：
-- 一个 Council 的两百块场地在这里是两百行，而在账号那边是一行。
--
-- `venue_asset_id` 是本仓的句柄（新行按它覆盖），`source_identity`
-- （`osm:node/123` / `site:<主机>`）是**上游认这一行的方式**，只列成一个可供查询
-- 的列 —— 它认的是场地，不是账号。拿它当账号键就是把 SOP L72 原样缝回去。
CREATE TABLE IF NOT EXISTS venue_asset (
    venue_asset_id  TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    payload         TEXT NOT NULL,
    stored_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS venue_asset_by_source ON venue_asset (source_identity);

-- 责任关系表（§5）。每一行是**一条判定**：「谁对哪块场地负什么责」。
--
-- 主键是四段 (workspace, account_id, venue_asset_id, relation)：同一块场地与同一个
-- 账号在两个 Scene 下可以判出不同的关系（一个 Scene 在找运营方、另一个在找采购方），
-- 所以那是两行。四个键段单独成列而不是只留在 payload 里 —— 主键要求它们在列上，
-- 这是它们与 venue_asset 的 source_identity 唯一不同的地方（后者只是可查询的一列，
-- 因为两行可以有同一个来源编号）。
CREATE TABLE IF NOT EXISTS account_venue_relation (
    workspace       TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    venue_asset_id  TEXT NOT NULL,
    relation        TEXT NOT NULL,
    payload         TEXT NOT NULL,
    stored_at       TEXT NOT NULL,
    PRIMARY KEY (workspace, account_id, venue_asset_id, relation)
);
-- 按账号读（「这个账号管哪些场地」）。§5 的读方向是资产 → 账号，但这一张表天然要
-- 两个方向读，而 §6 的账号池读路径正是按账号那一个。
CREATE INDEX IF NOT EXISTS account_venue_relation_by_account
    ON account_venue_relation (account_id, venue_asset_id);

-- 账号池（§6）。每一行是**这个市场现在有的一个账号**，不是一次研究作业的输入行
-- （那是 account_record），也不是发现期的一条候选（那是 account_candidate）。
--
-- 主键是 `account_id`，而 `workspace` 是普通的一列 —— 与 venue_asset 同形，与
-- account_venue_relation 不同（那张表的四段都是主键的一段）。两者的差别不是风格：
-- 关系表的键是「谁 × 哪块场地 × 什么关系」，四段合起来才是那一行的身份；账号的身份
-- 就是一个 id，工作区是**血统**而不是键的一段（同一个账号在两个 Scene 下是两行，但
-- 那是两个库各一行 —— 一个库绑一个工作区）。
--
-- **这一张表上只有主键这一个索引，而这是 §6 那一条的落点**：`domains` / `aliases` /
-- `addresses` 一律不是查询路径，因为「按域名找账号」正是 §6 明令不许做的事
-- （不得因为同域名 / 同名 / 近似地址合并两个实体）。加一个按域名的索引就等于把那条路
-- 铺好，而它的使用者只可能是下一个人顺手写的一行「这两个域名一样，应该是同一个吧」。
-- 域名那一格照样在 payload 里（它回答「官网在哪」），只是没有一条通往它的查询路径。
CREATE TABLE IF NOT EXISTS market_account (
    account_id TEXT PRIMARY KEY,
    workspace  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
);

-- §B12 的冻结成员名单（`domain/cohort.py::AccountCohort`）。主键是**内容寻址**的
-- cohort_id，所以「同一个池子按同一条规则读两次」是一次覆盖写而不是两行 ——
-- 这是 §B21「重放事件不重复昂贵工作」在主体那一侧的前提。
--
-- 列里那三格是**查询得到的那几格**，与 market_account 同一条分法：`workspace` 是
-- 血统（`db.owned` 要拿它跟库绑定比），`created_at` 是排序键（`list_cohorts` 的顺序
-- 就是「哪一份是当前的」那条判据的定义处），其余全在 `payload` 里。
--
-- 只有主键这一个索引，理由与 market_account 那一条**不同**：这里不是「不许按某某找」
-- （名单没有那种禁令），而是**没有第二条访问路径**。「取全部再按 (created_at,
-- cohort_id) 排」在几十行上是线性的，为一个今天只有几十行的表预铺索引，换来的是
-- 「按场景/按时间找名单」看起来像这一层的语义 —— 而它是 MCP 那一侧的一条过滤。
CREATE TABLE IF NOT EXISTS account_cohort (
    cohort_id  TEXT PRIMARY KEY,
    workspace  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
);

-- §12 的 Review Inbox：**两件人工对象**（`domain/review.py`）。分两张表不是范式洁癖，
-- 而是「当前状态」与「历史」在这件事上处置相反：
--
--   * `review_case` 是**当前**投影，一行会被覆盖 —— 每一次状态迁移都写回同一行的
--     `payload`，而 `version` 那一格守着它（乐观锁，见 `ports/review.py`）。
--   * `human_resolution` **只增不改**，一行都不许动。§12 逐字要求「保留历史和恢复
--     进度」，而「这件待办等了多少天」「谁提议的、谁批的」只能从这一串回答。
--
-- 合成一张表会让 `version` 那一格同时承担两件事（当前状态、写入序号），而
-- 「重放一条旧的决定」在那之后就无法与「一条新的决定」区分。
--
-- 列里的分法与 account_cohort 同一条：那几格是**查询得到的那几格**，其余全在
-- `payload` 里。这里能查的比它多，是因为 `list_cases` 的顺序（未决的在前、早的在前）
-- 与 `open_case` 的「它是不是已裁决过」都要在**不解析 payload** 的情况下答得出来。
--
-- `proposed_version` 单列一格而不是留在 payload 里：它是「有一版规则在等批准」的
-- 唯一机器可读的证据（`application/review.py` 与 `control_scene(activate)` 都读它），
-- 而一个需要先把几十行 payload 解成 JSON 才答得出来的判据，在调用点上会被写成
-- 「反正都是同一批，就都算等批准吧」。
--
-- **没有外键指向 scene**：`scene_id` 与 `scene` 表的主键同名，但一件待办可以在那个
-- Scene 归档之后仍然说得通（它记的是「当时按那一版规则判的」）。加一条 `REFERENCES`
-- 会让「归档一个 Scene」变成「删掉它的待办历史」，而那张表是只增不改的。
CREATE TABLE IF NOT EXISTS review_case (
    case_id          TEXT PRIMARY KEY,
    workspace        TEXT NOT NULL,
    scene_id         TEXT NOT NULL,
    reason           TEXT NOT NULL,
    subject_ref      TEXT NOT NULL,
    state            TEXT NOT NULL,
    proposed_version INTEGER,
    version          INTEGER NOT NULL,
    opened_at        TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    payload          TEXT NOT NULL,
    stored_at        TEXT NOT NULL
);

-- 排序键是 `(state, opened_at, case_id)`：`list_cases` 要「未决的在前、早的在前」，
-- 而末一段 `case_id` 不是装饰 —— 同一毫秒开出的两件待办若只按时刻排，两次调用的
-- 顺序可以不同，而报告的第一行必须是同一件。
CREATE INDEX IF NOT EXISTS review_case_by_state
    ON review_case (workspace, state, opened_at, case_id);

-- `REFERENCES review_case (case_id)` —— 与上面那条「不加外键」**不矛盾**：
-- 那一条说的是 `scene_id`（一个可以在别处独立存在的对象），这一条说的是这条决定
-- **属于哪一件待办**。一条没有案件的裁决是一行读不回来的历史：`inbox_flow` 按 case_id
-- 分组，落不进任何一组的那一行既不进 opened 也不进 resolved，而它会**静默消失**
-- （不是报错）—— 恒等式照样成立，只是少了一件本该记下的事。
--
-- `outcome` 单列一格：`list_resolutions` 的排序与「它让案件到了哪个状态」都要读它，
-- 而它是闭集（`domain/review.py::ResolutionOutcome`），值得被数据库认得。
CREATE TABLE IF NOT EXISTS human_resolution (
    resolution_id TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL REFERENCES review_case (case_id),
    workspace     TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    decided_by    TEXT NOT NULL,
    answered_at   TEXT NOT NULL,
    payload       TEXT NOT NULL,
    recorded_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS human_resolution_by_case
    ON human_resolution (case_id, answered_at, resolution_id);

-- 路线那三张表（route_observation / contact_route / route_rejection）与
-- route_opportunity **不在这里**：它们的建表语句在下面的 ``ROUTE_TABLE_DDL`` 里，
-- 由 ``replay_ddl`` 一并重放。分开的理由与 ``CONFIG_TABLE_DDL`` 同一个 ——
-- v4 → v5 必须重建 ``contact_route``（主键从 ``seq`` 换成 ``canonical_key``，
-- 而 SQLite 改不了主键），迁移那边也要一份形状；两边各写一遍的话，分家的表现是
-- 「迁移建出一张没有主键的表」，那是一句**不会报错**的错误。
--
-- ``research_job`` 也是这一族（v5 → v6 要重建它 —— ``ALTER TABLE`` 改不了 CHECK
-- 约束，而 ``input_kind`` 那一格要认得 ``cohort``），所以它的建表语句在下面的
-- ``JOB_TABLE_DDL`` 里，同样由 ``replay_ddl`` 重放。理由与上面逐字相同。

-- 一次被受理的研究请求。血缘与四个动词见 domain/job.py 与 ports/jobs.py。
--
-- job_id 是内容寻址的（scene + 幂等键），所以「同一个键不会受理两次」由主键
-- 本身保证。UNIQUE 那一列是**读了才有意义**的：request_sha256 要在同一个写
-- 事务里被读出来比对，才能把「重发」与「同一个键改了内容」分开。
--
-- **建表语句不在这里** —— 见 ``JOB_TABLE_DDL``。

-- 可执行单元。stop_reason 为 NULL 表示还没停 —— 它是**唯一**的停止判据，
-- 没有并存的「进行中」状态列（见 domain/job.py::WorkItem 的 docstring）。
CREATE TABLE IF NOT EXISTS work_item (
    work_item_id TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES research_job (job_id),
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    subject_ref  TEXT NOT NULL,
    stop_reason  TEXT CHECK (stop_reason IS NULL OR stop_reason IN
                     ('goal_met', 'budget_exhausted', 'blocked',
                      'ambiguous_hold', 'no_useful_next_action', 'manual_pause')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (job_id, ordinal)
);

-- WorkItem → Quay 任务的映射。**主键就是 work_item_id**：写一次是结构性的，
-- 不是一条「写之前先查一下」的约定。两个派发者抢同一个 work item 时，第二个
-- 撞在主键上，而不是悄悄建出第二个 Quay 任务。
CREATE TABLE IF NOT EXISTS execution_ref (
    work_item_id TEXT PRIMARY KEY REFERENCES work_item (work_item_id),
    quay_task_id TEXT NOT NULL,
    mapped_at    TEXT NOT NULL
);

-- WorkItem → 它编译时钉住的 Scene 版本（§6：Running tasks retain their pinned
-- guidance）。与 work_item 是**两张表**而不是一张表上的两列：加列要改一个已有
-- 表的形状，那是「新建一列 + 回填 + 保留旧列」的一次真正迁移，方向和旧库去留
-- 该由人决定；加表则由 ensure_schema 重放 DDL 自愈。
--
-- 这张表**可以没有对应的行**，而且那不是「数据坏了」：这个机制出现之前编译的
-- work item 在盘上就是没有钉，而 `ensure_schema` 只负责把表建出来、不负责给
-- 历史行补一个版本（补出来的那个是编的）。缺席要由读的那一侧面对 ——
-- `get_assignment` 拒绝、`compile_work_items` 拒绝重新编译那一批。
--
-- 写它的入口只有一个：compile_work_items 的写事务。没有「事后补一个版本上去」
-- 的入口，那正是让一批工作单元看起来像是**这一次**编译出来的成因。
--
-- **2026-09-23 起这张表是遗留的：没有写者，也没有读者。** 它被下面的
-- work_item_snapshot 取代（那一张连身份一起装），已有的行由 DDL 尾部那条
-- INSERT ... SELECT 原样搬过去。表本身不 drop —— 删数据是破坏性的，而且不是
-- 这个模块该替人做的决定。
CREATE TABLE IF NOT EXISTS work_item_guidance (
    work_item_id  TEXT PRIMARY KEY REFERENCES work_item (work_item_id),
    scene_version TEXT NOT NULL,
    pinned_at     TEXT NOT NULL
);

-- WorkItem → 它编译时冻住的**整个任务包**：针对谁（subject_json）+ 按哪一份
-- Scene 规则（scene_version + pinned_at）。
--
-- 取代 work_item_guidance 而不是与它并存：那两半是同一个问题「这一次派发到底派了
-- 什么」的两个答案，分成两条记录就有两个真相来源，而它们不一致时没有一处会红。
--
-- subject_json 可以 NULL，而那不是「数据坏了」：身份快照这个机制出现之前编译的
-- work item 在盘上只有规则的版本。补一个身份出来是编的（`ensure_schema` 不干
-- 这件事，下面那条 INSERT 写的也是 NULL），让整张表读不出来则会让一次与此无关的
-- 读操作不可用。缺席由读的那一侧面对 —— `get_assignment` 拒绝，理由说得出来。
--
-- 写它的入口只有一个：compile_work_items 的写事务。
CREATE TABLE IF NOT EXISTS work_item_snapshot (
    work_item_id  TEXT PRIMARY KEY REFERENCES work_item (work_item_id),
    scene_version TEXT NOT NULL,
    pinned_at     TEXT NOT NULL,
    subject_json  TEXT
);

-- 把遗留表已有的钉原样搬进来。**原样**：只搬三个真值，subject 写 NULL ——
-- 那些工作确实按某一版规则跑过（版本是真的），身份却没有记录过。
-- 不搬的话 `repin_refusal` 会把它们读成「没有钉」，而那对它们是一句假话。
INSERT OR IGNORE INTO work_item_snapshot (work_item_id, scene_version, pinned_at, subject_json)
    SELECT work_item_id, scene_version, pinned_at, NULL FROM work_item_guidance;

-- 一个 Scene 版本**指称的那批字节**（见 domain/profile.py 的 SceneFiles）。
--
-- 内容寻址：主键的第一段就是版本，而版本是这批字节的摘要 —— 所以「同号不同内容」
-- 在结构上不存在，重写同一个版本是幂等的。
--
-- 一行一文件而不是把整个目录塞进一个 JSON：路径是这批字节的一部分，把它编码进
-- 一个 blob 会让「这一份里有没有 skill/SKILL.md」变成一个要解析才答得出的问题。
--
-- **不是外键，也不可能是**：work_item_snapshot 里那些搬过来的历史行有版本、没有
-- 内容（内容这一半是后来才留的）。缺席要读得出缺席，而不是让那几行插不进去。
--
-- 写它的入口只有一个：compile_work_items 的写事务 —— 与版本号写在同一个事务里，
-- 于是「版本在、内容不在」这个窗口不存在。
CREATE TABLE IF NOT EXISTS scene_snapshot (
    scene_version TEXT NOT NULL,
    path          TEXT NOT NULL,
    content       BLOB NOT NULL,
    PRIMARY KEY (scene_version, path)
);

-- 一条租约。attempt_id 是内容寻址的（work_item + 序号），所以重发同一个
-- (work_item, holder) 拿回同一条。
--
-- 这张表上**没有** expires_at，而且不是「还没加」：租约不按时钟自动失效，
-- 因为猜错「那个 worker 死了」的代价是两个 worker 跑同一件昂贵的事。收回租约
-- 只有一条路 —— release(reason=...)，理由必填。
--
-- 「一个 work item 同时最多一条活租约」这条排他性由**应用代码在写事务里**保证
-- （写之前查有没有 state='leased' 的行）。SQLite 的部分唯一索引
-- （WHERE state='leased'）也能表达它，但那样约束就只存在于这一个实现里，而
-- 内存那份没有等价物 —— 契约测试会在两个实现上各跑一遍，而其中一个会静默地
-- 不守这条。判据放在两个实现都要满足的地方。
CREATE TABLE IF NOT EXISTS job_attempt (
    attempt_id   TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_item (work_item_id),
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    holder       TEXT NOT NULL,
    state        TEXT NOT NULL CHECK (state IN ('leased', 'settled', 'released')),
    leased_at    TEXT NOT NULL,
    settled_at   TEXT,
    note         TEXT NOT NULL DEFAULT '',
    UNIQUE (work_item_id, ordinal)
);
CREATE INDEX IF NOT EXISTS job_attempt_by_work_item
    ON job_attempt (work_item_id, ordinal);
CREATE INDEX IF NOT EXISTS job_attempt_live
    ON job_attempt (work_item_id, state);

-- 一次结算的产出（§3 submit_findings）。主键是 work_item_id，理由与
-- ports/jobs.py 的 settlement 单数一致：一个 work item 最多一条结算
-- （settle 只接受活租约，lease 不给停下的 work item 发新租约）。
--
-- 用主键表达「最多一条」而不是「今天恰好一条」：后者要靠读的那一侧去挑，
-- 而「多条里哪一条算数」没有诚实的答案。
--
-- payload 是规范 JSON（domain/findings.py::findings_payload）。这里**只存
-- payload**，不另存散列、也不另存 stop_reason：两者都能从 payload 与
-- work_item 现算，而多存一列就是同一件事的第二个真相来源 —— 它们不一致时
-- 没有任何一处会红，报告照常打印。
CREATE TABLE IF NOT EXISTS job_settlement (
    work_item_id TEXT PRIMARY KEY REFERENCES work_item (work_item_id),
    attempt_id   TEXT NOT NULL REFERENCES job_attempt (attempt_id),
    payload      TEXT NOT NULL,
    recorded_at  TEXT NOT NULL
);

-- 一条**判定**的记录（§2 / §4 / §10）：问过一次后端，问的是什么、它答了什么、
-- 花了多久、用了多少。
--
-- work_item_id 是**真外键且必填**。这不是洁癖：一条无法归属的判定记录正是
-- 「通用模型记忆库」的第一块砖 —— 它没有主语，于是谁都可以往里写、谁都可以
-- 从里读。归属由库自己拒（外键），不由「写之前先查一下」的自觉拒 —— 后者在
-- 并发下两个写者都会通过，而且它对任何写者都只是自觉。
--
-- UNIQUE (work_item_id, provider, case_id) 是**幂等键**，而它必须与 record_id
-- 分开：record_id 是内容寻址的，配方里含 decided_at，所以同一次调用的重放算出来
-- 的是另一个 id —— 光靠主键挡不住重复付费。case_id 由输入本身派生（题面版本 +
-- 题目清单 + 文本 + 引用），不含时钟，所以「同一个问题问第二次」认得出来。
--
-- provider 那一段是承重的。同一件工作、同一份输入被**两个后端**各判一次，是
-- **两次**付费，不是一次重放；而 case_id 里没有 provider（它由题面 + 输入派生），
-- 所以少了这一段，「拿 Jev 的结论和 Laya 比一次」会被读成重复付费而整次拒掉。
-- 一份记录只能有一个 provider，把两个后端的话合并进一行就是给同一个事实两个
-- 来源 —— 那比重复付费更坏。
--
-- 那一列**刻意不叫** idempotency_key：账本那张表里已经有同名的列，而它按
-- (scope, line) 去重；这里的去重键是 (work_item, provider, case)。两个不同的键
-- 共用一个名字，读的人会以为它们是一回事。
--
-- payload 是**回执的投影本身**（adapters/mcp_server/decisions.py::_receipt），
-- 不是 DecisionRecord 的 model_dump。理由：回执是调用方真看到的那份形状，而
-- 存另一份会让「存下来的」与「看到的」成为两个形状 —— 它们漂移的那天，按
-- record_id 读回来的记录与当时那一份对不上，而两边都没有错。
--
-- case_id / provider / subject_ref 摊成列，因为要按它们查询/去重（与 evidence
-- 那张表同一条取舍：列出来的只有需要被查询的那几个字段）。三者都**从 payload 里
-- 取**写入，所以不存在「列与 payload 不一致」的写入路径。
CREATE TABLE IF NOT EXISTS decision_record (
    record_id    TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_item (work_item_id),
    subject_ref  TEXT NOT NULL,
    provider     TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    UNIQUE (work_item_id, provider, case_id)
);
CREATE INDEX IF NOT EXISTS decision_record_by_subject
    ON decision_record (subject_ref, recorded_at);

-- ---------------------------------------------------------------- Scene

-- 一个 Scene 当前是什么状态。**不在这里存版本内容** —— 版本是 append-only 的，
-- 与这条会变的记录放在同一行，就得让每次 pause 都重写一遍全部规则。
CREATE TABLE IF NOT EXISTS scene (
    scene_id          TEXT PRIMARY KEY,
    workspace         TEXT NOT NULL,
    state             TEXT NOT NULL,
    latest_version    INTEGER NOT NULL,
    effective_version INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    pause_reason      TEXT,
    -- revision 是乐观锁的序号，不是历史。它是 put_record 的 CAS 全部依据：
    -- 比 state 多覆盖 approvals 的增长，比整行比较少一次逐字段比对。
    revision          INTEGER NOT NULL,
    payload           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scene_by_workspace ON scene (workspace, scene_id);

-- 一版规则。**只增不改**：同一个 (scene_id, version) 写第二次，内容相同即幂等，
-- 内容不同报错 —— 那意味着有人手工改过 id。这与 evidence 表同一条纪律。
CREATE TABLE IF NOT EXISTS scene_version (
    scene_id   TEXT NOT NULL,
    version    INTEGER NOT NULL,
    workspace  TEXT NOT NULL,
    frozen_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (scene_id, version)
);
CREATE INDEX IF NOT EXISTS scene_version_by_workspace
    ON scene_version (workspace, scene_id, version);

-- ------------------------------------------------------- 增量维护

-- 一次观测。**不去重同一格的不同时刻** —— 它们是「它当时是什么样」的不同事实，
-- 合并等于让「变了」这件事从记录里消失。同一 (主体, aspect, 时刻, 来源) 幂等。
--
-- 它**不带 scene_id**：原始事实在一个工作区内可跨场景复用，而那条边界是
-- workspace。带 scene_id 会让同一份事实在每个场景里各存一遍，而少存一次
-- 看起来只是「这个场景还没看过」。
CREATE TABLE IF NOT EXISTS observation (
    workspace     TEXT NOT NULL,
    subject_kind  TEXT NOT NULL,
    subject_id    TEXT NOT NULL,
    aspect        TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    PRIMARY KEY (workspace, subject_kind, subject_id, aspect, observed_at, source_ref)
);
CREATE INDEX IF NOT EXISTS observation_by_workspace
    ON observation (workspace, subject_kind, subject_id, aspect, observed_at);

CREATE TABLE IF NOT EXISTS change_signal (
    workspace    TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    aspect       TEXT NOT NULL,
    signalled_at TEXT NOT NULL,
    reason       TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (workspace, subject_kind, subject_id, aspect, signalled_at, reason)
);

-- 复核回执 = 租约。dedupe_key 是主键，于是「重启 / 重复 tick 不得重复计费」
-- 不是一条纪律，而是主键约束本身：第二个人写不进同一条。
--
-- 身份是 (dedupe_key, attempt)：去重身份完全由 dedupe_key 承载（同格同周期），
-- 尝试次数由行数承载。就地覆盖写（失败就改这一行）会让「这一格失败过几次」
-- 永远回答不了 —— 那一列永远是 1。
CREATE TABLE IF NOT EXISTS maintenance_claim (
    dedupe_key   TEXT NOT NULL,
    attempt      INTEGER NOT NULL,
    workspace    TEXT NOT NULL,
    scene_id     TEXT NOT NULL,
    scene_version INTEGER NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    aspect       TEXT NOT NULL,
    reason       TEXT NOT NULL,
    period       TEXT NOT NULL,
    state        TEXT NOT NULL,
    claimed_at   TEXT NOT NULL,
    finished_at  TEXT,
    result_ref   TEXT NOT NULL,
    failure      TEXT NOT NULL,
    PRIMARY KEY (dedupe_key, attempt)
);
CREATE INDEX IF NOT EXISTS maintenance_claim_by_scene
    ON maintenance_claim (scene_id, claimed_at, subject_kind, aspect);
"""

#: 续跑意图（§10 的 continuation/outbox）与它的唤醒归属。
#:
#: **加表不改 ``SCHEMA_VERSION``**：这份 DDL 每次 ``ensure_schema`` 都整份重放，
#: ``IF NOT EXISTS`` 让「盘上还没有这两张表」自愈。版本号只在**已有表改形状**时才动
#: —— 那才是重放救不了、需要一次真的迁移的事。与 ``outreach_*`` 那一段分开的理由
#: 同上：这几张表今天还没有一个跑着的构建写过。
_CONTINUATION_DDL = """
-- 一次业务事件**欠下**的下一步。身份 = 内容寻址 id，于是「同一个事件欠两遍」
-- 不是一条要靠人遵守的纪律，而是主键约束本身。
--
-- 它**不带 workspace**：``continuation_id`` 由 (scene_id, kind, business_key) 算出，
-- 而 scene_id 是 ``scene`` 表的主键（一个场景只属于一个工作区）。再存一列 workspace
-- 就是同一个事实的第二个产地，而它漂移的形态是「一行属于两个工作区」或
-- 「一个工作区看不见自己欠的东西」—— 两者都不报错。
--
-- state/due_at 摊成列：它们出现在每一次 WHERE 与 ORDER BY 里（控制器每一轮问的是
-- 「到期了还没做的」，顺序按 due_at）。其余（why / checkpoint / reason / subject）
-- 只被原样读回，留在 payload 之外按列存是因为它们各自有一处要按它读。
CREATE TABLE IF NOT EXISTS continuation (
    continuation_id TEXT PRIMARY KEY,
    scene_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    business_key    TEXT NOT NULL,
    subject_kind    TEXT NOT NULL,
    subject_ref     TEXT NOT NULL,
    why             TEXT NOT NULL,
    state           TEXT NOT NULL,
    due_at          TEXT NOT NULL,
    epoch           INTEGER NOT NULL,
    holder          TEXT NOT NULL,
    checkpoint      TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    blocked_on      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
-- 控制器每一轮的全部读法都走这条索引：按场景、按状态、按到期时刻。
-- 尾部的 continuation_id 不是为了好看 —— 同刻的一批行必须有**稳定**的顺序，
-- 否则两次 tick 在上限截断之下会做出不同的选择，而报告上只显示「这轮做了别的几件」。
CREATE INDEX IF NOT EXISTS continuation_by_scene
    ON continuation (scene_id, state, due_at, continuation_id);

-- 这个工作区现在由谁唤醒。**每个工作区一行**（见 ports/continuation.py）。
--
-- 让出走 DELETE，不把 heartbeat_at 改旧：下一任看到一行「没人」就从 epoch=1 开始，
-- 而看到一行「过期」会以为自己在顶掉一个崩溃的进程 —— 一次干净的交接里根本没有上一代。
CREATE TABLE IF NOT EXISTS continuation_owner (
    workspace    TEXT PRIMARY KEY,
    holder       TEXT NOT NULL,
    epoch        INTEGER NOT NULL,
    acquired_at  TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
"""

#: 访问尝试账（``ports/fetch_attempt.py``）的落点。
#:
#: **加表不改 ``SCHEMA_VERSION``**：与 ``_CONTINUATION_DDL`` 逐字同一条理由 —— 这份
#: DDL 每次 ``ensure_schema`` 都整份重放，``IF NOT EXISTS`` 让「盘上还没有这张表」
#: 自愈。版本号只在**已有表改形状**时才动。
#:
#: ## 为什么只有五列，其余全在 payload 里
#:
#: ``FetchAttempt`` 有二十来格，而摊平成列等于把**契约**钉死在这个存储上：契约是
#: domain 的事（它有自己的 JSON Schema，见 ``contracts/fetch-attempt.schema.json``），
#: 加一格就变成一次迁移。与 ``evidence`` 那张表同一条分工，代价也一样写在明处
#: （按 payload 里的字段筛要 ``json_extract``、没有索引）。
#:
#: 留下来的四列（除主键外）恰好是**出现在每一次 ``WHERE`` 或 ``ORDER BY`` 里**的那
#: 几个：``ports/fetch_attempt.py::list_attempts`` 只按 ``scope`` / ``url`` /
#: ``started_at`` 筛，且永远按 ``(started_at, attempt_id)`` 倒序。``method`` 与
#: ``observed_status`` 今天没有任何一处按它们筛，所以它们在 payload 里，不在这里 ——
#: 「先摊开、将来可能有用」的那一列，代价是一次迁移，而收益是一个没人写过的查询。
#:
#: ## 为什么 ``workspace`` 是一列而不是只躺在 payload 里
#:
#: 与 ``OWNED_TABLES`` 的既有规矩同一个形状（见 ``db.py::owned``）：这一列的用途是
#: 「一个装着两个部署的行的库要能被当场拒绝」，而它必须在**不解 payload** 的前提下
#: 被读到 —— 一个要先解 JSON 才知道自己属不属于这个库的判据，在一行 payload 坏掉时
#: 会以「解不开」的形式失败，而那一句错说的是另一件事。
#:
#: ## 索引：三条，各自对应一种真实的筛选
#:
#: 无筛选的那一条（``(started_at, attempt_id)``）不只是「筛 scope 与 url 之外」——
#: 它也是 ``since`` 那一条走的索引，而「最近发生过什么」正是这一页被打开的理由。
#: 三列都写成升序：``ORDER BY ... DESC`` 对一条全升序的索引是一次反向扫描，sqlite
#: 用得上；而一条 ``(a, b DESC)`` 这样的混合索引只有在**恰好**那个方向才被用上。
_ATTEMPT_DDL = """
-- 一次访问的实测记录（采集设计 §3 逐字要它承载的那几格，见 domain/fetch_attempt.py）。
--
-- 只增不改：这条记录是事后回答「这个 URL 那天到底怎么样了」的唯一凭据，而一条能被
-- 改写的访问记录在最需要它的场合恰好最不可信。所以这里没有 UPDATE，也没有删除入口
-- （要清账就换一个库 — 与 bind_workspace 对「换工作区」的处置同一句话）。
--
-- attempt_id 是主键，于是「同一次尝试写两遍」在数据库层就不可能变成两行；而幂等与
-- 冲突的区别（内容相同 → 幂等；内容不同 → 报错）由 ON CONFLICT DO NOTHING 之后的
-- 一次读取来判定，与 evidence 那张表逐字同一个形状。
CREATE TABLE IF NOT EXISTS fetch_attempt (
    attempt_id TEXT PRIMARY KEY,
    workspace  TEXT NOT NULL,
    scope      TEXT NOT NULL,
    url        TEXT NOT NULL,
    started_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
-- 三个筛选维度各一条。started_at 与 attempt_id 一起进索引的尾部，是为了同刻的一批行
-- 有一个**稳定**的顺序：只按 started_at 排序时，两次读在上限截断之下会给出不同的
-- 那一批，而报告上只显示「这次列的是别的几行」。
CREATE INDEX IF NOT EXISTS fetch_attempt_by_time
    ON fetch_attempt (started_at, attempt_id);
CREATE INDEX IF NOT EXISTS fetch_attempt_by_scope
    ON fetch_attempt (scope, started_at, attempt_id);
CREATE INDEX IF NOT EXISTS fetch_attempt_by_url
    ON fetch_attempt (url, started_at, attempt_id);
"""

#: Run（§B17）的落点。表名跟 ``research_job`` 同族，不叫 ``run`` —— 后者在别的
#: 方言与工具里到处都是，而一个要被人逐字读的表名不该在第一眼就有两种读法。
#:
#: **加表不改 ``SCHEMA_VERSION``**：与 ``_ATTEMPT_DDL`` / ``_CONTINUATION_DDL``
#: 逐字同一条理由 —— 这份 DDL 每次 ``ensure_schema`` 都整份重放，``IF NOT EXISTS``
#: 让「盘上还没有这张表」自愈。版本号只在**已有表改形状**时才动。
#:
#: ## 规格整份存 payload，不摊成列
#:
#: ``RunSpec`` 有十一格、其中两格还是**嵌套的预算元组**（本轮的 ``budget``，以及
#: 授权窗口自己的那一份）。摊成列等于把**契约**钉死在这个存储上（加一格就是一次
#: 迁移），而契约是 domain 的事 —— 它有自己的校验器，见 ``domain/run.py``。
#: 留下来的那几列只做三件事：``run_id`` 是主键（内容寻址，于是「同一个键不会受理
#: 两次」由主键本身保证），而 ``workspace`` 与 ``stop_reason`` 要能在**不解
#: payload** 的前提下被读到。
#:
#: ## ``workspace`` 为什么是一列
#:
#: 与 ``OWNED_TABLES`` 那条规矩同形：一个装着两个部署的行的库要能被当场拒绝，而它
#: 必须在不解 JSON 的前提下被读到 —— 一个要先解 payload 才知道自己属不属于这个库的
#: 判据，在一行 payload 坏掉时会以「解不开」的形式失败，而那句话说的是另一件事。
#:
#: ## ``stop_reason`` 是列，而且可以空
#:
#: 空 = 这一轮还在跑（见 ``domain/run.py::Run.stop_reason``）。它是一个**真的值**，
#: 不是「还没记」—— 受理那一刻它就被显式写下了。那一列的 CHECK 是 ``RunStatus``
#: 的成员表：一个拼错的结局词不会静默地在报告里长成第四种结局
#: （``tests/test_run_contract.py`` 比对这张表与那个枚举）。
#:
#: ## 三格配对：结局、Delta、停表同时有或同时无
#:
#: 那一条 CHECK 写成「三格的空值状态两两相等」。它不是防手滑的：一个「已经结束、
#: 但产出还没写」的窗口里，读的人无法区分「它什么都没产出」与「产出丢了」，而这两
#: 件事的处置相反。约束把那个窗口在**盘上**堵死（``domain/run.py::Run.__post_init__``
#: 是本进程里的同一条，两者各自守住一半 —— 一条管住别的进程，一条管住构造点）。
#:
#: 它**不**限制 ``COMPLETED`` 必须有一份非空的 Delta：那条判据在
#: ``ports/runs.py::ending_refusal``（要读 ``RunDelta`` 的八个格，SQL 表达不出，
#: 而表达得出来的近似写法 —— 「八个列至少一个非空」—— 会让形状的改动变成一次迁移）。
#:
#: ## 两条 ``spec`` 列都留着
#:
#: 不是冗余：``spec_sha256`` 是**冲突判据**（同键重提交时比对的唯一依据），``spec``
#: 是**内容**。只留内容会让每次重提交都重新散一次，而散列函数一旦被改，历史行的判据
#: 就全部作废 —— 判据必须是一个落盘的、不再重算的值。
_RUN_DDL = """
-- 一次被受理的增量（§B17）。十一格规格与两条推导见 domain/run.py。
CREATE TABLE IF NOT EXISTS research_run (
    run_id      TEXT PRIMARY KEY,
    workspace   TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL,
    spec        TEXT NOT NULL,
    stop_reason TEXT CHECK (stop_reason IN ('completed', 'partial', 'failed')),
    delta       TEXT,
    stopped_at  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((stop_reason IS NULL) = (delta IS NULL)
           AND (delta IS NULL) = (stopped_at IS NULL))
);
-- 「这个工作区受理了哪几轮」是唯一被真实问过的筛选，而它的顺序必须稳定：同刻受理
-- 的两轮只按 created_at 排时，两次读在上限截断之下会给出不同的那一批，而报告上
-- 只显示「这次列的是别的几行」。
CREATE INDEX IF NOT EXISTS research_run_by_workspace
    ON research_run (workspace, created_at, run_id);

-- 一轮 Run 的**折入账**（§B17 条件 2）。两张表，一个「有」，一个「有什么」。
--
-- ## 为什么账本本身要是一行
--
-- 「这一轮一行都没折」与「没有人记过这一轮折了什么」必须**可分辨**，因为这两件事的
-- 处置相反：前者照常停表，后者要停下来查。只留 ``run_fold`` 那张子表时，「零行」这
-- 一个读数要同时表达这两件事 —— 而它做不到，于是漏记会被读成零，池子变大了而账是平的。
--
-- 所以受理（``adapters/sqlite/runs.py::admit``）在**写 research_run 的同一个事务里**
-- 往这里插一行。它插进去的是**存在性**，不是内容：有了它，「这一轮折了零行」是一个真
-- 读数；没有它，读的人拿到的是 :class:`~marketops.domain.errors.RunFoldLedgerError`
-- —— 一条拒词，不是零。
--
-- ## 为什么 ``run_fold`` 记的是「池子那一行落了盘」，不记「处置」
--
-- 见 ``domain/fold.py`` 的模块 docstring。一句话：``asset`` 非空的行不全是
-- ``PROMOTED``（账号那一行没写进去的那些是 ``REJECTED``，而池子那一行确实落了盘），
-- 所以按处置记账会把这一类漏掉 —— 漏掉的那一条正是假账本身。
--
-- ## ``seq`` 为什么是一列而不是 ``rowid``
--
-- 账的顺序是**折入次序**，而它是这张表唯一被读出来的属性之一（``folds_of`` 按它排）。
-- 挂在 ``rowid`` 上等于把「次序」交给一个实现细节：一次 VACUUM 之后 rowid 会重排，
-- 而重排不报错。``seq`` 在写这一个事务里自己数（``BEGIN IMMEDIATE`` 之下没有别人在
-- 写同一轮的账），于是它是显式的、内容寻址的。
--
-- ## ``UNIQUE (run_id, receipt_id, ordinal)`` 是真判据，不是防手滑
--
-- 重放键是 ``(run_id, receipt_id, ordinal)`` 而不是 ``venue_asset_id``：一份回执里
-- 两行可以折进**同一块**场地（没有来源编号、名字与地点又相同的那两行），所以按句柄去重
-- 会把两条不同的行读成同一条。反过来，同一个键配一个**不同**的句柄是一次完整性冲突：
-- 要么这一笔被记了两次而第二次折的是别的行，要么有人动了库 —— 两种都不该被静默接受。
--
-- ## 这两张表为什么**不各带一列** ``workspace``
--
-- 它们的每一行都属于某个工作区，但那个归属**已经由外键说全了**：``run_fold_ledger``
-- 的 ``run_id`` 指向 ``research_run``，而 ``research_run.workspace`` 是那一行的所有者。
-- 多带一列会让「同一轮的两个归属」成为可构造的（外键管不住两列相等），而它是一句
-- 没有答案的话。``run_fold_ledger`` 仍然进 ``OWNED_TABLES`` —— 那个清单问的是
-- 「这张表的行有没有所有者」，不是「它有没有那一列」；漏掉它，一个账本有行却没有
-- 工作区绑定的库就不会被 ``ensure_schema`` 拒。
CREATE TABLE IF NOT EXISTS run_fold_ledger (
    run_id    TEXT PRIMARY KEY REFERENCES research_run (run_id),
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_fold (
    run_id         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    receipt_id     TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    venue_asset_id TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    UNIQUE (run_id, receipt_id, ordinal),
    FOREIGN KEY (run_id) REFERENCES run_fold_ledger (run_id)
);
"""


#: 产品目录（§B01）：产品资料、Offer、产品线三张表。
#:
#: ## 为什么 ``product_version`` **没有** ``workspace`` 列，另外两张有
#:
#: 因为 B01 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`` 逐字要求「一个获批产品
#: 版本可被两个 Scene 共用」—— 一个属于某个工作区的产品版本**没法**被两个工作区
#: 共用。产品资料是这一族的全局事实；Offer 与产品线是「在这个工作区里怎么用它」，
#: 所以它们各自带 ``workspace``、进 :data:`OWNED_TABLES`，产品资料不进。
#:
#: ## 幂等判定比的是整块 ``payload``，不是摘要
#:
#: ``scene_version`` 比摘要（``frozen_sha256``）是因为那个对象自带摘要字段。
#: ``ProductVersion`` 没有，而加一个要动 b01-a 已经落盘的领域形状。整块比在这里
#: 代价极低（一次 dict 比较），报错时还能列出**哪几格**不同 —— 比摘要更有用。
#:
#: ## 为什么三张表都没有摊平的业务列
#:
#: 见下面 ``_OUTREACH_DDL`` 那段：扁平列只给「要用它查」的字段。这一族的端口
#: （``ports/product_catalog.py``）上没有按状态、按品类、按市场的查询 —— 摊出来的
#: 列一处也不会被读，而它们各自都是一个与 payload 不一致的入口。
_PRODUCT_DDL = """
-- 产品资料的一版。主键就是那个「每一处引用都只写 (id, 2)」的 (id, 2)。
CREATE TABLE IF NOT EXISTS product_version (
    product_id TEXT NOT NULL,
    version    INTEGER NOT NULL CHECK (version >= 1),
    payload    TEXT NOT NULL,
    PRIMARY KEY (product_id, version)
);

-- Offer 的一版。形状与 product_version 同，多一列 workspace：Offer 说的是「在这个
-- 工作区里，拿这些产品做这个方案」，换一个工作区就是另一个 Offer。
CREATE TABLE IF NOT EXISTS offer_version (
    offer_id  TEXT NOT NULL,
    version   INTEGER NOT NULL CHECK (version >= 1),
    workspace TEXT NOT NULL,
    payload   TEXT NOT NULL,
    PRIMARY KEY (offer_id, version)
);

-- 产品线。PSM §4.1 说它是「零行为分组」——**没有版本号**，所以 line_id 自己就是
-- 那个唯一版本：同一个 line_id 写入不同内容报的是同一个 StoreError，不是覆盖。
-- 覆盖会让「这一组的成员什么时候变的」从记录里消失，而那一刻正是有人要查的东西。
CREATE TABLE IF NOT EXISTS product_line (
    line_id   TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    payload   TEXT NOT NULL
);
"""

#: 目标架构收敛这一轮（2026-09-29 起）新增的十五张表：Workstream / LaunchRecord /
#: EffectiveScene / FeedbackEvent / ExperienceCandidate / PolicyPatch / SavedView /
#: Watch / ActionItem / ScheduleDefinition / ScheduleFire / NotificationEvent /
#: NotificationSubscription / DeliveryReceipt / SessionBinding。
#:
#: **与上面几份分开**，理由与 ``_PRODUCT_DDL`` / ``_OUTREACH_DDL`` 那两段逐字同一条：
#: 上面每一张都已经被一个跑着的构建写过，这一份一张都没有。合成一份会让「这一段要
#: 不要上」变成「动一次所有表的 DDL」，而 DDL 只增不改这条规则的全部价值就在于
#: **加表不需要版本号**（见 :func:`ensure_schema` 的 docstring）。
#:
#: ## 为什么这一份先于实现落
#:
#: 十四条工作流并行时，第一条要写 Workstream 的实现与第二条要往 ActionItem 上挂
#: 待办的实现会各自把「业务键是什么」定成自己需要的样子。三处各定一次，就是三个
#: 入口读同一个名字、三个结果 —— 而分歧不在任何一次代码评审里可见，它只在两份报告
#: 对不上时可见。
#:
#: 所以**键与唯一性先落在这里**，每条都是插进去时就成立的约束，不是写在文档里的规则：
#:
#: * ``launch_record`` 的 ``UNIQUE (workspace, scene_id, scene_version, idempotency_key)``
#:   —— §B01 ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:126`` 逐字「重复同一请求回同一
#:   launch，不重置预算」。那条 UNIQUE **就是**这句话：第二次落不进去，读回第一次那一行。
#: * ``action_item`` 的 **partial unique index**（只盖未结状态）—— 「同一主体上的同一类
#:   待办只应有一条当前项」。没有它，每个 Run 都会新增一条；有了它，重复创建是插入失败。
#:   索引带 ``WHERE state IN ('open','claimed','waiting')`` 是因为**已结的可以有任意多条**：
#:   同一个账号上「核实转介联系人」做过三次就是三条历史，那不是重复。
#: * ``schedule_fire`` 的主键 ``(schedule_id, schedule_revision, fire_id)`` ——
#:   ``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md:320`` 逐字
#:   「重复 fire 幂等」。外部 cron / Hosting / ServerOS 与这个部署自己的 trigger
#:   driver 走的是同一条用例、同一个主键，所以「两套调度各启动一次全量刷新」
#:   在结构上不可能 —— 第二套落不进去。
#: * ``notification_event`` 的 ``UNIQUE (workspace, dedupe_key)`` ——
#:   ``:410`` 逐字「一个 NotificationEvent 只生成一次；投递到 N 个 sink 是 N 个
#:   receipt，不是 N 次业务事件」。那条 UNIQUE 让「N 次业务事件」写法不通。
#: * ``delivery_receipt`` 的主键带 ``attempt`` —— 重试产生**新的一行**，不覆盖。
#:   ``docs/DATA_MODEL.md:41`` 逐字要求它「without mutating the business event」，
#:   而覆盖写会让「第一次失败、第二次成功」在盘上只剩一个事实。
#: * ``effective_scene`` 的主键 ``(scene_id, version)`` 且**没有**可变的 ``state``
#:   列 —— §B02 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:110`` 逐字「编译…为不可变
#:   EffectiveScene 版本」。就地改写会让同一 ``(scene_id, version)`` 在两次部署之间
#:   是两份不同的东西，而引用它的每一处都看不出来。
#:
#: 上面几条里凡引到 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` 的，**引用只能出现在
#: 这段注释里，不能出现在下面那个字符串字面量里** —— ``tests/test_profiles.py`` 的
#: ``test_business_rules_are_not_hardcoded_in_engine`` 检查 ``marketops/**/*.py`` 里
#: 非文档字符串的字面量不含业务词，而那份文档的路径里带着一个。这正是下面
#: ``effective_scene`` 那一段只写 §B02 而不写全路径的原因。
#:
#: ## 为什么 payload 是整块 JSON
#:
#: 与 ``_PRODUCT_DDL`` 那段逐字同一条理由：扁平列只给「要用它查」的字段。上面
#: 摊出来的每一列都在本模块的一条查询或一条约束里真的出现过（键、唯一性、索引、
#: 状态过滤）；其余格子摊出来一处也不会被读，而它们各自都是一个与 payload
#: 不一致的入口。
_TARGET_DDL = """
-- 一个长期运行的调研任务。§B17 ``docs/B17_DESIGN_SLICES_2026-09-28.md:26`` 逐字
-- 「将来 Workstream 落地时它是**另一个格**，不是这一格改名」—— 所以它自成一张表，
-- 而不是往 ``research_run`` 或 ``scene`` 上加一列（那正是它点名不要的）。
--
-- **不存版本内容**，与 ``scene`` 同一条理由：绑定（objective / GeoScope / 政策 /
-- 预算信封 / freshness）是 append-only 的 EffectiveScene 那一侧，这一行只答
-- 「它现在是什么状态、第几版」。
--
-- ``scene_id`` 不是外键：一个 Scene 可以有多个 Workstream，而哪个 Scene 属于哪个
-- 工作区由 ``scene`` 表自己答。这里存下来是为了让「这个工作区里有哪些 Workstream」
-- 一次查完。
CREATE TABLE IF NOT EXISTS workstream (
    workstream_id TEXT PRIMARY KEY,
    workspace     TEXT NOT NULL,
    scene_id      TEXT NOT NULL,
    state         TEXT NOT NULL,
    revision      INTEGER NOT NULL CHECK (revision >= 1),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    pause_reason  TEXT,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS workstream_by_scene
    ON workstream (workspace, scene_id, workstream_id);

-- 一次被持久受理的启动。**只增不改**：它与 ``scene_version`` 同一条纪律 ——
-- 同一行写第二次、内容不同，意味着有人手工改过 key。
--
-- 它带 ``scene_version`` 而不只是 ``scene_id``，因为被预览、被批准、被受理的
-- 是**那一版**声明：b01-d 的 ``preview_launch`` 交出去的四格版本号里有它，而
-- 「换了一版再启动一次」是**另一次**启动，不是重复提交。
CREATE TABLE IF NOT EXISTS launch_record (
    launch_id       TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    scene_id        TEXT NOT NULL,
    scene_version   INTEGER NOT NULL CHECK (scene_version >= 1),
    idempotency_key TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    UNIQUE (workspace, scene_id, scene_version, idempotency_key)
);

-- 一版编译后的场景。**不可变**（§B02 逐字「编译…为不可变 EffectiveScene 版本」；
-- 那一句的完整定位在上方那段注释里，这一段只写小节号，理由也在那里），所以主键就是
-- (scene_id, version)，没有可变的 ``state`` 那一列 —— 状态在 ``scene`` 表上，它说的是**声明**。
--
-- ``digest`` 单独一列而不是从 payload 现算：它是 Run admission 的 ``policy_versions``
-- 里被引用的那个值，而「引用了一个刚刚被重算出来的摘要」与「引用了一个当时写下的
-- 摘要」在报告上长得一样。
CREATE TABLE IF NOT EXISTS effective_scene (
    scene_id   TEXT NOT NULL,
    version    INTEGER NOT NULL CHECK (version >= 1),
    workspace  TEXT NOT NULL,
    digest     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (scene_id, version)
);
CREATE INDEX IF NOT EXISTS effective_scene_by_workspace
    ON effective_scene (workspace, scene_id, version);

-- 一条反馈（人或代理提的）。**事件流，只增**，与 ``route_observation`` 同一条形状：
-- 它记的是「他当时说了什么」，而那句话不会被后来的判定改写。
--
-- ``scope_kind`` / ``scope_ref`` 是扁平列，因为它们要被用来查（「这个 Workstream
-- 上的反馈有哪些」）。``kind`` 同理。其余在 payload 里。
--
-- 幂等键由**调用方**给，与 ``idempotency_key`` 别处同款：服务端生成 ID 的话，
-- 「同一条反馈被提交了两次」与「他真的说了两遍」在盘上无法区分。
CREATE TABLE IF NOT EXISTS feedback_event (
    feedback_id     TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    principal       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    kind            TEXT NOT NULL,
    scope_kind      TEXT NOT NULL,
    scope_ref       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    UNIQUE (workspace, principal, idempotency_key)
);
CREATE INDEX IF NOT EXISTS feedback_event_by_scope
    ON feedback_event (workspace, scope_kind, scope_ref, created_at);

-- 慢环的第一步：一条可泛化的经验。**它不是批准**，所以它有自己的状态而没有
-- 任何一处权限判定读它 —— 读它的是 PolicyPatch 的提案者。
CREATE TABLE IF NOT EXISTS experience_candidate (
    candidate_id TEXT NOT NULL,
    revision     INTEGER NOT NULL CHECK (revision >= 1),
    workspace    TEXT NOT NULL,
    state        TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (candidate_id, revision)
);

-- 一份策略补丁。**批准之后不可变**，所以它每一版一行（与 ``outreach_family``
-- 那三张配置表同一条形状）；``review_case_id`` 是扁平列，因为「这一份提案挂在
-- 哪个案子上」是批准路径要读的那一格 —— 批准走的是**既有**的
-- ``application/review.py::approve_policy_change``，不是一条新的批准路径。
CREATE TABLE IF NOT EXISTS policy_patch (
    patch_id       TEXT NOT NULL,
    revision       INTEGER NOT NULL CHECK (revision >= 1),
    workspace      TEXT NOT NULL,
    state          TEXT NOT NULL,
    review_case_id TEXT,
    updated_at     TEXT NOT NULL,
    payload        TEXT NOT NULL,
    PRIMARY KEY (patch_id, revision)
);
CREATE INDEX IF NOT EXISTS policy_patch_by_case
    ON policy_patch (workspace, review_case_id);

-- 一个可重复查询的工作池。``name`` 是扁平列而不是只在 payload 里，因为它要
-- 被用来回答「这个人的同名视图是哪一条」—— 而那条判定的粒度是**当前版**：
-- 历史版本会重名（改名正是产生新版的理由之一），所以「当前版不重名」是 store
-- 的判据，不是这里的一条唯一索引。
CREATE TABLE IF NOT EXISTS saved_view (
    view_id    TEXT NOT NULL,
    revision   INTEGER NOT NULL CHECK (revision >= 1),
    workspace  TEXT NOT NULL,
    owner      TEXT NOT NULL,
    name       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (view_id, revision)
);

-- 一条监控规则。``next_due_at`` 是扁平列，因为「有哪些到期了」是唯一会全表扫的
-- 那个问题，而它是 trigger driver 每次醒来都要问的那一个。
--
-- 它**不自己创建 Run**：全仓唯一的 trigger driver 是 ``schedule_definition``
-- 那一侧，Watch 只产生 due request。
CREATE TABLE IF NOT EXISTS watch (
    watch_id    TEXT NOT NULL,
    revision    INTEGER NOT NULL CHECK (revision >= 1),
    workspace   TEXT NOT NULL,
    view_id     TEXT,
    enabled     INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    next_due_at TEXT,
    updated_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (watch_id, revision)
);
CREATE INDEX IF NOT EXISTS watch_by_due
    ON watch (workspace, enabled, next_due_at);

-- 一件待办。**跟进池是它的投影，不是一张表**（``docs/DATA_MODEL.md:37`` 逐字）。
--
-- 那条 partial unique index 是这张表存在的理由：同一主体上的同一类**未结**待办
-- 只应有一条。没有它，每个 Run 都会新增一条「核实转介联系人」，而报告上看起来
-- 只是「待办多了一些」。已结的可以有任意多条 —— 所以索引带 WHERE。
--
-- ``expected_version`` 是请求里带来的那一格（``:233`` 逐字），它不进 WHERE：
-- 「过期 version 拒写」是 store 的 CAS，不是一条索引能表达的东西。
CREATE TABLE IF NOT EXISTS action_item (
    action_id        TEXT NOT NULL,
    revision         INTEGER NOT NULL CHECK (revision >= 1),
    workspace        TEXT NOT NULL,
    scope_ref        TEXT NOT NULL,
    action_type      TEXT NOT NULL,
    owner            TEXT,
    state            TEXT NOT NULL,
    due_at           TEXT,
    expected_version INTEGER NOT NULL,
    updated_at       TEXT NOT NULL,
    payload          TEXT NOT NULL,
    PRIMARY KEY (action_id, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS action_item_open_by_subject
    ON action_item (workspace, action_type, scope_ref)
    WHERE state IN ('open', 'claimed', 'waiting');
CREATE INDEX IF NOT EXISTS action_item_by_owner
    ON action_item (workspace, owner, state);

-- 一份持久的调度声明。**一版一行**：改范围/预算/通知目标要版本和权限
-- （``:322`` 逐字），所以它不原地改。
--
-- ``trigger_type`` 与 ``timezone`` 留在 payload 里：没有一处查询按它们过滤，
-- 而摊出来的列每一格都是一个与 payload 不一致的入口。
CREATE TABLE IF NOT EXISTS schedule_definition (
    schedule_id       TEXT NOT NULL,
    schedule_revision INTEGER NOT NULL CHECK (schedule_revision >= 1),
    workspace         TEXT NOT NULL,
    enabled           INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    next_due_at       TEXT,
    updated_at        TEXT NOT NULL,
    payload           TEXT NOT NULL,
    PRIMARY KEY (schedule_id, schedule_revision)
);
CREATE INDEX IF NOT EXISTS schedule_definition_by_due
    ON schedule_definition (workspace, enabled, next_due_at);

-- 一次被受理的 fire。**主键就是幂等键**：``:320`` 逐字「外部 cron、Hosting、
-- ServerOS 以后都只能作为 trigger driver，带 fire_id/schedule_revision 调用
-- 同一 application use case；重复 fire 幂等」。
--
-- 它带 ``schedule_revision`` 而不只是 ``schedule_id``：同一份调度改过之后，
-- 同一个 ``fire_id`` 是**另一次** fire —— 而那种情形里「改了声明再重放」必须
-- 能发生，不能被上一版的那一行挡掉。
CREATE TABLE IF NOT EXISTS schedule_fire (
    schedule_id       TEXT NOT NULL,
    schedule_revision INTEGER NOT NULL CHECK (schedule_revision >= 1),
    fire_id           TEXT NOT NULL,
    workspace         TEXT NOT NULL,
    fired_at          TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    payload           TEXT NOT NULL,
    PRIMARY KEY (schedule_id, schedule_revision, fire_id)
);

-- 一条业务变化事件。**只增、不可变、只生成一次**（``docs/DATA_MODEL.md:39``
-- 逐字「immutable … emitted once」）。
--
-- 那条 ``UNIQUE (workspace, dedupe_key)`` 是「只生成一次」的载体。``dedupe_key``
-- 由业务处置点算（它知道「这一次变化」是什么），而**不是**由通知层算 —— 通知层
-- 算的话，加一个 sink 就会多一个去重域，而那正是 ``:410`` 逐字要挡的那件事。
CREATE TABLE IF NOT EXISTS notification_event (
    event_id    TEXT PRIMARY KEY,
    workspace   TEXT NOT NULL,
    dedupe_key  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload     TEXT NOT NULL,
    UNIQUE (workspace, dedupe_key)
);
CREATE INDEX IF NOT EXISTS notification_event_by_kind
    ON notification_event (workspace, kind, occurred_at);

-- 一条订阅。事件本身**不写**「已通知 Codex / 飞书」（``:337`` 逐字「事件本身不写
-- 已通知…订阅和投递分开」），所以「谁要什么」只在这一张表上。
CREATE TABLE IF NOT EXISTS notification_subscription (
    subscription_id TEXT NOT NULL,
    revision        INTEGER NOT NULL CHECK (revision >= 1),
    workspace       TEXT NOT NULL,
    principal       TEXT NOT NULL,
    sink            TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    PRIMARY KEY (subscription_id, revision)
);
CREATE INDEX IF NOT EXISTS notification_subscription_by_principal
    ON notification_subscription (workspace, principal, sink);

-- 一次逐 sink 的投递。**主键带 attempt**：重试产生新的一行，不覆盖。
--
-- ``sink`` 是扁平列而不是只在 payload 里，因为「这个 sink 是不是一直在失败」
-- 是重试策略要问的那个问题，而它是按 sink 聚合的。
--
-- 这张表**不改业务事件**（``docs/DATA_MODEL.md:41`` 逐字「without mutating the
-- business event」）：一条投递失败之后研究**不重跑**，只重试通知。
CREATE TABLE IF NOT EXISTS delivery_receipt (
    event_id        TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    sink            TEXT NOT NULL,
    attempt         INTEGER NOT NULL CHECK (attempt >= 1),
    workspace       TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    payload         TEXT NOT NULL,
    PRIMARY KEY (event_id, subscription_id, sink, attempt)
);

-- 客户端会话的一个便利指针。**它不提供权限、不成为 Account/Job 的外键、
-- 不参与业务幂等**（``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md:278``
-- 逐字）。
--
-- ``client_session_ref`` 可空 —— 客户端有稳定 id 时才写（``:271``），所以
-- 「同一个 principal 的两条无 id 绑定」不是重复，它们各自是一条。唯一性因此
-- 只在 session_ref 非空时才成立，而 SQLite 的 UNIQUE 把 NULL 当作互不相等
-- （这正是这里想要的），所以那条索引可以直接写。
CREATE TABLE IF NOT EXISTS session_binding (
    binding_id         TEXT PRIMARY KEY,
    workspace          TEXT NOT NULL,
    principal          TEXT NOT NULL,
    client_kind        TEXT NOT NULL,
    client_session_ref TEXT,
    expires_at         TEXT,
    last_seen          TEXT NOT NULL,
    payload            TEXT NOT NULL,
    UNIQUE (workspace, principal, client_kind, client_session_ref)
);
"""

#: 外向链（模板族 / 草稿 / 清单 / 授权 / 尝试 / 抑制 / 事件 / 交接）。
#:
#: **与上面那份分开**，理由只有一条：这几张表是产品闭环后半段的东西，
#: 而上面那份的每一张都已经被一个跑着的构建写过。合成一份会让「这一段要不要
#: 上」这个问题变成「动一次所有表的 DDL」—— 而 DDL 只增不改这条规则的全部价值
#: 就在于**不必**为了加一张表去动已经落盘的库。两份都在 ``ensure_schema`` 里
#: 重放，所以分成几个常量不改变任何行为。
#:
#: ## 扁平列只给「要用它查」的字段
#:
#: 每个领域对象整块存进 ``payload``，另外把**出现在 WHERE / ORDER BY / 去重键里**
#: 的字段摊成列。不这么做的表（比如 ``outreach_family``）只有一个主键列 ——
#: 一个「把所有字段都摊成列」的实现会在加字段时被迫做一次迁移，而它摊出来的
#: 那些列没有任何一处读。
#:
#: ## 三个 §6/§7 的约束落成了主键而不是纪律
#:
#: * ``outreach_attempt`` 的主键是 ``send_key``（**不是** (wave, recipient)）：
#:   「同一封信发给同一个人」在整条链上只有一行，于是「不得重复发」不是一条
#:   写在文档里的规则，是插入时的主键冲突。
#: * ``outreach_event`` 的去重键是 ``(sendbox, provider_message_id)`` 的摘要：
#:   webhook 重放与「两条一样的事件」因此分得开，而主题**不在**这个键里 ——
#:   对方可以随手改主题，而一条改了主题的「请不要再联系我」若被去重掉，
#:   它就消失了。
#: * ``outreach_suppression`` 的主键是 ``(recipient, reason)``：一条解除之后再
#:   收到同一理由的投诉是**两条**事实，所以这里不覆盖 ``released_at``，
#:   而是写不进去 —— 而「写不进去」正是它该有的行为。
#: 三张配置表的建表语句，**按表名单独一份**。
#:
#: 它们为什么与别的表分开：``migrate.py`` 的 v3 → v4 必须**重建**这三张表（主键从
#: ``(id)`` 变成 ``(id, revision)``，而 SQLite 改不了主键）。重建的动作是「把旧表
#: 改名搬开 → 按同一个形状建回来 → 搬行 → 删掉旧的」，于是那边也要一份建表语句。
#: 若两边各写一遍，分家的表现是「迁移建出一张没有主键的表」—— 那是一句**不会报错**
#: 的错误。所以形状只有这里一份，``_OUTREACH_DDL`` 与迁移都读它。
CONFIG_TABLE_DDL: tuple[tuple[str, str, str], ...] = (
    (
        "outreach_family",
        "family_id",
        """-- 模板族：按触达阶段与角色路线组织，共享地区/语言/产品配置。
-- **不存正文** —— §5 要挡的就是「预生成几百份静态模板」。
--
-- 主键是 (family_id, revision)：一族可以有多版，**每一版一行**。改话术的正确动作
-- 是发下一版，而不是原地改 —— 一条已经生成的草稿记的是 (id, 版)，原地改会让那句
-- 记录指向一份它当时没见过的配置，而盘上看起来一切正常。
CREATE TABLE IF NOT EXISTS outreach_family (
    family_id TEXT NOT NULL,
    revision  INTEGER NOT NULL,
    stage     TEXT NOT NULL,
    payload   TEXT NOT NULL,
    PRIMARY KEY (family_id, revision)
);""",
    ),
    (
        "outreach_writing_policy",
        "policy_id",
        """CREATE TABLE IF NOT EXISTS outreach_writing_policy (
    policy_id TEXT NOT NULL,
    revision  INTEGER NOT NULL,
    payload   TEXT NOT NULL,
    PRIMARY KEY (policy_id, revision)
);""",
    ),
    (
        "outreach_claim",
        "claim_id",
        """-- 已批准的说法。``expires_at`` **不在这里过滤** —— 过期是相对于某个时刻的判定，
-- 而一个在库里过滤的实现会让「这条说法现在还算不算数」取决于查询发生在什么时候。
CREATE TABLE IF NOT EXISTS outreach_claim (
    claim_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload  TEXT NOT NULL,
    PRIMARY KEY (claim_id, revision)
);""",
    ),
)

#: 表名 → 建表语句。迁移重建时按名字取。
CONFIG_TABLE_BY_NAME: dict[str, str] = {name: ddl for name, _, ddl in CONFIG_TABLE_DDL}

#: 表名 → 那一行的 id 列名。**与建表语句同源**（同一个元组的三格），因为迁移要按它在
#: 行与行之间定位：一句「这一行搬过去了」必须说得出是哪一行的。
CONFIG_KEY_COLUMNS: dict[str, str] = {name: key for name, key, _ in CONFIG_TABLE_DDL}

#: 路线那一族的建表语句，**按表名单独一份**（理由与 ``CONFIG_TABLE_DDL`` 同一个）。
#:
#: 三张表加一个机会表，它们放在一起是因为 v4 → v5 那一步同时动它们的形状：
#: ``contact_route`` 的主键从 ``seq`` 换成 ``canonical_key``，另两张是新的。
#: 重建的动作是「改名搬开 → 按同一个形状建回来 → 搬行 → 删掉旧的」，所以迁移那边
#: 也要一份建表语句 —— 与 ``CONFIG_TABLE_DDL`` 完全同一条理由，见它的注释。
#:
#: ``route_opportunity`` 的形状这一步**没有动**，它进来只是因为上面那条注释说的
#: 「不在这里」得有个去处：四张表在同一个族里，分开放会让「路线这一族有几张表」
#: 变成一件要去两个地方数的事。
ROUTE_TABLE_DDL: tuple[tuple[str, str, str], ...] = (
    (
        "route_observation",
        "observation_id",
        """-- 路线观察：**历史**，只追加，不去重。
--
-- 这张表是 v5 从旧的 contact_route 拆出来的那一半 —— 旧表的每一行都是一次观察，
-- 只是它没有名字，于是被当成了「这个账号的路线」（见 domain/route_records.py）。
--
-- observation_id 是内容寻址的（工作区 + 账号 + 规范键 + 观察时间 + 那条路线），
-- 所以**重放同一个观察是幂等的**，而两次真实的抓取（哪怕抓到一模一样的内容）
-- 留下两行 —— 后者正是历史该有的样子。canonical_key 与 account_id 各有索引：
-- 前者是折叠时的查找路径（`record_route` 每次都要按它读当前投影），后者是
-- `list_route_observations` 的路径。
CREATE TABLE IF NOT EXISTS route_observation (
    observation_id TEXT PRIMARY KEY,
    canonical_key  TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    workspace      TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    payload        TEXT NOT NULL,
    stored_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS route_observation_by_key
    ON route_observation (canonical_key, observed_at, observation_id);
CREATE INDEX IF NOT EXISTS route_observation_by_account
    ON route_observation (account_id, observed_at, observation_id);""",
    ),
    (
        "contact_route",
        "canonical_key",
        """-- 当前投影：**每个规范键恰好一行**。主键就是规范键 —— 这一格是这次改动的一半，
-- 而且它是结构性的，不是一条「写之前先查一下」的约定：两个 worker 同时折叠同一个
-- 键时，第二个撞在主键上走 UPDATE，而不是悄悄插出第二行。
--
-- 旧 contact_route 的列一个都没留：seq 是「第几次插入」，而这张表里没有这个概念；
-- account_id 留着（读路径按它取），workspace 留着（越权读的那条判据与其余表同款）。
CREATE TABLE IF NOT EXISTS contact_route (
    canonical_key TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    workspace     TEXT NOT NULL,
    payload       TEXT NOT NULL,
    stored_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contact_route_by_account
    ON contact_route (account_id, canonical_key);""",
    ),
    (
        "route_rejection",
        "rejection_id",
        """-- 被拒绝的路线候选：§6.3 要的那张表。rejection_id 是内容寻址的（工作区 + 账号 +
-- 原因码 + 邮箱 + 来源页 + 时间），所以同一次判定的重放是幂等的。
--
-- 它**不并进** route_observation：那边装的是成立的路线，这边装的是没有成立的候选。
-- 混在一起之后，「这个账号有几条路线」就要靠读的人按状态过滤。
CREATE TABLE IF NOT EXISTS route_rejection (
    rejection_id TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL,
    workspace    TEXT NOT NULL,
    code         TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    payload      TEXT NOT NULL,
    stored_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS route_rejection_by_account
    ON route_rejection (account_id, occurred_at, rejection_id);""",
    ),
    (
        "route_opportunity",
        "seq",
        """CREATE TABLE IF NOT EXISTS route_opportunity (
    seq        INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS route_opportunity_by_account
    ON route_opportunity (account_id, seq);""",
    ),
)

#: 表名 → 建表语句。迁移重建时按名字取（与 ``CONFIG_TABLE_BY_NAME`` 同款）。
ROUTE_TABLE_BY_NAME: dict[str, str] = {name: ddl for name, _, ddl in ROUTE_TABLE_DDL}

#: 表名 → 那一行的 id 列名。**与建表语句同源**，理由同 ``CONFIG_KEY_COLUMNS``。
ROUTE_KEY_COLUMNS: dict[str, str] = {name: key for name, key, _ in ROUTE_TABLE_DDL}

_ROUTE_DDL = "\n\n".join(ddl for _, _, ddl in ROUTE_TABLE_DDL)

#: ``research_job`` 的建表语句，**单独一份**（理由与 ``CONFIG_TABLE_DDL`` 同一个）。
#:
#: 它为什么与别的表分开：``migrate.py`` 的 v5 → v6 必须**重建**这张表 ——
#: ``input_kind`` 那一格的 CHECK 是一份词表，而 v5 写下的那一份少了 ``cohort``
#: 这个成员。``ALTER TABLE`` 没有改约束这一项，所以那一步是「按新形状建一张 →
#: 搬行 → 丢旧的 → 改名」，于是那边也要一份形状。两边各写一遍的话，分家的表现是
#: 「迁移把 CHECK 又搬回了旧词表」—— 那是一句**不会报错**的错误：重建照样成功，
#: 只是 cohort 仍然写不进去，而那正是这一步要修的东西。
#:
#: ## 那一格词表与枚举必须一起长
#:
#: CHECK 里列的就是 :class:`JobInputKind` 的成员值，一个字都不多、一个字都不少
#: —— ``tests/test_sqlite_job_kind.py`` 拿枚举当判据把它钉住（这一条与
#: ``migrate.py`` 那一步的判据是同一句话的两半：那一步用「盘上的词表 ≠ 这里这一份」
#: 决定要不要重建，而这一条保证「这里这一份 = 枚举」）。少了它，「加一个成员」
#: 与「库认不认得那个成员」就是两件要靠人记得的事 —— 而这一次忘了，代价恰恰是
#: 一次 CHECK 撞车。
#:
#: ``input_kind`` 的 CHECK 现在行长超过 100：拆成两行比压成一行可读，
#: 而这一份字符串是要被人逐字读的（它不是给 SQLite 看的第一手，是给**下一个改它
#: 的人**看的）。
JOB_TABLE_DDL: tuple[tuple[str, str, str], ...] = (
    (
        "research_job",
        "job_id",
        """-- 一次被受理的研究请求。血缘与四个动词见 domain/job.py 与 ports/jobs.py。
--
-- job_id 是内容寻址的（scene + 幂等键），所以「同一个键不会受理两次」由主键
-- 本身保证。UNIQUE 那一列是**读了才有意义**的：request_sha256 要在同一个写
-- 事务里被读出来比对，才能把「重发」与「同一个键改了内容」分开。
--
-- input_kind 的那份 CHECK 就是 JobInputKind 的成员表（四个）：v5 写下的那一份
-- 只有前三个，于是按 cohort 提交研究撞在 CHECK 上 —— 这句话读起来像存储坏了，
-- 而事实是那张表不认识这一版代码的枚举。v5 → v6 那一步重建这张表正是在修它。
CREATE TABLE IF NOT EXISTS research_job (
    job_id          TEXT PRIMARY KEY,
    scene_id        TEXT NOT NULL,
    input_kind      TEXT NOT NULL CHECK (input_kind IN
                        ('source_receipt', 'known_accounts', 'discovery_scope',
                         'cohort')),
    input_ref       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256  TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN
                        ('queued', 'paused', 'blocked', 'cancelled')),
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (scene_id, idempotency_key)
);""",
    ),
)

#: 表名 → 建表语句（与 ``CONFIG_TABLE_BY_NAME`` 同款）。
JOB_TABLE_BY_NAME: dict[str, str] = {name: ddl for name, _, ddl in JOB_TABLE_DDL}

#: 表名 → 那一行的键列名。**与建表语句同源**（同一个元组的三格）：迁移那一步要按它
#: 逐行对账（「每一行都还在」这句话必须说得出是按哪一格数的）。
JOB_KEY_COLUMNS: dict[str, str] = {name: key for name, key, _ in JOB_TABLE_DDL}

_JOB_DDL = "\n\n".join(ddl for _, _, ddl in JOB_TABLE_DDL)


def split_ddl_statements(ddl: str) -> tuple[str, ...]:
    """把一段建表语句切成**一句一条**。

    ``replay_ddl`` 用 ``executescript`` 一次重放全部，而迁移**不能**：``executescript``
    在 Python 的 ``sqlite3`` 里会先隐式提交当前事务（见 ``migrate._apply_revisions``），
    于是重建的表与这次迁移的其余部分不在同一个事务里。而
    ``sqlite3.Connection.execute`` 只收一条语句 —— 所以 ``ROUTE_TABLE_DDL`` 那种
    「CREATE TABLE + CREATE INDEX」两句话的条目必须先切开。

    **不能按 ``;`` 切**：注释里与字符串字面量里都可能有一个分号，而按它切出来的两半
    都会执行失败，失败点离原因很远（一句 ``near "(": syntax error`` 指不到那次切分）。
    这里用 SQLite 自己的 ``complete_statement`` 扫 —— 它认的就是数据库自己认的那种
    「这条说完了」，包括引号与注释的配对。

    结尾那一段不是完整语句时：纯注释的直接丢掉（它本来就不是一条语句），其余的原样
    留下 —— 让它炸在 SQLite 上，比在这里静默吃掉半条建表语句好。丢掉半条 DDL 是一句
    **不会报错**的错误：表建起来了，少一个索引或一个约束，而没有任何一处会说出来。
    """
    statements: list[str] = []
    buffer: list[str] = []
    for line in ddl.splitlines():
        buffer.append(line)
        if sqlite3.complete_statement("\n".join(buffer)):
            statements.append("\n".join(buffer).strip())
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail and any(
        line.strip() and not line.strip().startswith("--") for line in tail.splitlines()
    ):
        statements.append(tail)
    return tuple(statements)

_OUTREACH_DDL = (
    """
-- ------------------------------------------- 外向链：配置

"""
    + "\n\n".join(ddl for _, _, ddl in CONFIG_TABLE_DDL)
    + """

-- ------------------------------------------- 外向链：活动、批次、清单、授权"""
    + """

CREATE TABLE IF NOT EXISTS outreach_campaign (
    campaign_id TEXT PRIMARY KEY,
    workspace   TEXT NOT NULL,
    scene_id    TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outreach_campaign_by_scene
    ON outreach_campaign (workspace, scene_id, campaign_id);

-- 一版草稿。**唯一不覆盖的版本化对象之一**：同一 (draft_id, version) 写第二次，
-- 内容相同即幂等，不同报错 —— 覆盖会让「授权时批的那一稿」静默换掉。
CREATE TABLE IF NOT EXISTS outreach_draft (
    draft_id      TEXT NOT NULL,
    draft_version INTEGER NOT NULL,
    wave_id       TEXT NOT NULL,
    workspace     TEXT NOT NULL,
    body_sha256   TEXT NOT NULL,
    generation_key TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    PRIMARY KEY (draft_id, draft_version)
);
CREATE INDEX IF NOT EXISTS outreach_draft_by_wave
    ON outreach_draft (wave_id, draft_id, draft_version);

-- 冻结清单。**一个 wave 一份**：同 wave_id 配不同摘要是报错，不是覆盖 ——
-- 换掉一份清单会让绑在旧摘要上的授权全部失去意义，而盘上只显示「清单更新了」。
CREATE TABLE IF NOT EXISTS outreach_manifest (
    wave_id         TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    scene_id        TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    payload         TEXT NOT NULL
);

-- 批次状态。revision 是乐观锁序号，与 scene 表同一条：写的时候必须带上
-- 「我读到的是第几版」。
CREATE TABLE IF NOT EXISTS outreach_wave (
    wave_id         TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    scene_id        TEXT NOT NULL,
    state           TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outreach_wave_by_campaign
    ON outreach_wave (workspace, campaign_id, wave_id);

-- 授权。**只增不改**：一次撤回是一条新记录，旧的那条留着 —— 「谁在什么时候
-- 撤回了什么」正是事后要查的第一件事。
CREATE TABLE IF NOT EXISTS outreach_authorization (
    authorization_id TEXT PRIMARY KEY,
    wave_id          TEXT NOT NULL,
    workspace        TEXT NOT NULL,
    scene_id         TEXT NOT NULL,
    authorized_at    TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outreach_authorization_by_wave
    ON outreach_authorization (wave_id, authorized_at, authorization_id);

-- ------------------------------------------- 外向链：投递

-- 一次投递尝试。**主键是 send_key**，见本常量开头的说明。
-- ``attempt`` 是列也是 payload 的一部分，但它在**重试时会被就地加一**，
-- 所以它是「摊出来的那几个不只是索引的列」里最需要交叉校验的一个。
CREATE TABLE IF NOT EXISTS outreach_attempt (
    send_key   TEXT PRIMARY KEY,
    workspace  TEXT NOT NULL,
    wave_id    TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    draft_id   TEXT NOT NULL,
    attempt    INTEGER NOT NULL,
    state      TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outreach_attempt_by_wave
    ON outreach_attempt (wave_id, recipient, draft_id);
-- §6 逐字要求每一次投递前重查**跨批次**的发送历史：一个只查本批次历史的实现，
-- 会让同一个人在两个活动里各收到一封，而两批的记录各自完全正确。
CREATE INDEX IF NOT EXISTS outreach_attempt_by_recipient
    ON outreach_attempt (recipient, started_at);

-- 抑制名单。主键是 (recipient, reason)，见本常量开头的说明。
-- **不删行**：``released_at`` 记的是「有人手工解除了」这件事，而不是让这一行消失。
CREATE TABLE IF NOT EXISTS outreach_suppression (
    recipient     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    suppressed_at TEXT NOT NULL,
    released_at   TEXT,
    payload       TEXT NOT NULL,
    PRIMARY KEY (recipient, reason)
);
CREATE INDEX IF NOT EXISTS outreach_suppression_by_recipient
    ON outreach_suppression (recipient, suppressed_at);

-- ------------------------------------------- 外向链：事件与交接

-- 进来的事件（回信 / 退信 / 退订 / 投诉）。
-- ``dedupe_key`` 是 (sendbox, provider_message_id) 的摘要，**唯一** ——
-- 重放落到同一个键上，于是 put_event 能回答「这次是不是新的一条」。
CREATE TABLE IF NOT EXISTS outreach_event (
    event_id  TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    workspace TEXT NOT NULL,
    sendbox   TEXT NOT NULL,
    kind      TEXT NOT NULL,
    received_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    payload   TEXT NOT NULL
);
-- 顺序按**发生时刻**而不是落库时刻：乱序到达是常态，而一份按入库顺序排的事件表
-- 会让「谁先说的」在报告上变成「谁先被我们读到的」。
CREATE INDEX IF NOT EXISTS outreach_event_by_workspace
    ON outreach_event (workspace, received_at, event_id);

-- 销售交接。**与抑制相反，这里覆盖写**：抑制的解除是「这件事结束了」，
-- 交接的状态推进是「这件事正在走」—— 前者要留痕，后者要读当前。
CREATE TABLE IF NOT EXISTS outreach_handoff (
    handoff_id TEXT PRIMARY KEY,
    workspace  TEXT NOT NULL,
    scene_id   TEXT NOT NULL,
    state      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outreach_handoff_by_workspace
    ON outreach_handoff (workspace, created_at, handoff_id);
"""
)


def connect(db_path: Path) -> sqlite3.Connection:
    """打开一个连接。

    **每次调用新开一个**，不复用、不共享：``sqlite3.Connection`` 默认不允许跨
    线程使用，而共享连接在 Windows/Linux 上的行为不同。短连接在这个规模上的
    代价（每次约几十微秒）远低于一个只在特定平台上复现的并发 bug。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=_BUSY_TIMEOUT_MS / 1000)
    except sqlite3.Error as exc:
        raise StoreError(f"打开状态库失败（{db_path}）：{exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    """借一个连接，用完关掉。

    **不要**直接写 ``with connect(path) as conn``：``sqlite3.Connection`` 的
    上下文管理器管的是隐式事务的提交/回滚，它**不关连接**。在
    ``isolation_level=None``（本模块选的模式）下它连提交都不管，于是那个 ``with``
    既没管事务也没关连接 —— 一个看起来完全正确、只在文件描述符耗尽时才现形的
    写法。
    """
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """写事务。``BEGIN IMMEDIATE`` —— 见模块 docstring。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


#: 每个**旧**版本各自欠着什么：键是那个版本，值是一句「它为什么不能被这一版直接打开」。
#:
#: 必须逐个版本写，而不是一句「版本旧了」：处置虽然都是跑同一条迁移，但**为什么**
#: 是三件不同的事（v1 缺的是血统那一格，v2 缺的是词表，v3 缺的是版本那一列），而运维
#: 要判断的正是「这个库是怎么来的、它还值不值得迁」。一句笼统的话在这件事上什么都没说。
_VERSION_NOTES: dict[int, str] = {
    1: (
        "v1 的三张表（evidence / contact_route / route_opportunity）里每一行都没有"
        "血统（workspace），而这一版代码要求它必填 —— 直接打开会让那些行在模型校验"
        "上炸成一句看起来像「库坏了」的错。"
    ),
    2: (
        "v2 的维护三张表（observation / change_signal / maintenance_claim）里，aspect "
        "那一格用的是旧词表：source_supply 与 responsibility 换了名字，而 asset_fact 与 "
        "role_email 两格在新词表里**没有对应的那一格**（它们各自被拆成两个以不同速率"
        "过期的面）—— 直接打开会让那些行在 Aspect(...) 上炸成一句看起来像「库坏了」的错。"
    ),
    3: (
        "v3 的三张外向配置表（outreach_family / outreach_writing_policy / outreach_claim）"
        "主键只有 id 一格：一个模板族的第二版会**覆盖**第一版，而一条已经生成的草稿记的"
        "正是那个 id —— 于是它指向一份自己没见过的配置。这一版代码的读写两条路径都按 "
        "(id, revision) 走，而 v3 的表里没有 revision 那一列，所以第一次发布就会撞上一句"
        "「table outreach_family has no column named revision」那样的错。"
    ),
    4: (
        "v4 的 contact_route 是一份**事件流**：一行一条观察，主键是插入序号，而这一版"
        "代码已经改成两层（route_observation 不可变历史 + contact_route 当前投影，主键是"
        "规范键）。直接打开有三处会坏，且都长得像「库坏了」：读路径按 CanonicalContactRoute "
        "解 payload，而 v4 的行里没有 canonical_key / revision / observation_ids 这些格"
        "（它自己的字段反而多出来一批），于是每一行都校验失败；route_rejection 那张表在 "
        "v4 里根本不存在（第一次读拒收就撞上「no such table」）；而 contact_route 的主键"
        "列名也从 seq 换成了 canonical_key，任何按新形状写的语句都会撞上一句"
        "「no such column: canonical_key」。"
    ),
    5: (
        "v5 的 research_job.input_kind 那一格的 CHECK 只列着三个成员，而这一版代码的 "
        "JobInputKind 有四个（第四个是 cohort：按冻结的那一份队列提交研究）。表本身"
        "读得出来 —— 坏的只有那一处约束，所以它比前几个版本都安静：打开、读、写已知的"
        "那三种输入全都正常，只有**按 cohort 提交**撞在一句 CHECK 上，报出来的是一句"
        "看起来像存储坏了的 IntegrityError。"
    ),
}


class SchemaStanding(str, Enum):
    """一个状态库相对**这一版代码**的处境。八种，只有三种起得来。

    前两种是**文件级**的（能不能打开它），只有 :func:`read_database_readiness`
    会给出；后六种是**连接级**的，:func:`read_schema_state` 拿到一个已经打开的
    连接时给出。分成两处给不是因为它们不是一件事，而是因为「打不开」在拿到连接
    之前就已经发生了 —— 把那个判断塞进一个需要连接的函数里，只能靠伪造一个连接。

    * :attr:`ABSENT` —— 盘上没有这个文件。**不是故障**：第一次写入会建它。
    * :attr:`UNINITIALIZED` —— 文件在，但 ``schema_meta`` 里还没有版本那一行。
      同样不是故障：``ensure_schema`` 会盖章。这一格与上一格的区别是「谁建了它」
      —— 一个是本服务还没写过，一个是有人先建了个空文件。
    * :attr:`CURRENT` —— 版本与这一版代码一致，也没有「有行却没绑定」。
    * :attr:`BEHIND` / :attr:`AHEAD` —— 比代码旧 / 比代码新。**两者都拒绝**：
      旧的那个要在读之前补值，新的那个这一版代码不认识它的形状。
    * :attr:`UNKNOWN` —— 版本那一格在，但它不是数字。这一格以前会以一句
      ``ValueError`` 从 ``ensure_schema`` 里炸出来 —— 一句回溯看起来像代码坏了，
      而事实是那个库的那一格被写坏了。
    * :attr:`UNBOUND` —— 版本对，但库里有行、``schema_meta`` 里没有工作区绑定。
    * :attr:`UNREADABLE` —— 打不开：不是数据库文件、目录不可写、权限不够。
    """

    ABSENT = "absent"
    UNINITIALIZED = "uninitialized"
    CURRENT = "current"
    BEHIND = "behind"
    AHEAD = "ahead"
    UNKNOWN = "unknown"
    UNBOUND = "unbound"
    UNREADABLE = "unreadable"


#: 起得来的那三种。``ABSENT`` 与 ``UNINITIALIZED`` 都在里面：它们都是「还没有
#: 历史」而不是「有历史但对不上」，而 ``ensure_schema`` 正是从这两种状态出发
#: 把库带到一个可用的形状 —— 一个全新的部署第一次起服务时走的**就是**这一格。
_STARTABLE_STANDINGS: frozenset[SchemaStanding] = frozenset(
    {SchemaStanding.ABSENT, SchemaStanding.UNINITIALIZED, SchemaStanding.CURRENT}
)


@dataclass(frozen=True)
class SchemaState:
    """一个库的处境，以及**要说给运维的那句话**。

    ``message`` 非空 = 这一版代码打不开它，而那句话就是 ``ensure_schema`` 会抛的
    那一句。做成字段而不是让调用方自己拼，是因为这条拒绝有两个读法不同的读者：
    写路径上的人（拿到异常）与部署前那次自检（要把它印在 ``--check`` 的输出里）。
    两处各拼一遍，就会出现「自检说要跑 A 那条命令、真正启动时说要跑 B」。所以
    判据与措辞都只有一份，在这里。
    """

    standing: SchemaStanding
    recorded: int | None
    expected: int
    message: str


def _database_file(conn: sqlite3.Connection) -> str:
    """这个连接打开的是哪个文件。拒绝里点名它，运维才能去动对那个文件。"""
    return str(conn.execute("PRAGMA database_list").fetchone()["file"])


def read_schema_state(conn: sqlite3.Connection, *, where: str) -> SchemaState:
    """这个库相对这一版代码的处境。**只读**：一条 SELECT 也不带写。

    ``where`` 是报出去时给这个库起的名字（那句拒绝里点名的就是它）。要调用方传
    而不是在这里读 ``PRAGMA database_list``，是为了让**自检那条路径**也能问同一个
    问题：它打开的是一个 ``mode=ro`` 的连接，而它手里已经有这个路径了 —— 两处
    各自读一次，会出现「报道的是 A、诊断的是 B」而两边都不报错。

    这一份是那条判据的**唯一**来源：:func:`ensure_schema` 拿它说「不能打开」，
    部署前的自检拿它说「这个部署现在起不起得来」。
    """
    raw = get_meta(conn, SCHEMA_VERSION_KEY)
    if raw is None:
        return SchemaState(SchemaStanding.UNINITIALIZED, None, SCHEMA_VERSION, "")
    try:
        recorded = int(raw)
    except ValueError:
        return SchemaState(
            SchemaStanding.UNKNOWN,
            None,
            SCHEMA_VERSION,
            f"状态库 schema 版本那一格是 {raw!r}，不是一个版本号：{where}\n"
            "这一格由本服务的写入路径写（每一次打开库都会核对它），所以它只可能"
            "来自一次篡改或一次写坏了的手工操作。\n"
            "不猜它本来是什么：认不出来的版本号与「没有版本号」是两件事，后者会"
            "被盖章，前者必须由人处置。",
        )
    if recorded != SCHEMA_VERSION:
        note = _VERSION_NOTES.get(recorded)
        if note is None:
            return SchemaState(
                SchemaStanding.AHEAD if recorded > SCHEMA_VERSION else SchemaStanding.BEHIND,
                recorded,
                SCHEMA_VERSION,
                f"状态库 schema 版本是 {recorded}，代码期望 {SCHEMA_VERSION}：{where}\n"
                "不做自动迁移是刻意的 —— 迁移方向、是否需要保留旧库，是人的决定。\n"
                "要么用匹配版本的工具处理它，要么把它移开让本进程新建一个。",
            )
        return SchemaState(
            SchemaStanding.BEHIND,
            recorded,
            SCHEMA_VERSION,
            f"状态库 schema 版本是 {recorded}，代码期望 {SCHEMA_VERSION}：{where}\n"
            f"{note}\n"
            "先跑一次迁移（它先备份，再只改**证得出来 / 认得出来**的那些行，"
            "认不出来的会点名拒绝）：\n"
            f"    python -m marketops.adapters.sqlite.migrate --db {where}\n"
            "迁移**不自动跑**是刻意的：补什么值需要证据，旧库的去留是人的决定。",
        )
    if workspace_binding(conn) is None and _has_lineage_rows(conn):
        return SchemaState(
            SchemaStanding.UNBOUND,
            recorded,
            SCHEMA_VERSION,
            f"状态库 {where} 里已经有研究产出，却没有工作区绑定"
            "（schema_meta 里没有 workspace）。\n"
            "这个状态由写路径构造不出来（第一行写入就会绑），所以它只可能来自一次篡改"
            "或一次没做完的手工操作。\n"
            "不认领它：那些行属于谁没有答案，而按「当前部署」去读就是把别人的事实读成"
            "自己的。",
        )
    return SchemaState(SchemaStanding.CURRENT, recorded, SCHEMA_VERSION, "")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表并记版本。幂等。

    整份 DDL **每次都重放**，所以「往后加表 / 加索引」是自愈的（``IF NOT EXISTS``）
    —— 这正是「DDL 只增不改」那条规则换来的：加一张表不需要动版本号，也不需要
    一次迁移。改列则不行，那要新建一列 + 回填 + 保留旧列，是一个真正的迁移，
    必须由人决定方向和旧库去留。

    版本**不做**自动迁移：一个比代码新的库不能被旧代码打开（那会让旧代码按
    它不认识的形状读写），所以版本不匹配一律拒绝。比代码旧的库今天也会被拒，而那句
    拒绝**点名命令** —— 「不做自动迁移」不等于「不告诉人怎么做」。

    ## 版本对上了之后还有一条判据

    v3 的库如果**有行**却没有工作区绑定，那它的那些行谁也说不清是谁写的。这个
    状态在写路径上构造不出来（第一行写入就会绑），所以它只可能来自一次篡改或
    一次半途而废的手工操作。它在这里当场拒绝，而不是等到某一次读把它读成
    「这些行是你的」。

    ## 判据与措辞都不在本函数里

    三句拒绝的原文都住在 :func:`read_schema_state` —— 部署前那次自检（``--check``）
    读的是同一个返回值。这一层只做两件本函数才有权做的事：**盖章**（``UNINITIALIZED``
    时把版本写进去）与**抛**。所以「自检说要跑那条命令、真正启动时说要跑另一条」这种
    分家在结构上不可能出现。
    """
    replay_ddl(conn)
    state = read_schema_state(conn, where=_database_file(conn))
    if state.standing is SchemaStanding.UNINITIALIZED:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
        )
        return
    if state.message:
        raise StoreError(state.message)


def replay_ddl(conn: sqlite3.Connection) -> None:
    """只重放建表语句，**不判版本**。

    它是 :func:`ensure_schema` 的前半段，单独存在是因为迁移需要这半段而**不**需要
    后一半：迁移要打开的正是一个版本对不上的库。
    """
    conn.executescript(_DDL)
    conn.executescript(_ROUTE_DDL)
    conn.executescript(_JOB_DDL)
    conn.executescript(_OUTREACH_DDL)
    conn.executescript(_CONTINUATION_DDL)
    conn.executescript(_ATTEMPT_DDL)
    conn.executescript(_RUN_DDL)
    conn.executescript(_PRODUCT_DDL)
    conn.executescript(_TARGET_DDL)


#: ``schema_meta`` 里记版本的键。
SCHEMA_VERSION_KEY = "schema_version"


#: 三张带血统的表。它们各自的「一行」判据不同（evidence 是主键、另外两张是 seq），
#: 所以这里只给表名，键由调用方说。
#:
#: **这一份是冻结在 v1 上的**：``migrate.py`` 拿它回答「当年的库有哪几张表、要给
#: 哪几张补 ``workspace``」。它**不**随版本增长 —— 往后加的表不在 v1 里，迁移也不该
#: 去动它们。要问「现在有哪些表的每一行都写着工作区」，用 :data:`OWNED_TABLES`。
LINEAGE_TABLES: tuple[str, ...] = ("evidence", "contact_route", "route_opportunity")

#: 每一行都**有所有者**的表，也就是 ``db.owned`` 管得着的那些。
#:
#: 「有所有者」不等于「自己带一列 ``workspace``」：``run_fold_ledger`` 的所有者由它
#: 到 ``research_run`` 的外键说全（那一列在那张表上），它照样属于这一份 —— 这个清单
#: 问的是「这张表的行有没有所有者」，不是「它有没有那一列」。见 ``_RUN_DDL`` 里那两张
#: 折入账表的最后一段。
#:
#: 它与 ``LINEAGE_TABLES`` 回答的**不是同一个问题**，所以是两个常量而不是一个：
#: 那个是历史（v1 有哪几张表），这个是现在（哪几张表的行有所有者）。
#: ``ensure_schema`` 对「有行却没绑定」的库当场拒绝，用的就是这一份 —— 少列一张表
#: 等于给那张表开一条「混装之后没人发现」的路，而那条路的表现不是报错，是
#: 两个部署的行被静静地一起返回。
#:
#: ``route_observation`` / ``route_rejection`` / ``account_cohort`` 是**后补进来的**
#: 三张：它们的行也都写着 ``workspace``，而它们在 v5 与 §B12 落地时漏了列进这张表。
#: 漏的后果不是「读出错行」—— ``owned`` 那道判据在每一行上照样成立 —— 而是**一个只
#: 有这三张表里有行、却没有绑定的库不会被 ``ensure_schema`` 拒绝**：它会一路走到第一
#: 次写入，然后由**那一行自己的** ``workspace`` 把绑定盖上去。一个装着两个部署的行的
#: 库因此能被静静地读成「这些行都是你的」的第一半。``tests/test_route_store*.py`` 与
#: ``tests/test_sqlite_cohorts.py`` 各自钉住自己那一格。
OWNED_TABLES: tuple[str, ...] = (
    "evidence",
    "venue_asset",
    "account_venue_relation",
    "market_account",
    "contact_route",
    "route_opportunity",
    "route_observation",
    "route_rejection",
    "account_cohort",
    "review_case",
    "human_resolution",
    "fetch_attempt",
    #: §B17 的 Run。它的每一行都写着 ``workspace``，所以它属于这一份 —— 漏掉它
    #: 的后果与上面那三张一样：一个只有这张表里有行、却没有工作区绑定的库不会被
    #: ``ensure_schema`` 拒绝。
    "research_run",
    #: §B17 条件 2 的折入账（存在性那一张）。所有者来自它到 ``research_run`` 的外键 ——
    #: 上面那一段说的「不等于自己带一列」讲的就是它。
    "run_fold_ledger",
    #: §B01 的 Offer 与产品线。两张表的每一行都写着 ``workspace``。
    #:
    #: **产品资料（``product_version``）不在这里**，这是刻意的：它没有
    #: ``workspace`` 列 —— 一个获批产品版本可被两个工作区共用（B01 ``:104``），
    #: 所以它不属于任何一个。把它列进来会让 ``_has_lineage_rows`` 去查一张
    #: 没有所有者列的表。
    "offer_version",
    "product_line",
    #: 目标架构收敛这一轮那十五张表**每一张都写着 ``workspace``**，所以十五张全在这份清单里。
    #:
    #: 逐张列而不是按前缀推：这一份清单是一条判据的输入（见 :func:`_has_lineage_rows`），
    #: 而「按命名推出来的清单」在下一个表名不合规律时会静静少一张 —— 少一张的表现是
    #: 「一个只有那张表里有行、却没有工作区绑定的库不会被拒绝」，也就是**不报错**。
    #:
    #: ``effective_scene`` / ``launch_record`` / ``feedback_event`` / ``saved_view`` /
    #: ``watch`` / ``action_item`` / ``schedule_definition`` / ``schedule_fire`` /
    #: ``notification_event`` / ``notification_subscription`` / ``delivery_receipt`` /
    #: ``session_binding`` 与 ``workstream`` / ``experience_candidate`` /
    #: ``policy_patch``。**十五张全在**：这一族的每一张都带 ``workspace``，与
    #: ``product_version`` 那条例外不同 —— 没有一张是「可以被两个工作区共用」的全局事实。
    "workstream",
    "launch_record",
    "effective_scene",
    "feedback_event",
    "experience_candidate",
    "policy_patch",
    "saved_view",
    "watch",
    "action_item",
    "schedule_definition",
    "schedule_fire",
    "notification_event",
    "notification_subscription",
    "delivery_receipt",
    "session_binding",
)


def _has_lineage_rows(conn: sqlite3.Connection) -> bool:
    """库里有没有**带所有者**的行。问 :data:`OWNED_TABLES`，不问 v1 的那一份。"""
    for table in OWNED_TABLES:
        row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        if row is not None:
            return True
    return False


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """写一格 ``schema_meta``。**不自己开事务** —— 调用方决定它和谁在同一个事务里。"""
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def owned(
    payload: dict,
    *,
    where: str,
    bound: str | None,
    error: type[MarketOpsError] = StoreError,
) -> dict:
    """这一行属不属于这个库。不属于 → 抛 ``error``（**不是**跳过）。

    Args:
        payload: 已经解出来的行。
        where: 出问题时报出去时给这一行起的名字（表 + 主键/序号）—— 一句话要能让
            人直接去查那一行，所以两半都在。
        bound: 这个库的部署绑定（``schema_meta`` 的 ``workspace``）。``None`` 表示
            没绑过。
        error: 抛哪一个异常。**这一格是承重的，不是可调项**：证据那一侧的调用方
            按 ``EvidenceError`` 处理（``ports/store.py`` 的契约），而这里是那个
            契约的落点；换一个异常类型会改变调用方的分支，而两条分支的处置不同。
            判词（下面那两段话）只有一份 —— 那才是这个函数被提上来的理由。

    ``bound is None`` 而这一行存在，是一个**构造不出来**的状态：写路径第一行就绑，
    迁移也绑；``ensure_schema`` 对「有行无绑定」的库当场拒绝。所以走到这里说明有人
    手工改过库。此时**不能**把这一行读成「这个库的」——那正是把别人的事实读成自己的。

    ## 为什么它住在 ``db.py`` 而不是某一个 store 里

    它原来叫 ``evidence_store._owned``，是那个模块的私有函数。第二个要读带血统的表
    的端口（``ports/pool.py``）出现的那一天，它就有了两个调用点 —— 两处各写一遍这段
    判据，是「两处措辞会漂开、而漂开的那一处是没人读的那一处」的又一个实例。它读的
    是 ``payload`` 里的 ``workspace`` 与库绑定这两件事，两件都是 ``db`` 层的概念，
    所以它搬到这里，而两个 store 都从这同一个函数说话。
    """
    row_workspace = payload.get("workspace")
    if bound is None:
        raise error(
            f"{where} 存在，但这个库没有工作区绑定（schema_meta 里没有 workspace）。\n"
            "写路径构造不出这个状态（第一行写入就会绑），所以它只可能来自一次手工修改。\n"
            "这一行属于谁没有答案，而按「当前部署」去读就是把它读成自己的。"
        )
    if row_workspace != bound:
        raise error(
            f"{where} 写着 workspace={row_workspace!r}，而这个库绑在 {bound!r} 上。\n"
            "这一行不属于本库的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )
    return payload


def workspace_binding(conn: sqlite3.Connection) -> str | None:
    """这个库绑在哪个工作区。没绑过返回 ``None``（**不是**「当前部署」）。"""
    return get_meta(conn, WORKSPACE_META_KEY)


def bind_workspace(
    conn: sqlite3.Connection,
    workspace: str,
    *,
    error: type[MarketOpsError] = EvidenceError,
) -> None:
    """把库绑到 ``workspace``；已经绑了别的就拒绝。**必须在写事务里调用。**

    ## 为什么一个库只能绑一个工作区

    绑定回答的问题是「按 ``account_id`` 取到的这一行是谁的」。一个库同时装着两个
    部署的产出时，那个问题没有答案 —— 而它的表现不是报错：``list_evidence("kent-004")``
    会把两个部署关于同一个账号的行**混在一起**返回，报告上看起来只是「证据更多了」。
    所以第二个工作区的写入在这里被拒，而不是被接受之后再靠读的人分辨。

    ## 为什么绑定可以由**第一行写入**建立

    因为那一行的 ``workspace`` 是必填、无默认值的（``Evidence.workspace``）：落到
    这里的每一个值都是写入方**当场说清**的，不是从配置里推的。绑定记下的是这个
    事实，而不是一条新的判断。

    已知边界（写在这里，因为它是真的）：一个空库被第一个写入绑到 ``A`` 之后，``B``
    就写不进去了。要换，得换一个库 —— 那不是限制，是「这个库是谁的状态」这件事
    本来就该有一个答案。

    拒绝用 ``EvidenceError`` 而不是 ``StoreError``：这两类失败在调用点去向不同 ——
    ``StoreError`` 是「存储坏了 / 版本不对 / 库不可用」（处置是找运维），
    ``EvidenceError`` 是「这一行不能这么读或这么写」（处置是看这一行）。绑定冲突
    属于后者：库是好的，是这一行不属于它。三个 ``EvidenceStore`` 实现在这一点上
    必须一致（判据在 ``tests/test_evidence_store_contract.py`` 上对三个实现一起跑）。

    ## ``error`` 参数：判词一份，类型归主语

    与 :func:`owned` 上同名的那一格逐字同一条理由，而且是同一个失败的另一半 ——
    那边管**读**一行不属于本库的行，这边管**写**。两个端口现在都要这条判据：
    证据那一侧的调用方按 ``EvidenceError`` 分支，池子那一侧（``ports/pool.py``）
    按 ``StoreError`` 分支，而**判词只有一份**（下面那三句话）—— 两处各写一遍，
    漂开的那一处不会有人读。

    缺省仍是 ``EvidenceError``：四个已有调用点都在证据那一侧，而缺省值换了会让
    它们的 ``except`` 静默地接不住 —— 那不是「统一类型」，是把一条契约改掉。
    """
    bound = workspace_binding(conn)
    if bound is None:
        set_meta(conn, WORKSPACE_META_KEY, workspace)
        return
    if bound != workspace:
        raise error(
            f"这个状态库已经绑在工作区 {bound!r} 上，本次写入的对象属于 "
            f"{workspace!r}。\n"
            "一个库只装一个工作区的产出：混装之后「按账号取到的这一行是谁的」没有答案，"
            "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
            "要用另一个工作区，换一个库（或先把这个库移开）。"
        )
