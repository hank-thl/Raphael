export interface PreviewItem {
  text: string
  kind: string[]
  topics: string[]
  status: string | null
  time_text: string | null
  evidence: { type: string; sources: { record_id: string; quote: string }[] }
}

export interface AiPreview {
  provider: string
  model: string
  prompt_version: string
  contract_version: string
  persisted: boolean
  analysis: {
    content_type: string
    memory_action: string
    context_required: boolean
    context_record_ids: string[]
    items: PreviewItem[]
  }
}
