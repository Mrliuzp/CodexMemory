import { adminGet, adminPost, adminUpload } from './api'

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
