# 采集验收矩阵 · 逐行对账（2026-09-28）

这份文件把 `docs/ACQUISITION_AND_PROVIDER_DESIGN.md` §11 那 13 行**逐行**落成可核实的
证据指针：每一行给出机制、生产者、消费者、正证据、负证据、标注（fixture / live）、
状态，以及**缺口归谁**。

它**不改**设计文档，也不取代它。设计文档 header 把权威钉在旧基线（产品基线
`df689f52`、设计前身 `61419b7`）上，它记的是「当时要求什么」。这一份记的是
「今天这一棵树上，哪一条真的被守住了」—— 两份各自完整，改前者等于把一份在记录
「当时观察到什么」的文件改写成「当时的代码是对的」。

## 状态词表

| 状态 | 含义 |
|---|---|
| `DEMONSTRATED` | 有生产者、有消费者，正负证据都在本仓测试里，且**读得回** |
| `PARTIAL` | 某几格有判据，另几格没有；缺口逐条写在该行的「缺口」栏里 |
| `NOT_BUILT` | 全仓没有实现（不是「没测」——是「没有那个东西」） |
| `OUT_OF_SCOPE_HERE` | 属于别的切片（B16/B17/B01…），本矩阵只登记归属 |

**这份矩阵不宣称任何 live 结果。** 下面每一条证据都是 fixture 或纯函数判据，
零网络调用。§18 的 provider 版本与现场验证不在这份文件里，也在本片里没有做。

---

## 逐行对账

### 205 同源正常公开HTML → HTTP内容完整，无不必要browser；内容→绑定→route有真实消费者

**机制**：`adapters/http.py` 直连 HTTP，`_response_exchange` 收成 `_Exchange`，
`_completed` 落镜像并算 `content_sha256`；`FetchOutcome.OK` 是唯一会镜像的两个出口之一
（另一个是 `EMPTY_SHELL`）。

**正证据**：`tests/test_http_fetch_attempts.py::test_every_exit_leaves_exactly_one_attempt`
把 `CASES` 表里每一个出口过一遍，断言一次 `fetch()` 之后账上**恰好一行**、线上恰好那几跳。
下游消费者：`tests/test_application_contact_routes.py`、`tests/test_mcp_contact_routes.py`
（内容 → 绑定 → route）。

**负证据**：`tests/test_http_fetch_attempts.py` 的出口表本身——
`test_the_case_table_covers_every_exit` 判不了「新出口在表里有对应行」，只能判「表的规模
没有缩水」；真正让新出口当场现形的是 `test_no_exit_forgets_to_leave_a_diagnosis`。
两条都在这份测试文件里，且这条局限写在模块 docstring 里，不假装它做到了。

**标注**：fixture（`tests/fixtures/http_transport.py` 的 `MockTransport` + `FakeResolver`）。
**状态**：`DEMONSTRATED`。**缺口**：无。

---

### 206 正常JS-required fixture及批准公开样本 → Crawl4AI真实渲染；分别标synthetic与live；完整正文/链接不为占位

**机制**：`adapters/crawl4ai_fetcher.py`。`Crawl4AI` 未装时 `available` 为 False，
`fetch` 抛 `ParserUnavailableError` —— 「没装浏览器」因此不阻塞整个套件。

**材料标注**（这一格 `2026-09-28` 新收口的一半）：`domain/fetch_attempt.py` 的
`MaterialProvenance`（闭集，`live` / `synthetic`），落在 `FetchAttempt.material` 与
端口上的 `Fetcher.material`。承重的地方不是缺省取哪个成员，而是**声明落在哪一个
对象上**：它在**抓取器实例**上，不在 `FetchRequest` 上。生产里构造请求的那一处会
写死 `LIVE`，而**替换发生在它下面** —— 一条注入 `MockTransport` / 假渲染库的判据
走的是同一段业务代码、同一个构造点，于是请求级的标注会把一份自造页面记成现场覆盖，
而报告上看不出那一天。缺省是 `SYNTHETIC`（收回声明的一边，低报）；生产的两处构造点
（`interfaces/composition.py` 的 `default_http` 与 `default_crawl` 的浏览器那一跳）
各写一句 `LIVE`。

**消费者**：`list_fetch_attempts` 的 `counts.by_material` —— 枚举每个成员、零写零
（`synthetic: 0` 是一句「这一页每一行都出过网」的可对账声明，不是一个没算过的数）。
转抄链路上 `attempt_from` 的 `material` **没有缺省**：让它带个缺省是免费的，代价是
一个忘了转抄 `self.material` 的抓取器会把一次真出网的抓取安静地记成自造材料，而两条
路径在盘上同形。

**正证据**：`tests/test_adapters_hold_the_vendors.py`、`tests/test_acquisition_target_policy.py`
（渲染器也走地址判据）；材料标注一组：`tests/test_material_provenance.py`（8 条，
组合根两处各一条、裸构造两条、替换了网络的一次落 `SYNTHETIC` 两条、正向 `LIVE` 一条、
`attempt_from` 无缺省一条）、`tests/test_browser_egress_live.py` 第 (4) 组
（**一次真实的 Chromium 渲染**，靶子是本文件自己起的 `127.0.0.1` 监听 → 账上是
`synthetic`）、`tests/test_mcp_fetch_attempts.py::test_both_kinds_of_material_are_counted_on_the_page`、
`tests/test_fetch_attempt_store_contract.py` 的逐格往返；契约已重生成
（`contracts/fetch-attempt.schema.json` 的 `material` 一格）。

**渲染结果那一组**（同一文件第二个用例
`test_a_scripted_page_arrives_rendered_and_not_as_its_placeholder`）：那一页的正文与
链接**都由内联脚本写进 DOM**，脚本执行前页面上只有一个占位串。判据是「注入的正文与
`href` 逐字在镜像里」**加**「占位串不在镜像里」。**承重的是后一句，而这是量出来的**：
把 `--blink-settings=scriptEnabled=false` 加进 `_browser_config` 时，红的是占位串
那一句，两条「在场」照绿 —— 脚本没跑时交回来的是原始响应，而那两个**字面值就写在
`<script>` 的源码里**，于是「在场」分不出「执行过的 DOM」与「没执行的源码」。
（先试的 `--disable-javascript` **不是** Chromium 认得的开关，Chromium 对不认识的
开关一声不响 —— 那一次判据照样绿，那是探针写错了，不是判据弱。）
真渲染过的那一份长什么样也量过：镜像里 `<script` 与占位串**一个都没有**，
`<h1>` / `<a href>` 逐字在（探针在
`artifacts/work-items/marketops/TARGET-CONVERGENCE-B15H/`）。

**负证据**：`tests/test_no_evasion.py` 守住「不绕行」那一条；`ParserUnavailableError`
的**未装**路径有判据。这一格另做过一轮**变异对照**（13 处植入缺陷，逐个被声称覆盖它
的那条判据抓住，还原逐次 sha256 校验）：其中一处是「端口的缺省反转成 `LIVE`」，它
一开始**全绿** —— 因为 Python 的形参缺省取不到类属性，两个实现各自写了一遍缺省，
只改端口那一处谁都不受影响。补的判据钉的是「实现自己那一份与端口上那一份是同一个
值」，否则两处声明分叉时两边看起来都像权威。

