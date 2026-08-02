<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import {
  ArrowDown, Collection, Connection, DataAnalysis, Document, Expand, Fold, Menu,
  Monitor, Notebook, Setting, SwitchButton, Upload, UserFilled,
} from '@element-plus/icons-vue'
import { useContextStore } from './stores/context'
import { useSessionStore } from './stores/session'
import { scopeDisplayName } from './utils/format'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const context = useContextStore()
const sessionError = ref(null)
const collapsed = ref(typeof localStorage !== 'undefined' && localStorage.getItem('codex-memory-sidebar-collapsed') === '1')
const mobileOpen = ref(false)
const isMobile = ref(false)
const sidebarRef = ref(null)
const sidebarToggleRef = ref(null)

const navGroups = [
  { label: '总览', items: [{ to: '/dashboard', label: '运行概览', icon: DataAnalysis }] },
  { label: '记忆数据', items: [{ to: '/projects', label: '项目与 Scope', icon: Collection }, { to: '/records', label: '记忆账本', icon: Notebook }] },
  { label: '运行监控', items: [{ to: '/task-runs', label: '任务报告', icon: Document }, { to: '/system-status', label: '系统状态', icon: Monitor, permissions: ['admin', 'operations_read'] }] },
  { label: '数据治理', items: [{ to: '/imports', label: '历史知识导入', icon: Upload }] },
  { label: '接口治理', items: [{ to: '/contract-services', label: '接口契约', icon: Connection }] },
]

const title = computed(() => route.meta.label || 'Codex Memory 管理后台')
const section = computed(() => route.meta.section || '管理控制台')
const activeMenu = computed(() => route.meta.activeMenu || route.path)
const barePage = computed(() => route.name === 'login' || (route.name === 'not-found' && !session.token))
const visibleGroups = computed(() => navGroups.map((group) => ({
  ...group,
  items: group.items.filter((item) => !item.permissions || item.permissions.some(session.hasPermission)),
})).filter((group) => group.items.length))
const contextQuery = computed(() => ({
  ...(context.projectKey ? { project_key: context.projectKey } : {}),
  ...(context.scopeId ? { scope_id: context.scopeId } : {}),
}))

function updateViewport() {
  isMobile.value = window.innerWidth < 768
  if (!isMobile.value) mobileOpen.value = false
}

async function toggleSidebar() {
  if (isMobile.value) {
    mobileOpen.value = !mobileOpen.value
    await nextTick()
    if (mobileOpen.value) sidebarRef.value?.querySelector('.el-menu-item')?.focus()
    else sidebarToggleRef.value?.$el?.focus?.()
  }
  else {
    collapsed.value = !collapsed.value
    localStorage.setItem('codex-memory-sidebar-collapsed', collapsed.value ? '1' : '0')
  }
}

async function closeMobileNavigation() {
  if (!mobileOpen.value) return
  mobileOpen.value = false
  await nextTick()
  sidebarToggleRef.value?.$el?.focus?.()
}

function handleKeydown(event) {
  if (event.key === 'Escape' && mobileOpen.value) closeMobileNavigation()
}

async function hydrateContext() {
  if (!session.me) return
  await context.loadProjects()
  const principalProject = session.me.project_key && session.me.project_key !== '*' ? session.me.project_key : ''
  const projectKey = String(route.query.project_key || principalProject || '')
  if (projectKey) await context.selectProject(projectKey)
  if (route.query.scope_id) context.selectScope(route.query.scope_id)
}

async function syncContextFromRoute() {
  if (!session.me) return
  const principalProject = session.me.project_key && session.me.project_key !== '*' ? session.me.project_key : ''
  const nextProject = String(route.query.project_key || principalProject || '')
  if (nextProject !== context.projectKey) await context.selectProject(nextProject)
  const nextScope = String(route.query.scope_id || '')
  if (nextScope !== context.scopeId) context.selectScope(nextScope)
}

async function changeProject(value) {
  await context.selectProject(value)
  await router.replace({
    path: route.path,
    query: { ...route.query, project_key: value || undefined, scope_id: undefined, page: undefined, detail: undefined },
  })
}

async function changeScope(value) {
  context.selectScope(value)
  await router.replace({ path: route.path, query: { ...route.query, scope_id: value || undefined, page: undefined, detail: undefined } })
}

function navigate(item) {
  router.push({ path: item.to, query: contextQuery.value })
}

function logout() {
  const redirect = route.fullPath
  session.logout()
  context.reset()
  router.replace({ name: 'login', query: { redirect } })
}

function handleUnauthorized(event) {
  if (route.name === 'login') return
  sessionError.value = event.detail
  const redirect = route.fullPath
  session.logout()
  context.reset()
  router.replace({ name: 'login', query: { redirect, reason: 'session_expired' } })
}

