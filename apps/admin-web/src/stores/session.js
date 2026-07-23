import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: localStorage.getItem('codex-memory-admin-token') || '',
    me: JSON.parse(localStorage.getItem('codex-memory-admin-me') || 'null'),
  }),
  getters: { isAuthenticated: (state) => Boolean(state.token) },
  actions: {
    setToken(value) {
      this.token = value.trim()
      localStorage.setItem('codex-memory-admin-token', this.token)
    },
    setMe(value) {
      this.me = value
      localStorage.setItem('codex-memory-admin-me', JSON.stringify(value))
    },
    logout() {
      this.token = ''
      this.me = null
      localStorage.removeItem('codex-memory-admin-token')
      localStorage.removeItem('codex-memory-admin-me')
    },
  },
})