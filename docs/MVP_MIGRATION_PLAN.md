# MVP 目录迁移方案

```yaml
status:   Phase A 交付物 2/2
base:     5af2425 = origin/main (9a4fe4e) + origin/docs/mvp-required-integrations-20260922 (FF)
branch:   marketops/mvp
```

**目标不是「看起来架构很高级」。** 目标是：新工程师 10 分钟看懂、Claude Code 能通过 MCP 调它、
Quay 真正执行、Serper/Crawl4AI/Jev/Laya 真正工作、证据可靠、并发不丢状态、重启不丢工作。

---

## 0. 起点裁决（三个候选，选了第三个）

| 候选 | 判决 | 理由 |
|---|---|---|
| `src/marketops` | **不用** | origin 指向**已删除**的 `marketops-research-pipeline`；停在 `2ebeffc`。grep 它会自信地答错 |
| `marketops/v1-serper-discovery`（22 提交） | **不作为基线，作为抢救源** | 是 main 之前的实现工作，**不含** MCP/SQLite/Jev/Laya/ports 任何一样 |
| **`origin/main` + `docs/mvp-required-integrations-20260922`** | ✅ **采用** | 后者是 main 的**严格后代**（FF，+6 提交，5 个文件），把六项集成从「可选」收紧为「必做+必实测」，并给出验收包 A–E |

**为什么 FF 那份 docs 分支**：它动的 5 个文件里有 4 个正是本任务要求更新的
（`README.md` / `docs/ARCHITECTURE.md` / `docs/DEPLOYMENT.md` / `docs/MCP_SERVICE.md`）。
不建立在它之上，文档必然冲突。它是 FF（零冲突风险）、完全可回退、只在特性分支上、**main 不动**。

---

## 1. 目标树（§2）

```text
marketops/
  domain/          纯领域：实体、值对象、规则。禁止 import httpx/crawl4ai/jev/laya/mcp/sqlite3/飞书
  application/     用例编排：job 生命周期、预算、发现、定级流程
  ports/           接口：SearchPort AcquisitionPort DecisionPort DocumentPort StorePort
  adapters/        实现：serper / http / crawl4ai / pypdf / jev / laya / sqlite / jsonl / feishu
  interfaces/      入口：mcp/（control + research 两套目录）、cli.py
  config/          设置与环境（唯一读 os.environ 的地方）
  security/        权限与合规边界
profiles/<scene-id>/{scene,research,grading,role-matrix,freshness,writing}.yaml + skill.md
contracts/         契约 JSON（**单向：代码 → 契约**，不可手写）
docs/  deploy/  scripts/  tests/{unit,contract,integration,e2e,fixtures}
```

---

## 2. 迁移手法：包一个一个搬，**绝不一次搬完**

§21 逐字要求「不要一次搬完整个 repo 最后再修 import」。手法固定：

1. **每个提交搬一个包**，同提交内改完所有 import 与对应测试。
2. `git mv` 保留历史。**不用 `git add .`**，逐文件 `git add`。
3. **绝不保留两份真实实现。** 只在「本阶段还有阶段外的调用方」时留
   `marketops/<旧名>.py` 的**纯转发**（`from marketops.domain.x import *`），
   每个转发文件头写明**删除条件**，并在该调用方迁完的同一个提交里删掉。
4. 每个提交后：`pytest` 全绿 + `ruff check .` 干净 + 契约重生成与 committed 逐字节一致。
5. **测试目录的 unit/contract/integration/e2e 拆分放最后**（Phase I），
   因为它对能力零贡献而 churn 最大；中途只保证测试随代码一起搬。

### 搬运顺序（每步可独立回退）

| 步 | 动作 | 破坏半径 |
|---|---|---|
| B1 ✅ | `domain/`：`enums` `facts` `util` `errors` `entities/*` `evidence/models` | 已落地，见下 |
| B2 ✅ | **切掉 `grading/engine.py → profiles.py → config.py` 这条传递链** | 已落地，见下 |
| B3 ✅ | `ports/`：`search/base` `acquisition/base` `documents/base`（**`decisions.py` / `store.py` 延后**，见下） | 已落地，见下 |
| B4 ✅ | `adapters/`：`serper` `http` `crawl4ai_fetcher` `text_pdf` `mineru_adapter` `document_router` `classify` `feishu/*`（**`jev` / `laya` 延后**，见下） | 已落地，见下 |
| B5 ✅ | `adapters/sqlite/`：**SQLite 主存**（账本 + 证据）+ `ports/store.py` 提取（见下） | 已落地，见下 |
| B6a ✅ | `application/`：`pipeline` `metrics` `cohorts` + `queries` `escalation`（三个过渡包消失）；`profiles.py` → `adapters/profile_loader.py` | 已落地，见下 |
| B6b ✅ | 预算收口：`ports/budget.py` + `application/budget.py`（**`jobs.py` 不写**，理由见 B6a 节） | 已落地，见下 |
| B7a ✅ | `interfaces/`：`cli.py`（搬）+ `composition.py`（新）+ 组合根判据；`document_router` 的策略与接线分家 | 已落地，见下 |
| B7b ✅ | `interfaces/mcp/`（**新写**，属 Phase C）—— 落地时被搬到了 `adapters/mcp_server/`，见下 | 已落地，见下 |

### B1 实际怎么落的（2026-09-22）

两处**偏离上表的写法**，都是刻意的：

1. **`domain/` 是扁平的，没有子包。** 上表写的是 `entities/models`、`evidence/models`，
   落地成 `domain/entities.py`、`domain/evidence.py`。理由是相对 import 的层级：
   子包会让 `domain/entities/models.py` 里每一处 import 都变成 `...`，
   而扁平之后领域层内部**一律是 `from .X import`** —— 少一个可以数错的层级。
   文件数还不到需要再分层的规模。

2. **`util.py` 被拆开了。** 它原本是纯函数与文件助手的混合体，其中
   `sha256_file` 要 `pathlib`、要打开文件。只要它在，领域层就无法被无磁盘地测试。
   它搬去了 `marketops/adapters/hashing.py`。

`tests/test_domain_is_pure.py` 因此收紧成**两条**判据，其中第二条从
「除白名单外都不许」变成「**什么都不许**」：

```
>>> python -c "import marketops.domain.entities, marketops.domain.temporal, \
               marketops.domain.bind, sys; \
               print([m in sys.modules for m in ('yaml','marketops.config','marketops.profiles')])"
[False, False, False]
```

顺带清掉了三处各自漂移的 `_Strict`（`entities` / `evidence` / `temporal` 各一份，
`model_config` 完全相同）—— 合成 `marketops/domain/base.py` 里的 `StrictModel`。

**B1 没有解决 B2 的问题。** `grading/engine.py` 仍然 import `profiles.py`，
后者仍然 import `yaml` 与 `config.py`：

```
>>> python -c "import marketops.grading.engine, sys; \
               print([m in sys.modules for m in ('yaml','marketops.config','marketops.profiles')])"
[True, True, True]
```

「纯判定只在单文件内成立」这件事要等 B2 才结束。

### B2 实际怎么落的（2026-09-22）

新增 `marketops/domain/profile.py`，装的是**规则值**：`GradingRule`、`AccountRule`、
`DeclaredGrade`、`DeclaredGrading`、`DeclaredRoleGroup`、`DeclaredRoleMatrix`、
`DeclaredProfile`、`ProfileBundle`。`marketops/profiles.py` 只剩装载器：找目录、
读 YAML、把读出来的东西校验成规则值。分界线是——

> **「值是什么形状」在域里；「值是否合法」留在装载器。**

所以 `ConfigError`、`_load_yaml`、`_require_schema`、`_as_list`、`_as_enum`、
`_requires`、以及 `role-matrix` 的词表无歧义校验（`_check_group_disjointness`）
一个都没搬。它们是**读了磁盘之后才能回答**的问题，域层没有资格回答，
也不该有能力回答。

三处值得记的：

1. **`ProfileBundle.profile_dir` 被删掉了。** 这个字段的全部写入者是装载器
   （真实目录）和 `tests/test_grading.py` 的合成 bundle（`Path(".")` —— 一个没有
   任何含义的占位），全部读取者是 `tests/test_profiles.py` 的一行
   `bundle.profile_dir.is_dir()`。也就是说：**它的真实作用是让纯规则层 import
   `pathlib`**。目录是装载器的输入（`Settings.profile_dir()`），不是规则的一部分。
   那一行测试改成了断言**内容**（`bundle.profile.name` 来自 profile.yaml 的 `name`
   键，而不是 `settings.profile` 的兜底值）；目录是否存在本来就由
   `test_profile_files_are_all_present` 与 `test_missing_profile_directory_raises`
   两条覆盖。

2. **`_as_text` 只留一份。** 装载器里 25 处调用，`DeclaredProfile.exclusion_ids`
   里 1 处。实现搬进域层成为公开的 `as_text`，装载器用 `_as_text = as_text` 保留
   内部拼写。两份实现的下场不是「有一份是旧的」，而是「engine 用一份、装载器用
   另一份」，而两者在 `frozen=True` 下连 `==` 都还是 `True` —— 类型检查会过、
   测试会过，只有 `isinstance` 会假。

3. **`profiles.py` 保留了对这些类型的转发**（§2 的 thin re-export）。它仍是那些
   名字的公开入口，调用方不必知道它们住在哪一层。`orchestration/pipeline.py`、
   `search/queries.py`、`services/worker/cli.py`、`tests/conftest.py`、
   `tests/test_pipeline.py` 因此**没有改 import** —— 它们不是纯判定层，
   而 `tests/test_grading.py` 改了：它合成 bundle 时应当从**引擎真正消费的那个类型**
   取，而不是从装载器的转发取。

验证：

```
>>> python -c "import marketops.grading.engine, sys; \
               print([m in sys.modules for m in ('yaml','marketops.config','marketops.profiles')])"
[False, False, False]
```

404 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed 逐字节一致。
（基线 402 是因为两个**按文件参数化**的治理扫描器把新文件也纳入了扫描范围。）

### B3 实际怎么落的（2026-09-22）

`marketops/{search,acquisition,documents}/base.py` → `marketops/ports/{search,acquisition,documents}.py`，
`git mv` 保历史，同提交内改完 17 处 import（含 3 个包的 `__init__.py`、`orchestration/pipeline.py`、
两个测试文件），旧路径**不留转发**。三个模块的 relative import 一个都不用改 ——
`ports/` 与它们原先所在的包同深度。

#### 偏离一：`decisions.py` 与 `store.py` 不在 B3 落地

上表把这两个新端口排进 B3。**这是错的**，理由是判据本身：

> **端口在第二个实现出现时才提取。**

`ports/` 里这三个都满足 —— `search` 有 `serper` + `fake`，`acquisition` 有 `http` +
`crawl4ai_fetcher`，`documents` 有 `text_pdf` + `mineru_adapter`。它们的**方法签名因此是实测过的**，
不是推测的；而 `DecisionProvider`（Jev + Laya）与 `StorePort`（JSONL + SQLite）的两个实现
分别要到 Phase F 与 B5 才存在。

在没有第二个实现时写下端口，产出的不是「先行设计」，是一份**看起来已经设计完了**的猜测 ——
它会先被 review 通过、被引用、被写进文档，然后在第一个真实实现落地时按那个实现的形状重写一遍。
这与 §20「不得把『包能 import』说成『集成完成』」是同一种失真的两种写法。

→ `ports/store.py` 跟 **B5** 一起落地（SQLite 是第二个 store 实现，第一个是现有的 JSONL
`EvidenceStore`）；`ports/decisions.py` 跟 **Phase F** 一起落地（Jev + Laya 同时进）。

#### 偏离二：三个实现包的 `__init__.py` 不再转发端口类型

它们原本 re-export `SearchProvider` / `FetchResult` / `DocumentParser` 等。实测**零消费者**
（全仓只有 `from marketops.acquisition import classify`，是个子模块）。留着它的代价是：
业务层可以写 `from marketops.search import SearchProvider` —— 从一个**厂商命名**的门走进端口，
而 `marketops/search/` 接下来就要变成 `adapters/`。端口类型现在只有 `marketops.ports.*`
一个 import 路径。（这与 B2 保留 `profiles.py` 的转发不矛盾：那里的转发对象是**装载**这个
概念本身，不是某个实现。）

#### 新增执行器：`tests/test_ports_are_contracts_only.py`

B1 的教训是「一条边界没有执行器就只是自觉」，所以 B3 自带它的。三条判据 +
四个正对照（两个判据各一正一反）：

1. 端口不得触碰厂商（网络 / 浏览器 / 协议 SDK / 存储 / 决策 provider）；
2. 端口在仓库内只准依赖 `marketops.domain` 与 `marketops.ports`；
3. `ports/` 不为空且不缩水（防扫描失效）。

`FORBIDDEN_MODULES` 与领域层那份**刻意不同**：端口可以带 `pathlib`（`FetchResult.body_path`
描述的是「产物落在哪里」），领域层不可以。两份合并会把一条真实的边界抹平。

**这条测试还没覆盖 §2 的完整约束。** 「除 `adapters/` 之外谁也不许 import 厂商模块」
今天不成立 —— 厂商实现还住在 `marketops/search` / `acquisition` / `documents` 里，
现在写成测试只会钉住当前的过渡布局。它跟着 **B4** 落地。这一句写在新测试的 docstring 里，
不是只写在本文里。

#### 一处**不改**的：`docs/CURRENT_STATE.md` 的 `file:line`

它引用了 `documents/base.py:44` / `acquisition/base.py:75` —— B3 之后这些路径不再存在。
**保持原样是刻意的**：那份文件自带 `revision:` 头，`file:line` 只在指明的 revision 下有意义。
去「修」它的行号会把一份可复核的盘点变成一份无法复核的叙述。

417 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed 逐字节一致、
`services/` 四个包逐一 import 通过。

---

### B4 实际怎么落的（2026-09-22）

十个文件 `git mv`（保历史）：上表列的五个（`serper` `http` `crawl4ai_fetcher` `text_pdf`
`router`）+ 上表**漏掉**的五个 —— `mineru_adapter.py`、`classify.py`，以及
`integrations/feishu/` 的三个文件。后五个是真在跑的 adapter，只是当初排表时按「端口」
的思路数了端口，没按「谁碰了外界」数实现。
`documents/__init__.py` 与 `integrations/__init__.py` 删除后两个包整个消失 ——
**不是「空目录留着」，是包没了**，因为留着空包会让「文档层」这个已经不存在的层级
在目录树上继续声明自己存在。

重命名两处：`router.py` → `document_router.py`（``router`` 太泛，搬进一个以后还要收
决策 / 存储 adapter 的包里，名字必须自己说清它路由的是什么）；`classify.py` 名字不变。

#### 偏离一：`jev` / `laya` 不在 B4 落地

上表把「新 `jev` `laya`」排进 B4。按 B3 已立的同一条判据 —— **端口在第二个实现出现时
才提取** —— 这两个 adapter 与 `ports/decisions.py` 是**同一件事的两半**：单独搬 adapter
进来，会让 `pipeline` 直接依赖 `adapters/jev.py`，即「业务代码直接依赖决策 provider 的
API」，那正是 §2 要挡的；而今天就写 `ports/decisions.py`，又是在没有第二个实现时猜契约。
→ 两者都在 **Phase F** 一起落地。

#### 偏离二：`classify.py` 进 `adapters/`，不进 `domain/`

它是纯函数，一行 vendor 都不 import，看上去够格进领域层（也是领域层想要的那种东西）。
它进 `adapters/` 的理由是**判据读的是 HTTP 响应才有的形状**（状态码、`content-type`、
正文长度），而它的唯一消费者是 `http` 与 `crawl4ai_fetcher`。放进 `domain/`，领域层
就开始为「一个 HTTP 响应长什么样」积累指纹 —— 两张测试**依然全绿**，而「domain =
业务事实」这个模型已经失效。这是「改完之后没有任何执行器会响」的典型，写在新测试的
docstring 里。

#### 新增执行器：`tests/test_adapters_hold_the_vendors.py`

B3 那条测试自己写明「除 `adapters/` 之外谁也不许 import 厂商模块这句话此刻还不成立，
它跟着 B4 落地」——本提交就是 B4，所以这句话现在有执行器了。三条判据：

1. 厂商模块只准从 `marketops/adapters/` 里 import（扫 `marketops/` + `services/`）；
2. `VENDOR_MODULES` 覆盖 `adapters/` 里**真在用**的厂商（反缩水对照）；
3. 扫描范围不为空（`marketops/` + `services/` 下 ≥ 40 个模块、`adapters/` 下 ≥ 8 个）。

与端口那条判据的清单**刻意不同**，这次是反向的：`pydantic` / `yaml` / `bs4` **不是**
厂商。端口层禁 `yaml`（端口不得做 I/O），本层放行 `yaml`（`profiles.py` 是装载器，
`orchestration/cohorts.py` 读 YAML；它是**文件格式**，不是外部提供者）。两条判据的
清单合并会让其中一条失去意义 —— 与 B3 记录的 `pathlib` 那处不对称是同一个道理。

`tests/` **不在扫描范围内**：本测试问的是**业务代码**的边界，而测试需要能构造替身与
本地服务器。覆盖 `tests/` 的是 `test_no_evasion.py`，那是另一条判据（不得引入规避能力）。

**这条测试当时还没覆盖「组合根」规则**：「只有 `interfaces/` 与 adapters 自己可以点名
某个具体厂商 adapter」。当时的组合根是 `services/worker/cli.py`（它 `from
marketops.adapters.serper import SerperSearchProvider` —— 那是对的），但没有
`interfaces/` 这一层可作依据，写测试只会钉住过渡布局。**它已在 B7a-3 落地**，
落法与两处当时没预料到的地方见下面的 B7a 节。

#### 顺带改掉的三处陈旧路径

`docs/SOURCES.md` 的 `MKT-0002` 落点表、`services/feishu_bot/README.md` 三处、
`tests/test_no_evasion.py` 的扫描见证（`assert "marketops/acquisition/crawl4ai_fetcher.py"
in relative` —— 这是**执行器**，不改就红）。`marketops/{search,acquisition}/__init__.py`
的 docstring 与 `marketops/ports/{search,acquisition,documents}.py` 的「之后搬到
`adapters/`」全部改写成现在时，并写明两个残留包的**删除条件**。

#### 一处**不改**的：`docs/CURRENT_STATE.md:273`

它还写着 `search.serper` / `acquisition.http` / `acquisition.crawl4ai_fetcher`。
与 B3 记录的那处同理：该文件自带 `revision:` 头，路径只在指明的 revision 下有意义。
（`search.serper` 原本说的是「唯一导入者是 `cli.py`」这个**结构判断**，那个判断今天
依然成立，只是名字变了。）

#### 一个未决项：`yaml` 与「adapter 的定义」

`marketops/adapters/__init__.py` 写的判据是「把它 import 进来会不会需要磁盘、网络或
一个进程」。按这条，`profiles.py`（读 `profile.yaml`）**是** adapter。B2 刻意让它留在
顶层，理由是它是**装载器**这个概念的公开入口，而不是某个厂商的实现。两句话今天并存，
没有执行器，也就没有冲突可查 —— B6 搬 `application/` 时应当给它一个明确的落点
（`adapters/yaml_profile_loader.py`，或把这条判据改写成「触外部**系统**」并写明理由）。

423 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed 逐字节一致、
`marketops/adapters/*` 与两个残留包逐一 import 通过。

---

### B5 实际怎么落的（2026-09-22）

两个提交，因为它做的是两件可以分开验证的事：`5e59557`（账本与 `db.py`），以及
「提取端口 + 换主存」这一次。

#### 上表那一行说得不准：**账本在这条分支上没有东西可替换**

`adapters/sqlite/ledger.py` 是 `SqliteBudgetLedger`，它替换的那个「JSON 文件 +
``used += 1``」的账本住在**发现期分支**上（`v1-serper-discovery` 的 `search/session.py`），
这条检出根本没有它。所以这里的账本不是替换，是**新写** —— 而它此刻**没有任何消费者**。

这条必须写下来，否则它变成一份没有执行器的机制：它的第一个消费者是 **B6** 的
`application/budget.py`（把 §8 的「原子预算预留」接到 provider 调用点上）。
**删除条件：B6 落地时若 `application/budget.py` 没有出现，`sqlite/ledger.py` 与
`tests/test_budget_ledger.py` 一起删掉** —— 一个没人用的预算实现比没有预算实现更糟，
它会让「预算是被强制的」这句话在文档上成立、在行为上不成立。

证据存储那一半是真的替换：JSONL 从主存降级为导出与审计通道。

#### 端口是**第三个**实现出现时才提取的，不是第二个

B3 记的是「端口在第二个实现出现时才提取」。`StorePort` 当时被推迟，理由写在那里。
到 B5 为止有三个实现，而且它们是**真的有区别的三个**（并发模型不同、损坏语义不同、
持久性不同），所以 `ports/store.py` 的方法签名是被三种真实形状各压过一遍的。

#### 三个实现现在跑同一份判据

`tests/test_evidence_store_contract.py`：14 条判据 × 3 个实现 + 1 条正对照。
这条测试是 B5 里唯一一处能证明「契约」这个词不是形容词的东西 —— 在此之前，
JSONL 与内存实现的行为对齐靠的是「照着写一遍」，没有任何执行器同时看着它们。

正对照（`test_the_idempotency_criterion_actually_discriminates`）对着一个**刻意做错的**
实现跑同一组写入，断言它得到 3 而不是 1。没有它，`== 1` 只是「某个实现碰巧去重了」。

#### 一处**行为变更**：同一 `evidence_id` 配不同内容，现在报错

`evidence_id` 是内容寻址的，所以「同 id 不同内容」只可能意味着 id 被手工指定过。
三个实现现在都在这一点上显式失败：JSONL 新增 `_reject_conflicting_payload`，
内存实现从「先写的赢，静默」改成抛 `EvidenceError`。**静默保留先写的那条**会让
「证据写进去了」与「我以为写进去的这条证据」变成两件事，而报告只显示「证据更多了」。

#### 组合根换到了主存

`services/worker/cli.py` 的 `PipelineDeps.store` 从 `LocalEvidenceStore(settings)`
换成 `SqliteEvidenceStore(settings.database_path)`。`tests/test_pipeline.py` 一起换 ——
那一层是这份检出上最接近集成测试的东西，让它跑 JSONL 会通过，而**通过的路径在生产里
不存在**。

`Settings.database_path`（`state/marketops.db`）是**一个文件**，账本与证据共用它。
`tests/test_sqlite_evidence_store.py::test_the_ledger_and_the_store_share_one_database_file`
钉住的风险点是 `ensure_schema` 每次都重放整份 DDL —— 后进场的那个不能把先进场的表
清掉。DDL 只增不改换来的好处也在这里：加三张表**没有**动 `SCHEMA_VERSION`。

#### 一个**不改**的洞，以及为什么不改

`adapters/jsonl/store.py::_path_for` 的 `slugify` 不是单射：`Foo` 与 `foo` 会静默写进
同一个 `.jsonl`（§3 的必摘项 `52aba2a`）。B5 **没有**修它，理由是两条：

1. 它**不再是主存的洞** —— SQLite 按 `account_id` 原样做键，不折叠；
2. 修法已经写下来了，而且**不该落在这里**：只堵 `_path_for` 只保护这一个实现，而
   `Evidence.account_id` 今天连 `AccountRecord` 有的 `check_slug_safe` 都没有 ——
   三个实现看到的 id 空间本来就不同。`check_canonical_id` 应当落在**模型层**。

洞与理由写进了 `_path_for` 的 docstring 和那条测试的 docstring，跟着 Phase D 走 ——
**2026-09-22 结在 C6**（见 §2.5 末），落点就是当时写下的那一处：模型层。

#### 一处**不改**的：`docs/CURRENT_STATE.md`

它第 89 / 103 / 200 / 270 / 315 / 316 行都写着 `evidence/store.py`，B5 之后那个路径
不存在了。与 B3、B4 那两处同理：该文件自带 `revision:` 头，`file:line` 只在指明的
revision 下有意义。改了三处**活的**文档：`docs/SOURCES.md`（MKT-0004 落点）、
`docs/SOP_MAPPING.md`（第 8 行点名主存）、`docs/DATA_MODEL.md` §9（存储布局整节重写，
加上「主存 vs 导出通道」的判据对照表）。

