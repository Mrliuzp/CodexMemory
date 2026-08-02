import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useVisibilityRefresh(callback, interval = 30000) {
  const lastUpdatedAt = ref(null)
  let timer = null

  async function run() {
    await callback()
    lastUpdatedAt.value = new Date()
  }

  function stop() {
    if (timer) window.clearInterval(timer)
    timer = null
  }

  function start() {
    stop()
    if (typeof document !== 'undefined' && document.hidden) return
    timer = window.setInterval(run, interval)
  }

  function handleVisibility() {
    if (document.hidden) stop()
    else { run(); start() }
  }

  onMounted(() => {
    start()
    document.addEventListener('visibilitychange', handleVisibility)
  })
  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('visibilitychange', handleVisibility)
  })

  return { lastUpdatedAt, refresh: run, start, stop }
}
