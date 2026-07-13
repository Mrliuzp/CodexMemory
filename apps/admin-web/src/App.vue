<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { Menu, Monitor, Notebook, Collection, Key } from '@element-plus/icons-vue'
import { adminGet } from './api'
import { useSessionStore } from './stores/session'

const route = useRoute()
const session = useSessionStore()
const tokenInput = ref(session.token)
const meError = ref('')
const nav = [
  { to: '/dashboard', label: '运行概览', icon: Monitor },
  { to: '/projects', label: '项目与 Scope', icon: Collection },
  { to: '/records', label: '只读数据', icon: Notebook },
]
const title = computed(() => route.meta.label || 'Codex Memory Admin')

async function connect() {
  session.setToken(tokenInput.value)
  meError.value = ''
  try { session.me = (await adminGet('/me')).data } catch (error) { meError.value = error.message }
}

onMounted(() => { if (session.token) connect() })
</script>

<template>
  <el-container class="shell">
    <el-aside width="232px" class="sidebar">
      <div class="brand"><div class="brand-mark">CM</div><div><strong>Codex Memory</strong><small>Admin Console</small></div></div>
      <el-menu :default-active="route.path" router class="nav">
        <el-menu-item v-for="item in nav" :key="item.to" :index="item.to"><el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span></el-menu-item>
      </el-menu>
      <div class="sidebar-foot"><el-icon><Menu /></el-icon><span>V1.2 · P0 Read-only</span></div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><span class="eyebrow">ADMIN / OBSERVE</span><h1>{{ title }}</h1></div><div class="auth"><el-icon><Key /></el-icon><el-input v-model="tokenInput" type="password" show-password placeholder="Bearer token" @keyup.enter="connect" /><el-button type="primary" @click="connect">连接</el-button></div></el-header>
      <el-main class="content"><el-alert v-if="meError" :title="meError" type="error" show-icon closable @close="meError = ''" /><RouterView /></el-main>
    </el-container>
  </el-container>
</template>
