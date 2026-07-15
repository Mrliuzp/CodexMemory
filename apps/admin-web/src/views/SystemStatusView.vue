<script setup>
import { onMounted, ref } from 'vue'
import { adminGet } from '../api'
const loading = ref(true)
const error = ref('')
const data = ref({})
async function refresh() { loading.value = true; error.value = ''; try { data.value = (await adminGet('/system/status')).data } catch (e) { error.value = e.message } finally { loading.value = false } }
onMounted(refresh)
</script>
<template>
  <section v-loading="loading">
    <div class="section-heading"><div><span class="eyebrow">运行观测</span><h2>系统状态</h2></div><el-button circle aria-label="刷新" @click="refresh"><el-icon><Refresh /></el-icon></el-button></div>
    <el-alert v-if="error" :title="error" type="error" show-icon />
    <el-descriptions v-else :column="2" border>
      <el-descriptions-item label="数据库"><el-tag>{{ data.database || 'unknown' }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="迁移状态"><el-tag :type="data.migration_schema === 'ok' ? 'success' : 'info'">{{ data.latest_migration || data.migration_schema }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="待处理任务">{{ data.pending_jobs ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="服务端 outbox">{{ data.server_outbox ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="死信项目">{{ data.dead_letters ?? 0 }}</el-descriptions-item>
    </el-descriptions>
  </section>
</template>
