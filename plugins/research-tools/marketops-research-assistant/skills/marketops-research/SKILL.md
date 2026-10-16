---
name: marketops-research
description: Single entry for one human-initiated MarketOps candidate research Run; resume persistent state, load only the needed stage Skill, deliver audited XLSX and collectable experience without formal contact promotion.
---

# MarketOps Research — 一个入口，最小上下文

未配置时先调用 `marketops_setup_status` 并只问数据根与经验文件两个绝对路径；已配置不再问。新 Run 用 `marketops-intake` 保存明确研究目标；先检查已有 Run，绝不因模型重启而重新抓取。未知国家/产品/场景保持未知，**不默认绑定任何固定市场 Profile**。

## 小批次：只取需要的信息

1. 每批 `marketops_next(run_id, limit=10)`；只加载当前推荐的阶段 Skill（需要语义解释时才读单个 reference）。已知对象直接用过滤的 `marketops_status(limit=20)`，禁止每轮 dump 全库。
2. 需要资料才调用 provider router。优先复用当前 Run 的真实材料；用 `marketops_material_excerpt` 根据关键词读最多1200字符，不把整页 HTML、全部 Skill 或整批候选堆入上下文。允许联网和费用必须**明确、有限、可停**。
3. 对多个候选用 `marketops_propose_batch` 一次提交最多20条，原子写入、失败全回滚。不得在正式 Run 插测试探针。每条主张要有 `claims=[{field,artifact_id,quote}]`，关系、排除和场景要求的关键事实必须给出**同源引文**；仅有 artifact_id 不构成论据。
4. 用户要求哪些实体、数量、字段，严格按原话区分：场地探索数 ≠ 已核实客户数 ≠ 具名邮箱数；“争取”不是强制 `minimum_targets`。若有硬最低值，必须保存其 `minimum_targets_unit`，与 `target_kind` 一致。场景特定门槛写在 Brief 的 `evidence_requirements`，不写死在插件代码。
5. 每批调用 `marketops_evidence_audit` 看简短缺口码，再判断继续取证、HOLD、EXCLUDED 或暂停。引文匹配仅证明文本出现，**不代表事实语义、当前任职、采购权限或正式 SOP 通过**。查不到有效原文就 HOLD，禁猜邮箱/关系，禁绕访问墙。

## 收口与经验

经 `marketops-research-acceptance` 审查后**只调用一次** `marketops_handoff`：九表 XLSX/CSV/manifest + 经验文件回执。完整文件≠完成业务目标；汇报时分别列技术完整性、证据缺口、范围内有效结果与未完成核验。

失败/改路由/人工纠错时 `marketops_note` 只记录脱敏观察、采取措施、实际结果与独立假设，经验只累计不自动改代码或 SOP。不得自动扩大任务、调用未批准收费 API、外联或发送。
