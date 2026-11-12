<script setup lang="ts">
const { t } = useI18n()
// Keep old bookmarks working; personal records now live in the chat interface.
await navigateTo('/chat', { replace: true, redirectCode: 302 })
</script>

<template><p class="p-6 text-stone-500">{{ t('common.openingChat') }}</p></template>
