# 脱敏说明

本仓库是 MarketOps 的**脱敏公开快照**（2026-10-19）。原始仓库连同完整提交历史、Issue 与 PR
保持私有，不在本仓库中。

## 做了什么

| 对象 | 处理 |
|---|---|
| 同事姓名（飞书发言人、协作者） | 换成 `Member-A` … `Member-E` |
| 飞书 `open_id` / `chat_id` / `message_id` / `tenant_key` | 换成全零序列号，**保持唯一性与可关联性** |
| 飞书群与文档链接 | 域名换成 `applink.example.com` / `example.feishu.cn`，文档 token 换成 `TOKENxxxxxx` |
| 消息正文 | **原样保留**，仅替换上述标识符 |
| 客户/项目代号 | 换成 `Acme` / `AcmeCorp` |
| 公司品牌名与域名 | 换成 `EXD` / `example.com` |
| 内部主机名与路径 | 换成 `vm-example`、`host-example`、`/srv/marketops` 等 |
| 5 个二进制附件（2 个 docx 开发信模板、2 张会话截图、1 张报错截图） | **从仓库中移除**，`sources/manifest.yaml` 中对应条目光一并删除 |
| 以人名命名的目录、文档与常量 | 一律改为 `member-a-*` / `member-c-*` / `member-d-*` / `party-group` |

## 因此会看到的现象

- `sources/` 里的消息快照与上游 `upstream-records` 不再逐字节一致，`tests/test_business_sources.py`
  中依赖上游比对的断言在无上游环境时按设计跳过。
- 引用被移除附件的文档段落仍保留了文字说明。

本仓库不含任何可用的 API key、密码或令牌。测试夹具中的 `sk-…` 形态字符串均为占位符。
