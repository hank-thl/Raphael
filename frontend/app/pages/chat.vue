<script setup lang="ts">
import ChatMessageActions from '~/components/chat/ChatMessageActions.vue'
import ChatBubble from '~/components/chat/ChatBubble.vue'
import ChatComposer from '~/components/chat/ChatComposer.vue'
import type { ChatMessage } from '~/types/chat'
import { mergeChatMessages } from '~/utils/chat'
import { recordValidation, recordErrorStatus, recordErrorMessage, recordSaveErrorMessage } from '~/utils/records'
const { t, locale } = useI18n()

definePageMeta({ middleware: 'auth' })
useHead(() => ({ title: `${t('chat.title')}｜Raphael` }))
const translate = (key: string, values?: Record<string, string | number>) => t(key, values || {})
const auth = useAuth()
const accountOpen = ref(false)
const loggingOut = ref(false)
const { data: admin } = await useFetch<{ is_admin: boolean }>('/api/admin/me')
const pageUid = auth.user.value?.uid
const transport = useChatRecords()
const messages = ref<ChatMessage[]>([])
const selectedMessage = ref<ChatMessage | null>(null)
const deleting = ref(false)
const { text: deleteError, set: setDeleteError } = useTranslatedMessage()
const pending = ref<ChatMessage | null>(null)
const draft = ref('')
const nextCursor = ref<string | null>(null)
const loadingHistory = ref(false)
const sending = ref(false)
const sessionExpired = ref(false)
const uncertain = ref(false)
const { text: historyError, set: setHistoryError } = useTranslatedMessage()
const { text: actionError, set: setActionError, clear: clearActionError } = useTranslatedMessage()
const { text: announcement, set: setAnnouncement } = useTranslatedMessage()
const viewportRoot = ref<HTMLElement>()
const scroller = ref<HTMLElement>()
const bottom = ref<HTMLElement>()
const busy = computed(() => sending.value || loadingHistory.value || loggingOut.value || deleting.value)
const dayLabel = (value: string) => new Intl.DateTimeFormat(locale.value, { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'Asia/Taipei' }).format(new Date(value))
const cacheKey = `chat-records:${pageUid}`
const { data: initial, error: initialError } = await useAsyncData(cacheKey, () => transport.history())
messages.value = initial.value?.messages || []
nextCursor.value = initial.value?.nextCursor || null
setHistoryError(() => initialError.value ? recordErrorMessage(initialError.value, translate) : '')
sessionExpired.value = [401, 403].includes(recordErrorStatus(initialError.value) || 0)
let active = true

