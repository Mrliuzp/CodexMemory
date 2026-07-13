import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({ token: localStorage.getItem('codex-memory-admin-token') || '', me: null }),
  actions: {
    setToken(value) {
      this.token = value.trim()
      localStorage.setItem('codex-memory-admin-token', this.token)
    },
  },
})
