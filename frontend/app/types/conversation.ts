import type { SummarySource } from './topic-summary'

export interface ConversationTurn {
  id: string
  request_id: string
  question: string
  state: 'running' | 'succeeded' | 'failed' | 'invalid'
  answer: { passages: { text: string; basis: 'explicit' | 'contextual' | 'inferred' | 'limitation' | 'general'; sources: SummarySource[] }[]; insufficient_information: boolean } | null
  error: string | null
  created_at: string
  updated_at: string
}
export interface ConversationPage { items: ConversationTurn[]; next_cursor: string | null }
