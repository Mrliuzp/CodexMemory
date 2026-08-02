<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Refresh } from '@element-plus/icons-vue'
import { adminGet } from '../api'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { useVisibilityRefresh } from '../composables/useVisibilityRefresh'
import { useSessionStore } from '../stores/session'
import { compactNumber } from '../utils/format'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const loading = ref(true)
const refreshing = ref(false)
const error = ref(null)
const systemError = ref(null)
const dashboard = ref({})
const system = ref({})
const audits = ref([])
let loadSequence = 0

const canReadSystem = computed(() => session.isAdmin || session.hasPermission('operations_read'))
const queryContext = computed(() => ({ ...(route.query.project_key ? { project_key: route.query.project_key } : {}) }))
const attention = computed(() => dashboard.value.attention || {})
const pipeline = computed(() => dashboard.value.pipeline || {})
const levelCounts = computed(() => pipeline.value.memories_by_level || pipeline.value.levels || {})

const pulse = computed(() => [
  { key: 'api', label: 'API', status: error.value ? 'error' : 'ok', value: error.value ? '连接失败' : '响应正常', to: canReadSystem.value ? '/system-status' : '' },
  { key: 'database', label: '数据库', status: canReadSystem.value ? (system.value.database || 'unknown') : 'restricted', value: canReadSystem.value ? (system.value.database === 'ok' ? '连接正常' : '需要检查') : '权限受限', to: canReadSystem.value ? '/system-status' : '' },
  { key: 'migration', label: '迁移', status: canReadSystem.value ? (system.value.migration_schema === 'ok' ? 'ok' : 'pending') : 'restricted', value: canReadSystem.value ? (system.value.latest_migration || '等待检查') : '权限受限', to: canReadSystem.value ? '/system-status' : '' },
  { key: 'jobs', label: '任务队列', status: Number(system.value.pending_jobs || 0) ? 'pending' : 'ok', value: `${system.value.pending_jobs ?? 0} 个待处理`, to: '/records', query: { kind: 'jobs', status: 'pending' } },
  { key: 'outbox', label: 'Outbox', status: Number(system.value.server_outbox || 0) ? 'pending' : 'ok', value: `${system.value.server_outbox ?? 0} 个待投递`, to: '/records', query: { kind: 'outbox-events', status: 'pending' } },
  { key: 'dead', label: '死信', status: Number(system.value.dead_letters || attention.value.dead_letters || 0) ? 'error' : 'ok', value: `${system.value.dead_letters ?? attention.value.dead_letters ?? 0} 个`, to: '/records', query: { kind: 'outbox-events', status: 'dead' } },
])

const pipelineSteps = computed(() => [
  { key: 'l0', label: 'L0 原始记录', value: pipeline.value.raw_records ?? dashboard.value.raw_records ?? 0, hint: '可追溯事实' },
  { key: 'candidate', label: 'Candidate', value: pipeline.value.candidates ?? dashboard.value.candidates ?? 0, hint: '等待治理' },
  { key: 'l1', label: 'L1 项目知识', value: pipeline.value.l1 ?? levelCounts.value.L1 ?? levelCounts.value.l1 ?? 0, hint: '工作记忆' },
  { key: 'l2', label: 'L2 稳定知识', value: pipeline.value.l2 ?? levelCounts.value.L2 ?? levelCounts.value.l2 ?? 0, hint: '复用规则' },
  { key: 'l3', label: 'L3 错误记忆', value: pipeline.value.l3 ?? levelCounts.value.L3 ?? levelCounts.value.l3 ?? 0, hint: '防止重犯' },
])

const attentionCards = computed(() => [
  { key: 'pending_candidates', label: '待审核候选', value: attention.value.pending_candidates ?? 0, tone: 'warning', to: '/records', query: { kind: 'candidates', status: 'pending' }, hint: '确认是否进入正式记忆' },
  { key: 'failed_jobs', label: '失败任务', value: attention.value.failed_jobs ?? 0, tone: 'danger', to: '/records', query: { kind: 'jobs', status: 'failed' }, hint: '查看错误并决定后续处理' },
  { key: 'dead_letters', label: '死信事件', value: attention.value.dead_letters ?? system.value.dead_letters ?? 0, tone: 'danger', to: '/records', query: { kind: 'outbox-events', status: 'dead' }, hint: '定位无法继续投递的事件' },
  { key: 'active_imports', label: '活跃导入', value: attention.value.active_imports ?? 0, tone: 'primary', to: '/imports', query: { status: 'active' }, hint: '跟踪上传与处理进度' },
  { key: 'uncertain_task_runs', label: '不确定 TaskRun', value: attention.value.uncertain_task_runs ?? 0, tone: 'warning', to: '/task-runs', query: { uncertain: 'true' }, hint: '核对归因与验证证据' },
  { key: 'proposed_revisions', label: '待发布 Revision', value: attention.value.proposed_revisions ?? 0, tone: 'primary', to: '/contract-services', query: { status: 'proposed' }, hint: '校验通过后再发布' },
])

