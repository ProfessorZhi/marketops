# 落地记录 · B01-a —— 产品目录的形状

切片表在 `docs/B01_B02_DESIGN_SLICES_2026-09-28.md:127`（那一行是这一片的**设计**）；
这一份是**落地记录**，只记实际做了什么、以及设计与实际不一样的地方。冻结的那一份
逐字声明过「不再长」，所以本片另开文件。

## 一、这一片判的是哪句话

B01 的标题（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:96`）逐字是「产品资料、Offer
（可选）与 Workstream 启动：**登记不等于开工**」。它的最小实现逐字在 `:100`：

> 最小实现：ProductVersion来源索引/能力/限制/区域变体、可选OfferVersion明确产品
> 组合/允许claims，以及资料访问控制；ProductLine 仅在有真实管理需求时作为可选分组。
> 原始资料进受管artifact store；Git仅获准索引/schema/规则，不上传私有手册/联系人。
> 新型号无核实资料只可draft；旧字符串兼容显示并明确alias迁移，歧义不能猜。

**这一片只落这段的第一半：形状。** 没有存储、没有事务、没有调用者、没有 MCP 面 ——
所以它**不宣称关掉 B01 的任何一条关闭条件**。`:104` 那八条的第一句是「一个获批产品
版本可被两个 Scene 共用且草稿回链相同资料版本」，而「两个 Scene 共用」要有 Scene
那一侧的执行器（b01-g）才算数。

判据文件的模块 docstring 把每一组判据对应哪一种「报告上看起来一切正常的失败」写在
一起，这里不重复。下面记的是**决定**与**改正**。

## 二、落下的东西

新模块 `marketops/domain/product.py`，七个类型加一个枚举：

| 型 | 它回答的问题 |
| --- | --- |
| `SourceArtifactRef` | 这条资料在**哪**，以及它是**哪一份**（`locator` + `content_sha256`） |
| `RegionVariant` | 这个地区**做不到**什么（只能从主档里减） |
| `ProductRef` / `ClaimRef` | 引用落在**恰好那一版**上（`(id, version)`） |
| `QuotedPrice` | 这条报价**从哪来**、按什么算、什么时候的 |
| `ProductVersion` | 我们卖的东西：能力 / 限制 / 区域变体 / 未知项 / 出处 / 负责人 / 状态 |
| `OfferVersion` | 按什么条件可成交：产品组合 / 用途 / 可用市场 / 允许 claims / 可选报价 |
| `ProductLine` | 可选分组，**零行为** |
| `ProductStatus` | `draft -> ready -> retired` 闭集（PSM §4.1 逐字） |

`marketops/domain/outreach.py` 的 `ApprovedClaim`（`:735`）增了三格
（`product_ref` / `markets` / `locales`）与一个谓词 `applies_to`（`:824`），
按 PSM §6 逐字「复用当前 ApprovedClaim 并增作用域」。

三条**有意**的缺席，各自有判据：

1. 这一族里没有原始字节格（B01 `:100` 逐字「Git仅获准索引/schema/规则」）。
2. `ProductLine` 上没有布尔态（PSM §4.1 逐字：是否在经营由已批准 launch / active
   Scene **投影**）。
3. `Product/Offer` 的字段里没有产品线（`:110` 逐字「ProductLine不是运行边界」）。

## 三、判据

写在 `tests/test_domain_product.py`，八组、30 条。分组与它们护着的那句话：

| 组 | 条数 | 护着什么 |
| --- | --- | --- |
| 1 地区变体只能收窄 | 4 | PSM §6「地区版不能偷偷继承无效 claim」 |
| 2 无核实资料只可 draft | 4 | B01 `:100`；反面「草稿不受限」同时判 |
| 3 Offer 引用恰好那一版 | 4 | 「第 2 版」在两次部署之间可能是两份东西 |
| 4 价格只有一处落点且不是数字 | 6 | PSM `:19` / `:156` |
| 5 只留索引不留字节 | 2 | B01 `:100` 的资料访问控制 |
| 6 claim 的作用域是加出来的 | 6 | PSM §6「复用并增作用域」；含「空 = 旧读数」 |
| 7 ProductLine 是可选分组 | 3 | PSM §4.1 / `:110` |
| 8 全仓纪律（规范 id、带时区） | 2 | `util.check_canonical_id` / `util.to_utc` |

三处写法值得单说，因为它们各自堵掉一种「判据看着在判、其实恒为真」：

* **第 4 组的「只有一处落点」是结构判据**：它读
  `OfferVersion.model_fields["price"].annotation`，要求它恰好是
  `QuotedPrice | None`。所以它判的不是「今天有没有人写价格」，而是**类型上有没有
  一个裸数字格**——一个裸格存在的那一天，就有人在某处填一个看起来合理的数。
* **第 2 组与第 4 组都成对判正反两面**（`test_a_draft_is_free` /
  `test_an_offer_may_carry_no_price_at_all`）。少了反面，判据会逼着人「先填点什么」，
  而那正好是「草稿生成猜价」那条禁令的入口。
* **第 6 组第三行（`locale="en-GB"` 落在 `locales=("en",)` 上必须算数）**
  是判据的**收窄方向**。少了它，一个把两串整个比对的实现也全绿 —— 而那正是
  「英国 = 英语」被焊进类型的第一步。

## 四、变异控制：24 个变异全部被杀

脚本 `MUTATION-b01a.py`，读数 `mutation-b01a-readout.json`（两者都在
`/home/operator/artifacts/work-items/marketops/TARGET-CONVERGENCE-B01B02/b01-a/`，
**不在**仓库里）。每个变异配一条期望红的判据，判「杀掉」用 `exit == 1` **且**红名单
非空**且**期望的那一条在里面。

**24/24 被杀**，红名单基本各一条。两个变异连带红了别处，都是同一条谓词的邻条：

* M11（空作用域读成「哪个市场都不算」）连带 `test_a_claim_bound_to_a_product_does_not_cover_another_product`
  与 `test_a_locale_scoped_claim_is_not_inherited_by_another_language` —— 三条都在问
  `applies_to` 同一处的短路方向。
* M12（绑产品的说法对谁都算数）连带
  `test_a_claim_bound_to_one_product_version_does_not_cover_the_next_one` ——
  两条都在判 `product_ref` 那一次比较。

脚本里三条纪律是**从 h 片的教训里抄来的**，不是自选：还原之后才比字节、原文用
`setdefault` 存（同一文件改两处时不丢原文）、每次清 `__pycache__`。

## 五、三处**改正过**的地方（第一稿不对，落地前走回去了）

### 1. 价格格：第一稿把它整个拿掉了

切片表那一行写的是「价格可缺省而**不可编造**」。第一稿读作「缺席即不可编造」，
于是 `OfferVersion` 上一个价格格也没有，判据也是按「没有那一格」写的。

落地前对照 PSM 原文时发现这是**错的**：`:19` 逐字「价格可缺省而不可编造」，
`:156` 逐字「无价格可照样研究」——「价格可缺省」是一个**被写下来的状态**，
拿掉那一格就让它在这族类型里表达不出来，而下一个需要报价的人会自己加一个
`price: str = ""`，绕开「不可编造」那一半。

**改正**：加 `QuotedPrice`（`currency` ISO-4217 / `amount_minor` 整数最小单位 /
`basis` / `source_ref` / `quoted_at`），`OfferVersion.price` 是
`QuotedPrice | None`。两半都有执行器：类型上**可以**为空，而**造不出**一个没有
出处的报价。金额用整数最小单位与本仓 `domain/budget.py` 的 `units: int` /
`ceiling: int` 同一条理由。

判据从「没有那一格」换成 6 条（4 节表格里的第 4 组），变异从 1 个换成 5 个
（M8、M20–M24）。

### 2. `covers` 是**撞名**，改叫 `applies_to`

`outreach.py` 里已经有一个 `covers`（`WaveAuthorization.covers`，`:1404`），它问的是
「这份授权够不够覆盖这份清单」，而且**返回一条理由字符串**而不是布尔 —— 因为
「不够」那一个字读不出该做什么（撤回要去问人、内容变了要重新批、过期了要续）。

claim 这一侧问的是另一件事（「这条说法适用于这里吗」），形状也不同（一个**用来筛**
的说法集合上的谓词）。同一个模块里让一个名字表示两件事，读者每次都要回头确认自己
看的是哪一个 —— 所以这一侧叫 `applies_to`。改名没有动 `WaveAuthorization.covers`
的任何调用点。实测那**五处**全部原样：`adapters/mcp_server/outreach.py:1478` /
`:1484` / `:1541`、`application/outreach.py:1841` / `:2052`（另有两处是散文里的
引用：`adapters/mcp_server/outreach.py:1415`、`application/outreach.py:1830`；
`tests/` 里没有直接调用，它由 `tests/test_outreach_contract.py:1766` 从行为上判）。

### 3. 判据自己写错两处（不是实现的毛病）

* `model_copy(update=...)` **不走校验器**。第一稿用它探「规范 id」与「带时区」，
  两个探针在任何实现下都不会红。改成直接构造。
* 原始字节那条按**字段名里有没有** `content` 之类查，而 `SourceArtifactRef`
  的 `content_sha256` 会一起被判红。改成按 `_` 切词元、并且只放行名字自己声明是
  摘要的那一种写法（`sha256` / `digest` / `hash`）——例外只有一条，且它自己要说清
  自己是摘要。

## 六、这一片**不宣称**关掉的东西

* **B01 的八条关闭条件一条也没关**（`:104`）。理由是第一节那句：这一片只有形状。
* **旧的 9 处 `product: str` 一个也没改。** 在当前树上重测过：`domain/scene.py:133`、
  `domain/outreach.py:636`、`adapters/mcp_server/outreach.py:732` / `:2130`、
  `adapters/mcp_server/product_loop.py:556` / `:755` / `:867` / `:4942` / `:4976`。
  B01 `:100` 逐字要求「旧字符串兼容显示并明确alias迁移，歧义不能猜」——那是
  **b02-a**（设计件切片表那一行的括号里逐字「含摘要约束处理、alias 映射」）。
* **`contracts/` 一个字没动。** 实测那 15 个契约模型是
  `account-candidate` / `account-cohort` / `account-record` / `account-venue-relation` /
  `contact-route` / `evidence` / `fetch-attempt` / `market-account` / `person` /
  `research-run` / `responsible-entity` / `role` / `route-opportunity` /
  `source-receipt` / `venue-asset` —— `ApprovedClaim` / `SceneVersion` /
  `ProductVersion` 一个都不在其中，所以给 `ApprovedClaim` 增作用域不触发契约重生成。
* **没有生产者、没有消费者、没有存储。** 按 `:104` 的判词（「只有类名/字段/接口的
  无消费者实现不算完成」），这一片交付的**正是**那种东西，所以它**不算** B01 的完成 ——
  它是 B01 后面几片的前提。

## 七、差量台账（设计件说过的，与实际做的不一样的地方）

| # | 设计件说 | 实际做 | 为什么 |
| --- | --- | --- | --- |
| 1 | `OfferVersion` 含「价格可缺省而不可编造」 | 做了，但**第一稿落成「没有价格格」**，落地前走回去 | 见第五节第 1 条 |
| 2 | `ApprovedClaim` 增作用域（产品 / 市场 / **语言** / **有效期**） | 产品 / 市场 / 语言三格新增；**有效期没有新增** | `expires_at` 早已存在且有 `is_live_at`（`:809`）。这一片做的是把「期限」与「作用域」并进**一个**问题（`applies_to` 先问 `is_live_at`），而不是再加一格 |
| 3 | 设计件没提方法名 | claim 侧的谓词叫 `applies_to`，不叫 `covers` | `covers` 在本模块已被 `WaveAuthorization` 占用，且返回类型不同 |
| 4 | 设计件列的判据分组（形状级） | 判据 30 条，其中 8 条是**结构判据**（读 `model_fields`），不是行为判据 | 「不可编造」「不留字节」「零行为」这三件事在行为上判不出来 —— 它们说的是「类型上没有那个动作」 |
| 5 | 切片表说 b01-a 是「1 的前提」 | 仍是。本片**不**改矩阵里 B01 那一行的判词 | 矩阵在 `docs/B17_ACCEPTANCE_MATRIX_2026-09-28.md`，它判的是 B17 的七条；B01/B02 的判词要在 B01 收口时另写 |

## 八、实测读数

* 本片判据：`tests/test_domain_product.py`，**30 passed**。
* outreach 四族既有判据（`test_outreach_contract.py` / `test_outreach_store_contract.py` /
  `test_mcp_outreach.py`）＋本片：**306 passed**，`EXIT=0` —— 为的是确认给
  `ApprovedClaim` 加三格没有动到任何既有读法。
* 全量套件（本片终树，一次跑完）：**6328 collected，6324 passed，4 skipped，
  `EXIT=0`**。日志 `suite-b01a.log`。
* 上一条与本片的差**全部归位**，没有靠减法含糊过去：本片相对上一片基线
  （h 片 `ce65e2c`）净增 **32** 条 = 本片判据文件 **30** 条 + **2** 条
  **不是本片写的**——`tests/test_domain_is_pure.py` 按 `marketops/domain/*.py`
  参数化，新模块 `product.py` 一落地就自动进了它的射程：
  `test_domain_depends_on_nothing_outside_itself[product.py]` 与
  `test_domain_imports_nothing_impure[product.py]`，两条都绿。
  中间那次「差 2 条对不上」是用 `a14b523`（切片划分设计件）当解释编出来的，
  实测**它贡献 0 条**。量法：`git archive ce65e2c` 导出到证据目录下一棵
  临时树、各自 `--collect-only` 再取 id 差集（两份 id 清单留在 `b01-a/` 下），
  **没有动工作树，也没有建 worktree**。临时树测完即删。
* `ruff check .`：All checks passed!（本仓**从不**跑 `ruff format`。）
* `git status --porcelain` 四项：`M marketops/domain/outreach.py`、
  `?? docs/B01_A_PRODUCT_CATALOG_2026-09-28.md`、`?? marketops/domain/product.py`、
  `?? tests/test_domain_product.py`。（落地记录落盘**之前**是三项；本节第一稿
  写的也是三项，已按实测改。）

下一个切片是 **b01-b**（登记面 + 「登记不产生网络调用」构造化）。
