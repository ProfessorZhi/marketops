# Member-A 等研究用户：三项半自动实测

> 使用真实用户提供的范围和已经配置好的工具；下面出现的 Devon 只是可替换示例，不会自动触发付费调用。所有成果由人复核；外联不在本试用范围。

## Trial A — 来源到场地母池

提示词：

> 使用 MarketOps Research Assistant，先根据我提供的英国 Devon 天然草运动场研究 Brief，列出最多 5 个公开候选场地。搜索官网/地图/协会名录，但不要把搜索摘要当事实；尽可能核实场地本身、地理位置和负责组织。把所有来源记录到 sources/evidence，更新 venue_candidates、venues、organizations、venue_org_links。对无法证明运营关系的条目留 HOLD。不访问登录页面、不扩大地区、不使用付费浏览器 Agent，除非我另行授权。

验收：一个目录站多个场地不塌为一个；`venues.verified` 每行有真实网页证据；找不到运营者不捏造公司；人工审查可从原文重现判断。

## Trial B — 组织到候选人、联系方式

提示词：

> 使用我确认的 3 个组织，调查公开的 Grounds Manager / Head Greenkeeper / Facilities / Purchasing 等岗位及有字面来源的工作联系渠道。不要猜邮箱格式。人员、职位和联系渠道分别入 people、contact_points；缺少真实邮箱只形成待补研究项。把可以交给人工审核的路线列入 contact_routes，但在我复核前不标 ready。

验收：同名不同公司的人不合并；旧 PDF 的任职时效明确标记；邮箱字面确实出现在可信网页；部门通用邮箱不会误绑定到指定个人。

## Trial C — Excel 整理和交付

提示词：

> 使用 marketops-excel 和 marketops-evidence Skills，检查当前 research pack 的字段完整性、重复实体、错误引用和公式注入风险。按照 WORKBOOK_SPEC 的顺序创建 Excel（或者在当前工具没有 XLSX 能力时输出 Excel-safe CSV），标出哪些是候选、已核实、待补证据和正式 ready 路线。不要删掉 HOLD/UNKNOWN 行，也不要把行数写成整个市场的覆盖率。

验收：手工打开文件后列标题不乱码、ID 不丢前导零、来源 URL 与 evidence_id 可追、最终表可筛选排序，`Summary` 与明细的真实行数一致。若仅 CSV，请明确说不是 .xlsx。

## 本地离线快启（零 API 调用）

在 `marketops-research-assistant/` 根下：

```bash
python scripts/research_pack.py init ./devon-sample --request-id devon-001 --country GB --scene gb-natural-grass --objective "Public sports ground research"
python scripts/research_pack.py summary ./devon-sample
python scripts/research_pack.py validate ./devon-sample
python scripts/export_excel_safe_csv.py ./devon-sample --output ./devon-excel-review
```

空表只是 `STRUCTURALLY_VALID_EMPTY`，证明模板/字段能读，不是研究完成。请不要把测试数据或真实客户邮箱提交到 Git。

## Member-A 的价值衡量

每批记录：有效场地数、核实组织数、实际有公开职位的候选人数、可验证邮箱数、HOLD/UNKNOWN 数、人工纠错次数、被阻断源、支付的各 Provider 费用、手动来回搬运次数，以及能从来源复核的比例。**不是**仅衡量抓取 URL 数或对话回合数。