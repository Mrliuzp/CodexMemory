const STATUS_MAP = {
  active: ['活跃', 'success'],
  healthy: ['正常', 'success'],
  ok: ['正常', 'success'],
  ready: ['就绪', 'success'],
  queued: ['排队中', 'warning'],
  completed: ['已完成', 'success'],
  approved: ['已批准', 'success'],
  published: ['已发布', 'success'],
  passed: ['已通过', 'success'],
  generated: ['已生成', 'success'],
  running: ['运行中', 'primary'],
  in_progress: ['进行中', 'primary'],
  processing: ['处理中', 'primary'],
  uploading: ['上传中', 'primary'],
  proposed: ['待发布', 'warning'],
  pending: ['待处理', 'warning'],
  pending_review: ['待审核', 'warning'],
  awaiting_review: ['待审核', 'warning'],
  needs_review: ['待人工处理', 'warning'],
  retry_wait: ['等待重试', 'warning'],
  dispatched: ['已投递', 'info'],
  uncertain: ['不确定', 'warning'],
  degraded: ['已降级', 'warning'],
  failed: ['失败', 'danger'],
  error: ['异常', 'danger'],
  dead: ['死信', 'danger'],
  rejected: ['已拒绝', 'danger'],
  cancelled: ['已取消', 'info'],
  deprecated: ['已废弃', 'info'],
  quarantined: ['已隔离', 'warning'],
  parsed: ['已解析', 'success'],
  duplicate: ['重复', 'info'],
  inactive: ['未启用', 'info'],
  empty: ['暂无版本', 'info'],
  superseded: ['已替代', 'info'],
  draft: ['草稿', 'info'],
  finalized: ['已封版', 'success'],
  restricted: ['受限', 'info'],
  rolled_back: ['已回滚', 'info'],
  unknown: ['未知', 'info'],
}

const FIELD_LABELS = {
  id: 'ID',
  project_id: '项目 ID',
  project_key: '项目键',
  scope_id: 'Scope ID',
  scope: 'Scope',
  scope_key: 'Scope 键',
  event_key: '事件键',
  event_type: '事件类型',
  role: '角色',
  source: '来源',
  source_name: '来源名称',
  source_type: '来源类型',
  content: '内容',
  title: '标题',
  name: '名称',
  heading: '标题',
  label: '标签',
  summary: '摘要',
  description: '说明',
  text: '文本',
  value: '值',
  level: '层级',
  memory_type: '记忆类型',
  status: '状态',
  review_status: '审核状态',
  decision_queue_status: '决策队列状态',
  confidence: '置信度',
  model_confidence: '模型置信度',
  model: '模型',
  abstain: '暂缓决策',
  published_memory_id: '已发布记忆 ID',
  metadata: '元数据',
  metadata_json: '元数据',
  evidence: '证据',
  evidence_json: '证据',
  payload: '载荷',
  data: '数据',
  code: '代码',
  error: '错误',
  error_code: '错误代码',
  request_id: '请求 ID',
  subject_type: '主体类型',
  subject_id: '主体 ID',
  content_hash: '内容哈希',
  created_at: '创建时间',
  updated_at: '更新时间',
  started_at: '开始时间',
  ended_at: '结束时间',
  database: '数据库',
  dialect: '数据库方言',
  migration_schema: '迁移状态',
  latest_migration: '最新迁移',
  pending_jobs: '待处理任务',
  server_outbox: '待投递 Outbox',
  dead_letters: '死信事件',
  degraded: '已降级',
  degraded_reason: '降级原因',
  latency_ms: '延迟（毫秒）',
  truncated: '已截断',
  redaction_applied: '已脱敏',
}

const ROLE_LABELS = {
  user: '用户',
  assistant: '助手',
  system: '系统',
  tool: '工具',
}

const MEMORY_TYPE_LABELS = {
  fact: '事实',
  preference: '偏好',
  rule: '规则',
  summary: '摘要',
  imported_reference: '导入参考',
  reference: '参考资料',
}

const TITLE_KEYS = ['title', 'name', 'heading', 'label', 'subject', 'summary', 'text', 'value', 'content', 'source_name', 'event_type', 'event_key', 'id']

function isStructured(value) {
  return value !== null && typeof value === 'object'
}

function scalarText(value) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function scalarReadableValue(key, value) {
  if (value === null || value === undefined || value === '') return '-'
  const normalizedKey = String(key || '').toLowerCase()
  if (typeof value === 'boolean') {
    if (normalizedKey === 'degraded') return value ? '已降级' : '正常'
    return value ? '是' : '否'
  }
  if (normalizedKey === 'role') return ROLE_LABELS[String(value).toLowerCase()] || String(value)
  if (normalizedKey === 'memory_type') return MEMORY_TYPE_LABELS[String(value).toLowerCase()] || String(value)
  if (normalizedKey === 'status' || normalizedKey.endsWith('_status') || ['database', 'migration_schema'].includes(normalizedKey)) return statusMeta(value).label
  if (['confidence', 'model_confidence'].includes(normalizedKey) && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 1) return `${Math.round(Number(value) * 100)}%`
  if (normalizedKey.endsWith('_at') && (typeof value === 'string' || value instanceof Date)) return formatDateTime(value)
  return scalarText(value)
}

