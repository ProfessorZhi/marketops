# 试用验收清单

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

- [ ] 8 个 Skills 元数据被真实 Codex 读取；`.mcp.json` 空表不产生自动网络/费用。
- [ ] WorkBuddy 4.24.0+ 可以导入生成的 `skill-only` ZIP，8 个 Skills 可见；不需要四家供应商 Key 才能载入业务 Skill。
- [ ] DeepSeek Harness：基于官方 filesystem Skills 路线验证技能加载，原生 Cordis Plugin **仍为未发布**。
- [ ] 真实 3–5 家调查产生正确 SOURCE→ENTITY→EVIDENCE→CONTACT 引用，不用搜索摘要/挑战页充当证据。
- [ ] 故意损坏一条外键/证据 SHA/邮箱字面来源/人员归属，`research_pack.py validate` 必须失败。
- [ ] Excel 安全导出故意填入以 `=HYPERLINK(...)` 开头的来源文本，导出不能被 Excel 当作公式执行。
- [ ] 人工审核一条 `ready` 联系路线后仍不得自动发送邮件、提交表单或提高预算。

**静态 JSON/Frontmatter/Markdown 校验 ≠ Python 单元测试 PASS ≠ 客户机安装 PASS ≠ Provider 真实验收 PASS。** 未实际跑过的层级均维持 `NOT_TESTED`。
