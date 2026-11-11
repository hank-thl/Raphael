import assert from 'node:assert/strict'
import test from 'node:test'
import { createLongPress } from '../app/utils/long-press.ts'

function setup() {
  let callback: (() => void) | undefined
  let opens = 0
  const press = createLongPress(() => opens++, fn => { callback = fn; return 1 as unknown as ReturnType<typeof setTimeout> }, () => { callback = undefined })
  return { press, tick: () => { const fn = callback; callback = undefined; fn?.() }, opens: () => opens }
}
test('holding opens once; a normal release does not open', () => {
  const a = setup()
  a.press.start(1, 0, 0)
  a.tick(); a.tick()
  assert.equal(a.opens(), 1)
  a.press.start(1, 0, 0)
  a.press.cancel(); a.tick()
  assert.equal(a.opens(), 1)
})
test('scroll movement, cancellation and another pointer cancel the menu', () => {
  for (const action of ['movement', 'cancel', 'other-pointer']) {
    const a = setup()
    a.press.start(1, 20, 20)
    if (action === 'movement') a.press.move(1, 20, 31)
    else if (action === 'other-pointer') a.press.move(2, 20, 20)
    else a.press.cancel()
    a.tick()
    assert.equal(a.opens(), 0, action)
  }
})
test('minor finger movement is tolerated and restarting clears the old timer', () => {
  const a = setup()
  a.press.start(1, 0, 0)
  a.press.move(1, 3, 4)
  a.tick()
  assert.equal(a.opens(), 1)
  a.press.start(1, 0, 0)
  a.press.start(2, 0, 0)
  a.tick()
  assert.equal(a.opens(), 2)
})
