<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import FilterBar from '../components/FilterBar.vue'
import PageHeader from '../components/PageHeader.vue'
import PaginationBar from '../components/PaginationBar.vue'
import PermissionGate from '../components/PermissionGate.vue'
import StatusTag from '../components/StatusTag.vue'
import { useSessionStore } from '../stores/session'
import {
  buildContractServiceListParams,
  contractStatusOptions,
  createContractService,
  extractContractProjects,
  extractContractServices,
  extractPagination,
  isStableServiceKey,
  listContractProjects,
  listContractServices,
  resolveContractProjectKey,
} from '../contractServices'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const projectKey = ref(String(route.query.project_key || ''))
const status = ref(String(route.query.status || ''))
const keyword = ref(String(route.query.keyword || ''))
const projects = ref([])
const rows = ref([])
const loading = ref(false)
const error = ref(null)
const page = ref(Math.max(1, Number(route.query.page || 1) || 1))
const pageSize = ref(Math.max(1, Number(route.query.page_size || 20) || 20))
const total = ref(0)
const dialogVisible = ref(false)
const saving = ref(false)
const formError = ref(null)
const form = ref({ project_key: '', service_key: '', name: '', description: '' })
let loadSequence = 0

const isAdmin = computed(() => session.isAdmin ?? Boolean(session.me?.permissions?.includes('admin')))

function serviceId(row) {
  return row?.id ?? row?.service_id
}

function revisionCount(row) {
  return row?.revision_count ?? row?.revisions?.length ?? 0
}

function openDetail(row) {
  const id = serviceId(row)
  if (id !== undefined && id !== null) router.push({ name: 'contract-service-detail', params: { serviceId: id } })
}

function openCreate() {
  form.value = { project_key: projectKey.value.trim(), service_key: '', name: '', description: '' }
  formError.value = null
  dialogVisible.value = true
}

async function saveService() {
  const payload = {
    project_key: form.value.project_key.trim(),
    service_key: form.value.service_key.trim(),
    name: form.value.name.trim(),
    description: form.value.description.trim(),
  }
  if (!payload.project_key || !payload.service_key || !payload.name) {
    formError.value = new Error('请填写项目、稳定服务标识和服务名称')
    return
  }
  if (!isStableServiceKey(payload.service_key)) {
    formError.value = new Error('服务标识需使用 2–80 位小写字母、数字、点、短横线或下划线，并以字母或数字开头')
    return
  }
  if (saving.value) return
  saving.value = true
  formError.value = null
  try {
    const result = await createContractService(payload)
    dialogVisible.value = false
    await load()
    const created = result?.data || {}
    if (created.id || created.service_id) openDetail(created)
  } catch (cause) {
    formError.value = cause
  } finally {
    saving.value = false
  }
}

function currentParams() {
  return buildContractServiceListParams({
    projectKey: projectKey.value.trim(),
    status: status.value,
    keyword: keyword.value.trim(),
    page: page.value,
    pageSize: pageSize.value,
  })
}

function routeParams() {
  return buildContractServiceListParams({
    projectKey: String(route.query.project_key || ''),
    status: String(route.query.status || ''),
    keyword: String(route.query.keyword || ''),
    page: Math.max(1, Number(route.query.page || 1) || 1),
    pageSize: Math.max(1, Number(route.query.page_size || 20) || 20),
  })
}

function applyRouteParams(params) {
  projectKey.value = String(params.project_key || '')
  status.value = String(params.status || '')
  keyword.value = String(params.keyword || '')
  page.value = Number(params.page || 1)
  pageSize.value = Number(params.page_size || 20)
}

async function syncQuery(params) {
  await router.replace({
    name: 'contract-services',
    query: Object.fromEntries(Object.entries(params).map(([key, value]) => [key, String(value)])),
  })
}

