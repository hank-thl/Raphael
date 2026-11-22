<script setup lang="ts">
import type { UnifiedEntry, UnifiedPage, UnifiedRequest, ChatMode } from '~/types/unified-chat'
import type { ChatMessage } from '~/types/chat'
import type { MemoryDetail } from '~/types/memories'
import type { SummarySource } from '~/types/topic-summary'
import { createUnifiedSender, mergeEntries } from '~/utils/unified-chat'
import { recordToMessage } from '~/utils/chat'
import { recordErrorStatus } from '~/utils/records'
import ChatBubble from './ChatBubble.vue'
import ChatComposer from './ChatComposer.vue'
import ChatMessageActions from './ChatMessageActions.vue'

const props = defineProps<{ expectedUid: string; mode: ChatMode; captureEnabled: boolean; aiEnabled: boolean }>()
const draft = defineModel<string>('draft', { required: true })
const emit = defineEmits<{ busy: [value: boolean]; count: [value: number]; unauthorized: [error: unknown] }>()
const { t, locale } = useI18n()
const auth = useAuth()
const api = useRecords()
const requestFetch = useRequestFetch()
const headers = () => ({ 'X-Raphael-Account': props.expectedUid })
const cacheKey = `unified-chat:${props.expectedUid}`
const { data: initial, error: initialError } = await useAsyncData(cacheKey,
  () => requestFetch<UnifiedPage>('/api/chat', { query: { limit: 20 }, headers: headers(), retry: 0 }))
