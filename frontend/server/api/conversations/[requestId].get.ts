export default defineEventHandler(async event => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const id = getRouterParam(event, 'requestId') || ''
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) throw createError({ statusCode: 404 })
  return proxyRequest(event, new URL(`/conversations/${id}`, useRuntimeConfig(event).backendBaseUrl).toString())
})
