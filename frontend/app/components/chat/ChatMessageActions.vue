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
  <dialog ref="dialog" class="fixed inset-x-0 top-auto bottom-0 m-0 max-h-[min(85dvh,650px)] w-full max-w-full overflow-y-auto rounded-t-3xl border-0 bg-chat-paper p-0 text-chat-ink shadow-[0_-10px_45px_#20372b20] backdrop:bg-[#17281e66] sm:inset-0 sm:m-auto sm:max-h-[80dvh] sm:w-[calc(100%_-_48px)] sm:max-w-[440px] sm:rounded-[22px]" aria-labelledby="message-action-title" aria-describedby="message-action-description" :aria-busy="busy" @cancel.prevent="close" @click="backdrop">
    <div class="px-6 pt-2.5 pb-[max(24px,env(safe-area-inset-bottom))]">
      <span class="mx-auto mb-6 block h-1 w-9 rounded bg-[#d7dfd0]" aria-hidden="true" />
      <h2 class="text-[19px] font-semibold" id="message-action-title">{{ confirming ? '刪除這筆紀錄？' : '訊息操作' }}</h2>
      <p id="message-action-description" class="mt-2.5 text-[13px] leading-[1.9] text-chat-muted">{{ confirming ? '刪除後將從聊天室隱藏，原文仍保留在資料庫。目前不提供還原介面。' : '選擇要對這筆紀錄進行的操作。' }}</p>
      <blockquote class="my-[18px] max-h-[100px] overflow-y-auto rounded-md border-l-[3px] border-[#c7d4bb] bg-[#eff2e9] px-4 py-3 text-[13px] leading-[1.8] whitespace-pre-wrap text-[#67765d] wrap-anywhere">{{ message.content }}</blockquote>
      <p v-if="error" class="mt-2.5 text-[13px] leading-[1.9] text-[#a04434]" role="alert">{{ error }}</p>
      <button v-if="!confirming" type="button" class="mt-2.5 flex min-h-12 w-full items-center justify-center rounded-xl text-[14px] bg-[#f9ece6] text-[#a04434]" @click="confirming = true">刪除紀錄</button>
      <button v-else type="button" class="mt-2.5 flex min-h-12 w-full items-center justify-center rounded-xl text-[14px] bg-[#a44937] text-white" :disabled="busy" @click="$emit('remove')">{{ busy ? '刪除中…' : '確認刪除' }}</button>
      <button type="button" :disabled="busy" autofocus class="mt-2.5 flex min-h-12 w-full items-center justify-center rounded-xl text-[14px] bg-[#eaf0e4] text-[#405b35]" @click="close">取消</button>
    </div>
  </dialog>
</template>