#### 一个未决项：`legacy` 与 `run` 级元数据

`docs/CURRENT_STATE.md:103` 记着 `ResearchRun` **从未被构造**，run 级元数据不落盘。
B5 把三张表放进 `marketops.db` 时**没有**加 `research_run` 表 —— 那需要一个真实生产者，
而今天没有（`RunReport` 是内存对象，落 `benchmarks/<run_id>.json`）。等 Phase G 的
Quay 集成把这个「一次运行」变成一个有身份的东西，再决定它进库还是继续进 benchmarks。

#### 一处**做不到**的：`jsonl/store.py` 的改名没被 git 认出来

移动是用 `git mv` 做的（`marketops/evidence/store.py` → `marketops/adapters/jsonl/store.py`），
但**内容改动太大**：git 的相似度打分是 **49%**，差一个百分点没过默认的 50% 阈值，
所以这次提交的 `--stat` 里它是「新增 + 删除」而不是 rename，`git log --follow` 在这
一个文件上会断。commit message 里写了原路径作为补偿。

知道它却**不**去凑那个百分点，理由是这个顺序：要让纯改名单独成一个提交，
那个提交里 `ports/store.py` 还不存在、四个导入方也还没改指向 —— 也就是一个
**整套测试全红**的中间提交。为了一个显示用的启发式，换一个真的坏掉的提交，
不划算。（另一条路是把改名排在最前面、连带导入深度一起改，那要在提交之间手工回退
六个文件的内容，而这份检出不允许用 `git reset` / `git stash` 收拾残局。）

488 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed 逐字节一致、
`marketops/evidence/` 已从磁盘上消失（`git mv` + `git rm __init__.py`）。

---

### B6a 实际怎么落的（2026-09-22）

**三个过渡包一起消失。** `orchestration/{pipeline,metrics,cohorts}.py` 与
`search/queries.py`、`acquisition/escalation.py` 都进 `application/`，三个目录连同
`__init__.py` 一起从磁盘上消失。`search/fake.py`（`FakeSearchProvider`）**删掉而不是
搬走** —— 零导入者的死代码，`docs/CURRENT_STATE.md:77,100` 两处记过。

#### B4 挂给 B6 的未决项在这里结：`profiles.py` 搬进 `adapters/`

结论是**搬家**（`marketops/adapters/profile_loader.py`），不是把判据改写成
「触外部**系统**」。按 `adapters/__init__.py` 开头那条判据（「把它 import 进来会
不会需要磁盘、网络或一个进程」）它本来就是 adapter —— 它要读 `profiles/<id>/*.yaml`。
两条旁证：§2 的目标树里顶层只有 `config/`；`adapters/jsonl/` 早就立了「读数据文件
→ 产出领域对象」的先例。装载出来的 `ProfileBundle` 仍住 `domain/profile.py`，
所以 `application/` 拿到的是**领域类型**：`pipeline.py` / `queries.py` 的 import
顺势从 `..profiles` 改成 `..domain.profile`，这一层从此不 import 任何 adapter。

#### 三处**只有执行器会告诉你**的连带

1. **契约漂移。** `domain/entities.py` 里 `Role` 的 docstring 会被 pydantic 导出成
   `contracts/role.schema.json` 的 `description` —— 改一句文档注释就足以让
   `tests/test_contracts_sync.py` 变红。这正是那个测试存在的理由；重生成后
   逐字节一致。
2. **文档里的路径引用没有执行器。** 借这次搬迁把全仓 `.md` 里反引号包着的
   `marketops/...` 路径与磁盘对了一遍（`docs/CURRENT_STATE.md` 与本文的历史节除外），
   把 B1–B5 攒下的 rot 一次清完：`README.md` 的目录树整块重写（它还列着 `search/`
   `documents/` `evidence/` `entities/` `orchestration/` `integrations/` 六个已不存在的
   目录）、`docs/SOP_MAPPING.md` 五处 `entities/*.py` → `domain/*.py`、
   `docs/DATA_MODEL.md` 的 `orchestration/metrics.py`、以及
   `docs/PRODUCT_SPEC.md` / `domain/profile.py` / `domain/enums.py` 里的 dotted 路径。
   下一次搬迁前值得把这条**变成测试** —— 现在它仍靠自觉。
3. `marketops/adapters/serper.py` 的报错文案还在教用户「注入 `FakeSearchProvider`」，
   而那个类型是这次刚删的。

#### 一处**不改**的

`docs/CURRENT_STATE.md`（自带 `revision:` 头）与本文 B1–B5 的历史节，理由同前几次：
`file:line` 只在指明的 revision 下有意义。

#### 仍然偏离 §2 目标树的地方（不假装已经对齐）

顶层现在只剩 `config.py`（唯一读 `os.environ` 的模块）、`schema.py`、`grading/`
与四个包。按 §1 的目标树，`schema.py` 与 `grading/` **不在**那份名单里，`config/`
与 `security/` 还是文件而不是包。这些都是 B7 / Phase I 的事，不在这里静默处理：
`schema.py` 是「代码 → 契约」的单向出口，它的落点取决于 `contracts/` 归谁导出；
`grading/engine.py` 是纯判定（只依赖 `domain/`），但它是**策略**不是**事实**，
放进 `domain/` 会让「领域 = 业务事实」这个模型变模糊。**两条都留到 B7 之前给结论。**

（**结论已出，见下面的 B7b 节**：`grading/` 搬进 `application/`；`schema.py` 留顶层。）

488 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed
逐字节一致。这次 git 认出了全部五个改名（`R` 而不是 B5 那种 add+delete）。

#### B6b 的范围，以及**为什么 `jobs.py` 不写**

上表原写「+ 新 `jobs.py` `budget.py`」。`budget.py` 要做 —— B5 落地账本时写下的
**删除条件**是「B6 落地时若 `application/budget.py` 没有出现，`sqlite/ledger.py` 与
`tests/test_budget_ledger.py` 一起删掉」，所以 B6b 就是给 `SqliteBudgetLedger`
接上第一个消费者。

`jobs.py` **不写**：到 Phase G 之前它没有任何消费者，而「一个没有任何调用方的执行
机制」正是本仓反复批判的那种僵死代码（`search/fake.py` 刚刚因为同一个理由被删）。
作业生命周期的形状取决于 Quay 那侧的 `submit_research` / attempt / lease 到底长
什么样，那件事在 Phase G 之前没有事实基础 —— 现在写出来的会是一份按想象编的接口。

### B6b 实际怎么落的（2026-09-22）

分两步，每步一个提交。**第一步只做端口**（`a13fb0c`），第二步才接消费者。

#### B6b-1：端口在第二个实现出现时提取

`BudgetLedger` 从 `adapters/sqlite/ledger.py` 提进 `ports/budget.py`，同一步给出
第二个实现 `adapters/memory.py::InMemoryBudgetLedger`，判据随即拆成两处：
语义（预留占额度、只有显式退款才还、幂等键、科目与 scope 隔离）两个实现都跑
（`tests/test_budget_contract.py`），并发只跑 SQLite 那份
（`tests/test_budget_ledger.py`）。把并发那条也塞进共享契约，就得给内存实现加锁 ——
那不是「补齐契约」，是把测试替身伪装成并发账本。

**id 的推导上提到 `domain/budget.py`。** 若 id 由实现自己造（自增、UUID），
「同一个 `(scope, line, key)` 永远得到同一条预留」这条**幂等承诺**就退化成该实现的
巧合；放上去之后，契约测试里那条「`("a:b", "c")` 与 `("a", "b:c")` 不是同一条」
才在测真的东西。分隔符用 US（U+001F）而不是 `:`，因为三段都可能含 `:`。

端口上**没有** `timeout` / `expire` / `release`：§8 要求超时不得默认释放预算，而
「不存在的入口」比「存在的入口加一条注意事项」可靠。要退款得写出
`refund(..., reason=...)` 并给理由。

#### B6b-2：上限住进 profile，账扣在起飞之前

**上限不来自 `Settings`。** §8 逐字是「the caller cannot silently expand them」，
而「调用方」正是设环境变量的那个人：一个 `MARKETOPS_MAX_QUERIES=1000` 就能扩大上限，
且**不留痕** —— 环境变量不落盘、不进报告、不在 diff 里。所以上限是
`profiles/<id>/profile.yaml` 的 `search.max_queries`（本轮定为 20），版本化、可评审。
`set_ceiling(..., source="profile:<id>:search.max_queries")` 把出处写进账本，
于是「这个上限是谁定的」能从账本本身回答。

profile 没声明这个键时**失败关闭**（`BudgetNotConfiguredError`，报错指出是哪个文件
哪个键），不取缺省值。一个「没写就按 8 次跑」的缺省会让「忘了声明额度」与「故意给
8 次」在行为上不可区分 —— 与「未配置不等于无限」是同一条纪律。声明为 `0` 同样拒绝：
它不能表现为「跑完了、没有候选」。

**scope 是 `<profile>:<account_id>`，不是 run 级。** §14 的「一个 Account = 一个
研究任务」在额度上的形状：若 scope 是 run 级，一个跑疯了的账号会把整批账号的额度
吃光，而报告上只会显示「其余账号没有产出」。代价写在明处：一次运行的总额度 =
每账号上限 × 账号数，本层不假装知道那件事；真需要 run 级总额度时，它是**另一个
scope 上的另一条上限**（账本已支持），那属于 Phase G 的 `submit_research`。

**三段式，异常路径上什么都不做。** `_discover` 里每一次检索是
`reserve → 调用 → spend`；超时、连接中断、provider 报错之后**没有第三个动作**，
预留停在 `RESERVED` 一直扣着额度。所以 `application/budget.py` 里没有
`try` / `except` / `finally` —— 默认行为恰好就是正确行为。判据在
`tests/test_pipeline_budget.py::test_a_failed_query_keeps_holding_its_budget`。

#### 三处**只有执行器会告诉你**的判断

1. **`PipelineDeps.budget` 是必填的，没有缺省。** 它原本打算照 `browser` 的样子
   给个 `None`。不能：没有浏览器会让 `browser_escalations` 停在 0，而这个 0 在一份
   报告里可以追问；没有一个账本则**什么都不显示** —— 「没有账本」与「账本上还有
   大量余额」在任何指标上完全同形。全仓只有两处构造 `PipelineDeps`，所以这一步的
   代价是一行测试夹具，收益是「忘了决定预算」写不出来。
2. **重跑同一个账号会造出一条假账。** 幂等键取查询文本之后，重跑时 `reserve` 拿回的
   是上次那条**已结算**的预留。此时若照常发查询，钱花了而账本不记（那条预留早就是
   `spent`）—— 账本开始**少记**，而少记在本地永远看不出来。所以已结算的查询
   **不重发**，只记一个 `serp_queries_skipped`。反过来静默跳过也不行：那会让
   「检索过、没东西」与「压根没检索」在报告上同形。**两个走法都错，所以跳过必须
   是个能被看见的数字。**
3. **正对照。** `test_the_reservation_is_what_stops_the_run` 里有一条**故意不预留**
   的 pipeline。没有它，「上限 20、实际只发了 20 次」这个观察可能只是查询数恰好
   等于上限 —— 对照组把预留整个拿掉，于是它发出全部查询。

#### 顺手结掉的一处盘上死声明

`profile.yaml` 的 `regions:` 键出现过两次（`:48` 的 `[GB]` 与 `:126` 的四地区），
PyYAML 保留最后一个 ⇒ 第 48 行是**死声明**。删除前用脚本对过 HEAD 与工作树的生效值
（都是 `['England','Scotland','Wales','Northern Ireland']`），删除是行为保真的。
`max_queries` 是这次新增的，只在工作树里。

524 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约未变（`AccountMetrics`
不是 `CONTRACT_MODELS` 成员，所以本次没有契约重生成）。

---

### B7a 实际怎么落的（2026-09-22）

拆成三个**可独立回退**的提交，每个提交之后套件最多红一个 commit：

| 提交 | 内容 |
|---|---|
| B7a-1 | `interfaces/` 落地；`git mv services/worker/cli.py marketops/interfaces/cli.py`；`services/worker/` 退役 |
| B7a-2 | 策略与接线分家：`DocumentRouter` → `application/`，`default_router()` → `interfaces/composition.py` |
| B7a-3 | 组合根判据（传递闭包）+ 两处改写成现在时 |

#### 为什么非搬不可，而不是"整理目录"

`interfaces/` 是 §2 目标树里与另外四层并列的一层。而当时的组合根是
`services/worker/cli.py` —— 一个与 `marketops` **平级、无依赖关系**的目录。
组合根站在包外，B4 记下的那条规则（只有 `interfaces/` 与 adapters 自己可以点名具体
厂商 adapter）就**没有任何判据能约束它**：包内分层的测试扫不到包外的文件。
搬进来之后，"谁可以点名厂商 adapter"从一句自觉变成一句**可以查的话**。

#### 两处当时没预料到的地方

**一、判据的本体是传递闭包，不是直接 import 扫描。** `sqlite/evidence_store.py` 与
`sqlite/ledger.py` 自己一行厂商都不 import，它们 `from .db import ...`，而 `sqlite3`
在 `db.py` 里。只判直接 import 的版本会把这两个判成干净的，而且**不报错** ——
判据以"没有违规"的样子全绿。（这两个模块是账本与主存，说它们"不碰磁盘"是可笑的。）
所以 `_vendor_adapter_closure()` 算不动点，并**留一条见证链**：
`evidence_store → sqlite.db → sqlite3`。判决旁边放着理由，判错时能看出是哪一步算错了。

**二、传递闭包立刻指出一处真错位。** `application/pipeline.py` 有一条
`from ..adapters.document_router import DocumentRouter` —— 按闭包算下去真的通到
`pypdf` 与 MinerU。查下来不是误报：`adapters/document_router.py` 一个文件里住着
两样判据相反的东西（`DocumentRouter` 是纯策略，构造参数全是端口；
`default_router()` 在函数体内 import 两个厂商 adapter）。于是 B7a-2 把它们拆开 ——
分家之后 `application/` `domain/` `ports/` 三个包里**一条 `adapters` import 都没有**。

两个看起来更省事的方案被否掉，理由写在新模块的 docstring 里：
`default_router()` 留在 `adapters/` 再反向 import `application/` 会让方向反掉；
把 `PipelineDeps.router` 的类型改成 `DocumentParser` 会**改判据而不改事实**
（`DocumentRouter` 没有 `can_parse` / `name` / `source_kind`，它不是 `DocumentParser`）。

#### 组合根判据的四条对照

一条"谁都不准点名厂商 adapter"的判据同样永远全绿，也同样是废的，所以要成对地写：

| 对照 | 证明什么 |
|---|---|
| `test_the_criterion_sees_the_composition_roots` | 扫描**看见了东西**：`interfaces/` 之外只有两个点名位置，少了任何一个就说明解析器坏了（假绿） |
| `test_only_composition_roots_name_vendor_adapters` | 判据本体 |
| `test_a_module_outside_the_roots_would_be_caught` | 同一行 `from ..adapters.serper import ...` 落在 `application/` 里**必须**被判红 |
| `test_the_composition_roots_are_allowed_the_same_import` | 同一行落在 `interfaces/` 里**必须**被放行 —— 否则测的是"点名"这个动作，不是"位置" |
| `test_the_closure_finds_what_a_direct_scan_misses` | 闭包**真的在做事**：那两个模块在闭包里、且不在直接集合里 |

**实做过的变异验证**：往 `application/pipeline.py` 里真注入一行
`from ..adapters.serper import SerperSearchProvider`，两条判据当场变红并打印出见证
`marketops.adapters.serper -> httpx`；把文件恢复后 diff 为空。正对照不是纸面上的。

#### 一处**故意不改**的旧路径

`docs/CURRENT_STATE.md:46,260`、`docs/MVP_MIGRATION_PLAN.md:144,345` 里的
`services/worker/cli.py` 保持原样：前者自带 `revision:` 头（路径只在指明的 revision
下有意义），后者是 B 步骤当时的事实记录。改掉它们只会让"当时是什么样"消失。
真正面向使用者的五条 CLI 命令（`README.md`、`docs/POC_PLAN.md`、
`docs/DATA_MODEL.md` 的契约重生成、`services/api/README.md`）全部已改。

531 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约未变。

---

### B7b 实际怎么落的（2026-09-22）：`schema.py` 与 `grading/` 的落点结论

B6a 把这两条挂到 B7，逐字是「**两条都留到 B7 之前给结论**」。结论一个搬、一个留，
两条都不是"省事"的答案：

#### `grading/` → `marketops/application/grading.py`（搬）

§2 的目标树两侧都点到了这件事，而它们指的是**不同的东西**：

```text
domain/       纯领域：实体、值对象、**规则**
application/  用例编排：job 生命周期、预算、发现、**定级流程**
```

**规则**住在 `domain/profile.py` —— `AccountRule` / `GradeRule` / `ProfileBundle`
是规则的**形状**。而 `grading/engine.py` 不做别的，就是把它们**求值** ——
"遇到分支怎么走"，即 application 那一行的**定级流程**。B6a 当时只论证了
"不该进 `domain/`"（它确实是策略不是事实），却没论证该去哪；两个方向都看一遍之后，
"规则"那半句已经有主，而"定级流程"这半句在此之前**没有落点**。

`import marketops.grading.engine` 从外面看像一个与 `domain/` `application/`
`ports/` 并列的**第五层**，而分层模型里没有它的位置。

**收益不是"多了约束"，是答案只有一个。** 今天真正管着这个模块的仍然只有
`tests/test_adapters_hold_the_vendors.py`（扫 `marketops/` 全局，B7a-3 之后连
"点名具体厂商 adapter"也管）。搬进 `application/` **没有新增任何执行器** ——
这一点写在新模块的 docstring 里，免得下一个人把"换了位置"读成"加了约束"。

代价写在明处：`marketops.application.grading` 不再与 `grading.yaml` 的
`schema: marketops/grading.v2` 逐字对应。那一个是**契约标识符**（写进 profile、
会被下游引用），路径改它不变，也不需要变。

**一遍搬完的东西**（都不是"顺手"，是搬家的必然连带）：
`application/pipeline.py` 的 import；四处测试 import；两处**正对照的靶子**
（`test_ports_are_contracts_only.py` 的相对 import 探针、`test_adapters_hold_the_vendors.py`
的靶子路径）—— 靶子必须真实存在，所以它们**必须**跟着改；`docs/SOURCES.md` 的
`MKT-0005` 载体表与 `docs/SOP_MAPPING.md` 的第 7 行（这两处是**当前状态**的映射，
不是历史）；`README.md` 的目录树。

三处**历史陈述**（`domain/__init__.py`、`domain/profile.py`、
`adapters/profile_loader.py` 里记 B2 那次拆链的段落）保留 `grading/engine.py`
旧名 —— 它们描述的是"在此之前"，改名反而会让那段历史不成立；各补一句
「今天的 `application/grading.py`」，免得照着 grep 的人找不到。

#### `schema.py` 留顶层（留，并写下理由）

它**不属于任何一层**，三条理由：

1. **它不是入口。** `interfaces/` 是 MarketOps 的**运行时**表面；本模块的消费者是
   **构建期**（开发者与 CI，提交之前跑一次）。放进入口层，`interfaces/` 就同时意味着
   "产品表面"与"构建步骤"，而这两件事的唯一共同点是"可以被调用"。
2. **它连 adapter 都不是。** 按 `adapters/__init__.py` 开头那条判据：import 它不需要
   磁盘、网络或进程 —— 只有调用 `write_contracts()` 时才写盘，且写到一个显式路径。
3. **它是权威登记表。** `CONTRACT_MODELS` 被 `tests/test_contracts_sync.py` 当作判据
   来源读。让测试从入口层读权威，是把层级读反了。

这是一处**有意保留的偏离**，写进了 `schema.py` 的模块 docstring。`config.py` 同理
（目标树里是 `config/` 一个包，落地是一个模块），两处并列写在那里。

#### 顺手收掉的一处真缺陷：一份操作、两个都发布出去的名字

`schema.py` 的注释自己反对过这件事（说的是契约名：`contact-candidate` 改名
`contact-route` 时"留着它就等于同时存在两个名字，而下游只会用其中一个"）。
命令名有同一个毛病，而且**已经漂了**：`tests/test_contracts_sync.py` 的失败信息
指着 `python -m marketops.schema`，而文档里两条命令并存、读者分不出是不是两件事。

收法不是删功能，是**指明哪一条是规范的**：`python -m marketops.schema` 为规范命令
（就是测试失败信息里那一条），CLI 的 `export-contracts` 是转调同一函数的便利入口。
两处 docstring 与 `docs/DATA_MODEL.md` 都写明这层关系。

#### 一处契约重生成（不是漂移，是预期行为）

`domain/evidence.py` 的 docstring 里那句路径改成了 `marketops/application/grading.py`，
而 pydantic 会把模型 docstring 导出进契约的 `description` ⇒
`contracts/account-record.schema.json` 必须重生成。**一行、纯文字、无语义变化**，
重生成后与模型逐字节一致。

531 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与 committed
逐字节一致。

---

## 2.5 Phase C 实际怎么落的（2026-09-22）：服务面、权限边界、抓取目标判据

上表最后一行写着「`interfaces/mcp/`（**新写**，属 Phase C）」。它落地了，而且
**落在了另一个包里** —— 那是这一步唯一的意外，也是唯一值得先讲的事。

四个提交，按「任何一次 commit 之后套件最多红一个 commit」排：

| 提交 | 内容 | 对应 |
|---|---|---|
| `aaac486` C1 | `marketops/security/`：tokens / catalog / network | §7 §10 |
| `2d63543` C2 | MCP 服务面：`interfaces/mcp/` + `adapters/mcp_server/` + `config.py` 三个配置项 | §6 §7 |
| `ef3544e` C3 | 抓取目标判据接上两条抓取路径 | §10 |
| `ad77da6` C4 | `mint-token` 子命令 | §0 §7 |
| C6 | id → 文件名必须是一一映射，判据与界都只有一处 | §8 |

### 一次搬家：SDK 的 import 落在 `adapters/mcp_server/`

`tests/test_adapters_hold_the_vendors.py` 的 `VENDOR_MODULES` 在 **B4** 就把 `mcp`
列进了厂商清单（提交 `71b5d71`）—— 那时一行 MCP 代码都还没有。它是一条**预先声明**：
写它的人知道 Phase C 会 import 这个 SDK，并预先决定了这算「厂商依赖」。

`interfaces/` 被授予的权力是「点名我们自己的 adapter」（`COMPOSITION_ROOTS`），
不是「import 厂商 SDK」。所以 `interfaces/mcp/` 里 import `mcp` 必然违反那条判据 ——
不是判据写宽了，是**放错了地方**。

**没有造端口。** 三条理由写在 `marketops/adapters/mcp_server/__init__.py`：没有第二个
实现、没有第二条路线、一个只服务单一实现的端口是"为了满足扫描器而存在的接缝"。
拒绝掉的另外两条路也记在那里：re-export 转发层（等于静默废掉那条判据）、给
`interfaces/` 开豁免（那会让**任何**接口文件都能 `import httpx`）。

搬家之后 `interfaces/mcp/` 只剩两件事：读环境变量、把 `adapters.mcp_server` 装出来的
服务跑起来。它点名 `adapters.mcp_server`，与 `interfaces/cli.py` 点名 `adapters.serper`
是同一个动作 —— 组合根的第三个落点，`MUST_BE_VENDOR_ADAPTERS` 与
`test_the_criterion_sees_the_composition_roots` 的期望集合跟着加了一处。

### 三种失败合成一种回答（§7）

没有主体 / 工具不在目录里 / 角色不允许，对外都是与 SDK **逐字相同**的
`Unknown tool: <name>`。刻意不做「拒绝得清楚一点」：只要两句话可区分，一次枚举
就能把不属于自己的目录读出来 —— 差别本身就是泄漏。

判定挂在 `ServerMiddleware` 上而不是工具函数里，两个理由，第二个是决定性的：十四处
判定就有十四处可以漂移；而**顺序**上，中间件跑在参数校验之前，写在工具函数里会让一次
越权调用带非法参数时拿到「字段 X 不合法」，那句话本身泄漏了「这个工具存在」。

