import { adminGet } from './api'

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
  return payload?.data ?? payload ?? {}
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
  }
}

export function extractTaskRun(payload) {
  return unwrapData(payload)
}
