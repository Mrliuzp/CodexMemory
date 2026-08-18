# Codex CLI 记忆决策 Worker 集成边界

## 目标

本块实现候选记忆的决策状态机、发布治理、模型失败降级、人工纠错和运行观测。默认不调用真实模型、不读取真实凭据，也不打开生产自动发布。

候选创建后的链路为：

```text
MemoryCandidate
  → candidate.decision.requested.v1
  → decide_candidate
  → CodexCliDecisionAdapter（严格 ModelDecisionOutput）
  → DecisionService（运行与决策审计）
  → CandidatePolicyService（服务器策略门）
  → candidate.accepted.v1（仅项目级 L1 通过时）
  → 现有 publish_memory Worker
  → Memory
```

模型建议不能直接写 `memories`。L0 `messages` 是不可变事实，人工纠错只能改变候选/正式 Memory 的治理状态，或创建带新证据的替代候选。

## 自动发布门禁

自动路径必须同时满足：

- 候选为项目级 `L1`，不是 global、`L2` 或 `L3`；
- 项目 `candidate_publish_enabled` 开关为 true（`memory_v11_enabled` 只控制候选生成）；
- evidence 存在，消息 hash 和半开区间引用仍然有效；
- schema、project access、冲突/重复检查通过；
- 模型返回 `decision=publish`、有限的 `confidence`，且达到 `CODEX_MEMORY_L1_AUTO_PUBLISH_THRESHOLD`（默认 `0.80`）；该值可由项目决策策略显式覆盖。
- 模型未 abstain。

任何一项失败都不创建 `candidate.accepted.v1`。`skip` 写入策略结果和审计但不发布；低置信度、范围不允许、风险或验证失败进入 `needs_review`。

## 模型适配接口

Worker 内部的 `CandidateDecisionModel` 只表示经过 adapter 映射后的建议，不是可绕过 Runner 的注入入口：

```python
class CandidateDecisionModel(Protocol):
    name: str
    version: str

    def decide(self, candidate: CandidateSnapshot) -> CandidateDecision | Mapping[str, Any]: ...
```

Worker 默认构造关闭的 `CodexCliRunner`，并通过 `CodexCliDecisionAdapter` 将严格 `ModelDecisionOutput` 映射为候选建议；测试只能注入带 fake process 的 Runner。Runner 禁用、不可用、超时或输出无效时进入可观测重试或人工队列，不能伪装成成功。

适配器不得写数据库、发布 Memory 或接触 L0 写入接口。模型失败、超时和无效 JSON 统一按可重试错误处理；达到 Job 最大尝试次数后 Job 保持 `dead`，候选转为 `needs_review`，不会把失败标记为成功。

## 持久化契约依赖

最终集成同时使用 V1.1 候选表和 V1.6 决策审计表：

| 用途 | 当前接口 |
| --- | --- |
| 候选 | `memory_candidates` |
| 证据 | `candidate_evidence` |
| 决策结果 | `candidate_policy_results` |
| 决策/发布队列 | `outbox_events`、`processing_jobs` |
| 人工纠错与指标 | `security_audits` |
| 决策策略/运行/模型决策 | `project_decision_policies`、`decision_runs`、`model_decisions`、`decision_review_actions` |
| 正式发布 | 现有 `publish_memory` Handler 和 `memories` |

V1.6 专用表只保存严格校验后的运行、决策和人工动作；不改变 L0 和 `candidate.accepted.v1` 的服务器门禁。

## 人工纠错

`POST /api/admin/v1/candidates/{id}/correction` 支持 `discard`、`reject`、`supersede` 和 `replace`。每次操作要求 `reviewer` 与 `reason`，写入 policy result 和 `candidate_human_correction` 审计。`replace` 会创建新的候选和决策事件，不原地编辑旧候选或 L0；已发布内容只能 `supersede` 或 `replace`，正式 Memory 内容本身保持不可变。

## 观测接口

- `GET /api/admin/v1/decision-observability`：支持项目筛选；
- `GET/PUT /api/admin/v1/projects/{project_key}/decision-policy`：管理员显式读取/更新 `min_confidence`、策略开关和策略模式；兼容 V1 API 提供 `GET/POST /api/v1/admin/projects/{project_key}/decision-policy`。
- `GET /api/admin/v1/dashboard` 和 `GET /api/admin/v1/system/status`：增量返回 `decision`、`feature_flags` 和 attention 字段。

返回字段包括 `queued`、`human_review_queue`、`model_failures`、`retries`、`auto_published`、`human_overturned`、`stuck_jobs` 和功能开关/阈值状态。

## 集成顺序

1. 先升级 `0025_expand_audit_subject_id → 0026_init_project_flags → 0027_v16_decision_contracts`，保持单一 head。
2. Worker 通过 `CodexCliDecisionAdapter` 接入关闭的 Runner；测试使用 fake process，不调用真实 Codex。
3. 运行审计由 `DecisionService` 持久化，候选与正式 Memory 仍由现有 Candidate/Publish Worker 处理。
4. 集成测试验证 `candidate.accepted.v1` 被现有发布 Worker 消费，失败路径保持 `retry_wait/dead + needs_review`。

## 与并行提交的集成冲突点

- `48fb83c` 的 `DecisionService` 使用 `project_decision_policies.min_confidence`（默认 `0.80`）和 `decision_engine_enabled`。当前 adapter 优先使用该表；旧表回退只读安全关闭策略，不能静默自动发布。合入后仍需复核 `src/codex_memory/api/http_api.py` 的 flags 路由重叠。
- `67bb2e2` 的 `CodexCliRunner` 返回严格的 `ModelDecisionOutput`，字段包含 `title/content/reason/evidence_ranges/risk_flags/level/scope`；本块的 `CandidateDecisionModel` 是更小的 Worker 建议接口。需要单独 adapter 映射两者，不能把 Runner 的原始结果直接当作发布资格。
- `candidate_publish_enabled` 仍是项目发布门；如果 48fb 的 `decision_engine_enabled` 字段已经存在，本块会额外要求它为 true。任一开关关闭、策略未启用或策略模式不是 `auto_publish`，都不能产生 `candidate.accepted.v1`。
