import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adminGet } from './api'
import {
  extractPagination,
  extractTaskRun,
  extractTaskRuns,
  getTaskRun,
  getTaskRunReport,
  listTaskRuns,
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

})
