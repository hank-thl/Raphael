import type { AiPreview } from '../types/ai-preview.ts'

interface Options {
  uid(): string | undefined
  csrf(signal: AbortSignal): Promise<string>
  request(text: string, uid: string, token: string, signal: AbortSignal): Promise<AiPreview>
  busy(value: boolean): void
  result(value: AiPreview): void
  failure(error: unknown): void
}

// A transient run: no storage, retries or state shared between pages/users.
export function createAiPreview(options: Options) {
  let active = true, busy = false, epoch = 0
  let controller: AbortController | undefined
  function reset() {
    epoch++; controller?.abort(); busy = false
    if (active) options.busy(false)
  }
  return {
    reset,
    dispose() { active = false; reset() },
    async run(text: string) {
      const uid = options.uid()
      if (!active || busy || !uid || !text.trim() || Array.from(text).length > 10000) return
      const version = ++epoch
      const current = () => active && epoch === version && options.uid() === uid
      controller = new AbortController()
      const signal = controller.signal
      busy = true; options.busy(true)
      try {
        const token = await options.csrf(signal)
        if (!current()) return
        const result = await options.request(text, uid, token, signal)
        if (current()) options.result(result)
      } catch (error) {
        if (current()) options.failure(error)
      } finally {
        if (epoch === version) {
          busy = false
          if (active) options.busy(false)
        }
      }
    },
  }
}