const entries = ref<UnifiedEntry[]>(mergeEntries([], initial.value?.items || []))
const cursor = ref(initial.value?.next_cursor || null)
const loading = ref(false), sending = ref(false), deleting = ref(false), expired = ref(false)
const pending = ref<UnifiedRequest | null>(null)
const pendingTime = ref('')
const errorKey = ref(initialError.value ? 'conversation.failed' : '')
const canRetry = ref(false)
const selected = ref<ChatMessage | null>(null)
const deleteError = ref('')
const viewport = ref<HTMLElement>(), bottom = ref<HTMLElement>()
const sources = ref<Record<string, MemoryDetail['versions'][number]>>({})
const sourceBusy = ref<Record<string, boolean>>({})
const busy = computed(() => loading.value || sending.value || deleting.value)
let active = true, generation = 0
let poll: ReturnType<typeof setInterval> | undefined
const sameAccount = () => active && auth.user.value?.uid === props.expectedUid
const userMessage = (entry: UnifiedEntry): ChatMessage => entry.record ? recordToMessage(entry.record) : {
  id: entry.id, role: 'user', content: entry.turn?.question || '', createdAt: entry.created_at, status: 'success',
}
const day = (stamp: string) => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeZone: 'Asia/Taipei' }).format(new Date(stamp))
function fail(error: unknown) {
  const status = recordErrorStatus(error)
  const code = (error as { data?: { detail?: { code?: string } } })?.data?.detail?.code
  if ([401, 403, 409].includes(status || 0)) {
    if (status === 409 && code !== 'account_changed') {
      // Conflicting requests are recoverable only by checking the original ID.
      errorKey.value = 'unified.conflict'
      return
    }
    expired.value = true; entries.value = []; sources.value = {}; selected.value = null
    sender.dispose(); pending.value = null; draft.value = ''; clearNuxtData(cacheKey)
    emit('unauthorized', error)
  }
  errorKey.value = 'conversation.failed'
}
const memory = useMemoryProcessing(
  () => entries.value.flatMap(entry => entry.record ? [entry.record.id] : []),
  () => props.captureEnabled && !expired.value,
  fail,
  () => Object.fromEntries(entries.value.filter(entry => entry.record?.memory_processing)
    .map(entry => [entry.record!.id, entry.record!.memory_processing!])),
)
async function scroll(smooth = false) {
  await nextTick()
  if (active) bottom.value?.scrollIntoView({ block: 'end',
    behavior: smooth && !window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'instant' })
}
function apply(entry: UnifiedEntry) {
  if (!sameAccount()) return
  entries.value = mergeEntries(entries.value, [entry])
  sources.value = {}; errorKey.value = ''; canRetry.value = entry.needs_answer
  if (draft.value === entry.record?.content) draft.value = ''
  clearNuxtData(cacheKey)
  clearNuxtData(`records:${props.expectedUid}`)
  void scroll()
}
const sender = createUnifiedSender({
  id: () => crypto.randomUUID(),
  async post(body) {
    if (!sameAccount()) throw new Error('account_changed')
    const user = await auth.check()
    if (!sameAccount() || user.uid !== props.expectedUid) {
      if (active) await navigateTo('/chat', { external: true })
      throw new Error('account_changed')
    }
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf', { retry: 0 })
    if (!sameAccount()) throw new Error('account_changed')
    return $fetch<UnifiedEntry>('/api/chat', { method: 'POST', body, retry: 0, timeout: 150000,
      headers: { ...headers(), 'x-csrf-token': csrf_token } })
  },
  read: id => $fetch<UnifiedEntry>(`/api/chat/${id}`, { headers: headers(), retry: 0 }),
  result: apply,
  failure(error) {
    if (recordErrorStatus(error) === 404) { canRetry.value = true; errorKey.value = 'conversation.notStored' }
    else { canRetry.value = false; fail(error) }
  },
  busy(value) { if (active) sending.value = value },
  pending(value) { if (active) { pending.value = value; if (value) pendingTime.value = new Date().toISOString() } },
})
async function load(more = false) {
  if (busy.value || !sameAccount() || expired.value) return
  const version = ++generation
  const height = viewport.value?.scrollHeight || 0, top = viewport.value?.scrollTop || 0
  loading.value = true; errorKey.value = ''
  try {
    const page = await $fetch<UnifiedPage>('/api/chat', { query: { limit: 20,
      ...(more && cursor.value ? { cursor: cursor.value } : {}) }, headers: headers(), retry: 0 })
    if (!sameAccount() || version !== generation) return
    entries.value = mergeEntries(more ? entries.value : [], page.items)
    cursor.value = page.next_cursor; sources.value = {}
    if (pending.value) {
      const known = entries.value.find(row => row.request_id === pending.value?.request_id)
      if (known) canRetry.value = known.needs_answer
    }
    await nextTick()
    if (!more) void scroll()
    else if (viewport.value) viewport.value.scrollTop = top + viewport.value.scrollHeight - height
  } catch (error) { if (active && version === generation) fail(error) }
  finally { if (active && version === generation) loading.value = false }
}
async function checkRunning() {
  if (!sameAccount() || busy.value || document.hidden || expired.value) return
  if (pending.value) { if (!canRetry.value) await sender.check(); return }
  const entry = entries.value.find(row => row.turn?.state === 'running')
  if (!entry?.request_id) return
  const version = generation
  try {
    const path = entry.record ? `/api/chat/${entry.request_id}` : `/api/conversations/${entry.request_id}`
    if (entry.record) {
      const updated = await $fetch<UnifiedEntry>(path, { headers: headers(), retry: 0 })
      if (sameAccount() && generation === version) apply(updated)
    } else {
      const turn = await $fetch<NonNullable<UnifiedEntry['turn']>>(path, { headers: headers(), retry: 0 })
      if (sameAccount() && generation === version) apply({ ...entry, turn })
    }
  } catch (error) { if (sameAccount() && generation === version) fail(error) }
}
async function source(reference: SummarySource, entry: UnifiedEntry) {
  const key = `${reference.memory_id}:${reference.revision}`, version = generation
  if (sourceBusy.value[key] || !sameAccount()) return
  if (sources.value[key]) { delete sources.value[key]; return }
  sourceBusy.value[key] = true
  try {
    const detail = await $fetch<MemoryDetail>(`/api/memories/${reference.memory_id}`, { retry: 0 })
    if (!sameAccount() || version !== generation) return
    const found = detail.versions.find(row => row.revision === reference.revision)
    if (!found) throw new Error('source_unavailable')
    sources.value[key] = found
  } catch (error) {
    if (sameAccount() && generation === version) {
      entries.value = entries.value.map(row => row.id === entry.id && row.turn
        ? { ...row, turn: { ...row.turn, state: 'invalid', answer: null } } : row)
      sources.value = {}; fail(error)
    }
  } finally { if (active) sourceBusy.value[key] = false }
}
async function remove() {
  const message = selected.value
  if (!message || busy.value || !sameAccount()) return
  deleting.value = true; deleteError.value = ''
  try {
    const user = await auth.check()
    if (!sameAccount() || user.uid !== props.expectedUid) throw new Error('account_changed')
    try { await api.softDelete(message.id) }
    catch (error) { if (recordErrorStatus(error) !== 404) throw error }
    if (!sameAccount()) return
    entries.value = entries.value.filter(row => row.record?.id !== message.id)
    // Immediately hide dependent answers; refresh revalidates all loaded sources.
    entries.value = entries.value.map(row => row.turn ? { ...row,
      turn: { ...row.turn, state: 'invalid', answer: null } } : row)
    sources.value = {}; selected.value = null
    clearNuxtData(cacheKey); clearNuxtData(`records:${props.expectedUid}`)
  } catch (error) { if (active) { deleteError.value = t('chat.deleteUnknown'); fail(error) } }
  finally { if (active) deleting.value = false }
  if (!selected.value && !expired.value) await load()
}
function resume(entry: UnifiedEntry) {
  if (entry.request_id && entry.record && entry.needs_answer && props.aiEnabled) {
    void sender.resume({ request_id: entry.request_id, content: entry.record.content, mode: 'dialogue' })
  }
}
async function refresh() {
  if (busy.value || !sameAccount() || expired.value) return
  try {
    const user = await auth.check()
    if (!sameAccount() || user.uid !== props.expectedUid) {
      if (active) await navigateTo('/chat', { external: true })
      return
    }
    if (pending.value) await sender.check(); else await load()
  } catch (error) { if (active) fail(error) }
}
function restore(event: PageTransitionEvent) { if (event.persisted) void refresh() }
watch(busy, value => emit('busy', value), { immediate: true })
watch(() => entries.value.length, value => emit('count', value), { immediate: true })
watch(() => JSON.stringify([memory.statuses.value, memory.unavailable.value]), () => {
  const el = viewport.value
  if (el && !loading.value && el.scrollHeight - el.scrollTop - el.clientHeight < 64) void scroll()
})
watch(() => auth.user.value?.uid, () => {
  if (!sameAccount()) {
    expired.value = true
    generation++; sender.dispose(); entries.value = []; sources.value = {}; selected.value = null
    draft.value = ''; pending.value = null; clearNuxtData(cacheKey)
  }
})
onMounted(() => {
  if (initialError.value) fail(initialError.value)
  void scroll(); poll = setInterval(() => { void checkRunning() }, 4000)
  window.addEventListener('focus', refresh)
  window.addEventListener('pageshow', restore)
})
onBeforeUnmount(() => {
  active = false; generation++; sender.dispose(); clearNuxtData(cacheKey)
  if (poll) clearInterval(poll)
  window.removeEventListener('focus', refresh); window.removeEventListener('pageshow', restore); emit('busy', false)
})
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col" :aria-label="t('chat.history')">
    <div class="flex min-h-11 items-center justify-between gap-3 bg-[#f0f2e9] px-5 text-[11px] text-chat-muted md:px-9">
      <span>{{ t(mode === 'record' ? 'unified.recordHint' : 'unified.dialogueHint') }}</span>
      <button type="button" :disabled="busy" class="min-h-11 shrink-0 px-2 underline underline-offset-2" @click="refresh">{{ t('common.refresh') }}</button>
    </div>
    <main ref="viewport" class="min-h-0 flex-1 overflow-y-auto overscroll-y-contain [scrollbar-width:thin] [scrollbar-color:#d3dacd_transparent]" :aria-busy="loading">
      <div class="mx-auto max-w-[780px] px-5 pt-[22px] md:px-9 md:pt-[30px]">
        <div class="pb-[22px] text-center md:pb-[27px]"><span class="mb-2 inline-block text-[34px] text-[#7c9369]" aria-hidden="true">✳</span><h2 class="text-[18px] font-medium md:text-[21px]">{{ t('chat.welcome') }}</h2><p class="mt-2.5 text-[12px] leading-[1.9] text-chat-muted">{{ t('unified.welcome') }}</p></div>
        <div class="flex min-h-11 justify-center"><button v-if="cursor" type="button" :disabled="busy" class="min-h-11 rounded-lg px-4 text-[11px] text-chat-muted" @click="load(true)">{{ t('chat.older') }}</button></div>
        <p v-if="loading" role="status" class="py-2 text-center text-xs text-chat-muted">{{ t('common.loading') }}</p>
        <p v-if="!entries.length && !loading" class="py-8 text-center text-sm text-chat-muted">{{ t('chat.empty') }}</p>
        <ol class="flex list-none flex-col gap-3 pt-2.5 md:gap-[26px]">
          <template v-for="(entry, index) in entries" :key="entry.id">
            <li v-if="index === 0 || day(entries[index - 1]!.created_at) !== day(entry.created_at)" class="my-1.5 text-center text-[10px] text-chat-muted">{{ day(entry.created_at) }}</li>
            <ChatBubble :message="userMessage(entry)" :allow-actions="!!entry.record" :disabled="busy || expired"
              :memory="entry.record ? memory.statuses.value[entry.record.id] : undefined"
              :memory-unavailable="entry.record ? memory.unavailable.value[entry.record.id] : undefined"
              :memory-retrying="entry.record ? memory.retrying.value[entry.record.id] : undefined"
              @actions="selected = $event; deleteError = ''" @refresh-memory="memory.refresh"
              @retry-memory="memory.retry($event.id)" />
            <li v-if="entry.turn || entry.needs_answer" class="flex flex-col items-start">
              <div class="max-w-[94%] rounded-[14px_14px_14px_4px] bg-[#edf1e8] px-4 py-3 text-chat-ink md:max-w-[88%] md:px-5 md:py-4">
                <p v-if="entry.needs_answer" class="text-sm text-chat-muted">{{ t('unified.answerPending') }} <button type="button" :disabled="busy || !!pending" class="min-h-11 px-2 underline" @click="resume(entry)">{{ t('unified.resume') }}</button></p>
                <p v-else-if="entry.turn?.state === 'running'" role="status" class="text-sm text-chat-muted">{{ t('conversation.thinking') }}</p>
                <p v-else-if="entry.turn?.state === 'invalid'" class="text-sm leading-relaxed text-chat-muted">{{ t('conversation.invalid') }}</p>
                <p v-else-if="entry.turn?.state === 'failed'" class="text-sm leading-relaxed text-chat-error">{{ t(entry.turn.error === 'input_limit' ? 'conversation.limit' : 'conversation.answerFailed') }}</p>
                <div v-else-if="entry.turn?.answer" class="space-y-3">
                  <SynthesisPassage v-for="(passage, n) in entry.turn.answer.passages" :key="n" :passage="passage" citation-style="icon" :sources="sources" :source-busy="sourceBusy" :disabled="busy" @source="source($event, entry)" />
                  <p v-if="entry.turn.retrieval_mode" class="text-[10px] text-chat-muted"><span class="inline-block rounded-full border border-chat-green/15 px-2 py-0.5" :title="t(`conversation.retrieval.${entry.turn.retrieval_mode}Description`)">{{ t(`conversation.retrieval.${entry.turn.retrieval_mode}`) }}</span></p>
                </div>
              </div>
            </li>
          </template>
          <ChatBubble v-if="pending && !entries.some(row => row.request_id === pending?.request_id)" :message="{ id: pending.request_id, role: 'user', content: pending.content, createdAt: pendingTime, status: errorKey ? 'error' : 'loading', uncertain: !!errorKey }" :allow-actions="false" />
        </ol>
        <p v-if="sending && pending?.mode === 'dialogue'" role="status" class="mt-3 text-xs text-chat-muted">{{ t('conversation.thinking') }}</p>
        <div ref="bottom" class="h-1" />
      </div>
    </main>
    <footer class="shrink-0 bg-chat-paper px-4 pb-[calc(env(safe-area-inset-bottom)_+_12px)] md:px-9 md:pb-3">
      <div class="mx-auto max-w-[740px] pt-1">
        <p v-if="errorKey" role="alert" class="mb-2 text-sm text-chat-error">{{ t(errorKey) }}</p>
        <NuxtLink v-if="expired" to="/login" class="inline-flex min-h-11 items-center text-sm text-chat-green underline">{{ t('chat.loginNewTab') }}</NuxtLink>
        <div v-if="pending" class="mb-2 flex flex-wrap items-center text-xs text-chat-muted">
          <span>{{ t('conversation.checkFirst') }}</span>
          <button type="button" :disabled="busy" class="min-h-11 px-2 text-chat-green underline" @click="sender.check()">{{ t('conversation.check') }}</button>
          <button v-if="canRetry" type="button" :disabled="busy" class="min-h-11 px-2 text-chat-green underline" @click="canRetry = false; sender.retry()">{{ t('unified.resume') }}</button>
        </div>
        <ChatComposer v-model="draft" :disabled="busy || !!pending || expired" :placeholder="t(mode === 'record' ? 'composer.placeholder' : 'conversation.placeholder')" @send="sender.send(draft, mode)" />
      </div>
    </footer>
    <ChatMessageActions v-if="selected" :message="selected" :busy="deleting" :error="deleteError" @close="selected = null" @remove="remove" />
  </section>
</template>
