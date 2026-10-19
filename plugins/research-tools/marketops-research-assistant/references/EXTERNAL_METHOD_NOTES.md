# 可选外部方法参考（不是Member-A业务证据）

2026-10-15，经SciSpace语义检索发现，再核对下列arXiv作者摘要页。只读了摘要/元数据，没有声称做过全文复现、模型训练或效果验证。业务依据仍是MEMBER_A_SKILL_ANALYSIS列出的仓库来源。

- [AgentDebug / Where LLM Agents Fail](https://arxiv.org/abs/2509.25370)：作者提出按模块组织的错误分类和故障定位反馈。用于启发本版把工具失败、记忆/规划、动作和系统问题分开记录；不把其benchmark表现当MarketOps效果。
- [RIMRULE](https://arxiv.org/abs/2601.00086)：作者研究从失败轨迹提炼紧凑规则，并用自然语言与结构化形式保存。本版借鉴“经验既可读又可校验”，不引入其完整学习算法、不修改模型权重。
- [WISE-Flow](https://arxiv.org/abs/2601.08158)：作者研究带前置条件的工作流经验。本版采用明确when/preconditions、当前状态和下一动作的设计，不宣称已经实现该框架。

以上是外部方法启发，不用于填补未知Member-A需求或授权，不把论文的训练/评估环境等同于客户真实研究。提案回放与业务Owner采纳是本项目自己的工程决定。
