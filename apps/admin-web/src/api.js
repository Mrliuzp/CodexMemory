const base = import.meta.env.VITE_ADMIN_API_BASE || '/api/admin/v1'

export async function adminGet(path, params = {}) {
  const token = localStorage.getItem('codex-memory-admin-token') || ''
  const url = new URL(`${base}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => value !== undefined && value !== '' && url.searchParams.set(key, value))
  const response = await fetch(url, { headers: { Accept: 'application/json', Authorization: `Bearer ${token}` } })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error?.message || `请求失败 (${response.status})`)
  return payload
}
