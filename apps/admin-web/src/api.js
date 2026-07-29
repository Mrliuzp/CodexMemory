const base = import.meta.env.VITE_ADMIN_API_BASE || '/api/admin/v1'

async function request(path, options = {}, params = {}) {
  const token = localStorage.getItem('codex-memory-admin-token') || ''
  const url = new URL(`${base}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => value !== undefined && value !== '' && url.searchParams.set(key, value))
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const headers = { Accept: 'application/json', ...(options.body && !isFormData ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.error?.message || `请求失败（${response.status}）`)
    error.status = response.status
    error.code = payload.error?.code
    error.meta = payload.meta
    error.requestId = payload.request_id
    throw error
  }
  return payload
}

export function adminGet(path, params = {}) {
  return request(path, {}, params)
}

export function adminLogin(username, password) {
  return request('/login', { method: 'POST', body: JSON.stringify({ username, password }) })
}

export function adminPost(path, body, params = {}) {
  return request(path, { method: 'POST', body: JSON.stringify(body) }, params)
}

export function adminUpload(path, file, params = {}) {
  const form = new FormData()
  form.append('file', file)
  return request(path, { method: 'POST', body: form }, params)
}

export function adminPut(path, body, params = {}) {
  return request(path, { method: 'PUT', body: JSON.stringify(body) }, params)
}
