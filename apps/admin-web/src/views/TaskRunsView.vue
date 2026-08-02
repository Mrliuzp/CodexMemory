<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import FilterBar from '../components/FilterBar.vue'
import PageHeader from '../components/PageHeader.vue'
import PaginationBar from '../components/PaginationBar.vue'
import StatusTag from '../components/StatusTag.vue'
import { localDateTimeToIso } from '../utils/format'
import {
  buildTaskRunListParams,
  extractPagination,
  extractTaskRuns,
  listTaskRuns,
  taskRunProjectLabel,
  taskRunPrompt,
  taskRunStatusLabels,
  taskRunStatusOptions,
} from '../taskRuns'

const route = useRoute()
const router = useRouter()
const projectKey = ref(String(route.query.project_key || ''))
const status = ref(String(route.query.status || ''))
const uncertain = ref(String(route.query.uncertain || ''))
const keyword = ref(String(route.query.keyword || ''))
const startedFrom = ref(String(route.query.started_from || ''))
const startedTo = ref(String(route.query.started_to || ''))
const sortValue = ref(`${String(route.query.sort || 'created_at')}:${String(route.query.order || 'desc')}`)
const rows = ref([])
const loading = ref(false)
const error = ref(null)
const page = ref(Math.max(1, Number(route.query.page || 1) || 1))
const pageSize = ref(Math.max(1, Number(route.query.page_size || 20) || 20))
const total = ref(0)
let loadSequence = 0

function currentParams() {
  const [sort, order] = sortValue.value.split(':')
  return buildTaskRunListParams({
    projectKey: projectKey.value.trim(),
    status: status.value,
    uncertain: uncertain.value,
    keyword: keyword.value.trim(),
    startedFrom: startedFrom.value,
    startedTo: startedTo.value,
    sort,
    order,
    page: page.value,
    pageSize: pageSize.value,
  })
}

function routeParams() {
  const [sort, order] = `${String(route.query.sort || 'created_at')}:${String(route.query.order || 'desc')}`.split(':')
  return buildTaskRunListParams({
    projectKey: String(route.query.project_key || ''),
    status: String(route.query.status || ''),
    uncertain: String(route.query.uncertain || ''),
    keyword: String(route.query.keyword || ''),
    startedFrom: String(route.query.started_from || ''),
    startedTo: String(route.query.started_to || ''),
    sort,
    order,
    page: Math.max(1, Number(route.query.page || 1) || 1),
    pageSize: Math.max(1, Number(route.query.page_size || 20) || 20),
  })
}

function applyRouteParams(params) {
  projectKey.value = String(params.project_key || '')
  status.value = String(params.status || '')
  uncertain.value = String(params.uncertain || '')
  keyword.value = String(params.keyword || '')
  startedFrom.value = String(params.started_from || '')
  startedTo.value = String(params.started_to || '')
  sortValue.value = `${params.sort || 'created_at'}:${params.order || 'desc'}`
  page.value = Number(params.page || 1)
  pageSize.value = Number(params.page_size || 20)
}

async function syncQuery(params) {
  const query = Object.fromEntries(Object.entries(params).map(([key, value]) => [key, String(value)]))
  await router.replace({ name: 'task-runs', query })
}

