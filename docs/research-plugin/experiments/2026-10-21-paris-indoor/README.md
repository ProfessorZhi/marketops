# 巴黎室内场馆调研：两场 Codex 会话的探索性对照

日期：2026-10-21。时间统一为 Asia/Shanghai。

**本轮普通检索更快交付了少量个人邮箱线索。MarketOps 留存了更大的候选池和审计材料，但未交付完整场馆名录，实际客户表也只有一条邮箱路线。两组都没有证明“巴黎全部室内场馆”或“最高 ROI 买方”。**

这是一组每种条件各一次的事后观察，未随机化、未统一时长和停止条件，也缺少模型、token 与宿主费用数据，不能据此推断插件的因果效果或成本优势。原始会话和联系人不在此公开仓库发布。

## 会话与实验条件

| 条件 | Codex 会话原名 | 研究时间 | 研究回合耗时 |
|---|---|---|---:|
| A：明确禁止 MarketOps | 查找巴黎室内场馆及决策人邮箱 | 21:24:31—21:28:41 | 250,685 ms，约 4 分 11 秒 |
| B：使用 MarketOps 0.5.9-rc3 | 检查插件并评估巴黎场馆调研 | 18:49:25—19:08:10 | 1,125,083 ms，约 18 分 45 秒 |

两场均要求尽力调查巴黎地区室内体育场馆，寻找具体岗位与个人的公开工作邮箱，排除通用入口。两场最终均以 Île-de-France 而非巴黎市区作为实际范围。A 偏向大型商业场馆，B 扩展到八省地图点位与市政场馆，覆盖对象不相同。

B 之前有一个 42,489 ms 的可用性与可行性检查回合，**不计入研究耗时**，中间等待用户开始研究的时间也不计入。B 询问了产品/服务，用户答“暂不确定”；A 没有这次交互。两组都缺少可用于计算买方 ROI 的产品定义。

