import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adminGet, adminPost, adminUpload } from './api'
import {
  buildContractServiceListParams,
  canPublishContractRevision,
  extractContractRevision,
  extractContractProjects,
  extractContractService,
  extractContractServices,
  extractPagination,
  getContractRevisionMarkdown,
  getContractValidation,
  getContractRevision,
  getContractService,
  listContractServices,
  listContractProjects,
  publishContractRevision,
  resolveContractProjectKey,
  isStableServiceKey,
  uploadContractRevision,
} from './contractServices'

vi.mock('./api', () => ({ adminGet: vi.fn(), adminPost: vi.fn(), adminUpload: vi.fn() }))

describe('接口契约数据访问', () => {
  beforeEach(() => vi.clearAllMocks())

  it('使用统一 envelope 解包服务列表、Revision 和分页元数据', () => {
    const payload = { data: [{ id: 'service/1' }], meta: { page: 2, page_size: 10, total: 21, has_next: true } }
    expect(extractContractServices(payload)).toEqual([{
      id: 'service/1',
      revisions: [],
      status: 'empty',
      published_revision_number: null,
    }])
    expect(extractContractRevision({ data: { revision_number: 1 } })).toEqual({ revision_number: 1 })
    expect(extractPagination(payload)).toEqual({ page: 2, pageSize: 10, total: 21, hasNext: true })
  })

  it('将后端嵌套的服务详情归一化为页面使用的扁平结构', () => {
    const payload = {
      data: {
        service: { id: 7, name: '订单服务', current_published_revision_id: 12 },
        revisions: [{ id: 12, revision_number: 3, status: 'published' }],
      },
    }
    expect(extractContractService(payload)).toMatchObject({
      id: 7,
      name: '订单服务',
      status: 'published',
      published_revision_number: 3,
      revisions: [{ id: 12, revision_number: 3, status: 'published' }],
    })
  })

  it('优先显示新 Markdown 字段并兼容旧字段', () => {
    expect(getContractRevisionMarkdown({ markdown_document: '# 新文档', markdown: '# 旧文档' })).toBe('# 新文档')
    expect(getContractRevisionMarkdown({ markdown: '# 旧文档' })).toBe('# 旧文档')
    expect(getContractRevisionMarkdown({})).toBe('')
  })

  it('使用真实项目键初始化筛选，不把数据库项目 ID 当作 project_key', () => {
    const projects = extractContractProjects({ data: [
      { id: 1, project_key: 'codex-memory', name: 'Codex Memory' },
      { id: 2, project_key: 'demo', name: '演示项目' },
    ] })

    expect(resolveContractProjectKey(projects, 'codex-memory')).toBe('codex-memory')
    expect(resolveContractProjectKey(projects, '*')).toBe('')
    expect(resolveContractProjectKey([{ id: 1, project_key: 'codex-memory' }], '*')).toBe('codex-memory')
  })

  it('所有服务和 Revision 路径都编码资源标识', async () => {
    adminGet.mockResolvedValue({ data: {} })
    await listContractProjects()
    await listContractServices({ project_key: 'demo', page: 1, page_size: 20 })
    await getContractService('service/1')
    await getContractRevision('service/1', 2)
    await uploadContractRevision('service/1', new Blob(['{}'], { type: 'application/json' }))
    await publishContractRevision('service/1', 2, 'abc123')

    expect(adminGet).toHaveBeenNthCalledWith(1, '/projects', { page: 1, page_size: 100 })
    expect(adminGet).toHaveBeenNthCalledWith(2, '/contract-services', { project_key: 'demo', page: 1, page_size: 20 })
    expect(adminGet).toHaveBeenNthCalledWith(3, '/contract-services/service%2F1')
    expect(adminGet).toHaveBeenNthCalledWith(4, '/contract-services/service%2F1/revisions/2')
    expect(adminUpload).toHaveBeenCalledWith('/contract-services/service%2F1/revisions', expect.any(Blob))
    expect(adminPost).toHaveBeenCalledWith('/contract-services/service%2F1/revisions/2/publish', { expected_content_hash: 'abc123' })
  })

  it('构建项目、状态与关键词筛选并忽略空值', () => {
    expect(buildContractServiceListParams({ projectKey: 'erp', status: 'proposed', keyword: 'pets', page: 2, pageSize: 50 })).toEqual({
      project_key: 'erp',
      status: 'proposed',
      keyword: 'pets',
      page: 2,
      page_size: 50,
    })
    expect(buildContractServiceListParams({ page: 1, pageSize: 20 })).toEqual({ page: 1, page_size: 20 })
  })

  it('只允许校验通过且状态为 proposed 的 Revision 发布', () => {
    const valid = { status: 'proposed', content_hash: 'abc', validation: { errors: [], warnings: ['复核说明'] } }
    expect(getContractValidation(valid)).toEqual({ errors: [], warnings: ['复核说明'] })
    expect(canPublishContractRevision(valid)).toBe(true)
    expect(canPublishContractRevision({ ...valid, status: 'published' })).toBe(false)
    expect(canPublishContractRevision({ ...valid, validation: { errors: ['缺少 operationId'] } })).toBe(false)
  })

  it('校验稳定 service_key 的格式', () => {
    expect(isStableServiceKey('order-api.v1')).toBe(true)
    expect(isStableServiceKey('订单服务')).toBe(false)
    expect(isStableServiceKey('A')).toBe(false)
  })
})
