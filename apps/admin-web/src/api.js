const base = import.meta.env.VITE_ADMIN_API_BASE || '/api/admin'

async function request(path, options = {}, params = {}) {
  const token = localStorage.getItem('codex-memory-admin-token') || ''
  const url = new URL(`${base}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => value !== undefined && value !== '' && url.searchParams.set(key, value))
  const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.error?.message || `请求失败（${response.status}）`)
    error.status = response.status
    error.code = payload.error?.code
    throw error
  }
  return payload
}

export function adminGet(path, params = {}) {
  return request(path, {}, params)
}

export function adminPost(path, body) {
  return request(path, { method: 'POST', body: JSON.stringify(body) })
}

export function adminLogin(username, password) {
  return adminPost('/login', { username, password })
}

export function getMe() {
  return adminGet('/me')
}

export function getDashboard() {
  return adminGet('/dashboard')
}

export function getSystemStatus() {
  return adminGet('/system/status')
}

export function getProjectArchiveStatus(projectKey) {
  return adminGet('/projects/' + projectKey + '/archive-status')
}

export function getRawRecords(params) {
  return adminGet('/raw-records', params)
}

export function getOutboxEvents(params) {
  return adminGet('/outbox-events', params)
}

export function getRetrievalAudits(params) {
  return adminGet('/retrieval-audits', params)
}

export function getAuditEvents(params) {
  return adminGet('/audit-events', params)
}

export function getCandidates(params) {
  return adminGet('/candidates', params)
}

export function getMemories(params) {
  return adminGet('/memories', params)
}

export function getJobs(params) {
  return adminGet('/jobs', params)
}

export function getProjects(params) {
  return adminGet('/projects', params)
}