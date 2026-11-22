import test from 'node:test'
import assert from 'node:assert/strict'
import { createUnifiedSender, mergeEntries } from '../app/utils/unified-chat.ts'
import type { UnifiedEntry, UnifiedRequest } from '../app/types/unified-chat.ts'

function entry(overrides: Partial<UnifiedEntry> = {}): UnifiedEntry {
  return { id: 'record:a', created_at: '2026-11-22T00:00:00Z', record: {
    id: 'a', content: 'message', created_at: '2026-11-22T00:00:00Z',
  }, turn: null, mode: 'record', request_id: 'fixed', needs_answer: false, ...overrides }
}
function fixture(post: (body: UnifiedRequest) => Promise<UnifiedEntry>, read = async (_id: string) => entry()) {
  const results: UnifiedEntry[] = [], failures: unknown[] = []
  let pending: UnifiedRequest | null = null
  const sender = createUnifiedSender({ id: () => 'fixed', post, read, result: row => results.push(row),
    failure: error => failures.push(error), busy: () => {}, pending: body => { pending = body } })
  return { sender, results, failures, pending: () => pending }
}

test('one request preserves chosen mode and original text through explicit retry', async () => {
  const posts: UnifiedRequest[] = []
  const f = fixture(async body => { posts.push(body); if (posts.length === 1) throw new Error('offline'); return entry() })
  await f.sender.send(' 原文\n', 'dialogue')
  await f.sender.send('another message after mode switch', 'record')
  assert.equal(posts.length, 1)
  await f.sender.retry()
  assert.deepEqual(posts[0], { content: ' 原文\n', request_id: 'fixed', mode: 'dialogue' })
  assert.deepEqual(posts[1], posts[0]); assert.equal(f.pending(), null)
})

test('saved record with missing answer stays pending until explicit continuation', async () => {
  let posts = 0, reads = 0
  const recovery = fixture(async () => { posts++; throw new Error('lost') }, async () => {
    reads++; return entry({ mode: 'dialogue', needs_answer: true })
  })
  await recovery.sender.send('question', 'dialogue'); await recovery.sender.check()
  assert.equal(recovery.pending()?.request_id, 'fixed')
  assert.equal(recovery.results[0]?.needs_answer, true)
  assert.equal(posts, 1); assert.equal(reads, 1)
})

test('reading a terminal answer clears pending without a second POST', async () => {
  let posts = 0
  const f = fixture(async () => { posts++; throw new Error('lost') })
  await f.sender.send('message', 'record'); await f.sender.check()
  assert.equal(posts, 1); assert.equal(f.pending(), null)
})

test('double submit and dispose cannot duplicate or apply a late reply', async () => {
  let resolve!: (row: UnifiedEntry) => void
  let posts = 0
  const response = new Promise<UnifiedEntry>(done => { resolve = done })
  const f = fixture(async () => { posts++; return response })
  const sending = f.sender.send('question', 'dialogue')
  await f.sender.send('question', 'dialogue')
  assert.equal(posts, 1)
  f.sender.dispose(); resolve(entry()); await sending
  assert.deepEqual(f.results, [])
  await f.sender.retry(); assert.equal(posts, 1)
})

test('unified timeline merges updates without removing record-mode history', () => {
  const record = entry()
  const dialogue = entry({ id: 'record:b', mode: 'dialogue', created_at: '2026-11-22T00:01:00Z' })
  const latest = { ...dialogue, needs_answer: true }
  const result = mergeEntries([dialogue, record], [latest])
  assert.deepEqual(result.map(row => row.id), ['record:a', 'record:b'])
  assert.equal(result[1]?.needs_answer, true)
  assert.equal(record.mode, 'record')
})

test('blank and overlong input never submits', async () => {
  let posts = 0
  const f = fixture(async () => { posts++; return entry() })
  await f.sender.send('  ', 'record'); await f.sender.send('x'.repeat(10001), 'dialogue')
  assert.equal(posts, 0)
})
