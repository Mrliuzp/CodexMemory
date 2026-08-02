<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ modelValue: { type: Boolean, default: false }, reason: { type: String, default: '' }, title: { type: String, default: '确认操作' }, confirmText: { type: String, default: '确认' }, loading: { type: Boolean, default: false }, danger: { type: Boolean, default: false }, requiresReason: { type: Boolean, default: false }, reasonLabel: { type: String, default: '操作原因' } })
const emit = defineEmits(['update:modelValue', 'update:reason', 'confirm'])
const localReason = ref(props.reason)
const allowed = computed(() => !props.requiresReason || Boolean(localReason.value.trim()))
watch(() => props.reason, (value) => { localReason.value = value })
watch(localReason, (value) => emit('update:reason', value))
watch(() => props.modelValue, (open) => { if (open && !props.reason) localReason.value = '' })
</script>

<template><el-dialog :model-value="modelValue" :title="title" width="min(520px, 92vw)" :close-on-click-modal="!loading" :close-on-press-escape="!loading" @update:model-value="$emit('update:modelValue', $event)"><div class="confirm-dialog__body"><slot /><div v-if="$slots.impact" class="impact-box"><slot name="impact" /></div><el-form-item v-if="requiresReason" :label="reasonLabel" required><el-input v-model="localReason" type="textarea" :rows="3" maxlength="300" show-word-limit placeholder="请说明执行此操作的原因" /></el-form-item></div><template #footer><el-button :disabled="loading" @click="$emit('update:modelValue', false)">取消</el-button><el-button :type="danger ? 'danger' : 'primary'" :loading="loading" :disabled="!allowed" @click="$emit('confirm', localReason.trim())">{{ confirmText }}</el-button></template></el-dialog></template>
