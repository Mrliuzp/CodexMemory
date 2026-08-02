<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Search } from '@element-plus/icons-vue'
import { adminGet } from '../api'
import CopyableText from '../components/CopyableText.vue'
import DataTable from '../components/DataTable.vue'
import DateTime from '../components/DateTime.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import ErrorState from '../components/ErrorState.vue'
import ExpandableText from '../components/ExpandableText.vue'
import FilterBar from '../components/FilterBar.vue'
import PageHeader from '../components/PageHeader.vue'
import PaginationBar from '../components/PaginationBar.vue'
import StatusTag from '../components/StatusTag.vue'
import StructuredDataViewer from '../components/StructuredDataViewer.vue'
import { useRouteQuery } from '../composables/useRouteQuery'
import { useContextStore } from '../stores/context'
import { displayValue, localDateTimeToIso, scopeDisplayName } from '../utils/format'

const route = useRoute()
const router = useRouter()
const context = useContextStore()
const { state: filters, commit } = useRouteQuery({
  kind: 'candidates', project_key: '', scope_id: '', status: '', level: '', type: '', secondary_type: '',
  role: '', degraded: '', retrieval_mode: '', keyword: '', created_from: '', created_to: '', sort: 'created_at',
  order: 'desc', page: 1, page_size: 20, detail: '', detail_tab: 'overview',
}, ['page', 'page_size'])
const rows = ref([])
const meta = ref({ page: 1, page_size: 20, total: 0, has_next: false })
const loading = ref(true)
const error = ref(null)

const definitions = {
  'raw-records': {
    label: '原始记录', hint: 'L0', typeLabel: '',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'event_key', label: '事件键', minWidth: 220, type: 'mono', truncate: 28 },
      { key: 'role', label: '角色', width: 100 }, { key: 'content', label: '内容', minWidth: 360 }, { key: 'source', label: '来源', width: 120 }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  candidates: {
    label: '候选记忆', hint: 'Candidate', typeLabel: '记忆类型',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'title', label: '标题', minWidth: 220 }, { key: 'level', label: '层级', width: 86 },
      { key: 'memory_type', label: '记忆类型', width: 150 }, { key: 'model_confidence', label: '模型置信度', width: 112 }, { key: 'status', label: '状态', width: 112, type: 'status' }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  memories: {
    label: '正式记忆', hint: 'Memory', typeLabel: '记忆类型',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'title', label: '标题', minWidth: 240 }, { key: 'level', label: '层级', width: 86 },
      { key: 'memory_type', label: '记忆类型', width: 150 }, { key: 'confidence', label: '置信度', width: 100 }, { key: 'status', label: '状态', width: 112, type: 'status' }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  jobs: {
    label: '处理任务', hint: 'Worker', typeLabel: '任务类型',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'job_type', label: '任务类型', minWidth: 165 }, { key: 'job_key', label: '任务键', minWidth: 240, type: 'mono', truncate: 32 },
      { key: 'status', label: '状态', width: 112, type: 'status' }, { key: 'attempt_count', label: '尝试次数', width: 100, align: 'center' }, { key: 'last_error_code', label: '最近错误', width: 150 }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  'outbox-events': {
    label: 'Outbox 事件', hint: 'Outbox', typeLabel: '事件类型',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'event_type', label: '事件类型', minWidth: 220 }, { key: 'status', label: '状态', width: 112, type: 'status' },
      { key: 'attempt_count', label: '尝试次数', width: 100, align: 'center' }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  'retrieval-audits': {
    label: '检索审计', hint: 'Retrieval', typeLabel: '',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'retrieval_mode', label: '检索模式', width: 150 }, { key: 'degraded', label: '已降级', width: 100 },
      { key: 'degraded_reason', label: '降级原因', minWidth: 240 }, { key: 'latency_ms', label: '延迟（毫秒）', width: 120, align: 'right' }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
  'audit-events': {
    label: '审计事件', hint: 'Audit', typeLabel: '事件类型', secondaryTypeLabel: '主体类型',
    columns: [
      { key: 'id', label: 'ID', width: 84, type: 'mono' }, { key: 'event_type', label: '事件类型', minWidth: 190 }, { key: 'subject_type', label: '主体类型', width: 150 },
      { key: 'subject_id', label: '主体 ID', minWidth: 190, type: 'mono', truncate: 24 }, { key: 'created_at', label: '创建时间', width: 176, type: 'date' },
    ],
  },
}

const definition = computed(() => definitions[filters.kind] || definitions.candidates)
const currentRow = computed(() => rows.value.find((item) => String(item.id) === String(filters.detail)) || null)
const drawerOpen = computed({ get: () => Boolean(filters.detail), set: (value) => { if (!value) commit({ detail: '', detail_tab: 'overview' }) } })
const dateRange = computed({
  get: () => filters.created_from && filters.created_to ? [filters.created_from, filters.created_to] : [],
  set: (value) => { filters.created_from = value?.[0] || ''; filters.created_to = value?.[1] || '' },
})
const showsContentNotice = computed(() => ['raw-records', 'candidates', 'memories'].includes(filters.kind))

