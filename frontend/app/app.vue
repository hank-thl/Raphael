<script setup lang="ts">
const { t, locale } = useI18n()
useHead(() => ({
  htmlAttrs: { lang: locale.value, dir: 'ltr' },
  title: t('meta.title'),
  meta: [{ name: 'description', content: t('meta.description') }],
}))
</script>

<template>
  <NuxtRouteAnnouncer />
  <NuxtLoadingIndicator color="#115e59" />
  <NuxtPage />
</template>
