import { defineStore } from 'pinia'
import { adminGet } from '../api'

export const useContextStore = defineStore('admin-context', {
  state: () => ({
    projects: [],
    scopes: [],
    projectKey: '',
    scopeId: '',
    loadingProjects: false,
    loadingScopes: false,
    error: null,
  }),
  getters: {
    currentProject: (state) => state.projects.find((item) => item.project_key === state.projectKey) || null,
    currentScope: (state) => state.scopes.find((item) => String(item.scope_key || item.id) === state.scopeId) || null,
  },
  actions: {
    async loadProjects(force = false) {
      if (this.projects.length && !force) return this.projects
      this.loadingProjects = true
      this.error = null
      try {
        const result = await adminGet('/projects', { page: 1, page_size: 200, sort: 'project_key', order: 'asc' })
        this.projects = Array.isArray(result.data) ? result.data : []
        return this.projects
      } catch (error) {
        this.error = error
        return []
      } finally {
        this.loadingProjects = false
      }
    },
    async selectProject(projectKey, force = false) {
      const next = String(projectKey || '')
      if (!force && next === this.projectKey && (this.scopes.length || !next)) return
      this.projectKey = next
      this.scopeId = ''
      this.scopes = []
      if (!next) return
      this.loadingScopes = true
      try {
        const result = await adminGet(`/projects/${encodeURIComponent(next)}/scopes`, { page: 1, page_size: 200 })
        this.scopes = Array.isArray(result.data) ? result.data : []
      } catch (error) {
        this.error = error
      } finally {
        this.loadingScopes = false
      }
    },
    selectScope(scopeId) {
      this.scopeId = String(scopeId || '')
    },
    reset() {
      this.projects = []
      this.scopes = []
      this.projectKey = ''
      this.scopeId = ''
      this.error = null
    },
  },
})