function handleError(error: unknown) {
  if ([401, 403].includes(recordErrorStatus(error) || 0)) {
    sessionExpired.value = true
    selectedMessage.value = null
    messages.value = []
    pending.value = null
    nextCursor.value = null
    clearNuxtData(cacheKey)
  }
}
async function sameAccount() {
  try {
    const user = await auth.check()
    if (!active) return false
    if (user.uid !== pageUid) {
      selectedMessage.value = null
      messages.value = []
      pending.value = null
      draft.value = ''
      clearNuxtData()
      await navigateTo('/chat', { external: true })
      return false
    }
    sessionExpired.value = false
    return true
  } catch (error) { handleError(error); throw error }
}
async function scrollToLatest(smooth = false) {
  await nextTick()
  if (!active) return
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  bottom.value?.scrollIntoView({ block: 'end', behavior: smooth && !reducedMotion ? 'smooth' : 'instant' })
}
async function send() {
  if (busy.value || uncertain.value || recordValidation(draft.value)) return
  sending.value = true
  setActionError(() => '')
  let submitted = false
  try {
    if (!await sameAccount()) return
    const message: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: draft.value, createdAt: new Date().toISOString(), status: 'loading' }
    pending.value = message
    void scrollToLatest(true)
    submitted = true
    const saved = await transport.send(message)
    if (!active) return
    messages.value = mergeChatMessages(messages.value, [saved])
    pending.value = null
    draft.value = ''
    // Invalidate the record-list cache too; both views show the same collection.
    clearNuxtData(`records:${pageUid}`)
    clearNuxtData(cacheKey)
    setAnnouncement(() => t('chat.savedAnnouncement'))
    await scrollToLatest(true)
  } catch (error) {
    if (!active) return
    handleError(error)
    const status = recordErrorStatus(error)
    uncertain.value = submitted && (status === undefined || status >= 500)
    if (pending.value) {
      pending.value.status = 'error'
      pending.value.uncertain = uncertain.value
    }
    setActionError(() => submitted ? recordSaveErrorMessage(error, translate) : recordErrorMessage(error, translate))
    setAnnouncement(() => actionError.value)
  } finally { sending.value = false }
}
async function loadHistory(latest = false) {
  if (busy.value) return
  const before = scroller.value?.scrollHeight || 0
  const offset = scroller.value?.scrollTop || 0
  loadingHistory.value = true
  setHistoryError(() => '')
  try {
    const page = await transport.history(latest ? undefined : nextCursor.value || undefined)
    if (!active) return
    // Refresh replaces loaded history so soft-deleted records disappear too.
    messages.value = latest ? page.messages : mergeChatMessages(messages.value, page.messages)
    nextCursor.value = page.nextCursor
    clearNuxtData(cacheKey)
    await nextTick()
    if (latest) await scrollToLatest()
    else if (scroller.value) scroller.value.scrollTop = offset + scroller.value.scrollHeight - before
  } catch (error) {
    if (!active) return
    if (recordErrorStatus(error) === 409) {
      try { await sameAccount() } catch (failure) { handleError(failure) }
      if (!active) return
      setHistoryError(() => t('chat.accountChanged'))
      return
    }
    handleError(error)
    setHistoryError(() => recordErrorMessage(error, translate))
  } finally { loadingHistory.value = false }
}
function openMessageActions(message: ChatMessage) {
  if (busy.value || sessionExpired.value || message.status !== 'success') return
  accountOpen.value = false
  setDeleteError(() => '')
  selectedMessage.value = message
}
async function removeMessage() {
  const message = selectedMessage.value
  if (!message || busy.value) return
  deleting.value = true
  setDeleteError(() => '')
  try {
    if (!await sameAccount()) return
    try { await transport.softDelete(message.id) }
    catch (error) {
      // Missing / previously deleted / no longer accessible all mean no visible record.
      if (recordErrorStatus(error) !== 404) throw error
    }
    if (!active) return
    messages.value = messages.value.filter(item => item.id !== message.id)
    clearNuxtData(cacheKey)
    clearNuxtData(`records:${pageUid}`)
    selectedMessage.value = null
    setAnnouncement(() => t('chat.removedAnnouncement'))
  } catch (error) {
    if (!active) return
    handleError(error)
    const status = recordErrorStatus(error)
    setDeleteError(() => status === undefined || status >= 500
      ? t('chat.deleteUnknown')
      : recordErrorMessage(error, translate))
    if (sessionExpired.value) setActionError(() => deleteError.value)
  } finally { deleting.value = false }
}
async function logout() {
  if (busy.value) return
  if (draft.value.trim() && !window.confirm(t('chat.discardLogout'))) return
  loggingOut.value = true
  try {
    await auth.logout()
    selectedMessage.value = null
    messages.value = []
    pending.value = null
    draft.value = ''
    await navigateTo('/login', { replace: true })
  } catch { setActionError(() => t('chat.logoutFailed')) }
  finally { loggingOut.value = false }
}
async function onFocus() {
  if (busy.value) return
  const wasExpired = sessionExpired.value
  try { if (await sameAccount() && wasExpired) await loadHistory(true) }
  catch (error) { if (active) setActionError(() => recordErrorMessage(error, translate)) }
}
function onRestore(event: PageTransitionEvent) { if (event.persisted) void onFocus() }
function onViewportResize() {
  const viewport = window.visualViewport
  const el = viewportRoot.value
  if (viewport && el) { el.style.height = `${viewport.height}px`; el.style.top = `${viewport.offsetTop}px` }
}
onMounted(() => {
  void scrollToLatest()
  onViewportResize()
  window.addEventListener('focus', onFocus)
  window.addEventListener('pageshow', onRestore)
  window.visualViewport?.addEventListener('resize', onViewportResize)
  window.visualViewport?.addEventListener('scroll', onViewportResize)
})
onBeforeUnmount(() => {
  active = false
  clearNuxtData(cacheKey)
  window.removeEventListener('focus', onFocus)
  window.removeEventListener('pageshow', onRestore)
  window.visualViewport?.removeEventListener('resize', onViewportResize)
  window.visualViewport?.removeEventListener('scroll', onViewportResize)
})
</script>

