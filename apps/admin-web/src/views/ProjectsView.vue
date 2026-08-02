<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Refresh, Search } from '@element-plus/icons-vue'
import { adminGet } from '../api'
import CopyableText from '../components/CopyableText.vue'
import DataTable from '../components/DataTable.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import ErrorState from '../components/ErrorState.vue'
import FilterBar from '../components/FilterBar.vue'
import PageHeader from '../components/PageHeader.vue'
import PaginationBar from '../components/PaginationBar.vue'
import StatusTag from '../components/StatusTag.vue'
import { useRouteQuery } from '../composables/useRouteQuery'

const route = useRoute()
const router = useRouter()
const { state: filters, commit } = useRouteQuery({ keyword: '', status: '', page: 1, page_size: 20, detail: '' }, ['page', 'page_size'])
const allRows = ref([])
const loading = ref(true)
const error = ref(null)
const detailLoading = ref(false)
const detailError = ref(null)
const detail = ref(null)
const scopes = ref([])

const columns = [
  { key: 'name', label: '项目', minWidth: 190 },
  { key: 'project_key', label: '项目键', minWidth: 210, type: 'mono' },
  { key: 'repository', label: '仓库', minWidth: 250, type: 'mono', truncate: 38 },
  { key: 'scope_count', label: 'Scope', width: 100, align: 'center' },
  { key: 'status', label: '状态', width: 110, type: 'status' },
]

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return allRows.value.filter((row) => {
    const matchesKeyword = !keyword || [row.name, row.project_key, row.repository, row.description].some((value) => String(value || '').toLowerCase().includes(keyword))
    return matchesKeyword && (!filters.status || row.status === filters.status)
  })
})
const rows = computed(() => filteredRows.value.slice((filters.page - 1) * filters.page_size, filters.page * filters.page_size))
const drawerOpen = computed({
  get: () => Boolean(filters.detail),
  set: (value) => { if (!value) commit({ detail: '' }) },
})

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await adminGet('/projects', { page: 1, page_size: 200, sort: 'project_key', order: 'asc' })
    allRows.value = Array.isArray(result.data) ? result.data : []
    const maxPage = Math.max(1, Math.ceil(filteredRows.value.length / filters.page_size))
    if (filters.page > maxPage) await commit({ page: maxPage })
  } catch (cause) {
    allRows.value = []
    error.value = cause
  } finally {
    loading.value = false
  }
}

async function loadDetail(projectKey) {
  if (!projectKey) { detail.value = null; scopes.value = []; return }
  detailLoading.value = true
  detailError.value = null
  const [projectResult, scopeResult] = await Promise.allSettled([
    adminGet(`/projects/${encodeURIComponent(projectKey)}`),
    adminGet(`/projects/${encodeURIComponent(projectKey)}/scopes`, { page: 1, page_size: 200 }),
  ])
  if (projectResult.status === 'fulfilled') detail.value = projectResult.value.data || null
  else detailError.value = projectResult.reason
  if (scopeResult.status === 'fulfilled') scopes.value = Array.isArray(scopeResult.value.data) ? scopeResult.value.data : []
  detailLoading.value = false
}

async function applyFilters() {
  await commit({ keyword: filters.keyword.trim(), status: filters.status, page: 1 })
}

async function resetFilters() {
  await commit({ keyword: '', status: '', page: 1 })
}

async function openDetail(row) {
  await commit({ detail: row.project_key })
}

function changePagination(value) {
  commit({ page: value.page ?? filters.page, page_size: value.pageSize ?? filters.page_size })
}

function openProject(path, extra = {}) {
  router.push({ path, query: { project_key: filters.detail, ...extra } })
}

watch(() => filters.detail, loadDetail, { immediate: true })
watch(() => filters.page_size, () => commit({ page: 1 }))
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="项目注册表" title="项目与 Scope" description="查看当前身份可访问的项目、仓库与知识边界，并从项目直接进入数据、任务或契约。">
    <template #actions><el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>刷新项目</el-button></template>
  </PageHeader>

  <FilterBar :loading="loading">
    <el-input v-model="filters.keyword" clearable placeholder="搜索项目名、项目键或仓库" aria-label="搜索项目" @keyup.enter="applyFilters"><template #prefix><el-icon><Search /></el-icon></template></el-input>
    <el-select v-model="filters.status" clearable placeholder="全部状态" aria-label="筛选项目状态" @change="applyFilters"><el-option label="活跃" value="active" /><el-option label="未启用" value="inactive" /></el-select>
    <template #actions><el-button @click="resetFilters">重置</el-button><el-button type="primary" @click="applyFilters">查询</el-button></template>
  </FilterBar>

  <ErrorState v-if="error" :error="error" @retry="load" />
  <template v-else>
    <DataTable :rows="rows" :columns="columns" :loading="loading" empty-title="暂无匹配项目" empty-description="调整关键词或状态后再试。" @row-click="openDetail">
      <template #cell-name="{ row }"><div class="project-name-cell"><strong>{{ row.name || row.project_key }}</strong><small v-if="row.description">{{ row.description }}</small></div></template>
      <template #cell-project_key="{ value }"><CopyableText :value="value" /></template>
      <template #cell-repository="{ value }"><CopyableText :value="value" :truncate="42" /></template>
      <template #cell-scope_count="{ value }"><span class="count-pill">{{ value ?? '-' }}</span></template>
      <template #cell-status="{ value }"><StatusTag :status="value" /></template>
      <template #actions="{ row }"><el-button text type="primary" @click.stop="openDetail(row)">查看详情<el-icon><ArrowRight /></el-icon></el-button></template>
    </DataTable>
    <PaginationBar v-model:page="filters.page" v-model:page-size="filters.page_size" :total="filteredRows.length" :disabled="loading" @change="changePagination" />
  </template>

  <DetailDrawer v-model="drawerOpen" :title="detail?.name || filters.detail || '项目详情'" :loading="detailLoading">
    <ErrorState v-if="detailError" :error="detailError" compact @retry="loadDetail(filters.detail)" />
    <template v-else-if="detail">
      <div class="drawer-summary"><span class="eyebrow">项目档案</span><h3>{{ detail.name || detail.project_key }}</h3><p>{{ detail.description || '该项目尚未填写说明。' }}</p><StatusTag :status="detail.status" /></div>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="项目键"><CopyableText :value="detail.project_key" /></el-descriptions-item>
        <el-descriptions-item label="仓库"><CopyableText :value="detail.repository" /></el-descriptions-item>
        <el-descriptions-item label="Scope 数量">{{ scopes.length || detail.scope_count || 0 }}</el-descriptions-item>
      </el-descriptions>
      <section class="drawer-section"><h4>Scope</h4><div v-if="scopes.length" class="scope-list"><div v-for="scope in scopes" :key="scope.id || scope.scope_key"><div><strong>{{ scope.name || scope.scope_key }}</strong><small>{{ scope.description || scope.scope_key }}</small></div><StatusTag :status="scope.status || (scope.is_default ? 'active' : 'inactive')" :label="scope.is_default ? '默认' : ''" /></div></div><p v-else class="muted">暂无独立 Scope。</p></section>
      <section class="drawer-section"><h4>快捷入口</h4><div class="drawer-actions"><el-button type="primary" @click="openProject('/records')">查看记忆数据</el-button><el-button @click="openProject('/task-runs')">查看任务报告</el-button><el-button @click="openProject('/contract-services')">查看接口契约</el-button></div></section>
    </template>
  </DetailDrawer>
</template>
