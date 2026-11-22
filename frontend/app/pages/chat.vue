<script setup lang="ts">
import UnifiedChatPanel from '~/components/chat/UnifiedChatPanel.vue'
import type { ChatMode } from '~/types/unified-chat'
const { t } = useI18n()
definePageMeta({ middleware: 'auth' })
useHead(() => ({ title: `${t('chat.title')}｜Raphael` }))
const auth = useAuth()
const pageUid = auth.user.value?.uid
const { data: admin } = await useFetch<{ is_admin: boolean; ai_enabled: boolean; capture_enabled: boolean }>('/api/auth/capabilities')
const mode = ref<ChatMode>('record')
const draft = ref('')
const accountOpen = ref(false), loggingOut = ref(false), panelBusy = ref(false), sessionExpired = ref(false)
const messageCount = ref(0)
const logoutFailed = ref(false)
const viewportRoot = ref<HTMLElement>()
const busy = computed(() => loggingOut.value || panelBusy.value)
async function logout() {
  if (busy.value || (draft.value.trim() && !window.confirm(t('chat.discardLogout')))) return
  loggingOut.value = true
  logoutFailed.value = false
  try { await auth.logout(); draft.value = ''; await navigateTo('/login', { replace: true }) }
  catch { logoutFailed.value = true }
  finally { loggingOut.value = false }
}
function onViewportResize() {
  const viewport = window.visualViewport, el = viewportRoot.value
  if (viewport && el) { el.style.height = `${viewport.height}px`; el.style.top = `${viewport.offsetTop}px` }
}
onMounted(() => {
  onViewportResize()
  window.visualViewport?.addEventListener('resize', onViewportResize)
  window.visualViewport?.addEventListener('scroll', onViewportResize)
})
onBeforeUnmount(() => {
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
        <NuxtLink to="/chat" class="flex min-h-[47px] items-center gap-3 rounded-[10px] bg-[#e3eadb] px-3.5 text-[12px] font-medium text-[#415e38] [&>span:first-child]:text-[20px]" aria-current="page"><span aria-hidden="true">✧</span> {{ t('chat.title') }} <span class="ml-auto rounded-[5px] bg-[#d3dfc8] px-1.5 py-px text-[10px]">{{ messageCount }}</span></NuxtLink>
        <NuxtLink v-if="admin?.ai_enabled" to="/memories" class="flex min-h-[47px] items-center gap-3 rounded-[10px] px-3.5 text-[12px] text-[#7b8674]">{{ t('memoryLibrary.nav') }}</NuxtLink>
        <NuxtLink v-if="admin?.ai_enabled" to="/admin/ai-test" class="flex min-h-[47px] items-center rounded-[10px] px-3.5 text-[12px] text-[#7b8674]">{{ t('aiTest.nav') }}</NuxtLink>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users" class="flex min-h-[47px] items-center gap-3 rounded-[10px] px-3.5 text-[12px] text-[#7b8674]"><span aria-hidden="true" class="text-[20px]">▤</span> {{ t('admin.nav') }} <span aria-hidden="true" class="ml-auto">↗</span></NuxtLink>
      </nav>
      <div class="mx-3 mt-auto mb-[35px] pt-[55px] text-[11px] leading-[1.9] text-[#99a38b] [&>span]:mb-3 [&>span]:block [&>span]:text-[30px] [&>span]:text-[#a5b393]"><span aria-hidden="true">✳</span><p>{{ t('chat.motto') }}</p></div>
      <button type="button" class="flex items-center gap-2.5 border-t border-[#e0e5d7] pt-5 text-[11px] [&_small]:mt-1 [&_small]:block [&_small]:text-[9px] [&_small]:text-[#97a08c] [&>span:last-child]:ml-auto [&>span:last-child]:text-[#9ca58f]" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen"><span class="inline-flex size-9 items-center justify-center rounded-full bg-[#e8ecdf] text-[12px] text-[#627353]" aria-hidden="true">{{ t('chat.me') }}</span><span>{{ t('chat.account') }}<small>{{ t('chat.memorySpace') }}</small></span><span aria-hidden="true">↗</span></button>
    </aside>

    <div class="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <header class="flex items-center justify-between gap-3 border-b border-chat-line bg-chat-paper px-5 pt-[max(17px,env(safe-area-inset-top))] pb-4 md:px-9 md:pt-[25px] md:pb-[22px]">
        <div class="min-w-0"><NuxtLink to="/" class="mb-2 flex items-baseline gap-1 font-display text-[24px] tracking-[-.8px] md:hidden"><img src="/brand/raphael-mark.svg" alt="" width="26" height="26" class="mr-1 size-[26px] shrink-0 self-center">Raphael<span class="inline-block size-[5px] rounded-full bg-[#92ad68]" /></NuxtLink><h1 class="flex flex-wrap items-center gap-2.5 text-[15px] font-semibold md:text-[17px]">{{ t(mode === 'record' ? 'chat.title' : 'conversation.title') }} <span class="rounded-[5px] bg-[#edf1e9] px-2 py-[3px] text-[10px] font-normal text-[#61755c]">{{ t(mode === 'record' ? 'chat.mode' : 'conversation.mode') }}</span></h1><p class="hidden md:mt-1.5 md:block md:text-[11px] md:text-[#969e8e]">{{ t(mode === 'record' ? 'chat.subtitle' : 'conversation.title') }}</p></div>
        <nav :aria-label="t('chat.quickNav')" class="flex shrink-0 items-center gap-4 text-[12px]"><LanguageSwitcher compact /><button type="button" class="inline-flex size-11 items-center justify-center rounded-full bg-[#e8ecdf] text-[12px] text-[#627353]" :aria-label="t('chat.account')" :aria-expanded="accountOpen" aria-controls="chat-account-panel" @click="accountOpen = !accountOpen">{{ t('chat.me') }}</button></nav>
      </header>
      <section v-if="accountOpen" id="chat-account-panel" class="absolute top-20 right-5 z-10 w-[min(320px,calc(100%_-_40px))] rounded-2xl border border-[#dce2d4] bg-white px-[18px] py-[15px] shadow-[0_12px_35px_#253c3418]" :aria-label="t('chat.account')" @keydown.esc="accountOpen = false">
        <div class="flex items-center justify-between text-[14px]"><strong>{{ t('chat.account') }}</strong><button type="button" class="flex min-h-11 min-w-11 items-center justify-center text-[22px] text-chat-green" :aria-label="t('chat.closeAccount')" @click="accountOpen = false">×</button></div>
        <p class="mb-2.5 text-[12px] text-chat-muted wrap-anywhere">{{ auth.user.value?.email || t('chat.googleAccount') }}</p>
        <NuxtLink v-if="admin?.ai_enabled" to="/memories" class="flex min-h-11 items-center text-[13px] text-chat-green">{{ t('memoryLibrary.nav') }}</NuxtLink>
        <NuxtLink v-if="admin?.ai_enabled" to="/admin/ai-test" class="flex min-h-11 items-center text-[13px] text-chat-green">{{ t('aiTest.nav') }}</NuxtLink>
        <NuxtLink v-if="admin?.is_admin" to="/admin/users" class="flex min-h-11 items-center text-[13px] text-chat-green">{{ t('chat.manageAccounts') }} ↗</NuxtLink>
        <button type="button" class="flex min-h-11 items-center text-[13px] text-chat-green" :disabled="busy" @click="logout">{{ loggingOut ? t('common.loggingOut') : t('common.logout') }}</button>
        <p v-if="logoutFailed" role="alert" class="text-xs text-chat-error">{{ t('chat.logoutFailed') }}</p>
      </section>
      <div v-if="admin?.ai_enabled" class="flex shrink-0 gap-1 border-b border-chat-line px-5 py-1 md:px-9" role="group" :aria-label="t('conversation.switch')">
        <button type="button" :disabled="busy" :aria-pressed="mode === 'record'" class="min-h-10 rounded-lg px-4 text-xs" :class="mode === 'record' ? 'bg-[#e3eadb] text-chat-green' : 'text-chat-muted'" @click="mode = 'record'">{{ t('conversation.recordMode') }}</button>
        <button type="button" :disabled="busy || sessionExpired" :aria-pressed="mode === 'dialogue'" class="min-h-10 rounded-lg px-4 text-xs" :class="mode === 'dialogue' ? 'bg-[#e3eadb] text-chat-green' : 'text-chat-muted'" @click="mode = 'dialogue'">{{ t('conversation.mode') }}</button>
      </div>
      <UnifiedChatPanel v-if="pageUid" v-model:draft="draft" :expected-uid="pageUid" :mode="mode" :capture-enabled="!!admin?.capture_enabled" :ai-enabled="!!admin?.ai_enabled" @busy="panelBusy = $event" @count="messageCount = $event" @unauthorized="sessionExpired = true; mode = 'record'" />
    </div>
  </div>
</template>
