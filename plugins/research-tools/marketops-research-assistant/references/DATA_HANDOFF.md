# 统一存取与交接：不让Skill各自造一套池子

本文件是调用纪律，不声称已经实现统一Research MCP。现状：PR64有离线CSV包和校验器，内置MCP仍为空；生产领域模型在MarketOps主代码，当前切片不改它。

## 开工时选择且固定一个store

优先：客户已配置、经过授权的MarketOps MCP存储。记录实际服务标识、workspace、schema版本、run_id、工具catalog摘要与数据快照引用；所有业务Skill走相同服务。工具名以实际发现为准，不凭本手册虚构工具。

降级：用户明确选择的 `marketops-research-pack/0.2.0` 本地目录，按 `FLOW_AND_SCHEMA.md` 及 `research_pack.py.TABLES` 操作；单写者、数据在Git外。它是便携交接包，不是已完成的统一数据库。**不要同时双写本地表和远程库。** 改store必须显式导入、去重、校验、读回，记录映射与冲突。

## 每次操作的最小记录

经验旁录使用 `operation_id, run_id, stage, object_refs, capability, provider, interface, provider_version, schema_version, input_ref, result_ref, disposition_ref, transport_status, business_status, error_code, started_at, ended_at, usage`。这些是经验记录字段，不是伪称供应商返回同样字段。

- interface明确为MCP/API/SDK/CLI/manual；实际工具名另记。工具调用返回后还没有入库，business_status只能是pending/hold。
- source/material/evidence/decision/assessment分开；服务端生成的id、收据、hash原样保存，不由Agent猜测。
- 模型产物提交为候选事实，校验器/可信复核决定晋升；`reviewed_by`文本是本地审查记录，不是认证身份。
- 同样请求超时且可能已执行：先查operation/provider job状态；不能盲重试并重复消费。
- 每个来源结果都标处置，关联具体output id；已生成但未消费也保留，不能把等待提交当“没搜到”。

## 证据格式真实边界

保存的是原始下载字节时，才称raw/source-byte hash。供应商只返回抽取Markdown/JSON时，标 `material_kind=provider_extracted`、记录变换及该产物hash，不能编一个“原始网页hash”。若当前包的 `content_sha256` 语义要求raw而拿不到，则保留候选/unknown与原始Provider结果，不能用派生hash冒充晋升证据。离线校验器只查字符串形状/关联，不证明字节真实存在或支持语义。

单行evidence_id只是v0.2的最小视图，不足以表示多份材料/冲突/多次任职；保存补充材料引用到受控工件，升级统一模型时显式映射，不改列凑数据。

## 导出

Excel为同一事实快照的投影。业务版按客户总表、联系路线、排除/待确认、证据索引、本轮说明；审计版可展开现有13个Sheet。两种视图不得重新计算不同的资格或计数定义。任何导出都附store/run/snapshot、规则版本、实际行数、文件hash和未完成项。

业务数据留在客户域内；通用经验包只带脱敏定位符和必要最小案例。上传GitHub前人工检查，用户批准组装Skills不等于允许上传客户原始语料、邮件或凭据。
