<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { adminGet } from '../api'

const route = useRoute()
const router = useRouter()
const kind = ref(route.query.kind || 'candidates')
const rows = ref([])
const loading = ref(false)
const error = ref('')

const definitions = {
  candidates: {
    label: '候选记忆',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['title', '标题', 220], ['level', '层级', 100],
      ['scope', '作用域', 120], ['memory_type', '记忆类型', 150], ['status', '状态', 130],
      ['model_confidence', '置信度', 120], ['abstain', '放弃', 100], ['created_at', '创建时间', 220],
    ],
  },
  memories: {
    label: '已接受记忆',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['title', '标题', 240], ['level', '层级', 100],
      ['memory_type', '记忆类型', 160], ['scope', '作用域', 120], ['confidence', '置信度', 120],
      ['status', '状态', 130], ['created_at', '创建时间', 220],
    ],
  },
  jobs: {
    label: '处理任务',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['job_type', '任务类型', 190], ['job_key', '任务键', 300],
      ['status', '状态', 130], ['attempt_count', '尝试次数', 110], ['last_error_code', '最近错误', 150], ['created_at', '创建时间', 220],
    ],
  },
  'raw-records': {
    label: '原始记录',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['event_key', '事件键', 240], ['role', '角色', 110],
      ['content', '内容', 360], ['source', '来源', 120], ['created_at', '创建时间', 220],
    ],
  },
  'outbox-events': {
    label: 'Outbox 事件',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['event_type', '事件类型', 220], ['status', '状态', 130],
      ['attempt_count', '尝试次数', 110], ['created_at', '创建时间', 220],
    ],
  },
  'retrieval-audits': {
    label: '检索审计',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['retrieval_mode', '检索模式', 160], ['degraded', '降级', 110],
      ['degraded_reason', '降级原因', 220], ['latency_ms', '延迟（毫秒）', 130],
    ],
  },
  'audit-events': {
    label: '审计事件',
    columns: [
      ['id', 'ID', 90], ['project_id', '项目', 100], ['event_type', '事件类型', 220], ['subject_type', '主体类型', 150],
      ['subject_id', '主体 ID', 220], ['created_at', '创建时间', 220],
    ],
  },
}

const heading = computed(() => definitions[kind.value]?.label || '只读数据')
const columns = computed(() => definitions[kind.value]?.columns || definitions.candidates.columns)

const displayLabels = { pending: '待处理', running: '运行中', completed: '已完成', failed: '失败', dead: '终止', retry_wait: '等待重试', generated: '已生成', approved: '已批准', rejected: '已拒绝', shadow: '影子（Shadow）', active: '活跃', inactive: '未启用', project: '项目级', global: '全局', user: '用户', assistant: '助手', system: '系统' }

function stringify(value, field) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string' && ['status', 'scope', 'role'].includes(field)) return displayLabels[value] || value
  return String(value)
}

async function load() {
  loading.value = true
  error.value = ''
  router.replace({ path: '/records', query: { kind: kind.value } })
  try {
    const result = await adminGet(`/${kind.value}`, { page: 1, page_size: 50 })
    // 兼容新旧两种后端响应格式
        var raw = result.data || result.candidates || result.memories || result.jobs || []
        rows.value = Array.isArray(raw) ? raw : []
  } catch (cause) {
    rows.value = []
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">数据浏览</span><h2>{{ heading }}</h2></div>
    <el-select v-model="kind" style="width: 210px" @change="load">
      <el-option v-for="(definition, value) in definitions" :key="value" :label="definition.label" :value="value" />
    </el-select>
  </section>
  <el-alert v-if="error" :title="error" type="warning" show-icon />
  <el-table v-loading="loading" :data="rows" stripe :row-key="(row) => row.id" empty-text="暂无记录">
    <el-table-column v-for="column in columns" :key="column[0]" :prop="column[0]" :label="column[1]" :min-width="column[2]">
      <template #default="scope">{{ stringify(scope.row[column[0]], column[0]) }}</template>
    </el-table-column>
  </el-table>
</template>