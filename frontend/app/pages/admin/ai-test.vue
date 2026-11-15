<script setup lang="ts">
import type { AiPreview } from '~/types/ai-preview'
import { recordErrorStatus } from '~/utils/records'
import { createAiPreview } from '~/utils/ai-preview'

definePageMeta({ middleware: 'auth' })
const { t } = useI18n()
const requestFetch = useRequestFetch()
await requestFetch('/api/admin/me').catch((error: { statusCode?: number }) => {
  throw createError({ statusCode: error.statusCode || 503, statusMessage: 'Service Unavailable', message: t('admin.unavailable') })
})
const { user } = useAuth()
const content = ref('')
const busy = ref(false)
const result = ref<AiPreview>()
const errorKey = ref('')
const count = computed(() => Array.from(content.value).length)
const rawJson = computed(() => result.value ? JSON.stringify(result.value, null, 2) : '')
const runner = createAiPreview({
  uid: () => user.value?.uid,
  csrf: async signal => (await $fetch<{ csrf_token: string }>('/api/auth/csrf', { retry: 0, signal })).csrf_token,
  request: (text, uid, token, signal) => $fetch<AiPreview>('/api/admin/ai-preview', {
    method: 'POST', body: { content: text }, retry: 0, timeout: 150000, signal,
    headers: { 'x-csrf-token': token, 'X-Raphael-Account': uid },
  }),
  busy: value => { busy.value = value },
  result: value => { result.value = value },
  failure: error => {
    const status = recordErrorStatus(error)
    errorKey.value = status === 429 ? 'aiTest.busyError' : [401, 403, 409].includes(status || 0) ? 'aiTest.accessError' : 'aiTest.failed'
  },
})
watch(content, () => { result.value = undefined; errorKey.value = '' })
watch(() => user.value?.uid, () => {
  runner.reset(); result.value = undefined; errorKey.value = ''; content.value = ''
})
onBeforeUnmount(() => runner.dispose())
function analyze() {
  if (busy.value) return
  errorKey.value = ''; result.value = undefined
  return runner.run(content.value)
}
</script>

<template>
  <main class="mx-auto min-h-dvh max-w-3xl px-4 py-6 text-chat-ink sm:px-6 sm:py-10">
    <header class="flex items-center justify-between gap-3">
      <NuxtLink to="/chat" class="flex min-h-11 items-center text-sm text-chat-green">{{ t('common.backChat') }}</NuxtLink>
      <LanguageSwitcher />
    </header>
    <h1 class="mt-5 text-2xl font-semibold">{{ t('aiTest.title') }}</h1>
    <p class="mt-3 text-sm leading-relaxed text-chat-muted">{{ t('aiTest.description') }}</p>
    <form class="mt-6 rounded-2xl border border-stone-200 bg-white p-4 sm:p-5" @submit.prevent="analyze">
      <label for="test-content" class="text-sm font-medium">{{ t('aiTest.input') }}</label>
      <textarea id="test-content" v-model="content" rows="5" :disabled="busy" :placeholder="t('aiTest.placeholder')" class="mt-3 block min-h-32 w-full resize-y rounded-xl border border-stone-300 p-3 text-base leading-relaxed focus:border-chat-green focus:outline-none focus:ring-1 focus:ring-chat-green disabled:opacity-60" />
      <p class="mt-2 text-right text-xs text-chat-muted">{{ t('aiTest.characters', { count }) }}</p>
      <p v-if="count > 10000" role="alert" class="mt-2 text-sm text-chat-error">{{ t('aiTest.tooLong') }}</p>
      <button type="submit" :disabled="busy || !content.trim() || count > 10000" class="mt-3 min-h-11 w-full rounded-xl bg-chat-green px-5 py-3 text-sm text-white disabled:opacity-50 sm:w-auto">{{ busy ? t('aiTest.running') : t('aiTest.run') }}</button>
      <p v-if="busy" role="status" class="mt-3 text-sm text-chat-muted">{{ t('aiTest.wait') }}</p>
    </form>
    <p v-if="errorKey" role="alert" class="mt-4 text-sm leading-relaxed text-chat-error">{{ t(errorKey) }}</p>
    <section v-if="result" class="mt-6 space-y-4" :aria-label="t('aiTest.result')" aria-live="polite">
      <div class="rounded-2xl bg-stone-100 p-4 text-sm">
        <h2 class="font-semibold">{{ t('aiTest.result') }}</h2>
        <dl class="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2">
          <dt class="text-chat-muted">{{ t('aiTest.contentType') }}</dt><dd class="break-words">{{ result.analysis.content_type }}</dd>
          <dt class="text-chat-muted">{{ t('aiTest.action') }}</dt><dd>{{ result.analysis.memory_action }}</dd>
          <dt class="text-chat-muted">{{ t('aiTest.context') }}</dt><dd>{{ t(result.analysis.context_required ? 'aiTest.yes' : 'aiTest.no') }}</dd>
        </dl>
        <p class="mt-3 text-chat-muted">{{ t('aiTest.memoryCount', { count: result.analysis.items.length }) }}</p>
      </div>
      <article v-for="(item, index) in result.analysis.items" :key="index" class="rounded-2xl border border-stone-200 bg-white p-4 sm:p-5">
        <h3 class="text-xs font-medium text-chat-muted">{{ t('aiTest.item', { count: index + 1 }) }}</h3>
        <p class="mt-3 whitespace-pre-wrap break-words text-base leading-relaxed">{{ item.text }}</p>
        <div class="mt-4 flex flex-wrap gap-2"><span v-for="topic in item.topics" :key="topic" class="rounded-full bg-teal-50 px-3 py-1 text-xs text-chat-green">{{ topic }}</span></div>
        <dl class="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
          <dt class="text-chat-muted">{{ t('aiTest.kind') }}</dt><dd class="break-words">{{ item.kind.join(' · ') }}</dd>
          <dt class="text-chat-muted">{{ t('aiTest.status') }}</dt><dd>{{ item.status ?? t('aiTest.none') }}</dd>
          <dt class="text-chat-muted">{{ t('aiTest.time') }}</dt><dd class="break-words">{{ item.time_text ?? t('aiTest.none') }}</dd>
          <dt class="text-chat-muted">{{ t('aiTest.basis') }}</dt><dd>{{ item.evidence.type }}</dd>
        </dl>
        <h4 class="mt-4 text-xs font-medium text-chat-muted">{{ t('aiTest.quotes') }}</h4>
        <blockquote v-for="(source, number) in item.evidence.sources" :key="number" class="mt-2 whitespace-pre-wrap break-words border-l-2 border-teal-200 pl-3 text-sm leading-relaxed">{{ source.quote }}</blockquote>
      </article>
      <details class="rounded-2xl border border-stone-200 bg-white p-4">
        <summary class="cursor-pointer py-1 text-sm font-medium">{{ t('aiTest.json') }}</summary>
        <pre class="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-all text-xs leading-relaxed">{{ rawJson }}</pre>
      </details>
      <p class="break-all text-xs text-chat-muted">{{ result.model }} · {{ result.prompt_version }}</p>
    </section>
  </main>
</template>
