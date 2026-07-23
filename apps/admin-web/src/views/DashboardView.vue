<script setup>
import { onMounted, ref } from 'vue'
import { adminGet } from '../api'
const loading = ref(true)
const error = ref('')
const stats = ref({ raw_records: 0, candidates: 0, memories: 0, jobs: 0 })
onMounted(async () => { try { stats.value = (await adminGet('/dashboard')).data } catch (e) { error.value = e.message } finally { loading.value = false } })
</script>
<template>
  <el-alert v-if="error" :title="error" type="warning" show-icon />
  <div v-loading="loading" class="dashboard">
    <div class="metric-grid"><div v-for="item in [{k:'raw_records',l:'原始记录'}, {k:'candidates',l:'候选记忆'}, {k:'memories',l:'已接受记忆'}, {k:'jobs',l:'处理任务'}]" :key="item.k" class="metric"><span>{{ item.l }}</span><strong>{{ stats[item.k] ?? 0 }}</strong><small>当前授权项目</small></div></div>
    <section class="section-heading"><div><span class="eyebrow">只读模型</span><h2>系统状态</h2></div><el-tag type="success">只读模式</el-tag></section>
    <div class="status-table"><div><span>API 命名空间</span><code>/api/admin/v1</code><el-tag type="success">在线</el-tag></div><div><span>数据权限</span><span>按项目与作用域隔离</span><el-tag>已启用</el-tag></div><div><span>写入操作</span><span>导入、发布、重试、回放</span><el-tag type="info">P1 开放</el-tag></div></div>
  </div>
</template>
