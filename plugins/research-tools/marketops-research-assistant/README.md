# MarketOps Research Assistant 0.6.2

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
| `pool` | 从下载的开放数据文件建场地池和机构池：过滤、改列名、按运营方归并成机构 |
| `fetch` | Crawl4AI、TinyFish、DeepSeek 三路同时读同一页，每份都保存，联系人取并集；数据文件直接下载 |
| `crawl` | 后台批量读机构官网：首页，以及联系、Impressum、团队、董事会等页面 |
| `browse` | 后台用 TinyFish Agent 处理需要点击才能看到内容的难站 |
| `judge` | Jev 对任意池子逐行回答一个选择题，例如是否在范围内、是不是官网 |
| `grade` | Jev 按简报评 A/B/C/D/X，不分语言；agent 也可以手工改 |
| `run` | 后台长程调研：抓取全部机构 → 评级 → 对还没有 A/B 的机构一轮轮换方法深挖（本地关键词、站内与 PDF、名录、地图、宽搜）→ 导出；可续跑，也可脱离会话无人值守运行 |
| `tasks` | 共享任务队列：主 agent 把缺口机构、待复核、难站、数据源切成任务包，多个子 agent（`marketops-worker`）并行认领、完成、交回 |
| `export` | 导出 Excel（指标口径写明、分批、待复核标记）和 Bruce 的 18 列路线 CSV |

项目文件夹就是普通文件（`~/MarketOps/<项目>/`）：`brief.json`、`venues.csv`、`orgs.csv`、
`contacts.csv`、`crawl.csv`、`pages/`、`costs.jsonl`。agent 和人都可以直接打开、修改，断了接着跑。

## 0.6.2 更新

- 多 agent 并行：`tasks` 队列加子 agent `marketops-worker`；`run(shards=N)` 把机构池切给 N 个进程并行跑；项目文件带跨进程锁，多个进程和 agent 同时写也不会写坏。
- 供应商权重：`brief.providers` 设各家参与比例（默认 搜索 DeepSeek/TinyFish 全开、Serper 0.6、SerpApi 0.2；读页 Crawl4AI/TinyFish 全开、DeepSeek 0.6）；抽到的几家结果单薄时自动补上其余几家，不漏信息。Crawl4AI 始终参与。
- Jev 给深挖线索排优先级（只排序不删）；评级 8 路并行；单进程默认同时 12 家机构，本地浏览器最多 10 个标签页。

- 广度优先：头条改为"有 A+B 的机构占比"，另报每机构最多计 3 条的路线数；同一人两个地址计一条。
- 待复核写明原因；同一网站下多个账户共用的地址不再算"共用"；评级口径排除赞助人/支持者俱乐部、节庆酒吧、单队教练。
- A/B 行由 Jev 从引文片段里挑出姓名和岗位，交付不再空着。
- 电话尾数粘进邮箱的情况以 mailto 为准修正；电话单独存 `phones.csv`。
- `run` 作为独立进程运行，会话结束也继续；`run(stop=True)` 优雅停止并导出；进度写明每分钟机构数、剩余时间、每机构成本；目标改为机构占比，深挖轮次照常执行。
- 新工具 `pool`：从开放数据文件直接建池（过滤、映射、按运营方归并）。
- 抓取时新闻、活动、博客类链接降权，大门户不再把页数花在新闻上。

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
