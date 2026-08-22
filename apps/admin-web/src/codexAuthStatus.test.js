import { describe, expect, it } from 'vitest'
import { getCodexAuthStatusMessage } from './codexAuthStatus'

describe('getCodexAuthStatusMessage', () => {
  it('旧接口只返回错误状态时使用失败说明', () => {
    expect(getCodexAuthStatusMessage('error', '', '')).toBe('认证状态检查失败，请点击“重新检查”。')
  })

  it('优先使用后端消息，并兼容仅包含原因的响应', () => {
    expect(getCodexAuthStatusMessage('error', 'coordinator_unreachable', '')).toBe('认证协调器不可达，请确认协调器正在运行后再重试。')
    expect(getCodexAuthStatusMessage('error', 'coordinator_unreachable', '自定义安全提示')).toBe('自定义安全提示')
  })
})
