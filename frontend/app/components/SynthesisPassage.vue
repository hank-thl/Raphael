<script setup lang="ts">
import type { SynthesisPassage, SummarySource } from '~/types/topic-summary'
import type { MemoryDetail } from '~/types/memories'
const props = defineProps<{ passage: Pick<SynthesisPassage, 'text' | 'sources'> & { basis: string }; sources: Record<string, MemoryDetail['versions'][number]>; sourceBusy: Record<string, boolean>; disabled: boolean }>()
const emit = defineEmits<{ source: [reference: SummarySource] }>()
const { t } = useI18n()
const expanded = ref(false)
const keyOf = (source: SummarySource) => `${source.memory_id}:${source.revision}`
</script>

<template>
  <div class="min-w-0">
    <p class="whitespace-pre-wrap text-[14px] leading-[1.9] wrap-anywhere md:text-[15px]">{{ passage.text }} <button v-if="passage.sources.length" type="button" class="inline-flex min-h-8 items-center rounded-md px-1 text-xs text-chat-green underline decoration-chat-green/30 underline-offset-4" :aria-expanded="expanded" @click="expanded = !expanded">{{ t('topicSummary.references', { count: passage.sources.length }) }}</button></p>
    <div v-if="expanded" class="mt-2 rounded-xl border border-chat-line bg-white/60 px-3 py-2 text-xs text-chat-muted">
      <p v-if="passage.basis === 'inferred'" class="mb-1 leading-relaxed">{{ t('topicSummary.inferenceNote') }}</p>
      <div v-for="(source, number) in passage.sources" :key="keyOf(source)" class="py-1">
        <button type="button" class="min-h-10 rounded-md px-1 text-chat-green underline underline-offset-4 disabled:opacity-50" :disabled="disabled || sourceBusy[keyOf(source)]" :aria-expanded="!!sources[keyOf(source)]" @click="emit('source', source)">{{ sourceBusy[keyOf(source)] ? t('common.loading') : t('topicSummary.openSource', { number: number + 1 }) }}</button>
        <blockquote v-if="sources[keyOf(source)]" class="mb-2 space-y-2 border-l-2 border-[#b9cba9] pl-3 leading-relaxed">
          <p class="text-[10px]">{{ t('memoryLibrary.version', { count: source.revision }) }}</p>
          <p class="whitespace-pre-wrap wrap-anywhere">{{ sources[keyOf(source)]!.text }}</p>
          <p v-for="(quote, index) in sources[keyOf(source)]!.evidence.sources" :key="index" class="whitespace-pre-wrap wrap-anywhere">{{ quote.quote }}</p>
        </blockquote>
      </div>
    </div>
  </div>
</template>
