<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'
import {
  createContractService,
  extractContractProjects,
  extractContractServices,
  extractPagination,
  listContractProjects,
  listContractServices,
  resolveContractProjectKey,
} from '../contractServices'

const router = useRouter()
const session = useSessionStore()
const projectKey = ref('')
const projects = ref([])
const rows = ref([])
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const dialogVisible = ref(false)
const saving = ref(false)
const formError = ref('')
const form = ref({ project_key: '', name: '', description: '' })

const statusLabels = { empty: '暂无 Revision', proposed: '待发布', published: '已发布' }

function displayStatus(value) {
  return statusLabels[value] || value || '未知'
}

function statusType(value) {
  if (value === 'published') return 'success'
  if (value === 'proposed') return 'warning'
  return 'info'
}

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
  form.value = { project_key: projectKey.value.trim(), name: '', description: '' }
  formError.value = ''
  dialogVisible.value = true
}

async function saveService() {
  if (!form.value.project_key.trim() || !form.value.name.trim()) {
    formError.value = '请填写项目键和服务名称'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    const result = await createContractService({
      project_key: form.value.project_key.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim(),
    })
    dialogVisible.value = false
    await load()
    const created = result?.data || {}
    if (created.id || created.service_id) openDetail(created)
  } catch (cause) {
    formError.value = cause.message
  } finally {
    saving.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await listContractServices({ project_key: projectKey.value.trim(), page: page.value, page_size: pageSize.value })
    rows.value = extractContractServices(result)
    total.value = extractPagination(result, page.value, pageSize.value).total
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

function onPageChange(value) {
  page.value = value
  load()
}

function onProjectChange() {
  page.value = 1
  load()
}

async function loadProjects() {
  try {
    const result = await listContractProjects()
    projects.value = extractContractProjects(result)
    projectKey.value = resolveContractProjectKey(projects.value, session.me?.project_key)
  } catch (cause) {
    projects.value = []
    error.value = cause.message
  }
}

onMounted(async () => {
  await loadProjects()
  await load()
})
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">OpenAPI Revision</span><h2>接口契约</h2></div>
    <div class="heading-actions"><el-button :loading="loading" @click="load">刷新</el-button><el-button type="primary" @click="openCreate">创建服务</el-button></div>
  </section>

  <el-card class="contract-filter-card">
    <el-form inline @submit.prevent="load">
      <el-form-item label="项目">
        <el-select v-model="projectKey" clearable filterable placeholder="全部授权项目" style="width: 280px" @change="onProjectChange">
          <el-option v-for="project in projects" :key="project.project_key" :label="`${project.name || project.project_key}（${project.project_key}）`" :value="project.project_key" />
        </el-select>
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
    </el-form>
  </el-card>

  <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error = ''" />
  <el-table v-loading="loading" :data="rows" stripe row-key="id" empty-text="暂无接口契约服务" @row-click="openDetail">
    <el-table-column prop="name" label="服务名称" min-width="220" />
    <el-table-column prop="project_key" label="项目键" min-width="180" />
    <el-table-column label="状态" width="120"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ displayStatus(scope.row.status) }}</el-tag></template></el-table-column>
    <el-table-column label="Revision 数量" width="140"><template #default="scope">{{ revisionCount(scope.row) }}</template></el-table-column>
    <el-table-column prop="published_revision_number" label="已发布 Revision" width="150" />
    <el-table-column label="操作" width="120" fixed="right"><template #default="scope"><el-button link type="primary" @click.stop="openDetail(scope.row)">查看详情</el-button></template></el-table-column>
  </el-table>
  <div v-if="total > 0" class="task-pagination"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" background layout="total, prev, pager, next" :total="total" @current-change="onPageChange" /></div>

  <el-dialog v-model="dialogVisible" title="创建接口契约服务" width="520px">
    <el-alert v-if="formError" :title="formError" type="error" show-icon />
    <el-form label-position="top" class="contract-form" @submit.prevent="saveService">
      <el-form-item label="项目" required>
        <el-select v-model="form.project_key" filterable placeholder="请选择授权项目" style="width: 100%">
          <el-option v-for="project in projects" :key="project.project_key" :label="`${project.name || project.project_key}（${project.project_key}）`" :value="project.project_key" />
        </el-select>
      </el-form-item>
      <el-form-item label="服务名称" required><el-input v-model="form.name" placeholder="例如 订单服务" /></el-form-item>
      <el-form-item label="服务说明"><el-input v-model="form.description" type="textarea" :rows="3" placeholder="可选" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveService">创建服务</el-button></template>
  </el-dialog>
</template>
