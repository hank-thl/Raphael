<script setup lang="ts">
import type { PersonalRecord } from '~/types/record'
import { formatRecordTime } from '~/utils/records'

defineProps<{ records: PersonalRecord[]; busy: boolean }>()
const emit = defineEmits<{ remove: [record: PersonalRecord] }>()
</script>

<template>
  <ul class="space-y-4" aria-label="個人紀錄">
    <li v-for="record in records" :key="record.id" class="rounded-2xl border border-stone-200 bg-white p-5 sm:p-6">
      <article>
        <div class="flex items-center justify-between gap-3">
          <time :datetime="record.created_at" class="text-sm text-stone-500">{{ formatRecordTime(record.created_at) }}</time>
          <button type="button" :disabled="busy" :aria-label="`刪除 ${formatRecordTime(record.created_at)} 的紀錄`" class="min-h-11 shrink-0 rounded-lg px-3 text-sm text-stone-500 hover:bg-red-50 hover:text-red-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700 disabled:opacity-50" @click="emit('remove', record)">刪除</button>
        </div>
        <p class="mt-3 whitespace-pre-wrap wrap-anywhere leading-7 text-stone-800">{{ record.content }}</p>
      </article>
    </li>
  </ul>
</template>
