<script setup>
import { computed, ref } from 'vue'
import { Check, CopyDocument } from '@element-plus/icons-vue'

const props = defineProps({ value: { type: [String, Number], default: '' }, truncate: { type: Number, default: 0 }, mono: { type: Boolean, default: true } })
const copied = ref(false)
const text = computed(() => String(props.value ?? ''))
const shown = computed(() => props.truncate > 0 && text.value.length > props.truncate ? `${text.value.slice(0, props.truncate)}…` : text.value)
async function copy() {
  if (!text.value) return
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text.value)
  else {
    const node = document.createElement('textarea')
    node.value = text.value
    node.style.position = 'fixed'
    node.style.opacity = '0'
    document.body.appendChild(node)
    node.select()
    document.execCommand('copy')
    node.remove()
  }
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1400)
}
</script>

<template><span class="copyable-text" :class="{ 'mono-value': mono }"><span :title="text">{{ shown || '-' }}</span><el-tooltip :content="copied ? '已复制' : '复制'"><el-button text circle :aria-label="copied ? '已复制' : '复制内容'" @click.stop="copy"><el-icon><Check v-if="copied" /><CopyDocument v-else /></el-icon></el-button></el-tooltip></span></template>
