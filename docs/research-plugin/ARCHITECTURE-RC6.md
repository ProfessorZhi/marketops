# MarketOps 研究插件 · RC6 架构与接续计划

> 分支：`fix/marketops-rc6-contact-binding-20261020`（基于 PR #22 RC5 `7f090588`）。
> 本文是**接续文档**：任何 agent 接手时先读这里，再看「任务清单」里第一个未完成项。
> 每完成一项，在清单里打勾并写上提交号。

## 1. 目标

Bruce 这类销售提出调研需求后，agent 自主把整件事做完：从需求到来源池、目标池，再到联系人和证据池，最后交付。多个对话（Claude Code / Codex / WorkBuddy）各自装同一个插件、共用本机同一个数据库，像多条流水线一样并行，支持上万家规模。人只定边界、在少数必要时刻拍板、最后验收。

三层叠加：

| 层 | 作用 | 落地形式 |
|---|---|---|
| 业务经验（Bruce） | 找什么、怎样算好、常见碰壁及对策 | 可插拔场景包 + 经验参考 + 自动积累的碰壁统计 |
| 工作流程 | 谁在什么时候做什么 | 共享池子 + 领取租约 + 总控 / 深挖 / 质检三种角色 |
| 能力（五个项目） | 用什么做 | 逐级升级阶梯；Jev 替 Claude 做重复判断（润滑油） |

## 2. 数据流与池子

```
需求(brief) → 来源池 → 目标池(pending/claimed/done/hold/excluded) → 联系人池 + 证据池 → 交付快照
               ↑总控窗口        ↑深挖窗口 ×N（pool_claim 领 50 家，带租约）      ↑质检窗口
所有窗口用同一个插件、同一个数据目录（SQLite WAL）；交接只靠库里的状态，不靠文件或口头转述
```

- 数据目录由用户在首次设置时指定（`marketops_setup_configure`）。必须是本机硬盘，不能放在同步盘或网络盘上。
- 目标池跨 Run 共享：`pool`（项目）→ `target`（规范化键去重：名称 + 邮编/地区 + 域名辅助）。
- 领取带租约（默认 30 分钟）；会话崩溃后租约过期，自动可被别的窗口领。
- 深挖结果仍写成现有 Run 的候选记录（复用证据门禁），目标池只记状态和指针。

## 3. 能力层：升级阶梯

| 环节 | 第一档（默认） | 第二档 | 第三档（攻坚，需授权） |
|---|---|---|---|
| 找来源 | Serper | SerpApi（google_maps 等） | TinyFish search |
| 抓网页 | Crawl4AI 本地（免费） | TinyFish fetch（免费） | TinyFish 浏览器 Agent（付费，攻坚预算内） |
| 抽名录 | Crawl4AI 整站抓取 + CSS 抽取 | TinyFish fetch | — |
| 判断 | Jev（≥0.85 直接作为候选） | Claude 复判 | 人工 |

- 第三档只用于：分页检索门户、点击后才显示的邮箱、用户自己合法账号的登录态目录、高价值目标在前两档都失败时。验证码和反爬不碰。
- 域名记忆：某域名第一档失败、第二档成功后，同项目后续请求直接从第二档开始。

## 4. Jev 嵌入点（提速）

| 用在哪 | 问题 | 原语 |
|---|---|---|
| 场地分诊 | 真草 / 3G / 室内 / 高尔夫 / 不确定 | choice（多问题批量） |
| 页面价值 | 这页有没有职员或联系信息 | noul |
| 角色归类 | 联系方式上下文属于哪个角色组 | choice |
| 级别预判 | 按场景包规则预判 A/B/C/D/X | choice |
| 去重边界 | 两条是否同一机构 | noul |

规则：Jev 输入必须是已保存的 artifact；结论只作为候选和排序依据，事实只认原文引文。

## 5. 业务经验：场景包

`references/scenes/<scene>.md`：目标角色和优先级、分级规则、排除类别、权威来源清单、查询模板、语言和地区、交付列格式。brief 用 `scene` 字段指定；不指定就不加载任何场景包。现有 `ACCEPTANCE_PROFILE_UK_GRASS.md` 迁移为第一个场景包。