「还没实现」是**第四种**，而且必须可区分：目录里十四条、本阶段接线五条，其余九条的回答
带 `phase:` 阶段号。`Unknown tool` 对它们是**错误的事实** —— 它会把排查方向引到"调用方
写错了名字"。顺序也不能反：不属于你的工具先被拒绝，否则研究侧 worker 能靠"这个工具
还没实现"把 control 目录枚举出来。

### `security/` 是一个包，界线按「输入从哪来」划

| 模块 | 回答的问题 | 它**不接受**什么 |
|---|---|---|
| `catalog` | 拿到主体之后怎么判 | 不接受"这次调用想干什么" |
| `tokens` | 主体**从哪来** | 不接受"调用方自称是谁" |
| `network` | 这个地址允不允许去 / 允不允许绑 | 不接受"这次是内部调用" |

`network` 把两个方向放进同一个模块是刻意的：**出**（抓取目标必须是公开地址，
`127.0.0.1` 在这里是**禁止的目标**）与**入**（服务面只允许绑 `127.0.0.1`，同一个地址
在这里是**唯一允许的**值）是同一句话的两面。分成两个文件之后，读的人会以为这是两条
互不相干的偏好。这条判据的输入只有一个字符串，所以它可被单独调用、单独测试。

`tokens` 里盘上存 `sha256(token)` 而不是 token：这个文件可以被备份、被 `cat`、
被误贴进工单而不泄露一个可用凭据。代价是令牌只显示一次 —— 写在明处，不是藏起来。

`AuthorizationError` 独立于 `ConfigError`：配置错误是"部署写错了，应该起不来"，
越权是"服务正常，这次调用不允许"。共用一个类型会让越权尝试在日志里长得像一次配置事故。

### 抓取目标判据：重定向由**我们**跟

`follow_redirects=True` 是这一族保护里最常见的漏洞形态，而且它在研究场景里一点也不
罕见：一个公开站点回 `302 Location: http://169.254.169.254/`，客户端自己跟过去，
业务代码从头到尾只见过那个公开域名。所以关掉自动跟随，自己走一条**每跳都过判据**的
有界循环 —— 被拒绝的那一跳**根本不会被发出**。事后检查 `response.url` 的实现只能
"发现"一次 SSRF，而那时请求已经离开了进程。

顺带收掉两个同源的洞：注入的 `client` 也显式传 `follow_redirects=False`（"注入的
client 恰好也没开"是一次运气，不是一条约束）；相对 `Location` 按**当前这一跳**解析，
不是按最初请求的地址。

### `mint-token`：一条被别的模块点名的命令

`security/tokens.py` 的 `TokenRing.from_file` 在文件缺失时的报错**已经**指名了这条
命令。一条被别的模块点名的命令，如果不存在或者行为与那句话不符，读者会先怀疑自己的
环境 —— 「提到一个名字，那个名字就得真的是它」。所以补的不是功能，是那个名字。

`--out` **没有缺省值**：缺省值只有在"我知道这个部署把凭据放在哪"时才成立，而本仓
不知道。落点在仓库里、或父目录不存在，都是报错 —— 不建目录，因为为一份凭据文件顺手
把目录树造出来，正是"凭据出现在谁都没想到的地方"最常见的成因。

### 仍然偏离 §2 目标树的地方（不假装已经对齐）

* `config/` 是**模块**不是包；`scripts/` 还不存在；`deploy/` 只有一份 README。
  前两条与 `schema.py` 留在顶层是同一个判断（`config.py` 的 docstring 里写着）。
* `tests/{unit,contract,integration,e2e,fixtures}` 拆分仍在 Phase I。
  §2 逐字要求它「放最后」，因为它对能力零贡献而 churn 最大。
* `adapters/jev` / `adapters/laya` / `ports/decisions.py` 仍不存在 —— Phase F。
  （**已结**：Phase F1，2026-09-22，见 §2.8。）

### 记录但**未修**的债（2026-09-22 逐条重核）

Phase C 当时记下的三条，今天**一条都不成立** —— 而它们在这个文件里已经站了三个
阶段：

1. 「`services/feishu_bot/README.md` 指着一个不存在的 `tests/test_feishu_push.py`」：
   逐字读那一行，它是「接线时的顺序建议」的第 1 步（`并加 tests/test_feishu_push.py`），
   是一条**待办**，不是一条引用。同文件里也没有任何一处把它当既有文件点名。
2. 「`docs/DATA_MODEL.md` §9 的存储布局陈旧」：D1（`2ac6ad5`）已经把它改成
   `<scope>/<hash16>.json` 并写明为什么是内容寻址；今天逐行对着代码核过，
   `mirrors/pages/<account>/<hash16>.html` 与 `http.py::_persist` 一致。
3. 「`QueryLedger` 的缺省 `budget=DEFAULT_BUDGET`（8）还在」：B6b（`95c102e`
   「上限住进 profile，检索额度扣在起飞之前」）之后，全仓没有 `QueryLedger`，也没有
   `DEFAULT_BUDGET`。

一条挂在计划里的债会被**当作事实**读：这三条会让下一个工程师去修三个不存在的东西，
而修不动的结论通常是「我的理解有问题」。所以这张清单此后只在**重核过**之后才留在
文件里。

**今天真实存在的债**（每条都指到具体一处）。第 1、2 条已在 §2.16 结掉，第 4 条在 §2.17、
第 3 条在 §2.18，**第 5 条仍成立**：

| # | 债 | 为什么它是一条债 |
|---|---|---|
| 1 | ~~`mcp` 不在 `pyproject.toml` 的任何依赖或 extra 里~~ | **已结**（H3c，2026-09-22，见 §2.16）：`mcp>=2.2,<3` 进了 `dependencies`；两个方向各有一条判据，且 `interfaces/cli.py` 那张探针表此后也被一条判据管着 |
| 2 | ~~`beautifulsoup4` 是**声明**依赖，实际没有一处用它抽正文~~ | **已结**（H3c，2026-09-22，见 §2.16）：声明删了，`interfaces/cli.py` 那句探针从 `bs4` 换成 `mcp`，`adapters/text_pdf.py` 的那句「没有 BeautifulSoup」改指执行器 |
| 3 | ~~`TextPdfParser` 至今没有直接判据~~ | **已结**（§2.18，2026-09-22）：`tests/test_pdf_parser.py` 用手写的**真 PDF 字节**覆盖正文抽取的每一个分支，8 条判据、10 条变异全部打中。它**不**覆盖链接读法（样本是合成的），`extracts_links` 照旧是 `False` |
| 4 | ~~`AccountRecord.country` / `Evidence.country` 的缺省是 `"UK"`（`domain/entities.py:70,91`、`domain/evidence.py:67`）~~ | **已结**（§2.17，2026-09-22）：三处缺省都变成 `None`（= **还没查**），三份契约重生成，那条**设计好会红**的跳线按「换判据，不换代码」拆成 4 条。**顺带修掉这句话本身** —— 原文里的 `Evidence.country` 是一个**不存在**的字段名（第三处是住在 `domain/evidence.py` 里的 `AccountRecord.country`），而行号是对的：一个照这句话去 grep 的人只会得到「不存在」 |
| 5 | ~~`tests/test_sources_snapshot.py` 的上游对照只在有人裸跑套件时才跑~~ | **已结**（§2.19，2026-09-22）：一个环境变量兼两个决定（不变量那半找不到就 FAIL、增强那半找不到就 SKIP），于是规范调用**每一次**都让它 skip。修：上游对照有自己的变量；三条判据改读**对象库**（manifest 的声明在 commit 空间，而它们此前读工作树，会因为别人未提交的 WIP 转红，且报文把读者支去改 provenance）。现在它在本仓规范调用下**真的跑**：`1536 passed / 3 skipped` |

### C6：id → 文件名必须是一一映射（`ef3544e` 之后，见 `git log`）

C5 的债 1 在这里结掉，因为它是 C3 那条路径上的**同一个缺陷的另一半**：C3 管
「允许去哪」，C6 管「写下来的东西是谁的」。

**根因不是某一行写错了，而是同一个 id 空间里有三个界**：检索镜像按 40 切目录、
账号证据按 60 落文件、run 报告按 80 拼路径。三个数各自都说得通，合在一起就是
「按 40 落盘、按 60 读」—— 而这件事在纸面上看不出来。`slugify` 不是单射，
于是两个不同的 id 落进同一个文件，**没有任何一层报错**：台账行数不变、目录内容
变多、报告上只显示「证据更多了」。

三件事一起做，缺一条都堵不住：

| # | 判据 | 落点 |
|---|---|---|
| 1 | 值必须是 `slugify` 的**不动点**（拒绝，不截断） | `domain/util.check_canonical_id` |
| 2 | 界只有一个定义处 | `domain/util.FILENAME_ID_MAX_LENGTH` |
| 3 | 带 `account_id` 的每一个模型都用同一条判据 | 9 个模型 + 2 个端口 |

`check_canonical_id` 落在**模型层**而不是落盘处，这是 B5 当时就写下的结论，理由是
只堵 `_path_for` 只保护 `jsonl` 这一个实现，而 `sqlite` 按 `account_id` 原样做键、
不折叠 —— 两个实现看到的 id 空间本来就不同，修在一处会有第三个实现重新打开它。

顺带发现的**三处此前完全没管的字段**：`Person.account_id` / `Role.account_id` 连
slug-safe 都没有（这一族里唯一「怎么填都行」的两个），`AccountVenueRelation` 把
`account_id` 与 `venue_asset_id` 合在一个 `@field_validator` 里，于是只能取较松的
那一条 —— 而较松的那条放过去的东西，正是「两个账号合并进一个文件」。

执行器是新文件 `tests/test_ids_are_filenames.py`，36 条，其中一条**反向**证明
清单不缩水：它 AST 扫出 `domain/` 与 `ports/` 里所有声明了 `account_id` 的类，
与测试里那张 `CARRIERS` 清单对照，**两个方向的差集都报**。这条测试当场就红了
（它自己找出了 `Person` / `Role` / `AccountVenueRelation`）—— 人维护的清单与代码
对照，漏掉的那一个不是「没问题」，是「不受管」。

`store._path_for` 那段「修法已经写下来了」的 docstring 换成结论；`tests/test_store.py`
那条**自己演示洞**的 `test_account_id_is_slugified_for_filename`（它用 `My Account 001`
和 `my-account-001` 落进同一个文件）换成两条：规范 id 落盘**名字不变**、非规范 id
在**构造模型时**就被拒。反向验证跑过：把 `http.py` 改回 `max_length=40`、
把 `Evidence.account_id` 的校验摘掉，两条判据分别变红。

**状态**：848 passed / 6 skipped / 0 failed。

**状态**：811 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与
committed 逐字节一致、**未 push**。

---

## 2.6 Phase D 实际怎么落的（2026-09-22）：按语义摘三条真缺陷

| 提交 | 摘的是 | 落在新架构的哪里 |
|---|---|---|
| `2ac6ad5` D1 | `76f43ee` —— 镜像名不再带检索词 | `adapters/serper.py::_persist_raw` |
| `35c593c` D2 | `a203128` —— 未确认的事实不决定任何子句 | `application/grading.py::evaluate_requires` / `evaluate_clause` |
| `f2a9e7e` D3 | `52aba2a` —— `_path_for` 读侧也失败关闭 | `adapters/jsonl/store.py::_path_for` |

**「按语义摘」在这里不是一个谨慎的说法，它有实际后果。** 三条里有一条**已经一半
落在了 C6**：`52aba2a` 的两半是「`check_canonical_id` 这个判据」与「`store._path_for`
失败关闭」，而 C6 做 id → 文件名收口时就已经把判据建起来、并堵住了**写**侧。
所以 D3 摘的不是那条提交，是它的**残余**：`list_*` 的入参是裸字符串，不经过模型，
于是 `check_canonical_id` 在读取边界上一次都没跑过。如果照 cherry-pick 的字面做法，
这一条会被记成「C6 已经做过了」。

三条各自的形态：

**D1（泄漏）**。镜像身份 = query 文本的 `sha256[:16]`；`mirrors/serper/<scope>/<hash16>.json`。
按账号（现在叫 scope）分的目录**保留**——抹平它更省事，代价是两份产出混在一棵树上。
同一条提交里还带出第二个缺陷：`slugify("中文查询甲")` 与 `slugify("中文查询乙")`
都 fallback 到 `"unnamed"`，于是「按语义摘」也顺手修掉了这个碰撞。
执行器 `tests/test_mirror_names_do_not_carry_the_query.py`（10 条）遍历整棵树断言
检索词不出现，**同时**断言检索词在文件里面仍然可读——只查「不出现」的判据，
在「什么都没写」时同样是绿的。

**D2（三值求值）**。「未确认」的定义是键缺失**或**值为 `None`；未确认的子句在任何
极性下都不决定（包括取反）；已确认的假优先于未确认（整条合取已定，翻不过来）；
未确认**不短路**，所以 `unchecked` 由事实决定、不由子句顺序决定。
`""` / `[]` / `False` / `0` **仍然是证据**——这条是这一族里最容易写错的一条，
它单独立了一个参数化判据。

`_evaluate` / `_evaluate_clause` 从私有改名成 `evaluate_requires` / `evaluate_clause`
并进 `__all__`：它们现在是判据的**载体**，而判据要能被单独测。

反向验证按 B7 的老办法做：**先做纯重命名**（只改名、不改语义）再跑新判据 →
16 红 / 29 绿，每一条红都对得上它要抓的那个缺陷（其中
`test_a_negated_clause_naming_unknown_fact_does_not_match` 给出 `Grade.D` 而不是
`Grade.X`，正是 `!country in [gb, uk]` 把每一行判成越界的那个形态）。

顺带修掉一处**写得比代码更满**的 docstring：`AccountOutcome` 曾说「非空 `unchecked`
⇒ `status == discovered`」。实测不成立。装载期（`ungradeable` / `unknowns`，
「规则点名了引擎不认识的事实」）与运行期（`unchecked`，「规则点名的事实今天没有值」）
是两件事，现在五处（`evidence.py` / `profile.py` / `enums.py` / `facts.py` /
`profile_loader.py`）都写明了这一点，`EligibilityReason` 的 docstring 里还并了一张
`UNRESOLVED_FACTS` 与 `NO_RULE_FIRED` 的对照表。

**D3（读侧）**。改法比论证短：`_path_for` 用守卫的**返回值**当文件名，
`slugify` 从此完全退出存储层——「文件名就是 id」由构造保证，不靠一句说明。
`ValueError` 折成 `EvidenceError`（调用方按它处置损坏与写入失败，半路冒出来的裸
`ValueError` 会绕过处置）；校验排在 `mkdir` **之前**，被拒的调用在盘上什么也不留。

先跑红确认是洞、不是断言写错：5 条 `DID NOT RAISE`，而该文件旧的 13 条照常绿
—— 正控（规范 id 读写不受影响）成立。`tests/test_ids_are_filenames.py` 的
`slugify` 调用点计数 6 → 5，正好落在 `MIN_SLUGIFY_CALLS`。

**状态**：882 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与
committed 逐字节一致、**未 push**。

### 这一次跑出来的两件事（都不是本阶段引入的）

1. **上游快照对照在环境变量没设时会从 SKIP 变成硬失败。**
   `tests/test_sources_snapshot.py::test_upstream_blob_hashes_match` 的
   `upstream_root` 夹具在 `MARKETOPS_RECORDS_ROOT` 缺省时**自己去找**
   `REPO_ROOT.parents` 下的 `records/information/`，找到就用——于是那条「可选对照」
   实际对着**活着的共享 records 树**跑。本次它红了，offender 是
   `member-d-private`（声明 `85ac620f…`、实际 `04ff0b5c…`）。
   > **下面这句当时的推断是错的，已在 §2.19 改正**：「即上游被重新同步过」。
   > 实测六条条目的声明**全部**等于 `rev-parse <commit>:<upstream_path>`，
   > 真实情形是**别人改过工作树还没提交** —— 那是一条**常态**假红，处置是
   > **什么都别做**，而不是去改 provenance。留在这里是因为它示范了一件事：
   > 一个读错对象的判据，它的报文会把读者指向**另一件**正确的事。
   **不是本次改动引起的**（本次只碰了 `adapters/jsonl/store.py` 与 `tests/test_store.py`，
   与 `sources/` 无关），本阶段不动它。
   但这条本身值得记：按本仓的规范调用（`MARKETOPS_RECORDS_ROOT=` 指一个不存在的路），
   这条对照**永远不跑**；它只在有人裸跑套件时才跑，而那时它红。
   （当时的末句是「『绿是靠没跑它换来的』与『红是因为上游真的动了』是同一个现象的两面」
   —— 后半句错了；真正的一面是**读错了对象**。§2.19 结掉了整条债。）
2. `docs/DATA_MODEL.md` §9 的 `mirrors/serper/<account>--<query>.json` 在 D1 之后
   彻底过时，本次一并改成 `<scope>/<hash16>.json` 并写清为什么是内容寻址。

---

## 2.7 Phase E 实际怎么落的（2026-09-22）：多页抓取是一条成文的有界路

| 提交 | 交付 | 落在新架构的哪里 |
|---|---|---|
| `fb64944` E1 | 站点地图前沿：robots → 站点地图 → profile 的页面优先级 | `application/sitemap_frontier.py` |
| `6149f41` E2a | 有界多页抓取：两道闸、四种停止理由 | `application/site_crawl.py` |
| `a9a3090` E2b | `crawl_site` 接线到服务面 | `adapters/mcp_server/research.py` |
| `27cbc5b` E3 | 链接：端口字段**必填**，逐字记下、不解析 | `ports/documents.py`、`research.py` |

E 之前另有两片（`5dac3aa` 给「正文出自哪条解析路径」补执行器、`a8dc65b` MinerU
fallback 改读「能不能用」而不是「装了没」），它们关掉的是 Phase D 遗留的两条
**能力表与接线互相撒谎**的状态，不在这里另立一节。

**E1 修掉的是 v1 的一个真缺陷，不是搬运。** §3 的「值得摘」表把
`search/crawlplan.py` 记成「robots→sitemap→tier」，读起来像可以整段搬过来；实际
能搬的只有它想解决的问题。v1 的它**只读 robots.txt 里的 `Sitemap:` 行、完全不看
`Disallow`** —— 它把 robots.txt 当站点地图索引用。一次抓一页时勉强说得过去，
多页抓取时不成立：不读 Disallow 的多页抓取就是爬虫。重写的
`parse_robots` / `RobotsPolicy.allows` 是最长匹配、等长 Allow 胜、只应用
`User-agent: *` 那一组；取不到 robots 时两种失败的结论**相反**（404 = 没有规则，
5xx / 超时 / WAF = 按 RFC 9309 §2.3.1.4 假设全站拒绝）。

三片各自的形态：

**E1（去取哪些 URL）**。`SitemapFrontier.plan(seed, limit=, account_id=)` 的判据
顺序写在代码里：`check_target`（安全，在任何 I/O 之前）→ hints 空则拒绝 → robots
→ 站点地图 → 逐条分类。`SkipReason` 是闭集（`BY_ROBOTS` / `OFF_SITE` /
`UNSAFE_TARGET` / `DUPLICATE` / `UNMATCHED` / `BEYOND_LIMIT`），它们**不能**合成
一个「跳过」——「这次的额度不够」与「规则里没有它」处置完全不同。`tier_hits` 保留
**计数为零**的优先级，`truncated` 带理由，`gaps` 带话：**计划为空必须带一句说明**，
否则它与「这个站确实没有可用页面」在报告上同形，而后者是一次断言。

它住在 `application/` 而且**没有走 `ports/`** —— 两条都是判据的结果，不是偏好：
判据是 `adapters/__init__.py` 开头那一句（import 之后要不要磁盘、网络或进程），而
它读网全部经过注入的 `HttpFetcher`，所以它是策略；端口留到第二个实现出现再提取。
37 条判据零网络零磁盘 —— `FakeHttp` 把响应交给**真的** `assess_http_response` 分类、
落盘规则照 `http.py`，所以「200 的 WAF 页会被判成 BLOCKED」测的是真规则。

**E2a（取多少、什么时候停）**。`StopReason` 是闭集（`COMPLETED` / `PAGE_CEILING` /
`WALL_CLOCK` / `NO_TARGETS`）：页数上限时把 `remaining` 当种子再调一次**有用**，
墙钟用尽时该做的是调大预算 —— 报告存在的全部意义就是回答「下一步做什么」。多个种子
的计划去重合并，顺序 = 种子顺序 × 计划内的优先级顺序；**页面那一侧去重**（首页 +
某个具体页面是调用方最常给的形态，而站点地图几乎总会把自己列进去），**前沿那一侧
不去重**（同一主机被 N 条种子点到就有 N 次站点地图读），这个代价写在文件头，因为它
会真的发生。两道闸都在**取字节之前**，墙钟从**调用开始**算（前沿读 robots 与站点
地图同样是网络 I/O）；预算是**软界**，最后一次抓取可以越界，`elapsed_seconds` 把它
说出来。`planned_urls` / `urls` / `remaining` 是一个**分划**，而 `remaining` 由真的
取过几条算出来、不由 `pages` —— 只有这个算法在三种停止理由下都对。

E2a 里那处**更正**值得单记：E1 说「等第二个实现出现再提取端口」，而 E2a 的替身落在
**既有的** `ports.acquisition.HttpFetcher` 上，所以这里没有出现第二个前沿实现，也就
没有 `ports/frontier.py`，`application/__init__.py` 顺手更正了上一片那句预测 ——
提取指的是在它**真正出现的那一层**提取。

**E2b（接线）**。组合根只让**一个** `http` 在流通：`SitemapFrontier` 与
`DocumentAcquisition` 各要一个 `HttpFetcher`，各起一个会让超时、UA、重试策略变成
一条**没人观察的差异**，所以 `default_crawl` 只收抓取入口、前沿从
`acquisition.http` 取 —— 两份在**类型上不可表达**。执行器摆的陷阱正是最容易走错的
那一步（在 `default_crawl` 里再 `default_acquisition(settings)` 一次：跑得起来、
两份也都工作，只是前沿那两次读走了另一个实例）。四个上限住在 handler 里，六条校验
**全部在任何网络 I/O 之前**，判据断言的是抓取器的 `asked` 为空、不是某句话里出现了
哪个词 —— 一次注定取不到东西的调用不该先替调用方花掉一次 robots 读。URL 清单是有界
截断（站点地图可以几千条），而 `planned_count` / `remaining_count` 是**准确总数**，
所以「还有多少没取」仍然答得出来（§11：不得静默截断）。

**E3（链接）**。`ParsedDocument` 新增两个**必填**字段：`links` 与
`extracts_links`。给默认值 `()` 之后，一个漏填的构造点产出的是一份「这一页没有链接」
的文档，而它读起来像结论 —— 「声明了却没人填的字段比没有这个字段更坏」。
`extracts_links` 落在**文档**上而不是解析器类上，是这一片最重要的一处修正：
**回执读不到解析器对象**（`DocumentRouter` 采纳 fallback 时会重建一个
`ParsedDocument`，那一刻手里只有文档）。最初把它当解析器属性读，13 条 crawl/fetch
判据一起转红，而红的原因是**设计错了**、不是代码写错了。

`DocumentLink.url` 是 `href` 的**逐字值**，不解析成绝对地址：解析规则是文档与页面 URL
的**联合函数**（`<base href>` 只有文档知道，页面 URL 只有抓取层知道），而
`parse(path, *, content_type)` 拿不到后面那一半 —— 发出一个对带 `<base>` 的页面
**静默错误**的绝对地址，比不发出地址坏得多（「把一件事做成没有的入口优于有入口 +
一条注意事项」）。三条取舍各有判据：不解析 href、不去重（页眉与页脚各一条就是两条，
合并规则写错会把两个不同的链接并成一个）、跳过没有目标的（`href=""` 指的是文档
自己）。抽取跑在**去过 `<script>` / `<style>`** 的同一份字节上，否则
`document.write('<a href=…>')` 里的地址也会被收成一条链接，而它指向的页面并不存在。