**标注**：fixture。两个用例都是**真实 Chromium 渲染**，但靶子是我们自己立的，所以
它们证明的是「真的渲染也不会被记成现场覆盖」与「交回来的确实是渲染之后的 DOM」，
而不是现场覆盖。
**状态**：`PARTIAL`。
**缺口（一条，不是代码缺口）**：**「批准公开样本」那一半没有证据。**
这一行要两个输入 —— 一个 §10 意义上的正常 JS-required fixture（已做）与一个**批准
过的公开样本**（未做）。后者要一次对真实第三方站点的抓取，而这份矩阵开篇就写着
「**不宣称任何 live 结果**」，所以它在**这一份文件里不可能被关掉**。归 §18 的现场
验证；不把它读成「代码里还差什么」。

**已关闭两条**：
1. `synthetic` / `live` 分别标注（原先 `NOT_BUILT`：全仓没有任何字段承载它）。现在
   承载它的是上面那两处。**残留一并记下**：`live` 那一方向的证据是「生产组合根声明了
   `LIVE`」加一条走假渲染库的正向转抄，**不是**一次对真实第三方站点的抓取。
2. 「完整正文/链接不为占位」（原先：没有任何测试断言渲染结果里的链接是真的链接）。
   现在由上面那一组钉住，且哪一半承重是量出来的，不是推出来的。

---

### 207 200+cf-ray普通页面 → 不判IP封锁或challenge

**机制**：`domain/access.py::diagnose` 的 12 步优先级。`cf-ray` 的存在**不是**任何一步的
输入（`safe_response_headers` 的白名单收下它，供事后分诊读，但不产出结论）。

**正证据**：`tests/test_access_diagnosis.py::test_a_plain_page_with_ray_id_is_not_a_challenge`
与同文件开头那张 `Observation` 表（403 + `cf-ray` 与 200 + `cf-ray` 各一条）。

**负证据**：`tests/test_access_diagnosis.py::test_403_with_ray_id_is_not_an_ip_ban`。
这条判据杀掉的正是「看起来最像洞察」的那种改动：把 `cf-ray` 的存在读成封禁证据。

**标注**：纯函数（零 I/O）。
**状态**：`DEMONSTRATED`。**缺口**：无。

---

### 208 200/403挑战页 → 隔离为challenge，0业务邮箱产出，替代策略/熔断有回执

**机制**：`_response_exchange` 里那次**改判** —— 一个必须扣下正文的响应先被改判成
`FetchOutcome.BLOCKED` / `BlockReason.WAF_CHALLENGE`，然后才走到 `_completed`；因此
`body_may_kept` 为假时一个字节都不落盘。挑战识别以**诊断**为准（`cf-mitigated` 头或正文
指纹），不是以分类器为准。

**两层同形**：浏览器那一层（`crawl4ai_fetcher.py::_render`）守同一句话、用同一张判据表，
只差一格 —— 直连那一层的空壳还没折叠（它含 `EMPTY_SHELL`），而这一层的空壳已经折叠成
`RENDER_FAILURE`，一个挑战页正好常常就是一个一百来字节的空壳，所以这一层含
`RENDER_FAILURE`。少了那一格，同一次抓取会一边写着 `render_failure`、`block_reason`
为空，一边写着 `waf_challenge` 的诊断。（两层的差异逐条写在各自的模块 docstring 里。）

**正证据**：

* 直连：`tests/test_http_access_governance.py::test_twenty_accounts_on_one_challenge_page_send_three_requests`
  （20 个账号拿到同一份挑战页：全部 `BLOCKED` / `WAF_CHALLENGE`、`body_path is None`、
  `mirrored(settings) == []`、每一份回执都带诊断）。
* 浏览器：`tests/test_browser_fetch_attempts.py::test_a_challenge_page_is_never_mirrored`
  —— **两半各判一次 + 一个对照组**。正文指纹那一半由分类器判；`cf-mitigated: challenge`
  那一半分类器**看不见**（它只看状态码与正文，一个 200 的干净页面在它眼里是 `OK`），
  所以那半必须由诊断改判，而这一条在断言前先把分类器的结论（`OK`）判一次，免得它测的
  其实是分类器早就判过的那个局面。对照组是同一批字节去掉那个头 —— 少了他，一个
  「什么都不落盘」的实现会照样通过。出口表（`::test_every_exit_leaves_exactly_one_attempt`
  的 `挑战页-正文指纹` / `挑战页-响应头` 两格）再钉一次结论与账上那一行的
  `cause` / `action` / `block_reason`。

**负证据**：`tests/test_access_diagnosis.py::test_a_challenge_page_cannot_become_evidence`
与 `::test_only_a_challenge_withholds_the_body`（反向也钉着：**只有**挑战才扣正文，
别的失败不该顺手扣）。浏览器那一侧的反向是同一张表里的 `403` / `429` 两格 —— 它们的正文
此后仍然落盘（与直连那一层在这件事上的不一致是既有的，不在本片范围内，理由写在
`_render` 的注释里）。

**标注**：fixture（`CHALLENGE_BODY`）。
**状态**：`DEMONSTRATED`。**缺口**：「0 业务邮箱产出」这一半由「没有正文」蕴含
（没有正文 = 抽取没有输入），**不是**由一条直接断言邮箱数的判据守住的。这一点写在
213 行的判据注释里。

---

### 209 429/503/Retry-After → origin共享有界退避，attempt/预算不被SDK隐藏重试放大

**机制**：`domain/access_governance.py::AccessGovernor`（进程级共享一本来源账）+
`parse_retry_after`（上限 `RETRY_AFTER_MAX_SECONDS = 3600`）+ 组合根把这**同一个** governor
注入每一个抓取器。

**正证据**（三条，互相独立）：
1. `tests/test_http_access_governance.py::test_a_retry_after_on_one_account_holds_the_whole_origin`
   —— 一个账号收到 `Retry-After` 会按住整个来源。
2. `...::test_twenty_accounts_on_one_challenge_page_send_three_requests` —— 20 个账号
   撞同一个来源，线上真的发出的次数**从 policy 读出来**（不是文件里另写的一个 3）。
3. `...::test_the_sdk_hidden_retries_are_pinned_to_zero` —— `HttpxFetcher` 的 client
   显式 `transport=httpx.HTTPTransport(retries=0)`。

