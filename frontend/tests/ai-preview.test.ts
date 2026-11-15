import test from 'node:test'
import assert from 'node:assert/strict'
import { createAiPreview } from '../app/utils/ai-preview.ts'
import type { AiPreview } from '../app/types/ai-preview.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function setup() {
  let uid: string | undefined = 'owner'
  const csrf = deferred<string>(), response = deferred<AiPreview>()
  const calls: string[][] = [], results: AiPreview[] = [], failures: unknown[] = [], busy: boolean[] = []
  let signal: AbortSignal | undefined
  const runner = createAiPreview({
    uid: () => uid,
    csrf: value => { signal = value; return csrf.promise },
    request: (text, owner, token) => { calls.push([text, owner, token]); return response.promise },
    busy: value => busy.push(value), result: value => results.push(value), failure: value => failures.push(value),
  })
  return { runner, csrf, response, calls, results, failures, busy, signal: () => signal, uid: (value: string | undefined) => { uid = value } }
}
const result = { persisted: false } as AiPreview

test('double submit sends one request and preserves exact source text', async () => {
  const s = setup()
  const pending = s.runner.run(' 文字\n')
  await s.runner.run('duplicate')
  s.csrf.resolve('token'); await Promise.resolve()
  assert.deepEqual(s.calls, [[' 文字\n', 'owner', 'token']])
  s.response.resolve(result); await pending
  assert.deepEqual(s.results, [result])
  assert.deepEqual(s.busy, [true, false])
})

test('account switch during csrf prevents paid POST', async () => {
  const s = setup(), pending = s.runner.run('test')
  s.uid('other'); s.runner.reset(); s.csrf.resolve('token'); await pending
  assert.deepEqual(s.calls, [])
  assert.equal(s.signal()?.aborted, true)
})

test('leaving page suppresses late results', async () => {
  const s = setup(), pending = s.runner.run('test')
  s.csrf.resolve('token'); await Promise.resolve()
  s.runner.dispose(); s.response.resolve(result); await pending
  assert.deepEqual(s.results, [])
  assert.equal(s.signal()?.aborted, true)
})

test('unknown failure is surfaced once without automatic POST retry', async () => {
  let calls = 0
  const errors: unknown[] = []
  const runner = createAiPreview({ uid: () => 'owner', csrf: async () => 'token',
    request: async () => { calls++; throw new Error('lost response') },
    busy: () => {}, result: () => assert.fail('No successful result'), failure: error => errors.push(error) })
  await runner.run('test')
  assert.equal(calls, 1); assert.equal(errors.length, 1)
})

test('invalid or anonymous input never requests csrf', async () => {
  const s = setup()
  await s.runner.run(' \n'); await s.runner.run('x'.repeat(10001))
  s.uid(undefined); await s.runner.run('test')
  assert.deepEqual(s.busy, [])
})