同一条提交**删掉**了 `site_crawl.py` 的 `links_not_collected` gap：链接现在在服务面
采集，`_links_block` 是链接形状的**唯一**来源、三条路共用，`collected` 为假时
`reason` 必非空 —— 那正是「这一页没有链接」与「没人看过这一页」的区别，而它们在计数
上一样。删它的理由：**一条说谎的 gap 比缺一条 gap 坏得多**，留着它，`summary()` 会把
一件已经做完的事报成已知缺口，读 gap 清单的人会去补它。`TextPdfParser` /
`MinerUDocumentParser` 都**明说** `extracts_links = False`：两者其实抽得出来（PDF 的
`/Annots`、MinerU 的 markdown 链接），本构建没抽 —— 没有真实样本校准的抽取会产出一批
没人验证过的地址，而它们会一路走进证据。报告上的空链接因此读成**欠账**，不是一条
站点事实。

**三处「没交付」都各自说出来了**：链接（E3 交付）、持久操作 id（Phase G 的
job/assignment，写在 `note` 里）、空的种子清单。一个安静的缺字段会被读成一条**站点
事实**（「这一页没有链接」），而真相是「本构建没交付这件事」。

**这一片跑了两次「为错误的理由而绿」**，都值得记：

1. E2a 的「去掉去重」变异**第一次存活**：替身里两条种子没有共享的 URL（第二个站的
   站点地图列的是第一个站的 URL，对它是站外），那条判据一直是为错误的理由绿的。
   替身已改成「首页 + 它自己的一个页面」这种真实的重叠形态。同一个替身里还有一条
   同族教训：`happy_site()` 漏了裸种子的响应，而替身对未知 URL 答 404（安静的 200
   会让「忘了摆响应」长得像一次成功的抓取），所以第一条回执一直是 `NOT_FOUND`。
2. E2b 的一条变异**被 pyc 骗过**：把 `started = self._clock()` 从一处挪到另一处
   （净 0 字节）、再用 `cp` 还原，mtime 落在同一秒且尺寸相同 —— Python 的 pyc 失效
   判据正是（整秒 mtime, 尺寸），于是它继续跑变异版的字节码，表现成「这条判据存活」。
   清掉 `__pycache__` 后同一条立刻转红。结论记进了那条判据的 docstring：它**没有**在
   测「前沿那两次读算不算在墙钟里」，那件事由
   `tests/test_site_crawl.py::test_the_budget_covers_the_frontiers_own_reads` 守着。

**状态**：1077 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与
committed 逐字节一致、**未 push**。

---

## 2.8 Phase F 实际怎么落的（2026-09-22）：题面是声明、影子不裁决、调用点是第四块

| 提交 | 交付 | 落在新架构的哪里 |
|---|---|---|
| `e495205` F1 | 决策端口**与它的两个真实实现同一个提交** | `ports/decisions.py`、`adapters/{laya,jev}.py` |
| `8dc6c42` F2a | 题面的生产者；「对齐」是一条执行器 | `application/decision_specs.py` + `profile.yaml` 的 `decisions` 块 |
| `95f8374` F2b | 影子：**分组、不裁决**，三个分母只出计数 | `application/shadow.py` |
| F3（本节末的待提交改动） | 调用点：`decide` 接线 + 能力表逐后端 | `adapters/mcp_server/decisions.py`、`tools.py`、`interfaces/mcp/__main__.py` |

**F1 不是「先设计端口、再实现」**：B3 立的判据是**端口在第二个实现出现时才提取**，
所以两个后端是照着端口写的，端口上每个形状都被两条真实路径同时钉住。三处**刻意不
存在**的入口：没有「由调用方指定模型 / 阈值 / 题面」的参数；**没有打分题**；
**没有 confidence 的缺省值、也没有从概率推出来的标签**（laya 报的是 `p(yes)`，按
0.5 折成 `"yes"`/`"no"` 是一次没人授权过的阈值判定，所以端口只承载概率）。
`DecisionMode` 里**没有** automated acceptance 这个成员 —— 不存在的取值比「存在但
没人敢用」可靠。另外两处「不让库替我们做决定」：`_room_left` 用
`laya.common.build_sequence` **自己的算术**量出每题留给 state 的确切 token 数（不
估算、也不把它抄一遍），超出就弃权 —— 按最宽的那一题接线会让较紧的那题被库静默裁剪
（`st[:room]`），而报告上完全看不出来；`RetryPolicy(max_retries=0)`，因为 SDK 缺省
的 2 会让一次 `decide` 最多发三个请求，而账本扣的是一次。

**F2a 的核心决定是「题面住 profile」**，而且不是图省事：从 `role-matrix` 的 `match`
词派生选项描述，等于把匹配规则抄进题面，后端做的事从「判断」退化成「复述规则」，而
规则做不到的那部分（没见过的新写法）恰恰是这一族样本的全部意义；拿闭集取值当描述
（`"x"` → `"x"`）则字段非空却一个字的信息都没有，而 `DecisionQuestion` 的非空校验
会以为它填过了。三个 task 的闭集来源**不同**，这是它们最值得记下的差异：

| task | 闭集 |
|---|---|
| `page_triage` | `profile.page_discovery_hints` 的档位（profile 声明） |
| `responsibility_relation` | `VenueRelation` 五个成员（SOP L27；**与 Scene 无关**） |
| `role_function` | 该 profile 的 role-matrix **实际触达**的 `business_function` |

第三条用 `values()` 而不是枚举全体是关键：枚举里有 15 个成员而这份 role-matrix 只
触达 13 个，多出来的取值会让后端有机会选一个这个 Scene 根本不认识的口径 —— 而它
看起来仍是一条合法的回答。**对齐是一条执行器**：编译期断言「声明的 key 集 == 闭集」。
它防的不是手滑：role-matrix 里长出一个 `business_function: estates` 的角色组而题面
没跟上，后端就只能在闭集外作答，或者更糟 —— 照旧返回闭集内的某一个，于是**这一类
岗位全部被判成了别的东西**，而记录上看不出来。`version` 由题面自己算（instructions
+ 选项 key 与描述，按**题面顺序**），`spec_id` 是身份、不跟内容走：两者合一之后，
「换了题面」与「换了另一件事」在台账上没有区别；而选项顺序由**闭集**决定、不由 YAML
的书写顺序决定，否则一次纯排版改动会在台账上读成「题面改过」。

**F2b 的报告里没有裁决字段**：三个后端里两个报同一个词，看起来像一个可以拿去用的
结论，而**多数表决就是一个没人证明过的判定**（§4 要求 automated acceptance 在那次
独立留出评估之前保持关闭），且两个后端错得一样是常态（laya 与 Jev 是同一产品族的
本地版与托管版）。所以报告是分组，`as_dict()` 的键集由一条判据对着写死的外部清单
钉住，外加一个扫「裁决词」的递归扫描器（自带正对照，否则把报告删空也能通过）。
`SampleVerdict` 是**四值**不是布尔：AGREED / DIVERGED / PARTIAL_COVERAGE /
NO_COVERAGE —— 压成「有没有分歧」会同时丢掉「某个后端在这类输入上跑不起来」（去把
它接上）与「两个后端答案不同」（这才是本该比较的那件事），而两者的处置是反的。
**三个臂全都弃权不算一致**：那是共同阻塞，一个结论都没发生。没作答的臂**留在分组
里**自成一组：滤掉它，一个 2-of-3 的分组会显示成全体一致，而那次「一致」里有一条
从未发生的判定。三个分母（没标签 / 题面漂移过 / 没作答）各不相同，所以只出计数、
不出比率 —— 一个 `accuracy: float` 会逼人选一个分母。

F2b 立的两条纪律，F3 接着用：**模式不是参数**（`run_shadow` 没有 `mode` 形参，做成
旋钮，调用方就能把一次影子判定记成辅助判定，而两者的可见性不同）；**影子没有 MCP
入口**（`marketops/adapters/` 里没有一处 import 它，判据用 AST 扫 import —— 按词
匹配会在 `tools.py` 那句描述 `DecisionMode` 的**正确**散文上误报）。

### F3：调用点是第四块，而它只做三件事

`ports/decisions.py` 落下了**判定**，两个 adapter 落下了它的实现，
`application/decision_specs.py` 落下了题面的生产者。少了调用点，前三块都在，而 §4
那条 MVP 验收条件（「一个可调用的决策工具」）不成立。F3 是
`marketops/adapters/mcp_server/decisions.py` 与它周围的接线：

* **输入里没有题面。** 调用方拿到的是 `spec_id`，题面由服务端从冻结的 profile
  编译，找不到就报错、**绝不回落到其中一个**。「普通 worker 不得自造
  DecisionSpec」（`docs/ARCHITECTURE.md` §7 逐字）唯一可能的执行器就在这里 ——
  题面一改，旧标签就静默不可比了，而报告上只显示「后端不一致」。**实测**记下的
  SDK 行为让这条判据更硬：`tools/call` 里声明之外的参数被**静默丢掉**（不报错、
  也不起作用）—— 所以「送一份伪造题面进去」的判据不能写成「它会拒绝」，只能写成
  「它被忽略」：拒绝只说明这一次没成功，而忽略说明**这个参数不存在**。
* **没有 `mode` 参数。** 恒以 `DecisionMode.ASSIST` 调用后端：一个
  `mode="shadow"` 的参数就是一条让调用方把影子答案要回去的入口，而用它的那一次在
  记录上与 assist 完全同形。模式是**路径**的属性，不是每次调用的参数。
* **校验全部在推理之前，且拒绝不截断。** `MAX_STATE_CHARS` = 8000 是从
  `fetch_document` 的 `text_preview`（4000）推出来的「两页正文」，**不是**模型窗口
  —— 两个后端的窗口各自不同、且它们自己会判；这一层挡的是「把一整页文档当 state
  塞进来」，那对两个后端都注定弃权，而对 jev 是一次**付过费的**弃权。

**这一片新增了一行生产代码的守卫**（F3 唯一一处）：`_decide` 要求后端回的记录
`record.mode is _MODE`，否则整次拒绝。理由值得写下来，因为它是「判据只能放在这一层」
的一个实例：回执**如实**写着 `record.mode`，所以那不是数据损坏；被破坏的是 §4 的两条
可见性规则，而**判断这件事的字段（`mode`）不在任何人的读数范围里** —— 调用方读
`outcome` 与 `answers`，不会读 `mode`。两个真实 adapter 都原样透传，
`ports.decisions.decide(mode=)` 是端口对实现的约束、与工具面对调用方的约束是两件事，
所以执行点有两处，少了第二处，一个回了别的模式的实现会把一条影子记录交到研究侧，而
回执上完全看不出来。

**能力表这一格偏离了其余五行：`wired` 逐后端、按对象同一性。** 其余五行问的是「有
没有工具会调这一类集成」，一个 `"decide" in wired` 就够；决策不行，因为 assist 是
**单臂**模式，`decide` 只咨询**一个**后端，另一个今天根本没有调用路径 —— 给两行都写
`True` 等于说「两个都会被调用」，而那是这张表最容易被读成「两个都能用」的一格。所以
`wired` 认的是 `provider is advisory`，用 `is` 而不是 `name`：`advisory` 就是从同一份
`decisions` 里取出来的，同名但不是同一个对象意味着这份表与接线用的不是同一批东西。
传进来的 advisory 若不在这份表里，`_decision_rows` 直接报 `ConfigError` —— 那种状态
下每一行都会报 `wired: false` 而 `decide` 照样会调它，「表说没有调用路径」与「调用
路径正在跑」同时成立，且没有一处看得出来。同一格里另有一条：**不筛 `ready`**（两个
后端都构造），否则一个没就绪的后端在表上会以**缺席**的形式出现，而缺席读不出「没配
钥匙」。

**能力表那两行从常量变成读数。** 之前是写死的 `configured: False` —— 问题不是它当时
不对（当时确实没接线），而是它**不会变**：接线之后那两行照旧报「尚未接线」，于是
「哪一天该把它改过来」变成一件要靠人记得的事。一个恒假的健康旗与一个恒真的健康旗是
同一类东西 —— 它们都不随事实变化。现在四格逐格读 provider 的属性，而三种「没有调用
路径」仍是**三件事**：`decisions is None`（本实例压根没构造后端，配置可能配了）、
`advisory is None`（构造了，但本部署没指定辅助后端 —— 处置是**加一个环境变量**）、
`advisory` 是别的后端（处置是**别动它**）。合成一句「未接线」会让运维去接线一个已经
接好的东西。

**`decide` 不落盘 DecisionRecord，这条边界是有意的。** `ports/store.py` 的
`EvidenceStore` 是按账号分的，而决策记录既没有端口、也没有第二个实现 —— 按「端口在
第二个实现出现时才提取」刻意**不建**。回执里因此如实写明：`record_id` 是内容寻址的
（输入 + 后端 + 判定时刻，所以同一次判定的重试不会造出两条记录），而**没有任何一处能
按这个 id 把它取回来** —— 回执带的是那条记录**本身**。一条安静的缺失会被读成「有
这个 id 就能查到」。

> **这一段在 Phase F 之后被取代了（§2 收口）。** 边界本身是**有意撤掉**的，不是被
> 违反的：一条判定是**付过费**的，而「没有任何一处能按 id 把它取回来」意味着服务重启
> 或换一个客户端之后它就没有了 —— 那与 §10 要验收的
> 「decide 回执 → DB 行 → 服务重启 / 新客户端 → 同一个 decision_id 读回」正好相反。
> 端口因此建在了 `ports/decisions.py::DecisionRecordStore`，落点是
> `adapters/sqlite/decisions.py`，读回入口是 `get_decision`，而幂等键是
> `(work_item_id, provider, case_id)`（**不是** `record_id` —— 它的配方里含
> `decided_at`，挡得住「同一件事记成两条」、挡不住「同一件事做两次」）。
> 「端口在第二个实现出现时才提取」那条规矩没有被破：SQLite 是主存，且它与
> `EvidenceStore` 不是同一种存储。上面那两句仍然准确地描述着 **Phase F 当时**的状态。

**本机今天的实测状态**（跑出来的，不是推断）：`MARKETOPS_DECISION_PROVIDER` 未设置
⇒ `decide` 照常接线并明确回答「本部署没有指定辅助后端」；`jev` =
`configured: true, runtime_ready: false`（SDK 装了，缺 `TYPESAFE_API_KEY`）；
`laya` = `configured: true, runtime_ready: false`（库装了，checkpoint 目录没配）。
**§4 的 D4「Laya 真跑」今天不成立**：权重不在本机。§20：缺件不得写 PASS —— 两行如实
报 `runtime_ready: false`，表上没有任何一格是猜的。`tests/test_decision_providers.py`
因此**没有跑过真实的判定、也不假装跑过**：它最强的一条用**对方**的校验器
（`pydantic.TypeAdapter(typesafe_sdk.Questions)` 直接验 `as_questions()` 的输出），
正对照是删掉 `criteria` 的选择题必须 `ValidationError`。

**判据**：`tests/test_mcp_decision_tools.py` 21 条（后端全是替身，零网络零凭据）、
`tests/test_mcp_entry.py` 两条覆盖 `wire_decisions` 的三件返回值来自**同一次构造**
（用 monkeypatch 计数，因为 `providers[advisory.name] is advisory` 对「构造两遍、各
取出同一个名字」也成立）。每条「不该发生」旁边都有一条「该发生」——
`test_no_advisory_backend_is_refused_before_anything_else` 就是三调用对比：三句
各自的理由都出现，才证明校验顺序（advisory → 长度 → refs → spec）被证明了。
**一处刻意直接调私有函数**：三个 task 的题面全是 `QuestionKind.CHOICE`（`compile_spec`
里写死），所以 `_answer_view` 的 `YES_NO` 分支**经服务面到达不了** —— 那条判据直接调
它，并在 docstring 里写明为什么够不着，因为「到达不了的判据」与「测不到的判据」在
报告上同形。变异 **21/21 killed**（驱动在 `artifacts/tmp/`，不在仓内）：能力表四格
逐格取自后端、`_why_not_called` 三个分支各自活着、说谎组合的守卫、`_MODE` 改成
SHADOW、回执模式守卫、超长改成**截断**、`check_canonical_id` 直通、`case_id` 固定，
逐条都让某一条判据转红。

顺手收窄了两处会**随事实变旧**的散文执行器：`tests/test_shadow.py` 的动态名扫描器
（先是按整段文本匹配，于是任何一句「影子的调用点是 `application/shadow.py::run_shadow`」
都会被判成接线，而那句说明**是对的** —— 红的判据唯一正确的处置是改判据），现在只抓
**代码用到的**字符串字面量（docstring 与注释排除，`getattr(module, "run_shadow")` 里
那个名字在语法树上是一个非 docstring 的 `Constant`），并配了一条两半都断言的正对照：
docstring 不报警、`getattr` 与字符串常量报警 —— 只断言前者，一个「见到这个词就报警」
的实现也能过；只断言后者，一个「什么都不报警」的实现也能过。另一处是
`tests/test_mcp_control_tools.py` 那节标题里写死的「九个」：目录里没实现的总数随阶段
变化（Phase C 是九个，D/E/F 各接掉一部分），写死一个数字的注释会在下一次落地时变成
一句错话，而它是散文，没有执行器会替它转红。

**状态**：1228 passed / 6 skipped / 0 failed、`ruff check .` 干净、契约重生成与
committed 逐字节一致、**未 push**。

---

## 2.9 Phase G 的事实基础（2026-09-22）：Quay 没有 attempt，也没有 lease

§B6b 里那句「`jobs.py` 不写」把条件写成了「作业生命周期的形状取决于 Quay 那侧的
`submit_research` / attempt / lease 到底长什么样，那件事在 Phase G 之前没有事实
基础」。这一节就是那个事实基础 —— 它是**观察**，不是设计，所以单独落位：下面每
一条要么能由一条命令复现，要么能指到 0.11.0 的某一处代码。

两个来源，各自标明：**实测** = 在本节点的 `ARTIFACTS_ROOT/tmp/quay-probe-20260922`
（仓外探针目录，`quay` 0.11.0 的插件构建）里跑出来的逐字输出；**源码** = 读
`~/.claude/plugins/cache/quay/quay/0.11.0/` 下那两个 esbuild bundle（**没有 TS 源**，
只有保留 `// src/…ts` banner 的产物）。

### 任务模型，以及一个「声明与实现不一致」

**`status` 有五个，不是四个。** `vendor/quay/dist/quay.js:8642`（与
`quay-native.js:7385` 逐字相同）：

```js
TASK_STATUSES = ["todo", "ready", "done", "needs-human", "superseded"];
```

而 `vendor/quay-native/provider.yml` 里写的是「4 states, no more」，**漏了
`superseded`**。我先前那次 CLI 实测只探到四个，正是因为照着 provider 的声明去问的
—— 教训记在这里：**Quay 自己的声明文件不是它的实现**，凡是要写进适配器的闭集，
去 ABI 常量那行读。

合法迁移是一张表（`src/gate/lifecycle.ts`，`quay.js:50343`）：`todo→ready`、
`ready→done`、`done→ready`、`needs-human→todo`、`superseded` **两端都是 null**。
但 `assertTransition` 只被 `promote` / `retreat` 调用，`complete` 自己硬编码了
前置条件（`quay.js:50202`，逐字 `illegal transition: ${task.status} cannot
complete (must be ready)`）—— **同一件事两个写入面、两套规则**。

第三套规则在 CLI 上：`quay task edit --status` 默认**不设防**，`--enforce-gate`
是 opt-in（实测：对 `--status done` 拒绝退 1，对 `--status needs-human` **放行**）。
所以「状态迁移被强制」这句话只在走 lifecycle 四动词时成立。

### 四个退 0/退 1 的陷阱（实测，每一条都会写坏适配器）

| 现象 | 实测报文 | 后果 |
|---|---|---|
| `task check` 门失败**仍退 0** | `{"gate":"author->ready","ok":false,...}` | 靠退出码判断 = 把「门没过」读成「过了」 |
| CAS 冲突**退 1** | `CAS conflict on TASK-001: expected status "todo" but actual current status is "done" — another writer changed it first; refusing to overwrite (write() aborted, nothing written to disk)` | 与「任务不存在」**同一个码**，退出码分不开 |
| `--enforce-gate --status done` 拒绝 | `gate refused this write — gate check failed` | 与上一条同样退 1 |
| `quay complete` 自己强制迁移 | `illegal transition: needs-human cannot complete (must be ready)` | 旁路了 `--enforce-gate` 也逃不掉 |

**结论**：适配器必须**解析 JSON / stderr 正文**，不能读退出码。这一条要写进
Phase G 的代码注释里，因为「退出码 1 就是失败」是一个几乎不可能不写的直觉。

### attempt 不存在，lease 根本不存在

`attempt` 全仓只有三个住处，**没有一个在任务模型里**：

1. `quay.js:43271` fan-in 快进合并的**防活锁计数器**（`attempt >= 3` 就停自动重试）；
2. `quay.js:36775` 一个读 `.quay/worker-outcome.jsonl` 的读者（一行 JSONL = 一次）；
3. `scripts/workflow-event-schema.mjs:117` 的 StageEvent 必填字段（与 `runId` /
   `candidateId` 并列）—— 唯一一处**有类型**的 attempt，描述的是工作流日志里的
   一次阶段执行，不是 Quay 任务。

`lease` 是**零**：两个 bundle 里 32 处 `lease` 子串全部是 `release` /
`releaseLock` / `lockReleaseEpoch`。

`claim` 存在，但**在带外**：`scripts/claim-task.sh` 往 `refs/heads/task/<id>` 推一个
空 orphan commit，靠 `--force-with-lease` 做接管。它对 `quay task` / `quay run` / MCP
**不可见**，且没有显式 remote 时失败关闭（退 2）。

### Quay 真正给了什么原语

* **CAS**：`task_write` 的 `expectedStatus` —— CLI 上就是 `--expect-status`。这是
  唯一一个能跨机防重复写的原语，逐字报文见上。
* **门**：`gate --list` = `dod` / `acceptance` / `dark-axis` / doc-quay-directive-skill。
  `acceptance` 门执行的是 `extra.acceptance` 里的 shell 命令（默认 60s 超时）。注意
  `adjudicate` 是**只读**的：调 provider 的 `task_check`、追加一条 `audit` 事件、
  **永远退 0、永远不碰状态**。
* **driver loop**（`quay run`，`src/gate/driver.ts`）：`task_list{status:ready}` →
  筛出 `extra.acceptance` 非空的 → **按 id 排序** → `runComplete`（CAS
  `expectedStatus: ready`，写 `done`）→ 往 `.quay/gate-events.jsonl` 追加一条。
  带 `.quay/.stop` 哨兵与 1000 次上限。`--once` **永远退 0**，门失败只留一条事件。
* **`extra` 是自由 JSON**，且没有过滤器也没有索引；**`label` 有**，而且是
  **AND-join**（`quay-native.js:7987` `wanted.every((l) => have.includes(l))`）。

### 外部系统 id 没有正式落点

源码扫过 `externalId|external_id|jobId|job_id`：只有 MCP SDK 自己的资源注册表内部
命中，与任务无关。没有 `external_ref`、没有 `origin`、没有 provider 侧保留字段。
`extra` 里被 Quay 自己写过的键只有四个：`acceptance`（写得最多）、`malformed`
（存储层发现 frontmatter 坏掉时写）、`schema` 与 `dirStatus`（**只是 skill 惯例**，
代码不读）。

→ **关联只能是我们自己发明的约定。** 采用两条，各自服务一个方向，**同一条事实不
在两处重复**：

* Quay 侧：一个 label `marketops-job:<job_id>`。选 label 而不是 `extra`，唯一理由
  是**只有 label 能查**（`task_list` 过滤 label 不扫 body）；也是唯一让一个人能在
  Quay UI 里从 task 走回 job 的路。
* MarketOps 侧：`research_job.quay_task_id` 一列 —— 从 job 走回 task，不必反向扫
  Quay。

### 由此定下的形状（Phase G 的设计约束）

1. **attempt / lease / 结果修订必须住 `marketops.db`。** `docs/DATA_MODEL.md:126`
   已经写明「Quay state is execution metadata, not business truth」；而 Quay
   **没有**这三个概念（上面已证）。它们不是「Quay 有但我们不用」，是**不存在**。
2. **Quay 侧只经两样交互**：建/改任务时的 `--expect-status` CAS，和门。适配器
   **不得读退出码**。
