export interface MemoryContent {
  text: string
  kind: string[]
  topics: string[]
  status: 'considering' | 'confirmed' | 'occurred' | 'cancelled' | null
  time_text: string | null
}
export interface MemorySummary extends MemoryContent {
  id: string
  revision: number
  updated_at: string
  evidence_type: 'explicit' | 'contextual' | 'inferred'
}
export interface MemoryDetail extends MemorySummary {
  versions: (MemoryContent & { revision: number; created_at: string; evidence: {
    type: MemorySummary['evidence_type']; sources: { record_id: string; quote: string }[]
  } })[]
}
export interface MemoryPage { items: MemorySummary[]; next_cursor: string | null }
