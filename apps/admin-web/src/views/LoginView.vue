<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { adminLogin } from '../api'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await adminLogin(username.value, password.value)
    session.setToken(result.access_token)
    session.setMe({ username: username.value, permissions: ['admin', 'read'] })
    router.replace(String(route.query.redirect || '/dashboard'))
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-panel">
      <div class="brand login-brand"><div class="brand-mark">CM</div><div><strong>Codex Memory</strong><small>管理控制台</small></div></div>
      <div class="login-heading"><span class="eyebrow">安全管理登录</span><h1>登录后台</h1><p>使用管理员账号进入 Codex Memory 管理控制台。</p></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-form class="login-form" @submit.prevent="submit">
        <el-form-item label="用户名"><el-input v-model="username" size="large" autocomplete="username" placeholder="管理员用户名"><template #prefix><el-icon><User /></el-icon></template></el-input></el-form-item>
        <el-form-item label="密码"><el-input v-model="password" size="large" type="password" show-password autocomplete="current-password" placeholder="管理员密码" @keyup.enter="submit"><template #prefix><el-icon><Lock /></el-icon></template></el-input></el-form-item>
        <el-button class="login-button" type="primary" size="large" native-type="submit" :loading="loading">登录</el-button>
      </el-form>
      <small class="login-note">会话有效期 8 小时 · 仅限只读管理操作</small>
    </section>
  </main>
</template>