3. **租约不设自动过期。** 这一条是 Quay 观察之外的设计决定，但理由与本仓已有的
   一条同源：预算超时不自动退款，因为「超时」并不证明「对方没计费」；租约同理，
   **「租约过期」并不证明「那个 worker 死了」**，而猜错的代价正是 §8B 逐字禁止的
   「duplicate expensive work」。要收回租约，得有一个显式的动词并写明理由。
4. **不改 Quay 上游。** 上面每一个原语都是现成的；没有任何一条要求改源码。

---

## 2.10 Phase G 实际怎么落的（2026-09-22）：派发有实现、没有入口

| 提交 | 交付 | 落在新架构的哪里 |
|---|---|---|
| `74e2fdb` G1 | 持久作业记录：**同一件事被做两遍的五张脸** | `ports/jobs.py`、`adapters/sqlite/jobs.py` |
| `98dda19` G2 | `submit_research` + `get_job`；请求指纹；三件「报 0 就是撒谎」 | `adapters/mcp_server/tools.py` |
| `50345c0` G3 | `get_assignment` + `submit_findings`：**`settle` 是唯一的结果写者** | `domain/job.py`、`application/` |
| `2ddfaac` G4 | `control_job` 三个动词（**刻意没有 block**）、CAS 不匹配整次拒绝 | `adapters/mcp_server/tools.py` |
| `d1970e1` G5a | 外部执行器适配器 + 假 CLI；**十项实测**（已记在 §2.9） | `adapters/quay_cli.py`、`ports/dispatch.py` |
| `9ad6e6f` G5b | `WorkDispatch`：顺序表七步，三个出口 | `application/dispatch.py` |
| G5c（本节的待提交改动） | 组合根接线 + 能力表第七行 | `interfaces/composition.py`、`adapters/mcp_server/{tools,server}.py`、`interfaces/mcp/__main__.py` |

### §2.9 那两条关联约定，落地时各改了一处

**一、label 从一条变成两条。** §2.9 定的是 `marketops-job:<job_id>`，理由是「只有
label 能查」。落地时发现一条 label 答不了那个问题：`find(labels=…)` 是 **AND-join**
（M7 实测），而作业下面通常有多个 work item，只按 job 查会一次查到**同一作业的其他
单元**，于是「按 label 查，没有就建」的循环会把别人的单元读成自己的。
两条 label（`marketops-job:<job>` **与** `marketops-item:<work_item>`）合起来才是
「这一件」，而这两个前缀必须不同 —— 否则同一个 id 传两遍拼出同一个字符串，
读写两面都接受，两件事共用一个桶。判据用**同一个 id 传两遍**，因为那是唯一能让
「两个前缀一样」显形的输入。

**二、映射从 `research_job.quay_task_id` 挪到按 work item 的 `execution_ref`。**
那个列名是 §2.9 写的，落成表结构时按的是另一件事：**租约是按 work item 发的**
（G3），而「没有外部 ref 就没有租约」这条承重关系要求 ref 与 work item 同级。
挂在 job 上，一个作业里两个单元的租约就得共用一个外部任务 id —— 而
`LeaseError` 那条「绕过 Quay 直接开 worker 在代码里写不出来」正是靠这个对齐成立的。

### 一条我自己删掉的判据，与它为什么不该存在

`dispatch()` 里原本写了一个「这个 work item 已经停了就拒绝」的分支（先例：G3 的
`is_stopped`）。它是**走不到**的：要拿到 `stop_reason` 必须先 `settle`，`settle` 只收
活租约，租约要求先有 `ExecutionRef`，而映射是写一次的（全仓没有 delete/update
`execution_ref` 的路径）。所以「已停止」蕴含「已映射」，第 2 步的短路总是先返回。
删掉它，理由写进模块 docstring 的顺序表下方，并加
`test_a_stopped_work_item_is_always_already_mapped` 钉住这个蕴含关系。
**一条没有输入能走到的判据与一条恒真的健康旗同类** —— 这一条与 §2.8 的
「影子没有 MCP 入口」是同一条纪律的两次应用。

### 三个出口里 `ADOPTED` 不是兜底，是崩溃路径的正常出口

`DispatchOutcome` 是 `already_mapped` / `adopted` / `created`。第二种最容易被读成
「容错」，而它实际是：上一次派发把任务建出去了、进程在写映射之前死了。这时重跑必须
**认领**那一条而不是再建一条，否则同一件事在外部有两行。字典里没有「查到多条就挑
一条」这个选项 —— 那是把「同一件事被派发了两遍」的证据扔掉，所以它抛
`ExternalStateError`（那个类型的第三种原因，docstring 里逐条写了三者的处置差异）。

### G5c：三轴里有一格今天必须为假，而它为假的**理由**是可执行的

能力表第七行（`quay`）照旧三轴：`configured = settings.quay_enabled`、
`runtime_ready = quay is not None`（**读对象，不从 Settings 重推**，与决策那两行同源）、
`wired` 由 `tools._DISPATCH_TOOL` 驱动 —— 而它今天是**空串**，因为工具目录里没有一条
会走到 `WorkDispatch.dispatch`：§6 的派发是 worker 侧的动作，
`docs/MCP_SERVICE.md` §1 的流程图里它只是准入与 worker 之间的一步。
空串而不是一个假名字：写一个不存在的工具名会让这一格读起来像「工具在、只是没接线」，
而今天没有「忘了接上」这回事。

为了让它不是一条恒假的旗，加了一条**会自己翻面**的判据：扫 `marketops/` 里
`WorkDispatch` 一族名字的引用者（AST 名字节点，不是 grep —— 能力表那一行自己的消息里
就写着 `WorkDispatch.dispatch`），断言 `bool(引用者) == bool(_DISPATCH_TOOL)`。
接线的人不同时填那个常量，这条判据就红，并告诉他填哪里。

`default_dispatcher` 把「没配」与「配了一半」分成 `None` 与 `ConfigError`：三样一个
都没给是一个**事实**（处置是不派发），只给了一两样是一次**配置事故**（处置是把话说
到位）。压成一句的代价是：一个配错了的部署会在能力表上长成「本部署不派发」，而这两句
话对运维是两件事 —— 这也是 `Settings.quay_enabled` 那处 docstring 早就写下的伏笔。

### 一条只存在于错误消息里的变量名不是配置面

`default_dispatcher` 抛的那条消息逐字点名三个环境变量，`load_settings` 缺
`MARKETOPS_DATA_ROOT` 时也点名一个 —— 而运维拿到那些名字之后，去 `.env.example`
里**找不到它们的任何踪影**。补的时候顺手扫了一遍，差集是**七个**：MCP 三个
（`MARKETOPS_MCP_HOST` / `_PORT` / `_TOKENS`，Phase C 遗留）＋ 外部执行器四个
（`MARKETOPS_QUAY_*`）。反方向（写了但 `config.py` 不读）为空。

判据落在 `tests/test_config.py::test_env_example_names_every_variable_config_reads`：
AST 扫 `config.py` 里 `_raw` / `_raw_path` / `_raw_float` / `_raw_int` 的首参，
断言「读得到的 ⊆ `.env.example` 里有的」。它**刻意只查一个方向** —— 反方向不断言，
因为 `.env.example` 记下一个由别处读取的变量是合法的。

扫描器有一个自己的取舍值得写下来：**首参非字面量的调用点抛出，不是被跳过**。
跳过会让「有人把变量名挪进一个常量或参数里」这种改动在这条判据眼里完全隐形，
而一条扫不到的边界，它的读数与「没有这条边界」逐位相同。唯一的豁免窄到一行：
`_raw*` **本族之间**转交自己的形参（`_raw_path(key)` 里那句 `_raw(key)`）。
窄到这个形状是有意的 —— 「形参名出现在首参上」本身不能当豁免，因为
`def _load(name): return _raw(name)` 长得一模一样，而它是一次真读取被藏进了参数里。
两半都有对照：`tests/test_config.py` 里一份可接受样例 + 一份必须被拒的样例。

四条变异各打中预期：删掉 `.env.example` 的 quay 四行 → 主判据红；让扫描器什么都
扫不到 → 正对照先红（说明正对照是活的）；把豁免放宽回「形参名出现即放过」→ 负样例红；
去掉豁免 → 两份样例一起红。

### 偏离、缺口与债（Phase H 必须处置）

1. **派发路径今天没有工具落点。** 不是「没接线」，是入口不存在：`_DISPATCH_TOOL`
   为空串这件事由上面那条判据守着。Phase H 决定要不要给服务面一个派发入口。
   这一条直接卡住 §5 的 C：`ARCHITECTURE.md` §3 给 Quay 的验收证明是「actual
   driver/task/attempt records, overlapping independent workers, dependency
   negative test」，而**没有派发入口就没有一条真实的任务记录** —— 适配器与顺序
   都齐了，缺的是那个把工作单元交出去的人。按 §20 逐字，一个 configured-but-
   untested 的适配器不算满足验收。所以 C 今天**不通过**，而它在能力表上的样子
   是 `configured: true / runtime_ready: true / wired: false` —— 三格一起读才是
   实话，只读前两格会读成「Quay 就绪」。
2. **`subjects` 今天没有生产者。** `application/cohorts.py::load_accounts(path)` 要一个
   **显式路径**，而 `Settings` 里没有账号清单路径 —— 所以 `submit_research` 的
   `known_accounts` 输入今天只能指向一份不存在的清单。补这条生产者会越界进 Phase H。
   **→ 已在 §2.11 处置**（`application/inputs.py` + `Settings.inputs_dir` +
   受理之后立刻编译）；三条输入里补上的是 `known_accounts` 一条，另两条各给了一句
   说得出的理由，不假装有生产者。
3. **`BLOCKED` 今天没有生产者。** `JobState` 有它、`StopReason.blocked` 有它，但没有
   任何一条路径会写进去。**不假装它有**：一个只有取值、没有写入面的状态与没有它一样，
   而写在表上会让人以为那条路存在。
   **→ 已在 §2.11 处置**：它作为「如实说」的**后果**获得生产者（输入产不出工作单元时
   落 `BLOCKED`），而不是作为一个新功能被加进来。
4. **`task check` 的退出码跨构建不稳定**（0.4.0 退 1、0.11.0 退 0，§2.9）—— 这是
   「适配器不读退出码」那条纪律的**直接证据**，已写进适配器模块的十项实测。
5. **§5 那张表里的「六件套」是另一套六件**（含 Claude Code、不含 http/mineru），
   与能力表无关；能力表现在是**七**行。两处别互相对照。

---

## 2.11 Phase H 实际怎么落的（2026-09-22）：把 `submit_research` 接到它自己声明的输入上

Phase G 的结果是「派发有实现、没有入口」（§2.10）。Phase H 的第一片把那个入口
最后缺的一环补上：**受理之后，输入要真的变成工作单元**。在这之前，`compile_work_items`
在整份生产代码里**零调用** —— 于是 `get_job` 要报的进度、`get_assignment` 要租的活、
派发要遍历的清单，全都挂在一批没有任何生产者会产出它的行上。四个提交（H1、H2a
已提交，H2b / H2c 未做）：

* **H1（自检跑的不是整份装配）**：`marketops/interfaces/mcp/__main__.py` 的 `_compose()`
  由 `--check` 与 `_serve()` 共用，两者唯一的差别是最后跑不跑 `server.run(...)`。
  自检曾经**只**读配置、解析地址、装令牌环 —— 一个 `MARKETOPS_QUAY_*` 配了一半的
  部署会先拿到「自检不通过：没有配置令牌环（退 1）」，然后在启动时退 2。同一个部署、
  两句互相矛盾的话，而先被跑到的、也是唯一会被贴进工单的那句，是自检那句。
* **H2a（本提交）**：`marketops/application/inputs.py` 新增；`Settings.inputs_dir`
  新增；`submit_research` 受理之后立刻编译。
* **H2b / H2c（未做）**：派发入口（`_DISPATCH_TOOL` 填 `submit_research`、
  `control_job(resume)` 重新派发）与 worker 领活（`get_assignment` 在无活租约时
  `lease`）。**H2a 单独提交时 `paused` 仍然阻止不了任何东西** —— 因为还没有东西被派发。

### `input_ref` 是一个**名字**，不是一个路径

收一个路径等于把「读哪个文件」交给控制端；跨机器部署下那就是一条越权读盘的接口。
所以名字只接受 `check_canonical_id`（`slugify` 的不动点），路径由服务自己拼在
`Settings.inputs_dir` 下。**拒绝，不规范化**：`../secrets` 规范化之后是 `secrets`，
于是「读哪个文件」由调用方给的一串字符决定；`devon-2026-` 与 `devon-2026` 会落到
同一个文件上，而调用方给的是两个名字。三条都进了
`tests/test_job_inputs.py::test_a_name_that_needs_normalizing_is_refused_not_rewritten`。

`inputs/` 在 `data_root` 下、**不在** `state/` 下：这个目录装的是**调用方交进来的
东西**，`state/` 装的是我们自己写出来的状态 —— 混在一起之后，「这个文件是谁写的」
就没有答案了。

### 三条输入里只有一条有生产者，而另两条不退化成一串空 subject

`JobInputKind` 有三种，今天只有 `known_accounts` 有产出者。另两类**不返回空清单**：
空清单会被 `compile_work_items` 拒绝（它逐字写着「一个没有任何 work item 的作业在
报告里长得像已经做完」），于是缺一个生产者会被读成一次完成。它们各返回一句点名了
kind 的理由（`_NO_PRODUCER`），由调用点落成 `BLOCKED`。`SubjectResolution` 由
`__post_init__` 守着「恰好给出一半」—— 两样都没有的那种构造不出来，因为它在调用点
的表现是一条**没有理由**的 `BLOCKED`（`set_state` 对 BLOCKED 要求理由非空），
也就是一次崩溃。

**清单是空的**也是同一类：它不是「受理成功、只是没有活干」，理由里带上了
`compile_work_items` 的那句话与它去看的那个路径。

### 名字先校验，再受理

一次非法 `input_ref` 若先落下一个作业、再报错，那个作业的 `input_ref` 已经写死了：
调用方改好名字重发会撞上幂等冲突（同一个 key、不同的请求），于是**那个幂等键连同
它的作业一起废掉**。拒绝在门口，等于不给这个状态任何存在的机会。
`tests/test_mcp_job_tools.py::test_an_unusable_name_is_refused_before_any_job_exists`
的后半段就是这条：被拒之后用同一个键、一个合法名字重发**必须成功**。

### 阻塞只在两个条件下发生，各挡一种谎

* **作业手里一件工作都没有**。已经编译出工作单元的作业，不因为「今天读不出那个
  `input_ref`」而变成被阻塞 —— 那是把一次读盘失败说成一件没做过的事。判据：
  `test_blocking_never_rewrites_a_job_that_already_has_work`（同一键重发、删掉清单、
  状态与条数都不动，而回执如实报出这一次读输入失败了）。
* **作业处于 `QUEUED`**。`PAUSED` / `CANCELLED` 是控制端的判断，受理不是往回写
  状态的地方。

由此，回执里**分开报两件事**：`state` 是**作业**的状态，`work_items.blocker` 是
**这一次读 `input_ref`** 的结果。两处说法会漂移，除非它们本来就是两件事。

**阻塞的解除不在受理这一侧**：补齐清单之后重发同一个键，拿回同一个作业、工作单元
编译出来了，而状态**仍是** `blocked` —— 解除阻塞是控制端的一个动作。这正是 §2.12
里 `control_job(resume)` 做的事：重新解析、重新编译（幂等）、再派发。

### 顺带改掉的一件旧事：`work_items` 那三段在测试里都是手搓的

`tests/test_mcp_job_tools.py` 原来在受理之后**自己调** `compile_work_items` 造出
判据要的数据 —— 那些用例测的是读取侧，而工作单元是手搓的前置条件。现在缺省输入是一份
**真的**清单（`inputs` fixture 写进 `Settings.inputs_dir`），于是截断那条用例变成了
「51 个账号的清单编译出 51 条」而不是「手工塞 51 个 subject」。用一个读不出来的
`input_ref` 当缺省，会让这份文件里每一条判据都建在一个 `BLOCKED` 的作业上。

### 这一片的执行器

`1466 passed`（H1 之后是 1449）、`ruff check .` 干净、契约重生成与 committed 逐字节
一致。新判据的 5 条变异**全部打中**：`__post_init__` 的守、空清单那条分支、subject
的顺序（改成 `sorted`）、`_NO_PRODUCER` 少一句、以及「已编译的作业不被改写」里的
`not items` 那半句。全部用 `cp -p` 备份还原，还原后逐字节一致。

---

## 2.12 Phase H 实际怎么落的（2026-09-22）：把「受理了的工作」真的交出去（H2b）

§2.10 的结论是「派发有实现、没有入口」：`application/dispatch.py` 三条出口齐全、
13 条判据全绿，而它在整份生产代码里**零调用** —— 没有任何东西会去遍历一个作业的
工作单元并把它们交出去。H2a 补上了「工作单元从哪来」，H2b 补上「它们从哪出去」。

### 两个入口，**一条**顺序

* **`submit_research`**：受理 → 编译 → 派发。§6 逐字要求 "Store admission and
  dispatch intent transactionally"，分两次调会让「受理了、没派出去」与「受理了、
  正要去派」在报告上长得一样，而处置不同（前者要 `resume`，后者只要等）。
* **`control_job(action=resume)`**：把状态写回 `queued` 之后重走同一条路。两处调
  的是**同一个** `_resolve_and_compile` 与**同一个** `_dispatch_block`，所以「这个
  作业该派什么」只有一份判据 —— 两份的话，同一个作业会按「是从哪条路径进来的」
  得到两套编译语义，而它们在报告里长得一样。

`pause` / `cancel` **不派发**，而且它们的回执里**没有 `dispatch` 那一格**。给它一个
恒为空的那格是不行的：它会被读成「派发过了，一件都没成」—— 而前者的处置是去看执行器。

### 途中失败停在第一件，**不**回滚已经交出去的

三种失败（外部同 label 有不止一条任务、执行器起不来/答非所问、循环中被 pause 掉）
指向同一个判断：现在这条路径给出的答案不可信。继续往下派是真的把活交出去；而把前
几件「撤回」做不到 —— 端口上没有 `delete`，收回外面的一件事需要一个写明理由的动词，
它不在这条路径上（同 `dispatch` 的 TOCTOU 那一段）。所以正确答案是「停下 + 说清停在
哪」，不是一个看起来干净的原子性。

`DispatchRound` 因此有三格：`receipts`（逐件回执）、`stopped_at`、`blocker`。而
**`stopped_at` 为空**这一格是承重的：作业不放行派发时，一个「逐件去派、让第一件撞上
拒绝」的实现给出的 `blocker` 与早退那条路径**逐字相同**（两处共用
`_dispatch_refused`），只有这一格说得出「它压根没开始」—— 而差别不是文风：前者意味着
外部已经被读过。

### `handed_out` 只数**真的碰过外部**的那两格

`CREATED + ADOPTED`。`ALREADY_MAPPED` 不算，而且它连 `find` 都不调 —— 本地那条映射
就是「派发过没有」的权威。混用会让一次**重发**看起来像一次派发，而重发是正常动作
（受理、resume、断线重连都会走到这里）；§8B 逐字禁止 duplicate expensive work，判据
因此落在记账替身的 `outside.calls` 上，而不是回执的数字上。

### 顺带修掉的两处「声明了但没有人执行」

* **`input_ref` 的名字校验只对有生产者的那一类生效**。`subjects_for` 对
  `source_receipt` / `discovery_scope` 在给出阻塞理由之前就返回了，所以靠它顺手校验
  等于「这条规则只管今天有生产者的那一种」。现在受理时直接调 `check_canonical_id`，
  **三种输入一起过** —— 「`input_ref` 是一条名字、不是路径」是三种输入共有的约定，
  而第一个生产者会照它去拼 `inputs_dir` 下的路径，那时炸的是一个已经受理的作业。
* **能力表 quay 行的 `wired` 没有被任何东西翻面**。`wired` 由调用方声明的那一份只含
  §3 的研究侧（`search_web` 等），G 那五条进不进表由 `control_handlers` 按 `jobs`
  决定。现在 `get_capabilities` 在**表建完之后**才定义，判据是
  `wired | frozenset(table)` —— 一个「恒为假的健康旗」被换成了随实例变化的读数。
  正对照：装上作业存储那一格真的翻面、blocker 真的空。

### 这一片的执行器

`1484 passed`（H2a 之后是 1466）、`ruff check .` 干净、契约与 committed 逐字节一致。
新判据的 **9 条变异全部打中**：`dispatch_job` 的两处早退与失败时的 `continue`、
`handed_out` 把 `ALREADY_MAPPED` 算进去、`executor_configured` 恒真、`pause`/`cancel`
也带 `dispatch` 那一格、`resume` 不重新派发、`resume` 不落回 `BLOCKED`、名字校验退回
`subjects_for`、以及能力表那处 `wired |`。`tests/fakes.py` 是这一片新增的**共享**替身
（`test_dispatch_ordering.py` 与两个 MCP 文件各写一份会在「什么叫碰过」上漂移），
`tests/mcp_harness.py` 的 `write_accounts` 同理。

---

## 2.13 Phase H 实际怎么落的（2026-09-22）：派发之后，工作得**有人能领**（H2c）

§2.12 把工作交到了 Quay 那一侧。交出去之后那件工作的持有者是谁，答案是「没有人」——
`get_assignment` 在没有活租约时**拒绝**，而活租约只有一条路能建立：`store.lease`。
§3 的工具目录里没有 `lease`。于是这条链是闭合的死循环：没人能领，所以永远没有人持着；
而那句回答读起来完全像一个事实。

### 领活住进了 `get_assignment`，因为那是唯一一个「谁在跑」的问题

`_live_attempt_for`（要一条活租约，没有就拒绝）换成了 `_claim`（没有活的就**当场租给
本次调用的主体**，有的就拿回那一条）。同一条边界，两个动词：

* 受理/恢复那一刻把工作交给 Quay（控制面，§2.12）；
* `get_assignment` 那一刻把 Quay 上那份工作认领到一个具体主体名下（worker 面）。

§6 的「real process/task ownership 可核」要的是**有人**持着它，不是一个读起来像事实的
「暂时无人」。

### 三次调用只差在**谁先到**，而竞争窗口不在这层判

没有活租约 → 租给本次调用的主体；有、是本人 → 拿回同一条；有、是别人 → 照旧由
`_require_holder` 拒绝。第三种**不**在 `_claim` 里提前返回一句「已经被别人领了」：
存储层的 `lease` 会拒绝同一件事，但那句话说的是「等」，而调用方真正需要知道的是
「这份 assignment 不是你的」。在这里自己再判一次是同一份规则的两个实现，而漂移的那
一次会让两个 worker 同时以为这件事是自己的 —— 正是 §8B 「no duplicate expensive work」
要挡的东西。所以竞争窗口交给存储层的排他写，归属交给 `_require_holder`。

### 顺序不可换位，而这次是**真的**有顺序

解析 work item → 判 §6 的 `ExecutionRef` → 领活 → 验归属。第一处尤其要紧：

* **§6 那条判在 H2c 之前走不到。** 那时没有活租约的 work item 先被「没有活的租约」挡下，
  而活租约又造不出来（`lease` 自己也要求 `ExecutionRef`）—— `_assignment` 里那段
  §6 的措辞是一段**没人能读到的散文**。领活搬进来之后它才可达，所以现在它有了一条
  自己的判据（没有 `ExecutionRef` 的 work item 被拒、且盘上一条租约都没有）。
* 判词要落在 §6 那句话上，**负的那半才是判据**：存储层对同一个 work item 会拒绝同一
  件事，两句话里都含「还没有派发到 Quay」。只查这一句的话，把 §6 那条判挪到领活
  **之后**，用例照样绿 —— 而那正是要挡的形态（先去签一条签不下来的租约，再回头说
  「你该先派发」）。断言「存储层那句措辞不出现」才把顺序钉住。

### 顺带发现的一格：取消**不**解锁

