<script setup>
import { computed, ref } from 'vue'
import ReadableValue from './ReadableValue.vue'
import { serializeRawJson } from '../utils/format'

const props = defineProps({ value: { type: [Object, Array, String, Number, Boolean], default: null }, title: { type: String, default: '查看原始数据' }, open: { type: Boolean, default: false } })
const copied = ref(false)
const formatted = computed(() => serializeRawJson(props.value))

async function copyRaw() {
  if (!formatted.value) return
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(formatted.value)
    else {
      const node = document.createElement('textarea')
      node.value = formatted.value
      node.style.position = 'fixed'
      node.style.opacity = '0'
      document.body.appendChild(node)
      node.select()
      document.execCommand('copy')
      node.remove()
    }
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1400)
  } catch {
    copied.value = false
  }
}
</script>

<template><section class="structured-viewer"><div class="structured-viewer__readable"><ReadableValue :value="value" /></div><details :open="open"><summary>{{ title }}</summary><div class="structured-viewer__actions"><button type="button" class="structured-viewer__copy" @click.stop="copyRaw">{{ copied ? '已复制' : '复制原始数据' }}</button></div><pre>{{ formatted || '暂无数据' }}</pre></details></section></template>
