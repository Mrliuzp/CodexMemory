import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const sourceDirectory = dirname(fileURLToPath(import.meta.url))
const mainSource = readFileSync(join(sourceDirectory, 'main.js'), 'utf8')

function vueFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) return vueFiles(path)
    return entry.name.endsWith('.vue') ? [path] : []
  })
}

function componentName(tagName) {
  return tagName
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
}

describe('Element Plus 启动注册', () => {
  it('注册模板使用的全部组件并引入对应样式', () => {
    const usedTags = new Set(vueFiles(sourceDirectory).flatMap((path) => (
      [...readFileSync(path, 'utf8').matchAll(/<el-([a-z0-9-]+)/g)].map((match) => `el-${match[1]}`)
    )))
    const registry = mainSource.match(/const components = \[([\s\S]*?)\]/)?.[1] || ''
    const registeredComponents = new Set(registry.match(/\bEl[A-Z][A-Za-z]+\b/g) || [])

    for (const tag of usedTags) {
      expect(registeredComponents, `${tag} 未在 main.js 注册`).toContain(componentName(tag))
      expect(mainSource, `${tag} 未引入 Element Plus 样式`).toContain(`element-plus/theme-chalk/${tag}.css`)
    }
  })
})
