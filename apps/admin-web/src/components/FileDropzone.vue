<script setup>
import { computed, ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
const props = defineProps({ modelValue: { type: [File, Object], default: null }, accept: { type: String, default: '' }, maxSize: { type: Number, default: 20 * 1024 * 1024 }, disabled: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'error'])
const input = ref(null)
const active = ref(false)
const acceptText = computed(() => props.accept || '项目支持的文件格式')
function choose(file) {
  if (!file || props.disabled) return
  if (file.size > props.maxSize) { emit('error', `文件不能超过 ${Math.round(props.maxSize / 1024 / 1024)} MB`); return }
  emit('update:modelValue', file)
}
function drop(event) { active.value = false; choose(event.dataTransfer.files?.[0]) }
</script>

<template><div class="file-dropzone" :class="{ 'is-active': active, 'is-disabled': disabled }" role="button" tabindex="0" @click="input?.click()" @keydown.enter.prevent="input?.click()" @keydown.space.prevent="input?.click()" @dragover.prevent="active = true" @dragleave.prevent="active = false" @drop.prevent="drop"><input ref="input" type="file" :accept="accept" :disabled="disabled" hidden @change="choose($event.target.files?.[0])"><el-icon><UploadFilled /></el-icon><strong>{{ modelValue?.name || '拖放文件到这里，或点击选择' }}</strong><span>{{ modelValue ? `${Math.ceil(modelValue.size / 1024)} KB` : acceptText }}</span></div></template>
