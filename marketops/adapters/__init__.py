"""**实现层**：领域层与外界之间的每一处真实接触。

这一层是唯一允许碰网络、文件系统、浏览器、数据库、厂商 SDK 的地方。领域层
（``marketops/domain/``）定义**要什么**，端口层（``marketops/ports/``）定义
**叫什么**，本层提供**怎么做**。

分层的判据不是「代码写在哪」，而是**能不能在不碰外界的前提下测试**。因此判断
一个模块属不属于这里，只需问一句：把它 import 进来会不会需要磁盘、网络或
一个进程？会，就是 adapter。

## 现在住在这里的东西

| 模块 | 端口 | 它碰的是什么 |
|---|---|---|
| ``serper`` | ``ports.search`` | Serper HTTP API |
| ``http`` | ``ports.acquisition`` | 直连 HTTP（``httpx``） |
| ``crawl4ai_fetcher`` | ``ports.acquisition`` | 浏览器渲染（``crawl4ai``） |
| ``text_pdf`` | ``ports.documents`` | ``pypdf`` / 正则（无 ``bs4``） |
| ``mineru_adapter`` | ``ports.documents`` | MinerU 模型推理 |
| ``laya`` | ``ports.decisions`` | 本地 checkpoint + ``torch`` 进程 |
| ``jev`` | ``ports.decisions`` | TypeSafe AI 的托管 API（``typesafe_sdk``） |
| ``feishu/`` | —— | 飞书 bitable（``ADAPTER_STATE="partially_wired"``：交接的写入路径已接，路线/机会缺业务键；真实写入 ``BLOCKED``） |
| ``sqlite/`` | ``ports.store`` / ``ports.budget`` | SQLite 文件（**主存**：账本 + 证据/路线/机会） |
| ``jsonl/`` | ``ports.store`` | 磁盘上的 ``.jsonl``（导出与审计通道） |
| ``memory`` | ``ports.store`` / ``ports.budget`` | 无：**测试替身**，不是部署选项 |
| ``classify`` | —— | 无：纯函数，但读 ``httpx`` 才有意义的响应形状 |
| ``hashing`` | —— | 磁盘字节 |
| ``profile_loader`` | —— | ``profiles/<id>/`` 下的 YAML 文件 |
| ``mcp_server/`` | —— | ``mcp`` SDK（服务面 SDK，**不是**我们的某个端口） |

**本包是唯一允许 import 厂商模块的地方。** 这条由
``tests/test_adapters_hold_the_vendors.py`` 执行，不是靠自觉 —— §2 的
「业务代码不得直接依赖 Serper / Jev / Laya / Crawl4AI / 执行器的 API」如果只是
一句写在文档里的话，第一个赶时间的人就会在 pipeline 里直接 ``import httpx``。

``document_router`` 曾经在这一行里，B7a 搬走了（``application/document_router.py``
拿走策略，``interfaces/composition.py`` 拿走接线）。它是开头那一条判据的**正例** ——
一个文件里同时住着"只认端口"和"真的 import pypdf"两种东西时，按文件名定位一定会
把其中一半放错，而错的那一半总是**策略**：因为接线看起来"更需要"留在 adapter 层。

Phase E 的 ``sitemap_frontier`` 是同一判据的**第二个**正例，方向反着来：它整个文件
只认端口（读网全部经过注入的 ``HttpFetcher``），本来就不该按「站点地图」这个名字
留在这里，搬去了 ``application/``。所以上面那条判据要按字面读 —— 它问的是
import 之后要不要磁盘、网络或进程，不是「这个名字听起来像不像碰外界的东西」。

``classify`` 放在这里而不是 ``domain/``，理由值得写下来：它是纯函数，看起来
够格进领域层。但它的**唯一消费者**是 ``http`` 与 ``crawl4ai_fetcher``，而它的
判据读的是 HTTP 响应才有的形状（状态码、``content-type``、正文长度）。放进
``domain/`` 会让领域层开始为「一个 HTTP 响应长什么样」积累指纹，同时让两张
测试都通过 —— 模型（domain = 业务事实）就此失效，而没有任何执行器会响。

**`ports.store` 的三个实现不是三种部署选择。** ``sqlite/`` 是主存，``jsonl/`` 是导出
与审计通道（它不可替代的性质是不需要任何工具就能读），``memory`` 是测试替身。
三者的取舍与判据写在 ``adapters/jsonl/store.py`` 与 ``adapters/sqlite/evidence_store.py``
的模块 docstring 里，**同一组判据**在 ``tests/test_evidence_store_contract.py``。

**``ports.budget`` 的两个实现取舍不同，值得写下来。** ``sqlite/ledger.py`` 是唯一
替并发正确性背书的一个（``BEGIN IMMEDIATE``）；``memory.py`` 的
``InMemoryBudgetLedger`` **明确没有锁**，它只服务「一次测试里的一段流程」。所以
预算的判据分成两处：语义（预留占额度、不自动退款、幂等键、科目与 scope 隔离）两个
实现都跑（``tests/test_budget_contract.py``），并发只跑 SQLite 那份
（``tests/test_budget_ledger.py``）。把并发那条也放进共享契约，就得给内存实现加锁
—— 那不是「补齐契约」，是把一个测试替身伪装成并发账本。

``profile_loader``（B6a 搬进来）是上面那条判据与「端口」这条判据**不重合**的例子：
它不实现任何端口、不认任何厂商，但装载 ``profiles/<id>/`` 要磁盘 —— 按开头那一句它
就是 adapter。B4 记录把这条挂给 B6 结（见 ``docs/MVP_MIGRATION_PLAN.md`` 的 B4 节），
结论是**搬家**而不是改判据：§2 的目标树里顶层只有 ``config.py``（唯一读
``os.environ`` 的模块），而 ``adapters/jsonl/`` 早就立了「读数据文件 → 产出领域对象」
的先例。它装载出来的 ``ProfileBundle`` 仍住在 ``domain/profile.py``，所以
``application/`` 依赖的是**领域类型**，不是本层 —— 见 ``application/pipeline.py``。

``mcp_server``（B7b 之后搬进来）是 ``profile_loader`` 的**同一个判据、另一种情形**：
它同样不实现任何 ``ports/`` 端口 —— 它是**入口**，不是出口。搬它的理由不是分层美学，
而是一条已经写在盘的规则被撞上了：``tests/test_adapters_hold_the_vendors.py`` 的
``VENDOR_MODULES`` 在 **B4** 就把 ``mcp`` 列进了厂商清单（那时一行 MCP 代码都还没有，
它是一条**预先声明**）。于是 ``interfaces/mcp/`` 里 import 它必然违反那条判据 —— 而
``interfaces/`` 被授予的权力是「点名我们自己的 adapter」，不是「import 厂商 SDK」。

没有为它造端口，理由写在 ``marketops/adapters/mcp_server/__init__.py`` 里（一句话：
一个只服务单一实现、没有第二条路线的端口，是为了让扫描器满意而存在的接缝）。
``marketops/interfaces/mcp/`` 因此只剩入口与组合：它点名 ``adapters.mcp_server``，
这与 ``interfaces/cli.py`` 点名 ``adapters.serper`` 是同一个动作。

决策族 adapter（``jev`` / ``laya``）是 Phase F 的（见 ``docs/MVP_MIGRATION_PLAN.md``）。
它们与 ``document_router`` / ``sitemap_frontier`` 是同一个判据的第三种情形，而方向
是**留在这里**：两者都真的碰外界（一个碰磁盘上的权重与一个 ``torch`` 进程，一个碰
一份托管的 HTTP API 与一份凭据），所以按开头那一句它们就是 adapter。策略部分
（题面由谁持有、影子怎么写回执）不在它们里面 —— 那在 ``application/``。

两者的 ``configured`` 与 ``ready`` 分开，理由与 ``mineru_adapter`` 完全一样：
「装了没」与「能不能真的产出一条记录」是两个问题，而按前者接线是把调用点交给
一个还没校准的依赖。它们比 MinerU 那一处多一层：laya 的 ``ready`` 还要求权重
**完整地**在本地（缺任何一样它都会去 HuggingFace 取），jev 的 ``ready`` 还要求
手里有一把非空凭据（否则 SDK 会自己去读环境变量）。两条都是「让隐式的路不可达」，
不是「加一条注意事项」。
"""
