export type MemoryJobState = 'not_requested' | 'pending' | 'running' | 'succeeded' | 'failed'
export interface MemoryJobStatus { state: MemoryJobState; attempts: number; error: string | null }
