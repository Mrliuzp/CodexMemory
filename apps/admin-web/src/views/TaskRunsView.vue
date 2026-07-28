<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { extractPagination, extractTaskRuns, listTaskRuns } from '../taskRuns'

const route = useRoute()
const router = useRouter()
const projectKey = ref(String(route.query.project_key || ''))
const rows = ref([])
const loading = ref(false)
const error = ref('')
const page = ref(Number(route.query.page || 1))
const pageSize = ref(Number(route.query.page_size || 20))
const total = ref(0)

const statusLabels = {
  pending: '待开始',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
  open: '进行中',
  closed: '已关闭',
}

function displayStatus(status) {
  return statusLabels[status] || status || '未知'
}

function statusType(status) {
  if (['completed', 'closed'].includes(status)) return 'success'
  if (['failed'].includes(status)) return 'danger'
  if (['running', 'open'].includes(status)) return 'warning'
  return 'info'
}

function displayTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function syncQuery() {
  const query = { page: String(page.value), page_size: String(pageSize.value) }
  if (projectKey.value.trim()) query.project_key = projectKey.value.trim()
  router.replace({ path: '/task-runs', query })
}

async function load() {
  loading.value = true
  error.value = ''
  syncQuery()
  try {
    const result = await listTaskRuns({ project_key: projectKey.value.trim(), page: page.value, page_size: pageSize.value })
    rows.value = extractTaskRuns(result)
    total.value = extractPagination(result, page.value, pageSize.value).total
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  if (!row?.id) return
  router.push({ name: 'task-run-detail', params: { id: row.id } })
}

function onPageChange(value) {
  page.value = value
  load()
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">执行观测</span><h2>任务报告</h2></div>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </section>

  <el-card class="task-filter-card">
    <el-form inline @submit.prevent="load">
      <el-form-item label="项目键">
        <el-input v-model="projectKey" clearable placeholder="可选，按项目筛选" @keyup.enter="load" />
      </el-form-item>
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
    </el-form>
  </el-card>

  <el-alert v-if="error" :title="error" type="warning" show-icon closable @close="error = ''" />
  <el-table v-loading="loading" :data="rows" stripe row-key="id" empty-text="暂无任务运行记录" @row-click="openDetail">
    <el-table-column prop="id" label="运行 ID" min-width="180" />
    <el-table-column prop="project_id" label="项目 ID" min-width="150" />
    <el-table-column prop="session_key" label="会话标识" min-width="220" show-overflow-tooltip />
    <el-table-column label="状态" width="120">
      <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ displayStatus(scope.row.status) }}</el-tag></template>
    </el-table-column>
    <el-table-column label="开始时间" min-width="180"><template #default="scope">{{ displayTime(scope.row.started_at) }}</template></el-table-column>
    <el-table-column label="结束时间" min-width="180"><template #default="scope">{{ displayTime(scope.row.ended_at) }}</template></el-table-column>
    <el-table-column label="操作" width="120" fixed="right">
      <template #default="scope"><el-button link type="primary" @click.stop="openDetail(scope.row)">查看报告</el-button></template>
    </el-table-column>
  </el-table>
  <div class="task-pagination" v-if="total > 0">
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" background layout="total, prev, pager, next" :total="total" @current-change="onPageChange" />
  </div>
</template>
