<script setup lang="ts">
import type { MemoryDetail, MemoryPage, MemorySummary, MemoryTopicPage } from '~/types/memories'
import { recordErrorStatus } from '~/utils/records'
const { t, locale } = useI18n()
definePageMeta({ middleware: 'auth' })
useHead(() => ({ title: `${t('memoryLibrary.title')}｜Raphael` }))
const auth = useAuth()
const uid = auth.user.value?.uid
const route = useRoute()
const selectedTopic = computed(() => typeof route.query.topic === 'string' && route.query.topic.length > 0 ? route.query.topic : undefined)
const unclassified = computed(() => !selectedTopic.value && route.query.unclassified === '1')
const browsing = computed(() => selectedTopic.value !== undefined || unclassified.value)
const requestFetch = useRequestFetch()
const key = `memories:${uid}`
const fetchPage = (cursor?: string) => requestFetch<MemoryPage>('/api/memories', {
  query: { limit: 10, ...(cursor ? { cursor } : {}), ...(selectedTopic.value ? { topic: selectedTopic.value } : {}), ...(unclassified.value ? { unclassified: 'true' } : {}) }, headers: uid ? { 'X-Raphael-Account': uid } : undefined, retry: 0,
})
const topicKey = `${key}:topics`
const fetchTopics = () => requestFetch<MemoryTopicPage>('/api/memories/topics', { headers: uid ? { 'X-Raphael-Account': uid } : undefined, retry: 0 })
const { data: catalog, error: catalogError } = await useAsyncData(topicKey, fetchTopics)
const { data: initial, error: initialError } = await useAsyncData(key, () => browsing.value ? fetchPage() : Promise.resolve<MemoryPage>({ items: [], next_cursor: null }))
const items = ref<MemorySummary[]>(initial.value?.items || [])
const cursor = ref<string | null>(initial.value?.next_cursor || null)
const details = ref<Record<string, MemoryDetail>>({})
const loadingDetails = ref<Record<string, boolean>>({})
const detailErrors = ref<Record<string, boolean>>({})
const busy = ref(false)
const firstError = browsing.value ? initialError.value : catalogError.value
const errorKey = ref(firstError ? (recordErrorStatus(firstError) === 403 ? 'memoryLibrary.forbidden' : 'memoryLibrary.failed') : '')
const removedNotice = ref(false)
let active = true
let generation = 0
const dateLabel = (value: string) => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Taipei' }).format(new Date(value))
function clear() { items.value = []; details.value = {}; cursor.value = null; busy.value = false; clearNuxtData(key); generation++ }
function handleError(error: unknown) {
  if ([401, 403, 409].includes(recordErrorStatus(error) || 0)) { clear(); catalog.value = undefined; clearNuxtData(topicKey) }
  errorKey.value = recordErrorStatus(error) === 403 ? 'memoryLibrary.forbidden' : 'memoryLibrary.failed'
}
async function sameAccount() {
  const user = await auth.check()
  if (!active) return false
  if (user.uid !== uid) { clear(); catalog.value = undefined; clearNuxtData(topicKey); await navigateTo('/chat', { external: true }); return false }
  return true
}
async function load(more = false) {
  if (busy.value && more) return
  busy.value = true; errorKey.value = ''; removedNotice.value = false
  const requestGeneration = ++generation
  try {
    if (!await sameAccount()) return
    const page = await fetchPage(more ? cursor.value || undefined : undefined)
    if (!active || generation !== requestGeneration) return
    const rows = more ? [...items.value, ...page.items] : page.items
    items.value = [...new Map(rows.map(row => [row.id, row])).values()]
    cursor.value = page.next_cursor
    if (!more) details.value = {}
    clearNuxtData(key)
  } catch (error) { if (active && generation === requestGeneration) handleError(error) }
  finally { if (active && generation === requestGeneration) busy.value = false }
}
async function loadTopics() {
  busy.value = true; errorKey.value = ''
  const version = ++generation
  try {
    if (!await sameAccount()) return
    const result = await fetchTopics()
    if (active && generation === version) catalog.value = result
  } catch (error) { if (active && generation === version) handleError(error) }
  finally { if (active && generation === version) busy.value = false }
}
watch([selectedTopic, unclassified], () => {
  clear(); errorKey.value = ''; removedNotice.value = false
  if (browsing.value) void load()
  else void loadTopics()
})
async function showDetails(id: string) {
  if (busy.value || loadingDetails.value[id]) return
  if (details.value[id]) { delete details.value[id]; return }
  loadingDetails.value[id] = true; detailErrors.value[id] = false
  const version = generation
  try {
    if (!await sameAccount()) return
    const detail = await $fetch<MemoryDetail>(`/api/memories/${encodeURIComponent(id)}`, { retry: 0 })
    if (active && version === generation && items.value.some(row => row.id === id)) details.value[id] = detail
  } catch (error) {
    if (!active || version !== generation) return
    if (recordErrorStatus(error) === 404) {
      items.value = items.value.filter(row => row.id !== id); delete details.value[id]; removedNotice.value = true
    } else if ([401, 403].includes(recordErrorStatus(error) || 0)) handleError(error)
    else detailErrors.value[id] = true
  } finally { if (active) loadingDetails.value[id] = false }
}
async function onFocus() {
  try { await sameAccount() } catch (error) { if (active) handleError(error) }
}
onMounted(() => window.addEventListener('focus', onFocus))
onBeforeUnmount(() => { active = false; clearNuxtData(key); clearNuxtData(topicKey); window.removeEventListener('focus', onFocus) })
</script>

