<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { adminGet } from '../api'

const loading = ref(true)
const error = ref('')
const data = ref({})
const migrationText = computed(() => ({
  completed: '已完成',
  importing: '导入中',
  inventory: '待导入',
  not_applicable: '未适用',
}[data.value.latest_migration] || (data.value.migration_schema === 'ok' ? '就绪' : '需检查')))

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    data.value = (await adminGet('/system/status')).data
  } catch (requestError) {
    error.value = requestError.message
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section v-loading="loading">
    <div class="section-heading">
      <div><span class="eyebrow">运行观测</span><h2>系统状态</h2></div>
      <el-tooltip content="刷新状态"><el-button circle aria-label="刷新状态" @click="refresh"><el-icon><Refresh /></el-icon></el-button></el-tooltip>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon />
    <el-descriptions v-else :column="2" border>
      <el-descriptions-item label="数据库"><el-tag>{{ data.database || '未知' }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="迁移状态"><el-tag :type="data.migration_schema === 'ok' ? 'success' : 'info'">{{ migrationText }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="待处理任务">{{ data.pending_jobs ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="服务端待投递事件">{{ data.server_outbox ?? 0 }}</el-descriptions-item>
      <el-descriptions-item label="死信事件">{{ data.dead_letters ?? 0 }}</el-descriptions-item>
    </el-descriptions>
  </section>
</template>