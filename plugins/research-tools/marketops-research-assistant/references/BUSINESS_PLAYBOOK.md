# 业务执行手册：一项研究从需求到交付

这是本插件业务层的统一阅读入口，8个业务Skills按任务展开，不建立第二份SOP。依据和边界见 [MEMBER_A_SKILL_ANALYSIS](MEMBER_A_SKILL_ANALYSIS.md)。运行前先读 [PROVIDER_ROUTING](PROVIDER_ROUTING.md) 与 [DATA_HANDOFF](DATA_HANDOFF.md)。

## 1. 半自动的准确含义

人确定目标、范围、费用和真正有争议的业务裁决；Agent在这份已批准任务内连续完成研究、检查、保存和导出，不应每做一页就要求用户点继续。宿主退出时没有能力继续推理就保存断点，不宣称后台仍在研究。不新建定时器、不主动启动无限下一批。

本轮结束应同时交付 **业务结果包** 与 **复盘经验包**；后者不能替代前者。

## 2. 阶段合同

| 阶段 / Skill | 必需输入 | 工作方法 | 必须保存的产物 | 交接校验 |
|---|---|---|---|---|
| intake | 原始需求、已有workspace、范围/授权 | 原话与结构化解释分开，绑定scene/profile/version、合格定义和停止条件 | Brief及本轮run/store引用 | 未批准项显式pending；不悄悄选国家/扩大费用 |
| source-discovery | Brief、已有资产与来源 | 先复用，再搜索名录/开放数据、分类索引和真实链接；不要从零猜路径 | source hit、查询/语言/区域、覆盖范围 | 检索结果是候选，不是已证实的场地/邮箱 |
| venue-account | source/material | 从场地或组织两条入口；责任拆为owner/operator/maintainer/procurement | 场地/组织候选、责任关系、支持材料 | 一个域可多场地，一场地可多组织；不得按域抹掉实体 |
| qualification | 已核实属性、批准SOP | 场地适配、组织责任与场景资格分开；Jev只辅助语义 | eligible/hold/irrelevant/duplicate及理由/规则版本 | 未知事实不按false；缺SOP不默认给某等级 |
| person-research | 组织、角色策略、既有材料 | 优先相关staff/team/部门页；有依据地补站内来源；保留原职位与时间 | 人或部门候选、RoleAssignment、联系触点/机会 | 名字、岗位、邮箱须能证明关联；旧任职不当现职 |
| route assessment | 字面邮箱、归属、资格、SOP | 双轴：Account状态不决定ContactRoute等级；A/B/C/D逐条按定义 | 正式研究路线或hold/X/opportunity | 无邮箱不变B；部门路线无需捏造人名；采购缺口不自判 |
| evidence（并行） | 每次取得的材料 | 区分原始字节/渲染HTML/派生Markdown；记录source→claim→entity支持关系 | 资料、证据、冲突、时效 | hash只是完整性；摘要不能证明自己；核验原文语义 |
| excel + handoff | 同一持久数据快照 | 生成业务视图和可选审计视图；读回文件、链接、计数 | report、XLSX/CSV、manifest、pending项 | 申请过但未完成对象不消失；CSV不伪称XLSX |
| retrospective | 各阶段operation记录与交付回执 | 归纳卡点、成功方法、比较和假设 | experience.json + REVIEW.md +可选改进提案 | 不自动发布规则；没有证据就标UNTESTED/UNKNOWN |

## 3. 跨阶段决策规则

先查询现有池子，优先补“合格组织缺负责人”“已有人但缺现职证据”“已提取未保存”等缺口。任务可以跳过已完成阶段，不能跳过晋升所需证据。不同对象可并行推进，但本地CSV只允许一个写者，别靠增大并发掩盖坏解析或坏交接。

记录所有输入对象的处置：已提交、未变化、暂存、拒收、待处理。按**同一单位、同一输入唯一键集合**对账；来源URL展开多个场地时，输出数量可以大于输入，不能用减法反推“丢失条数”。只要有未解释对象就记 `UNACCOUNTED_OUTPUT`。

## 4. 复核与更正

只允许读取当前有效规则；历史经验可建议策略，不可覆盖批准规则。一个人名/同域邮箱不能跨组织自动合并；相关性推断、岗位事实、采购权、邮箱可投递性各不相等。

人修正资格或角色标准时，保存反馈来源、scope、规则版本、影响对象集合；先重算可复用判断，只有证据缺失/过期才重新抓取。旧交付出修正版，不删除旧测量。

研究达到范围内的终态（包括有理由的hold/blocked）可交付部分结果；不能用“未全找到邮箱”否定全部成果，也不能把partial写成full-pass。读回成功和业务审查分别列出。

## 5. 后续外联边界

本插件只准备可审查研究结果。发信、私信、表单、回复监控、CRM/飞书外写需要其独立连接器与明确业务授权。No-Go、退订、频率、重复触达与发送凭据不是这份Skill的隐含许可。
