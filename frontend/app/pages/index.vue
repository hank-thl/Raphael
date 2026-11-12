<script setup lang="ts">
const { t } = useI18n()
</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-2xl flex-col justify-center px-6 py-12 sm:px-8">
    <LanguageSwitcher class="mb-4 self-end" />
    <p class="text-sm font-semibold tracking-widest text-teal-700">RAPHAEL</p>
    <h1 class="mt-5 text-3xl font-semibold leading-snug tracking-tight sm:text-4xl">{{ t('home.title') }}</h1>
    <p class="mt-5 leading-8 text-stone-600">{{ t('home.description') }}</p>
    <NuxtLink to="/chat" class="mt-8 w-fit rounded-xl bg-teal-800 px-6 py-4 font-medium text-white hover:bg-teal-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700">{{ t('common.openChat') }}</NuxtLink>
  </main>
</template>
