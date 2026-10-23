# 当前 v0.4.0 统一 Research MCP 验收入口

**唯一现行发布门禁是 [marketops-research-assistant/references/RELEASE_GATE.md](marketops-research-assistant/references/RELEASE_GATE.md)**。下方旧版 v0.1–v0.3 测试清单是历史记录，不可直接按其 8/15 Skills、空 MCP或仅双搜索MCP 判断当前版本已合格。

本次源码已准备：16 Skills、一个本地 marketops-research MCP、SerpApi/Serper/Crawl4AI/TinyFish/Jev 五家的基础路由、单客户SQLite暂存、操作事件/经验包、Codex/WorkBuddy机器本地安装计划。

先在源码目录执行（零真实 Provider 请求）：

~~~bash
python scripts/preflight.py
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
python scripts/setup_clients.py --destination /USER/EMPTY/INSTALLROOT --client both
~~~

没有本地测试结果或 Client tools/list 结果时，状态必须是 NOT_RUN，不得因为文件已入库就标 PASS。经用户明确批准才执行带 --apply 的本地安装；依赖下载需要单独 --install-deps。真实 API、身份、企业关系、正式联系人 A/B/C/D、Excel XLSX 与体验回放须按 RELEASE_GATE 的 D/E 环节逐项验收。**在这些验收前不出正式给 Member-A 的 ZIP。**

---

## 历史候选测试（v0.1-v0.3；保留溯源）


## A. 静态检查（可离线）

- [ ] 所有 JSON 解析成功；WorkBuddy 连接器每包恰好一个 `mcpServers`；所有源标识唯一。
- [ ] `SKILL.md` 有 `name` 与 `description`；链接指向官方来源或标记为第三方。
- [ ] 无真实凭据、绝对服务器路径、生产账号及授权绕过；没有新建东京文件或修改代码服务。
- [ ] Codex `.mcp.json` 中的远程 MCP 参数与官方规范匹配。

## B. 客户端真实安装（未验收）

- [ ] Codex：安装/注册后重新打开会话，工具可见；用户完成 TinyFish OAuth，`search`/`fetch_content` 可用。
- [ ] WorkBuddy：用真实客户端版本导入连接器，认证成功；低版本按 `minWorkbuddyVersion` 说明兼容行为。
- [ ] Crawl4AI：用户自己的 Cloud API Key 与 MCP 可通过只读或低成本单页请求验证。
- [ ] Serper：仅测试一次用户批准的搜索（记录 country / language / credits）。第三方 MCP 需额外安全审查。
- [ ] Jev：官方 Skill 可识别，TypeSafe SDK 在用户批准后执行一个已标注的有限分类题，概率保存原值。

## C. 业务验收（人工复核）

- [ ] 固定最多 5 个已批准的公开目标站点；记录成功/失败/阻断/费用。
- [ ] 搜索结果只作为发现候选，官网有效正文及抓取时间单独留存。
- [ ] 同一网站的多个场地不因按主机去重而被错误合并。
- [ ] 每个场地的机构归属和人员任职关系都有证据或标注为待核实。
- [ ] 中断/重复执行不会悄悄扩大浏览器使用、预算或重复外联。

## 交付状态字段

`NOT_TESTED` / `PASS` / `PARTIAL` / `BLOCKED` / `FAIL`。单独记录每个客户端版本、插件版本、供应商模型/接口、任务 ID、证据 URL 和费用。**静态 JSON 通过不是“插件实测成功”，单个 API 成功不是全流程完成。**



## D. v0.2 MarketOps Research Assistant（新增，未实机验收）

主入口：[`marketops-research-assistant/README.md`](marketops-research-assistant/README.md)。**不依赖四家 Provider 已安装**：先验证全部离线门禁，再用用户获批账户单次测试 Search/Fetch。

离线检查命令（在 `marketops-research-assistant/` 下运行）：

```bash
python -m unittest discover -s tests -v
python scripts/research_pack.py init ~/marketops-research-data/check-one --request-id check-one --country GB --scene gb-demo --objective "Offline schema smoke test"
python scripts/research_pack.py validate ~/marketops-research-data/check-one
python scripts/research_pack.py summary ~/marketops-research-data/check-one
python scripts/export_excel_safe_csv.py ~/marketops-research-data/check-one --output ~/marketops-research-data/check-one-excel-csv
python scripts/build_client_bundles.py --output ~/marketops-research-bundles
```

