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
  <form @submit.prevent="send">
    <label for="chat-draft" class="sr-only">想記下的文字</label>
    <div class="flex items-end gap-2.5 rounded-2xl border border-[#dce2d4] bg-white py-0.5 pr-1.5 pl-3 md:rounded-[18px] md:py-1 md:pr-2 md:pl-3.5 shadow-[0_4px_20px_#2c442b05] focus-within:border-[#9daf91] focus-within:shadow-[0_0_0_3px_#a1b39312]">
      <textarea class="m-0 min-h-11 max-h-32 min-w-0 flex-1 resize-none rounded-none border-0 bg-transparent px-0 py-2.5 text-base leading-6 text-[#32483a] outline-none placeholder:text-[#a0a79b] focus-visible:outline-none disabled:opacity-60 md:max-h-44" id="chat-draft" ref="input" v-model="content" :disabled="disabled" rows="1" enterkeyhint="enter" :aria-describedby="count > 10000 ? 'chat-input-error' : undefined" :aria-invalid="count > 10000" placeholder="此刻，想記下什麼？" @input="resize" @keydown="onKeydown" />
      <button type="submit" class="relative mb-1 flex size-9 min-w-9 shrink-0 items-center justify-center rounded-[11px] bg-chat-green text-white transition-colors duration-150 after:absolute after:-inset-1 after:content-[''] enabled:hover:bg-[#203e30] motion-reduce:transition-none md:mb-0 md:size-11 md:min-w-11 md:rounded-[13px] md:after:hidden [&_svg]:size-[18px] md:[&_svg]:size-[22px]" :disabled="disabled || invalid" aria-label="傳送訊息">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 12 6-6 6 6M12 6v13" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
    <p v-if="count > 10000" id="chat-input-error" role="alert" class="mt-1 text-[12px] text-chat-error">文字超過 10,000 字，請縮短後再傳送。</p>
  </form>
</template>
