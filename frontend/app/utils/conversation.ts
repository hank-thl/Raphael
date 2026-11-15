import type { ConversationTurn } from '../types/conversation.ts'

export interface ConversationRequest { content: string; request_id: string }
interface Options {
  id(): string
  post(request: ConversationRequest): Promise<ConversationTurn>
  read(requestId: string): Promise<ConversationTurn>
  result(turn: ConversationTurn): void
  failure(error: unknown): void
  busy(value: boolean): void
  pending(value: ConversationRequest | null): void
}
// Preserve one idempotency key across uncertain writes and explicit retries.
export function createConversationSender(options: Options) {
  let active = true, busy = false
  let pending: ConversationRequest | null = null
  function accept(turn: ConversationTurn) {
    if (!active) return
    options.result(turn)
    if (turn.state !== 'running') { pending = null; options.pending(null) }
  }
  async function act(action: () => Promise<ConversationTurn>) {
    if (!active || busy) return
    busy = true; options.busy(true)
    try { accept(await action()) }
    catch (error) { if (active) options.failure(error) }
    finally { busy = false; if (active) options.busy(false) }
  }
  return {
    send(content: string) {
      if (!active || busy || pending || !content.trim() || Array.from(content).length > 10000) return Promise.resolve()
      pending = { content, request_id: options.id() }; options.pending(pending)
      const request = pending
      return act(() => options.post(request))
    },
    check() { const request = pending; return request ? act(() => options.read(request.request_id)) : Promise.resolve() },
    retry() { const request = pending; return request ? act(() => options.post(request)) : Promise.resolve() },
    dispose() { active = false; pending = null },
  }
}
