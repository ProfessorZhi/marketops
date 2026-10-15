# Codex / WorkBuddy / DeepSeek Harness 安装与卸载

**目标是“一个用户可调用的调研工作流入口”，不是一次安装四套计费服务**。此包本身不含 Provider API Key，不包含 Serper/Crawl4AI/TinyFish/Jev 的第三方源码，也不启动东京 CC。

## 1. Codex

1. 在独立工作目录查看 `.codex-plugin/plugin.json` 和 Skills；开发/试用安装使用 Codex 当前版本支持的本地插件路径/市场机制。Codex 官方插件手册：https://developers.openai.com/plugins/build/plugins 。
2. 本包的 `.mcp.json` 默认为 `{"mcpServers":{}}`，没有偷偷启用任何收费接口。用户可以逐个安装自己需要的 Provider：
   - TinyFish 官方：`npx @tiny-fish/cli connect codex --dry-run` → 审核 → `npx @tiny-fish/cli connect codex` → OAuth。
   - Crawl4AI Cloud：按上级 `providers/crawl4ai.md` 在 `~/.codex/config.toml` 单独配置远程 MCP + 用户自己的 Bearer Key。
   - Serper：按 `providers/serper.md` 使用官方 API 或经审查的社区 MCP，不宣称 Serper 官方 MCP 存在。
   - TypeSafe/Jev：`npx skills add typesafe-ai/skills --skill typesafe-ai` 为官方方法知识；实际调用仍需 SDK/API 或受管 Tool。
3. 可选通用 Excel 功能：`npx skills add https://github.com/openai/skills --skill spreadsheet`。本包 `marketops-excel` 提供业务表格和引用质量合同。
4. 在新会话先问“帮我在本地创建一个空的 MarketOps research pack，并解释有哪些研究阶段”；这一步应零 Provider 调用。
5. 之后只对明确批准的查询使用真实 Search/Fetch；检查实际工具可见性、OAuth 与费用，再开展小批研究。

**卸载**：从当前 Codex 插件管理界面移除 MarketOps Research Assistant；TinyFish 如通过官方 CLI 安装，可经用户确认执行 `npx @tiny-fish/cli connect codex --uninstall`。仅删除用户明确要删除的连接项，不覆盖整个 `~/.codex/config.toml`。已创建 research pack 属用户数据，不随卸载清除。

## 2. WorkBuddy

WorkBuddy 官方 Connector：https://open.workbuddy.cn/docs/connector 。它支持 `type: "skill-only"`，所以可以先安装只有 MarketOps Skills 的**单一调研连接器**；不需要把四个 MCP 服务硬塞到一个连接器。

```bash
python scripts/build_client_bundles.py --output ~/marketops-research-bundles
```

得到 `dist-marketops-local/workbuddy/marketops-research-assistant/`（及 ZIP）；检查目录和元数据后，在支持导入自定义连接器的 WorkBuddy 客户端中安装。具体 UI 操作依当前客户端版本；官方 `name_zh`/示例字段要求 4.24.0+。

需要网络工具时可继续分别接入上级已准备的 [TinyFish](../../workbuddy/tinyfish/) 和 [Crawl4AI](../../workbuddy/crawl4ai/) **MCP+Skill 候选连接器**。两者需客户端真机 OAuth/Key 验收；Serper/Jev 则使用官方 API/SDK 或经过审核的 MCP 接入，不能被伪装成已安装。

**卸载**：通过 WorkBuddy 的连接器管理移除 MarketOps Research Assistant（以及用户明确决定要卸载的供应商连接器）。不应自动删除客户研究文件或供应商账户。

## 3. DeepSeek Harness / DSH

官方源代码和文档：https://github.com/deepseek-ai/deepseek-harness 。DSH 使用自身 Cordis 插件框架、filesystem Skill provider。首版交付的是可复制的标准 `skills/*/SKILL.md` + 工作流数据包脚本；由 DSH 管理员根据官方 Skill 发现目录进行安装并验证。**目前不提供未经测试的 Cordis 原生 plugin.ts，也不保证直接识别 Codex Manifest。**

参考官方 `packages/skill/README.md` 与 `packages/skill/tool-skill/README.md`。执行真实 Provider 操作前同样必须单独配置受控 MCP/SDK 并核实权限。

## 4. 隔离与费用

- Provider 凭据只保留在客户自己的凭据管理器 / 客户机器，不上传到共享 Git 或本插件 ZIP。
- 默认人工 `manual`；不运行东京 Agent、不创建自动任务、不启动外联。
- Skill 指令只能指导行为，**不能**替代服务端授权和真实收费上限。限制 Provider 额度并检查 Token、认证和工具权限。
- 不在多人共享网络磁盘对同一个 CSV 包并发读改写；多人持续使用以后再连接 MarketOps 持久数据库。

## 5. 试用验收口径

按照四层验收：文件解包正确 → Skill 可见 → 外部 MCP 可见并完成 OAuth（如需）→ 一次有真实来源的 3–5 个对象调查 + Excel/CSV 交接。四层未全过时标 `PARTIAL`，不能声称完成客户端接入。