取消之后存储层不再出租（`lease` 先判 `accepts_results`）。如果这条读路径每次都去
`lease` 一遍，那个**正持有租约的 worker 自己也读不到自己的 assignment** 了 —— 它会
拿到「作业已取消，不再出租」，然后交不出手里那份结果；而它手上那条租约并没有被谁收回，
是这条读路径把它变成了读不到。`_claim` 的幂等早退因此不是「省一次调用」：它让
「已经有了」这条路径**绕开准入检查**。另一半同样有判据：取消**不**解锁，别人来读仍然
是「不属于你」—— 否则一次取消会顺带把一件还没结算的工作交给任何一个知道 id 的主体。

### 这一片的执行器

`1487 passed`（H2b 之后是 1484）、`ruff check .` 干净、契约与 committed 逐字节一致。
新判据的 **4 条变异全部打中**：`_claim` 的幂等早退关掉、活租约筛选关掉（已交还的那条
被当成 assignment）、`_require_holder` 不调、§6 那条判挪到领活之后。第 5 条变异
（删掉「没有已认证的主体」那个分支）**故意留活**：`RoleGuard` 用 `authorize` 先拒掉无
主体的调用，handler 根本不会被叫到，所以那条分支**经传输层不可达**。它在 docstring 里
被如实标成不可达，存在的理由是让绕过 middleware 的调用者拿到一句能读的拒绝而不是
`AttributeError` —— `_require_holder` 里那条同款分支同理。一条留活且写明理由的变异，
比一条假装被打中的强。

---

## 2.14 Phase H 实际怎么落的（2026-09-22）：版本从哪来，以及指引**不**走 Quay 的正文（H3a）

规格四处点名同一件东西，而全仓**一个版本都不存在**：

* `docs/MCP_SERVICE.md:29` —— `get_scene` 要读 "effective policy/guidance version"；
* `:47` —— `get_assignment` 要返回 "Frozen Scene/**guidance**"；
* `docs/ARCHITECTURE.md:64/156` —— Scene owns versioned guidance；Running tasks retain
  their **pinned guidance**；
* `docs/SCENE_EVOLUTION.md` —— ScenePack 含 `skill/SKILL.md`，且「Quay-launched Claude
  workers receive the exact frozen ScenePack version attached to their WorkItem」。

载体则一个都没有：`ProfileBundle` 没有版本字段、全仓没有 `SKILL.md`、`ExecutionUnit`
没有指令字段、`_create_argv` 不发 `--body`。**`get_scene` 的目录摘要从 Phase C 起就写着
「生效的策略/指引版本」** —— 一句承诺，底下没有任何东西。

本片（H3a）只做**机制**：算出那个版本并报出来。把版本钉到 WorkItem 上是 H3b。

### 版本是**目录**的摘要，不是那三个 YAML 的

覆盖面的选择就是这个机制的全部。指引正文（`skill/SKILL.md` 那类）今天还不存在，
所以「一份写死的文件清单」看起来暂时没有区别 —— 而这正是它危险的地方：第一个被加进去
的指引文件**不参与版本**，于是「指引换了、版本没动」，加文件的人收不到任何信号。
版本因此走整个目录（递归、按相对 posix 路径排序）。

同理**没有排除模式**（`.DS_Store`、`*~`、`__pycache__`）。排除表的代价是某天排除掉一个
真的指引文件而无人察觉；不排除的代价只是版本多动几次。前者不可见，后者可见。

### 逐文件定长摘要，不是把内容拼成一个大串

文件名可以含换行，所以「名字 + 内容拼起来再散」是有歧义的（`("a", "bc")` 与
`("ab", "c")` 给出同一个串）。先算**定长**的每文件摘要、再拼这些定长块，歧义在结构上
不存在。路径与内容之间用一个不可能出现在路径里的字节隔开 —— POSIX 文件名里被禁的只有
`/` 与它。

排序不是风格：目录的列出顺序由文件系统给，不保证稳定，不排序的版本会在没有任何改动时
变来变去，而**每次都响的告警与每次都静的告警是同一类东西**。

### 散字节而不是散规则值：已知代价与它的方向

YAML 里加一行注释、把缩进从两格改成四格 —— 规则值逐位不变，而版本会换。这是选「字节」
的代价，方向安全（多一次假的「版本变了」，而不是少一次真的）。反过来「散解码后的值」
也不是更好的口径：那时版本描述的是一份被解释过的东西，而真正被部署的是盘上的字节。
这条取舍写了判据，免得日后被当成 bug「修」成散规则值。

### 指引走 `get_assignment`，**不**走 Quay 的 task 正文

一个必须回答的问题是：钉住的指引怎么**到达**执行者。查了 Quay 的实现（结论来自后台
调查，v0.11.0-dev 的 `plugin/scripts/worker-driver.ts:1709-1722`；本机的 0.4.0 树里那个
文件**不存在**，所以这一条要以「版本外证据」读）：

```js
export function buildWorkerPrompt(task: string, root: string): string {
  return [
    `You are a per-task worker in the quay repo ...`,
    `Task: ${task}. Repo root: ${root}.`,
    `Run the implementation chain: (1) create an isolated git worktree for ${task}, ...`
```

worker 的 `-p` 载荷里**只有 task id 与仓库根**加固定指令链，正文一个字节都不插值；
`Task.body` 确实在 ABI 里、也确实能通过 `task_get` / `task view --json` 读到 —— 但那是
worker **自己去拉**的一条通道。所以「把指引写进 task 正文」不是推送，是一条旁路，
而它绕开的恰恰是 §6 要的那条链（`ExecutionUnit` 在 `ports/dispatch.py` 里明确
「没有 `extra` / 自由 JSON 的入口。只给 label，因为只有 label 是**可查的**」）。

规格自己写好了通道：`docs/MCP_SERVICE.md:47` 的 `get_assignment` 要返回
"Frozen Scene/**guidance**"。**指引由 MarketOps MCP 承载，worker 拉取** —— 于是 H3b 的
钉住落在 WorkItem 上、由 `get_assignment` 报出，Quay 适配器一个字节都不用改。

### 一件属于停报清单的事：指引**正文**还没写

`skill/SKILL.md` 是**业务内容**（SOP 派生的程序性指引），写它接近「更改 SOP 业务定义」——
按停报清单该由人点头或指向既有权威来源。所以本片与 H3b 只做「版本 + 钉住」这个**机制**，
不发明业务散文。机制现在就对，正文后补时**进版本是自动的**。

### 这一片的执行器

`1492 passed`（H2c 之后是 1487）、`ruff check .` 干净、契约与 committed 逐字节一致。
新判据的 **5 条变异全部打中**，且每条都只被**它该打中的**那条判据打中：

| 变异 | 打中的判据 |
|---|---|
| 版本走写死的三文件清单 | `test_a_guidance_file_enters_the_version` |
| 只散文件名、不散字节 | `test_one_byte_of_one_file_changes_the_version` |
| 去掉排序 | `test_the_version_does_not_follow_the_order_the_filesystem_lists` |
| 不递归（`glob` 替 `rglob`） | `test_a_guidance_file_enters_the_version` |
| `get_scene` 报空版本 | `test_scene_reports_the_effective_guidance_version` |

第 3 条判据是**单向的**，这一点没有藏起来：文件系统若对两个目录恰好按同一个顺序列出，
一个不排序的实现也会通过，所以那条用例在顺序相同时**跳过而不是假装验证过**。本机
（XFS + `/data/scratch` 下的 TMPDIR）实测**没有跳过**，去掉排序时真的红了 —— 单向，
但这次真的走到了输入。

---

## 2.15 Phase H 实际怎么落的（2026-09-22）：把版本**钉在 work item 上**，以及那个还没做的缺口（H3b）

§6 逐字：

> **Running tasks retain their pinned guidance.**

H3a 让「指引的版本」存在且可读（目录摘要，`get_scene` 报出）。H3b 把那个版本
**钉在 work item 上**，并且让它可读、可拒绝。这一片回答的是「一件正在飞的工作按
哪一份规则在跑」—— 一个在此之前没有答案的问题。

### 钉在 **work item** 上，不是作业上、也不是这次调用上

`GuidancePin(work_item_id, scene_version, pinned_at)`（`domain/job.py`）。

* 不是作业级的：同一个作业的工作单元是**在不同时刻**被领走的（暂停、resume、
  结算之后又派发下一件）。按作业记一个版本时，后编译的那一批会**连带**改掉先
  编译的那一批的版本 —— 而这正是要防的那件事。
* 不是调用级的：一次调用结束之后，「它当时按哪一份规则跑的」必须还在盘上。

`tests/test_job_contract.py::test_every_work_item_in_the_batch_carries_its_own_pin`
是**逐件**这条的判据（变异：只在 `ordinal == 0` 时写钉 → 打中）。

### 独立一张表，不是 `WorkItem` 上的两列

`work_item_guidance`（`adapters/sqlite/db.py`）。理由是 `ensure_schema` 的既有
规则：整份 DDL 每次都重放，所以**加表自愈**；改列不行，那要新建一列 + 回填 +
保留旧列，是一次真正的迁移，方向和旧库去留必须由人决定。

这张表**可以没有对应的行**，而那不是「数据坏了」——在这个机制出现之前编译的
work item 在盘上就是没有钉。`ensure_schema` 只负责把表建出来，**不**给历史行
补一个版本（补出来的那个是编的）。缺席由读的那一侧面对：

* `get_assignment` **拒绝**领活，并引 §6 的原文；
* `compile_work_items` **拒绝**重新编译那一批，并说明它们是在机制之前编译的。

### 写它的入口**只有一个**，而且与工作单元在同一个写事务里

`compile_work_items(self, job_id, subjects, *, scene_version: str)` —— 版本是
**必填的关键字**（忘记传得 `TypeError`，而不是一批钉在空版本上、看起来完全正常的
工作单元），且两条 `INSERT` 在同一个 `write_transaction` 里。一次编译之后，
「有没有这批工作」与「它按哪一份规则在跑」不可能分家；进程死在这两句中间也不会
留下一批没有钉的工作。

版本在 `_resolve_and_compile` 里读、且**只在那里**读 —— 它是受理与 resume 共用
的唯一一条编译路径，所以放在那里等于「没有任何一条路径能编译出一批没有钉的工作
单元」。读在**阻塞判据之后**：输入产不出来时走的是 `BLOCKED`，一次装载失败不该
把「清单还没补齐」变成一次错误。

### 重新编译**拒绝**，不重钉、也不静默返回

`ports/jobs.py::repin_refusal(pin, scene_version, job_id) -> str` —— 与
`settlement_outcome` 同住端口层，同一条理由：两个实现共用的**同一份判定**，
各写一遍的后果不是文案不一致，是两个实现放行/拒绝的**条件**先分家。

三条出口各自的理由：

| 情形 | 处置 | 为什么不是别的 |
|---|---|---|
| 钉住的版本 = 现在这个 | 幂等返回同一批 | —— |
| 钉住的版本 ≠ 现在这个 | 拒绝，理由里带**两个**版本 | 重钉会把历史改成一份它们没跑过的规则，而没有任何一处会报错；静默返回则让调用方以为刚才那份规则生效了 |
| 没有钉（`None`） | 拒绝 | 就地补一个版本会让它们看起来像是**这一次**编译出来的 |

### `get_assignment` 同时报两个版本 + 一个服务端算好的比较

```json
"guidance": {"pinned": "...", "pinned_at": "...", "effective": "...",
             "pinned_is_effective": false}
```

`scene` 块里那套规则是**此刻**从盘上装载的那一份；`guidance` 块是这件工作
**编译时钉住**的那一份。两者在编译之后有人改过 profile 时不是同一份 —— 所以
回执把两件事分开报，并在 `notes` 里写明「那个 `false` 是要读的，不是一个装饰」。

比较放在服务端算，不留给读的人去比两个字符串：两个值一样长、一样是十六进制，
**比错方向不会报错**，只会让人按新规则去解释一批老工作。

### 一条**被命名的**缺口：内容层面的保留还没有做

上面做到的，全部是「**说得出来**」，不是「**守得住**」：

> 一件正在飞的工作不会**静默地**换规则（回执里两个版本都在），
> 说不出自己按哪一份在跑时会被**拒绝**（`get_assignment`）。
> 但它**仍然是按现在从盘上装载的那份规则跑的**。

内容层面的保留 —— 把编译那一刻的 `ScenePack` 按版本**冻下来**、`get_assignment`
按版本读回来 —— **还没有做**。今天 `scene_version` 是一个标签，不是一个可寻址的
历史。写在这里而不是含糊过去，是因为这两件事在报告上长得**一模一样**：都报出了
正确的版本号，只有改过 profile 之后再跑一件老工作才能把它们分开。

这一条是 Phase H 之后第一件该做的事，也是 §2.14 那份「指引正文还没写」的停报清单
之后的下一格。

> **2026-09-29 补记 —— 这一格已经闭合；上面那一段保留为写下它那一刻的记录，
> 不是今天的读数。**
>
> 「内容层面的保留」现在做了：编译那一刻的声明按 `scene_version` **连字节存下来**
> （`scene_snapshot` 表），`decide` 与 `get_assignment` 都按 `work_item_id` 把那批
> 字节**读回来再编译**，读不回来时**拒绝**，不回退到盘上那一版 —— 所以
> `scene_version` 不再只是一个标签，而是一个可寻址的历史。
>
> 判据在 `tests/test_mcp_decision_tools.py`：真的改一次盘之后，在飞的那件报出的
> `spec_version` 与改盘之前逐字相同，而改盘之后新受理、编译的那件报出新的那一个。
> 它自己先断言「盘上那一份真的换成了另一版」，所以在一个恒读盘上 profile 的实现
> 上会转红 —— 不是一条空判据。

### 这一片的执行器

`1506 passed`（H3a 之后是 1492）、`ruff check .` 干净、契约与 committed 逐字节一致。
新判据的 **6 条变异全部打中**，且每条都只被**它该打中的**那条判据打中：

| 变异 | 打中的判据 |
|---|---|
| `repin_refusal` 的 `None` 分支改成放行 | `test_a_batch_with_no_pin_is_reported_as_unpinned_not_guessed` |
| 只给 `ordinal == 0` 那件写钉 | `test_every_work_item_in_the_batch_carries_its_own_pin` |
| 内存实现把钉的版本写成空串 | `test_the_compiled_batch_records_the_version_it_was_compiled_under` |
| `_assignment` 不判有没有钉 | `test_a_work_item_without_a_pinned_version_cannot_be_claimed` |
| `pinned_is_effective` 恒 `True` | `test_a_work_item_pinned_to_another_version_says_the_two_disagree` |
| 没有那一行时 `_pin` 编一个出来 | `test_an_unknown_work_item_has_no_pin` |

**「同一事务」这条性质没有被变异证明**，这一点不藏起来：把两条 `INSERT` 拆到两个
事务里在文本上不是一处可以替换的锚点。它的可观察后果就是「一批没有钉的工作
单元」，而那正是 `test_a_batch_with_no_pin_is_reported_as_unpinned_not_guessed`
用一条 `DELETE` 复现出来的状态 —— **复现了后果，没有复现成因**。真正的保障是
「公开入口只有 `compile_work_items` 一个」，那是一条代码形状上的事实，不是一条判据。

---

## 2.16 Phase H 之后：把 `pyproject` 的依赖表变成一条**真的**判据（H3c）

债表第 1、2 条是同一件事的两个方向 —— **说了，却没有执行器**：

* `mcp` 被整片服务面用着（`marketops/adapters/mcp_server/` 四个模块），而它不在
  `pyproject.toml` 的任何依赖或 extra 里。D1 的 pin `>=2.2,<3` 只写在 §4 的散文里。
* `beautifulsoup4` 声明着，而全仓没有一处 import 它 —— 正文抽取走的是 pypdf 与
  正则（`adapters/text_pdf.py` 自己的 docstring 这么写着）。

两条的症状不同，而**后者更坏**：少一个依赖会当场坏（干净安装之后
`packages = ["marketops"]` 会把 `marketops/adapters/mcp_server/` 一起装出去，
那是一个装得上、import 不了的包）；多一个依赖**永远不坏**，它只是让下一个读这张表的
人以为 HTML 是 BeautifulSoup 解析的。一条会坏的债会被修，一条只会误导的债会被继承。

**而两条都不会在本仓现形**，这是它们能一起站到今天的真正原因：

* `mcp` 在测试环境里装着 —— 当时的 1506 条判据全绿，故障只在别人的干净安装上。
* `beautifulsoup4` 有**两个证人互相印证**：`pyproject` 那一行，与
  `interfaces/cli.py` 那句 `("beautifulsoup4", *_probe("bs4"))`。
  两个证人，一句假话，没有任何一处会响。

  （第三个提到它的地方是 `adapters/__init__.py` 那张表，而它写的是「无 `bs4`」
   —— **站在真话那一边**。这条记下来是因为它是个反例：三处里有一处是对的，
  而「有没有一处会响」与「有没有一处是对的」是两件事。）

### 为什么映射表是手写的

发行包名与顶层 import 名不是一回事（`PyYAML` → `yaml`、`typesafe-sdk` →
`typesafe_sdk`）。这件事本来可以问 `importlib.metadata.packages_distributions()` ——
而它只认识**装上的**那些，本仓四个能力型 extra（`crawl4ai` / `mineru` / `laya` /
`typesafe-sdk`）**刻意没有安装**（按需启用的重依赖）。所以那条路只能答一半，
而**答一半的清单比没有清单更坏**：它会让这张表看起来是被校验过的。

手写的表因此自己也要被守：它的键必须与 `pyproject` 声明的那一组**恰好相等**。
人维护的清单与代码对照，漏掉的那一个不是「没问题」，是「不受管」。

### 判据的分工（这一片最重要的一张表）

`tests/test_declared_dependencies.py`。四条判据，**每条只抓一件事**：

| 判据 | 它读什么 | 它抓什么 | 它**不**抓什么 |
|---|---|---|---|
| 表 == 清单 | `IMPORT_NAMES` 键 vs `dependencies` ∪ 能力型 extra | 新依赖没进表、旧声明没出表 | 有没有人 import |
| 声明了有没有人用 | 扫描集合 vs 声明集合 | `beautifulsoup4` 那一条 | **未声明**的 import |
| import 了有没有声明 | 扫描集合 vs **映射表** | `mcp` 那一条；`dev` 工具混进运行代码 | 声明被删（上一条抓） |
| 探针表探的是什么 | `cli.DEPENDENCY_PROBES` vs 映射表 | 第二个证人替不存在的东西作证 | 别处还有没有假证人 |

第二、三条读的是**不同的集合**（清单 / 表），这不是重复：第三条读表、第一条保证表
可信，两条合起来才等于 `import ⊆ 声明`。所以**把一条依赖从 `pyproject` 里删掉时，
红的只有第一条** —— 「表不再可信」才是要先修的那件事。这个分工是变异测试里第一次
跑才看清的（M1 的预期原本写了两个判据会红，只有一个红了）。

### 一条**走不到**的判据（写变异测试之前自己抓出来的）

工具链那条分支（`dev` 的成员出现在 `marketops/` 的 import 里）最初是不可达的：
`import pytest` 会先被「未声明」那条断言吃掉。而两条的**修法相反** ——
一个是「声明它」，一个是「把它从运行代码里删掉」—— 于是那条断言会给出一个错的修法。

改法不是在别处补一句说明，而是让 `_undeclared` 把工具链的名字**减掉**：两条分支各自
可达，各自带自己的诊断。随后 M4 注入 `import pytest` 并**读了 traceback**，
确认走到的是泄漏那一支、带的是它自己的话（"工具链依赖出现在 ('marketops', 'services')
的 import 里：['pytest']"），而不是那条「未声明」的通用话术 ——
「打中了那条判据」不等于「打中了那条判据里该打中的那一支」。

同一条纪律也用在正对照上：它原本拿 `boto3` 当探针，而变异 M3 恰好往表里塞了 `boto3`，
于是正对照**因为表长了一行而转红**。一条这样的正对照证明的是「表里有什么」，
不是「函数会不会红」—— 探针因此换成一个不可能是真实发行包的名字，
并把这个理由写在判据里（那正是 `beautifulsoup4` 事件在测试内部的同形复现：
一个证人，因为别处改了一个字，就悄悄不作数了）。

### 探针表：第二个证人也必须被执行

`interfaces/cli.py` 的 `("beautifulsoup4", *_probe("bs4"))` 是那句假话的第二个证人。
它原本写在 `rows` 里的一串字面量中，而**另外三条判据都看不见它**：一条只管已声明的
有没有人用，一条只扫 **import 语句**（探针名是两个字符串字面量），第一条只看表与清单。
所以这一类假证人随时可以再生。

改法是把探针拆成数据 `DEPENDENCY_PROBES: tuple[tuple[str, str], ...]`（模块名 + 人读的
标签），让第四条判据能直接读它、要求「`check-deps` 报告的每一个名字都必须真的是一条被
声明的依赖」。CLI 的输出逐行没变。

顺带把**扫描范围也交给 manifest**：`[tool.hatch.build.targets.wheel] packages` 里的每个
包都必须在 `SCANNED_ROOTS` 里。往 wheel 里加一个包而不加扫描范围，那几条判据会对新包
一个字都说不出 —— 而它们仍然全绿（M6）。

### 这一片的边界（写下来，不假装没有）

* 扫描读的是 **import 语句**。运行期拼出来的名字（`__import__(变量)`、
  `importlib.import_module(f"…")`）看不见。今天全仓唯一的这种调用点是
  `cli._probe`，而它的输入现在是常量 `DEPENDENCY_PROBES`、有自己那条判据 ——
  **这一条路径被覆盖了，但这个类别的洞没有**。
* 扫描范围是 `marketops/` 与 `services/`，即会被运行的东西。`tests/` 不在里面 ——
  它的依赖由 `dev` 那张表反向管（工具链不得出现在运行代码里）。`scripts/` 还不存在；
  它出现时这段范围要跟着改，而那是一条**没有执行器**的约定。
* `_distribution` 在第一个非包名字符处切分（`mcp>=2.2,<3` → `mcp`）。环境标记
  （`; python_version < "3.12"`）会被正确切掉，但**条件本身不被理解** ——
  一条带标记的依赖在这里读作无条件。

### 这一片的执行器

`1515 passed`（H3b 之后是 1506；新增 9 条判据）、`ruff check .` 干净、契约与 committed
逐字节一致。**7 条变异全部打中**，且每条都只被**它该打中的**那条判据打中：

| 变异 | 打中的判据 |
|---|---|
| 把 `mcp` 从 `dependencies` 里删掉 | 表 == 清单 |
| 把 `beautifulsoup4` 声明回去并补上映射 | 声明了有没有人用 |
| 给映射表塞一个没有声明的键（`boto3`） | 表 == 清单（顺带打红正对照 → 已改成合成探针名） |
| 让 `import pytest` 出现在 `marketops/` 里 | import 了有没有声明（**泄漏那一支**，读 traceback 确认） |
| 探针表里加一行 `("bs4", "beautifulsoup4")` | 探针表探的是什么 |
| 往 wheel 的 `packages` 里加 `config` | 扫描范围非空 |
| 删掉 `_unused` 里「跳过工具链」那一格 | 声明了有没有人用 **+** 它自己的正对照（两条一起红是对的） |

还原用 `cp -p`（**绝不** `git checkout`）、前置锚点自检、还原后逐字节 md5 校验一致。
**变异在最终树上跑的**：中途加的 `_unused`（见下）改动了制品，所以上一轮的 6/6
不作数，重跑成 7/7。

### 顺带抓到的第三处：一条**名不副实**的注释

写正对照的时候发现「声明了有没有人用」那一侧根本没有正对照，而它旁边那句注释
（「而『声明了没人用』那一侧的正对照」）读起来像是有的 —— 这正是这一片在治的病
（**说了，却没有执行器**），只是病灶长在我自己的测试里。改法是把那段循环抽成纯函数
`_unused(...)`，它的表与 extra 集合都是**参数**，于是正对照可以用合成的输入、
不受这张表实际内容的影响（这个教训是 M3 给的），并顺手把 `dev` 那格豁免也变成一条
判据：工具链成员全都「声明了没人 import」，少了那格跳过，pytest / ruff / jsonschema
会一起被报成债 —— 而正确的动作是把豁免删掉，不是去 import 它们。

---

## 2.17 结掉债 #4：缺省值不得充当一次没人做过的判定（2026-09-22）