**浏览器那一层的同一句话**（§5 的后半句：「SDK 隐藏重试必须可观察，且不得双重放大」）：
`crawl4ai_fetcher.py::_run_config` 显式 `max_retries=0`。这一格**不是摆设**，实测
（0.9.3）`async_webcrawler.py:405` 是 `_max_attempts = 1 + getattr(config, "max_retries", 0)`，
而那个循环的触发条件是**防反爬判定**（日志 tag `ANTIBOT`，消息
`Anti-bot retry {attempt}/{max_retries}`）。它今天为 0 **只因那是库的缺省值**，而
一次库升级把它改成 3 之后，我们真的发出的请求数会翻几倍 —— **账上却一个字不变**
（一次 `fetch()` 仍只写一行），于是同源共享限流会按一个偏小的数算。
判据两条，分开是因为替身与真库各能判一半：
`tests/test_browser_fetch_attempts.py::test_the_run_config_asks_for_no_hidden_retries`
（我们写了那个决定 —— 判的是 `arun` **真的收到**的那份 config，不是
`_run_config()` 的返回值，否则一个在 `_render` 里另构造一份的实现会全绿）与
`::test_the_library_actually_reads_that_field`（**正控**：先证明真库里
`CrawlerRunConfig(max_retries=3).max_retries == 3` 读得回来，再证明抓取器的是 0）。

**负证据**（同样是三条）：
1. `...::test_with_one_book_per_account_the_same_page_costs_twenty_requests` ——
   **对照组**：把「共享」那一步拿掉，同一条线上会发出 20 次。没有它，那个 3 可能只是
   「20 个账号各发了一次、其中 17 次因为别的原因没发出去」的巧合。
2. `tests/test_access_diagnosis.py::test_retry_after_refuses_to_guess`（非法的
   `Retry-After` 返回 `None` 而不是截断成一个数）。
3. `test_the_sdk_hidden_retries_are_pinned_to_zero` 里有一条**正控**：先证明
   `httpx.Client(transport=httpx.HTTPTransport(retries=3))._transport._pool._retries == 3`，
   再证明抓取器的是 0 —— 否则「读到 0」可能只是这个属性根本读不出来。

浏览器那一侧的「可观察」一半另有一条互相独立的判据：
`tests/test_browser_fetch_attempts.py::test_every_exit_leaves_exactly_one_attempt`
—— 一次 `fetch()` 之后账上**恰好一行**。它**判不到**隐藏重试（库在内部重试，我们在
外面仍只看到一行），所以它**不能**当作 `max_retries=0` 的证据；它判的是另一半：
这一层自己没有在循环里记第二次账。

**标注**：fixture（零网络调用）。浏览器那一侧的正控要构造**真的** `CrawlerRunConfig`，
所以它带 `crawl4ai_available()` 的 skipif —— 与 `test_browser_egress_live.py` 同一口径，
那是可选运行依赖。注意它**不是** live 判据：它构造一个配置对象，不发任何请求。
**状态**：`DEMONSTRATED`。**缺口**：无。

> 这一行此前在别处的盘点里被判成 `PARTIAL`。复核之后不是：上面的第 2、3 条与两条
> 负证据都在 `tests/` 里躺着，且第 2 条**带对照组**。判成 `PARTIAL` 的原因看起来是
> 只搜了 `single-flight`/`ReuseDecision` 一类词，而这一行的实现词汇是
> `AccessGovernor` / `circuit_setback_threshold`。

---

### 210 403根因不明 → 不称已证IP封禁、不强行升级browser；允许继续其他合法来源

**机制**：403 的默认结论是 `SuspectedCause.ACCESS_DENIED_UNKNOWN`，而
`SuspectedCause.IP_POLICY` **只能**由 `with_cause_evidence` 产生 —— 它需要一个引用
（`cause_evidence_ref`），构造不出来就取不到。

**正证据**：`tests/test_access_diagnosis.py::test_machine_diagnosis_never_invents_an_ip_policy_ban`
（参数化的 `Observation` 表逐个过）、`::test_ip_policy_requires_a_citation`。
浏览器那一层另有一条，它顺带钉住一件**与 policy 有关**的事：
`tests/test_browser_fetch_attempts.py::test_the_governed_disposition_replaces_the_default_one_without_losing_the_cause`
—— 403 的判断句（「不得据此判定出口 IP 被封」）必须活到回执上，而**处置**那一格
按当前 policy 被改写成 `STOPPED_METHOD`（`diagnose` 单独给的是 `REVIEW_REQUESTED`）。
两件事一起判：处置是「实际做了什么」，所以它必须是改写之后的那个；而判断句是
「为什么」，与 policy 无关，所以它必须留着 —— 只剩「停了」的话，读报告的人会自己
补一个根因，最可能补成「IP 被封」。

**负证据**：`tests/test_discovery_run.py::test_a_refused_query_reports_the_cause_as_a_value_not_as_prose`
在**报告面**上再钉一次：403 的每一行 `cause` 都是 `access_denied_unknown`，且**没有任何
一行**是 `ip_policy`。

**「允许继续其他合法来源」那一半**：来源级熔断（`CIRCUIT_OPENED`）只关掉那**一个**来源，
`AccessGovernor._origins` 是按来源分键的 —— 判据同上一条的第 2、3 条。

**标注**：纯函数 + fixture。
**状态**：`DEMONSTRATED`。**缺口**：「不强行升级 browser」是一个**否定**要求，今天的
落点是「升级到浏览器的动作由诊断决定」（`ActionTaken.ESCALATED_TO_BROWSER` 的产出条件），
没有一条判据直接断言「403 之后没有升级」。归**B15 剩余**。

---

### 211 多账号/Scene同一URL → 仅授权范围内single-flight复用；关联保留，不串账号/不重复费用

**机制**：`application/reuse.py::ReuseLedger` —— **一轮 tick 之内、一个许可范围之内**
的共享台账，键是 `(逐字节 URL, FetchMode)`。逐页复核在**取回之前**先问一次
`ReuseDecision`（`application/refresh.py::_recheck_page`），判成 `REUSE` 时把捐赠方的
字节**复制进接收方自己的目录**（`adapters/page_mirror.py::copy_page`，经
`ports/documents.py::PageCopier` 注入），而不是把捐赠方的路径交出去。

这一行是四个分句，各有各的落点：

| 分句 | 落点 |
|---|---|
| **single-flight** | 台账的 `_bodies`：同一个键第二次问就是 `REUSE`，**不再发请求** |
| **仅授权范围内** | 范围 = 工作区，这不是本行另立的：`domain/evidence.py::Evidence.workspace` 逐字写着「复用范围**止于工作区边界**」。执行在**构造处**而不在比较处 —— `refresh.build_refresher` 一轮造一张台账，范围取自它自己那个 `workspace` 参数，而那个函数一轮只服务一个工作区（请求上没有「范围」这一格，台账分不出两个工作区的请求有什么不同）。非规范范围在**构造时抛**（`check_canonical_id`）：两个不同的工作区被静默合成一个，在这张台账上就是一次跨主体的读 |
| **关联保留** | `ReuseLedger.reuse` 交回的 `FetchResult.request` 换成**接收方自己**那一个（`FetchRequest.scope` 会逐字成为 `Evidence.account_id`）；`reason` 串成三段「谁共享的 → 捐赠方为什么升级 → 捐赠方那次抓取自己怎么说」，**捐赠方那一次的升级判定原样接在后面** —— 换掉它，「这一页当初为什么进了浏览器」会在接收方的记录里消失，而它的 `tier` 明明写着浏览器 |
| **不串账号** | 字节落进接收方自己的 `account_page_dir`，读回路径 `(account_id, source_url, content_sha256)`（`page_mirror.read_stored_page`）一个字节都不用改 —— 共享的**不是**一次跨主体读 |
| **不重复费用** | 这一轮之内同一个键最多发一次请求；命中复用的那一页**不 reserve、不 spend** |

