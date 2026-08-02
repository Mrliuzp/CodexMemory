<script setup>
defineProps({ modelValue: { type: Boolean, default: false }, title: { type: String, default: '详情' }, size: { type: [String, Number], default: 'min(720px, 92vw)' }, loading: { type: Boolean, default: false } })
defineEmits(['update:modelValue'])
</script>

<template><el-drawer :model-value="modelValue" :title="title" :size="size" destroy-on-close class="detail-drawer" @update:model-value="$emit('update:modelValue', $event)"><div v-loading="loading" class="detail-drawer__body"><slot /></div><template v-if="$slots.footer" #footer><slot name="footer" /></template></el-drawer></template>
