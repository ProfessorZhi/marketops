---
name: marketops-tinyfish
description: Use TinyFish search and fetch first and authorized browser interaction only when necessary; inspect current MCP schema, preserve asynchronous run identity, and record provider output separately from verified evidence.
---

# TinyFish：合法的交互式补充能力

MarketOps自编任务Skill，不复制厂商“所有网页都选TinyFish”的全局触发。官方安装入口：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md；参考Skill：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/skills/use-tinyfish/SKILL.md 。

输入：未知来源查询或已知URL、需要的事实/字段、允许网站/动作/费用、统一store。

- 没有URL可用Search；已有URL先Fetch。Fetch本身可取得JS渲染内容，不能只因“网站用JS”就升级Agent。
- 读取本连接实际MCP schema；常见search/fetch_content/run_web_automation只是能力例子，CLI选项不当MCP参数。
- Fetch批次按已发现schema限制，每个URL保留errors/result。缓存时间、published_at与observed_at分别记，缓存命中不冒充刚从源站验证。
- 真正需要点击公开筛选器/翻页时，才用获准交互工具。目标写清网站、字段JSON形状、最多对象/页面、只读动作、完成/停止条件和禁止外部提交。
- OAuth、API Key及客户MCP Token不可互换；不借用他人profile/vault、不开充值/监控或持久Browser session来绕授权。
- 异步：保存provider run_id后查询原任务，超时/断线先核实状态；不能每轮轮询都新建任务。批量相互独立任务仍受总额度限制。
- 输出看真实result/材料，不把COMPLETED或生成JSON看作业务验收。模型综合文本标provider_generated，不伪造原文或原始hash。

取得结果后调用统一存取路径并读回；缺少源URL/原文支持则保留候选。付费交互失败、Selector不匹配、错误缓存、数据未回写分别进入复盘。可以换合法源，不能用另一个Provider绕验证码或权限墙。
