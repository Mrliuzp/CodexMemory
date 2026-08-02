import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adminGet } from './api'
import {
  buildTaskRunListParams,
  extractPagination,
  extractTaskRun,
  extractTaskRuns,
  getTaskRun,
  getTaskRunReport,
  listTaskRuns,
  taskRunProjectLabel,
  taskRunPrompt,
  unwrapData,
} from './taskRuns'

vi.mock('./api', () => ({ adminGet: vi.fn() }))

describe('任务报告只读数据访问', () => {
  beforeEach(() => vi.clearAllMocks())

  it('只通过三个 GET 路径读取任务运行和报告', async () => {
    adminGet.mockResolvedValue({ data: [] })
    await listTaskRuns({ project_key: 'demo', page: 1, page_size: 20 })
    await getTaskRun('run/1')
    await getTaskRunReport('run/1', 2)

    expect(adminGet).toHaveBeenNthCalledWith(1, '/task-runs', { project_key: 'demo', page: 1, page_size: 20 })
    expect(adminGet).toHaveBeenNthCalledWith(2, '/task-runs/run%2F1')
    expect(adminGet).toHaveBeenNthCalledWith(3, '/task-runs/run%2F1/reports/2')
  })

  it('只解包统一 data envelope 并读取统一 meta 分页', () => {
    const payload = { data: [{ id: 1 }], meta: { page: 2, page_size: 10, total: 21, has_next: true } }
    expect(extractTaskRuns(payload)).toEqual([{ id: 1 }])
    expect(extractPagination(payload)).toEqual({ page: 2, pageSize: 10, total: 21, hasNext: true })
    expect(unwrapData({ id: 1 })).toEqual({})
  })

  it('按后端详情契约读取报告版本、事件和 Git 基线', () => {
    const taskRun = extractTaskRun({
      data: {
        id: 9,
        current_report_revision: 2,
        git_baseline: { branch: 'main', available: true },
        events: [{ sequence_no: 1, truncated: false }],
        reports: [{ revision: 2, report_kind: 'final', uncertain: false, truncated: false }],
      },
    })
    expect(taskRun.current_report_revision).toBe(2)
    expect(taskRun.git_baseline.branch).toBe('main')
    expect(taskRun.events[0].sequence_no).toBe(1)
    expect(taskRun.reports[0].report_kind).toBe('final')
  })

  it('把页面筛选转换为可复制的查询参数并移除空值', () => {
    expect(buildTaskRunListParams({
      projectKey: 'demo',
      status: 'failed',
      uncertain: 'true',
      keyword: '迁移',
      startedFrom: '2026-08-01T00:00:00',
      startedTo: '',
      sort: 'started_at',
      order: 'desc',
      page: 2,
      pageSize: 50,
    })).toEqual({
      project_key: 'demo',
      status: 'failed',
      uncertain: 'true',
      keyword: '迁移',
      started_from: '2026-08-01T00:00:00',
      sort: 'started_at',
      order: 'desc',
      page: 2,
      page_size: 50,
    })
  })

  it('优先展示后端提供的项目键和脱敏摘要', () => {
    const row = { project_key: 'erp', project_id: 7, prompt_excerpt: '修复导入任务', prompt_truncated: true }
    expect(taskRunProjectLabel(row)).toBe('erp')
    expect(taskRunPrompt(row)).toBe('修复导入任务')
    expect(taskRunProjectLabel({ project_id: 7 })).toBe('项目 #7')
  })

})
