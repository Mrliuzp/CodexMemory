import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import ProjectsView from './views/ProjectsView.vue'
import RecordsView from './views/RecordsView.vue'
import LoginView from './views/LoginView.vue'

export const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true, label: '登录' } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: DashboardView, meta: { requiresAuth: true, label: '运行概览' } },
  { path: '/projects', component: ProjectsView, meta: { requiresAuth: true, label: '项目与作用域' } },
  { path: '/record', redirect: (to) => ({ path: '/records', query: to.query }), meta: { requiresAuth: true } },
  { path: '/records', component: RecordsView, meta: { requiresAuth: true, label: '只读数据' } },
]

const router = createRouter({
  history: typeof window === 'undefined' ? createMemoryHistory() : createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const hasToken = Boolean(localStorage.getItem('codex-memory-admin-token'))
  if (to.meta.requiresAuth && !hasToken) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && hasToken) return '/dashboard'
})

export default router