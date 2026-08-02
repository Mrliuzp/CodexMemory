<script setup>
defineProps({ progress: { type: Number, default: 0 }, fileName: { type: String, default: '' }, status: { type: String, default: '' }, message: { type: String, default: '' } })
</script>

<template><div class="upload-progress"><div class="upload-progress__meta"><strong>{{ fileName || '正在上传' }}</strong><span>{{ Math.round(progress) }}%</span></div><el-progress :percentage="Math.round(progress)" :status="status || undefined" :stroke-width="8" /><p v-if="message">{{ message }}</p></div></template>