被评估代码参考：[eba4d3541fdbdd7bfbba00c97fb9c89676641f58](https://github.com/ProfessorZhi/marketops/commit/eba4d3541fdbdd7bfbba00c97fb9c89676641f58)，对应本地 0.5.9-rc3。此目录仅增加实验记录、离线采集和复现脚本，未修改插件行为，也未重跑收费调研。

## 实际交付对账

| 指标 | A：普通检索 | B：MarketOps |
|---|---|---|
| 候选发现 | 最终列出 10 家大型场馆 | 地图返回 146 条，去重导入 143 个点位 |
| 完整室内场馆名录 | 未完成，最终文字明确声明 | 未完成，`coverage_complete=false` |
| 最终文字提及的个人邮箱线索 | 3 个，集中在款待/商业销售岗位 | 1 个，来自 2024 年招聘 PDF 的个人工作邮箱 |
| 可供核对的 XLSX | 未交付；一次区域 CSV 下载命令失败 | 主工作簿及客户视图实际存在 |
| 实际获准导出的客户/组织行 | 不适用 | **1** |
| 实际获准导出的邮箱路线行 | 不适用 | **1**，职能 `UNKNOWN`，评级 `RULES_UNKNOWN` |
| 实际导出的场地行 | 无结构化工作簿 | **0** |
| 实际导出的人员/岗位行 | 无结构化工作簿 | **0** |
| 保存的审计记录 | 未建立可对应的插件证据库 | 833 条原始候选记录，824 条 active 记录 |
| 保存的材料 | 会话引用的网页结果，未提供独立材料索引 | 258 项，另有 529 条来源候选记录 |
| 具名买方适配、邮箱有效性和 ROI | 未核实 | 未核实 |

“143 个点位”“136 个组织候选”“833 条审计记录”“1 条客户表”属于不同对象和不同处理阶段，**不能互换，更不能当作已验证场馆覆盖数**。

B 的 136 个组织目标中，135 个为 HOLD，1 个为 `RESEARCHED_CANDIDATE`。最终池状态为 143 个点位中 142 HOLD、1 done。按 `organization_key = entity_key` 的精确键对账：130 个池键对应组织候选，13 个池键没有对应组织记录，另有 6 个组织键不在池中。它们是待核对的实体关联差异，不能简单解释成“少了 7 家”，也未进行人工同实体归并。

主 XLSX 九张表、同批 CSV、导出 manifest 的行数已独立核对一致；manifest 中 18 个交付文件的大小与 SHA-256 均匹配。公开的 [metrics.json](metrics.json) 和 [source_hashes.json](source_hashes.json) 保留统计和文件指纹，不包含联系人行。

## 观察到的问题

### F1：Serper 的失败可在本地复现，不能据此判断 Key 无效

36 次组合搜索均传入 `country="France"`。Serper 的请求身份规范化要求两位国家码，于服务派发之前抛出 `IDENTITY_VALUE_INVALID: country`；组合结果只保留了泛化的 `IDENTITY_VALUE_INVALID`，会话因而猜测账户身份有问题。

纯函数复现结果：`France` 拒绝，`fr`、`FR` 均规范化为 `FR` 并通过。B 的 Serper 账本行数为 0，与派发前失败相符。这证明有输入契约/错误提示问题，**不证明凭据错误，也不证明本地修正后真实网络请求必定成功**。

离线复现见 [reproduce_country_error.py](reproduce_country_error.py) 与 [country_reproduction.json](country_reproduction.json)。脚本禁止创建 socket，不读取凭据，不调用供应商。

### F2：本机 READY 与真实抓取能力存在落差

B 的预检将五家服务列为 READY，但实际有 95 次 Crawl4AI 本地尝试落入不可用路径。账本对应 95 行 `NO_CHARGE_PROVEN`，不应称作成功抓取或收费调用。系统仍在后续目标上尝试该路径，再转向 TinyFish。

应将 SDK 存在、受管浏览器可启动、受管页面可抓取分开验证，并在同一运行环境内缓存持续的本地能力不可用状态。尚未确定此环境的具体不可用原因，不建议据此重装浏览器或修改 Key。

### F3：唯一邮箱路线与“具名岗位”交付不一致

准入检查拒绝了 1 条 person 和 5 条 active role，仍允许 1 条 contact_point 进入客户视图。最终人员/岗位表为空，邮箱路线的联系人显示为部门称谓，职能为 `UNKNOWN`。这不能作为“姓名—岗位—组织—邮箱链条完整”的结构化交付。

两个记录还出现 `ORGANIZATION_PROVENANCE_MISMATCH`。需要核实品牌、法律主体、岗位和邮箱的证据关系，并检查具名联系人 brief 在最终交付是否一致执行。不能为提高数量而降低来源门槛。该邮箱来源为招聘用途且有年代差异，其商务适配及当前有效性仍待验证。

### F4：文件成功导出与任务成功需要分别呈现

handoff 的 `delivery_completeness="COMPLETE"` 表示文件和相关输出成功生成。同时，manifest 明确给出：

```json
{
  "goal_status": "STOPPED_WITH_GAPS",
  "goal_satisfied": false,
  "evidence_ready_for_business_use": false,
  "business_verification": "NOT_PERFORMED",
  "evidence_envelope_export_gate": "BLOCKED_LEDGER_NOT_WIRED"
}
```

126 个候选缺少所需身份/来源/摘录材料。公开报告应先显示任务未满足，再显示文件交付成功；还需排查证据信封与账本的导出关联。`BLOCKED_LEDGER_NOT_WIRED` 是导出证明链的状态，不能据此说整个运行没有用量账本。

### F5：错误、查询和费用计量口径需改进

B 会话中 8 次调用没有结构化结果，其中部分是输入校验或池操作失败，不能统一归为上游服务故障。显式 Jev `primitive="score"` 调用只显示通用工具错误；另有 11 行 Jev 账本操作标记执行 OK。因此不能把那次工具错误扩展成“Jev 的 11 次请求全部失败”。

A 自报 9 次搜索；会话实际记录 17 个 web 动作，其中 13 个 search 动作、45 条查询，另有 1 个 openPage 和 3 个 other。动作数、批次和查询数需要分开。

B 研究回合有 163 次宿主 MCP 调用，连同先前检查共 165 次。底层账本另有 250 行操作，和宿主工具调用不是同一指标。脱敏的 [tool_trace.json](tool_trace.json) 将粘贴记录与直接读取的会话调用逐一匹配，保留行号、工具、状态和时长，不保留查询、参数、联系人或响应正文。

## 消耗与成本的边界

| B 账本操作 | 数量 | 执行/计费状态 |
|---|---:|---|
| Crawl4AI render | 95 | 本地失败路径，`NO_CHARGE_PROVEN` |
| SerpApi search | 8 | 执行 OK，金额 `UNKNOWN` |
| TinyFish search | 39 | 执行 OK，金额 `UNKNOWN` |
| TinyFish fetch | 97 | 执行 OK，金额 `UNKNOWN` |
| Jev judge | 11 | 执行 OK，金额 `UNKNOWN` |

本 Run 有 155 行 `UNKNOWN` 金额操作。会话余额视图的 TinyFish 137 笔包含此前连通性测试的 1 笔，本 Run 为 136 笔，不能混用这两个分母。

handoff 的 **$0.0014 是费率卡估算**，主要来自 Jev 的请求近似，既不是供应商结算账单，也不包含 Codex 模型/token 成本。A 的实际成本未知，两组的宿主模型成本均未知，不能计算完整成本或成本优势。B 最后记录的配置余量仍为 41 次，原项目上限与余额也不代表本实验实际支出。

## 结论与下一轮

本次只支持以下观察：A 更快给出了少量可继续核验的商业联系人线索；B 的候选留存、拒收、审计与可恢复性更强，但本轮技术问题和结构化交付缺口明显，未转化为更多满足原要求的结果。无法给出“插件提高了多少覆盖率、节省了多少钱”的结论。

先修复或查明 F1—F4，再按 [NEXT_TRIAL.md](NEXT_TRIAL.md) 的等条件方案复测。优先评估已证明室内属性的场馆和完整具名岗位邮箱链条，避免把更多候选行当作业务效果。

## 证据与重现

- [metrics.json](metrics.json)：时间、分层数量、费用状态与边界。
- [tool_trace.json](tool_trace.json)：165 条调用的脱敏状态和时长。
- [source_hashes.json](source_hashes.json)：两份原始粘贴会话和本地交付文件的指纹。
- [collect_metrics.py](collect_metrics.py)：从私有输入重新生成统计，只读打开 SQLite 和 XLSX；不调用插件或供应商。
- [country_reproduction.json](country_reproduction.json)：本地国家码校验复现。
- [validation.json](validation.json)：行数、18 个文件哈希、165 条会话调用匹配及公开内容检查的结果。

本次直接读取了两个完整会话页面（无更早分页），并以用户提供的完整粘贴记录补齐 MCP 响应。会话工具返回的单条输出可能截断，故不把该接口当作完整响应唯一来源。源文件、完整反馈 ZIP、SQLite、CSV 联系人行、XLSX 与任何凭据均留在本地。此记录未重新验证网页时效性或邮箱可投递性。

采集示例，参数为本机私有路径占位符：

```text
python collect_metrics.py --case-a THREAD_A_JSON --case-b THREAD_B_JSON --transcript-a TRANSCRIPT_A --transcript-b TRANSCRIPT_B --export-dir B_EXPORT_DIRECTORY --state-dir LOCAL_STATE_DIRECTORY --feedback-zip B_FEEDBACK_ZIP --source-revision eba4d3541fdbdd7bfbba00c97fb9c89676641f58 --output-dir NEW_LOCAL_OUTPUT_DIRECTORY
python reproduce_country_error.py --scripts INSTALLED_PLUGIN_SCRIPTS_DIRECTORY
```

`THREAD_*_JSON` 使用 Codex `read_thread` 的 `thread/turns/items` 结构，只需用户消息、最终消息、命令元数据、web 动作和 MCP 调用元数据；无需推理内容。输出目录应为新目录，重现结果先在本地比较，再决定是否更新此记录。

## 后续复测

[2026-10-22：0.5.11-rc1 巴黎长调研提前停止复现](../2026-10-22-paris-continuation/README.md)记录了新版金额预算生效后的一次旧 Run 续接。停止时仍有 70 个待处理目标和约 $1.93 授权预算；这是持续执行失败的复测记录，不是与本轮同条件的新 A/B 对照，也不改写本轮历史结论。
