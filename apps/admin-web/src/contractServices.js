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

export function extractContractServices(payload) {
  const data = unwrapData(payload)
  if (Array.isArray(data)) return data
  return data?.items || data?.services || []
}

export function extractContractService(payload) {
  return unwrapData(payload)
}

export function extractContractRevision(payload) {
  return unwrapData(payload)
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
