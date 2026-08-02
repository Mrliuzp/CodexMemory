const STATUS_MAP = {
  active: ['活跃', 'success'],
  healthy: ['正常', 'success'],
  ok: ['正常', 'success'],
  ready: ['就绪', 'success'],
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
  retry_wait: ['等待重试', 'warning'],
  uncertain: ['不确定', 'warning'],
  degraded: ['已降级', 'warning'],
  failed: ['失败', 'danger'],
  error: ['异常', 'danger'],
  dead: ['死信', 'danger'],
  rejected: ['已拒绝', 'danger'],
  cancelled: ['已取消', 'info'],
  inactive: ['未启用', 'info'],
  empty: ['暂无版本', 'info'],
  superseded: ['已替代', 'info'],
  draft: ['草稿', 'info'],
  finalized: ['已封版', 'success'],
  restricted: ['受限', 'info'],
  rolled_back: ['已回滚', 'info'],
  unknown: ['未知', 'info'],
}

export function statusMeta(status, fallbackLabel = '') {
  const normalized = String(status || 'unknown').toLowerCase()
  const [label, type] = STATUS_MAP[normalized] || [fallbackLabel || String(status || '未知'), 'info']
  return { status: normalized, label, type }
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
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
