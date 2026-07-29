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

const reports = computed(() => taskRun.value?.reports || [])
const gitBaseline = computed(() => taskRun.value?.git_baseline || {})
const events = computed(() => taskRun.value?.events || [])
const fileChanges = computed(() => report.value?.file_changes || [])

const statusLabels = { pending: '待开始', running: '运行中', completed: '已完成', failed: '失败', open: '进行中', closed: '已关闭' }

function displayStatus(status) { return statusLabels[status] || status || '未知' }
function statusType(status) {
  if (['completed', 'closed'].includes(status)) return 'success'
  if (status === 'failed') return 'danger'
  if (['running', 'open'].includes(status)) return 'warning'
  return 'info'
}
function reportKindLabel(kind) {
  return kind === 'checkpoint' ? 'checkpoint（检查点）' : kind === 'final' ? 'final（最终报告）' : kind || '未知'
}
function displayTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
function displayValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
function displayJson(value) {
  if (value === null || value === undefined || value === '') return '-'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

async function loadReport(nextRevision, updateUrl = true) {
  const selectedRevision = String(nextRevision || '').trim()
  if (!selectedRevision) {
    report.value = null
    return
  }
  revision.value = selectedRevision
  reportLoading.value = true
  error.value = ''
  try {
    const result = await getTaskRunReport(route.params.id, selectedRevision)
    report.value = unwrapData(result)
    if (updateUrl) router.replace({ query: { revision: selectedRevision } })
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
    await loadReport(revision.value || String(taskRun.value.current_report_revision), false)
  } catch (cause) {
    taskRun.value = null
    report.value = null
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
        <el-descriptions-item label="运行 ID">{{ taskRun.id }}</el-descriptions-item>
        <el-descriptions-item label="项目 ID">{{ taskRun.project_id }}</el-descriptions-item>
        <el-descriptions-item label="会话标识">{{ taskRun.session_key }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusType(taskRun.status)">{{ displayStatus(taskRun.status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ displayTime(taskRun.started_at) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ displayTime(taskRun.ended_at) }}</el-descriptions-item>
        <el-descriptions-item label="当前报告版本">{{ taskRun.current_report_revision }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card v-if="taskRun" class="task-card">
      <template #header><span>报告版本</span></template>
      <el-table :data="reports" stripe empty-text="暂无报告版本" class="report-versions-table">
        <el-table-column prop="revision" label="版本" width="90" />
        <el-table-column label="类型" width="180"><template #default="scope">{{ reportKindLabel(scope.row.report_kind) }}</template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ displayStatus(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="不确定" width="100"><template #default="scope">{{ scope.row.uncertain ? '是' : '否' }}</template></el-table-column>
        <el-table-column label="已截断" width="100"><template #default="scope">{{ scope.row.truncated ? '是' : '否' }}</template></el-table-column>
        <el-table-column label="创建时间" min-width="180"><template #default="scope">{{ displayTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="120"><template #default="scope"><el-button link type="primary" :loading="reportLoading && revision === String(scope.row.revision)" @click="loadReport(scope.row.revision)">读取版本</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <template v-if="report">
      <el-card class="task-card">
        <template #header><span>报告固定字段</span></template>
        <div class="task-status-row">
          <el-tag type="primary">版本 {{ report.revision }}</el-tag>
          <el-tag :type="report.report_kind === 'final' ? 'success' : 'warning'">{{ reportKindLabel(report.report_kind) }}</el-tag>
          <el-tag :type="report.uncertain ? 'danger' : 'success'">{{ report.uncertain ? '归因不确定' : '归因确定' }}</el-tag>
          <el-tag :type="report.truncated ? 'warning' : 'success'">{{ report.truncated ? '内容已截断' : '内容完整' }}</el-tag>
        </div>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="报告 ID">{{ report.id }}</el-descriptions-item>
          <el-descriptions-item label="项目 ID">{{ report.project_id }}</el-descriptions-item>
          <el-descriptions-item label="任务运行 ID">{{ report.task_run_id }}</el-descriptions-item>
          <el-descriptions-item label="来源事件 ID">{{ displayValue(report.source_event_id) }}</el-descriptions-item>
          <el-descriptions-item label="状态"><el-tag :type="statusType(report.status)">{{ displayStatus(report.status) }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ displayTime(report.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="内容哈希" :span="3">{{ report.content_hash }}</el-descriptions-item>
        </el-descriptions>
        <div class="report-body"><h3>报告正文</h3><pre class="report-text">{{ displayJson(report.body) }}</pre></div>
        <div class="report-body"><h3>报告结构化内容</h3><pre class="report-text">{{ displayJson(report.report_json) }}</pre></div>
      </el-card>

      <el-card class="task-card">
        <template #header><span>ChangeManifest 文件变更</span></template>
        <el-table :data="fileChanges" stripe empty-text="暂无文件变更">
          <el-table-column prop="change_index" label="序号" width="80" />
          <el-table-column prop="path" label="路径" min-width="280" />
          <el-table-column prop="old_path" label="旧路径" min-width="240" />
          <el-table-column prop="change_type" label="变更类型" width="120" />
          <el-table-column prop="before_hash" label="变更前哈希" min-width="180" show-overflow-tooltip />
          <el-table-column prop="after_hash" label="变更后哈希" min-width="180" show-overflow-tooltip />
          <el-table-column prop="attribution" label="归因" width="120" />
          <el-table-column label="元数据" min-width="220"><template #default="scope"><span class="mono-value">{{ displayJson(scope.row.metadata) }}</span></template></el-table-column>
        </el-table>
      </el-card>

      <el-card class="task-card">
        <template #header><span>Git 基线</span></template>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="分支">{{ gitBaseline.branch }}</el-descriptions-item>
          <el-descriptions-item label="提交">{{ gitBaseline.head }}</el-descriptions-item>
          <el-descriptions-item label="可用"><el-tag :type="gitBaseline.available ? 'success' : 'warning'">{{ gitBaseline.available ? '是' : '否' }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="差异哈希">{{ gitBaseline.diff_hash }}</el-descriptions-item>
          <el-descriptions-item label="未跟踪文件">{{ displayJson(gitBaseline.untracked) }}</el-descriptions-item>
          <el-descriptions-item label="工作区状态"><pre class="inline-pre">{{ gitBaseline.status_porcelain || '干净' }}</pre></el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card class="task-card">
        <template #header><span>事件与工具摘要</span></template>
        <el-table :data="events" stripe empty-text="暂无事件">
          <el-table-column prop="sequence_no" label="序号" width="80" />
          <el-table-column prop="event_type" label="事件类型" width="160" />
          <el-table-column prop="event_key" label="事件键" min-width="220" show-overflow-tooltip />
          <el-table-column prop="occurred_at" label="发生时间" min-width="180" />
          <el-table-column prop="command_summary" label="命令摘要" min-width="260" show-overflow-tooltip />
          <el-table-column prop="result_summary" label="结果摘要" min-width="260" show-overflow-tooltip />
          <el-table-column prop="exit_code" label="退出码" width="90" />
          <el-table-column label="脱敏/截断" width="140"><template #default="scope">{{ scope.row.redaction_applied ? '已脱敏' : '未脱敏' }} / {{ scope.row.truncated ? '已截断' : '完整' }}</template></el-table-column>
        </el-table>
      </el-card>
    </template>
    <el-card v-else class="task-card"><el-empty description="请选择报告版本" /></el-card>
  </div>
</template>