**正证据**（本片新文件 `tests/test_page_reuse.py`，14 条）：

* `test_two_subjects_on_one_url_pay_once_a_tick` —— 两个主体、同一个 URL、同一轮：
  `http.calls == [PAGE]`，捐赠方 `held == 1` 而接收方 `held == 0`，接收方逐页记录的
  `reason` 以 `shared_within_range:` 开头。
* `test_the_receiver_gets_its_own_copy_on_disk` —— 接收方**自己目录**下有一份字节，
  与捐赠方那份逐字节相同。
* `test_a_share_hands_over_the_receivers_own_request_and_a_copy` —— 交回的结果里
  `request` 是接收方的。
* `test_the_donors_story_is_not_erased_from_the_receivers_record` —— 记录里的 `reason`
  是 `shared_within_range:acct-a;escalate:browser;rendered_after_js`：捐赠方的名字、
  它的升级判定、它那次抓取自己的说法，三段都在。
* `test_an_unnamed_donor_still_leaves_a_name`（`shared_within_range:unnamed`）。
* 三条**边界**：`test_the_url_is_byte_exact`（`?page=1` / `?page=2` / `#top` 各是各的键）、
  `test_the_mode_is_part_of_the_key`（`http` 与 `browser` 取回的不是同一份材料）、
  `test_the_permission_range_must_be_canonical`（构造时 `ValueError`）。

**负证据**：

* `test_a_donor_without_bytes_is_not_a_donor`（`donor_had_no_body`）—— 一次超时或一次
  403 **不是**可以发给别人的材料：发给别人会把它变成**别人**的观测，而那一次请求发生在
  别人的名下、别人的时刻。
* `test_a_deployment_that_cannot_write_a_second_copy_does_not_share`
  （`no_mirror_writer`）—— 落不下第二份就**不共享**，而不是给出一条读不回去的引用。
* **正对照** `test_without_a_mirror_writer_nothing_is_shared`：同一个场景只把
  `copy_page` 去掉，断言 `http.calls == [PAGE, PAGE]` —— 共享确实发生在那**一格**上，
  不是别处顺手省下的。
* `test_the_ledger_lives_one_tick_and_not_the_process` —— 第二轮 `http.calls` 再加一次
  （**不是**进程级缓存），且第二轮里捐赠方自己那一页是**取**回来的、接收方共享它。
* 两轮变异对照（每轮先清 `__pycache__`）：把 `refresh._recheck_page` 的
  `if reuse is not None:` 折成 `if False and …` ⇒ **3 条集成判据转红**；把
  `ReuseLedger.key` 折成 `return (request.url, "")` ⇒ 只有
  `test_the_mode_is_part_of_the_key` 转红，且失败输出里那行
  `ReuseDecision(..., mode='browser', …).shared` 正是它要挡的形态。

**标注**：fixture（落 `tmp_path`，`FakeHttp` 与一个记名的 `Copier` 替身），零网络调用。

**状态**：`DEMONSTRATED`（本片补齐）。

**缺口**（归 **B15-h 剩余**，本片不假装覆盖）：

1. 设计给这条判据的键有**五样**，今天有生产者的只有**两样**（逐字节 URL 与请求变体
   `FetchMode`）：locale / render 版本 / 解析版本在抓取请求上**没有那几格**。这张表
   逐行写在 `application/reuse.py` 的模块 docstring 里 —— 是「还没有生产者」，不是「忘了」。
2. 台账的寿命是**一轮**。跨轮的第二次取回照付，而那一层归 plan 的时效判定
   （`recheck_pages` 的第二、五步），不归共享 —— 共享省的是「同一轮里同一个 URL 的
   第二次取回」。
