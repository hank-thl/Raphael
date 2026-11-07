import assert from 'node:assert/strict'
import test from 'node:test'
import {
  appendRecordPage, prependRecord, recordCharacterCount, recordValidation,
  recordErrorMessage, recordSaveErrorMessage, formatRecordTime,
} from '../app/utils/records.ts'

const record = (id: string) => ({ id, content: `內容 ${id}`, created_at: '2026-11-07T00:00:00Z' })

test('text validation matches Python Unicode character limits and keeps newlines', () => {
  assert.equal(recordCharacterCount('記錄🙂'), 3)
  assert.equal(recordValidation('🙂'.repeat(10000)), '')
  assert.notEqual(recordValidation('🙂'.repeat(10001)), '')
  assert.notEqual(recordValidation(' \n\t'), '')
  assert.equal(recordValidation('  原文\n第二行  '), '')
})

test('loading another cursor page deduplicates overlap and advances cursor without losing old rows', () => {
  const current = { items: [record('3'), record('2')], next_cursor: 'first' }
  const next = { items: [record('2'), record('1')], next_cursor: null }
  const merged = appendRecordPage(current, next)
  assert.deepEqual(merged.items.map(item => item.id), ['3', '2', '1'])
  assert.equal(merged.next_cursor, null)
  assert.deepEqual(current.items.map(item => item.id), ['3', '2'])
})

test('saving a record preserves the old cursor boundary and existing history', () => {
  const current = { items: [record('2'), record('1')], next_cursor: 'boundary' }
  const updated = prependRecord(current, record('3'))
  assert.deepEqual(updated.items.map(item => item.id), ['3', '2', '1'])
  assert.equal(updated.next_cursor, 'boundary')
  assert.deepEqual(prependRecord(updated, record('3')).items.map(item => item.id), ['3', '2', '1'])
})

test('uncertain writes explicitly warn before retrying; expired sessions and validation remain distinct', () => {
  assert.match(recordSaveErrorMessage(new Error('private-body')), /無法確認.*避免重複/)
  assert.match(recordSaveErrorMessage({ statusCode: 503 }), /無法確認/)
  assert.match(recordSaveErrorMessage({ statusCode: 401 }), /重新登入/)
  assert.match(recordSaveErrorMessage({ statusCode: 422 }), /一萬字/)
  assert.doesNotMatch(recordErrorMessage({ message: 'private-body', data: { detail: 'secret' } }), /secret|private-body/)
})

test('record times have a deterministic Taipei timezone for server and browser rendering', () => {
  const display = formatRecordTime('2026-11-07T00:00:00Z')
  assert.match(display, /2026/)
  assert.match(display, /08:00/)
})
