<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { adminGet } from '../api'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { useVisibilityRefresh } from '../composables/useVisibilityRefresh'

const router = useRouter()
const loading = ref(true)
const refreshing = ref(false)
const error = ref(null)
const data = ref({})
const requestId = ref('')

const migrationText = computed(() => data.value.migration_schema === 'ok' ? '结构就绪' : '需要检查')
const overallStatus = computed(() => {
  if (error.value || data.value.database === 'error' || Number(data.value.dead_letters || 0) > 0) return 'error'
  if (data.value.migration_schema !== 'ok' || Number(data.value.pending_jobs || 0) > 0 || Number(data.value.server_outbox || 0) > 0) return 'pending'
  return 'ok'
})
const checks = computed(() => [
  { label: '管理 API', status: error.value ? 'error' : 'ok', value: error.value ? '请求失败' : '响应正常', note: '当前浏览器已成功访问正式 Admin API' },
  { label: '数据库', status: data.value.database || 'unknown', value: data.value.database === 'ok' ? '连接正常' : '连接异常', note: `数据库方言：${data.value.dialect || '未知'}` },
  { label: '迁移', status: data.value.migration_schema === 'ok' ? 'ok' : 'pending', value: migrationText.value, note: data.value.latest_migration || '未获取到迁移版本' },
  { label: '任务队列', status: Number(data.value.pending_jobs || 0) ? 'pending' : 'ok', value: `${data.value.pending_jobs ?? 0} 个待处理`, note: '处理任务等待 Worker 消费', to: '/records', query: { kind: 'jobs', status: 'pending' } },
  { label: 'Outbox', status: Number(data.value.server_outbox || 0) ? 'pending' : 'ok', value: `${data.value.server_outbox ?? 0} 个待投递`, note: '服务端事件等待可靠投递', to: '/records', query: { kind: 'outbox-events', status: 'pending' } },
  { label: '死信', status: Number(data.value.dead_letters || 0) ? 'error' : 'ok', value: `${data.value.dead_letters ?? 0} 个`, note: '无法继续投递的事件', to: '/records', query: { kind: 'outbox-events', status: 'dead' } },
])

async function refresh(manual = false) {
  if (manual) refreshing.value = true
  else if (!Object.keys(data.value).length) loading.value = true
  error.value = null
  try {
    const result = await adminGet('/system/status')
    data.value = result.data || {}
    requestId.value = result.request_id || ''
  } catch (requestError) {
    error.value = requestError
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function open(item) {
  if (item.to) router.push({ path: item.to, query: item.query })
}

const { lastUpdatedAt } = useVisibilityRefresh(refresh, 30000)
onMounted(refresh)
</script>

<template>
  <section v-loading="loading">
    <PageHeader eyebrow="运行健康度" title="系统状态" description="检查 API、数据库、迁移、任务队列与可靠投递链路。页面在可见时每 30 秒自动刷新。">
      <template #meta><span v-if="lastUpdatedAt" class="last-updated">最近刷新：<DateTime :value="lastUpdatedAt" /></span></template>
      <template #actions><el-button :loading="refreshing" @click="refresh(true)"><el-icon><Refresh /></el-icon>立即刷新</el-button></template>
    </PageHeader>

    <ErrorState v-if="error && !Object.keys(data).length" :error="error" @retry="refresh(true)" />
    <template v-else>
      <div class="health-banner" :class="`health-banner--${overallStatus}`">
        <div><span class="health-banner__pulse" /><div><span class="eyebrow">综合状态</span><h3>{{ overallStatus === 'ok' ? '关键链路运行正常' : overallStatus === 'error' ? '发现需要立即处理的异常' : '系统可用，但存在待处理事项' }}</h3></div></div>
        <StatusTag :status="overallStatus" :label="overallStatus === 'ok' ? '健康' : overallStatus === 'error' ? '异常' : '有待办'" />
      </div>

      <div class="health-grid">
        <button v-for="item in checks" :key="item.label" class="health-card" :class="{ 'is-actionable': item.to }" :disabled="!item.to" @click="open(item)">
          <div><span>{{ item.label }}</span><StatusTag :status="item.status" :label="item.status === 'ok' ? '正常' : item.status === 'error' ? '异常' : item.status === 'pending' ? '待处理' : '未知'" /></div>
          <strong>{{ item.value }}</strong><small>{{ item.note }}</small>
        </button>
      </div>

      <section class="system-detail-card">
        <div class="section-heading"><div><span class="eyebrow">运行明细</span><h2>运行信息</h2></div><span v-if="requestId" class="muted">请求 ID：<CopyableText :value="requestId" /></span></div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="数据库方言"><code>{{ data.dialect || '未知' }}</code></el-descriptions-item>
          <el-descriptions-item label="迁移版本"><CopyableText :value="data.latest_migration || '未知'" /></el-descriptions-item>
          <el-descriptions-item label="待处理任务">{{ data.pending_jobs ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="待投递 Outbox">{{ data.server_outbox ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="死信事件">{{ data.dead_letters ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="自动刷新">页面可见时每 30 秒</el-descriptions-item>
        </el-descriptions>
      </section>
    </template>
  </section>
</template>