function handleUserCommand(command) {
  if (command === 'logout') logout()
}

watch(() => route.fullPath, () => { mobileOpen.value = false })
watch(() => `${route.query.project_key || ''}:${route.query.scope_id || ''}`, syncContextFromRoute)
watch(() => session.me, (value) => { if (value) hydrateContext() }, { immediate: true })

onMounted(() => {
  updateViewport()
  window.addEventListener('resize', updateViewport)
  window.addEventListener('admin:unauthorized', handleUnauthorized)
  window.addEventListener('keydown', handleKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateViewport)
  window.removeEventListener('admin:unauthorized', handleUnauthorized)
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <el-config-provider :locale="zhCn">
    <RouterView v-if="barePage" />
    <div v-else class="app-shell" :class="{ 'is-collapsed': collapsed, 'is-mobile-open': mobileOpen }">
      <button v-if="mobileOpen" class="sidebar-backdrop" aria-label="关闭导航" @click="closeMobileNavigation" />
      <aside ref="sidebarRef" class="sidebar" aria-label="主导航">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div class="brand-copy"><strong>Codex Memory</strong><small>记忆治理控制台</small></div>
        </div>
        <el-scrollbar class="nav-scroll">
          <el-menu :default-active="activeMenu" :collapse="collapsed && !isMobile" class="nav">
            <template v-for="group in visibleGroups" :key="group.label">
              <li class="nav-group-label">{{ group.label }}</li>
              <el-menu-item v-for="item in group.items" :key="item.to" :index="item.to" @click="navigate(item)">
                <el-icon><component :is="item.icon" /></el-icon><template #title>{{ item.label }}</template>
              </el-menu-item>
            </template>
          </el-menu>
        </el-scrollbar>
        <div class="sidebar-foot">
          <el-icon><Setting /></el-icon>
          <span>Admin · V1</span>
        </div>
      </aside>

      <div class="workspace">
        <header class="topbar">
          <div class="topbar__title">
            <el-button ref="sidebarToggleRef" text circle class="sidebar-toggle" :aria-label="isMobile ? (mobileOpen ? '关闭导航' : '打开导航') : (collapsed ? '展开导航' : '收起导航')" @click="toggleSidebar">
              <el-icon><Menu v-if="isMobile" /><Expand v-else-if="collapsed" /><Fold v-else /></el-icon>
            </el-button>
            <div>
              <el-breadcrumb separator="/">
                <el-breadcrumb-item>{{ section }}</el-breadcrumb-item>
                <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
              </el-breadcrumb>
              <h1>{{ title }}</h1>
            </div>
          </div>

          <div class="topbar__context" aria-label="全局数据上下文">
            <el-select :model-value="context.projectKey" :loading="context.loadingProjects" filterable placeholder="全部授权项目" aria-label="选择项目" @change="changeProject">
              <el-option v-if="session.me?.project_key === '*'" label="全部授权项目" value="" />
              <el-option v-for="project in context.projects" :key="project.project_key" :label="project.name || project.project_key" :value="project.project_key" />
            </el-select>
            <el-select :model-value="context.scopeId" :loading="context.loadingScopes" :disabled="!context.projectKey" clearable placeholder="全部 Scope" aria-label="选择 Scope" @change="changeScope">
              <el-option v-for="scope in context.scopes" :key="scope.id || scope.scope_key" :label="scopeDisplayName(scope)" :value="String(scope.scope_key || scope.id)" />
            </el-select>
          </div>

          <div class="user-menu">
            <span v-if="session.isReadOnly" class="readonly-badge">只读访问</span>
            <el-dropdown trigger="click" @command="handleUserCommand">
              <button class="user-trigger">
                <span class="user-avatar"><el-icon><UserFilled /></el-icon></span>
                <span class="user-copy"><strong>{{ session.displayName }}</strong><small>{{ session.me?.auth_type === 'session' ? '管理会话' : 'Bearer 身份' }}</small></span>
                <el-icon><ArrowDown /></el-icon>
              </button>
              <template #dropdown><el-dropdown-menu><el-dropdown-item command="logout"><el-icon><SwitchButton /></el-icon>退出登录</el-dropdown-item></el-dropdown-menu></template>
            </el-dropdown>
          </div>
        </header>

        <main class="content" tabindex="-1">
          <el-alert v-if="session.restoreError && session.restoreError.status !== 401" class="global-alert" :title="session.restoreError.message" type="warning" show-icon :closable="false" />
          <RouterView />
        </main>
      </div>
    </div>
  </el-config-provider>
</template>
