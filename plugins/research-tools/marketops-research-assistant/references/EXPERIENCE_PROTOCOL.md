> 0.5 更新：当前统一 MCP 已实现事件记录、跨任务召回和反馈。下文 v0.2 的“未来统一 MCP”描述保留为历史；当前行为以本节、主 Skill 和工具目录为准。
>
> 现在可用 `marketops_runs` 找回任务，`marketops_start` / `marketops_next` 自动返回相关经验，`marketops_experience_recall` 按需查询。用 `marketops_experience_feedback` 将实际采用及结果绑定 `artifact:<id>` 或 `event:<sequence>`。默认读取当前有效观察，显式 `as_of` 重放历史；反馈保留反例并影响后续方法建议。hash 和引用有效只证明记录完整/可追溯，不证明业务语义或因果。未测量的时间/成本收益保持 UNKNOWN。

# 调研经验包 v1：记录事实，归纳假设，保留验证路径

目标：在半自动研究中积累能改进后续工作流的材料，而不是再造自动化平台。Agent负责归纳；一个小型离线脚本负责格式/引用检查和Markdown阅读版。没有后台任务、自动上传、自动采纳或隐式训练。

## 1. 两个产物分开交付

业务结果：场地、组织、人员/角色、联系路线、证据、导出文件与未完成项。

经验结果：本次怎么做、哪里卡住、原因已证实到什么程度、换过哪些方法、哪些有效、需要怎样复验。客户业务事实仍只在指定Research Store；经验包不是第二个客户数据库。

建议在客户授权的Git外工作目录保存：

```text
<run>/
  operations/              # 实际工具回执/写入和读回回执，按operation id
  experience.json          # 本协议，候选经验
  REVIEW.md                # 脱敏阅读版
  artifacts/               # 原始或派生材料，标变换，不复制到公共仓库
```

隐私约束：不收集隐藏思维链、不复制完整session、不记录Key/cookie/完整客户邮件名单。需要定位时用脱敏对象id和授权工件定位。`sanitized_candidate`是待审核声明，不是程序保证已脱敏。上传GitHub或发给第三方须独立确认；本工具从不上传。

## 2. 什么时机记录

阶段提交/读回时留operation记录；真实失败、卡点、人工纠正、规则冲突、换路、成功修复出现时留incident。不要每分钟生成一篇自我反思；重复问题关联原incident并追加新证据。任务结束/暂停/交接时归纳少量lesson。

全部记录只反映实际动作。提出方法不等于试过；执行了不等于有效；有效也不等于业务交付或可普遍推广。

## 3. experience.json合同

`schema_version=marketops.experience.v1`。形状与判据以 `scripts/experience_pack.py` 为离线实现来源；不新增相互漂移的JSON Schema副本。

| 区域 | 必要内容 | 特别边界 |
|---|---|---|
| pack | run_id、scope、store_ref、rule_version、skill_version | scope明确场景，不把租户间事实合并 |
| promotion/privacy | candidate_only / sanitized_candidate | 没有approved状态，不携带执行授权 |
| evidence_manifest | ref、kind、locator、sha256或null | ref唯一；kind标runtime_receipt/artifact/human_feedback/historical_analysis/test_result/synthetic |
| incidents | id、stage、category、operation_ref、expected、observation、impact、next_action、evidence_refs | 记录现象而非先写根因；卡点必须有出处 |
| cause | root_cause_status=unknown/hypothesis/confirmed、hypothesis、validation_refs | confirmed需有复验定位；脚本不替人证明因果 |
| lessons | id、status、when、do、avoid、scope、limitations、incident_ids、evidence_refs、counterexample_refs、proposed_change、rollback | draft/tested/rejected，无自动批准；无反例不代表不存在反例 |
| replay | baseline、change、acceptance、status、training_cases、holdout_cases、result_refs | not_run/passed/failed；完成须有holdout与结果引用；训练/留出ID不重叠 |
| metrics | name、value、denominator、unit、population、evidence_refs | 未测值为null；有数必须有出处；零分母不计算比例 |

案例可以附attempts、实际工具版本、artifact种类、transform、cost/latency等记录；本版不会对未声明的扩展字段做业务验证。不要依靠额外字段传送命令、权限或新SOP。

