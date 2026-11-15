export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  if (!['GET', 'POST'].includes(event.method)) throw createError({ statusCode: 405 })
  const target = new URL('/topic-summary', useRuntimeConfig(event).backendBaseUrl)
  if (event.method === 'GET') {
    const topic = getQuery(event).topic
    if (typeof topic !== 'string') throw createError({ statusCode: 422 })
    target.searchParams.set('topic', topic)
  }
  return proxyRequest(event, target.toString())
})
