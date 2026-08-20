<script setup>
import { computed, ref } from 'vue'
import { getErrorMessage } from '../api'
import {
  SAFE_FEATURE_FLAG_DEFINITIONS,
  buildDecisionPolicyPayload,
  buildFeatureFlagsPayload,
  synchronizeDecisionPolicy,
  validateDecisionPolicy,
} from '../decisionPolicy'

const props = defineProps({
  projectKey: { type: String, default: '' },
  policy: { type: Object, default: null },
  flags: { type: Object, default: null },
  savingSection: { type: String, default: '' },
  error: { type: [Object, String], default: null },
  canEdit: { type: Boolean, default: false },
})

const emit = defineEmits(['save-policy', 'save-flags'])
const localValidationError = ref('')
const isSaving = computed(() => Boolean(props.savingSection))
const policyValidationError = computed(() => validateDecisionPolicy(props.policy))
const errorMessage = computed(() => localValidationError.value || (props.error ? getErrorMessage(props.error) : ''))
const debugText = computed(() => JSON.stringify(
  props.error?.payload || {
    status: props.error?.status || 0,
    code: props.error?.code || 'request_failed',
    request_id: props.error?.requestId || '',
    message: props.error?.message || '',
    meta: props.error?.meta || {},
  },
  null,
  2,
))

function syncPolicy() {
  localValidationError.value = ''
  synchronizeDecisionPolicy(props.policy)
}

function savePolicy() {
  localValidationError.value = ''
  try {
    emit('save-policy', buildDecisionPolicyPayload(props.policy))
  } catch (error) {
    localValidationError.value = getErrorMessage(error)
  }
}

function saveFlags() {
  localValidationError.value = ''
  try {
    emit('save-flags', buildFeatureFlagsPayload(props.flags))
  } catch (error) {
    localValidationError.value = getErrorMessage(error)
  }
}
</script>

<template>
  <section class="system-detail-card project-governance" aria-labelledby="project-governance-title">
    <div class="section-heading">
      <div><span class="eyebrow">项目治理</span><h2 id="project-governance-title">决策策略与功能开关</h2></div>
      <span class="muted">项目：{{ projectKey || '未选择' }}</span>
    </div>

    <el-alert v-if="!projectKey" title="请先在顶栏选择项目，才能查看或配置项目治理。" type="info" :closable="false" />
    <template v-else>
      <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />
      <details v-if="error" class="config-debug">
        <summary>查看调试信息</summary>
        <pre>{{ debugText }}</pre>
      </details>

      <p class="config-note">配置范围固定为 <code>L1 / project</code>。先保存决策策略，再按需保存项目功能开关；页面不会提供放宽到其他层级或作用域的设置。</p>

      <div v-if="policy" class="policy-config">
        <div class="policy-config__row">
          <span>策略启用</span>
          <el-switch v-model="policy.enabled" :disabled="!canEdit || isSaving" active-text="启用" inactive-text="关闭" aria-label="策略启用" @change="syncPolicy" />
        </div>
        <div class="policy-config__row">
          <span>自动发布启用</span>
          <el-switch v-model="policy.auto_publish_enabled" :disabled="!canEdit || !policy.enabled || isSaving" active-text="启用" inactive-text="关闭" aria-label="自动发布启用" @change="syncPolicy" />
        </div>
        <div class="policy-config__row">
          <span>执行模式</span>
          <el-select v-model="policy.strategy" :disabled="!canEdit || !policy.enabled || isSaving" style="width: 180px" aria-label="执行模式" @change="syncPolicy">
            <el-option label="人工审核" value="manual_review" />
            <el-option label="自动发布（仅 L1）" value="auto_publish" :disabled="!policy.auto_publish_enabled" />
          </el-select>
        </div>
        <div class="policy-config__row">
          <span>最小可信度阈值</span>
          <el-input-number v-model="policy.min_confidence" :min="0" :max="1" :step="0.01" :precision="2" :disabled="!canEdit || isSaving" aria-label="最小可信度阈值" />
        </div>
        <div class="policy-config__row"><span>固定安全边界</span><code>L1 / project</code></div>
        <el-alert v-if="policyValidationError" :title="policyValidationError" type="warning" :closable="false" />
        <el-button type="primary" :loading="savingSection === 'policy'" :disabled="!canEdit || Boolean(policyValidationError)" @click="savePolicy">保存决策策略</el-button>
      </div>
      <p v-else class="muted config-empty">项目决策策略尚未读取。</p>

      <div v-if="flags" class="flag-config">
        <div class="flag-config__head"><span>项目功能开关</span><span class="muted">仅显示当前项目治理范围内的安全开关</span></div>
        <el-alert v-if="!flags.initialized" title="项目功能开关记录尚未初始化，当前按关闭处理；保存前请确认项目初始化状态。" type="warning" :closable="false" show-icon />
        <div class="flag-config__grid">
          <div v-for="item in SAFE_FEATURE_FLAG_DEFINITIONS" :key="item.key" class="flag-config__item">
            <span>{{ item.label }}</span>
            <el-switch v-model="flags.flags[item.key]" :disabled="!canEdit || isSaving" :aria-label="item.label" />
          </div>
        </div>
        <el-button :loading="savingSection === 'flags'" :disabled="!canEdit" @click="saveFlags">保存功能开关</el-button>
      </div>
      <p v-else class="muted config-empty">项目功能开关尚未读取。</p>
    </template>
  </section>
</template>
