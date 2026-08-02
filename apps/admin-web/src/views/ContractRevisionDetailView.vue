<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfirmActionDialog from '../components/ConfirmActionDialog.vue'
import CopyableText from '../components/CopyableText.vue'
import DateTime from '../components/DateTime.vue'
import ErrorState from '../components/ErrorState.vue'
import MarkdownPreview from '../components/MarkdownPreview.vue'
import PageHeader from '../components/PageHeader.vue'
import PermissionGate from '../components/PermissionGate.vue'
import StatusTag from '../components/StatusTag.vue'
import StructuredDataViewer from '../components/StructuredDataViewer.vue'
import { useSessionStore } from '../stores/session'
import {
  canPublishContractRevision,
  extractContractRevision,
  extractContractService,
  getContractRevision,
  getContractRevisionMarkdown,
  getContractService,
  getContractValidation,
  publishContractRevision,
} from '../contractServices'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const service = ref(null)
const revision = ref(null)
const activeTab = ref(['validation', 'operations', 'specification', 'markdown'].includes(String(route.query.tab)) ? String(route.query.tab) : 'validation')
const loading = ref(false)
const publishing = ref(false)
const publishVisible = ref(false)
const error = ref(null)
const success = ref('')

const validation = computed(() => getContractValidation(revision.value || {}))
const operations = computed(() => Array.isArray(revision.value?.operations) ? revision.value.operations : [])
const markdown = computed(() => getContractRevisionMarkdown(revision.value || {}))
const normalizedDocument = computed(() => revision.value?.normalized_document || revision.value?.normalized_spec || revision.value?.document || {})
const isAdmin = computed(() => session.isAdmin ?? Boolean(session.me?.permissions?.includes('admin')))
const publishAllowed = computed(() => isAdmin.value && canPublishContractRevision(revision.value || {}))

