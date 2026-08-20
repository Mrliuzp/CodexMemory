import { describe, expect, it } from 'vitest'
import {
  DECISION_POLICY_FIELD_DEFINITIONS,
  SAFE_FEATURE_FLAG_DEFINITIONS,
  buildDecisionPolicyPayload,
  buildFeatureFlagsPayload,
  normalizeDecisionPolicy,
  normalizeFeatureFlags,
  resolveProjectKey,
  synchronizeDecisionPolicy,
} from './decisionPolicy'

describe('项目治理表单契约', () => {
  it('呈现后端支持的四个决策策略字段和固定安全边界', () => {
    expect(DECISION_POLICY_FIELD_DEFINITIONS.map((item) => item.label)).toEqual([
      '策略启用',
      '自动发布启用',
      '执行模式',
      '最小可信度阈值',
    ])
    expect(SAFE_FEATURE_FLAG_DEFINITIONS.map((item) => item.key)).toEqual([
      'memory_v11_enabled',
      'lexical_retrieval_enabled',
      'dense_retrieval_enabled',
      'embedding_profile_v2_enabled',
      'candidate_publish_enabled',
      'decision_engine_enabled',
    ])
    expect(SAFE_FEATURE_FLAG_DEFINITIONS.map((item) => item.key)).not.toEqual(expect.arrayContaining([
      'llm_shadow_enabled',
      'server_outbox_enabled',
      'async_pipeline_v13_enabled',
    ]))
  })

  it('按 GET 响应回填策略，并强制只读安全边界', () => {
    expect(normalizeDecisionPolicy({
      enabled: true,
      auto_publish_enabled: true,
      strategy: 'auto_publish',
      min_confidence: 0.73,
      allowed_level: 'L2',
      allowed_scope: 'global',
    })).toMatchObject({
      enabled: true,
      auto_publish_enabled: true,
      strategy: 'auto_publish',
      min_confidence: 0.73,
      allowed_level: 'L1',
      allowed_scope: 'project',
    })
  })

  it('项目键优先使用 URL，其次使用当前项目上下文，再回退到身份项目', () => {
    expect(resolveProjectKey({ queryProjectKey: 'query', contextProjectKey: 'context', principalProjectKey: 'principal' })).toBe('query')
    expect(resolveProjectKey({ contextProjectKey: 'context', principalProjectKey: 'principal' })).toBe('context')
    expect(resolveProjectKey({ principalProjectKey: 'principal' })).toBe('principal')
    expect(resolveProjectKey({ principalProjectKey: '*' })).toBe('')
  })

  it('关闭策略时联动关闭自动发布和自动模式', () => {
    const policy = { enabled: true, auto_publish_enabled: true, strategy: 'auto_publish' }
    policy.enabled = false
    synchronizeDecisionPolicy(policy)
    expect(policy).toMatchObject({ enabled: false, auto_publish_enabled: false, strategy: 'manual_review' })

    policy.enabled = true
    policy.auto_publish_enabled = false
    policy.strategy = 'auto_publish'
    synchronizeDecisionPolicy(policy)
    expect(policy.strategy).toBe('manual_review')
  })

  it('拒绝非法策略组合和越界阈值，不向后端提交', () => {
    expect(() => buildDecisionPolicyPayload({
      enabled: false,
      auto_publish_enabled: true,
      strategy: 'manual_review',
      min_confidence: 0.8,
    })).toThrow('未启用策略时不能启用自动发布')
    expect(() => buildDecisionPolicyPayload({
      enabled: true,
      auto_publish_enabled: false,
      strategy: 'auto_publish',
      min_confidence: 0.8,
    })).toThrow('未启用自动发布时不能选择自动发布模式')
    expect(() => buildDecisionPolicyPayload({
      enabled: true,
      auto_publish_enabled: false,
      strategy: 'manual_review',
      min_confidence: 1.01,
    })).toThrow('最小可信度阈值必须是 0 到 1 之间的数字')
  })

  it('只提交安全项目开关，不提交影子、任务或全局开关', () => {
    const payload = buildFeatureFlagsPayload({
      flags: {
        memory_v11_enabled: true,
        candidate_publish_enabled: true,
        llm_shadow_enabled: true,
        server_outbox_enabled: true,
        async_pipeline_v13_enabled: true,
      },
    })
    expect(payload).toEqual({
      memory_v11_enabled: true,
      lexical_retrieval_enabled: false,
      dense_retrieval_enabled: false,
      embedding_profile_v2_enabled: false,
      candidate_publish_enabled: true,
      decision_engine_enabled: false,
    })
  })

  it('保留功能开关响应元数据并为安全开关补齐布尔初值', () => {
    expect(normalizeFeatureFlags({ initialized: true, flags: { memory_v11_enabled: true } })).toMatchObject({
      initialized: true,
      flags: {
        memory_v11_enabled: true,
        decision_engine_enabled: false,
      },
    })
  })
})
