<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import {
  extractTaskRun,
  getTaskRun,
  getTaskRunReport,
  taskRunProjectLabel,
  taskRunPrompt,
  taskRunStatusLabels,
  unwrapData,
} from '../taskRuns'

const route = useRoute()
const router = useRouter()
const taskRun = ref(null)
const report = ref(null)
const revision = ref(String(route.query.revision || ''))
const activeTab = ref(['overview', 'report', 'files', 'git', 'events'].includes(String(route.query.tab)) ? String(route.query.tab) : 'overview')
const loading = ref(false)
const reportLoading = ref(false)
const error = ref(null)

const reports = computed(() => taskRun.value?.reports || [])
const gitBaseline = computed(() => taskRun.value?.git_baseline || {})
const events = computed(() => taskRun.value?.events || [])
const fileChanges = computed(() => report.value?.file_changes || [])
const reportBody = computed(() => {
  const value = report.value?.body
  if (value === null || value === undefined || value === '') return '暂无报告正文'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
})
const reportJson = computed(() => JSON.stringify(report.value?.report_json || {}, null, 2))

function reportKindLabel(kind) {
  if (kind === 'checkpoint') return '检查点报告'
  if (kind === 'final') return '最终报告'
  return kind || '未知类型'
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

function changeTypeLabel(type) {
  return ({ added: '新增', modified: '修改', deleted: '删除', renamed: '重命名', untracked: '未跟踪' })[type] || type || '未知'
}

function changeTypeTag(type) {
  if (type === 'added' || type === 'untracked') return 'success'
  if (type === 'deleted') return 'danger'
  if (type === 'modified' || type === 'renamed') return 'warning'
  return 'info'
}

function eventTypeLabel(type) {
  const normalized = String(type || '').replace(/([a-z])([A-Z])/g, '$1_$2').replaceAll('.', '_').toLowerCase()
  return ({
    user_prompt_submit: '用户提交任务',
    pre_tool_use: '工具执行前',
    post_tool_use: '工具执行后',
    stop: '任务停止',
    session_end: '会话结束',
  })[normalized] || type || '未知事件'
}

async function syncDetailQuery() {
  const query = { ...route.query, tab: activeTab.value }
  if (revision.value) query.revision = revision.value
  else delete query.revision
  await router.replace({ query })
}

async function loadReport(nextRevision, updateUrl = true) {
  const selectedRevision = String(nextRevision || '').trim()
  if (!selectedRevision) {
    report.value = null
    return
  }
  revision.value = selectedRevision
  reportLoading.value = true
  error.value = null
  try {
    report.value = unwrapData(await getTaskRunReport(route.params.id, selectedRevision))
    if (updateUrl) await syncDetailQuery()
  } catch (cause) {
    report.value = null
    error.value = cause
  } finally {
    reportLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    taskRun.value = extractTaskRun(await getTaskRun(route.params.id))
    const preferredRevision = revision.value || String(taskRun.value.current_report_revision || reports.value.at(-1)?.revision || '')
    await loadReport(preferredRevision, false)
    await syncDetailQuery()
  } catch (cause) {
    taskRun.value = null
    report.value = null
    error.value = cause
  } finally {
    loading.value = false
  }
}

function switchTab() {
  syncDetailQuery()
}

function backToList() {
  const query = {}
  if (taskRun.value?.project_key) query.project_key = taskRun.value.project_key
  router.push({ name: 'task-runs', query })
}

onMounted(load)
watch(() => route.query.tab, (value) => {
  const next = String(value || 'overview')
  if (['overview', 'report', 'files', 'git', 'events'].includes(next) && next !== activeTab.value) activeTab.value = next
})
watch(() => route.query.revision, (value) => {
  const next = String(value || '')
  if (next && next !== revision.value) loadReport(next, false)
})
watch(() => route.params.id, () => {
  revision.value = String(route.query.revision || '')
  load()
})
</script>

<template>
  <PageHeader eyebrow="运行监控 / 任务报告" title="任务运行详情" description="把任务结论、文件影响、Git 基线与工具事件放在同一条证据链上。">
    <template #actions>
      <el-button @click="backToList">返回列表</el-button>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </template>
  </PageHeader>

  <ErrorState v-if="error" :error="error" @retry="load" />
  <div v-loading="loading" class="task-detail">
    <section v-if="taskRun" class="task-run-identity">
      <div class="task-run-identity__main">
        <div class="task-run-identity__kicker">
          <StatusTag :status="taskRun.status" :label="taskRunStatusLabels[taskRun.status]" />
          <span>{{ taskRunProjectLabel(taskRun) }}</span>
          <span>报告 v{{ taskRun.current_report_revision || 0 }}</span>
        </div>
        <h2>{{ taskRunPrompt(taskRun) || `任务运行 #${taskRun.id}` }}</h2>
        <p v-if="taskRun.prompt_truncated">该摘要已在服务端脱敏并截断；详情页不会恢复原始敏感内容。</p>
      </div>
      <CopyableText :value="taskRun.session_key" />
    </section>

    <el-tabs v-if="taskRun" v-model="activeTab" class="task-evidence-tabs" @tab-change="switchTab">
      <el-tab-pane label="概览" name="overview">
        <div class="task-summary-grid">
          <article><span>开始时间</span><strong><DateTime :value="taskRun.started_at" /></strong></article>
          <article><span>结束时间</span><strong><DateTime :value="taskRun.ended_at" /></strong></article>
          <article><span>文件变更</span><strong>{{ fileChanges.length }}</strong></article>
          <article><span>工具事件</span><strong>{{ events.length }}</strong></article>
        </div>
        <el-card shadow="never" class="task-card">
          <template #header><div class="card-heading"><strong>报告版本</strong><span>选择任一版本查看当时的证据快照</span></div></template>
          <el-table :data="reports" row-key="id" empty-text="暂无报告版本">
            <el-table-column label="版本" width="92"><template #default="scope"><span class="mono-value">v{{ scope.row.revision }}</span></template></el-table-column>
            <el-table-column label="类型" min-width="150"><template #default="scope">{{ reportKindLabel(scope.row.report_kind) }}</template></el-table-column>
            <el-table-column label="状态" width="112"><template #default="scope"><StatusTag :status="scope.row.status" :label="taskRunStatusLabels[scope.row.status]" /></template></el-table-column>
            <el-table-column label="归因" width="120"><template #default="scope"><el-tag :type="scope.row.uncertain ? 'warning' : 'success'" effect="plain">{{ scope.row.uncertain ? '需要复核' : '明确' }}</el-tag></template></el-table-column>
            <el-table-column label="创建时间" min-width="180"><template #default="scope"><DateTime :value="scope.row.created_at" /></template></el-table-column>
            <el-table-column label="操作" width="116"><template #default="scope"><el-button link type="primary" :loading="reportLoading && revision === String(scope.row.revision)" @click="loadReport(scope.row.revision); activeTab = 'report'">查看报告</el-button></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="报告" name="report">
        <div class="report-toolbar">
          <span>报告版本</span>
          <el-select v-model="revision" :loading="reportLoading" placeholder="选择版本" style="width: 210px" @change="loadReport">
            <el-option v-for="item in reports" :key="item.revision" :label="`v${item.revision} · ${reportKindLabel(item.report_kind)}`" :value="String(item.revision)" />
          </el-select>
        </div>
        <template v-if="report">
          <div class="task-status-row">
            <el-tag type="primary">版本 {{ report.revision }}</el-tag>
            <el-tag :type="report.report_kind === 'final' ? 'success' : 'warning'">{{ reportKindLabel(report.report_kind) }}</el-tag>
            <el-tag :type="report.uncertain ? 'warning' : 'success'">{{ report.uncertain ? '归因需要复核' : '归因明确' }}</el-tag>
            <el-tag v-if="report.truncated" type="warning">内容已截断</el-tag>
          </div>
          <el-card shadow="never" class="task-card report-paper">
            <template #header><div class="card-heading"><strong>报告正文</strong><CopyableText :value="report.content_hash" /></div></template>
            <pre class="report-text">{{ reportBody }}</pre>
          </el-card>
          <el-collapse class="raw-data-collapse">
            <el-collapse-item title="查看报告结构化原始数据" name="raw-report">
              <pre class="report-text">{{ reportJson }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
        <el-empty v-else description="当前任务尚未生成报告" />
      </el-tab-pane>

      <el-tab-pane :label="`文件变更 ${fileChanges.length}`" name="files">
        <el-card shadow="never" class="task-card">
          <el-table :data="fileChanges" row-key="id" empty-text="当前报告没有文件变更">
            <el-table-column label="类型" width="104"><template #default="scope"><el-tag :type="changeTypeTag(scope.row.change_type)" effect="plain">{{ changeTypeLabel(scope.row.change_type) }}</el-tag></template></el-table-column>
            <el-table-column label="文件路径" min-width="300"><template #default="scope"><CopyableText :value="scope.row.path" /></template></el-table-column>
            <el-table-column label="原路径" min-width="240"><template #default="scope">{{ scope.row.old_path || '-' }}</template></el-table-column>
            <el-table-column label="归因" width="120"><template #default="scope">{{ displayValue(scope.row.attribution) }}</template></el-table-column>
            <el-table-column label="变更后哈希" min-width="190"><template #default="scope"><CopyableText :value="scope.row.after_hash" /></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="Git" name="git">
        <el-card shadow="never" class="task-card">
          <template #header><div class="card-heading"><strong>任务开始前的工作区基线</strong><el-tag :type="gitBaseline.available ? 'success' : 'warning'" effect="plain">{{ gitBaseline.available ? '基线可用' : '基线不可用' }}</el-tag></div></template>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="分支"><CopyableText :value="gitBaseline.branch" /></el-descriptions-item>
            <el-descriptions-item label="提交"><CopyableText :value="gitBaseline.head" /></el-descriptions-item>
            <el-descriptions-item label="差异哈希" :span="2"><CopyableText :value="gitBaseline.diff_hash" /></el-descriptions-item>
            <el-descriptions-item label="未跟踪文件" :span="2"><pre class="inline-pre">{{ displayJson(gitBaseline.untracked) }}</pre></el-descriptions-item>
            <el-descriptions-item label="工作区状态" :span="2"><pre class="inline-pre">{{ gitBaseline.status_porcelain || '工作区干净' }}</pre></el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <el-tab-pane :label="`事件 ${events.length}`" name="events">
        <el-card shadow="never" class="task-card task-timeline-card">
          <el-timeline v-if="events.length">
            <el-timeline-item v-for="event in events" :key="event.id || event.event_key" :timestamp="event.occurred_at" placement="top" :type="event.exit_code && event.exit_code !== 0 ? 'danger' : 'primary'">
              <div class="event-card">
                <div class="event-card__heading"><strong>{{ event.sequence_no }} · {{ eventTypeLabel(event.event_type) }}</strong><el-tag v-if="event.exit_code !== null && event.exit_code !== undefined" :type="event.exit_code === 0 ? 'success' : 'danger'">退出码 {{ event.exit_code }}</el-tag></div>
                <p v-if="event.command_summary"><span>命令</span>{{ event.command_summary }}</p>
                <p v-if="event.result_summary"><span>结果</span>{{ event.result_summary }}</p>
                <div class="event-card__meta"><el-tag v-if="event.redaction_applied" size="small" type="info">已脱敏</el-tag><el-tag v-if="event.truncated" size="small" type="warning">已截断</el-tag><CopyableText :value="event.event_key" /></div>
              </div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无任务事件" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.task-detail {
  min-height: 320px;
}

.task-run-identity {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;
  padding: 22px 24px;
  color: #fff;
  border-radius: 10px;
  background: #14272e;
}

.task-run-identity__main {
  min-width: 0;
}

.task-run-identity__kicker,
.task-status-row,
.event-card__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.task-run-identity__kicker {
  color: #b8d9d3;
  font-size: 13px;
}

.task-run-identity h2 {
  max-width: 900px;
  margin: 12px 0 6px;
  font-size: clamp(20px, 2.2vw, 30px);
  line-height: 1.35;
}

.task-run-identity p {
  margin: 0;
  color: #c8d8d5;
}

.task-evidence-tabs {
  padding: 0 20px 20px;
  border: 1px solid #dbe7e4;
  border-radius: 10px;
  background: #fff;
}

.task-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.task-summary-grid article {
  padding: 16px;
  border: 1px solid #dbe7e4;
  border-radius: 10px;
  background: #f6faf9;
}

.task-summary-grid span,
.card-heading span {
  display: block;
  color: #647b80;
  font-size: 13px;
}

.task-summary-grid strong {
  display: block;
  margin-top: 8px;
  color: #14272e;
  font-size: 20px;
}

.task-card + .task-card,
.raw-data-collapse {
  margin-top: 14px;
}

.card-heading,
.event-card__heading,
.report-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.report-toolbar {
  justify-content: flex-start;
  margin-bottom: 14px;
}

.task-status-row {
  margin-bottom: 12px;
}

.report-paper {
  border-left: 4px solid #0f766e;
}

.report-text,
.inline-pre {
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 13px;
  line-height: 1.65;
}

.report-text {
  max-height: 560px;
  padding: 18px;
  color: #20363d;
  border-radius: 8px;
  background: #f3f8f7;
}

.task-timeline-card {
  padding-top: 8px;
}

.event-card {
  padding: 14px 16px;
  border: 1px solid #dbe7e4;
  border-radius: 10px;
  background: #fbfdfd;
}

.event-card p {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 8px;
  margin: 10px 0;
  overflow-wrap: anywhere;
}

.event-card p span {
  color: #647b80;
}

@media (max-width: 900px) {
  .task-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .task-run-identity,
  .card-heading,
  .event-card__heading {
    align-items: stretch;
    flex-direction: column;
  }

  .task-summary-grid {
    grid-template-columns: 1fr;
  }

  .task-evidence-tabs {
    padding-inline: 12px;
  }
}
</style>
