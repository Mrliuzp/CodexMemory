import { createPinia, setActivePinia } from 'pinia'
import { createSSRApp, defineComponent, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { beforeEach, describe, expect, it } from 'vitest'
import CodexAuthCard from './CodexAuthCard.vue'
import { useSessionStore } from '../stores/session'

const ButtonStub = defineComponent({
  props: ['disabled'],
  setup: (props, { slots }) => () => h('button', { disabled: props.disabled }, slots.default?.()),
})

const TagStub = defineComponent({
  setup: (_, { slots }) => () => h('span', slots.default?.()),
})

async function render(props, permissions) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore(pinia).setMe({ permissions })
  const app = createSSRApp({ render: () => h(CodexAuthCard, { ...props }) })
  app.use(pinia)
  app.component('el-button', ButtonStub)
  app.component('el-tag', TagStub)
  return renderToString(app)
}

describe('CodexAuthCard', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('仅显示有限状态和管理员登录操作，不渲染账号或认证地址', async () => {
    const html = await render({ status: 'ready' }, ['admin'])

    expect(html).toContain('已登录')
    expect(html).toContain('更换账号')
    expect(html).toContain('取消')
    expect(html).toContain('重新检查')
    expect(html).not.toContain('auth.openai.com')
    expect(html).not.toContain('token')
  })

  it('只读用户看得到状态但看不到操作按钮', async () => {
    const html = await render({ status: 'not_logged_in' }, ['read'])

    expect(html).toContain('未登录')
    expect(html).toContain('只读访问')
    expect(html).not.toContain('开始登录')
    expect(html).not.toContain('重新检查')
  })
})
