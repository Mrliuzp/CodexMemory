<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { adminGet } from '../api'
const route = useRoute(); const router = useRouter(); const kind = ref(route.query.kind || 'candidates'); const rows = ref([]); const loading = ref(false); const error = ref('')
const labels = { candidates: '候选记忆', memories: '已接受记忆', jobs: '处理任务', 'raw-records': '原始记录' }
const heading = computed(() => labels[kind.value] || '只读数据')
async function load() { loading.value = true; error.value = ''; router.replace({ query: { kind: kind.value } }); try { rows.value = (await adminGet(`/${kind.value}`, { page: 1, page_size: 50 })).data } catch (e) { error.value = e.message } finally { loading.value = false } }
onMounted(load)
</script>
<template>
  <section class="section-heading"><div><span class="eyebrow">DATA EXPLORER</span><h2>{{ heading }}</h2></div><el-select v-model="kind" style="width: 180px" @change="load"><el-option v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></el-select></section>
  <el-alert v-if="error" :title="error" type="warning" show-icon /><el-table v-loading="loading" :data="rows" stripe empty-text="暂无数据"><el-table-column prop="id" label="ID" width="90" /><el-table-column prop="title" label="标题" min-width="220" /><el-table-column prop="status" label="状态" width="140" /><el-table-column prop="memory_type" label="类型" width="140" /><el-table-column prop="created_at" label="创建时间" min-width="220" /></el-table>
</template>
