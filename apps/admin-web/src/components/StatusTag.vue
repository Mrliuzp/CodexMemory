<script setup>
import { computed } from 'vue'
import { statusMeta } from '../utils/format'

const props = defineProps({ status: { type: [String, Boolean], default: 'unknown' }, label: { type: String, default: '' }, size: { type: String, default: 'small' } })
const meta = computed(() => statusMeta(props.status === true ? 'ok' : props.status === false ? 'error' : props.status, props.label))
</script>

<template><el-tag class="status-tag" :class="`status-tag--${meta.status}`" :type="meta.type" :size="size" effect="light"><span class="status-tag__dot" />{{ label || meta.label }}</el-tag></template>
