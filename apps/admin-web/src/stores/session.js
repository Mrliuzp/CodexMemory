import { defineStore } from 'pinia'
import { adminGet } from '../api'

const TOKEN_KEY = 'codex-memory-admin-token'
const ME_KEY = 'codex-memory-admin-me'
let restorePromise = null

function readStorage(key, fallback = '') {
  if (typeof localStorage === 'undefined') return fallback
  return localStorage.getItem(key) || fallback
}

function writeStorage(key, value) {
  if (typeof localStorage === 'undefined') return
  if (value === null || value === '') localStorage.removeItem(key)
  else localStorage.setItem(key, value)
}

function readMe() {
  try {
    return JSON.parse(readStorage(ME_KEY, 'null'))
  } catch {
    return null
  }
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: readStorage(TOKEN_KEY),
    me: readMe(),
    restored: false,
    restoring: false,
    restoreError: null,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    isAdmin: (state) => Boolean(state.me?.permissions?.includes('admin')),
    isReadOnly: (state) => Boolean(state.me && !state.me.permissions?.includes('admin')),
    displayName: (state) => state.me?.display_name || state.me?.username || '管理员',
    hasPermission: (state) => (permission) => Boolean(state.me?.permissions?.includes('admin') || state.me?.permissions?.includes(permission)),
  },
  actions: {
    setToken(value) {
      this.token = String(value || '').trim()
      writeStorage(TOKEN_KEY, this.token)
    },
    setMe(value) {
      this.me = value
      this.restored = Boolean(value)
      writeStorage(ME_KEY, value ? JSON.stringify(value) : null)
    },
    async ensureSession(force = false) {
      if (!this.token) return null
      if (this.restored && this.me && !force) return this.me
      if (restorePromise) return restorePromise
      this.restoring = true
      this.restoreError = null
      restorePromise = adminGet('/me')
        .then((result) => {
          this.setMe(result.data || null)
          return this.me
        })
        .catch((error) => {
          this.restoreError = error
          if (error.status === 401) this.logout()
          throw error
        })
        .finally(() => {
          this.restoring = false
          restorePromise = null
        })
      return restorePromise
    },
    logout() {
      this.token = ''
      this.me = null
      this.restored = false
      this.restoring = false
      this.restoreError = null
      restorePromise = null
      writeStorage(TOKEN_KEY, null)
      writeStorage(ME_KEY, null)
    },
  },
})
