<script setup lang="ts">
import { recordCharacterCount, recordValidation } from '~/utils/records'

const content = defineModel<string>({ required: true })
const props = defineProps<{ busy: boolean }>()
const emit = defineEmits<{ submit: [content: string] }>()
const count = computed(() => recordCharacterCount(content.value))
const validation = computed(() => recordValidation(content.value))

function submit() {
  if (!props.busy && !validation.value) emit('submit', content.value)
}
</script>

<template>
  <form class="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm sm:p-6" @submit.prevent="submit">
    <label for="record-content" class="text-lg font-semibold">今天想記下什麼？</label>
    <p id="record-help" class="mt-2 text-sm leading-6 text-stone-500">想法、遇見的人，或一件值得留下的小事。</p>
    <textarea id="record-content" v-model="content" :disabled="busy" :aria-invalid="count > 10000" aria-describedby="record-help record-count" rows="6" placeholder="從這裡開始寫…" class="mt-4 block w-full resize-y rounded-xl border border-stone-300 bg-stone-50 p-4 text-base leading-7 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20 disabled:opacity-60" />
    <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
      <p id="record-count" class="text-sm" :class="count > 10000 ? 'text-red-700' : 'text-stone-500'">{{ count.toLocaleString('zh-TW') }} / 10,000 字</p>
      <button type="submit" :disabled="busy || Boolean(validation)" class="min-h-11 rounded-xl bg-teal-800 px-6 py-3 font-medium text-white hover:bg-teal-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50">{{ busy ? '處理中…' : '儲存紀錄' }}</button>
    </div>
    <p v-if="count > 10000" role="alert" class="mt-3 text-sm text-red-700">{{ validation }}</p>
  </form>
</template>
