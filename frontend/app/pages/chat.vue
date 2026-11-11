<script setup lang="ts">
import ChatMessageActions from '~/components/chat/ChatMessageActions.vue'
import ChatBubble from '~/components/chat/ChatBubble.vue'
import ChatComposer from '~/components/chat/ChatComposer.vue'
import type { ChatMessage } from '~/types/chat'
import { mergeChatMessages } from '~/utils/chat'
import { recordValidation, recordErrorStatus, recordErrorMessage, recordSaveErrorMessage } from '~/utils/records'

definePageMeta({ middleware: 'auth' })
useHead({ title: '記下此刻｜Raphael' })
const auth = useAuth()
const accountOpen = ref(false)
const loggingOut = ref(false)
const { data: admin } = await useFetch<{ is_admin: boolean }>('/api/admin/me')
const pageUid = auth.user.value?.uid
const transport = useChatRecords()
const messages = ref<ChatMessage[]>([])
const selectedMessage = ref<ChatMessage | null>(null)
const deleting = ref(false)
const deleteError = ref('')
const pending = ref<ChatMessage | null>(null)
const draft = ref('')
const nextCursor = ref<string | null>(null)
const loadingHistory = ref(false)
const sending = ref(false)
const sessionExpired = ref(false)
const uncertain = ref(false)
const historyError = ref('')
const actionError = ref('')
const announcement = ref('')
const scroller = ref<HTMLElement>()
const bottom = ref<HTMLElement>()
const busy = computed(() => sending.value || loadingHistory.value || loggingOut.value || deleting.value)
const dayLabel = (value: string) => new Intl.DateTimeFormat('zh-TW', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'Asia/Taipei' }).format(new Date(value))
const cacheKey = `chat-records:${pageUid}`
const { data: initial, error: initialError } = await useAsyncData(cacheKey, () => transport.history())
messages.value = initial.value?.messages || []
nextCursor.value = initial.value?.nextCursor || null
historyError.value = initialError.value ? recordErrorMessage(initialError.value) : ''
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
  actionError.value = ''
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
    announcement.value = '訊息已儲存。'
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
    actionError.value = submitted ? recordSaveErrorMessage(error) : recordErrorMessage(error)
    announcement.value = actionError.value
  } finally { sending.value = false }
}
async function loadHistory(latest = false) {
  if (busy.value) return
  const before = scroller.value?.scrollHeight || 0
  const offset = scroller.value?.scrollTop || 0
  loadingHistory.value = true
  historyError.value = ''
  try {
    if (!await sameAccount()) return
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
    handleError(error)
    historyError.value = recordErrorMessage(error)
  } finally { loadingHistory.value = false }
}
function openMessageActions(message: ChatMessage) {
  if (busy.value || sessionExpired.value || message.status !== 'success') return
  accountOpen.value = false
  deleteError.value = ''
  selectedMessage.value = message
}
async function removeMessage() {
  const message = selectedMessage.value
  if (!message || busy.value) return
  deleting.value = true
  deleteError.value = ''
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
    announcement.value = '紀錄已從聊天室移除。'
  } catch (error) {
    if (!active) return
    handleError(error)
    const status = recordErrorStatus(error)
    deleteError.value = status === undefined || status >= 500
      ? '無法確認刪除結果，請取消並重新整理確認。原文仍保留在資料庫。'
      : recordErrorMessage(error)
    if (sessionExpired.value) actionError.value = deleteError.value
  } finally { deleting.value = false }
}
async function logout() {
  if (busy.value) return
  if (draft.value.trim() && !window.confirm('還有尚未儲存的文字，確定放棄並登出？')) return
  loggingOut.value = true
  try {
    await auth.logout()
    selectedMessage.value = null
    messages.value = []
    pending.value = null
    draft.value = ''
    await navigateTo('/login', { replace: true })
  } catch { actionError.value = '登出失敗，請稍後再試。' }
  finally { loggingOut.value = false }
}
async function onFocus() {
  if (busy.value) return
  const wasExpired = sessionExpired.value
  try { if (await sameAccount() && wasExpired) await loadHistory(true) }
  catch (error) { if (active) actionError.value = recordErrorMessage(error) }
}
function onRestore(event: PageTransitionEvent) { if (event.persisted) void onFocus() }
function onViewportResize() {
  const viewport = window.visualViewport
  const el = scroller.value?.closest<HTMLElement>('.chat-app')
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
  <div class="chat-app">
    <aside class="chat-sidebar" aria-label="主要導覽">
      <NuxtLink to="/" class="chat-brand"><span class="chat-brand-mark">r.</span>Raphael<span class="chat-brand-dot" /></NuxtLink>
      <p class="chat-sidebar-caption">YOUR PERSONAL SPACE</p>
      <nav>
        <NuxtLink to="/chat" class="chat-nav-active" aria-current="page"><span aria-hidden="true">✧</span> 記下此刻 <span class="chat-nav-count">{{ messages.length }}</span></NuxtLink>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users"><span aria-hidden="true">▤</span> 帳號管理 <span aria-hidden="true">↗</span></NuxtLink>
      </nav>
      <div class="chat-sidebar-note"><span aria-hidden="true">✳</span><p>每一個微小的瞬間，<br>都值得被好好記得。</p></div>
      <button type="button" class="chat-account" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen"><span class="chat-avatar" aria-hidden="true">我</span><span>我的帳號<small>個人的記憶空間</small></span><span aria-hidden="true">↗</span></button>
    </aside>

    <div class="chat-workspace">
      <header class="chat-header">
        <div><NuxtLink to="/" class="chat-mobile-brand">Raphael<span class="chat-brand-dot" /></NuxtLink><h1>記下此刻 <span class="chat-mode">記錄模式</span></h1><p>讓想法有個安放的地方。</p></div>
        <nav aria-label="快捷導覽" class="chat-header-nav"><button type="button" class="chat-avatar" aria-label="我的帳號" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen">我</button></nav>
      </header>
      <section v-if="accountOpen" id="chat-account-panel" class="chat-account-panel" aria-label="我的帳號" @keydown.esc="accountOpen = false">
        <div class="chat-account-panel-heading"><strong>我的帳號</strong><button type="button" aria-label="關閉帳號選單" @click="accountOpen = false">×</button></div>
        <p>{{ auth.user.value?.email || 'Google 帳號' }}</p>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users">管理允許帳號 ↗</NuxtLink>
        <button type="button" :disabled="busy" @click="logout">{{ loggingOut ? '登出中…' : '登出' }}</button>
      </section>
      <div class="chat-storage-bar"><span>個人紀錄 · 僅自己可見</span><button type="button" :disabled="busy" @click="loadHistory(true)">{{ loadingHistory ? '載入中…' : '重新整理' }}</button></div>
      <main ref="scroller" class="chat-timeline" aria-label="歷史訊息">
        <div class="chat-timeline-inner">
          <div class="chat-intro"><span class="chat-intro-symbol" aria-hidden="true">✳</span><h2>生活裡的小事，也很重要。</h2><p>一個想法、一段對話，或今天的心情。<br>不用整理好，從一句話開始就可以。</p></div>
          <div class="chat-history-control"><button v-if="nextCursor || historyError" type="button" :disabled="loadingHistory" @click="loadHistory(!nextCursor)">{{ loadingHistory ? '正在載入…' : historyError ? '重新載入訊息' : '↑ 載入更早的訊息' }}</button></div>
          <p v-if="historyError" role="alert" class="chat-history-error">{{ historyError }}</p>
          <ol class="chat-message-list" :aria-busy="loadingHistory">
            <template v-for="(message, index) in messages" :key="message.id">
              <li v-if="index === 0 || dayLabel(messages[index - 1]!.createdAt) !== dayLabel(message.createdAt)" class="chat-date-divider"><span>{{ dayLabel(message.createdAt) }}</span></li>
              <ChatBubble :message="message" :disabled="busy || sessionExpired" @actions="openMessageActions" />
            </template>
            <ChatBubble v-if="pending" :message="pending" />
          </ol>
          <p v-if="!messages.length && !pending && !historyError" class="chat-empty">還沒有紀錄。先寫下今天的一件小事吧。</p>
          <div ref="bottom" class="chat-bottom-anchor" />
        </div>
      </main>
      <footer class="chat-footer">
        <div class="chat-footer-inner">
          <div v-if="actionError || sessionExpired" class="chat-action-error" role="alert">
            <p>{{ actionError || '登入已失效，請重新登入。' }}</p>
            <NuxtLink v-if="sessionExpired" to="/login" target="_blank" rel="noopener">在新分頁重新登入</NuxtLink>
            <template v-if="uncertain">
              <p>請先按「重新整理」確認是否已儲存；必要時載入更早紀錄查看。</p>
              <button type="button" :disabled="busy" @click="uncertain = false; pending = null; actionError = ''">已確認沒有儲存，允許重新傳送</button>
            </template>
          </div>
          <ChatComposer v-model="draft" :disabled="busy || uncertain" @send="send" />
          <p class="chat-footer-note"><span aria-hidden="true">◇</span> 留下今天，讓未來的自己想起。</p>
        </div>
      </footer>
      <ChatMessageActions v-if="selectedMessage" :message="selectedMessage" :busy="deleting" :error="deleteError" @close="selectedMessage = null" @remove="removeMessage" />
      <p class="sr-only" role="status" aria-live="polite">{{ announcement }}</p>
    </div>
  </div>
</template>

<style src="~/assets/css/chat.css" />
