import type { UnifiedEntry, UnifiedRequest, ChatMode } from '../types/unified-chat.ts'

export function mergeEntries(current: UnifiedEntry[], incoming: UnifiedEntry[]) {
  const entries = new Map(current.map(row => [row.id, row]))
  for (const row of incoming) entries.set(row.id, row)
  return [...entries.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
}

interface Options {
  id(): string
  post(request: UnifiedRequest): Promise<UnifiedEntry>
  read(id: string): Promise<UnifiedEntry>
  result(entry: UnifiedEntry): void
  failure(error: unknown): void
  busy(value: boolean): void
  pending(value: UnifiedRequest | null): void
}

export function createUnifiedSender(options: Options) {
  let active = true, busy = false
  let pending: UnifiedRequest | null = null
  function accept(row: UnifiedEntry) {
    if (!active) return
    options.result(row)
    if (!row.needs_answer && row.turn?.state !== 'running') {
      pending = null; options.pending(null)
    }
  }
  async function act(action: () => Promise<UnifiedEntry>) {
    if (!active || busy) return
    busy = true; options.busy(true)
    try { accept(await action()) }
    catch (error) { if (active) options.failure(error) }
    finally { busy = false; if (active) options.busy(false) }
  }
  return {
    send(content: string, mode: ChatMode) {
      if (!active || busy || pending || !content.trim() || Array.from(content).length > 10000) return Promise.resolve()
      pending = { content, mode, request_id: options.id() }; options.pending(pending)
      const body = pending
      return act(() => options.post(body))
    },
    check() { const body = pending; return body ? act(() => options.read(body.request_id)) : Promise.resolve() },
    retry() { const body = pending; return body ? act(() => options.post(body)) : Promise.resolve() },
    resume(body: UnifiedRequest) {
      if (!active || busy || pending) return Promise.resolve()
      pending = { ...body }; options.pending(pending)
      return act(() => options.post(body))
    },
    dispose() { active = false; pending = null },
  }
}
