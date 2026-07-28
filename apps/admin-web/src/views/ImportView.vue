<script setup>
import { onMounted, ref } from 'vue'
import { adminGet, adminPost, adminPut } from '../api'

const projectKey = ref('demo')
const scopeKey = ref('project')
const selectedFiles = ref([])
const batches = ref([])
const candidates = ref([])
const loading = ref(false)
const submitting = ref(false)
const message = ref('')
const error = ref('')

function chooseFiles(event) {
  selectedFiles.value = Array.from(event.target.files || [])
  message.value = selectedFiles.value.length ? `已选择 ${selectedFiles.value.length} 个文件` : ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [batchResult, candidateResult] = await Promise.all([
      adminGet('/import-batches', { project_key: projectKey.value, page: 1, page_size: 50 }),
      adminGet('/reference-candidates', { project_key: projectKey.value, page: 1, page_size: 100 }),
    ])
    batches.value = batchResult.data || []
    candidates.value = candidateResult.data || []
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

const CHUNK_CHARS = 1024 * 1024

function isBinaryImport(file) {
  return /\.(pdf|docx|zip)$/i.test(file.name)
}

async function readImportItem(file) {
  if (isBinaryImport(file)) {
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ""
    for (let index = 0; index < bytes.length; index += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
    }
    return { source_name: file.name, content_base64: btoa(binary) }
  }
  return { source_name: file.name, content: await file.text() }
}

async function uploadFileByChunks(batchId, file) {
  const binary = isBinaryImport(file)
  const content = binary ? (await readImportItem(file)).content_base64 : await file.text()
  const parts = []
  if (binary) {
    for (let index = 0; index < content.length; index += CHUNK_CHARS) parts.push({ content_base64: content.slice(index, index + CHUNK_CHARS) })
  } else {
    for (let index = 0; index < content.length; index += CHUNK_CHARS) parts.push({ content: content.slice(index, index + CHUNK_CHARS) })
  }
  const started = await adminPost(`/import-batches/${batchId}/uploads`, { source_name: file.name, source_type: undefined, total_parts: parts.length || 1 })
  const uploadId = started.data.upload_id
  const sourceType = started.data.source_type
  for (let index = 0; index < (parts.length || 1); index += 1) {
    const payload = parts[index] || (binary ? { content_base64: '' } : { content: '' })
    if (index === 0) Object.assign(payload, { total_parts: parts.length || 1, source_name: file.name, source_type: sourceType })
    await adminPut(`/import-batches/${batchId}/uploads/${uploadId}/parts/${index}`, payload)
  }
  await adminPost(`/import-batches/${batchId}/uploads/${uploadId}:complete`, {})
}

async function importFiles() {
  if (!selectedFiles.value.length) {
    error.value = '请至少选择一个文件'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    const created = await adminPost('/import-batches', { project_key: projectKey.value, scope_key: scopeKey.value })
    const batchId = created.data.batch_id
    for (const file of selectedFiles.value) await uploadFileByChunks(batchId, file)
    await adminPost(`/import-batches/${batchId}/start`, {})
    message.value = `批次 ${batchId} 已创建，正在后台解析文件`
    selectedFiles.value = []
    await load()
  } catch (cause) {
    error.value = cause.message
  } finally {
    submitting.value = false
  }
}

async function review(candidate, decision) {
  try {
    await adminPost(`/reference-candidates/${candidate.id}/review`, { decision, reviewer: '管理后台', reason: decision === 'approve' ? '管理端审核通过' : '管理端审核拒绝' })
    message.value = decision === 'approve' ? '候选已发布为正式 Memory' : '候选已拒绝'
    await load()
  } catch (cause) {
    error.value = cause.message
  }
}

async function retryBatch(batch) {
  try {
    await adminPost(`/import-batches/${batch.id}/retry`, {})
    message.value = `批次 ${batch.id} 已重新排队`
    await load()
  } catch (cause) {
    error.value = cause.message
  }
}
async function cancelBatch(batch) {
  try {
    await adminPost(`/import-batches/${batch.id}/cancel`, {})
    message.value = `批次 ${batch.id} 已请求取消`
    await load()
  } catch (cause) {
    error.value = cause.message
  }
}
async function rollback(batch) {
  try {
    await adminPost(`/import-batches/${batch.id}/rollback`, { decision: 'rollback', reason: '管理端批次回滚' })
    message.value = `批次 ${batch.id} 已回滚`
    await load()
  } catch (cause) {
    error.value = cause.message
  }
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">Reference Layer</span><h2>历史知识导入</h2></div>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </section>

  <el-alert v-if="message" :title="message" type="success" show-icon closable @close="message = ''" />
  <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error = ''" />

  <el-card class="import-card">
    <template #header><span>创建导入批次</span></template>
    <el-form label-position="top">
      <el-form-item label="项目键"><el-input v-model="projectKey" placeholder="例如 demo" /></el-form-item>
      <el-form-item label="作用域"><el-input v-model="scopeKey" placeholder="project" /></el-form-item>
      <el-form-item label="资料文件">
        <input type="file" multiple accept=".md,.markdown,.txt,.json,.jsonl,.sql,.pdf,.docx,.zip,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.yaml,.yml,.toml,.sh,.ps1" @change="chooseFiles" />
        <div class="field-hint">支持 Markdown、TXT、JSON/JSONL、PDF、DOCX、ZIP、SQL 和常见源码；大文件自动使用可恢复分片上传。</div>
      </el-form-item>
      <el-button type="primary" :loading="submitting" @click="importFiles">上传并解析</el-button>
    </el-form>
  </el-card>

  <el-card class="import-card">
    <template #header><span>导入批次</span></template>
    <el-table v-loading="loading" :data="batches" stripe empty-text="暂无导入批次">
      <el-table-column prop="id" label="批次" width="90" />
      <el-table-column prop="status" label="状态" width="120" />
      <el-table-column prop="source_count" label="文件数" width="90" />
      <el-table-column prop="processed_count" label="已处理" width="90" />
      <el-table-column prop="document_count" label="文档数" width="90" />
      <el-table-column prop="chunk_count" label="分块数" width="90" />
            <el-table-column label="操作" min-width="180">
        <template #default="scope">
                    <el-button v-if="scope.row.status === 'running' || scope.row.status === 'pending' || scope.row.status === 'queued'" link type="warning" @click="cancelBatch(scope.row)">取消</el-button>
          <el-button v-if="scope.row.status === 'failed' || scope.row.status === 'cancelled'" link type="primary" @click="retryBatch(scope.row)">重试</el-button>
          <el-button v-if="scope.row.status === 'completed' || scope.row.status === 'awaiting_review'" link type="danger" @click="rollback(scope.row)">回滚批次</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-card class="import-card">
    <template #header><span>待审核候选</span></template>
    <el-table v-loading="loading" :data="candidates" stripe empty-text="暂无候选">
      <el-table-column prop="id" label="候选" width="90" />
      <el-table-column prop="title" label="标题" width="180" />
      <el-table-column prop="status" label="状态" width="120" />
      <el-table-column label="内容" min-width="360" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.content?.text || "-" }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="scope">
          <el-button v-if="scope.row.status === 'pending_review'" link type="primary" @click="review(scope.row, 'approve')">批准发布</el-button>
          <el-button v-if="scope.row.status === 'pending_review'" link type="danger" @click="review(scope.row, 'reject')">拒绝</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>