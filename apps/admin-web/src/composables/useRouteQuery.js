import { reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

function clean(query) {
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '' && value !== undefined && value !== null))
}

export function useRouteQuery(defaults, numericKeys = []) {
  const route = useRoute()
  const router = useRouter()
  const parse = () => Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => {
    const value = route.query[key]
    if (value === undefined) return [key, fallback]
    return [key, numericKeys.includes(key) ? Number(value) || fallback : String(value)]
  }))
  const state = reactive(parse())

  watch(() => route.query, () => Object.assign(state, parse()))

  async function commit(patch = {}, replace = true) {
    Object.assign(state, patch)
    const query = clean({ ...route.query, ...state })
    await router[replace ? 'replace' : 'push']({ path: route.path, query })
  }

  return { state, commit }
}
