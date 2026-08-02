<script setup>
defineProps({ loading: { type: Boolean, default: false } })
</script>

<template>
  <section class="filter-bar" :aria-busy="loading">
    <div class="filter-bar__fields"><slot /></div>
    <div v-if="$slots.actions" class="filter-bar__actions"><slot name="actions" /></div>
  </section>
</template>
