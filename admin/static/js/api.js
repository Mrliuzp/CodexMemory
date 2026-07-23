var API = {};

API.base = '/api/admin';

async function apiFetch(path, options) {
  const url = API.base + path;
  const resp = await fetch(url, {
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const body = await resp.json(); detail = body.detail || body.message || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

API.get = function(path) { return apiFetch(path); };
API.post = function(path, body) { return apiFetch(path, { method: 'POST', body: JSON.stringify(body || {}) }); };
API.put = function(path, body) { return apiFetch(path, { method: 'PUT', body: JSON.stringify(body || {}) }); };

API.health = function() { return API.get('/health'); };
API.stats = function() { return API.get('/stats'); };
API.projects = function() { return API.get('/projects'); };
API.project = function(id) { return API.get('/projects/' + id); };
API.jobs = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/jobs' + qs);
};
API.retryJob = function(id) { return API.post('/jobs/' + id + '/retry'); };
API.candidates = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/candidates' + qs);
};
API.reviewCandidate = function(id, decision, reviewer, reason) {
  return API.post('/candidates/' + id + '/review', { decision: decision, reviewer: reviewer, reason: reason });
};
API.profiles = function() { return API.get('/profiles'); };
API.createProfile = function(data) { return API.post('/profiles', data); };
API.flags = function(projectId) { return API.get('/flags/' + projectId); };
API.updateFlags = function(projectId, flags) { return API.put('/flags/' + projectId, { flags: flags }); };
API.memories = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/memories' + qs);
};
API.logs = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/logs' + qs);
};
API.tokenUsage = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/token-usage' + qs);
};
API.auditLogs = function(params) {
  var qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return API.get('/audit-logs' + qs);
};
