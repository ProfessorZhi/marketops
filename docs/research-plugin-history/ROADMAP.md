# 两阶段交付：先半自动，再全自动

## 当前交付物（P0）：MarketOps Research Assistant

目标：Member-A 等人继续在 Codex/WorkBuddy 使用既有熟悉的 Agent，加上我们的业务 Skills、可选的四家 Provider MCP/API，以及本地可检查的数据包，显著减少人工查询、复制、去重、校验和 Excel 制表。

边界：手动发起/继续，目标和费用由人定；尚不把 `research_pack` 当 MarketOps 生产数据库；尚不驱动真实邮件发送。安装一个“业务入口”不等于四个供应商都能即刻使用，API/OAuth 仍需按用户权限配置。

P0 可验收结果：
- 在同一个明确限定的研究任务里完成来源→场地/组织→相关岗位/联系渠道→有出处的 Excel/CSV 交付；
- 任意阶段可暂停并交给另一个 Agent/同事（文件或未来服务端数据库），不丢已核实对象和 evidence 指针；
- 程序检查唯一键、阶段关联、原文邮箱、状态晋升、易错字段；正式联系人由人审查；
- 未接入 MCP 时仍可初始化、查看和校验本地包；没有实际有效结果时不报 AUTO/FULL PASS。

## 后续交付物（P1/P2）：可在客户服务器部署的全自动工作流

优先复用当前 `marketops/` 的 Scene/Run/WorkItem/Budget/Evidence/ContactRoute 等领域模型和已在 Draft PR 中实现的受管 Serper/Crawl4AI/Jev 组合。根据受控测试结果决定 TinyFish Adapter、后台 CC Native Worker、Durable Queue、RunDelta/Watch/Schedule 何时接入；不把 PR #64 的 CSV 交接文件误当生产端唯一真源。

增加自动化主要改变“**谁负责继续下一批**”，不改变证据和业务晋升规则。达到全自动之前，须实现客户自己的独立服务器部署、最小系统权限、任务恢复、取消/暂停、累积预算上限、失败隔离、跨 Run 幂等和客户隐私保护。

## 外联后置的独立阶段

联系人可进入审核草稿，但**发送**至少还需：独立批准、Campaign/Wave 的冻结名单、抑制和退订硬闸、收件人唯一性、频率上限、异常停机、回复及退信处理。不能因研究 Run 完成而默认允许发送。

## 防止项目走偏的四项判断

- 优先评测市场研究**有效产物**，不是 Agent 运行小时数；
- Excel 是交付视图，Evidence / Market Graph 才是事实源；
- 第一批用户不需要东京 CC 的 Linux 权限；
- 原始用户需求/业务判断与 Skill 版本受人工控制，市场未知数据不由大模型补齐。