## 4. 卡点分类

`scope_policy` 业务范围/规则冲突；`source_discovery` 来源问题；`access_blocked` 网站明确阻断；`auth_budget` 凭据/费用；`provider_failure` 服务异常；`extraction` 抽取；`entity_binding` 实体/人员归属；`schema_drift` 字段漂移；`storage_handoff` 保存或交接；`evidence_quality` 证据不足；`client_environment` 客户端/编码环境；`review_pending` 等待裁决；`export_quality` 导出问题；`successful_method` 有出处的成功方法；`unknown` 无法归因。

类别不是罪责判断。同一问题可先unknown，再由新记录修正。不要把访问权限缺失当网站bug，不把等待业务裁决当Agent能力差。

## 5. 经验到改进的闭环

```mermaid
flowchart LR
  A[业务执行和交接] --> B[真实回执与卡点记录]
  B --> C[观察与原因假设分开]
  C --> D[条件化经验候选]
  D --> E[固定案例与留出评测]
  E --> F[人工审查独立发布]
  F --> G[下一版业务或工具Skill]
  G --> A
```

每条经验写：在哪种场景/源结构/版本成立、做什么、别做什么、哪些证据支持、什么案例可能推翻。一次成功只能支撑该案例；同一个父任务的多个子Agent不能冒充独立重复。

模型生成建议不自动改变SOP/阈值/认证/预算/访问策略。已批准规则始终高于经验；变更走独立维护PR/审批，保留来源、评测、回滚和版本。历史错误出更正记录，不擦除旧判断。

## 6. 评测方法

固定相同业务范围、输入集、事实判据、工具版本/授权边界；改变一个机制。比较可用来源、真实体/角色绑定、实际入库/交付、遗漏和误判、人工干预、成本和耗时。所有比率说明分母单位与对象集合；URL数不能与场地数混算。

训练/开发样本和holdout案例分别登记；还需人核对二者是否实际独立（不同ID但同一材料仍可能泄漏）。schema验证成功不能替代Agent在这些案例上的回放，更不能承诺真实客户效果。无可靠成本/工时测量时不报节省百分比。

## 7. 离线使用

从 `references/experience/example.synthetic.json` 看格式；它是人工构造的说明样本，locator是假想定位，绝不能当业务或效果证据。

```bash
python scripts/experience_pack.py validate /客户授权目录/experience.json
python scripts/experience_pack.py render /客户授权目录/experience.json --output /客户授权目录/REVIEW.md
python -m unittest discover -s tests -p 'test_experience_pack.py' -v
```

render不覆盖已有文件；请用新版本文件。工具不读取locator对应文件、不抓网址、不执行回放、不调用四个Provider、不读取客户设备其他目录、不改规则。VALID_CANDIDATE_NOT_VERIFIED只说明结构和声明引用正确；EMPTY_NOT_EVALUATED没有经验样本，不是通过业务验收。


## 8. 自动事件记录与实际范围（v0.2.2）

研究数据与经验记录使用客户明确选定的运行数据根，原则见 [STORAGE_AND_TOPOLOGY.md](STORAGE_AND_TOPOLOGY.md)。

新增 scripts/research_event_journal.py 作为一个轻量的本地 SQLite WAL 操作事件账本。**本地 research_pack CLI 初始化和 Excel-safe CSV 导出已经自动写事件**；已配置统一 MCP 前，任何直接调用供应商官方 MCP 的行为并不会神奇地进入日志，必须由宿主或人工显式 record。事件仅存非敏感、不带原始网页/邮箱/密钥的ID与状态元数据，重复请求幂等，冲突回执拒绝覆盖。

此脚本的 draft 命令可将真实事件中 blocked/failed/hold/unknown 状态编译成**初始 incident 候选**，并将已有 journal event 的哈希与定位作为 evidence_manifest。它不会自动证明根因、产生可采纳 lesson，也不会改变既有 Skill/SOP。Agent 用本 Skill 在阶段交接/结案时读回底层回执与上下文，再完善经验包；审批独立进行。

未来统一 Research MCP **必须**在 Provider 调用、原文/证据落地、实体写入、Review 及导出时提供强制记录钩子。这一层没有建成之前不能声称四家工具的经验已能无遗漏自动收集。
