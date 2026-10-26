# MarketOps Research Assistant 0.6.1

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
| `run` | 后台长程调研：抓取全部机构 → 评级 → 对还没有 A/B 的机构一轮轮换方法深挖（本地关键词、站内与 PDF、名录、地图、宽搜）→ 导出；可续跑，也可脱离会话无人值守运行 |
| `export` | 导出 Excel（指标口径写明、分批、待复核标记）和 Bruce 的 18 列路线 CSV |

项目文件夹就是普通文件（`~/MarketOps/<项目>/`）：`brief.json`、`venues.csv`、`orgs.csv`、
`contacts.csv`、`crawl.csv`、`pages/`、`costs.jsonl`。agent 和人都可以直接打开、修改，断了接着跑。

## 0.6.1 更新

- 抓取：默认 15 页、按链接打分往下两层，站内 PDF 交给 TinyFish/DeepSeek 读；同一网址一个项目只抓一次。
- 引文按页面结构取（只含这一个邮箱的卡片或行），卡片布局不再错位。
- 评级：告诉 Jev 地址自带的岗位词、该地址还挂在几家机构、页面是否在本机构网站；低置信度、共用地址、他站页面的 A/B 标"待复核"，不计入头条指标。
- 长程：`run` 一轮轮深挖直到达标或连续两轮无新增；`python scripts/server.py run <项目>` 可无人值守。
- 指标改名并写明定义；新增分批表与 18 列路线 CSV；作业按项目隔离；通用信箱规则判 D 后不再被改。

## 规则只有这些

- 邮箱必须原样出现在网页上，不许猜；每行都带来源页面和原文引用。
- 只做调研，不发信。
- 结果先放宽：域外邮箱、X 级都保留并打上标签，以后可以再收窄。
- 每次调用都记账；每个付费账户以首次读到余额的 10% 为红线，低于红线就跳过这家，其他照常跑。没有别的上限。

业务经验见 [PLAYBOOK](references/PLAYBOOK.md)。0.5.x 的旧实现在 git 历史和旧版 Release 里。
