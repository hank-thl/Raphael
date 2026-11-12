<script setup lang="ts">
import { createLongPress } from '~/utils/long-press'
import type { ChatMessage } from '~/types/chat'
const { t, locale } = useI18n()
const props = defineProps<{ message: ChatMessage; disabled?: boolean }>()
const emit = defineEmits<{ actions: [message: ChatMessage] }>()
const moreButton = ref<HTMLButtonElement>()
const actionable = computed(() => props.message.status === 'success' && props.message.role === 'user' && !props.disabled)
function openActions() {
  press.cancel()
  if (!actionable.value) return
  moreButton.value?.focus({ preventScroll: true })
  emit('actions', props.message)
}
const press = createLongPress(openActions)
function startPress(event: PointerEvent) {
  if (!actionable.value || !event.isPrimary || event.button !== 0) { press.cancel(); return }
  if (event.pointerType === 'touch' || event.pointerType === 'pen') press.start(event.pointerId, event.clientX, event.clientY)
}
function contextMenu(event: MouseEvent) {
  if (!actionable.value) return
  event.preventDefault()
  openActions()
}
watch(actionable, () => press.cancel())
onBeforeUnmount(press.cancel)
const time = computed(() => new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Taipei' }).format(new Date(props.message.createdAt)))
</script>

<template>
  <li class="flex w-full flex-col" :class="message.role === 'assistant' ? 'items-start' : 'items-end'">
    <div class="max-w-[90%] px-3.5 py-2.5 md:max-w-[78%] md:px-[21px] md:py-4"
      :class="[
        message.role === 'assistant'
          ? 'rounded-[14px_14px_14px_4px] md:rounded-[18px_18px_18px_5px] bg-[#edf1e8] text-chat-ink shadow-none'
          : message.status === 'error'
            ? 'rounded-[14px_14px_4px_14px] md:rounded-[18px_18px_5px_18px] border border-[#ebd0c7] bg-[#f8efeb] text-[#8c4b3c] shadow-none'
            : 'rounded-[14px_14px_4px_14px] md:rounded-[18px_18px_5px_18px] bg-chat-green text-chat-bubble-text shadow-[0_2px_3px_#20372b08]',
        actionable && 'touch-pan-y touch-pinch-zoom [-webkit-touch-callout:none] pointer-coarse:select-none',
      ]" @pointerdown="startPress" @pointermove="press.move($event.pointerId, $event.clientX, $event.clientY)" @pointerup="press.cancel" @pointercancel="press.cancel" @pointerleave="press.cancel" @contextmenu="contextMenu"><p class="text-[14px] leading-[1.7] md:text-[15px] md:leading-[1.85] tracking-[.015em] whitespace-pre-wrap wrap-anywhere">{{ message.content }}</p></div>
    <div class="mt-[7px] flex flex-wrap items-center gap-[9px] pr-[3px] text-[10px] text-[#90978d]">
      <time :datetime="message.createdAt">{{ time }}</time>
      <span v-if="message.status === 'loading'" class="inline-flex items-center gap-[3px] [&_svg]:size-3"><span class="size-[9px] animate-[spin_.8s_linear_infinite] rounded-full border border-[#a6b09c] border-t-transparent motion-reduce:animate-none" /> {{ t('bubble.sending') }}</span>
      <span v-else-if="message.status === 'error'" class="text-chat-error">{{ message.uncertain ? t('bubble.uncertain') : t('bubble.unsaved') }}</span>
      <span v-else class="inline-flex items-center gap-[3px] [&_svg]:size-3"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>{{ t('bubble.saved') }}</span>
      <button v-if="message.status === 'success' && message.role === 'user'" ref="moreButton" type="button" class="relative -my-[3px] -mr-1 inline-flex min-h-7 w-7 items-center justify-center rounded-md border-0 bg-transparent p-0 text-[18px] leading-none text-chat-muted shadow-none after:absolute after:-inset-2 after:content-[''] enabled:hover:text-chat-green" :disabled="disabled" :aria-label="t('bubble.actions')" aria-haspopup="dialog" @click="openActions">⋯</button>
    </div>
  </li>
</template>
