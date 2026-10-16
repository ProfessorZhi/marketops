# Excel 交付规范：Research Pack -> Workbook

## 工作簿目标

Excel 是**审查/展示层**，并不是第二个“客户事实数据库”。数据从受限的本地 research pack 或 MarketOps 正式 export 来；严禁在 Excel 里用公式捏造人员邮箱、资格或已发送状态。

推荐工作表顺序：

1. `Instructions`：目标地区/行业、Brief 摘要、状态释义、来源时间、文件生成时间、人工审查备注；
2. `Summary`：来源条数、候选场地、核实场地、组织、合格组织、候选人、可用证据、正式邮件路线、Hold 数；只统计本文件实际行数，不宣称市场完整覆盖；
3. `SourceHits`
4. `Evidence`
5. `VenueCandidates`
6. `Venues`
7. `Organizations`
8. `VenueOrgLinks`
9. `AccountAssessments`
10. `People`
11. `ContactPoints`
12. `ContactRoutes`
13. `ReviewQueue`

各数据表**第一行必须与脚本字段名逐字一致**。导出时可设列宽、自动筛选、冻结标题、样式、链接，但不能改变原始 ID 及 JOIN 语义。

## 整理规则

- 保持一个对象一行；重复 `source_id` 可以链接多个对象，重复人名不能自动合并不同机构。
- `scene` 是哪个业务市场的资格判定，不能简化为全局好/坏。
- `evidence_id` 是每条关键事实的指针，证据表必须可见 `url`、`observed_at`、`content_sha256` 与简短原文字面 `excerpt`。
- `contact_routes.status=ready` 不自动生成发信批次。`X`、`hold` 和 `unknown` 不能被 Excel 默认公式算成已审核联系路线。
- 所有 ID/邮编/电话/registration number 列使用**文本格式**，不要让 Excel 去掉前导 0 或更改科学计数法。
- 只有真正可复算的统计才写 Excel 公式；在 `Summary` 边标注计算来源、时间和不包括什么。
- 对来源中的任意文本使用文本单元格；不应被 Excel 解析为公式的前缀包括 `=`、`+`、`-`、`@`（含前置空白），尤其是外部 HTML/搜索摘要中的文本。

## 数据质量和视觉要求

- 先调用 `python scripts/research_pack.py validate <pack>`；阻塞错误未处理时，明确标注 `DRAFT / NOT_VERIFIED`，不是给表上绿勾。
- 标题冻结、筛选、列宽 12–32（证据/长 URL 按需更宽）、必要时 wrap text；正文不要盖住链接。
- 状态采用一致颜色或标签，不靠颜色作为唯一信息。
- 不要合并明细行、不要隐藏必须核实的证据与状态列。
- 输出前检查计算错误（#REF!、#VALUE!、#DIV/0! 等）、重新打开检查字段与表数；导出后按真实数据确认行数。
- 文件名包含研究 request_id + 版本；同时保留源 CSV pack 作为可复核输入。避免两人覆盖写同一个输出文件。

## 可选通用 Excel 工具

Codex 如需成熟通用工作簿能力，可按其当前官方文档安装 `openai/skills` 的 `spreadsheet` Skill：

```bash
npx skills add https://github.com/openai/skills --skill spreadsheet
```

这是**通用工作簿制作 Skill**；它无法自动满足本业务的真实性门禁，需要与 `marketops-excel` 联合执行。WorkBuddy/DSH 可使用宿主已有 Excel 工具或同类能力；需要在真客户端测试，不能声称该 Codex 命令也在所有宿主受支持。

## Current executable path vs formal target

The source plugin's actual `marketops_export_xlsx` produces **eight** candidate-stage sheets: 客户总表, 联系人路线, 排除与待确认, 证据索引, 本轮说明, 场地与资产, 来源与查询, 候选数据审计. These are a readable **Lite projection** of the staging store; no row claims that an email route is formally A/B/C/D.

The thirteen-sheet description earlier in this document is a **target detailed audit workbook** for canonical formal research; it is not the current automatically generated file. Avoid treating the two as conflicting contractual outputs. For formal production handoff, reuse canonical five-sheet `marketops/application/export.py` schemas and authorized grade rules, not a second set of judgments in the plugin.
