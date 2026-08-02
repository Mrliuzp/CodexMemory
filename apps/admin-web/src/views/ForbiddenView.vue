<script setup>
import { Lock } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template><section class="page-state"><div class="page-state__code">403</div><el-icon><Lock /></el-icon><h2>当前账号无权访问</h2><p>此页面需要更高权限。你仍可返回运行概览查看已授权的数据。</p><el-button type="primary" @click="router.replace('/dashboard')">返回运行概览</el-button></section></template>