`STRUCTURALLY_VALID_EMPTY` 仅表示空的 11 张 CSV 表形状正确，不得升级为真实研究完成。交接 CSV 工具**不会**生成 XLSX；生成真正 Excel 需 Agent 已具备 Excel 文件处理能力，并按 [WORKBOOK_SPEC](marketops-research-assistant/references/WORKBOOK_SPEC.md) 逐格复验。

- [ ] **15 个 Skills** 在真实 Codex 中可见；v0.3 `.mcp.json` 注册一个本地双搜索 MCP，但未设 Key / 网络开关 / 额度时拒绝真实付费请求。
- [ ] WorkBuddy 4.24.0+ 可以导入默认 `skill-only` ZIP，15 个 Skills 可见；`--with-search-mcp` 模式另验 Token 表单与机器绑定 stdio。
- [ ] DeepSeek Harness：基于官方 filesystem Skills 路线验证技能加载，原生 Cordis Plugin **仍为未发布**。
- [ ] 真实 3–5 家调查产生正确 SOURCE→ENTITY→EVIDENCE→CONTACT 引用，不用搜索摘要/挑战页充当证据。
- [ ] 故意损坏一条外键/证据 SHA/邮箱字面来源/人员归属，`research_pack.py validate` 必须失败。
- [ ] Excel 安全导出故意填入以 `=HYPERLINK(...)` 开头的来源文本，导出不能被 Excel 当作公式执行。
- [ ] 人工审核一条 `ready` 联系路线后仍不得自动发送邮件、提交表单或提高预算。

**静态 JSON/Frontmatter/Markdown 校验 ≠ Python 单元测试 PASS ≠ 客户机安装 PASS ≠ Provider 真实验收 PASS。** 未实际跑过的层级均维持 `NOT_TESTED`。


## E. v0.2.2 本地事件自动保存（待执行并验收）

- [ ] python -m unittest discover -s tests -p 'test_workflow_event_journal.py' -v 在客户批准的隔离环境中运行。
- [ ] 在客户数据根外运行CLI init，检查研究包与state/research-events.sqlite3确实落盘；日志中只有非敏感操作元数据。
- [ ] Excel-safe CSV导出成功时追加export操作事件。
- [ ] 相同operation的相同状态重复写入幂等；同标识但内容不同拒绝。
- [ ] 错误/阻断事件生成候选经验时根因必须是unknown，不能自动被证据不足的事件批准。
- [ ] 未通过统一Research MCP的四个Provider操作必须显示NOT_CAPTURED/observability_gap，不得声称自动记录全部调用。
- [ ] 不在Git、临时共享目录、生产MarketOps数据根存真实客户原文或Keys。

以上是待验收要求，不是实时客户端实测结果。

## F. v0.3 双搜索 MCP（已入库、离线和客户端验收待完成）

- [ ] `python -m unittest discover -s tests -p 'test_search_dual_search_mcp.py' -v` 使用合成密钥/mock 响应，绝不发真实 API 请求。
- [ ] 安装 Python `mcp>=2.2,<3` 后真实 `tools/list` 可列出 `marketops_search_capabilities` 与 `marketops_search`。
- [ ] 默认联网关闭 / 额度为 0；能力查询不收费；真实搜索被拒绝。
- [ ] 两种凭据各进对应官方域名/鉴权方式，不自动切换，不复制另一家 Key。
- [ ] Web/Maps 结果保留明确 Provider、Place ID、时间和发现状态；搜索摘要不得成为业务 Evidence。
- [ ] 跨重启累计操作预留有效，失败/超时不能用相同 operation_id 重复扣费。
- [ ] 搜索回执及事件不包含真实 Key，SerpApi key-bearing GET 不得被重定向；代理日志风险明确告知。
- [ ] WorkBuddy MCP+Skills 候选在目标机器重新打包，Token 字段与 mcp.json 的 env 完全匹配。
- [ ] 固定英国场地样本比较两家真实合格场地数量、官网证据、误合并率、花费与人工成本。

**上述均为待执行的测试合同，不代表当前已实现真实付费 E2E。**
