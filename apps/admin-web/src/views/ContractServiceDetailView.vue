<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { extractContractService, extractContractRevision, getContractService, uploadContractRevision } from '../contractServices'

const route = useRoute()
const router = useRouter()
const service = ref(null)
const loading = ref(false)
const uploading = ref(false)
const error = ref('')
const fileError = ref('')
const selectedFile = ref(null)
const uploadResult = ref(null)

const revisions = computed(() => service.value?.revisions || service.value?.revision_summaries || [])

const statusLabels = { proposed: '待发布', published: '已发布', superseded: '已被替代' }

function displayStatus(value) {
  return statusLabels[value] || value || '未知'
}

function statusType(value) {
  if (value === 'published') return 'success'
  if (value === 'proposed') return 'warning'
  return 'info'
}

function revisionNumber(row) {
  return row?.revision_number ?? row?.revision
}

function openRevision(row) {
  const number = revisionNumber(row)
  if (number !== undefined && number !== null) router.push({ name: 'contract-revision-detail', params: { serviceId: route.params.serviceId, revisionNumber: number } })
}

function selectFile(uploadFile) {
  const file = uploadFile?.raw || uploadFile
  selectedFile.value = null
  uploadResult.value = null
  fileError.value = ''
  if (!file) return
  const extension = String(file.name || '').toLowerCase().split('.').pop()
  if (!['json', 'yaml', 'yml'].includes(extension)) {
    fileError.value = '仅支持 JSON 或 YAML 文件'
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    fileError.value = '文件大小不能超过 2 MiB'
    return
  }
  selectedFile.value = file
}

async function upload() {
  if (!selectedFile.value) {
    fileError.value = '请先选择 JSON 或 YAML 文件'
    return
  }
  uploading.value = true
  error.value = ''
  fileError.value = ''
  try {
    const result = await uploadContractRevision(route.params.serviceId, selectedFile.value)
    uploadResult.value = extractContractRevision(result)
    selectedFile.value = null
    await load()
  } catch (cause) {
    error.value = cause.message
  } finally {
    uploading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    service.value = extractContractService(await getContractService(route.params.serviceId))
  } catch (cause) {
    service.value = null
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">接口契约 / 服务</span><h2>服务详情</h2></div>
    <div class="heading-actions"><el-button @click="router.push({ name: 'contract-services' })">返回列表</el-button><el-button :loading="loading" @click="load">刷新</el-button></div>
  </section>

  <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error = ''" />
  <div v-loading="loading" class="contract-detail">
    <el-card v-if="service" class="contract-card">
      <template #header><span>服务信息</span></template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="服务 ID">{{ service.id || service.service_id }}</el-descriptions-item>
        <el-descriptions-item label="服务名称">{{ service.name }}</el-descriptions-item>
        <el-descriptions-item label="项目键">{{ service.project_key }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusType(service.status)">{{ displayStatus(service.status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="已发布 Revision">{{ service.published_revision_number || '-' }}</el-descriptions-item>
        <el-descriptions-item label="服务说明">{{ service.description || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card v-if="service" class="contract-card">
      <template #header><span>上传 Revision</span></template>
      <div class="upload-row">
        <el-upload :auto-upload="false" :show-file-list="false" accept=".json,.yaml,.yml" :on-change="selectFile">
          <el-button>选择文件</el-button>
        </el-upload>
        <span class="selected-file">{{ selectedFile?.name || '未选择文件' }}</span>
        <el-button type="primary" :loading="uploading" @click="upload">上传并校验</el-button>
      </div>
      <div class="field-hint">仅上传本地 UTF-8 JSON/YAML 文件，大小不超过 2 MiB。上传只会创建待发布 Revision，不会自动发布。</div>
      <el-alert v-if="fileError" class="inline-alert" :title="fileError" type="warning" show-icon />
      <el-alert v-if="uploadResult" class="inline-alert" title="Revision 已创建或复用，请查看详情" type="success" show-icon>
        <template #default><el-button link type="primary" @click="openRevision(uploadResult)">打开 Revision 详情</el-button></template>
      </el-alert>
    </el-card>

    <el-card v-if="service" class="contract-card">
      <template #header><span>Revision 列表</span></template>
      <el-table :data="revisions" stripe empty-text="暂无 Revision">
        <el-table-column label="Revision" width="110"><template #default="scope">{{ revisionNumber(scope.row) }}</template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ displayStatus(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="content_hash" label="内容哈希" min-width="360" show-overflow-tooltip />
        <el-table-column prop="profile_version" label="Profile" width="110" />
        <el-table-column label="操作" width="130"><template #default="scope"><el-button link type="primary" @click="openRevision(scope.row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
