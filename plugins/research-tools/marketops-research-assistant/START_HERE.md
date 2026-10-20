# MarketOps：安装、账号与启动

> 版本 **0.5.6-rc5**（release candidate，不是 0.5.5）。请使用本包 ZIP 与随附 SHA256。

Windows x64：解压整个源码包，双击 **Install.cmd**。选择安装目录；程序准备 Python 3.12、四家接入代码、Crawl4AI 和 Chromium，然后打开本地设置。没有 Python 时通过 Windows Package Manager 安装官方 Python；没有 winget 时按提示先安装 Python 3.12 x64。安装过程会下载软件，但不会购买额度或提交调研请求。

Linux x64：准备 Python 3.12，在包目录运行 `sh install.sh /你的绝对安装目录 both`。不需要另装 TinyFish、SerpApi 插件。两家官方远程 MCP 的客户端已随 MarketOps 包提供。

1. 查看 [四账户与新用户额度](references/FOUR_ACCOUNT_START.md)，自己注册并取得 API Key。已注册就使用现有账户。
2. 在本地隐藏输入提示中填 Key，保存在系统凭据库；不要贴到聊天。选择研究数据目录和独立经验汇总文件。换会话、重启后会继续使用。
3. 安装后打开**安装目录自动生成的 START_HERE.md**，其中有本机精确的 Codex/WorkBuddy 导入路径、配置命令和启动说明。
4. 新会话说：**“使用 MarketOps。先检查安装、数据目录、四家凭据和已授权额度；然后按我的研究目标建立 Brief，先做 3 个候选，保存原文和证据，导出结果并记录可复用经验。无法核实的保留 HOLD。”**

安装中断时保留目录，再运行相同安装入口。它只恢复自己创建、版本和源码一致的安装；不会清空研究数据。更换版本选择新安装目录，已有数据与经验配置保持不变。

完整步骤见 [客户快速开始](references/CUSTOMER_QUICKSTART.md)。
