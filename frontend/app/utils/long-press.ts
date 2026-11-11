// Pointer cancellation and movement must end the gesture before it opens a menu.
export function createLongPress(open: () => void, schedule = (fn: () => void) => setTimeout(fn, 550), cancelTimer = (id: ReturnType<typeof setTimeout>) => clearTimeout(id)) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let start: { id: number; x: number; y: number } | undefined
  function cancel() {
    if (timer !== undefined) cancelTimer(timer)
    timer = undefined
    start = undefined
  }
  return {
    start(id: number, x: number, y: number) {
      cancel()
      start = { id, x, y }
      timer = schedule(() => { timer = undefined; start = undefined; open() })
    },
    move(id: number, x: number, y: number) {
      if (start && (id !== start.id || Math.hypot(x - start.x, y - start.y) > 10)) cancel()
    },
    cancel,
  }
}