function buildParams() {
  const params = {
    project_key: filters.project_key, scope_id: filters.scope_id, created_from: localDateTimeToIso(filters.created_from),
    created_to: localDateTimeToIso(filters.created_to), sort: filters.sort, order: filters.order, page: filters.page, page_size: filters.page_size,
  }
  if (['candidates', 'memories', 'jobs', 'outbox-events'].includes(filters.kind)) params.status = filters.status
  if (['candidates', 'memories'].includes(filters.kind)) { params.level = filters.level; params.memory_type = filters.type; params.keyword = filters.keyword }
  if (filters.kind === 'raw-records') { params.role = filters.role; params.keyword = filters.keyword }
  if (filters.kind === 'jobs') { params.job_type = filters.type; params.keyword = filters.keyword }
  if (filters.kind === 'outbox-events') params.event_type = filters.type
  if (filters.kind === 'retrieval-audits') { params.degraded = filters.degraded; params.retrieval_mode = filters.retrieval_mode }
  if (filters.kind === 'audit-events') { params.event_type = filters.type; params.subject_type = filters.secondary_type }
  return params
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await adminGet(`/${filters.kind}`, buildParams())
    rows.value = Array.isArray(result.data) ? result.data : []
    meta.value = { page: Number(result.meta?.page || filters.page), page_size: Number(result.meta?.page_size || filters.page_size), total: Number(result.meta?.total || 0), has_next: Boolean(result.meta?.has_next) }
  } catch (cause) {
    rows.value = []
    meta.value = { page: filters.page, page_size: filters.page_size, total: 0, has_next: false }
    error.value = cause
  } finally {
    loading.value = false
  }
}

async function changeKind(value) {
  await commit({ kind: value, status: '', level: '', type: '', secondary_type: '', role: '', degraded: '', retrieval_mode: '', keyword: '', page: 1, detail: '', detail_tab: 'overview' })
}

async function changeProject(value) {
  await context.selectProject(value)
  filters.scope_id = ''
}

async function applyFilters() {
  await commit({ ...filters, keyword: filters.keyword.trim(), page: 1 })
}

async function resetFilters() {
  await commit({ status: '', level: '', type: '', secondary_type: '', role: '', degraded: '', retrieval_mode: '', keyword: '', created_from: '', created_to: '', sort: 'created_at', order: 'desc', page: 1 })
}

function openDetail(row) { commit({ detail: row.id, detail_tab: 'overview' }) }
function changePagination(value) { commit({ page: value.page ?? filters.page, page_size: value.pageSize ?? filters.page_size }) }

watch(() => route.fullPath, load)
onMounted(async () => {
  await context.loadProjects()
  if (filters.project_key) await context.selectProject(filters.project_key)
  if (filters.scope_id) context.selectScope(filters.scope_id)
  await load()
})
</script>