async function load({ sync = true } = {}) {
  const sequence = ++loadSequence
  loading.value = true
  error.value = null
  const params = currentParams()
  if (sync) await syncQuery(params)
  try {
    const result = await listContractServices(params)
    if (sequence !== loadSequence) return
    rows.value = extractContractServices(result)
    total.value = extractPagination(result, page.value, pageSize.value).total
  } catch (cause) {
    if (sequence !== loadSequence) return
    rows.value = []
    total.value = 0
    error.value = cause
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function reset() {
  projectKey.value = ''
  status.value = ''
  keyword.value = ''
  page.value = 1
  load()
}

async function loadProjects() {
  try {
    const result = await listContractProjects()
    projects.value = extractContractProjects(result)
    if (!projectKey.value) projectKey.value = resolveContractProjectKey(projects.value, session.me?.project_key)
  } catch (cause) {
    projects.value = []
    error.value = cause
  }
}

onMounted(async () => {
  await loadProjects()
  await load()
})
watch(() => route.fullPath, () => {
  const incoming = routeParams()
  if (JSON.stringify(incoming) === JSON.stringify(currentParams())) return
  applyRouteParams(incoming)
  load({ sync: false })
})
</script>

<template>
  <PageHeader eyebrow="接口治理 / OpenAPI Revision" title="接口契约" description="用稳定服务标识组织 OpenAPI Revision，清楚区分待发布、已发布与历史版本。">
    <template #actions>
      <el-button :loading="loading" @click="load">刷新</el-button>
      <PermissionGate permission="admin"><el-button type="primary" @click="openCreate">创建服务</el-button></PermissionGate>
    </template>
  </PageHeader>

  <el-alert v-if="!isAdmin" class="page-alert" title="当前为只读访问，可查看服务与 Revision，但不能创建、上传或发布。" type="info" show-icon />

  <FilterBar>
    <el-form class="contract-filters" inline @submit.prevent="search">
      <el-form-item label="项目">
        <el-select v-model="projectKey" clearable filterable placeholder="全部授权项目" style="width: 280px">
          <el-option v-for="project in projects" :key="project.project_key" :label="`${project.name || project.project_key}（${project.project_key}）`" :value="project.project_key" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="status" clearable placeholder="全部状态" style="width: 150px">
          <el-option v-for="option in contractStatusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键词">
        <el-input v-model="keyword" clearable placeholder="服务名称或 service_key" @keyup.enter="search" />
      </el-form-item>
    </el-form>
    <template #actions><el-button @click="reset">重置</el-button><el-button type="primary" :loading="loading" @click="search">查询</el-button></template>
  </FilterBar>

  <ErrorState v-if="error" :error="error" @retry="load" />
  <el-card class="contract-list-card" shadow="never">
    <el-table v-loading="loading" :data="rows" row-key="id" empty-text="暂无符合条件的接口契约服务" @row-click="openDetail">
      <el-table-column label="服务" min-width="260">
        <template #default="scope"><div class="service-name-cell"><strong>{{ scope.row.name || scope.row.service_key }}</strong><CopyableText :value="scope.row.service_key" /></div></template>
      </el-table-column>
      <el-table-column prop="project_key" label="项目" min-width="170" />
      <el-table-column label="状态" width="126"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
      <el-table-column label="Revision" width="112"><template #default="scope">{{ revisionCount(scope.row) }}</template></el-table-column>
      <el-table-column label="当前发布" width="120"><template #default="scope">{{ scope.row.published_revision_number ? `v${scope.row.published_revision_number}` : '-' }}</template></el-table-column>
      <el-table-column label="更新时间" min-width="180"><template #default="scope"><DateTime :value="scope.row.updated_at || scope.row.created_at" /></template></el-table-column>
      <el-table-column label="操作" width="116" fixed="right"><template #default="scope"><el-button link type="primary" @click.stop="openDetail(scope.row)">查看详情</el-button></template></el-table-column>
    </el-table>
    <PaginationBar v-if="total > 0" v-model:page="page" v-model:page-size="pageSize" :total="total" @change="load" />
  </el-card>

  <el-dialog v-model="dialogVisible" title="创建接口契约服务" width="min(560px, 92vw)" :close-on-click-modal="!saving">
    <el-alert v-if="formError" class="dialog-alert" :title="formError.message || String(formError)" type="error" show-icon>
      <template v-if="formError.requestId" #default><span>请求 ID：</span><CopyableText :value="formError.requestId" /></template>
    </el-alert>
    <el-form label-position="top" class="contract-form" @submit.prevent="saveService">
      <el-form-item label="项目" required>
        <el-select v-model="form.project_key" filterable placeholder="请选择授权项目" style="width: 100%">
          <el-option v-for="project in projects" :key="project.project_key" :label="`${project.name || project.project_key}（${project.project_key}）`" :value="project.project_key" />
        </el-select>
      </el-form-item>
      <el-form-item label="稳定服务标识" required>
        <el-input v-model="form.service_key" maxlength="80" placeholder="例如 order-api" class="mono-input" />
        <div class="field-hint">创建后作为项目内稳定标识使用，建议与仓库或部署服务名称一致。</div>
      </el-form-item>
      <el-form-item label="服务名称" required><el-input v-model="form.name" maxlength="120" placeholder="例如 订单服务" /></el-form-item>
      <el-form-item label="服务说明"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="说明服务边界与主要消费者" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveService">创建服务</el-button></template>
  </el-dialog>
</template>

<style scoped>
.page-alert {
  margin-bottom: 14px;
}

.contract-filters {
  display: flex;
  flex: 1 1 680px;
  flex-wrap: wrap;
  gap: 0 6px;
}

.contract-list-card {
  overflow: hidden;
}

.service-name-cell strong,
.service-name-cell :deep(.copyable-text) {
  display: block;
}

.service-name-cell strong {
  margin-bottom: 5px;
  color: #14272e;
}

.dialog-alert {
  margin-bottom: 16px;
}

.mono-input :deep(input) {
  font-family: "Cascadia Code", Consolas, monospace;
}

@media (max-width: 768px) {
  .contract-filters :deep(.el-form-item),
  .contract-filters :deep(.el-input),
  .contract-filters :deep(.el-select) {
    width: 100% !important;
  }
}
</style>
