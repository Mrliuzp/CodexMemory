<script setup>
import { computed } from 'vue'
import { formatDateTime } from '../utils/format'
const props = defineProps({ value: { type: [String, Number, Date], default: '' }, seconds: { type: Boolean, default: true } })
const label = computed(() => formatDateTime(props.value, { seconds: props.seconds }))
const datetime = computed(() => {
  if (!props.value) return undefined
  const date = new Date(props.value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
})
</script>

<template><time :datetime="datetime" :title="label">{{ label }}</time></template>
