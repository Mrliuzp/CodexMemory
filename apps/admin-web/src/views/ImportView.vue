<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfirmActionDialog from '../components/ConfirmActionDialog.vue'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import PermissionGate from '../components/PermissionGate.vue'
import StatusTag from '../components/StatusTag.vue'
import { adminGet, adminPost, adminPut } from '../api'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const projectKey = ref(String(route.query.project_key || (session.me?.project_key === '*' ? '' : session.me?.project_key || '')))
const scopeKey = ref(String(route.query.scope_id || route.query.scope_key || 'project'))
const statusFilter = ref(String(route.query.status || ''))
const projects = ref([])
const initialStep = Number(route.query.step || 0)
const currentStep = ref(Number.isFinite(initialStep) ? Math.min(3, Math.max(0, initialStep)) : 0)
const fileInput = ref(null)
const dragActive = ref(false)
const selectedFiles = ref([])
const batches = ref([])
const candidates = ref([])
const loading = ref(false)
const submitting = ref(false)
const message = ref('')
const error = ref(null)
const activeBatchId = ref(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailTab = ref(['overview', 'files', 'issues'].includes(String(route.query.detail_tab)) ? String(route.query.detail_tab) : 'overview')
const batchDetail = ref(null)
const batchFiles = ref([])
const batchIssues = ref([])
const actionVisible = ref(false)
const actionReason = ref('')
const actionSubmitting = ref(false)
const action = ref(null)
const rowLoading = ref(new Set())
let refreshTimer
let loadSequence = 0

const CHUNK_CHARS = 1024 * 1024
const MAX_FILE_BYTES = 128 * 1024 * 1024
const MAX_FILES = 100
const BINARY_EXTENSIONS = new Set(['pdf', 'docx', 'zip'])
const ACCEPTED_EXTENSIONS = new Set([
  'md', 'markdown', 'txt', 'json', 'jsonl', 'sql', 'pdf', 'docx', 'zip',
  'py', 'js', 'ts', 'tsx', 'jsx', 'java', 'go', 'rs', 'yaml', 'yml', 'toml', 'sh', 'ps1',
])
const ACTIVE_BATCH_STATUSES = new Set(['pending', 'queued', 'uploading', 'running', 'processing', 'awaiting_review'])

const isAdmin = computed(() => session.isAdmin ?? Boolean(session.me?.permissions?.includes('admin')))
const validFiles = computed(() => selectedFiles.value.filter((item) => item.status !== 'invalid'))
const hasInvalidFiles = computed(() => selectedFiles.value.some((item) => item.status === 'invalid'))
const overallProgress = computed(() => {
  if (!selectedFiles.value.length) return 0
  return Math.round(selectedFiles.value.reduce((sum, item) => sum + Number(item.progress || 0), 0) / selectedFiles.value.length)
})
const hasActiveBatches = computed(() => batches.value.some((item) => ACTIVE_BATCH_STATUSES.has(item.status)))

function fileExtension(name) {
  return String(name || '').toLowerCase().split('.').pop() || ''
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function importStatusLabel(status) {
  return ({
    active: '活跃批次',
    pending: '待处理', queued: '已排队', uploading: '上传中', uploaded: '已上传',
    running: '运行中', processing: '处理中', awaiting_review: '待审核', completed: '已完成',
    failed: '失败', cancelled: '已取消', published: '已发布', pending_review: '待审核',
    rejected: '已拒绝', rolled_back: '已回滚',
  })[status] || status || '未知'
}

function severityLabel(severity) {
  return severity === 'error' ? '错误' : severity === 'warning' ? '告警' : severity || '告警'
}

function validateFile(file) {
  if (!file?.name) return '无法读取文件名'
  if (!ACCEPTED_EXTENSIONS.has(fileExtension(file.name))) return '不支持此文件格式'
  if (file.size <= 0) return '文件内容为空'
  if (file.size > MAX_FILE_BYTES) return '单个文件不能超过 128 MiB'
  return ''
}

function addFiles(fileList) {
  const incoming = Array.from(fileList || [])
  const existingKeys = new Set(selectedFiles.value.map((item) => item.key))
  const remaining = Math.max(0, MAX_FILES - selectedFiles.value.length)
  const next = incoming.slice(0, remaining).flatMap((file) => {
    const key = `${file.name}:${file.size}:${file.lastModified || 0}`
    if (existingKeys.has(key)) return []
    existingKeys.add(key)
    const validationError = validateFile(file)
    return [{ key, file, name: file.name, size: file.size, status: validationError ? 'invalid' : 'ready', progress: 0, error: validationError }]
  })
  selectedFiles.value = [...selectedFiles.value, ...next]
  if (incoming.length > remaining) error.value = new Error(`单个批次最多选择 ${MAX_FILES} 个文件`)
  if (selectedFiles.value.length) currentStep.value = 1
  syncWizardQuery()
}

function chooseFiles(event) {
  addFiles(event.target.files)
  event.target.value = ''
}

function onDrop(event) {
  dragActive.value = false
  addFiles(event.dataTransfer?.files)
}

function removeFile(index) {
  if (submitting.value) return
  selectedFiles.value.splice(index, 1)
  if (!selectedFiles.value.length) currentStep.value = 0
}

function resetWizard() {
  selectedFiles.value = []
  activeBatchId.value = null
  currentStep.value = 0
  message.value = ''
  error.value = null
  syncWizardQuery()
}

async function syncWizardQuery(extra = {}) {
  const query = { ...route.query, ...extra, step: String(currentStep.value) }
  if (projectKey.value.trim()) query.project_key = projectKey.value.trim()
  else delete query.project_key
  if (scopeKey.value.trim()) query.scope_id = scopeKey.value.trim()
  else delete query.scope_id
  delete query.scope_key
  if (statusFilter.value) query.status = statusFilter.value
  else delete query.status
  if (detailVisible.value) query.detail_tab = detailTab.value
  else delete query.detail_tab
  await router.replace({ query })
}

function resetContextWork() {
  if (submitting.value) return
  selectedFiles.value = []
  activeBatchId.value = null
  currentStep.value = 0
  detailVisible.value = false
  batchDetail.value = null
  batchFiles.value = []
  batchIssues.value = []
}

async function changeImportContext() {
  resetContextWork()
  await syncWizardQuery({ batch_id: undefined })
  await load({ syncQuery: false })
}

async function clearStatusFilter() {
  statusFilter.value = ''
  await syncWizardQuery()
  await load({ syncQuery: false })
}

async function loadProjects() {
  try {
    const result = await adminGet('/projects', { page: 1, page_size: 100 })
    projects.value = Array.isArray(result.data) ? result.data : []
  } catch (cause) {
    projects.value = []
    error.value = cause
  }
}

async function load(options = {}) {
  const sequence = ++loadSequence
  if (!options.silent) loading.value = true
  error.value = null
  try {
    const params = { project_key: projectKey.value.trim(), page: 1, page_size: 100 }
    const [batchResult, candidateResult] = await Promise.all([
      adminGet('/import-batches', { ...params, status: statusFilter.value }),
      adminGet('/reference-candidates', { ...params, scope_id: scopeKey.value.trim() }),
    ])
    if (sequence !== loadSequence) return
    batches.value = Array.isArray(batchResult.data) ? batchResult.data : []
    candidates.value = Array.isArray(candidateResult.data) ? candidateResult.data : []
    if (!options.silent && options.syncQuery !== false) await syncWizardQuery()
  } catch (cause) {
    if (sequence !== loadSequence) return
    error.value = cause
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

function isBinaryImport(file) {
  return BINARY_EXTENSIONS.has(fileExtension(file.name))
}

function bytesToBase64(bytes) {
  let binary = ''
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
  }
  return btoa(binary)
}

async function readImportContent(file) {
  const buffer = await file.arrayBuffer()
  if (isBinaryImport(file)) return { binary: true, content: bytesToBase64(new Uint8Array(buffer)) }
  try {
    return { binary: false, content: new TextDecoder('utf-8', { fatal: true }).decode(buffer) }
  } catch {
    throw new Error('文本文件必须使用 UTF-8 编码')
  }
}

async function uploadFileByChunks(batchId, entry) {
  entry.status = 'reading'
  entry.progress = 2
  entry.error = ''
  const { binary, content } = await readImportContent(entry.file)
  const parts = []
  for (let index = 0; index < content.length; index += CHUNK_CHARS) {
    parts.push(binary ? { content_base64: content.slice(index, index + CHUNK_CHARS) } : { content: content.slice(index, index + CHUNK_CHARS) })
  }
  if (!parts.length) parts.push(binary ? { content_base64: '' } : { content: '' })
  const started = await adminPost(`/import-batches/${batchId}/uploads`, {
    source_name: entry.name,
    total_parts: parts.length,
  })
  const uploadId = started.data.upload_id
  const sourceType = started.data.source_type
  entry.status = 'uploading'
  for (let index = 0; index < parts.length; index += 1) {
    const payload = { ...parts[index] }
    if (index === 0) Object.assign(payload, { total_parts: parts.length, source_name: entry.name, source_type: sourceType })
    await adminPut(`/import-batches/${batchId}/uploads/${uploadId}/parts/${index}`, payload)
    entry.progress = Math.max(5, Math.round(((index + 1) / parts.length) * 92))
  }
  await adminPost(`/import-batches/${batchId}/uploads/${uploadId}:complete`, {})
  entry.status = 'completed'
  entry.progress = 100
}

async function importFiles() {
  if (!isAdmin.value || submitting.value) return
  if (!projectKey.value.trim()) {
    error.value = new Error('请先选择项目')
    currentStep.value = 0
    return
  }
  if (!scopeKey.value.trim()) {
    error.value = new Error('请填写 Scope')
    currentStep.value = 0
    return
  }
  if (!validFiles.value.length || hasInvalidFiles.value) {
    error.value = new Error(hasInvalidFiles.value ? '请移除校验失败的文件后再上传' : '请至少选择一个有效文件')
    currentStep.value = 1
    return
  }
  submitting.value = true
  error.value = null
  message.value = ''
  currentStep.value = 2
  await syncWizardQuery()
  try {
    const created = await adminPost('/import-batches', { project_key: projectKey.value.trim(), scope_key: scopeKey.value.trim() })
    const batchId = created.data.batch_id ?? created.data.id
    activeBatchId.value = batchId
    let failed = false
    for (const entry of validFiles.value) {
      try {
        await uploadFileByChunks(batchId, entry)
      } catch (cause) {
        entry.status = 'failed'
        entry.error = cause.message || '上传失败'
        failed = true
      }
    }
    if (failed) throw new Error(`批次 ${batchId} 已创建，但部分文件上传失败；请根据文件列表定位后重试`)
    await adminPost(`/import-batches/${batchId}/start`, {})
    currentStep.value = 3
    message.value = `批次 ${batchId} 已进入处理队列，可在批次详情跟踪进度`
    await load()
    const createdBatch = batches.value.find((item) => String(item.id) === String(batchId)) || { id: batchId }
    await openBatch(createdBatch)
  } catch (cause) {
    error.value = cause
  } finally {
    submitting.value = false
  }
}

async function loadBatchDetail(batch, options = {}) {
  if (!batch?.id) return
  if (!options.silent) detailLoading.value = true
  try {
    const [detailResult, filesResult, issuesResult] = await Promise.all([
      adminGet(`/import-batches/${batch.id}`),
      adminGet(`/import-batches/${batch.id}/files`),
      adminGet(`/import-batches/${batch.id}/issues`),
    ])
    batchDetail.value = detailResult.data || batch
    batchFiles.value = Array.isArray(filesResult.data) ? filesResult.data : []
    batchIssues.value = Array.isArray(issuesResult.data) ? issuesResult.data : []
  } catch (cause) {
    error.value = cause
  } finally {
    if (!options.silent) detailLoading.value = false
  }
}

async function openBatch(batch) {
  detailVisible.value = true
  detailTab.value = ['overview', 'files', 'issues'].includes(String(route.query.detail_tab)) ? String(route.query.detail_tab) : 'overview'
  batchDetail.value = batch
  await syncWizardQuery({ batch_id: String(batch.id), detail_tab: detailTab.value })
  await loadBatchDetail(batch)
}

function closeBatch() {
  const query = { ...route.query }
  delete query.batch_id
  delete query.detail_tab
  router.replace({ query })
}

function actionKey(kind, target) {
  return `${kind}:${target?.id}`
}

function isRowLoading(kind, target) {
  return rowLoading.value.has(actionKey(kind, target))
}

function openAction(kind, target) {
  const definitions = {
    approve: { title: '批准并发布候选', confirmText: '确认批准', requiresReason: true, reasonLabel: '审核原因', impact: '候选将发布为正式 Memory，并进入后续检索结果。' },
    reject: { title: '拒绝候选', confirmText: '确认拒绝', requiresReason: true, reasonLabel: '拒绝原因', impact: '候选将标记为已拒绝，不会进入正式 Memory。' },
    candidateRollback: { title: '回滚已发布候选', confirmText: '确认回滚', requiresReason: true, reasonLabel: '回滚原因', impact: '该候选对应的正式 Memory 将被撤回，可能影响后续检索。' },
    cancel: { title: '取消导入批次', confirmText: '确认取消', requiresReason: false, reasonLabel: '', impact: `批次 #${target.id} 的未完成文件和队列任务将停止处理。` },
    retry: { title: '重试导入批次', confirmText: '确认重试', requiresReason: false, reasonLabel: '', impact: `批次 #${target.id} 将重新进入处理队列。` },
    rollback: { title: '回滚导入批次', confirmText: '确认回滚', requiresReason: true, reasonLabel: '回滚原因', impact: `批次 #${target.id} 发布的候选与 Memory 将被撤回，此操作会改变检索结果。` },
  }
  action.value = { kind, target, ...definitions[kind] }
  actionReason.value = ''
  actionVisible.value = true
}

async function confirmAction() {
  if (!action.value || actionSubmitting.value) return
  if (action.value.requiresReason && !actionReason.value.trim()) return
  const { kind, target } = action.value
  const key = actionKey(kind, target)
  actionSubmitting.value = true
  rowLoading.value = new Set([...rowLoading.value, key])
  error.value = null
  try {
    if (kind === 'approve' || kind === 'reject') {
      await adminPost(`/reference-candidates/${target.id}/review`, {
        decision: kind,
        reviewer: session.displayName || session.me?.display_name || '管理后台',
        reason: actionReason.value.trim(),
      })
      message.value = kind === 'approve' ? '候选已发布为正式 Memory' : '候选已拒绝'
    } else if (kind === 'candidateRollback') {
      await adminPost(`/reference-candidates/${target.id}/rollback`, { decision: 'rollback', reason: actionReason.value.trim() })
      message.value = `候选 #${target.id} 已回滚`
    } else if (kind === 'cancel') {
      await adminPost(`/import-batches/${target.id}/cancel`, {})
      message.value = `批次 #${target.id} 已取消`
    } else if (kind === 'retry') {
      await adminPost(`/import-batches/${target.id}/retry`, {})
      message.value = `批次 #${target.id} 已重新排队`
    } else if (kind === 'rollback') {
      await adminPost(`/import-batches/${target.id}/rollback`, { decision: 'rollback', reason: actionReason.value.trim() })
      message.value = `批次 #${target.id} 已回滚`
    }
    actionVisible.value = false
    await load()
    if (detailVisible.value && String(batchDetail.value?.id) === String(target.id)) await loadBatchDetail(target)
  } catch (cause) {
    error.value = cause
  } finally {
    const next = new Set(rowLoading.value)
    next.delete(key)
    rowLoading.value = next
    actionSubmitting.value = false
  }
}

async function refreshActiveWork() {
  if (document.visibilityState !== 'visible' || !hasActiveBatches.value) return
  await load({ silent: true })
  if (detailVisible.value && batchDetail.value && ACTIVE_BATCH_STATUSES.has(batchDetail.value.status)) {
    await loadBatchDetail(batchDetail.value, { silent: true })
  }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') refreshActiveWork()
}

onMounted(async () => {
  await Promise.all([loadProjects(), load()])
  const batchId = route.query.batch_id
  if (batchId) await openBatch(batches.value.find((item) => String(item.id) === String(batchId)) || { id: batchId })
  refreshTimer = window.setInterval(refreshActiveWork, 15_000)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

watch(() => route.fullPath, async () => {
  const nextProject = String(route.query.project_key || (session.me?.project_key === '*' ? '' : session.me?.project_key || ''))
  const nextScope = String(route.query.scope_id || route.query.scope_key || 'project')
  const nextStatus = String(route.query.status || '')
  const nextStepValue = Number(route.query.step || 0)
  const nextStep = Number.isFinite(nextStepValue) ? Math.min(3, Math.max(0, nextStepValue)) : 0
  const nextTab = ['overview', 'files', 'issues'].includes(String(route.query.detail_tab)) ? String(route.query.detail_tab) : 'overview'
  const nextBatchId = String(route.query.batch_id || '')
  const currentBatchId = detailVisible.value ? String(batchDetail.value?.id || '') : ''
  const contextChanged = nextProject !== projectKey.value || nextScope !== scopeKey.value
  const listChanged = contextChanged || nextStatus !== statusFilter.value
  const drawerChanged = nextBatchId !== currentBatchId
  if (!listChanged && nextStep === currentStep.value && nextTab === detailTab.value && !drawerChanged) return
  if (contextChanged) resetContextWork()
  projectKey.value = nextProject
  scopeKey.value = nextScope
  statusFilter.value = nextStatus
  currentStep.value = nextStep
  detailTab.value = nextTab
  if (listChanged) await load({ syncQuery: false })
  if (nextBatchId) {
    detailVisible.value = true
    const target = batches.value.find((item) => String(item.id) === nextBatchId) || { id: nextBatchId }
    batchDetail.value = target
    await loadBatchDetail(target)
  } else if (drawerChanged) {
    detailVisible.value = false
    batchDetail.value = null
    batchFiles.value = []
    batchIssues.value = []
  }
})

onBeforeUnmount(() => {
  window.clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <PageHeader eyebrow="数据治理 / 历史资料" title="历史知识导入" description="先校验资料，再分片上传；批次处理、问题定位与审核留在一条可追踪流程中。">
    <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>

  <el-alert v-if="!isAdmin" class="page-alert" title="当前为只读访问，可查看批次、文件与候选，但不能上传或审核。" type="info" show-icon />
  <el-alert v-if="message" class="page-alert" :title="message" type="success" show-icon closable @close="message = ''" />
  <ErrorState v-if="error" :error="error" @retry="load" />

  <el-card class="import-card import-wizard" shadow="never">
    <el-steps :active="currentStep" finish-status="success" align-center>
      <el-step title="项目与 Scope" description="确定数据边界" />
      <el-step title="选择文件" description="本地前置校验" />
      <el-step title="分片上传" description="逐文件显示进度" />
      <el-step title="处理与审核" description="跟踪批次结果" />
    </el-steps>

    <div class="wizard-panel">
      <div class="import-context-grid">
        <el-form-item label="项目" required>
          <el-select v-model="projectKey" filterable placeholder="请选择项目" :disabled="submitting" @change="changeImportContext">
            <el-option v-for="project in projects" :key="project.project_key" :label="`${project.name || project.project_key}（${project.project_key}）`" :value="project.project_key" />
          </el-select>
        </el-form-item>
        <el-form-item label="Scope" required>
          <el-input v-model="scopeKey" placeholder="例如 project" :disabled="submitting" @change="changeImportContext" />
        </el-form-item>
      </div>

      <PermissionGate permission="admin">
        <div class="file-dropzone" :class="{ 'is-dragging': dragActive }" role="button" tabindex="0" @click="fileInput?.click()" @keydown.enter.prevent="fileInput?.click()" @keydown.space.prevent="fileInput?.click()" @dragenter.prevent="dragActive = true" @dragover.prevent="dragActive = true" @dragleave.prevent="dragActive = false" @drop.prevent="onDrop">
          <input ref="fileInput" class="visually-hidden" type="file" multiple accept=".md,.markdown,.txt,.json,.jsonl,.sql,.pdf,.docx,.zip,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.yaml,.yml,.toml,.sh,.ps1" @change="chooseFiles" />
          <strong>{{ dragActive ? '松开即可加入文件' : '拖放资料到这里' }}</strong>
          <span>或点击选择文件；支持文档、压缩包与常见源码，单文件最大 128 MiB</span>
          <el-button type="primary" plain tabindex="-1">选择文件</el-button>
        </div>
        <template #fallback><div class="readonly-placeholder">只读账号不能创建导入批次。</div></template>
      </PermissionGate>

      <div v-if="selectedFiles.length" class="selected-files" aria-live="polite">
        <div class="selected-files__heading"><strong>待导入文件（{{ selectedFiles.length }}）</strong><span>总进度 {{ overallProgress }}%</span></div>
        <article v-for="(entry, index) in selectedFiles" :key="entry.key" class="file-row" :class="`is-${entry.status}`">
          <div class="file-row__main"><strong>{{ entry.name }}</strong><span>{{ formatBytes(entry.size) }} · {{ entry.error || ({ ready: '等待上传', reading: '正在读取', uploading: '正在上传', completed: '上传完成', failed: '上传失败' })[entry.status] }}</span></div>
          <el-progress :percentage="entry.progress" :status="entry.status === 'failed' || entry.status === 'invalid' ? 'exception' : entry.status === 'completed' ? 'success' : undefined" />
          <el-button v-if="!submitting" link type="danger" aria-label="移除文件" @click="removeFile(index)">移除</el-button>
        </article>
      </div>

      <div class="wizard-actions">
        <el-button v-if="selectedFiles.length && !submitting" @click="resetWizard">清空</el-button>
        <PermissionGate permission="admin">
          <el-button type="primary" :loading="submitting" :disabled="!validFiles.length || hasInvalidFiles" @click="importFiles">上传并开始解析</el-button>
        </PermissionGate>
      </div>
    </div>
  </el-card>

  <el-card class="import-card" shadow="never">
    <template #header><div class="card-heading"><div><strong>导入批次</strong><span v-if="hasActiveBatches">活跃批次每 15 秒自动刷新</span></div><div class="card-heading__tags"><el-tag v-if="statusFilter" closable type="warning" effect="plain" @close="clearStatusFilter">筛选：{{ importStatusLabel(statusFilter) }}</el-tag><el-tag v-if="hasActiveBatches" type="success" effect="plain">自动跟踪中</el-tag></div></div></template>
    <el-table v-loading="loading" :data="batches" row-key="id" empty-text="暂无导入批次" @row-click="openBatch">
      <el-table-column label="批次" width="100"><template #default="scope"><span class="mono-value">#{{ scope.row.id }}</span></template></el-table-column>
      <el-table-column label="状态" width="126"><template #default="scope"><StatusTag :status="scope.row.status" :label="importStatusLabel(scope.row.status)" /></template></el-table-column>
      <el-table-column prop="scope_key" label="Scope" min-width="130" />
      <el-table-column label="处理进度" min-width="180"><template #default="scope"><el-progress :percentage="scope.row.source_count ? Math.min(100, Math.round((scope.row.processed_count || 0) / scope.row.source_count * 100)) : 0" /></template></el-table-column>
      <el-table-column label="产出" min-width="150"><template #default="scope">{{ scope.row.document_count || 0 }} 文档 / {{ scope.row.chunk_count || 0 }} 分块</template></el-table-column>
      <el-table-column label="问题" width="90"><template #default="scope"><el-tag v-if="scope.row.error_count" type="danger">{{ scope.row.error_count }}</el-tag><span v-else>0</span></template></el-table-column>
      <el-table-column label="创建时间" min-width="180"><template #default="scope"><DateTime :value="scope.row.created_at" /></template></el-table-column>
      <el-table-column label="操作" min-width="250" fixed="right">
        <template #default="scope">
          <el-button link type="primary" @click.stop="openBatch(scope.row)">查看详情</el-button>
          <PermissionGate permission="admin">
            <el-button v-if="ACTIVE_BATCH_STATUSES.has(scope.row.status)" link type="warning" :loading="isRowLoading('cancel', scope.row)" @click.stop="openAction('cancel', scope.row)">取消</el-button>
            <el-button v-if="['failed', 'cancelled'].includes(scope.row.status)" link type="primary" :loading="isRowLoading('retry', scope.row)" @click.stop="openAction('retry', scope.row)">重试</el-button>
            <el-button v-if="['completed', 'awaiting_review'].includes(scope.row.status)" link type="danger" :loading="isRowLoading('rollback', scope.row)" @click.stop="openAction('rollback', scope.row)">回滚</el-button>
          </PermissionGate>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-card class="import-card" shadow="never">
    <template #header><div class="card-heading"><div><strong>候选审核</strong><span>审核原因会进入审计记录</span></div><el-tag type="info" effect="plain">{{ candidates.length }} 条</el-tag></div></template>
    <el-table v-loading="loading" :data="candidates" row-key="id" empty-text="暂无候选">
      <el-table-column label="候选" width="100"><template #default="scope"><span class="mono-value">#{{ scope.row.id }}</span></template></el-table-column>
      <el-table-column prop="title" label="标题" min-width="190" show-overflow-tooltip />
      <el-table-column label="状态" width="126"><template #default="scope"><StatusTag :status="scope.row.status" :label="importStatusLabel(scope.row.status)" /></template></el-table-column>
      <el-table-column label="置信度" width="108"><template #default="scope">{{ Math.round(Number(scope.row.model_confidence ?? scope.row.confidence ?? 0) * 100) }}%</template></el-table-column>
      <el-table-column label="内容" min-width="360"><template #default="scope"><p class="candidate-content">{{ scope.row.content?.text || '暂无文本内容' }}</p></template></el-table-column>
      <el-table-column label="操作" min-width="220" fixed="right">
        <template #default="scope">
          <PermissionGate permission="admin">
            <template v-if="scope.row.status === 'pending_review'">
              <el-button link type="primary" :loading="isRowLoading('approve', scope.row)" @click="openAction('approve', scope.row)">批准</el-button>
              <el-button link type="danger" :loading="isRowLoading('reject', scope.row)" @click="openAction('reject', scope.row)">拒绝</el-button>
            </template>
            <el-button v-if="scope.row.status === 'published' || scope.row.published_memory_id" link type="danger" :loading="isRowLoading('candidateRollback', scope.row)" @click="openAction('candidateRollback', scope.row)">回滚</el-button>
          </PermissionGate>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <DetailDrawer v-model="detailVisible" :title="`导入批次 #${batchDetail?.id || ''}`" size="760px" @update:model-value="(value) => { if (!value) closeBatch() }">
    <div v-loading="detailLoading">
      <div v-if="batchDetail" class="drawer-status-strip"><StatusTag :status="batchDetail.status" :label="importStatusLabel(batchDetail.status)" /><span>{{ batchDetail.processed_count || 0 }} / {{ batchDetail.source_count || 0 }} 个文件已处理</span><span>{{ batchIssues.length }} 个问题</span></div>
      <el-tabs v-model="detailTab" @tab-change="syncWizardQuery">
        <el-tab-pane label="概览" name="overview">
          <el-descriptions v-if="batchDetail" :column="2" border>
            <el-descriptions-item label="批次 ID"><CopyableText :value="batchDetail.id" /></el-descriptions-item>
            <el-descriptions-item label="Scope">{{ batchDetail.scope_key || '-' }}</el-descriptions-item>
            <el-descriptions-item label="文件数">{{ batchDetail.source_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="重试次数">{{ batchDetail.retry_count || 0 }}</el-descriptions-item>
            <el-descriptions-item label="创建时间"><DateTime :value="batchDetail.created_at" /></el-descriptions-item>
            <el-descriptions-item label="完成时间"><DateTime :value="batchDetail.completed_at" /></el-descriptions-item>
            <el-descriptions-item v-if="batchDetail.error_message" label="错误信息" :span="2">{{ batchDetail.error_message }}</el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
        <el-tab-pane :label="`文件 ${batchFiles.length}`" name="files">
          <el-table :data="batchFiles" row-key="id" empty-text="暂无文件记录">
            <el-table-column prop="source_name" label="文件" min-width="230" show-overflow-tooltip />
            <el-table-column label="大小" width="110"><template #default="scope">{{ formatBytes(scope.row.size_bytes) }}</template></el-table-column>
            <el-table-column label="状态" width="120"><template #default="scope"><StatusTag :status="scope.row.status" :label="importStatusLabel(scope.row.status)" /></template></el-table-column>
            <el-table-column prop="error_message" label="错误定位" min-width="220" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="`问题 ${batchIssues.length}`" name="issues">
          <el-table :data="batchIssues" row-key="id" empty-text="暂无解析问题">
            <el-table-column label="级别" width="100"><template #default="scope"><el-tag :type="scope.row.severity === 'error' ? 'danger' : 'warning'">{{ severityLabel(scope.row.severity) }}</el-tag></template></el-table-column>
            <el-table-column prop="issue_type" label="类型" min-width="140" />
            <el-table-column prop="message" label="问题说明" min-width="300" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </DetailDrawer>

  <ConfirmActionDialog v-if="action" v-model="actionVisible" v-model:reason="actionReason" :title="action.title" :confirm-text="action.confirmText" :loading="actionSubmitting" :danger="['reject', 'candidateRollback', 'cancel', 'rollback'].includes(action.kind)" :requires-reason="action.requiresReason" :reason-label="action.reasonLabel" @confirm="confirmAction">
    <template #impact><p>{{ action.impact }}</p></template>
  </ConfirmActionDialog>
</template>

<style scoped>
.page-alert,
.import-card + .import-card {
  margin-top: 14px;
}

.import-wizard :deep(.el-card__body) {
  padding: 26px;
}

.wizard-panel {
  max-width: 980px;
  margin: 30px auto 0;
}

.import-context-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.import-context-grid :deep(.el-select) {
  width: 100%;
}

.file-dropzone {
  display: grid;
  place-items: center;
  gap: 10px;
  min-height: 190px;
  padding: 26px;
  text-align: center;
  color: #526b70;
  border: 1px dashed #83aaa4;
  border-radius: 10px;
  background: #f3f9f7;
  cursor: pointer;
  transition: border-color 140ms ease, background-color 140ms ease, transform 140ms ease;
}

.file-dropzone:hover,
.file-dropzone:focus-visible,
.file-dropzone.is-dragging {
  outline: none;
  color: #0f5f59;
  border-color: #0f766e;
  background: #e8f4f1;
  transform: translateY(-1px);
}

.file-dropzone strong {
  color: #14272e;
  font-size: 20px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.readonly-placeholder {
  padding: 24px;
  text-align: center;
  color: #647b80;
  border: 1px dashed #c6d7d4;
  border-radius: 10px;
}

.selected-files {
  margin-top: 18px;
}

.selected-files__heading,
.card-heading,
.drawer-status-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.card-heading > div:first-child > span,
.selected-files__heading span {
  display: block;
  margin-top: 3px;
  color: #647b80;
  font-size: 13px;
}

.card-heading__tags {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(180px, 320px) auto;
  align-items: center;
  gap: 18px;
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid #dbe7e4;
  border-radius: 9px;
}

.file-row.is-invalid,
.file-row.is-failed {
  border-color: #e8bcc1;
  background: #fff7f8;
}

.file-row__main {
  min-width: 0;
}

.file-row__main strong,
.file-row__main span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-row__main span {
  margin-top: 4px;
  color: #647b80;
  font-size: 13px;
}

.wizard-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}

.candidate-content {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.drawer-status-strip {
  justify-content: flex-start;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding: 12px 14px;
  color: #40595e;
  border-radius: 8px;
  background: #e8f4f1;
}

@media (max-width: 768px) {
  .import-wizard :deep(.el-card__body) {
    padding: 18px 14px;
  }

  .import-wizard :deep(.el-step__description) {
    display: none;
  }

  .import-context-grid,
  .file-row {
    grid-template-columns: 1fr;
  }

  .file-row {
    gap: 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .file-dropzone {
    transition: none;
  }
}
</style>
