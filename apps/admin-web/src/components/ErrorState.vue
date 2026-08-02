<script setup>
import { computed, getCurrentInstance } from 'vue'
import { WarningFilled } from '@element-plus/icons-vue'
import CopyableText from './CopyableText.vue'
defineProps({ error: { type: [Object, String], default: null }, title: { type: String, default: '内容加载失败' }, compact: { type: Boolean, default: false } })
defineEmits(['retry'])
const instance = getCurrentInstance()
const canRetry = computed(() => Boolean(instance?.vnode.props?.onRetry))
</script>

<template><div v-if="error" class="error-state" :class="{ 'error-state--compact': compact }" role="alert"><el-icon class="error-state__icon"><WarningFilled /></el-icon><div><strong>{{ title }}</strong><p>{{ typeof error === 'string' ? error : error.message }}</p><small v-if="error?.requestId">请求 ID：<CopyableText :value="error.requestId" /></small></div><el-button v-if="canRetry" @click="$emit('retry')">重新加载</el-button></div></template>
