import test from 'node:test'
import assert from 'node:assert/strict'
import { createConversationSender, type ConversationRequest } from '../app/utils/conversation.ts'
import type { ConversationTurn } from '../app/types/conversation.ts'

function turn(state: ConversationTurn['state'] = 'succeeded'): ConversationTurn {
  return { id: 'turn', request_id: 'fixed-id', question: 'question', state, answer: null, error: null,
    created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' }
}
function setup() {
  const posts: ConversationRequest[] = [], reads: string[] = [], results: ConversationTurn[] = [], errors: unknown[] = []
  let resolve!: (value: ConversationTurn) => void
  const response = new Promise<ConversationTurn>(done => { resolve = done })
  let pending: ConversationRequest | null = null
  const sender = createConversationSender({ id: () => 'fixed-id',
    post: async body => { posts.push(body); return response },
    read: async id => { reads.push(id); return turn() },
    result: value => results.push(value), failure: value => errors.push(value), busy: () => {},
    pending: value => { pending = value },
  })
  return { sender, posts, reads, results, errors, resolve, pending: () => pending }
}

test('double send uses a single paid POST and preserves source text', async () => {
  const s = setup(), first = s.sender.send(' question\n')
  await s.sender.send('duplicate')
  assert.equal(s.posts.length, 1)
  assert.equal(s.posts[0]!.content, ' question\n')
  s.resolve(turn()); await first
  assert.equal(s.pending(), null)
})

test('uncertain request is checked without another POST; terminal status unblocks input', async () => {
  const posts: ConversationRequest[] = [], reads: string[] = []
  const sender = createConversationSender({ id: () => 'fixed-id',
    post: async body => { posts.push(body); throw new Error('lost response') },
    read: async id => { reads.push(id); return turn() },
    result: () => {}, failure: () => {}, busy: () => {}, pending: () => {},
  })
  await sender.send('question'); await sender.send('new question')
  assert.equal(posts.length, 1)
  await sender.check()
  assert.deepEqual(reads, ['fixed-id'])
  assert.equal(posts.length, 1)
})

test('explicit retry reuses original ID and question after a failed request', async () => {
  const posts: ConversationRequest[] = []
  const sender = createConversationSender({ id: () => 'fixed-id',
    post: async body => { posts.push(body); if (posts.length === 1) throw new Error('offline'); return turn() },
    read: async () => turn(), result: () => {}, failure: () => {}, busy: () => {}, pending: () => {},
  })
  await sender.send('question'); await sender.retry()
  assert.equal(posts.length, 2)
  assert.deepEqual(posts[0], posts[1])
})

test('running status stays pending until a terminal GET, without regenerating', async () => {
  const s = setup(), pending = s.sender.send('question')
  s.resolve(turn('running')); await pending
  assert.equal(s.pending()?.request_id, 'fixed-id')
  await s.sender.send('new question')
  assert.equal(s.posts.length, 1)
  await s.sender.check()
  assert.equal(s.pending(), null)
})

test('leaving suppresses late response application and retries', async () => {
  const s = setup(), pending = s.sender.send('question')
  s.sender.dispose(); s.resolve(turn()); await pending
  assert.deepEqual(s.results, [])
  await s.sender.retry(); assert.equal(s.posts.length, 1)
})

test('blank and overlong questions do not send requests', async () => {
  const s = setup()
  await s.sender.send('  \n'); await s.sender.send('x'.repeat(10001))
  assert.deepEqual(s.posts, [])
})