async function load({ sync = true } = {}) {
  const sequence = ++loadSequence
  loading.value = true
  error.value = null
  const params = currentParams()
  if (sync) await syncQuery(params)
  try {
    const result = await listTaskRuns({
      ...params,
      started_from: localDateTimeToIso(params.started_from),
      started_to: localDateTimeToIso(params.started_to),
    })
    if (sequence !== loadSequence) return
    rows.value = extractTaskRuns(result)
    const pagination = extractPagination(result, page.value, pageSize.value)
    total.value = pagination.total
  } catch (cause) {
    if (sequence !== loadSequence) return
    rows.value = []
    total.value = 0
    error.value = cause
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

async function openDetail(row) {
  if (!row?.id) return
  router.push({ name: 'task-run-detail', params: { id: row.id } })
}

function search() {
  page.value = 1
  load()
}

function reset() {
  projectKey.value = ''
  status.value = ''
  uncertain.value = ''
  keyword.value = ''
  startedFrom.value = ''
  startedTo.value = ''
  sortValue.value = 'created_at:desc'
  page.value = 1
  load()
}

function attributionLabel(row) {
  if (row.uncertain === true) return '需要复核'
  if (row.uncertain === false) return '归因明确'
  return '暂无报告'
}

function attributionType(row) {
  if (row.uncertain === true) return 'warning'
  if (row.uncertain === false) return 'success'
  return 'info'
}

function onPaginationChange() {
  load()
}

watch(() => route.fullPath, () => {
  const incoming = routeParams()
  if (JSON.stringify(incoming) === JSON.stringify(currentParams())) return
  applyRouteParams(incoming)
  load({ sync: false })
})
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="运行监控 / 任务报告" title="任务运行" description="从脱敏任务摘要定位执行结果、文件影响与验证证据。">
    <template #actions><el-button :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>

  <FilterBar>
    <el-form class="task-run-filters" inline @submit.prevent="search">
      <el-form-item label="项目键">
        <el-input v-model="projectKey" clearable placeholder="全部项目" @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="status" clearable placeholder="全部状态" style="width: 150px">
          <el-option v-for="option in taskRunStatusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="归因">
        <el-select v-model="uncertain" clearable placeholder="全部报告" style="width: 150px">
          <el-option label="需要复核" value="true" />
          <el-option label="归因明确" value="false" />
        </el-select>
      </el-form-item>
      <el-form-item label="任务摘要">
        <el-input v-model="keyword" clearable placeholder="搜索脱敏摘要或会话标识" @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="开始时间">
        <el-date-picker v-model="startedFrom" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="起始时间" />
      </el-form-item>
      <el-form-item label="至">
        <el-date-picker v-model="startedTo" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="结束时间" />
      </el-form-item>
      <el-form-item label="排序">
        <el-select v-model="sortValue" style="width: 170px">
          <el-option label="最近创建" value="created_at:desc" />
          <el-option label="最早创建" value="created_at:asc" />
          <el-option label="最近开始" value="started_at:desc" />
          <el-option label="状态" value="status:asc" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #actions>
      <el-button @click="reset">重置</el-button>
      <el-button type="primary" :loading="loading" @click="search">查询</el-button>
    </template>
  </FilterBar>

  <ErrorState v-if="error" :error="error" @retry="load" />
  <el-card class="task-list-card" shadow="never">
    <el-table v-loading="loading" :data="rows" row-key="id" empty-text="暂无符合条件的任务运行" @row-click="openDetail">
      <el-table-column label="任务摘要" min-width="320">
        <template #default="scope">
          <div class="task-prompt-cell">
            <span class="task-prompt-text">{{ taskRunPrompt(scope.row) || '未记录任务摘要' }}</span>
            <el-tooltip v-if="scope.row.prompt_truncated" content="摘要已在服务端脱敏并截断，仅展示前 160 字" placement="top">
              <el-tag size="small" type="info">已脱敏截断</el-tag>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="项目" min-width="170"><template #default="scope"><span class="mono-value">{{ taskRunProjectLabel(scope.row) }}</span></template></el-table-column>
      <el-table-column label="状态" width="112"><template #default="scope"><StatusTag :status="scope.row.status" :label="taskRunStatusLabels[scope.row.status]" /></template></el-table-column>
      <el-table-column label="归因" width="122">
        <template #default="scope"><el-tag :type="attributionType(scope.row)" effect="plain">{{ attributionLabel(scope.row) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="报告版本" width="112"><template #default="scope"><span class="mono-value">{{ scope.row.current_report_revision ? `v${scope.row.current_report_revision}` : '-' }}</span></template></el-table-column>
      <el-table-column label="开始时间" min-width="180"><template #default="scope"><DateTime :value="scope.row.started_at" /></template></el-table-column>
      <el-table-column label="操作" width="116" fixed="right"><template #default="scope"><el-button link type="primary" @click.stop="openDetail(scope.row)">查看详情</el-button></template></el-table-column>
    </el-table>
    <PaginationBar v-if="total > 0" v-model:page="page" v-model:page-size="pageSize" :total="total" @change="onPaginationChange" />
  </el-card>
</template>

<style scoped>
.task-run-filters {
  display: flex;
  flex: 1 1 760px;
  flex-wrap: wrap;
  gap: 0 6px;
}

.task-list-card {
  overflow: hidden;
}

.task-prompt-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.task-prompt-text {
  display: -webkit-box;
  overflow: hidden;
  color: #14272e;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

@media (max-width: 768px) {
  .task-run-filters :deep(.el-form-item),
  .task-run-filters :deep(.el-input),
  .task-run-filters :deep(.el-select),
  .task-run-filters :deep(.el-date-editor) {
    width: 100% !important;
  }
}
</style>