债表第 4 条逐字是「``AccountRecord.country`` / ``Evidence.country`` 的缺省是 ``"UK"``」。
**这一条里有一个从来没有过的字段名**：``Evidence`` 上根本没有 ``country``，第三处是
``AccountRecord.country``，它住在 ``domain/evidence.py`` 里。行号是对的，名字是错的 ——
而这一整张债表要治的病正是「说了，却没有执行器」：一个照着这句话去 grep
``Evidence.country`` 的人会得到「不存在」，而他的下一步通常是怀疑自己的理解。

这与 §2.5 的 ``mint-token`` 是同一条纪律的另一面。那一次我们补的不是功能，**是那个
名字** —— 因为 ``TokenRing.from_file`` 的报错点名了它，而「提到一个名字，那个名字就得
真的是它」。这一次方向反过来：债表点名了一个**不存在**的名字，于是要修的是那句话。

### 为什么缺省 ``"UK"`` 是一台**伪造证据**的机器

三处 ``country`` 都只做字面透出，但只有一处有消费者：``account_facts`` 把它投影进事实
字典，而 ``grading.yaml`` 有一条

```yaml
  - id: out_of_region
    requires: ["!country in [gb, uk]"]
```

**取反、且没有正面守卫。** 于是「还没查这块的区域」与「查过了，不在区域内」在缺省值上
长得**一模一样**：缺省 ``"UK"`` ⇒ ``country in [gb, uk]`` 为真 ⇒ 取反为假 ⇒ 闸门不满足
⇒ 规则**静默不触发**，报告读成「这个账号在区域内」。

它不是假绿，是**假红缺席**：本该落在 ``unchecked`` 里的那条规则被记成了「核对过、
没问题」。而 ``unchecked`` 是本仓唯一一处「哪些事实还没有值」的**机器可读**清单
（``DeclaredGrading.ungradeable`` 记的是装载期那一半、即「哪些事实名还没有实现」）——
**缺省值把这份清单变短了**。

按方言（``application/grading.py`` 的 ``evaluate_clause``）：``None`` = **还没查**，
未确认的子句在任何极性下都不决定；``""`` = **证据**（「查了，是没有」），可以被取反。
缺省 ``"UK"`` 两者都不是 —— 它是一个**没人做过的判定**。

### 换判据，不换代码：那条**设计好会红**的跳线

``tests/test_grading.py`` 里本来有一条自己声明会红的跳线，逐字是：

> 这条断言的价值在于它**会红**。某天有人给某个字段加上「没有就说 ``None``」的
> 生产者，这里立刻红，而红的时候指向的语义已经在上面钉好了。

它守的正是今天。于是按本仓「**换判据，不换代码**」的规矩，正确的动作是**拆开**它，
而不是把那一行删掉：

| 原来 | 现在 |
|---|---|
| 路线侧 + 账号侧一起断言「都不产出 ``None``」 | ``test_the_route_projection_produces_no_unconfirmed_value_today``：路线侧照旧（今天仍不产出 ``None``，跳线留着） |
| — | ``test_an_unresearched_country_projects_as_none_and_a_declared_one_does_not``：账号侧三个取值各有断言 |
| — | ``test_a_missing_country_leaves_out_of_region_unchecked_rather_than_firing``：**真实 profile** 上的活体判据，带正对照 |
| — | ``test_a_missing_country_is_not_a_country``：**三处**缺省一起断言 |

留着原来那一行只有两种下场：一直红着没人管，或者有人把那一行删掉 —— **两种都让路线侧
一起失守**。

活体判据的正对照是同一份 bundle 上的 ``country="US"``：它**必须**真的被判
``irrelevant``。没有这一半，那句「没写国家必须落 ``unchecked``」可以被一个「永远不排除
任何账号」的实现满足，而那个实现会让越界账号一直留在名单里。

### 「显式空串」为什么单独有一条断言

把 ``""`` 也折成 ``None`` 是这次改动最像样的走法，而它与要修的错**方向相同**：那会让
一条「确认没有国家」的记录变成一条没人查过的记录。两个方向都要有执行器，所以三个取值
各占一条断言。变异 M4 打的就是这一条，而且**只**打中它。

### 一条**不是**判据的改动（说清楚，不冒充）

``tests/test_profiles.py`` 里把词表**抄成字面量**的 ``("country", "has_domain",
"has_notes")`` 换成了 ``ACCOUNT_FACT_NAMES``。它**不增加检出力**：profile 装载层用同一份
常量并且 fail-closed，所以「profile 引用了不存在的词」**已经**有人拦。

它去掉的是**假红**：抄一份副本之后，给这份 profile 加一条引用新事实的规则时，装载层拿
新词表放行、这条判据拿旧副本报「未知事实」—— 它会在一个**正确的**改动上转红，而报文读
起来像「profile 写错了」，修法看起来是去改 profile。那正是「换判据，不换代码」的反面。

### 顺带抓到的两处**已经说了假话**的文档

1. ``docs/CURRENT_STATE.md`` 写着「``grade_account`` 在真实 profile 下**恒返回
   ``DISCOVERED``/``UNRESOLVED_FACTS``，账号永远不被排除**」。这句话在本片之前**就已经
   是假的**：声明 ``country="US"`` 的记录一直会被真的判成 ``irrelevant``。今天有判据
   执行它了，所以改成分开写：4 条落 ``unchecked``、第 5 条**真的会判**，以及没写国家时
   落 ``unchecked``。
2. ``application/grading.py`` 里 ``evaluate_clause`` 的 docstring 写着「两条投影今天都
   不产出 ``None``，所以『值 ``None``』这条路在公开面上暂时没有消费者」—— 这句话是本片
   **自己**弄假的（那个生产者就是我加的）。已改成「这条路今天已经有消费者了」，并保留
   路线侧那半。

### 这一片的执行器

``1518 passed``（H3c 之后是 1515；删 1 条跳线、新增 4 条判据）、``ruff check .`` 干净、
契约重生成与 committed 逐字节一致（**三份**契约的 ``country`` 从 ``{"default": "UK",
"type": "string"}`` 变成 ``{"anyOf": [string, null], "default": null}``；
``evidence.schema.json`` **没动** —— 这本身就是那个假字段名的第二个反证）。

**7 条变异全部打中**，每条都只被它该打中的判据打中：

| 变异 | 打中的判据 |
|---|---|
| ``AccountRecord.country`` 缺省改回 ``"UK"`` | 账号侧三条（投影 + 活体判据 + 三处缺省） |
| ``VenueAsset.country`` 缺省改回 ``"UK"``（另两处拷贝之一） | 三处缺省那条（**证明另两处也有执行器**） |
| ``account_facts`` 把 ``None`` 兜成 ``"UK"`` | 投影 + 活体判据 |
| ``account_facts`` 把确认的空串折成 ``None`` | **只有**「显式空串」那一条 |
| ``evaluate_clause`` 把 ``None`` 当成一个值 | 活体判据 + 未确认那组机制测试（整套红 14） |
| 把 ``country`` 从 ``ACCOUNT_FACT_NAMES`` 里删掉 | 由**装载层 fail-closed** 拦下，判据随之 ERROR |
| ``route_facts`` 产出一个 ``None`` | 路线侧那条跳线（**证明它没死**） |

还原用 ``cp -p``（**绝不** ``git checkout``）、前置锚点自检（先用一个不存在的锚点证明
自检会拒绝）、还原后逐字节 md5 校验一致、独占临时目录已删。

### 这一片自己的两次错

**第一次：M6 的预期错了。** 我以为「删掉词表里的 ``country``」会打红 ``test_profiles.py``
那条判据的**断言**。实际打红它的是**装载层**（profile 装载期就 ``ConfigError``），判据
根本没跑到断言 —— 与 §2.16 的 M1 是同一种误判：**预测错了哪条判据会响**。这也正是上面
那条「不是判据的改动」的由来。

**第二次：结果分类器漏了 ERROR。** 第一轮 7 条里有 1 条被报成「漏网」，而 pytest 的报文
里明明有它 —— 因为 ``-rf`` 的 ``f`` **只管 FAILED**，fixture 炸掉的用例进的是 ``ERRORS``
那一栏，stdout 里根本不出现。两处都改了，并**整轮重跑**：制品在两次之间被改过（一条
注释、一句 docstring），按本仓的规矩上一轮结果一律不作数。

---

## 2.18 结掉债 #3：唯一一处决定「该不该交给 MinerU」的代码，此前没人跑过（2026-09-22）

债表第 3 条逐字是「``TextPdfParser`` 至今没有直接判据：只有 ``extracts_links is False``
与一条同形替身；它的正文抽取没有执行器（E3 补的是链接那一半）」。

### 为什么这是一条真债，而不只是覆盖率不好看

``tests/test_parsers_report_links.py`` 的头一段写着「HTML 抽取这一组**不**用替身……
这里写进文件、读回来，走的是真的那条路」。**PDF 那半走的不是那条路**：当时与
``TextPdfParser`` 同形的唯一对象是 ``tests/test_mcp_research_tools.py`` 里的一个替身
parser，而它测的是**服务面回执怎么读 ``extracts_links``**，不是 pypdf。于是一个替身的
绿被读成了 pypdf 的绿 —— 同形替身最典型的失效方式。

而 ``TextPdfParser.parse`` 是本仓**唯一一处决定「这份 PDF 该不该交给 MinerU」**的地方。
它判错的两个方向都有价钱：

* 把真扫描件读成「这个站点没有邮箱」→ 污染 §10 的 net-new C/D 指标（这正是它 docstring
  里写着不硬撑的那个理由）；
* 把一份正常文档推进 MinerU → 那条路要下几百 MB 的权重。

### 样本：**真的 PDF 字节**，但**不是**真实站点的 PDF

两句话都要说，混起来会得出一个错的结论。

**真**在：写进盘上的是真的 PDF —— 真的对象字典、真的 xref 表、真的内容流，``pypdf``
按自己的读法读。没有一处 monkeypatch、没有替身。手写而不是引一个 PDF 写作库，是因为
本仓的运行依赖只有五个，为了测试再引一个，会让「这份样本是什么」变成**需要装依赖才能
复核**的问题；这里的对象表与 xref 表在几十行之内，读的人可以自己数。

**不是**在：它是合成的，只覆盖 ASCII 正文 + 标准 Type1 字体 + 未压缩内容流。真实站点的
PDF 有压缩流、嵌入子集字体、CID 编码、多栏排版。所以它能证明「这条路径怎么走」，
**不能**证明「真实站点的 PDF 都读得对」。

由此有一条**明确不被授权**的事，写进了两处 docstring：这**不构成** ``TextPdfParser``
里那句「本构建没有一份真实的 PDF 样本去校准那条读法」的样本。PDF 的链接住在
``/Annots`` 的 URI action 里，那条读法要的正是真实站点的编码多样性 —— 新判据没有一条
碰它，``extracts_links`` 照旧是 ``False``。**一条新判据不构成对另一条欠账的清偿**，
这句话必须写在明处，否则下一个读到「有 PDF 测试了」的人会顺手把链接打开。

### 两个阈值，四侧断言

``SCANNED_PAGE_TEXT_THRESHOLD = 40`` 是「这一页有没有可用文本」，``SCANNED_PAGE_RATIO
= 0.7`` 是「多少页空白算整份不可用」。两个都是**取舍**，两个方向都有价钱，所以每一侧
都要有断言 —— 只测一侧的实现，另一侧的改动就只是「测试还绿」：

| 判据 | 这一侧 | 那一侧 |
|---|---|---|
| 阈值 | 39 个字符的页**算**空白（触发 warning） | 41 个字符的页**不算**空白（``warnings == ()``） |
| 比例 | 2/4 收下并警告 | 3/4 拒绝、报文点名 MinerU fallback |

只测「短的那页触发警告」会被一个「凡有文字就算有文本」的实现满足 —— 而那个实现会让
真扫描件里夹着的一行页码把整份文档放行。

**写这一组时我自己踩了它**：第一版样本里 33 个字符的一页被算成了空白页，红起来像是
「阈值坏了」，实际上坏的是样本。所以模块 docstring 里写死了这条：「每一个『有文本的页』
都必须真的超过 40 个字符」，``BODY_A`` 因此是刻意写长的。

### 两条拒绝分支不能合成一条

「全是空白页」与「大部分空白」都抛 ``ParseError``，报文里**都有**「疑似扫描件」。分得开
它们的是下半句（「全部空白」vs「应交给 MinerU fallback」），而这两句话对应两种不同的
下一步：前者是这份文档本身的问题，后者是「也许 MinerU 还能救」。

断言因此写在**能分开两者的那一截**上，并**各配一条反向断言**（「另一支的那半句不许
出现」）。只查「报文里有『疑似扫描件』」的话，把两条合并、或者把分支写反，判据照样绿。

### 失败必须是**域错误**

``parse`` 的调用方（``document_router``）只认 ``ParseError``。一个漏出来的
``PdfStreamError`` 会穿过 router 的 except 冒到 pipeline 顶层，而它长得像「解析器崩了」，
处置是改代码；实际情形是「这次抓回来的东西不是 PDF」，处置是换 parser 或记一笔 blocked。
所以有一条判据专门把这层翻译钉住，并顺带钉住报文里**带文件名**（多份 PDF 一起失败时
分得出是哪一份）。那条 ``except Exception`` 上的 ``# noqa: BLE001`` 注释写着「pypdf 的
异常类型不稳定」—— 一个不稳定的外部类型名现在有了执行器。

### 这一片的执行器

``1526 passed``（本片之前 1518；新增 8 条判据）、``ruff check .`` 干净。

**10 条变异全部打中**，每条都只被它该打中的判据打中：

| 变异 | 打中的判据 |
|---|---|
| 关掉「没有页面」那条分支 | 无页面 |
| 删掉「全是空白」那条分支（两条合一、顺序也反） | 另一条拒绝分支 |
| 阈值 40 → 0（凡有文字就算有文本） | 阈值两侧 + 比例两侧 |
| 比例 0.7 → 0.4（一半空白就交给 MinerU） | 比例两侧 |
| 收窄 ``except``（让 pypdf 的异常类型漏出去） | 失败变成域错误 |
| 失败报文里去掉文件名 | 同上（钉住「带文件名」那半） |
| 摘要改成文件名的摘要（不是字节的） | 文件字节的摘要 |
| warning 恒非空（噪音化） | 干净 PDF 无 warning + 阈值那一侧 |
| 页与页用空格拼（丢掉那个空行） | 页面顺序与分隔 |
| ``can_parse`` 不再看 ``content_type`` | 两条入口都能认 PDF |

还原用 ``cp -p``（**绝不** ``git checkout``）、前置锚点自检、还原后逐字节 md5 校验、
独占临时目录已删。**制品改过之后整轮重跑**：两条 docstring 在本片中途改过，按本仓的
规矩上一轮结果一律不作数。

---

## §2.19 上游对照读的是 pin，不是工作树（债表第 5 条，2026-09-22）

**债表第 5 条**：``tests/test_sources_snapshot.py`` 的上游对照只在有人裸跑套件时才跑。

**这一片发现它是两条缺陷叠在一起，而且它们互相掩盖。** 一条让它从不跑，另一条让
它在真的跑起来时**读错对象** —— 后者只有把前者修好才会显形。

### 实测（全部只读，没有动 records 一个字节）

| 事实 | 值 |
|---|---|
| ``sources/manifest.yaml`` 声明的 commit | ``b81eb7760e6c…`` |
| 六条条目的 ``upstream_blob_sha`` vs ``rev-parse <commit>:<upstream_path>`` | **6/6 相符** |
| 其中 ``member-d-private`` 的工作树 | 被别人改过未提交（``04ff0b5c…`` vs 声明的 ``85ac620f…``） |
| records 检出的 HEAD | ``b81eb7760e6c…``，与声明**逐位相同** |
| ``member-d-private`` 这条快照 | **0 行**（``none-matched``）—— 逐字子集检查对它本就空转 |

所以**manifest 是对的**，红的原因是判据读错了对象：它主张的是「快照是 commit
``b81eb776`` 的一个子集」，而去读了**此刻磁盘上**那棵树。本仓规范明说 records 的
工作树**可以**一直带着别人的 WIP（CLAUDE.md：不得 reset / clean / 不得丢别人的 WIP），
于是这是个**常态**假红，而它的报文说的是「上游被重新同步过」—— 一个关于**历史**的
结论，处置是去改 provenance（§8：追加新条目），而真正该做的是**什么都别做**。

### 两条缺陷

1. **一个环境变量兼两个决定。** ``MARKETOPS_RECORDS_ROOT`` 同时回答「自足不变量那半
   用哪个检出」（``test_business_sources.py``：找不到就 **FAIL**）与「上游对照那半有
   没有检可用」（找不到就 **SKIP**）。本仓规范的套件调用把它指向一个不存在的路径
   —— 那**正好**让上游对照每一次都 skip。而判据自己的补救提示（「要跑它：
   ``MARKETOPS_RECORDS_ROOT=/abs/path/to/records``」）会让**另一类**测试开始读真实
   records：两个需求在一个变量下互斥。
2. **判据与它的主语不在同一个空间。** 声明的两个量都在 commit 空间，读的却是工作树。

### 处置

- 上游对照有自己的变量 ``MARKETOPS_UPSTREAM_RECORDS_ROOT``；解析抽成纯函数
  ``_resolve_upstream_root(env, start)``，**不读** ``MARKETOPS_RECORDS_ROOT``。
  点名的目录不存在时返回 ``None`` 且**不回退**到上溯（退化了的话，人给的那个值就
  推不出跑的是哪个检出）。
- 三条判据改读**对象库**：``rev-parse <commit>:<path>`` 拿 blob 哈希与声明比，
  ``cat-file blob <sha>`` 取字节用于逐行比对。参数里从不出现工作树路径，顺带
  没有「``git -C`` 把相对路径拼两遍」那条风险；**字节**读取，因为六条里两条是图片。
- 三种失败分得开：声明的 commit 不在检出里／路径在这个 commit 上不存在／blob 变了。
  旧报文把三者都说成「上游被重新同步过」。
- **``manifest`` 与 ``sources/`` 一个字节都没改** —— 它们是对的。

### 一条既有判据挡住了我的第一版设计（记下来，因为它是**对的**）

我第一版把「缺席就 skip」搬进了夹具（夹具返回 ``Path`` 并自己 ``pytest.skip``），
看起来更干净。**``tests/test_no_stale_assumptions.py`` 里两条判据立刻转红**：

- ``test_every_test_needing_the_upstream_checkout_can_skip`` 用 **AST** 回答「这个测试
  要不要外部检出」，要求每个依赖上游夹具的测试**函数体里**有 ``pytest.skip``。
  跳过搬进夹具后，「哪些测试是可选的」从源码里消失了。
- ``test_upstream_fixtures_do_resolve_to_none_without_the_checkout`` **真的把「上游不在」
  做了一遍**：设 ``MARKETOPS_RECORDS_ROOT`` 指向坏路径，断言两个夹具都返回 ``None``。

我改的是**设计**，不是那条判据：夹具仍然返回 ``Path | None``、三条判据体内仍然各写
一次 ``pytest.skip``。**那条判据的「把上游不在真的做一遍」现在要用新变量造**（旧旋钮
已经不再决定上游对照的有无 —— 那正是本片修掉的东西），因此同一条断言顺带钉住了
「点名坏路径 ⇒ ``None`` 而不是退化成旁边那个检出」这个决定。

### 这一片的执行器

``1536 passed / 3 skipped / 0 failed``（本片之前 ``1526 passed / 6 skipped``；新增 7 条
判据，**且三条上游对照从 skip 变成真的跑**）。三条剩下的 skip 是
``test_business_sources.py`` 的 dossier 交叉核对 —— 它的探测判据是
``projects/marketops/items/``（dossier 常先落在 records 的一个分支上），与上游快照的六条
来源是**两件事**，不并到这里。

**9 条变异全部打中**：

| 变异 | 打中的判据 |
|---|---|
| 上游对照改读 ``MARKETOPS_RECORDS_ROOT`` | 两个变量两个决定 |
| 删掉显式指定那一支（只自动发现） | 不回退 + 两个决定 + 既有那条「真的做一遍」 |
| 点名的路径不存在时退化成上溯 | 不回退 + 既有那条「真的做一遍」 |
| 读 ``HEAD`` 而不是声明的 commit | 读的是声明的 commit |
| ``_commit_exists`` 恒真 | 两个探针在没有的时候说没有 |
| ``_is_git_checkout`` 恒真 | 同上 |
| blob 判据改回**读工作树** | 上游 blob 哈希（这条是本片要修的那个 bug 本身） |
| 探测判据 ``information`` → ``projects`` | 自动发现的正对照（附带打中「两个决定」） |
| 测试体里的 ``pytest.skip`` 换成炸 | §18 那条 AST 判据（证明它活着） |

一条**没构成变异**的对照：给一条测试加 ``# noqa``（不改变行为）⇒ 0 条红，符合预期。

**一条已知的分辨力边界**：``_upstream_commit`` 里「声明的 commit 不在检出里」这一支，
本机走不到（commit 就在），所以它没有变异能打中 —— 与 §2.17 的 M6 同类：
**它只在那个分支真的被走到的时候才有分辨力**，本片不为此造一个假检出。

### 本片顺带核出的两处文档不实

- ``docs/MVP_MIGRATION_PLAN.md`` §2.6 末把这次的红记成「即上游被重新同步过」——
  那是**错的**，而 ``docs/CURRENT_STATE.md`` §0（同一份仓里，更早）已经把这件事
  诊断对了。同一份文档里两个说法并存，而错的那一个读起来更像一个可执行的下一步。
- ``docs/CURRENT_STATE.md`` §0 的基线（``356 passed / 6 skipped``，且「这不是代码
  缺陷」）在本片之后两句都不再成立。

---

## §2.20 目录清理：`services/` 退场（2026-09-22）

落点：`services/` 整个目录（5 个文件）删除；它的推理搬进 `docs/NOT_BUILT.md`；
三处扫描范围各减一个成员；根 `README.md` 的布局一节从「一份追赶中的清单」
改成当前实际的树；新增 `docs/README.md` 作为 `docs/` 的索引。

### 为什么是现在，以及为什么不只是「删掉几个空目录」

`marketops/interfaces/__init__.py` 里早就写着这两个占位目录的处置属于目录清理，
并把两个选项都写下了：**删掉，或者把那段推理搬进 `docs/`**。两件一起做了 ——
只搬推理会留下两个空目录，只删目录会把那段推理丢掉。

推理值得留，因为它是一段**会被重新追问的决定**：「为什么不建一个 HTTP API」
不是懒，是「本阶段的技术范围里没有它要服务的对象」。删掉它，下一个人会把同一件事
再想一遍，而且很可能想出不一样的答案。而一个只为承载这段说明而存在的目录是噪声 ——
所以留下的是**说明**，去掉的是**目录**。

### 三处扫描范围必须一起改，而漏改一处不会有任何东西响

同一份「会被运行的代码在哪」写在了三个文件里：`test_declared_dependencies.py` 的
`SCANNED_ROOTS`、`test_portability.py` 的 `SOURCE_ROOTS`、
`test_adapters_hold_the_vendors.py` 的 `SCANNED_ROOTS`。

三个都留着 `("marketops", "services")` **不会红**：每个扫描器都写着
`if not root.is_dir(): continue`，一个不存在的根是**静默跳过**，而不是报错。
把范围收成一个成员之后仍有各自的非空下限兜着（`test_the_scan_is_not_empty`
要求 ≥ 40 个模块、`test_declared_dependencies` 的 `MIN_SCANNED_MODULES` 同理），
所以「目录改名导致范围失效」这一类会被抓住 —— 但「少删了一个根」不会。
这正是三处必须同一次改的理由。

### 不改的地方，以及为什么

* B4 与 H3c 两节里写的「扫 `marketops/` + `services/`」保持原样：那是**当时的范围**。
* §2.16 里引号内那段变异输出（`工具链依赖出现在 ('marketops', 'services')`）保持原样：
  那是**当时的输出**，是被引用的证据。改写它等于把一次真实的变异结果改成没发生过的样子。
* `docs/CURRENT_STATE.md` 的三处同理 —— 那份文件冻结在 `origin/main @ 9a4fe4e`，
  理由见 B3 / B4 / B5 三节各自的「一处**不改**的」。

