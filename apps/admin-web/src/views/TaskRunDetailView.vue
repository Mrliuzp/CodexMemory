<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { extractTaskRun, getTaskRun, getTaskRunReport, unwrapData } from '../taskRuns'

const route = useRoute()
const router = useRouter()
const taskRun = ref(null)
const report = ref(null)
const revision = ref(String(route.query.revision || ''))
const loading = ref(false)
const reportLoading = ref(false)
const error = ref('')

const reportJson = computed(() => JSON.stringify(report.value, null, 2))

function displayTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadReport() {
  if (!revision.value.trim()) return
  reportLoading.value = true
  error.value = ''
  try {
    const result = await getTaskRunReport(route.params.id, revision.value.trim())
    report.value = unwrapData(result)
    router.replace({ query: { revision: revision.value.trim() } })
  } catch (cause) {
    report.value = null
    error.value = cause.message
  } finally {
    reportLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await getTaskRun(route.params.id)
    taskRun.value = extractTaskRun(result)
  } catch (cause) {
    taskRun.value = null
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">执行观测 / 任务报告</span><h2>任务运行详情</h2></div>
    <el-button @click="router.push({ name: 'task-runs' })">返回列表</el-button>
  </section>

  <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error = ''" />
  <div v-loading="loading" class="task-detail">
    <el-card v-if="taskRun" class="task-card">
      <template #header><span>运行概览</span></template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="运行 ID">{{ taskRun.id ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="项目 ID">{{ taskRun.project_id ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="会话标识">{{ taskRun.session_key ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ taskRun.status ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ displayTime(taskRun.started_at) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ displayTime(taskRun.ended_at) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="task-card">
      <template #header><span>报告版本</span></template>
      <el-form inline @submit.prevent="loadReport">
        <el-form-item label="版本号"><el-input v-model="revision" placeholder="请输入后端返回的 revision" /></el-form-item>
        <el-button type="primary" :loading="reportLoading" @click="loadReport">读取报告</el-button>
      </el-form>
      <div class="field-hint">当前页面暂保留骨架，待 V1.4 后端实际接口结构确认后接入版本选择和报告字段。</div>
    </el-card>

    <template v-if="report">
      <el-card class="task-card">
        <template #header><span>报告内容</span></template>
        <pre class="report-json">{{ reportJson }}</pre>
      </el-card>
      <el-card class="task-card">
        <template #header><span>ChangeManifest</span></template>
        <el-empty description="待后端实际接口结构确认后展示变更清单" />
      </el-card>
      <el-card class="task-card">
        <template #header><span>checkpoint / final、不确定性与截断状态</span></template>
        <el-empty description="待后端实际接口结构确认后展示报告类型、不确定性和截断状态" />
      </el-card>
    </template>
    <el-card v-else class="task-card">
      <template #header><span>报告预览</span></template>
      <el-empty description="请输入 revision 读取只读报告" />
    </el-card>
  </div>
</template>
