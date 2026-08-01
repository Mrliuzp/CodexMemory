<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { extractContractRevision, getContractRevision, getContractRevisionMarkdown, publishContractRevision } from '../contractServices'

const route = useRoute()
const router = useRouter()
const revision = ref(null)
const expectedHash = ref('')
const loading = ref(false)
const publishing = ref(false)
const error = ref('')
const success = ref('')

const statusLabels = { proposed: '待发布', published: '已发布', superseded: '已被替代' }

function displayStatus(value) {
  return statusLabels[value] || value || '未知'
}

function statusType(value) {
  if (value === 'published') return 'success'
  if (value === 'proposed') return 'warning'
  return 'info'
}

function displayJson(value) {
  if (value === null || value === undefined || value === '') return '-'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function revisionValue(value) {
  return value?.revision_number ?? value?.revision ?? route.params.revisionNumber
}

function validationErrors() {
  return revision.value?.validation?.errors || revision.value?.validation_errors || []
}

function warnings() {
  return revision.value?.warnings || revision.value?.validation?.warnings || []
}

function operations() {
  return revision.value?.operations || revision.value?.operation_summary || []
}

async function load() {
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    revision.value = extractContractRevision(await getContractRevision(route.params.serviceId, route.params.revisionNumber))
    expectedHash.value = revision.value.content_hash || ''
  } catch (cause) {
    revision.value = null
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function publish() {
  if (!expectedHash.value.trim()) {
    error.value = '请填写 expected_content_hash'
    return
  }
  publishing.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await publishContractRevision(route.params.serviceId, route.params.revisionNumber, expectedHash.value.trim())
    revision.value = extractContractRevision(result)
    expectedHash.value = revision.value.content_hash || expectedHash.value.trim()
    success.value = 'Revision 发布请求已完成'
  } catch (cause) {
    error.value = cause.message
  } finally {
    publishing.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-heading">
    <div><span class="eyebrow">接口契约 / Revision</span><h2>Revision 详情</h2></div>
    <div class="heading-actions"><el-button @click="router.push({ name: 'contract-service-detail', params: { serviceId: route.params.serviceId } })">返回服务</el-button><el-button :loading="loading" @click="load">刷新</el-button></div>
  </section>

  <el-alert v-if="error" :title="error" type="error" show-icon closable @close="error = ''" />
  <el-alert v-if="success" :title="success" type="success" show-icon closable @close="success = ''" />
  <div v-loading="loading" class="contract-detail">
    <el-card v-if="revision" class="contract-card">
      <template #header><span>Revision 概览</span></template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="Revision">{{ revisionValue(revision) }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusType(revision.status)">{{ displayStatus(revision.status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="Profile">{{ revision.profile_version || 'v1' }}</el-descriptions-item>
        <el-descriptions-item label="内容哈希" :span="3"><span class="mono-value">{{ revision.content_hash }}</span></el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ revision.created_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="文件名">{{ revision.source_filename || revision.filename || '-' }}</el-descriptions-item>
        <el-descriptions-item label="文件大小">{{ revision.size_bytes ? `${revision.size_bytes} 字节` : '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card v-if="revision" class="contract-card">
      <template #header><span>手工发布</span></template>
      <div class="publish-row">
        <el-input v-model="expectedHash" class="hash-input" placeholder="输入 expected_content_hash" aria-label="expected_content_hash" />
        <el-button type="primary" :loading="publishing" :disabled="revision.status === 'superseded'" @click="publish">确认发布</el-button>
      </div>
      <div class="field-hint">发布请求必须携带 expected_content_hash；上传和查看不会触发发布。</div>
    </el-card>

    <el-card v-if="revision" class="contract-card">
      <template #header><span>校验结果</span></template>
      <el-alert v-if="validationErrors().length" title="存在校验错误" type="error" show-icon />
      <el-alert v-else title="校验通过" type="success" show-icon />
      <ul v-if="validationErrors().length" class="contract-list"><li v-for="(item, index) in validationErrors()" :key="`error-${index}`">{{ displayJson(item) }}</li></ul>
      <el-alert v-if="warnings().length" class="inline-alert" title="校验告警" type="warning" show-icon />
      <ul v-if="warnings().length" class="contract-list"><li v-for="(item, index) in warnings()" :key="`warning-${index}`">{{ displayJson(item) }}</li></ul>
    </el-card>

    <el-card v-if="revision" class="contract-card">
      <template #header><span>操作摘要</span></template>
      <el-table v-if="Array.isArray(operations())" :data="operations()" stripe empty-text="暂无操作摘要">
        <el-table-column prop="method" label="方法" width="100" />
        <el-table-column prop="path" label="路径" min-width="280" />
        <el-table-column prop="operationId" label="operationId" min-width="220" />
        <el-table-column prop="summary" label="说明" min-width="220" />
      </el-table>
      <pre v-else class="report-text">{{ displayJson(operations()) }}</pre>
    </el-card>

    <el-card v-if="revision" class="contract-card">
      <template #header><span>规范化文档</span></template>
      <pre class="report-text">{{ displayJson(revision.normalized_document || revision.normalized_spec || revision.document) }}</pre>
    </el-card>

    <el-card v-if="revision" class="contract-card">
      <template #header><span>Markdown 纯文本预览</span></template>
      <pre class="markdown-preview">{{ getContractRevisionMarkdown(revision) }}</pre>
    </el-card>
  </div>
</template>
