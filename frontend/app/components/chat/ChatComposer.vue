<script setup lang="ts">
import { recordCharacterCount, recordValidation } from '~/utils/records'
const content = defineModel<string>({ required: true })
const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ send: [] }>()
const input = ref<HTMLTextAreaElement>()
const count = computed(() => recordCharacterCount(content.value))
const invalid = computed(() => Boolean(recordValidation(content.value)))
function resize() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  const maxHeight = Number.parseFloat(getComputedStyle(el).maxHeight) || 176
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
}
function send() { if (!props.disabled && !invalid.value) emit('send') }
function onKeydown(event: KeyboardEvent) {
  // Preserve IME composition and mobile Enter for line breaks.
  if (event.key === 'Enter' && !event.isComposing && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    send()
  }
}
watch(content, () => nextTick(resize))
onMounted(resize)
</script>

<template>
  <form class="chat-composer" @submit.prevent="send">
    <label for="chat-draft" class="sr-only">想記下的文字</label>
    <div class="chat-input-shell">
      <textarea id="chat-draft" ref="input" v-model="content" :disabled="disabled" rows="1" enterkeyhint="enter" :aria-describedby="count > 10000 ? 'chat-input-error' : undefined" :aria-invalid="count > 10000" placeholder="此刻，想記下什麼？" @input="resize" @keydown="onKeydown" />
      <button type="submit" class="chat-send" :disabled="disabled || invalid" aria-label="傳送訊息">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 12 6-6 6 6M12 6v13" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
    <p v-if="count > 10000" id="chat-input-error" role="alert" class="chat-input-error">文字超過 10,000 字，請縮短後再傳送。</p>
  </form>
</template>
