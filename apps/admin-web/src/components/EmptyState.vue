<script setup>
defineProps({ title: { type: String, default: '暂无数据' }, description: { type: String, default: '当前筛选条件下没有可展示的内容。' }, compact: { type: Boolean, default: false } })
</script>

<template><div class="state-view" :class="{ 'state-view--compact': compact }"><el-empty :description="title" :image-size="compact ? 64 : 96"><p class="state-view__description">{{ description }}</p><slot name="action" /></el-empty></div></template>
