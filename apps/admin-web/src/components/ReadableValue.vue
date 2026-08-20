<script setup>
import { computed } from 'vue'
import { formatReadableValue } from '../utils/format'
import { renderSafeMarkdown } from '../utils/markdown'

const props = defineProps({
  value: { type: [Object, Array, String, Number, Boolean], default: null },
  maxDepth: { type: Number, default: 6 },
})

const source = computed(() => formatReadableValue(props.value, { maxDepth: props.maxDepth }))
const html = computed(() => renderSafeMarkdown(source.value))
</script>

<template><div class="readable-value" v-html="html" /></template>
