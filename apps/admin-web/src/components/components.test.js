import { createPinia, setActivePinia } from 'pinia'
import { createSSRApp, defineComponent, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { beforeEach, describe, expect, it } from 'vitest'
import ConfirmActionDialog from './ConfirmActionDialog.vue'
import PageHeader from './PageHeader.vue'
import PermissionGate from './PermissionGate.vue'
import { useSessionStore } from '../stores/session'
import { renderSafeMarkdown } from '../utils/markdown'
import { scopeDisplayName } from '../utils/format'

const dialogStubs = {
  ElDialog: defineComponent({ props: ['modelValue'], setup: (props, { slots }) => () => props.modelValue ? h('section', [slots.default?.(), slots.footer?.()]) : null }),
  ElFormItem: defineComponent({ setup: (_, { slots }) => () => h('label', slots.default?.()) }),
  ElInput: defineComponent({ props: ['modelValue'], setup: (props) => () => h('textarea', { value: props.modelValue }) }),
  ElButton: defineComponent({ props: ['disabled'], setup: (props, { slots }) => () => h('button', { disabled: props.disabled }, slots.default?.()) }),
}

async function render(component, { props = {}, slots = {}, components = {}, pinia = createPinia() } = {}) {
  const app = createSSRApp({ render: () => h(component, props, slots) })
  app.use(pinia)
  Object.entries(components).forEach(([name, value]) => app.component(name, value))
  return renderToString(app)
}

describe('后台通用组件', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('PageHeader 保持标题、说明和操作区语义', async () => {
    const html = await render(PageHeader, {
      props: { eyebrow: '运行监控', title: '系统状态', description: '查看关键链路。' },
      slots: { actions: () => h('button', '刷新') },
    })
    expect(html).toContain('<h2>系统状态</h2>')
    expect(html).toContain('查看关键链路。')
    expect(html).toContain('page-header__actions')
    expect(html).toContain('刷新')
  })

  it('PermissionGate 仅向管理员显示写操作', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useSessionStore(pinia)
    store.setMe({ permissions: ['read'] })
    const slots = { default: () => h('button', '发布'), fallback: () => h('span', '只读') }
    expect(await render(PermissionGate, { slots, pinia })).toContain('只读')
    store.setMe({ permissions: ['read', 'admin'] })
    expect(await render(PermissionGate, { slots, pinia })).toContain('发布')
  })

  it('需要原因的确认弹窗会阻止空原因提交', async () => {
    const emptyHtml = await render(ConfirmActionDialog, {
      props: { modelValue: true, requiresReason: true, confirmText: '确认回滚' },
      components: dialogStubs,
    })
    const reasonHtml = await render(ConfirmActionDialog, {
      props: { modelValue: true, reason: '验证后执行回滚', requiresReason: true, confirmText: '确认回滚' },
      components: dialogStubs,
    })
    expect(emptyHtml).toMatch(/<button[^>]*disabled[^>]*>确认回滚<\/button>/)
    expect(reasonHtml).toMatch(/<button(?![^>]*disabled)[^>]*>确认回滚<\/button>/)
  })

  it('Markdown 预览会转义不安全 HTML', () => {
    const html = renderSafeMarkdown('# 标题\n<script>alert(1)</script>\n[文档](https://example.com)')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('Scope 名称遇到历史问号占位符时显示中文兜底', () => {
    expect(scopeDisplayName({ scope_key: 'default', name: '?????', is_default: true })).toBe('默认 Scope')
    expect(scopeDisplayName({ scope_key: 'team', name: '团队知识' })).toBe('团队知识')
  })
})
