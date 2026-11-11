<script setup lang="ts">
import type { ChatMessage } from '~/types/chat'
const props = defineProps<{ message: ChatMessage; busy: boolean; error: string }>()
const emit = defineEmits<{ close: []; remove: [] }>()
const dialog = ref<HTMLDialogElement>()
const confirming = ref(false)
function close() { if (!props.busy) emit('close') }
function backdrop(event: MouseEvent) { if (event.target === dialog.value) close() }
onMounted(() => dialog.value?.showModal())
onBeforeUnmount(() => dialog.value?.close())
</script>

<template>
  <dialog ref="dialog" class="chat-message-dialog" aria-labelledby="message-action-title" aria-describedby="message-action-description" :aria-busy="busy" @cancel.prevent="close" @click="backdrop">
    <div class="chat-message-sheet">
      <span class="chat-sheet-handle" aria-hidden="true" />
      <h2 id="message-action-title">{{ confirming ? '刪除這筆紀錄？' : '訊息操作' }}</h2>
      <p id="message-action-description">{{ confirming ? '刪除後將從聊天室隱藏，原文仍保留在資料庫。目前不提供還原介面。' : '選擇要對這筆紀錄進行的操作。' }}</p>
      <blockquote>{{ message.content }}</blockquote>
      <p v-if="error" class="chat-sheet-error" role="alert">{{ error }}</p>
      <button v-if="!confirming" type="button" class="chat-sheet-delete" @click="confirming = true">刪除紀錄</button>
      <button v-else type="button" class="chat-sheet-delete chat-sheet-confirm" :disabled="busy" @click="$emit('remove')">{{ busy ? '刪除中…' : '確認刪除' }}</button>
      <button type="button" :disabled="busy" autofocus class="chat-sheet-cancel" @click="close">取消</button>
    </div>
  </dialog>
</template>
