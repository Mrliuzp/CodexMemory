<script setup>
import { computed } from 'vue'
import PermissionGate from './PermissionGate.vue'
import StatusTag from './StatusTag.vue'

const props = defineProps({
  status: { type: String, default: 'error' },
  busy: { type: Boolean, default: false },
})

defineEmits(['start', 'cancel', 'recheck'])

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
    <div class="codex-auth-card__actions">
      <PermissionGate permission="admin">
        <el-button type="primary" :loading="busy" :disabled="status === 'login_in_progress'" @click="$emit('start')">{{ startLabel }}</el-button>
        <el-button :disabled="!busy && status !== 'login_in_progress'" @click="$emit('cancel')">取消</el-button>
        <el-button :loading="busy" @click="$emit('recheck')">重新检查</el-button>
      </PermissionGate>
      <PermissionGate permission="admin" :show-readonly="true" />
    </div>
  </section>
</template>
