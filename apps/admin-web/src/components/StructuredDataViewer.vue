<script setup>
import { computed } from 'vue'
const props = defineProps({ value: { type: [Object, Array, String, Number, Boolean], default: null }, title: { type: String, default: '查看原始数据' }, open: { type: Boolean, default: false } })
const formatted = computed(() => typeof props.value === 'string' ? props.value : JSON.stringify(props.value, null, 2))
</script>

<template><details class="structured-viewer" :open="open"><summary>{{ title }}</summary><pre>{{ formatted || '暂无数据' }}</pre></details></template>