<template>
  <PageHeader eyebrow="记忆数据账本" title="记忆账本" description="按资源、项目、Scope 与时间定位数据；筛选条件会写入地址，可直接复制给协作者。">
    <template #actions><el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>刷新数据</el-button></template>
  </PageHeader>

  <el-tabs :model-value="filters.kind" class="resource-tabs" @tab-change="changeKind">
    <el-tab-pane v-for="(item, key) in definitions" :key="key" :name="key"><template #label><span class="resource-tab-label"><small>{{ item.hint }}</small>{{ item.label }}</span></template></el-tab-pane>
  </el-tabs>

  <FilterBar :loading="loading">
    <el-select v-model="filters.project_key" clearable filterable placeholder="全部项目" aria-label="筛选项目" @change="changeProject"><el-option v-for="project in context.projects" :key="project.project_key" :label="project.name || project.project_key" :value="project.project_key" /></el-select>
    <el-select v-model="filters.scope_id" clearable filterable :disabled="!filters.project_key" placeholder="全部 Scope" aria-label="筛选 Scope"><el-option v-for="scope in context.scopes" :key="scope.id || scope.scope_key" :label="scopeDisplayName(scope)" :value="String(scope.scope_key || scope.id)" /></el-select>
    <el-select v-if="['candidates', 'memories', 'jobs', 'outbox-events'].includes(filters.kind)" v-model="filters.status" clearable placeholder="全部状态" aria-label="筛选状态"><el-option label="待处理" value="pending" /><el-option label="运行中" value="running" /><el-option label="已完成" value="completed" /><el-option label="失败" value="failed" /><el-option label="死信" value="dead" /><el-option label="已批准" value="approved" /><el-option label="已拒绝" value="rejected" /></el-select>
    <el-select v-if="['candidates', 'memories'].includes(filters.kind)" v-model="filters.level" clearable placeholder="全部层级" aria-label="筛选记忆层级"><el-option v-for="level in ['L0', 'L1', 'L2', 'L3']" :key="level" :label="level" :value="level" /></el-select>
    <el-select v-if="filters.kind === 'raw-records'" v-model="filters.role" clearable placeholder="全部角色" aria-label="筛选角色"><el-option label="用户" value="user" /><el-option label="助手" value="assistant" /><el-option label="系统" value="system" /></el-select>
    <el-select v-if="filters.kind === 'retrieval-audits'" v-model="filters.degraded" clearable placeholder="全部降级状态" aria-label="筛选降级状态"><el-option label="已降级" value="true" /><el-option label="未降级" value="false" /></el-select>
    <el-input v-if="filters.kind === 'retrieval-audits'" v-model="filters.retrieval_mode" clearable placeholder="检索模式" aria-label="筛选检索模式" />
    <el-input v-if="definition.typeLabel" v-model="filters.type" clearable :placeholder="definition.typeLabel" :aria-label="`筛选${definition.typeLabel}`" />
    <el-input v-if="definition.secondaryTypeLabel" v-model="filters.secondary_type" clearable :placeholder="definition.secondaryTypeLabel" :aria-label="`筛选${definition.secondaryTypeLabel}`" />
    <el-input v-if="['raw-records', 'candidates', 'memories', 'jobs'].includes(filters.kind)" v-model="filters.keyword" clearable placeholder="搜索关键词" aria-label="搜索关键词" @keyup.enter="applyFilters"><template #prefix><el-icon><Search /></el-icon></template></el-input>
    <el-date-picker v-model="dateRange" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss" start-placeholder="开始时间" end-placeholder="结束时间" range-separator="至" />
    <el-select v-model="filters.sort" placeholder="排序字段" aria-label="选择排序字段"><el-option label="创建时间" value="created_at" /><el-option label="ID" value="id" /><el-option label="状态" value="status" /></el-select>
    <el-select v-model="filters.order" placeholder="排序方向" aria-label="选择排序方向"><el-option label="由新到旧" value="desc" /><el-option label="由旧到新" value="asc" /></el-select>
    <template #actions><el-button @click="resetFilters">重置</el-button><el-button type="primary" @click="applyFilters">查询</el-button></template>
  </FilterBar>

  <el-alert v-if="showsContentNotice" class="redaction-notice" title="内容由服务端执行凭据脱敏；空值或省略字段可能代表受保护内容。" type="info" show-icon :closable="false" />
  <ErrorState v-if="error" :error="error" @retry="load" />
  <template v-else>
    <DataTable :rows="rows" :columns="definition.columns" :loading="loading" :empty-title="`暂无${definition.label}`" empty-description="当前筛选条件下没有数据。" @row-click="openDetail">
      <template #cell-content="{ value }"><ExpandableText :value="value" /></template>
      <template #cell-title="{ row, value }"><div class="record-title"><strong>{{ value || '未命名记录' }}</strong><small v-if="row.content">{{ String(row.content).slice(0, 80) }}</small></div></template>
      <template #cell-role="{ value }"><StatusTag :status="value" :label="({ user: '用户', assistant: '助手', system: '系统' })[value] || value" /></template>
      <template #cell-degraded="{ value }"><StatusTag :status="value ? 'degraded' : 'ok'" :label="value ? '已降级' : '正常'" /></template>
      <template #cell-model_confidence="{ value }">{{ value == null ? '-' : `${Math.round(Number(value) * 100)}%` }}</template>
      <template #cell-confidence="{ value }">{{ value == null ? '-' : `${Math.round(Number(value) * 100)}%` }}</template>
      <template #actions="{ row }"><el-button text type="primary" @click.stop="openDetail(row)">查看详情</el-button></template>
    </DataTable>
    <PaginationBar v-model:page="filters.page" v-model:page-size="filters.page_size" :total="meta.total" :disabled="loading" @change="changePagination" />
  </template>

  <DetailDrawer v-model="drawerOpen" :title="`${definition.label}详情`">
    <template v-if="currentRow">
      <el-tabs :model-value="filters.detail_tab" @tab-change="(value) => commit({ detail_tab: value })"><el-tab-pane label="概览" name="overview" /><el-tab-pane label="原始数据" name="raw" /></el-tabs>
      <el-descriptions v-if="filters.detail_tab === 'overview'" :column="1" border>
        <el-descriptions-item v-for="column in definition.columns" :key="column.key" :label="column.label"><DateTime v-if="column.type === 'date'" :value="currentRow[column.key]" /><StatusTag v-else-if="column.type === 'status'" :status="currentRow[column.key]" /><ExpandableText v-else-if="column.key === 'content'" :value="currentRow[column.key]" :limit="360" /><CopyableText v-else-if="column.type === 'mono'" :value="currentRow[column.key]" /><span v-else>{{ displayValue(currentRow[column.key]) }}</span></el-descriptions-item>
      </el-descriptions>
      <StructuredDataViewer v-else :value="currentRow" title="完整结构化数据" open />
    </template>
    <p v-else class="muted">该记录不在当前页，请关闭详情后重新定位。</p>
  </DetailDrawer>
</template>
