export const DECISION_POLICY_DEFAULTS = Object.freeze({
  enabled: false,
  auto_publish_enabled: false,
  strategy: 'manual_review',
  allowed_level: 'L1',
  allowed_scope: 'project',
  min_confidence: 0.8,
})

export const DECISION_POLICY_FIELD_DEFINITIONS = Object.freeze([
  { key: 'enabled', label: '策略启用', kind: 'switch' },
  { key: 'auto_publish_enabled', label: '自动发布启用', kind: 'switch' },
  { key: 'strategy', label: '执行模式', kind: 'strategy' },
  { key: 'min_confidence', label: '最小可信度阈值', kind: 'threshold' },
])

// 仅展示项目治理范围内且不会触及 Worker、影子模式或全局 CLI 的开关。
export const SAFE_FEATURE_FLAG_DEFINITIONS = Object.freeze([
  { key: 'memory_v11_enabled', label: 'V1.1 记忆管线' },
  { key: 'lexical_retrieval_enabled', label: '词法检索' },
  { key: 'dense_retrieval_enabled', label: '稠密检索' },
  { key: 'embedding_profile_v2_enabled', label: '嵌入配置 V2' },
  { key: 'candidate_publish_enabled', label: '候选发布门' },
  { key: 'decision_engine_enabled', label: '决策引擎' },
])

function readThreshold(value) {
  if (value === null || value === undefined || (typeof value === 'string' && !value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null
}

export function normalizeDecisionPolicy(value = {}) {
  const source = value && typeof value === 'object' ? value : {}
  const enabled = source.enabled === true
  const autoPublishEnabled = enabled && source.auto_publish_enabled === true
  const strategy = autoPublishEnabled && source.strategy === 'auto_publish' ? 'auto_publish' : 'manual_review'
  return {
    ...source,
    ...DECISION_POLICY_DEFAULTS,
    enabled,
    auto_publish_enabled: autoPublishEnabled,
    strategy,
    allowed_level: 'L1',
    allowed_scope: 'project',
    min_confidence: readThreshold(source.min_confidence) ?? DECISION_POLICY_DEFAULTS.min_confidence,
  }
}

export function normalizeFeatureFlags(value = {}) {
  const source = value && typeof value === 'object' ? value : {}
  const sourceFlags = source.flags && typeof source.flags === 'object' ? source.flags : {}
  const flags = { ...sourceFlags }
  SAFE_FEATURE_FLAG_DEFINITIONS.forEach(({ key }) => {
    flags[key] = sourceFlags[key] === true
  })
  return { ...source, flags }
}

export function resolveProjectKey({ queryProjectKey = '', contextProjectKey = '', principalProjectKey = '' } = {}) {
  const principal = principalProjectKey === '*' ? '' : principalProjectKey
  return String(queryProjectKey || contextProjectKey || principal || '').trim()
}

export function synchronizeDecisionPolicy(policy) {
  if (!policy) return policy
  if (policy.enabled !== true) {
    policy.enabled = false
    policy.auto_publish_enabled = false
    policy.strategy = 'manual_review'
  } else if (policy.auto_publish_enabled !== true) {
    policy.auto_publish_enabled = false
    if (policy.strategy === 'auto_publish') policy.strategy = 'manual_review'
  }
  return policy
}

export function validateDecisionPolicy(policy) {
  if (!policy || typeof policy !== 'object') return '项目决策策略尚未加载。'
  if (typeof policy.enabled !== 'boolean') return '策略启用必须是布尔值。'
  if (typeof policy.auto_publish_enabled !== 'boolean') return '自动发布启用必须是布尔值。'
  if (!['manual_review', 'auto_publish'].includes(policy.strategy)) return '执行模式不受支持。'
  const threshold = readThreshold(policy.min_confidence)
  if (threshold === null) return '最小可信度阈值必须是 0 到 1 之间的数字。'
  if (policy.auto_publish_enabled && !policy.enabled) return '未启用策略时不能启用自动发布。'
  if (policy.strategy === 'auto_publish' && !policy.auto_publish_enabled) return '未启用自动发布时不能选择自动发布模式。'
  return ''
}

export function buildDecisionPolicyPayload(policy) {
  const error = validateDecisionPolicy(policy)
  if (error) throw new Error(error)
  return {
    min_confidence: Number(policy.min_confidence),
    enabled: policy.enabled,
    auto_publish_enabled: policy.auto_publish_enabled,
    strategy: policy.strategy,
  }
}

export function buildFeatureFlagsPayload(value) {
  const source = value?.flags && typeof value.flags === 'object' ? value.flags : value
  if (!source || typeof source !== 'object') throw new Error('项目功能开关尚未加载。')
  return Object.fromEntries(SAFE_FEATURE_FLAG_DEFINITIONS.map(({ key }) => [key, source[key] === true]))
}
