import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import { pinia } from './stores'
import { useSessionStore } from './stores/session'

const views = {
  LoginView: () => import('./views/LoginView.vue'),
  DashboardView: () => import('./views/DashboardView.vue'),
  ProjectsView: () => import('./views/ProjectsView.vue'),
  RecordsView: () => import('./views/RecordsView.vue'),
  SystemStatusView: () => import('./views/SystemStatusView.vue'),
  ImportView: () => import('./views/ImportView.vue'),
  TaskRunsView: () => import('./views/TaskRunsView.vue'),
  TaskRunDetailView: () => import('./views/TaskRunDetailView.vue'),
  ContractServicesView: () => import('./views/ContractServicesView.vue'),
  ContractServiceDetailView: () => import('./views/ContractServiceDetailView.vue'),
  ContractRevisionDetailView: () => import('./views/ContractRevisionDetailView.vue'),
  ForbiddenView: () => import('./views/ForbiddenView.vue'),
  NotFoundView: () => import('./views/NotFoundView.vue'),
}
const lazy = (name) => views[name]

export const routes = [
  { path: '/login', name: 'login', component: lazy('LoginView'), meta: { public: true, label: '登录' } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: lazy('DashboardView'), meta: { requiresAuth: true, permission: 'read', section: '总览', label: '运行概览' } },
  { path: '/projects', name: 'projects', component: lazy('ProjectsView'), meta: { requiresAuth: true, permission: 'read', section: '记忆数据', label: '项目与 Scope' } },
  { path: '/record', redirect: (to) => ({ path: '/records', query: to.query }), meta: { requiresAuth: true } },
  { path: '/system-status', name: 'system-status', component: lazy('SystemStatusView'), meta: { requiresAuth: true, anyPermissions: ['admin', 'operations_read'], section: '运行监控', label: '系统状态' } },
  { path: '/records', name: 'records', component: lazy('RecordsView'), meta: { requiresAuth: true, permission: 'read', section: '记忆数据', label: '记忆账本' } },
  { path: '/imports', name: 'imports', component: lazy('ImportView'), meta: { requiresAuth: true, permission: 'read', section: '数据治理', label: '历史知识导入' } },
  { path: '/task-runs', name: 'task-runs', component: lazy('TaskRunsView'), meta: { requiresAuth: true, permission: 'read', section: '运行监控', label: '任务报告' } },
  { path: '/task-runs/:id', name: 'task-run-detail', component: lazy('TaskRunDetailView'), meta: { requiresAuth: true, permission: 'read', activeMenu: '/task-runs', section: '运行监控', label: '任务运行详情' } },
  { path: '/contract-services', name: 'contract-services', component: lazy('ContractServicesView'), meta: { requiresAuth: true, permission: 'read', section: '接口治理', label: '接口契约' } },
  { path: '/contract-services/:serviceId', name: 'contract-service-detail', component: lazy('ContractServiceDetailView'), meta: { requiresAuth: true, permission: 'read', activeMenu: '/contract-services', section: '接口治理', label: '契约服务详情' } },
  { path: '/contract-services/:serviceId/revisions/:revisionNumber', name: 'contract-revision-detail', component: lazy('ContractRevisionDetailView'), meta: { requiresAuth: true, permission: 'read', activeMenu: '/contract-services', section: '接口治理', label: '接口契约 Revision 详情' } },
  { path: '/forbidden', name: 'forbidden', component: lazy('ForbiddenView'), meta: { requiresAuth: true, label: '无权访问' } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: lazy('NotFoundView'), meta: { public: true, label: '页面不存在' } },
]

const router = createRouter({
  history: typeof window === 'undefined' ? createMemoryHistory() : createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const session = useSessionStore(pinia)
  if (to.name === 'login') {
    if (!session.token) return true
    try {
      await session.ensureSession()
      return String(to.query.redirect || '/dashboard')
    } catch {
      return true
    }
  }
  if (!to.meta.requiresAuth) return true
  if (!session.token) return { name: 'login', query: { redirect: to.fullPath } }
  try {
    await session.ensureSession()
  } catch (error) {
    if (error.status === 401) return { name: 'login', query: { redirect: to.fullPath, reason: 'session_expired' } }
    return true
  }
  if (to.meta.permission && !session.hasPermission(to.meta.permission)) return { name: 'forbidden', query: { from: to.fullPath } }
  if (to.meta.anyPermissions && !to.meta.anyPermissions.some((item) => session.hasPermission(item))) {
    return { name: 'forbidden', query: { from: to.fullPath } }
  }
  return true
})

export default router
