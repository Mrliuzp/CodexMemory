<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { DataAnalysis, Collection, Menu, Monitor, Notebook, SwitchButton, UserFilled } from '@element-plus/icons-vue'
import { adminGet } from './api'
import { useSessionStore } from './stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const sessionError = ref('')
const nav = [
  { to: '/dashboard', label: '运行概览', icon: Monitor },
  { to: '/projects', label: '项目与作用域', icon: Collection },
  { to: '/records', label: '只读数据', icon: Notebook },
  { to: '/system-status', label: '\u8fd0\u884c\u72b6\u6001', icon: DataAnalysis },
]
const title = computed(() => route.meta.label || 'Codex Memory 管理后台')
const isLoginPage = computed(() => route.name === 'login')
const displayName = computed(() => session.me?.username || '管理员')

async function verifySession() {
  if (!session.token) return
  try {
    const result = await adminGet('/me')
    session.setMe(result.data)
  } catch (error) {
    session.logout()
    sessionError.value = error.message
    router.replace({ name: 'login', query: { redirect: route.fullPath } })
  }
}

function logout() {
  session.logout()
  router.replace({ name: 'login', query: { redirect: route.fullPath } })
}

onMounted(verifySession)
</script>

<template>
  <RouterView v-if="isLoginPage" />
  <el-container v-else class="shell">
    <el-aside width="232px" class="sidebar">
      <div class="brand"><div class="brand-mark">CM</div><div><strong>Codex Memory</strong><small>管理控制台</small></div></div>
      <el-menu :default-active="route.path" router class="nav">
        <el-menu-item v-for="item in nav" :key="item.to" :index="item.to"><el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span></el-menu-item>
      </el-menu>
      <div class="sidebar-foot"><el-icon><Menu /></el-icon><span>V1.2 · P0 只读模式</span></div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><span class="eyebrow">管理 / 观测</span><h1>{{ title }}</h1></div><div class="user-menu"><el-icon><UserFilled /></el-icon><span>{{ displayName }}</span><el-button text @click="logout"><el-icon><SwitchButton /></el-icon>退出登录</el-button></div></el-header>
      <el-main class="content"><el-alert v-if="sessionError" :title="sessionError" type="error" show-icon closable @close="sessionError = ''" /><RouterView /></el-main>
    </el-container>
  </el-container>
</template>