<template>
  <main class="min-h-dvh bg-chat-paper px-4 pt-[max(20px,env(safe-area-inset-top))] pb-[max(28px,env(safe-area-inset-bottom))] text-chat-ink md:px-8">
    <div class="mx-auto max-w-3xl">
      <nav class="mb-7 flex items-center justify-between gap-4 text-sm text-chat-green">
        <NuxtLink to="/chat" class="inline-flex min-h-11 items-center">← {{ t('common.backChat') }}</NuxtLink><LanguageSwitcher />
      </nav>
      <header class="mb-6 flex items-start justify-between gap-4">
        <div><h1 class="text-xl font-semibold md:text-2xl">{{ selectedTopic || (unclassified ? t('memoryLibrary.unclassified') : t('memoryLibrary.title')) }}</h1><p class="mt-3 text-sm leading-relaxed text-chat-muted">{{ t('memoryLibrary.description') }}</p></div>
        <button type="button" class="min-h-11 shrink-0 rounded-xl bg-[#e8ecdf] px-4 text-sm text-chat-green disabled:opacity-50" :disabled="busy" @click="browsing ? load() : loadTopics()">{{ busy ? t('common.loading') : t('common.refresh') }}</button>
      </header>
      <p v-if="errorKey" role="alert" class="mb-5 text-sm text-chat-error">{{ t(errorKey) }}</p>
      <NuxtLink v-if="browsing" to="/memories" class="mb-5 inline-flex min-h-11 items-center text-sm text-chat-green">← {{ t('memoryLibrary.backTopics') }}</NuxtLink>
      <section v-if="!browsing && catalog && !errorKey" :aria-label="t('memoryLibrary.topics')">
        <p class="mb-4 text-sm text-chat-muted">{{ t('memoryLibrary.chooseTopic') }}</p>
        <div class="grid gap-3 sm:grid-cols-2">
          <NuxtLink v-for="topic in catalog.topics" :key="topic.name" :to="{ path: '/memories', query: { topic: topic.name } }" class="flex min-h-24 items-center justify-between gap-4 rounded-2xl border border-chat-line bg-white/60 p-5 hover:bg-[#edf1e9]">
            <span class="min-w-0 text-base font-medium wrap-anywhere">{{ topic.name }}</span><span class="shrink-0 text-xs text-chat-muted">{{ t('memoryLibrary.count', { count: topic.count }) }}</span>
          </NuxtLink>
          <NuxtLink v-if="catalog.unclassified_count" :to="{ path: '/memories', query: { unclassified: '1' } }" class="flex min-h-24 items-center justify-between gap-4 rounded-2xl border border-dashed border-chat-line p-5 hover:bg-[#edf1e9]">
            <span class="text-base">{{ t('memoryLibrary.unclassified') }}</span><span class="text-xs text-chat-muted">{{ t('memoryLibrary.count', { count: catalog.unclassified_count }) }}</span>
          </NuxtLink>
        </div>
        <p v-if="!catalog.total_count" class="py-10 text-center text-sm text-chat-muted">{{ t('memoryLibrary.empty') }}</p>
      </section>
      <p v-if="removedNotice" role="status" class="mb-5 text-sm text-chat-muted">{{ t('memoryLibrary.unavailable') }}</p>
      <p v-if="browsing && !items.length && !errorKey" class="py-10 text-center text-sm text-chat-muted">{{ cursor ? t('memoryLibrary.filtered') : t('memoryLibrary.empty') }}</p>
      <ol v-if="browsing" class="space-y-4" :aria-busy="busy">
        <li v-for="item in items" :key="item.id" class="rounded-2xl border border-chat-line bg-white/60 p-4 md:p-6">
          <div class="mb-3 flex flex-wrap gap-2 text-xs text-chat-muted"><span class="rounded-md bg-[#e8ecdf] px-2 py-1">{{ t('memoryLibrary.candidate') }}</span><span class="px-1 py-1">{{ t(`memoryLibrary.evidence.${item.evidence_type}`) }}</span><span v-if="item.status" class="px-1 py-1">{{ t(`memoryLibrary.states.${item.status}`) }}</span></div>
          <p class="whitespace-pre-wrap text-sm leading-relaxed wrap-anywhere md:text-base">{{ item.text }}</p>
          <div v-if="item.topics.length" class="mt-3 flex flex-wrap items-center gap-2 text-xs text-chat-muted"><span>{{ t('memoryLibrary.topics') }}</span><span v-for="topic in item.topics" :key="topic" class="rounded-md bg-[#edf1e9] px-2 py-1 wrap-anywhere">{{ topic }}</span></div>
          <div class="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-chat-muted"><time :datetime="item.updated_at">{{ t('memoryLibrary.updated', { time: dateLabel(item.updated_at) }) }}</time><button type="button" class="min-h-11 rounded-lg px-2 text-chat-green underline underline-offset-4 disabled:opacity-50" :disabled="busy || loadingDetails[item.id]" :aria-expanded="!!details[item.id]" :aria-controls="`versions-${item.id}`" @click="showDetails(item.id)">{{ loadingDetails[item.id] ? t('common.loading') : details[item.id] ? t('memoryLibrary.hide') : t('memoryLibrary.show', { count: item.revision }) }}</button></div>
          <p v-if="detailErrors[item.id]" role="alert" class="text-xs text-chat-error">{{ t('memoryLibrary.detailFailed') }}</p>
          <ol v-if="details[item.id]" :id="`versions-${item.id}`" class="mt-4 space-y-5 border-t border-chat-line pt-5">
            <li v-for="version in details[item.id]!.versions" :key="version.revision" class="space-y-2 text-sm">
              <h2 class="font-medium">{{ t('memoryLibrary.version', { count: version.revision }) }}</h2>
              <p class="whitespace-pre-wrap leading-relaxed wrap-anywhere">{{ version.text }}</p>
              <div v-if="version.topics.length" class="flex flex-wrap gap-2 text-xs text-chat-muted"><span>{{ t('memoryLibrary.topics') }}</span><span v-for="topic in version.topics" :key="topic">{{ topic }}</span></div>
              <p class="text-xs text-chat-muted">{{ t('memoryLibrary.quotes') }}</p>
              <blockquote v-for="(source, index) in version.evidence.sources" :key="`${source.record_id}-${index}`" class="border-l-2 border-[#c7d4bb] pl-3 whitespace-pre-wrap text-xs leading-relaxed text-chat-muted wrap-anywhere">{{ source.quote }}</blockquote>
            </li>
          </ol>
        </li>
      </ol>
      <div v-if="browsing && cursor" class="mt-6 text-center"><button type="button" class="min-h-11 rounded-xl bg-[#e8ecdf] px-5 text-sm text-chat-green disabled:opacity-50" :disabled="busy" @click="load(true)">{{ busy ? t('common.loading') : t('memoryLibrary.more') }}</button></div>
    </div>
  </main>
</template>
