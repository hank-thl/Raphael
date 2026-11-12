<script setup lang="ts">
import { supportedLocales, normalizeLocale } from '~/utils/locale'
defineProps<{ compact?: boolean }>()
const { t, locale, setLocale } = useI18n()
const busy = ref(false)
const mobileMenu = ref<HTMLDetailsElement>()
const mobileTrigger = ref<HTMLElement>()

function closeMobileMenu(restoreFocus = false) {
  if (mobileMenu.value) mobileMenu.value.open = false
  if (restoreFocus) mobileTrigger.value?.focus({ preventScroll: true })
}
async function choose(value: string) {
  if (busy.value) return
  busy.value = true
  try {
    await setLocale(normalizeLocale(value))
    if (mobileMenu.value?.open) closeMobileMenu(true)
  } finally { busy.value = false }
}
function change(event: Event) {
  return choose((event.target as HTMLSelectElement).value)
}
function onOutsidePointer(event: PointerEvent) {
  if (event.target instanceof Node && !mobileMenu.value?.contains(event.target)) closeMobileMenu()
}
function onFocusOut(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && !mobileMenu.value?.contains(event.relatedTarget)) closeMobileMenu()
}
onMounted(() => document.addEventListener('pointerdown', onOutsidePointer))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutsidePointer))
</script>

<template>
  <div class="inline-flex min-w-0 max-w-full items-center">
    <details v-if="compact" ref="mobileMenu" class="relative min-w-11 text-chat-green sm:hidden" @keydown.esc.stop.prevent="closeMobileMenu(true)" @focusout="onFocusOut">
      <summary ref="mobileTrigger" :aria-label="t('common.language')" class="flex size-11 cursor-pointer list-none items-center justify-center rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-chat-focus [&::-webkit-details-marker]:hidden">
        <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></svg>
      </summary>
      <ul :aria-label="t('common.language')" class="absolute top-full right-0 z-30 mt-1 w-40 max-w-[calc(100vw_-_40px)] rounded-xl border border-chat-line bg-chat-paper p-1 shadow-lg">
        <li v-for="code in supportedLocales" :key="code">
          <button type="button" :disabled="busy" :lang="code" :aria-current="locale === code ? 'true' : undefined" class="flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-chat-focus disabled:opacity-50" :class="locale === code ? 'bg-[#e3eadb] font-medium' : 'hover:bg-[#edf1e9]'" @click="choose(code)">
            <span>{{ t(`languages.${code}`) }}</span><span v-if="locale === code" aria-hidden="true">✓</span>
          </button>
        </li>
      </ul>
    </details>
    <label class="relative min-h-11 min-w-0 max-w-full items-center  text-sm text-chat-green" :class="compact ? 'hidden min-w-11 justify-center rounded-lg sm:inline-flex' : 'inline-flex'">
      <span class="sr-only">{{ t('common.language') }}</span>
      <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></svg>
      <select :value="locale" :disabled="busy" :aria-label="t('common.language')" :class="compact && 'absolute inset-0 w-full opacity-0 sm:static sm:w-auto sm:opacity-100'" class="min-h-11 min-w-0 max-w-36 cursor-pointer rounded-lg bg-chat-paper px-2 text-sm outline-none  disabled:opacity-50" @change="change">
        <option v-for="code in supportedLocales" :key="code" :value="code" :lang="code">{{ t(`languages.${code}`) }}</option>
      </select>
    </label>
  </div>
</template>
