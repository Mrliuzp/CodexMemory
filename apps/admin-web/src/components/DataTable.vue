<script setup>
import CopyableText from './CopyableText.vue'
import DateTime from './DateTime.vue'
import EmptyState from './EmptyState.vue'
import StatusTag from './StatusTag.vue'
import { displayValue } from '../utils/format'
defineProps({ rows: { type: Array, default: () => [] }, columns: { type: Array, default: () => [] }, loading: { type: Boolean, default: false }, rowKey: { type: [String, Function], default: 'id' }, emptyTitle: { type: String, default: '暂无记录' }, emptyDescription: { type: String, default: '调整筛选条件后再试。' } })
defineEmits(['row-click'])
</script>

<template><div class="data-table-wrap"><el-table v-loading="loading" :data="rows" :row-key="rowKey" class="data-table" @row-click="(...args) => $emit('row-click', ...args)"><template #empty><EmptyState compact :title="emptyTitle" :description="emptyDescription" /></template><el-table-column v-for="column in columns" :key="column.key" :prop="column.key" :label="column.label" :min-width="column.minWidth" :width="column.width" :align="column.align"><template #default="scope"><slot :name="`cell-${column.key}`" :row="scope.row" :value="scope.row[column.key]" :column="column"><DateTime v-if="column.type === 'date'" :value="scope.row[column.key]" /><StatusTag v-else-if="column.type === 'status'" :status="scope.row[column.key]" /><CopyableText v-else-if="column.type === 'mono'" :value="scope.row[column.key]" :truncate="column.truncate || 0" /><span v-else :class="{ 'cell-clamp': column.clamp }">{{ column.formatter ? column.formatter(scope.row[column.key], scope.row) : displayValue(scope.row[column.key]) }}</span></slot></template></el-table-column><el-table-column v-if="$slots.actions" label="操作" fixed="right" width="132"><template #default="scope"><slot name="actions" :row="scope.row" /></template></el-table-column></el-table></div></template>
