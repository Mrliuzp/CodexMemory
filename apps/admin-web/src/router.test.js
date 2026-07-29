import { describe, expect, it } from 'vitest'
import router from './router'

describe('admin navigation', () => {
  it('keeps the P0 read-only entry points addressable', () => {
    const paths = router.getRoutes().map((route) => route.path)
    expect(paths).toEqual(expect.arrayContaining(['/dashboard', '/projects', '/records', '/task-runs', '/task-runs/:id']))
  })

  it('使用中文只读任务报告路由', () => {
    const taskRuns = router.getRoutes().find((route) => route.name === 'task-runs')
    const detail = router.getRoutes().find((route) => route.name === 'task-run-detail')
    expect(taskRuns.meta.label).toBe('任务报告')
    expect(detail.meta.label).toBe('任务运行详情')
  })
})
