import type { ChatMessage, ChatHistory } from '../types/chat.ts'
import type { PersonalRecord, RecordPage } from '../types/record.ts'

export function recordToMessage(record: PersonalRecord): ChatMessage {
  return { id: record.id, role: 'user', content: record.content, createdAt: record.created_at, status: 'success' }
}
export function recordPageToHistory(page: RecordPage): ChatHistory {
  return { messages: [...page.items].reverse().map(recordToMessage), nextCursor: page.next_cursor }
}
export function mergeChatMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const unique = new Map(current.map(message => [message.id, message]))
  for (const message of incoming) unique.set(message.id, message)
  return [...unique.values()].sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime() || a.id.localeCompare(b.id))
}
