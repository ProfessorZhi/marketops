# MarketOps Research Assistant 0.6

调研插件：从公开数据集到场地池、机构池，再到带证据、已评级的联系人，最后交付一张 Excel。
给 Claude Code 用。插件只提供能力，流程由 agent 自己掌握。

## 装

```bash
./install.sh            # Linux / macOS。Windows 用 Install.ps1
```

安装器做三件事：
- 建私有 Python 环境 `~/.marketops/venv`；
- 装 Crawl4AI 用的本地浏览器；
- 逐个询问五把密钥（Serper、SerpApi、DeepSeek、TinyFish、Jev），存到 `~/.marketops/keys.env`，只有本人可读。

然后在 Claude Code 里：

```
/plugin marketplace add ProfessorZhi/marketops
/plugin install marketops-research-assistant@marketops
```

## 用

直接说任务，或者用 `/marketops-research-assistant:research 瑞士户外天然草足球场，找场地负责人`。

| 工具 | 做什么 |
|---|---|
| `project` | 建立或打开项目文件夹，保存简报，查看进度、成本和余额 |
| `search` | Serper、SerpApi、DeepSeek、TinyFish 四路同时搜，按网址合并；`maps=True` 查地图地点 |
| `fetch` | Crawl4AI、TinyFish、DeepSeek 三路同时读同一页，每份都保存，联系人取并集；数据文件直接下载 |
| `crawl` | 后台批量读机构官网：首页，以及联系、Impressum、团队、董事会等页面 |
| `browse` | 后台用 TinyFish Agent 处理需要点击才能看到内容的难站 |
| `judge` | Jev 对任意池子逐行回答一个选择题，例如是否在范围内、是不是官网 |
| `grade` | Jev 按简报评 A/B/C/D/X，不分语言；agent 也可以手工改 |
| `export` | 导出 Excel：说明、联系人（每行附来源页面和原文）、机构、场地、成本、排除 |

项目文件夹就是普通文件（`~/MarketOps/<项目>/`）：`brief.json`、`venues.csv`、`orgs.csv`、
`contacts.csv`、`crawl.csv`、`pages/`、`costs.jsonl`。agent 和人都可以直接打开、修改，断了接着跑。

## 规则只有这些

- 邮箱必须原样出现在网页上，不许猜；每行都带来源页面和原文引用。
- 只做调研，不发信。
- 结果先放宽：域外邮箱、X 级都保留并打上标签，以后可以再收窄。
- 每次调用都记账；每个付费账户以首次读到余额的 10% 为红线，低于红线就跳过这家，其他照常跑。没有别的上限。

业务经验见 [PLAYBOOK](references/PLAYBOOK.md)。0.5.x 的旧实现在 git 历史和旧版 Release 里。
