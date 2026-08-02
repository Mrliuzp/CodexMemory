<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import PermissionGate from '../components/PermissionGate.vue'
import StatusTag from '../components/StatusTag.vue'
import { useSessionStore } from '../stores/session'
import { extractContractService, extractContractRevision, getContractService, uploadContractRevision } from '../contractServices'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const service = ref(null)
const activeTab = ref(['overview', 'revisions', 'upload'].includes(String(route.query.tab)) ? String(route.query.tab) : 'overview')
const loading = ref(false)
const uploading = ref(false)
const uploadPercent = ref(0)
const error = ref(null)
const fileError = ref('')
const selectedFile = ref(null)
const uploadResult = ref(null)
const uploadReused = ref(false)

const revisions = computed(() => service.value?.revisions || service.value?.revision_summaries || [])
const isAdmin = computed(() => session.isAdmin ?? Boolean(session.me?.permissions?.includes('admin')))

function revisionNumber(row) {
  return row?.revision_number ?? row?.revision
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function openRevision(row) {
  const number = revisionNumber(row)
  if (number !== undefined && number !== null) {
    router.push({ name: 'contract-revision-detail', params: { serviceId: route.params.serviceId, revisionNumber: number } })
  }
}

async function syncTab() {
  await router.replace({ query: { ...route.query, tab: activeTab.value } })
}

async function validateSelectedFile(file) {
  const extension = String(file.name || '').toLowerCase().split('.').pop()
  if (!['json', 'yaml', 'yml'].includes(extension)) return '仅支持 JSON 或 YAML 文件'
  if (!file.size) return '文件内容为空'
  if (file.size > 2 * 1024 * 1024) return '文件大小不能超过 2 MiB'
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
    if (extension === 'json') JSON.parse(text)
  } catch (cause) {
    return extension === 'json' && cause instanceof SyntaxError ? 'JSON 语法无效，请修正后重试' : '文件必须使用 UTF-8 编码'
  }
  return ''
}

async function selectFile(uploadFile) {
  const file = uploadFile?.raw || uploadFile
  selectedFile.value = null
  uploadResult.value = null
  uploadReused.value = false
  fileError.value = ''
  uploadPercent.value = 0
  if (!file) return
  fileError.value = await validateSelectedFile(file)
  if (!fileError.value) selectedFile.value = file
}

function clearFile() {
  if (uploading.value) return
  selectedFile.value = null
  uploadResult.value = null
  uploadReused.value = false
  fileError.value = ''
  uploadPercent.value = 0
}

async function upload() {
  if (!selectedFile.value) {
    fileError.value = '请先选择有效的 JSON 或 YAML 文件'
    return
  }
  if (uploading.value) return
  uploading.value = true
  uploadPercent.value = 18
  error.value = null
  fileError.value = ''
  try {
    uploadPercent.value = 42
    const result = await uploadContractRevision(route.params.serviceId, selectedFile.value)
    uploadPercent.value = 100
    uploadResult.value = extractContractRevision(result)
    uploadReused.value = Boolean(result?.meta?.reused)
    selectedFile.value = null
    await load()
  } catch (cause) {
    uploadPercent.value = 0
    error.value = cause
  } finally {
    uploading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    service.value = extractContractService(await getContractService(route.params.serviceId))
  } catch (cause) {
    service.value = null
    error.value = cause
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  await syncTab()
})
watch(() => route.query.tab, (value) => {
  const next = String(value || 'overview')
  if (['overview', 'revisions', 'upload'].includes(next) && next !== activeTab.value) activeTab.value = next
})
watch(() => route.params.serviceId, load)
</script>

