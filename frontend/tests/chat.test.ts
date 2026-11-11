import assert from 'node:assert/strict'
import test from 'node:test'
import { mergeChatMessages, recordPageToHistory, recordToMessage } from '../app/utils/chat.ts'

test('records history reverses server order without mutating page or losing cursor', () => {
  const page = { items: [
    { id: 'b', content: 'new', created_at: '2026-11-08T10:00:00Z' },
    { id: 'a', content: 'old\ntext', created_at: '2026-11-08T09:00:00Z' },
  ], next_cursor: 'opaque-cursor' }
  const history = recordPageToHistory(page)
  assert.deepEqual(history.messages.map(m => m.id), ['a', 'b'])
  assert.equal(history.nextCursor, 'opaque-cursor')
  assert.deepEqual(page.items.map(m => m.id), ['b', 'a'])
  assert.equal(history.messages[0]?.content, 'old\ntext')
  assert.ok(history.messages.every(m => m.status === 'success' && m.role === 'user'))
})

test('overlapping history and saved responses merge once in chronological order', () => {
  const make = (id: string, time: string) => recordToMessage({ id, content: id, created_at: time })
  const current = [make('b', '2026-11-08T09:00:00Z'), make('c', '2026-11-08T09:00:00.100Z')]
  const result = mergeChatMessages(current, [make('a', '2026-11-08T16:00:00+08:00'), make('b', '2026-11-08T09:00:00Z')])
  assert.deepEqual(result.map(m => m.id), ['a', 'b', 'c'])
  assert.equal(current.length, 2)
  assert.deepEqual(mergeChatMessages(result, [result[2]!]).map(m => m.id), ['a', 'b', 'c'])
})

test('empty history retains exhausted cursor', () => {
  assert.deepEqual(recordPageToHistory({ items: [], next_cursor: null }), { messages: [], nextCursor: null })
})
