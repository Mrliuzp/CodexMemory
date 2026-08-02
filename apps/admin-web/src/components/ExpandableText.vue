<script setup>
import { computed, ref } from 'vue'
import CopyableText from './CopyableText.vue'
const props = defineProps({ value: { type: [String, Number], default: '' }, limit: { type: Number, default: 160 } })
const expanded = ref(false)
const text = computed(() => typeof props.value === 'string' ? props.value : JSON.stringify(props.value ?? ''))
const truncated = computed(() => text.value.length > props.limit)
const shown = computed(() => expanded.value || !truncated.value ? text.value : `${text.value.slice(0, props.limit)}…`)
</script>

<template><div class="expandable-text"><p>{{ shown || '-' }}</p><div v-if="text" class="expandable-text__actions"><el-button v-if="truncated" text type="primary" @click.stop="expanded = !expanded">{{ expanded ? '收起' : '展开' }}</el-button><CopyableText :value="text" /></div></div></template>