function indentMarkdown(source) {
  return source.split('\n').map((line) => `  ${line}`).join('\n')
}

function renderReadableValue(value, key, state) {
  if (value === null || value === undefined || value === '') return '-'
  if (!isStructured(value)) return scalarReadableValue(key, value)
  if (state.depth >= state.maxDepth) return '…（层级过深，已省略）'
  if (state.seen.has(value)) return '…（循环引用，已省略）'
  state.seen.add(value)
  let output
  if (Array.isArray(value)) {
    output = value.length
      ? value.map((item, index) => isStructured(item)
        ? `- **第 ${index + 1} 项**\n${indentMarkdown(renderReadableValue(item, '', { ...state, depth: state.depth + 1 }))}`
        : `- ${scalarReadableValue('', item)}`).join('\n')
      : '-'
  } else {
    const entries = Object.entries(value)
    output = entries.length
      ? entries.map(([entryKey, entryValue]) => {
        const label = fieldLabel(entryKey)
        if (isStructured(entryValue)) return `- **${label}**：\n${indentMarkdown(renderReadableValue(entryValue, entryKey, { ...state, depth: state.depth + 1 }))}`
        return `- **${label}**：${scalarReadableValue(entryKey, entryValue)}`
      }).join('\n')
      : '-'
  }
  state.seen.delete(value)
  return output
}

function titleCandidate(value, seen = new WeakSet()) {
  if (value === null || value === undefined || value === '') return ''
  if (!isStructured(value)) {
    const text = scalarText(value)
    return text === '-' ? '' : text.trim()
  }
  if (seen.has(value)) return ''
  seen.add(value)
  if (Array.isArray(value)) {
    const first = value.map((item) => titleCandidate(item, seen)).find(Boolean) || ''
    seen.delete(value)
    return first
  }
  for (const key of TITLE_KEYS) {
    const candidate = titleCandidate(value[key], seen)
    if (candidate) {
      seen.delete(value)
      return candidate
    }
  }
  const first = Object.values(value).map((item) => titleCandidate(item, seen)).find(Boolean) || ''
  seen.delete(value)
  return first
}

export function statusMeta(status, fallbackLabel = '') {
  const source = isStructured(status) ? (status.status ?? status.code ?? status.value) : status
  const normalized = String(source || 'unknown').toLowerCase()
  const fallback = fallbackLabel || (isStructured(status) ? status.label : '') || scalarText(source)
  const [label, type] = STATUS_MAP[normalized] || [fallback || '未知', 'info']
  return { status: normalized, label, type }
}

export function fieldLabel(key, fallbackLabel = '') {
  const normalized = String(key || '').trim()
  if (!normalized) return fallbackLabel
  return FIELD_LABELS[normalized] || fallbackLabel || normalized.replaceAll('_', ' ')
}

export function roleLabel(value) {
  return ROLE_LABELS[String(value || '').toLowerCase()] || scalarText(value)
}

export function memoryTypeLabel(value) {
  return MEMORY_TYPE_LABELS[String(value || '').toLowerCase()] || scalarText(value)
}

export function formatReadableValue(value, options = {}) {
  return renderReadableValue(value, '', { depth: 0, maxDepth: options.maxDepth ?? 6, seen: new WeakSet() })
}

export function readableText(value, limit = 0) {
  const text = formatReadableValue(value).replaceAll('**', '').replaceAll('`', '').replace(/\s+/g, ' ').trim()
  if (limit > 0 && text.length > limit) return `${text.slice(0, limit)}…`
  return text
}

export function readableTitle(value, fallback = '未命名记录') {
  return titleCandidate(value) || fallback
}

export function serializeRawJson(value) {
  try {
    return JSON.stringify(value === undefined ? null : value, null, 2) || 'null'
  } catch {
    return formatReadableValue(value)
  }
}

export function formatDateTime(value, options = {}) {
  if (!value) return '-'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: options.seconds === false ? undefined : '2-digit',
    hour12: false,
  }).format(date).replaceAll('/', '-')
}

export function localDateTimeToIso(value) {
  if (!value) return ''
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toISOString()
}

export function compactNumber(value) {
  const number = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { notation: number >= 10000 ? 'compact' : 'standard' }).format(number)
}

export function displayValue(value) {
  return formatReadableValue(value)
}

export function scopeDisplayName(scope) {
  const name = readableText(scope?.name)
  if (name && name !== '-' && !/^\?+$/.test(name)) return name
  if (scope?.is_default || scope?.scope_key === 'default') return '默认 Scope'
  return String(scope?.scope_key || scope?.id || '未命名 Scope')
}
