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
  <label class="relative inline-flex min-h-11 min-w-0 max-w-full items-center text-sm text-chat-green" :class="compact && 'size-11 shrink-0 justify-center rounded-lg md:h-auto md:w-auto md:min-w-11'">
    <span class="sr-only">{{ t('common.language') }}</span>
    <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></svg>
    <select :value="locale" :disabled="busy" :aria-label="t('common.language')" :class="compact
        ? 'absolute inset-0 h-full w-full text-base opacity-0 disabled:opacity-0 md:static md:h-auto md:w-auto md:text-sm md:opacity-100 md:disabled:opacity-50'
        : 'text-sm disabled:opacity-50'" class="min-h-11 min-w-0 max-w-36 cursor-pointer rounded-lg bg-chat-paper px-2 outline-none" @change="change">
      <option v-for="code in supportedLocales" :key="code" :value="code" :lang="code">{{ t(`languages.${code}`) }}</option>
    </select>
  </label>
</template>
