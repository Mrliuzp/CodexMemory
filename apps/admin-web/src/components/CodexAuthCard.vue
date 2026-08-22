<script setup>
import { computed } from 'vue'
import { getCodexAuthStatusMessage } from '../codexAuthStatus'
import PermissionGate from './PermissionGate.vue'
import StatusTag from './StatusTag.vue'

const props = defineProps({
  status: { type: String, default: 'error' },
  reason: { type: String, default: '' },
  message: { type: String, default: '' },
  busyAction: { type: String, default: '' },
  feedback: { type: Object, default: null },
})

const emit = defineEmits(['start', 'cancel', 'recheck'])

const statusText = computed(() => ({
  ready: '已登录',
  not_logged_in: '未登录',
  login_in_progress: '登录进行中',
  error: '错误',
}[props.status] || '错误'))

const tagStatus = computed(() => ({
  ready: 'ok',
  login_in_progress: 'pending',
  not_logged_in: 'unknown',
  error: 'error',
}[props.status] || 'error'))

const startLabel = computed(() => props.status === 'ready' ? '更换账号' : '开始登录')
const busy = computed(() => Boolean(props.busyAction))
const statusMessage = computed(() => getCodexAuthStatusMessage(props.status, props.reason, props.message))
</script>

<template>
  <section class="codex-auth-card" aria-labelledby="codex-auth-title">
    <div class="codex-auth-card__heading">
      <div>
        <span class="eyebrow">Worker 能力</span>
        <h2 id="codex-auth-title">Codex CLI 登录状态</h2>
      </div>
      <StatusTag :status="tagStatus" :label="statusText" />
    </div>
    <p class="codex-auth-card__note">仅显示 Worker 专用登录状态，不显示账号、令牌、认证文件或认证地址。</p>
    <p class="codex-auth-card__message" :class="`codex-auth-card__message--${tagStatus}`">{{ statusMessage }}</p>
    <el-alert v-if="feedback" class="codex-auth-card__feedback" :title="feedback.message" :type="feedback.type || 'info'" show-icon :closable="false">
      <span v-if="feedback.requestId">请求 ID：{{ feedback.requestId }}</span>
    </el-alert>
    <div class="codex-auth-card__actions">
      <PermissionGate permission="admin">
        <el-button type="primary" :loading="busyAction === 'start'" :disabled="busy || status === 'login_in_progress'" @click="emit('start')">{{ startLabel }}</el-button>
        <el-button :loading="busyAction === 'cancel'" :disabled="busy || status !== 'login_in_progress'" @click="emit('cancel')">取消</el-button>
        <el-button :loading="busyAction === 'recheck'" :disabled="busy" @click="emit('recheck')">重新检查</el-button>
      </PermissionGate>
      <PermissionGate permission="admin" :show-readonly="true" />
    </div>
  </section>
</template>
