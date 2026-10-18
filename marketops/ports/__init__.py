"""端口：领域与外界之间的**契约**。

本层只说「我需要什么」，不说「谁能给」。实现全部在 ``marketops/adapters/``：

===========================  =======================================
:mod:`marketops.ports.search`  ``SearchProvider`` —— URL discovery
:mod:`marketops.ports.acquisition`  ``Fetcher`` / ``HttpFetcher`` / ``BrowserFetcher``
:mod:`marketops.ports.documents`  ``DocumentParser``
:mod:`marketops.ports.store`  ``EvidenceStore`` —— 产出怎么落盘
:mod:`marketops.ports.budget`  ``BudgetLedger`` —— 额度怎么扣在起飞之前
:mod:`marketops.ports.decisions`  ``DecisionProvider`` —— 有界语义判断
===========================  =======================================

**为什么要有这一层。** §2 的硬约束是「业务代码不得直接依赖 Serper / Jev /
Laya / Crawl4AI / Quay 的 API」。这条约束不能靠自觉 —— 它必须有一个**位置**
承载，所以：

* 业务层（``application`` / ``domain``）只能 import ``ports``；
* 厂商名只允许出现在 ``adapters/`` 的文件名与实现里；
* 于是「换掉 Serper」是一件在 ``adapters/`` 里完成的事，业务层不动。

**端口里可以有 ABC，不可以有厂商。** ``HttpFetcher`` / ``BrowserFetcher`` 是
``Fetcher`` 的抽象子类（只固定 ``tier``），它们不含任何具体传输实现 ——
具体实现是 ``adapters/http.py`` 与 ``adapters/crawl4ai_fetcher.py``。

**这一层不是「先设计后实现」的产物。** 前五个模块都从**已经在跑的实现**里提取出来
（实现原先是它们的同级文件）。端口的方法签名因此是实测过的，不是推测的；
后续新增端口遵循同一条纪律：**等第二个实现出现再提取**，否则写出来的是一份
「看起来设计完了」的猜测，而它会在第一个真实实现落地时被重写。

``ports.store`` 是这条纪律最强的一个例子：它被 B3 刻意推迟过，因为当时只有 JSONL
一个实现 —— 而它现在有三个（``adapters/sqlite/`` 主存、``adapters/jsonl/`` 导出通道、
``adapters/memory.py`` 测试替身），**同一组判据**跑在三个上面
（``tests/test_evidence_store_contract.py``）。B3 写下的推迟理由与它现在成立的理由
是同一句话。

``ports.budget`` 是同一个规矩在 B6b 的复现：``SqliteBudgetLedger`` 在 B5a 落地时
**没有**端口，因为那时只有它一个实现；B6b 出现了第二个（``InMemoryBudgetLedger``，
测试替身），端口随即提取，两个实现跑同一份
``tests/test_budget_contract.py``。端口上的形状也因此被一件事钉住了：**没有**
``timeout`` / ``expire`` / ``release`` 这类入口 —— §8 要求超时不得默认释放预算，
而「不存在的入口」比「存在的入口加一条注意事项」可靠。

B5a 给 ``sqlite/ledger.py`` 留过一条删除条件：「B6 落地时若 ``application/budget.py``
没有出现，账本与它的测试一起删掉」。**B6b 让那个条件不成立** —— 消费者出现了，而且是
接在链路里的那一个：``application/pipeline.py::_discover`` 每一次检索都走
``reserve → 调用 → spend``，判据在 ``tests/test_pipeline_budget.py``（含一条
「拿掉预留就拦不住」的正对照）。一个语义正确但没人调用的账本，与没有账本，在报告上
一模一样 —— 那条删除条件要防的就是这件事。

``ports.decisions`` 是这条纪律第一次**在写端口的时候第二个实现已经在手边**的实例，
而规矩没有变：端口与它的两个实现（``adapters/laya.py`` 本地权重、
``adapters/jev.py`` 托管 API）**同一个提交**落地。与前五处不同的只有一点 ——
它没有「原先是同级的实现文件」那一段历史，两个后端是照着端口写的。所以它比前五处
多承担一件事：端口上的每个形状都得当场被两条真实的路径同时钉住，否则它就是一份
「看起来设计完了」的猜测。三种 ``DecisionOutcome``（``ANSWERED`` / ``ABSTAINED`` /
``UNAVAILABLE``）里后两者**不能压成一个布尔**，就是被这件事逼出来的：缺凭据与
读不懂这一段输入是两种不同的事实，而它们的处置（换后端 / 换输入）也不同。见
``ports/decisions.py`` 的模块 docstring。

**站点抓取的前沿不在这一层**，而它长得像该在这里：``SitemapFrontier`` 是「去取哪些
URL」的策略，看起来正是个端口。它住在 ``application/sitemap_frontier.py`` —— 判据
是上面那条「能不能在不碰外界的前提下测试」，它读网全部经过注入的 ``HttpFetcher``，
所以按那条判据它是策略。**等它出现第二个实现再提取端口**，与 ``ports.store`` /
``ports.budget`` 的时机一模一样。
"""
