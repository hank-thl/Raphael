export type MessageStatus = 'loading' | 'success' | 'error'
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  status: MessageStatus
  uncertain?: boolean
}
export interface ChatHistory { messages: ChatMessage[]; nextCursor: string | null }
// Presentation stays independent of the records API response shape.
export interface ChatTransport {
  history(cursor?: string): Promise<ChatHistory>
  send(message: ChatMessage): Promise<ChatMessage>
}
