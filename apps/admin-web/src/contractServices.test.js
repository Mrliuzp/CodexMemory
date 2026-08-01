import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adminGet, adminPost, adminUpload } from './api'
import {
  extractContractRevision,
  extractContractServices,
  extractPagination,
  getContractRevisionMarkdown,
  getContractRevision,
  getContractService,
  listContractServices,
  publishContractRevision,
  uploadContractRevision,
} from './contractServices'

vi.mock('./api', () => ({ adminGet: vi.fn(), adminPost: vi.fn(), adminUpload: vi.fn() }))

describe('接口契约数据访问', () => {
  beforeEach(() => vi.clearAllMocks())

  it('使用统一 envelope 解包服务列表、Revision 和分页元数据', () => {
    const payload = { data: [{ id: 'service/1' }], meta: { page: 2, page_size: 10, total: 21, has_next: true } }
    expect(extractContractServices(payload)).toEqual([{ id: 'service/1' }])
    expect(extractContractRevision({ data: { revision_number: 1 } })).toEqual({ revision_number: 1 })
    expect(extractPagination(payload)).toEqual({ page: 2, pageSize: 10, total: 21, hasNext: true })
  })

  it('优先显示新 Markdown 字段并兼容旧字段', () => {
    expect(getContractRevisionMarkdown({ markdown_document: '# 新文档', markdown: '# 旧文档' })).toBe('# 新文档')
    expect(getContractRevisionMarkdown({ markdown: '# 旧文档' })).toBe('# 旧文档')
    expect(getContractRevisionMarkdown({})).toBe('')
  })

  it('所有服务和 Revision 路径都编码资源标识', async () => {
    adminGet.mockResolvedValue({ data: {} })
    await listContractServices({ project_key: 'demo', page: 1, page_size: 20 })
    await getContractService('service/1')
    await getContractRevision('service/1', 2)
    await uploadContractRevision('service/1', new Blob(['{}'], { type: 'application/json' }))
    await publishContractRevision('service/1', 2, 'abc123')

    expect(adminGet).toHaveBeenNthCalledWith(1, '/contract-services', { project_key: 'demo', page: 1, page_size: 20 })
    expect(adminGet).toHaveBeenNthCalledWith(2, '/contract-services/service%2F1')
    expect(adminGet).toHaveBeenNthCalledWith(3, '/contract-services/service%2F1/revisions/2')
    expect(adminUpload).toHaveBeenCalledWith('/contract-services/service%2F1/revisions', expect.any(Blob))
    expect(adminPost).toHaveBeenCalledWith('/contract-services/service%2F1/revisions/2/publish', { expected_content_hash: 'abc123' })
  })
})
