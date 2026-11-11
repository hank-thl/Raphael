<script setup lang="ts">
import type { ChatMessage } from '~/types/chat'
const props = defineProps<{ message: ChatMessage }>()
const time = computed(() => new Intl.DateTimeFormat('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Taipei' }).format(new Date(props.message.createdAt)))
</script>

<template>
  <li class="chat-message" :class="{ 'chat-message-assistant': message.role === 'assistant' }">
    <div class="chat-bubble" :class="{ 'chat-bubble-error': message.status === 'error' }"><p>{{ message.content }}</p></div>
    <div class="chat-message-meta">
      <time :datetime="message.createdAt">{{ time }}</time>
      <span v-if="message.status === 'loading'" class="chat-status"><span class="chat-spinner" /> 傳送中</span>
      <span v-else-if="message.status === 'error'" class="chat-error-text">{{ message.uncertain ? '儲存結果未確認' : '尚未儲存' }}</span>
      <span v-else class="chat-status"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>已儲存</span>
    </div>
  </li>
</template>
