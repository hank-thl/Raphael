import type { MemoryJobStatus } from './memory.ts'

export interface PersonalRecord {
  id: string
  content: string
  created_at: string
  memory_processing?: MemoryJobStatus | null
}

export interface RecordPage {
  items: PersonalRecord[]
  next_cursor: string | null
}
