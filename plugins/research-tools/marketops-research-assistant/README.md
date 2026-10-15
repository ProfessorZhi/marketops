# MarketOps Research Assistant — 半自动调研与经验积累

> **v0.2.2 数据记录与拓扑试用候选**。MarketOps的两个交付物仍是半自动Research MCP/Plugin和后续全自动工作流。本切片先完善Member-A业务方法、四Provider使用方法与复盘经验；**统一MCP Server/业务Research Store、四家真实连通和客户机验收尚未完成**。没有部署东京CC，没有付费调用，没有外联。

> **搜索 Provider 名称校正：** 当前 MarketOps 后端接的是 **Serper.dev**；用户提供的 `https://serpapi.com/manage-api-key` 是另一家 **SerpApi.com**。后者已有[官方开源 MCP](../providers/serpapi.md)，但**尚未接入本插件的统一 Gateway**。两者 Key 不互通，不能因为名字相近就默认用户持有的是 Serper 账号。

## 三层能力，一条业务主线

```text
业务层：需求→来源→场地/组织母池→资格→人/部门与岗位→公开联系触点→正式研究路线→Excel/报告
工具层：按缺口选择 Serper / Crawl4AI / TinyFish / Jev，不强制四家串行
经验层：真实回执→卡点/成功方法→观察与假设→经验候选→回放→人工采纳→下一版Skill
证据层贯穿业务全程；两种结果分别交付：业务结果包、复盘经验包。
```

半自动不是让人搬运每一张表。用户给出边界后，Agent在本次Run内连续推进，只有确需授权/裁决或硬阻塞才停；不自动扩下一轮、不假称宿主关闭后仍执行。

## 先读这几份

- [Member-A分析与16条业务规则映射](references/MEMBER_A_SKILL_ANALYSIS.md)：固定分析revision、依据、不推广的历史阈值和规则缺口。
- [业务执行手册](references/BUSINESS_PLAYBOOK.md)：每阶段输入、方法、产物、交接校验。
- [四Provider路由与接口边界](references/PROVIDER_ROUTING.md)：实际MCP/API/SDK发现、成本、失败分类、合法换路。
- [统一存取与交接合同](references/DATA_HANDOFF.md)：一个store、一次提交/读回、同单位对账；不让Skill各自造数据库。
- [运行数据目录和完整拓扑](references/STORAGE_AND_TOPOLOGY.md)：当前与目标、Windows/本地/服务器的数据根、MCP Client/Server角色、供应商开源与自托管范围。
- [经验包协议](references/EXPERIENCE_PROTOCOL.md)：卡点记录、条件化经验、留出回放、独立采纳。
- [外部方法参考](references/EXTERNAL_METHOD_NOTES.md)：SciSpace发现、作者摘要复核；不是Member-A需求依据。

## 14个Skills，按需加载

| 层 | Skills |
|---|---|
| 业务8项 | marketops-research、marketops-intake、marketops-source-discovery、marketops-venue-account、marketops-person-research、marketops-evidence、marketops-excel、marketops-handoff |
| 工具5项 | marketops-provider-router、marketops-serper、marketops-crawl4ai、marketops-tinyfish、marketops-jev |
| 复盘1项 | marketops-research-retrospective |

统一入口是 `skills/marketops-research/SKILL.md`。其余按研究阶段和实际工具展开，避免所有文档挤满上下文。厂商Skill只作接口方法参考，不覆盖MarketOps业务优先级或批准规则。

## 实际可交付与未实现

已有：Skills、源码/注册/安装指南、v0.2.0离线CSV交接包、结构校验、Excel-safe CSV、WorkBuddy skill-only候选打包；203行标准库经验校验/阅读版脚本与12个离线测试。v0.2.2新增本地SQLite事件日志、操作幂等与经验草稿导出；插件CLI的建包、导出动作现在自动落操作事件，尚未接管厂商直接MCP调用。

未有：本包内可直接调用四家的统一MCP运行时；自动入库的统一Research Store；可信审批服务；完整实机OAuth/Provider/Agent端到端验收。`.mcp.json`保持空，不能把装入本包等同于工具连通。现有已配置供应商MCP/SDK可按工具Skills使用，费用边界由实际环境负责。

CSV仍为单写者交接格式，不是生产唯一真源。客户已连接受管MarketOps存储时优先用它；不能同时双写两个store。未来统一服务复用主项目领域模型，本文不新建另一套数据库。

## 离线使用

在本目录，输出指定到Git以外的客户目录：

```bash
python scripts/research_pack.py init ~/marketops-research-data/my-research --request-id demo-001 --country GB --objective "Find public sports venues"
python scripts/research_pack.py validate ~/marketops-research-data/my-research
python scripts/research_pack.py summary ~/marketops-research-data/my-research
python scripts/research_event_journal.py --data-root ~/marketops-research-data list --run-id demo-001
python scripts/experience_pack.py validate references/experience/example.synthetic.json
python scripts/experience_pack.py render /客户目录/experience.json --output /客户目录/REVIEW.md
python scripts/build_client_bundles.py --output ~/marketops-research-bundles
python -m unittest discover -s tests -p 'test_experience_pack.py' -v
```

示例是合成结构样本，不包含真实资料或已跑回放。结构正确不等于证据/根因/隐私审核通过。render拒绝覆盖已有文件；经验只产candidate，不自动改Skills/SOP，也不上传日志。

## 客户端与既有资料

[CLIENT_SETUP](references/CLIENT_SETUP.md) 仍是客户端候选说明。Codex加载manifest/Skills；WorkBuddy生成skill-only ZIP，供应商MCP另行按需连接；DSH仍待实际宿主验证。新包含14项Skills，旧v0.2安装说明中的8项是当时版本，不是新包计数。

已有 [FLOW_AND_SCHEMA](references/FLOW_AND_SCHEMA.md)、[QUALITY_GATES](references/QUALITY_GATES.md)、[WORKBOOK_SPEC](references/WORKBOOK_SPEC.md)、[MEMBER_A_PILOT](references/MEMBER_A_PILOT.md)、[ROADMAP](references/ROADMAP.md) 保留；离线数据格式版本仍为0.2.0。供应商独立指南在 [上级工具目录](../README.md)。

本地日志的自动保存只覆盖已接入记录钩子的操作；四家供应商的直接调用未自动捕获，真正全链路事件记录必须由未来MarketOps Research MCP事务保证。不自动发送、登录绕过、充值或启用持久监控。正式联系人只是研究路线，不产生外联许可。业务标准缺口（例如当前英国profile的具名采购角色分级）必须保留并交业务所有者裁决。
