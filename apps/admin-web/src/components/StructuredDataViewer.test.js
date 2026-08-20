import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'
import StructuredDataViewer from './StructuredDataViewer.vue'

async function render(value) {
  const app = createSSRApp({ render: () => h(StructuredDataViewer, { value }) })
  return renderToString(app)
}

describe('结构化数据展示', () => {
  it('默认以可读内容展示，并把原始 JSON 放在折叠区域且支持复制', async () => {
    const html = await render({ title: { text: '测试标题' }, content: { text: '测试正文' } })

    expect(html).toContain('测试标题')
    expect(html).toContain('测试正文')
    expect(html).toContain('查看原始数据')
    expect(html).toContain('复制原始数据')
    expect(html).toContain('&quot;content&quot;')
    expect(html).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/)
    expect(html).not.toContain('[object Object]')
  })
})