<template>
  <div ref="viewportRoot" class="fixed inset-0 flex h-dvh overflow-hidden bg-chat-paper text-chat-ink [&_:is(button,a,textarea,summary)]:[-webkit-tap-highlight-color:transparent] [&_button]:cursor-pointer [&_button:disabled]:cursor-default [&_button:disabled]:opacity-45 [&_:focus-visible:not(textarea)]:outline-2 [&_:focus-visible:not(textarea)]:outline-offset-4 [&_:focus-visible:not(textarea)]:outline-chat-focus">
    <aside class="hidden w-[236px] shrink-0 flex-col border-r border-chat-line bg-[#f3f4ed] px-5 pt-[35px] pb-6 md:flex" :aria-label="t('chat.primaryNav')">
      <NuxtLink to="/" class="flex items-baseline gap-[3px] pl-2.5 font-display text-[29px] tracking-[-1px]"><img src="/brand/raphael-mark.svg" alt="" width="29" height="29" class="mr-[7px] size-[29px] shrink-0 self-center">Raphael<span class="inline-block size-[5px] rounded-full bg-[#92ad68]" /></NuxtLink>
      <p class="mx-2.5 mt-[19px] mb-[25px] text-[8px] tracking-[.2em] text-[#9da68f]">{{ t('chat.personalSpace') }}</p>
      <nav class="grid gap-[7px]">
        <NuxtLink to="/chat" class="flex min-h-[47px] items-center gap-3 rounded-[10px] bg-[#e3eadb] px-3.5 text-[12px] font-medium text-[#415e38] [&>span:first-child]:text-[20px]" aria-current="page"><span aria-hidden="true">✧</span> {{ t('chat.title') }} <span class="ml-auto rounded-[5px] bg-[#d3dfc8] px-1.5 py-px text-[10px]">{{ messages.length }}</span></NuxtLink>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users" class="flex min-h-[47px] items-center gap-3 rounded-[10px] px-3.5 text-[12px] text-[#7b8674]"><span aria-hidden="true" class="text-[20px]">▤</span> {{ t('admin.nav') }} <span aria-hidden="true" class="ml-auto">↗</span></NuxtLink>
      </nav>
      <div class="mx-3 mt-auto mb-[35px] pt-[55px] text-[11px] leading-[1.9] text-[#99a38b] [&>span]:mb-3 [&>span]:block [&>span]:text-[30px] [&>span]:text-[#a5b393]"><span aria-hidden="true">✳</span><p>{{ t('chat.motto') }}</p></div>
      <button type="button" class="flex items-center gap-2.5 border-t border-[#e0e5d7] pt-5 text-[11px] [&_small]:mt-1 [&_small]:block [&_small]:text-[9px] [&_small]:text-[#97a08c] [&>span:last-child]:ml-auto [&>span:last-child]:text-[#9ca58f]" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen"><span class="inline-flex size-9 items-center justify-center rounded-full bg-[#e8ecdf] text-[12px] text-[#627353]" aria-hidden="true">{{ t('chat.me') }}</span><span>{{ t('chat.account') }}<small>{{ t('chat.memorySpace') }}</small></span><span aria-hidden="true">↗</span></button>
    </aside>

    <div class="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <header class="flex items-center justify-between gap-3 border-b border-chat-line bg-chat-paper px-5 pt-[max(17px,env(safe-area-inset-top))] pb-4 md:px-9 md:pt-[25px] md:pb-[22px]">
        <div class="min-w-0"><NuxtLink to="/" class="mb-2 flex items-baseline gap-1 font-display text-[24px] tracking-[-.8px] md:hidden"><img src="/brand/raphael-mark.svg" alt="" width="26" height="26" class="mr-1 size-[26px] shrink-0 self-center">Raphael<span class="inline-block size-[5px] rounded-full bg-[#92ad68]" /></NuxtLink><h1 class="flex flex-wrap items-center gap-2.5 text-[15px] font-semibold md:text-[17px]">{{ t('chat.title') }} <span class="rounded-[5px] bg-[#edf1e9] px-2 py-[3px] text-[10px] font-normal text-[#61755c]">{{ t('chat.mode') }}</span></h1><p class="hidden md:mt-1.5 md:block md:text-[11px] md:text-[#969e8e]">{{ t('chat.subtitle') }}</p></div>
        <nav :aria-label="t('chat.quickNav')" class="flex shrink-0 items-center gap-4 text-[12px]"><LanguageSwitcher compact /><button type="button" class="inline-flex size-11 items-center justify-center rounded-full bg-[#e8ecdf] text-[12px] text-[#627353]" :aria-label="t('chat.account')" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen">{{ t('chat.me') }}</button></nav>
      </header>
      <section v-if="accountOpen" id="chat-account-panel" class="absolute top-20 right-5 z-10 w-[min(320px,calc(100%_-_40px))] rounded-2xl border border-[#dce2d4] bg-white px-[18px] py-[15px] shadow-[0_12px_35px_#253c3418]" :aria-label="t('chat.account')" @keydown.esc="accountOpen = false">
        <div class="flex items-center justify-between text-[14px]"><strong>{{ t('chat.account') }}</strong><button type="button" class="flex min-h-11 min-w-11 items-center justify-center text-[22px] text-chat-green" :aria-label="t('chat.closeAccount')" @click="accountOpen = false">×</button></div>
        <p class="mb-2.5 text-[12px] text-chat-muted wrap-anywhere">{{ auth.user.value?.email || t('chat.googleAccount') }}</p>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users" class="flex min-h-11 items-center text-[13px] text-chat-green">{{ t('chat.manageAccounts') }} ↗</NuxtLink>
        <button type="button" class="flex min-h-11 items-center text-[13px] text-chat-green" :disabled="busy" @click="logout">{{ loggingOut ? t('common.loggingOut') : t('common.logout') }}</button>
      </section>
      <div class="flex min-h-11 items-center justify-between gap-3 bg-[#f0f2e9] px-5 text-[11px] text-[#718267]"><span>{{ t('chat.private') }}</span><button type="button" class="min-h-11 px-2 underline underline-offset-[3px]" :disabled="busy" @click="loadHistory(true)">{{ loadingHistory ? t('common.loading') : t('common.refresh') }}</button></div>
      <main ref="scroller" class="min-h-0 flex-1 overflow-y-auto overscroll-y-contain [scrollbar-width:thin] [scrollbar-color:#d3dacd_transparent]" :aria-label="t('chat.history')">
        <div class="mx-auto max-w-[780px] px-5 pt-[22px] md:px-9 md:pt-[30px]">
          <div class="pt-[5px] pb-[22px] text-center md:pt-3 md:pb-[27px]"><span class="mb-2 inline-block text-[34px] text-[#7c9369]" aria-hidden="true">✳</span><h2 class="text-[18px] font-medium tracking-[.03em] md:text-[21px]">{{ t('chat.welcome') }}</h2><p class="mt-2.5 text-[12px] leading-[1.9] text-chat-muted">{{ t('chat.welcomeDetail') }}</p></div>
          <div class="flex min-h-11 justify-center"><button v-if="nextCursor || historyError" type="button" class="min-h-11 rounded-lg px-[15px] text-[11px] text-[#75856f] hover:bg-[#eef1e8]" :disabled="loadingHistory" @click="loadHistory(!nextCursor)">{{ loadingHistory ? t('chat.loadingHistory') : historyError ? t('chat.retryHistory') : t('chat.older') }}</button></div>
          <p v-if="historyError" role="alert" class="text-center text-[12px] text-chat-error">{{ historyError }}</p>
          <ol class="flex list-none flex-col gap-3 pt-2.5 md:gap-[26px]" :aria-busy="loadingHistory">
            <template v-for="(message, index) in messages" :key="message.id">
              <li v-if="index === 0 || dayLabel(messages[index - 1]!.createdAt) !== dayLabel(message.createdAt)" class="mt-0.5 mb-1.5 flex items-center gap-4 text-[10px] text-[#969c91] before:h-px before:flex-1 before:bg-chat-line before:content-[''] after:h-px after:flex-1 after:bg-chat-line after:content-['']"><span>{{ dayLabel(message.createdAt) }}</span></li>
              <ChatBubble :message="message" :disabled="busy || sessionExpired" @actions="openMessageActions" />
            </template>
            <ChatBubble v-if="pending" :message="pending" />
          </ol>
          <p v-if="!messages.length && !pending && !historyError" class="py-[25px] text-center text-[13px] text-chat-muted">{{ t('chat.empty') }}</p>
          <div ref="bottom" class="h-1" />
        </div>
      </main>
      <footer class="shrink-0 bg-chat-paper px-4 pb-[calc(env(safe-area-inset-bottom)_+_12px)] md:px-9 md:pb-3">
        <div class="mx-auto max-w-[740px] pt-1">
          <div v-if="actionError || sessionExpired" class="mb-3 max-h-[25dvh] overflow-y-auto rounded-xl border border-[#ebd0c7] bg-[#fff5ef] px-4 py-3 text-[12px] leading-[1.8] text-[#8c4b3c]" role="alert">
            <p>{{ actionError || t('chat.expired') }}</p>
            <NuxtLink v-if="sessionExpired" class="inline-flex min-h-11 items-center text-left underline underline-offset-[3px]" to="/login" target="_blank" rel="noopener">{{ t('chat.loginNewTab') }}</NuxtLink>
            <template v-if="uncertain">
              <p>{{ t('chat.checkSaved') }}</p>
              <button type="button" class="inline-flex min-h-11 items-center text-left underline underline-offset-[3px]" :disabled="busy" @click="uncertain = false; pending = null; clearActionError()">{{ t('chat.allowRetry') }}</button>
            </template>
          </div>
          <ChatComposer v-model="draft" :disabled="busy || uncertain" @send="send" />
        </div>
      </footer>
      <ChatMessageActions v-if="selectedMessage" :message="selectedMessage" :busy="deleting" :error="deleteError" @close="selectedMessage = null" @remove="removeMessage" />
      <p class="sr-only" role="status" aria-live="polite">{{ announcement }}</p>
    </div>
  </div>
</template>
