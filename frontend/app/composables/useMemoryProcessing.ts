import type { MemoryJobStatus } from '~/types/memory'
import { createMemoryMonitor } from '~/utils/memory-monitor'
import { recordErrorStatus } from '~/utils/records'

export function useMemoryProcessing(ids: () => string[], enabled: () => boolean, onExpired: (error: unknown) => void) {
  const api = useRecords()
  const statuses = ref<Record<string, MemoryJobStatus>>({})
  const unavailable = ref<Record<string, boolean>>({})
  const retrying = ref<Record<string, boolean>>({})
  const forbidden = ref(false)
  let mounted = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const allowed = () => mounted && enabled() && !forbidden.value
  const monitor = createMemoryMonitor({
    ids: () => allowed() && document.visibilityState === 'visible' ? ids() : [], read: api.memoryStatus, retry: api.retryMemory,
    status: (id, value) => { statuses.value[id] = value },
    unavailable: (id, value) => { unavailable.value[id] = value },
    retrying: (id, value) => { retrying.value[id] = value },
    failure: (error) => {
      const code = recordErrorStatus(error)
      if (code === 401) onExpired(error)
      if (code === 403) { forbidden.value = true; statuses.value = {}; unavailable.value = {} }
    },
  })
  async function tick() {
    timer = undefined
    if (!allowed()) return
    if (document.visibilityState === 'visible') await monitor.poll()
    if (allowed()) timer = setTimeout(tick, 4000)
  }
  function refresh() { if (allowed() && document.visibilityState === 'visible') void monitor.refresh() }
  function visibility() { if (document.visibilityState === 'visible') refresh() }
  watch(() => ids().join(','), () => {
    monitor.prune()
    const current = new Set(ids())
    for (const map of [statuses.value, unavailable.value, retrying.value]) {
      for (const id of Object.keys(map)) if (!current.has(id)) delete map[id]
    }
    if (allowed()) void monitor.refresh(monitor.unknown())
  })
  watch(enabled, () => {
    if (!allowed() && timer) { clearTimeout(timer); timer = undefined }
    if (allowed()) { refresh(); if (!timer) void tick() }
  })
  onMounted(() => {
    mounted = true
    refresh()
    if (allowed()) void tick()
    document.addEventListener('visibilitychange', visibility)
  })
  onBeforeUnmount(() => {
    mounted = false
    if (timer) clearTimeout(timer)
    monitor.dispose()
    document.removeEventListener('visibilitychange', visibility)
  })
  return { statuses, unavailable, retrying, refresh, retry: monitor.retry }
}
