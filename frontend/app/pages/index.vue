<script setup lang="ts">
const { data, status, error, refresh } = await useFetch<{ status: 'ok' }>('/api/health')

const checking = computed(() => status.value === 'pending')
const connected = computed(() => !error.value && data.value?.status === 'ok')
</script>

<template>
  <main class="mx-auto flex min-h-dvh w-full max-w-2xl flex-col justify-center px-5 py-12 sm:px-8">
    <p class="mb-3 text-sm font-medium tracking-widest text-teal-700">RAPHAEL</p>
    <h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">為生活留下一點記憶。</h1>
    <p class="mt-4 leading-7 text-stone-600">你的個人紀錄空間，從每一段文字開始。</p>

    <NuxtLink to="/login" class="mt-6 w-fit text-teal-800 underline underline-offset-4">使用 Google 登入</NuxtLink>

    <section aria-labelledby="status-heading" class="mt-10 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
      <h2 id="status-heading" class="text-lg font-semibold">開發環境連線</h2>
      <p role="status" aria-live="polite" class="mt-3 leading-7 text-stone-600">
        <template v-if="checking">正在確認後端連線…</template>
        <template v-else-if="connected">後端已連線。</template>
        <template v-else>暫時無法連線，請確認後端已啟動後再試一次。</template>
      </p>
      <button
        type="button"
        :disabled="checking"
        class="mt-5 min-h-11 rounded-xl bg-teal-800 px-5 py-3 font-medium text-white hover:bg-teal-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700 disabled:cursor-wait disabled:opacity-50"
        @click="refresh()"
      >
        {{ checking ? '確認中…' : '重新確認' }}
      </button>
    </section>
  </main>
</template>
