import { describe, expect, it } from 'vitest'
import {
  fieldLabel,
  formatReadableValue,
  memoryTypeLabel,
  readableText,
  readableTitle,
  roleLabel,
  serializeRawJson,
  statusMeta,
} from './format'

describe('前端显示格式化', () => {
  it('提供常见字段和状态的中文映射', () => {
    expect(fieldLabel('created_at')).toBe('创建时间')
    expect(fieldLabel('model_confidence')).toBe('模型置信度')
    expect(statusMeta('pending_review')).toMatchObject({ label: '待审核', type: 'warning' })
    expect(roleLabel('assistant')).toBe('助手')
    expect(memoryTypeLabel('imported_reference')).toBe('导入参考')
  })

  it('递归渲染对象和数组时不会退化为 object Object', () => {
    const value = {
      title: { text: '对象标题' },
      content: { text: '对象内容', tags: ['规则', { source_name: '资料.md' }] },
      status: 'pending_review',
    }
    const readable = formatReadableValue(value)

    expect(readable).toContain('标题')
    expect(readable).toContain('对象内容')
    expect(readable).toContain('待审核')
    expect(readable).not.toContain('[object Object]')
    expect(readableText(value)).not.toContain('[object Object]')
  })

  it('标题优先使用对象中的可读字段，并为全空对象提供中文兜底', () => {
    expect(readableTitle({ title: { text: '嵌套标题' }, content: { text: '正文' } })).toBe('嵌套标题')
    expect(readableTitle({ name: '备用名称' })).toBe('备用名称')
    expect(readableTitle({})).toBe('未命名记录')
    expect(formatReadableValue(null)).toBe('-')
    expect(formatReadableValue(undefined)).toBe('-')
  })

  it('原始数据序列化保留 JSON 结构且不写入日志', () => {
    const raw = serializeRawJson({ content: { text: '仅用于界面测试' } })
    expect(raw).toContain('"content"')
    expect(raw).toContain('"text"')
    expect(raw).not.toContain('[object Object]')
  })
})
