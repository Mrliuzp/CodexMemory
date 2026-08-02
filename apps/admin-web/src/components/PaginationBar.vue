<script setup>
defineProps({ page: { type: Number, default: 1 }, pageSize: { type: Number, default: 20 }, total: { type: Number, default: 0 }, disabled: { type: Boolean, default: false }, pageSizes: { type: Array, default: () => [20, 50, 100] } })
const emit = defineEmits(['update:page', 'update:pageSize', 'change'])
function sizeChange(value) { emit('update:pageSize', value); emit('update:page', 1); emit('change', { page: 1, pageSize: value }) }
function pageChange(value) { emit('update:page', value); emit('change', { page: value }) }
</script>

<template><div class="pagination-bar"><span class="pagination-bar__total">共 {{ total }} 条</span><el-pagination :current-page="page" :page-size="pageSize" :page-sizes="pageSizes" :total="total" :disabled="disabled" layout="sizes, prev, pager, next" background @update:page-size="sizeChange" @update:current-page="pageChange" /></div></template>
