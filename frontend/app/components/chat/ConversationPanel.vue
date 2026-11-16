<script setup lang="ts">
import type { ConversationPage, ConversationTurn } from '~/types/conversation'
import type { MemoryDetail } from '~/types/memories'
import type { SummarySource } from '~/types/topic-summary'
import { createConversationSender, type ConversationRequest } from '~/utils/conversation'
import { recordErrorStatus } from '~/utils/records'
import ChatBubble from './ChatBubble.vue'
import ChatComposer from './ChatComposer.vue'
const props = defineProps<{ expectedUid: string }>()
const emit = defineEmits<{ busy: [value: boolean]; unauthorized: [error: unknown] }>()
const { t, locale } = useI18n()
const { user } = useAuth()
const turns = ref<ConversationTurn[]>([])
const draft = ref('')
const cursor = ref<string | null>(null)
const loading = ref(false)
const sending = ref(false)
const request = ref<ConversationRequest | null>(null)
const errorKey = ref('')
const mayRetry = ref(false)
const bottom = ref<HTMLElement>()
const viewport = ref<HTMLElement>()
const pendingTime = ref('')
const sources = ref<Record<string, MemoryDetail['versions'][number]>>({})
const sourceBusy = ref<Record<string, boolean>>({})
const busy = computed(() => loading.value || sending.value)
watch(busy, value => emit('busy', value))
let active = true
let generation = 0
let poll: ReturnType<typeof setInterval> | undefined
const headers = () => ({ 'X-Raphael-Account': props.expectedUid })
const sameAccount = () => active && user.value?.uid === props.expectedUid
const date = (stamp: string) => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Taipei' }).format(new Date(stamp))
function fail(error: unknown) {
  const status = recordErrorStatus(error)
  const code = (error as { data?: { detail?: { code?: string } } })?.data?.detail?.code
  if ([401, 403].includes(status || 0) || code === 'account_changed') {
    turns.value = []; sources.value = {}; draft.value = ''; request.value = null
    sender.dispose(); emit('unauthorized', error)
  }
  errorKey.value = 'conversation.failed'
}
function apply(turn: ConversationTurn) {
  if (!sameAccount()) return
  turns.value = [...turns.value.filter(row => row.id !== turn.id), turn].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
  sources.value = {}; errorKey.value = ''; mayRetry.value = false
  if (turn.state !== 'running' && draft.value === turn.question) draft.value = ''
  void scroll()
}
const sender = createConversationSender({
  id: () => crypto.randomUUID(),
  async post(body) {
    if (!sameAccount()) throw new Error('Account changed')
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf', { retry: 0 })
    if (!sameAccount()) throw new Error('Account changed')
    return $fetch<ConversationTurn>('/api/conversations', { method: 'POST', body, timeout: 150000, retry: 0,
      headers: { ...headers(), 'x-csrf-token': csrf_token } })
  },
  read: id => $fetch<ConversationTurn>(`/api/conversations/${id}`, { headers: headers(), retry: 0 }),
  result: apply,
  failure(error) {
    if (recordErrorStatus(error) === 404) { mayRetry.value = true; errorKey.value = 'conversation.notStored' }
    else { mayRetry.value = false; fail(error) }
  },
  busy(value) { if (active) sending.value = value },
  pending(value) { if (active) { request.value = value; if (value) pendingTime.value = new Date().toISOString() } },
})
async function scroll() { await nextTick(); if (active) bottom.value?.scrollIntoView({ block: 'end', behavior: 'instant' }) }
async function load(more = false) {
  if (busy.value || !sameAccount()) return
  const version = ++generation
  const oldHeight = viewport.value?.scrollHeight || 0, oldTop = viewport.value?.scrollTop || 0
  loading.value = true; errorKey.value = ''
  try {
    const page = await $fetch<ConversationPage>('/api/conversations', { query: { limit: 10, ...(more && cursor.value ? { cursor: cursor.value } : {}) }, headers: headers(), retry: 0 })
    if (!sameAccount() || generation !== version) return
    const rows = more ? [...turns.value, ...page.items] : page.items
    turns.value = [...new Map(rows.map(row => [row.id, row])).values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
    cursor.value = page.next_cursor; sources.value = {}
    if (!more) void scroll()
    else { await nextTick(); if (active && viewport.value) viewport.value.scrollTop = oldTop + viewport.value.scrollHeight - oldHeight }
  } catch (error) { if (active && version === generation) fail(error) }
  finally { if (active && version === generation) loading.value = false }
}
function check() { return sender.check() }
async function readRunning() {
  if (!sameAccount() || busy.value || document.hidden) return
  if (request.value) { if (!mayRetry.value) await check(); return }
  const row = turns.value.find(item => item.state === 'running')
  if (!row) return
  const version = generation
  try {
    const response = await $fetch<ConversationTurn>(`/api/conversations/${row.request_id}`, { headers: headers(), retry: 0 })
    if (sameAccount() && generation === version) apply(response)
  } catch (error) { if (sameAccount() && generation === version) fail(error) }
}
async function source(reference: SummarySource, turn: ConversationTurn) {
  const key = `${reference.memory_id}:${reference.revision}`, version = generation
  if (sourceBusy.value[key] || !sameAccount()) return
  if (sources.value[key]) { delete sources.value[key]; return }
  sourceBusy.value[key] = true
  try {
    const detail = await $fetch<MemoryDetail>(`/api/memories/${reference.memory_id}`, { retry: 0 })
    if (!sameAccount() || version !== generation) return
    const found = detail.versions.find(row => row.revision === reference.revision)
    if (!found) throw new Error('Missing source')
    sources.value[key] = found
  } catch (error) {
    if (sameAccount() && generation === version) {
      turns.value = turns.value.map(row => row.id === turn.id ? { ...row, answer: null, state: 'invalid' } : row)
      sources.value = {}; fail(error)
    }
  } finally { if (active) sourceBusy.value[key] = false }
}
function refresh() { if (request.value) void check(); else void load() }
watch(() => user.value?.uid, () => {
  if (user.value?.uid !== props.expectedUid) {
    sender.dispose(); turns.value = []; sources.value = {}; draft.value = ''; request.value = null
    generation++
  }
})
onMounted(() => { void load(); poll = setInterval(() => { void readRunning() }, 4000); window.addEventListener('focus', refresh) })
onBeforeUnmount(() => { active = false; generation++; sender.dispose(); if (poll) clearInterval(poll); window.removeEventListener('focus', refresh); emit('busy', false) })
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col" :aria-label="t('conversation.title')">
    <div class="flex items-center justify-between gap-3 bg-[#f0f2e9] px-5 py-2 text-xs leading-relaxed text-chat-muted">
      <p>{{ t('conversation.description') }}</p>
      <button type="button" :disabled="busy" class="min-h-11 shrink-0 px-2 text-chat-green underline" @click="refresh">{{ t('common.refresh') }}</button>
    </div>
    <div ref="viewport" class="min-h-0 flex-1 overflow-y-auto overscroll-y-contain px-4 py-5 md:px-9" :aria-busy="busy">
      <div class="mx-auto max-w-[740px]">
        <button v-if="cursor" type="button" :disabled="busy" class="mx-auto block min-h-11 px-4 text-xs text-chat-green" @click="load(true)">{{ t('chat.older') }}</button>
        <p v-if="loading" role="status" class="py-3 text-center text-sm text-chat-muted">{{ t('common.loading') }}</p>
        <p v-if="!loading && !turns.length" class="py-10 text-center text-sm leading-relaxed text-chat-muted">{{ t('conversation.empty') }}</p>
        <ol class="space-y-5">
          <li v-for="turn in turns" :key="turn.id" class="space-y-3">
            <p class="text-center text-[10px] text-chat-muted">{{ date(turn.created_at) }}</p>
            <ol class="list-none"><ChatBubble :message="{ id: turn.id, role: 'user', content: turn.question, createdAt: turn.created_at, status: 'success' }" :allow-actions="false" /></ol>
            <div class="mr-auto max-w-[94%] rounded-[14px_14px_14px_4px] bg-[#edf1e8] px-4 py-3 text-chat-ink md:max-w-[88%] md:px-5 md:py-4">
              <p v-if="turn.state === 'running'" role="status" class="text-sm text-chat-muted">{{ t('conversation.thinking') }}</p>
              <p v-else-if="turn.state === 'invalid'" class="text-sm leading-relaxed text-chat-muted">{{ t('conversation.invalid') }}</p>
              <p v-else-if="turn.state === 'failed'" class="text-sm leading-relaxed text-chat-error">{{ t(turn.error === 'input_limit' ? 'conversation.limit' : 'conversation.answerFailed') }}</p>
              <div v-else-if="turn.answer" class="space-y-3">
                <SynthesisPassage v-for="(passage, index) in turn.answer.passages" :key="index" :passage="passage" citation-style="icon" :sources="sources" :source-busy="sourceBusy" :disabled="busy" @source="source($event, turn)" />
              </div>
            </div>
          </li>
        </ol>
        <ol v-if="request && !turns.some(row => row.request_id === request?.request_id)" class="mt-5 list-none">
          <ChatBubble :message="{ id: request.request_id, role: 'user', content: request.content, createdAt: pendingTime, status: 'loading' }" :allow-actions="false" />
        </ol>
        <p v-if="sending && !turns.some(row => row.request_id === request?.request_id)" role="status" class="mt-5 text-sm text-chat-muted">{{ t('conversation.thinking') }}</p>
        <div ref="bottom" class="h-1" />
      </div>
    </div>
    <footer class="shrink-0 px-4 pb-[calc(env(safe-area-inset-bottom)_+_12px)] md:px-9 md:pb-3">
      <div class="mx-auto max-w-[740px]">
        <p v-if="errorKey" role="alert" class="mb-2 text-sm text-chat-error">{{ t(errorKey) }}</p>
        <div v-if="request" class="mb-2 flex flex-wrap items-center gap-2 text-xs text-chat-muted">
          <span>{{ t('conversation.checkFirst') }}</span>
          <button type="button" :disabled="busy" class="min-h-11 px-2 text-chat-green underline" @click="check">{{ t('conversation.check') }}</button>
          <button v-if="mayRetry" type="button" :disabled="busy" class="min-h-11 px-2 text-chat-green underline" @click="mayRetry = false; errorKey = ''; sender.retry()">{{ t('conversation.retry') }}</button>
        </div>
        <ChatComposer v-model="draft" :disabled="busy || !!request" :placeholder="t('conversation.placeholder')" :label="t('conversation.title')" @send="sender.send(draft)" />
      </div>
    </footer>
  </section>
</template>
