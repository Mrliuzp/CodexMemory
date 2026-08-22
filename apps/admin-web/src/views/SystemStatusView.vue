<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { adminGet, adminPost, adminPut, getErrorMessage } from '../api'
import { getCodexAuthStatusMessage } from '../codexAuthStatus'
import CodexAuthCard from '../components/CodexAuthCard.vue'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import ProjectGovernanceForm from '../components/ProjectGovernanceForm.vue'
import StatusTag from '../components/StatusTag.vue'
import { useVisibilityRefresh } from '../composables/useVisibilityRefresh'
import { useContextStore } from '../stores/context'
import { useSessionStore } from '../stores/session'
import {
  normalizeDecisionPolicy,
  normalizeFeatureFlags,
  resolveProjectKey,
} from '../decisionPolicy'
import { readableText } from '../utils/format'

const router = useRouter()
const route = useRoute()
const context = useContextStore()
const session = useSessionStore()
const loading = ref(true)
const refreshing = ref(false)
const error = ref(null)
const data = ref({})
const requestId = ref('')
const policy = ref(null)
const flags = ref(null)
const savingSection = ref('')
const configError = ref(null)
const codexAuthStatus = ref('error')
const codexAuthReason = ref('')
const codexAuthMessage = ref('')
const codexAuthBusy = ref('')
const codexAuthFeedback = ref(null)
const projectKey = computed(() => resolveProjectKey({
  queryProjectKey: route.query.project_key,
  contextProjectKey: context.projectKey,
  principalProjectKey: session.me?.project_key,
}))

const migrationText = computed(() => data.value.migration_schema === 'ok' ? '结构就绪' : '需要检查')
const decision = computed(() => data.value.decision || {})
const overallStatus = computed(() => {
  if (error.value || data.value.database === 'error' || Number(data.value.dead_letters || 0) > 0) return 'error'
  if (data.value.migration_schema !== 'ok' || Number(data.value.pending_jobs || 0) > 0 || Number(data.value.server_outbox || 0) > 0) return 'pending'
  return 'ok'
})
const checks = computed(() => [
  { label: '管理 API', status: error.value ? 'error' : 'ok', value: error.value ? '请求失败' : '响应正常', note: '当前浏览器已成功访问正式 Admin API' },
  { label: '数据库', status: data.value.database || 'unknown', value: data.value.database === 'ok' ? '连接正常' : '连接异常', note: `数据库方言：${readableText(data.value.dialect || '未知')}` },
  { label: '迁移', status: data.value.migration_schema === 'ok' ? 'ok' : 'pending', value: migrationText.value, note: data.value.latest_migration || '未获取到迁移版本' },
  { label: '任务队列', status: Number(data.value.pending_jobs || 0) ? 'pending' : 'ok', value: `${data.value.pending_jobs ?? 0} 个待处理`, note: '处理任务等待 Worker 消费', to: '/records', query: { kind: 'jobs', status: 'pending' } },
  { label: 'Outbox', status: Number(data.value.server_outbox || 0) ? 'pending' : 'ok', value: `${data.value.server_outbox ?? 0} 个待投递`, note: '服务端事件等待可靠投递', to: '/records', query: { kind: 'outbox-events', status: 'pending' } },
  { label: '死信', status: Number(data.value.dead_letters || 0) ? 'error' : 'ok', value: `${data.value.dead_letters ?? 0} 个`, note: '无法继续投递的事件', to: '/records', query: { kind: 'outbox-events', status: 'dead' } },
  { label: '决策队列', status: Number(decision.value.queued || 0) ? 'pending' : 'ok', value: `${decision.value.queued ?? 0} 个`, note: '候选等待决策 Worker 处理', to: '/records', query: { kind: 'jobs', status: 'pending', job_type: 'decide_candidate' } },
  { label: '人工队列', status: Number(decision.value.human_review_queue || 0) ? 'pending' : 'ok', value: `${decision.value.human_review_queue ?? 0} 个`, note: '低置信度或风险候选待人工处理', to: '/records', query: { kind: 'candidates', status: 'needs_review' } },
])