本节之后，实际范围只有一个成员。历史叙述里剩下的 `services/` 引用**都是历史**，
这一条不是自动的，靠的是本节把它写下来。

### 一处刻意不做：`tests/{unit,contract,integration,e2e,fixtures}` 的拆分

§1 把它排在 Phase I，并写明理由（对能力零贡献、churn 最大）。今天把代价量出来了：

```text
tests/ 下 66 个 test_*.py（共用 9 个非测试文件：conftest / fakes / mcp_harness / fixtures）
全仓指向 tests/<name>.py 的引用   259 处
  其中 tests/ 之外                155 处（marketops/ 各模块 docstring 84 · docs/ 69 · CI 2）
```

搬文件会让这 155 处**全部指错**，而本仓对「一条指错地方的引用」的判词是
「比没有引用坏」—— 读者会照着它去找一个不在那儿的文件，然后以为自己找错了。
要搬就得连同这 155 处一起搬，那是一次独立的、可单独回退的改动。
**它不是「顺手整理一下目录」。** 在有人真的被 66 个平铺文件绊住之前，不做。

### 这一节的执行器

目录清理没有一条判据能替它作证。能作证的是三件事，各自承担一部分：

1. 三处扫描范围仍指向存在的目录 —— 各自有非空下限，**已自动**；
2. 套件与 lint 全绿、契约重生成零变化 —— **已自动**；
3. `git grep services/` 剩下的每一处都能说出它是历史 —— **不是自动的**，
   本节的「不改的地方」就是把这份判断写下来，供下一个人核对。

---

## §2.21 分支退役：远端从 6 支收到 2 支（2026-09-22）

`ProfessorZhi/marketops` 此前有 6 支远端分支。本轮删掉 4 支，留下 `main` 与
`marketops/v1-serper-discovery`，并多出一个 tag。

### 判据是两个事实，不是一个

「这条分支能不能删」在本仓**不能**只问一次：

| 事实 | 怎么读 | 只读它会怎样 |
|---|---|---|
| 内容在不在 main | 逐文件比 `main:<path>` 与 `<tip>:<path>` 的 blob sha | —— |
| 提交在不在 main 的 ancestry | `git merge-base --is-ancestor <tip> main` | 删掉会**静默丢掉历史** |

**只看第一条就删是错的。** 一个分支可以「内容一条不落地进了 `main`，但提交不是 `main`
的祖先」—— 那说明内容是**按语义摘过去的**（§3 的手法），不是合并过去的。此时删掉分支，
`main` 上留下的是等价的内容，而「这些内容是怎么来的」没了。

四条里恰好有一条是这个形状 —— 而它**不是**最显眼的那一条。

### 四条

| 远端分支 | tip | 独有提交 | 内容在 main | 提交是 main 祖先 | 处置 |
|---|---|---|---|---|---|
| `docs/mvp-required-integrations-20260922` | `5af24251` | 0 | 是（由前两列推出） | **是** | 删 |
| `marketops/repository-convergence` | `d471c336` | 0 | 是（由前两列推出） | **是** | 删 |
| `docs/marketops-v1-research-20260920` | `8dabd2d3` | **1** | 是（**逐字节核过**） | 不是 | 删 |
| `docs/platform-architecture-20260921` | `a31ca225` | **26** | 是（按语义摘） | 不是 | **先打 tag 再删** |

前两行是普通的「已完全合并」：`main..<tip>` 为空且 tip 是 `main` 的祖先，
两行判据给出同一个答案 —— 这两行不必单独核内容。

第三、四行的独有提交不是 0，删掉会让这些提交失去**所有**引用。两行处置不同，
差别在**内容能不能逐字节核**：

- `8dabd2d3` 只加了一个文件：`docs/research/MARKETOPS_V1_ARCHITECTURE_RESEARCH_2026-09-20.md`，
  797 行。它在 `main` 里的 blob sha 与分支上的**逐字节相同**（
  `git rev-parse main:<path>` == `git rev-parse 8dabd2d3:<path>`）。所以删分支丢的是
  **提交对象本身**（作者、日期、提交信息），内容一个字不少。
  这是一条**明知的、核过的**损失，不是疏忽 —— 写在这里，免得下一个人将来发现它没了时
  以为出过事故。「内容在不在 main」与「提交在不在 main 的 ancestry」这两个事实的区分，
  在这里第一次变成一次真金白银的取舍。
- `a31ca225` 的 26 个提交是**按语义摘进 `main`** 的（§3），内容落进了不同的提交里，
  逐字节核不成立。所以它需要一个引用：annotated tag
  `archive/platform-architecture-20260921`（tag 对象 `2896dad`，指向提交 `a31ca225`）。
  打 tag 而不留分支，因为分支名会被读成「这里还有活」；`archive/` 这个前缀说的是
  「这是封存的历史，不是该接着做的地方」。

### 留的那一支

`marketops/v1-serper-discovery` @ `cb9dac1`，相对 `main` 有 **22 个独有提交** ——
Account Discovery 的 Phase 0/1/2（候选账号契约、配对键、开放网络展开器、
台账 `account_id` → `scope` 改名、预算从冻结规格读）。**它不在 `main` 里**，所以留着。
这是「留 1」的全部理由，不是「分支名看着重要」。

### 一处**不改**的：`sources/manifest.yaml:19`

```yaml
snapshot_branch: marketops/repository-convergence
```

`marketops/repository-convergence` 就是上面第二行删掉的那一支。**这一行不动**，两条理由：

1. `manifest.yaml:10-12` 的 refresh 规则只允许**追加**新的 snapshot 记录，不允许原地改写
   已有条目的 provenance。「我们当时从哪一支取的快照」是历史事实；改它等于把一件
   发生过的事改成没发生过。
2. 改它也不解决任何问题：那个名字今天**本来就解析不到了**，而它指向的提交
   （`d471c336`）是 `main` 的祖先（落后 79 个提交、独有 0），内容整份在 `main` 里。
   名字失效**不影响**快照能否被读到 —— `sources/` 是自包含的逐字节冻结快照，
   这正是它存在的理由（`docs/SOURCES.md`）。

但「有一处名字今天解析不到」这件事必须**写在有人能读到的地方**：否则下一个人读
`manifest.yaml` 会去找一个不存在的远端分支，然后以为自己记错了分支名。
本节就是那个地方 ——
**`marketops/repository-convergence` 的 tip 是 `d471c33614de2f1ffc1be361743f06ff6d6dd3c7`。**
（抄在这里而不是补进 `manifest.yaml`，是上面第 1 条的直接后果。）

### 权威读数

`gh api repos/ProfessorZhi/marketops/branches`，或者 `git ls-remote --heads origin`。
**不是** `git branch -r` —— 本地 remote-tracking refs 在 `fetch --prune` 之前仍会列出
已删掉的分支。本轮删完立刻 `git fetch origin --prune`，所以两种读法现在一致：
`main` @ `4de70e1`、`marketops/v1-serper-discovery` @ `cb9dac1`。

tag 有一个读数上的坑：annotated tag 在 `git ls-remote --tags` 里出**两行** ——
`refs/tags/<name>`（tag 对象 `2896dad`）与 `refs/tags/<name>^{}`（它指向的提交 `a31ca225`）。
那不是两个 tag，是一枚 annotated tag 的两个视图。

### 这一节的执行器

与 §2.20 同形：能自动作证的只有一部分。

1. 删完的远端状态 —— **不是自动的**。本轮用 `gh api` 与 `git ls-remote --heads` 各读了一次，
   读数抄在上面，供下一个人复核。
2. 本地无未推送提交 —— `git log --branches --not --remotes` 为空，**可随手复核**。
3. 上表每一行「内容在不在 main」「ancestry 成不成立」—— **不是自动的**。
   把读法写下来，是为了让下一个人能自己再读一遍，而不是相信一段当时跑过、
   现在无从复核的结论。

---

## §2.22 结算之后把结局回传到外部看板（G5c，2026-09-22）

**先说这一节不是什么。** 它**不是**「Quay 集成完成」—— 那条路上还差两样更靠前的
东西（见本节末）。这一片只解决其中**最后**的一环：结算的结果回不到看板上。

### 缺口的样子：一个写动词，零个调用者

`Dispatcher.transition` 端口有定义（`ports/dispatch.py`）、适配器有实现
（`adapters/quay_cli.py`）、判据也测了它（`tests/test_quay_cli_adapter.py`），而
**生产代码里零调用点**。于是 `marketops-mvp-quay` 的看板上 5 件任务全是 `todo` ——
其中一件（`c4f36f96`）在 07:15 已经结算过。

这是那个 dashboard「三类空白」里的第三类。前两类（Loop pulse / Driver 六项）是
**真的不该跑**：MarketOps 不用 quay 的 loop 派发，作业由外部 worker 领。这一类不是
不跑，是**做了而没写回去** —— 看板在说谎。

根因是结构性的：`_submit_findings` 的签名里没有 `dispatcher`，而同一个闭包里的
`control_job` 拿得到（传的是 `dispatcher=quay`）。补一行传参就通了 —— 真正要设计
的是**传进去之后做什么**。

### 收敛，不是迁移

| | 迁移式 | 收敛式（本片） |
|---|---|---|
| 动作 | 直接发 `transition(expected=<本地以为的>, status=<目标>)` | 先 `find` 读**当下**状态，已等于目标就什么都不发 |
| 重发第二次 | 撞 CAS 冲突，报文说「有别人先写了」 | 读到「已经是目标」，零次外呼 |

结算是一次只发生一次的事件，回传是它之后的一次外呼，**两者不可能原子**。所以这条
路一定会被走到第二次（响应丢了、客户端重发、进程重启后再调）。迁移式的回传在第二
次会**凭空造出一条并发** —— 而那是本仓最不该制造的一类假事实。

所以回执里是两个字段而不是一个：`synced`（看板现在对不对）与 `changed`（这一次真
的动了吗）。重发恰好落在 `changed=false, synced=true` —— 那不是失败，是「上一次
已经写好了」。

### 回传失败不升级成错误

本地结算已经落库。把回传失败抛成 `ToolError` 会让调用方以为产出没落盘 → 它重发 →
拿到 `replayed: true` → 而看板**永远**没被同步过。一个「说错了但做完了」的中间态
比一条明确的失败更坏。所以失败进 `board_sync.error`，回执照常返回；可预期的失败只
有两类（执行器起不来 / CAS 拒了），其余照常上抛。

「同一组 label 下不止一条」那句话抽成了 `_duplicate_tasks_message`：`dispatch` 把
它抛成异常，`reconcile` 把它放进 `error`，两个入口共用一份措辞 —— 处置是同一个
（停下报告），漂移的那天读的人会以为这是两种不同的发现。

### 那 5 个任务**不会**因此变绿

回传发生在**结算的那一刻**。补丁之前发生的结算，在它们那一刻没有「回传」这个动作
—— 所以看板上仍是 `todo`。补一条回填入口是**另一件事**，它需要一个显式的动词和
一次人的批准，本轮不做。写在这里，是因为「看板还是没变」最容易被读成「这个补丁没
生效」。

### 判据与执行器

12 条：`tests/test_board_sync.py` 10 条（映射是闭集上的映射、值域只落在外部认识的
状态上、分界线是「还欠不欠人一个动作」、收敛、已收敛不碰、重发幂等、回写失败只报、
映射指向空、从没派发过、CAS 冲突报出**外面真实的**状态），
`tests/test_mcp_assignment_tools.py` 2 条（**工具面**真的回传了、没接执行器时照常
结算并说清是「没接线」）。

工具面那一对是**唯一能证伪「零调用点」的判据** —— 只测 `reconcile()` 本身恰好就是
那个「有实现、没调用者」的形状。它做过一次变异检查：把闭包里的 `dispatcher=quay`
改成 `None`，回传那条红，而「没接线」那条仍绿（证明两条守的是不同分支）。

### 仍然差的两样（**不在本片**）

1. `fetch_document` / `crawl_site` → `EvidenceStore` 的 durable Evidence ingest；
2. 正式的 `QuayResearchAdapter` / `marketops-research-worker`，使 Quay task 能真的
   启动 MarketOps research Claude，而不是进默认的软件工程 worker 流程。

**最终验收的标准不变**：当前 `marketops-mvp-quay` 的 Loop Pulse 出现真实 MOPS
in-flight task。

### 验证

`1760 passed, 3 skipped`（基线 1748 + 12）、`ruff check .` 干净、契约重生成后
`contracts/` 零变化。

---

## 3. 从 22 提交分支抢救什么（**不 rebase，按语义摘**）

### 必摘 —— 三条**真缺陷修复**，与发现期无关

**三条已全部摘完**（Phase D，2026-09-22），落点见「§2.6 Phase D 实际怎么落的」。

| 提交 | 修的是什么 | 为什么必须摘 | 落点 |
|---|---|---|---|
| `a203128` | `_evaluate_clause` 在 `fact not in facts or facts[fact] is None` 时**任何极性都不决定**；合取三值化 | 修掉 `!country in [gb,uk]` 曾把**每一行**无区域事实判成「越界」的真 bug | ✅ `35c593c` |
| `52aba2a` | `check_canonical_id`（不动点判据）+ `store._path_for` 失败关闭 | 原实现 `slugify(id, 60)` 会把 `Foo`/`foo` **静默合并进同一 `.jsonl`** | ✅ 写侧 `2c6c17b`（C6）/ 读侧 `f2a9e7e`（D3） |
| `76f43ee` | SERP 镜像文件名从 `slugify(query)` 改 `sha256[:16]` | **真泄漏修复**：原文件名让下一个代理能读到同组织上一轮的检索词 | ✅ `2ac6ad5` |

### 值得摘 —— 架构形状正确

`search/planner.py`（真规划器：预算、轮转展开、`skipped`/`truncated` 审计）、
`search/discovery.py`（`DiscoveryStrategy` 端口 + `apply_cap`/`TruncationDecision`）、
`search/crawlplan.py`（robots→sitemap→tier）、
`discovery/pairing.py`（`canonical_host`，纯函数、零市场知识、失败关闭）、
`discovery/openweb.py`（开放网络展开器，自带占位符闭集）、
`discovery/qualify.py`（候选→事实→**账号侧那一份**求值循环，不新增规则）、
`discovery/sources.py`（`source_method_id` 闭集）。

### **明确不摘**

| 对象 | 理由 |
|---|---|
| `marketops/experiments/*`、`experiments/`、`services/benchmark/`、`search/choices.py` | 全是 A/B 对照实验层，服务的是上一轮的 C−B 配对 |
| `experiments/v1-serper-discovery.yaml` | **含全仓唯一一处绝对 host 路径，指向真实邮箱清单** |
| `tests/test_{audit,paired_runner,experiment_spec,choices}.py` | 同上；`test_paired_runner.py` 还反向 import `tests.test_pipeline` |
| `marketops/experiments/runner.py` | `_assert_out_of_repo` 靠 `Path(__file__).parents[2]`，**换布局会封死唯一合法的产物目录** |

---

## 3.1 摘取实际怎么落的（发现期 H1…H5，2026-09-22）

**先说字母：这一节的「发现期 H1…H5」不是 §2.11–§2.18 那组「Phase H」。** 两处都用
了 H —— 服务面的 H 是「Phase A…H」里的那一格（派发入口、work item、版本），发现期
这一系列是 §3 这张清单的落片序号。五个提交的主语**都带「发现期」三个字**，按那个
前缀分，不要按字母认。

| 片 | 提交 | 落了什么 | 落在哪 |
|---|---|---|---|
| H1 | `ded01ba` | 候选账号契约（三个 id 空间分开）+ `source_method_id` 闭集 | `domain/candidate.py`、`domain/sources.py` |
| H2 | `638fba7` | `canonical_host` 配对键（纯函数、零市场知识、失败关闭） | `application/pairing.py` |
| H3 | `5dcc9c1` | 开放网络查询展开器（自带占位符闭集、空词表失败关闭） | `application/openweb.py` |
| H4a | `5c6abe1` | 候选事实词表 + 把求值循环从 `grade_account` 里抽出来 | `domain/facts.py`、`application/grading.py` |
| H4b | `25e94cd` | 候选行 → 准入事实的投影，喂给**账号侧那一个**循环 | `application/qualify.py` |
| H5 | `337f54e` | 上限截断，以及**被砍掉的那部分** | `application/caps.py` |
| H6 | 本片 | 「没有生产者」的理由改正（内含两处不实），并把「缺的是 runner」写清楚 | `application/inputs.py` |

**§3 那张「值得摘」表有两行按字面读会读错，逐条改正：**

1. **`search/discovery.py` 只摘了一半，而且是刻意的一半。**「`DiscoveryStrategy` 端口
   + `apply_cap`/`TruncationDecision`」两个词并列，读起来像一个整体；实际落地的只有
   **`apply_cap` / `TruncationDecision`**（`application/caps.py`），**端口没建**。
   理由两条：main 里**零实现**（房子规矩是「等第二个实现出现再提取端口」，`ports.store`
   与 `ports.budget` 都是这么来的），而 v1 那个端口是**账号中心**的（
   `build_plan(account, bundle)` + `SearchBudget`）—— 那正是 discovery 这一轮明确拒绝的
   形状（发现期检索不属于任何账号）。所以端口不是「还没轮到」，是**判定为不该建**。
2. **`search/crawlplan.py` 已经在 Phase E 处置过了**（§2.7）：能搬的只有它想解决的问题，
   v1 的它把 robots.txt 当站点地图索引用、完全不看 `Disallow`。不重复摘。

**H5 有一处刻意偏离 v1。** v1 用 `cap=-1` 表示「不封顶」—— 一个需要注释才读得懂的
哨兵值，与「有上限但确实很小」共用同一个类型。main 的方言是 ``None`` = 不封顶，
**负数是一个调用方的 bug ⇒ `ConfigError`，失败关闭**。判据是「把一件事做成没有的入口
（`None` 提供方）优于有入口 + 一条注意事项」：静默接受负数，会让一次配置算错变成
「没设上限」，而两者在报告上长得一样，更糟的是上限从此消失而没有任何东西会报警。

**§3 的清单还剩一行没落：`search/planner.py`。** 它要与 main 已有的
`application/queries.py` + `billed_search.py` + `budget.py` 对表之后再决定摘什么 ——
「真规划器：预算、轮转展开、`skipped`/`truncated` 审计」这几件事**有一部分已经在了**
（预算在 `budget.py`、`skipped` 在 H5 之后的 `caps`/指标里），整段搬会造出第二份。
与 Phase 4 harness（§6）同批处置。

**一条**本轮**不闭合的缺口**（写给 Phase 4，不是留给读者猜）：**`discovery_scope`
今天仍然没有生产者**，而且**本轮到此为止** —— 建一个需要 Phase 4 的 harness
（`openweb.py`、`pairing.py`、`qualify.py`、`caps.py` 都是零件，缺的是把它们串成
一圈、真的发查询抓页面并写回候选行的那条路径），而 §6 明写那一层**本轮不执行**。

H6 只做了这件事的**可做部分**：`application/inputs.py` 的
`_NO_PRODUCER[JobInputKind.DISCOVERY_SCOPE]` 理由里原本写着「发现管线不在本仓库里，
见 §2.10 的缺口 #2」，这句话里**有两处不实**：

1. 「不在本仓库里」在 H3 + H4b 之后**部分失真** —— 展开器与投影都在了；
2. §2.10 的缺口 #2 讲的是**服务面**的 `subjects` 生产者，与发现期无关。一条指错地方
   的引用比没有引用坏：读者会去读那一节，然后以为自己读懂了。

改后的理由**点名本仓已有的载体**（四个模块路径），并把「缺的是 runner、属于 Phase 4」
写清楚。这条约定配了执行器：`tests/test_job_inputs.py` 会把每条理由里的
`marketops/....py` 路径**逐个到盘上核对** —— 于是「只说东西不在、一个载体都不给」
那种写法写不出来。它证明的是「指向哪里」，**不**证明理由在语义上是对的（后者只能靠
人读，这条限制写在判据的 docstring 里）。

同一次清扫还改掉**两处会把读者引向错误形状**的措辞：`docs/PRODUCT_SPEC.md` 的非阻塞
清单原本把目标写成「`application/queries.py::build_queries` 接入 `query_templates` /
`venue_types` / `regions`（缺展开器）」—— 展开器已经有了，而**接线的地方刻意不是
`build_queries`**：两族模板不共用校验器，否则 `{domain}` 会变成开放网络模板里的合法
占位符，也就是允许把账号事实注入发现期查询。照原文去做的读者会造出本仓明确拒绝的东西。
`docs/SOP_MAPPING.md` §1 第 2 行补上「那一圈 runner 属于 Phase 4」，把「还没接线」
从一个状态变成一件有主的事。

---

## 4. 已定的工程决策（§23：自己决定，不逐条问）

| # | 决策 | 理由 |
|---|---|---|
| D1 | **pin `mcp>=2.2,<3`**，按 v2 API 写（`from mcp.server.mcpserver import MCPServer`） | 最新 SDK 把 FastMCP **改名为 MCPServer**；`mcp.server.fastmcp` 已不存在。**照旧 FastMCP 例子写 + 装最新 SDK = 立刻坏** |
| D2 | **SQLite 是主存**，JSONL 降级为导出/审计 | `docs/MCP_SERVICE.md:89` 推荐；且现有无锁 JSONL 与自家 `:81`「原子预留」直接冲突 |
| D3 | **Jev 实现、但 live = `NOT_RUN`** | 本机**没有** `TYPESAFE_API_KEY`。§20 明令：缺 key 不得写 PASS |
| D4 | **Laya 真跑** | `pip install laya` + 公开 HF 权重，CPU 可跑（本机 246 GiB / 128 核 / 无 GPU → `preload=True` 是必须的，否则交替语言每次重载 ~7.4s） |
| D5 | **只做一个 DecisionSpec**：`role_function_relevance` | §12 明令「不要一开始就建 30 个」 |
| D6 | **不 fork Quay** | Kent 轮实测证明扩展面是纯增量的，零源码改动可跑通 |
| D7 | **不改 MUC/Hosting 源码** | §0 硬约束 |
| D8 | **transport 先 Streamable HTTP，绑 loopback** | §6 + 本机安全约束（Quay UI 那次的同一条） |
| D9 | 不引入 Redis/Kafka/Celery/K8s/向量库 | §17 |

---

## 5. 验收对应（§20 / `docs/MCP_SERVICE.md` §8 的 A–E）

| 包 | 内容 | 本机可达性 |
|---|---|---|
| **A** | 无网络协议与安全测试：schema/隔离/危险 URL 拒绝/预算竞争/幂等/陈旧 attempt/暂停与恢复 | ✅ 全可达 |
| **B** | 真实端到端调研：≤5 候选、冻结范围、Claude Code 经 MCP 提交、**真 Quay worker**、断开重连、**≥2 worker 重叠且无重复昂贵工作** | ✅ 需 Serper 额度 |
| **C** | 六件套真实使用证据 | Serper ✅ / Crawl4AI ✅ / Jev ❌ **NOT_RUN** / Laya ✅ / Quay ✅ / Claude Code ✅ |
| **D** | 质量与连续性：历史页/换人页/他人邮箱/重名/PDF/显式拦截/一个 provider 不可用/worker 崩溃 | ✅ |
| **E** | 逐项 `PASS`/`FAIL`/`NOT_RUN` + 证据引用；**缺件则整体 gate 不完整** | —— |

**C 那一行的 ✅ 是可达性（本机装得上、跑得动），不是「已经跑过」。** 决策两个后端
今天实测都是 `runtime_ready: false`（见 §2.8 末）：jev 缺 `TYPESAFE_API_KEY`、laya
缺本地 checkpoint，所以 `decide` 的 live 判定仍是 `NOT_RUN` —— §20：缺件不得写 PASS。

**绝不做**：把 mock 结果说成 live、把「包能 import」说成「集成完成」、
把「Quay 任务 done」说成「业务已验收」、把 worker 重叠说成性能提升。

---

## 6. 本阶段不做

联系人深挖之外的：30 个 DecisionSpec、Scene 自学习 Agent、写邮件、监听回复、
生产飞书写入、OCR、国家级铺开、`future_auto` 模式、向量库、新调度器。
