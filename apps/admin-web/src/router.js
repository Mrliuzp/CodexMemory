import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import ProjectsView from './views/ProjectsView.vue'
import RecordsView from './views/RecordsView.vue'

export const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: DashboardView, meta: { label: 'Dashboard' } },
  { path: '/projects', component: ProjectsView, meta: { label: 'Projects and Scopes' } },
  { path: '/records', component: RecordsView, meta: { label: 'Read-only data' } },
]

export default createRouter({
  history: typeof window === 'undefined' ? createMemoryHistory() : createWebHistory(),
  routes,
})