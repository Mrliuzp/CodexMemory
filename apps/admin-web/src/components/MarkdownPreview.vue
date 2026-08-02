<script setup>
import { computed } from 'vue'
import { renderSafeMarkdown } from '../utils/markdown'
const props = defineProps({ source: { type: String, default: '' } })
const html = computed(() => renderSafeMarkdown(props.source))
</script>

<template><article v-if="source" class="safe-markdown" v-html="html" /><div v-else class="muted">暂无 Markdown 内容</div></template>