function revisionValue(value) {
  return value?.revision_number ?? value?.revision ?? route.params.revisionNumber
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function validationMessage(item) {
  if (typeof item === 'string') return item
  if (item?.message && item?.code) return `${item.message}（${item.code}）`
  if (item?.message) return item.message
  return JSON.stringify(item)
}

function methodType(method) {
  const value = String(method || '').toUpperCase()
  if (value === 'GET') return 'success'
  if (value === 'DELETE') return 'danger'
  if (['POST', 'PUT', 'PATCH'].includes(value)) return 'warning'
  return 'info'
}

async function syncTab() {
  await router.replace({ query: { ...route.query, tab: activeTab.value } })
}

async function load(options = {}) {
  loading.value = true
  if (!options.keepFeedback) {
    error.value = null
    success.value = ''
  }
  try {
    const [revisionResult, serviceResult] = await Promise.all([
      getContractRevision(route.params.serviceId, route.params.revisionNumber),
      getContractService(route.params.serviceId),
    ])
    revision.value = extractContractRevision(revisionResult)
    service.value = extractContractService(serviceResult)
    await syncTab()
  } catch (cause) {
    revision.value = null
    error.value = cause
  } finally {
    loading.value = false
  }
}

function openPublish() {
  if (!publishAllowed.value) return
  publishVisible.value = true
}

async function publish() {
  if (!publishAllowed.value || publishing.value) return
  const expectedHash = revision.value.content_hash
  publishing.value = true
  error.value = null
  success.value = ''
  try {
    revision.value = extractContractRevision(await publishContractRevision(route.params.serviceId, route.params.revisionNumber, expectedHash))
    publishVisible.value = false
    success.value = `Revision v${revisionValue(revision.value)} 已发布`
    await load({ keepFeedback: true })
  } catch (cause) {
    if (cause.status === 409) {
      await load({ keepFeedback: true })
      const conflict = new Error('Revision 内容或状态已变化，页面已刷新；请核对最新哈希和状态后再操作。')
      conflict.status = 409
      conflict.requestId = cause.requestId
      error.value = conflict
      publishVisible.value = false
    } else {
      error.value = cause
    }
  } finally {
    publishing.value = false
  }
}

onMounted(load)
watch(() => route.query.tab, (value) => {
  const next = String(value || 'validation')
  if (['validation', 'operations', 'specification', 'markdown'].includes(next) && next !== activeTab.value) activeTab.value = next
})
watch(() => `${route.params.serviceId}:${route.params.revisionNumber}`, load)
</script>

<template>
  <PageHeader eyebrow="接口治理 / Revision" :title="`Revision v${revisionValue(revision)}`" :description="`${service?.name || '接口契约服务'} · 校验结果、操作清单与可读规范预览`">
    <template #actions>
      <el-button @click="router.push({ name: 'contract-service-detail', params: { serviceId: route.params.serviceId }, query: { tab: 'revisions' } })">返回服务</el-button>
      <el-button :loading="loading" @click="load">刷新</el-button>
      <PermissionGate permission="admin"><el-button v-if="publishAllowed" type="primary" :loading="publishing" @click="openPublish">发布 Revision</el-button></PermissionGate>
    </template>
  </PageHeader>

  <el-alert v-if="!isAdmin" class="page-alert" title="当前为只读访问，可查看规范和校验结果，但不能发布 Revision。" type="info" show-icon />
  <el-alert v-if="success" class="page-alert" :title="success" type="success" show-icon closable @close="success = ''" />
  <ErrorState v-if="error" :error="error" @retry="load" />

  <div v-loading="loading" class="contract-revision-detail">
    <section v-if="revision" class="revision-identity">
      <div class="revision-identity__main">
        <div class="revision-identity__tags"><StatusTag :status="revision.status" /><el-tag :type="validation.errors.length ? 'danger' : 'success'" effect="plain">{{ validation.errors.length ? `${validation.errors.length} 个校验错误` : '校验通过' }}</el-tag><el-tag v-if="validation.warnings.length" type="warning" effect="plain">{{ validation.warnings.length }} 个告警</el-tag></div>
        <h2>{{ service?.name || `服务 #${route.params.serviceId}` }} / v{{ revisionValue(revision) }}</h2>
        <p>{{ revision.source_filename || '未记录源文件名' }} · {{ formatBytes(revision.size_bytes) }} · {{ revision.operation_count ?? operations.length }} 个操作</p>
      </div>
      <div class="revision-hash"><span>内容哈希</span><CopyableText :value="revision.content_hash" :truncate="18" /></div>
    </section>

    <div v-if="revision && !publishAllowed && revision.status === 'proposed'" class="publish-blocker">
      <strong>当前 Revision 暂不可发布</strong>
      <span>{{ validation.errors.length ? '请先修复校验错误并上传新的 Revision。' : '当前账号没有发布权限。' }}</span>
    </div>

    <el-tabs v-if="revision" v-model="activeTab" class="revision-tabs" @tab-change="syncTab">
      <el-tab-pane :label="`校验 ${validation.errors.length + validation.warnings.length}`" name="validation">
        <div class="validation-summary" :class="validation.errors.length ? 'is-error' : 'is-success'">
          <strong>{{ validation.errors.length ? '校验未通过' : '校验通过，可以进入发布确认' }}</strong>
          <span>{{ validation.errors.length ? '错误会阻止发布；告警不会阻止，但建议在发布前复核。' : `共识别 ${operations.length} 个 API 操作。` }}</span>
        </div>
        <el-card v-if="validation.errors.length" shadow="never" class="validation-card">
          <template #header><strong>错误</strong></template>
          <ul class="validation-list is-error"><li v-for="(item, index) in validation.errors" :key="`error-${index}`"><span>{{ index + 1 }}</span><p>{{ validationMessage(item) }}</p></li></ul>
        </el-card>
        <el-card v-if="validation.warnings.length" shadow="never" class="validation-card">
          <template #header><strong>告警</strong></template>
          <ul class="validation-list is-warning"><li v-for="(item, index) in validation.warnings" :key="`warning-${index}`"><span>{{ index + 1 }}</span><p>{{ validationMessage(item) }}</p></li></ul>
        </el-card>
        <el-descriptions :column="2" border class="revision-metadata">
          <el-descriptions-item label="OpenAPI 源版本">{{ revision.source_version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="规范化版本">{{ revision.normalized_version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="Profile">{{ revision.profile_version || 'v1' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间"><DateTime :value="revision.created_at" /></el-descriptions-item>
          <el-descriptions-item label="创建者">{{ revision.created_by || '-' }}</el-descriptions-item>
          <el-descriptions-item label="发布时间"><DateTime :value="revision.published_at" /></el-descriptions-item>
        </el-descriptions>
        <StructuredDataViewer :value="revision.validation || revision.validation_result || revision.validation_summary" title="查看完整校验数据" />
      </el-tab-pane>

      <el-tab-pane :label="`操作清单 ${operations.length}`" name="operations">
        <el-card shadow="never" class="operations-card">
          <el-table :data="operations" row-key="id" empty-text="暂无 API 操作">
            <el-table-column label="方法" width="100"><template #default="scope"><el-tag :type="methodType(scope.row.method)" effect="dark">{{ String(scope.row.method || '').toUpperCase() }}</el-tag></template></el-table-column>
            <el-table-column label="路径" min-width="260"><template #default="scope"><CopyableText :value="scope.row.path" /></template></el-table-column>
            <el-table-column label="operationId" min-width="230"><template #default="scope"><CopyableText :value="scope.row.operation_id || scope.row.operationId" /></template></el-table-column>
            <el-table-column prop="summary" label="说明" min-width="220" />
            <el-table-column label="状态" width="100"><template #default="scope"><el-tag v-if="scope.row.deprecated" type="warning">已弃用</el-tag><span v-else>可用</span></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="规范文档" name="specification">
        <div class="spec-heading"><div><strong>规范化 OpenAPI</strong><span>以下内容是服务端校验并规范化后的只读文档。</span></div><CopyableText :value="revision.content_hash" :truncate="18" /></div>
        <pre class="spec-document">{{ JSON.stringify(normalizedDocument, null, 2) }}</pre>
        <StructuredDataViewer :value="revision.source_document" title="查看上传时的源文档" />
      </el-tab-pane>

      <el-tab-pane label="Markdown" name="markdown">
        <div class="markdown-heading"><div><strong>前端联调说明</strong><span>预览会转义不安全 HTML，仅允许受控 Markdown 结构。</span></div></div>
        <MarkdownPreview :source="markdown" />
      </el-tab-pane>
    </el-tabs>
  </div>

  <ConfirmActionDialog v-model="publishVisible" title="发布接口契约 Revision" confirm-text="确认发布" :loading="publishing" @confirm="publish">
    <div class="publish-summary">
      <div><span>服务</span><strong>{{ service?.name || `服务 #${route.params.serviceId}` }}</strong></div>
      <div><span>Revision</span><strong>v{{ revisionValue(revision) }}</strong></div>
      <div><span>内容哈希</span><CopyableText :value="revision?.content_hash" :truncate="24" /></div>
    </div>
    <template #impact><p>发布后，此 Revision 将成为该服务的当前契约；原已发布 Revision 会进入历史状态，前端联调应以新版本为准。</p></template>
  </ConfirmActionDialog>
</template>

<style scoped>
.page-alert {
  margin-bottom: 14px;
}

.revision-identity {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 14px;
  padding: 22px 24px;
  color: #fff;
  border-radius: 10px;
  background: #14272e;
}

.revision-identity__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.revision-identity h2 {
  margin: 12px 0 6px;
  font-size: clamp(22px, 2.4vw, 32px);
}

.revision-identity p,
.revision-hash span {
  margin: 0;
  color: #b8d4d0;
}

.revision-hash {
  min-width: 180px;
  padding: 12px 14px;
  border: 1px solid rgb(255 255 255 / 13%);
  border-radius: 8px;
  background: rgb(255 255 255 / 5%);
}

.revision-hash span {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
}

.publish-blocker,
.validation-summary {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
  padding: 13px 16px;
  border-radius: 8px;
}

.publish-blocker {
  color: #7e4d0d;
  border: 1px solid #ead1aa;
  background: #fff8ec;
}

.revision-tabs {
  padding: 0 20px 20px;
  border: 1px solid #dbe7e4;
  border-radius: 10px;
  background: #fff;
}

.validation-summary {
  align-items: flex-start;
  flex-direction: column;
  gap: 4px;
  color: #27534f;
  border: 1px solid #b9d8d2;
  background: #e8f4f1;
}

.validation-summary.is-error {
  color: #8f2f3a;
  border-color: #e8bcc1;
  background: #fff3f4;
}

.validation-card + .validation-card,
.revision-metadata,
.validation-card + .revision-metadata,
.revision-metadata + :deep(.structured-viewer) {
  margin-top: 14px;
}

.validation-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.validation-list li {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #edf2f1;
}

.validation-list li:last-child {
  border-bottom: 0;
}

.validation-list li > span {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #f4dddd;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 12px;
}

.validation-list.is-warning li > span {
  background: #f7ead5;
}

.validation-list p {
  margin: 3px 0 0;
  overflow-wrap: anywhere;
}

.spec-heading,
.markdown-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 14px;
}

.spec-heading strong,
.spec-heading span,
.markdown-heading strong,
.markdown-heading span {
  display: block;
}

.spec-heading span,
.markdown-heading span {
  margin-top: 4px;
  color: #647b80;
  font-size: 13px;
}

.spec-document {
  overflow: auto;
  max-height: 680px;
  margin: 0 0 14px;
  padding: 18px;
  color: #d9ebe7;
  border-radius: 9px;
  background: #14272e;
  white-space: pre;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
}

.publish-summary {
  display: grid;
  gap: 10px;
}

.publish-summary > div {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  align-items: center;
  padding: 10px 12px;
  border: 1px solid #dbe7e4;
  border-radius: 8px;
}

.publish-summary span {
  color: #647b80;
}

@media (max-width: 640px) {
  .revision-identity {
    flex-direction: column;
  }

  .revision-hash {
    width: 100%;
  }

  .revision-tabs {
    padding-inline: 12px;
  }

  .publish-blocker {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }
}
</style>
