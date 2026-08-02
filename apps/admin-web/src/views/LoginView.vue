<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { adminLogin } from '../api'
import ErrorState from '../components/ErrorState.vue'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref(null)

function safeRedirect() {
  const value = String(route.query.redirect || '/dashboard')
  return value.startsWith('/') && !value.startsWith('//') ? value : '/dashboard'
}

async function submit() {
  if (loading.value) return
  if (!username.value.trim() || !password.value) {
    error.value = new Error('请输入用户名和密码。')
    return
  }
  loading.value = true
  error.value = null
  try {
    const result = await adminLogin(username.value.trim(), password.value)
    session.setToken(result.access_token)
    await session.ensureSession(true)
    await router.replace(safeRedirect())
  } catch (cause) {
    session.logout()
    error.value = cause
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-story" aria-label="Codex Memory 管理控制台简介">
      <div>
        <span class="eyebrow">记忆运维账本</span>
        <h1>让每一条记忆<br>都有迹可循。</h1>
        <p>从原始记录到候选、正式 Memory 与任务报告，在同一个治理视图中确认系统健康、发现待办并追溯证据。</p>
      </div>
      <div class="login-ledger" aria-label="记忆处理流程">
        <div><strong>L0</strong><span>原始事实</span></div>
        <div><strong>Candidate</strong><span>治理候选</span></div>
        <div><strong>L1—L3</strong><span>可用记忆</span></div>
      </div>
    </section>
    <section class="login-side">
      <div class="login-panel">
        <div class="brand login-brand">
          <div class="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div class="brand-copy"><strong>Codex Memory</strong><small>记忆治理控制台</small></div>
        </div>
        <div class="login-heading"><span class="eyebrow">安全管理登录</span><h2>欢迎回来</h2><p>使用已配置的管理员凭据进入控制台。</p></div>
        <el-alert v-if="route.query.reason === 'session_expired' && !error" class="login-session-alert" title="登录状态已失效，请重新登录。" type="warning" show-icon :closable="false" />
        <ErrorState v-if="error" compact :error="error" title="登录失败" />
        <el-form class="login-form" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名"><el-input v-model="username" size="large" autocomplete="username" placeholder="请输入管理员用户名" :disabled="loading"><template #prefix><el-icon><User /></el-icon></template></el-input></el-form-item>
          <el-form-item label="密码"><el-input v-model="password" size="large" type="password" show-password autocomplete="current-password" placeholder="请输入管理员密码" :disabled="loading" @keyup.enter="submit"><template #prefix><el-icon><Lock /></el-icon></template></el-input></el-form-item>
          <el-button class="login-button" type="primary" size="large" native-type="submit" :loading="loading">登录管理控制台</el-button>
        </el-form>
        <small class="login-note">凭据通过加密连接提交，会话信息仅保存在当前浏览器。</small>
      </div>
    </section>
  </main>
</template>
