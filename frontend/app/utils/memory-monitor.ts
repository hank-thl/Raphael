import type { MemoryJobStatus } from '../types/memory.ts'

interface Options {
  ids(): string[]
  read(id: string): Promise<MemoryJobStatus>
  retry(id: string): Promise<MemoryJobStatus>
  status(id: string, value: MemoryJobStatus): void
  unavailable(id: string, value: boolean): void
  retrying(id: string, value: boolean): void
  failure(error: unknown): void
}

// Each request has a generation: a delayed GET cannot overwrite a newer retry.
export function createMemoryMonitor(options: Options) {
  const states = new Map<string, MemoryJobStatus>()
  const generations = new Map<string, number>()
  const reading = new Set<string>()
  const retrying = new Set<string>()
  let active = true
  const tracked = (id: string) => active && options.ids().includes(id)
  const next = (id: string) => { const n = (generations.get(id) || 0) + 1; generations.set(id, n); return n }
  const current = (id: string, generation: number) => tracked(id) && generations.get(id) === generation
  function apply(id: string, status: MemoryJobStatus) {
    states.set(id, status)
    options.status(id, status)
    options.unavailable(id, false)
  }
  async function read(id: string) {
    if (!tracked(id) || reading.has(id) || retrying.has(id)) return
    reading.add(id)
    const generation = next(id)
    try {
      const result = await options.read(id)
      if (current(id, generation)) apply(id, result)
    } catch (error) {
      if (current(id, generation)) { options.unavailable(id, true); options.failure(error) }
    } finally { reading.delete(id) }
  }
  async function refresh(ids = options.ids()) {
    const queue = [...new Set(ids)]
    // Bound concurrent requests when loading a history page.
    await Promise.all(Array.from({ length: Math.min(4, queue.length) }, async () => {
      while (active && queue.length) await read(queue.shift()!)
    }))
  }
  async function retry(id: string) {
    if (!tracked(id) || retrying.has(id) || states.get(id)?.state !== 'failed') return
    retrying.add(id)
    options.retrying(id, true)
    const generation = next(id)
    try {
      const result = await options.retry(id)
      if (current(id, generation)) apply(id, result)
    } catch (error) {
      if (current(id, generation)) {
        options.unavailable(id, true)
        options.failure(error)
      }
    } finally {
      retrying.delete(id)
      if (tracked(id)) options.retrying(id, false)
    }
    // Even if POST's response was lost, query before permitting another retry.
    if (tracked(id)) await read(id)
  }
  return {
    refresh, retry,
    poll() { return refresh(options.ids().filter(id => ['pending', 'running'].includes(states.get(id)?.state || ''))) },
    unknown() { return options.ids().filter(id => !states.has(id)) },
    prune() { const ids = new Set(options.ids()); for (const id of states.keys()) if (!ids.has(id)) states.delete(id) },
    dispose() { active = false; states.clear(); generations.clear() },
  }
}
