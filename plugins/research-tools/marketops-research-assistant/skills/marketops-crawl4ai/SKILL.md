---
name: marketops-crawl4ai
description: Use authorized Crawl4AI SDK, self-hosted MCP or Cloud MCP to acquire and batch-extract source materials while preserving per-URL errors, record boundaries, provenance and honest evidence status.
---

# Crawl4AI：取得可消费的材料

MarketOps自编使用Skill。先读 [路由合同](../../references/PROVIDER_ROUTING.md)。官方文档：https://docs.crawl4ai.com/ 与 https://docs.crawl4ai.com/core/self-hosting/ 。本地/Cloud是不同接口，读取实际catalog，不照抄其他端点工具名。

输入：获准URL集合、必须取得的字段、存量/时效、输出schema与费用边界。

1. 小样本查看真实HTML/链接/记录块，再选CSS/XPath或合适的结构提取；不能凭标题猜selector。
2. 正文、链接/`mailto:`、姓名职位卡片、分页、PDF和来源发布时间按需求保留。Markdown过度清理丢掉字段时，改用已取得HTML/结构化材料重解析，而不是再抓相同站点。
3. 批量逐URL记录成功、阻断、空、解析错误；一页失败不得伪称整批成功，也不得抹掉其他页成果。
4. 保存真实取得的材料，明确raw/rendered/extracted及变换；字节hash由程序算。Provider只给Markdown时不谎称取得原始网页。
5. 检查正文是不是权限墙、错误页或目录导航，再做事实和实体归属。HTTP200/hash不证明内容正确。
6. 数据落到固定store并读回，输出实体id、evidence/claim引用及每条输入处置；多页一个组织与一页多个场地不能相互混淆。

遇到WAF/CAPTCHA/登录权限停止该来源，换获准公共资料；不得启动规避访问控制的代理/脚本。Cloud账单管理、远程执行脚本、充值及扩大额度不在研究许可内。提取结果异常先诊断压缩/编码/schema/record切分，经验记录精确到输入样本和版本。
