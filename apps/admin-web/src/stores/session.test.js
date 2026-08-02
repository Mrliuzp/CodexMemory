import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionStore } from './session'

function success(data) {
  return { ok: true, status: 200, headers: { get: () => null }, json: vi.fn().mockResolvedValue({ data }) }
}

describe('管理会话', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('进入保护页面时只恢复一次真实身份', async () => {
    localStorage.setItem('codex-memory-admin-token', 'session-token')
    localStorage.setItem('codex-memory-admin-me', JSON.stringify({ display_name: '旧缓存', permissions: ['admin'] }))
    const fetchMock = vi.fn().mockResolvedValue(success({ display_name: '真实管理员', auth_type: 'session', permissions: ['admin', 'read'] }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useSessionStore()

    expect(store.restored).toBe(false)
    await Promise.all([store.ensureSession(), store.ensureSession()])

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(store.displayName).toBe('真实管理员')
    expect(store.isAdmin).toBe(true)
    expect(store.hasPermission('read')).toBe(true)
  })

  it('退出后清理令牌和缓存身份', () => {
    const store = useSessionStore()
    store.setToken('session-token')
    store.setMe({ permissions: ['read'] })
    store.logout()
    expect(store.token).toBe('')
    expect(store.me).toBeNull()
    expect(localStorage.getItem('codex-memory-admin-token')).toBeNull()
  })

  it('管理员权限是读取与运维权限的超集', () => {
    const store = useSessionStore()
    store.setMe({ permissions: ['admin'] })
    expect(store.hasPermission('read')).toBe(true)
    expect(store.hasPermission('operations_read')).toBe(true)
  })
})
