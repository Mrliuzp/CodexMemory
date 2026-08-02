import { describe, expect, it, vi } from 'vitest'
import { adminGet, AdminApiError } from './api'

function response(status, payload, requestId = '') {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name === 'x-request-id' ? requestId : null },
    json: vi.fn().mockResolvedValue(payload),
  }
}

describe('管理 API 客户端', () => {
  it('携带令牌并保留分页查询参数', async () => {
    localStorage.setItem('codex-memory-admin-token', 'token-value')
    const fetchMock = vi.fn().mockResolvedValue(response(200, { data: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await adminGet('/projects', { page: 2, page_size: 20, keyword: '' })

    const [url, options] = fetchMock.mock.calls[0]
    expect(url.searchParams.get('page')).toBe('2')
    expect(url.searchParams.get('page_size')).toBe('20')
    expect(url.searchParams.has('keyword')).toBe(false)
    expect(options.headers.Authorization).toBe('Bearer token-value')
  })

  it('在 401 时发出会话失效事件并保留请求 ID', async () => {
    const eventTarget = new EventTarget()
    Object.defineProperty(globalThis, 'window', { configurable: true, value: eventTarget })
    const listener = vi.fn()
    window.addEventListener('admin:unauthorized', listener, { once: true })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(401, { error: { code: 'invalid_session' }, request_id: 'req-401' })))

    await expect(adminGet('/me')).rejects.toMatchObject({ status: 401, requestId: 'req-401' })
    expect(listener).toHaveBeenCalledOnce()
    expect(listener.mock.calls[0][0].detail).toBeInstanceOf(AdminApiError)
    delete globalThis.window
  })

  it('将网络故障转换为可行动的中文错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')))
    await expect(adminGet('/dashboard')).rejects.toMatchObject({ code: 'network_error', message: '无法连接管理 API，请检查服务状态后重试。' })
  })
})
