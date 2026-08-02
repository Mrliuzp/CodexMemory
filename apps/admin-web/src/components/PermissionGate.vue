<script setup>
import { computed } from 'vue'
import { useSessionStore } from '../stores/session'

const props = defineProps({
  permission: { type: String, default: 'admin' },
  anyOf: { type: Array, default: () => [] },
  allOf: { type: Array, default: () => [] },
  showReadonly: { type: Boolean, default: false },
})
const session = useSessionStore()
const allowed = computed(() => {
  const permissions = new Set(session.me?.permissions || [])
  if (props.allOf.length && !props.allOf.every((item) => permissions.has(item))) return false
  if (props.anyOf.length) return props.anyOf.some((item) => permissions.has(item))
  return permissions.has(props.permission)
})
</script>

<template>
  <slot v-if="allowed" />
  <slot v-else name="fallback">
    <span v-if="showReadonly" class="readonly-hint">当前为只读访问</span>
  </slot>
</template>
