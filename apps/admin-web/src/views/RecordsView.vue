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
    label: 'Memory Candidates',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['title', 'Title', 220], ['level', 'Level', 100],
      ['scope', 'Scope', 120], ['memory_type', 'Memory Type', 150], ['status', 'Status', 130],
      ['model_confidence', 'Confidence', 120], ['abstain', 'Abstain', 100], ['created_at', 'Created At', 220],
    ],
  },
  memories: {
    label: 'Accepted Memories',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['title', 'Title', 240], ['level', 'Level', 100],
      ['memory_type', 'Memory Type', 160], ['scope', 'Scope', 120], ['confidence', 'Confidence', 120],
      ['status', 'Status', 130], ['created_at', 'Created At', 220],
    ],
  },
  jobs: {
    label: 'Processing Jobs',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['job_type', 'Job Type', 190], ['job_key', 'Job Key', 300],
      ['status', 'Status', 130], ['attempt_count', 'Attempts', 110], ['last_error_code', 'Last Error', 150], ['created_at', 'Created At', 220],
    ],
  },
  'raw-records': {
    label: 'Raw Records',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['event_key', 'Event Key', 240], ['role', 'Role', 110],
      ['content', 'Content', 360], ['source', 'Source', 120], ['created_at', 'Created At', 220],
    ],
  },
  'outbox-events': {
    label: 'Outbox Events',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['event_type', 'Event Type', 220], ['status', 'Status', 130],
      ['attempt_count', 'Attempts', 110], ['created_at', 'Created At', 220],
    ],
  },
  'retrieval-audits': {
    label: 'Retrieval Audits',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['retrieval_mode', 'Retrieval Mode', 160], ['degraded', 'Degraded', 110],
      ['degraded_reason', 'Degraded Reason', 220], ['latency_ms', 'Latency (ms)', 130],
    ],
  },
  'audit-events': {
    label: 'Audit Events',
    columns: [
      ['id', 'ID', 90], ['project_id', 'Project', 100], ['event_type', 'Event Type', 220], ['subject_type', 'Subject Type', 150],
      ['subject_id', 'Subject ID', 220], ['created_at', 'Created At', 220],
    ],
  },
}

const heading = computed(() => definitions[kind.value]?.label || 'Read-only Data')
const columns = computed(() => definitions[kind.value]?.columns || definitions.candidates.columns)

function stringify(value) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

async function load() {
  loading.value = true
  error.value = ''
  router.replace({ path: '/records', query: { kind: kind.value } })
  try {
    const result = await adminGet(`/${kind.value}`, { page: 1, page_size: 50 })
    rows.value = Array.isArray(result.data) ? result.data : []
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
    <div><span class="eyebrow">DATA EXPLORER</span><h2>{{ heading }}</h2></div>
    <el-select v-model="kind" style="width: 210px" @change="load">
      <el-option v-for="(definition, value) in definitions" :key="value" :label="definition.label" :value="value" />
    </el-select>
  </section>
  <el-alert v-if="error" :title="error" type="warning" show-icon />
  <el-table v-loading="loading" :data="rows" stripe :row-key="(row) => row.id" empty-text="No records">
    <el-table-column v-for="column in columns" :key="column[0]" :prop="column[0]" :label="column[1]" :min-width="column[2]">
      <template #default="scope">{{ stringify(scope.row[column[0]]) }}</template>
    </el-table-column>
  </el-table>
</template>