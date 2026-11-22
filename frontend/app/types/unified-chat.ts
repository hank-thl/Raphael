import type { PersonalRecord } from './record.ts'
import type { ConversationTurn } from './conversation.ts'

export type ChatMode = 'record' | 'dialogue'
export interface UnifiedEntry {
  id: string
  created_at: string
  record: PersonalRecord | null
  turn: ConversationTurn | null
  mode: ChatMode
  request_id: string | null
  needs_answer: boolean
}
export interface UnifiedPage { items: UnifiedEntry[]; next_cursor: string | null }
export interface UnifiedRequest { content: string; request_id: string; mode: ChatMode }
