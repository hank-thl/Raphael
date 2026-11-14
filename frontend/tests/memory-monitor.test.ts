import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryMonitor } from '../app/utils/memory-monitor.ts'
import type { MemoryJobStatus } from '../app/types/memory.ts'
const status = (state: MemoryJobStatus['state']): MemoryJobStatus => ({ state, attempts: 1, error: null })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function setup(read: (id: string) => Promise<MemoryJobStatus>, retry = async (_id: string) => status('pending')) {
  const displayed = new Map<string, MemoryJobStatus>()
  const ids = { value: ['a', 'b'] }
  const monitor = createMemoryMonitor({ ids: () => ids.value, read, retry,
    status: (id, value) => displayed.set(id, value), unavailable: () => {}, retrying: () => {}, failure: () => {} })
  return { monitor, displayed, ids }
}

test('poll only pending/running, not completed analyses', async () => {
  const calls: string[] = []
  const fixture = setup(async id => { calls.push(id); return status(id === 'a' ? 'pending' : 'succeeded') })
  await fixture.monitor.refresh()
  calls.length = 0
  await fixture.monitor.poll()
  assert.deepEqual(calls, ['a'])
})

test('delayed GET cannot overwrite retry; double retry sends one POST', async () => {
  const oldGet = deferred<MemoryJobStatus>(), post = deferred<MemoryJobStatus>()
  let reads = 0, posts = 0
  const fixture = setup(async () => ++reads === 1 ? status('failed') : oldGet.promise,
    async () => { posts++; return post.promise })
  fixture.ids.value = ['a']
  await fixture.monitor.refresh()
  const reading = fixture.monitor.refresh()
  const retrying = fixture.monitor.retry('a')
  await fixture.monitor.retry('a')
  assert.equal(posts, 1)
  post.resolve(status('pending'))
  await retrying
  oldGet.resolve(status('failed'))
  await reading
  assert.equal(fixture.displayed.get('a')?.state, 'pending')
})

test('old and successful records cannot be retried', async () => {
  let posts = 0
  const fixture = setup(async id => status(id === 'a' ? 'not_requested' : 'succeeded'), async () => { posts++; return status('pending') })
  await fixture.monitor.refresh()
  await fixture.monitor.retry('a'); await fixture.monitor.retry('b')
  assert.equal(posts, 0)
})

test('disposed pages ignore outstanding responses', async () => {
  const response = deferred<MemoryJobStatus>()
  const fixture = setup(async () => response.promise)
  const refresh = fixture.monitor.refresh()
  fixture.ids.value = []
  fixture.monitor.dispose()
  response.resolve(status('succeeded'))
  await refresh
  assert.equal(fixture.displayed.size, 0)
})

test('history removed then loaded again is checked again', async () => {
  let reads = 0
  const fixture = setup(async () => { reads++; return status('succeeded') })
  fixture.ids.value = ['a']; await fixture.monitor.refresh()
  fixture.ids.value = []; fixture.monitor.prune()
  fixture.ids.value = ['a']; await fixture.monitor.refresh(fixture.monitor.unknown())
  assert.equal(reads, 2)
})

test('history status queries have at most four concurrent requests', async () => {
  let active = 0, maximum = 0
  const fixture = setup(async () => {
    active++; maximum = Math.max(maximum, active)
    await new Promise<void>(resolve => setImmediate(resolve))
    active--; return status('not_requested')
  })
  fixture.ids.value = Array.from({ length: 20 }, (_, i) => String(i))
  await fixture.monitor.refresh()
  assert.equal(maximum, 4)
})

test('lost retry response triggers status GET, never another automatic POST', async () => {
  let reads = 0, posts = 0
  const fixture = setup(async () => status(++reads === 1 ? 'failed' : 'running'), async () => { posts++; throw new Error('network') })
  fixture.ids.value = ['a']; await fixture.monitor.refresh(); await fixture.monitor.retry('a')
  assert.equal(posts, 1); assert.equal(reads, 2)
  assert.equal(fixture.displayed.get('a')?.state, 'running')
})
