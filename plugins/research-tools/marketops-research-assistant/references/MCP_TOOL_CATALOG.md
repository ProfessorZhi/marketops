# MarketOps Research MCP 0.5 工具目录

安装器启动 `scripts/research_mcp.py`，同进程 facade 提供兼容 schema。宿主连接一个 `marketops-research` 服务，实际工具以 `tools/list` 为准。

| 工作 | 工具 | 行为 |
|---|---|---|
| 路径设置 | `marketops_setup_status`、`marketops_setup_configure` | 读取配置；保存用户选定的数据根和经验文件 |
| 找回任务 | `marketops_runs` | 只读、有限分页，返回 ID、范围、规则与目标 |
| 开始/继续 | `marketops_start`、`marketops_status`、`marketops_next` | 保存 Brief、读取候选、给出下一阶段和适用经验 |
| 当前能力 | `marketops_capabilities` | 与调用相同的本地准入、依赖和累计请求台账；身份/余额未知不假绿 |
| 有界降级 | `marketops_research_step` | 在已准入渠道中有限尝试，保留每次原因、结果和回执 |
| 单次调用 | `marketops_managed_call` | 统一 Registry/Policy/Ledger/Adapter，返回业务数据和持久资料引用 |
| 搜索 | `marketops_search`、`marketops_serpapi_mcp_search` | Serper REST / SerpApi 内嵌官方 MCP，兼容名称使用同一调用链 |
| 抓取 | `marketops_crawl`、`marketops_tinyfish` | 本地 Crawl4AI / TinyFish 内嵌 MCP；保留来源种类、时间和 hash |
| 语义判断 | `marketops_jev_choice` | Choice/Noul/Score 绑定已保存材料；结果不是事实审批 |
| 保存候选 | `marketops_propose`、`marketops_propose_batch` | 幂等、原子写入；关键字段有 `claims[{field,artifact_id,quote}]` |
| 读证据 | `marketops_material_excerpt`、`marketops_evidence_audit` | 有界原文摘录和缺口检查；文本匹配不证明语义 |
| 看调用 | `marketops_events` | 本 Run 受管事件、实际路径和失败 |
| 经验记录 | `marketops_note`、`marketops_experience_draft` | 真实卡点、方法与纠正，附证据引用 |
| 经验复用 | `marketops_experience_recall` | 范围、规则、阶段与供应商过滤；默认当前时刻，可历史回放 |
| 经验反馈 | `marketops_experience_feedback`、`marketops_experience_summary` | 区分采用和有效性，绑定真实材料/事件，保留反例和未知收益 |
| 交付 | `marketops_export`、`marketops_export_xlsx`、`marketops_evidence_workbook`、`marketops_handoff` | CSV、九表研究 XLSX 或五表证据视图，读取核对数量与证据 |

`marketops_tinyfish_agent` 和 `marketops_tinyfish_agent_wait` 保留兼容名称，但本次研究产品不启用付费浏览器 Agent。旧 Cloud/直连 REST 分支不作隐式后备。

四 Key 在本地 `research_credentials.py` 配置；联网范围、有限额度与官方 MCP schema 检查由 `research_runtime_setup.py` 完成。MCP 工具不能自签这些授权或清空预算。

续跑/同类新批保留同一数据根。请求次数与美元账单分别计量；空任务不能产生成功交付，无证据、过期、冲突和未覆盖对象保持可见。
