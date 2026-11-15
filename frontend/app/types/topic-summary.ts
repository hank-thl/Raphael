export interface SummarySource { memory_id: string; revision: number }
export interface SynthesisPassage {
  text: string
  basis: 'explicit' | 'contextual' | 'inferred'
  sources: SummarySource[]
  time_label: string | null
}
export interface MemorySynthesis {
  title: string
  summary: SynthesisPassage[]
  sections: { heading: string; layout: 'paragraphs' | 'bullets' | 'timeline'; items: SynthesisPassage[] }[]
}
export interface TopicSummary {
  state: 'missing' | 'ready' | 'stale' | 'invalid'
  generated_at: string | null
  memory_count: number
  format: 'none' | 'legacy' | 'article'
  contract_version: string | null
  content: MemorySynthesis | null
  items: { text: string; basis: SynthesisPassage['basis']; sources: SummarySource[] }[]
}
