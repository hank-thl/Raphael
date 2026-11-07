export interface PersonalRecord {
  id: string
  content: string
  created_at: string
}

export interface RecordPage {
  items: PersonalRecord[]
  next_cursor: string | null
}
