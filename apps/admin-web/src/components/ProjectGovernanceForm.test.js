import { createSSRApp, defineComponent, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'
import ProjectGovernanceForm from './ProjectGovernanceForm.vue'

function stub(tag, propNames = []) {
  return defineComponent({
    inheritAttrs: false,
    props: propNames,
    setup(props, { attrs, slots }) {
      return () => h(tag, {
        ...attrs,
        disabled: props.disabled ? true : undefined,
        'data-value': props.modelValue === undefined ? undefined : String(props.modelValue),
      }, slots.default?.())
    },
  })
}

const components = {
  'el-alert': defineComponent({
    props: ['title'],
    setup: (props, { slots }) => () => h('div', { role: 'alert' }, [props.title, slots.default?.()]),
  }),
  'el-button': stub('button', ['disabled', 'loading', 'type']),
  'el-input-number': stub('input', ['modelValue', 'disabled', 'min', 'max', 'step', 'precision']),
  'el-option': defineComponent({
    inheritAttrs: false,
    props: ['label', 'value', 'disabled'],
    setup: (props) => () => h('option', { value: props.value, disabled: props.disabled ? true : undefined }, props.label),
  }),
  'el-select': stub('select', ['modelValue', 'disabled']),
  'el-switch': stub('button', ['modelValue', 'disabled', 'activeText', 'inactiveText']),
}

async function renderForm(props = {}) {
  const app = createSSRApp({ render: () => h(ProjectGovernanceForm, props) })
  Object.entries(components).forEach(([name, component]) => app.component(name, component))
  return renderToString(app)
}

describe('项目治理表单', () => {
  it('显示策略字段、回填值和固定边界', async () => {
    const html = await renderForm({
      projectKey: 'demo',
      canEdit: true,
      policy: { enabled: true, auto_publish_enabled: true, strategy: 'auto_publish', min_confidence: 0.73 },
      flags: { initialized: true, flags: { memory_v11_enabled: true } },
    })
    expect(html).toContain('策略启用')
    expect(html).toContain('自动发布启用')
    expect(html).toContain('执行模式')
    expect(html).toContain('最小可信度阈值')
    expect(html).toContain('L1 / project')
    expect(html).toContain('data-value="0.73"')
    expect(html).toContain('V1.1 记忆管线')
    expect(html).not.toContain('LLM Shadow')
  })

  it('未启用策略时禁用自动发布和执行模式选择', async () => {
    const html = await renderForm({
      projectKey: 'demo',
      canEdit: true,
      policy: { enabled: false, auto_publish_enabled: false, strategy: 'manual_review', min_confidence: 0.8 },
      flags: { initialized: true, flags: {} },
    })
    expect(html).toContain('aria-label="自动发布启用" disabled')
    expect(html).toContain('aria-label="执行模式" disabled')
    expect(html).toContain('value="auto_publish" disabled')
  })

  it('展示后端返回的中文错误，并将调试 JSON 放在折叠区', async () => {
    const html = await renderForm({
      projectKey: 'demo',
      canEdit: true,
      policy: { enabled: true, auto_publish_enabled: false, strategy: 'manual_review', min_confidence: 0.8 },
      flags: { initialized: true, flags: {} },
      error: {
        status: 422,
        code: 'decision_policy_invalid',
        message: '决策策略无效：未启用自动发布时不能选择自动发布模式',
        requestId: 'req-422',
        payload: { error: { code: 'decision_policy_invalid', message: '决策策略无效：未启用自动发布时不能选择自动发布模式' }, request_id: 'req-422' },
      },
    })
    expect(html).toContain('决策策略无效：未启用自动发布时不能选择自动发布模式')
    expect(html).toContain('<details')
    expect(html).toContain('req-422')
    expect(html).not.toContain('项目配置读取或保存失败')
  })
})