3. 本行说的是「多账号/**Scene**同一 URL」。判据里的两个主体是**账号**
   （`acct-a` / `acct-b`），而台账对主语一视同仁（`reuse` 只看 `scope` 是不是规范 id，
   `test_a_non_canonical_workspace_is_refused_at_construction` 校验的就是这一点）；
   同工作区**跨 Scene** 的那一组今天**没有一条判据** —— 那是「同一段代码、另一个主语」
   的推理，不是判据。

---

### 212 同URL内容改变/原文件已存在 → 新工件hash和实际读取字节一致，旧引用不变；不伪造verified_at

**机制**：`adapters/page_mirror.py::store_page_bytes` 的**字节判据**（三段）：
文件不存在 → 写并交回；字节相同 → 交回原文件；字节不同 → 落到
`page_mirror.page_version_path(...)` 算出的**另一个名字**上，并交回**那一版**。
两个抓取器的 `_persist`（`adapters/http.py` 与 `adapters/crawl4ai_fetcher.py`）各自
只挑扩展名，判据**只在 `store_page_bytes` 一处** —— 搬动之前它在这两处各有一份逐字
相同的实现，而更早的时候两个答案不同（浏览器那一侧无条件覆盖，由上面那条负证据对齐）。
`store_page_bytes` 的 docstring 逐字写着这一格守的是什么：「旧引用不变；同一个 URL 换内容时，
新工件落在另一个文件名上，而不是覆盖它」。

**正证据**（本片新增的一条）：`tests/test_page_mirror.py::test_the_second_version_lands_under_its_own_name_and_the_old_reference_still_reads`
—— 同一个 URL 第二次抓到**不同**字节之后，断言：返回的是**另一个**文件、新文件字节是新
内容、新文件仍落在 `page_prefix(URL)` 前缀下、**旧文件逐字节未变**、且
`read_stored_page` 用旧 `content_sha256` 读得回**旧**字节、用新 digest 读得回**新**字节。

**负证据**：`tests/test_page_mirror.py::test_neither_writer_overwrites_bytes_already_on_disk`
（两个写者都不覆盖已在盘上的字节）＋变异对照里的三条
（第二版只算名字不写文件 / 交回原文件而不是那一版 / 原文件无条件覆盖）。

> **为什么这条判据此前不存在**：`command grep -rn "page_version_path" tests/` 在加这一条
> 之前**零命中**。上一条判据（`test_neither_writer_overwrites_bytes_already_on_disk`）
> 只断言「旧文件没被改」，它把写入方的**返回值丢掉了** —— 于是一个「算出了新名字、
> 却没把新字节写进那个文件」的实现能全绿。新判据钉的是承重的那一段：**交回的那一份，
> 读得回它自己说的那些字节。**

**标注**：fixture（落 `tmp_path`）。
**状态**：`DEMONSTRATED`（本片补齐）。
**缺口**：「不伪造 `verified_at`」那一半 —— 今天 `Freshness` 一侧（`application/freshness.py`）
只消费 `fetched_at`，没有任何代码去**写**一个 verified 时间戳，所以「不伪造」今天成立的
方式是「没有那个动作」。归 B15 剩余（与 216 行的对账同一处）。

---

### 213 缺状态码、解析错误、压缩/编码错误 → 记录unknown/parse_error，不折算HTTP200/零邮箱

**机制**：三段，分别落三处。
1. **缺状态码**：`_transport_exchange` 构造 `Observation` 时**不设** `observed_status`
   （观察值就是「没观测到」），`_completed` 的 `response is None` 支也**不设**
   `FetchResult.status_code`。`attempt` 那一格从**回执**取（`http.py:277`），于是账上留空。
2. **解析错误**：`ParseError`（`domain/errors.py:90`）有生产者（`adapters/text_pdf.py` 三处、
   `adapters/mineru_adapter.py:129`）与消费者（`adapters/mcp_server/research.py:1072`）。
3. **压缩/编码错误**：正文解码失败抛 `httpx.DecodingError`，它是
   `RequestError ⊂ HTTPError`（httpx 0.28.1 实测），被 `http.py:548` 的
   `except httpx.HTTPError` 收成一次**传输层失败**。

**正证据**：
- 判定层：`tests/test_access_diagnosis.py::test_unknown_status_does_not_become_200`、
  `::test_transport_error_beats_a_stale_status_code`、`::test_attempt_keeps_an_unobserved_status_empty`。
- **端到端层（本片新增）**：
  `tests/test_http_fetch_attempts.py::test_a_body_that_cannot_be_decoded_is_not_read_as_a_page`
  —— 声明 `content-encoding: gzip` 而正文不是 gzip，断言账上 `observed_status is None`、
  `status_unknown is True`、`ok is False`、`body_kept is False`、回执
  `outcome is ERROR` / `status_code is None` / `body_path is None`、诊断
  `cause is not NONE` 且 `action is not PROCEEDED`、`detail` 里点出这是**解码**那一类、
  且**镜像目录里一个文件都没有**。

**负证据**：同一文件里既有那条 `test_a_transport_failure_records_no_status_and_no_headers`；
变异对照里「传输失败也给回执一个状态码」被 3 条判据抓住（含本片新增的这条）。

> **为什么这条判据此前不存在 —— 一个结构性的原因**：造页面的那个替身**造不出**它。
> `tests/fixtures/http_transport.py::page()` 用 `content=` 造响应，而
> `httpx.Response.__init__` 会**当场** `read()`：于是「声明 gzip 再配一段非 gzip 正文」
> 炸的是 `page(...)` 那一行（实测异常从 `httpx/_models.py` 里抛出来），测试还没走到被
> 测量的那个抓取器。要让解码发生在 `Client.send` 里面（真实那一侧就是在这里发生的），
> 必须给 `stream=` 一个 `ByteStream`，把读取推迟到发送之后。这一条因此**自己造响应**，
> 不复用 `page()`。把 `page()` 当成唯一的造页面工具，就会永远造不出「一个读的时候才
> 失败的响应」—— 而 §11 这一行要求的恰恰是它。

**标注**：fixture（`ByteStream`，零网络）。
**状态**：`DEMONSTRATED`（本片补齐第 3 段；第 1、2 段此前已有）。
**缺口**：`_decode`（`http.py:1003`）对**能**解码但解码错误的正文（例如 UTF-16 页面
按 latin-1 解出乱码）是**宽松**的：依次试 utf-8 → latin-1 → `errors="replace"`。
latin-1 能映射任意字节序列，所以第三条分支**不可达** —— 一份 Shift-JIS 或 UTF-16 的
挑战页不会被判成解码失败，而是被判成一份「正常但读不出内容」的页面。这是 §11 这一行
「零邮箱」那个反面在**另一个入口**上的形态。归 **B15-i**（连同 `_decode` 的 docstring
与代码不一致这件事：docstring 说「失败则替换错误字节」，而实际先走 latin-1）。

---

### 214 恶意重定向/子资源 → 在受控测试里网络发出前被阻止，不只是最后不落库

**机制**：`security/network.py::check_target` 在**发请求之前**跑，`_pin` 把连接钉在
DNS 量回来的地址上（`sni_hostname` 仍是域名），逐跳重判（`MAX_REDIRECTS`）。
禁止 private / loopback / link-local / metadata 端点。

**浏览器那一层的同一句话**（§7：「若所锁浏览器接口不能在发送前拦截全部必要请求，
组合根需采用可验证的受管网络层/拦截器」）：入口判据在 `check_target` 之前不启动任何浏览器，
落地之后 `check_target(final_url)` 再判一次，而 Chromium 的全部网络动作按注入的
`EgressProxy` 走。**这一层有一个直连那一层没有的困难**：一次浏览器渲染里的每一次请求
（主导航、重定向、iframe/worker、子资源）都过出口判据，但那些**不是**这一次抓取的结果
—— 它们的形状是 `FetchResult.egress_refusals`（见 215 行相邻的诊断面）。

**正证据**：`tests/test_acquisition_target_policy.py`（每一跳都断言主机名是 IP 字面量、
`sni_hostname` 是对的 —— **少了这两条，一个「忘了钉」的实现会照样全绿**）、
`tests/test_network_policy.py`。

浏览器那一层的正证据全部锚在**计数**上，而不是锚在「结果是什么」上：

* `tests/test_browser_fetch_attempts.py::test_every_exit_leaves_exactly_one_attempt`
  的出口表里，`URL 是环回地址` / `域名解析到内网` / `DNS-名字不存在` / `DNS-超时`
  四格断言 `library.calls == 0`（**一次都没到浏览器**），而 `渲染之后落在私网地址`
  那一格是 `1`（到了、渲染了、落点被拒）。这个读数就是「发出前」与「发出后」的分界
  —— 「到了但结果被丢弃」在这里拿不到同一个数。
* `::test_a_refused_or_unresolvable_target_never_touches_the_source_ledger`
  —— 被拒的目标连来源账都不该多一条记录。
* `::test_the_browser_goes_through_the_injected_egress` —— 这一条用**真的**
  `BrowserConfig` 构造一次：抓取器算出来的地址必须活着到库会用的那一格
  （真库会把那个字典收编成 `ProxyConfig` 对象，所以只有真构造一次才判得到
  「字段名库认、值原样活着」）。本文件其余各条把库替掉，唯独这一条不能 ——
  拿替身判它只能证明「抓取器交给替身的字典长这样」，那是它自己的说法。

**负证据**：`tests/fixtures/http_transport.py::client` 对**没有登记**的地址直接
`AssertionError`（「这一跳不该被发出」）；`FailingResolver` 造出「这个名字解析到内网」
或「答不出来」两种局面 —— 而在真实 DNS 上这两种局面不可构造（设计 §7 明令不向真实内网
或云 metadata 探测），所以它是可注入的。

**标注**：fixture；`test_browser_egress_live.py` 是**真的**浏览器 + 真的出口，
但它判的是「网络动作真的从出口走」，不是判据表，所以不在这一行的正证据里。
**状态**：`DEMONSTRATED`（**「发出前」那一层是承重的**：判据锚在 `REQUESTS`/`WIRE` 这两个
「真的离开过进程」的记录器上，而不是锚在盘上有没有文件）。
**缺口**：无。

---

### 215 Jev超时/错schema/低把握/误绑 → typed failure或hold，无虚构答案；下游不按"answered"强行确认

**机制**：`ports`／`adapters/jev.py::JevDecisionProvider`：
`configured` / `ready` / `blocker` 三个属性把「没配」「没装」「没就绪」分开报，
`decide` 抛 typed error，`_record` 记 `DecisionRecord`。

**正证据**：`tests/test_adapters_hold_the_vendors.py`（厂商被 hold 的形状）、
`tests/test_decision_providers.py`、`tests/test_decision_store.py`。

**负证据**：「下游不按 `answered` 强行确认」——`decision_id` 谱系那一侧在 `tests/test_application_relations.py`
与 `tests/test_mcp_decision_tools.py` 里有判据。

**标注**：fixture（`Jev` 是替身）。
**状态**：`PARTIAL`。
**缺口（两条，都能被一句话说清）**：
1. **「低把握 → hold」没有落点。** `marketops/adapters/jev.py`（295 行）里
   `confidence` / `hold` / `abstain` 三个词**零命中**。今天 Jev 的回答要么被当成答案、
   要么是一次 typed failure，中间那一档不存在。
2. **`timeout` 没有从组合根传下去**（`interfaces/composition.py:281` 只传 `api_key`）。
   一个没有超时的外部调用在报告上与「问过但没答」长得一样。
   两条都归 **B14**（§9 那一节：已调用 / 已消费 / 实际改进的三分，第三格没有独立标签
   就是 `NOT_DEMONSTRATED`）。

---

### 216 断线/重启/重复事件 → 已批准下一批继续，重复昂贵动作无静默发生；未知计费显式对账

**机制**：前半段（断线/重启/重复事件）在 `application/continuation*` 与
`adapters/sqlite/continuation.py`：Job/WorkItem/Continuation 的三元组 + Quay 的
`settlement`。后半段（未知计费对账）在
`ports/budget.py::open_reservations` + `domain/fetch_attempt.py::SettlementState`。

**正证据**：`tests/test_continuation_contract.py`、`tests/test_continuation_controller.py`、
`tests/test_sqlite_continuation.py`、`tests/test_reconcile.py`；
额度一侧：`tests/test_budget_contract.py::test_open_reservations_lists_what_still_needs_reconciling`、
`tests/test_billed_search.py::test_a_failed_query_keeps_holding_the_budget`
（`len(ledger.open_reservations(SCOPE)) == 1`，「那笔悬着的额度要能被对账找出来」）；
**服务面**（B15-h 3/3）：`tests/test_mcp_budget.py`（10 条，
含「拿回执的 id 去结算必须真的落定」与「空部署上读一次不建库」）、
`tests/test_mcp_fetch_attempts.py::test_a_suspended_charge_is_countable_and_not_inferred_from_the_rest`、
`tests/test_fetch_attempt.py`（两格的内部一致性，正反两个方向）。

**负证据**：`tests/test_reconcile.py` 的重复事件一侧；
`tests/test_mcp_budget.py::test_a_research_identity_cannot_read_the_ledger`（研究身份读不到，
且被拒的理由**不是**「尚未接线」）、
`tests/test_mcp_budget.py::test_an_empty_scope_is_a_filter_and_not_a_wildcard`
（`scope=""` 是「筛不属于任何主体的那批」，不是通配）。

**标注**：fixture。
**状态**：`DEMONSTRATED`（B15-h 5/5 关闭了此前记着的那条缺口）。

**那条缺口（历史，已关闭）**：抓取面这一格曾**恒为 `NOT_APPLICABLE`** ——
`SettlementState` 在生产代码里被写下的地方只有两处，且都是显式传 `NOT_APPLICABLE`
（`domain/fetch_attempt.py` 的字段缺省值，以及两个抓取器的调用点）。B15-h 2/3 把
「判定」与「没想过」在盘上的区别钉出了一条判据
（`tests/test_browser_fetch_attempts.py::test_the_row_names_the_browser_as_the_method_and_the_landing_as_the_final_url`
断言 `budget_settlement is NOT_APPLICABLE` 且 `budget_reservation_id == ""`），但那是
**把缺省变成判定**，不是给那三个成员造出生产者。

B15-h 5/5 把那两处常量换成**一处判断**：`domain/fetch_attempt.py::settlement_of` 把
`FetchRequest.reservation_id` 推成两格（押着就写 `RESERVED` 并把那一份的名字记下来，
请求里没有预留名就写 `NOT_APPLICABLE`），两个抓取器不再各判各的。名字由
`application/refresh.py::_recheck_page` 在**拿到预留之后**补进请求（那一页先预留、
后发请求，只有拿到字节才 `spend`），于是 `RESERVED` 的含义是「这一次尝试是在一份
预留之下发生的」，而不是「这一次花掉了那份预留」—— 它后来是被结算、被退款还是就那么
扣着，是**账本那一侧**的答案，两本账按这一格的 id 对起来。

判据是 `tests/test_settlement_producer.py`（14 条，零网络），四段：规则是全函数且它
产出的两格永远落得下账；请求上那一格过形状判据（拒绝而不是规范化，且与账本算 id 是
**同一个量具**）；**接进页面复核之后账上那一行指得回账本里的那一条**（拿到字节的那
一次问得到 `SPENT`，一跳都没发出去的那一次问得到 `RESERVED` 且仍然扣着额度 —— 那一行
是那笔「没对账」在报告上唯一的痕迹）；一条扫描判据钉住「**没有哪条抓取路径**声称
`SETTLED` / `UNKNOWN_PENDING_RECONCILIATION`」，扫描器的字面量与成员访问两半各有一条
变异证明它在判事（`mutation-check-b15h-5.py` 的 M8 / M9）。

剩下两条仍然**没有生产者，而这是事实不是待办**：`SETTLED` 需要一次**我们结算过**的抓取
出口，而抓取这一层不消费任何计费额度（仅有的计费出口是检索 Serper 与抽取 Jev，都不
经过 `FetchAttempt`）；`UNKNOWN_PENDING_RECONCILIATION` 需要一次**对我们自己未知**的
计费出口，而本账本里的预留永远不会对我们未知。真到了那一天，要改的是 `settlement_of`
（给它加一个那个出口才拿得到的输入），**不是**两个调用点各补一句常量 —— 判据今天钉住
的正是这个形状：哪条抓取路径声称了这两态，哪条就红。

B15-h 3/3 补上的消费者与判据照旧成立：

* `UNKNOWN_PENDING_RECONCILIATION` **没有生产者**（生产代码里零个赋值点），
  但它没有消费者：`list_fetch_attempts` 的 `counts.by_settlement`
  （`adapters/mcp_server/fetch_attempt.py`）把四个成员**逐个**数出来，
  判据是 `tests/test_mcp_fetch_attempts.py::test_a_suspended_charge_is_countable_and_not_inferred_from_the_rest`
  —— 它先钉住「这一格不能从别的格推出来」（`by_settlement["unknown_pending_reconciliation"] == 1`
  时 `reserved == 0`），再断言那一行自己说得出 `budget_reservation_id`。
  两格的**内部一致性**（`domain/fetch_attempt.py::_the_settlement_names_its_reservation`）
  同样有了判据：`tests/test_fetch_attempt.py` 用一张检查过是**划分**的两张表
  （`_OWES_A_RESERVATION` / `_DOES_NOT_OWE_A_RESERVATION`）判两个方向 ——
  欠而不指要拒、不欠而指了也要拒，且枚举新增成员时两条路径都拒绝受理。
  B15-h 5/5 给 `RESERVED` 造出了生产者之后，这一格**不再**是「有判据、没生产者」的
  孤例：`RESERVED` 有了生产赋值点，而剩下那两个成员没有生产者的**理由**被写成了一条
  会红的扫描判据（声称它们就红），不再依赖读的人记得。
* `open_reservations` 在**生产代码里有调用者了**：
  `adapters/mcp_server/budget.py::list_open_reservations`（只读，
  `_CONTROL`，目录 `security/catalog.py` 的相位 J，`docs/MCP_PRODUCT_LOOP.md` §2 有行），
  组合根 `interfaces/mcp/__main__.py::_compose` 用与 `get_job` 同一个
  `partial(SqliteBudgetLedger, settings.database_path)` 接到它上面。
  判据是 `tests/test_mcp_budget.py`（10 条），其中最承重的一条是**开环 → 闭环**：
  拿这一页回的 `reservation_id` 去调 `ledger.spend(...)` 必须真的落定，
  用完再读必须为空 —— 一个自己拼一个好看 id 的实现能让所有形状判据全绿而对不了账。
  角色一侧由 `tests/test_mcp_product_loop.py::test_a_research_principal_cannot_see_or_call_the_product_loop`
  判（它必须**真的接线**才作数，否则「不可见」的理由会从「角色不对」漂成「没接线」）。
  这一页在生产上**读得到东西**：`search` 科目由 `application/billed_search.py` 预留并结算
  （`:121` / `:145`），`document` 科目由 `application/refresh.py::_recheck_page` 预留并
  结算（先预留、拿到字节才 `spend`），而**一次失败的检索会一直押着那笔额度**
  （`tests/test_billed_search.py::test_a_failed_query_keeps_holding_the_budget`）
  —— 那正是这一页要列出来给人对账的行。仍未闭环的是 **`browser_subrequest` 科目**：
  全仓生产代码零个预留点（只有消费侧与一条手工播种账本的判据），所以这条线在上游没有
  生产者，这一页对它恒回零。（抓取尝试那一侧的 `SettlementState` 曾并列在这里，
  B15-h 5/5 之后它有了生产者，见上。）

这是 §16 G4 点名的那种状态的一个**真实实例**：「仅有类或 schema，而无真实
生产者/消费者 = NOT COMPLETE」。归 **B15-h**（与 211 同一片：两件事都要求「一个抓取
尝试的花费能被事后算清」）。

---

### 217 一个产品线暂停/另一个仍active → 前者不新增昂贵动作，后者照常受配额调度；账号事实仍共享

**机制**：**本矩阵不覆盖。** 它要求 `ProductLine` / `ProductVersion` 一级的暂停语义与
配额调度，属设计 §8（B01/B02）与 §7（B17）的地界。

**状态**：`OUT_OF_SCOPE_HERE`。
**缺口**：归 **B01/B02**（ProductLine 不是研究前提，但「暂停」要求它存在）与 **B17**
（配额调度要挂在 `EffectiveResearchPlan` 上才有主语）。

---

## 本片（B15-g）新增的两条证据

| 行 | 新判据 | 补的是哪个洞 |
|---|---|---|
| 212 | `tests/test_page_mirror.py::test_the_second_version_lands_under_its_own_name_and_the_old_reference_still_reads` | `page_version_path` 在 `tests/` 里零命中 —— 第二版文件的名字与字节从无判据 |
| 213 | `tests/test_http_fetch_attempts.py::test_a_body_that_cannot_be_decoded_is_not_read_as_a_page` | 全仓 tests 无 gzip / `DecodingError` / `content-encoding` 命中 —— 压缩/编码错误那半边没有端到端判据 |

两条都进 `TARGET-CONVERGENCE-B15F/mutation-control-3.py`（不在仓库里），
**23/23 条变异被判据抓住**，日志 `mutation-control-3.log`。

其中变异 18 值得单独记一笔：它最初写成「在 `_transport_exchange` 的 `Observation` 上
折一个 `observed_status=200`」，跑出来是 **GREEN**。查清楚之后那是**变异本身行为等价**，
不是判据不够强 —— `diagnose` 在 `transport_error` 非空时先判传输失败，
`observed_status` 压根不参与（实测折成 200 与折成 0，结论逐字都是
`transient_or_unknown` / `deferred_backoff`），而回执与尝试账的状态码都不从那一格取。
也就是说**那一格在传输失败这条路上今天没有消费者**。留着一条行为等价的变异在表里会
稀释这张表，所以 18 换成了本片真正的落点：折在**回执**上（3 条判据抓住它）。

## 本片（B15-h 3/3）新增的证据

| 行 | 新判据 | 补的是哪个洞 |
|---|---|---|
| 216 | `tests/test_fetch_attempt.py`（13 条） | 那两格预算字段**从无一致性判据** —— 一行说「押着额度」而答不出押的是哪一份，今天在构造上不可能，此前没有一条判据说过这件事 |
| 216 | `tests/test_mcp_fetch_attempts.py::test_a_suspended_charge_is_countable_and_not_inferred_from_the_rest` | `UNKNOWN_PENDING_RECONCILIATION` **没有消费者**：账上数得出它，且不能从别的格推出来 |
| 216 | `tests/test_mcp_budget.py`（10 条） | `open_reservations` **在生产代码里没有调用者** —— 对账的第一步在服务面上无从开始 |

三条里最承重的一条是 `test_mcp_budget.py::test_the_id_this_page_hands_back_is_the_id_the_settlement_takes`：
**开环 → 闭环**（拿回执给的 id 去 `spend`，必须真的落定；用完再读必须为空）。
一个自己拼一个好看 id 的实现能让形状判据全绿而对不了账 —— 而「对得了账」正是这一页存在的理由。

十二条变异进 `TARGET-CONVERGENCE-B15H/mutation-check-b15h-3.py`（不在仓库里），
**12/12 各让判据转红**，日志 `mutation-control-b15h-3.log`。

一处值得记一笔：M5 把 `by_settlement` 的**四个键全留着**，只把没结算的一律记成
`reserved` —— 那是「按其余推出来的实现」的精确形状。它抓住了
`test_a_suspended_charge_is_countable_and_not_inferred_from_the_rest`，而**没有**
让「四个成员都列出来」那条红：后者只判键集合，判不到值。两条判据各守一半，
这也是把它们分成两条的理由。

另一处：M3 第一次跑是**跳过**（锚点写了三行、而第三行在源码里还带着半句中文），
不是假绿 —— 脚本把「锚点出现 0 次」与「变异之后仍全绿」报成两类，正是为了
不把「我写错了锚点」读成「这条判据没用」。锚点改成两行之后重跑，转红。

## 本片（B15-h 4/4）新增的证据

| 行 | 新判据 | 补的是哪个洞 |
|---|---|---|
| 211 | `tests/test_page_reuse.py`（14 条，新文件） | 跨主体的同一 URL 复用**整个不存在** —— 同一轮里两个主体抓同一个 URL 会各发一次请求、各落一份文件、各扣一份额度 |
| 211 | `tests/test_resident_controller.py::test_the_controller_hands_the_refresher_a_mirror_writer` | 组合根漏接一样东西的形态是**静默**的（缺省 `copy_page=None` 的含义是「本部署落不下第二份」），此前没有任何判据说得出接线在不在 |
| 211 | `tests/test_mcp_product_loop.py::test_the_maintenance_tool_hands_the_refresher_a_mirror_writer` | 同上，另一个入口。两个入口只有一份判据时，漏的那一个的报告仍然自洽 |
| 211 | `tests/test_page_mirror.py` 的三条复制判据（不属于这个 URL 的不复制 / 只有前缀的名字拒绝 / 名字整段带走） | 第三类写入方的两道守卫与扩展名取回从无判据 |
| 212 | `tests/test_page_mirror.py` 原有 13 条（未改动） | 211-a 的搬动本身：写入判据收成 `store_page_bytes` 一处，就是那 13 条判据在守 |

这一片里最承重的两条是 `test_two_subjects_on_one_url_pay_once_a_tick` 与它的**正对照**
`test_without_a_mirror_writer_nothing_is_shared`：前者说「省了一次取回」，后者（只把
`copy_page` 这一格拿掉）说「省下来的那一次确实是这一格省下的」。只有前者时，一个
「因为别的原因恰好没发第二次请求」的实现也能全绿 —— 而它会在 `copy_page` 真的接上之后
才露出原形。

十八条变异进 `TARGET-CONVERGENCE-B15H/mutation-check-b15h-4.py`（不在仓库里），
**18/18 各让判据转红**，日志 `mutation-control-b15h-4.log`。分组与它们各自打在什么上：

* **A 组（台账）** M1–M8 —— 键、没字节的捐赠方、落不下第二份、接收方的 `request`、
  reason 三段、无名落款、许可范围校验、复制落点。
* **B 组（复核那一侧）** M9–M11 —— 判定排在哪、台账记不记、整格那一句说不说得出。
* **C 组（页面镜像）** M12–M16 —— `store_page_bytes` 的第二版那一支（211-a 的承重判据）
  与 `copy_page` 的两道守卫加扩展名取回。
* **D 组（两个组合根）** M17–M18 —— 各删一处接线。

M3 值得单独记一笔：它折的是「落不下第二份也照样共享」，而**两条**判据同时转红 ——
台账那一条（理由不再是 `no_mirror_writer`）与集成那一侧的正对照（`http.calls` 从
`[PAGE, PAGE]` 变成 `[PAGE]`）。正对照能红，正是它存在的理由：没有它，M3 只会让一条
判据转红，而那条判据判的是**理由字符串**，不是「到底有没有省下那一次请求」。

## 本片（B15-h 5/5）关闭的那一条缺口

| 行 | 新判据 | 补的是哪个洞 |
|---|---|---|
| 216 | `tests/test_settlement_producer.py`（14 条，新文件） | 抓取面那两格**恒为 `NOT_APPLICABLE`** —— 两个调用点各写死一个常量，于是一次押着预留发起的抓取在账上写着「我没花钱」 |
| 216 | `marketops/domain/fetch_attempt.py::settlement_of`（新） | 把「哪一格该写什么」收成**一处判断**；此前要改的是两个调用点，而漏改一个不会有任何东西变红（那一格正是这样长期恒为 `NOT_APPLICABLE` 的） |
| 216 | `marketops/ports/acquisition.py::FetchRequest.reservation_id`（新字段） | 预留的名字此前**没有落点**：账本里扣着的那一份在尝试账上无名可指 |

这一片里最承重的一条是 `test_the_attempt_row_points_back_at_the_reservation_that_paid_for_it`
里那句**连接**：拿账上那一格里的 id 去账本里问，问得到那一条，且它的科目、作用域、
幂等键都指回**这一页**。只断言「那一格写着 `reserved`」的实现可以编一个 id 出来而
判据不红 —— 而编出来的 id 正是对账的人核不出东西的那一种。它的**另一半**
（`test_a_page_that_never_left_this_machine_still_names_the_reservation`）落在同一个
问题上更值钱的一面：一跳都没发出去、一分钱没花，而额度仍然扣着 —— 那一行是那笔
「没对账」在报告上唯一的痕迹。

九条变异进 `TARGET-CONVERGENCE-B15H/mutation-check-b15h-5.py`（不在仓库里），
**9/9 各让判据转红**，日志 `mutation-control-b15h-5.log`。分组与它们各自打在什么上：

* **A 组（推导规则）** M1–M3 —— 规则永远答 `NOT_APPLICABLE`（写死的行为只是搬进了
  函数里）、名字换成**形状合法但账本里没有**的那一个、落账时不读推导结果。
* **B 组（请求上那一格）** M4–M6 —— 名字不进请求、请求不再过形状判据、形状判据本身
  放过一切。
* **C 组（两跳同一个请求对象）** M7 —— 升级那一跳拿到的是拷贝。
* **D 组（「没有生产者」那句话）** M8–M9 —— 扫描器的字面量那一半与成员访问那一半。

M2 值得单独记一笔：它折的是「编一个形状合法的 id」，红的是**三条**判据，其中一条正是
那句连接 —— 而 M1（永远答 `NOT_APPLICABLE`）红的三条里也有它。两条方向相反的折法
落在同一条判据上，说明它判的是「这一行有没有指着一份真的东西」，不是「这一格写了什么
字样」。M8 / M9 两次都**只**让扫描判据转红（它们不改变任何行为），那正是那一条要的
形状：它红了才说明「没有哪条抓取路径声称这两态」是一句被**执行**的话。

## 这份矩阵不做的事

* **不宣称任何 live 结果。** 上面每一条都是 fixture 或纯函数，零网络调用。
  「测试通过」不等于「现场 provider 已验证」（§18）。
* **不把 fixture 说成现场。** 每行的「标注」栏逐条写明。
* **不把 13 行之外的东西算进来。** 别的验收面（B16/B17/B23/B24/B25）各有自己的矩阵，
  这一份只管 §11。
* **不给完成百分比。** 13 行里 `DEMONSTRATED` 10 行、`PARTIAL` 2 行
  （206、215）、`OUT_OF_SCOPE_HERE` 1 行（217）—— 这些是**行数**，不是进度。
  （`NOT_BUILT` 那一类在这一片之后**空了**：唯一的 211 行转成 `DEMONSTRATED`。
  一个 0 行的类目不写出来，免得下一版读成「还有一行没建」。）
  （216 行在 B15-h 5/5 从 `PARTIAL` 转成 `DEMONSTRATED`，于是本节此前那一版
  「`PARTIAL` 3 行」作废：变的是**行数**，而每一行的判词各自带着自己的证据。）
