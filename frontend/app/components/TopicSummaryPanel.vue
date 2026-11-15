<script setup lang="ts">
import type { TopicSummary, SummarySource } from '~/types/topic-summary'
import type { MemoryDetail } from '~/types/memories'
import { recordErrorStatus } from '~/utils/records'
const props = defineProps<{ topic: string; expectedUid?: string; refreshKey?: number }>()
const emit = defineEmits<{ unauthorized: [error: unknown] }>()
const { t, locale } = useI18n()
const data = ref<TopicSummary>()
const loading = ref(true)
const generating = ref(false)
const errorKey = ref('')
const needsCheck = ref(false)
const sources = ref<Record<string, MemoryDetail['versions'][number]>>({})
const sourceBusy = ref<Record<string, boolean>>({})
let active = true
let epoch = 0
const keyOf = (source: SummarySource) => `${source.memory_id}:${source.revision}`
const headers = (): Record<string, string> => props.expectedUid ? { 'X-Raphael-Account': props.expectedUid } : {}
const timeLabel = computed(() => data.value?.generated_at ? new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Taipei' }).format(new Date(data.value.generated_at)) : '')
function failure(error: unknown) {
  const code = recordErrorStatus(error)
  if ([401, 403, 409].includes(code || 0)) {
    const reason = (error as { data?: { detail?: { code?: string } } })?.data?.detail?.code
    if (code === 401 || code === 403 || reason === 'account_changed') emit('unauthorized', error)
  }
  return code === 413 ? 'topicSummary.limit' : code === 409 ? 'topicSummary.changed' : 'topicSummary.failed'
}
async function readSaved() {
  if (generating.value) return
  const version = ++epoch
  loading.value = true; errorKey.value = ''; sources.value = {}; data.value = undefined
  const topic = props.topic
  try {
    const result = await $fetch<TopicSummary>('/api/memories/summary', { query: { topic }, headers: headers(), retry: 0 })
    if (active && epoch === version) { data.value = result; needsCheck.value = false }
  } catch (error) {
    if (active && epoch === version) { data.value = undefined; errorKey.value = failure(error); needsCheck.value = true }
  } finally { if (active && epoch === version) loading.value = false }
}
async function generate() {
  if (generating.value || loading.value || needsCheck.value) return
  const version = ++epoch, topic = props.topic
  generating.value = true; errorKey.value = ''; data.value = undefined; sources.value = {}
  try {
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf', { retry: 0 })
    if (!active || epoch !== version) return
    const result = await $fetch<TopicSummary>('/api/memories/summary', { method: 'POST',
      headers: { ...headers(), 'x-csrf-token': csrf_token }, body: { topic }, retry: 0 })
    if (active && epoch === version) { data.value = result; sources.value = {} }
  } catch (error) {
    if (active && epoch === version) { data.value = undefined; errorKey.value = failure(error); needsCheck.value = true }
  } finally { if (active && epoch === version) generating.value = false }
}
async function showSource(source: SummarySource) {
  const key = keyOf(source), version = epoch
  if (sourceBusy.value[key]) return
  if (sources.value[key]) { delete sources.value[key]; return }
  sourceBusy.value[key] = true
  try {
    const detail = await $fetch<MemoryDetail>(`/api/memories/${source.memory_id}`, { retry: 0 })
    if (!active || version !== epoch) return
    const found = detail.versions.find(row => row.revision === source.revision)
    if (!found) throw new Error('Source version missing')
    sources.value[key] = found
  } catch (error) {
    if (active && version === epoch) {
      data.value = undefined; needsCheck.value = true; errorKey.value = failure(error)
    }
  } finally { if (active) sourceBusy.value[key] = false }
}
function onFocus() { if (!generating.value && !loading.value) void readSaved() }
watch(() => props.refreshKey, onFocus)
watch(() => props.topic, () => {
  epoch++; data.value = undefined; generating.value = false; needsCheck.value = false; sources.value = {}
  void readSaved()
})
onMounted(() => { void readSaved(); window.addEventListener('focus', onFocus) })
onBeforeUnmount(() => { active = false; epoch++; window.removeEventListener('focus', onFocus) })
</script>

<template>
  <section class="mb-6 rounded-2xl border border-[#d6dfcd] bg-[#f7f9f3] p-4 md:p-6" :aria-label="t('topicSummary.title')">
    <h2 class="text-sm font-medium text-chat-green">{{ t('topicSummary.title') }}</h2>
    <p class="mt-2 text-[11px] leading-relaxed text-chat-muted">{{ t('topicSummary.description') }}</p>
    <p v-if="loading" role="status" class="mt-3 text-sm text-chat-muted">{{ t('common.loading') }}</p>
    <p v-if="errorKey" role="alert" class="mt-3 text-sm text-chat-error">{{ t(errorKey) }}</p>
    <p v-if="data?.state === 'stale'" role="status" class="mt-3 text-sm text-chat-error">{{ t('topicSummary.stale') }}</p>
    <p v-if="data?.state === 'invalid'" class="mt-3 text-sm text-chat-muted">{{ t('topicSummary.invalid') }}</p>
    <p v-if="data?.state === 'missing'" class="mt-3 text-sm text-chat-muted">{{ t('topicSummary.missing') }}</p>
    <p v-if="data && (data.format === 'legacy' || (!data.format && data.items.length))" class="mt-4 text-sm leading-relaxed text-chat-muted">{{ t('topicSummary.legacy') }}</p>
    <article v-if="data?.format === 'article' && data.content" class="mt-5">
      <h3 class="font-display text-xl font-semibold leading-relaxed md:text-2xl">{{ data.content.title }}</h3>
      <div class="mt-4 space-y-3">
        <SynthesisPassage v-for="(passage, index) in data.content.summary" :key="`summary-${index}`" :passage="passage" :sources="sources" :source-busy="sourceBusy" :disabled="loading || generating" @source="showSource" />
      </div>
      <section v-for="(section, index) in data.content.sections" :key="index" class="mt-7">
        <h4 class="mb-3 text-base font-semibold leading-relaxed">{{ section.heading }}</h4>
        <ul :class="section.layout === 'bullets' ? 'list-disc space-y-2 pl-5' : section.layout === 'timeline' ? 'space-y-5 border-l border-[#c7d4bb] pl-4' : 'list-none space-y-3'">
          <li v-for="(passage, number) in section.items" :key="number">
            <p v-if="section.layout === 'timeline' && passage.time_label" class="mb-1 text-xs font-medium text-chat-green">{{ passage.time_label }}</p>
            <SynthesisPassage :passage="passage" :sources="sources" :source-busy="sourceBusy" :disabled="loading || generating" @source="showSource" />
          </li>
        </ul>
      </section>
    </article>
    <p v-if="data?.generated_at" class="mt-6 text-[10px] leading-relaxed text-chat-muted">{{ t('topicSummary.generated', { time: timeLabel, count: data.memory_count }) }}</p>
    <div class="mt-4 flex flex-wrap gap-3">
      <button type="button" class="min-h-11 rounded-xl bg-chat-green px-4 text-sm text-white disabled:opacity-50" :disabled="loading || generating || needsCheck" @click="generate">{{ generating ? t('topicSummary.generating') : data && data.state !== 'missing' ? t('topicSummary.update') : t('topicSummary.generate') }}</button>
      <button type="button" class="min-h-11 rounded-xl px-3 text-sm text-chat-green disabled:opacity-50" :disabled="loading || generating" @click="readSaved">{{ t('topicSummary.check') }}</button>
    </div>
  </section>
</template>
