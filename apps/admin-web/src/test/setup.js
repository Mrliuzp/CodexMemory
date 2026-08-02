import { afterEach, vi } from 'vitest'

class MemoryStorage {
  #values = new Map()

  getItem(key) { return this.#values.has(String(key)) ? this.#values.get(String(key)) : null }
  setItem(key, value) { this.#values.set(String(key), String(value)) }
  removeItem(key) { this.#values.delete(String(key)) }
  clear() { this.#values.clear() }
}

Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: new MemoryStorage() })
if (typeof globalThis.CustomEvent === 'undefined') {
  globalThis.CustomEvent = class CustomEvent extends Event {
    constructor(type, options = {}) {
      super(type)
      this.detail = options.detail
    }
  }
}

afterEach(() => {
  localStorage.clear()
  if (typeof window !== 'undefined') delete globalThis.window
  vi.restoreAllMocks()
})
