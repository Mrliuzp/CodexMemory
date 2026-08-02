import { describe, expect, it } from 'vitest'
import router from './router'

describe('管理后台导航', () => {
  it('保留正式后台的核心入口', () => {
    const paths = router.getRoutes().map((route) => route.path)
    expect(paths).toEqual(expect.arrayContaining(['/dashboard', '/projects', '/records', '/task-runs', '/task-runs/:id']))
  })

  it('使用懒加载页面并提供权限状态页', () => {
    const dashboard = router.getRoutes().find((route) => route.name === 'dashboard')
    const paths = router.getRoutes().map((route) => route.path)
    expect(typeof dashboard.components.default).toBe('function')
    expect(paths).toEqual(expect.arrayContaining(['/system-status', '/forbidden', '/:pathMatch(.*)*']))
  })

  it('使用中文只读任务报告路由', () => {
    const taskRuns = router.getRoutes().find((route) => route.name === 'task-runs')
    const detail = router.getRoutes().find((route) => route.name === 'task-run-detail')
    expect(taskRuns.meta.label).toBe('任务报告')
    expect(detail.meta.label).toBe('任务运行详情')
  })

  it('提供接口契约服务和 Revision 详情路由', () => {
    const paths = router.getRoutes().map((route) => route.path)
    expect(paths).toEqual(expect.arrayContaining([
      '/contract-services',
      '/contract-services/:serviceId',
      '/contract-services/:serviceId/revisions/:revisionNumber',
    ]))
    expect(router.getRoutes().find((route) => route.name === 'contract-services').meta.label).toBe('接口契约')
    expect(router.getRoutes().find((route) => route.name === 'contract-revision-detail').meta.label).toBe('接口契约 Revision 详情')
  })
})
