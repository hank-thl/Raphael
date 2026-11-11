<script setup lang="ts">
import { createLongPress } from '~/utils/long-press'
import type { ChatMessage } from '~/types/chat'
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
const time = computed(() => new Intl.DateTimeFormat('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Taipei' }).format(new Date(props.message.createdAt)))
</script>

<template>
  <li class="chat-message" :class="{ 'chat-message-assistant': message.role === 'assistant' }">
    <div class="chat-bubble" :class="{ 'chat-bubble-error': message.status === 'error', 'chat-bubble-actionable': actionable }" @pointerdown="startPress" @pointermove="press.move($event.pointerId, $event.clientX, $event.clientY)" @pointerup="press.cancel" @pointercancel="press.cancel" @pointerleave="press.cancel" @contextmenu="contextMenu"><p>{{ message.content }}</p></div>
    <div class="chat-message-meta">
      <time :datetime="message.createdAt">{{ time }}</time>
      <span v-if="message.status === 'loading'" class="chat-status"><span class="chat-spinner" /> 傳送中</span>
      <span v-else-if="message.status === 'error'" class="chat-error-text">{{ message.uncertain ? '儲存結果未確認' : '尚未儲存' }}</span>
      <span v-else class="chat-status"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>已儲存</span>
      <button v-if="message.status === 'success' && message.role === 'user'" ref="moreButton" type="button" class="chat-message-more" :disabled="disabled" aria-label="訊息操作" aria-haspopup="dialog" @click="openActions">⋯</button>
    </div>
  </li>
</template>
