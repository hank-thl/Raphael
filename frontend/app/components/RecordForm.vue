<script setup lang="ts">
import { recordCharacterCount, recordValidation } from '~/utils/records'
const { t, locale } = useI18n()

const content = defineModel<string>({ required: true })
const props = defineProps<{ busy: boolean }>()
const emit = defineEmits<{ submit: [content: string] }>()
const count = computed(() => recordCharacterCount(content.value))
const validation = computed(() => recordValidation(content.value, (key, values) => t(key, values || {})))

function submit() {
  if (!props.busy && !validation.value) emit('submit', content.value)
}
</script>

<template>
  <form class="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm sm:p-6" @submit.prevent="submit">
    <label for="record-content" class="text-lg font-semibold">{{ t('record.heading') }}</label>
    <p id="record-help" class="mt-2 text-sm leading-6 text-stone-500">{{ t('record.help') }}</p>
    <textarea id="record-content" v-model="content" :disabled="busy" :aria-invalid="count > 10000" aria-describedby="record-help record-count" rows="6" :placeholder="t('record.placeholder')" class="mt-4 block w-full resize-y rounded-xl border border-stone-300 bg-stone-50 p-4 text-base leading-7 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20 disabled:opacity-60" />
    <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
      <p id="record-count" class="text-sm" :class="count > 10000 ? 'text-red-700' : 'text-stone-500'">{{ t('record.count', { count: count.toLocaleString(locale) }) }}</p>
      <button type="submit" :disabled="busy || Boolean(validation)" class="min-h-11 rounded-xl bg-teal-800 px-6 py-3 font-medium text-white hover:bg-teal-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50">{{ busy ? t('common.processing') : t('record.save') }}</button>
    </div>
    <p v-if="count > 10000" role="alert" class="mt-3 text-sm text-red-700">{{ validation }}</p>
  </form>
</template>
