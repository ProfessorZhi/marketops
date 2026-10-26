# MarketOps Research Assistant 0.6.4

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
| `browse` | 后台用 TinyFish Agent 处理需要点击才能看到内容的难站 |
| `judge` | Jev 对任意池子逐行回答一个选择题，例如是否在范围内、是不是官网 |
| `grade` | Jev 按简报评 A/B/C/D/X，不分语言；agent 也可以手工改 |
| `run` | 后台流水线：每家机构 抓官网 → 评级 → 没有 A/B 就换方法深挖（本地关键词、站内与 PDF、名录、地图、宽搜）→ 补证据；可分片多进程、边建池边跑、续跑、脱离会话运行 |
| `tasks` | 共享任务队列：主 agent 把缺口机构、待复核、难站、数据源切成任务包，多个子 agent（`marketops-worker`）并行认领、完成、交回 |
| `export` | 导出 Excel（指标口径写明、分批、待复核标记）和 Bruce 的 18 列路线 CSV |

项目文件夹就是普通文件（`~/MarketOps/<项目>/`）：`brief.json`、`venues.csv`、`orgs.csv`、
`contacts/`、`crawl.csv`、`trace.jsonl`、`costs.jsonl`；网页原件在共享资料库 `~/.marketops/library`。agent 和人都可以直接打开、修改，断了接着跑。

## 0.6.4 更新：先去掉白干的活和误拦，再提并发

依据：巴黎题实测（0.6.2）加两道题的离线模拟（假网络，读页、搜索、Jev 都有真实延迟）。

**提速**
- 读页"先到先用"：几家读取器同时读，最快的一家读到内容后最多再等 3 秒就往下走；慢的在后台读完，联系人照样补进来。搜索同理（慢引擎的结果进资料库）。每家读取器、每个搜索引擎各用自己的线程池，一家堵住不拖累别家。
- 找人名只查刚评出的 A/B 行，同一地址加同一段引文只问一次，多批并行（之前每轮把所有没名字的 A/B 重问一遍，规模越大越慢）。
- 同一地址加同一段引文只评一次，评过的直接复用；简报 `address_rules`（如 `{"info": "X"}`）在入库时就定级。
- 共用门户（一个域名挂 3 家以上机构）只读本机构那一页及其子页，不再整站爬。
- 各来源轮流进流水线；单进程默认同时 24 家（模拟里 12 → 24 吞吐翻倍，结果不变）。

**不再误拦**
- 只有纯通用信箱（info、contact、office……）直接判 D；secretariat、sports、service、mairie 这类岗位信箱交给 Jev，可以是 B 或 C。
- 他站页面（联合会、名录）上的 A/B，只要引文旁写着本机构名字，就计入头条，只是证据分低一些；不写名字的才标待复核。
- 共用地址挂 3 家以上机构才标待复核（一个市政体育处管两三块场地是常态）。
- 没有官网的机构先按名字和城市找一次官网（域名带名字，或首页标题是它的名字）。

**留痕**：每家机构在 `trace.jsonl` 记一行（各阶段秒数、联系人和 A/B 在哪一步出现）；工作簿"过程（耗时）"页按来源、阶段汇总。

**模拟结果**（法国场景 120 家，与本版第一个提交对比）：同样 12 路并发，每分钟 30.7 → 36.9 家，找到的 A/B 机构 47 → 62（共 62 家，零误报），其中俱乐部 5 → 20；默认改 24 路后约每分钟 77 家（240 家规模，127 家全部找到）。巴黎场景同样全部找到。消融：关掉深挖快一倍，但俱乐部的 A/B 少一半以上，保留。

**业务知识**（来自 Bruce 会话分析 `marketops-session`，每个数字有出处，删掉了没有出处的产出率表）：
- 五个 skill：`marketops-research`（流程，含 Bruce 踩过的坑）、`marketops-brief`（客户/竞品、评级、各国岗位词、交付规则）、`marketops-sources`（建池通道顺序、页面类型产出、来源止损规则、各国起点）、`marketops-tools`（各家搜索/读页/Jev/Agent 怎么用、Bruce 最常用的查询写法）、`marketops-benchmarks`（两道常设题、投入按轮次和人工消息计）。
- `references/projects/`：每条业务线一个文件（法国天然草、英国球场与学校、美国运动场、德国、瑞士、全球高尔夫、其他线），统一模板；新项目结束后用 `/marketops-research-assistant:learn <项目>` 把来源产出、有效页面、岗位词、纠正和基准追加进去，以后的项目自动从这里起步。
- 由此改的代码：人员页（staff/team/board/bureau/Vorstand）优先于联系页、首页（Bruce 实测每百页 A/B 1.73 : 0.18 : 0.08）；跟进俱乐部托管平台上的页面；429/5xx/断连自动重试一次（失败一次不等于死站）；来源跑满 30 家后按 A/B 产出分配名额（≥10% 加倍，<5% 排最后，不丢）。

更早版本（0.6.0–0.6.3）见 git 历史和 Release 说明。

## 规则只有这些

- 邮箱必须原样出现在网页上，不许猜；每行都带来源页面和原文引用。
- 只做调研，不发信。
- 结果先放宽：域外邮箱、X 级都保留并打上标签，以后可以再收窄。
- 每次调用都记账；每个付费账户以首次读到余额的 10% 为红线，低于红线就跳过这家，其他照常跑。没有别的上限。

业务经验见 `skills/` 和 [`references/projects/`](references/projects/README.md)。
