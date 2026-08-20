<script setup>
import { computed, ref } from 'vue'
import CopyableText from './CopyableText.vue'
import { formatReadableValue } from '../utils/format'
import { renderSafeMarkdown } from '../utils/markdown'

const props = defineProps({ value: { type: [Object, Array, String, Number, Boolean], default: '' }, limit: { type: Number, default: 160 } })
const expanded = ref(false)
const text = computed(() => formatReadableValue(props.value))
const truncated = computed(() => text.value.length > props.limit)
const shown = computed(() => expanded.value || !truncated.value ? text.value : `${text.value.slice(0, props.limit)}…`)
const html = computed(() => renderSafeMarkdown(shown.value))
const hasValue = computed(() => props.value !== null && props.value !== undefined && props.value !== '')
</script>

<template><div class="expandable-text"><div class="expandable-text__content" v-html="html" /><div v-if="hasValue" class="expandable-text__actions"><el-button v-if="truncated" text type="primary" @click.stop="expanded = !expanded">{{ expanded ? '收起' : '展开' }}</el-button><CopyableText :value="text" /></div></div></template>
