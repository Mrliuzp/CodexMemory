import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import ProjectsView from './views/ProjectsView.vue'
import RecordsView from './views/RecordsView.vue'
import SystemStatusView from './views/SystemStatusView.vue'
import LoginView from './views/LoginView.vue'
import ImportView from './views/ImportView.vue'
import TaskRunsView from './views/TaskRunsView.vue'
import TaskRunDetailView from './views/TaskRunDetailView.vue'

export const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true, label: '登录' } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: DashboardView, meta: { requiresAuth: true, label: '运行概览' } },
  { path: '/projects', component: ProjectsView, meta: { requiresAuth: true, label: '项目与作用域' } },
  { path: '/record', redirect: (to) => ({ path: '/records', query: to.query }), meta: { requiresAuth: true } },
  { path: '/system-status', component: SystemStatusView, meta: { requiresAuth: true, label: '\u8fd0\u884c\u72b6\u6001' } },
  { path: '/records', component: RecordsView, meta: { requiresAuth: true, label: '只读数据' } },
  { path: '/imports', component: ImportView, meta: { requiresAuth: true, label: '历史知识导入' } },
  { path: '/task-runs', name: 'task-runs', component: TaskRunsView, meta: { requiresAuth: true, label: '任务报告' } },
  { path: '/task-runs/:id', name: 'task-run-detail', component: TaskRunDetailView, meta: { requiresAuth: true, label: '任务运行详情' } },
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
