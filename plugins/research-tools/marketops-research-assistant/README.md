# MarketOps Research Assistant — 半自动市场调研插件

> **v0.2.0 试用候选**。面向 Member-A 等已使用 Codex、WorkBuddy 或 DeepSeek Harness 的研究人员。**不是**后台自动化服务、不是四家 API 的统一付费代理，也不要求东京 CC 或 MarketOps 生产服务。当前版本只承诺可阅读的 Skills、离线数据交接格式、校验脚本与客户端打包候选；需逐客户端验收。

## 从一句自然语言到可审核的数据

```text
研究需求 / ResearchBrief
  -> 来源候选池 (sources.csv: Search URLs / Maps / public datasets)
  -> 场地候选池 (venue_candidates.csv)
  -> 场地母池 (venues.csv) + 组织池 (organizations.csv)
  -> 场地-组织责任关系 (venue_org_links.csv)
  -> 场景资格判断 (account_assessments.csv)
  -> 人员/部门候选池 (people.csv)
  -> 联系渠道候选 (contact_points.csv)
  -> 正式联系路线 (contact_routes.csv: A/B/C/D + 人工复核)
  -> Excel / CSV / 人工检查 / 可选后续受控导入

                Evidence Store (evidence.csv)
           支撑从场地、组织到任职、邮箱的每次晋升
                Review Queue (review_queue.csv)
           缺证据/矛盾/未知的信息始终可见
```

证据不是最后才生成的一张表，它与整个实体流并行。**正式联系路线不代表发信许可**；本插件没有发送、表单提交、自动充值、登录绕过或无人值守下一批功能。

## 安装入口

- **Codex**：本目录是可移植 Skills / Codex plugin 候选；见 [安装](references/CLIENT_SETUP.md) 和 `.codex-plugin/plugin.json`。为了不偷偷调用收费 API，内置 `.mcp.json` 只有空 `mcpServers`；需用户自愿配置外部 Provider。
- **WorkBuddy**：运行 `python scripts/build_client_bundles.py --output /你的独立输出目录` 得到一个 **skill-only** 连接器 ZIP；供应商官方 MCP 分别按 [Provider 指南](../providers/) 选择安装，不能把四个 MCP 塞到一个 WorkBuddy 连接器。
- **DeepSeek Harness**：现阶段提供文件系统 Skills 入口与 [兼容说明](references/CLIENT_SETUP.md)，未发布或验收 Cordis 原生插件。
- **Excel**：`skills/marketops-excel/` 对研究表和工作簿做业务检查；可配合用户已有的表格工具或可选官方 Spreadsheet Skill 使用。

## Skills

| Skill | 业务动作 |
|---|---|
| `marketops-research` | 自然语言总入口，逐步路由 |
| `marketops-intake` | 固定 Brief、范围和准入/停止条件 |
| `marketops-source-discovery` | 来源发现、URL 去重、零结果解释 |
| `marketops-venue-account` | 场地与运营/采购组织关系 |
| `marketops-person-research` | 候选人/部门、岗位和联系渠道 |
| `marketops-evidence` | 来源、事实归属、矛盾、人工复核 |
| `marketops-excel` | Excel 数据整理、去重、质量门与导出 |
| `marketops-handoff` | 分阶段存档、接班、人工交付 |

## 离线研究包

```bash
python scripts/research_pack.py init ./my-research --request-id demo-001 --country GB --objective "Find public sports venues in Devon"
python scripts/research_pack.py validate ./my-research
python scripts/research_pack.py summary ./my-research
python scripts/build_client_bundles.py --output ./local-build
python -m unittest discover -s tests -v
```

命令只创建/检查**用户指定的本地目录**，不访问外网、不消耗 API Key、不运行 CC。数据是**便携交接工件**，不是 MarketOps 真正的数据库唯一真源；后续如接入 MarketOps 服务，必须通过有鉴权、有场景、有证据的导入 API。

详细数据规则见 [FLOW_AND_SCHEMA](references/FLOW_AND_SCHEMA.md)、[QUALITY_GATES](references/QUALITY_GATES.md)、[CLIENT_SETUP](references/CLIENT_SETUP.md)、[WORKBOOK_SPEC](references/WORKBOOK_SPEC.md)。Member-A 可按 [三项试用场景](references/MEMBER_A_PILOT.md) 开始；[ROADMAP](references/ROADMAP.md) 区分半自动与未来全自动。服务申请及官方源码见上级 [四家 Provider 指南](../README.md)。

## 试用范围与真实性

目前阶段是 **本地人工辅助**：用户选择目标、启动查询、核验证据、批准导出。搜索结果不等于验证结果；任务报告不能把 `pending` 说成 `completed`。即使导出完成，也不表示市场已被全面覆盖。