async function load({ manual = false } = {}) {
  const sequence = ++loadSequence
  if (manual) refreshing.value = true
  else if (!Object.keys(dashboard.value).length) loading.value = true
  error.value = null
  systemError.value = null
  const requests = [
    adminGet('/dashboard', queryContext.value),
    adminGet('/audit-events', { ...queryContext.value, page: 1, page_size: 6, sort: 'created_at', order: 'desc' }),
    canReadSystem.value ? adminGet('/system/status') : Promise.resolve({ data: {} }),
  ]
  const [dashboardResult, auditResult, systemResult] = await Promise.allSettled(requests)
  if (sequence !== loadSequence) return
  if (dashboardResult.status === 'fulfilled') dashboard.value = dashboardResult.value.data || {}
  else error.value = dashboardResult.reason
  if (auditResult.status === 'fulfilled') audits.value = Array.isArray(auditResult.value.data) ? auditResult.value.data : []
  if (systemResult.status === 'fulfilled') system.value = systemResult.value.data || {}
  else systemError.value = systemResult.reason
  loading.value = false
  refreshing.value = false
}

function open(target, extra = {}) {
  if (!target) return
  router.push({ path: target, query: { ...queryContext.value, ...extra } })
}

const { lastUpdatedAt } = useVisibilityRefresh(load, 30000)
watch(() => route.query.project_key, () => load())
onMounted(load)
</script>

<template>
  <div v-loading="loading" class="dashboard">
    <PageHeader eyebrow="实时运行视图" title="系统与记忆，一眼看清" description="优先处理异常与待办，再沿记忆流水线追溯数据来源。页面在可见时每 30 秒自动刷新。">
      <template #meta><span v-if="lastUpdatedAt" class="last-updated">最近刷新：<DateTime :value="lastUpdatedAt" /></span></template>
      <template #actions><el-button :loading="refreshing" @click="load({ manual: true })"><el-icon><Refresh /></el-icon>立即刷新</el-button></template>
    </PageHeader>

    <ErrorState v-if="error && !Object.keys(dashboard).length" :error="error" @retry="load({ manual: true })" />
    <template v-else>
      <section class="system-pulse" aria-labelledby="system-pulse-title">
        <div class="system-pulse__head"><div><span class="pulse-beacon" aria-hidden="true"><i /></span><strong id="system-pulse-title">系统脉搏</strong></div><span>实时状态 · 六项关键检查</span></div>
        <div class="system-pulse__track">
          <button v-for="item in pulse" :key="item.key" class="pulse-node" :class="[`pulse-node--${item.status}`, { 'is-actionable': item.to }]" :disabled="!item.to" @click="open(item.to, item.query)">
            <span class="pulse-node__label">{{ item.label }}</span><StatusTag :status="item.status" :label="item.status === 'ok' ? '正常' : item.status === 'restricted' ? '受限' : item.status === 'error' ? '异常' : '有待办'" /><strong>{{ item.value }}</strong>
          </button>
        </div>
        <ErrorState v-if="systemError" compact :error="systemError" title="部分运行状态暂不可用" @retry="load({ manual: true })" />
      </section>

      <section class="dashboard-section">
        <div class="section-heading"><div><span class="eyebrow">记忆处理链</span><h2>记忆流水线</h2></div><span class="muted">L0 → Candidate → L1 / L2 / L3</span></div>
        <div class="memory-pipeline">
          <template v-for="(item, index) in pipelineSteps" :key="item.key">
            <button class="pipeline-step" @click="open('/records', { kind: item.key === 'l0' ? 'raw-records' : item.key === 'candidate' ? 'candidates' : 'memories', ...(item.key.startsWith('l') && item.key !== 'l0' ? { level: item.key.toUpperCase() } : {}) })">
              <span>{{ item.label }}</span><strong>{{ compactNumber(item.value) }}</strong><small>{{ item.hint }}</small>
            </button>
            <el-icon v-if="index < pipelineSteps.length - 1" class="pipeline-arrow"><ArrowRight /></el-icon>
          </template>
        </div>
      </section>

      <section class="dashboard-section">
        <div class="section-heading"><div><span class="eyebrow">待办队列</span><h2>现在需要关注</h2></div><span class="muted">点击卡片进入已筛选的目标页</span></div>
        <div class="attention-grid">
          <button v-for="item in attentionCards" :key="item.key" class="attention-card" :class="`attention-card--${item.tone}`" @click="open(item.to, item.query)">
            <span>{{ item.label }}</span><strong>{{ compactNumber(item.value) }}</strong><small>{{ item.hint }}</small><el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </section>

      <section class="dashboard-section">
        <div class="section-heading"><div><span class="eyebrow">审计轨迹</span><h2>最近审计事件</h2></div><el-button text @click="open('/records', { kind: 'audit-events' })">查看全部<el-icon><ArrowRight /></el-icon></el-button></div>
        <div v-if="audits.length" class="audit-list">
          <button v-for="item in audits" :key="item.id" @click="open('/records', { kind: 'audit-events', detail: item.id })"><span class="audit-list__type">{{ item.event_type || '审计事件' }}</span><strong>{{ item.subject_type || '系统' }} · {{ item.subject_id || '-' }}</strong><DateTime :value="item.created_at" /></button>
        </div>
        <div v-else class="audit-list audit-list--empty">暂无审计事件</div>
      </section>
    </template>
  </div>
</template>