<template>
  <PageHeader eyebrow="接口治理 / 服务" :title="service?.name || '接口契约服务'" :description="service?.description || '查看服务边界、Revision 时间线与上传校验结果。'">
    <template #actions><el-button @click="router.push({ name: 'contract-services' })">返回列表</el-button><el-button :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>

  <el-alert v-if="!isAdmin" class="page-alert" title="当前为只读访问，可查看所有 Revision，但不能上传新规范。" type="info" show-icon />
  <ErrorState v-if="error" :error="error" @retry="load" />

  <div v-loading="loading" class="contract-detail">
    <section v-if="service" class="service-identity">
      <div><span>稳定服务标识</span><CopyableText :value="service.service_key" /></div>
      <div><span>当前状态</span><StatusTag :status="service.status" /></div>
      <div><span>已发布 Revision</span><strong>{{ service.published_revision_number ? `v${service.published_revision_number}` : '尚未发布' }}</strong></div>
      <div><span>Revision 总数</span><strong>{{ revisions.length }}</strong></div>
    </section>

    <el-tabs v-if="service" v-model="activeTab" class="contract-service-tabs" @tab-change="syncTab">
      <el-tab-pane label="概览" name="overview">
        <el-card shadow="never" class="contract-card">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="服务 ID"><CopyableText :value="service.id || service.service_id" /></el-descriptions-item>
            <el-descriptions-item label="项目键"><CopyableText :value="service.project_key" /></el-descriptions-item>
            <el-descriptions-item label="服务名称">{{ service.name }}</el-descriptions-item>
            <el-descriptions-item label="服务标识"><CopyableText :value="service.service_key" /></el-descriptions-item>
            <el-descriptions-item label="创建时间"><DateTime :value="service.created_at" /></el-descriptions-item>
            <el-descriptions-item label="更新时间"><DateTime :value="service.updated_at" /></el-descriptions-item>
            <el-descriptions-item label="服务说明" :span="2">{{ service.description || '暂无说明' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`Revision ${revisions.length}`" name="revisions">
        <el-card shadow="never" class="contract-card">
          <el-table :data="revisions" row-key="id" empty-text="暂无 Revision" @row-click="openRevision">
            <el-table-column label="Revision" width="110"><template #default="scope"><strong>v{{ revisionNumber(scope.row) }}</strong></template></el-table-column>
            <el-table-column label="状态" width="126"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
            <el-table-column label="操作数" width="100"><template #default="scope">{{ scope.row.operation_count || 0 }}</template></el-table-column>
            <el-table-column label="内容哈希" min-width="300"><template #default="scope"><CopyableText :value="scope.row.content_hash" /></template></el-table-column>
            <el-table-column label="大小" width="110"><template #default="scope">{{ formatBytes(scope.row.size_bytes) }}</template></el-table-column>
            <el-table-column label="创建时间" min-width="180"><template #default="scope"><DateTime :value="scope.row.created_at" /></template></el-table-column>
            <el-table-column label="操作" width="116" fixed="right"><template #default="scope"><el-button link type="primary" @click.stop="openRevision(scope.row)">查看详情</el-button></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="上传 Revision" name="upload">
        <PermissionGate permission="admin">
          <el-card shadow="never" class="contract-card upload-card">
            <div class="upload-thesis">
              <span class="upload-thesis__marker">提案</span>
              <div><h3>上传只创建待发布 Revision</h3><p>服务端会校验 OpenAPI 版本、operationId 稳定性和 V1.5 Profile；校验通过后仍需人工发布。</p></div>
            </div>
            <el-upload drag :auto-upload="false" :show-file-list="false" accept=".json,.yaml,.yml" :disabled="uploading" :on-change="selectFile">
              <strong>拖放 OpenAPI 文件，或点击选择</strong>
              <p>UTF-8 JSON / YAML，最大 2 MiB</p>
            </el-upload>
            <div v-if="selectedFile" class="selected-contract-file"><div><strong>{{ selectedFile.name }}</strong><span>{{ formatBytes(selectedFile.size) }} · 本地校验通过</span></div><el-button link type="danger" :disabled="uploading" @click="clearFile">移除</el-button></div>
            <el-progress v-if="uploading || uploadPercent" class="upload-progress" :percentage="uploadPercent" :status="uploadPercent === 100 ? 'success' : undefined" />
            <el-alert v-if="fileError" class="inline-alert" :title="fileError" type="warning" show-icon />
            <el-alert v-if="uploadResult" class="inline-alert" :title="uploadReused ? '相同内容已存在，已复用原 Revision' : 'Revision 已创建并完成校验'" type="success" show-icon>
              <template #default><el-button link type="primary" @click="openRevision(uploadResult)">打开 Revision v{{ revisionNumber(uploadResult) }}</el-button></template>
            </el-alert>
            <div class="upload-actions"><el-button type="primary" :loading="uploading" :disabled="!selectedFile" @click="upload">上传并校验</el-button></div>
          </el-card>
          <template #fallback><el-empty description="只读账号不能上传 Revision" /></template>
        </PermissionGate>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.page-alert {
  margin-bottom: 14px;
}

.service-identity {
  display: grid;
  grid-template-columns: 1.3fr 1fr 1fr 1fr;
  overflow: hidden;
  margin-bottom: 16px;
  color: #e5f1ef;
  border-radius: 10px;
  background: #14272e;
}

.service-identity > div {
  min-width: 0;
  padding: 18px 20px;
  border-right: 1px solid rgb(255 255 255 / 10%);
}

.service-identity > div:last-child {
  border-right: 0;
}

.service-identity span,
.service-identity strong {
  display: block;
}

.service-identity > div > span:first-child {
  margin-bottom: 8px;
  color: #9ec7c1;
  font-size: 12px;
  letter-spacing: .04em;
}

.service-identity strong {
  color: #fff;
  font-size: 18px;
}

.contract-service-tabs {
  padding: 0 20px 20px;
  border: 1px solid #dbe7e4;
  border-radius: 10px;
  background: #fff;
}

.upload-thesis,
.selected-contract-file,
.upload-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.upload-thesis {
  align-items: flex-start;
  margin-bottom: 20px;
}

.upload-thesis__marker {
  padding: 6px 9px;
  color: #0f5f59;
  border: 1px solid #87b8b0;
  border-radius: 5px;
  background: #e8f4f1;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  letter-spacing: .08em;
}

.upload-thesis h3,
.upload-thesis p {
  margin: 0;
}

.upload-thesis p {
  margin-top: 6px;
  color: #647b80;
  line-height: 1.6;
}

.upload-card :deep(.el-upload),
.upload-card :deep(.el-upload-dragger) {
  width: 100%;
}

.upload-card :deep(.el-upload-dragger) {
  padding: 38px 20px;
  border-color: #83aaa4;
  background: #f3f9f7;
}

.upload-card :deep(.el-upload-dragger:hover) {
  border-color: #0f766e;
}

.upload-card :deep(.el-upload-dragger strong) {
  color: #14272e;
  font-size: 17px;
}

.upload-card :deep(.el-upload-dragger p),
.selected-contract-file span {
  color: #647b80;
}

.selected-contract-file {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid #dbe7e4;
  border-radius: 8px;
}

.selected-contract-file strong,
.selected-contract-file span {
  display: block;
}

.selected-contract-file span {
  margin-top: 4px;
  font-size: 13px;
}

.upload-progress,
.inline-alert,
.upload-actions {
  margin-top: 14px;
}

@media (max-width: 850px) {
  .service-identity {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .service-identity > div:nth-child(2) {
    border-right: 0;
  }
}

@media (max-width: 560px) {
  .service-identity {
    grid-template-columns: 1fr;
  }

  .service-identity > div {
    border-right: 0;
    border-bottom: 1px solid rgb(255 255 255 / 10%);
  }

  .contract-service-tabs {
    padding-inline: 12px;
  }

  .upload-thesis {
    flex-direction: column;
  }
}
</style>
