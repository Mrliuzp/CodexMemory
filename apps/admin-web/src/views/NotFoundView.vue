<script setup>
import { Compass } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template><section class="page-state page-state--standalone"><div class="page-state__code">404</div><el-icon><Compass /></el-icon><h2>没有找到这个页面</h2><p>地址可能已变化，或该入口不属于当前版本。</p><el-button type="primary" @click="router.replace('/dashboard')">返回运行概览</el-button></section></template>
