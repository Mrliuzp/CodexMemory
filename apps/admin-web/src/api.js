const base = import.meta.env.VITE_ADMIN_API_BASE || '/api/admin/v1'
const TOKEN_KEY = 'codex-memory-admin-token'

const STATUS_MESSAGES = {
  400: '请求内容有误，请检查后重试。',
  401: '登录状态已失效，请重新登录。',
  403: '当前账号没有执行此操作的权限。',
  404: '未找到请求的数据，内容可能已被移除。',
  409: '数据已发生变化，请刷新后重试。',
  413: '文件过大，请压缩或拆分后重试。',
  422: '部分输入不符合要求，请检查表单。',
  429: '请求过于频繁，请稍后重试。',
  500: '服务暂时不可用，请稍后重试。',
  502: '上游服务暂时不可用，请稍后重试。',
  503: '服务正在维护，请稍后重试。',
}

function storageToken() {
  return typeof localStorage === 'undefined' ? '' : localStorage.getItem(TOKEN_KEY) || ''
}

function appendQuery(url, params) {
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    if (Array.isArray(value)) {
      value.forEach((item) => url.searchParams.append(key, String(item)))
      return
    }
    url.searchParams.set(key, String(value))
  })
}

export class AdminApiError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'AdminApiError'
    this.status = options.status || 0
    this.code = options.code || 'request_failed'
    this.meta = options.meta || {}
    this.requestId = options.requestId || ''
    this.cause = options.cause
  }
}

function notifyUnauthorized(error) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('admin:unauthorized', { detail: error }))
  }
}

export function getErrorMessage(error) {
  if (error?.name === 'AbortError') return '请求已取消。'
  return error?.message || '请求失败，请稍后重试。'
}

export async function request(path, options = {}, params = {}) {
  const origin = typeof window === 'undefined' || !window.location?.origin ? 'http://localhost' : window.location.origin
  const url = new URL(`${base}${path}`, origin)
  appendQuery(url, params)
  const token = storageToken()
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const headers = {
    Accept: 'application/json',
    ...(options.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  let response
  try {
    response = await fetch(url, { ...options, headers })
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause
    throw new AdminApiError('无法连接管理 API，请检查服务状态后重试。', { code: 'network_error', cause })
  }

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new AdminApiError(
      payload.error?.message || STATUS_MESSAGES[response.status] || `请求失败（HTTP ${response.status}）。`,
      {
        status: response.status,
        code: payload.error?.code,
        meta: payload.error?.meta || payload.meta,
        requestId: payload.request_id || response.headers.get('x-request-id'),
      },
    )
    if (response.status === 401 && !options.skipUnauthorized) notifyUnauthorized(error)
    throw error
  }
  return payload
}

export function adminGet(path, params = {}) {
  return request(path, {}, params)
}

export function adminLogin(username, password) {
  return request('/login', { method: 'POST', body: JSON.stringify({ username, password }), skipUnauthorized: true })
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

export { TOKEN_KEY }
