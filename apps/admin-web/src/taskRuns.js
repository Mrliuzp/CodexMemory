import { adminGet } from './api'

export const taskRunStatusLabels = {
  pending: '待开始',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
  open: '进行中',
  closed: '已关闭',
}

export const taskRunStatusOptions = Object.entries(taskRunStatusLabels).map(([value, label]) => ({ value, label }))

export function compactParams(params = {}) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== ''),
  )
}

export function buildTaskRunListParams(filters = {}) {
  return compactParams({
    project_key: filters.projectKey,
    status: filters.status,
    uncertain: filters.uncertain,
    keyword: filters.keyword,
    started_from: filters.startedFrom,
    started_to: filters.startedTo,
    sort: filters.sort,
    order: filters.order,
    page: filters.page || 1,
    page_size: filters.pageSize || 20,
  })
}

export function listTaskRuns(params = {}) {
  return adminGet('/task-runs', params)
}

export function getTaskRun(id) {
  return adminGet(`/task-runs/${encodeURIComponent(id)}`)
}

export function getTaskRunReport(id, revision) {
  return adminGet(`/task-runs/${encodeURIComponent(id)}/reports/${encodeURIComponent(revision)}`)
}

export function unwrapData(payload) {
  return payload?.data ?? {}
}

export function extractTaskRuns(payload) {
  const data = unwrapData(payload)
  return Array.isArray(data) ? data : []
}

export function extractPagination(payload, page = 1, pageSize = 50) {
  const meta = payload?.meta || {}
  return {
    page: Number(meta.page ?? page),
    pageSize: Number(meta.page_size ?? pageSize),
    total: Number(meta.total ?? 0),
    hasNext: Boolean(meta.has_next),
  }
}

export function extractTaskRun(payload) {
  const data = unwrapData(payload)
  return {
    ...data,
    git_baseline: data?.git_baseline || {},
    events: Array.isArray(data?.events) ? data.events : [],
    reports: Array.isArray(data?.reports) ? data.reports : [],
  }
}

export function taskRunProjectLabel(taskRun = {}) {
  return taskRun.project_key || (taskRun.project_id != null ? `项目 #${taskRun.project_id}` : '未标记项目')
}

export function taskRunPrompt(taskRun = {}) {
  return String(taskRun.prompt_excerpt || taskRun.prompt_summary || '').trim()
}