async function refresh(manual = false) {
  if (manual) refreshing.value = true
  else if (!Object.keys(data.value).length) loading.value = true
  error.value = null
  configError.value = null
  codexAuthFeedback.value = null
  try {
    const result = await adminGet('/system/status', projectKey.value ? { project_key: projectKey.value } : {})
    data.value = result.data || {}
    requestId.value = result.request_id || ''
    await refreshCodexAuth()
    if (projectKey.value) {
      policy.value = null
      flags.value = null
      const [policyResult, flagsResult] = await Promise.allSettled([
        adminGet(`/projects/${encodeURIComponent(projectKey.value)}/decision-policy`),
        adminGet(`/projects/${encodeURIComponent(projectKey.value)}/feature-flags`),
      ])
      const failures = []
      if (policyResult.status === 'fulfilled') policy.value = normalizeDecisionPolicy(policyResult.value.data)
      else failures.push(policyResult.reason)
      if (flagsResult.status === 'fulfilled') flags.value = normalizeFeatureFlags(flagsResult.value.data)
      else failures.push(flagsResult.reason)
      if (failures.length) configError.value = failures[0]
    } else {
      policy.value = null
      flags.value = null
    }
  } catch (requestError) {
    error.value = requestError
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function refreshCodexAuth() {
  try {
    const result = await adminGet('/codex-auth/status')
    codexAuthStatus.value = result.data?.status || 'error'
    codexAuthReason.value = result.data?.reason || ''
    codexAuthMessage.value = result.data?.message || ''
  } catch (requestError) {
    codexAuthStatus.value = 'error'
    codexAuthReason.value = requestError?.code || 'admin_api_unavailable'
    codexAuthMessage.value = getErrorMessage(requestError)
    codexAuthFeedback.value = {
      type: 'error',
      message: codexAuthMessage.value,
      requestId: requestError?.requestId || '',
    }
  }
}

async function codexAuthAction(action) {
  if (codexAuthBusy.value) return
  codexAuthBusy.value = action
  codexAuthFeedback.value = null
  try {
    const result = await adminPost(`/codex-auth/${action}`, {})
    codexAuthStatus.value = result.data?.status || 'error'
    codexAuthReason.value = result.data?.reason || ''
    codexAuthMessage.value = result.data?.message || ''
    const actionLabel = { start: '开始登录', cancel: '取消登录', recheck: '重新检查' }[action] || '操作'
    const feedbackMessage = getCodexAuthStatusMessage(codexAuthStatus.value, codexAuthReason.value, codexAuthMessage.value)
    codexAuthFeedback.value = {
      type: codexAuthStatus.value === 'error' ? 'error' : 'success',
      message: `${actionLabel}：${feedbackMessage}`,
      requestId: result.request_id || '',
    }
  } catch (requestError) {
    codexAuthStatus.value = 'error'
    codexAuthReason.value = requestError?.meta?.reason || requestError?.code || 'admin_api_unavailable'
    codexAuthMessage.value = getErrorMessage(requestError)
    codexAuthFeedback.value = {
      type: 'error',
      message: codexAuthMessage.value,
      requestId: requestError?.requestId || '',
    }
  } finally {
    codexAuthBusy.value = ''
  }
}

function startCodexLogin() {
  return codexAuthAction('start')
}

function cancelCodexLogin() {
  return codexAuthAction('cancel')
}

function recheckCodexLogin() {
  return codexAuthAction('recheck')
}

async function savePolicy(payload) {
  if (!payload || !projectKey.value) return
  savingSection.value = 'policy'
  configError.value = null
  try {
    const result = await adminPut(`/projects/${encodeURIComponent(projectKey.value)}/decision-policy`, {
      ...payload,
    })
    policy.value = normalizeDecisionPolicy(result.data || policy.value)
  } catch (requestError) {
    configError.value = requestError
  } finally {
    savingSection.value = ''
  }
}

async function saveFlags(payload) {
  if (!payload || !projectKey.value) return
  savingSection.value = 'flags'
  configError.value = null
  try {
    const result = await adminPut(`/projects/${encodeURIComponent(projectKey.value)}/feature-flags`, payload)
    flags.value = normalizeFeatureFlags(result.data || flags.value)
  } catch (requestError) {
    configError.value = requestError
  } finally {
    savingSection.value = ''
  }
}

function open(item) {
  if (item.to) router.push({ path: item.to, query: item.query })
}

const { lastUpdatedAt } = useVisibilityRefresh(refresh, 30000)
onMounted(refresh)
watch(projectKey, () => refresh())
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

      <CodexAuthCard
        :status="codexAuthStatus"
        :reason="codexAuthReason"
        :message="codexAuthMessage"
        :busy-action="codexAuthBusy"
        :feedback="codexAuthFeedback"
        @start="startCodexLogin"
        @cancel="cancelCodexLogin"
        @recheck="recheckCodexLogin"
      />

      <div class="health-grid">
        <button v-for="item in checks" :key="item.label" class="health-card" :class="{ 'is-actionable': item.to }" :disabled="!item.to" @click="open(item)">
          <div><span>{{ item.label }}</span><StatusTag :status="item.status" :label="item.status === 'ok' ? '正常' : item.status === 'error' ? '异常' : item.status === 'pending' ? '待处理' : '未知'" /></div>
          <strong>{{ item.value }}</strong><small>{{ item.note }}</small>
        </button>
      </div>

      <section class="system-detail-card">
        <div class="section-heading"><div><span class="eyebrow">运行明细</span><h2>运行信息</h2></div><span v-if="requestId" class="muted">请求 ID：<CopyableText :value="requestId" /></span></div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="数据库方言"><code>{{ readableText(data.dialect || '未知') }}</code></el-descriptions-item>
          <el-descriptions-item label="迁移版本"><CopyableText :value="data.latest_migration || '未知'" /></el-descriptions-item>
          <el-descriptions-item label="待处理任务">{{ data.pending_jobs ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="待投递 Outbox">{{ data.server_outbox ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="死信事件">{{ data.dead_letters ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="模型失败">{{ decision.model_failures ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="决策重试">{{ decision.retries ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="自动发布">{{ decision.auto_published ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="人工推翻">{{ decision.human_overturned ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="卡死任务">{{ decision.stuck_jobs ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="L1 自动发布开关">{{ decision.feature_flags?.auto_publish_l1_enabled ? '已启用' : '未启用' }}</el-descriptions-item>
          <el-descriptions-item label="L1 置信度阈值">{{ decision.feature_flags?.auto_publish_l1_threshold ?? '0.80' }}</el-descriptions-item>
          <el-descriptions-item label="自动刷新">页面可见时每 30 秒</el-descriptions-item>
        </el-descriptions>
      </section>

      <ProjectGovernanceForm
        :project-key="projectKey"
        :policy="policy"
        :flags="flags"
        :saving-section="savingSection"
        :error="configError"
        :can-edit="session.isAdmin"
        @save-policy="savePolicy"
        @save-flags="saveFlags"
      />
    </template>
  </section>
</template>