## 6. 自动碰壁经验

- 每次受管调用的失败或升级都自动写一条 `wall_event`（域名、阶段、项目、失败码、最终成功的档位、成本），agent 不用做任何事。
- 域名记忆来自这些事件。
- `marketops_lessons_report` 导出 Markdown + CSV：碰壁类型占比、各档救回率、每条 A/B 联系人的成本、来源方法账户率、建议新增的经验。报告里的经验只是候选，人审后才写进场景包或 skill。

## 7. Skill（渐进式披露）

元数据始终加载；正文用到才加载；细节放在 references。
- 入口：`marketops-research`
- 角色：`marketops-orchestrate`（总控）、`marketops-accounts`（深挖）、`marketops-delivery`（质检交付）
- 其他：`marketops-discovery`、`marketops-setup`
- 项目：`marketops-providers`（短路由）+ `marketops-serper` / `-serpapi` / `-crawl4ai` / `-tinyfish` / `-jev`

## 8. 一键打包

- TinyFish、SerpApi 官方 MCP 已作为客户端内嵌在插件 MCP 内，无需单独安装（RC6 已真实握手验证）。
- Serper REST、Jev SDK 已内置；Crawl4AI SDK 和 Chromium 由安装器一键安装并自检。
- `/marketops-setup`：一次问 4 个 key 和数据目录，存进系统钥匙串，每家做一次免费验证。

## 9. 任务清单（按顺序，接手者从第一个未勾选项开始）

- [x] **T1 共享任务池**（见本提交）：`research_pool.py` + 工具 `marketops_pool_import / pool_claim / pool_complete / pool_status`；租约、规范化去重、来源列出数对账。验收：两个进程并发领取不重复，崩溃后租约过期可重领，对账公式成立。
- [x] **T2 批量挖掘**（见本提交）：`marketops_harvest_batch`（工具内部并发，全局限速，只返回摘要）；修复 site_harvest 只读 500 条记录的问题；准入改为只检查新写入的记录；放开 500 次额度上限（可配置到 10 万）。验收：200 家合成站点的回放测试耗时线性增长。
- [x] **T3 碰壁记录 + 域名记忆**：`wall_events` 表，由 fallback 和 harvest 自动写入；域名记忆影响升级起点；`marketops_lessons_report`。验收：被拦域名第二次直接走 TinyFish；报告能导出。
- [x] **T4 Jev 批量**（第一版：并发逐页判断，一次工具调用；合并成单次计费的真正批量待做）：`marketops_jev_triage`（多问题批量、置信度阈值）；site_harvest 可选用 Jev 判断页面价值和角色。验收：真实 Jev 调用批量返回，低置信度项被标出。
- [ ] **T5 第三档攻坚**：TinyFish 浏览器 Agent 接入升级阶梯，带攻坚预算和授权开关。
- [x] **T6 Skill 拆分 + 场景包**：按第 7 节重排；英国草坪场景包补充来源和查询模板。
- [ ] **T7 一键安装**：Crawl4AI 和 Chromium 自动安装与自检；`/marketops-setup` 命令。
- [ ] **T8 上游代理**：出站通道支持接 HTTPS_PROXY（公司网络）。

## 10. 接续须知

- 测试：`cd plugins/research-tools/marketops-research-assistant && python3.12 -m unittest discover -s tests`，以及 `python -m pytest tests/qa_gate`。沙箱里有 8 个环境性失败（root 用户、本地代理），与代码无关；比较时看失败集合是否新增。
- 真实测试：key 只能通过环境变量提供，绝不写进仓库。在只能走代理、没有本地 DNS 的沙箱里，需要一个只在测试时使用的出站替身（把 `research_egress.open_session` 指向 HTTPS_PROXY），并在隔离副本里生成 BUILD_QA 回执；这两样都不提交。
- RC6 已完成的部分（见 git log）：同站联系方式绑定、site_harvest、子代理和 `/research` 命令、工具从 31 个减到 18 个、skill 从 17 个减到 6 个、预算随结果返回、`next` 精简、公网抓取授权、未发出的请求不再记成未知费用、TinyFish 和 Jev 的真实接口问题修复、真实 Essex 运行 4/5。
