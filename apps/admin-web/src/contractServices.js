import { adminGet, adminPost, adminUpload } from './api'

export const contractStatusOptions = [
  { value: 'proposed', label: '待发布' },
  { value: 'published', label: '已发布' },
  { value: 'superseded', label: '已被替代' },
]

export function compactContractParams(params = {}) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== ''),
  )
}

export function buildContractServiceListParams(filters = {}) {
  return compactContractParams({
    project_key: filters.projectKey,
    status: filters.status,
    keyword: filters.keyword,
    page: filters.page || 1,
    page_size: filters.pageSize || 20,
  })
}

function servicePath(serviceId) {
  return `/contract-services/${encodeURIComponent(serviceId)}`
}

function revisionPath(serviceId, revisionNumber) {
  return `${servicePath(serviceId)}/revisions/${encodeURIComponent(revisionNumber)}`
}

export function unwrapData(payload) {
  return payload && Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : {}
}

function normalizeContractService(value) {
  const source = value?.service && typeof value.service === 'object'
    ? { ...value.service, revisions: value.revisions || [] }
    : { ...(value || {}) }
  const revisions = Array.isArray(source.revisions) ? source.revisions : []
  const current = revisions.find((item) => item.id === source.current_published_revision_id)
  return {
    ...source,
    revisions,
    status: source.status || (source.current_published_revision_id != null ? 'published' : (revisions.length ? 'proposed' : 'empty')),
    published_revision_number: source.published_revision_number ?? current?.revision_number ?? null,
  }
}

export function extractContractServices(payload) {
  const data = unwrapData(payload)
  const services = Array.isArray(data) ? data : (data?.items || data?.services || [])
  return services.map(normalizeContractService)
}

export function extractContractService(payload) {
  return normalizeContractService(unwrapData(payload))
}

export function extractContractProjects(payload) {
  const data = unwrapData(payload)
  return (Array.isArray(data) ? data : []).filter((item) => typeof item?.project_key === 'string' && item.project_key.trim())
}

export function resolveContractProjectKey(projects, principalProjectKey = '') {
  const principal = String(principalProjectKey || '').trim()
  const keys = projects.map((item) => item.project_key)
  if (principal && principal !== '*' && keys.includes(principal)) return principal
  return keys.length === 1 ? keys[0] : ''
}

export function extractContractRevision(payload) {
  return unwrapData(payload)
}

export function getContractRevisionMarkdown(revision) {
  return revision?.markdown_document ?? revision?.markdown ?? ''
}

export function getContractValidation(revision = {}) {
  const validation = revision.validation || revision.validation_result || revision.validation_summary || {}
  return {
    errors: Array.isArray(validation.errors) ? validation.errors : (Array.isArray(revision.validation_errors) ? revision.validation_errors : []),
    warnings: Array.isArray(validation.warnings) ? validation.warnings : (Array.isArray(revision.warnings) ? revision.warnings : []),
  }
}

export function canPublishContractRevision(revision = {}) {
  return revision.status === 'proposed' && Boolean(revision.content_hash) && getContractValidation(revision).errors.length === 0
}

export function isStableServiceKey(value) {
  return /^[a-z0-9][a-z0-9._-]{1,79}$/.test(String(value || '').trim())
}

export function extractPagination(payload, fallbackPage = 1, fallbackPageSize = 20) {
  const meta = payload?.meta || {}
  const pagination = meta.pagination || meta
  return {
    page: Number(pagination.page || fallbackPage),
    pageSize: Number(pagination.page_size || pagination.pageSize || fallbackPageSize),
    total: Number(pagination.total || 0),
    hasNext: Boolean(pagination.has_next ?? pagination.hasNext ?? false),
  }
}

export function listContractServices(params = {}) {
  return adminGet('/contract-services', params)
}

export function listContractProjects() {
  return adminGet('/projects', { page: 1, page_size: 100 })
}

export function createContractService(body) {
  return adminPost('/contract-services', body)
}

export function getContractService(serviceId) {
  return adminGet(servicePath(serviceId))
}

export function uploadContractRevision(serviceId, file) {
  return adminUpload(`${servicePath(serviceId)}/revisions`, file)
}

export function getContractRevision(serviceId, revisionNumber) {
  return adminGet(revisionPath(serviceId, revisionNumber))
}

export function publishContractRevision(serviceId, revisionNumber, expectedContentHash) {
  return adminPost(`${revisionPath(serviceId, revisionNumber)}/publish`, {
    expected_content_hash: expectedContentHash,
  })
}
