<script setup lang="ts">
import { supportedLocales, normalizeLocale } from '~/utils/locale'
defineProps<{ compact?: boolean }>()
const { t, locale, setLocale } = useI18n()
const busy = ref(false)
async function change(event: Event) {
  busy.value = true
  try { await setLocale(normalizeLocale((event.target as HTMLSelectElement).value)) }
  finally { busy.value = false }
}
</script>

<template>
  <label class="relative inline-flex min-h-11 min-w-0 max-w-full items-center  text-sm text-chat-green" :class="compact && 'min-w-11 justify-center rounded-lg '">
    <span class="sr-only">{{ t('common.language') }}</span>
    <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></svg>
    <select :value="locale" :disabled="busy" :aria-label="t('common.language')" :class="compact && 'absolute inset-0 w-full opacity-0 sm:static sm:w-auto sm:opacity-100'" class="min-h-11 min-w-0 max-w-36 cursor-pointer rounded-lg bg-chat-paper px-2 text-sm outline-none  disabled:opacity-50" @change="change">
      <option v-for="code in supportedLocales" :key="code" :value="code" :lang="code">{{ t(`languages.${code}`) }}</option>
    </select>
  </label>
</template>
