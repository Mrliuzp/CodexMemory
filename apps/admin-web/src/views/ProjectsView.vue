<script setup>
import { onMounted, ref } from 'vue'
import { adminGet } from '../api'
const rows = ref([]); const loading = ref(true); const error = ref('')
onMounted(async () => {
  try {
    const result = await adminGet('/projects', { page: 1, page_size: 100 })
    rows.value = Array.isArray(result.data) ? result.data : []
  } catch (e) {
    rows.value = []
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>
<template>
  <el-alert v-if="error" :title="error" type="warning" show-icon />
  <section class="section-heading"><div><span class="eyebrow">项目</span><h2>授权项目</h2></div><span class="muted">{{ rows.length }} 个项目</span></section>
  <el-table v-loading="loading" :data="rows" stripe empty-text="暂无项目">
    <el-table-column prop="project_key" label="项目键" min-width="180" /><el-table-column prop="name" label="名称" min-width="180" /><el-table-column prop="repository" label="仓库" min-width="240" /><el-table-column prop="status" label="状态" width="120"><template #default="scope"><el-tag :type="scope.row.status === 'active' ? 'success' : 'info'">{{ scope.row.status === 'active' ? '活跃' : scope.row.status }}</el-tag></template></el-table-column>
  </el-table>
</template>
